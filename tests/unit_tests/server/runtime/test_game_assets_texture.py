# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``game.assets.texture`` / ``game.assets.texture_check_status`` 的行为。

纹理贴图是"提交 -> 轮询 -> 下载"的单段流水线（Tripo3D models/texture），
这里把三件最容易写错的事钉住：

1. 源素材必须是已就绪的 model / model_part，否则直接报参数错误；
2. 提交时把风格提示词与质量落到占位素材上，轮询期间不丢；
3. 成功后占位素材被替换成真正的模型素材，params.source == "texture"，
   并保留 source_asset_id（供「生成实验室流程图」还原依赖）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.game import game_manager as gm
from jiuwenswarm.server.runtime.game import game_store as gs
from jiuwenswarm.server.runtime.game.game_item_texture import TextureStatus
from jiuwenswarm.server.runtime.game.game_store import GameAsset


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(gs, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def manager(isolated_root: Path) -> gm.GameManager:
    return gm.GameManager()


@pytest.fixture
def project(manager: gm.GameManager) -> Any:
    created = manager._store.create_project("测试工程")
    manager._store.append_asset(
        created.project_id,
        GameAsset(
            asset_id="gasset_model",
            type="model",
            name="主角模型",
            file_path="/tmp/hero.glb",
            status="ready",
            params={},
        ),
    )
    return created


def _stub_submit(monkeypatch: pytest.MonkeyPatch, manager: gm.GameManager) -> list[tuple]:
    calls: list[tuple] = []

    async def fake_submit(model_path: str, *, prompt: str | None = None, quality: str = "standard") -> str:
        calls.append((model_path, prompt, quality))
        return "task-texture-1"

    monkeypatch.setattr(manager._texture, "submit", fake_submit)
    return calls


@pytest.mark.asyncio
async def test_submit_creates_pending_placeholder_with_options(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_submit(monkeypatch, manager)

    result = await manager.handle_game_assets_texture(
        {
            "project_id": project.project_id,
            "source_asset_id": "gasset_model",
            "prompt": "worn leather",
            "quality": "detailed",
        }
    )

    assert calls == [("/tmp/hero.glb", "worn leather", "detailed")]
    assert result["job_id"] == "task-texture-1"
    placeholder = next(a for a in result["project"]["assets"] if a["asset_id"] == result["placeholder_asset_id"])
    assert placeholder["status"] == "pending"
    assert placeholder["params"]["source"] == "texture"
    assert placeholder["params"]["prompt"] == "worn leather"
    assert placeholder["params"]["quality"] == "detailed"
    assert placeholder["source_asset_id"] == "gasset_model"


@pytest.mark.asyncio
async def test_submit_rejects_non_model_source(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_submit(monkeypatch, manager)
    manager._store.append_asset(
        project.project_id,
        GameAsset(asset_id="gasset_img", type="character", name="主角图", file_path="/tmp/a.png", status="ready"),
    )

    with pytest.raises(gm.GameRpcError) as exc:
        await manager.handle_game_assets_texture(
            {"project_id": project.project_id, "source_asset_id": "gasset_img"}
        )

    assert exc.value.code == "INVALID_PARAMS"
    assert calls == []


@pytest.mark.asyncio
async def test_check_status_pending_keeps_placeholder(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_submit(monkeypatch, manager)
    submitted = await manager.handle_game_assets_texture(
        {"project_id": project.project_id, "source_asset_id": "gasset_model"}
    )

    async def fake_poll(task_id: str, *, save_dir: str) -> TextureStatus:
        assert task_id == "task-texture-1"
        return TextureStatus(status="pending")

    monkeypatch.setattr(manager._texture, "poll", fake_poll)
    result = await manager.handle_game_assets_texture_check_status(
        {
            "project_id": project.project_id,
            "placeholder_asset_id": submitted["placeholder_asset_id"],
            "job_id": submitted["job_id"],
        }
    )

    assert result["status"] == "pending"
    assert any(a["asset_id"] == submitted["placeholder_asset_id"] for a in result["project"]["assets"])


@pytest.mark.asyncio
async def test_check_status_success_replaces_placeholder_with_textured_model(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_submit(monkeypatch, manager)
    submitted = await manager.handle_game_assets_texture(
        {
            "project_id": project.project_id,
            "source_asset_id": "gasset_model",
            "prompt": "worn leather",
            "quality": "extreme",
        }
    )

    async def fake_poll(task_id: str, *, save_dir: str) -> TextureStatus:
        return TextureStatus(
            status="success", model_file_path="/tmp/hero_textured.glb", rendered_image_path="/tmp/preview.png"
        )

    monkeypatch.setattr(manager._texture, "poll", fake_poll)
    result = await manager.handle_game_assets_texture_check_status(
        {
            "project_id": project.project_id,
            "placeholder_asset_id": submitted["placeholder_asset_id"],
            "job_id": submitted["job_id"],
        }
    )

    assert result["status"] == "success"
    asset = result["asset"]
    assert asset["file_path"] == "/tmp/hero_textured.glb"
    assert asset["type"] == "model"
    assert asset["source_asset_id"] == "gasset_model"
    assert asset["params"]["source"] == "texture"
    assert asset["params"]["prompt"] == "worn leather"
    assert asset["params"]["quality"] == "extreme"
    assert asset["params"]["rendered_image_path"] == "/tmp/preview.png"
    assert not any(
        a["asset_id"] == submitted["placeholder_asset_id"] for a in result["project"]["assets"]
    )


@pytest.mark.asyncio
async def test_check_status_failure_reports_error_and_drops_placeholder(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_submit(monkeypatch, manager)
    submitted = await manager.handle_game_assets_texture(
        {"project_id": project.project_id, "source_asset_id": "gasset_model"}
    )

    async def fake_poll(task_id: str, *, save_dir: str) -> TextureStatus:
        return TextureStatus(status="failed", error="Tripo3D 贴图任务未成功: boom")

    monkeypatch.setattr(manager._texture, "poll", fake_poll)
    result = await manager.handle_game_assets_texture_check_status(
        {
            "project_id": project.project_id,
            "placeholder_asset_id": submitted["placeholder_asset_id"],
            "job_id": submitted["job_id"],
        }
    )

    assert result["status"] == "failed"
    assert "boom" in result["error"]
    assert not any(
        a["asset_id"] == submitted["placeholder_asset_id"] for a in result["project"]["assets"]
    )
