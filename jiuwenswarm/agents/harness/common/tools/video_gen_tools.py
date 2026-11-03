# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Text-to-video / image-to-video generation tools.

Video generation is a submit-then-poll job (can take minutes), not a single
request/response call. generate_video submits the job and polls for up to
_MAX_POLL_SECONDS synchronously (a short wait is fine inside one tool call);
if the job is still running after that, it returns the job_id and tells the
agent to call check_video_status instead of blocking indefinitely or
pretending the video is ready when it isn't.

Credentials come from a dedicated "Video processing" config panel slot
(VIDEO_GEN_API_KEY/VIDEO_GEN_API_BASE/VIDEO_GEN_MODEL_NAME/VIDEO_GEN_PROVIDER)
- separate from the "Video Model" slot video_understanding reads, so the two
capabilities can point at independent models (e.g. a chat-completions-style
video-analysis model for understanding, and OpenRouter's
bytedance/seedance-2.0-fast for generation) without one breaking the other.
There is no built-in default endpoint/model: whatever's configured there is
what these tools use, and api_key/api_base/model must all be set (a
self-deployed vLLM-Omni endpoint needs only the API URL).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openjiuwen.core.foundation.tool import tool
from PIL import Image

from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    RootDecisionContext,
    current_root_decision_context,
)
from jiuwenswarm.agents.harness.common.tools import gen_toolkits
from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.common.chat_final import FINAL_MODE_APPEND, annotate_chat_final
from jiuwenswarm.common.host_services import send_runtime_push
from jiuwenswarm.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 10
_MAX_POLL_SECONDS = 120
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}
_VIDEO_MIN_SECONDS = 4
_VIDEO_MAX_SECONDS = 15
# MiniMax-H3-Max rejects clips shorter than 5 s.
_H3_MAX_MIN_SECONDS = 5

# A video job that is still running when generate_video's/check_video_status's
# own bounded poll window gives up is not forgotten here: this module keeps
# polling it in the background and pushes the outcome into the chat once it
# settles. Without this, a slow job submitted in Team/multi-agent mode is
# never surfaced again once that round's stream ends (GitCode #5806) - the
# user has to think to ask again, and the agent has to think to re-call
# check_video_status, neither of which is guaranteed once the turn is over.
_WATCH_POLL_INTERVAL_SECONDS = 20
_WATCH_MAX_SECONDS = 30 * 60
# All of generate_video's/check_video_status's own "still running" messages
# (both the direct OpenRouter-style path here and every gen_toolkits backend)
# share this "Video job <id> submitted and still ..." / "Video job <id> is
# still ..." shape - see _pending_submit_message and the check_video_status
# paths below.
_PENDING_JOB_ID_RE = re.compile(r"^Video job (\S+) (?:submitted and still|is still) \w+")
_SAVED_TO_RE = re.compile(r"Saved to: (.+?)\n")
# Every backend's provider-reported terminal failure shares this exact shape
# (gen_toolkits.py's four _*_finish helpers and this module's own direct
# path all format it as f"[ERROR]: video job {id} ended with status {status}: ...").
_TERMINAL_FAILURE_RE = re.compile(r"^\[ERROR\]: video job \S+ ended with status \w+")
# job_ids with an active background watcher - guards against piling up a
# second watcher every time the agent (or this module itself) polls a job
# that is still pending.
_WATCHED_PENDING_JOBS: set[str] = set()
# Keeps the scheduled tasks referenced so they aren't garbage-collected mid-flight.
_BACKGROUND_WATCH_TASKS: set[asyncio.Task[None]] = set()


def _extract_pending_job_id(message: str) -> str | None:
    match = _PENDING_JOB_ID_RE.match(message or "")
    return match.group(1) if match else None


def _is_terminal_job_result(result: str) -> bool:
    """Whether *result* is a definitive outcome - success, or a provider-
    reported terminal status - rather than a transient/intermediate error
    (a non-200 poll response, invalid JSON, a momentary download hiccup,
    credentials cleared mid-watch, an unrecognized error shape, ...) that
    should just be retried instead of reported as a false failure.

    Used instead of "not a recognized still-pending message" to decide
    whether the watcher has something final to deliver: every result string
    this module or gen_toolkits.py can produce for an error that is NOT a
    genuine terminal outcome is also not one of these two shapes, so
    treating anything else as "keep polling" never mistakes a transient
    hiccup for completion.
    """
    return result.startswith("Video generated successfully!") or bool(_TERMINAL_FAILURE_RE.match(result))


def _schedule_completion_watch_if_pending(result: str, save_dir: str | None) -> None:
    """Arm a background watcher the first time *result* reports a still-running job."""
    job_id = _extract_pending_job_id(result)
    if not job_id or job_id in _WATCHED_PENDING_JOBS:
        return
    context = current_root_decision_context()
    if context is None:
        # No session/request to deliver to (e.g. a CLI/offline call) - the
        # caller is expected to poll check_video_status itself.
        return
    _WATCHED_PENDING_JOBS.add(job_id)
    task = asyncio.create_task(_watch_until_settled(job_id, save_dir, context))
    _BACKGROUND_WATCH_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_WATCH_TASKS.discard)


async def _watch_until_settled(
    job_id: str, save_dir: str | None, context: RootDecisionContext
) -> None:
    try:
        elapsed = 0
        while elapsed < _WATCH_MAX_SECONDS:
            await asyncio.sleep(_WATCH_POLL_INTERVAL_SECONDS)
            elapsed += _WATCH_POLL_INTERVAL_SECONDS
            result = await _check_video_status_impl(job_id, save_dir)
            if _is_terminal_job_result(result):
                await _deliver_watch_result(job_id, result, context)
                return
            if _extract_pending_job_id(result) is None:
                # Not a recognized "still pending" message either - a
                # transient/intermediate error. Keep waiting rather than
                # guessing: see _is_terminal_job_result.
                logger.warning(
                    "[generate_video] non-terminal error while watching job %s, retrying: %s",
                    job_id, result,
                )
        logger.warning(
            "[generate_video] gave up watching job %s for completion after %ss",
            job_id, elapsed,
        )
    except Exception:
        logger.exception("[generate_video] background completion watch failed for job %s", job_id)
    finally:
        _WATCHED_PENDING_JOBS.discard(job_id)


# SendFileToolkit.send_file never raises - a failure to deliver (no active
# Runtime push host, which is exactly the "turn already ended" scenario this
# watcher exists for) comes back as one of these Chinese failure strings
# instead (see send_file_to_user.py's _send_file_with_envelope/send_file).
_SEND_FILE_FAILURE_PREFIXES = ("发送文件失败", "提交文件失败")


async def _deliver_watch_result(job_id: str, result: str, context: RootDecisionContext) -> None:
    """Push a settled job's outcome into the chat it was submitted from.

    There is no live agent turn left to read this tool's return value once
    the bounded poll window (and the turn/stream that originally called
    generate_video) has already ended - this is the proactive-delivery path
    send_file_to_user already uses for files, reused here for the same reason.
    """
    saved_to = _SAVED_TO_RE.search(result + "\n")
    if saved_to:
        from jiuwenswarm.agents.harness.common.tools.send_file_to_user import SendFileToolkit

        local_path = saved_to.group(1).strip()
        toolkit = SendFileToolkit(context.request_id, context.session_id, context.channel_id)
        delivery = await toolkit.send_file(local_path)
        if delivery.startswith(_SEND_FILE_FAILURE_PREFIXES):
            # The video itself is fine - only the chat delivery failed - so
            # don't claim success, and don't lose the user's generated video
            # either: fall back to the same text-notice push as a true
            # generation failure, but say where the local file is.
            logger.warning(
                "[generate_video] send_file_to_user failed delivering completed job %s to session %s: %s",
                job_id, context.session_id, delivery,
            )
            await _push_fallback_notice(
                job_id, context,
                f"视频已生成，但自动投递到会话失败：{delivery}\n本地文件：{local_path}",
            )
            return
        logger.info(
            "[generate_video] delivered completed job %s to session %s: %s",
            job_id, context.session_id, delivery,
        )
        return

    await _push_fallback_notice(job_id, context, f"视频生成任务 {job_id} 未能成功完成：{result}")


async def _push_fallback_notice(job_id: str, context: RootDecisionContext, content: str) -> None:
    pushed = await send_runtime_push(
        {
            "request_id": context.request_id,
            "channel_id": context.channel_id,
            "session_id": context.session_id,
            "payload": annotate_chat_final({"content": content}, final_mode=FINAL_MODE_APPEND),
            "is_complete": False,
        }
    )
    if not pushed:
        logger.warning(
            "[generate_video] no active push host to deliver job %s result to session %s",
            job_id, context.session_id,
        )

# 见 visual_gen_tools 模块同款注释：多个生成请求并发打到同一个服务商时，
# 偶发在提交这一步就被对端直接断连（RemoteProtocolError），不是任务本身
# 有问题，短暂退避后重试通常就能成功。
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_BASE_DELAY_S = 1.5

# Frame-reference images (first/last frame) are base64-embedded directly into
# the submit request body. A full-resolution PNG from a prior 720p/1080p
# generation can be 1.5-2MB each; with both a first and last frame that pushes
# the request past ~5MB, which has been observed to make the provider drop
# the connection mid-response (httpx ReadError) instead of returning a clean
# error. Downscaling + re-encoding as JPEG keeps the request body small and
# reliable regardless of the source resolution.
_MAX_REFERENCE_DIMENSION = 1280
_REFERENCE_JPEG_QUALITY = 85

# Frame references also have to carry the **requested aspect ratio**: with a
# frame present the provider derives the output geometry from that frame and
# ignores the `aspect_ratio` field entirely (measured: 16:9 frame + 9:16 request
# -> 1280x720 output, while the same request without a frame correctly returns
# 496x864). So a frame that doesn't match the request silently vetoes it. The
# frame is therefore center-cropped to the requested ratio here - cropping
# rather than letterboxing, because bars baked into the first frame are content
# the model would animate and keep. Tolerance below is loose enough that a
# 1376x768 frame still counts as 16:9 (1.792 vs 1.778) and is left untouched.
_ASPECT_RATIO_TOLERANCE = 0.02

# Reference audio (multimodal reference-to-video, e.g. "lip-sync to this clip")
# is base64-embedded into the submit body exactly like frame images, so it hits
# the same provider-side connection drops once the body gets large. Unlike an
# image there is no pure-Python way to re-encode a clip smaller here, so an
# oversized file is rejected with an actionable message instead of being sent
# and failing opaquely mid-request.
_MAX_REFERENCE_AUDIO_BYTES = 12 * 1024 * 1024
_AUDIO_EXTENSION_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".wma": "audio/x-ms-wma",
}


def video_gen_enabled() -> bool:
    """Whether the "Video processing" switch in configuration settings is on.

    Mirrors multimodal_model_enabled's env-var gate (see multimodal_config.py):
    VIDEO_GEN_ENABLED is set from that switch and checked independently of
    whether the Video processing config itself is complete, so a user can
    leave valid credentials in place while still turning generation off.
    """
    return str(os.environ.get("VIDEO_GEN_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def _get_video_gen_api_credentials() -> tuple[str, str, str]:
    """Resolve video-generation credentials from the dedicated "Video
    processing" config panel slot only (VIDEO_GEN_API_KEY/VIDEO_GEN_API_BASE/
    VIDEO_GEN_MODEL_NAME). No fallback env vars, no built-in default endpoint
    or model - an empty string means unconfigured.
    """
    api_key = os.environ.get("VIDEO_GEN_API_KEY", "").strip()
    api_base = os.environ.get("VIDEO_GEN_API_BASE", "").strip()
    model = os.environ.get("VIDEO_GEN_MODEL_NAME", "").strip()
    return api_key, api_base, model


def video_gen_configured() -> bool:
    """Whether the Video processing config (key/base/model) is complete.

    Public entry point for callers outside this module (interface_deep.py,
    swarm/providers/tools.py) that only need a yes/no gate and shouldn't
    depend on _get_video_gen_api_credentials' private tuple shape.
    """
    return _credentials_complete(*_get_video_gen_api_credentials())


def _credentials_complete(api_key: str, api_base: str, model: str) -> bool:
    if gen_toolkits.detect_backend("VIDEO_GEN_PROTOCOL", api_base) in gen_toolkits.KEYLESS_BACKENDS:
        return bool(api_base)
    return bool(api_key and api_base and model)


def parse_aspect_ratio(value: str | None) -> float | None:
    """``"9:16"`` -> ``0.5625`` (width/height); ``None`` when unusable.

    Only the ``W:H`` form is understood - that is what every caller in this
    project passes (the lab's cards, the composer presets). Anything else
    (empty, ``"adaptive"``, ``"16x9"``, a zero denominator) returns ``None``,
    which callers read as "no target ratio, don't touch the frame".
    """
    text = (value or "").strip()
    if ":" not in text:
        return None
    left, _, right = text.partition(":")
    try:
        width, height = float(left), float(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _center_crop_to_ratio(img: "Image.Image", ratio: float) -> "Image.Image":
    """Center-crop ``img`` to ``ratio`` (width/height), keeping the middle.

    Center is the right anchor for the material this feature handles: generated
    scenes and product shots put the subject near the middle, so a center crop
    keeps it. A ratio already within :data:`_ASPECT_RATIO_TOLERANCE` is returned
    untouched so an ordinary 16:9 frame is never needlessly re-encoded.
    """
    width, height = img.size
    if height <= 0 or width <= 0:
        return img
    if abs((width / height) - ratio) < _ASPECT_RATIO_TOLERANCE:
        return img
    if width / height > ratio:
        # Wider than the target: trim the sides.
        new_width = max(1, round(height * ratio))
        left = (width - new_width) // 2
        return img.crop((left, 0, left + new_width, height))
    # Taller than the target: trim top and bottom.
    new_height = max(1, round(width / ratio))
    top = (height - new_height) // 2
    return img.crop((0, top, width, top + new_height))


def _downscale_reference_image(
    path: Path, target_ratio: float | None = None
) -> tuple[bytes, str]:
    """Downscale + re-encode a local reference image as JPEG (see module-level
    comment above _MAX_REFERENCE_DIMENSION for why).

    ``target_ratio`` (width/height) center-crops first, because a frame that
    disagrees with the requested ``aspect_ratio`` overrides it at the provider
    (see _ASPECT_RATIO_TOLERANCE). Crop before thumbnail so the output keeps the
    requested shape while still bounded by _MAX_REFERENCE_DIMENSION.
    """
    with Image.open(path) as img:
        img = img.convert("RGB")
        if target_ratio:
            cropped = _center_crop_to_ratio(img, target_ratio)
            if cropped.size != img.size:
                logger.info(
                    "[generate_video] 首/尾帧按请求比例重新构图: %dx%d -> %dx%d",
                    img.size[0], img.size[1], cropped.size[0], cropped.size[1],
                )
            img = cropped
        img.thumbnail((_MAX_REFERENCE_DIMENSION, _MAX_REFERENCE_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_REFERENCE_JPEG_QUALITY)
        return buf.getvalue(), "image/jpeg"


def _clamp_duration(model: str, duration_seconds: int) -> int:
    compact = "".join(ch for ch in model.lower() if ch.isalnum())
    low = _H3_MAX_MIN_SECONDS if "h3max" in compact else _VIDEO_MIN_SECONDS
    return max(low, min(_VIDEO_MAX_SECONDS, int(duration_seconds)))


def _resolve_frame_reference(
    path_or_url: str, target_ratio: float | None = None, arg_name: str = "first_frame_path"
) -> tuple[str | None, str | None]:
    """first_frame_path may be a real http(s) URL, an already-complete data:
    URI, or a local file path - resolved to a data: URI here (server-side)
    rather than asked of the calling agent, since a large base64 blob is not
    something an LLM can reliably reproduce byte-for-byte through its own
    generation.

    ``target_ratio`` is honored only for a **local file**, since cropping a
    remote http(s)/data: URI would mean downloading and re-encoding it here; a
    remote frame is passed through untouched and may therefore win over the
    requested ratio at the provider. ``arg_name`` names the caller's own
    parameter in the error message (e.g. "reference_image_paths" for one entry
    of that list), since this helper is shared across several differently-named
    path/URL arguments.
    """
    value = (path_or_url or "").strip()
    if not value:
        return None, None
    if value.startswith(("http://", "https://", "data:")):
        if target_ratio:
            logger.warning(
                "[generate_video] 首/尾帧是远程 URL/data URI，无法在本端按请求比例重新构图，"
                "成片比例可能由该帧决定而非 aspect_ratio"
            )
        return value, None
    path = Path(value).expanduser()
    if not path.is_file():
        return None, f"[ERROR]: {arg_name} {value!r} is not a URL/data URI and no such file exists."
    try:
        data, mime = _downscale_reference_image(path, target_ratio)
    except Exception:
        logger.exception("[generate_video] downscaling reference image failed, using raw file: %s", path)
        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(str(path))
        if not mime or not mime.startswith("image/"):
            mime = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}", None


def _resolve_audio_reference(path_or_url: str) -> tuple[str | None, str | None]:
    """Resolve a reference audio to a data: URI.

    Same contract as _resolve_frame_reference (accepts an http(s) URL, an
    already-complete data: URI, or a local file path) minus the image-specific
    downscaling - there is no re-encoding step for audio here.
    """
    value = (path_or_url or "").strip()
    if not value:
        return None, None
    if value.startswith(("http://", "https://", "data:")):
        return value, None
    path = Path(value).expanduser()
    if not path.is_file():
        return None, (
            f"[ERROR]: reference_audio_path {value!r} is not a URL/data URI and no such file exists."
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"[ERROR]: reading reference_audio_path {value!r} failed: {exc!r}"
    limit_mb = _MAX_REFERENCE_AUDIO_BYTES // (1024 * 1024)
    if len(data) > _MAX_REFERENCE_AUDIO_BYTES:
        return None, (
            f"[ERROR]: reference_audio_path {value!r} is {len(data) // (1024 * 1024)}MB, "
            f"over the {limit_mb}MB limit for an embedded audio reference."
        )
    mime = _AUDIO_EXTENSION_MIME.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0]
    if not mime or not mime.startswith("audio/"):
        mime = "audio/mpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", None


def _resolve_video_reference(path_or_url: str) -> tuple[str | None, str | None]:
    """Resolve a reference video to the HTTPS URL the provider has to fetch.

    Unlike images and audio there is deliberately **no** local-file / data: URI
    fallback here: the provider fetches reference videos itself and rejects every
    non-HTTPS address with "Invalid reference URL: ... Only HTTPS URLs are
    allowed" (verified against the live API). Embedding a clip as base64 would
    only produce the same rejection after a much larger upload, so a local path
    fails fast with an actionable message instead.
    """
    value = (path_or_url or "").strip()
    if not value:
        return None, None
    if value.startswith("https://"):
        return value, None
    return None, (
        f"[ERROR]: reference_video_path {value[:60]!r} is not a usable reference video: the "
        "provider downloads reference videos itself and only accepts publicly reachable "
        "https:// URLs (local files, data: URIs and plain http:// are rejected with "
        "'Only HTTPS URLs are allowed'). Host the clip somewhere public and pass that URL."
    )


def _resolve_save_path(save_dir: str | None, filename: str) -> Path:
    root = Path(save_dir).expanduser() if save_dir else (get_agent_workspace_dir() / "generated_videos")
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


@dataclass(frozen=True)
class _JobContext:
    """Shared per-request transport/identity context for a video-generation
    job - bundles what would otherwise be 3 separate, always-travel-together
    parameters (client/headers/job_id) across _poll_job/_download_video and
    their callers (see G.FNM.03: too many arguments)."""

    client: httpx.AsyncClient
    headers: dict[str, str]
    job_id: str


async def _download_video(ctx: _JobContext, api_base: str, save_dir: str | None) -> str:
    try:
        content = await ctx.client.get(
            f"{api_base}/videos/{ctx.job_id}/content", params={"index": 0}, headers=ctx.headers
        )
    except httpx.HTTPError as exc:
        return f"[ERROR]: downloading video job {ctx.job_id} failed: {exc!r}"
    if content.status_code != 200:
        return f"[ERROR]: video job {ctx.job_id} completed but downloading content failed: {content.status_code}"

    filename = f"video_{ctx.job_id}.mp4"
    try:
        target = _resolve_save_path(save_dir, filename)
        target.write_bytes(content.content)
    except OSError as exc:
        return f"[ERROR]: failed to save video ({filename}) to {save_dir or 'agent workspace'}: {exc!r}"
    return (
        "Video generated successfully!\n"
        f"Saved to: {target}\n"
        f"(job {ctx.job_id} - the remote source URL expires 24h after completion, so this "
        "local file is now the durable copy.)"
    )


async def _poll_job(
    ctx: _JobContext,
    polling_url: str,
    status: str,
    job: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], int, str | None]:
    """Poll until the job reaches a terminal status or the poll budget runs
    out.

    Returns (status, job, elapsed_seconds, error). error is None on success
    (including the ordinary "still running after budget" case, which the
    caller reports itself); when set, the caller should return it directly
    as the tool's result rather than relying on status/job/elapsed. A
    non-200 poll response or a malformed JSON body is reported this way
    instead of raised: a raised exception here would only be caught by
    generate_video's ``except httpx.HTTPError``, and neither RuntimeError
    nor json.JSONDecodeError (a ValueError) is an instance of that, which
    previously let both crash the tool call instead of degrading gracefully.
    """
    # Seeded with the caller's already-known job state (e.g. the submit
    # response) rather than {}, so that a job already terminal on submit -
    # no polling iteration ever runs - doesn't lose its "error" field to an
    # empty dict here.
    job = dict(job) if job else {}
    elapsed = 0
    while status not in _TERMINAL_STATUSES and elapsed < _MAX_POLL_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS
        poll = await ctx.client.get(polling_url, headers=ctx.headers)
        if poll.status_code != 200:
            return status, job, elapsed, (
                f"[ERROR]: polling video job {ctx.job_id} failed: {poll.status_code} {poll.text}"
            )
        try:
            job = poll.json()
        except ValueError as exc:
            return status, job, elapsed, (
                f"[ERROR]: polling video job {ctx.job_id} returned invalid JSON: {exc!r}"
            )
        status = job.get("status", "pending")
    return status, job, elapsed, None


@tool(
    name="generate_video",
    description=(
        "Generate a video clip from a text prompt (and optionally a first-frame and/or "
        "last-frame image, reference image/audio/video inputs, or multiple reference images) "
        "using AI video generation models. Use this tool when the user wants to create or "
        "generate a video based on a text description. Video generation can take several "
        "minutes; if this tool returns a job_id instead of a finished video, call "
        "check_video_status with that job_id to keep waiting for it instead of resubmitting."
    ),
)
async def generate_video(  # pylint: disable=huawei-too-many-arguments
    prompt: str,
    aspect_ratio: str = "16:9",
    resolution: str = "480p",
    duration_seconds: int = 15,
    first_frame_path: str | None = None,
    last_frame_path: str | None = None,
    reference_image_path: str | None = None,
    reference_audio_path: str | None = None,
    reference_video_path: str | None = None,
    generate_audio: bool = False,
    save_dir: str | None = None,
    reference_image_paths: list[str] | None = None,
    reference_mode: bool = False,
) -> str:
    """
    Generate a video from a text prompt.

    Deliberately not bundled into a dataclass/BaseModel despite the arg
    count (see G.FNM.03): this signature is what CallableSchemaExtractor
    (openjiuwen) auto-extracts into the tool's JSON tool-calling schema for
    the LLM. A plain dataclass param collapses to an opaque, propertyless
    `{"type": "object"}` there (no registered extractor for dataclasses,
    only pydantic BaseModel) - confirmed directly against
    CallableSchemaExtractor.get_type_schema - which would leave the model
    with no visible fields to call this tool with at all. A BaseModel would
    expand correctly, but changes the calling convention from flat
    top-level args to one nested object, which is a real behavioral change
    to an already-verified tool, not a mechanical refactor - flat args are
    kept intentionally.

    Args:
        prompt: Text description of the video to generate.
        aspect_ratio: e.g. "16:9", "9:16", "1:1". With a first/last frame this is
            enforced by center-cropping that frame to the requested ratio, because
            the provider otherwise derives the output geometry from the frame and
            ignores this field.
        resolution: e.g. "480p", "720p", "1080p".
        duration_seconds: Clip length in seconds (model-dependent, typically 5-15).
        first_frame_path: Optional local file path, http(s) URL, or data: URI to
            condition the first frame on (image-to-video).
        last_frame_path: Optional local file path, http(s) URL, or data: URI to
            condition the last frame on - combined with first_frame_path this asks
            the model to interpolate a clip between the two (model-dependent support;
            the OpenRouter /videos path only - native backends have no last-frame slot).
        reference_image_path: Optional local file path, http(s) URL, or data: URI
            used as a *reference* (not an exact frame) for reference-to-video.
            Unlike first_frame_path this is passed as a multimodal reference, so
            the prompt can talk about it (e.g. "@Image1 is a girl in a park").
        reference_audio_path: Optional local file path, http(s) URL, or data: URI
            of an audio clip used as a reference - models that support it (BytePlus
            Seedance 2.0 and newer) lip-sync the generated video to this audio.
            References are only honored by providers that support them.
        reference_video_path: Optional **https:// URL** of a video clip to restyle
            (video-to-video): the model re-renders the clip according to prompt
            while keeping its subject and motion. Must be a publicly reachable
            HTTPS URL - unlike the image/audio references there is no local-file
            fallback, because the provider fetches it itself and rejects every
            non-HTTPS address.
        generate_audio: Whether to request native audio generation, if supported
            by the model. Leave false when reference_audio_path supplies the audio.
        save_dir: Optional directory to save the video (defaults to the agent
            workspace's generated_videos/ folder).
        reference_image_paths: Optional local file paths, http(s) URLs or data:
            URIs of images (characters, props, scenes) the clip must stay
            consistent with (reference-to-video). Native backends only (MiniMax,
            BytePlus ModelArk, Alibaba DashScope, vLLM-Omni) - use reference_image_path
            instead on the OpenRouter /videos path.
        reference_mode: Treat first_frame_path as one more reference image
            instead of the literal opening frame. Native backends only.

    Returns:
        Path to the generated video file, or a job_id + status message if the
        job is still running after the bounded poll window.
    """
    api_key, api_base, model = _get_video_gen_api_credentials()
    if not _credentials_complete(api_key, api_base, model):
        return (
            "[ERROR]: video generation is not configured - set the Video processing "
            "API key, API URL, and model name in configuration settings."
        )
    prompt = (prompt or "").strip()
    if not prompt:
        return "[ERROR]: prompt is required."

    frame_images: list[dict[str, Any]] = []
    # 首/尾帧必须与请求的画面比例一致：服务商在有帧时按**帧的比例**决定成片
    # 几何，aspect_ratio 字段会被整个忽略（实测 16:9 帧 + 9:16 请求 → 输出
    # 1280x720）。所以这里把请求比例传给帧解析，由它对本地帧重新构图（居中裁切）。
    target_ratio = parse_aspect_ratio(aspect_ratio)
    frame_data_uri: str | None = None
    if first_frame_path:
        frame_data_uri, err = _resolve_frame_reference(first_frame_path, target_ratio)
        if err:
            return err
        frame_images.append({"type": "image_url", "image_url": {"url": frame_data_uri}, "frame_type": "first_frame"})
    if last_frame_path:
        last_frame_data_uri, err = _resolve_frame_reference(last_frame_path, target_ratio)
        if err:
            return err
        frame_images.append({"type": "image_url", "image_url": {"url": last_frame_data_uri}, "frame_type": "last_frame"})

    # Multimodal references (reference-to-video) over the OpenRouter-style path: an
    # image reference, an audio reference to lip-sync to, a video to restyle, or any
    # combination. Deliberately a separate channel from frame_images - OpenRouter
    # treats a request carrying both as plain image-to-video and drops
    # input_references entirely, so callers use one or the other, never both.
    reference_image_data_uri: str | None = None
    if reference_image_path:
        reference_image_data_uri, err = _resolve_frame_reference(
            reference_image_path, arg_name="reference_image_path"
        )
        if err:
            return err
    reference_audio_data_uri: str | None = None
    if reference_audio_path:
        reference_audio_data_uri, err = _resolve_audio_reference(reference_audio_path)
        if err:
            return err
    # 参考视频走 video_url 引用（视频生视频）：与图片/音频参考并列放在
    # input_references 里，服务商自己去抓这条地址。
    reference_video_url: str | None = None
    if reference_video_path:
        reference_video_url, err = _resolve_video_reference(reference_video_path)
        if err:
            return err

    input_references: list[dict[str, Any]] = []
    if reference_image_data_uri:
        input_references.append({"type": "image_url", "image_url": {"url": reference_image_data_uri}})
    if reference_audio_data_uri:
        input_references.append({"type": "audio_url", "audio_url": {"url": reference_audio_data_uri}})
    if reference_video_url:
        input_references.append({"type": "video_url", "video_url": {"url": reference_video_url}})

    # Multi-reference images (reference_image_paths/reference_mode): native backends
    # only (MiniMax/ModelArk/DashScope/vLLM-Omni via gen_toolkits) - resolved here so
    # the "not configured for reference images" error below covers both this and the
    # pre-existing OpenRouter-direct references above.
    reference_uris: list[str] = []
    for path in reference_image_paths or []:
        uri, err = _resolve_frame_reference(path, arg_name="reference_image_paths")
        if err:
            return err
        reference_uris.append(uri)
    if reference_mode and not (reference_uris or frame_data_uri):
        return "[ERROR]: reference_mode needs reference_image_paths or first_frame_path."

    # MiniMax (v2 task API), BytePlus ModelArk (Seedance), DashScope (Wan) and
    # vLLM-Omni are not OpenRouter's /videos API, so they have their own backends.
    backend = gen_toolkits.detect_backend("VIDEO_GEN_PROTOCOL", api_base)
    if backend:
        # 这些专用后端（MiniMax / BytePlus ModelArk）的 VideoRequest 没有参考视频
        # 这一路，静默丢掉就等于用户填了地址却拿回一段和源片无关的视频。
        if reference_video_url:
            return (
                "[ERROR]: reference_video_path (video-to-video) is only supported by the "
                f"OpenRouter /videos backend, but this deployment uses {backend!r}."
            )
        target = gen_toolkits.GenerationTarget(backend, api_key, api_base, model)
        if backend != gen_toolkits.VLLM_OMNI:
            duration_seconds = _clamp_duration(model, duration_seconds)
        request = gen_toolkits.VideoRequest(
            prompt,
            aspect_ratio,
            resolution,
            duration_seconds,
            generate_audio,
            frame_data_uri,
            reference_image_data_uri,
            reference_audio_data_uri,
            reference_image_uris=tuple(reference_uris),
            reference_mode=reference_mode,
        )
        result = await gen_toolkits.submit_video(target, request, save_dir)
        _schedule_completion_watch_if_pending(result, save_dir)
        return result
    if reference_uris or reference_mode:
        return (
            f"[ERROR]: the configured video model {model!r} ({api_base}) only supports text-to-video and "
            "first-frame image-to-video, not reference images. Retry with first_frame_path or reference_image_path "
            "only, or ask the user to switch Settings > Agent (Video generation) to a provider that supports "
            "reference-to-video: MiniMax (MiniMax-H3), BytePlus ModelArk / Volcengine (Seedance 2.x), Alibaba "
            "DashScope (wan3.0-video / wan3.0-video-prime) or a self-deployed vLLM-Omni server."
        )

    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration": duration_seconds,
        "generate_audio": bool(generate_audio),
    }
    if frame_images:
        body["frame_images"] = frame_images
    if input_references:
        body["input_references"] = input_references

    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info(
        "[generate_video] using model: %s (api_base: %s, aspect_ratio: %s, resolution: %s, "
        "duration: %ss, frame_images: %s, references: %s)",
        model, api_base, aspect_ratio, resolution, duration_seconds,
        [f["frame_type"] for f in frame_images],
        [r["type"] for r in input_references],
    )

    # 提交步骤单独重试（不是把整个 submit+poll+download 都包进重试）：
    # 观察到的失败模式是提交这一步偶发被对端直接断连（并发多个
    # generate_video/generate_visual 请求同时打到服务商时更容易触发，见
    # visual_gen_tools 模块同款注释），不是任务真的跑失败了；如果把整个
    # 流程都重试，一旦断连发生在"提交已经成功、正在轮询"之后，重试会
    # 再提交一个新任务，白白重复消耗一次生成额度。只重试提交本身，提交
    # 成功后走正常的一次性 poll/下载流程，出问题就如实报错，不再重试。
    submit_client: httpx.AsyncClient | None = None
    submit: httpx.Response | None = None
    last_submit_exc: httpx.HTTPError | None = None
    try:
        for attempt in range(_TRANSIENT_RETRY_ATTEMPTS):
            submit_client = httpx.AsyncClient(timeout=60, verify=get_requests_verify())
            try:
                submit = await submit_client.post(f"{api_base}/videos", headers=headers, json=body)
                break
            except httpx.RemoteProtocolError as exc:
                await submit_client.aclose()
                submit_client = None
                last_submit_exc = exc
                if attempt + 1 >= _TRANSIENT_RETRY_ATTEMPTS:
                    return f"[ERROR]: video generation submit failed after {attempt + 1} attempts: {exc!r}"
                logger.warning(
                    "[generate_video] transient connection drop on submit (attempt %d/%d), retrying: %r",
                    attempt + 1, _TRANSIENT_RETRY_ATTEMPTS, exc,
                )
                await asyncio.sleep(_TRANSIENT_RETRY_BASE_DELAY_S * (attempt + 1))

        if submit is None or submit_client is None:
            return f"[ERROR]: video generation submit failed after {_TRANSIENT_RETRY_ATTEMPTS} attempts: {last_submit_exc!r}"

        if submit.status_code not in (200, 201, 202):
            return f"[ERROR]: video generation submit failed: {submit.status_code} {submit.text}"
        try:
            job = submit.json()
        except ValueError as exc:
            return f"[ERROR]: video generation submit returned invalid JSON: {exc!r}"
        job_id = job.get("id")
        if not job_id:
            return f"[ERROR]: video generation submit returned no job id: {job}"
        polling_url = job.get("polling_url") or f"{api_base}/videos/{job_id}"
        status = job.get("status", "pending")
        ctx = _JobContext(client=submit_client, headers=headers, job_id=job_id)

        status, job, elapsed, poll_error = await _poll_job(ctx, polling_url, status, job)
        if poll_error:
            return poll_error

        if status not in _TERMINAL_STATUSES:
            pending_message = (
                f"Video job {job_id} submitted and still {status} after {elapsed}s - generation can "
                f"take several minutes. Call check_video_status with job_id={job_id} to check progress "
                "and download it once ready."
            )
            _schedule_completion_watch_if_pending(pending_message, save_dir)
            return pending_message
        if status != "completed":
            return (
                f"[ERROR]: video job {job_id} ended with status {status}: "
                f"{job.get('error', 'no error detail provided')}"
            )

        return await _download_video(ctx, api_base, save_dir)
    except httpx.HTTPError as exc:
        return f"[ERROR]: video generation request failed: {exc!r}"
    finally:
        if submit_client is not None:
            await submit_client.aclose()


async def _check_video_status_impl(job_id: str, save_dir: str | None = None) -> str:
    api_key, api_base, model = _get_video_gen_api_credentials()
    backend = gen_toolkits.detect_backend("VIDEO_GEN_PROTOCOL", api_base)
    if not (api_base and (api_key or backend in gen_toolkits.KEYLESS_BACKENDS)):
        return (
            "[ERROR]: video generation is not configured - set the Video processing "
            "API key, API URL, and model name in configuration settings."
        )
    job_id = (job_id or "").strip()
    if not job_id:
        return "[ERROR]: job_id is required."

    if backend:
        target = gen_toolkits.GenerationTarget(backend, api_key, api_base, model)
        result = await gen_toolkits.check_video(target, job_id, save_dir)
        _schedule_completion_watch_if_pending(result, save_dir)
        return result

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30, verify=get_requests_verify()) as client:
            poll = await client.get(f"{api_base}/videos/{job_id}", headers=headers)
            if poll.status_code != 200:
                return f"[ERROR]: checking video job {job_id} failed: {poll.status_code} {poll.text}"
            try:
                job = poll.json()
            except ValueError as exc:
                return f"[ERROR]: checking video job {job_id} returned invalid JSON: {exc!r}"
            status = job.get("status", "unknown")
            if status == "completed":
                ctx = _JobContext(client=client, headers=headers, job_id=job_id)
                return await _download_video(ctx, api_base, save_dir)
            if status in _TERMINAL_STATUSES:
                return (
                    f"[ERROR]: video job {job_id} ended with status {status}: "
                    f"{job.get('error', 'no error detail provided')}"
                )
            pending_message = f"Video job {job_id} is still {status}."
            _schedule_completion_watch_if_pending(pending_message, save_dir)
            return pending_message
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        # A transient provider-reachability blip is not a terminal outcome -
        # the job keeps running server-side. Reporting it as [ERROR] here
        # would fail _PENDING_JOB_ID_RE and make _watch_until_settled treat
        # the job as settled, pushing a bogus failure notice and giving up
        # instead of retrying (matches gen_toolkits' own backends, which all
        # distinguish these via _TRANSIENT_HTTP_ERRORS/_unreachable_job_message).
        pending_message = (
            f"Video job {job_id} is still unconfirmed - the provider could not be reached ({exc!r}). "
            "Call check_video_status again to keep waiting (do not use shell sleep)."
        )
        _schedule_completion_watch_if_pending(pending_message, save_dir)
        return pending_message
    except httpx.HTTPError as exc:
        return f"[ERROR]: checking video job {job_id} failed: {exc!r}"


@tool(
    name="check_video_status",
    description=(
        "Poll a previously submitted generate_video job by job_id and download it once "
        "complete. Use this after generate_video returns a job_id instead of a finished video."
    ),
)
async def check_video_status(job_id: str, save_dir: str | None = None) -> str:
    """
    Check the status of a video generation job, downloading it if ready.

    Args:
        job_id: The job id returned by generate_video.
        save_dir: Optional directory to save the video (defaults to the agent
            workspace's generated_videos/ folder).

    Returns:
        Path to the generated video file, or a status message if still running.
    """
    return await _check_video_status_impl(job_id, save_dir)
