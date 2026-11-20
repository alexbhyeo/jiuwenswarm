# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""MXC-based runtime adapter (Windows processcontainer backend).

Spawns `wxc-exec.exe` once per sandbox lifecycle to start the in-sandbox
daemon (mxc_daemon.py), same architecture as ProcessRuntime uses for
bubblewrap on Linux: a single one-shot sandboxed execution boots a
long-lived daemon, and all later ``exec``/``exec_background`` calls talk to
that daemon via a file-based IPC channel inside the sandbox's own granted
workspace directory -- not back through MXC.

This backend exists because MXC's Windows default (`processcontainer`) and
Linux/macOS defaults (`bubblewrap`/`seatbelt`) are all one-shot
(`ScriptRunner`) only; none of them expose a native
provision/exec-many/deprovision lifecycle. See
``.agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/``
for the design rationale, architecture diagrams, and the standalone spike
this adapter is ported from.

Known limitations (MVP scope, not yet at parity with ProcessRuntime):
  - Windows only (processcontainer); no Linux/macOS MXC backend wired here.
  - write_file/read_file/list_dir/search_files are not implemented; callers
    fall back to exec() (the RuntimeAdapter base class default).
  - Policy support is limited to `filesystem_policy.read_only` /
    `read_write` string-path entries; namespace/capabilities/landlock/
    seccomp/cgroup/network fields in the bwrap-oriented policy schema are
    not applicable to this backend and are ignored.
  - No PTY, no stdin streaming for background jobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml

from jiuwenbox.models.sandbox import (
    BackgroundExecResult,
    BackgroundJobStatus,
    BackgroundJobSummary,
    ExecResult,
    KillBackgroundJobResult,
)
from jiuwenbox.server.runtime.base import (
    RuntimeAdapter,
    RuntimeBackgroundExecRequest,
    RuntimeExecRequest,
)
from jiuwenbox.server.runtime.errors import BackgroundJobNotFoundError
from jiuwenbox.server.workspace import SANDBOX_WORKSPACE

logger = logging.getLogger(__name__)

MXC_EXEC_PATH_ENV = "JIUWENBOX_MXC_EXEC_PATH"
_DAEMON_SCRIPT = Path(__file__).resolve().parent / "mxc_daemon.py"
_DAEMON_READY_TIMEOUT_SECONDS = 15.0
_DEFAULT_EXEC_TIMEOUT_SECONDS = 30.0
_RESULT_POLL_INTERVAL_SECONDS = 0.05


def _resolve_wxc_exec() -> Path:
    override = os.environ.get(MXC_EXEC_PATH_ENV)
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise FileNotFoundError(f"{MXC_EXEC_PATH_ENV}={override!r} does not exist")
    found = shutil.which("wxc-exec.exe") or shutil.which("wxc-exec")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "wxc-exec.exe not found. Build MXC (build.bat) and set "
        f"{MXC_EXEC_PATH_ENV} to its path, or put it on PATH."
    )


def _is_windows_absolute_path(value: str) -> bool:
    """True only for a real Windows filesystem path (drive-qualified).

    The shared ``filesystem_policy.read_only``/``read_write`` fields are
    written in the bwrap-oriented schema as POSIX-style *sandbox-visible*
    paths (``/home``, ``/etc``) -- meaningless as real host paths to this
    backend, and MXC's processcontainer rejects them outright ("expected a
    fully qualified Windows filesystem path"). Only entries that are
    already real Windows paths (e.g. ``C:\\tools``), as an operator would
    author specifically for an MXC-backed policy, are forwarded.
    """
    try:
        return Path(value).is_absolute() and Path(value).drive != ""
    except (OSError, ValueError):
        return False


def _read_mxc_filesystem_policy(policy_path: Path) -> tuple[list[str], list[str]]:
    """Best-effort reuse of the generic filesystem_policy fields.

    Only plain string entries in ``read_only``/``read_write`` that are
    already real Windows paths are honored; everything else in the
    bwrap-oriented policy schema (including POSIX-style sandbox paths
    inherited from a bwrap-authored policy) does not apply to this backend
    and is silently skipped (see module docstring and
    ``_is_windows_absolute_path``).
    """
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("mxc: could not read policy %s: %s", policy_path, exc)
        return [], []
    fs_policy = raw.get("filesystem_policy") or {}
    readonly = [p for p in fs_policy.get("read_only") or [] if isinstance(p, str) and _is_windows_absolute_path(p)]
    readwrite = [p for p in fs_policy.get("read_write") or [] if isinstance(p, str) and _is_windows_absolute_path(p)]
    skipped = [
        p for p in (fs_policy.get("read_only") or []) + (fs_policy.get("read_write") or [])
        if isinstance(p, str) and not _is_windows_absolute_path(p)
    ]
    if skipped:
        logger.debug("mxc: ignoring non-Windows sandbox paths from policy %s: %s", policy_path, skipped)
    return readonly, readwrite


class _Session:
    __slots__ = ("proc", "workdir", "config_path")

    def __init__(self, proc: asyncio.subprocess.Process, workdir: Path, config_path: Path):
        self.proc = proc
        self.workdir = workdir
        self.config_path = config_path


class MxcRuntime(RuntimeAdapter):
    """RuntimeAdapter backed by MXC's processcontainer backend on Windows."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    # -- lifecycle -----------------------------------------------------

    async def create(
        self,
        sandbox_id: str,
        policy_path: Path,
        env: dict[str, str] | None = None,
    ) -> int | None:
        wxc_exec = _resolve_wxc_exec()
        workdir = SANDBOX_WORKSPACE / sandbox_id
        workdir.mkdir(parents=True, exist_ok=True)

        extra_readonly, extra_readwrite = _read_mxc_filesystem_policy(policy_path)
        readwrite = [str(workdir), *extra_readwrite]
        # sys.prefix alone is not enough when running from a venv: the venv's
        # python.exe launcher reads pyvenv.cfg and needs to see the base
        # interpreter install (sys.base_prefix) too, or it fails to start
        # with "No Python at '<base_prefix>\python.exe'" before any of our
        # own code runs.
        python_paths = {str(Path(sys.prefix)), str(Path(sys.base_prefix))}
        # Tool calls from jiuwenswarm's harness are POSIX-shaped (argv[0] is
        # typically "bash"), so the sandbox needs a real bash on its granted
        # path set. Git for Windows' bash is the common case on a dev box;
        # when it is not installed, bash-shaped commands will still fail
        # (same as any other missing tool) but Python-shaped commands are
        # unaffected.
        git_bash_root = Path(r"C:\Program Files\Git")
        extra_tool_paths = [str(git_bash_root)] if git_bash_root.exists() else []
        readonly = [*python_paths, str(_DAEMON_SCRIPT.parent), *extra_tool_paths, *extra_readonly]

        cmd_line = f'"{sys.executable}" "{_DAEMON_SCRIPT}" "{workdir}"'
        config = {
            "version": "1.0.0",
            "containment": "processcontainer",
            "process": {"commandLine": cmd_line, "cwd": str(workdir), "timeout": 0},
            "filesystem": {"readwritePaths": readwrite, "readonlyPaths": readonly},
        }
        config_path = workdir / "_mxc_config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            str(wxc_exec), str(config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        ready_file = workdir / "daemon.ready"
        deadline = time.monotonic() + _DAEMON_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if ready_file.exists():
                self._sessions[sandbox_id] = _Session(proc, workdir, config_path)
                return proc.pid
            if proc.returncode is not None:
                _, stderr = await proc.communicate()
                raise RuntimeError(
                    f"mxc: wxc-exec exited early ({proc.returncode}) for sandbox "
                    f"{sandbox_id}: {stderr.decode(errors='replace')[:2000]}"
                )
            await asyncio.sleep(0.1)

        proc.kill()
        raise TimeoutError(f"mxc: daemon for sandbox {sandbox_id} not ready within {_DAEMON_READY_TIMEOUT_SECONDS}s")

    async def stop(self, sandbox_id: str, timeout: float = 10.0) -> None:
        session = self._sessions.get(sandbox_id)
        if session is None:
            return
        (session.workdir / "shutdown.signal").touch()
        try:
            await asyncio.wait_for(session.proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            session.proc.kill()
            await session.proc.wait()

    async def is_running(self, sandbox_id: str) -> bool:
        session = self._sessions.get(sandbox_id)
        if session is None:
            return False
        return session.proc.returncode is None

    async def cleanup(self, sandbox_id: str) -> None:
        session = self._sessions.pop(sandbox_id, None)
        if session is None:
            workdir = SANDBOX_WORKSPACE / sandbox_id
            shutil.rmtree(workdir, ignore_errors=True)
            return
        if session.proc.returncode is None:
            (session.workdir / "shutdown.signal").touch()
            try:
                await asyncio.wait_for(session.proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                session.proc.kill()
                await session.proc.wait()
        shutil.rmtree(session.workdir, ignore_errors=True)

    # -- exec ------------------------------------------------------------

    async def _poll_result(self, workdir: Path, req_id: str, timeout: float) -> dict:
        result_path = workdir / f"result-{req_id}.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result_path.exists():
                try:
                    data = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    await asyncio.sleep(_RESULT_POLL_INTERVAL_SECONDS)
                    continue
                result_path.unlink(missing_ok=True)
                return data
            await asyncio.sleep(_RESULT_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"mxc: no daemon response within {timeout}s")

    def _require_session(self, sandbox_id: str) -> _Session:
        session = self._sessions.get(sandbox_id)
        if session is None:
            raise KeyError(f"mxc: no active session for sandbox {sandbox_id}")
        return session

    @staticmethod
    def _filtered_env(env: dict[str, str | None] | None) -> dict[str, str]:
        if not env:
            return {}
        return {k: v for k, v in env.items() if v is not None}

    @staticmethod
    def _exe_basename(argv0: str) -> str:
        name = argv0.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        return name[:-4] if name.endswith(".exe") else name

    @classmethod
    def _command_line(cls, command: list[str]) -> str:
        """Build the command-line string the daemon executes via ``shell=True``.

        jiuwenswarm's harness (``command_tools._resolve_execution_plan``)
        resolves a shell tool call on Windows to one of two shapes, using
        whatever bash/powershell it finds on the *host's* own PATH (so
        argv[0] is typically an absolute path, not a bare name):
          - ``[<bash-or-sh-path>, "-lc"|"-c", script]``
          - ``[<powershell-path>, "-NoProfile", "-NonInteractive",
            "-Command", script]``
        Neither shell actually works inside this sandbox's minimal
        filesystem grant: MSYS2 bash and powershell.exe both fail with
        STATUS_DLL_INIT_FAILED on launch even when their own binary
        directory is readable, because their startup needs more than file
        access (registry, profile, desktop heap, etc.) under a restricted
        ProcessContainer token. ``cmd.exe`` does not have that problem and
        is already covered by the sandbox's baseline ACL, so translate the
        inner script to run through it instead. This is a best-effort
        translation -- bash/PowerShell-specific syntax in ``script`` that
        cmd cannot parse will fail the same way it would in any other
        cmd.exe context; this only restores the common case (simple
        commands, basic pipelines) rather than reimplementing either shell.
        Anything else (a direct ``cmd``/``python``/... invocation) passes
        through unchanged.
        """
        if len(command) >= 1:
            exe = cls._exe_basename(command[0])
            if exe in ("bash", "sh") and len(command) == 3 and command[1] in ("-c", "-lc"):
                return f'cmd /c "{command[2]}"'
            if exe == "powershell" and len(command) == 5 and command[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]:
                return f'cmd /c "{command[4]}"'
        return subprocess.list2cmdline(command)

    async def exec(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> ExecResult:
        session = self._require_session(sandbox_id)
        req_id = uuid.uuid4().hex
        timeout = request.timeout or _DEFAULT_EXEC_TIMEOUT_SECONDS
        payload = {
            "op": "exec",
            "command": self._command_line(request.command),
            "workdir": request.workdir,
            "env": self._filtered_env(request.env),
            "timeout": timeout,
        }
        (session.workdir / f"cmd-{req_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        result = await self._poll_result(session.workdir, req_id, timeout + 5.0)
        return ExecResult(
            exit_code=result.get("exit_code", -1),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )

    async def exec_background(
        self,
        sandbox_id: str,
        request: RuntimeBackgroundExecRequest,
    ) -> BackgroundExecResult:
        session = self._require_session(sandbox_id)
        req_id = uuid.uuid4().hex
        payload = {
            "op": "exec_background",
            "command": self._command_line(request.command),
            "job_id": request.job_id,
            "workdir": request.workdir,
            "env": self._filtered_env(request.env),
        }
        (session.workdir / f"cmd-{req_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        ack = await self._poll_result(session.workdir, req_id, 15.0)
        return BackgroundExecResult(
            started=bool(ack.get("started")),
            job_id=request.job_id,
            pid=ack.get("pid"),
            command=request.command,
            running=bool(ack.get("started")),
            exit_code=None,
            error_message=ack.get("error"),
        )

    def _read_job_file(self, workdir: Path, job_id: str) -> dict | None:
        job_file = workdir / f"job-{job_id}.json"
        if not job_file.exists():
            return None
        try:
            return json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    async def get_background_job(
        self,
        sandbox_id: str,
        job_id: str,
    ) -> BackgroundJobStatus:
        session = self._require_session(sandbox_id)
        data = self._read_job_file(session.workdir, job_id)
        if data is None:
            raise BackgroundJobNotFoundError(f"{job_id} not found for sandbox {sandbox_id}")
        return BackgroundJobStatus(
            job_id=job_id,
            sandbox_id=sandbox_id,
            command=self._split_command(data.get("command", "")),
            pid=data.get("pid"),
            running=bool(data.get("running")),
            exit_code=data.get("exit_code"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )

    async def list_background_jobs(
        self,
        sandbox_id: str,
        *,
        running_only: bool = False,
    ) -> list[BackgroundJobSummary]:
        session = self._require_session(sandbox_id)
        summaries: list[BackgroundJobSummary] = []
        for job_file in sorted(session.workdir.glob("job-*.json")):
            try:
                data = json.loads(job_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if running_only and not data.get("running"):
                continue
            summaries.append(BackgroundJobSummary(
                job_id=data.get("job_id", job_file.stem.split("-", 1)[1]),
                pid=data.get("pid"),
                command=self._split_command(data.get("command", "")),
                running=bool(data.get("running")),
                exit_code=data.get("exit_code"),
                started_at=data.get("started_at"),
                finished_at=data.get("finished_at"),
            ))
        return summaries

    async def kill_background_job(
        self,
        sandbox_id: str,
        job_id: str,
        signum: int = 15,
    ) -> KillBackgroundJobResult:
        session = self._require_session(sandbox_id)
        if self._read_job_file(session.workdir, job_id) is None:
            raise BackgroundJobNotFoundError(f"{job_id} not found for sandbox {sandbox_id}")
        req_id = uuid.uuid4().hex
        payload = {"op": "bg_kill", "job_id": job_id, "signal": signum}
        (session.workdir / f"cmd-{req_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        result = await self._poll_result(session.workdir, req_id, 15.0)
        after = self._read_job_file(session.workdir, job_id) or {}
        return KillBackgroundJobResult(
            job_id=job_id,
            killed=bool(result.get("killed")),
            reason=result.get("reason", "unknown"),
            exit_code=after.get("exit_code"),
        )

    @staticmethod
    def _split_command(command_line: str) -> list[str]:
        # Commands are stored as a single Windows command-line string
        # (subprocess.list2cmdline); round-trip it back to argv form for the
        # API surface, which expects list[str].
        try:
            import shlex
            return shlex.split(command_line, posix=False)
        except ValueError:
            return [command_line]
