# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``game.assets.generate`` 在实验室「文生图 / 图生图」卡片下的行为。

这两个卡片和创作页的生成共用同一个 RPC，差别全在参数上，所以最值得钉住的
是三件事：

1. ``source`` 原样写进素材 ``params.source``（「生成实验室流程图」靠它还原
   卡片），不传时仍是创作页原来的 ``generate``；
2. 图生图的 ``reference_asset_ids`` 在服务端解析成文件路径交给
   ``generate_visual``，并把 id 列表与第一张的来源素材一起落到素材上；
3. 参考图不存在/未就绪时直接报参数错误，不白白发起一次真实生成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.agents.harness.common.tools import visual_gen_tools as vgt
from jiuwenswarm.server.runtime.game import game_manager as gm
from jiuwenswarm.server.runtime.game import game_store as gs
from jiuwenswarm.server.runtime.game.game_store import GameAsset


class _StubGenerateVisual:
    """替身：记录调用参数，返回一行 ``Saved to:`` 结果（与真实工具一致）。"""

    def __init__(self, result: str = "Saved to: /tmp/out.png") -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def _func(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.result


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
            asset_id="gasset_ref_a",
            type="character",
            name="主角",
            file_path="/tmp/hero.png",
            status="ready",
            params={},
        ),
    )
    manager._store.append_asset(
        created.project_id,
        GameAsset(
            asset_id="gasset_ref_b",
            type="world",
            name="武器",
            file_path="/tmp/sword.png",
            status="ready",
            params={},
        ),
    )
    return created


@pytest.mark.asyncio
async def test_text2image_records_source_and_has_no_reference_image(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerateVisual()
    monkeypatch.setattr(vgt, "generate_visual", stub)

    result = await manager.handle_game_assets_generate(
        {
            "project_id": project.project_id,
            "prompt": "chibi forest elf",
            "type": "character",
            "source": "text2image",
        }
    )

    asset = result["asset"]
    assert asset["status"] == "ready"
    assert asset["params"]["source"] == "text2image"
    assert asset["params"]["prompt"] == "chibi forest elf"
    assert asset["source_asset_id"] is None
    assert stub.calls[0]["reference_image_paths"] is None


@pytest.mark.asyncio
async def test_image_ref_resolves_reference_asset_ids_to_paths(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerateVisual()
    monkeypatch.setattr(vgt, "generate_visual", stub)

    result = await manager.handle_game_assets_generate(
        {
            "project_id": project.project_id,
            "prompt": "side-on battle pose",
            "type": "world",
            "source": "image_ref",
            "reference_asset_ids": ["gasset_ref_a", "gasset_ref_b"],
        }
    )

    assert stub.calls[0]["reference_image_paths"] == ["/tmp/hero.png", "/tmp/sword.png"]
    asset = result["asset"]
    assert asset["params"]["source"] == "image_ref"
    assert asset["params"]["reference_asset_ids"] == ["gasset_ref_a", "gasset_ref_b"]
    assert asset["source_asset_id"] == "gasset_ref_a"


@pytest.mark.asyncio
async def test_image_ref_rejects_missing_reference_asset(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubGenerateVisual()
    monkeypatch.setattr(vgt, "generate_visual", stub)

    with pytest.raises(gm.GameRpcError) as exc:
        await manager.handle_game_assets_generate(
            {
                "project_id": project.project_id,
                "prompt": "x",
                "type": "character",
                "source": "image_ref",
                "reference_asset_ids": ["gasset_missing"],
            }
        )

    assert exc.value.code == "INVALID_PARAMS"
    assert stub.calls == []


@pytest.mark.asyncio
async def test_composer_generation_keeps_generate_source(
    manager: gm.GameManager, project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """创作页不传 ``source`` 时行为不变，历史素材继续按 generate 归类。"""
    stub = _StubGenerateVisual()
    monkeypatch.setattr(vgt, "generate_visual", stub)

    result = await manager.handle_game_assets_generate(
        {"project_id": project.project_id, "prompt": "silent hill", "type": "world"}
    )

    assert result["asset"]["params"]["source"] == "generate"
    assert result["asset"]["source_asset_id"] is None
