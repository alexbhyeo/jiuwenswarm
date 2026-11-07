# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Godot 桥接层：MCP 工具代理 + 工程脚手架 + 可用性探测.

设计依据见 ``.agents/agent-docs/designs/proposals/D-20261107-1637-game-mode``：
所有对 Godot 工程的写操作都从这里过，保证可预览、可回退、可审计。

首期（P0）只实现三件可确定的事：

- 探测 Godot 与 MCP 是否可用（结果缓存，避免每次请求都起进程）；
- 生成最小可运行工程（``project.godot`` + 主场景 + 玩家脚本）；
- 代理 MCP 工具调用，链路未就绪时抛结构化错误而不是崩。

真正的工具清单与传输细节依赖 Godot-MCP 运行时，见 5.3 的契约；探测失败时
游戏模式仍可用（项目、素材、代码都会落盘），只是不能驱动编辑器。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Godot-MCP 以编辑器插件形式暴露的工具族（见 README「Tools Reference」）。
GODOT_MCP_TOOLS = (
    "ping",
    "rql-query",
    "rql-mutate",
    "rql-schema",
    "script",
    "screenshot",
    "console",
    "reflection",
    "runtime-errors",
    "session",
    "skills",
)

_PROBE_TTL_SECONDS = 30.0

_PROJECT_GODOT = """\
; Engine configuration file.
; 由 WorkSwarm 游戏工坊生成；可直接用 Godot 4.3+（.NET 版）打开。

config_version=5

[application]

config/name="{name}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.3", "Forward Plus")

[rendering]

renderer/rendering_method="forward_plus"
"""

_MAIN_SCENE = """\
[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://scripts/player.gd" id="1_player"]

[sub_resource type="BoxShape3D" id="BoxShape3D_player"]
size = Vector3(0.8, 1.2, 0.8)

[node name="Main" type="Node3D"]

[node name="Camera3D" type="Camera3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 0.94, 0.34, 0, -0.34, 0.94, 0, 4, 8)

[node name="DirectionalLight3D" type="DirectionalLight3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 0.7, 0.7, 0, -0.7, 0.7, 0, 8, 0)
shadow_enabled = true

[node name="Ground" type="StaticBody3D" parent="."]

[node name="Mesh" type="MeshInstance3D" parent="Ground"]
mesh = SubResource("PlaneMesh_ground")

[node name="Collision" type="CollisionShape3D" parent="Ground"]
shape = SubResource("BoxShape3D_ground")

[node name="Player" type="CharacterBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0)
script = ExtResource("1_player")

[node name="Collision" type="CollisionShape3D" parent="Player"]
shape = SubResource("BoxShape3D_player")
"""

_PLAYER_SCRIPT = """\
extends CharacterBody3D

## 最小可玩角色：WASD/方向键移动，空格跳跃。

@export var move_speed: float = 6.0
@export var jump_force: float = 5.5
@export var gravity: float = ProjectSettings.get_setting("physics/3d/default_gravity")

var velocity: Vector3 = Vector3.ZERO


func _physics_process(delta: float) -> void:
\t_apply_gravity(delta)
\t_handle_movement(delta)
\t_handle_jump()
\tmove_and_slide()


func _apply_gravity(delta: float) -> void:
\tif not is_on_floor():
\t\tvelocity.y -= gravity * delta


func _handle_movement(_delta: float) -> void:
\tvar input := Vector3.ZERO
\tinput.x = Input.get_axis("ui_left", "ui_right")
\tinput.z = Input.get_axis("ui_up", "ui_down")
\tvelocity.x = input.x * move_speed
\tvelocity.z = input.z * move_speed


func _handle_jump() -> void:
\tif Input.is_action_just_pressed("ui_accept") and is_on_floor():
\t\tvelocity.y = jump_force
"""


class GodotUnavailable(RuntimeError):
    """Godot 或 MCP 链路当前不可用；调用方据此降级而不是报错崩溃."""

    code = "GODOT_UNAVAILABLE"


class GodotBridge:
    """Godot 工程脚手架与 MCP 工具代理."""

    def __init__(self) -> None:
        self._probe_cache: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------------
    def _find_godot_binary(self) -> str | None:
        for name in (os.getenv("GODOT_BIN", "").strip(), "godot", "godot4", "Godot"):
            if not name:
                continue
            found = shutil.which(name) if not os.path.isabs(name) else name
            if found and Path(found).is_file():
                return found
        return None

    async def probe(self, *, force: bool = False) -> dict[str, Any]:
        """探测 Godot 与 MCP 可用性；失败也返回结构，不抛异常."""
        now = time.monotonic()
        if not force and self._probe_cache and now - self._probe_cache[0] < _PROBE_TTL_SECONDS:
            return dict(self._probe_cache[1])

        binary = self._find_godot_binary()
        version = ""
        if binary:
            try:
                proc = await asyncio.create_subprocess_exec(
                    binary, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                version = out.decode("utf-8", "replace").strip().splitlines()[:1] and \
                    out.decode("utf-8", "replace").strip().splitlines()[0]
            except (OSError, asyncio.TimeoutError) as exc:
                logger.warning("[game] 探测 Godot 版本失败: %s", exc)

        # mono 构建才带 C#，Godot-MCP 的插件需要它。
        dotnet_binary = shutil.which("dotnet") or ""
        state = {
            "godot": bool(binary),
            "godot_binary": binary or "",
            "godot_version": version,
            "godot_mono": "mono" in version.lower(),
            "dotnet": bool(dotnet_binary),
            "dotnet_version": "",
            "mcp": bool(os.getenv("GODOT_MCP_URL", "").strip()
                        or os.getenv("GODOT_MCP_COMMAND", "").strip()),
            "tools": list(GODOT_MCP_TOOLS),
        }
        if dotnet_binary:
            try:
                proc = await asyncio.create_subprocess_exec(
                    dotnet_binary, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                state["dotnet_version"] = out.decode("utf-8", "replace").strip().splitlines()[0]
            except (OSError, asyncio.TimeoutError, IndexError) as exc:
                logger.warning("[game] 探测 .NET 版本失败: %s", exc)

        state["ready"] = bool(state["godot"] and state["godot_mono"] and state["dotnet"])
        self._probe_cache = (now, state)
        return dict(state)

    # ------------------------------------------------------------------
    # 脚手架
    # ------------------------------------------------------------------
    def scaffold(self, project_dir: Path, name: str) -> list[str]:
        """生成最小可运行工程；已存在的文件不覆盖，返回本次新建的相对路径."""
        created: list[str] = []
        files = {
            "project.godot": _PROJECT_GODOT.format(name=name),
            "scenes/main.tscn": _MAIN_SCENE,
            "scripts/player.gd": _PLAYER_SCRIPT,
        }
        for relative, content in files.items():
            target = project_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                continue
            target.write_text(content, encoding="utf-8")
            created.append(relative)
        for directory in ("assets", "addons"):
            (project_dir / directory).mkdir(parents=True, exist_ok=True)
        logger.info("[game] 脚手架完成 %s，新建 %d 个文件", project_dir.name, len(created))
        return created

    # ------------------------------------------------------------------
    # MCP 代理
    # ------------------------------------------------------------------
    async def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 Godot-MCP 工具；链路未配置时抛 ``GodotUnavailable``."""
        url = os.getenv("GODOT_MCP_URL", "").strip()
        command = os.getenv("GODOT_MCP_COMMAND", "").strip()
        if not (url or command):
            raise GodotUnavailable(
                "Godot MCP 未配置：请设置 GODOT_MCP_URL 或 GODOT_MCP_COMMAND"
                "（参考 Godot-MCP README 的 Connect 章节）"
            )
        if not url:
            raise GodotUnavailable(
                "Godot MCP 仅配置了 GODOT_MCP_COMMAND（stdio 传输）；"
                "当前实现走 HTTP JSON-RPC，请改配 GODOT_MCP_URL"
            )
        import urllib.request

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}},
        }

        def _post() -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            result = await asyncio.to_thread(_post)
        except Exception as exc:  # noqa: BLE001 - 传输失败同样走降级
            raise GodotUnavailable(f"Godot MCP 调用失败（{tool}）：{exc}") from exc
        if isinstance(result, dict) and result.get("error"):
            raise GodotUnavailable(f"Godot MCP 返回错误（{tool}）：{result['error']}")
        return result

    @staticmethod
    def tool_catalog() -> list[dict[str, str]]:
        """给前端「技能/工具」面板用的静态清单."""
        return [{"name": name} for name in GODOT_MCP_TOOLS]
