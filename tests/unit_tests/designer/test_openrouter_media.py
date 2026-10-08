# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Design image/video generation on an OpenRouter-style slot.

The chat tools in ``visual_gen_tools`` / ``video_gen_tools`` are openjiuwen
``@tool`` objects: the module attribute is a ``LocalFunction`` wrapper, not the
coroutine, so Design reaches through ``_func`` for the plain callable. These
tests patch ``_func`` and therefore fail if the wiring goes back to calling the
module attribute directly (which raises
``TypeError: 'LocalFunction' object is not callable``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.agents.harness.common.tools import video_gen_tools, visual_gen_tools
from jiuwenswarm.server.runtime.designer import media_generation as mg


@pytest.fixture
def openrouter_image_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slot shape a 图片处理 panel pointing at OpenRouter produces."""
    monkeypatch.setenv("VISUAL_GEN_ENABLED", "true")
    monkeypatch.setenv("VISUAL_GEN_API_KEY", "sk-or-test")
    monkeypatch.setenv("VISUAL_GEN_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("VISUAL_GEN_MODEL_NAME", "google/gemini-3.1-flash-image")
    monkeypatch.setenv("VISUAL_GEN_PROTOCOL", "google")


@pytest.fixture
def openrouter_video_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Note the vendor-name 协议 (``bytedance``) with an OpenRouter base URL."""
    monkeypatch.setenv("VIDEO_GEN_ENABLED", "true")
    monkeypatch.setenv("VIDEO_GEN_API_KEY", "sk-or-test")
    monkeypatch.setenv("VIDEO_GEN_API_BASE", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("VIDEO_GEN_MODEL_NAME", "bytedance/seedance-2.0-fast")
    monkeypatch.setenv("VIDEO_GEN_PROTOCOL", "bytedance")


def test_generation_tools_are_framework_wrapped() -> None:
    """The reason ``_func`` is needed at all: these are Tool objects, not functions."""
    for tool in (
        visual_gen_tools.generate_visual,
        video_gen_tools.generate_video,
        video_gen_tools.check_video_status,
    ):
        assert not callable(tool)
        assert callable(tool._func)  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_image_calls_the_openrouter_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, openrouter_image_slot: None
) -> None:
    saved = tmp_path / "out.png"
    seen: list[tuple[Any, ...]] = []

    async def fake_generate_visual(prompt, aspect_ratio="16:9", resolution="512", save_dir=None):
        seen.append((prompt, aspect_ratio, resolution, save_dir))
        return f"Image generated successfully!\nSaved to: {saved}"

    monkeypatch.setattr(visual_gen_tools.generate_visual, "_func", fake_generate_visual)
    result = await mg.generate_image("关键帧", size="1024x1024", save_dir=str(tmp_path))

    assert result == {"image_path": str(saved)}
    [(prompt, _aspect_ratio, resolution, save_dir)] = seen
    assert prompt == "关键帧"
    assert resolution == "1024"  # short edge of 1024x1024
    assert save_dir == str(tmp_path)


@pytest.mark.asyncio
async def test_image_drops_references_without_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, openrouter_image_slot: None
) -> None:
    """That path is text-to-image, so refs degrade to a warning, not an error."""
    character = tmp_path / "character.png"
    character.write_bytes(b"png-character")
    saved = tmp_path / "out.png"

    async def fake_generate_visual(prompt, aspect_ratio="16:9", resolution="512", save_dir=None):
        return f"Image generated successfully!\nSaved to: {saved}"

    monkeypatch.setattr(visual_gen_tools.generate_visual, "_func", fake_generate_visual)
    result = await mg.generate_image("关键帧", reference_images=[str(character)])

    assert result == {"image_path": str(saved)}


@pytest.mark.asyncio
async def test_video_submits_then_polls_the_openrouter_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, openrouter_video_slot: None
) -> None:
    job = "gen-vid-1791447875-kcvDiEOcJLDv7t6LWyap"
    saved = tmp_path / "clip.mp4"
    submitted: list[tuple[Any, ...]] = []
    polled: list[str] = []

    async def fake_generate_video(*args: Any, **kwargs: Any) -> str:
        submitted.append(args)
        return (
            f"Video job {job} submitted and still pending after 120s - generation can take "
            f"several minutes. Call check_video_status with job_id={job} to check progress "
            "and download it once ready."
        )

    async def fake_check_video_status(job_id: str, save_dir: str | None = None) -> str:
        polled.append(job_id)
        return (
            f"Video generated successfully!\nSaved to: {saved}\n(job {job_id} - the remote "
            "source URL expires 24h after completion, so this local file is now the durable copy.)"
        )

    monkeypatch.setattr(video_gen_tools.generate_video, "_func", fake_generate_video)
    monkeypatch.setattr(video_gen_tools.check_video_status, "_func", fake_check_video_status)
    monkeypatch.setattr(mg, "_VIDEO_POLL_SECONDS", 0)

    result = await mg.generate_video(mg.DesignerVideoRequest(prompt="shot", duration=5))

    assert result == {"video_path": str(saved)}
    assert polled == [job]  # the submitted job id drives the polling loop
    assert submitted and submitted[0][0] == "shot"


def test_openrouter_host_passes_the_gate(
    monkeypatch: pytest.MonkeyPatch, openrouter_video_slot: None, openrouter_image_slot: None
) -> None:
    assert mg.generation_problem("video") is None
    assert mg.generation_problem("image") is None


def test_unknown_endpoint_is_still_refused(
    monkeypatch: pytest.MonkeyPatch, openrouter_video_slot: None
) -> None:
    """The guard the native-only check provided is kept, just widened."""
    monkeypatch.delenv("VIDEO_GEN_ENDPOINT_PROFILE", raising=False)
    monkeypatch.setenv("VIDEO_GEN_API_BASE", "https://api.example.com/v1")

    problem = mg.generation_problem("video")

    assert problem is not None
    assert "OpenRouter endpoint" in problem  # the message names the way out
    assert "https://api.example.com/v1" in problem


def test_unknown_image_endpoint_is_still_refused(
    monkeypatch: pytest.MonkeyPatch, openrouter_image_slot: None
) -> None:
    monkeypatch.delenv("VISUAL_GEN_ENDPOINT_PROFILE", raising=False)
    monkeypatch.setenv("VISUAL_GEN_API_BASE", "https://api.example.com/v1")

    assert "OpenRouter endpoint" in (mg.generation_problem("image") or "")


def test_endpoint_profile_admits_a_proxied_openrouter(
    monkeypatch: pytest.MonkeyPatch, openrouter_video_slot: None
) -> None:
    """A non-openrouter.ai host is fine when the slot declares the profile."""
    monkeypatch.setenv("VIDEO_GEN_API_BASE", "https://gateway.internal/v1")
    monkeypatch.setenv("VIDEO_GEN_ENDPOINT_PROFILE", "openrouter")

    assert mg.generation_problem("video") is None
