# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Director Mode 项目/素材持久化.

单进程内的简单全量 JSON 读写存储，参照 skill_manager.py 的
_load_state/_save_state 模式：单用户桌面进程，与 SkillManager 相同的信任/
并发画像，无需 project_store.py 那种跨进程文件锁。

每次公开方法调用都会重新从磁盘 _load()，不在 __init__ 里缓存一份长期持有的
`self._projects`——这不是性能考量，而是修复一个真实 bug：director.* 的 WS
RPC 都经由 DirectorManager 一个常驻（按 channel 缓存）实例处理，而
`/file-api/director/upload` 走独立的 HTTP handler，每次请求都会 new 一个
DirectorStore()。如果 DirectorStore 在构造时缓存内存快照，DirectorManager
那个常驻实例的快照就会与上传接口刚写盘的内容永久不同步——上传的素材能显示
（因为 projects.list 走的还是同一份陈旧内存），但对它 rename/delete 会因为
"就是找不到这个 asset_id"而失败，因为常驻实例的内存里压根没有这条记录。
文件很小、单用户，全量重读的开销可以忽略，用"永远读当前磁盘状态"换掉"内存
缓存分歧"更稳。并发写入仍由 DirectorManager 的 asyncio.Lock（WS 路径）和
director_multipart_http 的 threading.Lock（上传路径）分别串行化。
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
    type: str  # "video" | "image" | "character"
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
class EditChatMessage:
    """剪辑 tab 对话助手的一轮消息（用户或助手）.

    与 DirectorAsset 同样存于整份 director_state.json 里，不单开存储——
    对话历史本来就是这个项目的一部分数据，没必要再引入一种新的持久化方式。
    """

    role: str  # "user" | "assistant"
    content: str
    image_asset_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "EditChatMessage":
        raw_ids = data.get("image_asset_ids")
        return EditChatMessage(
            role=str(data.get("role") or "user"),
            content=str(data.get("content") or ""),
            image_asset_ids=[str(x) for x in raw_ids] if isinstance(raw_ids, list) else [],
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class DirectorProject:
    project_id: str
    name: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    assets: list[DirectorAsset] = field(default_factory=list)
    edit_chat_messages: list[EditChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assets": [asset.to_dict() for asset in self.assets],
            "edit_chat_messages": [msg.to_dict() for msg in self.edit_chat_messages],
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
        raw_messages = data.get("edit_chat_messages")
        edit_chat_messages = (
            [
                EditChatMessage.from_dict(item)
                for item in raw_messages
                if isinstance(item, dict)
            ]
            if isinstance(raw_messages, list)
            else []
        )
        return DirectorProject(
            project_id=str(data.get("project_id") or ""),
            name=str(data.get("name") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            assets=assets,
            edit_chat_messages=edit_chat_messages,
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
    """Director 项目集合的简单全量 JSON 存储（见模块 docstring 的并发说明）.

    每个公开方法都自成一次完整的 load → (改) → save 往返，不持有任何跨调用
    的内存状态；`DirectorStore()` 因此可以随意多次构造而不必担心快照分歧。
    """

    def __init__(self) -> None:
        self._state_file = _get_state_file()

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

    def _save(self, projects: dict[str, DirectorProject]) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _STATE_VERSION,
                "projects": [p.to_dict() for p in projects.values()],
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("[DirectorStore] 保存 director_state.json 失败")

    def list_projects(self) -> list[DirectorProject]:
        projects = self._load()
        return sorted(projects.values(), key=lambda p: p.updated_at, reverse=True)

    def get_project(self, project_id: str) -> DirectorProject | None:
        return self._load().get(project_id)

    def create_project(self, name: str) -> DirectorProject:
        projects = self._load()
        now = time.time()
        project = DirectorProject(
            project_id=f"dproj_{secrets.token_hex(4)}",
            name=name.strip() or "未命名项目",
            created_at=now,
            updated_at=now,
        )
        projects[project.project_id] = project
        self._save(projects)
        return project

    def append_asset(self, project_id: str, asset: DirectorAsset) -> DirectorProject:
        projects = self._load()
        project = projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.assets.append(asset)
        project.updated_at = time.time()
        self._save(projects)
        return project

    def append_edit_chat_messages(
        self, project_id: str, messages: list[EditChatMessage]
    ) -> DirectorProject:
        """一次性追加多条消息（典型调用：一条用户消息 + 一条助手回复），
        只落一次盘——避免用户消息和助手回复分两次写导致中间状态被
        并发读到，也少一次磁盘往返。
        """
        projects = self._load()
        project = projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.edit_chat_messages.extend(messages)
        project.updated_at = time.time()
        self._save(projects)
        return project

    def update_asset(self, project_id: str, asset_id: str, **patch: Any) -> DirectorProject:
        projects = self._load()
        project = projects.get(project_id)
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
        self._save(projects)
        return project

    def delete_asset(self, project_id: str, asset_id: str) -> DirectorProject:
        """从项目里移除该素材的元数据记录（不含磁盘文件删除——调用方
        （DirectorManager）在拿到被删素材的 file_path 后自行处理，
        以保持本方法单一职责：只管 director_state.json 的一致性）。
        """
        projects = self._load()
        project = projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.assets = [a for a in project.assets if a.asset_id != asset_id]
        project.updated_at = time.time()
        self._save(projects)
        return project

    def find_asset_by_name(self, project_id: str, name: str) -> DirectorAsset | None:
        """大小写不敏感精确匹配；同名时取 updated_at 最新的一个.

        供 "@名称" 引用解析使用（见 director_manager._resolve_at_reference）。
        """
        project = self._load().get(project_id)
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
        counts: dict[str, int] = {"video": 0, "image": 0, "character": 0}
        for project in self._load().values():
            for asset in project.assets:
                if asset.type in counts:
                    counts[asset.type] += 1
        return counts
