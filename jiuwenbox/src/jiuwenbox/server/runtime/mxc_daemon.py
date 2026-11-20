# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Long-lived daemon launched *inside* an MXC processcontainer sandbox.

This file has no jiuwenbox imports -- it runs as a plain script inside the
sandbox, started by a single one-shot MXC create-and-run request
(MxcRuntime.create() in mxc.py). It polls its own workspace directory, the
one path the sandbox's filesystem policy grants readwritePaths to, for
request files, and writes result files back. The workspace directory is the
IPC channel: no named pipe or socket crosses the sandbox boundary.

Request files: cmd-<id>.json, one of:
  {"op": "exec", "command": str, "workdir": str|None, "env": dict|None,
   "timeout": float}
      -> writes result-<id>.json: {"exit_code", "stdout", "stderr"}
  {"op": "exec_background", "command": str, "job_id": str,
   "workdir": str|None, "env": dict|None}
      -> starts the command without waiting, writes result-<id>.json:
         {"started": bool, "pid": int|None, "error": str|None} immediately,
         and maintains job-<job_id>.json for the life of the job (see below)
  {"op": "bg_kill", "job_id": str, "signal": int}
      -> writes result-<id>.json: {"killed": bool, "reason": str}

job-<job_id>.json (written by exec_background, updated on completion):
  {"job_id", "pid", "command", "running", "exit_code", "started_at",
   "finished_at"}

Mirrors what jiuwenbox's server/runtime/process.py does for bubblewrap on
Linux (one-shot sandbox launch bootstraps a daemon; everything else talks to
that daemon directly) -- see the D-20261120-1025-mxc-process-isolation
design proposal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

POLL_INTERVAL_SECONDS = 0.05
IDLE_TIMEOUT_SECONDS = 1800.0

_jobs_lock = threading.Lock()
_running_procs: dict[str, subprocess.Popen] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _run_foreground(request: dict, workdir: Path) -> dict:
    command = request["command"]
    requested_cwd = request.get("workdir")
    cwd = requested_cwd or str(workdir)
    env = dict(os.environ)
    env.update(request.get("env") or {})
    timeout = request.get("timeout", 30)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": -1, "stdout": exc.stdout or "", "stderr": f"timed out after {timeout}s"}
    except OSError as exc:
        # The requested workdir may be outside this sandbox's filesystem
        # grant (e.g. a real project directory the policy didn't bind-mount
        # for this backend) -- that surfaces as WinError 267 ("The directory
        # name is invalid"), not a permission error, because the sandboxed
        # process cannot resolve the path at all. Retry in the session's own
        # (always-granted) workspace rather than failing the whole command.
        if requested_cwd and cwd != str(workdir):
            try:
                proc = subprocess.run(
                    command, shell=True, cwd=str(workdir), env=env,
                    capture_output=True, text=True, timeout=timeout,
                )
                note = f"[mxc daemon: requested workdir {requested_cwd!r} was not accessible; ran in {workdir} instead]\n"
                return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": note + proc.stderr}
            except Exception as exc2:  # noqa: BLE001
                return {"exit_code": -1, "stdout": "", "stderr": f"{exc} (fallback also failed: {exc2})"}
        return {"exit_code": -1, "stdout": "", "stderr": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": -1, "stdout": "", "stderr": str(exc)}


def _start_background(request: dict, workdir: Path) -> dict:
    command = request["command"]
    job_id = request["job_id"]
    cwd = request.get("workdir") or str(workdir)
    env = dict(os.environ)
    env.update(request.get("env") or {})
    job_file = workdir / f"job-{job_id}.json"

    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return {"started": False, "pid": None, "error": str(exc)}

    with _jobs_lock:
        _running_procs[job_id] = proc

    _write_json(job_file, {
        "job_id": job_id, "pid": proc.pid, "command": command,
        "running": True, "exit_code": None,
        "started_at": _now_iso(), "finished_at": None,
    })

    def _watch() -> None:
        exit_code = proc.wait()
        with _jobs_lock:
            _running_procs.pop(job_id, None)
        _write_json(job_file, {
            "job_id": job_id, "pid": proc.pid, "command": command,
            "running": False, "exit_code": exit_code,
            "started_at": _now_iso(), "finished_at": _now_iso(),
        })

    threading.Thread(target=_watch, daemon=True).start()
    return {"started": True, "pid": proc.pid, "error": None}


def _kill_job(request: dict) -> dict:
    job_id = request["job_id"]
    with _jobs_lock:
        proc = _running_procs.get(job_id)
    if proc is None or proc.poll() is not None:
        return {"killed": False, "reason": "not_running"}
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=10,
        )
        return {"killed": True, "reason": "killed"}
    except Exception as exc:  # noqa: BLE001
        return {"killed": False, "reason": str(exc)}


def _dispatch(request: dict, workdir: Path) -> dict:
    op = request.get("op", "exec")
    if op == "exec":
        return _run_foreground(request, workdir)
    if op == "exec_background":
        return _start_background(request, workdir)
    if op == "bg_kill":
        return _kill_job(request)
    return {"exit_code": -1, "stdout": "", "stderr": f"unknown op: {op!r}"}


def main() -> None:
    workdir = Path(sys.argv[1])
    stop_file = workdir / "shutdown.signal"
    ready_file = workdir / "daemon.ready"
    ready_file.write_text(str(os.getpid()), encoding="utf-8")

    last_activity = time.monotonic()
    while not stop_file.exists():
        pending = sorted(workdir.glob("cmd-*.json"))
        if pending:
            last_activity = time.monotonic()
        for cmd_file in pending:
            req_id = cmd_file.stem.split("-", 1)[1]
            try:
                request = json.loads(cmd_file.read_text(encoding="utf-8"))
                result = _dispatch(request, workdir)
            except Exception as exc:  # noqa: BLE001
                result = {"exit_code": -1, "stdout": "", "stderr": f"daemon error: {exc}"}
            _write_json(workdir / f"result-{req_id}.json", result)
            cmd_file.unlink(missing_ok=True)
        if time.monotonic() - last_activity > IDLE_TIMEOUT_SECONDS:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    stop_file.unlink(missing_ok=True)
    ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
