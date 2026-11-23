"""Guest-agent: runs *inside* the Firecracker microVM, listening on
AF_VSOCK. Installed as a systemd service in the guest rootfs (see
inject_guest_agent.sh) so it starts automatically on every boot.

Protocol (newline-delimited JSON over vsock, matches the
D-20261123-1054-firecracker-microvm-per-session design doc Section 4):
  {"op": "ping"}
      -> {"ok": true}
  {"op": "exec", "command": str, "workdir": str|None, "env": dict|None,
   "timeout": float, "stdin_b64": str|None}
      -> {"exit_code": int, "stdout": str, "stderr": str}
  {"op": "exec_background", "command": str, "job_id": str,
   "workdir": str|None, "env": dict|None}
      -> {"started": bool, "pid": int|None, "error": str|None}
  {"op": "bg_status", "job_id": str}
      -> {"job_id", "pid", "running", "exit_code", "command", "started_at",
          "finished_at"} or {"error": "not_found"}
  {"op": "bg_list"}
      -> {"jobs": [ ...same shape as bg_status, minus the error case... ]}
  {"op": "bg_kill", "job_id": str, "signal": int}
      -> {"killed": bool, "reason": str}

No jiuwenbox imports -- this file ships inside the guest image, standalone.
"""
import base64
import json
import os
import signal
import socket
import subprocess
import threading
import time

VSOCK_PORT = 52

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}  # job_id -> {proc, command, started_at, finished_at, exit_code}


def _start_background(req: dict) -> dict:
    command = req["command"]
    job_id = req["job_id"]
    workdir = req.get("workdir")
    env = dict(os.environ)
    env.update(req.get("env") or {})
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=workdir, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return {"started": False, "pid": None, "error": str(exc)}

    with _jobs_lock:
        _jobs[job_id] = {
            "proc": proc, "command": command, "running": True,
            "exit_code": None, "started_at": time.time(), "finished_at": None,
        }

    def _watch() -> None:
        exit_code = proc.wait()
        with _jobs_lock:
            _jobs[job_id]["running"] = False
            _jobs[job_id]["exit_code"] = exit_code
            _jobs[job_id]["finished_at"] = time.time()

    threading.Thread(target=_watch, daemon=True).start()
    return {"started": True, "pid": proc.pid, "error": None}


def _job_view(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id, "pid": job["proc"].pid, "command": job["command"],
        "running": job["running"], "exit_code": job["exit_code"],
        "started_at": job["started_at"], "finished_at": job["finished_at"],
    }


def _bg_status(req: dict) -> dict:
    with _jobs_lock:
        job = _jobs.get(req["job_id"])
        if job is None:
            return {"error": "not_found"}
        return _job_view(req["job_id"], job)


def _bg_list() -> dict:
    with _jobs_lock:
        return {"jobs": [_job_view(jid, j) for jid, j in _jobs.items()]}


def _bg_kill(req: dict) -> dict:
    with _jobs_lock:
        job = _jobs.get(req["job_id"])
    if job is None or not job["running"]:
        return {"killed": False, "reason": "not_running"}
    try:
        job["proc"].send_signal(req.get("signal", signal.SIGTERM))
        return {"killed": True, "reason": "signalled"}
    except Exception as exc:  # noqa: BLE001
        return {"killed": False, "reason": str(exc)}


def handle_request(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True}
    if op == "exec":
        command = req["command"]
        workdir = req.get("workdir")
        env = dict(os.environ)
        env.update(req.get("env") or {})
        timeout = req.get("timeout", 30)
        stdin_b64 = req.get("stdin_b64")
        stdin_bytes = base64.b64decode(stdin_b64) if stdin_b64 else None
        try:
            proc = subprocess.run(
                command, shell=True, cwd=workdir, env=env, input=stdin_bytes,
                capture_output=True, timeout=timeout,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout.decode("utf-8", "replace"),
                "stderr": proc.stderr.decode("utf-8", "replace"),
            }
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
            return {"exit_code": -1, "stdout": out, "stderr": f"timed out after {timeout}s"}
        except Exception as exc:  # noqa: BLE001
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}
    if op == "exec_background":
        return _start_background(req)
    if op == "bg_status":
        return _bg_status(req)
    if op == "bg_list":
        return _bg_list()
    if op == "bg_kill":
        return _bg_kill(req)
    return {"exit_code": -1, "stdout": "", "stderr": f"unknown op: {op!r}"}


def handle_connection(conn: socket.socket) -> None:
    buf = b""
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line)
                    result = handle_request(req)
                except Exception as exc:  # noqa: BLE001
                    result = {"exit_code": -1, "stdout": "", "stderr": f"guest-agent error: {exc}"}
                conn.sendall(json.dumps(result).encode() + b"\n")
    finally:
        conn.close()


def main() -> None:
    srv = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    srv.bind((socket.VMADDR_CID_ANY, VSOCK_PORT))
    srv.listen(16)
    print(f"guest-agent listening on vsock port {VSOCK_PORT}", flush=True)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_connection, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
