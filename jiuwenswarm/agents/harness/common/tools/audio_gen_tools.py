# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Text-to-speech (TTS) generation tool via OpenRouter's dedicated audio
endpoint (e.g. google/gemini-3.8-flash-lite-tts).

Unlike image generation (visual_gen_tools.py, a chat-completions call that
returns the image inline) this is a plain synchronous request/response call to
``POST {api_base}/audio/speech`` whose body is **raw audio bytes**, not JSON -
see https://openrouter.ai/docs/api/api-reference/tts/create-speech. There is
no submit-then-poll job to track, so a single call is the whole operation.

Credentials come from a dedicated "Audio generation" config panel slot
(AUDIO_GEN_API_KEY/AUDIO_GEN_API_BASE/AUDIO_GEN_MODEL_NAME, plus the
AUDIO_GEN_ENABLED switch) - deliberately *separate* from the "Audio
processing" slot audio_tools.py reads (AUDIO_API_KEY/AUDIO_API_BASE/
AUDIO_MODEL_NAME), which serves audio *understanding* (transcription and
audio question answering). Unlike the video/visual generation slots there is
no provider/protocol pair here: OpenRouter's TTS endpoint has a single
request shape, so there is no vendor-native backend to dispatch to. Keeping
the two slots independent means a speech-to-text
model and a text-to-speech model can be configured side by side without one
disabling the other - the same split already used for VIDEO_* (understanding)
vs VIDEO_GEN_* (generation) and VISION_* (understanding) vs VISUAL_GEN_*
(generation).

There is no built-in default endpoint/model: whatever's configured in the
dedicated slot above is what this tool uses, and api_key/api_base/model must
all be set.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from pathlib import Path

import httpx
from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

# See visual_gen_tools._TRANSIENT_RETRY_* for why: several generation requests
# hitting the same provider concurrently occasionally get the connection cut at
# submit time (RemoteProtocolError) rather than a clean 4xx/5xx. That is a
# provider-side concurrency artefact, not a bad request, so a short backoff and
# retry usually succeeds.
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_BASE_DELAY_S = 1.5

# Voices the Gemini TTS family exposes (pass-through: the list is neither
# closed nor fully documented, and the endpoint itself returns a clean 400 for
# an unsupported one, so it is not hard-validated here).
_DEFAULT_VOICE = "Zephyr"

# "pcm" is the only encoding the Gemini TTS family accepts - asking for mp3 is
# rejected live with `400 Gemini TTS only supports response_format="pcm"`.
# PCM is headerless raw audio that no player opens directly, so the PCM branch
# in generate_audio wraps it in a WAV container; a model that does accept mp3
# keeps its raw bytes and gets an .mp3.
_DEFAULT_RESPONSE_FORMAT = "pcm"

# Gemini TTS returns 24 kHz, 16-bit, mono PCM. Verified live: a 62-character
# sentence came back as 259200 bytes, i.e. 5.40 s at this format, matching the
# expected ~5 s of speech (it would read as 2.70 s if the samples were 32-bit).
_DEFAULT_PCM_SAMPLE_RATE_HZ = 24000
_PCM_SAMPLE_WIDTH_BYTES = 2
_PCM_CHANNELS = 1

_RESPONSE_FORMAT_EXTENSIONS = {
    "mp3": "mp3",
    "pcm": "pcm",
    "wav": "wav",
    "opus": "opus",
    "aac": "aac",
    "flac": "flac",
}

_CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/pcm": "pcm",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


def audio_gen_enabled() -> bool:
    """Whether the audio-generation switch in configuration settings is on.

    Mirrors visual_gen_tools.visual_gen_enabled's env-var gate: AUDIO_GEN_ENABLED
    is set from that switch and checked independently of whether the Audio
    generation config itself is complete, so a user can leave valid credentials
    in place while still turning generation off.
    """
    return str(os.environ.get("AUDIO_GEN_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def _get_audio_gen_api_credentials() -> tuple[str, str, str]:
    """Resolve TTS credentials from the dedicated "Audio generation" config
    panel slot only (AUDIO_GEN_API_KEY/AUDIO_GEN_API_BASE/AUDIO_GEN_MODEL_NAME).
    No fallback env vars, no built-in default endpoint or model - an empty
    string means unconfigured.
    """
    api_key = os.environ.get("AUDIO_GEN_API_KEY", "").strip()
    api_base = os.environ.get("AUDIO_GEN_API_BASE", "").strip()
    model = os.environ.get("AUDIO_GEN_MODEL_NAME", "").strip()
    return api_key, api_base, model


def audio_gen_configured() -> bool:
    """Whether the Audio generation config (key/base/model) is complete."""
    api_key, api_base, model = _get_audio_gen_api_credentials()
    return bool(api_key and api_base and model)


def _speech_endpoint(api_base: str) -> str:
    """Join the configured API base with the TTS path.

    The panel slot holds an OpenAI-style base that already ends in ``/v1``
    (e.g. ``https://openrouter.ai/api/v1``), so the endpoint is that base plus
    ``/audio/speech``. A trailing slash is tolerated so a pasted
    ``https://openrouter.ai/api/v1/`` does not produce a double slash.
    """
    return f"{api_base.rstrip('/')}/audio/speech"


def _resolve_save_path(save_dir: str | None, filename: str) -> Path:
    root = Path(save_dir).expanduser() if save_dir else (get_agent_workspace_dir() / "generated_audio")
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def _wrap_pcm_as_wav(data: bytes, sample_rate_hz: int) -> bytes:
    """Prepend a 44-byte RIFF/WAVE header to headerless PCM samples.

    OpenRouter returns raw PCM for the Gemini TTS family, which no player opens
    as-is - the samples are already correct, only the container is missing.
    Building the header inline (rather than via the ``wave`` module) keeps this
    dependency-free and makes the exact byte layout explicit.
    """
    channels = _PCM_CHANNELS
    sample_width = _PCM_SAMPLE_WIDTH_BYTES
    data_len = len(data)
    header = b"".join(
        (
            b"RIFF",
            (36 + data_len).to_bytes(4, "little"),
            b"WAVE",
            b"fmt ",
            (16).to_bytes(4, "little"),
            (1).to_bytes(2, "little"),  # audio format: 1 = linear PCM
            channels.to_bytes(2, "little"),
            sample_rate_hz.to_bytes(4, "little"),
            (sample_rate_hz * channels * sample_width).to_bytes(4, "little"),
            (channels * sample_width).to_bytes(2, "little"),
            (sample_width * 8).to_bytes(2, "little"),
            b"data",
            data_len.to_bytes(4, "little"),
        )
    )
    return header + data


def _is_pcm_payload(content_type: str, response_format: str) -> bool:
    """Whether the response body is headerless PCM and needs WAV wrapping.

    A Content-Type naming a known audio encoding is decisive; a generic or
    missing one (e.g. application/octet-stream) falls back to the encoding that
    was requested.
    """
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in _CONTENT_TYPE_EXTENSIONS:
        return mime == "audio/pcm"
    return response_format.strip().lower() == "pcm"


def _extension_for_response(content_type: str, response_format: str) -> str:
    """Pick a file extension from the response Content-Type, falling back to
    the requested response_format when the provider sends a generic type."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in _CONTENT_TYPE_EXTENSIONS:
        return _CONTENT_TYPE_EXTENSIONS[mime]
    return _RESPONSE_FORMAT_EXTENSIONS.get(response_format.strip().lower(), "mp3")


def _error_detail(resp: httpx.Response) -> str:
    """Extract a provider error message, tolerating a non-JSON error body."""
    try:
        payload = resp.json()
    except ValueError:
        return resp.text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return resp.text


@tool(
    name="generate_audio",
    description=(
        "Convert text into spoken audio (text-to-speech) using an AI speech "
        "generation model. Use this tool when the user wants a voiceover, "
        "narration, or any text read aloud as an audio file. Returns the path "
        "to the saved audio file."
    ),
)
async def generate_audio(
    text: str,
    voice: str = _DEFAULT_VOICE,
    response_format: str = _DEFAULT_RESPONSE_FORMAT,
    sample_rate_hz: int = _DEFAULT_PCM_SAMPLE_RATE_HZ,
    save_dir: str | None = None,
) -> str:
    """
    Synthesize speech from text.

    Args:
        text: The text to speak. This is sent verbatim as the synthesis input.
        voice: Voice name, e.g. "Zephyr", "Puck", "Charon", "Kore", "Fenrir",
            "Aoede". Available voices depend on the configured model; an
            unsupported one comes back as a clear provider error.
        response_format: Output audio encoding. "pcm" is the only value the
            Gemini TTS family accepts; a model that also accepts "mp3" can be
            asked for it. A PCM response is wrapped into a WAV container before
            saving, so the saved file is playable either way.
        sample_rate_hz: Sample rate assumed when wrapping PCM into WAV.
            Defaults to the 24 kHz the Gemini TTS family emits.
        save_dir: Optional directory to save the audio (defaults to the agent
            workspace's generated_audio/ folder).

    Returns:
        Path to the generated audio file, or an error message.
    """
    api_key, api_base, model = _get_audio_gen_api_credentials()
    if not (api_key and api_base and model):
        return (
            "[ERROR]: audio generation is not configured - set the Audio generation "
            "API key, API URL, and model name in configuration settings."
        )
    text = (text or "").strip()
    if not text:
        return "[ERROR]: text is required."

    voice = (voice or "").strip() or _DEFAULT_VOICE
    response_format = (response_format or "").strip().lower() or _DEFAULT_RESPONSE_FORMAT

    body = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    url = _speech_endpoint(api_base)
    logger.info(
        "[generate_audio] using model: %s (api_base: %s, voice: %s, response_format: %s, chars: %d)",
        model, api_base, voice, response_format, len(text),
    )

    resp: httpx.Response | None = None
    last_exc: httpx.HTTPError | None = None
    for attempt in range(_TRANSIENT_RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=120, verify=get_requests_verify()) as client:
                resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                return (
                    f"[ERROR]: audio generation request failed: "
                    f"{resp.status_code} {_error_detail(resp)}"
                )
            break
        except httpx.RemoteProtocolError as exc:
            last_exc = exc
            if attempt + 1 >= _TRANSIENT_RETRY_ATTEMPTS:
                return f"[ERROR]: audio generation request failed after {attempt + 1} attempts: {exc!r}"
            logger.warning(
                "[generate_audio] transient connection drop (attempt %d/%d), retrying: %r",
                attempt + 1, _TRANSIENT_RETRY_ATTEMPTS, exc,
            )
            await asyncio.sleep(_TRANSIENT_RETRY_BASE_DELAY_S * (attempt + 1))
        except httpx.HTTPError as exc:
            return f"[ERROR]: audio generation request failed: {exc!r}"

    if resp is None:
        return f"[ERROR]: audio generation request failed after {_TRANSIENT_RETRY_ATTEMPTS} attempts: {last_exc!r}"

    audio_bytes = resp.content
    if not audio_bytes:
        # A 200 with an empty body is not usable audio; surfacing it here beats
        # writing a zero-byte file the caller would only discover later.
        return "[ERROR]: audio generation returned an empty response body."

    content_type = resp.headers.get("content-type", "")
    if _is_pcm_payload(content_type, response_format):
        # Headerless PCM: wrap it in a WAV container so the saved file opens in
        # a player rather than being a stream of samples with no header.
        payload = _wrap_pcm_as_wav(audio_bytes, max(1, int(sample_rate_hz)))
        ext = "wav"
    else:
        payload = audio_bytes
        ext = _extension_for_response(content_type, response_format)
    # A second-resolution timestamp alone can collide when several generate_audio
    # calls run concurrently (see visual_gen_tools' equivalent note), so a random
    # suffix keeps two calls from computing the same filename and one silently
    # overwriting the other.
    filename = f"audio_{int(time.time())}_{secrets.token_hex(4)}.{ext}"
    try:
        target = _resolve_save_path(save_dir, filename)
        target.write_bytes(payload)
    except OSError as exc:
        return f"[ERROR]: failed to save audio ({filename}) to {save_dir or 'agent workspace'}: {exc!r}"

    return f"Audio generated successfully!\nSaved to: {target}"
