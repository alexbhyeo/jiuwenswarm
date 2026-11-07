# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""游戏助手：一轮对话 + 变更集.

设计见 D-20261107-1637：助手只通过工具改工程，一轮结束后把「本轮改了什么」
整理成变更集交给前端预览/应用/回退（不变量 I4）。

工具集刻意保持在工程文件层面（列目录/读/写）而不是直接给模型 CRUD 接口：
工程文件是唯一事实来源，变更集就是从文件快照前后对比出来的，谁也绕不过去。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from jiuwenswarm.agents.harness.common.tools.edit_chat_tools import (
    call_edit_chat_completion,
)

logger = logging.getLogger(__name__)

EDITABLE_SUFFIXES = {".gd", ".cs", ".tscn", ".tres", ".godot", ".json", ".cfg", ".md", ".txt"}
_MAX_READ_BYTES = 256 * 1024
_MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """你是 WorkSwarm 游戏模式（游戏工坊）的开发助手。

项目是一个真实的 Godot 4 工程，根目录下：
- project.godot：工程配置，run/main_scene 指向主场景
- scenes/*.tscn：场景（Godot 场景文本格式）
- scripts/*.gd：GDScript
- assets/：图片等资源

你的任务：根据用户的要求改进这个游戏。你必须用工具修改文件，不要只在回复里描述。

规则：
1. 动手前先 list_files / read_file 看清现状，不要凭空重写整个文件。
2. 用 write_file 整文件写入；写完保持 GDScript 缩进为 **Tab**（Godot 的约定）。
3. 需要新美术素材时用 generate_asset（type 取 character / prop / scene），
   它会把图生成到 assets/ 并在下一轮可用；不要伪造图片路径。
4. 需要新建或修复工程骨架时用 build_2d。
5. 改动尽量小且聚焦；一次只解决用户提出的事。
6. 最后用中文简要说明改了什么、玩家能观察到什么变化。"""

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出工程内的可编辑文件路径。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个工程文件的完整内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "工程相对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "整文件写入（不存在则创建）。GDScript 使用 Tab 缩进。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_asset",
            "description": "生成一张游戏美术素材并放入 assets/。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["character", "prop", "scene"]},
                    "prompt": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["type", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_2d",
            "description": "生成/修复 2D 主城工程骨架（幂等）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _editable(path: Path) -> bool:
    return path.suffix.lower() in EDITABLE_SUFFIXES


def snapshot(root: Path) -> dict[str, str]:
    """把工程内可编辑文件读成 {相对路径: 内容}，用于前后对比."""
    snap: dict[str, str] = {}
    if not root.is_dir():
        return snap
    for path in root.rglob("*"):
        if not path.is_file() or not _editable(path):
            continue
        try:
            if path.stat().st_size > _MAX_READ_BYTES:
                continue
            snap[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return snap


def diff(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    """产出变更集：added / modified / removed，带前后内容便于预览与回退."""
    changes: list[dict[str, Any]] = []
    for path in sorted(after):
        if path not in before:
            changes.append({"path": path, "status": "added", "before": None, "after": after[path]})
        elif before[path] != after[path]:
            changes.append({
                "path": path, "status": "modified",
                "before": before[path], "after": after[path],
            })
    for path in sorted(before):
        if path not in after:
            changes.append({"path": path, "status": "removed", "before": before[path], "after": None})
    return changes


@dataclass
class GameChatTools:
    """助手可用的回调，由 GameManager 注入（避免循环依赖）."""

    root: Path
    generate_asset: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    build_2d: Callable[[], Awaitable[dict[str, Any]]]


class GameChatError(RuntimeError):
    code = "GAME_CHAT_FAILED"


async def _dispatch(name: str, args: dict[str, Any], tools: GameChatTools) -> dict[str, Any]:
    if name == "list_files":
        return {"files": sorted(snapshot(tools.root))}
    if name == "read_file":
        rel = str(args.get("path") or "").strip()
        target = (tools.root / rel).resolve()
        if tools.root.resolve() not in target.parents and target != tools.root.resolve():
            return {"error": "路径必须位于工程目录内"}
        if not target.is_file():
            return {"error": f"文件不存在: {rel}"}
        return {"path": rel, "content": target.read_text(encoding="utf-8", errors="replace")}
    if name == "write_file":
        rel = str(args.get("path") or "").strip()
        content = args.get("content")
        if not rel or not isinstance(content, str):
            return {"error": "缺少 path 或 content"}
        target = (tools.root / rel).resolve()
        if tools.root.resolve() not in target.parents:
            return {"error": "路径必须位于工程目录内"}
        if not _editable(target):
            return {"error": f"不支持编辑该类型文件: {target.suffix}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": rel, "bytes": len(content.encode("utf-8"))}
    if name == "generate_asset":
        return await tools.generate_asset(args)
    if name == "build_2d":
        return await tools.build_2d()
    return {"error": f"未知工具: {name}"}


async def run_turn(
    *,
    root: Path,
    history: list[dict[str, Any]],
    user_text: str,
    tools: GameChatTools,
) -> dict[str, Any]:
    """跑一轮助手对话，返回 {reply, messages, changes}."""
    before = snapshot(root)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    reply = ""
    for _ in range(_MAX_TOOL_ITERATIONS):
        message = await call_edit_chat_completion(messages, tools=TOOLS_SCHEMA)
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or None,
        })
        if not tool_calls:
            reply = content
            break
        for call in tool_calls:
            fn = (call.get("function") or {})
            name = str(fn.get("name") or "")
            try:
                import json

                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            result = await _dispatch(name, args if isinstance(args, dict) else {}, tools)
            import json as _json

            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or "",
                "content": _json.dumps(result, ensure_ascii=False)[:8000],
            })
    else:
        reply = "（本轮工具调用次数达到上限，已停下；可以继续让我接着改。）"

    changes = diff(before, snapshot(root))
    logger.info("[game] 助手一轮完成：改动 %d 个文件", len(changes))
    return {"reply": reply, "messages": messages, "changes": changes}


def revert(root: Path, changes: list[dict[str, Any]]) -> list[str]:
    """按变更集回退：added 删掉，modified 写回 before，removed 还原."""
    restored: list[str] = []
    for change in changes:
        rel = str(change.get("path") or "")
        if not rel:
            continue
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            continue
        status = change.get("status")
        if status == "added":
            if target.is_file():
                target.unlink()
                restored.append(rel)
        elif status in ("modified", "removed"):
            before = change.get("before")
            if isinstance(before, str):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(before, encoding="utf-8")
                restored.append(rel)
    return restored
