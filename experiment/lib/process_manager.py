# Restarts the jiuwenswarm backend with a given JIUWENSWARM_A2UI_ENABLED value.
#
# `uv run jiuwenswarm-start --stop default` only tears down the top-level
# app_web process it tracks via PID file; the agent_server/gateway process
# tree it spawns is left orphaned (observed repeatedly during manual testing
# of this project). This module cleans up the full tree via psutil before
# starting the next instance, the same way it was done by hand throughout
# development.

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import psutil
import requests


def _jiuwenswarm_start_exe(repo_root: str) -> str:
    # Calls the venv's jiuwenswarm-start.exe directly rather than `uv run
    # jiuwenswarm-start`: this script itself typically runs under
    # `uv run --with playwright ...`, and a nested `uv run` invocation from
    # inside that ephemeral environment was observed to hang indefinitely
    # (likely contending for uv's environment lock with the outer process).
    # The direct exe path sidesteps uv entirely for this inner call.
    return str(Path(repo_root) / ".venv" / "Scripts" / "jiuwenswarm-start.exe")


# Precise markers for the backend's own processes: the venv-installed launcher
# exe, and the `-m jiuwenswarm.<x>` module invocations start_services.py uses.
# NOT a loose "jiuwenswarm" substring match - that also matches this benchmark
# script's own path (.../d--git-projects-jiuwenswarm/...), which meant an
# earlier version of this function found and killed its own running process.
_BACKEND_MODULE_MARKERS = (
    "jiuwenswarm-start.exe",
    "jiuwenswarm.app",
    "jiuwenswarm.channels.web.app_web",
    "jiuwenswarm.server.app_agentserver",
    "jiuwenswarm.gateway.app_gateway",
)


def _find_jiuwenswarm_processes() -> list[psutil.Process]:
    matches = []
    current_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if proc.info.get("pid") == current_pid:
            continue
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cmdline_text = " ".join(str(part) for part in cmdline)
        if any(marker in cmdline_text for marker in _BACKEND_MODULE_MARKERS):
            matches.append(proc)
    return matches


def stop_all(repo_root: str) -> None:
    try:
        subprocess.run(
            [_jiuwenswarm_start_exe(repo_root), "--stop", "default"],
            cwd=repo_root,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    time.sleep(2)
    for proc in _find_jiuwenswarm_processes():
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(2)


def start(repo_root: str, *, a2ui_enabled: bool) -> subprocess.Popen:
    env = os.environ.copy()
    env["JIUWENSWARM_A2UI_ENABLED"] = "1" if a2ui_enabled else "0"
    # Detached so this Python process doesn't inherit stdout/stderr pipes that
    # could fill up and block the long-running backend.
    proc = subprocess.Popen(
        [_jiuwenswarm_start_exe(repo_root)],
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_health(base_url: str, *, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.get(base_url, timeout=3)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def restart_with_a2ui(repo_root: str, *, a2ui_enabled: bool, base_url: str, startup_timeout_seconds: int) -> None:
    stop_all(repo_root)
    start(repo_root, a2ui_enabled=a2ui_enabled)
    if not wait_for_health(base_url, timeout_seconds=startup_timeout_seconds):
        raise RuntimeError(
            f"jiuwenswarm did not become healthy within {startup_timeout_seconds}s "
            f"(a2ui_enabled={a2ui_enabled})"
        )
    # The web frontend can report healthy slightly before the gateway->agent_server
    # tunnel is fully wired (see _wait_for_gateway in app_web.py) - give it a
    # short additional margin before sending real traffic.
    time.sleep(5)
