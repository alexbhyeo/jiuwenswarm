# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""运行与日志：把 Godot 进程的输出接进「日志」面板.

设计里运行日志的首选来源是 Godot-MCP 的 ``console`` / ``runtime-errors`` 两个
工具；但它们需要 C# 版 Godot + .NET SDK + MCP server 三件套都在位。这里用
**进程直跑 + 输出捕获**作为等价实现：标准版 Godot 就能起来，输出、报错、退出码
全部进环形缓冲，前端轮询即可。接上 MCP 后两者可以并存——MCP 负责"在编辑器里
看到什么"，这里负责"跑起来发生了什么"。

进程生命周期：一个项目同时只允许一个运行实例；进程退出后状态变
``exited``，日志保留供复查；``stop`` 走 terminate → 超时 kill。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_LOG_LINES = 2000
_STOP_GRACE_SECONDS = 5.0


@dataclass
class GameRun:
    project_id: str
    process: asyncio.subprocess.Process | None = None
    started_at: float = field(default_factory=time.time)
    status: str = "starting"
    exit_code: int | None = None
    headless: bool = True
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_LOG_LINES))
    reader: asyncio.Task | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "headless": self.headless,
            "started_at": self.started_at,
            "uptime": (time.time() - self.started_at) if self.status == "running" else 0.0,
            "lines": len(self.lines),
        }


class GameRunner:
    """按项目维护运行实例与日志缓冲."""

    def __init__(self) -> None:
        self._runs: dict[str, GameRun] = {}

    # ------------------------------------------------------------------
    def find_godot(self) -> str | None:
        for name in (os.getenv("GODOT_BIN", "").strip(), "godot", "godot4", "Godot",
                     "/Applications/Godot.app/Contents/MacOS/Godot"):
            if not name:
                continue
            found = name if os.path.isabs(name) else shutil.which(name)
            if found and Path(found).exists():
                return found
        return None

    def _run(self, project_id: str) -> GameRun:
        run = self._runs.get(project_id)
        if run is None:
            run = GameRun(project_id=project_id)
            self._runs[project_id] = run
        return run

    # ------------------------------------------------------------------
    async def start(self, project_id: str, root: Path, *, headless: bool = True,
                    extra_args: list[str] | None = None) -> dict[str, Any]:
        existing = self._runs.get(project_id)
        if existing and existing.status == "running":
            return existing.to_dict()

        binary = self.find_godot()
        if not binary:
            raise RuntimeError("未找到 Godot 可执行文件；请设置 GODOT_BIN")

        run = self._run(project_id)
        run.lines.clear()
        run.started_at = time.time()
        run.exit_code = None
        run.headless = headless
        run.status = "starting"

        argv = [binary, "--path", str(root)]
        if headless:
            argv.append("--headless")
        argv.extend(extra_args or [])

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
            )
        except OSError as exc:
            run.status = "failed"
            run.lines.append(f"[runner] 启动失败: {exc}")
            raise RuntimeError(f"启动 Godot 失败: {exc}") from exc

        run.process = process
        run.status = "running"
        run.lines.append(f"[runner] {Path(binary).name} pid={process.pid} headless={headless}")
        run.reader = asyncio.create_task(self._pump(run))
        logger.info("[game] 运行项目 %s (pid=%s headless=%s)", project_id, process.pid, headless)
        return run.to_dict()

    async def _pump(self, run: GameRun) -> None:
        process = run.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                run.lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            run.lines.append(f"[runner] 读取输出失败: {exc}")
        await process.wait()
        run.exit_code = process.returncode
        run.status = "exited"
        run.lines.append(f"[runner] 进程退出，code={process.returncode}")

    async def stop(self, project_id: str) -> dict[str, Any]:
        run = self._runs.get(project_id)
        if run is None or run.process is None or run.status != "running":
            return (run.to_dict() if run else {"project_id": project_id, "status": "idle"})
        process = run.process
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        run.status = "exited"
        run.exit_code = process.returncode
        return run.to_dict()

    def logs(self, project_id: str, *, since: int = 0) -> dict[str, Any]:
        run = self._runs.get(project_id)
        if run is None:
            return {"state": {"project_id": project_id, "status": "idle"}, "lines": [], "next": 0}
        lines = list(run.lines)
        start = max(0, min(since, len(lines)))
        return {
            "state": run.to_dict(),
            "lines": lines[start:],
            "next": len(lines),
        }

    def state(self, project_id: str) -> dict[str, Any]:
        run = self._runs.get(project_id)
        return run.to_dict() if run else {"project_id": project_id, "status": "idle"}

    async def stop_all(self) -> None:
        for project_id in list(self._runs):
            await self.stop(project_id)
