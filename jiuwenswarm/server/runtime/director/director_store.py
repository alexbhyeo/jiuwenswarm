# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Director Mode 项目/素材持久化.

单进程内的简单全量 JSON 读写存储，参照 skill_manager.py 的
_load_state/_save_state 模式：单用户桌面进程，与 SkillManager（同样零文件锁、
每进程构造一次）相同的信任/并发画像，无需 project_store.py 那种跨进程文件锁。
并发写入（同一进程内多个 RPC 同时到达）由 DirectorManager 持有的
asyncio.Lock 串行化，这里不做任何锁处理。
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import get_agent_root_dir

logger = logging.getLogger(__name__)

_STATE_VERSION = 1


@dataclass
class DirectorAsset:
    asset_id: str
    type: str  # "video" | "image"
    status: str  # "ready" | "pending" | "failed"
    prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    file_path: str | None = None
    job_id: str | None = None
    error: str | None = None
    # 用户自定义素材名——为空时前端回退展示 prompt。图片素材命名后可在同一
    # 项目内通过composer 提示词里的 "@名称" 引用，作为 generate_visual 的
    # reference_image_path（见 director_manager._resolve_at_reference）。
    name: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DirectorAsset":
        return DirectorAsset(
            asset_id=str(data.get("asset_id") or ""),
            type=str(data.get("type") or ""),
            status=str(data.get("status") or "pending"),
            prompt=str(data.get("prompt") or ""),
            params=dict(data.get("params") or {}),
            file_path=data.get("file_path"),
            job_id=data.get("job_id"),
            error=data.get("error"),
            name=data.get("name"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


@dataclass
class DirectorProject:
    project_id: str
    name: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    assets: list[DirectorAsset] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assets": [asset.to_dict() for asset in self.assets],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DirectorProject":
        raw_assets = data.get("assets")
        assets = (
            [
                DirectorAsset.from_dict(item)
                for item in raw_assets
                if isinstance(item, dict)
            ]
            if isinstance(raw_assets, list)
            else []
        )
        return DirectorProject(
            project_id=str(data.get("project_id") or ""),
            name=str(data.get("name") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            assets=assets,
        )


def _get_director_dir() -> Path:
    return get_agent_root_dir() / "director"


def _get_state_file() -> Path:
    return _get_director_dir() / "director_state.json"


def get_project_assets_dir(project_id: str) -> Path:
    """generate_video/generate_visual 的 save_dir 落点.

    按项目隔离产物，不混入共享的 generated_videos/ · generated_images/。
    """
    path = _get_director_dir() / "projects" / project_id / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


class DirectorStore:
    """Director 项目集合的简单全量 JSON 存储（见模块 docstring 的并发说明）."""

    def __init__(self) -> None:
        self._state_file = _get_state_file()
        self._projects: dict[str, DirectorProject] = self._load()

    def _load(self) -> dict[str, DirectorProject]:
        try:
            if self._state_file.exists():
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                items = raw.get("projects") if isinstance(raw, dict) else None
                if isinstance(items, list):
                    projects = [
                        DirectorProject.from_dict(item)
                        for item in items
                        if isinstance(item, dict)
                    ]
                    return {p.project_id: p for p in projects if p.project_id}
        except Exception:
            logger.exception("[DirectorStore] 加载 director_state.json 失败，使用空状态")
        return {}

    def _save(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _STATE_VERSION,
                "projects": [p.to_dict() for p in self._projects.values()],
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("[DirectorStore] 保存 director_state.json 失败")

    def list_projects(self) -> list[DirectorProject]:
        return sorted(self._projects.values(), key=lambda p: p.updated_at, reverse=True)

    def get_project(self, project_id: str) -> DirectorProject | None:
        return self._projects.get(project_id)

    def create_project(self, name: str) -> DirectorProject:
        now = time.time()
        project = DirectorProject(
            project_id=f"dproj_{secrets.token_hex(4)}",
            name=name.strip() or "未命名项目",
            created_at=now,
            updated_at=now,
        )
        self._projects[project.project_id] = project
        self._save()
        return project

    def append_asset(self, project_id: str, asset: DirectorAsset) -> DirectorProject:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.assets.append(asset)
        project.updated_at = time.time()
        self._save()
        return project

    def update_asset(self, project_id: str, asset_id: str, **patch: Any) -> DirectorProject:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        for asset in project.assets:
            if asset.asset_id == asset_id:
                for key, value in patch.items():
                    if hasattr(asset, key):
                        setattr(asset, key, value)
                asset.updated_at = time.time()
                break
        project.updated_at = time.time()
        self._save()
        return project

    def delete_asset(self, project_id: str, asset_id: str) -> DirectorProject:
        """从项目里移除该素材的元数据记录（不含磁盘文件删除——调用方
        （DirectorManager）在拿到被删素材的 file_path 后自行处理，
        以保持本方法单一职责：只管 director_state.json 的一致性）。
        """
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.assets = [a for a in project.assets if a.asset_id != asset_id]
        project.updated_at = time.time()
        self._save()
        return project

    def find_asset_by_name(self, project_id: str, name: str) -> DirectorAsset | None:
        """大小写不敏感精确匹配；同名时取 updated_at 最新的一个.

        供 "@名称" 引用解析使用（见 director_manager._resolve_at_reference）。
        """
        project = self._projects.get(project_id)
        if project is None:
            return None
        target = name.strip().lower()
        if not target:
            return None
        matches = [a for a in project.assets if a.name and a.name.strip().lower() == target]
        if not matches:
            return None
        return max(matches, key=lambda a: a.updated_at)

    def asset_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {"video": 0, "image": 0}
        for project in self._projects.values():
            for asset in project.assets:
                if asset.type in counts:
                    counts[asset.type] += 1
        return counts
