# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""动画三件套：骨骼检查、自动绑定、动画重定向（Tripo3D ``/animations/*``）。

三条流水线的输入都是"一个已就绪的 3D 模型"，与网格补全/拓扑重构/贴图
并列，但产出形态不同：

- **骨骼检查**（``rig_check``）：只回答"这个模型能不能绑定、推荐哪种骨架"，
  不产出新模型。运行结果（``riggable`` / ``rig_type``）直接回给前端卡片
  展示，不落素材——所以它是唯一一张不参与「生成实验室流程图」还原的卡片。
- **自动绑定**（``rig``）：给模型加上骨架，产出一个新的 model 素材；产出
  素材会记下这次绑定任务的 ``task_id``，动画重定向要用的正是它。
- **动画重定向**（``retarget``）：把预设动画套到**已绑定的模型**上。文档
  要求 ``input`` 是"绑定任务的 task_id，或之后跑在已绑定模型上的任务
  task_id"，不接受 file_token/URL——所以它的输入素材必须带着
  ``params.tripo_task_id``（自动绑定或贴图等后续任务都会写入这个字段），
  否则直接报参数错误，不去打一次注定失败的真实请求。

三条都是"提交拿 task_id -> 轮询 /tasks/{id}"的异步形状，上传/鉴权/错误
解析复用 ``game_tripo_client.py``。
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

# 与 Tripo3D 文档一致。绑定版本固定用 v3.0：biped 用它更快、质量更好，
# 而且 ``preset:walk`` 这类新预设只在 v3.0 骨架上才有效（见 animations-retarget
# 文档）；非 biped 骨架在 v3.0 下仍然按 v2.5 质量处理，不需要分情况。
RIG_MODEL_VERSION = "v3.0-20260909"
RIG_TYPES = (
    "biped",
    "quadruped",
    "hexapod",
    "octopod",
    "avian",
    "serpentine",
    "aquatic",
    "others",
)
RIG_SPECS = ("tripo", "mixamo", "actorcore", "unity", "unreal", "vrm")
DEFAULT_RIG_TYPE = "biped"
DEFAULT_RIG_SPEC = "tripo"
# v3.0 biped 预设 + 其它骨架的行走/行进预设；后端不强制校验（未来新增预设
# 不该被前端写死的表挡住），这里只作为前端下拉的可选清单与默认值。
ANIMATION_PRESETS = (
    "preset:idle",
    "preset:walk",
    "preset:run",
    "preset:jump",
    "preset:dive",
    "preset:climb",
    "preset:slash",
    "preset:shoot",
    "preset:hurt",
    "preset:fall",
    "preset:turn",
    "preset:quadruped:walk",
    "preset:hexapod:walk",
    "preset:octopod:walk",
    "preset:serpentine:march",
    "preset:aquatic:march",
)
DEFAULT_ANIMATION = "preset:idle"


class GameItemAnimationError(Exception):
    """带 code 的动画处理失败，与 ``GameItemMeshOpsError`` 同一个形状。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RigCheckStatus:
    status: str  # "pending" | "success" | "failed"
    riggable: bool | None = None
    rig_type: str | None = None
    error: str | None = None


@dataclass
class RigStatus:
    status: str
    model_file_path: str | None = None
    error: str | None = None


@dataclass
class RetargetStatus:
    status: str
    model_file_path: str | None = None
    error: str | None = None


def _save_model(model_bytes: bytes, save_dir: str, *, prefix: str, suffix: str = ".glb") -> str:
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{int(time.time())}_{secrets.token_hex(3)}{suffix}"
    dest = out_dir / filename
    dest.write_bytes(model_bytes)
    return str(dest)


class GameItemAnimation:
    """Tripo3D 动画（rig-check / rig / retarget）编排。"""

    async def configured(self) -> bool:
        return tripo_configured()

    # ------------------------------------------------------------------
    # 骨骼检查
    # ------------------------------------------------------------------
    async def submit_rig_check(self, model_path: str) -> str:
        if not tripo_configured():
            raise GameItemAnimationError("NOT_CONFIGURED", "Tripo3D 骨骼检查未配置，请先在设置中配置「3D 分解」。")
        token = await self._upload(model_path, label="骨骼检查")
        return await self._submit("/animations/rig-check", {"input": token}, "骨骼检查")

    async def poll_rig_check(self, task_id: str) -> RigCheckStatus:
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                data = await self._query_task(client, task_id)
        except TRANSIENT_HTTP_ERRORS as exc:
            return RigCheckStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return RigCheckStatus(status="failed", error=f"查询 Tripo3D 骨骼检查任务失败: {exc!r}")
        if isinstance(data, RigCheckStatus):
            return data
        status = str(data.get("status") or "").lower()
        if status in PENDING_STATUSES:
            return RigCheckStatus(status="pending")
        if status != "success":
            detail = str(data.get("error") or data.get("message") or status or "unknown")
            return RigCheckStatus(status="failed", error=f"Tripo3D 骨骼检查任务未成功: {detail}")
        output = data.get("output") or {}
        riggable = output.get("riggable")
        return RigCheckStatus(
            status="success",
            riggable=bool(riggable) if riggable is not None else None,
            rig_type=str(output.get("rig_type") or "") or None,
        )

    # ------------------------------------------------------------------
    # 自动绑定
    # ------------------------------------------------------------------
    async def submit_rig(
        self,
        model_path: str,
        *,
        rig_type: str = DEFAULT_RIG_TYPE,
        spec: str = DEFAULT_RIG_SPEC,
        out_format: str = "glb",
    ) -> str:
        if not tripo_configured():
            raise GameItemAnimationError("NOT_CONFIGURED", "Tripo3D 自动绑定未配置，请先在设置中配置「3D 分解」。")
        token = await self._upload(model_path, label="自动绑定")
        if out_format != "glb":
            raise GameItemAnimationError("INVALID_PARAMS", "实验室只产出 glb 模型（画布缩略图需要 glb）")
        body: dict[str, Any] = {
            "input": token,
            "model": RIG_MODEL_VERSION,
            "rig_type": rig_type if rig_type in RIG_TYPES else DEFAULT_RIG_TYPE,
            "out_format": "glb",
        }
        # spec 只对 biped 生效，其它骨架传了也会被忽略；仍然只在 biped 时
        # 带上，避免请求体里出现无意义字段。
        if body["rig_type"] == "biped":
            body["spec"] = spec if spec in RIG_SPECS else DEFAULT_RIG_SPEC
        return await self._submit("/animations/rig", body, "自动绑定")

    async def poll_rig(self, task_id: str, *, save_dir: str) -> RigStatus:
        return await self._poll_model_task(task_id, save_dir=save_dir, label="自动绑定", prefix="model_rig")

    # ------------------------------------------------------------------
    # 动画重定向
    # ------------------------------------------------------------------
    async def submit_retarget(
        self,
        task_id: str,
        *,
        animation: str = DEFAULT_ANIMATION,
        animate_in_place: bool = False,
        out_format: str = "glb",
    ) -> str:
        if not tripo_configured():
            raise GameItemAnimationError("NOT_CONFIGURED", "Tripo3D 动画重定向未配置，请先在设置中配置「3D 分解」。")
        if not task_id:
            raise GameItemAnimationError("INVALID_PARAMS", "动画重定向需要一个已绑定的模型任务 id")
        if out_format != "glb":
            raise GameItemAnimationError("INVALID_PARAMS", "实验室只产出 glb 模型（画布缩略图需要 glb）")
        body: dict[str, Any] = {
            "input": task_id,
            "animation": animation or DEFAULT_ANIMATION,
            "out_format": "glb",
            "export_with_geometry": True,
            "animate_in_place": bool(animate_in_place),
        }
        return await self._submit("/animations/retarget", body, "动画重定向")

    async def poll_retarget(self, task_id: str, *, save_dir: str) -> RetargetStatus:
        result = await self._poll_model_task(task_id, save_dir=save_dir, label="动画重定向", prefix="model_anim")
        return RetargetStatus(status=result.status, model_file_path=result.model_file_path, error=result.error)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _upload(self, model_path: str, *, label: str) -> str:
        path = Path(model_path)
        if not path.is_file():
            raise GameItemAnimationError("INVALID_PARAMS", f"源模型不存在: {model_path}")
        try:
            # 与网格/贴图同档：模型文件常见 40+MB，上传预算放宽到 300s。
            async with httpx.AsyncClient(timeout=300, verify=get_requests_verify()) as client:
                return await upload_file(client, path)
        except TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemAnimationError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            raise GameItemAnimationError("SUBMIT_FAILED", f"提交 Tripo3D {label}任务失败: {exc!r}") from exc

    async def _submit(self, path: str, body: dict[str, Any], label: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
                resp = await client.post(f"{api_base()}{path}", headers=headers(), json=body)
                payload = resp.json() if resp.content else {}
        except TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemAnimationError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise GameItemAnimationError("SUBMIT_FAILED", f"提交 Tripo3D {label}任务失败: {exc!r}") from exc
        if resp.status_code not in (200, 201, 202) or error_detail(payload):
            detail = error_detail(payload) or resp.status_code
            raise GameItemAnimationError("SUBMIT_FAILED", f"提交 Tripo3D {label}任务失败: {detail}")
        task_id = str(((payload or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise GameItemAnimationError("SUBMIT_FAILED", f"Tripo3D {label}任务提交后没有返回 task_id: {str(payload)[:300]}")
        return task_id

    async def _query_task(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any] | RigCheckStatus:
        resp = await client.get(f"{api_base()}/tasks/{task_id}", headers=headers())
        try:
            payload = resp.json()
        except ValueError:
            return RigCheckStatus(status="failed", error=f"查询 Tripo3D 任务返回了非 JSON 响应: {resp.status_code}")
        if resp.status_code != 200 or error_detail(payload):
            detail = error_detail(payload) or str(resp.status_code)
            return RigCheckStatus(status="failed", error=f"查询 Tripo3D 任务失败: {detail}")
        return (payload or {}).get("data") or {}

    async def _poll_model_task(
        self, task_id: str, *, save_dir: str, label: str, prefix: str
    ) -> RigStatus:
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                data = await self._query_task(client, task_id)
                if isinstance(data, RigCheckStatus):  # 查询本身失败，复用同一个错误形状
                    return RigStatus(status=data.status, error=data.error)
                status = str(data.get("status") or "").lower()
                if status in PENDING_STATUSES:
                    return RigStatus(status="pending")
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return RigStatus(status="failed", error=f"Tripo3D {label}任务未成功: {detail}")
                output = data.get("output") or {}
                model_url = str(output.get("model_url") or "")
                if not model_url:
                    return RigStatus(status="failed", error=f"Tripo3D {label}任务成功但没有返回模型地址: {str(output)[:300]}")
                model_resp = await client.get(model_url, timeout=120)
                if model_resp.status_code != 200:
                    return RigStatus(status="failed", error=f"下载 Tripo3D {label}模型失败: {model_resp.status_code}")
                model_bytes = model_resp.content
        except TRANSIENT_HTTP_ERRORS as exc:
            return RigStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return RigStatus(status="failed", error=f"查询/下载 Tripo3D {label}任务失败: {exc!r}")
        suffix = ".glb"
        return RigStatus(
            status="success",
            model_file_path=_save_model(model_bytes, save_dir, prefix=prefix, suffix=suffix),
        )
