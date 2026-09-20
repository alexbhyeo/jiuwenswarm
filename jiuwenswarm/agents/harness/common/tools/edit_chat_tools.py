# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Conversational text chat for the 剪辑 (Edit) tab's editing-assistant panel,
via a plain OpenRouter-style chat-completions endpoint.

Unlike generate_visual/generate_video, this is not a generation tool with a
single fixed-shape prompt - it's a multi-turn conversation (system + prior
history + latest user turn), and the response is plain assistant text, not a
media file.

Credentials come from a dedicated "Edit chat" config panel slot
(EDIT_CHAT_API_KEY/EDIT_CHAT_API_BASE/EDIT_CHAT_MODEL_NAME/
EDIT_CHAT_PROVIDER/EDIT_CHAT_PROTOCOL) - independent of the video/visual
generation slots, since a model tuned for image/video generation is not
necessarily a good fit for open-ended conversation, and vice versa.

There is no built-in default endpoint/model: whatever's configured in the
dedicated slot above is what this tool uses, and api_key/api_base/model
must all be set.
"""
from __future__ import annotations

import base64
import io
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify

logger = logging.getLogger(__name__)

# See visual_gen_tools._MAX_REFERENCE_DIMENSION for why: a full-resolution
# image base64-embedded directly into the request body has been observed to
# make the provider drop the connection (httpx ReadError) rather than respond
# cleanly. Downscale + re-encode as JPEG first.
_MAX_REFERENCE_DIMENSION = 1280
_REFERENCE_JPEG_QUALITY = 85


def edit_chat_enabled() -> bool:
    """Whether the "Edit chat" switch in configuration settings is on.

    Mirrors visual_gen_tools.visual_gen_enabled's env-var gate.
    """
    return str(os.environ.get("EDIT_CHAT_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def _get_edit_chat_api_credentials() -> tuple[str, str, str]:
    """Resolve chat credentials from the dedicated "Edit chat" config panel
    slot only (EDIT_CHAT_API_KEY/EDIT_CHAT_API_BASE/EDIT_CHAT_MODEL_NAME). No
    fallback env vars, no built-in default endpoint or model - an empty
    string means unconfigured.
    """
    api_key = os.environ.get("EDIT_CHAT_API_KEY", "").strip()
    api_base = os.environ.get("EDIT_CHAT_API_BASE", "").strip()
    model = os.environ.get("EDIT_CHAT_MODEL_NAME", "").strip()
    return api_key, api_base, model


def edit_chat_configured() -> bool:
    """Whether the Edit chat config (key/base/model) is complete."""
    api_key, api_base, model = _get_edit_chat_api_credentials()
    return bool(api_key and api_base and model)


def _downscale_reference_image(path: Path) -> tuple[bytes, str]:
    """Downscale + re-encode a local image as JPEG (see module-level comment
    above _MAX_REFERENCE_DIMENSION for why)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_REFERENCE_DIMENSION, _MAX_REFERENCE_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_REFERENCE_JPEG_QUALITY)
        return buf.getvalue(), "image/jpeg"


def _resolve_image_data_uri(path_or_url: str) -> str | None:
    """A local file path is resolved to a data: URI (server-side); an
    already-complete http(s) URL or data: URI passes through unchanged.
    Mirrors visual_gen_tools._resolve_reference_image, minus the error-string
    return (a single bad path here shouldn't fail the whole chat turn - it's
    just skipped, logged, and the conversation proceeds text-only for it).
    """
    value = (path_or_url or "").strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        logger.warning("[chat_with_editor] image path does not exist, skipping: %s", value)
        return None
    try:
        data, mime = _downscale_reference_image(path)
    except Exception:
        logger.exception("[chat_with_editor] downscaling image failed, using raw file: %s", path)
        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(str(path))
        if not mime or not mime.startswith("image/"):
            mime = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def chat_with_editor(
    messages: list[dict[str, str]],
    image_paths: list[str] | None = None,
) -> str:
    """Send a conversation to the configured Edit chat model and return the
    assistant's text reply.

    Args:
        messages: Full turn list so far, oldest first, each
            {"role": "system"|"user"|"assistant", "content": str}. The caller
            (director_manager.handle_director_edit_chat_send) is responsible
            for building this - a system prompt plus prior history plus the
            newest user turn.
        image_paths: Local file paths, http(s) URLs, or data: URIs to attach
            to the LAST message in `messages` (expected to be the newest user
            turn) as multimodal content parts - e.g. 素材 images the user
            referenced with "@名称". Ignored if `messages` is empty.

    Returns:
        The assistant's reply text, or a "[ERROR]: ..." string on failure.
    """
    api_key, api_base, model = _get_edit_chat_api_credentials()
    if not (api_key and api_base and model):
        return (
            "[ERROR]: edit chat is not configured - set the Edit chat "
            "API key, API URL, and model name in configuration settings."
        )
    if not messages:
        return "[ERROR]: messages is required."

    wire_messages: list[dict[str, Any]] = [dict(m) for m in messages]
    if image_paths:
        data_uris = [uri for uri in (_resolve_image_data_uri(p) for p in image_paths) if uri]
        if data_uris:
            last = wire_messages[-1]
            text = str(last.get("content") or "")
            last["content"] = [
                *[{"type": "image_url", "image_url": {"url": uri}} for uri in data_uris],
                {"type": "text", "text": text},
            ]

    body: dict[str, Any] = {"model": model, "messages": wire_messages}
    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info(
        "[chat_with_editor] using model: %s (api_base: %s, turns: %d, images: %d)",
        model, api_base, len(wire_messages), len(image_paths or []),
    )

    try:
        async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
            resp = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
            if resp.status_code != 200:
                return f"[ERROR]: edit chat request failed: {resp.status_code} {resp.text}"
            try:
                data = resp.json()
            except ValueError as exc:
                return f"[ERROR]: edit chat request returned invalid JSON: {exc!r}"
    except httpx.HTTPError as exc:
        return f"[ERROR]: edit chat request failed: {exc!r}"

    try:
        reply = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return f"[ERROR]: unexpected response shape from provider: {data}"

    if not isinstance(reply, str) or not reply.strip():
        return f"[ERROR]: empty reply from provider. Full response: {data}"
    return reply
