# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the 剪辑助手 (edit chat) skill wiring.

Two behaviours are pinned here:

* the assistant's own instructions come from the installed
  ``director-edit-assistant`` skill (so users can read/edit them in the Skills
  panel) and fall back to the built-in constant when that skill is missing or
  unreadable - losing the skill must never break the panel;
* skills the user selected for a turn are appended to the system prompt, while
  names that no longer resolve are skipped instead of failing the turn.

The filesystem is redirected into ``tmp_path`` by patching the two directory
helpers in ``director_skills``, so nothing touches the real workspace.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.director import director_skills as dsk
from jiuwenswarm.server.runtime.director.director_manager import (
    _EDIT_CHAT_SYSTEM_PROMPT,
    _build_edit_chat_system_prompt,
)


def _write_skill(root: Path, name: str, *, description: str = "", body: str = "正文内容") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\nname: {name}\ndescription: \"{description}\"\n---\n\n" if description else ""
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"{frontmatter}{body}\n", encoding="utf-8")
    return skill_md


@pytest.fixture
def skills_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point both skill lookups at empty tmp dirs (workspace + builtin)."""
    workspace = tmp_path / "workspace-skills"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    builtin.mkdir()
    monkeypatch.setattr(dsk, "get_agent_skills_dir", lambda: workspace)
    monkeypatch.setattr(dsk, "get_builtin_skills_dir", lambda: builtin)
    return workspace


# ---------------------------------------------------------------------------
# Assistant body: installed skill wins, constant is the fallback
# ---------------------------------------------------------------------------


def test_assistant_body_prefers_the_installed_skill(skills_root: Path) -> None:
    _write_skill(skills_root, dsk.ASSISTANT_SKILL_NAME, description="助手技能", body="# 剪辑助手\n来自技能的说明")

    body = dsk.load_assistant_skill_body()

    assert body is not None
    assert "来自技能的说明" in body
    # frontmatter 不进正文——它只是技能列表要的元数据。
    assert "description:" not in body
    assert not body.startswith("---")


def test_assistant_body_falls_back_to_the_builtin_dir(skills_root: Path, tmp_path: Path) -> None:
    builtin = tmp_path / "builtin-skills"
    _write_skill(builtin, dsk.ASSISTANT_SKILL_NAME, body="内置目录里的说明")

    assert "内置目录里的说明" in (dsk.load_assistant_skill_body() or "")


def test_assistant_body_is_none_when_the_skill_is_missing(skills_root: Path) -> None:
    assert dsk.load_assistant_skill_body() is None


def test_assistant_skill_resolution_reports_the_installed_path(skills_root: Path) -> None:
    """`load_assistant_skill` 要连路径一起给出来——剪辑助手面板就是靠"读到了
    哪个文件"来判断默认技能是工作区副本、内置副本还是根本没装。"""
    skill_md = _write_skill(skills_root, dsk.ASSISTANT_SKILL_NAME, body="工作区副本")
    builtin = skills_root.parent / "builtin-skills"
    _write_skill(builtin, dsk.ASSISTANT_SKILL_NAME, body="内置副本")

    loaded = dsk.load_assistant_skill()

    assert loaded is not None
    path, body = loaded
    # 工作区优先于内置（与 skills.list 的口径一致）。
    assert path == skill_md
    assert body == "工作区副本"


def test_assistant_skill_resolution_is_none_when_missing(skills_root: Path) -> None:
    assert dsk.load_assistant_skill() is None


def _capture_director_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    """让 caplog 能收到 jiuwenswarm 的日志。

    项目的 ``jiuwenswarm`` logger 自建 handler 且 ``propagate=False``（见
    common/utils.py），而 caplog 只挂在 root 上——不临时打开传播就一个字都收不到。
    """
    monkeypatch.setattr(logging.getLogger("jiuwenswarm"), "propagate", True)
    caplog.set_level(level, logger="jiuwenswarm")


def test_prompt_assembly_logs_which_skill_file_was_used(
    skills_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """"改了技能不生效"第一个要看的就是这一轮到底读的哪个文件，所以加载来源要有
    日志。只记路径和字数，不记正文。"""
    skill_md = _write_skill(skills_root, dsk.ASSISTANT_SKILL_NAME, body="# 剪辑助手\n技能版说明")
    _capture_director_logs(monkeypatch, caplog, logging.INFO)

    _build_edit_chat_system_prompt([])

    assert str(skill_md) in caplog.text
    assert "技能版说明" not in caplog.text


def test_prompt_assembly_logs_the_fallback(
    skills_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _capture_director_logs(monkeypatch, caplog, logging.WARNING)

    _build_edit_chat_system_prompt([])

    assert dsk.ASSISTANT_SKILL_NAME in caplog.text


def test_prompt_uses_the_constant_when_the_skill_is_missing(skills_root: Path) -> None:
    """技能被删掉/读不出来时，剪辑助手仍然按内置说明工作。"""
    assert _build_edit_chat_system_prompt([]) == _EDIT_CHAT_SYSTEM_PROMPT


def test_prompt_uses_the_skill_body_when_installed(skills_root: Path) -> None:
    _write_skill(skills_root, dsk.ASSISTANT_SKILL_NAME, body="# 剪辑助手\n技能版说明")

    prompt = _build_edit_chat_system_prompt([])

    assert "技能版说明" in prompt
    assert prompt != _EDIT_CHAT_SYSTEM_PROMPT


def test_unreadable_assistant_skill_falls_back(skills_root: Path) -> None:
    """文件存在但读不出来（比如编码坏了）时也要回退，而不是抛异常。"""
    skill_dir = skills_root / dsk.ASSISTANT_SKILL_NAME
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")

    assert _build_edit_chat_system_prompt([]) == _EDIT_CHAT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Selected skills appended to the prompt
# ---------------------------------------------------------------------------


def test_selected_skills_are_appended_to_the_prompt(skills_root: Path) -> None:
    _write_skill(skills_root, "promo-video", description="商品宣传片流程", body="第一步：拍产品图")

    prompt = _build_edit_chat_system_prompt(["promo-video"])

    assert _EDIT_CHAT_SYSTEM_PROMPT in prompt  # 基础说明保持不变，追加在后面
    assert "用户为这一轮选中的技能" in prompt
    assert "promo-video" in prompt
    assert "商品宣传片流程" in prompt
    assert "第一步：拍产品图" in prompt


def test_unknown_skill_names_are_skipped(skills_root: Path) -> None:
    """前端列表可能过期——一个找不到的名字不该把整轮对话搞失败。"""
    _write_skill(skills_root, "known-skill", body="已知技能正文")

    prompt = _build_edit_chat_system_prompt(["gone-skill", "known-skill"])

    assert "已知技能正文" in prompt
    assert "gone-skill" not in prompt


def test_assistant_skill_is_not_repeated_as_a_selection(skills_root: Path) -> None:
    """用户把助手自己的技能也勾上时不能重复注入——它已经是基础说明了。"""
    _write_skill(skills_root, dsk.ASSISTANT_SKILL_NAME, body="助手说明只应出现一次")

    sections = dsk.load_skill_sections([dsk.ASSISTANT_SKILL_NAME])

    assert sections == []
    assert _build_edit_chat_system_prompt([dsk.ASSISTANT_SKILL_NAME]).count("助手说明只应出现一次") == 1


def test_duplicate_selections_are_deduped(skills_root: Path) -> None:
    _write_skill(skills_root, "dup-skill", body="只注入一次")

    sections = dsk.load_skill_sections(["dup-skill", "dup-skill", " dup-skill "])

    assert len(sections) == 1


def test_long_skill_body_is_truncated(skills_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dsk, "_MAX_SKILL_BODY_CHARS", 50)
    _write_skill(skills_root, "huge-skill", body="X" * 400)

    sections = dsk.load_skill_sections(["huge-skill"])

    assert len(sections) == 1
    assert "截断" in sections[0]["body"]
    assert len(sections[0]["body"]) < 200


def test_skill_without_description_still_loads(skills_root: Path) -> None:
    _write_skill(skills_root, "nodesc", description="", body="没有 description 的正文")

    sections = dsk.load_skill_sections(["nodesc"])

    assert len(sections) == 1
    assert sections[0]["description"] == ""
    assert "没有 description 的正文" in _build_edit_chat_system_prompt(["nodesc"])


# ---------------------------------------------------------------------------
# Name handling
# ---------------------------------------------------------------------------


def test_skill_names_cannot_escape_the_skills_dir(skills_root: Path, tmp_path: Path) -> None:
    """技能名来自前端勾选，不能靠它读到技能目录之外的文件。"""
    outside = tmp_path / "secret.md"
    outside.write_text("机密", encoding="utf-8")

    assert dsk.load_skill_sections(["../../secret"]) == []
    assert dsk.load_skill_sections(["/etc/passwd"]) == []
    assert dsk.load_skill_sections([".."]) == []
    assert dsk.load_assistant_skill_body() is None


def test_skill_without_md_file_is_skipped(skills_root: Path) -> None:
    (skills_root / "empty-skill").mkdir()

    assert dsk.load_skill_sections(["empty-skill"]) == []


def test_blank_names_are_ignored(skills_root: Path) -> None:
    assert dsk.load_skill_sections(["", "   ", None]) == []  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Handler wiring: selected skills reach the system prompt of the turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_puts_selected_skills_in_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch, skills_root: Path, tmp_path: Path
) -> None:
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds
    from jiuwenswarm.server.runtime.director.director_manager import DirectorManager

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)
    _write_skill(skills_root, "promo-video", description="宣传片流程", body="先拍产品图，再剪。")

    seen: list[list[dict]] = []

    async def fake_completion(messages, tools=None):  # noqa: ANN001, ARG001
        seen.append(messages)
        return {"content": "好的，我按宣传片流程来。"}

    monkeypatch.setattr(dm, "call_edit_chat_completion", fake_completion)

    manager = DirectorManager()
    created = await manager.handle_director_projects_create({"name": "技能测试"})
    project_id = created["project"]["project_id"]

    result = await manager.handle_director_edit_chat_send(
        {"project_id": project_id, "text": "做个广告", "skill_names": ["promo-video"]}
    )

    system_prompt = seen[0][0]["content"]
    assert seen[0][0]["role"] == "system"
    assert "promo-video" in system_prompt
    assert "先拍产品图，再剪。" in system_prompt
    assert "用户为这一轮选中的技能" in system_prompt
    # 用户消息仍然排在系统提示词之后，历史没有被技能段挤掉。
    assert seen[0][-1]["role"] == "user"
    assert result["project"]["edit_chat_messages"][-1]["content"] == "好的，我按宣传片流程来。"


@pytest.mark.asyncio
async def test_handler_without_skill_names_uses_the_plain_base_prompt(
    monkeypatch: pytest.MonkeyPatch, skills_root: Path, tmp_path: Path
) -> None:
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds
    from jiuwenswarm.server.runtime.director.director_manager import DirectorManager

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)

    seen: list[list[dict]] = []

    async def fake_completion(messages, tools=None):  # noqa: ANN001, ARG001
        seen.append(messages)
        return {"content": "收到"}

    monkeypatch.setattr(dm, "call_edit_chat_completion", fake_completion)

    manager = DirectorManager()
    created = await manager.handle_director_projects_create({"name": "无技能"})
    await manager.handle_director_edit_chat_send(
        {"project_id": created["project"]["project_id"], "text": "你好"}
    )

    assert seen[0][0]["content"] == _EDIT_CHAT_SYSTEM_PROMPT
    assert "用户为这一轮选中的技能" not in seen[0][0]["content"]


@pytest.mark.asyncio
async def test_handler_tolerates_a_stale_skill_name(
    monkeypatch: pytest.MonkeyPatch, skills_root: Path, tmp_path: Path
) -> None:
    """勾选的技能在发消息之前被删掉了——这一轮照常发出去，只是没有那段技能说明。"""
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds
    from jiuwenswarm.server.runtime.director.director_manager import DirectorManager

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)

    seen: list[list[dict]] = []

    async def fake_completion(messages, tools=None):  # noqa: ANN001, ARG001
        seen.append(messages)
        return {"content": "收到"}

    monkeypatch.setattr(dm, "call_edit_chat_completion", fake_completion)

    manager = DirectorManager()
    created = await manager.handle_director_projects_create({"name": "过期技能"})
    await manager.handle_director_edit_chat_send(
        {"project_id": created["project"]["project_id"], "text": "继续", "skill_names": ["deleted-skill"]}
    )

    assert seen[0][0]["content"] == _EDIT_CHAT_SYSTEM_PROMPT
