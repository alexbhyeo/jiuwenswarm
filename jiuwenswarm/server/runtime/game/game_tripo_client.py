# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tripo3D 接入的共享基础设施：开关/鉴权/上传/错误解析。

分解、2D 转 3D（``game_item_decompose.py``）与网格补全、拓扑重构
（``game_item_mesh_ops.py``）四条流水线都要调 Tripo3D，且都要先把一个本地
文件（图片或模型）上传成 ``file_token``、都用同一套环境变量鉴权、都是
"提交拿 task_id -> 轮询 /tasks/{id} -> 按 code/status 判断成败"的形状，所以
这些边角料抽到这一个模块，四条流水线共用，不各自再写一遍。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

_API_BASE_DEFAULT = "https://openapi.tripo3d.ai/v3"
PENDING_STATUSES = ("queued", "running")
TRANSIENT_HTTP_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


def tripo_enabled() -> bool:
    """设置页「3D 分解」开关是否打开，与 VIDEO_ENABLED 等既有开关同一套取舍。"""
    return str(os.environ.get("TRIPO_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def tripo_configured() -> bool:
    """3D 分解/模型处理是否可用：开关打开且 key/base 都齐全。"""
    return bool(tripo_enabled() and os.environ.get("TRIPO_API_KEY", "").strip() and api_base())


def api_base() -> str:
    return (os.environ.get("TRIPO_API_BASE", "").strip() or _API_BASE_DEFAULT).rstrip("/")


def headers() -> dict[str, str]:
    api_key = os.environ.get("TRIPO_API_KEY", "").strip()
    return {"Authorization": f"Bearer {api_key}"}


def error_detail(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if code in (None, 0):
        return None
    message = (payload.get("message") or payload.get("msg") or "").strip()
    return f"{code}: {message}" if message else str(code)


async def upload_file(client: httpx.AsyncClient, path: Path) -> str:
    """上传本地文件（图片或模型，Tripo3D 的 ``/files`` 端点不区分）换
    ``file_token``。"""
    data = path.read_bytes()
    files = {"file": (path.name, data)}
    resp = await client.post(f"{api_base()}/files", headers=headers(), files=files)
    payload = resp.json() if resp.content else {}
    if resp.status_code != 200 or error_detail(payload):
        detail = error_detail(payload) or resp.status_code
        raise RuntimeError(f"上传文件到 Tripo3D 失败: {detail}")
    file_token = str(((payload or {}).get("data") or {}).get("file_token") or "")
    if not file_token:
        raise RuntimeError(f"上传文件到 Tripo3D 没有返回 file_token: {str(payload)[:300]}")
    return file_token
