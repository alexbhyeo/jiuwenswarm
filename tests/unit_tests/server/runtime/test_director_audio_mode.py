# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for Director Mode's audio (text-to-speech) composer mode.

Covers the ``audio`` branch of ``DirectorManager.handle_director_generate``: the
tool call, the persisted asset, the ``NOT_CONFIGURED`` gate, error mapping and
the ``world`` regression guard. The TTS tool itself is stubbed here - its own
behaviour is covered by ``tests/unit_tests/agents/test_audio_gen_tools.py``.

Every filesystem path is redirected into ``tmp_path`` by patching
``get_agent_root_dir`` in the ``director_store`` module: all of its path helpers
derive from that one function, so a single patch isolates both
``director_state.json`` and the per-project ``assets/`` directory and the test
never touches the real ``~/.jiuwenswarm``.
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


class _StubAudioTool:
    """Stands in for ``audio_gen_tools.generate_audio`` (a LocalFunction).

    The manager calls ``generate_audio._func(...)`` directly, so the stub only
    needs that one attribute - and it records the kwargs to assert on.
    """

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def _func(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
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


def _configure_audio(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> None:
    """Gate flags are read from the manager module's own namespace."""
    monkeypatch.setattr(dm, "audio_gen_enabled", lambda: enabled)
    monkeypatch.setattr(dm, "audio_gen_configured", lambda: enabled)


def _stub_tool(monkeypatch: pytest.MonkeyPatch, result: str) -> _StubAudioTool:
    tool = _StubAudioTool(result)
    monkeypatch.setattr(dm, "generate_audio", tool)
    return tool


async def _create_project(manager: DirectorManager, name: str = "语音项目") -> str:
    created = await manager.handle_director_projects_create({"name": name})
    return created["project"]["project_id"]


def _asset_of(result: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_audio_mode_saves_ready_audio_asset(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)

    # The tool writes the file itself; the manager only parses its "Saved to:"
    # line, so create it for real to keep the asset's path meaningful.
    assets_dir = ds.get_project_assets_dir(project_id)
    audio_file = assets_dir / "audio_123.wav"
    audio_file.write_bytes(b"RIFFfake")
    tool = _stub_tool(monkeypatch, f"Audio generated successfully!\nSaved to: {audio_file}")

    result = await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "audio",
            "prompt": "你好，世界",
            "voice": "Kore",
        }
    )

    asset = _asset_of(result)
    assert asset["type"] == "audio"
    assert asset["status"] == "ready"
    assert asset["file_path"] == str(audio_file)
    assert asset["params"] == {"voice": "Kore"}
    assert result["asset_counts"]["audio"] == 1

    # 没选风格时的调用形态：style 传空串，工具据此不发 instructions。
    assert tool.calls == [
        {"text": "你好，世界", "voice": "Kore", "style": "", "save_dir": str(assets_dir)}
    ]


async def test_audio_asset_is_persisted_to_disk(
    manager: DirectorManager, isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The asset must survive a fresh store read, not just live in the response."""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {"project_id": project_id, "mode": "audio", "prompt": "hi", "voice": "Puck"}
    )

    reloaded = ds.DirectorStore().get_project(project_id)
    assert reloaded is not None
    persisted = next(a for a in reloaded.assets if a.asset_id == result["asset_id"])
    assert persisted.type == "audio"
    assert persisted.params == {"voice": "Puck"}


# ---------------------------------------------------------------------------
# gating / validation
# ---------------------------------------------------------------------------


async def test_audio_mode_requires_configuration(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_audio(monkeypatch, enabled=False)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "unused")

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {"project_id": project_id, "mode": "audio", "prompt": "hi"}
        )

    assert excinfo.value.code == "NOT_CONFIGURED"
    assert tool.calls == []


async def test_provider_error_becomes_generation_failed(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    _stub_tool(monkeypatch, '[ERROR]: audio generation request failed: 400 unsupported voice')

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {"project_id": project_id, "mode": "audio", "prompt": "hi", "voice": "Nope"}
        )

    assert excinfo.value.code == "GENERATION_FAILED"
    assert "unsupported voice" in excinfo.value.message


async def test_world_mode_still_unsupported(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabling audio must not accidentally enable the other placeholder mode."""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)

    with pytest.raises(DirectorRpcError) as excinfo:
        await manager.handle_director_generate(
            {"project_id": project_id, "mode": "world", "prompt": "hi"}
        )

    assert excinfo.value.code == "NOT_SUPPORTED"


def test_supported_modes_include_audio() -> None:
    assert "audio" in dm._SUPPORTED_MODES
    assert "world" not in dm._SUPPORTED_MODES


# ---------------------------------------------------------------------------
# audio-specific argument handling
# ---------------------------------------------------------------------------


async def test_audio_mode_speaks_at_tokens_verbatim(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS has no reference-image slot, so an @token is spoken, not resolved."""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "audio",
            "prompt": "读一下 @播报员 这个词",
        }
    )

    assert tool.calls[0]["text"] == "读一下 @播报员 这个词"


async def test_audio_mode_without_voice_leaves_params_empty(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No voice means the tool's own default applies; params records nothing."""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {"project_id": project_id, "mode": "audio", "prompt": "hi"}
    )

    assert _asset_of(result)["params"] == {}
    assert tool.calls[0]["voice"] == ""


async def test_audio_mode_ignores_visual_params_sent_by_the_lab(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every Lab process card sends aspect/resolution/duration; the audio branch
    must ignore them instead of choking on unexpected keys."""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "audio",
            "prompt": "游过平静的海面",
            "voice": "Leda",
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "duration_seconds": 15,
        }
    )

    asset = _asset_of(result)
    assert asset["type"] == "audio"
    # Only the voice is meaningful for TTS - no visual params leak into the asset.
    assert asset["params"] == {"voice": "Leda"}
    assert tool.calls[0]["text"] == "游过平静的海面"


# ---------------------------------------------------------------------------
# 风格（delivery style）
# ---------------------------------------------------------------------------


async def test_audio_mode_forwards_style_to_the_tool_and_records_it(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """风格与音色正交：音色决定"谁在说"，风格决定"怎么说"。两者都要原样
    交给 TTS 工具（工具再把它作为 instructions 发出去），并记进 params——
    否则重摆画布/事后追溯就还原不出用户当时选的是哪一档。"""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "audio",
            "prompt": "The vault door creaked open behind me.",
            "voice": "Kore",
            "style": "whispered urgently, barely audible",
        }
    )

    assert tool.calls[0]["style"] == "whispered urgently, barely audible"
    assert tool.calls[0]["voice"] == "Kore"
    assert _asset_of(result)["params"] == {
        "voice": "Kore",
        "style": "whispered urgently, barely audible",
    }


async def test_audio_mode_without_style_omits_it_from_params(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没选风格时 params 里不该出现 style，交给工具的是空串（工具据此不发
    instructions）——历史素材与旧画布数据的行为因此完全不变。"""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {"project_id": project_id, "mode": "audio", "prompt": "hi", "voice": "Kore"}
    )

    assert _asset_of(result)["params"] == {"voice": "Kore"}
    assert tool.calls[0]["style"] == ""


async def test_audio_mode_treats_blank_style_as_absent(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前端在"默认"档上发的就是空串，不能因此记一个空风格进 params。"""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    result = await manager.handle_director_generate(
        {"project_id": project_id, "mode": "audio", "prompt": "hi", "voice": "Kore", "style": "   "}
    )

    assert "style" not in _asset_of(result)["params"]
    assert tool.calls[0]["style"] == ""


async def test_audio_mode_passes_inline_tags_through_verbatim(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """尖括号标记是"即时效果"，必须原样出现在朗读文本里：任何清洗/转义都会
    让效果失效（或被一字不差地读出来）。"""
    _configure_audio(monkeypatch)
    project_id = await _create_project(manager)
    tool = _stub_tool(monkeypatch, "Audio generated successfully!\nSaved to: /tmp/x.wav")

    spoken = "Wait... <short pause> did you hear that? <sigh>"
    await manager.handle_director_generate(
        {"project_id": project_id, "mode": "audio", "prompt": spoken, "voice": "Kore"}
    )

    assert tool.calls[0]["text"] == spoken
