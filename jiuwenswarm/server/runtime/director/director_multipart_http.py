# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``/file-api/director/upload`` multipart 上传处理.

让用户把本地图片/视频直接导入某个项目的"素材"，不经过 generate_video /
generate_visual —— 与 skills 的 upload 端点同构（见
server/runtime/skill/skills_multipart_http.py），复用其 multipart 解析，
本地直接落盘 + 写 DirectorStore，不经过 AgentServer WS 往返。
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.director.director_store import (
    DirectorAsset,
    DirectorStore,
    get_project_assets_dir,
)
from jiuwenswarm.server.runtime.skill.skills_multipart_http import parse_multipart_form

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
_ERROR_STATUS: dict[str, int] = {
    "INVALID_PARAMS": 400,
    "PROJECT_NOT_FOUND": 404,
    "INTERNAL_ERROR": 500,
}

# app_web.py's HTTP handler serves requests on separate threads, so two
# uploads landing close together each build their own DirectorStore()
# (load-modify-save, no shared instance) and can race on
# director_state.json - the second save silently drops the first upload's
# append. This lock serializes the read-modify-write section per process,
# mirroring DirectorManager's asyncio.Lock for the WS RPC path (a different
# lock is required here since this handler is synchronous/threaded, not
# asyncio).
_UPLOAD_LOCK = threading.Lock()


def _error_body(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message, "error": message}


def handle_director_asset_upload_http(*, content_type: str, body: bytes) -> tuple[int, dict[str, Any]]:
    """处理 ``POST /file-api/director/upload``，返回 (status, json_body).

    表单字段：project_id（必填）、file（必填，图片或视频）、asset_type
    （可选："image" | "video" | "character"）。角色素材本质上也是一张
    图片，文件扩展名本身分不出"这是一张普通图片还是角色参考图"——前端从
    "素材 · 角色"分类上传时显式传 asset_type=character 来区分；不传时
    沿用原来按扩展名推断 image/video 的行为，兼容既有调用方。
    """
    try:
        fields = parse_multipart_form(content_type, body)
    except Exception as exc:  # noqa: BLE001 - 转成统一的 400 响应
        return 400, _error_body("INVALID_PARAMS", f"无法解析上传表单: {exc}")

    project_id = str(fields.get("project_id") or "").strip()
    if not project_id:
        return _ERROR_STATUS["INVALID_PARAMS"], _error_body("INVALID_PARAMS", "缺少 project_id")

    file_field = fields.get("file")
    if not isinstance(file_field, dict) or not isinstance(file_field.get("content"), (bytes, bytearray)):
        return _ERROR_STATUS["INVALID_PARAMS"], _error_body("INVALID_PARAMS", "缺少 file 字段")

    filename = str(file_field.get("filename") or "upload.bin")
    ext = Path(filename).suffix.lower()
    if ext in _ALLOWED_IMAGE_EXT:
        inferred_type = "image"
    elif ext in _ALLOWED_VIDEO_EXT:
        inferred_type = "video"
    else:
        allowed = ", ".join(sorted(_ALLOWED_IMAGE_EXT | _ALLOWED_VIDEO_EXT))
        return _ERROR_STATUS["INVALID_PARAMS"], _error_body(
            "INVALID_PARAMS", f"不支持的文件类型: {ext or '(无扩展名)'}；仅支持 {allowed}"
        )

    requested_type = str(fields.get("asset_type") or "").strip()
    if requested_type == "character":
        if inferred_type != "image":
            return _ERROR_STATUS["INVALID_PARAMS"], _error_body(
                "INVALID_PARAMS", "角色素材只能上传图片文件"
            )
        asset_type = "character"
    elif requested_type in ("image", "video") and requested_type != inferred_type:
        return _ERROR_STATUS["INVALID_PARAMS"], _error_body(
            "INVALID_PARAMS", f"文件扩展名 {ext} 与所选分类（{requested_type}）不匹配"
        )
    else:
        asset_type = inferred_type

    content = bytes(file_field["content"])
    # 以原始文件名（去扩展名）作为素材的默认展示名——上传的素材一般本来就有
    # 有意义的文件名，不必强制用户再手动重命名一次才能用 "@名称" 引用。
    display_name = Path(filename).stem.strip() or None

    with _UPLOAD_LOCK:
        store = DirectorStore()
        project = store.get_project(project_id)
        if project is None:
            return _ERROR_STATUS["PROJECT_NOT_FOUND"], _error_body("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")

        save_dir = get_project_assets_dir(project_id)
        dest = save_dir / f"upload_{secrets.token_hex(8)}{ext}"
        try:
            dest.write_bytes(content)
        except OSError as exc:
            logger.exception("[director_multipart_http] 保存上传文件失败: %s", dest)
            return _ERROR_STATUS["INTERNAL_ERROR"], _error_body("INTERNAL_ERROR", f"保存文件失败: {exc}")

        asset = DirectorAsset(
            asset_id=f"asset_{secrets.token_hex(4)}",
            type=asset_type,
            status="ready",
            prompt="",
            params={"source": "upload", "original_filename": filename},
            file_path=str(dest),
            name=display_name,
        )
        project = store.append_asset(project_id, asset)
        return 200, {
            "project": project.to_dict(),
            "asset_id": asset.asset_id,
            "asset_counts": store.asset_counts(),
        }
