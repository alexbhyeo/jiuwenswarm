# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Text-to-image generation tool via an OpenRouter-style chat-completions image
modality (e.g. google/gemini-3.1-flash-image).

Unlike video generation (video_gen_tools.py), this call is synchronous - the
model returns the generated image(s) directly in the chat-completions
response (as data: URIs or remote URLs), with no submit-then-poll job to
track.

Credentials come from a dedicated "Visual processing" config panel slot
(VISUAL_GEN_API_KEY/VISUAL_GEN_API_BASE/VISUAL_GEN_MODEL_NAME/
VISUAL_GEN_PROVIDER/VISUAL_GEN_PROTOCOL) - independent of both:
- visual_question_answering's VISION_* slot (image understanding, a
  different capability), and
- image_tools.py's generate_image (IMAGE_GEN_* slot), whose implementation
  is hard-locked to a DashScope-only code path (see
  OpenAIModelClient._require_dashscope_media_profile in openjiuwen) and
  cannot serve an OpenRouter/Gemini-style model no matter what provider or
  key is configured there.

There is no built-in default endpoint/model: whatever's configured in the
dedicated slot above is what this tool uses, and api_key/api_base/model
must all be set.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from openjiuwen.core.foundation.tool import tool
from PIL import Image

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

# See video_gen_tools._MAX_REFERENCE_DIMENSION for why: a full-resolution
# reference image base64-embedded directly into the request body has been
# observed to make the provider drop the connection (httpx ReadError) rather
# than respond cleanly. Downscale + re-encode as JPEG first.
_MAX_REFERENCE_DIMENSION = 1280
_REFERENCE_JPEG_QUALITY = 85

# 观察到的另一种同类"服务商丢连接"场景：多个 generate_visual 并发打到
# 同一个服务商时（比如 实验室 一张处理卡片一次开了好几份输出），偶发
# RemoteProtocolError("Server disconnected without sending a response.")
# ——不是超时、也没有明确的 4xx/5xx，纯粹是对端在并发压力下把连接掐断，
# 不代表这次请求本身有问题。短暂退避后重试通常就能成功，不需要用户自己
# 再点一次生成；重试次数不多，即使真的是持续性故障也不会把失败拖得太久。
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_BASE_DELAY_S = 1.5


def visual_gen_enabled() -> bool:
    """Whether the "Visual processing" switch in configuration settings is on.

    Mirrors video_gen_tools.video_gen_enabled's env-var gate: VISUAL_GEN_ENABLED
    is set from that switch and checked independently of whether the Visual
    processing config itself is complete, so a user can leave valid
    credentials in place while still turning generation off.
    """
    return str(os.environ.get("VISUAL_GEN_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def _get_visual_gen_api_credentials() -> tuple[str, str, str]:
    """Resolve image-generation credentials from the dedicated "Visual
    processing" config panel slot only (VISUAL_GEN_API_KEY/VISUAL_GEN_API_BASE/
    VISUAL_GEN_MODEL_NAME). No fallback env vars, no built-in default endpoint
    or model - an empty string means unconfigured.
    """
    api_key = os.environ.get("VISUAL_GEN_API_KEY", "").strip()
    api_base = os.environ.get("VISUAL_GEN_API_BASE", "").strip()
    model = os.environ.get("VISUAL_GEN_MODEL_NAME", "").strip()
    return api_key, api_base, model


def visual_gen_configured() -> bool:
    """Whether the Visual processing config (key/base/model) is complete.

    Public entry point for callers outside this module (interface_deep.py,
    swarm/providers/tools.py) that only need a yes/no gate and shouldn't
    depend on _get_visual_gen_api_credentials' private tuple shape.
    """
    api_key, api_base, model = _get_visual_gen_api_credentials()
    return bool(api_key and api_base and model)


def _downscale_reference_image(path: Path) -> tuple[bytes, str]:
    """Downscale + re-encode a local reference image as JPEG (see module-level
    comment above _MAX_REFERENCE_DIMENSION for why)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_REFERENCE_DIMENSION, _MAX_REFERENCE_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_REFERENCE_JPEG_QUALITY)
        return buf.getvalue(), "image/jpeg"


def _resolve_reference_image(path_or_url: str) -> tuple[str | None, str | None]:
    """reference_image_path may be a real http(s) URL, an already-complete
    data: URI, or a local file path - resolved to a data: URI here
    (server-side), mirroring video_gen_tools._resolve_frame_reference.
    """
    value = (path_or_url or "").strip()
    if not value:
        return None, None
    if value.startswith(("http://", "https://", "data:")):
        return value, None
    path = Path(value).expanduser()
    if not path.is_file():
        return None, f"[ERROR]: reference_image_path {value!r} is not a URL/data URI and no such file exists."
    try:
        data, mime = _downscale_reference_image(path)
    except Exception:
        logger.exception("[generate_visual] downscaling reference image failed, using raw file: %s", path)
        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(str(path))
        if not mime or not mime.startswith("image/"):
            mime = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}", None


def _resolve_save_path(save_dir: str | None, filename: str) -> Path:
    root = Path(save_dir).expanduser() if save_dir else (get_agent_workspace_dir() / "generated_images")
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def _extension_for_mime(mime: str) -> str:
    if "png" in mime:
        return "png"
    if "webp" in mime:
        return "webp"
    if "gif" in mime:
        return "gif"
    return "jpg"


@tool(
    name="generate_visual",
    description=(
        "Generate an image from a text prompt using AI image generation models. "
        "Use this tool when the user wants to create or generate an image based "
        "on a text description. Returns the path to the saved generated image file."
    ),
)
async def generate_visual(
    prompt: str,
    aspect_ratio: str = "16:9",
    resolution: str = "512",
    reference_image_paths: list[str] | None = None,
    save_dir: str | None = None,
) -> str:
    """
    Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate.
        aspect_ratio: e.g. "16:9", "9:16", "1:1".
        resolution: e.g. "512", "1024" (short-edge pixel size).
        reference_image_paths: Optional list of local file paths, http(s)
            URLs, or data: URIs of one or more reference images to
            edit/compose from (image-to-image, or multi-image composition -
            e.g. placing a product from one reference onto a surface from
            another). Support depends on the configured model - Gemini-family
            image models accept multiple input images in the same chat turn;
            text-only image models will simply ignore them.
        save_dir: Optional directory to save the image (defaults to the agent
            workspace's generated_images/ folder).

    Returns:
        Path to the generated image file, or an error message.
    """
    api_key, api_base, model = _get_visual_gen_api_credentials()
    if not (api_key and api_base and model):
        return (
            "[ERROR]: image generation is not configured - set the Visual processing "
            "API key, API URL, and model name in configuration settings."
        )
    prompt = (prompt or "").strip()
    if not prompt:
        return "[ERROR]: prompt is required."

    reference_data_uris: list[str] = []
    for path in reference_image_paths or []:
        data_uri, err = _resolve_reference_image(path)
        if err:
            return err
        if data_uri:
            reference_data_uris.append(data_uri)

    # Not every provider/model honors aspect_ratio/resolution as separate
    # request-body fields, so the hint is also folded into the prompt text
    # itself as a best-effort fallback the model can act on directly.
    full_prompt = f"{prompt}\n\n(Aspect ratio: {aspect_ratio}, resolution: {resolution}px)"
    content: Any = full_prompt
    if reference_data_uris:
        content = [
            *({"type": "image_url", "image_url": {"url": uri}} for uri in reference_data_uris),
            {"type": "text", "text": full_prompt},
        ]
    body: dict[str, Any] = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": content}],
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info(
        "[generate_visual] using model: %s (api_base: %s, aspect_ratio: %s, resolution: %s, references: %d)",
        model, api_base, aspect_ratio, resolution, len(reference_data_uris),
    )

    data: dict[str, Any] | None = None
    last_exc: httpx.HTTPError | None = None
    for attempt in range(_TRANSIENT_RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
                resp = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
            if resp.status_code != 200:
                return f"[ERROR]: image generation request failed: {resp.status_code} {resp.text}"
            try:
                data = resp.json()
            except ValueError as exc:
                return f"[ERROR]: image generation request returned invalid JSON: {exc!r}"
            break
        except httpx.RemoteProtocolError as exc:
            last_exc = exc
            if attempt + 1 >= _TRANSIENT_RETRY_ATTEMPTS:
                return f"[ERROR]: image generation request failed after {attempt + 1} attempts: {exc!r}"
            logger.warning(
                "[generate_visual] transient connection drop (attempt %d/%d), retrying: %r",
                attempt + 1, _TRANSIENT_RETRY_ATTEMPTS, exc,
            )
            await asyncio.sleep(_TRANSIENT_RETRY_BASE_DELAY_S * (attempt + 1))
        except httpx.HTTPError as exc:
            return f"[ERROR]: image generation request failed: {exc!r}"

    if data is None:
        return f"[ERROR]: image generation request failed after {_TRANSIENT_RETRY_ATTEMPTS} attempts: {last_exc!r}"

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return f"[ERROR]: unexpected response shape from provider: {data}"

    images = message.get("images") or []
    if not images:
        content = message.get("content", "")
        return f"[ERROR]: no images returned. Model response: {content}"

    saved_paths: list[str] = []
    for index, image in enumerate(images):
        url_field = ((image.get("image_url") or {}).get("url")) or image.get("url") or ""
        if not url_field.startswith("data:"):
            saved_paths.append(url_field)  # already a remote URL, nothing to download
            continue
        header, _, b64data = url_field.partition(",")
        mime = header[len("data:"):].split(";")[0] if header.startswith("data:") else "image/png"
        ext = _extension_for_mime(mime)
        # index 只在同一次调用返回多张图片时才能区分文件名——秒级时间戳
        # 撞在一起时（并发跑多个 generate_visual 调用，各自的 index 通常都
        # 是 0），两次调用会算出同一个文件名，后写的覆盖先写的，其中一张
        # 图片就无声地丢了。加一段随机后缀彻底避免这种碰撞，不依赖调用之间
        # 互相错开时间。
        filename = f"image_{int(time.time())}_{index}_{secrets.token_hex(4)}.{ext}"
        try:
            target = _resolve_save_path(save_dir, filename)
            target.write_bytes(base64.b64decode(b64data))
        except OSError as exc:
            return f"[ERROR]: failed to save image ({filename}) to {save_dir or 'agent workspace'}: {exc!r}"
        saved_paths.append(str(target))

    return "Image generated successfully!\nSaved to: " + ", ".join(saved_paths)
