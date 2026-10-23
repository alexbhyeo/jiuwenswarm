# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the edit-chat `generate_shot_video` tool.

The tool is the only way the 剪辑助手 conversation turns a storyboard frame into
a real clip, and it is the one place that has to wait longer than
``generate_video`` is willing to. ``generate_video`` polls for
``_MAX_POLL_SECONDS`` (~120s) and then hands back a job id; the tool is supposed
to pick that id up and keep polling until the job really finishes, because the
edit chat has no canvas-style background poller - if the tool gives up early,
the user never sees a clip at all.

These tests pin exactly that contract, which broke in practice: the re-poll loop
kept its ``job_id`` only on the first pass, so any job slower than
``generate_video``'s own budget was reported as "still generating" and never
became an asset.

`dm.generate_video` / `dm.check_video_status` are stubbed with the exact
tool-shaped strings the real tools return; every filesystem path is redirected
into ``tmp_path`` through ``director_store.get_agent_root_dir``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_manager import (
    DirectorManager,
    _parse_generation_result,
)

# The two "still running" phrasings the tools actually emit.
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
        # Reuse the last result once the scripted ones run out: a job that never
        # finishes is a case under test, not a test bug.
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


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
    # 真实间隔是 10s；测试里压成 0，否则一次循环预算就是 5 分钟。
    monkeypatch.setattr(dm, "_EDIT_CHAT_VIDEO_POLL_INTERVAL_S", 0)


def _add_frame(manager: DirectorManager, project_id: str, name: str) -> str:
    """A ready named image asset, i.e. what generate_design_image leaves behind."""
    path = ds.get_project_assets_dir(project_id) / "frame.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"frame-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id="asset_frame_1",
            type="image",
            status="ready",
            name=name,
            prompt="keyframe",
            file_path=str(path),
        ),
    )
    return str(path)


async def _run(manager: DirectorManager, project_id: str) -> tuple[str, str | None]:
    return await manager._execute_generate_shot_video(
        manager._store.get_project(project_id),
        {
            "name": "Ad Shot1 Video",
            "first_frame_name": "Ad Shot1",
            "description": "slow push in",
            "duration_seconds": 5,
        },
    )


# ---------------------------------------------------------------------------
# The parser must keep the job id for both still-running phrasings
# ---------------------------------------------------------------------------


def test_parser_keeps_the_job_id_from_generate_video() -> None:
    assert _parse_generation_result(_GENERATE_GAVE_UP) == {
        "status": "pending",
        "job_id": "job-xyz",
    }


def test_parser_keeps_the_job_id_from_check_video_status() -> None:
    """check_video_status 还在跑时也必须带回 job_id——复查循环靠它判断"还要不要
    接着轮询"。丢掉 job_id 等于把循环条件短路掉，只多查一次就放弃。"""
    assert _parse_generation_result(_CHECK_STILL_RUNNING) == {
        "status": "pending",
        "job_id": "job-xyz",
    }


# ---------------------------------------------------------------------------
# The re-poll loop keeps going until the job really finishes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shot_video_keeps_polling_past_the_generate_video_budget(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """generate_video 预算用尽 → 接着查；查到完成 → 落成 ready 视频素材。

    这是核心回归：修复前查一轮就把 job_id 丢了，第二圈直接退出，用户拿到的
    永远是"仍在生成中"。
    """
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "镜头视频"})
    project_id = created["project"]["project_id"]
    frame_path = _add_frame(manager, project_id, "Ad Shot1")

    out_path = tmp_path / "clip.mp4"
    generate = _StubTool([_GENERATE_GAVE_UP])
    # First two re-polls still pending, third completes.
    check = _StubTool(
        [_CHECK_STILL_RUNNING, _CHECK_STILL_RUNNING, _SAVED.format(path=str(out_path))]
    )
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert len(check.calls) == 3, "复查循环没有接着轮询到完成为止"
    assert asset_id, f"视频没有落成素材: {result_text!r}"

    asset = next(
        a for a in manager._store.get_project(project_id).assets if a.name == "Ad Shot1 Video"
    )
    assert asset.type == "video"
    assert asset.status == "ready"
    assert asset.file_path == str(out_path)
    assert asset.params["duration_seconds"] == 5
    assert asset.params["first_frame_path"] == frame_path


@pytest.mark.asyncio
async def test_shot_video_reports_pending_after_the_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """跑不完也不能装作成功：如实报"仍在生成中"，且不落任何素材。

    这里同时钉住循环**会**停：预算用尽必须退出，不能无限轮询把这一轮对话挂死。
    """
    monkeypatch.setattr(dm, "_EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS", 3)
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "跑不完"})
    project_id = created["project"]["project_id"]
    _add_frame(manager, project_id, "Ad Shot1")

    generate = _StubTool([_GENERATE_GAVE_UP])
    check = _StubTool([_CHECK_STILL_RUNNING])
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "仍在生成中" in result_text
    assert len(check.calls) == 3, "复查次数应当正好用完预算"


@pytest.mark.asyncio
async def test_shot_video_fails_loudly_when_the_job_fails(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """终态是失败时直接如实报错，不再继续轮询。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "失败"})
    project_id = created["project"]["project_id"]
    _add_frame(manager, project_id, "Ad Shot1")

    generate = _StubTool([_GENERATE_GAVE_UP])
    check = _StubTool(["[ERROR]: video job job-xyz ended with status failed: boom"])
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", check)

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "视频生成失败" in result_text
    assert len(check.calls) == 1, "已经失败就不该继续轮询"


@pytest.mark.asyncio
async def test_shot_video_has_audio_by_default(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """缺省就有声音：画面与人声由同一次生成产出，口型天然同步，所以用户不用
    额外说明"这一段要有人声"——只有明确要求静音时才关掉。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "默认有声"})
    project_id = created["project"]["project_id"]
    _add_frame(manager, project_id, "Ad Shot1")

    out_path = tmp_path / "clip.mp4"
    generate = _StubTool([_SAVED.format(path=str(out_path))])
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", _StubTool([_CHECK_STILL_RUNNING]))

    _, asset_id = await _run(manager, project_id)

    assert generate.calls[0]["kwargs"]["generate_audio"] is True
    assert (
        next(a for a in manager._store.get_project(project_id).assets if a.asset_id == asset_id)
        .params["generate_audio"]
        is True
    )


@pytest.mark.asyncio
async def test_shot_video_can_turn_audio_off(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """用户明确要求"这一段静音/不要声音"时才关掉。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "静音"})
    project_id = created["project"]["project_id"]
    _add_frame(manager, project_id, "Ad Shot1")

    out_path = tmp_path / "clip.mp4"
    generate = _StubTool([_SAVED.format(path=str(out_path))])
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", _StubTool([_CHECK_STILL_RUNNING]))

    _, asset_id = await manager._execute_generate_shot_video(
        manager._store.get_project(project_id),
        {
            "name": "Ad Shot1 Video",
            "first_frame_name": "Ad Shot1",
            "description": "Silent establishing shot of the bottle on a wet windowsill.",
            "duration_seconds": 5,
            "generate_audio": False,
        },
    )

    assert generate.calls[0]["kwargs"]["generate_audio"] is False
    assert (
        next(a for a in manager._store.get_project(project_id).assets if a.asset_id == asset_id)
        .params["generate_audio"]
        is False
    )


@pytest.mark.asyncio
async def test_shot_video_can_turn_audio_on(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """显式传 generate_audio=true 与默认行为一致——显式开关不能因为缺省已经是
    有声就被忽略掉（模型仍可能主动写这个参数）。"""
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "有人声"})
    project_id = created["project"]["project_id"]
    _add_frame(manager, project_id, "Ad Shot1")

    out_path = tmp_path / "clip.mp4"
    generate = _StubTool([_SAVED.format(path=str(out_path))])
    monkeypatch.setattr(dm, "generate_video", generate)
    monkeypatch.setattr(dm, "check_video_status", _StubTool([_CHECK_STILL_RUNNING]))

    _, asset_id = await manager._execute_generate_shot_video(
        manager._store.get_project(project_id),
        {
            "name": "Ad Shot1 Video",
            "first_frame_name": "Ad Shot1",
            "description": "She speaks to camera: every day, gentle oat care.",
            "duration_seconds": 5,
            "generate_audio": True,
        },
    )

    assert generate.calls[0]["kwargs"]["generate_audio"] is True
    asset = next(a for a in manager._store.get_project(project_id).assets if a.asset_id == asset_id)
    assert asset.params["generate_audio"] is True


@pytest.mark.asyncio
async def test_shot_video_rejects_an_unknown_first_frame(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch)
    created = await manager.handle_director_projects_create({"name": "缺关键帧"})
    project_id = created["project"]["project_id"]

    result_text, asset_id = await _run(manager, project_id)

    assert asset_id is None
    assert "Ad Shot1" in result_text
