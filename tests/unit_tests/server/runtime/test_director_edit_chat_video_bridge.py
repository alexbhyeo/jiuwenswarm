# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the edit-chat `generate_video_bridge` tool.

This is the 剪辑助手 (director-edit-assistant) conversational entry point to
FLF2V bridge-clip generation: the chat assistant resolves two already-generated
*named* shot-video assets (not asset_ids - the chat tool schema only ever gives
the model names, same as `generate_shot_video`'s `first_frame_name`), extracts
片段一's last frame and 片段二's first frame, and calls the same
`generate_video(first_frame_path=..., last_frame_path=...)` FLF2V path the Lab
canvas's `video_bridge` RPC mode already uses - both go through the shared
`DirectorManager._run_video_bridge_flf2v` helper, so these tests focus on what
is specific to the *chat* entry point:

* resolving clips by name (not asset_id) via `find_asset_by_name`,
* only accepting `type == "video", status == "ready"` assets as clips - an
  image/character asset or a still-pending clip must be rejected with an
  actionable message pointing at `generate_shot_video`,
* falling back to a generic smooth-transition prompt when `description` is
  omitted (FLF2V has no other way to know how to move between the two static
  frames),
* the same "poll past generate_video's own ~120s budget" contract as
  `generate_shot_video` (see test_director_edit_chat_shot_video.py) - the edit
  chat has no canvas-style background poller, so the tool must not give up
  early,
* the new tool is actually registered in `_EDIT_CHAT_TOOLS_SCHEMA` and reachable
  through `_execute_edit_chat_tool_call`'s dispatch, not just directly callable.

`dm.extract_frame`, `dm.generate_video` and `dm.check_video_status` are all
stubbed; the real ffmpeg frame-extraction mechanics are covered separately in
test_director_frame_extract.py, and the real Lab-RPC FLF2V orchestration in
test_director_video_bridge_mode.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_clip_compose import ComposeError
from jiuwenswarm.server.runtime.director.director_manager import DirectorManager

_GENERATE_GAVE_UP = (
    "Video job job-xyz submitted and still pending after 120s - generation can "
    "take several minutes. Call check_video_status with job_id=job-xyz to check "
    "progress and download it once ready."
)
_CHECK_STILL_RUNNING = "Video job job-xyz is still pending."
_SAVED = "Video generated successfully!\nSaved to: {path}"


class _StubTool:
    """Stands in for a ``video_gen_tools`` LocalFunction."""

    def __init__(self, results: list[str]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def _func(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class _StubFrameExtractor:
    """Stands in for ``director_frame_extract.extract_frame``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on: str | None = None

    async def __call__(self, video_path: str, output_path: Path, *, position: Literal["first", "last"]) -> Path:
        self.calls.append({"video_path": video_path, "output_path": output_path, "position": position})
        if self.fail_on == position:
            raise ComposeError("FRAME_EXTRACT_FAILED", f"抓帧失败：{video_path}")
        output_path.write_bytes(b"fake-png-bytes")
        return output_path


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def manager(isolated_root: Path) -> DirectorManager:
    return DirectorManager()


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dm, "video_gen_enabled", lambda: True)
    monkeypatch.setattr(dm, "video_gen_configured", lambda: True)
    monkeypatch.setattr(dm, "_EDIT_CHAT_VIDEO_POLL_INTERVAL_S", 0)


def _stub_extractor(monkeypatch: pytest.MonkeyPatch) -> _StubFrameExtractor:
    stub = _StubFrameExtractor()
    monkeypatch.setattr(dm, "extract_frame", stub)
    return stub


def _add_shot_video(manager: DirectorManager, project_id: str, *, asset_id: str, name: str) -> str:
    """A ready named video asset, i.e. what generate_shot_video leaves behind."""
    path = ds.get_project_assets_dir(project_id) / f"{asset_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-mp4-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id=asset_id,
            type="video",
            status="ready",
            name=name,
            prompt="a shot",
            file_path=str(path),
        ),
    )
    return str(path)


async def _run(manager: DirectorManager, project_id: str, **extra: Any) -> tuple[str, str | None]:
    return await manager._execute_generate_video_bridge(
        manager._store.get_project(project_id),
        {
            "name": "Scene1 Shot1-1→1-2 过渡",
            "clip1_name": "Scene1 Shot1-1 Video",
            "clip2_name": "Scene1 Shot1-2 Video",
            "description": "camera drifts gently from the garden into the house",
            "duration_seconds": 5,
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# Tool is actually registered and reachable
# ---------------------------------------------------------------------------


def test_generate_video_bridge_tool_is_in_schema() -> None:
    names = [t["function"]["name"] for t in dm._EDIT_CHAT_TOOLS_SCHEMA]
    assert "generate_video_bridge" in names
    schema = next(t for t in dm._EDIT_CHAT_TOOLS_SCHEMA if t["function"]["name"] == "generate_video_bridge")
    required = schema["function"]["parameters"]["required"]
    assert set(required) == {"name", "clip1_name", "clip2_name"}


@pytest.mark.asyncio
async def test_dispatch_routes_generate_video_bridge_tool_call(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "调度"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    _stub_extractor(monkeypatch)
    monkeypatch.setattr(dm, "generate_video", _StubTool([_SAVED.format(path=str(tmp_path / "out.mp4"))]))

    import json

    text, asset_id = await manager._execute_edit_chat_tool_call(
        manager._store.get_project(project_id),
        {
            "function": {
                "name": "generate_video_bridge",
                "arguments": json.dumps(
                    {
                        "name": "过渡",
                        "clip1_name": "Scene1 Shot1-1 Video",
                        "clip2_name": "Scene1 Shot1-2 Video",
                    }
                ),
            }
        },
    )

    assert asset_id is not None
    assert "过渡" in text


# ---------------------------------------------------------------------------
# Happy path: correct frame positions, correct FLF2V call, correct asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_extracts_last_of_clip1_and_first_of_clip2(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "桥接"})
    project_id = created["project"]["project_id"]
    clip1_path = _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    clip2_path = _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    extractor = _stub_extractor(monkeypatch)
    video_stub = _StubTool([_SAVED.format(path=str(tmp_path / "out.mp4"))])
    monkeypatch.setattr(dm, "generate_video", video_stub)

    result_text, asset_id = await _run(manager, project_id)

    assert len(extractor.calls) == 2
    clip1_call = next(c for c in extractor.calls if c["video_path"] == clip1_path)
    clip2_call = next(c for c in extractor.calls if c["video_path"] == clip2_path)
    assert clip1_call["position"] == "last"
    assert clip2_call["position"] == "first"

    kwargs = video_stub.calls[0]["kwargs"]
    assert kwargs["first_frame_path"] == str(clip1_call["output_path"])
    assert kwargs["last_frame_path"] == str(clip2_call["output_path"])
    assert kwargs["prompt"] == "camera drifts gently from the garden into the house"
    assert kwargs["generate_audio"] is False

    assert asset_id is not None
    asset = next(a for a in manager._store.get_project(project_id).assets if a.asset_id == asset_id)
    assert asset.type == "video"
    assert asset.status == "ready"
    assert asset.params["bridge_clip1_path"] == clip1_path
    assert asset.params["bridge_clip2_path"] == clip2_path
    assert "过渡" in result_text


@pytest.mark.asyncio
async def test_bridge_falls_back_to_generic_prompt_when_description_omitted(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """FLF2V 只拿到两张静态图，没有
    description 时模型无从得知该怎么连
    接——这里不能像纯文生视频
    那样回落到空提示词，要有个
    通用的平滑过渡默认值。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "默认提示词"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    _stub_extractor(monkeypatch)
    video_stub = _StubTool([_SAVED.format(path=str(tmp_path / "out.mp4"))])
    monkeypatch.setattr(dm, "generate_video", video_stub)

    await _run(manager, project_id, description="")

    prompt = video_stub.calls[0]["kwargs"]["prompt"]
    assert prompt.strip()
    assert "transition" in prompt.lower()


# ---------------------------------------------------------------------------
# Clip resolution: must be named, ready video assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_rejects_unknown_clip1_name(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "缺片段"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    extractor = _stub_extractor(monkeypatch)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "Scene1 Shot1-1 Video" in result_text
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_bridge_rejects_a_clip_that_is_still_pending(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """还没生成完成的镜头视频不能
    当成可用的桥接源片。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "还没好"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id="clip-2",
            type="video",
            status="pending",
            name="Scene1 Shot1-2 Video",
            prompt="still cooking",
            file_path=None,
        ),
    )
    extractor = _stub_extractor(monkeypatch)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_bridge_rejects_a_non_video_asset_with_the_same_name(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """同名的关键帧图片不能当作
    视频片段（generate_shot_video 的 first_frame_name
    走图片，这里必须是视频）。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "类型错"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    path = ds.get_project_assets_dir(project_id) / "frame.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"frame-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id="frame-1",
            type="image",
            status="ready",
            name="Scene1 Shot1-1 Video",
            prompt="a frame, not a video",
            file_path=str(path),
        ),
    )
    extractor = _stub_extractor(monkeypatch)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_bridge_requires_configured_video_slot(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    monkeypatch.setattr(dm, "video_gen_enabled", lambda: False)
    monkeypatch.setattr(dm, "video_gen_configured", lambda: False)
    created = await manager.handle_director_projects_create({"name": "未配置"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "视频处理" in result_text


# ---------------------------------------------------------------------------
# Frame extraction failure surfaces as a clean chat error, not an exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_frame_extraction_failure_is_reported_cleanly(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "抽帧失败"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    extractor = _stub_extractor(monkeypatch)
    extractor.fail_on = "last"
    video_stub = _StubTool([_SAVED.format(path="/tmp/never.mp4")])
    monkeypatch.setattr(dm, "generate_video", video_stub)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert video_stub.calls == []


# ---------------------------------------------------------------------------
# Same "poll past generate_video's own budget" contract as generate_shot_video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_keeps_polling_past_the_generate_video_budget(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "继续轮询"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    _stub_extractor(monkeypatch)

    out_path = tmp_path / "clip.mp4"
    monkeypatch.setattr(dm, "generate_video", _StubTool([_GENERATE_GAVE_UP]))
    check = _StubTool([_CHECK_STILL_RUNNING, _CHECK_STILL_RUNNING, _SAVED.format(path=str(out_path))])
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert len(check.calls) == 3
    assert asset_id is not None
    asset = next(a for a in manager._store.get_project(project_id).assets if a.asset_id == asset_id)
    assert asset.status == "ready"
    assert asset.file_path == str(out_path)


@pytest.mark.asyncio
async def test_bridge_reports_pending_after_the_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    monkeypatch.setattr(dm, "_EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS", 3)
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "跑不完"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    _stub_extractor(monkeypatch)

    monkeypatch.setattr(dm, "generate_video", _StubTool([_GENERATE_GAVE_UP]))
    check = _StubTool([_CHECK_STILL_RUNNING])
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "仍在生成中" in result_text
    assert len(check.calls) == 3


@pytest.mark.asyncio
async def test_bridge_fails_loudly_when_the_job_fails(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "失败"})
    project_id = created["project"]["project_id"]
    _add_shot_video(manager, project_id, asset_id="clip-1", name="Scene1 Shot1-1 Video")
    _add_shot_video(manager, project_id, asset_id="clip-2", name="Scene1 Shot1-2 Video")
    _stub_extractor(monkeypatch)

    monkeypatch.setattr(dm, "generate_video", _StubTool([_GENERATE_GAVE_UP]))
    check = _StubTool(["[ERROR]: video job job-xyz ended with status failed: boom"])
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "过渡视频生成失败" in result_text
    assert len(check.calls) == 1
