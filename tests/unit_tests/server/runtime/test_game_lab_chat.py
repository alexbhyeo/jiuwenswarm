# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""实验室助手（游戏模式）的行为约束。

三件事必须钉住，否则"和剪辑助手一样但只能调处理卡片"这条要求会在别处悄悄失效：

1. 工具表里**只有**游戏模式的四张处理卡片——多一个 ``generate_design_image``
   就等于把导演模式的生成能力漏进游戏实验室。
2. 素材名称只通过 system 上下文给出，不额外开"读素材"工具，权限边界才清晰。
3. 一轮对话要真的落盘（用户消息 + 助手消息），且卡片产出的名称要能回到模型
   的 tool 消息里，下一句才不会编造产出。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.game import game_lab_chat as glc
from jiuwenswarm.server.runtime.game import game_manager as gm
from jiuwenswarm.server.runtime.game import game_store as gs
from jiuwenswarm.server.runtime.game.game_lab_chat import (
    LAB_TOOL_NAMES,
    LAB_TOOLS_SCHEMA,
    GameLabChat,
    LabChatDeps,
    build_asset_context,
    resolve_asset_by_name,
)
from jiuwenswarm.server.runtime.game.game_store import GameAsset, GameStore


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(gs, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(isolated_root: Path) -> GameStore:
    return GameStore()


def _project(store: GameStore) -> Any:
    project = store.create_project("测试工程")
    store.append_asset(
        project.project_id,
        GameAsset(
            asset_id="gasset_img",
            type="character",
            name="主角",
            file_path="/tmp/hero.png",
            status="ready",
            params={},
        ),
    )
    return store.get_project(project.project_id)


class _FakeCards:
    """替代真实 Tripo3D 调用：提交即排队，第一次轮询就成功。"""

    def __init__(self, store: GameStore, *, pending_forever: bool = False) -> None:
        self._store = store
        self._pending_forever = pending_forever
        self.calls: list[str] = []
        # 记录每次 check_status 收到的参数：卡片契约要求哪些字段靠这里钉住。
        self.check_params: dict[str, list[dict[str, Any]]] = {}

    async def handle_game_assets_decompose(self, params: dict) -> dict:
        self.calls.append("decompose")
        return {
            "placeholder_asset_id": "ph_1",
            "job_id": "job_1",
            "decompose_batch_id": "batch_1",
        }

    def _record(self, kind: str, params: dict) -> None:
        self.check_params.setdefault(kind, []).append(dict(params))

    async def handle_game_assets_decompose_check_status(self, params: dict) -> dict:
        self._record("decompose", params)
        if self._pending_forever:
            return {"status": "pending"}
        project = self._store.update_project(
            params["project_id"],
            assets=[
                GameAsset(
                    asset_id="gasset_part", type="model_part", name="主角 · 头部",
                    file_path="/tmp/head.glb", status="ready", params={},
                )
            ],
        )
        return {"status": "success", "project": project.to_dict(), "assets": [{"name": "主角 · 头部"}]}

    async def handle_game_assets_to_model(self, params: dict) -> dict:
        self.calls.append("to_model")
        return {"placeholder_asset_id": "ph_2", "job_id": "job_2"}

    async def handle_game_assets_to_model_check_status(self, params: dict) -> dict:
        self._record("to_model", params)
        return {"status": "success", "asset": {"name": "主角 · 2D转3D"}}

    async def handle_game_assets_mesh_complete(self, params: dict) -> dict:
        self.calls.append("mesh_complete")
        return {"placeholder_asset_id": "ph_3", "job_id": "job_3"}

    async def handle_game_assets_mesh_complete_check_status(self, params: dict) -> dict:
        self._record("mesh_complete", params)
        return {"status": "success", "asset": {"name": "主角 · 网格补全"}}

    async def handle_game_assets_mesh_decimate(self, params: dict) -> dict:
        self.calls.append("mesh_decimate")
        return {"placeholder_asset_id": "ph_4", "job_id": "job_4"}

    async def handle_game_assets_mesh_decimate_check_status(self, params: dict) -> dict:
        self._record("mesh_decimate", params)
        return {"status": "success", "asset": {"name": "主角 · 拓扑重构"}}

    # GameLabChat 通过 manager._store 读写对话
    @property
    def _store_ref(self) -> GameStore:
        return self._store


class _TurnScript:
    """按脚本依次返回 assistant 消息，并记录收到的 messages。"""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self.seen: list[list[dict[str, Any]]] = []

    async def __call__(self, messages: list[dict[str, Any]], tools: Any = None, **_: Any) -> dict[str, Any]:
        self.seen.append(list(messages))
        index = min(len(self.seen) - 1, len(self._replies) - 1)
        return self._replies[index]


def _tool_call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _deps(complete: _TurnScript, *, timeout: float = 5.0) -> LabChatDeps:
    async def _no_sleep(_seconds: float) -> None:
        return None

    return LabChatDeps(complete=complete, sleep=_no_sleep, poll_interval=0.0, tool_timeout=timeout)


def test_tool_schema_contains_only_game_process_cards() -> None:
    assert LAB_TOOL_NAMES == {"decompose_asset", "image_to_model", "mesh_complete", "mesh_decimate"}
    serialized = json.dumps(LAB_TOOLS_SCHEMA, ensure_ascii=False)
    for director_tool in ("generate_design_image", "generate_shot_video", "generate_video_bridge"):
        assert director_tool not in serialized
    assert len(LAB_TOOLS_SCHEMA) == 4


def test_asset_context_lists_ready_assets(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    context = build_asset_context(project)
    assert "主角" in context
    assert "角色图片" in context


def test_asset_context_guides_empty_project(isolated_root: Path, store: GameStore) -> None:
    project = store.create_project("空工程")
    context = build_asset_context(store.get_project(project.project_id))
    assert "创作" in context


def test_resolve_asset_by_name_matches_exactly_and_by_prefix(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    assert resolve_asset_by_name(project, "主角") is not None
    assert resolve_asset_by_name(project, "@主角") is not None
    assert resolve_asset_by_name(project, "主") is not None
    assert resolve_asset_by_name(project, "不存在") is None


@pytest.mark.asyncio
async def test_run_turn_executes_card_and_persists_messages(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    cards = _FakeCards(store)
    script = _TurnScript(
        [
            {"content": "我来分解主角。", "tool_calls": [_tool_call("decompose_asset", {"asset_name": "主角"})]},
            {"content": "已经拆好了。"},
        ]
    )

    result = await GameLabChat(_deps(script)).run_turn(cards, project, "把主角拆成部件")

    assert cards.calls == ["decompose"]
    assert result["reply"] == "已经拆好了。"
    assert result["applied"] and "主角 · 头部" in result["applied"][0]

    saved = store.get_project(project.project_id)
    roles = [m["role"] for m in saved.lab_chat_messages]
    assert roles == ["user", "assistant"]
    assert saved.lab_chat_messages[0]["content"] == "把主角拆成部件"

    # 系统上下文 + 历史都进了模型输入；tool 消息带回了产出名称
    second_round = script.seen[1]
    tool_messages = [m for m in second_round if m.get("role") == "tool"]
    assert tool_messages and "主角 · 头部" in tool_messages[0]["content"]
    assert any(m.get("role") == "system" and "主角" in str(m.get("content")) for m in second_round)


@pytest.mark.asyncio
async def test_run_turn_rejects_non_card_tool(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    cards = _FakeCards(store)
    script = _TurnScript(
        [
            {
                "content": "",
                "tool_calls": [_tool_call("generate_design_image", {"name": "x", "description": "y"})],
            },
            {"content": "抱歉，我不能生成图片。"},
        ]
    )

    result = await GameLabChat(_deps(script)).run_turn(cards, project, "画一张图")

    assert cards.calls == []
    tool_messages = [m for m in script.seen[1] if m.get("role") == "tool"]
    assert "不支持的工具" in tool_messages[0]["content"]
    assert result["applied"] == []


@pytest.mark.asyncio
async def test_run_turn_unknown_asset_returns_context(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    cards = _FakeCards(store)
    script = _TurnScript(
        [
            {
                "content": "",
                "tool_calls": [_tool_call("image_to_model", {"asset_name": "不存在的主角"})],
            },
            {"content": "项目里没有这个素材。"},
        ]
    )

    await GameLabChat(_deps(script)).run_turn(cards, project, "把它转成模型")

    assert cards.calls == []
    tool_messages = [m for m in script.seen[1] if m.get("role") == "tool"]
    assert "找不到名为" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_poll_timeout_marks_card_failed(isolated_root: Path, store: GameStore) -> None:
    project = _project(store)
    cards = _FakeCards(store, pending_forever=True)
    script = _TurnScript(
        [
            {"content": "", "tool_calls": [_tool_call("decompose_asset", {"asset_name": "主角"})]},
            {"content": "任务超时了。"},
        ]
    )

    result = await GameLabChat(_deps(script, timeout=0.0)).run_turn(cards, project, "分解主角")

    tool_messages = [m for m in script.seen[1] if m.get("role") == "tool"]
    assert "超时" in tool_messages[0]["content"]
    assert result["applied"] == []


@pytest.mark.asyncio
async def test_handler_rejects_when_edit_chat_not_configured(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = gm.GameManager()
    project = manager._store.create_project("测试工程")
    monkeypatch.setattr(gm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(gm, "edit_chat_configured", lambda: False)

    with pytest.raises(gm.GameRpcError) as excinfo:
        await manager.handle_game_lab_chat_send({"project_id": project.project_id, "text": "你好"})

    assert excinfo.value.code == "NOT_CONFIGURED"


def test_turn_has_bounded_iterations() -> None:
    """迭代上限存在且为正——防止模型不停调卡片把一轮对话拖死。"""
    assert glc.MAX_TOOL_ITERATIONS >= 1
    assert LabChatDeps().max_iterations == glc.MAX_TOOL_ITERATIONS


@pytest.mark.asyncio
async def test_mesh_decimate_poll_carries_job_id(isolated_root: Path, store: GameStore) -> None:
    """拓扑重构的 check_status 要求显式 job_id。

    实测踩过：只传 placeholder_asset_id 时后端直接抛
    ``INVALID_PARAMS：缺少 placeholder_asset_id / job_id``，卡片在用户看来
    是"点了没反应"，还会留下 pending 占位素材。
    """
    project = store.create_project("测试工程")
    store.append_asset(
        project.project_id,
        GameAsset(
            asset_id="gasset_model",
            type="model",
            name="剑模型",
            file_path="/tmp/sword.glb",
            status="ready",
            params={},
        ),
    )
    project = store.get_project(project.project_id)
    cards = _FakeCards(store)
    script = _TurnScript(
        [
            {"content": "", "tool_calls": [_tool_call("mesh_decimate", {"asset_name": "剑模型"})]},
            {"content": "减面完成。"},
        ]
    )

    await GameLabChat(_deps(script)).run_turn(cards, project, "给剑减面")

    params = cards.check_params.get("mesh_decimate")[0]
    assert params["placeholder_asset_id"] == "ph_4"
    assert params["job_id"] == "job_4"


@pytest.mark.asyncio
async def test_card_failure_cleans_up_placeholder(isolated_root: Path, store: GameStore) -> None:
    """轮询抛错时要删掉自己建的占位素材，不留 pending 孤儿。"""
    project = _project(store)
    cards = _FakeCards(store)
    errors: list[dict] = []

    async def _boom(params: dict) -> dict:
        errors.append(dict(params))
        raise RuntimeError("check_status 挂了")

    cards.handle_game_assets_decompose_check_status = _boom  # type: ignore[method-assign]
    script = _TurnScript(
        [
            {"content": "", "tool_calls": [_tool_call("decompose_asset", {"asset_name": "主角"})]},
            {"content": "失败了。"},
        ]
    )

    with pytest.raises(RuntimeError):
        await GameLabChat(_deps(script)).run_turn(cards, project, "分解主角")

    assert errors, "check_status 应该被调用"
    assert not any(a.asset_id == "ph_1" for a in store.get_project(project.project_id).assets)
