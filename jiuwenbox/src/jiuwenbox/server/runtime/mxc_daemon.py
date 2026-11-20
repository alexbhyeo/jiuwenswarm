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
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

POLL_INTERVAL_SECONDS = 0.005
IDLE_TIMEOUT_SECONDS = 1800.0

_jobs_lock = threading.Lock()
_running_procs: dict[str, subprocess.Popen] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


_CMD_WRAPPER_RE = re.compile(r'^cmd /c "(.*)"$')
_shell_lock = threading.Lock()
_shell_proc: subprocess.Popen | None = None
_shell_queue: "queue.Queue[str]" = queue.Queue()
_SHELL_SENTINEL_PREFIX = "MXC_PERSISTENT_SHELL_DONE_"


def _shell_reader(proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        _shell_queue.put(line)
    _shell_queue.put("__MXC_SHELL_READER_EOF__\n")


def _get_persistent_shell(workdir: Path) -> subprocess.Popen | None:
    """Lazily spawn (or respawn, if dead) the one persistent cmd.exe this
    daemon reuses for simple foreground commands. Returns None if it could
    not be started; callers fall back to a fresh per-command spawn."""
    global _shell_proc
    with _shell_lock:
        if _shell_proc is not None and _shell_proc.poll() is None:
            return _shell_proc
        try:
            proc = subprocess.Popen(
                ["cmd.exe", "/Q"], cwd=str(workdir), env=dict(os.environ),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except Exception:  # noqa: BLE001
            _shell_proc = None
            return None
        threading.Thread(target=_shell_reader, args=(proc,), daemon=True).start()
        # /Q disables echoing the *command text*, but the prompt itself
        # (PROMPT env var set to "" does not blank it -- cmd falls back to
        # its $P$G default) still prints before each command's output, and
        # the version banner is printed once at startup. Setting the prompt
        # to a single newline (`$_`) via the `prompt` *command* (not the env
        # var) makes every prompt occurrence exactly one blank line, which
        # _run_in_persistent_shell strips as a fixed, known prefix. Drain
        # everything up to and including the warm-up sentinel, discarding
        # the banner and the prompt-change command's own leftover prompt.
        token = uuid.uuid4().hex
        try:
            proc.stdin.write("prompt $_\r\n")
            proc.stdin.write(f"echo {_SHELL_SENTINEL_PREFIX}{token}_0\r\n")
            proc.stdin.flush()
            deadline = time.monotonic() + 5.0
            marker = f"{_SHELL_SENTINEL_PREFIX}{token}_0"
            while time.monotonic() < deadline:
                line = _shell_queue.get(timeout=max(0.0, deadline - time.monotonic()))
                if marker in line:
                    break
        except Exception:  # noqa: BLE001
            proc.kill()
            _shell_proc = None
            return None
        _shell_proc = proc
        return _shell_proc


def _run_in_persistent_shell(command: str, timeout: float, workdir: Path) -> dict | None:
    """Best-effort fast path: pipe ``command`` into the one long-lived
    cmd.exe this daemon keeps alive instead of spawning a fresh process.
    Only used when the caller did not ask for a specific workdir/env (those
    would require 'cd'/'set' commands that persist across later, unrelated
    exec calls on the same shell -- not worth the state-leak risk for the
    latency win). stdout and stderr are merged (stderr=STDOUT at spawn
    time), so the returned "stderr" is always empty; this is a deliberate
    trade documented at the call site, not a bug.
    Returns None (never a dict with an error) on any setup failure, so the
    caller always has a clean fallback to the normal per-command spawn.
    """
    proc = _get_persistent_shell(workdir)
    if proc is None:
        return None

    # mxc.py always hands this daemon a command already wrapped as
    # cmd /c "<script>" (its own bash/PowerShell-to-cmd translation, or an
    # explicit ["cmd", "/c", script] in the request). The persistent shell
    # IS a cmd.exe already, so write <script> straight into it instead of
    # spawning a nested cmd.exe to re-interpret the wrapper -- that nested
    # spawn is exactly the per-command cost this fast path exists to avoid.
    unwrap = _CMD_WRAPPER_RE.match(command)
    inner = unwrap.group(1) if unwrap else command

    token = uuid.uuid4().hex
    try:
        proc.stdin.write(f"{inner}\r\n")
        proc.stdin.write(f"echo {_SHELL_SENTINEL_PREFIX}{token}_%errorlevel%\r\n")
        proc.stdin.flush()
    except Exception:  # noqa: BLE001
        return None

    lines: list[str] = []
    deadline = time.monotonic() + timeout
    sentinel_re = re.compile(re.escape(_SHELL_SENTINEL_PREFIX) + token + r"_(-?\d+)")
    while time.monotonic() < deadline:
        try:
            line = _shell_queue.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            break
        if line == "__MXC_SHELL_READER_EOF__\n":
            return None  # shell died mid-command; caller falls back
        match = sentinel_re.search(line)
        if match:
            # `prompt $_` makes every prompt occurrence one or more blank
            # lines (cmd.exe's own end-of-prompt newline plus $_'s own),
            # printed right before the real command's output and again
            # right before the sentinel echo that just matched. Strip
            # leading/trailing blank lines only -- a genuine blank line in
            # the middle of real multi-line output is left alone.
            while lines and lines[0] == "\n":
                lines = lines[1:]
            while lines and lines[-1] == "\n":
                lines = lines[:-1]
            return {"exit_code": int(match.group(1)), "stdout": "".join(lines), "stderr": ""}
        lines.append(line)
    return None  # timed out waiting for the sentinel; caller falls back


def _run_foreground(request: dict, workdir: Path) -> dict:
    command = request["command"]
    requested_cwd = request.get("workdir")
    requested_env = request.get("env")
    timeout = request.get("timeout", 30)

    if not requested_cwd and not requested_env:
        fast_result = _run_in_persistent_shell(command, timeout, workdir)
        if fast_result is not None:
            return fast_result
        # Fall through to the normal per-command spawn below.

    cwd = requested_cwd or str(workdir)
    env = dict(os.environ)
    env.update(requested_env or {})
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
    if _shell_proc is not None and _shell_proc.poll() is None:
        _shell_proc.kill()


if __name__ == "__main__":
    main()
