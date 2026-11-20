"""Standalone spike: per-session isolation and parallelism via MXC processcontainer.

NOT part of jiuwenbox. Validates the two claims in architecture.md Section 3
before any production integration is attempted:

  Phase A - one-shot isolation: N sessions each write a marker file inside
            their own MXC-granted workspace, concurrently. A session that
            then tries to read ANOTHER session's workspace must fail
            (deny-by-default). Marker content must never cross sessions.

  Phase B - daemon-in-sandbox reuse: N sessions each get one long-lived
            daemon started by a single one-shot MXC create-and-run call
            (Session.create). Repeat exec() calls go straight to that
            daemon over the shared workspace directory (file-based IPC),
            never back through MXC. Verifies concurrent exec across
            sessions completes without serialization and without
            cross-session data leakage.

Run: python mxc_session_spike.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MXC_REPO = Path(r"D:\git_projects\mxc")
PYTHON_EXE = sys.executable
PYTHON_PREFIX = str(Path(sys.prefix))
DAEMON_SCRIPT = Path(__file__).resolve().parent / "session_daemon.py"
SPIKE_ROOT = Path(tempfile.gettempdir()) / "mxc_session_spike"


def find_wxc_exec() -> Path:
    candidates = sorted(MXC_REPO.glob("src/target/**/wxc-exec.exe"))
    if not candidates:
        raise FileNotFoundError(
            f"wxc-exec.exe not found under {MXC_REPO / 'src' / 'target'}; build MXC first (build.bat)."
        )
    # Prefer a release build if more than one is present.
    release = [c for c in candidates if "release" in c.parts]
    return (release or candidates)[0]


WXC_EXEC = None  # resolved lazily in main() so import-time failures are avoidable in tests


def build_config(*, readwrite: list[str], readonly: list[str], command_line: str, cwd: str, timeout_ms: int = 30000) -> dict:
    return {
        "version": "1.0.0",
        "containment": "processcontainer",
        "process": {"commandLine": command_line, "cwd": cwd, "timeout": timeout_ms},
        "filesystem": {"readwritePaths": readwrite, "readonlyPaths": readonly},
    }


def run_one_shot(config: dict, *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    fd_path = SPIKE_ROOT / f"config-{uuid.uuid4().hex}.json"
    fd_path.parent.mkdir(parents=True, exist_ok=True)
    fd_path.write_text(json.dumps(config), encoding="utf-8")
    try:
        return subprocess.run(
            [str(WXC_EXEC), str(fd_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        fd_path.unlink(missing_ok=True)


def spawn_one_shot(config: dict) -> tuple[subprocess.Popen, Path]:
    fd_path = SPIKE_ROOT / f"config-{uuid.uuid4().hex}.json"
    fd_path.parent.mkdir(parents=True, exist_ok=True)
    fd_path.write_text(json.dumps(config), encoding="utf-8")
    proc = subprocess.Popen([str(WXC_EXEC), str(fd_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc, fd_path


# ---------------------------------------------------------------------------
# Phase A: one-shot parallel isolation
# ---------------------------------------------------------------------------

def phase_a(n: int = 3) -> bool:
    print(f"\n=== Phase A: one-shot parallel isolation across {n} sessions ===")
    dirs = []
    for i in range(n):
        wd = SPIKE_ROOT / f"phaseA-{i}"
        wd.mkdir(parents=True, exist_ok=True)
        dirs.append(wd)

    def write_marker(i: int):
        wd = dirs[i]
        code = f"open('marker.txt','w').write('session-{i}')"
        config = build_config(
            readwrite=[str(wd)],
            readonly=[PYTHON_PREFIX],
            command_line=f'"{PYTHON_EXE}" -c "{code}"',
            cwd=str(wd),
        )
        result = run_one_shot(config)
        return i, result

    ok = True
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {ex.submit(write_marker, i): i for i in range(n)}
        for fut in as_completed(futures):
            i, result = fut.result()
            marker = dirs[i] / "marker.txt"
            content = marker.read_text(encoding="utf-8") if marker.exists() else None
            expected = f"session-{i}"
            passed = content == expected
            ok &= passed
            print(f"  session {i}: exit={result.returncode} marker={content!r} expected={expected!r} -> {'PASS' if passed else 'FAIL'}")
            if result.returncode != 0:
                print(f"    stderr: {result.stderr.strip()[:300]}")

    # Cross-session denial: session 0 tries to read session 1's marker without
    # session 1's directory in its own readwrite/readonly grant.
    target = dirs[1] / "marker.txt"
    own = dirs[0]
    code = f"import pathlib; print(pathlib.Path(r'{target}').read_text())"
    config = build_config(
        readwrite=[str(own)],
        readonly=[PYTHON_PREFIX],
        command_line=f'"{PYTHON_EXE}" -c "{code}"',
        cwd=str(own),
    )
    result = run_one_shot(config)
    denied = result.returncode != 0
    ok &= denied
    print(f"  session 0 reading session 1's marker: exit={result.returncode} -> {'PASS (denied as expected)' if denied else 'FAIL (leaked!)'}")
    if not denied:
        print(f"    stdout: {result.stdout.strip()[:300]}")
    else:
        print(f"    stderr: {result.stderr.strip()[:300]}")

    return ok


# ---------------------------------------------------------------------------
# Phase B: daemon-in-sandbox reuse + concurrency
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, session_id: str):
        self.id = session_id
        self.workdir = SPIKE_ROOT / f"phaseB-{session_id}"
        self.proc: subprocess.Popen | None = None
        self.config_path: Path | None = None

    def create(self, ready_timeout: float = 15.0) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        cmd_line = f'"{PYTHON_EXE}" "{DAEMON_SCRIPT}" "{self.workdir}"'
        config = build_config(
            readwrite=[str(self.workdir)],
            readonly=[PYTHON_PREFIX, str(DAEMON_SCRIPT.parent)],
            command_line=cmd_line,
            cwd=str(self.workdir),
            timeout_ms=180_000,
        )
        self.proc, self.config_path = spawn_one_shot(config)
        ready = self.workdir / "daemon.ready"
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if ready.exists():
                return
            if self.proc.poll() is not None:
                out, err = self.proc.communicate(timeout=5)
                raise RuntimeError(f"session {self.id}: wxc-exec exited early ({self.proc.returncode}): {err.decode(errors='replace')[:500]}")
            time.sleep(0.1)
        raise TimeoutError(f"session {self.id}: daemon did not become ready within {ready_timeout}s")

    def exec(self, command: str, timeout: float = 15.0) -> dict:
        req_id = uuid.uuid4().hex
        (self.workdir / f"cmd-{req_id}.json").write_text(
            json.dumps({"command": command, "timeout": timeout}), encoding="utf-8"
        )
        result_path = self.workdir / f"result-{req_id}.json"
        deadline = time.monotonic() + timeout + 5
        while time.monotonic() < deadline:
            if result_path.exists():
                data = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink(missing_ok=True)
                return data
            time.sleep(0.05)
        raise TimeoutError(f"session {self.id}: no response to exec within {timeout + 5}s")

    def cleanup(self) -> None:
        (self.workdir / "shutdown.signal").touch()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.config_path is not None:
            self.config_path.unlink(missing_ok=True)
        shutil.rmtree(self.workdir, ignore_errors=True)


def phase_b(n: int = 3, calls_per_session: int = 3) -> bool:
    print(f"\n=== Phase B: daemon-in-sandbox reuse, {n} sessions x {calls_per_session} calls, concurrent ===")
    sessions = [Session(f"s{i}") for i in range(n)]

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(lambda s: s.create(), sessions))
    print(f"  all {n} sessions created (daemon.ready observed in each workspace)")

    ok = True
    start = time.monotonic()

    def drive(session: Session):
        outcomes = []
        for call_i in range(calls_per_session):
            code = f"import time; time.sleep(0.5); print('{session.id}-{call_i}')"
            result = session.exec(f'"{PYTHON_EXE}" -c "{code}"')
            outcomes.append((call_i, result))
        return session.id, outcomes

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [ex.submit(drive, s) for s in sessions]
        all_outcomes = [f.result() for f in as_completed(futures)]
    elapsed = time.monotonic() - start

    for session_id, outcomes in all_outcomes:
        for call_i, result in outcomes:
            expected = f"{session_id}-{call_i}"
            got = result["stdout"].strip()
            passed = result["exit_code"] == 0 and got == expected
            ok &= passed
            status = "PASS" if passed else "FAIL"
            print(f"  {session_id} call {call_i}: exit={result['exit_code']} stdout={got!r} expected={expected!r} -> {status}")

    # Each session does calls_per_session * 0.5s of real sleeping. If sessions
    # were serialized rather than run in parallel, wall time would scale with
    # n * calls_per_session * 0.5s; running in parallel it should stay close
    # to calls_per_session * 0.5s regardless of n.
    serial_bound = calls_per_session * 0.5
    concurrent_ok = elapsed < serial_bound * n
    print(f"  wall time: {elapsed:.2f}s (serial bound for n={n} would be ~{serial_bound * n:.2f}s) -> "
          f"{'PASS (ran concurrently)' if concurrent_ok else 'FAIL (looks serialized)'}")
    ok &= concurrent_ok

    for s in sessions:
        s.cleanup()
    print("  all sessions cleaned up")

    return ok


def main() -> int:
    global WXC_EXEC
    WXC_EXEC = find_wxc_exec()
    print(f"Using wxc-exec.exe: {WXC_EXEC}")
    print(f"Using python: {PYTHON_EXE}")
    SPIKE_ROOT.mkdir(parents=True, exist_ok=True)

    a_ok = phase_a()
    b_ok = phase_b()

    print("\n=== Summary ===")
    print(f"Phase A (one-shot isolation):       {'PASS' if a_ok else 'FAIL'}")
    print(f"Phase B (daemon reuse + parallel):   {'PASS' if b_ok else 'FAIL'}")
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
