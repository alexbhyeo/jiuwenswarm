# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MiniMax-native image / video generation backends.

MiniMax's generation APIs are not OpenAI-compatible, so the OpenRouter-style
request path in visual_gen_tools / video_gen_tools cannot drive them:

- image-01: ``POST {root}/v1/image_generation`` (synchronous) returning
  ``data.image_urls`` / ``data.image_base64`` plus a ``base_resp`` status.
- MiniMax-H3: ``POST {root}/v2/video_generation`` with a ``content`` array
  (returns a ``task_id``), then ``GET {root}/v2/query/video_generation/{id}``
  until ``task.status`` is ``succeeded`` (``task.content.url``) or ``failed``
  (``task.error``).

References: https://platform.minimax.io/docs/api-reference/image-generation-t2i
and https://platform.minimax.io/docs/api-reference/video-generation-v2-create

The configured "API URL" is the OpenAI-style base (``https://api.minimax.io/v1``
global, ``https://api.minimaxi.com/v1`` China); the v2 endpoints live at the
host root, so the trailing ``/v1`` is stripped to get the root. Keys are
region-bound: a global key is rejected by the China host and vice versa.

Return strings follow the same conventions as the OpenRouter path
("[ERROR]: ...", "Saved to: ...", "Video job {id} submitted and still ...")
so callers such as the chat agent and check_video_status need no changes.
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

_IMAGE_ASPECT_RATIOS = {"1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"}
_VIDEO_RATIOS = {"16:9", "21:9", "4:3", "1:1", "3:4", "9:16"}
_VIDEO_MIN_SECONDS = 4
_VIDEO_MAX_SECONDS = 15
_POLL_INTERVAL_SECONDS = 10
_MAX_POLL_SECONDS = 120
_IMAGE_PROMPT_LIMIT = 1500
_VIDEO_PROMPT_LIMIT = 7000
_MINIMAX_HOST = re.compile(r"(^|\.)minimax(i)?\.(io|com)$", re.IGNORECASE)


def is_minimax(protocol_env: str, api_base: str) -> bool:
    """Whether the configured slot should use the MiniMax-native API: the saved
    协议 is "minimax", or the API URL is a MiniMax host."""
    if os.environ.get(protocol_env, "").strip().lower() == "minimax":
        return True
    host = re.sub(r"^https?://", "", api_base.strip(), flags=re.IGNORECASE).split("/", 1)[0].split(":", 1)[0]
    return bool(_MINIMAX_HOST.search(host))


def _root(api_base: str) -> str:
    return re.sub(r"/v[0-9]+/?$", "", api_base.strip().rstrip("/"))


# MiniMax keys are bound to one region: a global key (platform.minimax.io) is
# rejected by the China host with "invalid api key (2049)" and vice versa. The
# built-in provider preset points at the China host while most keys are global,
# so a misconfigured region is the common failure - try the other region's host
# before reporting the key as bad.
_REGION_ROOTS = ("https://api.minimax.io", "https://api.minimaxi.com")
_AUTH_STATUS_CODES = (1004, 2049)


def _candidate_roots(api_base: str) -> list[str]:
    primary = _root(api_base)
    if primary not in _REGION_ROOTS:
        return [primary]
    return [primary, *[root for root in _REGION_ROOTS if root != primary]]


def _is_auth_failure(http_status: int, payload: Any) -> bool:
    if http_status == 401:
        return True
    if not isinstance(payload, dict):
        return False
    base = payload.get("base_resp")
    if isinstance(base, dict) and base.get("status_code") in _AUTH_STATUS_CODES:
        return True
    err = payload.get("error")
    return isinstance(err, dict) and (err.get("type") == "authorized_error" or str(err.get("http_code")) == "401")


def _auth_failure_message(roots: list[str], detail: str) -> str:
    hosts = " and ".join(re.sub(r"^https?://", "", root) for root in roots)
    return (
        f"[ERROR]: MiniMax rejected the API key on {hosts} ({detail}). The key is wrong, expired or revoked - "
        "update it in Settings > Agent (Image/Video generation). Do not search other config files for a different key."
    )


def _base_error(payload: Any) -> str | None:
    """MiniMax reports failures in ``base_resp`` (HTTP 200) or in an ``error``
    object; returns a readable message, or None when the call succeeded."""
    if not isinstance(payload, dict):
        return f"unexpected response: {payload!r}"
    base = payload.get("base_resp")
    if isinstance(base, dict) and base.get("status_code") not in (0, None):
        return f"{base.get('status_code')} {base.get('status_msg', '')}".strip()
    err = payload.get("error")
    if isinstance(err, dict):
        return f"{err.get('type', 'error')}: {err.get('message', '')}".strip()
    return None


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


async def generate_image(
    api_key: str,
    api_base: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    save_dir: str | None,
) -> str:
    aspect = aspect_ratio if aspect_ratio in _IMAGE_ASPECT_RATIOS else "1:1"
    body = {
        "model": model,
        "prompt": prompt[:_IMAGE_PROMPT_LIMIT],
        "aspect_ratio": aspect,
        "response_format": "base64",
        "n": 1,
    }
    roots = _candidate_roots(api_base)
    payload: Any = None
    try:
        async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
            for index, root in enumerate(roots):
                logger.info("[generate_visual] MiniMax model: %s (root: %s, aspect_ratio: %s)", model, root, aspect)
                resp = await client.post(
                    f"{root}/v1/image_generation", headers={"Authorization": f"Bearer {api_key}"}, json=body
                )
                try:
                    payload = resp.json()
                except ValueError:
                    return f"[ERROR]: MiniMax image generation returned a non-JSON response: {resp.status_code} {resp.text[:300]}"
                if _is_auth_failure(resp.status_code, payload):
                    if index + 1 < len(roots):
                        logger.warning("[generate_visual] MiniMax rejected the key on %s, trying %s", root, roots[index + 1])
                        continue
                    return _auth_failure_message(roots, _base_error(payload) or str(resp.status_code))
                break
    except httpx.HTTPError as exc:
        return f"[ERROR]: MiniMax image generation request failed: {exc!r}"
    if resp.status_code != 200 or _base_error(payload):
        return f"[ERROR]: MiniMax image generation failed: {_base_error(payload) or resp.status_code}"

    images = ((payload.get("data") or {}).get("image_base64")) or []
    if not images:
        return f"[ERROR]: MiniMax returned no images. Response: {str(payload)[:300]}"
    saved: list[str] = []
    for index, encoded in enumerate(images):
        try:
            data = base64.b64decode(encoded)
            target = _save_path(save_dir, "generated_images", f"image_{int(time.time())}_{index}_{secrets.token_hex(4)}.{_image_extension(data)}")
            target.write_bytes(data)
        except (OSError, ValueError) as exc:
            return f"[ERROR]: failed to save MiniMax image: {exc!r}"
        saved.append(str(target))
    return "Image generated successfully!\nSaved to: " + ", ".join(saved)


def _video_resolution(resolution: str) -> str:
    """The tool's resolution vocabulary ("480p"/"720p"/"1080p") -> H3's
    "768P" / "2K"."""
    text = (resolution or "").strip().upper()
    if text == "2K":
        return "2K"
    digits = re.sub(r"\D", "", text)
    return "768P" if not digits or int(digits) <= 768 else "2K"


async def submit_video(
    api_key: str,
    api_base: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int,
    first_frame_data_uri: str | None,
    save_dir: str | None,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt[:_VIDEO_PROMPT_LIMIT]}]
    if first_frame_data_uri:
        content.append({"type": "image_url", "image_url": {"url": first_frame_data_uri}, "role": "first_frame"})
    body = {
        "model": model,
        "content": content,
        "resolution": _video_resolution(resolution),
        "duration": max(_VIDEO_MIN_SECONDS, min(_VIDEO_MAX_SECONDS, int(duration_seconds))),
        "ratio": aspect_ratio if aspect_ratio in _VIDEO_RATIOS else "16:9",
    }
    roots = _candidate_roots(api_base)
    root = roots[0]
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
            for index, candidate in enumerate(roots):
                root = candidate
                logger.info(
                    "[generate_video] MiniMax model: %s (root: %s, body: %s)",
                    model, root, {k: v for k, v in body.items() if k != "content"},
                )
                submit = await client.post(f"{root}/v2/video_generation", headers=headers, json=body)
                try:
                    payload = submit.json()
                except ValueError:
                    return f"[ERROR]: MiniMax video submit returned a non-JSON response: {submit.status_code} {submit.text[:300]}"
                if _is_auth_failure(submit.status_code, payload):
                    if index + 1 < len(roots):
                        logger.warning("[generate_video] MiniMax rejected the key on %s, trying %s", root, roots[index + 1])
                        continue
                    return _auth_failure_message(roots, _base_error(payload) or str(submit.status_code))
                break
            if submit.status_code not in (200, 201, 202) or _base_error(payload):
                return f"[ERROR]: MiniMax video generation submit failed: {_base_error(payload) or submit.status_code}"
            task_id = str(payload.get("task_id") or ((payload.get("task") or {}).get("id")) or "")
            if not task_id:
                return f"[ERROR]: MiniMax video submit returned no task id: {str(payload)[:300]}"

            status, task, elapsed = "queued", {}, 0
            while status in ("queued", "running") and elapsed < _MAX_POLL_SECONDS:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                elapsed += _POLL_INTERVAL_SECONDS
                status, task, error = await _query(client, root, headers, task_id)
                if status == "auth_failed":
                    return _auth_failure_message([root], error or "")
                if error:
                    return error
            if status in ("queued", "running"):
                return (
                    f"Video job {task_id} submitted and still {status} after {elapsed}s - generation can "
                    f"take several minutes. Call check_video_status with job_id={task_id} to check progress "
                    "and download it once ready."
                )
            return await _finish(client, task_id, status, task, save_dir)
    except httpx.HTTPError as exc:
        return f"[ERROR]: MiniMax video generation request failed: {exc!r}"


async def _query(
    client: httpx.AsyncClient, root: str, headers: dict[str, str], task_id: str
) -> tuple[str, dict[str, Any], str | None]:
    resp = await client.get(f"{root}/v2/query/video_generation/{task_id}", headers=headers)
    try:
        payload = resp.json()
    except ValueError:
        return "", {}, f"[ERROR]: polling MiniMax video job {task_id} returned a non-JSON response: {resp.status_code}"
    if _is_auth_failure(resp.status_code, payload):
        return "auth_failed", {}, _base_error(payload) or str(resp.status_code)
    if resp.status_code != 200 or _base_error(payload):
        return "", {}, f"[ERROR]: polling MiniMax video job {task_id} failed: {_base_error(payload) or resp.status_code}"
    task = payload.get("task") or {}
    return str(task.get("status") or "").lower(), task, None


async def _finish(client: httpx.AsyncClient, task_id: str, status: str, task: dict[str, Any], save_dir: str | None) -> str:
    if status != "succeeded":
        error = task.get("error") or {}
        detail = f"{error.get('code', '')} {error.get('message', '')}".strip() if isinstance(error, dict) else str(error)
        return f"[ERROR]: video job {task_id} ended with status {status}: {detail or 'no error detail provided'}"
    url = ((task.get("content") or {}).get("url")) or ""
    if not url:
        return f"[ERROR]: video job {task_id} succeeded but returned no video URL."
    try:
        # The CDN URL is pre-signed - it must not carry the API key.
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
        f"(job {task_id} - the remote source URL is time-limited, so this local file is the durable copy.)"
    )


async def check_video(api_key: str, api_base: str, task_id: str, save_dir: str | None) -> str:
    roots = _candidate_roots(api_base)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60, verify=get_requests_verify()) as client:
            for index, root in enumerate(roots):
                status, task, error = await _query(client, root, headers, task_id)
                if status == "auth_failed":
                    if index + 1 < len(roots):
                        continue
                    return _auth_failure_message(roots, error or "")
                break
            if error:
                return error
            if status in ("queued", "running"):
                return f"Video job {task_id} is still {status}."
            return await _finish(client, task_id, status, task, save_dir)
    except httpx.HTTPError as exc:
        return f"[ERROR]: checking MiniMax video job {task_id} failed: {exc!r}"
