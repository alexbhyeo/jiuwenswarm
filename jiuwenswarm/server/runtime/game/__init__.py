# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""游戏模式（游戏工坊）运行时.

一个项目对应磁盘上一个真实的 Godot 工程目录；项目与素材的元数据持久化在
``<root>/agent/game/game_state.json``。设计见
``.agents/agent-docs/designs/proposals/D-20261107-1637-game-mode``。
"""

from jiuwenswarm.server.runtime.game.game_store import (
    GAME_ASSET_TYPES,
    GameAsset,
    GameProject,
    GameStore,
    get_game_projects_dir,
)

__all__ = [
    "GAME_ASSET_TYPES",
    "GameAsset",
    "GameProject",
    "GameStore",
    "get_game_projects_dir",
]
