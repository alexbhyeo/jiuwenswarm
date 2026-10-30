# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for jiuwenswarm.agents.harness.common.tools.video_gen_tools."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import httpx
import pytest

from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    RootDecisionContext,
    bind_root_decision_context,
    reset_root_decision_context,
)
from jiuwenswarm.agents.harness.common.tools import send_file_to_user as sfu
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
    for name in (
        "VIDEO_GEN_API_KEY", "VIDEO_GEN_API_BASE", "VIDEO_GEN_MODEL_NAME", "VIDEO_GEN_ENABLED", "VIDEO_GEN_PROTOCOL",
    ):
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
# Reference-to-video and self-deployed (vLLM-Omni) backends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_references_on_openrouter_point_to_supported_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    ref = tmp_path / "hero.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request expected: {request.url}")

    _patch_async_client(monkeypatch, handler)
    result = await generate_video(prompt="hero walks", reference_image_paths=[str(ref)])
    assert result.startswith("[ERROR]: the configured video model")
    assert "first_frame_path" in result and "wan3.0-video" in result and "vLLM-Omni" in result

    result = await generate_video(prompt="hero walks", first_frame_path=str(ref), reference_mode=True)
    assert result.startswith("[ERROR]: the configured video model")


@pytest.mark.asyncio
async def test_generate_video_reference_argument_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_video_model_config(monkeypatch)
    missing = await generate_video(prompt="p", reference_image_paths=[str(tmp_path / "nope.png")])
    assert missing.startswith("[ERROR]: reference_image_paths") and "no such file" in missing
    empty = await generate_video(prompt="p", reference_mode=True)
    assert empty == "[ERROR]: reference_mode needs reference_image_paths or first_frame_path."


@pytest.mark.asyncio
async def test_generate_video_forwards_references_to_native_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_video_model_config(monkeypatch)
    monkeypatch.setenv("VIDEO_GEN_PROTOCOL", "dashscope")
    ref = tmp_path / "hero.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    captured: dict[str, Any] = {}

    async def fake_submit(target: Any, request: Any, save_dir: str | None) -> str:
        captured.update(target=target, request=request)
        return "Video generated successfully!\nSaved to: /tmp/v.mp4"

    monkeypatch.setattr(vg.gen_toolkits, "submit_video", fake_submit)
    result = await generate_video(prompt="hero walks", reference_image_paths=[str(ref)], reference_mode=True)
    assert result.startswith("Video generated successfully!")
    assert captured["target"].backend == "dashscope"
    request = captured["request"]
    assert request.reference_mode is True
    assert len(request.reference_image_uris) == 1 and request.reference_image_uris[0].startswith("data:image/png")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,model,requested,expected",
    [
        ("minimax", "MiniMax-H3", 99, 15),
        ("minimax", "MiniMax-H3", 1, 4),
        ("minimax", "MiniMax-H3-Max", 4, 5),
        ("modelark", "doubao-seedance-2-5-260628", 25, 15),
        ("vllm-omni", "MiniMax-H3", 2, 2),
    ],
)
async def test_generate_video_clamps_duration_for_native_backends(
    monkeypatch: pytest.MonkeyPatch, protocol: str, model: str, requested: int, expected: int
):
    _set_video_model_config(monkeypatch)
    monkeypatch.setenv("VIDEO_GEN_PROTOCOL", protocol)
    monkeypatch.setenv("VIDEO_GEN_MODEL_NAME", model)
    captured: dict[str, Any] = {}

    async def fake_submit(target: Any, request: Any, save_dir: str | None) -> str:
        captured.update(request=request)
        return "Video generated successfully!\nSaved to: /tmp/v.mp4"

    monkeypatch.setattr(vg.gen_toolkits, "submit_video", fake_submit)
    await generate_video(prompt="fox", duration_seconds=requested)
    assert captured["request"].duration_seconds == expected


def test_vllm_omni_is_configured_without_key_or_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIDEO_GEN_API_BASE", "http://127.0.0.1:8091/v1")
    assert vg.video_gen_configured() is False
    monkeypatch.setenv("VIDEO_GEN_PROTOCOL", "vllm-omni")
    assert vg.video_gen_configured() is True


# ---------------------------------------------------------------------------
# Background completion watcher (GitCode #5806)
#
# generate_video/check_video_status poll synchronously for a bounded window
# and, if the job is still running, tell the agent to call check_video_status
# again. In Team/multi-agent mode the round's stream can end before anyone
# does that, so the finished video was never reaching the user. These tests
# cover the background watcher that keeps polling after the bounded window
# gives up and pushes the outcome into the chat once the job settles.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_watch_registries():
    vg._WATCHED_PENDING_JOBS.clear()
    vg._BACKGROUND_WATCH_TASKS.clear()
    yield
    vg._WATCHED_PENDING_JOBS.clear()
    vg._BACKGROUND_WATCH_TASKS.clear()


def _root_context(
    session_id: str = "sess-1", request_id: str = "req-1", channel_id: str = "web"
) -> RootDecisionContext:
    return RootDecisionContext(
        session_id=session_id, request_id=request_id, channel_id=channel_id, trusted_turns=(),
    )


@contextlib.contextmanager
def _bound_root_context(context: RootDecisionContext):
    token = bind_root_decision_context(context)
    try:
        yield
    finally:
        reset_root_decision_context(token)


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Video job job-abc submitted and still pending after 10s - generation can take "
            "several minutes. Call check_video_status with job_id=job-abc to check progress "
            "and download it once ready.",
            "job-abc",
        ),
        ("Video job job-xyz is still running.", "job-xyz"),
        (
            "Video job job-xyz is still running. Call check_video_status again to keep waiting "
            "(do not use shell sleep).",
            "job-xyz",
        ),
        (
            "Video job job-unreachable is still unconfirmed - the provider could not be reached "
            "(ConnectError()). Call check_video_status again to keep waiting (do not use shell sleep).",
            "job-unreachable",
        ),
        ("Video generated successfully!\nSaved to: /tmp/v.mp4", None),
        ("[ERROR]: video job job-err ended with status failed: boom", None),
        ("", None),
    ],
)
def test_extract_pending_job_id(message: str, expected: str | None):
    assert vg._extract_pending_job_id(message) == expected


def test_schedule_completion_watch_noop_without_root_context():
    vg._schedule_completion_watch_if_pending("Video job job-1 is still running.", None)
    assert vg._WATCHED_PENDING_JOBS == set()
    assert vg._BACKGROUND_WATCH_TASKS == set()


def test_schedule_completion_watch_noop_on_terminal_message():
    with _bound_root_context(_root_context()):
        vg._schedule_completion_watch_if_pending("Video generated successfully!\nSaved to: /tmp/v.mp4", None)
    assert vg._WATCHED_PENDING_JOBS == set()
    assert vg._BACKGROUND_WATCH_TASKS == set()


@pytest.mark.asyncio
async def test_schedule_completion_watch_dedupes_same_job_id(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def fake_watch(job_id: str, save_dir: str | None, context: RootDecisionContext) -> None:
        calls.append(job_id)
        await asyncio.sleep(10)  # never resolves within the test

    monkeypatch.setattr(vg, "_watch_until_settled", fake_watch)
    with _bound_root_context(_root_context()):
        vg._schedule_completion_watch_if_pending("Video job job-dup is still running.", None)
        vg._schedule_completion_watch_if_pending("Video job job-dup is still running.", None)
        # Scheduling a task only queues it on the loop; yield once so it
        # actually starts running (up to its first await) before asserting.
        await asyncio.sleep(0)

    assert calls == ["job-dup"]
    assert len(vg._BACKGROUND_WATCH_TASKS) == 1
    for task in vg._BACKGROUND_WATCH_TASKS:
        task.cancel()
    await asyncio.gather(*vg._BACKGROUND_WATCH_TASKS, return_exceptions=True)


@pytest.mark.asyncio
async def test_generate_video_pending_job_delivers_file_via_watcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A job still pending when generate_video's bounded poll window gives up
    must still reach the chat once it finishes - nobody is guaranteed to call
    check_video_status again on its behalf once the turn (or, in Team mode,
    the whole round) has already ended."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    video_bytes = b"fake-mp4-bytes"
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-watch", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-watch":
            poll_count["n"] += 1
            # First poll (inside generate_video's own bounded wait) is still
            # pending; the background watcher's own poll finds it done.
            status = "pending" if poll_count["n"] == 1 else "completed"
            return httpx.Response(200, json={"id": "job-watch", "status": status})
        if request.url.path == "/api/v1/videos/job-watch/content":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), _bound_root_context(_root_context(session_id="sess-watch", request_id="req-watch")):
        result = await generate_video(prompt="a cyclist riding through a park", save_dir=str(tmp_path))
        assert "job-watch" in result
        assert "check_video_status" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert "job-watch" not in vg._WATCHED_PENDING_JOBS
    assert len(pushed) == 1
    assert pushed[0]["payload"]["event_type"] == "chat.file"
    assert pushed[0]["session_id"] == "sess-watch"
    files = pushed[0]["payload"]["files"]
    assert files[0]["name"] == "video_job-watch.mp4"
    saved_path = tmp_path / "video_job-watch.mp4"
    assert saved_path.read_bytes() == video_bytes


@pytest.mark.asyncio
async def test_generate_video_pending_job_pushes_error_when_it_eventually_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """A job that settles into a failure state after the bounded window also
    must not vanish silently - the user otherwise sees nothing at all."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-fail", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-fail":
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return httpx.Response(200, json={"id": "job-fail", "status": "pending"})
            return httpx.Response(
                200, json={"id": "job-fail", "status": "failed", "error": "model overloaded"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    monkeypatch.setattr(vg, "send_runtime_push", _push)

    with _bound_root_context(_root_context(session_id="sess-fail", request_id="req-fail")):
        result = await generate_video(prompt="a puppy running")
        assert "job-fail" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert len(pushed) == 1
    payload = pushed[0]["payload"]
    assert payload["event_type"] == "chat.final"
    assert payload["final_mode"] == vg.FINAL_MODE_APPEND
    assert "job-fail" in payload["content"]
    assert "model overloaded" in payload["content"]
    assert pushed[0]["session_id"] == "sess-fail"


@pytest.mark.asyncio
async def test_watcher_gives_up_after_max_watch_seconds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(vg, "_WATCH_MAX_SECONDS", 0.02)

    async def _always_pending(job_id: str, save_dir: str | None = None) -> str:
        return f"Video job {job_id} is still running."

    monkeypatch.setattr(vg, "_check_video_status_impl", _always_pending)
    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    monkeypatch.setattr(vg, "send_runtime_push", _push)

    with _bound_root_context(_root_context()):
        vg._schedule_completion_watch_if_pending("Video job job-stuck is still running.", None)
        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert pushed == []
    assert "job-stuck" not in vg._WATCHED_PENDING_JOBS


@pytest.mark.asyncio
async def test_check_video_status_also_arms_watcher_when_still_pending(
    monkeypatch: pytest.MonkeyPatch,
):
    """The agent re-calling check_video_status (instead of generate_video)
    after a first timeout must also arm the watcher - this is the path an
    agent actually takes per generate_video's own returned instructions."""
    _set_video_model_config(monkeypatch)
    check_video_status = vg.check_video_status._func

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job-check", "status": "running"})

    _patch_async_client(monkeypatch, handler)

    with _bound_root_context(_root_context()):
        result = await check_video_status("job-check")
        assert "job-check" in result
        assert "is still running" in result
        assert "job-check" in vg._WATCHED_PENDING_JOBS

    for task in vg._BACKGROUND_WATCH_TASKS:
        task.cancel()
    await asyncio.gather(*vg._BACKGROUND_WATCH_TASKS, return_exceptions=True)


@pytest.mark.asyncio
async def test_check_video_status_transient_network_error_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
):
    """A transient provider-reachability blip (ConnectError etc.) on the
    legacy/direct API path must not look like a terminal failure to
    _extract_pending_job_id - otherwise _watch_until_settled treats a
    still-running job as settled, pushes a bogus failure notice, and gives
    up instead of retrying."""
    _set_video_model_config(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_async_client(monkeypatch, handler)

    with _bound_root_context(_root_context()):
        result = await vg._check_video_status_impl("job-flaky")

    assert "job-flaky" in result
    assert "is still unconfirmed" in result
    assert vg._extract_pending_job_id(result) == "job-flaky"
    assert "job-flaky" in vg._WATCHED_PENDING_JOBS

    for task in vg._BACKGROUND_WATCH_TASKS:
        task.cancel()
    await asyncio.gather(*vg._BACKGROUND_WATCH_TASKS, return_exceptions=True)


@pytest.mark.asyncio
async def test_watcher_survives_transient_poll_error_and_still_delivers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A single flaky poll mid-watch must not abort delivery: the watcher
    keeps retrying past a transient network error and still delivers the
    file once the job actually completes."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    video_bytes = b"fake-mp4-bytes"
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-flaky2", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-flaky2":
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return httpx.Response(200, json={"id": "job-flaky2", "status": "pending"})
            if poll_count["n"] == 2:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"id": "job-flaky2", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-flaky2/content":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), _bound_root_context(_root_context(session_id="sess-flaky", request_id="req-flaky")):
        result = await generate_video(prompt="a cyclist riding through a park", save_dir=str(tmp_path))
        assert "job-flaky2" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert "job-flaky2" not in vg._WATCHED_PENDING_JOBS
    assert len(pushed) == 1
    assert pushed[0]["payload"]["event_type"] == "chat.file"
    saved_path = tmp_path / "video_job-flaky2.mp4"
    assert saved_path.read_bytes() == video_bytes


# ---------------------------------------------------------------------------
# _is_terminal_job_result (code review on 07a49414f: "not a recognized
# still-pending message" is not the same as "settled" - every other
# transient/intermediate error shape must also keep the watcher retrying)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Video generated successfully!\nSaved to: /tmp/v.mp4", True),
        ("[ERROR]: video job job-err ended with status failed: boom", True),
        ("[ERROR]: video job job-err ended with status cancelled: no detail", True),
        ("Video job job-abc is still running.", False),
        (
            "Video job job-abc submitted and still pending after 10s - generation can take "
            "several minutes. Call check_video_status with job_id=job-abc to check progress "
            "and download it once ready.",
            False,
        ),
        ("[ERROR]: checking video job job-x failed: 503 service unavailable", False),
        ("[ERROR]: checking video job job-x returned invalid JSON: ValueError('bad')", False),
        ("[ERROR]: video job job-x completed but downloading content failed: 500", False),
        (
            "[ERROR]: video generation is not configured - set the Video processing "
            "API key, API URL, and model name in configuration settings.",
            False,
        ),
        ("[ERROR]: job_id is required.", False),
        ("", False),
    ],
)
def test_is_terminal_job_result(message: str, expected: bool):
    assert vg._is_terminal_job_result(message) is expected


@pytest.mark.asyncio
async def test_watcher_survives_non_200_poll_response_and_still_delivers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A single 5xx poll response mid-watch (a plain non-200 HTTP response,
    not an httpx exception) must not look terminal either - before this fix
    _watch_until_settled used "not a still-pending message" as its only
    terminal signal, so this would have pushed a bogus failure and stopped
    watching before the job actually finished."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    video_bytes = b"fake-mp4-bytes"
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-5xx", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-5xx":
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return httpx.Response(200, json={"id": "job-5xx", "status": "pending"})
            if poll_count["n"] == 2:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(200, json={"id": "job-5xx", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-5xx/content":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), _bound_root_context(_root_context(session_id="sess-5xx", request_id="req-5xx")):
        result = await generate_video(prompt="a cyclist riding through a park", save_dir=str(tmp_path))
        assert "job-5xx" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert "job-5xx" not in vg._WATCHED_PENDING_JOBS
    assert len(pushed) == 1
    assert pushed[0]["payload"]["event_type"] == "chat.file"
    saved_path = tmp_path / "video_job-5xx.mp4"
    assert saved_path.read_bytes() == video_bytes


@pytest.mark.asyncio
async def test_watcher_survives_download_failure_and_retries_until_it_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The job can report status=completed while the content download itself
    transiently fails (e.g. a momentary CDN hiccup) - that must not be
    treated as a final failure either; the watcher keeps polling (which
    re-attempts the download on the next completed poll) until it actually
    succeeds, instead of declaring the real video lost."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    video_bytes = b"fake-mp4-bytes"
    poll_count = {"n": 0}
    download_attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-dl", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-dl":
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return httpx.Response(200, json={"id": "job-dl", "status": "pending"})
            return httpx.Response(200, json={"id": "job-dl", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-dl/content":
            download_attempts["n"] += 1
            if download_attempts["n"] == 1:
                return httpx.Response(500, text="internal error")
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    pushed: list[dict] = []

    async def _push(message: dict) -> bool:
        pushed.append(message)
        return True

    with patch.object(sfu, "send_runtime_push", _push), patch(
        "jiuwenswarm.server.runtime.session.session_history.append_history_record",
    ), _bound_root_context(_root_context(session_id="sess-dl", request_id="req-dl")):
        result = await generate_video(prompt="a cyclist riding through a park", save_dir=str(tmp_path))
        assert "job-dl" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert "job-dl" not in vg._WATCHED_PENDING_JOBS
    assert download_attempts["n"] == 2
    assert len(pushed) == 1
    assert pushed[0]["payload"]["event_type"] == "chat.file"
    saved_path = tmp_path / "video_job-dl.mp4"
    assert saved_path.read_bytes() == video_bytes


# ---------------------------------------------------------------------------
# _deliver_watch_result must not log/treat a send_file_to_user FAILURE as a
# successful delivery (code review on 07a49414f: send_file never raises, it
# returns a Chinese failure string - the exact "no active push host" case
# this watcher exists for was being logged as "delivered" and otherwise
# dropped, leaving the user with neither the file nor any notice).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_watch_result_falls_back_when_send_file_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Direct unit check that _deliver_watch_result inspects send_file's
    return value instead of treating any string back from it as success."""
    video_path = tmp_path / "video_job-direct.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")
    result = f"Video generated successfully!\nSaved to: {video_path}\n(job job-direct - the remote source URL expires 24h after completion, so this local file is now the durable copy.)"

    async def _failing_send_file(self, path, target_channels=None, **_ignored):
        return "发送文件失败：所有文件均不存在"

    monkeypatch.setattr(sfu.SendFileToolkit, "send_file", _failing_send_file)

    fallback_pushed: list[dict] = []

    async def _fallback_push(message: dict) -> bool:
        fallback_pushed.append(message)
        return True

    monkeypatch.setattr(vg, "send_runtime_push", _fallback_push)

    await vg._deliver_watch_result("job-direct", result, _root_context())

    assert len(fallback_pushed) == 1
    content = fallback_pushed[0]["payload"]["content"]
    assert "视频已生成" in content
    assert "发送文件失败" in content
    assert str(video_path) in content


@pytest.mark.asyncio
async def test_watcher_pushes_text_notice_when_delivery_fails_despite_completed_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Full regression: the job completes and downloads fine, but there's no
    active Runtime push host for send_file_to_user by the time the watcher
    fires (the literal "turn/session already ended" scenario). The user must
    still get a text notice naming the local file, not silence and not a
    misleading "delivered" log."""
    _set_video_model_config(monkeypatch)
    _speed_up_polling(monkeypatch)
    monkeypatch.setattr(vg, "_WATCH_POLL_INTERVAL_SECONDS", 0.01)
    video_bytes = b"fake-mp4-bytes"
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-nopush", "status": "pending"})
        if request.url.path == "/api/v1/videos/job-nopush":
            poll_count["n"] += 1
            status = "pending" if poll_count["n"] == 1 else "completed"
            return httpx.Response(200, json={"id": "job-nopush", "status": status})
        if request.url.path == "/api/v1/videos/job-nopush/content":
            return httpx.Response(200, content=video_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    async def _file_push_unavailable(message: dict) -> bool:
        return False

    fallback_pushed: list[dict] = []

    async def _fallback_push(message: dict) -> bool:
        fallback_pushed.append(message)
        return True

    monkeypatch.setattr(vg, "send_runtime_push", _fallback_push)

    with patch.object(sfu, "send_runtime_push", _file_push_unavailable), _bound_root_context(
        _root_context(session_id="sess-nopush", request_id="req-nopush")
    ):
        result = await generate_video(prompt="a cyclist riding through a park", save_dir=str(tmp_path))
        assert "job-nopush" in result

        tasks = list(vg._BACKGROUND_WATCH_TASKS)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    assert "job-nopush" not in vg._WATCHED_PENDING_JOBS
    saved_path = tmp_path / "video_job-nopush.mp4"
    assert saved_path.read_bytes() == video_bytes
    assert len(fallback_pushed) == 1
    content = fallback_pushed[0]["payload"]["content"]
    assert "视频已生成" in content
    assert str(saved_path) in content

# ---------------------------------------------------------------------------
# Director mode additions: multimodal reference channels (image + audio + video)
# and request-ratio frame re-framing.
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


# ---------------------------------------------------------------------------
# Requested aspect_ratio vs. the first/last frame
#
# With a frame in the request the provider derives the output geometry from that
# frame and ignores ``aspect_ratio`` entirely (measured: 16:9 frame + 9:16
# request -> 1280x720 output, while the identical request without a frame
# correctly returned 496x864). The frame therefore has to be reframed to the
# requested ratio, or the card's ratio pill is a lie.
# ---------------------------------------------------------------------------


def _write_image(path: Path, size: tuple[int, int], color: tuple[int, int, int] = (120, 140, 160)) -> Path:
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return path


def _decode_frame_size(data_uri: str) -> tuple[int, int]:
    """Pull the embedded image back out of the data: URI and return its size -
    the only way to assert what the provider actually receives."""
    import base64
    import io

    from PIL import Image

    payload = data_uri.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(payload))) as img:
        return img.size


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9:16", 9 / 16),
        ("16:9", 16 / 9),
        ("1:1", 1.0),
        ("4:3", 4 / 3),
    ],
)


def test_parse_aspect_ratio_understands_w_h(raw: str, expected: float) -> None:
    assert vg.parse_aspect_ratio(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", None, "adaptive", "16x9", "16:", ":9", "0:9", "16:0", "abc:def"])


def test_parse_aspect_ratio_rejects_anything_else(raw: str | None) -> None:
    """看不懂就当没有目标比例（返回 None）——绝不能拿一个瞎猜的比例去裁用户的帧。"""
    assert vg.parse_aspect_ratio(raw) is None


def test_local_frame_is_reframed_to_the_requested_ratio(tmp_path: Path) -> None:
    landscape = _write_image(tmp_path / "wide.png", (1376, 768))

    data_uri, error = vg._resolve_frame_reference(str(landscape), target_ratio=9 / 16)

    assert error is None and data_uri is not None
    width, height = _decode_frame_size(data_uri)
    assert width / height == pytest.approx(9 / 16, abs=0.01)


def test_frame_matching_the_request_is_left_alone(tmp_path: Path) -> None:
    """请求比例与帧本来一致时不做任何裁切——否则每一张普通 16:9 帧都会被白白
    重新编码一次（1376x768 的 1.792 与 16:9 的 1.778 之差在容差内）。"""
    landscape = _write_image(tmp_path / "wide.png", (1376, 768))

    data_uri, _ = vg._resolve_frame_reference(str(landscape), target_ratio=16 / 9)

    width, height = _decode_frame_size(data_uri)
    assert width / height == pytest.approx(1376 / 768, abs=0.02)
    assert width > height


def test_portrait_frame_is_reframed_to_a_landscape_request(tmp_path: Path) -> None:
    """反方向也要成立：竖版帧 + 横版请求 → 成片是横版。"""
    portrait = _write_image(tmp_path / "tall.png", (720, 1280))

    data_uri, _ = vg._resolve_frame_reference(str(portrait), target_ratio=16 / 9)

    width, height = _decode_frame_size(data_uri)
    assert width / height == pytest.approx(16 / 9, abs=0.01)
    assert width > height


def test_no_target_ratio_keeps_the_frame_untouched(tmp_path: Path) -> None:
    landscape = _write_image(tmp_path / "wide.png", (1376, 768))

    data_uri, _ = vg._resolve_frame_reference(str(landscape), target_ratio=None)

    width, height = _decode_frame_size(data_uri)
    assert (width, height) == (1280, 714)  # only the max-dimension bound applied


def test_remote_frame_is_passed_through_untouched() -> None:
    """远程 http(s)/data URI 帧不下载、不重编码，原样透传（并在日志里说明成片
    比例可能由该帧决定）——不能为了裁切去抓一个远端文件。"""
    url = "https://example.com/frame.png"

    data_uri, error = vg._resolve_frame_reference(url, target_ratio=9 / 16)

    assert error is None
    assert data_uri == url


def test_unreadable_image_still_falls_back_to_raw_bytes(tmp_path: Path) -> None:
    """PIL 打不开的载荷（既有测试用的假 PNG 就是这种）仍然要能发出去：
    重新构图失败不能把整次生成变成错误。"""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")

    data_uri, error = vg._resolve_frame_reference(str(broken), target_ratio=9 / 16)

    assert error is None
    assert data_uri is not None and data_uri.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_generate_video_sends_a_frame_matching_the_requested_ratio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """端到端那一条：请求 9:16 时，**发出去的帧**必须已经是 9:16——否则服务商
    按帧的比例出片，卡片上的比例就是摆设（这正是线上复现出来的 bug）。"""
    _set_video_model_config(monkeypatch)
    frame = _write_image(tmp_path / "wide.png", (1376, 768))
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-ratio", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-ratio/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="animate this scene",
        aspect_ratio="9:16",
        first_frame_path=str(frame),
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    body = json.loads(requests_seen[0].content)
    assert body["aspect_ratio"] == "9:16"  # still sent for the no-frame case
    width, height = _decode_frame_size(body["frame_images"][0]["image_url"]["url"])
    assert width / height == pytest.approx(9 / 16, abs=0.01)


@pytest.mark.asyncio
async def test_generate_video_reframes_both_frames_to_the_same_ratio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """首尾帧必须被裁到**同一个**比例，否则首尾插值出来的画面会一宽一窄。"""
    _set_video_model_config(monkeypatch)
    first = _write_image(tmp_path / "first.png", (1376, 768))
    last = _write_image(tmp_path / "last.png", (720, 1280))
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-both", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-both/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="transition",
        aspect_ratio="9:16",
        first_frame_path=str(first),
        last_frame_path=str(last),
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    frames = json.loads(requests_seen[0].content)["frame_images"]
    assert [f["frame_type"] for f in frames] == ["first_frame", "last_frame"]
    for frame in frames:
        width, height = _decode_frame_size(frame["image_url"]["url"])
        assert width / height == pytest.approx(9 / 16, abs=0.01), frame["frame_type"]



# ---------------------------------------------------------------------------
# generate_video - reference video input (video-to-video)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_sends_video_url_reference_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """视频生视频：源片走 input_references 里的 video_url，并且原样透传这条
    HTTPS 地址——服务商自己去抓它，本地下载再编码成 base64 只会被拒绝。"""
    _set_video_model_config(monkeypatch)
    source_url = "https://examdatalake.blob.core.windows.net/examdatalake/clips/dragon_flame.mp4"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/v1/videos" and request.method == "POST":
            return httpx.Response(200, json={"id": "job-v2v", "status": "completed"})
        if request.url.path == "/api/v1/videos/job-v2v/content":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="change the flame to blue",
        reference_video_path=source_url,
        duration_seconds=5,
        save_dir=str(tmp_path),
    )

    assert "Video generated successfully!" in result
    body = json.loads(requests_seen[0].content)
    assert "frame_images" not in body
    assert body["input_references"] == [{"type": "video_url", "video_url": {"url": source_url}}]
    # 只有提交 + 下载两次请求：源片不由我们再取一遍。
    assert len(requests_seen) == 2


@pytest.mark.parametrize(
    "value",
    [
        "/Users/someone/clips/local.mp4",
        "file:///Users/someone/clips/local.mp4",
        "data:video/mp4;base64,AAAA",
        "http://example.com/clip.mp4",
    ],
)


def test_resolve_video_reference_only_accepts_https_urls(value: str):
    """服务商自己抓参考视频，只认 HTTPS 直链——本地文件/data:/http: 都要在
    提交前就被挡下来（否则要白上传一次几十 MB 再被拒）。"""
    resolved, error = vg._resolve_video_reference(value)

    assert resolved is None
    assert error is not None
    assert error.startswith("[ERROR]: reference_video_path")
    assert "https://" in error


def test_resolve_video_reference_passes_https_url_through():
    url = "https://example.com/clips/clip.mp4"

    assert vg._resolve_video_reference(url) == (url, None)


def test_resolve_video_reference_ignores_empty_value():
    assert vg._resolve_video_reference("") == (None, None)


@pytest.mark.asyncio
async def test_generate_video_local_video_reference_fails_before_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _set_video_model_config(monkeypatch)
    local_clip = tmp_path / "clip.mp4"
    local_clip.write_bytes(b"fake-mp4-bytes")
    submitted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        submitted.append(request)
        raise AssertionError("no request may be sent for an unusable reference video")

    _patch_async_client(monkeypatch, handler)

    result = await generate_video(
        prompt="make the flame blue", reference_video_path=str(local_clip), save_dir=str(tmp_path)
    )

    assert result.startswith("[ERROR]: reference_video_path")
    assert submitted == []
