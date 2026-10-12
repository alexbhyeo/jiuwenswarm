# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the 剪辑 timeline composer (``director_clip_compose``).

Two things are pinned here, both of which are easy to get subtly wrong and hard
to notice by eye:

* **the render semantics match the preview** — the topmost track covering a
  moment wins (``locateActive``), everything is cut into abutting non-overlapping
  segments, and a moment nothing covers becomes a black gap. Getting this wrong
  produces a video that plays a *different* clip than the one the user saw.
* **silent files never enter the audio graph** — a video clip without an audio
  stream must not contribute an ``[n:a]`` input, or ffmpeg fails the whole
  render with "matches no streams".

``ffmpeg`` itself is never invoked: probing is stubbed and the command builder is
a pure function, so these run everywhere (including machines without ffmpeg).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_clip_compose as cc
from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_manager import (
    DirectorManager,
    DirectorRpcError,
)


def _clip(
    *,
    name: str,
    kind: str = "video",
    start: float = 0.0,
    trim_in: float = 0.0,
    trim_out: float = 5.0,
    path: str | None = None,
) -> dict[str, Any]:
    return {
        "file_path": path or f"/tmp/{name}.{ 'png' if kind == 'image' else 'mp4' }",
        "type": kind,
        "trim_in": trim_in,
        "trim_out": trim_out,
        "start": start,
        "name": name,
    }


# ---------------------------------------------------------------------------
# flatten_timeline: the render semantics
# ---------------------------------------------------------------------------


def test_topmost_track_wins_when_clips_overlap() -> None:
    """两条轨道在同一时刻都有片段时，上面那条（轨道号更小）赢——与预览
    locateActive 的"从上往下找第一条覆盖这一刻的轨道"完全一致。"""
    tracks = cc.parse_tracks(
        [
            [_clip(name="upper", start=0, trim_out=5)],
            [_clip(name="lower", start=0, trim_out=5)],
        ]
    )

    plan = cc.flatten_timeline(tracks)

    assert [seg.clip.name for seg in plan.visuals] == ["upper"]
    assert plan.duration == 5.0


def test_lower_track_shows_through_where_the_upper_one_has_a_hole() -> None:
    tracks = cc.parse_tracks(
        [
            [_clip(name="upper", start=0, trim_out=2)],
            [_clip(name="lower", start=0, trim_out=6)],
        ]
    )

    plan = cc.flatten_timeline(tracks)

    assert [(seg.clip.name if seg.clip else None, seg.start, seg.duration) for seg in plan.visuals] == [
        ("upper", 0.0, 2.0),
        ("lower", 2.0, 4.0),
    ]


def test_uncovered_time_becomes_a_black_gap() -> None:
    """片段之间/开头的空档是没有画面的：渲染成黑场，而不是把后面的片段提前。"""
    tracks = cc.parse_tracks([[_clip(name="later", start=3.0, trim_out=5.0)]])

    plan = cc.flatten_timeline(tracks)

    assert [
        (seg.clip.name if seg.clip else None, seg.start, seg.duration) for seg in plan.visuals
    ] == [
        (None, 0.0, 3.0),
        ("later", 3.0, 5.0),
    ]
    assert plan.duration == 8.0


def test_segments_abut_and_cover_the_whole_timeline() -> None:
    """段与段必须首尾相接、合起来正好是整条时间线——concat 的前提。"""
    tracks = cc.parse_tracks(
        [
            [
                _clip(name="a", start=0, trim_out=1.5),
                _clip(name="b", start=4.0, trim_out=2.0),
            ]
        ]
    )

    plan = cc.flatten_timeline(tracks)

    cursor = 0.0
    for seg in plan.visuals:
        assert seg.start == pytest.approx(cursor)
        assert seg.duration > 0
        cursor = seg.end
    assert cursor == pytest.approx(plan.duration)


def test_one_clip_covering_many_boundaries_is_collapsed_into_one_segment() -> None:
    """同一条素材连续盖住好几个边界点时只出一段，不要拼出一堆碎段。"""
    tracks = cc.parse_tracks(
        [
            [_clip(name="long", start=0, trim_out=10.0)],
            [_clip(name="small", start=2.0, trim_out=3.0)],
        ]
    )

    plan = cc.flatten_timeline(tracks)

    assert len(plan.visuals) == 1
    assert plan.visuals[0].duration == 10.0


def test_audio_sources_are_video_and_audio_clips_only() -> None:
    """音源 = 音频片段 + 视频片段自带的音轨；图片没有音轨，不参与。"""
    tracks = cc.parse_tracks(
        [
            [
                _clip(name="v", kind="video", start=0, trim_out=5),
                _clip(name="img", kind="image", start=5, trim_out=3),
            ],
            [_clip(name="music", kind="audio", start=1, trim_out=4)],
        ]
    )

    plan = cc.flatten_timeline(tracks)

    assert [Path(item[0]).stem for item in plan.audios] == ["v", "music"]
    # (文件, 源窗口起点, 时长, 落点)
    assert plan.audios[1][1:] == (0.0, 4.0, 1.0)


def test_trim_window_is_preserved_as_the_source_range() -> None:
    tracks = cc.parse_tracks([[_clip(name="c", start=2.0, trim_in=1.0, trim_out=3.5)]])

    plan = cc.flatten_timeline(tracks)

    assert plan.visuals[-1].clip.trim_in == 1.0
    assert plan.visuals[-1].clip.duration == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_empty_timeline_is_rejected() -> None:
    with pytest.raises(cc.ComposeError) as err:
        cc.flatten_timeline(cc.parse_tracks([[]]))
    assert err.value.code == "INVALID_PARAMS"


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"file_path": "", "type": "video", "trim_in": 0, "trim_out": 5, "start": 0}, "INVALID_PARAMS"),
        ({"file_path": "/x.mp4", "type": "gif", "trim_in": 0, "trim_out": 5, "start": 0}, "INVALID_PARAMS"),
        ({"file_path": "/x.mp4", "type": "video", "trim_in": 0, "trim_out": 0.001, "start": 0}, "INVALID_PARAMS"),
        ({"file_path": "/x.mp4", "type": "video", "trim_in": 0, "trim_out": "abc", "start": 0}, "INVALID_PARAMS"),
    ],
)
def test_bad_clip_payloads_are_rejected(bad: dict[str, Any], code: str) -> None:
    with pytest.raises(cc.ComposeError) as err:
        cc.parse_clip(bad, track_index=0)
    assert err.value.code == code


def test_nan_position_is_rejected() -> None:
    """NaN 会安静地毁掉滤镜图（ffmpeg 要么报晦涩的错，要么出诡异结果），
    所以在边界上就拦住。"""
    with pytest.raises(cc.ComposeError):
        cc.parse_clip(
            {"file_path": "/x.mp4", "type": "video", "trim_in": 0, "trim_out": 5, "start": float("nan")},
            track_index=0,
        )


def test_over_long_timeline_is_rejected() -> None:
    with pytest.raises(cc.ComposeError) as err:
        cc.flatten_timeline(
            cc.parse_tracks([[_clip(name="x", start=0, trim_out=cc.MAX_TIMELINE_SECONDS + 1)]])
        )
    assert err.value.code == "INVALID_PARAMS"


# ---------------------------------------------------------------------------
# resolve_ffmpeg
# ---------------------------------------------------------------------------


def test_resolve_ffmpeg_prefers_the_bundled_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """项目依赖里带的 ffmpeg 优先——用户装完项目就能用，不必自己装。"""
    bundled = tmp_path / "ffmpeg-bundled"
    bundled.write_bytes(b"#!/bin/sh\n")

    class _FakeImageio:
        @staticmethod
        def get_ffmpeg_exe() -> str:
            return str(bundled)

    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", _FakeImageio)
    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    assert cc.resolve_ffmpeg() == str(bundled)


def test_resolve_ffmpeg_falls_back_to_the_system_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc.shutil, "which", lambda _: "/opt/homebrew/bin/ffmpeg")

    class _Boom:
        @staticmethod
        def get_ffmpeg_exe() -> str:
            raise RuntimeError("not installed")

    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", _Boom)

    assert cc.resolve_ffmpeg() == "/opt/homebrew/bin/ffmpeg"


def test_resolve_ffmpeg_reports_clearly_when_nothing_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc.shutil, "which", lambda _: None)

    class _Missing:
        @staticmethod
        def get_ffmpeg_exe() -> str:
            return "/nonexistent/ffmpeg"

    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", _Missing)

    with pytest.raises(cc.ComposeError) as err:
        cc.resolve_ffmpeg()
    assert err.value.code == "FFMPEG_UNAVAILABLE"


# ---------------------------------------------------------------------------
# build_command
# ---------------------------------------------------------------------------


def _plan(*tracks: list[dict[str, Any]]) -> cc.TimelinePlan:
    return cc.flatten_timeline(cc.parse_tracks(list(tracks)))


def test_command_pads_scales_and_concatenates_every_segment() -> None:
    plan = _plan(
        [
            _clip(name="a", start=0, trim_out=2),
            _clip(name="b", kind="image", start=2, trim_out=4),
        ]
    )

    args = cc.build_command("ffmpeg", plan, "/tmp/out.mp4", canvas=(1280, 720))

    joined = " ".join(args)
    # 一段视频 + 一段图片 → 两次 scale/pad + 一次两路 concat
    assert joined.count("force_original_aspect_ratio=decrease") == 2
    assert "concat=n=2:v=1:a=0[vout]" in joined
    assert "-map [vout]" in joined
    # 图片靠 -loop 1 撑出时长
    assert "-loop 1" in joined
    assert args[-1] == "/tmp/out.mp4"
    assert "+faststart" in joined


def test_command_renders_black_frames_for_gaps() -> None:
    plan = _plan([_clip(name="later", start=2.0, trim_out=5.0)])

    args = cc.build_command("ffmpeg", plan, "/tmp/out.mp4", canvas=(640, 360))
    joined = " ".join(args)

    assert "color=c=black:s=640x360" in joined
    assert "concat=n=2:v=1:a=0[vout]" in joined


def test_command_omits_audio_entirely_when_there_is_no_audio_source() -> None:
    """整条时间线只有图片/无声片段时不该凭空造一条音轨——"无声"就是没有音轨，
    这样"有没有声音"在成片上还是可判定的。"""
    plan = _plan([_clip(name="img", kind="image", start=0, trim_out=3)])

    args = cc.build_command("ffmpeg", plan, "/tmp/out.mp4", canvas=(640, 360))

    assert "-an" not in args  # 没有音频输出时本来就不需要 -an
    assert "[aout]" not in " ".join(args)
    assert "amix" not in " ".join(args)


def test_command_mixes_audio_sources_with_delay_and_no_normalisation() -> None:
    plan = _plan(
        [
            _clip(name="talk", start=0, trim_out=5),
            _clip(name="music", kind="audio", start=5, trim_out=5),
        ]
    )

    joined = " ".join(cc.build_command("ffmpeg", plan, "/tmp/out.mp4", canvas=(1280, 720)))

    assert "adelay=0:all=1" in joined
    assert "adelay=5000:all=1" in joined
    # 3 路 = 两个音源 + 等长静音兜底；normalize=0 保住各自电平
    assert "amix=inputs=3:duration=longest:dropout_transition=0:normalize=0[aout]" in joined
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in joined
    assert "-map [aout]" in joined
    assert "-c:a aac" in joined


def test_timeout_scales_with_the_timeline_but_stays_bounded() -> None:
    assert cc._timeout_for(0) == 120.0
    assert cc._timeout_for(600) == 1200.0
    assert cc._timeout_for(30) == 240.0


# ---------------------------------------------------------------------------
# probe_media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_reads_size_duration_and_audio_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    """随包那份 ffmpeg 不带 ffprobe，所以探测必须只靠 ffmpeg -i 的 stderr。
    样本取自真实输出（带音轨的 mp4 与一张图片）。"""
    with_audio = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':\n"
        "  Duration: 00:00:05.09, start: 0.000000, bitrate: 4595 kb/s\n"
        "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9]\n"
        "  Stream #0:1[0x2](und): Audio: aac (LC), 48000 Hz, stereo, fltp, 192 kb/s\n"
    )
    silent = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'silent.mp4':\n"
        "  Duration: 00:00:05.04, start: 0.000000, bitrate: 4595 kb/s\n"
        "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9]\n"
    )

    async def _fake(_exe: str, args: list[str]) -> tuple[int, str]:
        return (0, with_audio if "clip.mp4" in args[-1] else silent)

    monkeypatch.setattr(cc, "_run_ffmpeg_capture", _fake)

    loud = await cc.probe_media("ffmpeg", "/tmp/clip.mp4")
    quiet = await cc.probe_media("ffmpeg", "/tmp/silent.mp4")

    assert loud.size == (1280, 720)
    assert loud.duration == pytest.approx(5.09)
    assert loud.has_audio is True
    assert quiet.size == (1280, 720)
    assert quiet.has_audio is False


@pytest.mark.asyncio
async def test_probe_failure_degrades_to_empty_info(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> tuple[int, str]:
        raise OSError("no such binary")

    monkeypatch.setattr(cc, "_run_ffmpeg_capture", _boom)

    info = await cc.probe_media("ffmpeg", "/tmp/x.mp4")

    assert info.size is None and info.duration is None and info.has_audio is False


# ---------------------------------------------------------------------------
# compose_timeline: silent clips must not enter the audio graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_clips_are_dropped_from_the_audio_graph(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """核心回归：没有音轨的视频片段不能进音频滤镜图，否则 ffmpeg 报
    "Stream specifier ':a' ... matches no streams"，整条成片都合不出来。"""
    monkeypatch.setattr(cc, "resolve_ffmpeg", lambda: "ffmpeg")
    probed: list[str] = []

    async def _fake_probe(_exe: str, file_path: str) -> cc.MediaInfo:
        probed.append(file_path)
        # 只有 talk.mp4 有音轨；silent.mp4 与图片都没有
        return cc.MediaInfo(
            size=(1280, 720), duration=5.0, has_audio=file_path.endswith("talk.mp4")
        )

    monkeypatch.setattr(cc, "probe_media", _fake_probe)

    captured: dict[str, Any] = {}

    def _fake_command(exe: str, plan: cc.TimelinePlan, out: str, **kwargs: Any) -> list[str]:
        captured["plan"] = plan
        captured["canvas"] = kwargs.get("canvas")
        return ["true"]
    monkeypatch.setattr(cc, "build_command", _fake_command)

    # "true" 这个假命令不产出文件，所以 compose 会报"没有产出文件"——这里只关心
    # 进到命令构造前的音源过滤结果，用 try 包住即可。
    tracks = cc.parse_tracks(
        [
            [
                _clip(name="talk", path="/tmp/talk.mp4", start=0, trim_out=5),
                _clip(name="silent", path="/tmp/silent.mp4", start=5, trim_out=5),
                _clip(name="img", kind="image", path="/tmp/img.png", start=10, trim_out=2),
            ]
        ]
    )
    with pytest.raises(cc.ComposeError):
        await cc.compose_timeline(tracks, tmp_path)

    plan = captured["plan"]
    assert [Path(item[0]).name for item in plan.audios] == ["talk.mp4"]
    # 同一个文件只探一次
    assert probed.count("/tmp/silent.mp4") == 1
    assert captured["canvas"] == (1280, 720)


# ---------------------------------------------------------------------------
# RPC handler
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DirectorManager:
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    return DirectorManager()


@pytest.mark.asyncio
async def test_handler_saves_the_composed_clip_as_a_video_asset(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
) -> None:
    created = await manager.handle_director_projects_create({"name": "剪辑成片"})
    project_id = created["project"]["project_id"]

    out = tmp_path / "timeline.mp4"
    out.write_bytes(b"mp4-bytes")
    plan = cc.flatten_timeline(
        cc.parse_tracks(
            [
                [
                    _clip(name="a", start=0, trim_out=5),
                    _clip(name="b", start=5, trim_out=5),
                ]
            ]
        )
    )
    seen: dict[str, Any] = {}

    async def _fake_compose(tracks: Any, out_dir: Path, **kwargs: Any) -> Any:
        seen["tracks"] = tracks
        seen["out_dir"] = out_dir
        return out, plan, (1280, 720)

    monkeypatch.setattr(dm, "compose_timeline", _fake_compose)

    result = await manager.handle_director_clip_compose(
        {
            "project_id": project_id,
            "name": "剪辑成片 21-30",
            "tracks": [
                [
                    {"file_path": "/tmp/a.mp4", "type": "video", "trim_in": 0, "trim_out": 5, "start": 0},
                    {"file_path": "/tmp/b.mp4", "type": "video", "trim_in": 0, "trim_out": 5, "start": 5},
                ]
            ],
        }
    )

    asset = next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])
    assert asset["type"] == "video"
    assert asset["status"] == "ready"
    assert asset["name"] == "剪辑成片 21-30"
    assert asset["file_path"] == str(out)
    assert asset["params"]["source"] == "timeline"
    assert asset["params"]["duration_seconds"] == pytest.approx(10.0)
    assert asset["params"]["segment_count"] == 2
    assert seen["out_dir"] == ds.get_project_assets_dir(project_id)


@pytest.mark.asyncio
async def test_handler_defaults_the_clip_name(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager) -> None:
    created = await manager.handle_director_projects_create({"name": "默认命名"})
    project_id = created["project"]["project_id"]

    async def _fake_compose(tracks: Any, out_dir: Path, **kwargs: Any) -> Any:
        return Path("/tmp/x.mp4"), cc.flatten_timeline(cc.parse_tracks([[_clip(name="a")]])), (1280, 720)

    monkeypatch.setattr(dm, "compose_timeline", _fake_compose)

    result = await manager.handle_director_clip_compose(
        {
            "project_id": project_id,
            "tracks": [
                [{"file_path": "/tmp/a.mp4", "type": "video", "trim_in": 0, "trim_out": 5, "start": 0}]
            ],
        }
    )

    asset = next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])
    assert asset["name"] == "剪辑成片"


@pytest.mark.asyncio
async def test_handler_maps_compose_failures_to_rpc_errors(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
) -> None:
    created = await manager.handle_director_projects_create({"name": "失败"})
    project_id = created["project"]["project_id"]

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise cc.ComposeError("COMPOSE_FAILED", "合成失败：ffmpeg 出错")

    monkeypatch.setattr(dm, "compose_timeline", _boom)

    with pytest.raises(DirectorRpcError) as err:
        await manager.handle_director_clip_compose(
            {
                "project_id": project_id,
                "tracks": [
                    [{"file_path": "/tmp/a.mp4", "type": "video", "trim_in": 0, "trim_out": 5, "start": 0}]
                ],
            }
        )
    assert err.value.code == "COMPOSE_FAILED"


@pytest.mark.asyncio
async def test_handler_rejects_unknown_project_and_missing_params(manager: DirectorManager) -> None:
    with pytest.raises(DirectorRpcError) as err:
        await manager.handle_director_clip_compose({"tracks": []})
    assert err.value.code == "INVALID_PARAMS"

    with pytest.raises(DirectorRpcError) as err:
        await manager.handle_director_clip_compose({"project_id": "dproj_nope", "tracks": []})
    assert err.value.code == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_handler_rejects_an_empty_timeline(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager) -> None:
    created = await manager.handle_director_projects_create({"name": "空时间线"})
    project_id = created["project"]["project_id"]

    with pytest.raises(DirectorRpcError) as err:
        await manager.handle_director_clip_compose({"project_id": project_id, "tracks": [[]]})
    assert err.value.code == "INVALID_PARAMS"
