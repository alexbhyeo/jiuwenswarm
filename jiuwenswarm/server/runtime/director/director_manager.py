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
from jiuwenswarm.server.runtime.director.director_store import (
    DirectorAsset,
    DirectorProject,
    DirectorStore,
    get_project_assets_dir,
)

logger = logging.getLogger(__name__)

_SUPPORTED_MODES = ("video", "image")

_RE_STILL_RUNNING_JOB_ID = re.compile(r"^Video job (\S+) submitted and still")
_RE_SAVED_TO = re.compile(r"Saved to:\s*(.+)")
# "@名称" 引用：名称本身不含空白，与常见 @提及 约定一致（重命名素材时应
# 避免空格）。
_RE_AT_REFERENCE = re.compile(r"@(\S+)")


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


class DirectorManager:
    """导演模式的项目/生成 RPC 处理器（进程内单例，见 interface.py 构造点）."""

    def __init__(self) -> None:
        self._store = DirectorStore()
        # 同一进程内多个 director.generate RPC 并发到达时，串行化对
        # director_state.json 的读改写；见 director_store 模块 docstring。
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

    def _resolve_at_reference(self, project: DirectorProject, prompt: str) -> tuple[str, str | None]:
        """解析提示词中的 "@名称" 引用为一张已命名的图片素材.

        只认第一个能匹配到"就绪图片素材"的 @token；命中后从提示词里去掉该
        token（其余文本原样发给模型），返回 (清理后的提示词, 图片路径)。
        未命中任何素材时原样返回 (prompt, None) —— 调用方据此决定是否传
        reference_image_path，而不是把裸露的 "@xxx" 发给模型。
        """
        for match in _RE_AT_REFERENCE.finditer(prompt):
            asset = self._store.find_asset_by_name(project.project_id, match.group(1))
            if asset and asset.type == "image" and asset.status == "ready" and asset.file_path:
                cleaned = (prompt[: match.start()] + prompt[match.end():]).strip()
                cleaned = re.sub(r"\s{2,}", " ", cleaned)
                return cleaned or prompt, asset.file_path
        return prompt, None

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

        if not project_id:
            raise DirectorRpcError("INVALID_PARAMS", "缺少 project_id")
        if not prompt:
            raise DirectorRpcError("INVALID_PARAMS", "提示词不能为空")
        if mode not in _SUPPORTED_MODES:
            raise DirectorRpcError(
                "NOT_SUPPORTED", f"暂不支持的生成类型: {mode or '(空)'}（即将推出）"
            )
        project = self._store.get_project(project_id)
        if project is None:
            raise DirectorRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")

        aspect_ratio = str(params.get("aspect_ratio") or "16:9")
        resolution = str(params.get("resolution") or ("720p" if mode == "video" else "512"))
        save_dir = str(get_project_assets_dir(project_id))

        if mode == "video":
            if not (video_gen_enabled() and video_gen_configured()):
                raise DirectorRpcError("NOT_CONFIGURED", "视频生成未配置，请先在设置中配置「视频处理」")
            duration_seconds = int(params.get("duration_seconds") or 15)
            generate_audio = bool(params.get("generate_audio") or False)
            gen_params: dict[str, Any] = {
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "duration_seconds": duration_seconds,
                "generate_audio": generate_audio,
            }
            async with self._lock:
                result_str = await generate_video._func(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    duration_seconds=duration_seconds,
                    generate_audio=generate_audio,
                    save_dir=save_dir,
                )
        else:
            if not (visual_gen_enabled() and visual_gen_configured()):
                raise DirectorRpcError("NOT_CONFIGURED", "图片生成未配置，请先在设置中配置「图片处理」")
            cleaned_prompt, reference_image_path = self._resolve_at_reference(project, prompt)
            gen_params = {"aspect_ratio": aspect_ratio, "resolution": resolution}
            if reference_image_path:
                gen_params["reference_image_path"] = reference_image_path
            async with self._lock:
                result_str = await generate_visual._func(
                    prompt=cleaned_prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    reference_image_path=reference_image_path,
                    save_dir=save_dir,
                )

        parsed = _parse_generation_result(result_str)
        if parsed["status"] == "failed":
            raise DirectorRpcError("GENERATION_FAILED", parsed.get("error") or result_str)

        asset = DirectorAsset(
            asset_id=f"asset_{secrets.token_hex(4)}",
            type=mode,
            status=parsed["status"],
            prompt=prompt,
            params=gen_params,
            file_path=parsed.get("file_path"),
            job_id=parsed.get("job_id"),
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

