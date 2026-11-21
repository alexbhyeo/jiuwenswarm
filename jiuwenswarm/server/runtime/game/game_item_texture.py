# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""纹理贴图：给一个已存在的 3D 模型重新生成贴图（Tripo3D ``/models/texture``）。

与 ``game_item_mesh_ops.py`` 里的网格补全/拓扑重构并列，输入同样是一个
已就绪的 model/model_part 素材，产出一个带贴图的 ``model`` 素材；区别只是
调用的端点与可选项：

- ``POST {base}/models/texture``，``input`` 用 ``file_token``（先把本地
  .glb 上传到 ``/files``）——文档明确支持 file_token / URL / task_id 三选一。
- ``texture_prompt.text`` 是可选的风格描述；``texture_quality`` 取
  fast/standard/detailed/extreme，默认 standard（与文档默认值一致）。
- ``pbr`` 固定 true、``model`` 版本省略（文档默认 v3.5-20260815），不把
  这些底层参数开放成用户选项——卡片上只需要"贴成什么样"和"多精细"。

单段流水线："提交拿 task_id -> 轮询 /tasks/{id} -> success 后下载模型"，
与拓扑重构完全同构，所以 stage 之类的进程内状态不需要持久化。
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

# 与文档一致的四档贴图质量；不传时用 standard。
TEXTURE_QUALITIES = ("fast", "standard", "detailed", "extreme")
DEFAULT_TEXTURE_QUALITY = "standard"
# 文档里 prompt 的长度/内容没有硬限制，这里只做一个防御性截断，避免把
# 异常长的文本塞进请求体。
_MAX_PROMPT_CHARS = 2000


class GameItemTextureError(Exception):
    """带 code 的贴图失败，与 ``GameItemMeshOpsError`` 同一个形状。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class TextureStatus:
    status: str  # "pending" | "success" | "failed"
    model_file_path: str | None = None
    # Tripo3D 同时返回一张预览图；存下来只作素材元数据，前端缩略图仍然
    # 用 <model-viewer> 直接渲染模型。
    rendered_image_path: str | None = None
    error: str | None = None


def _save_output(data: bytes, save_dir: str, *, prefix: str, suffix: str) -> str:
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{int(time.time())}_{secrets.token_hex(3)}{suffix}"
    dest = out_dir / filename
    dest.write_bytes(data)
    return str(dest)


class GameItemTexture:
    """Tripo3D 纹理贴图编排（单段式）。"""

    async def configured(self) -> bool:
        return tripo_configured()

    async def submit(self, model_path: str, *, prompt: str | None = None, quality: str = DEFAULT_TEXTURE_QUALITY) -> str:
        """上传源模型并提交贴图任务，返回 task_id。"""
        if not tripo_configured():
            raise GameItemTextureError("NOT_CONFIGURED", "Tripo3D 贴图未配置，请先在设置中配置「3D 分解」。")
        path = Path(model_path)
        if not path.is_file():
            raise GameItemTextureError("INVALID_PARAMS", f"源模型不存在: {model_path}")
        normalized_quality = quality if quality in TEXTURE_QUALITIES else DEFAULT_TEXTURE_QUALITY
        text = (prompt or "").strip()[:_MAX_PROMPT_CHARS]

        body: dict[str, Any] = {"pbr": True, "texture_quality": normalized_quality}
        if text:
            body["texture_prompt"] = {"text": text}

        try:
            # 与网格补全/拓扑重构同档：上传的是 40+MB 量级的 .glb，默认
            # 60s/120s 不够用（见 gameApi.ts GAME_MESH_OP_SUBMIT_TIMEOUT_MS）。
            async with httpx.AsyncClient(timeout=300, verify=get_requests_verify()) as client:
                file_token = await upload_file(client, path)
                body["input"] = file_token
                resp = await client.post(f"{api_base()}/models/texture", headers=headers(), json=body)
                payload = resp.json() if resp.content else {}
                if resp.status_code not in (200, 201, 202) or error_detail(payload):
                    detail = error_detail(payload) or resp.status_code
                    raise GameItemTextureError("SUBMIT_FAILED", f"提交 Tripo3D 贴图任务失败: {detail}")
                task_id = str(((payload or {}).get("data") or {}).get("task_id") or "")
                if not task_id:
                    raise GameItemTextureError(
                        "SUBMIT_FAILED", f"Tripo3D 贴图任务提交后没有返回 task_id: {str(payload)[:300]}"
                    )
        except TRANSIENT_HTTP_ERRORS as exc:
            raise GameItemTextureError("UNREACHABLE", f"连接 Tripo3D 失败: {exc!r}") from exc
        except GameItemTextureError:
            raise
        except (httpx.HTTPError, RuntimeError) as exc:
            raise GameItemTextureError("SUBMIT_FAILED", f"提交 Tripo3D 贴图任务失败: {exc!r}") from exc
        return task_id

    async def poll(self, task_id: str, *, save_dir: str) -> TextureStatus:
        try:
            async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
                resp = await client.get(f"{api_base()}/tasks/{task_id}", headers=headers())
                try:
                    payload = resp.json()
                except ValueError:
                    return TextureStatus(
                        status="failed", error=f"查询 Tripo3D 任务返回了非 JSON 响应: {resp.status_code}"
                    )
                if resp.status_code != 200 or error_detail(payload):
                    detail = error_detail(payload) or str(resp.status_code)
                    return TextureStatus(status="failed", error=f"查询 Tripo3D 任务失败: {detail}")
                data = (payload or {}).get("data") or {}
                status = str(data.get("status") or "").lower()
                if status in PENDING_STATUSES:
                    return TextureStatus(status="pending")
                if status != "success":
                    detail = str(data.get("error") or data.get("message") or status or "unknown")
                    return TextureStatus(status="failed", error=f"Tripo3D 贴图任务未成功: {detail}")

                output = data.get("output") or {}
                model_url = str(output.get("model_url") or "")
                if not model_url:
                    return TextureStatus(
                        status="failed",
                        error=f"Tripo3D 贴图任务成功但没有返回模型地址: {str(output)[:300]}",
                    )
                model_resp = await client.get(model_url, timeout=120)
                if model_resp.status_code != 200:
                    return TextureStatus(
                        status="failed", error=f"下载 Tripo3D 贴图模型失败: {model_resp.status_code}"
                    )
                model_bytes = model_resp.content

                rendered_path: str | None = None
                rendered_url = str(output.get("rendered_image_url") or "")
                if rendered_url:
                    try:
                        image_resp = await client.get(rendered_url, timeout=60)
                        if image_resp.status_code == 200:
                            suffix = Path(rendered_url.split("?", 1)[0]).suffix or ".png"
                            rendered_path = _save_output(
                                image_resp.content, save_dir, prefix="texture_preview", suffix=suffix
                            )
                    except TRANSIENT_HTTP_ERRORS:
                        # 预览图只是元数据，下载失败不影响交付模型。
                        logger.warning("[game] 下载贴图预览图失败，忽略: %s", rendered_url)
        except TRANSIENT_HTTP_ERRORS as exc:
            return TextureStatus(status="pending", error=f"轮询暂时失败，稍后重试: {exc!r}")
        except httpx.HTTPError as exc:
            return TextureStatus(status="failed", error=f"查询/下载 Tripo3D 贴图任务失败: {exc!r}")

        return TextureStatus(
            status="success",
            model_file_path=_save_output(model_bytes, save_dir, prefix="model_texture", suffix=".glb"),
            rendered_image_path=rendered_path,
        )
