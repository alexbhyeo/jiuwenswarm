# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""实验室助手：游戏模式「实验室」里的对话智能体。

与导演模式的剪辑助手（``director_manager`` 的 edit chat 一条线）同构：一轮
对话里模型可以发起若干次工具调用，每次工具调用真实执行一次生成/处理任务，
结果作为 ``role=tool`` 消息喂回模型，直到模型给出不再包含工具调用的最终回复
或触发迭代上限。

差别只有一处，也是本模块存在的理由：**工具集只有游戏模式的处理卡片**
（素材分解 / 2D 转 3D / 网格补全 / 拓扑重构），不暴露导演模式的
generate_design_image / generate_shot_video / generate_video_bridge。游戏模式
的"生成"（角色/世界/视频）属于创作页，不在实验室助手的权限里。

项目里已有的素材不走工具暴露，而是作为一条 system 消息注入（
:func:`build_asset_context`）——这样工具表里的四项就是助手能做的全部动作，
不会出现"读素材"这种第五个工具把权限边界糊掉。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jiuwenswarm.agents.harness.common.tools.edit_chat_tools import (
    call_edit_chat_completion,
)

from jiuwenswarm.server.runtime.game.game_store import GameAsset, GameProject

logger = logging.getLogger(__name__)


def _rpc_error_cls() -> type[Exception]:
    """延迟取 GameRpcError：game_manager 会 import 本模块，顶层互相 import 会成环。"""
    from jiuwenswarm.server.runtime.game.game_manager import GameRpcError

    return GameRpcError


def _rpc_error(code: str, message: str) -> Exception:
    return _rpc_error_cls()(code, message)

# 一轮里最多几轮工具调用（与剪辑助手的 _EDIT_CHAT_MAX_TOOL_ITERATIONS 同量级）。
MAX_TOOL_ITERATIONS = 6
# 单个卡片任务的最长等待（分解/转 3D/补全/重构都是外部异步任务，分钟级）。
DEFAULT_TOOL_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 3.0

LAB_CHAT_SYSTEM_PROMPT = """你是 WorkSwarm 游戏工坊「实验室」里的助手。

实验室里有十种处理卡片，你的全部能力就是帮用户调用其中四种：文生图、图生图、纹理贴图、骨骼检查、自动绑定、动画重定向由用户在画布上手动操作，不在你的工具表里。你**只能**调用这四种卡片：
- 素材分解 decompose_asset：把一张已就绪的「角色」或「世界」图片拆成多个 3D 部件。
- 2D 转 3D image_to_model：把一张已就绪的「角色」或「世界」图片转成一个完整 3D 模型。
- 网格补全 mesh_complete：修复一个已就绪的 3D 模型/部件上的破洞，产出一个新模型。
- 拓扑重构 mesh_decimate：对已就绪的 3D 模型/部件减面重拓扑，产出一个新模型。

规则：
1. 你**不能**生成图片或视频（文生图/图生图卡片由用户手动操作，你没有对应工具），也不能修改工程代码。用户要新素材时，告诉他去画布上点「文生图/图生图」，或去「创作」页生成。
2. 素材用**名称**引用（见下方"当前项目素材"）。名称有歧义时先问用户，不要猜。
3. 一次只做用户要求的处理；不确定就先问，不要顺手把整条链路都跑完。
4. 卡片是异步任务，工具返回时已经等到了结果（成功/失败），照实转述，不要编造产出名称。
5. 最后用中文简要说明：调用了哪张卡片、产出了什么、下一步可以做什么。"""


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


LAB_TOOLS_SCHEMA: list[dict[str, Any]] = [
    _tool(
        "decompose_asset",
        "素材分解卡片：把一张已就绪的「角色」或「世界」图片拆成多个 3D 部件素材。",
        {
            "asset_name": {"type": "string", "description": "源图片素材的名称（见当前项目素材列表）"},
            "hint": {"type": "string", "description": "可选：想怎么拆，例如「按头/身体/武器拆」"},
            "granularity": {
                "type": "string",
                "enum": ["coarse", "medium", "fine"],
                "description": "拆分粒度，默认 medium",
            },
        },
        ["asset_name"],
    ),
    _tool(
        "image_to_model",
        "2D 转 3D 卡片：把一张已就绪的「角色」或「世界」图片转成一个完整 3D 模型素材。",
        {"asset_name": {"type": "string", "description": "源图片素材的名称"}},
        ["asset_name"],
    ),
    _tool(
        "mesh_complete",
        "网格补全卡片：修复一个已就绪的 3D 模型/部件上的破洞，产出一个新模型素材。",
        {"asset_name": {"type": "string", "description": "源 3D 模型或部件素材的名称"}},
        ["asset_name"],
    ),
    _tool(
        "mesh_decimate",
        "拓扑重构卡片：对一个已就绪的 3D 模型/部件做减面重拓扑，产出一个新模型素材。",
        {"asset_name": {"type": "string", "description": "源 3D 模型或部件素材的名称"}},
        ["asset_name"],
    ),
]

# 工具名 → 是否属于「处理卡片」。测试与调用方用它校验权限边界。
LAB_TOOL_NAMES = frozenset(item["function"]["name"] for item in LAB_TOOLS_SCHEMA)


@dataclass
class LabChatDeps:
    """一轮对话的外部依赖；测试里替换掉真实生成与等待。"""

    complete: Callable[..., Awaitable[dict[str, Any]]] = call_edit_chat_completion
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS
    tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    max_iterations: int = MAX_TOOL_ITERATIONS


def resolve_asset_by_name(project: GameProject, name: str) -> GameAsset | None:
    """按名称找素材：先精确，再忽略大小写/空白，最后唯一前缀匹配。

    只返回 ``ready`` 且有文件的素材——处理卡片的输入本来就要求就绪。
    """
    wanted = str(name or "").strip().lstrip("@")
    if not wanted:
        return None
    ready = [a for a in project.assets if a.status == "ready"]
    for asset in ready:
        if a_name := (asset.name or "").strip():
            if a_name == wanted:
                return asset
    lowered = wanted.lower()
    for asset in ready:
        if (asset.name or "").strip().lower() == lowered:
            return asset
    prefix_hits = [a for a in ready if (a.name or "").strip().lower().startswith(lowered)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    return None


def build_asset_context(project: GameProject) -> str:
    """把项目里可引用的素材列成一条 system 消息。

    助手只有四张处理卡片，没有"读素材"工具，所以名称必须靠这条上下文给出，
    否则模型只能凭空编名字。
    """
    groups: dict[str, list[str]] = {}
    for asset in project.assets:
        if asset.status != "ready" or not asset.name:
            continue
        groups.setdefault(asset.type, []).append(asset.name)
    if not groups:
        return (
            "当前项目还没有可用素材。实验室的四张处理卡片都需要现成素材作输入，"
            "请让用户先去「创作」页生成角色/世界图片。"
        )
    lines = ["当前项目素材（用名称引用）："]
    label = {
        "character": "角色图片",
        "world": "世界图片",
        "image": "图片",
        "model": "3D 模型",
        "model_part": "3D 部件",
        "video": "视频",
    }
    for asset_type, names in sorted(groups.items()):
        lines.append(f"- {label.get(asset_type, asset_type)}：{'、'.join(names)}")
    return "\n".join(lines)


class GameLabChat:
    """实验室助手的一轮对话执行器。"""

    def __init__(self, deps: LabChatDeps | None = None) -> None:
        self._deps = deps or LabChatDeps()

    async def run_turn(self, manager: Any, project: GameProject, text: str) -> dict[str, Any]:
        """跑一轮：写用户消息 → 工具循环 → 写助手消息 → 返回项目快照。

        ``manager`` 是 :class:`GameManager`（用它的卡片 handler 执行工具），
        测试可传入等价的替身。
        """
        store = manager._store  # noqa: SLF001 - 与 game_manager 同一模块族的内部协作
        project = self._append_message(store, project.project_id, {"role": "user", "content": text})

        history = [
            {"role": str(m.get("role") or ""), "content": str(m.get("content") or "")}
            for m in project.lab_chat_messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": LAB_CHAT_SYSTEM_PROMPT},
            {"role": "system", "content": build_asset_context(project)},
            *history,
        ]

        applied: list[str] = []
        final_text = ""
        for _ in range(max(1, self._deps.max_iterations)):
            assistant = await self._deps.complete(messages, tools=LAB_TOOLS_SCHEMA)
            content = str(assistant.get("content") or "")
            if content.startswith("[ERROR]:"):
                raise _rpc_error("GENERATION_FAILED", content)

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                final_text = content
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for call in tool_calls:
                result_text, note = await self._run_tool(manager, project, call)
                if note:
                    applied.append(note)
                    # 同一轮里后续调用要能看到刚产出的素材（例如先分解再补全某个部件）。
                    refreshed = store.get_project(project.project_id)
                    if refreshed is not None:
                        project = refreshed
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": result_text}
                )
        else:
            final_text = final_text or "这一轮已经调用了几次处理卡片，先停一下，可以告诉我结果是否满意。"

        project = self._append_message(
            store,
            project.project_id,
            {"role": "assistant", "content": final_text, "applied": applied},
        )
        return {"project": project.to_dict(), "reply": final_text, "applied": applied}

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------
    async def _run_tool(self, manager: Any, project: GameProject, call: dict[str, Any]) -> tuple[str, str]:
        """执行一次卡片调用，返回 (喂回模型的文本, 成功产出说明或空串)。"""
        fn = call.get("function") or {}
        name = str(fn.get("name") or "").strip()
        raw_args = fn.get("arguments")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        if isinstance(raw_args, str) and raw_args.strip():
            import json

            try:
                parsed = json.loads(raw_args)
            except ValueError:
                return f"参数不是合法 JSON：{raw_args[:120]}", ""
            if isinstance(parsed, dict):
                args = parsed

        if name not in LAB_TOOL_NAMES:
            return f"不支持的工具 {name}：实验室助手只能调用游戏模式的处理卡片。", ""

        asset_name = str(args.get("asset_name") or "").strip()
        source = resolve_asset_by_name(project, asset_name)
        if source is None:
            return (
                f"找不到名为「{asset_name}」的可用素材。\n{build_asset_context(project)}",
                "",
            )

        try:
            if name == "decompose_asset":
                return await self._run_decompose(manager, project, source, args)
            if name == "image_to_model":
                return await self._run_to_model(manager, project, source)
            if name == "mesh_complete":
                return await self._run_mesh(manager, project, source, "mesh_complete")
            return await self._run_mesh(manager, project, source, "mesh_decimate")
        except Exception as exc:  # noqa: BLE001 - 只有卡片错误转成喂回模型的文本，其余照抛
            if not isinstance(exc, _rpc_error_cls()):
                raise
            return f"卡片执行失败（{getattr(exc, 'code', 'TASK_FAILED')}）：{exc}", ""

    async def _run_decompose(
        self, manager: Any, project: GameProject, source: GameAsset, args: dict[str, Any]
    ) -> tuple[str, str]:
        submitted = await manager.handle_game_assets_decompose(
            {
                "project_id": project.project_id,
                "source_asset_id": source.asset_id,
                "hint": str(args.get("hint") or "").strip(),
                "granularity": str(args.get("granularity") or "medium"),
            }
        )
        placeholder_id = str(submitted["placeholder_asset_id"])
        try:
            final = await self._poll(
                manager.handle_game_assets_decompose_check_status,
                {
                    "project_id": project.project_id,
                    "placeholder_asset_id": placeholder_id,
                    "job_id": submitted["job_id"],
                    "decompose_batch_id": submitted["decompose_batch_id"],
                },
            )
        except Exception:
            self._discard_placeholder(manager, project.project_id, placeholder_id)
            raise
        if final.get("status") != "success":
            return f"素材分解失败：{final.get('error') or '未知原因'}", ""
        names = [str(a.get("name") or a.get("asset_id")) for a in (final.get("assets") or [])]
        text = f"素材分解完成，产出 {len(names)} 个部件：{'、'.join(names) or '（无）'}"
        return text, text

    async def _run_to_model(self, manager: Any, project: GameProject, source: GameAsset) -> tuple[str, str]:
        submitted = await manager.handle_game_assets_to_model(
            {"project_id": project.project_id, "source_asset_id": source.asset_id}
        )
        placeholder_id = str(submitted["placeholder_asset_id"])
        try:
            final = await self._poll(
                manager.handle_game_assets_to_model_check_status,
                {
                    "project_id": project.project_id,
                    "placeholder_asset_id": placeholder_id,
                    "job_id": submitted["job_id"],
                },
            )
        except Exception:
            self._discard_placeholder(manager, project.project_id, placeholder_id)
            raise
        if final.get("status") != "success":
            return f"2D 转 3D 失败：{final.get('error') or '未知原因'}", ""
        name = str((final.get("asset") or {}).get("name") or "")
        text = f"2D 转 3D 完成，产出模型：{name}"
        return text, text

    async def _run_mesh(
        self, manager: Any, project: GameProject, source: GameAsset, kind: str
    ) -> tuple[str, str]:
        submit = (
            manager.handle_game_assets_mesh_complete
            if kind == "mesh_complete"
            else manager.handle_game_assets_mesh_decimate
        )
        check = (
            manager.handle_game_assets_mesh_complete_check_status
            if kind == "mesh_complete"
            else manager.handle_game_assets_mesh_decimate_check_status
        )
        label = "网格补全" if kind == "mesh_complete" else "拓扑重构"
        submitted = await submit({"project_id": project.project_id, "source_asset_id": source.asset_id})
        placeholder_id = str(submitted["placeholder_asset_id"])
        poll_params = {"project_id": project.project_id, "placeholder_asset_id": placeholder_id}
        # 拓扑重构的 check_status 要求显式 job_id（网格补全的从占位素材里读，
        # 多传不会报错）——两边都传，避免两条链路参数不一致。
        if submitted.get("job_id"):
            poll_params["job_id"] = str(submitted["job_id"])
        try:
            final = await self._poll(check, poll_params)
        except Exception:
            self._discard_placeholder(manager, project.project_id, placeholder_id)
            raise
        if final.get("status") != "success":
            return f"{label}失败：{final.get('error') or '未知原因'}", ""
        name = str((final.get("asset") or {}).get("name") or "")
        text = f"{label}完成，产出模型：{name}"
        return text, text

    async def _poll(self, check: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]], params: dict[str, Any]) -> dict[str, Any]:
        """反复调 check_status 直到不再是 pending；超时按失败处理。"""
        deadline = time.monotonic() + max(0.0, self._deps.tool_timeout)
        while True:
            result = await check(dict(params))
            status = str(result.get("status") or "")
            if status != "pending":
                return result
            if time.monotonic() >= deadline:
                return {"status": "failed", "error": f"等待超时（{int(self._deps.tool_timeout)} 秒）"}
            await self._deps.sleep(self._deps.poll_interval)

    @staticmethod
    def _discard_placeholder(manager: Any, project_id: str, placeholder_id: str) -> None:
        """卡片提交成功但轮询失败时，把占位素材清掉。

        不清的话素材库里会永远留一条「拓扑重构中…」的 pending 记录，用户和
        助手都会以为任务还在跑（实测踩过：参数不齐导致 check_status 直接抛错，
        两次重试留下两条 pending 占位）。
        """
        store = getattr(manager, "_store", None)
        if store is None or not placeholder_id:
            return
        try:
            store.delete_asset(project_id, placeholder_id)
        except Exception:  # noqa: BLE001 - 清理失败不掩盖原始错误
            logger.debug("清理占位素材失败: %s", placeholder_id, exc_info=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _append_message(store: Any, project_id: str, message: dict[str, Any]) -> GameProject:
        project = store.get_project(project_id)
        if project is None:
            raise _rpc_error("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")
        messages = list(project.lab_chat_messages)
        messages.append(message)
        return store.update_project(project_id, lab_chat_messages=messages)
