# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``game.assets.rig_check`` / ``game.assets.rig`` / ``game.assets.retarget``.

动画三件套的行为约束：

1. 骨骼检查只回 riggable / rig_type，**不产出素材**（工程里不应多出占位或
   结果资产）；
2. 自动绑定产出模型，并把绑定任务的 task_id 落到素材上（动画重定向唯一
   接受的输入形态）；
3. 动画重定向对没有绑定 task_id 的模型直接报参数错误，不发起真实请求。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.game import game_manager as gm
from jiuwenswarm.server.runtime.game import game_store as gs
from jiuwenswarm.server.runtime.game.game_item_animation import RigCheckStatus, RigStatus, RetargetStatus
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


# ----------------------------------------------------------------------
# 骨骼检查
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rig_check_submit_returns_job_without_touching_assets(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_submit(model_path: str) -> str:
        assert model_path == "/tmp/hero.glb"
        return "task-check-1"

    monkeypatch.setattr(manager._animation, "submit_rig_check", fake_submit)
    before = len(manager._store.get_project(project.project_id).assets)

    result = await manager.handle_game_assets_rig_check(
        {"project_id": project.project_id, "source_asset_id": "gasset_model"}
    )

    assert result["job_id"] == "task-check-1"
    assert len(result["project"]["assets"]) == before, "骨骼检查不应创建任何素材"


@pytest.mark.asyncio
async def test_rig_check_status_reports_riggable_and_type(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_poll(task_id: str) -> RigCheckStatus:
        assert task_id == "task-check-1"
        return RigCheckStatus(status="success", riggable=True, rig_type="quadruped")

    monkeypatch.setattr(manager._animation, "poll_rig_check", fake_poll)
    result = await manager.handle_game_assets_rig_check_status(
        {"project_id": project.project_id, "job_id": "task-check-1"}
    )

    assert result["status"] == "success"
    assert result["riggable"] is True
    assert result["rig_type"] == "quadruped"


# ----------------------------------------------------------------------
# 自动绑定
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rig_submit_and_success_keep_rig_task_id(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []

    async def fake_submit(model_path: str, *, rig_type: str, spec: str, out_format: str) -> str:
        calls.append((model_path, rig_type, spec, out_format))
        return "task-rig-1"

    async def fake_poll(task_id: str, *, save_dir: str) -> RigStatus:
        assert task_id == "task-rig-1"
        return RigStatus(status="success", model_file_path="/tmp/hero_rigged.glb")

    monkeypatch.setattr(manager._animation, "submit_rig", fake_submit)
    monkeypatch.setattr(manager._animation, "poll_rig", fake_poll)

    submitted = await manager.handle_game_assets_rig(
        {
            "project_id": project.project_id,
            "source_asset_id": "gasset_model",
            "rig_type": "biped",
            "spec": "mixamo",
        }
    )
    assert calls == [("/tmp/hero.glb", "biped", "mixamo", "glb")]
    placeholder = next(
        a for a in submitted["project"]["assets"] if a["asset_id"] == submitted["placeholder_asset_id"]
    )
    assert placeholder["params"]["source"] == "auto_rig"
    assert placeholder["params"]["job_id"] == "task-rig-1"

    result = await manager.handle_game_assets_rig_status(
        {
            "project_id": project.project_id,
            "placeholder_asset_id": submitted["placeholder_asset_id"],
            "job_id": submitted["job_id"],
        }
    )

    assert result["status"] == "success"
    asset = result["asset"]
    assert asset["file_path"] == "/tmp/hero_rigged.glb"
    assert asset["params"]["source"] == "auto_rig"
    assert asset["params"]["rig_type"] == "biped"
    assert asset["params"]["spec"] == "mixamo"
    assert asset["params"]["rig_task_id"] == "task-rig-1"
    assert asset["params"]["tripo_task_id"] == "task-rig-1"
    assert asset["source_asset_id"] == "gasset_model"


# ----------------------------------------------------------------------
# 动画重定向
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retarget_rejects_model_without_rig_task_id(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    async def fake_submit(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "task-anim"

    monkeypatch.setattr(manager._animation, "submit_retarget", fake_submit)

    with pytest.raises(gm.GameRpcError) as exc:
        await manager.handle_game_assets_retarget(
            {"project_id": project.project_id, "source_asset_id": "gasset_model", "animation": "preset:walk"}
        )

    assert exc.value.code == "INVALID_PARAMS"
    assert calls == []


@pytest.mark.asyncio
async def test_retarget_uses_rig_task_id_and_creates_animated_model(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._store.append_asset(
        project.project_id,
        GameAsset(
            asset_id="gasset_rigged",
            type="model",
            name="主角模型 · 自动绑定",
            file_path="/tmp/hero_rigged.glb",
            status="ready",
            params={"source": "auto_rig", "rig_task_id": "task-rig-1"},
            source_asset_id="gasset_model",
        ),
    )
    seen: list[tuple] = []

    async def fake_submit(task_id: str, *, animation: str, animate_in_place: bool, out_format: str) -> str:
        seen.append((task_id, animation, animate_in_place, out_format))
        return "task-anim-1"

    async def fake_poll(task_id: str, *, save_dir: str) -> RetargetStatus:
        return RetargetStatus(status="success", model_file_path="/tmp/hero_walk.glb")

    monkeypatch.setattr(manager._animation, "submit_retarget", fake_submit)
    monkeypatch.setattr(manager._animation, "poll_retarget", fake_poll)

    submitted = await manager.handle_game_assets_retarget(
        {
            "project_id": project.project_id,
            "source_asset_id": "gasset_rigged",
            "animation": "preset:walk",
            "animate_in_place": True,
        }
    )
    # 传给 Tripo3D 的是上游绑定任务的 task_id，不是文件路径
    assert seen == [("task-rig-1", "preset:walk", True, "glb")]

    result = await manager.handle_game_assets_retarget_status(
        {
            "project_id": project.project_id,
            "placeholder_asset_id": submitted["placeholder_asset_id"],
            "job_id": submitted["job_id"],
        }
    )

    assert result["status"] == "success"
    asset = result["asset"]
    assert asset["file_path"] == "/tmp/hero_walk.glb"
    assert asset["params"]["source"] == "animation_retarget"
    assert asset["params"]["animation"] == "preset:walk"
    assert asset["params"]["animate_in_place"] is True
    assert asset["source_asset_id"] == "gasset_rigged"
