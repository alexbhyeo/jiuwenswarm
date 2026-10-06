# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Lab's 视频生音频 (``video2audio``) composer mode.

``video2audio`` does not add a new generation capability - it *chains* two that
already exist: video understanding writes a narration script from the video, and
``audio_gen_tools.generate_audio`` speaks that script. These tests pin the parts
that only exist because it is a chain:

* the two-step call order (understanding first, TTS second, TTS fed the script
  rather than the raw composer text),
* two independent configuration gates with distinguishable messages,
* the input asset must be a *ready video*, not just any asset id,
* the persisted asset is an ``audio`` asset whose ``params`` record both the
  script and the input video path - the frontend rebuilds the dependency edge
  from ``input_video_path`` when it restores the canvas.

Both tools are stubbed here; their own behaviour is covered by
``tests/unit_tests/agents/test_audio_gen_tools.py``.

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

_SAVED = "Audio generated successfully!\nSaved to: {path}"


class _StubTool:
    """Stands in for a LocalFunction tool (``generate_audio`` /
    ``video_understanding``): the manager calls ``._func(...)`` directly."""

    def __init__(self, result: str = "") -> None:
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


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    audio: bool = True,
    understanding: bool = True,
) -> None:
    """Both gates are read from the manager module's own namespace."""
    monkeypatch.setattr(dm, "audio_gen_enabled", lambda: audio)
    monkeypatch.setattr(dm, "audio_gen_configured", lambda: audio)
    monkeypatch.setattr(dm, "_video_understanding_configured", lambda: understanding)


def _stub_tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: str = "镜头里是一只猫在窗台上打盹。",
    tts_result: str = _SAVED.format(path="/tmp/out.wav"),
) -> tuple[_StubTool, _StubTool]:
    """Returns ``(understanding, tts)`` stubs."""
    understanding = _StubTool(script)
    tts = _StubTool(tts_result)
    monkeypatch.setattr(dm, "video_understanding", understanding)
    monkeypatch.setattr(dm, "generate_audio", tts)
    return understanding, tts


async def _create_project(manager: DirectorManager, name: str = "视频配音项目") -> str:
    created = await manager.handle_director_projects_create({"name": name})
    return created["project"]["project_id"]


def _add_asset(
    manager: DirectorManager,
    project_id: str,
    *,
    asset_id: str = "asset-video-1",
    asset_type: str = "video",
    status: str = "ready",
    file_path: str | None = None,
) -> str:
    """Append a real asset record so the branch's lookup has something to find."""
    if file_path is None:
        video_path = ds.get_project_assets_dir(project_id) / f"{asset_id}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        file_path = str(video_path)
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id=asset_id,
            type=asset_type,
            status=status,
            prompt="a generated clip",
            file_path=file_path,
        ),
    )
    return asset_id


def _asset_of(result: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])


async def _generate(
    manager: DirectorManager, project_id: str, **params: Any
) -> dict[str, Any]:
    return await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "video2audio",
            "input_video_asset_id": "asset-video-1",
            **params,
        }
    )


# ---------------------------------------------------------------------------
# the chain itself
# ---------------------------------------------------------------------------


async def test_video2audio_writes_narration_then_speaks_it(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    asset_id = _add_asset(manager, project_id)
    understanding, tts = _stub_tools(monkeypatch)

    result = await _generate(manager, project_id, voice="Leda")

    # Step 1: the video is read with the narration prompt, not the composer text.
    assert len(understanding.calls) == 1
    understanding_inputs = understanding.calls[0]["args"][0]
    assert understanding_inputs["video_path"].endswith(f"{asset_id}.mp4")
    assert understanding_inputs["query"] == dm._NARRATION_PROMPT

    # Step 2: TTS speaks the *script*, not the raw prompt.
    assert len(tts.calls) == 1
    assert tts.calls[0]["kwargs"]["text"] == "镜头里是一只猫在窗台上打盹。"
    assert tts.calls[0]["kwargs"]["voice"] == "Leda"

    asset = _asset_of(result)
    # The product is speech, so the asset is an audio asset even though the mode
    # is video2audio - otherwise it would never show up in the 音频 category.
    assert asset["type"] == "audio"
    assert asset["status"] == "ready"
    assert asset["params"]["script"] == "镜头里是一只猫在窗台上打盹。"
    assert asset["params"]["voice"] == "Leda"


async def test_video2audio_records_the_input_video_for_canvas_reload(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``input_video_path`` is what lets the Lab redraw the dependency edge."""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _stub_tools(monkeypatch)

    result = await _generate(manager, project_id)

    asset = _asset_of(result)
    assert asset["params"]["input_video_asset_id"] == "asset-video-1"
    assert Path(asset["params"]["input_video_path"]).name == "asset-video-1.mp4"
    assert Path(asset["params"]["input_video_path"]).exists()


async def test_video2audio_counts_as_an_audio_asset(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _stub_tools(monkeypatch)

    result = await _generate(manager, project_id)

    assert result["asset_counts"]["audio"] == 1
    assert result["asset_counts"]["video"] == 1  # the input clip itself


async def test_video2audio_shows_the_script_when_no_text_was_typed(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The text port is optional, so the asset must not fall back to a missing
    variable - it shows the generated narration instead."""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _stub_tools(monkeypatch, script="这是自动生成的解说。")

    result = await _generate(manager, project_id)

    assert _asset_of(result)["prompt"] == "这是自动生成的解说。"


async def test_video2audio_forwards_extra_requirements_from_the_text_node(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    understanding, _tts = _stub_tools(monkeypatch)

    await _generate(manager, project_id, prompt="活泼一点，适合短视频")

    query = understanding.calls[0]["args"][0]["query"]
    assert query.startswith(dm._NARRATION_PROMPT)
    assert "活泼一点，适合短视频" in query


async def test_video2audio_without_voice_uses_the_tool_default(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _understanding, tts = _stub_tools(monkeypatch)

    result = await _generate(manager, project_id)

    assert tts.calls[0]["kwargs"]["voice"] == ""
    assert "voice" not in _asset_of(result)["params"]


# ---------------------------------------------------------------------------
# configuration gates - each missing piece must name itself
# ---------------------------------------------------------------------------


async def test_video2audio_requires_tts_configuration(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, audio=False)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    understanding, tts = _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "NOT_CONFIGURED"
    assert "语音生成" in excinfo.value.message
    # Neither step should have run.
    assert understanding.calls == []
    assert tts.calls == []


async def test_video2audio_requires_video_understanding_configuration(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS alone is not enough - the narration has to come from somewhere."""
    _configure(monkeypatch, understanding=False)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    understanding, tts = _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "NOT_CONFIGURED"
    assert "视频理解" in excinfo.value.message
    assert understanding.calls == []
    assert tts.calls == []


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


async def test_video2audio_rejects_a_missing_input_video(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {"project_id": project_id, "mode": "video2audio", "prompt": "x"}
        )

    assert excinfo.value.code == "INVALID_PARAMS"


async def test_video2audio_rejects_an_unknown_input_video(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id, input_video_asset_id="asset-video-does-not-exist")

    assert excinfo.value.code == "INVALID_PARAMS"


@pytest.mark.parametrize("status", ["pending", "failed"])
async def test_video2audio_rejects_a_video_that_is_not_ready(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A clip that is still rendering has no usable file yet."""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, status=status)
    understanding, tts = _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "INVALID_PARAMS"
    assert understanding.calls == []
    assert tts.calls == []


async def test_video2audio_rejects_an_image_asset_as_input(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Lab's video port only accepts video nodes, but the RPC must not trust
    the client: passing an image id is an invalid parameter, not a crash."""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_type="image")
    understanding, tts = _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "INVALID_PARAMS"
    assert understanding.calls == []
    assert tts.calls == []


async def test_video2audio_rejects_a_video_without_a_file(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, file_path="")
    _stub_tools(monkeypatch)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "INVALID_PARAMS"


# ---------------------------------------------------------------------------
# failure mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        "[ERROR]: glm video understanding failed: boom",
        "",
        "   ",
    ],
)
async def test_video2audio_understanding_failure_is_generation_failed(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """An unusable script must not be silently spoken as empty audio."""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _understanding, tts = _stub_tools(monkeypatch, script=script)

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "GENERATION_FAILED"
    assert tts.calls == []


async def test_video2audio_tts_failure_is_generation_failed(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id)
    _stub_tools(monkeypatch, tts_result="[ERROR]: speech synthesis failed: 429")

    with pytest.raises(DirectorRpcError) as excinfo:
        await _generate(manager, project_id)

    assert excinfo.value.code == "GENERATION_FAILED"
    assert "429" in excinfo.value.message


# ---------------------------------------------------------------------------
# mode registration
# ---------------------------------------------------------------------------


def test_video2audio_is_a_supported_mode() -> None:
    assert "video2audio" in dm._SUPPORTED_MODES
