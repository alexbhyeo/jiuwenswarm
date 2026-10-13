# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Lab's 视频生视频 (``video2video``) mode.

The mode is one reference-to-video call whose reference is the source clip
itself, so most of what these tests pin is about *where the source clip may come
from*:

* a public HTTPS URL travels through as ``video_url`` in ``input_references``
  (the provider fetches it itself),
* a clip wired in from the asset library is refused up front with an actionable
  message - the provider only accepts HTTPS reference videos, so embedding a
  local clip as a data URI would fail at the provider after a wasted upload,
* the prompt is required (it is the entire description of the intended change),
* the source path is recorded in the persisted asset's ``params`` so
  ``buildFlowFromChat`` can restore either the 视频链接 field or the edge,
* the persisted asset is a ``video`` asset (mode says how it was made, type says
  what came out) and audio defaults to on so the source soundtrack survives.

``generate_video`` itself is stubbed here; its own reference handling is covered
by ``tests/unit_tests/agents/test_video_gen_tools.py``.

Every filesystem path is redirected into ``tmp_path`` by patching
``get_agent_root_dir`` in the ``director_store`` module - all of its path helpers
derive from that one function, so a single patch isolates both
``director_state.json`` and the per-project ``assets/`` directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_manager import (
    DirectorManager,
    DirectorRpcError,
)

_SOURCE_URL = "https://examdatalake.blob.core.windows.net/examdatalake/clips/dragon_flame.mp4"
_SAVED = "Video generated successfully!\nSaved to: {path}"


class _StubVideoTool:
    """Stands in for ``video_gen_tools.generate_video`` (a LocalFunction)."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def _func(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.result


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the director store (and therefore the whole feature) at tmp_path."""
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def manager(isolated_root: Path) -> DirectorManager:
    # Constructed after the path patch is in place.
    return DirectorManager()


def _configure(monkeypatch: pytest.MonkeyPatch, *, video: bool = True) -> None:
    monkeypatch.setattr(dm, "video_gen_enabled", lambda: video)
    monkeypatch.setattr(dm, "video_gen_configured", lambda: video)


def _stub_video_tool(monkeypatch: pytest.MonkeyPatch, result: str) -> _StubVideoTool:
    stub = _StubVideoTool(result)
    monkeypatch.setattr(dm, "generate_video", stub)
    return stub


async def _create_project(manager: DirectorManager, name: str = "视频生视频项目") -> str:
    created = await manager.handle_director_projects_create({"name": name})
    return created["project"]["project_id"]


def _add_video_asset(
    manager: DirectorManager, project_id: str, *, asset_id: str = "asset-video-1"
) -> str:
    path = ds.get_project_assets_dir(project_id) / f"{asset_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-mp4-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id=asset_id,
            type="video",
            status="ready",
            prompt="dragon flame",
            file_path=str(path),
            name="dragon_flame",
        ),
    )
    return str(path)


def _asset_of(result: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])


async def _generate(manager: DirectorManager, project_id: str, **params: Any) -> dict[str, Any]:
    return await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "video2video",
            "prompt": "change the flame to blue",
            **params,
        }
    )


def test_video2video_is_a_supported_mode() -> None:
    assert "video2video" in dm._SUPPORTED_MODES


@pytest.mark.asyncio
async def test_generate_passes_source_url_as_reference_video(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    saved = tmp_path / "out.mp4"
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=saved))

    result = await _generate(manager, project_id, input_video_url=_SOURCE_URL)

    assert len(stub.calls) == 1
    kwargs = stub.calls[0]["kwargs"]
    assert kwargs["reference_video_path"] == _SOURCE_URL
    assert kwargs["prompt"] == "change the flame to blue"
    # 源片自带音轨，默认保留——重绘画面不该顺手把声音抹掉。
    assert kwargs["generate_audio"] is True
    # 链接是外部地址，不是首/尾帧，不进 frame_images 那条路。
    assert "first_frame_path" not in kwargs
    assert "last_frame_path" not in kwargs

    asset = _asset_of(result)
    assert asset["type"] == "video"
    assert asset["params"]["input_video_path"] == _SOURCE_URL
    assert asset["params"]["generate_audio"] is True


@pytest.mark.asyncio
async def test_generate_honours_explicit_silent_choice(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=tmp_path / "out.mp4"))

    result = await _generate(manager, project_id, input_video_url=_SOURCE_URL, generate_audio=False)

    assert stub.calls[0]["kwargs"]["generate_audio"] is False
    assert _asset_of(result)["params"]["generate_audio"] is False


@pytest.mark.asyncio
async def test_url_wins_over_a_wired_clip(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """卡片上填了链接就以链接为准（与图音生视频的音频链接同一取舍），
    连着的素材不该让这次生成直接失败。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=tmp_path / "out.mp4"))

    await _generate(manager, project_id, input_video_asset_id="asset-video-1", input_video_url=_SOURCE_URL)

    assert stub.calls[0]["kwargs"]["reference_video_path"] == _SOURCE_URL


@pytest.mark.asyncio
async def test_wired_local_clip_is_refused_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    """本地素材送不进去：服务商自己抓参考视频，只认 HTTPS 直链。宁可在这里
    明确说清楚该怎么做，也不要白上传几十 MB 再吃一句 Only HTTPS URLs are
    allowed。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_video_asset(manager, project_id)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path="/tmp/never.mp4"))

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, input_video_asset_id="asset-video-1")

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "HTTPS" in excinfo.value.message
    assert "视频链接" in excinfo.value.message
    assert "dragon_flame" in excinfo.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_missing_source_video_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    _configure(monkeypatch)
    project_id = await _create_project(manager)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "视频素材" in excinfo.value.message


@pytest.mark.asyncio
async def test_non_https_url_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    _configure(monkeypatch)
    project_id = await _create_project(manager)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, input_video_url="http://example.com/clip.mp4")

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "https" in excinfo.value.message


@pytest.mark.asyncio
async def test_empty_prompt_is_rejected(monkeypatch: pytest.MonkeyPatch, manager: DirectorManager):
    """提示词就是"要改成什么样"的全部说明，这张卡片没有它就没有生成目标——
    不能像文生视频那样回落到一句默认提示词。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path="/tmp/never.mp4"))

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {
                "project_id": project_id,
                "mode": "video2video",
                "prompt": "   ",
                "input_video_url": _SOURCE_URL,
            }
        )

    assert excinfo.value.code == "INVALID_PARAMS"
    assert "提示词" in excinfo.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_requires_configured_video_slot(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager
):
    _configure(monkeypatch, video=False)
    project_id = await _create_project(manager)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, input_video_url=_SOURCE_URL)

    assert excinfo.value.code == "NOT_CONFIGURED"
