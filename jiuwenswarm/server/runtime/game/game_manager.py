# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""游戏模式的 RPC 处理器（``game.*``）.

与 ``director_manager.DirectorManager`` 同构：一个方法一个处理器，返回可直接
序列化的 dict。这里只做 P0 范围（设计 D-20261107-1637 第 8 节）：项目生命周期、
脚手架、素材登记、文件工作区读写、桥接状态。
"""

from __future__ import annotations

import logging
import secrets
import shutil
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.game.game_store import (
    GAME_ASSET_TYPES,
    GameAsset,
    GameProject,
    GameStore,
    get_project_assets_dir,
    get_project_dir,
)
from jiuwenswarm.server.runtime.game.godot_bridge import GodotBridge
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
        """用 ``generate_visual`` 生成一张游戏素材并登记到项目.

        与导演模式的生成卡同理：参数已由前端完全确定，再走一次 LLM 回合只会
        增加延迟与不确定性，所以这里直接调工具函数。
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

        from jiuwenswarm.agents.harness.common.tools.visual_gen_tools import generate_visual
        from jiuwenswarm.server.runtime.director.director_manager import (
            _parse_generation_result,
        )

        save_dir = str(get_project_assets_dir(project.project_id))
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
        if not any(a.asset_id == asset_id for a in project.assets):
            raise GameRpcError("ASSET_NOT_FOUND", f"未找到素材: {asset_id}")
        return {"project": self._store.delete_asset(project.project_id, asset_id).to_dict()}

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
