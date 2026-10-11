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

import json
import logging
import re
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import (
    get_agent_root_dir,
    get_agent_skills_dir,
    get_builtin_skills_dir,
)

logger = logging.getLogger(__name__)

# 剪辑助手的**基础技能**（正文即助手的系统提示词）。它是可切换的：出厂默认是
# director-edit-assistant，用户可以把它换成 ads-codirector（广告片共创），两者
# 互为替身——谁被选为默认，谁就是这一轮的基础说明，另一个仍然可以像普通技能一样
# 勾选、作为额外工作流追加在后面。切换选择落在 ``assistant_skill.json``。
ASSISTANT_SKILL_NAME = "director-edit-assistant"
ASSISTANT_SKILL_CANDIDATES: tuple[str, ...] = (
    "director-edit-assistant",
    "ads-codirector",
)

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
    """当前默认技能（基础说明）的 ``(文件路径, 正文)``；技能不存在/读不出来时返回
    None（调用方回退到内置常量说明）。

    返回路径是为了让调用方能记一条"这一轮的基础说明到底是从哪个技能文件读的"
    日志——剪辑助手加载的是哪一份技能，是排查"改了技能不生效"时第一个要看的东西。
    """
    path = _skill_md_path(resolve_default_skill_name())
    if path is None:
        return None
    loaded = _read_skill_markdown(path)
    if loaded is None:
        return None
    return path, loaded[1]


def load_assistant_skill_body() -> str | None:
    """当前默认技能的正文；技能不存在/读不出来时返回 None。"""
    loaded = load_assistant_skill()
    return loaded[1] if loaded else None


# --------------------------------------------------------------------------
# 默认（基础）技能的切换
#
# 候选就这两个（见 ASSISTANT_SKILL_CANDIDATES）：一个是剪辑助手本身的说明，一个是
# 广告片共创工作流。选择存在 <agent_root>/director/assistant_skill.json —— 与项目
# 数据同一个目录，跟着工作区走，切换后下一轮对话立刻生效（不需要重启服务：每轮都
# 重新读技能正文）。
# --------------------------------------------------------------------------


def _config_file() -> Path:
    return get_agent_root_dir() / "director" / "assistant_skill.json"


def _read_selected_name() -> str:
    """配置里记的默认技能名；文件缺失/损坏/字段不对都当作"没配过"。"""
    path = _config_file()
    try:
        if not path.is_file():
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("[director_skills] 无法读取默认技能配置: %s", path, exc_info=True)
        return ""
    if not isinstance(raw, dict):
        return ""
    name = str(raw.get("default_skill") or "").strip()
    return name


def skill_candidates() -> list[dict[str, Any]]:
    """候选基础技能 + 每个是否真的能读到技能文件。

    读不到（工作区、内置目录里都没有）的候选**不能**被选为默认——选它只会让助手
    掉回内置常量说明，比不选还差。所以 ``available`` 决定前端是否给出切换入口。
    """
    selected = resolve_default_skill_name()
    out: list[dict[str, Any]] = []
    for name in ASSISTANT_SKILL_CANDIDATES:
        path = _skill_md_path(name)
        loaded = _read_skill_markdown(path) if path else None
        out.append(
            {
                "name": name,
                "available": loaded is not None,
                "is_default": name == selected,
                "description": loaded[0] if loaded else "",
                "path": str(path) if path else "",
            }
        )
    return out


def resolve_default_skill_name() -> str:
    """当前生效的默认（基础）技能名。

    三重校验：配置里写过、在候选名单里、并且真的能读到技能文件。任一不满足就退回
    出厂默认 ``ASSISTANT_SKILL_NAME``——配置文件被手动改坏、或者选了之后把技能删了，
    助手都要还能按出厂说明工作。
    """
    name = _read_selected_name()
    if not name or name == ASSISTANT_SKILL_NAME:
        return ASSISTANT_SKILL_NAME
    if name not in ASSISTANT_SKILL_CANDIDATES:
        logger.info("[director_skills] 忽略不在候选名单里的默认技能: %s", name)
        return ASSISTANT_SKILL_NAME
    if _skill_md_path(name) is None:
        logger.warning("[director_skills] 默认技能 %s 已找不到，退回 %s", name, ASSISTANT_SKILL_NAME)
        return ASSISTANT_SKILL_NAME
    return name


def get_assistant_skill_config() -> dict[str, Any]:
    """给前端的默认技能状态：当前生效的那一个 + 可切换的候选。"""
    return {
        "default_skill": resolve_default_skill_name(),
        "factory_default": ASSISTANT_SKILL_NAME,
        "candidates": skill_candidates(),
        "config_path": str(_config_file()),
    }


def set_assistant_skill_config(name: str) -> dict[str, Any]:
    """切换默认技能并落盘，返回切换后的状态。

    只接受候选名单里、且真的读得到的技能名——前端理论上只给这两个选项，但接口不能
    靠"前端会守规矩"来保护：写进配置的是下一轮的**系统提示词来源**，一个乱填的名字
    会让助手掉回内置说明。
    """
    safe = str(name or "").strip()
    if safe not in ASSISTANT_SKILL_CANDIDATES:
        raise ValueError(f"不支持的基础技能: {name!r}")
    if _skill_md_path(safe) is None:
        raise ValueError(f"技能 {safe} 未安装或读不到正文，无法设为默认")

    path = _config_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"default_skill": safe}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - 磁盘不可写时明确报错，不静默吞掉
        raise ValueError(f"默认技能配置写入失败: {exc}") from exc

    logger.info("[director] 剪辑助手默认技能已切换为 %s", safe)
    return get_assistant_skill_config()



def load_skill_sections(
    names: list[str], *, skip: set[str] | None = None
) -> list[dict[str, str]]:
    """把用户勾选的技能读成 ``{name, description, body}`` 列表。

    找不到的技能会被跳过（前端列表可能已经过期，比如技能刚被删掉）——不应该
    因为一个过期的名字整轮对话就失败。当前**生效的默认技能**也跳过（由调用方经
    ``skip`` 传入，缺省时自己解析）：它的说明已经是系统提示词本身，再追加一遍只会
    重复。注意只跳过生效的那一个，另一个候选仍是正常的可勾选技能。
    """
    if skip is None:
        skip = {resolve_default_skill_name()}
    sections: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name in seen or name in skip:
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
