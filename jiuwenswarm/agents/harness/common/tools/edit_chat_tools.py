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


def build_multimodal_user_message(text: str, image_paths: list[str] | None) -> dict[str, Any]:
    """Build a single user turn, embedding any reference images as
    multimodal content parts (OpenAI-style image_url parts).

    Kept separate from call_edit_chat_completion (rather than that function
    attaching images to "the last message" on every call) because a tool-
    calling conversation grows the message list turn by turn - images belong
    on the one real user turn, not re-attached on every follow-up model call
    that's really just feeding back a tool result.
    """
    if not image_paths:
        return {"role": "user", "content": text}
    data_uris = [uri for uri in (_resolve_image_data_uri(p) for p in image_paths) if uri]
    if not data_uris:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            *[{"type": "image_url", "image_url": {"url": uri}} for uri in data_uris],
            {"type": "text", "text": text},
        ],
    }


async def call_edit_chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send one chat/completions turn to the configured Edit chat model and
    return the assistant's raw message object (OpenAI-compatible shape:
    {"role": "assistant", "content": str | None, "tool_calls": [...] | absent}).

    Unlike a plain single-shot chat helper, this deliberately returns the
    raw message rather than just text: the caller (director_manager's
    tool-calling loop) needs to inspect `tool_calls` to decide whether to
    execute a generate_design_image call and feed the result back for
    another turn, or treat `content` as the final reply.

    Args:
        messages: Full turn list so far, oldest first - system prompt, prior
            history, the newest user turn (see build_multimodal_user_message
            for how to construct that one), and any assistant/tool turns
            already appended by an in-progress tool-calling loop.
        tools: OpenAI-style tool schema list to advertise to the model, or
            None to disable tool calling for this call.

    Returns:
        The assistant message dict on success. On failure, a dict with only
        {"content": "[ERROR]: ..."} so callers can uniformly check
        `content.startswith("[ERROR]:")` without a separate exception path.
    """
    api_key, api_base, model = _get_edit_chat_api_credentials()
    if not (api_key and api_base and model):
        return {
            "content": (
                "[ERROR]: edit chat is not configured - set the Edit chat "
                "API key, API URL, and model name in configuration settings."
            )
        }
    if not messages:
        return {"content": "[ERROR]: messages is required."}

    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info(
        "[call_edit_chat_completion] using model: %s (api_base: %s, turns: %d, tools: %d)",
        model, api_base, len(messages), len(tools or []),
    )

    try:
        async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
            resp = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
            if resp.status_code != 200:
                return {"content": f"[ERROR]: edit chat request failed: {resp.status_code} {resp.text}"}
            try:
                data = resp.json()
            except ValueError as exc:
                return {"content": f"[ERROR]: edit chat request returned invalid JSON: {exc!r}"}
    except httpx.HTTPError as exc:
        return {"content": f"[ERROR]: edit chat request failed: {exc!r}"}

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {"content": f"[ERROR]: unexpected response shape from provider: {data}"}

    if not isinstance(message, dict):
        return {"content": f"[ERROR]: unexpected message shape from provider: {data}"}
    # 没有工具调用时才要求非空文本——纯文本轮次的空回复视为异常；带
    # tool_calls 的轮次 content 本来就可能是 None/空字符串（模型只想调用
    # 工具，还没打算说话），不能按同样标准判为错误。
    if not message.get("tool_calls") and not str(message.get("content") or "").strip():
        return {"content": f"[ERROR]: empty reply from provider. Full response: {data}"}
    return message
