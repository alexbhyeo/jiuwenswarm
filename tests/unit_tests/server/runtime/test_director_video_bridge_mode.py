# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Lab's 视频连视频 (``video_bridge``) mode.

视频连视频 bridges two source clips with a FLF2V (first/last-frame-to-video)
transition: it grabs 片段一's *last* frame and 片段二's *first* frame, then
calls the exact same ``generate_video(first_frame_path=…, last_frame_path=…)``
path ``image2video`` already uses. What these tests pin:

* both clips must be wired, already-ready, local video assets - there is no
  HTTPS-URL alternative (frame extraction runs on the local file, not through
  the generation API's reference-video fetch),
* the prompt is required (FLF2V has no other way to know how to move between
  the two static frames),
* 片段一 is extracted with ``position="last"`` and 片段二 with
  ``position="first"`` - get these backwards and the bridge plays in reverse,
* a frame-extraction failure (``ComposeError``) surfaces as a
  ``GENERATION_FAILED`` ``DirectorRpcError``, not an unhandled exception,
* the two source clip paths are recorded in the persisted asset's ``params``
  so ``buildFlowFromChat`` can restore both dependency edges,
* the persisted asset is a ``video`` asset and audio defaults to **off** - a
  synthesized transition has no source track to preserve (unlike
  ``video2video``, whose source clip genuinely has one).

``generate_video`` and ``extract_frame`` are both stubbed here; their own
internals are covered by ``tests/unit_tests/agents/test_video_gen_tools.py``
and ffmpeg behaviour is exercised indirectly via ``director_clip_compose``.

Every filesystem path is redirected into ``tmp_path`` by patching
``get_agent_root_dir`` in the ``director_store`` module, same as the sibling
``video2video`` test file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_clip_compose import ComposeError
from jiuwenswarm.server.runtime.director.director_manager import (
    DirectorManager,
    DirectorRpcError,
)

_SAVED = "Video generated successfully!\nSaved to: {path}"


class _StubVideoTool:
    """Stands in for ``video_gen_tools.generate_video`` (a LocalFunction)."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def _func(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.result


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


def _configure(monkeypatch: pytest.MonkeyPatch, *, video: bool = True) -> None:
    monkeypatch.setattr(dm, "video_gen_enabled", lambda: video)
    monkeypatch.setattr(dm, "video_gen_configured", lambda: video)


def _stub_video_tool(monkeypatch: pytest.MonkeyPatch, result: str) -> _StubVideoTool:
    stub = _StubVideoTool(result)
    monkeypatch.setattr(dm, "generate_video", stub)
    return stub


def _stub_frame_extractor(monkeypatch: pytest.MonkeyPatch) -> _StubFrameExtractor:
    stub = _StubFrameExtractor()
    monkeypatch.setattr(dm, "extract_frame", stub)
    return stub


async def _create_project(manager: DirectorManager, name: str = "视频连视频项目") -> str:
    created = await manager.handle_director_projects_create({"name": name})
    return created["project"]["project_id"]


def _add_video_asset(manager: DirectorManager, project_id: str, *, asset_id: str, name: str) -> str:
    path = ds.get_project_assets_dir(project_id) / f"{asset_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-mp4-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id=asset_id,
            type="video",
            status="ready",
            prompt=name,
            file_path=str(path),
            name=name,
        ),
    )
    return str(path)


def _asset_of(result: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])


async def _generate(manager: DirectorManager, project_id: str, **params: Any) -> dict[str, Any]:
    return await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "video_bridge",
            "prompt": "bridge the two clips smoothly",
            **params,
        }
    )


def test_video_bridge_is_a_supported_mode() -> None:
    assert "video_bridge" in dm._SUPPORTED_MODES


@pytest.mark.asyncio
async def test_generate_extracts_last_of_clip1_and_first_of_clip2(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    clip1_path = _add_video_asset(manager, project_id, asset_id="clip-1", name="clip one")
    clip2_path = _add_video_asset(manager, project_id, asset_id="clip-2", name="clip two")
    extractor = _stub_frame_extractor(monkeypatch)
    video_stub = _stub_video_tool(monkeypatch, _SAVED.format(path=tmp_path / "out.mp4"))

    result = await _generate(manager, project_id, clip1_asset_id="clip-1", clip2_asset_id="clip-2")

    assert len(extractor.calls) == 2
    clip1_call = next(c for c in extractor.calls if c["video_path"] == clip1_path)
    clip2_call = next(c for c in extractor.calls if c["video_path"] == clip2_path)
    # 片段一取最后一帧（当桥接片的首帧），片段二取第一帧（当桥接片的尾帧）
    # ——这两个方向反过来，桥接片就会倒着从片段二连到片段一。
    assert clip1_call["position"] == "last"
    assert clip2_call["position"] == "first"

    assert len(video_stub.calls) == 1
    kwargs = video_stub.calls[0]["kwargs"]
    assert kwargs["first_frame_path"] == str(clip1_call["output_path"])
    assert kwargs["last_frame_path"] == str(clip2_call["output_path"])
    assert kwargs["prompt"] == "bridge the two clips smoothly"
    # 桥接片是新合成的过渡镜头，没有源片音轨可沿用，默认静音。
    assert kwargs["generate_audio"] is False

    asset = _asset_of(result)
    assert asset["type"] == "video"
    assert asset["params"]["bridge_clip1_path"] == clip1_path
    assert asset["params"]["bridge_clip2_path"] == clip2_path


@pytest.mark.asyncio
async def test_generate_honours_explicit_audio_choice(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    _add_video_asset(manager, project_id, asset_id="clip-2", name="two")
    _stub_frame_extractor(monkeypatch)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=tmp_path / "out.mp4"))

    await _generate(
        manager, project_id, clip1_asset_id="clip-1", clip2_asset_id="clip-2", generate_audio=True
    )

    assert stub.calls[0]["kwargs"]["generate_audio"] is True


@pytest.mark.asyncio
async def test_missing_clip2_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    extractor = _stub_frame_extractor(monkeypatch)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path="/tmp/never.mp4"))

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, clip1_asset_id="clip-1")

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "片段一" in excinfo.value.message or "片段二" in excinfo.value.message
    assert extractor.calls == []
    assert stub.calls == []


@pytest.mark.asyncio
async def test_missing_both_clips_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    _configure(monkeypatch)
    project_id = await _create_project(manager)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_not_ready_clip_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    """还没生成完成（或生成失败）的视频素材不该被当成可用的源片。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(asset_id="clip-2", type="video", status="pending", prompt="two", file_path=None),
    )
    extractor = _stub_frame_extractor(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, clip1_asset_id="clip-1", clip2_asset_id="clip-2")

    assert excinfo.value.code == "INVALID_PARAMS"
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_empty_prompt_is_rejected_even_with_both_clips_wired(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """FLF2V 只拿到两张静态图，没有提示词模型无从得知该怎么从第一张动到
    第二张——两段素材都连好了也不能回落到空提示词。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    _add_video_asset(manager, project_id, asset_id="clip-2", name="two")
    extractor = _stub_frame_extractor(monkeypatch)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path="/tmp/never.mp4"))

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {
                "project_id": project_id,
                "mode": "video_bridge",
                "prompt": "   ",
                "clip1_asset_id": "clip-1",
                "clip2_asset_id": "clip-2",
            }
        )

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "提示词" in excinfo.value.message
    assert extractor.calls == []
    assert stub.calls == []


@pytest.mark.asyncio
async def test_frame_extraction_failure_is_reported_as_generation_failed(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    _add_video_asset(manager, project_id, asset_id="clip-2", name="two")
    extractor = _stub_frame_extractor(monkeypatch)
    extractor.fail_on = "last"
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path="/tmp/never.mp4"))

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, clip1_asset_id="clip-1", clip2_asset_id="clip-2")

    assert excinfo.value.code == "GENERATION_FAILED"
    # 抓帧失败在真正调用生成接口之前就发现，不该浪费一次视频生成请求。
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_requires_configured_video_slot(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch, video=False)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id, asset_id="clip-1", name="one")
    _add_video_asset(manager, project_id, asset_id="clip-2", name="two")

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, clip1_asset_id="clip-1", clip2_asset_id="clip-2")

    assert excinfo.value.code == "NOT_CONFIGURED"
