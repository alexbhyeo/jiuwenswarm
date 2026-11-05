# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""剪辑助手 must reuse assets the user already has instead of regenerating them,
and a canvas swap must actually retarget what ``@名称`` resolves to.

Two field reports drove these tests:

1. The user attached the main-character picture and wrote "@full 是主角", yet the
   assistant generated a *second* image and also named it ``full``.
2. After swapping that card's picture back to the original, later scene
   generation still used the generated image.

Root causes pinned here:

- ``@名称`` resolution (``DirectorStore.find_asset_by_name``) is name-based and
  picks the **most recently updated** holder. Two assets called ``full`` make the
  target flip to whoever wrote last, and nothing on the canvas can fix it.
- The single-agent edit-chat turn never told the model which assets exist, nor
  which asset each attached image is, so "regenerate everything" was the only
  thing it could do.
- A canvas swap only rewrote the node mirrors; the asset record — and therefore
  ``@名称`` — kept pointing at the old picture.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_manager import DirectorManager
from jiuwenswarm.server.runtime.director.director_store import DirectorAsset, DirectorStore


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(isolated_root: Path) -> DirectorStore:
    return DirectorStore()


@pytest.fixture
def manager(isolated_root: Path) -> DirectorManager:
    return DirectorManager()


def _asset(
    asset_id: str,
    name: str | None,
    *,
    kind: str = "image",
    path: str | None = None,
    updated_at: float | None = None,
) -> DirectorAsset:
    now = time.time() if updated_at is None else updated_at
    return DirectorAsset(
        asset_id=asset_id,
        type=kind,
        status="ready",
        prompt="",
        params={},
        file_path=path or f"/tmp/{asset_id}.png",
        job_id=None,
        error=None,
        name=name,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# DirectorStore.bind_asset_name — one name, exactly one holder
# ---------------------------------------------------------------------------


def test_bind_asset_name_makes_the_named_asset_the_one_at_name_resolves_to(store: DirectorStore) -> None:
    project = store.create_project("同名校验")
    pid = project.project_id
    original = _asset("asset_orig", "full", updated_at=time.time() - 60)
    generated = _asset("asset_gen", "full", kind="character", updated_at=time.time())
    store.append_asset(pid, original)
    store.append_asset(pid, generated)
    # 默认行为就是这次的 bug：同名取最新的 → 指到"生成"的那条。
    assert store.find_asset_by_name(pid, "full").asset_id == "asset_gen"

    store.bind_asset_name(pid, "full", "asset_orig")

    resolved = store.find_asset_by_name(pid, "full")
    assert resolved is not None and resolved.asset_id == "asset_orig"
    names = {a.asset_id: a.name for a in store.get_project(pid).assets}
    assert names["asset_orig"] == "full"
    # 另一条让出名字（文件保留，只是回退展示 prompt），否则同名又会出现。
    assert names["asset_gen"] is None


def test_bind_asset_name_is_case_insensitive_about_the_previous_holder(store: DirectorStore) -> None:
    project = store.create_project("大小写")
    pid = project.project_id
    store.append_asset(pid, _asset("asset_a", "Full"))
    store.append_asset(pid, _asset("asset_b", "full"))

    store.bind_asset_name(pid, "FULL", "asset_b")

    names = {a.asset_id: (a.name or "").lower() for a in store.get_project(pid).assets}
    assert names["asset_b"] == "full"
    assert names["asset_a"] == ""


# ---------------------------------------------------------------------------
# generate_design_image — refuse to duplicate an existing name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_design_image_refuses_a_name_that_already_exists(
    manager: DirectorManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No second image, no model call — tell the model to reuse ``@name``."""

    class _ExplodingVisual:
        calls = 0

        async def _func(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover - must not run
            _ExplodingVisual.calls += 1
            raise AssertionError("generate_visual must not be called for an existing name")

    monkeypatch.setattr(dm, "visual_gen_enabled", lambda: True)
    monkeypatch.setattr(dm, "visual_gen_configured", lambda: True)
    monkeypatch.setattr(dm, "generate_visual", _ExplodingVisual())

    project = manager._store.create_project("去重")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_existing", "full", kind="character"))
    project = manager._store.get_project(pid)

    text, asset_id = await manager._execute_generate_design_image(
        project, {"kind": "character", "name": "full", "description": "一个 Q 版小男孩医生"}
    )

    assert asset_id is None
    assert _ExplodingVisual.calls == 0
    assert "@full" in text
    # 素材数量没变，项目里仍然只有一条叫 full 的素材。
    assets = manager._store.get_project(pid).assets
    assert len(assets) == 1
    assert manager._store.find_asset_by_name(pid, "full").asset_id == "asset_existing"


# ---------------------------------------------------------------------------
# Attached picture + "@新名字 是主角" → bind the name to the attachment
# ---------------------------------------------------------------------------


def test_mentioned_name_binds_to_the_attached_picture(manager: DirectorManager) -> None:
    project = manager._store.create_project("绑定附件")
    pid = project.project_id
    # 用户上传的主角图，还没有名字。
    manager._store.append_asset(pid, _asset("asset_attached", None))
    project = manager._store.get_project(pid)

    updated = manager._bind_mentioned_names_to_attachments(project, "@full 是主角。帮我做一个 15 秒的视频。", ["asset_attached"])

    resolved = manager._store.find_asset_by_name(pid, "full")
    assert resolved is not None, "点名 + 附件应当补上名字"
    assert resolved.asset_id == "asset_attached"
    assert updated.project_id == pid


def test_mentioned_name_does_not_hijack_an_existing_asset(manager: DirectorManager) -> None:
    """``@已存在的名字`` 已经能解析，不许把附件改成那个名字。"""

    project = manager._store.create_project("不改已有")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_named", "full", kind="character"))
    manager._store.append_asset(pid, _asset("asset_attached", None))
    project = manager._store.get_project(pid)

    manager._bind_mentioned_names_to_attachments(project, "@full 是主角", ["asset_attached"])

    assert manager._store.find_asset_by_name(pid, "full").asset_id == "asset_named"
    attached = next(a for a in manager._store.get_project(pid).assets if a.asset_id == "asset_attached")
    assert attached.name is None


def test_mention_renames_an_attachment_named_after_its_file(manager: DirectorManager) -> None:
    """上传的素材默认用文件名当名字，用户用 ``@名字`` 点名时应以文字为准。"""

    project = manager._store.create_project("文件名改名")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_attached", "heroA"))
    project = manager._store.get_project(pid)

    manager._bind_mentioned_names_to_attachments(project, "@full 是主角", ["asset_attached"])

    assert manager._store.find_asset_by_name(pid, "full").asset_id == "asset_attached"
    assert manager._store.find_asset_by_name(pid, "heroA") is None
    attached = next(a for a in manager._store.get_project(pid).assets if a.asset_id == "asset_attached")
    assert attached.params.get("prior_name") == "heroA", "原名要留在 params 里便于追溯"


def test_ambiguous_attachments_are_left_alone(manager: DirectorManager) -> None:
    """两张附件配一个名字时无从判断，不做猜测。"""

    project = manager._store.create_project("多附件")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_a", None))
    manager._store.append_asset(pid, _asset("asset_b", None))
    project = manager._store.get_project(pid)

    manager._bind_mentioned_names_to_attachments(project, "@full 是主角", ["asset_a", "asset_b"])

    assert manager._store.find_asset_by_name(pid, "full") is None


# ---------------------------------------------------------------------------
# The prompt context the model actually sees
# ---------------------------------------------------------------------------


def test_asset_context_lists_reusable_assets_and_maps_attachments(manager: DirectorManager) -> None:
    project = manager._store.create_project("上下文")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_full", "full", kind="character"))
    manager._store.append_asset(pid, _asset("asset_lobby", "Hospital Lobby"))
    manager._store.append_asset(pid, _asset("asset_vid", "clip", kind="video"))
    project = manager._store.get_project(pid)

    text = dm._build_asset_context(project, ["asset_full"])

    assert "@full（角色/道具）" in text
    assert "@Hospital Lobby（图片）" in text
    # 视频不是 @引用源，不该出现在清单里。
    assert "@clip" not in text
    assert "第 1 张图片" in text
    assert "不要重新生成" in text


def test_asset_context_is_empty_without_assets_or_attachments(manager: DirectorManager) -> None:
    project = manager._store.create_project("空项目")
    assert dm._build_asset_context(project, []) == ""


def test_asset_context_mentions_unnamed_attachments(manager: DirectorManager) -> None:
    project = manager._store.create_project("未命名附件")
    pid = project.project_id
    manager._store.append_asset(pid, _asset("asset_attached", None))
    project = manager._store.get_project(pid)

    text = dm._build_asset_context(project, ["asset_attached"])

    assert "未命名素材" in text
