# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""素材分解与 2D 转 3D：两条都走 Tripo3D，但调的是不同端点，产物形状也
不同（见 D-20261116-1047-game-workshop-lab D-04/D-06，以及后续「实验室
圆形工具栏」需求里新增的 2D 转 3D）。

- **分解**（``submit``/``poll``）：把一张图片经 ``mesh/smartsegment`` 分解——
  该端点核实下来只返回一个合并的 ``.glb``（单个 ``model_url``），不是多个
  独立文件，"一张图 → N 个独立部件"里"拆成 N 个文件"这一半是本模块自己
  做的：下载这一个合并模型，按 glTF 场景图的顶层节点拆成多份独立的
  ``.glb``（见 ``_split_glb``）。
- **2D 转 3D**（``submit_to_model``/``poll_to_model``）：把一张图片经
  ``generation/image-to-model`` 转成**一个**单体模型——没有"多个部件"这
  一说，产物就是直接下载保存的那一个 ``.glb``，不需要 trimesh 拆分。

两条流水线共用上传/鉴权，这部分（以及网格补全、拓扑重构也要用到的同一套
上传/鉴权）已经抽到 ``game_tripo_client.py``；分解与 2D 转 3D 本身仍然放在
同一个 ``GameItemDecompose`` 类里，没有分成两个类。

与导演模式的 ``generate_video``/``check_video_status`` 同构：提交
（``submit``/``submit_to_model``）只拿一个服务商任务 id，真正的生成仍要
轮询（``poll``/``poll_to_model``）——Tripo3D 任务时长未知，不适合在一次
RPC 里阻塞等到底。
"""

from __future__ import annotations

import logging
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    PENDING_STATUSES as _PENDING_STATUSES,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    TRANSIENT_HTTP_ERRORS as _TRANSIENT_HTTP_ERRORS,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    api_base as _api_base,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    error_detail as _error_detail,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    headers as _headers,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    tripo_configured,
)
from jiuwenswarm.server.runtime.game.game_tripo_client import (
    upload_file as _upload_file,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5
# image-to-model 的 model 参数是必填项；固定用文档推荐的最新版本，不开放
# 成用户可选参数——多一个下拉就多一种"选了不存在的版本号"的失败模式，
# 而这个功能目前没有谁会需要切版本。
_TO_MODEL_VERSION = "v3.1-20260211"


class GameItemDecomposeError(Exception):
    """带 code 的分解失败，与游戏模式其它处理器的错误形状一致
    （见 ``game_manager.GameRpcError``）。调用方据此转成 RPC 错误响应，
    不需要再分别处理网络异常/glTF 解析异常等具体类型。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class DecomposedPart:
    name: str
    file_path: str
    rendered_image_path: str | None = None


@dataclass
class DecomposeStatus:
    status: str  # "pending" | "success" | "failed"
    parts: list[DecomposedPart] = field(default_factory=list)
    error: str | None = None


@dataclass
class ToModelStatus:
    status: str  # "pending" | "success" | "failed"
    model_file_path: str | None = None
    error: str | None = None


class GameItemDecompose:
    """Tripo3D 分解编排：上传图片 -> 提交 ``mesh/smartsegment`` -> 轮询 ->
    下载合并模型 -> 拆分成 N 个独立 ``.glb`` 文件。"""

    async def configured(self) -> bool:
        return tripo_configured()

    async def submit(self, image_path: str, *, hint: str | None = None, granularity: str = "medium") -> str:
        """上传源图并提交分解任务，返回 Tripo3D 的 task_id。"""
        if not tripo_configured():
            raise GameItemDecomposeError(
                "NOT_CONFIGURED", "Tripo3D 3D 分解未配置，请先在设置中配置「3D 分解」。"
            )
        path = Path(image_path)
        if not path.is_file():
            raise GameItemDecomposeError("INVALID_PARAMS", f"源图不存在: {image_path}")

        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                file_token = await self._upload_image(client, path)
                task_id = await self._submit_smartsegment(client, file_token, hint, granularity)
        except _TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemDecomposeError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise GameItemDecomposeError("SUBMIT_FAILED", f"提交 Tripo3D 分解任务失败: {exc!r}") from exc
        return task_id

    async def poll(self, task_id: str, *, save_dir: str) -> DecomposeStatus:
        """查询分解任务状态；成功时下载合并模型并拆分，失败时返回错误详情。"""
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                resp = await client.get(f"{_api_base()}/tasks/{task_id}", headers=_headers())
                try:
                    payload = resp.json()
                except ValueError:
                    return DecomposeStatus(
                        status="failed",
                        error=f"查询 Tripo3D 任务返回了非 JSON 响应: {resp.status_code}",
                    )
                if resp.status_code != 200 or _error_detail(payload):
                    detail = _error_detail(payload) or str(resp.status_code)
                    return DecomposeStatus(status="failed", error=f"查询 Tripo3D 任务失败: {detail}")
                data = (payload or {}).get("data") or {}
                status = str(data.get("status") or "").lower()
                if status in _PENDING_STATUSES:
                    return DecomposeStatus(status="pending")
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return DecomposeStatus(status="failed", error=f"Tripo3D 分解任务未成功: {detail}")

                output = data.get("output") or {}
                model_url = str(output.get("seg_model_url") or output.get("model_url") or "")
                if not model_url:
                    return DecomposeStatus(
                        status="failed",
                        error=f"Tripo3D 分解任务成功但没有返回模型地址: {str(output)[:300]}",
                    )
                part_prompt = str(output.get("prompt") or "")

                merged_resp = await client.get(model_url, timeout=120)
                if merged_resp.status_code != 200:
                    return DecomposeStatus(
                        status="failed",
                        error=f"下载 Tripo3D 分解模型失败: {merged_resp.status_code}",
                    )
                merged_bytes = merged_resp.content
        except _TRANSIENT_HTTP_ERRORS as exc:
            return DecomposeStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return DecomposeStatus(status="failed", error=f"查询/下载 Tripo3D 分解任务失败: {exc!r}")

        try:
            parts = _split_glb(merged_bytes, part_prompt, save_dir)
        except Exception as exc:  # noqa: BLE001 - glTF 解析失败需要明确报错，不留半成品
            logger.exception("[game] glTF 拆分失败 (task_id=%s)", task_id)
            return DecomposeStatus(status="failed", error=f"拆分分解模型失败: {exc!r}")

        if not parts:
            return DecomposeStatus(
                status="failed",
                error="分解模型没有可拆分的部件（合并模型只有一个整体网格，未能按节点拆分）。",
            )
        return DecomposeStatus(status="success", parts=parts)

    async def submit_to_model(self, image_path: str) -> str:
        """上传源图并提交 2D 转 3D（单体模型）任务，返回 Tripo3D 的 task_id。"""
        if not tripo_configured():
            raise GameItemDecomposeError(
                "NOT_CONFIGURED", "Tripo3D 2D 转 3D 未配置，请先在设置中配置「3D 分解」。"
            )
        path = Path(image_path)
        if not path.is_file():
            raise GameItemDecomposeError("INVALID_PARAMS", f"源图不存在: {image_path}")

        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                file_token = await self._upload_image(client, path)
                task_id = await self._submit_image_to_model(client, file_token)
        except _TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemDecomposeError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise GameItemDecomposeError("SUBMIT_FAILED", f"提交 Tripo3D 2D 转 3D 任务失败: {exc!r}") from exc
        return task_id

    async def poll_to_model(self, task_id: str, *, save_dir: str) -> ToModelStatus:
        """查询 2D 转 3D 任务状态；成功时下载单体模型，失败时返回错误详情。"""
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                resp = await client.get(f"{_api_base()}/tasks/{task_id}", headers=_headers())
                try:
                    payload = resp.json()
                except ValueError:
                    return ToModelStatus(
                        status="failed",
                        error=f"查询 Tripo3D 任务返回了非 JSON 响应: {resp.status_code}",
                    )
                if resp.status_code != 200 or _error_detail(payload):
                    detail = _error_detail(payload) or str(resp.status_code)
                    return ToModelStatus(status="failed", error=f"查询 Tripo3D 任务失败: {detail}")
                data = (payload or {}).get("data") or {}
                status = str(data.get("status") or "").lower()
                if status in _PENDING_STATUSES:
                    return ToModelStatus(status="pending")
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return ToModelStatus(status="failed", error=f"Tripo3D 2D 转 3D 任务未成功: {detail}")

                output = data.get("output") or {}
                model_url = str(output.get("model_url") or "")
                if not model_url:
                    return ToModelStatus(
                        status="failed",
                        error=f"Tripo3D 2D 转 3D 任务成功但没有返回模型地址: {str(output)[:300]}",
                    )

                model_resp = await client.get(model_url, timeout=120)
                if model_resp.status_code != 200:
                    return ToModelStatus(
                        status="failed",
                        error=f"下载 Tripo3D 2D 转 3D 模型失败: {model_resp.status_code}",
                    )
                model_bytes = model_resp.content
        except _TRANSIENT_HTTP_ERRORS as exc:
            return ToModelStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return ToModelStatus(status="failed", error=f"查询/下载 Tripo3D 2D 转 3D 任务失败: {exc!r}")

        out_dir = Path(save_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"model_{int(time.time())}_{secrets.token_hex(3)}.glb"
        dest = out_dir / filename
        dest.write_bytes(model_bytes)
        return ToModelStatus(status="success", model_file_path=str(dest))

    async def _submit_image_to_model(self, client: httpx.AsyncClient, file_token: str) -> str:
        # 与 smartsegment 一致：input 是裸字符串（file_token），不是对象
        # 包装；这次直接照文档实现，文档对这个端点的请求体给得很明确。
        body: dict[str, Any] = {"input": file_token, "model": _TO_MODEL_VERSION}
        resp = await client.post(f"{_api_base()}/generation/image-to-model", headers=_headers(), json=body)
        payload = resp.json() if resp.content else {}
        if resp.status_code not in (200, 201, 202) or _error_detail(payload):
            detail = _error_detail(payload) or resp.status_code
            raise GameItemDecomposeError("SUBMIT_FAILED", f"提交 Tripo3D 2D 转 3D 任务失败: {detail}")
        task_id = str(((payload or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise GameItemDecomposeError(
                "SUBMIT_FAILED", f"Tripo3D 2D 转 3D 任务提交后没有返回 task_id: {str(payload)[:300]}"
            )
        return task_id

    async def _upload_image(self, client: httpx.AsyncClient, path: Path) -> str:
        try:
            return await _upload_file(client, path)
        except RuntimeError as exc:
            raise GameItemDecomposeError("SUBMIT_FAILED", str(exc)) from exc

    async def _submit_smartsegment(
        self, client: httpx.AsyncClient, file_token: str, hint: str | None, granularity: str
    ) -> str:
        body: dict[str, Any] = {
            "seg_type": "image",
            # 实测确认：smartsegment 的 input 要求是一个裸字符串（直接是
            # file_token），不是其它端点那种 {"file_token": ...} 对象包装；
            # 传对象会被服务端报 1003 反序列化失败。
            "input": file_token,
            "granularity": granularity if granularity in ("coarse", "medium", "fine") else "medium",
        }
        if hint and hint.strip():
            body["hint"] = hint.strip()
        resp = await client.post(f"{_api_base()}/mesh/smartsegment", headers=_headers(), json=body)
        payload = resp.json() if resp.content else {}
        if resp.status_code not in (200, 201, 202) or _error_detail(payload):
            detail = _error_detail(payload) or resp.status_code
            raise GameItemDecomposeError("SUBMIT_FAILED", f"提交 Tripo3D 分解任务失败: {detail}")
        task_id = str(((payload or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise GameItemDecomposeError(
                "SUBMIT_FAILED", f"Tripo3D 分解任务提交后没有返回 task_id: {str(payload)[:300]}"
            )
        return task_id


def _part_names(prompt: str, count: int) -> list[str]:
    """把 ``prompt`` 字段（逗号分隔的部件名称，如 "head, body, legs"）拆成
    跟节点数量一致的命名列表；数量对不上就整体放弃，调用方按顺序编号兜底
    （见 A1：这个对应关系没有文档保证，只是尽量而为）。"""
    candidates = [part.strip() for part in re.split(r"[,，]", prompt) if part.strip()]
    if len(candidates) == count:
        return candidates
    return [f"part_{i + 1}" for i in range(count)]


def _split_glb(merged_bytes: bytes, part_prompt: str, save_dir: str) -> list[DecomposedPart]:
    """把一个合并的 glTF 场景按顶层节点拆成多份独立 ``.glb``。

    用 ``trimesh`` 而不是手写 glTF 缓冲区/accessor 重映射——分割类端点的
    产物在 trimesh 眼里就是"一个场景，多个具名几何体"，``scene.geometry``
    直接给出每个部件的网格，导出单个部件就是普通的 ``export(path)``，不需要
    自己处理 buffer/accessor 的裁剪与重新编号。
    """
    import trimesh  # pylint: disable=import-outside-toplevel  # 仅此一处用到，延迟导入

    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp.write(merged_bytes)
        tmp_path = Path(tmp.name)
    try:
        loaded = trimesh.load(tmp_path, file_type="glb", force="scene", process=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not isinstance(loaded, trimesh.Scene) or not loaded.geometry:
        # 只有一个整体网格、没有分出多个具名几何体——合并模型本身没有按部件
        # 切开，调用方把这种情况当"没有可拆分的部件"处理。
        return []

    node_names = list(loaded.geometry.keys())
    labels = _part_names(part_prompt, len(node_names))
    stamp = int(time.time())
    parts: list[DecomposedPart] = []
    for index, (node_name, label) in enumerate(zip(node_names, labels)):
        geometry = loaded.geometry[node_name]
        part_scene = trimesh.Scene(geometry)
        safe_label = re.sub(r"[^a-zA-Z0-9_一-鿿-]+", "_", label).strip("_") or f"part_{index + 1}"
        filename = f"model_part_{stamp}_{index}_{secrets.token_hex(3)}_{safe_label}.glb"
        dest = out_dir / filename
        part_scene.export(dest, file_type="glb")
        parts.append(DecomposedPart(name=label, file_path=str(dest)))
    return parts
