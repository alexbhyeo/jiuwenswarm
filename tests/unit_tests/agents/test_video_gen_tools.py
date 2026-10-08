# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for jiuwenswarm.agents.harness.common.tools.video_gen_tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from jiuwenswarm.agents.harness.common.tools import video_gen_tools as vg

# The @tool decorator wraps each function in a LocalFunction; ``._func`` is the
# original plain async function it wraps, callable directly with the same
# keyword arguments - see LocalFunction.__init__ (openjiuwen). Testing through
# this avoids bootstrapping the whole tool-invocation/callback framework that
# the wrapper's own __call__ wires up, which is out of scope for a unit test.
generate_video = vg.generate_video._func


_TEST_API_BASE = "https://video-model.example/api/v1"
_TEST_MODEL = "example/video-gen-model"


def _clear_video_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VIDEO_GEN_API_KEY", "VIDEO_GEN_API_BASE", "VIDEO_GEN_MODEL_NAME", "VIDEO_GEN_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def _set_video_model_config(monkeypatch: pytest.MonkeyPatch, *, api_key: str = "sk-test") -> None:
    """Configure the dedicated "Video processing" panel slot the tools read."""
    monkeypatch.setenv("VIDEO_GEN_API_KEY", api_key)
    monkeypatch.setenv("VIDEO_GEN_API_BASE", _TEST_API_BASE)
    monkeypatch.setenv("VIDEO_GEN_MODEL_NAME", _TEST_MODEL)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with a clean slate for video-gen env vars."""
    _clear_video_env(monkeypatch)
    yield


def _mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Force every httpx.AsyncClient() constructed by the module under test to
    route through a MockTransport, so no real network call is ever made."""
    real_async_client = httpx.AsyncClient

    class _PatchedAsyncClient(real_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = _mock_transport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)


def _speed_up_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the poll loop's interval/budget to the same value, so it runs
    exactly one real (but ~10ms) iteration instead of up to _MAX_POLL_SECONDS
    wall-clock seconds - ``elapsed`` is a plain accumulator incremented by
    ``_POLL_INTERVAL_SECONDS`` each pass (see _poll_job), so setting both to
    0.01 guarantees the loop body runs once and then exits (0.01 < 0.01 is
    False), deterministically."""
    monkeypatch.setattr(vg, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(vg, "_MAX_POLL_SECONDS", 0.01)


# ---------------------------------------------------------------------------
# video_gen_enabled - the "Video processing" settings switch
# ---------------------------------------------------------------------------


def test_video_gen_enabled_false_when_unset():
    assert vg.video_gen_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_video_gen_enabled_true_for_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("VIDEO_GEN_ENABLED", value)
    assert vg.video_gen_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", "", "garbage"])
def test_video_gen_enabled_false_for_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("VIDEO_GEN_ENABLED", value)
    assert vg.video_gen_enabled() is False


# ---------------------------------------------------------------------------
# _get_video_gen_api_credentials
# ---------------------------------------------------------------------------


def test_credentials_empty_when_video_model_config_unset():
    api_key, api_base, model = vg._get_video_gen_api_credentials()
    assert (api_key, api_base, model) == ("", "", "")


def test_credentials_read_only_from_video_model_config_panel(monkeypatch: pytest.MonkeyPatch):
    _set_video_model_config(monkeypatch, api_key="sk-from-video-model-panel")

    api_key, api_base, model = vg._get_video_gen_api_credentials()

    assert api_key == "sk-from-video-model-panel"
    assert api_base == _TEST_API_BASE
    assert model == _TEST_MODEL


def test_credentials_partial_config_leaves_missing_fields_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIDEO_GEN_API_KEY", "sk-only-key-set")
    # VIDEO_GEN_API_BASE / VIDEO_GEN_MODEL_NAME intentionally left unset.

    api_key, api_base, model = vg._get_video_gen_api_credentials()

    assert api_key == "sk-only-key-set"
    assert api_base == ""
    assert model == ""


# ---------------------------------------------------------------------------
# _resolve_save_path
# ---------------------------------------------------------------------------


def test_resolve_save_path_defaults_to_agent_workspace_generated_videos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(vg, "get_agent_workspace_dir", lambda: tmp_path)

    target = vg._resolve_save_path(None, "video_abc.mp4")

    assert target == tmp_path / "generated_videos" / "video_abc.mp4"
    assert target.parent.is_dir()


def test_resolve_save_path_honors_custom_save_dir(tmp_path: Path):
    custom_dir = tmp_path / "my_videos"

    target = vg._resolve_save_path(str(custom_dir), "video_abc.mp4")

    assert target == custom_dir / "video_abc.mp4"
    assert custom_dir.is_dir()


# ---------------------------------------------------------------------------
# generate_video - happy path (job completes within the first poll window)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_success_downloads_and_saves_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    video_bytes = b"fake-mp4-bytes"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(
                200, json={"id": "job-123", "status": "completed", "polling_url": None}
            )
        if request.url.path == "/api/v1/videos/job-123/content":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="a golden retriever puppy running through a meadow",
        aspect_ratio="16:9",
        resolution="480p",
        duration_seconds=5,
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    saved_path = tmp_path / "video_job-123.mp4"
    assert str(saved_path) in result
    assert saved_path.read_bytes() == video_bytes

    submit_request = requests_seen[0]
    submitted_body = json.loads(submit_request.content)
    assert submitted_body["model"] == _TEST_MODEL
    assert submitted_body["prompt"] == "a golden retriever puppy running through a meadow"
    assert submitted_body["aspect_ratio"] == "16:9"
    assert submitted_body["resolution"] == "480p"
    assert submitted_body["duration"] == 5
    assert submit_request.headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_generate_video_save_failure_returns_error_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A disk-full/permission-denied write must surface as a clean [ERROR]
    string, not an unhandled OSError - save_dir is agent-controlled input."""
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-123", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-123/content":
            return httpx.Response(200, content=b"fake-mp4-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    def _raise_disk_full(self, data):
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _raise_disk_full)

    result = await generate_video(prompt="a puppy running", save_dir=str(tmp_path))

    assert result.startswith("[ERROR]: failed to save video")
    assert "No space left on device" in result


@pytest.mark.asyncio
async def test_generate_video_includes_frame_images_for_image_to_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    frame_path = tmp_path / "first_frame.png"
    frame_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-456", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-456/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="animate this scene",
        first_frame_path=str(frame_path),
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    submitted_body = json.loads(requests_seen[0].content)
    assert "frame_images" in submitted_body
    assert submitted_body["frame_images"][0]["frame_type"] == "first_frame"
    assert submitted_body["frame_images"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


# ---------------------------------------------------------------------------
# generate_video - error / edge paths from the remote API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_submit_failure_returns_error(monkeypatch: pytest.MonkeyPatch):
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request: invalid model")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a puppy running")

    assert result == "[ERROR]: video generation submit failed: 400 bad request: invalid model"


@pytest.mark.asyncio
async def test_generate_video_submit_without_job_id_returns_error(monkeypatch: pytest.MonkeyPatch):
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "queued"})

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a puppy running")

    assert result.startswith("[ERROR]: video generation submit returned no job id")


@pytest.mark.asyncio
async def test_generate_video_terminal_failed_status_returns_error(monkeypatch: pytest.MonkeyPatch):
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"id": "job-err", "status": "failed", "error": "model overloaded"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a puppy running")

    assert result == (
        "[ERROR]: video job job-err ended with status failed: model overloaded"
    )


@pytest.mark.asyncio
async def test_generate_video_still_running_after_poll_window_reports_job_id(
    monkeypatch: pytest.MonkeyPatch,
):
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-slow", "status": "pending"})
        # Exactly one poll GET happens (see _speed_up_polling); it reports
        # still-running, so the loop exits after this single iteration.
        return httpx.Response(200, json={"id": "job-slow", "status": "running"})

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a puppy running")

    assert "job-slow" in result
    assert "check_video_status" in result
    assert "job_id=job-slow" in result


@pytest.mark.asyncio
async def test_generate_video_http_error_during_submit_is_caught(monkeypatch: pytest.MonkeyPatch):
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a puppy running")

    assert result.startswith("[ERROR]: video generation request failed:")


# ---------------------------------------------------------------------------
# _download_video / _poll_job (direct helper coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_video_non_200_returns_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    _patch_async_client(monkeypatch, handler)

    async with httpx.AsyncClient() as client:
        ctx = vg._JobContext(client=client, headers={}, job_id="job-x")
        result = await vg._download_video(ctx, _TEST_API_BASE, None)

    assert result == "[ERROR]: video job job-x completed but downloading content failed: 500"


@pytest.mark.asyncio
async def test_poll_job_returns_error_on_non_200_poll_response(monkeypatch: pytest.MonkeyPatch):
    _speed_up_polling(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    _patch_async_client(monkeypatch, handler)

    async with httpx.AsyncClient() as client:
        ctx = vg._JobContext(client=client, headers={}, job_id="job-y")
        _, _, _, error = await vg._poll_job(ctx, f"{_TEST_API_BASE}/videos/job-y", "pending")

    assert error == "[ERROR]: polling video job job-y failed: 503 service unavailable"


@pytest.mark.asyncio
async def test_poll_job_returns_error_on_invalid_json_poll_response(monkeypatch: pytest.MonkeyPatch):
    _speed_up_polling(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _patch_async_client(monkeypatch, handler)

    async with httpx.AsyncClient() as client:
        ctx = vg._JobContext(client=client, headers={}, job_id="job-z")
        _, _, _, error = await vg._poll_job(ctx, f"{_TEST_API_BASE}/videos/job-z", "pending")

    assert error is not None
    assert error.startswith("[ERROR]: polling video job job-z returned invalid JSON:")


@pytest.mark.asyncio
async def test_generate_video_returns_error_when_submit_response_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
):
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="a cat")

    assert result.startswith("[ERROR]: video generation submit returned invalid JSON:")


# ---------------------------------------------------------------------------
# generate_video - reference-to-video inputs (reference image + reference audio)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_sends_reference_image_and_audio_as_input_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """图音生视频：参考图和参考音频一起走 input_references，并且不能再带
    frame_images——OpenRouter 对同时带两者的请求按"纯图生视频"处理，会丢掉
    input_references，参考音频就白连了。"""
    _set_video_model_config(monkeypatch)
    reference_image = tmp_path / "ref.png"
    reference_image.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    reference_audio = tmp_path / "voice.wav"
    reference_audio.write_bytes(b"RIFF....WAVEfmt ")
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-ref", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-ref/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="@Image1 is a cute girl standing in a park. She lip-syncs to @Audio1.",
        reference_image_path=str(reference_image),
        reference_audio_path=str(reference_audio),
        duration_seconds=5,
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    submitted_body = json.loads(requests_seen[0].content)
    assert "frame_images" not in submitted_body
    references = submitted_body["input_references"]
    assert [r["type"] for r in references] == ["image_url", "audio_url"]
    assert references[0]["image_url"]["url"].startswith("data:image/")
    assert references[1]["audio_url"]["url"].startswith("data:audio/wav;base64,")


@pytest.mark.asyncio
async def test_generate_video_audio_reference_only_keeps_reference_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    reference_audio = tmp_path / "voice.mp3"
    reference_audio.write_bytes(b"ID3fake")
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-a", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-a/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="sing this", reference_audio_path=str(reference_audio), save_dir=str(tmp_path)
    )

    assert "Video generated successfully!" in result
    references = json.loads(requests_seen[0].content)["input_references"]
    assert references == [
        {"type": "audio_url", "audio_url": {"url": references[0]["audio_url"]["url"]}}
    ]
    assert references[0]["audio_url"]["url"].startswith("data:audio/mpeg;base64,")


def test_resolve_audio_reference_rejects_missing_file(tmp_path: Path):
    data_uri, error = vg._resolve_audio_reference(str(tmp_path / "nope.mp3"))

    assert data_uri is None
    assert error is not None and "reference_audio_path" in error


def test_resolve_audio_reference_rejects_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vg, "_MAX_REFERENCE_AUDIO_BYTES", 8)
    oversized = tmp_path / "big.mp3"
    oversized.write_bytes(b"0123456789")

    data_uri, error = vg._resolve_audio_reference(str(oversized))

    assert data_uri is None
    assert error is not None and "over the 0MB limit" in error


def test_resolve_audio_reference_defaults_to_mpeg_for_unknown_extension(tmp_path: Path):
    unknown = tmp_path / "clip.bin"
    unknown.write_bytes(b"raw-audio")

    data_uri, error = vg._resolve_audio_reference(str(unknown))

    assert error is None
    assert data_uri is not None and data_uri.startswith("data:audio/mpeg;base64,")


@pytest.mark.asyncio
async def test_generate_video_oversized_audio_reference_fails_before_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    monkeypatch.setattr(vg, "_MAX_REFERENCE_AUDIO_BYTES", 4)
    big = tmp_path / "big.mp3"
    big.write_bytes(b"0123456789")
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be hit
        requests_seen.append(request)
        raise AssertionError("no request should be submitted for an oversized reference audio")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(prompt="p", reference_audio_path=str(big), save_dir=str(tmp_path))

    assert result.startswith("[ERROR]:") and "reference_audio_path" in result
    assert requests_seen == []
