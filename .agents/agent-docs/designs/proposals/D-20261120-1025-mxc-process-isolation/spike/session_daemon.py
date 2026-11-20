"""Long-lived daemon launched *inside* an MXC processcontainer sandbox.

Runs once per session, started by a single one-shot MXC create-and-run
request (see mxc_session_spike.py Session.create()). Polls its own
workspace directory -- the one path the sandbox's filesystem policy grants
readwritePaths to -- for request files, executes the requested command, and
writes a result file back. The workspace directory is the IPC channel: no
named pipe or socket crosses the sandbox boundary.

This mirrors what jiuwenbox's server/runtime/process.py does for bubblewrap
on Linux (start a daemon once inside the one-shot sandbox, talk to it via
IPC for the rest of the session) -- see architecture.md Section 4.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

POLL_INTERVAL_SECONDS = 0.05
IDLE_TIMEOUT_SECONDS = 120.0


def run_command(request: dict, workdir: Path) -> dict:
    command = request["command"]  # a single shell command-line string
    cwd = request.get("workdir") or str(workdir)
    env = dict(os.environ)
    env.update(request.get("env") or {})
    timeout = request.get("timeout", 30)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except Exception as exc:  # noqa: BLE001 - report any failure back to the caller
        return {"exit_code": -1, "stdout": "", "stderr": str(exc)}


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
                result = run_command(request, workdir)
            except Exception as exc:  # noqa: BLE001
                result = {"exit_code": -1, "stdout": "", "stderr": f"daemon error: {exc}"}
            (workdir / f"result-{req_id}.json").write_text(json.dumps(result), encoding="utf-8")
            cmd_file.unlink(missing_ok=True)
        if time.monotonic() - last_activity > IDLE_TIMEOUT_SECONDS:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    stop_file.unlink(missing_ok=True)
    ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
