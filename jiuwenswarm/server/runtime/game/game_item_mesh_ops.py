# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""网格补全与拓扑重构：两条都走 Tripo3D，输入都是一个已存在的 3D 模型
素材（不是图片），与 ``game_item_decompose.py`` 的两条图片流水线并列。

- **网格补全**（``submit_mesh_complete``/``poll_mesh_complete``）：修复模型
  上的破洞/不完整几何。实测确认 ``mesh/complete`` 端点的 ``input`` 只接受
  "一个 ``mesh/segment`` 任务的 task_id"，不接受 file_token 或 URL——这与
  ``mesh/decimate`` 不同，意味着一个本地已有的模型文件不能直接喂给
  ``mesh/complete``，必须先把它上传并过一遍 ``mesh/segment``（哪怕不需要
  真的分段，这一步只是把文件"注册"进 Tripo3D 的任务图里），拿到那个
  task_id 后才能提交补全。所以这是一条两段式流水线：
  上传 -> 提交 segment -> 轮询 segment 到成功 -> 提交 complete -> 轮询
  complete 到成功 -> 下载。``poll_mesh_complete`` 把"当前处于哪一段"
  （``stage``：``"segment"`` 或 ``"complete"``）和该段对应的 Tripo3D
  task_id 都交给调用方持久化（落在占位素材的 ``params`` 里，见
  ``game_manager.handle_game_assets_mesh_complete_check_status``），下一次
  轮询时原样传回来——这样即使服务重启，只要占位素材还在，就能从正确的
  阶段继续轮询，不需要本模块自己维护进程内状态。

- **拓扑重构**（``submit_mesh_decimate``/``poll_mesh_decimate``）：对模型做
  减面重拓扑。``mesh/decimate`` 的 ``input`` 直接接受 file_token，不需要
  先过 segment，是普通的单段"提交 -> 轮询 -> 下载"，与
  ``game_item_decompose.submit_to_model``/``poll_to_model`` 同构。
  ``face_limit``/``model``/``quad`` 都不开放成用户可选参数（省略时 Tripo3D
  按 adaptive 策略自动定目标面数、默认不输出四边面），这功能目前只是"一键
  优化"，不需要一整套减面参数面板。

两条流水线共用的上传/鉴权/错误解析在 ``game_tripo_client.py``。
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    PENDING_STATUSES,
    TRANSIENT_HTTP_ERRORS,
    api_base,
    error_detail,
    headers,
    tripo_configured,
    upload_file,
)

logger = logging.getLogger(__name__)

MESH_COMPLETE_STAGE_SEGMENT = "segment"
MESH_COMPLETE_STAGE_COMPLETE = "complete"


class GameItemMeshOpsError(Exception):
    """带 code 的网格处理失败，与 ``GameItemDecomposeError`` 同一个形状。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class MeshCompleteStatus:
    status: str  # "pending" | "success" | "failed"
    # status == "pending" 时，stage/task_id 是"下一次轮询应该用的阶段与
    # task_id"——可能跟调用方传进来的一样（还在同一阶段轮询中），也可能已经
    # 从 segment 推进到 complete（调用方据此更新占位素材的 params）。
    stage: str | None = None
    task_id: str | None = None
    model_file_path: str | None = None
    error: str | None = None


@dataclass
class MeshDecimateStatus:
    status: str  # "pending" | "success" | "failed"
    model_file_path: str | None = None
    error: str | None = None


def _save_model(model_bytes: bytes, save_dir: str, *, prefix: str) -> str:
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{int(time.time())}_{secrets.token_hex(3)}.glb"
    dest = out_dir / filename
    dest.write_bytes(model_bytes)
    return str(dest)


class GameItemMeshOps:
    """Tripo3D 网格补全（两段式）/ 拓扑重构（单段式）编排。"""

    async def configured(self) -> bool:
        return tripo_configured()

    # ------------------------------------------------------------------
    # 网格补全：upload -> mesh/segment -> mesh/complete
    # ------------------------------------------------------------------
    async def submit_mesh_complete(self, model_path: str) -> str:
        """上传源模型并提交第一段（``mesh/segment``），返回其 task_id——
        调用方把这个 task_id 和 stage="segment" 一起存起来，交给
        ``poll_mesh_complete`` 继续推进。"""
        if not tripo_configured():
            raise GameItemMeshOpsError("NOT_CONFIGURED", "Tripo3D 网格补全未配置，请先在设置中配置「3D 分解」。")
        path = Path(model_path)
        if not path.is_file():
            raise GameItemMeshOpsError("INVALID_PARAMS", f"源模型不存在: {model_path}")

        try:
            # 上传的是模型文件（.glb），实测完整角色模型常见 40+MB，比分解/
            # 2D转3D 上传的源图大一个量级，默认 60s/120s 客户端超时都不够用，
            # 放宽到跟生成类调用同一档 300s（见 gameApi.ts
            # GAME_MESH_OP_SUBMIT_TIMEOUT_MS 的同一个取舍）。
            async with httpx.AsyncClient(timeout=300, verify=get_requests_verify()) as client:
                file_token = await upload_file(client, path)
                task_id = await self._submit_segment(client, file_token)
        except TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemMeshOpsError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            raise GameItemMeshOpsError("SUBMIT_FAILED", f"提交 Tripo3D 网格补全任务失败: {exc!r}") from exc
        return task_id

    async def poll_mesh_complete(self, task_id: str, stage: str, *, save_dir: str) -> MeshCompleteStatus:
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                data = await self._query_task(client, task_id)
                if isinstance(data, MeshCompleteStatus):  # 查询本身失败
                    return data
                status = str(data.get("status") or "").lower()
                if status in PENDING_STATUSES:
                    return MeshCompleteStatus(status="pending", stage=stage, task_id=task_id)
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return MeshCompleteStatus(status="failed", error=f"Tripo3D 网格补全任务未成功: {detail}")

                if stage == MESH_COMPLETE_STAGE_SEGMENT:
                    # segment 段完成：拿它的 task_id 提交 complete 段，推进
                    # 到下一阶段，还不下载——complete 段自己的成功才算数。
                    complete_task_id = await self._submit_complete(client, task_id)
                    return MeshCompleteStatus(
                        status="pending", stage=MESH_COMPLETE_STAGE_COMPLETE, task_id=complete_task_id
                    )

                output = data.get("output") or {}
                model_url = str(output.get("model_url") or "")
                if not model_url:
                    return MeshCompleteStatus(
                        status="failed",
                        error=f"Tripo3D 网格补全任务成功但没有返回模型地址: {str(output)[:300]}",
                    )
                model_resp = await client.get(model_url, timeout=120)
                if model_resp.status_code != 200:
                    return MeshCompleteStatus(status="failed", error=f"下载 Tripo3D 补全模型失败: {model_resp.status_code}")
                model_bytes = model_resp.content
        except TRANSIENT_HTTP_ERRORS as exc:
            return MeshCompleteStatus(status="pending", stage=stage, task_id=task_id, error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return MeshCompleteStatus(status="failed", error=f"查询/下载 Tripo3D 网格补全任务失败: {exc!r}")
        except GameItemMeshOpsError as exc:
            return MeshCompleteStatus(status="failed", error=str(exc))

        return MeshCompleteStatus(status="success", model_file_path=_save_model(model_bytes, save_dir, prefix="model_complete"))

    # ------------------------------------------------------------------
    # 拓扑重构：upload -> mesh/decimate（单段）
    # ------------------------------------------------------------------
    async def submit_mesh_decimate(self, model_path: str) -> str:
        if not tripo_configured():
            raise GameItemMeshOpsError("NOT_CONFIGURED", "Tripo3D 拓扑重构未配置，请先在设置中配置「3D 分解」。")
        path = Path(model_path)
        if not path.is_file():
            raise GameItemMeshOpsError("INVALID_PARAMS", f"源模型不存在: {model_path}")

        try:
            # 同 submit_mesh_complete：上传的是模型文件，放宽到 300s。
            async with httpx.AsyncClient(timeout=300, verify=get_requests_verify()) as client:
                file_token = await upload_file(client, path)
                task_id = await self._submit_decimate(client, file_token)
        except TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemMeshOpsError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            raise GameItemMeshOpsError("SUBMIT_FAILED", f"提交 Tripo3D 拓扑重构任务失败: {exc!r}") from exc
        return task_id

    async def poll_mesh_decimate(self, task_id: str, *, save_dir: str) -> MeshDecimateStatus:
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                data = await self._query_task(client, task_id)
                if isinstance(data, MeshCompleteStatus):  # 查询本身失败，复用同一个错误形状
                    return MeshDecimateStatus(status=data.status, error=data.error)
                status = str(data.get("status") or "").lower()
                if status in PENDING_STATUSES:
                    return MeshDecimateStatus(status="pending")
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return MeshDecimateStatus(status="failed", error=f"Tripo3D 拓扑重构任务未成功: {detail}")

                output = data.get("output") or {}
                model_url = str(output.get("model_url") or "")
                if not model_url:
                    return MeshDecimateStatus(
                        status="failed",
                        error=f"Tripo3D 拓扑重构任务成功但没有返回模型地址: {str(output)[:300]}",
                    )
                model_resp = await client.get(model_url, timeout=120)
                if model_resp.status_code != 200:
                    return MeshDecimateStatus(status="failed", error=f"下载 Tripo3D 重构模型失败: {model_resp.status_code}")
                model_bytes = model_resp.content
        except TRANSIENT_HTTP_ERRORS as exc:
            return MeshDecimateStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return MeshDecimateStatus(status="failed", error=f"查询/下载 Tripo3D 拓扑重构任务失败: {exc!r}")

        return MeshDecimateStatus(status="success", model_file_path=_save_model(model_bytes, save_dir, prefix="model_decimate"))

    # ------------------------------------------------------------------
    # 内部：提交/查询
    # ------------------------------------------------------------------
    async def _query_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any] | MeshCompleteStatus:
        resp = await client.get(f"{api_base()}/tasks/{task_id}", headers=headers())
        try:
            payload = resp.json()
        except ValueError:
            return MeshCompleteStatus(status="failed", error=f"查询 Tripo3D 任务返回了非 JSON 响应: {resp.status_code}")
        if resp.status_code != 200 or error_detail(payload):
            detail = error_detail(payload) or str(resp.status_code)
            return MeshCompleteStatus(status="failed", error=f"查询 Tripo3D 任务失败: {detail}")
        return (payload or {}).get("data") or {}

    async def _submit_segment(self, client: httpx.AsyncClient, file_token: str) -> str:
        return await self._submit(client, "/mesh/segment", {"input": file_token}, "网格补全（分段）")

    async def _submit_complete(self, client: httpx.AsyncClient, segment_task_id: str) -> str:
        return await self._submit(client, "/mesh/complete", {"input": segment_task_id}, "网格补全")

    async def _submit_decimate(self, client: httpx.AsyncClient, file_token: str) -> str:
        return await self._submit(client, "/mesh/decimate", {"input": file_token}, "拓扑重构")

    async def _submit(self, client: httpx.AsyncClient, path: str, body: dict[str, Any], label: str) -> str:
        resp = await client.post(f"{api_base()}{path}", headers=headers(), json=body)
        payload = resp.json() if resp.content else {}
        if resp.status_code not in (200, 201, 202) or error_detail(payload):
            detail = error_detail(payload) or resp.status_code
            raise GameItemMeshOpsError("SUBMIT_FAILED", f"提交 Tripo3D {label}任务失败: {detail}")
        task_id = str(((payload or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise GameItemMeshOpsError("SUBMIT_FAILED", f"Tripo3D {label}任务提交后没有返回 task_id: {str(payload)[:300]}")
        return task_id
