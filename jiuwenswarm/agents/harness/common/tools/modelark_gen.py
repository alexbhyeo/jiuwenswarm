# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""BytePlus ModelArk (Volcengine Ark) image / video generation backends.

ModelArk serves Seedream (images) and Seedance (video) through its own Ark API
rather than OpenRouter's, so the OpenRouter-style path in visual_gen_tools /
video_gen_tools cannot drive it:

- Seedream: ``POST {base}/images/generations`` (synchronous) with
  ``{model, prompt, size, response_format, watermark}`` returning
  ``data[].b64_json`` / ``data[].url``.
- Seedance: ``POST {base}/contents/generations/tasks`` with a ``content`` array
  plus ``resolution`` / ``ratio`` / ``duration`` / ``generate_audio`` /
  ``watermark`` (returns ``id``), then ``GET .../tasks/{id}`` until ``status``
  is ``succeeded`` (``content.video_url``) or a failure status (``error``).

``{base}`` is the configured API URL, e.g. ``https://ark.ap-southeast.bytepluses.com/api/v3``
(BytePlus international) or ``https://ark.cn-beijing.volces.com/api/v3``
(Volcengine China). Keys and model activation are region-bound, so a key that
is rejected on the configured host is retried on the other known hosts before
being reported as bad. A model the account has not activated comes back as
``ModelNotOpen``; that is surfaced with the console step needed to fix it.

Return strings follow the same conventions as the other backends
("[ERROR]: ...", "Saved to: ...", "Video job {id} submitted and still ...").
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

_KNOWN_BASES = (
    "https://ark.ap-southeast.bytepluses.com/api/v3",
    "https://ark.eu-west.bytepluses.com/api/v3",
    "https://ark.cn-beijing.volces.com/api/v3",
)
_HOST = re.compile(r"^ark\.[\w-]+\.(bytepluses\.com|volces\.com)$", re.IGNORECASE)
_VIDEO_RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}
_VIDEO_RESOLUTIONS = {"480p", "720p", "1080p"}
_VIDEO_MIN_SECONDS = 4
_VIDEO_MAX_SECONDS = 15
_POLL_INTERVAL_SECONDS = 10
_MAX_POLL_SECONDS = 300
_PENDING_STATUSES = ("queued", "running")


def is_modelark(protocol_env: str, api_base: str) -> bool:
    """Whether the configured slot should use the ModelArk backend: the saved
    协议 is "modelark", or the API URL is an Ark host."""
    if os.environ.get(protocol_env, "").strip().lower() == "modelark":
        return True
    host = re.sub(r"^https?://", "", api_base.strip(), flags=re.IGNORECASE).split("/", 1)[0].split(":", 1)[0]
    return bool(_HOST.match(host))


def _normalize_base(api_base: str) -> str:
    base = api_base.strip().rstrip("/")
    return base if re.search(r"/api/v\d+$", base) else f"{base}/api/v3"


def _candidate_bases(api_base: str) -> list[str]:
    primary = _normalize_base(api_base)
    if primary not in _KNOWN_BASES:
        return [primary]
    return [primary, *[base for base in _KNOWN_BASES if base != primary]]


# Account-side failures: nothing the agent can change by retrying, switching
# models or searching config files for another key - it should tell the user.
_ACCOUNT_HINTS = {
    "ModelNotOpen": (
        "Activate this model for your account in the ModelArk console "
        "(https://console.byteplus.com/ark, Model activation) and try again."
    ),
    "SetLimitExceeded": (
        "The account's usage limit for this model has been reached (Safe Experience Mode / Free Credits Only "
        "Mode pauses the model once the free quota runs out). On the Model Activation page of the ModelArk "
        "console (https://console.byteplus.com/ark) adjust or close \"Safe Experience Mode\" (or add credits) "
        "and try again."
    ),
}


def _account_hint(code: str) -> str:
    hint = _ACCOUNT_HINTS.get(code)
    return f" -> {hint} This is an account-side problem: report it to the user; retrying or searching config files for another key will not help." if hint else ""


def _error_detail(payload: Any) -> str | None:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        code = str(err.get("code", "error"))
        return f"{code}: {err.get('message', '')}".strip() + _account_hint(code)
    return None


def _is_auth_failure(http_status: int, payload: Any) -> bool:
    """A request that failed because of the key or the region it was sent to:
    a rejected key (401 / AuthenticationError), or "model not found" - which is
    how a host answers when the key/model belongs to a different region."""
    if http_status == 401:
        return True
    err = payload.get("error") if isinstance(payload, dict) else None
    code = str(err.get("code", "")) if isinstance(err, dict) else ""
    return code.startswith("Authentication") or code == "InvalidEndpointOrModel.NotFound"


def _auth_failure_message(bases: list[str], detail: str) -> str:
    hosts = ", ".join(re.sub(r"^https?://|/api/v\d+$", "", base) for base in bases)
    return (
        f"[ERROR]: ModelArk request failed on every region host tried ({hosts}): {detail}. Check that the API key "
        "is valid, that key and model belong to one of these regions, and that the model name is correct and "
        "activated for your account. Update the settings in Settings > Agent (Image/Video generation). "
        "Do not search other config files for a different key."
    )


def _save_path(save_dir: str | None, default_sub: str, filename: str) -> Path:
    root = Path(save_dir).expanduser() if save_dir else (get_agent_workspace_dir() / default_sub)
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


async def _post(
    client: httpx.AsyncClient, bases: list[str], path: str, headers: dict[str, str], body: dict[str, Any], label: str
) -> tuple[str, httpx.Response | None, Any, str | None]:
    """POST to the first base that accepts the key. Returns (base, response,
    payload, error); a non-None error is the finished tool result."""
    resp: httpx.Response | None = None
    payload: Any = None
    base = bases[0]
    for index, candidate in enumerate(bases):
        base = candidate
        logger.info("[%s] ModelArk POST %s%s", label, base, path)
        resp = await client.post(f"{base}{path}", headers=headers, json=body)
        try:
            payload = resp.json()
        except ValueError:
            return base, resp, None, f"[ERROR]: ModelArk returned a non-JSON response: {resp.status_code} {resp.text[:300]}"
        if _is_auth_failure(resp.status_code, payload):
            if index + 1 < len(bases):
                logger.warning("[%s] ModelArk rejected the key on %s, trying %s", label, base, bases[index + 1])
                continue
            return base, resp, payload, _auth_failure_message(bases, _error_detail(payload) or str(resp.status_code))
        break
    return base, resp, payload, None


async def generate_image(
    api_key: str,
    api_base: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    save_dir: str | None,
) -> str:
    body = {
        "model": model,
        # Seedream takes a size tier rather than an aspect ratio, so the ratio is stated in the
        # prompt. "2k" is the one tier every Seedream 5.x model accepts (the lite model rejects
        # "1K": size must be WIDTHxHEIGHT, 2k, 3k or 4k), so it is used regardless of the
        # tool's resolution argument.
        "prompt": f"{prompt}\n\n(Aspect ratio: {aspect_ratio})",
        "size": "2k",
        "response_format": "b64_json",
        "watermark": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=180, verify=get_requests_verify()) as client:
            _, resp, payload, error = await _post(
                client, _candidate_bases(api_base), "/images/generations", headers, body, "generate_visual"
            )
            if error:
                return error
            if resp is None or resp.status_code != 200 or _error_detail(payload):
                return f"[ERROR]: ModelArk image generation failed: {_error_detail(payload) or (resp.status_code if resp else 'no response')}"
            items = (payload or {}).get("data") or []
            saved: list[str] = []
            for index, item in enumerate(items):
                if item.get("b64_json"):
                    data = base64.b64decode(item["b64_json"])
                elif item.get("url"):
                    download = await client.get(item["url"], follow_redirects=True, timeout=120)
                    if download.status_code != 200:
                        return f"[ERROR]: ModelArk image was generated but downloading it failed: {download.status_code}"
                    data = download.content
                else:
                    continue
                target = _save_path(
                    save_dir, "generated_images", f"image_{int(time.time())}_{index}_{secrets.token_hex(4)}.{_image_extension(data)}"
                )
                target.write_bytes(data)
                saved.append(str(target))
    except httpx.HTTPError as exc:
        return f"[ERROR]: ModelArk image generation request failed: {exc!r}"
    except (OSError, ValueError) as exc:
        return f"[ERROR]: failed to save ModelArk image: {exc!r}"
    if not saved:
        return f"[ERROR]: ModelArk returned no images. Response: {str(payload)[:300]}"
    return "Image generated successfully!\nSaved to: " + ", ".join(saved)


async def submit_video(
    api_key: str,
    api_base: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int,
    generate_audio: bool,
    first_frame_data_uri: str | None,
    save_dir: str | None,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if first_frame_data_uri:
        content.append({"type": "image_url", "image_url": {"url": first_frame_data_uri}, "role": "first_frame"})
    res = (resolution or "").strip().lower()
    body = {
        "model": model,
        "content": content,
        "resolution": res if res in _VIDEO_RESOLUTIONS else "720p",
        # With a first frame the output must follow that frame's aspect ratio.
        "ratio": "adaptive" if first_frame_data_uri else (aspect_ratio if aspect_ratio in _VIDEO_RATIOS else "16:9"),
        "duration": max(_VIDEO_MIN_SECONDS, min(_VIDEO_MAX_SECONDS, int(duration_seconds))),
        "generate_audio": bool(generate_audio),
        "watermark": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
            base, resp, payload, error = await _post(
                client, _candidate_bases(api_base), "/contents/generations/tasks", headers, body, "generate_video"
            )
            if error:
                return error
            if resp is None or resp.status_code not in (200, 201, 202) or _error_detail(payload):
                return f"[ERROR]: ModelArk video generation submit failed: {_error_detail(payload) or (resp.status_code if resp else 'no response')}"
            task_id = str((payload or {}).get("id") or "")
            if not task_id:
                return f"[ERROR]: ModelArk video submit returned no task id: {str(payload)[:300]}"

            status, task, elapsed = "queued", {}, 0
            while status in _PENDING_STATUSES and elapsed < _MAX_POLL_SECONDS:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                elapsed += _POLL_INTERVAL_SECONDS
                status, task, query_error = await _query(client, base, headers, task_id)
                if query_error:
                    return query_error
            if status in _PENDING_STATUSES:
                return (
                    f"Video job {task_id} submitted and still {status} after {elapsed}s - generation can "
                    f"take several minutes. Call check_video_status with job_id={task_id} to check progress "
                    "and download it once ready (call the tool again to wait; do not use shell sleep)."
                )
            return await _finish(client, task_id, status, task, save_dir)
    except httpx.HTTPError as exc:
        return f"[ERROR]: ModelArk video generation request failed: {exc!r}"


async def _query(
    client: httpx.AsyncClient, base: str, headers: dict[str, str], task_id: str
) -> tuple[str, dict[str, Any], str | None]:
    resp = await client.get(f"{base}/contents/generations/tasks/{task_id}", headers=headers)
    try:
        payload = resp.json()
    except ValueError:
        return "", {}, f"[ERROR]: polling ModelArk video job {task_id} returned a non-JSON response: {resp.status_code}"
    if _is_auth_failure(resp.status_code, payload):
        return "auth_failed", {}, _error_detail(payload) or str(resp.status_code)
    if resp.status_code != 200:
        return "", {}, f"[ERROR]: polling ModelArk video job {task_id} failed: {_error_detail(payload) or resp.status_code}"
    return str(payload.get("status") or "").lower(), payload, None


async def _finish(client: httpx.AsyncClient, task_id: str, status: str, task: dict[str, Any], save_dir: str | None) -> str:
    if status != "succeeded":
        error = task.get("error") or {}
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            detail = f"{code} {error.get('message', '')}".strip() + _account_hint(code)
        else:
            detail = str(error)
        return f"[ERROR]: video job {task_id} ended with status {status}: {detail or 'no error detail provided'}"
    url = ((task.get("content") or {}).get("video_url")) or ""
    if not url:
        return f"[ERROR]: video job {task_id} succeeded but returned no video URL."
    try:
        # The download URL is pre-signed - it must not carry the API key.
        content = await client.get(url, follow_redirects=True, timeout=300)
    except httpx.HTTPError as exc:
        return f"[ERROR]: downloading video job {task_id} failed: {exc!r}"
    if content.status_code != 200:
        return f"[ERROR]: video job {task_id} completed but downloading content failed: {content.status_code}"
    try:
        target = _save_path(save_dir, "generated_videos", f"video_{task_id}.mp4")
        target.write_bytes(content.content)
    except OSError as exc:
        return f"[ERROR]: failed to save video for job {task_id}: {exc!r}"
    return (
        "Video generated successfully!\n"
        f"Saved to: {target}\n"
        f"(job {task_id} - the remote source URL expires after 24h, so this local file is the durable copy.)"
    )


async def check_video(api_key: str, api_base: str, task_id: str, save_dir: str | None) -> str:
    bases = _candidate_bases(api_base)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
            for index, base in enumerate(bases):
                status, task, error = await _query(client, base, headers, task_id)
                if status == "auth_failed":
                    if index + 1 < len(bases):
                        continue
                    return _auth_failure_message(bases, error or "")
                break
            if error:
                return error
            if status in _PENDING_STATUSES:
                return (
                    f"Video job {task_id} is still {status}. Call check_video_status again to keep waiting "
                    "(do not use shell sleep)."
                )
            return await _finish(client, task_id, status, task, save_dir)
    except httpx.HTTPError as exc:
        return f"[ERROR]: checking ModelArk video job {task_id} failed: {exc!r}"
