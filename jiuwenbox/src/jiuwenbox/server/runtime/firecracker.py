# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Firecracker sandbox runtime adapter.

Per-session microVM backend (see
``.agents/agent-docs/designs/proposals/D-20261123-1054-firecracker-microvm-per-session``).
Each sandbox is one Firecracker process with its own API socket, its own
writable rootfs copy, and a guest-agent reachable over vsock (host side is a
plain AF_UNIX socket per Firecracker's documented host-initiated-connection
handshake: ``CONNECT <port>\\n`` -> ``OK <port>\\n`` ack, then the socket is
the data channel). No Firecracker Python SDK exists, so the control plane
(HTTP-over-UDS) and the data plane (vsock) are both hand-rolled here and kept
deliberately small.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import shlex
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)

GUEST_VSOCK_PORT = 52
_CID_START = 100
_API_SOCKET_WAIT_SECONDS = 5.0
_API_SOCKET_POLL_INTERVAL_SECONDS = 0.05
_BOOT_TIMEOUT_SECONDS = float(os.getenv("JIUWENBOX_FIRECRACKER_BOOT_TIMEOUT_SECONDS", "10"))
_STOP_WAIT_SECONDS = 5.0
_DEFAULT_BOOT_ARGS = "console=ttyS0 reboot=k panic=1 pci=off"


class FirecrackerConfigError(RuntimeError):
    """Raised when the Firecracker binary / kernel / golden rootfs are missing."""


def _env_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


def _resolve_firecracker_bin() -> str:
    value = os.getenv("JIUWENBOX_FIRECRACKER_BIN", "firecracker").strip()
    return value or "firecracker"


def _resolve_kernel_path() -> Path:
    path = _env_path("JIUWENBOX_FIRECRACKER_KERNEL")
    if path is None or not path.is_file():
        raise FirecrackerConfigError(
            "JIUWENBOX_FIRECRACKER_KERNEL must point at a bootable kernel image "
            f"(got {path!r})"
        )
    return path


def _resolve_golden_rootfs_path() -> Path:
    path = _env_path("JIUWENBOX_FIRECRACKER_ROOTFS")
    if path is None or not path.is_file():
        raise FirecrackerConfigError(
            "JIUWENBOX_FIRECRACKER_ROOTFS must point at the golden guest rootfs "
            f"ext4 image with guest-agent baked in (got {path!r})"
        )
    return path


def _resolve_workdir_root() -> Path:
    value = os.getenv("JIUWENBOX_FIRECRACKER_WORKDIR", "").strip()
    return Path(value) if value else Path("/var/lib/jiuwenbox/firecracker")


async def _http_over_uds(
    uds_path: Path,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, str]:
    """Minimal HTTP/1.1 client over an AF_UNIX socket (Firecracker's API transport)."""
    payload = json.dumps(body).encode() if body is not None else b""
    request_lines = [f"{method} {path} HTTP/1.1", "Host: localhost"]
    if body is not None:
        request_lines.append("Content-Type: application/json")
        request_lines.append(f"Content-Length: {len(payload)}")
    request = ("\r\n".join(request_lines) + "\r\n\r\n").encode() + payload

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path=str(uds_path)), timeout=timeout
    )
    try:
        writer.write(request)
        await writer.drain()
        # Firecracker's API server keeps the connection open (it ignores a
        # client-sent ``Connection: close``), so a plain ``reader.read()``
        # waiting for EOF would block until our timeout on every call. Parse
        # the headers for ``Content-Length`` and read exactly that many body
        # bytes instead.
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
        status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
        content_length = 0
        for line in head.decode(errors="replace").split("\r\n"):
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                content_length = int(value.strip() or "0")
                break
        body_bytes = (
            await asyncio.wait_for(reader.readexactly(content_length), timeout=timeout)
            if content_length
            else b""
        )
    finally:
        writer.close()

    parts = status_line.split(" ", 2)
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    return status_code, body_bytes.decode(errors="replace")


async def _fc_api(
    uds_path: Path,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> None:
    status, text = await _http_over_uds(uds_path, method, path, body)
    if status // 100 != 2:
        raise RuntimeError(f"Firecracker API {method} {path} failed: HTTP {status}: {text.strip()}")


async def _vsock_request(
    uds_path: Path,
    guest_port: int,
    req: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Host-initiated vsock call: CONNECT handshake, then one JSON round-trip."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path=str(uds_path)), timeout=min(timeout, 5.0)
    )
    try:
        writer.write(f"CONNECT {guest_port}\n".encode())
        await writer.drain()
        ack = await asyncio.wait_for(reader.readline(), timeout=min(timeout, 5.0))
        if not ack.startswith(b"OK"):
            raise RuntimeError(f"vsock connect refused: {ack!r}")

        writer.write(json.dumps(req).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise RuntimeError("guest-agent closed the connection without a response")
        return json.loads(line)
    finally:
        writer.close()


@dataclass
class _FirecrackerBackgroundJobCache:
    """Last-known view of a guest-side background job (source of truth is the guest)."""

    job_id: str
    command: list[str]
    workdir: str | None


@dataclass
class _FirecrackerSandbox:
    sandbox_id: str
    process: asyncio.subprocess.Process
    workdir: Path
    api_sock: Path
    vsock_uds: Path
    guest_cid: int
    log_file: Any
    jobs: dict[str, _FirecrackerBackgroundJobCache] = field(default_factory=dict)


class FirecrackerRuntime(RuntimeAdapter):
    """Runtime adapter backed by one Firecracker microVM per sandbox."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sandboxes: dict[str, _FirecrackerSandbox] = {}
        self._cid_counter = itertools.count(_CID_START)

    async def create(
        self,
        sandbox_id: str,
        policy_path: Path,
        env: dict[str, str] | None = None,
    ) -> int | None:
        del policy_path, env  # Firecracker sizing/policy mapping is a later phase.
        async with self._lock:
            if sandbox_id in self._sandboxes:
                raise RuntimeError(f"Firecracker sandbox {sandbox_id} already has an active handle")
            guest_cid = next(self._cid_counter)

        firecracker_bin = _resolve_firecracker_bin()
        kernel_path = _resolve_kernel_path()
        golden_rootfs = _resolve_golden_rootfs_path()
        boot_args = os.getenv("JIUWENBOX_FIRECRACKER_BOOT_ARGS", _DEFAULT_BOOT_ARGS)
        mem_mib = int(os.getenv("JIUWENBOX_FIRECRACKER_MEM_MIB", "256"))
        vcpus = int(os.getenv("JIUWENBOX_FIRECRACKER_VCPUS", "1"))

        workdir = _resolve_workdir_root() / sandbox_id
        await asyncio.to_thread(workdir.mkdir, parents=True, exist_ok=True)
        rootfs_copy = workdir / "rootfs.ext4"
        api_sock = workdir / "fc-api.sock"
        vsock_uds = workdir / "fc-vsock.sock"
        log_path = workdir / "firecracker.log"
        for stale in (api_sock, vsock_uds):
            await asyncio.to_thread(stale.unlink, True)

        await asyncio.to_thread(shutil.copyfile, golden_rootfs, rootfs_copy)

        log_file = open(log_path, "ab")
        try:
            process = await asyncio.create_subprocess_exec(
                firecracker_bin,
                "--api-sock", str(api_sock),
                "--id", sandbox_id,
                stdout=log_file,
                stderr=log_file,
            )
        except FileNotFoundError as exc:
            log_file.close()
            raise FirecrackerConfigError(
                f"Firecracker binary {firecracker_bin!r} not found on PATH"
            ) from exc

        sandbox = _FirecrackerSandbox(
            sandbox_id=sandbox_id,
            process=process,
            workdir=workdir,
            api_sock=api_sock,
            vsock_uds=vsock_uds,
            guest_cid=guest_cid,
            log_file=log_file,
        )

        try:
            await self._wait_for_api_socket(api_sock, process)
            await _fc_api(api_sock, "PUT", "/logger", {"log_path": str(log_path), "level": "Info"})
            await _fc_api(
                api_sock, "PUT", "/boot-source",
                {"kernel_image_path": str(kernel_path), "boot_args": boot_args},
            )
            await _fc_api(
                api_sock, "PUT", "/drives/rootfs",
                {
                    "drive_id": "rootfs",
                    "path_on_host": str(rootfs_copy),
                    "is_root_device": True,
                    "is_read_only": False,
                },
            )
            await _fc_api(
                api_sock, "PUT", "/machine-config",
                {"vcpu_count": vcpus, "mem_size_mib": mem_mib},
            )
            await _fc_api(
                api_sock, "PUT", "/vsock",
                {"guest_cid": guest_cid, "uds_path": str(vsock_uds), "vsock_id": "vsock0"},
            )
            await _fc_api(api_sock, "PUT", "/actions", {"action_type": "InstanceStart"})
            await self._wait_for_guest_agent(vsock_uds, process)
        except Exception:
            await self._force_kill(sandbox)
            await asyncio.to_thread(shutil.rmtree, workdir, True)
            raise

        async with self._lock:
            self._sandboxes[sandbox_id] = sandbox
        return process.pid

    async def _wait_for_api_socket(
        self, api_sock: Path, process: asyncio.subprocess.Process,
    ) -> None:
        deadline = time.monotonic() + _API_SOCKET_WAIT_SECONDS
        while time.monotonic() < deadline:
            if api_sock.exists():
                return
            if process.returncode is not None:
                raise RuntimeError(
                    f"firecracker exited early with code {process.returncode} "
                    "before the API socket appeared"
                )
            await asyncio.sleep(_API_SOCKET_POLL_INTERVAL_SECONDS)
        raise RuntimeError(f"Firecracker API socket {api_sock} did not appear in time")

    async def _wait_for_guest_agent(
        self, vsock_uds: Path, process: asyncio.subprocess.Process,
    ) -> None:
        deadline = time.monotonic() + _BOOT_TIMEOUT_SECONDS
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise RuntimeError(f"firecracker exited early with code {process.returncode}")
            try:
                result = await _vsock_request(vsock_uds, GUEST_VSOCK_PORT, {"op": "ping"}, timeout=1.0)
                if result.get("ok"):
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            await asyncio.sleep(0.1)
        raise RuntimeError(
            f"guest-agent did not become reachable within {_BOOT_TIMEOUT_SECONDS}s"
            f"{f': {last_error}' if last_error else ''}"
        )

    async def stop(self, sandbox_id: str, timeout: float = 10.0) -> None:
        sandbox = await self._require_sandbox(sandbox_id)
        process = sandbox.process
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def is_running(self, sandbox_id: str) -> bool:
        sandbox = await self._get_sandbox(sandbox_id)
        if sandbox is None:
            return False
        return sandbox.process.returncode is None

    async def cleanup(self, sandbox_id: str) -> None:
        async with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is None:
            return
        await self._force_kill(sandbox)
        await asyncio.to_thread(shutil.rmtree, sandbox.workdir, True)

    async def _force_kill(self, sandbox: _FirecrackerSandbox) -> None:
        process = sandbox.process
        if process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=_STOP_WAIT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("firecracker process for %s did not exit after SIGKILL", sandbox.sandbox_id)
        try:
            sandbox.log_file.close()
        except Exception:  # noqa: BLE001
            pass

    async def exec(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> ExecResult:
        sandbox = await self._require_sandbox(sandbox_id)
        if not request.command:
            raise ValueError("command must not be empty")
        req: dict[str, Any] = {
            "op": "exec",
            "command": shlex.join(request.command),
            "workdir": request.workdir,
            "env": {k: v for k, v in (request.env or {}).items() if v is not None},
        }
        if request.timeout is not None:
            req["timeout"] = request.timeout
        if request.stdin_data is not None:
            req["stdin_b64"] = base64.b64encode(request.stdin_data).decode("ascii")

        result = await _vsock_request(
            sandbox.vsock_uds, GUEST_VSOCK_PORT, req,
            timeout=(request.timeout or 30.0) + 5.0,
        )
        return ExecResult(
            exit_code=int(result.get("exit_code", -1)),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )

    async def exec_background(
        self,
        sandbox_id: str,
        request: RuntimeBackgroundExecRequest,
    ) -> BackgroundExecResult:
        sandbox = await self._require_sandbox(sandbox_id)
        if not request.command:
            return BackgroundExecResult(started=False, command=[], error_message="command must not be empty")

        req: dict[str, Any] = {
            "op": "exec_background",
            "command": shlex.join(request.command),
            "job_id": request.job_id,
            "workdir": request.workdir,
            "env": {k: v for k, v in (request.env or {}).items() if v is not None},
        }
        result = await _vsock_request(sandbox.vsock_uds, GUEST_VSOCK_PORT, req, timeout=10.0)
        if not result.get("started"):
            return BackgroundExecResult(
                started=False,
                command=list(request.command),
                job_id=request.job_id,
                error_message=result.get("error") or "guest-agent failed to start job",
            )

        async with self._lock:
            sandbox.jobs[request.job_id] = _FirecrackerBackgroundJobCache(
                job_id=request.job_id,
                command=list(request.command),
                workdir=request.workdir,
            )
        return BackgroundExecResult(
            started=True,
            job_id=request.job_id,
            pid=result.get("pid"),
            command=list(request.command),
            running=True,
            exit_code=None,
        )

    async def get_background_job(
        self,
        sandbox_id: str,
        job_id: str,
    ) -> BackgroundJobStatus:
        sandbox = await self._require_sandbox(sandbox_id)
        cache = await self._require_job_cache(sandbox, job_id)
        result = await _vsock_request(
            sandbox.vsock_uds, GUEST_VSOCK_PORT, {"op": "bg_status", "job_id": job_id}, timeout=10.0,
        )
        if result.get("error") == "not_found":
            raise BackgroundJobNotFoundError(f"Background job '{job_id}' not found in sandbox '{sandbox_id}'")
        return self._job_status(sandbox_id, cache, result)

    async def list_background_jobs(
        self,
        sandbox_id: str,
        *,
        running_only: bool = False,
    ) -> list[BackgroundJobSummary]:
        sandbox = await self._require_sandbox(sandbox_id)
        result = await _vsock_request(sandbox.vsock_uds, GUEST_VSOCK_PORT, {"op": "bg_list"}, timeout=10.0)
        summaries = []
        for job in result.get("jobs", []):
            summaries.append(
                BackgroundJobSummary(
                    job_id=job["job_id"],
                    pid=job.get("pid"),
                    command=(await self._get_job_cache(sandbox, job["job_id"])) or [job["command"]],
                    running=job["running"],
                    exit_code=job.get("exit_code"),
                    started_at=_epoch_to_datetime(job["started_at"]),
                    finished_at=_epoch_to_datetime(job.get("finished_at")),
                )
            )
        if running_only:
            summaries = [item for item in summaries if item.running]
        summaries.sort(key=lambda item: item.started_at, reverse=True)
        return summaries

    async def kill_background_job(
        self,
        sandbox_id: str,
        job_id: str,
        signum: int = 15,
    ) -> KillBackgroundJobResult:
        sandbox = await self._require_sandbox(sandbox_id)
        await self._require_job_cache(sandbox, job_id)
        result = await _vsock_request(
            sandbox.vsock_uds, GUEST_VSOCK_PORT,
            {"op": "bg_kill", "job_id": job_id, "signal": signum}, timeout=10.0,
        )
        status = await _vsock_request(
            sandbox.vsock_uds, GUEST_VSOCK_PORT, {"op": "bg_status", "job_id": job_id}, timeout=10.0,
        )
        exit_code = status.get("exit_code") if status.get("error") != "not_found" else None
        return KillBackgroundJobResult(
            job_id=job_id,
            killed=bool(result.get("killed")),
            reason=result.get("reason", "unknown"),
            exit_code=exit_code,
        )

    async def _get_sandbox(self, sandbox_id: str) -> _FirecrackerSandbox | None:
        async with self._lock:
            return self._sandboxes.get(sandbox_id)

    async def _require_sandbox(self, sandbox_id: str) -> _FirecrackerSandbox:
        sandbox = await self._get_sandbox(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Firecracker sandbox {sandbox_id} is not active")
        return sandbox

    async def _get_job_cache(self, sandbox: _FirecrackerSandbox, job_id: str) -> list[str] | None:
        async with self._lock:
            cache = sandbox.jobs.get(job_id)
        return list(cache.command) if cache else None

    async def _require_job_cache(
        self, sandbox: _FirecrackerSandbox, job_id: str,
    ) -> _FirecrackerBackgroundJobCache:
        async with self._lock:
            cache = sandbox.jobs.get(job_id)
        if cache is None:
            raise BackgroundJobNotFoundError(
                f"Background job '{job_id}' not found in sandbox '{sandbox.sandbox_id}'"
            )
        return cache

    @staticmethod
    def _job_status(
        sandbox_id: str,
        cache: _FirecrackerBackgroundJobCache,
        result: dict[str, Any],
    ) -> BackgroundJobStatus:
        return BackgroundJobStatus(
            job_id=cache.job_id,
            sandbox_id=sandbox_id,
            command=list(cache.command),
            pid=result.get("pid"),
            running=bool(result.get("running")),
            exit_code=result.get("exit_code"),
            started_at=_epoch_to_datetime(result.get("started_at")) or datetime.now(timezone.utc),
            finished_at=_epoch_to_datetime(result.get("finished_at")),
            workdir=cache.workdir,
        )


def _epoch_to_datetime(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)
