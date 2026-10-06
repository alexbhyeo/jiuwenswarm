# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for jiuwenswarm.agents.harness.common.tools.audio_gen_tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from jiuwenswarm.agents.harness.common.tools import audio_gen_tools as ag

# The @tool decorator wraps the function in a LocalFunction; ``._func`` is the
# original plain async function it wraps, callable directly with the same
# keyword arguments - see LocalFunction.__init__ (openjiuwen). Testing through
# this avoids bootstrapping the whole tool-invocation/callback framework that
# the wrapper's own __call__ wires up, which is out of scope for a unit test.
generate_audio = ag.generate_audio._func

_TEST_API_BASE = "https://audio-model.example/api/v1"
_TEST_MODEL = "example/audio-gen-model"
_SPEECH_PATH = "/api/v1/audio/speech"


def _clear_audio_gen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AUDIO_GEN_API_KEY",
        "AUDIO_GEN_API_BASE",
        "AUDIO_GEN_MODEL_NAME",
        "AUDIO_GEN_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_audio_gen_config(monkeypatch: pytest.MonkeyPatch, *, api_key: str = "sk-test") -> None:
    """Configure the dedicated "Audio generation" panel slot the tool reads."""
    monkeypatch.setenv("AUDIO_GEN_API_KEY", api_key)
    monkeypatch.setenv("AUDIO_GEN_API_BASE", _TEST_API_BASE)
    monkeypatch.setenv("AUDIO_GEN_MODEL_NAME", _TEST_MODEL)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with a clean slate for audio-gen env vars."""
    _clear_audio_gen_env(monkeypatch)
    # Keep the transient-retry backoff from sleeping in tests.
    monkeypatch.setattr(ag, "_TRANSIENT_RETRY_BASE_DELAY_S", 0)
    yield


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Force every httpx.AsyncClient() constructed by the module under test to
    route through a MockTransport, so no real network call is ever made."""
    real_async_client = httpx.AsyncClient

    class _PatchedAsyncClient(real_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)


def _audio_response(payload: bytes = b"ID3fakeaudiobytes", content_type: str = "audio/mpeg") -> httpx.Response:
    return httpx.Response(200, content=payload, headers={"Content-Type": content_type})


# ---------------------------------------------------------------------------
# audio_gen_enabled - the "Audio generation" settings switch
# ---------------------------------------------------------------------------


def test_audio_gen_enabled_false_when_unset():
    assert ag.audio_gen_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_audio_gen_enabled_true_for_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("AUDIO_GEN_ENABLED", value)
    assert ag.audio_gen_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", "", "garbage"])
def test_audio_gen_enabled_false_for_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("AUDIO_GEN_ENABLED", value)
    assert ag.audio_gen_enabled() is False


# ---------------------------------------------------------------------------
# _get_audio_gen_api_credentials / audio_gen_configured
# ---------------------------------------------------------------------------


def test_credentials_empty_when_unset():
    api_key, api_base, model = ag._get_audio_gen_api_credentials()
    assert (api_key, api_base, model) == ("", "", "")


def test_credentials_read_from_dedicated_slot(monkeypatch: pytest.MonkeyPatch):
    _set_audio_gen_config(monkeypatch, api_key="sk-from-audio-panel")

    api_key, api_base, model = ag._get_audio_gen_api_credentials()

    assert api_key == "sk-from-audio-panel"
    assert api_base == _TEST_API_BASE
    assert model == _TEST_MODEL


def test_credentials_partial_config_leaves_missing_fields_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUDIO_GEN_API_KEY", "sk-only-key-set")

    api_key, api_base, model = ag._get_audio_gen_api_credentials()

    assert api_key == "sk-only-key-set"
    assert api_base == ""
    assert model == ""


def test_audio_gen_configured_requires_all_three_fields(monkeypatch: pytest.MonkeyPatch):
    assert ag.audio_gen_configured() is False

    _set_audio_gen_config(monkeypatch)
    assert ag.audio_gen_configured() is True

    monkeypatch.delenv("AUDIO_GEN_MODEL_NAME")
    assert ag.audio_gen_configured() is False


def test_credentials_ignore_the_audio_understanding_slot(monkeypatch: pytest.MonkeyPatch):
    """AUDIO_* drives audio understanding (audio_tools.py); it must not be read
    as a fallback here, or a transcription model would silently become the TTS
    model."""
    monkeypatch.setenv("AUDIO_API_KEY", "sk-understanding")
    monkeypatch.setenv("AUDIO_API_BASE", "https://understanding.example/v1")
    monkeypatch.setenv("AUDIO_MODEL_NAME", "whisper-like")

    assert ag._get_audio_gen_api_credentials() == ("", "", "")
    assert ag.audio_gen_configured() is False


# ---------------------------------------------------------------------------
# generate_audio - happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_audio_success_saves_audio_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_audio_gen_config(monkeypatch)
    audio_bytes = b"ID3\x03\x00fakeencodedaudio"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        assert request.url.path == _SPEECH_PATH
        return _audio_response(audio_bytes)

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(
        text="Hello! This is a text-to-speech test.",
        voice="Kore",
        save_dir=str(tmp_path),
    )

    assert "Audio generated successfully!" in result
    saved_files = list(tmp_path.glob("*.mp3"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == audio_bytes
    assert str(saved_files[0]) in result

    submitted_body = json.loads(requests_seen[0].content)
    assert submitted_body["model"] == _TEST_MODEL
    assert submitted_body["input"] == "Hello! This is a text-to-speech test."
    assert submitted_body["voice"] == "Kore"
    # The default request is pcm, but this provider answers with audio/mpeg -
    # a known Content-Type wins, so the bytes are saved verbatim as .mp3 rather
    # than being wrapped in a WAV header.
    assert submitted_body["response_format"] == "pcm"
    assert requests_seen[0].headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_generate_audio_uses_defaults_when_unspecified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_audio_gen_config(monkeypatch)
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return _audio_response()

    _patch_async_client(monkeypatch, handler)

    await generate_audio(text="narration", save_dir=str(tmp_path))

    submitted_body = json.loads(requests_seen[0].content)
    assert submitted_body["voice"] == "Zephyr"
    # pcm: the only encoding the Gemini TTS family accepts (mp3 is rejected
    # live with a 400). The PCM response is wrapped into a WAV container.
    assert submitted_body["response_format"] == "pcm"


@pytest.mark.asyncio
async def test_generate_audio_accepts_trailing_slash_in_api_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A pasted base with a trailing slash must not produce a double slash."""
    monkeypatch.setenv("AUDIO_GEN_API_KEY", "sk-test")
    monkeypatch.setenv("AUDIO_GEN_API_BASE", _TEST_API_BASE + "/")
    monkeypatch.setenv("AUDIO_GEN_MODEL_NAME", _TEST_MODEL)
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return _audio_response()

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hi", save_dir=str(tmp_path))

    assert seen_paths == [_SPEECH_PATH]
    assert "Audio generated successfully!" in result


@pytest.mark.asyncio
async def test_generate_audio_uses_content_type_for_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The provider's Content-Type decides the extension when it is specific."""
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(b"RIFFfakewav", content_type="audio/wav")

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hi", response_format="pcm", save_dir=str(tmp_path))

    saved = list(tmp_path.glob("*"))
    assert len(saved) == 1
    assert saved[0].suffix == ".wav"
    assert str(saved[0]) in result


@pytest.mark.asyncio
async def test_generate_audio_generic_content_type_falls_back_to_requested_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """An opaque Content-Type is not decisive, so the requested format wins -
    and a pcm body is wrapped into a playable WAV."""
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(b"rawpcmbytes", content_type="application/octet-stream")

    _patch_async_client(monkeypatch, handler)

    await generate_audio(text="hi", response_format="pcm", save_dir=str(tmp_path))

    saved = list(tmp_path.glob("*.wav"))
    assert len(saved) == 1
    assert saved[0].read_bytes().startswith(b"RIFF")


@pytest.mark.asyncio
async def test_generate_audio_wraps_pcm_into_playable_wav(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A PCM body is saved as a WAV with a correct RIFF header, not raw .pcm -
    otherwise the file cannot be opened by a player."""
    _set_audio_gen_config(monkeypatch)
    pcm = b"\x01\x02\x03\x04" * 250  # 1000 bytes of 16-bit mono samples

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(pcm, content_type="audio/pcm")

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hi", save_dir=str(tmp_path))

    saved = list(tmp_path.glob("*.wav"))
    assert len(saved) == 1
    body = saved[0].read_bytes()
    assert body[:4] == b"RIFF"
    assert body[8:12] == b"WAVE"
    assert body[12:16] == b"fmt "
    assert int.from_bytes(body[16:20], "little") == 16  # fmt chunk size
    assert int.from_bytes(body[20:22], "little") == 1  # audio format: linear PCM
    assert int.from_bytes(body[22:24], "little") == 1  # mono
    assert int.from_bytes(body[24:28], "little") == 24000  # sample rate
    assert int.from_bytes(body[28:32], "little") == 48000  # byte rate
    assert int.from_bytes(body[34:36], "little") == 16  # bits per sample
    assert body[36:40] == b"data"
    assert int.from_bytes(body[40:44], "little") == len(pcm)
    assert body[44:] == pcm
    assert str(saved[0]) in result
    assert not list(tmp_path.glob("*.pcm"))


@pytest.mark.asyncio
async def test_generate_audio_respects_custom_pcm_sample_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A provider whose PCM rate differs can be described correctly."""
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(b"\x00\x00" * 100, content_type="audio/pcm")

    _patch_async_client(monkeypatch, handler)

    await generate_audio(text="hi", sample_rate_hz=48000, save_dir=str(tmp_path))

    body = next(tmp_path.glob("*.wav")).read_bytes()
    assert int.from_bytes(body[24:28], "little") == 48000
    assert int.from_bytes(body[28:32], "little") == 96000


@pytest.mark.asyncio
async def test_generate_audio_concurrent_calls_do_not_overwrite_each_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Two calls in the same second must land in two distinct files."""
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(b"ID3audio")

    _patch_async_client(monkeypatch, handler)

    first = await generate_audio(text="one", save_dir=str(tmp_path))
    second = await generate_audio(text="two", save_dir=str(tmp_path))

    assert first != second
    assert len(list(tmp_path.glob("*.mp3"))) == 2


# ---------------------------------------------------------------------------
# generate_audio - error / edge paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_audio_unconfigured_returns_error_without_calling_network(
    monkeypatch: pytest.MonkeyPatch,
):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request should be sent when unconfigured")

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hello")

    assert result.startswith("[ERROR]: audio generation is not configured")


@pytest.mark.asyncio
async def test_generate_audio_requires_text(monkeypatch: pytest.MonkeyPatch):
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request should be sent for blank text")

    _patch_async_client(monkeypatch, handler)

    assert await generate_audio(text="   ") == "[ERROR]: text is required."


@pytest.mark.asyncio
async def test_generate_audio_non_200_extracts_provider_error_message(
    monkeypatch: pytest.MonkeyPatch,
):
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": 400, "message": "unsupported voice"}})

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="a cat")

    assert result == "[ERROR]: audio generation request failed: 400 unsupported voice"


@pytest.mark.asyncio
async def test_generate_audio_non_200_with_plain_text_body(monkeypatch: pytest.MonkeyPatch):
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="missing or invalid API key")

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="a cat")

    assert result == "[ERROR]: audio generation request failed: 401 missing or invalid API key"


@pytest.mark.asyncio
async def test_generate_audio_empty_body_returns_error(monkeypatch: pytest.MonkeyPatch):
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response(b"", content_type="audio/mpeg")

    _patch_async_client(monkeypatch, handler)

    assert await generate_audio(text="hello") == "[ERROR]: audio generation returned an empty response body."


@pytest.mark.asyncio
async def test_generate_audio_http_error_is_caught(monkeypatch: pytest.MonkeyPatch):
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="a cat")

    assert result.startswith("[ERROR]: audio generation request failed:")


@pytest.mark.asyncio
async def test_generate_audio_retries_transient_connection_drop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A RemoteProtocolError on the first attempt is retried, not surfaced."""
    _set_audio_gen_config(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return _audio_response()

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hello", save_dir=str(tmp_path))

    assert len(attempts) == 2
    assert "Audio generated successfully!" in result


@pytest.mark.asyncio
async def test_generate_audio_gives_up_after_max_transient_retries(
    monkeypatch: pytest.MonkeyPatch,
):
    _set_audio_gen_config(monkeypatch)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    _patch_async_client(monkeypatch, handler)

    result = await generate_audio(text="hello")

    assert len(attempts) == ag._TRANSIENT_RETRY_ATTEMPTS
    assert result.startswith("[ERROR]: audio generation request failed after 3 attempts:")


@pytest.mark.asyncio
async def test_generate_audio_save_failure_returns_error_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A disk-full/permission-denied write must surface as a clean [ERROR]
    string, not an unhandled OSError - save_dir is agent-controlled input."""
    _set_audio_gen_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _audio_response()

    _patch_async_client(monkeypatch, handler)

    def _raise_disk_full(self, data):
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _raise_disk_full)

    result = await generate_audio(text="hello", save_dir=str(tmp_path))

    assert result.startswith("[ERROR]: failed to save audio")
    assert "No space left on device" in result


# ---------------------------------------------------------------------------
# helpers: _speech_endpoint / _resolve_save_path / _extension_for_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_base, expected",
    [
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/audio/speech"),
        ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1/audio/speech"),
        ("https://audio-model.example/api/v1", "https://audio-model.example/api/v1/audio/speech"),
    ],
)
def test_speech_endpoint(api_base: str, expected: str):
    assert ag._speech_endpoint(api_base) == expected


def test_resolve_save_path_defaults_to_agent_workspace_generated_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(ag, "get_agent_workspace_dir", lambda: tmp_path)

    target = ag._resolve_save_path(None, "audio_abc.mp3")

    assert target == tmp_path / "generated_audio" / "audio_abc.mp3"
    assert target.parent.is_dir()


def test_resolve_save_path_honors_custom_save_dir(tmp_path: Path):
    custom_dir = tmp_path / "my_audio"

    target = ag._resolve_save_path(str(custom_dir), "audio_abc.mp3")

    assert target == custom_dir / "audio_abc.mp3"
    assert custom_dir.is_dir()


@pytest.mark.parametrize(
    "content_type, response_format, expected",
    [
        ("audio/mpeg", "mp3", "mp3"),
        ("audio/mp3", "mp3", "mp3"),
        ("audio/pcm", "mp3", "pcm"),
        ("audio/wav", "mp3", "wav"),
        ("audio/x-wav", "mp3", "wav"),
        ("audio/ogg", "mp3", "ogg"),
        ("audio/flac", "mp3", "flac"),
        ("audio/mpeg; charset=binary", "mp3", "mp3"),
        ("application/octet-stream", "pcm", "pcm"),
        ("application/octet-stream", "opus", "opus"),
        ("", "mp3", "mp3"),
        ("application/octet-stream", "unknown-format", "mp3"),
    ],
)
def test_extension_for_response(content_type: str, response_format: str, expected: str):
    assert ag._extension_for_response(content_type, response_format) == expected


@pytest.mark.parametrize(
    "content_type, response_format, expected",
    [
        # A known audio Content-Type is decisive.
        ("audio/pcm", "mp3", True),
        ("audio/pcm; charset=binary", "mp3", True),
        ("audio/mpeg", "pcm", False),
        # Generic / missing Content-Type falls back to what was requested.
        ("application/octet-stream", "pcm", True),
        ("application/octet-stream", "mp3", False),
        ("", "pcm", True),
        ("", "mp3", False),
    ],
)
def test_is_pcm_payload(content_type: str, response_format: str, expected: bool):
    assert ag._is_pcm_payload(content_type, response_format) is expected
