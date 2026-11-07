# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""游戏模式的项目与素材持久化.

与导演模式的 ``DirectorStore`` 同构：单文件 JSON 状态 + 进程内可重入锁，
读改写都在锁内完成。不做跨进程加锁（HTTP 上传那类线程化入口另有锁，见
``director_multipart_http`` 的取舍说明）。
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import get_agent_root_dir

logger = logging.getLogger(__name__)

GAME_ASSET_TYPES = ("character", "prop", "scene", "model", "texture", "audio", "animation")
PROJECT_STATUSES = ("draft", "scaffolded", "building", "ready", "failed")


def _get_game_dir() -> Path:
    return get_agent_root_dir() / "game"


def get_game_projects_dir() -> Path:
    """所有游戏工程的根目录（每个项目一个子目录）."""
    return _get_game_dir() / "projects"


def get_project_dir(project_id: str) -> Path:
    """项目自己的工程目录；只算路径，不创建."""
    return get_game_projects_dir() / project_id


def get_project_assets_dir(project_id: str) -> Path:
    """项目素材目录（生成工具的 save_dir 落点），按需创建."""
    path = get_project_dir(project_id) / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_file() -> Path:
    return _get_game_dir() / "game_state.json"


@dataclass
class GameAsset:
    asset_id: str
    type: str
    name: str
    file_path: str | None = None
    status: str = "ready"
    params: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "type": self.type,
            "name": self.name,
            "file_path": self.file_path,
            "status": self.status,
            "params": dict(self.params),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameAsset":
        return cls(
            asset_id=str(data.get("asset_id") or ""),
            type=str(data.get("type") or "prop"),
            name=str(data.get("name") or ""),
            file_path=(str(data["file_path"]) if data.get("file_path") else None),
            status=str(data.get("status") or "ready"),
            params=dict(data["params"]) if isinstance(data.get("params"), dict) else {},
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class GameProject:
    project_id: str
    name: str
    engine: str = "godot"
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    assets: list[GameAsset] = field(default_factory=list)
    build: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def root_dir(self) -> Path:
        return get_project_dir(self.project_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "engine": self.engine,
            "status": self.status,
            "root_dir": str(self.root_dir),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assets": [asset.to_dict() for asset in self.assets],
            "build": dict(self.build),
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameProject":
        raw_assets = data.get("assets")
        return cls(
            project_id=str(data.get("project_id") or ""),
            name=str(data.get("name") or ""),
            engine=str(data.get("engine") or "godot"),
            status=str(data.get("status") or "draft"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            assets=[
                GameAsset.from_dict(item)
                for item in (raw_assets if isinstance(raw_assets, list) else [])
                if isinstance(item, dict)
            ],
            build=dict(data["build"]) if isinstance(data.get("build"), dict) else {},
            params=dict(data["params"]) if isinstance(data.get("params"), dict) else {},
        )


class GameStore:
    """游戏项目的读改写，所有变更都在同一把可重入锁内完成."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def _load(self) -> dict[str, GameProject]:
        path = _state_file()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("[game] 状态文件损坏，按空状态处理: %s", path)
            return {}
        raw = data.get("projects") if isinstance(data, dict) else None
        projects: dict[str, GameProject] = {}
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("project_id"):
                project = GameProject.from_dict(item)
                projects[project.project_id] = project
        return projects

    def _save(self, projects: dict[str, GameProject]) -> None:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"projects": [p.to_dict() for p in projects.values()]}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_projects(self) -> list[GameProject]:
        with self._lock:
            projects = list(self._load().values())
        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def get_project(self, project_id: str) -> GameProject | None:
        with self._lock:
            return self._load().get(project_id)

    def create_project(self, name: str) -> GameProject:
        with self._lock:
            projects = self._load()
            project = GameProject(
                project_id=f"gproj_{secrets.token_hex(4)}",
                name=name.strip() or "未命名游戏",
            )
            get_project_dir(project.project_id).mkdir(parents=True, exist_ok=True, mode=0o700)
            get_project_assets_dir(project.project_id)
            projects[project.project_id] = project
            self._save(projects)
            return project

    def update_project(self, project_id: str, **fields: Any) -> GameProject:
        with self._lock:
            projects = self._load()
            project = projects.get(project_id)
            if project is None:
                raise KeyError(f"未找到项目: {project_id}")
            for key, value in fields.items():
                if not hasattr(project, key):
                    raise ValueError(f"未知字段: {key}")
                setattr(project, key, value)
            project.updated_at = time.time()
            self._save(projects)
            return project

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            projects = self._load()
            if project_id not in projects:
                raise KeyError(f"未找到项目: {project_id}")
            del projects[project_id]
            self._save(projects)

    def append_asset(self, project_id: str, asset: GameAsset) -> GameProject:
        with self._lock:
            projects = self._load()
            project = projects.get(project_id)
            if project is None:
                raise KeyError(f"未找到项目: {project_id}")
            project.assets = [a for a in project.assets if a.asset_id != asset.asset_id]
            project.assets.append(asset)
            project.updated_at = time.time()
            self._save(projects)
            return project

    def delete_asset(self, project_id: str, asset_id: str) -> GameProject:
        with self._lock:
            projects = self._load()
            project = projects.get(project_id)
            if project is None:
                raise KeyError(f"未找到项目: {project_id}")
            project.assets = [a for a in project.assets if a.asset_id != asset_id]
            project.updated_at = time.time()
            self._save(projects)
            return project
