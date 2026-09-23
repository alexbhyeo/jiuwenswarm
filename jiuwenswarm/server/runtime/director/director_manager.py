# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DirectorManager - 导演模式 RPC 处理器.

无状态请求，在 interface.py 的 _handle_skills_request 同级挂载（在
_ensure_adapter 之前，见 interface.py 的"无状态请求"分派链），composer 的
"生成"直接调用 generate_video._func / generate_visual._func，不经过完整的
Agent/会话/LLM 工具调用回合 —— 这两个 @tool 函数的参数已经由前端表单完全
确定，走一次 LLM 回合只会增加延迟与不确定性，没有任何收益。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.harness.common.tools.video_gen_tools import (
    check_video_status,
    generate_video,
    video_gen_configured,
    video_gen_enabled,
)
from jiuwenswarm.agents.harness.common.tools.visual_gen_tools import (
    generate_visual,
    visual_gen_configured,
    visual_gen_enabled,
)
from jiuwenswarm.agents.harness.common.tools.edit_chat_tools import (
    build_multimodal_user_message,
    call_edit_chat_completion,
    edit_chat_configured,
    edit_chat_enabled,
)
from jiuwenswarm.server.runtime.director.director_store import (
    DirectorAsset,
    DirectorProject,
    DirectorStore,
    EditChatMessage,
    get_project_assets_dir,
)

logger = logging.getLogger(__name__)

_SUPPORTED_MODES = ("video", "image", "character")

_RE_STILL_RUNNING_JOB_ID = re.compile(r"^Video job (\S+) submitted and still")
_RE_SAVED_TO = re.compile(r"Saved to:\s*(.+)")

_EDIT_CHAT_SYSTEM_PROMPT = (
    "你是「导演模式 · 剪辑」里的视频创作助手，帮用户把一个故事构想变成可以"
    "实际生成的分镜方案。用户可能会在对话里给你一段故事描述，并附上参考图片"
    "（角色/道具/场景的参照）。收到这样的创作请求时，按下面的流程工作：\n\n"
    "1. **故事板脚本**：先给出一份结构化的故事板脚本，包含画面比例"
    "（aspect ratio，没有特别要求时用 16:9）、画面风格、预估总时长、"
    "主要角色/道具列表（每个给出简短的外观/性格描述）、场景列表（每个给出"
    "简短的氛围/环境描述）。\n"
    "2. **生成设计图**：脚本定下来之后，为每一个主要角色/道具、以及每一个"
    "场景，调用 generate_design_image 工具真正生成一张设计图——角色/道具用"
    "kind=\"character\"，场景用 kind=\"scene\"。生成时用简洁、有辨识度的名称"
    "（如 \"Boss Orange\"、\"Mysterious Briefcase\"、\"Rainy Dark Alley\"），"
    "之后所有分镜描述里都要用 \"@名称\" 引用这些已经生成的设计图，而不是重新"
    "描述一遍外观——@名称 会被解析成真正的参考图片，保持角色/场景在不同镜头"
    "间的一致性。如果用户提供了参考图片，生成同一个角色/场景时把这些参考图片"
    "的内容体现在 description 里，帮助保持相似度。\n"
    "3. **分镜列表（Shot List）**：为每个场景写出具体的分镜，每个分镜包含"
    "画面描述（Visual Description，用 @名称 引用出场的角色/道具）、"
    "音效描述（Sound Effects Description），如果有台词也一并给出。\n\n"
    "分镜列表定下来之后，如果用户要求「生成分镜关键帧」或类似的下一步，"
    "为每个分镜调用 generate_design_image（kind=\"scene\"）生成一张关键帧"
    "画面——描述里综合这个分镜的画面描述，并用 @名称 引用画面中出现的"
    "角色/道具/场景设计图，保持视觉一致性；给关键帧起名时体现是第几场第几镜"
    "（如 \"Scene1 Shot1-1 Frame\"），方便后续引用。\n\n"
    "如果用户要求「用这些关键帧生成视频」或类似的下一步，为每个分镜调用"
    "generate_shot_video：first_frame_name 填这个分镜关键帧的名称，"
    "description 填这个镜头里发生的动作/运镜，duration_seconds 用分镜列表里"
    "该镜头的时长；如果分镜有明确的首尾两帧（比如同一分镜前后有两张关键帧），"
    "也可以填 last_frame_name 让生成的视频从首帧过渡到尾帧。\n\n"
    "完成每一步后，向用户确认结果是否满意，是否需要调整，或者可以继续"
    "下一步（脚本 → 设计图 → 分镜列表 → 分镜关键帧 → 分镜视频）。你现在能够"
    "真实生成角色/道具/场景设计图、分镜关键帧、以及基于关键帧的视频片段，"
    "但仍然不能执行真正的剪辑合成操作——无法把生成的多段视频剪切、拼接、"
    "配上完整音轨并导出成一条成片。如果用户要求你直接完成剪辑合成或保存"
    "最终成片，明确说明这一点，然后继续给出具体、可执行的建议，供用户自己"
    "在时间线上把生成好的这些视频片段拼起来。"
)

# generate_design_image：让模型在对话过程中真正生成一张角色/道具设计图或
# 场景设计图（走 generate_visual，落成该项目的真实素材），而不是只在文字里
# 描述"应该有一张这样的图"。这是本工具唯一开放给对话模型的能力，刻意不
# 暴露文件系统/网络访问等更大的工具面——保持这是一个范围明确的"设计图生成
# 助手"，而不是完整 Agent。
_EDIT_CHAT_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "generate_design_image",
            "description": (
                "生成一张角色/道具设计图或场景设计图，真实调用图片生成模型并保存为"
                "该项目的素材。生成后可以在后续对话或分镜描述里用 @名称 引用这张图"
                "作为参考，保持角色/场景在不同镜头间的一致性。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["character", "scene"],
                        "description": "character：角色或道具设计图；scene：场景设计图",
                    },
                    "name": {
                        "type": "string",
                        "description": "这张设计图的名称，简洁且有辨识度，之后用 @名称 引用，例如 Boss Orange 或 Mysterious Briefcase",
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "详细的视觉描述，用于生成图片。可以用 @名称 引用之前已经生成"
                            "的角色/道具/场景设计图作为参考，帮助保持一致性。"
                        ),
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "画面比例，例如 16:9、9:16、1:1；不确定时用 16:9",
                    },
                },
                "required": ["kind", "name", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_shot_video",
            "description": (
                "把一张已经生成的分镜关键帧真实转成一段视频片段（调用视频生成模型），"
                "保存为该项目的素材。生成后可以用 @名称 引用这段视频。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "这段视频片段的名称，之后用 @名称 引用，建议体现是第几场第几镜",
                    },
                    "first_frame_name": {
                        "type": "string",
                        "description": "作为视频首帧的已生成分镜关键帧/设计图名称（不带 @ 符号）",
                    },
                    "last_frame_name": {
                        "type": "string",
                        "description": "可选：作为视频尾帧的已生成分镜关键帧名称，用于生成从首帧过渡到尾帧的视频；不提供则只用首帧生成运动视频",
                    },
                    "description": {
                        "type": "string",
                        "description": "这个镜头里发生的动作/运镜描述，用于指导视频生成",
                    },
                    "duration_seconds": {
                        "type": "integer",
                        "description": "视频时长（秒），跟分镜列表里这一镜的时长保持一致，不确定时用 5",
                    },
                },
                "required": ["name", "first_frame_name", "description"],
            },
        },
    },
]
_EDIT_CHAT_MAX_TOOL_ITERATIONS = 6
# generate_video 自身的内部轮询预算只有 ~120s（见 video_gen_tools 模块
# docstring），真实生成经常需要更久——generate_shot_video 在工具调用内部
# 自己接着轮询到真正完成为止，而不是把一个 status=pending 的半成品素材扔
# 给模型：剪辑对话没有实验室画布那样的后台轮询 UI，这里不等到底，用户在
# 对话里就永远看不到这段视频到底成没成。
_EDIT_CHAT_VIDEO_POLL_INTERVAL_S = 10
_EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS = 30  # 30 * 10s = 5 分钟，叠加 generate_video 自身的 ~120s 预算
# "角色" composer 的输入约定是 "名称: 描述"（如 "美妆博主: 一位..."），
# 冒号支持全角/半角。识别出来的名称直接作为素材的 name，与图片素材命名后
# 可用 "@名称" 引用的机制保持一致；识别不出格式时整段文本仍按描述处理。
_RE_CHARACTER_NAME = re.compile(r"^\s*([^:：\n]{1,40})[:：]\s*(.+)$", re.DOTALL)

# "图片参考"（imageRef）处理卡片/generate_design_image 的多参考图合成一次
# 最多接受几张参考图——generate_visual 本身不限制数量，这里设一个实用上限
# 避免请求体/prompt 里 @引用过多，托底供应商侧的负载与耐心。
_MAX_IMAGE_REFERENCES = 4


class DirectorRpcError(Exception):
    """Director RPC 稳定业务错误（带 code，供前端/网关透传）.

    与 skill_manager.SkillRpcError 完全同构，供 interface.py 的错误处理
    走同一条 `code = getattr(exc, "code", None)` 透传路径。
    """

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _parse_generation_result(result_str: str) -> dict[str, Any]:
    """解析 generate_video / check_video_status / generate_visual 的自然语言返回.

    这三个工具都返回提示性字符串而非结构化 JSON（其函数签名/返回约定本次
    明确排除在改造范围之外），因此这里做的是必要的字符串解析，而非结构化
    协议解析 —— 三种结果形态：
    - "[ERROR]: ..." 前缀 → 失败
    - "Video job {job_id} submitted and still ..." → 视频任务仍在生成中
    - 含 "Saved to: {path}" → 生成完成（视频/图片共用同一 _download_video
      文案；图片多路径以 ", " 拼接，这里取第一张）
    """
    if result_str.startswith("[ERROR]:"):
        return {"status": "failed", "error": result_str}

    still_running = _RE_STILL_RUNNING_JOB_ID.match(result_str)
    if still_running:
        return {"status": "pending", "job_id": still_running.group(1)}

    saved_to = _RE_SAVED_TO.search(result_str)
    if saved_to:
        raw_path = saved_to.group(1).strip()
        first_path = raw_path.split(", ")[0].strip()
        return {"status": "ready", "file_path": first_path}

    # 既不是错误、还在生成中，也不是"已保存"——例如 check_video_status 的
    # "Video job {id} is still {status}." 分支：仍视为进行中，而非失败。
    return {"status": "pending"}


def _parse_character_prompt(prompt: str) -> tuple[str | None, str]:
    """从 "名称: 描述" 格式里拆出角色名称，用作素材的 name。

    识别不出该格式（没有冒号，或冒号前/后为空）时返回 (None, prompt)，
    整段文本原样作为生成描述——不强制用户遵守格式。
    """
    match = _RE_CHARACTER_NAME.match(prompt)
    if not match:
        return None, prompt
    name = match.group(1).strip()
    description = match.group(2).strip()
    if not name or not description:
        return None, prompt
    return name, description


class DirectorManager:
    """导演模式的项目/生成 RPC 处理器（进程内单例，见 interface.py 构造点）."""

    def __init__(self) -> None:
        self._store = DirectorStore()
        # 不是用来保护 director_state.json 的——DirectorStore 的
        # _load/_save（含 append_asset 等）全程没有一个 await，在 asyncio
        # 单线程协作调度下天然不会被另一个协程打断，本来就不需要这把锁
        # （见 director_store 模块 docstring）。这把锁目前只用来串行化
        # 剪辑助手 一轮对话里可能连续发起的多次工具调用/模型请求（见
        # handle_director_edit_chat_send 及其调用的
        # _execute_generate_design_image / _execute_generate_shot_video）
        # ——那些调用之间本身就该按模型给的顺序一个接一个跑，用锁保证顺序
        # 更省事。handle_director_generate 里对 generate_visual/
        # generate_video 的调用不再用这把锁：那是"打给生成服务商的这次
        # 请求要不要排队"的效率决定，不是正确性要求，去掉锁能让 实验室
        # 一张处理卡片同时开的多份输出真正并发跑起来，而不是排队等最慢的。
        self._lock = asyncio.Lock()

    async def handle_director_projects_list(self, params: dict) -> dict:
        projects = self._store.list_projects()
        return {
            "projects": [p.to_dict() for p in projects],
            "asset_counts": self._store.asset_counts(),
        }

    async def handle_director_projects_get(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        if not project_id:
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id")
        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")
        return {"project": project.to_dict()}

    def _resolve_at_references(
        self, project: DirectorProject, prompt: str, max_refs: int
    ) -> tuple[str, list[str]]:
        """解析提示词中最多 max_refs 个 "@名称" 引用为已命名图片/角色素材的路径.

        跨类别：无论当前是图片还是视频生成模式，@ 引用总是从该项目"素材 ·
        图片"和"素材 · 角色"分类里找已命名、已就绪的图片（视频素材不可作为
        引用源；角色素材本质上就是一张人物参考图，和普通图片素材同等对待
        ——见 handle_director_generate 里 "角色"素材生成时的注释）。按出现
        顺序取前 max_refs 个命中，命中的 token 从提示词里移除（其余文本原样
        发给模型）。image 模式下 max_refs=_MAX_IMAGE_REFERENCES，结果整体
        作为 generate_visual 的 reference_image_paths（一张或多张参考图的
        多图合成）；video 模式下 max_refs=2，调用方把结果按顺序映射为
        first_frame_path（首帧）/ last_frame_path（尾帧）——generate_video
        最多只接受这两张。未命中任何素材时原样返回 (prompt, [])。

        "@名称" 的边界按项目里已存在的素材名称本身来定，不能假设 @引用后面
        一定跟着空白再结束——中文文本里 "@名称" 后面通常紧跟着别的字/标点、
        没有空格分隔（比如模型自己写的"把@道具名放在@场景名的台面上"），
        原来那种按 \\S+ 切 token 的正则会把 "@道具名放在@场景名的台面上"
        整段当成一个查无此素材的 token：一个引用都匹配不上，还顺带把紧跟
        在后面的第二个 "@场景名" 也吞没在同一个 token 里，永远扫描不到。
        这里改成直接用项目里已有的素材名称构造一个 "@(名称1|名称2|...)"
        的联合正则，名称按长度降序排列，保证像 "Fox"/"FoxPickB" 这样互为
        前缀的两个名称在同一位置上优先命中更长的那个。
        """
        names = {a.name for a in project.assets if a.name}
        if not names:
            return (prompt, [])
        pattern = re.compile("@(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + ")")

        resolved: list[str] = []
        cleaned = prompt
        offset = 0
        for match in pattern.finditer(prompt):
            if len(resolved) >= max_refs:
                break
            asset = self._store.find_asset_by_name(project.project_id, match.group(1))
            if not (asset and asset.type in ("image", "character") and asset.status == "ready" and asset.file_path):
                continue
            resolved.append(asset.file_path)
            start, end = match.start() - offset, match.end() - offset
            cleaned = cleaned[:start] + cleaned[end:]
            offset += match.end() - match.start()
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return (cleaned or prompt, resolved) if resolved else (prompt, [])

    def _resolve_asset_path(self, project: DirectorProject, asset_id: str) -> str | None:
        """按 asset_id 直接取一张就绪图片/角色素材的路径（不经过 "@名称" 文本解析）.

        供 实验室 节点画布使用——节点间的连线本身就是显式引用，不需要（也不该）
        把连线再编码成提示词里的 "@名称" 文本。角色素材本质上就是一张人物
        参考图，和普通图片素材同等对待。
        """
        if not asset_id:
            return None
        asset = next((a for a in project.assets if a.asset_id == asset_id), None)
        if asset and asset.type in ("image", "character") and asset.status == "ready" and asset.file_path:
            return asset.file_path
        return None

    async def handle_director_projects_create(self, params: dict) -> dict:
        name = str(params.get("name") or "").strip()
        if not name:
            raise DirectorRpcError("INVALID_PARAMS", "项目名称不能为空")
        project = self._store.create_project(name)
        return {"project": project.to_dict(), "asset_counts": self._store.asset_counts()}

    async def handle_director_generate(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        mode = str(params.get("mode") or "").strip()
        prompt = str(params.get("prompt") or "").strip()
        first_frame_asset_id = str(params.get("first_frame_asset_id") or "").strip()
        last_frame_asset_id = str(params.get("last_frame_asset_id") or "").strip()
        raw_reference_asset_ids = params.get("reference_asset_ids")
        reference_asset_ids = (
            [str(x).strip() for x in raw_reference_asset_ids if str(x).strip()]
            if isinstance(raw_reference_asset_ids, list)
            else []
        )
        has_explicit_reference = bool(first_frame_asset_id or last_frame_asset_id or reference_asset_ids)

        if not project_id:
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id")
        if not prompt and not has_explicit_reference:
            raise DirectorRpcError("INVALID_PARAMS", "提示词不能为空")
        if mode not in _SUPPORTED_MODES:
            raise DirectorRpcError(
                "NOT_SUPPORTED", f"暂不支持的生成类型: {mode or '(空)'}（即将推出）"
            )
        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")

        # "角色"素材本质上是一张人物参考图（同样调用 generate_visual），
        # 只是从 "名称: 描述" 里拆出名称直接作为素材命名，不需要用户生成后
        # 再手动重命名一遍。
        character_name: str | None = None
        if mode == "character":
            character_name, prompt = _parse_character_prompt(prompt)

        aspect_ratio = str(params.get("aspect_ratio") or "16:9")
        resolution = str(params.get("resolution") or ("720p" if mode == "video" else "512"))
        save_dir = str(get_project_assets_dir(project_id))

        if mode == "video":
            if not (video_gen_enabled() and video_gen_configured()):
                raise DirectorRpcError("NOT_CONFIGURED", "视频生成未配置，请先在设置中配置「视频处理」")
            duration_seconds = int(params.get("duration_seconds") or 15)
            generate_audio = bool(params.get("generate_audio") or False)
            # 实验室节点画布：连线本身就是显式引用，直接按 asset_id 取路径；
            # 未提供时回退到 composer 的 "@名称" 文本解析（最多 2 个 @引用：
            # 第 1 个当首帧，第 2 个当尾帧），两条路径互斥、不叠加。
            first_frame_path = self._resolve_asset_path(project, first_frame_asset_id)
            last_frame_path = self._resolve_asset_path(project, last_frame_asset_id)
            if first_frame_path or last_frame_path:
                cleaned_prompt = prompt or "Generate a video based on the provided reference image(s)."
            else:
                cleaned_prompt, ref_paths = self._resolve_at_references(project, prompt, max_refs=2)
                first_frame_path = ref_paths[0] if len(ref_paths) > 0 else None
                last_frame_path = ref_paths[1] if len(ref_paths) > 1 else None
            gen_params: dict[str, Any] = {
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "duration_seconds": duration_seconds,
                "generate_audio": generate_audio,
            }
            if first_frame_path:
                gen_params["first_frame_path"] = first_frame_path
            if last_frame_path:
                gen_params["last_frame_path"] = last_frame_path
            # 这里故意不拿 self._lock：它序列化的是"打给生成服务商的这次
            # 请求"本身，不是 director_state.json 的读改写——后者
            # （DirectorStore._load/_save，见下面 append_asset）全程没有一个
            # await，在 asyncio 单线程协作调度下本来就不可能被另一个协程的
            # 同类调用打断，天然互斥，不靠这把锁保护。锁如果继续包住这次
            # await，效果只是白白把多个并发的 director.generate 请求（比如
            # 实验室 一张处理卡片同时开了几份输出）在生成服务商那一步强行
            # 排成队——串行等最慢的是效率问题，不是正确性问题，去掉锁才能
            # 真正让它们并发跑起来。
            result_str = await generate_video._func(
                prompt=cleaned_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=duration_seconds,
                first_frame_path=first_frame_path,
                last_frame_path=last_frame_path,
                generate_audio=generate_audio,
                save_dir=save_dir,
            )
        else:
            if not (visual_gen_enabled() and visual_gen_configured()):
                raise DirectorRpcError("NOT_CONFIGURED", "图片生成未配置，请先在设置中配置「图片处理」")
            # 实验室节点画布："图片参考" 处理卡片的 image1 端口可以接多条
            # 连线（多参考图合成），每条连线都是一个显式的 asset_id；未提供
            # 时回退到 composer 的 "@名称" 文本解析（最多
            # _MAX_IMAGE_REFERENCES 个 @引用），两条路径互斥、不叠加。
            reference_image_paths = [
                path
                for path in (self._resolve_asset_path(project, aid) for aid in reference_asset_ids)
                if path
            ]
            if reference_image_paths:
                cleaned_prompt = prompt or "Generate an image based on the provided reference image(s)."
            else:
                cleaned_prompt, reference_image_paths = self._resolve_at_references(
                    project, prompt, max_refs=_MAX_IMAGE_REFERENCES
                )
            gen_params = {"aspect_ratio": aspect_ratio, "resolution": resolution}
            if reference_image_paths:
                gen_params["reference_image_paths"] = reference_image_paths
            # 同上：不拿锁，让多个并发的图片生成请求真正并发打到服务商，而
            # 不是在这里排队串行。
            result_str = await generate_visual._func(
                prompt=cleaned_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                reference_image_paths=reference_image_paths or None,
                save_dir=save_dir,
            )

        parsed = _parse_generation_result(result_str)
        if parsed["status"] == "failed":
            raise DirectorRpcError("GENERATION_FAILED", parsed.get("error") or result_str)

        asset = DirectorAsset(
            asset_id=f"asset_{secrets.token_hex(4)}",
            type=mode,
            status=parsed["status"],
            prompt=prompt or cleaned_prompt,
            params=gen_params,
            file_path=parsed.get("file_path"),
            job_id=parsed.get("job_id"),
            name=character_name,
        )
        project = self._store.append_asset(project_id, asset)
        return {
            "project": project.to_dict(),
            "asset_id": asset.asset_id,
            "asset_counts": self._store.asset_counts(),
        }

    async def handle_director_generate_check_status(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        asset_id = str(params.get("asset_id") or "").strip()
        job_id = str(params.get("job_id") or "").strip()
        if not (project_id and asset_id and job_id):
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id / asset_id / job_id")

        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")

        save_dir = str(get_project_assets_dir(project_id))
        async with self._lock:
            result_str = await check_video_status._func(job_id=job_id, save_dir=save_dir)

        parsed = _parse_generation_result(result_str)
        if parsed["status"] == "failed":
            project = self._store.update_asset(
                project_id, asset_id, status="failed", error=parsed.get("error") or result_str
            )
        elif parsed["status"] == "ready":
            project = self._store.update_asset(
                project_id, asset_id, status="ready", file_path=parsed.get("file_path")
            )
        # "pending"：任务仍在进行，asset 状态保持不变，仅原样返回给前端继续轮询。

        return {
            "project": project.to_dict(),
            "asset_id": asset_id,
            "status": parsed["status"],
            "asset_counts": self._store.asset_counts(),
        }

    async def handle_director_asset_rename(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        asset_id = str(params.get("asset_id") or "").strip()
        # 空字符串合法——用于清除自定义名称，回退展示 prompt。
        name = str(params.get("name") or "").strip()
        if not (project_id and asset_id):
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id / asset_id")

        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")
        if not any(a.asset_id == asset_id for a in project.assets):
            raise DirectorRpcError("ASSET_NOT_FOUND", f"未找到素材: {asset_id}")

        project = self._store.update_asset(project_id, asset_id, name=name or None)
        return {"project": project.to_dict()}

    async def handle_director_asset_delete(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        asset_id = str(params.get("asset_id") or "").strip()
        if not (project_id and asset_id):
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id / asset_id")

        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")
        asset = next((a for a in project.assets if a.asset_id == asset_id), None)
        if asset is None:
            raise DirectorRpcError("ASSET_NOT_FOUND", f"未找到素材: {asset_id}")

        project = self._store.delete_asset(project_id, asset_id)

        # 尽力删除磁盘文件；失败只记日志，不影响已经生效的元数据删除
        # （用户在 UI 上看到的"已删除"以 director_state.json 为准）。
        if asset.file_path:
            try:
                Path(asset.file_path).unlink(missing_ok=True)
            except OSError:
                logger.exception("[DirectorManager] 删除素材文件失败: %s", asset.file_path)

        return {"project": project.to_dict(), "asset_counts": self._store.asset_counts()}

    async def _execute_edit_chat_tool_call(
        self, project: DirectorProject, tool_call: dict[str, Any]
    ) -> tuple[str, str | None]:
        """执行模型发起的一次工具调用（generate_design_image 或
        generate_shot_video），按 function.name 分派.

        返回 (给模型看的结果文本, 生成成功时的 asset_id 或 None)——结果文本
        作为 role=tool 消息喂回模型，让它知道生成是否成功、该用什么名称
        继续引用；asset_id 收集起来挂到这一轮助手消息的 image_asset_ids 上，
        前端就能在对话里直接展示这张刚生成的图/视频（ChatMessageBubble 已经
        按 image_asset_ids 渲染缩略图，视频缩略图不需要额外的前端改动——
        见下方 generate_shot_video 的返回处理，asset_id 一样会被收集）。
        """
        function = tool_call.get("function") or {}
        function_name = function.get("name")

        try:
            args = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            return "工具参数不是合法的 JSON，生成失败。", None

        if function_name == "generate_design_image":
            return await self._execute_generate_design_image(project, args)
        if function_name == "generate_shot_video":
            return await self._execute_generate_shot_video(project, args)
        return f"未知工具: {function_name}", None

    async def _execute_generate_design_image(
        self, project: DirectorProject, args: dict[str, Any]
    ) -> tuple[str, str | None]:
        kind = str(args.get("kind") or "").strip()
        design_name = str(args.get("name") or "").strip()
        description = str(args.get("description") or "").strip()
        aspect_ratio = str(args.get("aspect_ratio") or "16:9").strip() or "16:9"

        if kind not in ("character", "scene"):
            return "kind 必须是 character 或 scene。", None
        if not design_name or not description:
            return "缺少 name 或 description，生成失败。", None
        if not (visual_gen_enabled() and visual_gen_configured()):
            return "图片生成未配置，请先在设置中配置「图片处理」，暂时无法生成设计图。", None

        # description 里的 "@已有设计图名称" 解析成真正的参考图路径（可以有
        # 多个，比如同时 @一个道具和一个场景做合成）——和 composer/实验室
        # 画布同一套 "@名称" 引用机制（见 _resolve_at_references），保持
        # 角色/场景在不同镜头间的视觉一致性。
        cleaned_prompt, reference_image_paths = self._resolve_at_references(
            project, description, max_refs=_MAX_IMAGE_REFERENCES
        )
        save_dir = str(get_project_assets_dir(project.project_id))

        async with self._lock:
            result_str = await generate_visual._func(
                prompt=cleaned_prompt,
                aspect_ratio=aspect_ratio,
                resolution="512",
                reference_image_paths=reference_image_paths or None,
                save_dir=save_dir,
            )

        parsed = _parse_generation_result(result_str)
        if parsed["status"] == "failed":
            return f"生成失败：{parsed.get('error') or result_str}", None

        gen_params: dict[str, Any] = {"aspect_ratio": aspect_ratio, "resolution": "512"}
        if reference_image_paths:
            gen_params["reference_image_paths"] = reference_image_paths
        asset = DirectorAsset(
            asset_id=f"asset_{secrets.token_hex(4)}",
            type="character" if kind == "character" else "image",
            status=parsed["status"],
            prompt=description,
            params=gen_params,
            file_path=parsed.get("file_path"),
            job_id=parsed.get("job_id"),
            name=design_name,
        )
        self._store.append_asset(project.project_id, asset)

        kind_label = "角色/道具" if kind == "character" else "场景"
        return f"已生成并保存「{design_name}」（{kind_label}设计图）。后续可以用 @{design_name} 引用这张图。", asset.asset_id

    async def _execute_generate_shot_video(
        self, project: DirectorProject, args: dict[str, Any]
    ) -> tuple[str, str | None]:
        video_name = str(args.get("name") or "").strip()
        first_frame_name = str(args.get("first_frame_name") or "").strip()
        last_frame_name = str(args.get("last_frame_name") or "").strip()
        description = str(args.get("description") or "").strip()
        try:
            duration_seconds = int(args.get("duration_seconds") or 5)
        except (TypeError, ValueError):
            duration_seconds = 5

        if not video_name or not first_frame_name:
            return "缺少 name 或 first_frame_name，生成失败。", None
        if not (video_gen_enabled() and video_gen_configured()):
            return "视频生成未配置，请先在设置中配置「视频处理」，暂时无法生成视频。", None

        first_frame_asset = self._store.find_asset_by_name(project.project_id, first_frame_name)
        if not (
            first_frame_asset
            and first_frame_asset.type in ("image", "character")
            and first_frame_asset.status == "ready"
            and first_frame_asset.file_path
        ):
            return (
                f"找不到名为「{first_frame_name}」的已生成分镜关键帧，"
                "请先用 generate_design_image 生成这一帧，或检查名称拼写。",
                None,
            )
        first_frame_path = first_frame_asset.file_path

        last_frame_path = None
        if last_frame_name:
            last_frame_asset = self._store.find_asset_by_name(project.project_id, last_frame_name)
            if (
                last_frame_asset
                and last_frame_asset.type in ("image", "character")
                and last_frame_asset.status == "ready"
                and last_frame_asset.file_path
            ):
                last_frame_path = last_frame_asset.file_path

        save_dir = str(get_project_assets_dir(project.project_id))
        prompt = description or "Generate a video based on the provided reference image(s)."
        async with self._lock:
            result_str = await generate_video._func(
                prompt=prompt,
                aspect_ratio="16:9",
                resolution="720p",
                duration_seconds=duration_seconds,
                first_frame_path=first_frame_path,
                last_frame_path=last_frame_path,
                generate_audio=False,
                save_dir=save_dir,
            )
        parsed = _parse_generation_result(result_str)

        # generate_video 自身的内部轮询预算只有 ~120s，真实生成经常需要
        # 更久——这里接着轮询到真正完成为止（成功或失败），而不是把一个
        # status=pending 的半成品素材扔给模型：剪辑对话没有实验室画布那样
        # 的后台轮询 UI，这里不等到底，用户在对话里就永远看不到这段视频
        # 到底成没成。
        attempts = 0
        while parsed["status"] == "pending" and parsed.get("job_id") and attempts < _EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS:
            await asyncio.sleep(_EDIT_CHAT_VIDEO_POLL_INTERVAL_S)
            async with self._lock:
                result_str = await check_video_status._func(job_id=parsed["job_id"], save_dir=save_dir)
            parsed = _parse_generation_result(result_str)
            attempts += 1

        if parsed["status"] == "failed":
            return f"视频生成失败：{parsed.get('error') or result_str}", None
        if parsed["status"] == "pending":
            return (
                f"视频「{video_name}」仍在生成中，超出了等待时间——可以稍后在素材面板里查看，"
                "或者告诉我重新生成。",
                None,
            )

        gen_params: dict[str, Any] = {
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "duration_seconds": duration_seconds,
            "generate_audio": False,
            "first_frame_path": first_frame_path,
        }
        if last_frame_path:
            gen_params["last_frame_path"] = last_frame_path
        asset = DirectorAsset(
            asset_id=f"asset_{secrets.token_hex(4)}",
            type="video",
            status=parsed["status"],
            prompt=description,
            params=gen_params,
            file_path=parsed.get("file_path"),
            job_id=parsed.get("job_id"),
            name=video_name,
        )
        self._store.append_asset(project.project_id, asset)
        return f"已生成视频「{video_name}」。后续可以用 @{video_name} 引用这段视频。", asset.asset_id

    async def handle_director_edit_chat_send(self, params: dict) -> dict:
        """剪辑 tab 创作助手：发一条用户消息，让模型规划故事板/分镜，并在
        需要时真正调用 generate_design_image 生成角色/场景设计图.

        与 handle_director_generate 同样是无状态直连调用，不经过完整的
        Agent/会话；但这里允许模型在一轮回复里发起若干次工具调用（OpenAI
        兼容的 tools/tool_calls 协议）——每次工具调用都会真实生成一张设计图
        并落成该项目的素材，调用结果（成功/失败 + 名称）作为 role=tool 消息
        喂回模型，模型据此决定要不要接着用这张图继续规划分镜，直到给出一段
        不再包含工具调用的最终回复，或达到 _EDIT_CHAT_MAX_TOOL_ITERATIONS
        次迭代上限（避免模型陷入不停调用工具的死循环）。

        没有单独的"取历史"RPC：project.to_dict() 已经带上
        edit_chat_messages，现有的 director.projects.get/list 打开项目时
        就能拿到完整对话历史，不需要再开一个端点。
        """
        project_id = str(params.get("project_id") or "").strip()
        text = str(params.get("text") or "").strip()
        raw_image_ids = params.get("image_asset_ids")
        image_asset_ids = [str(x) for x in raw_image_ids if str(x)] if isinstance(raw_image_ids, list) else []

        if not project_id:
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id")
        if not text:
            raise DirectorRpcError("INVALID_PARAMS", "消息不能为空")
        if not (edit_chat_enabled() and edit_chat_configured()):
            raise DirectorRpcError("NOT_CONFIGURED", "剪辑对话未配置，请先在设置中配置「剪辑对话」")

        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")

        image_paths = [
            path
            for path in (self._resolve_asset_path(project, asset_id) for asset_id in image_asset_ids)
            if path
        ]

        # 这一轮开始前的历史（不含这一轮用户消息本身）——用户这一轮的文本
        # 连同参考图片，作为一条多模态消息单独构造并追加在后面。
        history_messages = [{"role": m.role, "content": m.content} for m in project.edit_chat_messages]

        # 用户这一轮先单独落盘，即使随后的模型调用失败，输入也不会丢——
        # 用户不用重新打一遍字，重试时历史里已经有这条消息了。
        user_message = EditChatMessage(role="user", content=text, image_asset_ids=image_asset_ids)
        project = self._store.append_edit_chat_messages(project_id, [user_message])

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _EDIT_CHAT_SYSTEM_PROMPT},
            *history_messages,
            build_multimodal_user_message(text, image_paths),
        ]

        generated_asset_ids: list[str] = []
        final_text = ""
        for _ in range(_EDIT_CHAT_MAX_TOOL_ITERATIONS):
            async with self._lock:
                assistant_msg = await call_edit_chat_completion(messages, tools=_EDIT_CHAT_TOOLS_SCHEMA)

            content = str(assistant_msg.get("content") or "")
            if content.startswith("[ERROR]:"):
                raise DirectorRpcError("GENERATION_FAILED", content)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                final_text = content
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tool_call in tool_calls:
                result_text, asset_id = await self._execute_edit_chat_tool_call(project, tool_call)
                if asset_id:
                    generated_asset_ids.append(asset_id)
                    # 同一轮里后面的工具调用（比如生成场景时引用刚生成的角色）
                    # 要能通过 "@名称" 找到这张刚生成的图，刷新本地这份
                    # project 快照，而不是继续用回合开始时的旧素材列表。
                    refreshed = self._store.get_project(project.project_id)
                    if refreshed is not None:
                        project = refreshed
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.get("id"), "content": result_text}
                )
        else:
            final_text = (
                final_text
                or "这一轮已经生成了不少设计图，先在这里停一下——可以告诉我这些设计图是否满意，或者继续说说下一步想做什么。"
            )

        assistant_message = EditChatMessage(role="assistant", content=final_text, image_asset_ids=generated_asset_ids)
        project = self._store.append_edit_chat_messages(project_id, [assistant_message])

        return {"project": project.to_dict()}

    async def handle_director_lab_canvas_save(self, params: dict) -> dict:
        """实验室节点画布持久化：切换 tab 会整个卸载 LabCanvas（React 组件
        状态随之清空），之前画布节点/连线只存在 useNodesState 的内存里，一
        离开 实验室 tab 就丢——这里把前端 debounce 后发来的完整节点/连线
        快照整体落盘到该项目，下次打开 实验室 tab 时从 project.lab_nodes/
        lab_edges 里原样恢复。
        """
        project_id = str(params.get("project_id") or "").strip()
        raw_nodes = params.get("nodes")
        raw_edges = params.get("edges")

        if not project_id:
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise DirectorRpcError("INVALID_PARAMS", "nodes/edges 必须是数组")

        nodes = [item for item in raw_nodes if isinstance(item, dict)]
        edges = [item for item in raw_edges if isinstance(item, dict)]

        try:
            project = self._store.save_lab_canvas(project_id, nodes, edges)
        except KeyError:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}") from None

        return {"project": project.to_dict()}

