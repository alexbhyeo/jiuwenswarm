# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""剪辑助手（导演模式 · 剪辑）的技能读取。

剪辑助手本身就是一个已安装的 Skill（``director-edit-assistant``）：面板的
工作流说明不再只是一个写死在 director_manager.py 里的常量，而是从技能目录里
读出来的 SKILL.md 正文——用户可以在「技能」面板里查看/编辑这份说明，改完下
一轮对话就生效，不需要改代码重新打包。常量 ``_EDIT_CHAT_SYSTEM_PROMPT`` 保留
作为兜底：技能没装、被删、或读不出来时仍然按原来的说明工作。

同时这里也负责把用户在剪辑助手里勾选的**已安装技能**读出来，交给
director_manager 追加到系统提示词里——用户选中的技能就是这一轮要遵循的
额外工作流。

技能查找范围与「技能」面板的口径一致（见 skill_manager 的 _scan_local_skills /
_scan_builtin_skills）：先看工作区 ``<workspace>/skills/``，再看内置
``<package>/resources/agent/workspace/skills/``。只读 SKILL.md / 目录下第一个
``*.md``（与 _try_find_skill_file 一致），不做 frontmatter 之外的解析。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jiuwenswarm.common.utils import get_agent_skills_dir, get_builtin_skills_dir

logger = logging.getLogger(__name__)

# 剪辑助手自己的技能名（目录名即技能名，见 skill_manager._scan_one_skill_dir）。
ASSISTANT_SKILL_NAME = "director-edit-assistant"

# 注入系统提示词的技能正文上限。技能正文是给人看的文档，个别技能可能很长；
# 一整段塞进提示词会挤掉真正的对话上下文，这里截断并明确标出被截断。
_MAX_SKILL_BODY_CHARS = 12000

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)


def _skill_search_dirs() -> list[Path]:
    """技能目录，按优先级排列（工作区覆盖内置）。"""
    return [get_agent_skills_dir(), get_builtin_skills_dir()]


def _skill_md_path(name: str) -> Path | None:
    """按名字找一个技能的 Markdown 文件；找不到返回 None。

    名字只取最后一段，不接受 ``..`` / 路径分隔符——技能名来自前端勾选，
    不能让一个构造出来的名字把读取范围伸出技能目录之外。
    """
    safe = str(name or "").strip()
    if not safe or safe != Path(safe).name or safe in {".", ".."}:
        return None
    for root in _skill_search_dirs():
        try:
            if not root.is_dir():
                continue
        except OSError:  # pragma: no cover - 目录不可访问时按"没有这个技能"处理
            continue
        skill_dir = root / safe
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md
        # 与 skill_manager._try_find_skill_file 一致：没有 SKILL.md 时退回到
        # 目录下第一个 Markdown 文件。
        try:
            candidates = sorted(skill_dir.glob("*.md"))
        except OSError:  # pragma: no cover
            candidates = []
        if candidates:
            return candidates[0]
    return None


def _read_skill_markdown(path: Path) -> tuple[str, str] | None:
    """读一个技能文件，返回 (description, body)。

    description 从 YAML frontmatter 的 ``description`` 行取（没有就算空），
    body 是去掉 frontmatter 之后的正文——注入提示词只想要正文，frontmatter
    里的 name/description_cn 之类是给技能列表看的元数据。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("[director_skills] 无法读取技能文件: %s", path, exc_info=True)
        return None

    description = ""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if match:
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if key.strip() == "description" and separator:
                description = value.strip().strip("'\"")
                break

    body = _FRONTMATTER_RE.sub("", text, count=1).strip()
    if not body:
        return None
    return description, body


def load_assistant_skill() -> tuple[Path, str] | None:
    """剪辑助手自身技能的 ``(文件路径, 正文)``；技能不存在/读不出来时返回 None
    （调用方回退到内置常量说明）。

    返回路径是为了让调用方能记一条"这一轮的基础说明到底是从哪个技能文件读的"
    日志——剪辑助手加载的是哪一份技能，是排查"改了技能不生效"时第一个要看的东西。
    """
    path = _skill_md_path(ASSISTANT_SKILL_NAME)
    if path is None:
        return None
    loaded = _read_skill_markdown(path)
    if loaded is None:
        return None
    return path, loaded[1]


def load_assistant_skill_body() -> str | None:
    """剪辑助手自身的技能正文；技能不存在/读不出来时返回 None。"""
    loaded = load_assistant_skill()
    return loaded[1] if loaded else None


def load_skill_sections(names: list[str]) -> list[dict[str, str]]:
    """把用户勾选的技能读成 ``{name, description, body}`` 列表。

    找不到的技能会被跳过（前端列表可能已经过期，比如技能刚被删掉）——不应该
    因为一个过期的名字整轮对话就失败。剪辑助手自己的技能也跳过：它的说明已经
    是系统提示词本身，再追加一遍只会重复。
    """
    sections: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name in seen or name == ASSISTANT_SKILL_NAME:
            continue
        seen.add(name)
        path = _skill_md_path(name)
        if path is None:
            logger.info("[director_skills] 忽略找不到的技能: %s", name)
            continue
        loaded = _read_skill_markdown(path)
        if loaded is None:
            continue
        description, body = loaded
        if len(body) > _MAX_SKILL_BODY_CHARS:
            body = body[:_MAX_SKILL_BODY_CHARS] + "\n\n（技能内容过长，以上为截断部分）"
        sections.append({"name": name, "description": description, "body": body})
    return sections
