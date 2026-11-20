# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""游戏模式的 RPC 处理器（``game.*``）.

与 ``director_manager.DirectorManager`` 同构：一个方法一个处理器，返回可直接
序列化的 dict。P0 范围（设计 D-20261107-1637 第 8 节）：项目生命周期、脚手架、
素材登记、文件工作区读写、桥接状态；D-20261116-1047-game-workshop-lab 在此
基础上加了素材分解（Tripo3D）与实验室画布持久化。
"""

from __future__ import annotations

import logging
import secrets
import shutil
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.game.game_item_decompose import (
    GameItemDecompose,
    GameItemDecomposeError,
)
from jiuwenswarm.server.runtime.game.game_item_mesh_ops import (
    MESH_COMPLETE_STAGE_SEGMENT,
    GameItemMeshOps,
    GameItemMeshOpsError,
)
from jiuwenswarm.server.runtime.game.game_store import (
    GAME_ASSET_TYPES,
    GameAsset,
    GameProject,
    GameStore,
    get_project_assets_dir,
    get_project_dir,
)
from jiuwenswarm.agents.harness.common.tools.edit_chat_tools import (
    edit_chat_configured,
    edit_chat_enabled,
)

from jiuwenswarm.server.runtime.game.godot_bridge import GodotBridge
from jiuwenswarm.server.runtime.game.game_lab_chat import GameLabChat
from jiuwenswarm.server.runtime.game.game_scaffold import scaffold_home_town

logger = logging.getLogger(__name__)

# 代码工作区只暴露工程内这些后缀，避免把任意文件当代码编辑。
_EDITABLE_SUFFIXES = {".gd", ".cs", ".tscn", ".tres", ".godot", ".json", ".cfg", ".md", ".txt"}
_MAX_READ_BYTES = 512 * 1024


class GameRpcError(Exception):
    """带 code 的 RPC 失败，与导演模式的错误形状保持一致."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GameManager:
    """游戏模式处理器集合；由 AgentServer 在构造时持有."""

    def __init__(self) -> None:
        self._store = GameStore()
        self._bridge = GodotBridge()
        self._decompose = GameItemDecompose()
        self._mesh_ops = GameItemMeshOps()
        self._lab_chat = GameLabChat()

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------
    def _require_project(self, project_id: str) -> GameProject:
        if not project_id:
            raise GameRpcError("INVALID_PARAMS", "缺少 project_id")
        project = self._store.get_project(project_id)
        if project is None:
            raise GameRpcError("PROJECT_NOT_FOUND", f"未找到项目: {project_id}")
        return project

    async def handle_game_projects_list(self, params: dict) -> dict:
        return {"projects": [p.to_dict() for p in self._store.list_projects()]}

    async def handle_game_projects_create(self, params: dict) -> dict:
        name = str(params.get("name") or "").strip()
        if not name:
            raise GameRpcError("INVALID_PARAMS", "缺少项目名称")
        project = self._store.create_project(name)
        return {"project": project.to_dict()}

    async def handle_game_projects_get(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        return {"project": project.to_dict()}

    async def handle_game_projects_rename(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        name = str(params.get("name") or "").strip()
        if not name:
            raise GameRpcError("INVALID_PARAMS", "缺少项目名称")
        self._require_project(project_id)
        return {"project": self._store.update_project(project_id, name=name).to_dict()}

    async def handle_game_projects_delete(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        self._require_project(project_id)
        self._store.delete_project(project_id)
        # 尽力删除工程目录；失败只记日志，元数据删除已经生效。
        try:
            project_dir = get_project_dir(project_id)
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
        except OSError:
            logger.exception("[game] 删除工程目录失败: %s", project_id)
        return {"deleted": project_id}

    async def handle_game_projects_scaffold(self, params: dict) -> dict:
        """生成/修复最小可运行 Godot 工程（幂等）."""
        project = self._require_project(str(params.get("project_id") or "").strip())
        project_dir = get_project_dir(project.project_id)
        project_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = self._bridge.scaffold(project_dir, project.name)
        status = "scaffolded" if (project_dir / "project.godot").is_file() else project.status
        project = self._store.update_project(project.project_id, status=status)
        return {"project": project.to_dict(), "created": created}

    async def handle_game_projects_build_2d(self, params: dict) -> dict:
        """生成 2D 主城工程（森林冒险形态：主城 + 五个入口 + 灵宠卡）."""
        project = self._require_project(str(params.get("project_id") or "").strip())
        project_dir = get_project_dir(project.project_id)
        project_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = scaffold_home_town(project_dir, project.name, project.assets)
        project = self._store.update_project(project.project_id, status="scaffolded")
        return {"project": project.to_dict(), "created": created}

    # ------------------------------------------------------------------
    # 素材
    # ------------------------------------------------------------------
    async def handle_game_assets_list(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        if project_id:
            project = self._require_project(project_id)
            assets = [a.to_dict() for a in project.assets]
        else:
            assets = [a.to_dict() for p in self._store.list_projects() for a in p.assets]
        return {
            "assets": assets,
            "types": list(GAME_ASSET_TYPES),
        }

    async def handle_game_assets_import(self, params: dict) -> dict:
        """把已有文件登记为项目素材（生成工具的产物或用户上传）."""
        project = self._require_project(str(params.get("project_id") or "").strip())
        file_path = str(params.get("file_path") or "").strip()
        asset_type = str(params.get("type") or "").strip() or "prop"
        if not file_path:
            raise GameRpcError("INVALID_PARAMS", "缺少 file_path")
        if asset_type not in GAME_ASSET_TYPES:
            raise GameRpcError(
                "INVALID_PARAMS",
                f"不支持的素材类型 {asset_type}；可选：{', '.join(GAME_ASSET_TYPES)}",
            )
        source = Path(file_path)
        if not source.is_file():
            raise GameRpcError("INVALID_PARAMS", f"文件不存在: {file_path}")
        name = str(params.get("name") or "").strip() or source.stem
        asset = GameAsset(
            asset_id=f"gasset_{source.stem[:12]}_{abs(hash(str(source))) % 10**6:06d}",
            type=asset_type,
            name=name,
            file_path=str(source),
            params={"source": "import"},
        )
        project = self._store.append_asset(project.project_id, asset)
        return {"project": project.to_dict(), "asset": asset.to_dict()}

    async def handle_game_assets_generate(self, params: dict) -> dict:
        """用 ``generate_visual``/``generate_video`` 生成一张/一段游戏素材
        并登记到项目.

        与导演模式的生成卡同理：参数已由前端完全确定，再走一次 LLM 回合只会
        增加延迟与不确定性，所以这里直接调工具函数。``video`` 类目复用
        导演模式已验证的 ``generate_video``（D-03），不等 Godot-MCP 落地。
        """
        project = self._require_project(str(params.get("project_id") or "").strip())
        prompt = str(params.get("prompt") or "").strip()
        asset_type = str(params.get("type") or "").strip() or "character"
        if not prompt:
            raise GameRpcError("INVALID_PARAMS", "缺少 prompt")
        if asset_type not in GAME_ASSET_TYPES:
            raise GameRpcError(
                "INVALID_PARAMS",
                f"不支持的素材类型 {asset_type}；可选：{', '.join(GAME_ASSET_TYPES)}",
            )

        from jiuwenswarm.server.runtime.director.director_manager import (
            _parse_generation_result,
        )

        save_dir = str(get_project_assets_dir(project.project_id))
        if asset_type == "video":
            from jiuwenswarm.agents.harness.common.tools.video_gen_tools import (
                video_gen_configured,
                video_gen_enabled,
            )

            if not (video_gen_enabled() and video_gen_configured()):
                raise GameRpcError("NOT_CONFIGURED", "视频生成未配置，请先在设置中配置「视频处理」")

            from jiuwenswarm.agents.harness.common.tools.video_gen_tools import generate_video

            result_str = await generate_video._func(
                prompt=prompt,
                aspect_ratio=str(params.get("aspect_ratio") or "16:9"),
                resolution=str(params.get("resolution") or "720p"),
                duration_seconds=int(params.get("duration_seconds") or 5),
                save_dir=save_dir,
            )
        else:
            from jiuwenswarm.agents.harness.common.tools.visual_gen_tools import generate_visual

            result_str = await generate_visual._func(
                prompt=prompt,
                aspect_ratio=str(params.get("aspect_ratio") or "1:1"),
                resolution=str(params.get("resolution") or "1k"),
                reference_image_paths=params.get("reference_image_paths") or None,
                save_dir=save_dir,
            )
        parsed = _parse_generation_result(result_str)
        name = str(params.get("name") or "").strip() or prompt[:24]
        asset = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type=asset_type,
            name=name,
            file_path=parsed.get("file_path"),
            status=parsed["status"],
            params={"source": "generate", "prompt": prompt},
        )
        project = self._store.append_asset(project.project_id, asset)
        payload = {"project": project.to_dict(), "asset": asset.to_dict()}
        if parsed["status"] == "failed":
            payload["error"] = parsed.get("error") or result_str
        return payload

    async def handle_game_assets_delete(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        asset_id = str(params.get("asset_id") or "").strip()
        decompose_batch_id = str(params.get("decompose_batch_id") or "").strip()
        if decompose_batch_id:
            # 整批删除同一次分解产出的所有部件（D-06），不要求逐个点掉。
            return {
                "project": self._store.delete_assets_by_batch(
                    project.project_id, decompose_batch_id
                ).to_dict()
            }
        if not any(a.asset_id == asset_id for a in project.assets):
            raise GameRpcError("ASSET_NOT_FOUND", f"未找到素材: {asset_id}")
        return {"project": self._store.delete_asset(project.project_id, asset_id).to_dict()}

    # ------------------------------------------------------------------
    # 素材分解（Tripo3D，D-20261116-1047-game-workshop-lab D-04/D-06）
    # ------------------------------------------------------------------
    async def handle_game_assets_decompose(self, params: dict) -> dict:
        """提交一次分解：源素材必须是「角色」或「世界」类目下已就绪的图片
        素材。与导演模式的 video_bridge/视频生成一致——这里只提交任务、
        落一个 pending 占位素材，真正的产物由 check_status 轮询拿回来。
        """
        project = self._require_project(str(params.get("project_id") or "").strip())
        source_asset_id = str(params.get("source_asset_id") or "").strip()
        if not source_asset_id:
            raise GameRpcError("INVALID_PARAMS", "缺少 source_asset_id")
        source = next((a for a in project.assets if a.asset_id == source_asset_id), None)
        if source is None or source.type not in ("character", "world") or source.status != "ready" or not source.file_path:
            raise GameRpcError(
                "INVALID_PARAMS", "分解需要先选中一个已就绪的「角色」或「世界」类目图片素材"
            )
        hint = str(params.get("hint") or "").strip() or None
        granularity = str(params.get("granularity") or "medium").strip()

        try:
            task_id = await self._decompose.submit(source.file_path, hint=hint, granularity=granularity)
        except GameItemDecomposeError as exc:
            raise GameRpcError(exc.code, str(exc)) from exc

        decompose_batch_id = f"gbatch_{secrets.token_hex(4)}"
        placeholder = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model_part",
            name=f"分解中…（来自 {source.name}）",
            status="pending",
            params={"source": "decompose", "job_id": task_id},
            source_asset_id=source_asset_id,
            decompose_batch_id=decompose_batch_id,
        )
        project = self._store.append_asset(project.project_id, placeholder)
        return {
            "project": project.to_dict(),
            "placeholder_asset_id": placeholder.asset_id,
            "decompose_batch_id": decompose_batch_id,
            "job_id": task_id,
        }

    async def handle_game_assets_decompose_check_status(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        placeholder_asset_id = str(params.get("placeholder_asset_id") or "").strip()
        job_id = str(params.get("job_id") or "").strip()
        decompose_batch_id = str(params.get("decompose_batch_id") or "").strip()
        if not (placeholder_asset_id and job_id and decompose_batch_id):
            raise GameRpcError(
                "INVALID_PARAMS", "缺少 placeholder_asset_id / job_id / decompose_batch_id"
            )

        save_dir = str(get_project_assets_dir(project.project_id))
        status = await self._decompose.poll(job_id, save_dir=save_dir)

        if status.status == "pending":
            return {"project": project.to_dict(), "status": "pending"}

        # 先从仍带着占位素材的 project 里取出 source_asset_id，再删占位——
        # 占位无论成功还是失败都要清掉：成功时被真正的部件素材替换，失败时
        # 不留一个永远 pending 的孤儿。
        source_asset_id = next(
            (a.source_asset_id for a in project.assets if a.asset_id == placeholder_asset_id),
            None,
        )
        project = self._store.delete_asset(project.project_id, placeholder_asset_id)

        if status.status == "failed":
            return {"project": project.to_dict(), "status": "failed", "error": status.error}

        created_assets: list[dict[str, Any]] = []
        for part in status.parts:
            asset = GameAsset(
                asset_id=f"gasset_{secrets.token_hex(4)}",
                type="model_part",
                name=part.name,
                file_path=part.file_path,
                status="ready",
                params={"source": "decompose", "rendered_image_path": part.rendered_image_path},
                source_asset_id=source_asset_id,
                decompose_batch_id=decompose_batch_id,
            )
            project = self._store.append_asset(project.project_id, asset)
            created_assets.append(asset.to_dict())
        return {"project": project.to_dict(), "status": "success", "assets": created_assets}

    # ------------------------------------------------------------------
    # 2D 转 3D（Tripo3D image-to-model，单体模型，不分部件）
    # ------------------------------------------------------------------
    async def handle_game_assets_to_model(self, params: dict) -> dict:
        """提交一次 2D 转 3D：源素材必须是「角色」或「世界」类目下已就绪的
        图片素材。与分解同一套取舍——这里只提交任务、落一个 pending 占位
        素材，真正的产物由 check_status 轮询拿回来。
        """
        project = self._require_project(str(params.get("project_id") or "").strip())
        source_asset_id = str(params.get("source_asset_id") or "").strip()
        if not source_asset_id:
            raise GameRpcError("INVALID_PARAMS", "缺少 source_asset_id")
        source = next((a for a in project.assets if a.asset_id == source_asset_id), None)
        if source is None or source.type not in ("character", "world") or source.status != "ready" or not source.file_path:
            raise GameRpcError(
                "INVALID_PARAMS", "2D 转 3D 需要先选中一个已就绪的「角色」或「世界」类目图片素材"
            )

        try:
            task_id = await self._decompose.submit_to_model(source.file_path)
        except GameItemDecomposeError as exc:
            raise GameRpcError(exc.code, str(exc)) from exc

        placeholder = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"2D转3D 中…（来自 {source.name}）",
            status="pending",
            params={"source": "to_model", "job_id": task_id},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, placeholder)
        return {
            "project": project.to_dict(),
            "placeholder_asset_id": placeholder.asset_id,
            "job_id": task_id,
        }

    async def handle_game_assets_to_model_check_status(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        placeholder_asset_id = str(params.get("placeholder_asset_id") or "").strip()
        job_id = str(params.get("job_id") or "").strip()
        if not (placeholder_asset_id and job_id):
            raise GameRpcError("INVALID_PARAMS", "缺少 placeholder_asset_id / job_id")

        save_dir = str(get_project_assets_dir(project.project_id))
        status = await self._decompose.poll_to_model(job_id, save_dir=save_dir)

        if status.status == "pending":
            return {"project": project.to_dict(), "status": "pending"}

        # 同分解一样：先从仍带着占位素材的 project 里取出源信息，再删占位。
        source = next(
            (a for a in project.assets if a.asset_id == placeholder_asset_id),
            None,
        )
        source_asset_id = source.source_asset_id if source else None
        source_name = source.name.removeprefix("2D转3D 中…（来自 ").removesuffix("）") if source else ""
        project = self._store.delete_asset(project.project_id, placeholder_asset_id)

        if status.status == "failed":
            return {"project": project.to_dict(), "status": "failed", "error": status.error}

        asset = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"{source_name} · 2D转3D" if source_name else "2D转3D 模型",
            file_path=status.model_file_path,
            status="ready",
            params={"source": "to_model"},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, asset)
        return {"project": project.to_dict(), "status": "success", "asset": asset.to_dict()}

    # ------------------------------------------------------------------
    # 网格补全 / 拓扑重构（Tripo3D mesh/complete、mesh/decimate；输入都是
    # 一个已存在的 3D 模型素材，不是图片——与分解/2D 转 3D 的来源类目相反）
    # ------------------------------------------------------------------
    def _require_model_source(self, project: GameProject, source_asset_id: str, *, action: str) -> GameAsset:
        if not source_asset_id:
            raise GameRpcError("INVALID_PARAMS", "缺少 source_asset_id")
        source = next((a for a in project.assets if a.asset_id == source_asset_id), None)
        if source is None or source.type not in ("model", "model_part") or source.status != "ready" or not source.file_path:
            raise GameRpcError("INVALID_PARAMS", f"{action}需要先选中一个已就绪的 3D 模型素材")
        return source

    async def handle_game_assets_mesh_complete(self, params: dict) -> dict:
        """提交一次网格补全：源素材必须是已就绪的模型/部件素材。补全是两段式
        流水线（先 mesh/segment 再 mesh/complete，见
        ``game_item_mesh_ops.py`` 模块注释），占位素材的 params 里记着当前
        处于哪一段（``stage``）与该段对应的 Tripo3D task_id，供 check_status
        轮询时推进。
        """
        project = self._require_project(str(params.get("project_id") or "").strip())
        source_asset_id = str(params.get("source_asset_id") or "").strip()
        source = self._require_model_source(project, source_asset_id, action="网格补全")

        try:
            task_id = await self._mesh_ops.submit_mesh_complete(source.file_path)
        except GameItemMeshOpsError as exc:
            raise GameRpcError(exc.code, str(exc)) from exc

        placeholder = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"网格补全中…（来自 {source.name}）",
            status="pending",
            params={"source": "mesh_complete", "stage": MESH_COMPLETE_STAGE_SEGMENT, "job_id": task_id},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, placeholder)
        return {
            "project": project.to_dict(),
            "placeholder_asset_id": placeholder.asset_id,
            "job_id": task_id,
        }

    async def handle_game_assets_mesh_complete_check_status(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        placeholder_asset_id = str(params.get("placeholder_asset_id") or "").strip()
        if not placeholder_asset_id:
            raise GameRpcError("INVALID_PARAMS", "缺少 placeholder_asset_id")
        placeholder = next((a for a in project.assets if a.asset_id == placeholder_asset_id), None)
        if placeholder is None:
            raise GameRpcError("ASSET_NOT_FOUND", f"未找到占位素材: {placeholder_asset_id}")
        # 当前阶段/task_id 永远以占位素材自己持久化的 params 为准——不信
        # 前端传回来的 job_id，这样即使服务在两段之间重启，也能从正确的
        # 阶段继续轮询（见 game_item_mesh_ops.py 模块注释）。
        stage = str(placeholder.params.get("stage") or MESH_COMPLETE_STAGE_SEGMENT)
        task_id = str(placeholder.params.get("job_id") or "")
        if not task_id:
            raise GameRpcError("INVALID_PARAMS", "占位素材缺少 job_id")

        save_dir = str(get_project_assets_dir(project.project_id))
        status = await self._mesh_ops.poll_mesh_complete(task_id, stage, save_dir=save_dir)

        if status.status == "pending":
            if status.stage and status.task_id and (status.stage != stage or status.task_id != task_id):
                project = self._store.update_asset(
                    project.project_id,
                    placeholder_asset_id,
                    params={**placeholder.params, "stage": status.stage, "job_id": status.task_id},
                )
            return {"project": project.to_dict(), "status": "pending"}

        source_asset_id = placeholder.source_asset_id
        source_name = placeholder.name.removeprefix("网格补全中…（来自 ").removesuffix("）")
        project = self._store.delete_asset(project.project_id, placeholder_asset_id)

        if status.status == "failed":
            return {"project": project.to_dict(), "status": "failed", "error": status.error}

        asset = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"{source_name} · 网格补全" if source_name else "网格补全模型",
            file_path=status.model_file_path,
            status="ready",
            params={"source": "mesh_complete"},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, asset)
        return {"project": project.to_dict(), "status": "success", "asset": asset.to_dict()}

    async def handle_game_assets_mesh_decimate(self, params: dict) -> dict:
        """提交一次拓扑重构：单段流水线，与 2D 转 3D 同构（见
        ``game_item_mesh_ops.submit_mesh_decimate``）。"""
        project = self._require_project(str(params.get("project_id") or "").strip())
        source_asset_id = str(params.get("source_asset_id") or "").strip()
        source = self._require_model_source(project, source_asset_id, action="拓扑重构")

        try:
            task_id = await self._mesh_ops.submit_mesh_decimate(source.file_path)
        except GameItemMeshOpsError as exc:
            raise GameRpcError(exc.code, str(exc)) from exc

        placeholder = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"拓扑重构中…（来自 {source.name}）",
            status="pending",
            params={"source": "mesh_decimate", "job_id": task_id},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, placeholder)
        return {
            "project": project.to_dict(),
            "placeholder_asset_id": placeholder.asset_id,
            "job_id": task_id,
        }

    async def handle_game_assets_mesh_decimate_check_status(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        placeholder_asset_id = str(params.get("placeholder_asset_id") or "").strip()
        job_id = str(params.get("job_id") or "").strip()
        if not (placeholder_asset_id and job_id):
            raise GameRpcError("INVALID_PARAMS", "缺少 placeholder_asset_id / job_id")

        save_dir = str(get_project_assets_dir(project.project_id))
        status = await self._mesh_ops.poll_mesh_decimate(job_id, save_dir=save_dir)

        if status.status == "pending":
            return {"project": project.to_dict(), "status": "pending"}

        source = next((a for a in project.assets if a.asset_id == placeholder_asset_id), None)
        source_asset_id = source.source_asset_id if source else None
        source_name = (
            source.name.removeprefix("拓扑重构中…（来自 ").removesuffix("）") if source else ""
        )
        project = self._store.delete_asset(project.project_id, placeholder_asset_id)

        if status.status == "failed":
            return {"project": project.to_dict(), "status": "failed", "error": status.error}

        asset = GameAsset(
            asset_id=f"gasset_{secrets.token_hex(4)}",
            type="model",
            name=f"{source_name} · 拓扑重构" if source_name else "拓扑重构模型",
            file_path=status.model_file_path,
            status="ready",
            params={"source": "mesh_decimate"},
            source_asset_id=source_asset_id,
        )
        project = self._store.append_asset(project.project_id, asset)
        return {"project": project.to_dict(), "status": "success", "asset": asset.to_dict()}

    # ------------------------------------------------------------------
    # 实验室画布（与导演模式 director.lab_canvas.save 同构）
    # ------------------------------------------------------------------
    async def handle_game_lab_canvas_save(self, params: dict) -> dict:
        project_id = str(params.get("project_id") or "").strip()
        self._require_project(project_id)
        nodes = params.get("nodes")
        edges = params.get("edges")
        project = self._store.save_lab_canvas(
            project_id,
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
        )
        return {"project": project.to_dict()}

    async def handle_game_lab_chat_send(self, params: dict) -> dict:
        """实验室助手：一条用户消息 + 处理卡片工具循环。

        与导演模式 ``director.edit_chat.send`` 同构（无状态直连模型 + tools/
        tool_calls 循环），区别只在工具集：这里只暴露游戏模式的四张处理卡片，
        生成类能力（角色/世界/视频）不在实验室助手的权限内。
        """
        project = self._require_project(str(params.get("project_id") or "").strip())
        text = str(params.get("text") or "").strip()
        if not text:
            raise GameRpcError("INVALID_PARAMS", "消息不能为空")
        if not (edit_chat_enabled() and edit_chat_configured()):
            raise GameRpcError("NOT_CONFIGURED", "实验室助手未配置，请先在设置中配置「剪辑对话」")

        return await self._lab_chat.run_turn(self, project, text)

    # ------------------------------------------------------------------
    # 代码工作区
    # ------------------------------------------------------------------
    def _resolve_inside_project(self, project: GameProject, relative: str) -> Path:
        root = get_project_dir(project.project_id).resolve()
        target = (root / relative).resolve()
        if root != target and root not in target.parents:
            raise GameRpcError("INVALID_PARAMS", "路径必须位于工程目录内")
        return target

    async def handle_game_files_tree(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        root = get_project_dir(project.project_id)
        entries: list[dict[str, Any]] = []
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                if path.suffix.lower() not in _EDITABLE_SUFFIXES:
                    continue
                entries.append({
                    "path": relative,
                    "size": path.stat().st_size,
                    "editable": True,
                })
        return {"files": entries}

    async def handle_game_files_read(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        relative = str(params.get("path") or "").strip()
        if not relative:
            raise GameRpcError("INVALID_PARAMS", "缺少 path")
        target = self._resolve_inside_project(project, relative)
        if not target.is_file():
            raise GameRpcError("NOT_FOUND", f"文件不存在: {relative}")
        if target.suffix.lower() not in _EDITABLE_SUFFIXES:
            raise GameRpcError("INVALID_PARAMS", f"不支持编辑该类型文件: {target.suffix}")
        if target.stat().st_size > _MAX_READ_BYTES:
            raise GameRpcError("TOO_LARGE", "文件超过可编辑体积上限")
        return {"path": relative, "content": target.read_text(encoding="utf-8", errors="replace")}

    async def handle_game_files_write(self, params: dict) -> dict:
        project = self._require_project(str(params.get("project_id") or "").strip())
        relative = str(params.get("path") or "").strip()
        content = params.get("content")
        if not relative or not isinstance(content, str):
            raise GameRpcError("INVALID_PARAMS", "缺少 path 或 content")
        target = self._resolve_inside_project(project, relative)
        if target.suffix.lower() not in _EDITABLE_SUFFIXES:
            raise GameRpcError("INVALID_PARAMS", f"不支持编辑该类型文件: {target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": relative, "bytes": len(content.encode("utf-8"))}

    # ------------------------------------------------------------------
    # 桥接状态
    # ------------------------------------------------------------------
    async def handle_game_bridge_status(self, params: dict) -> dict:
        return {"bridge": await self._bridge.probe(force=bool(params.get("force")))}
