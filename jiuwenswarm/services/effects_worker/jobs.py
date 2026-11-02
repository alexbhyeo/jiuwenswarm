# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""任务队列/进程池：每个任务独立临时目录、独立 effectcraft-cli 子进程，
用后即焚——一进程=一任务=一租户，隔离边界是进程生命周期，不是权限校验
（见设计文档 D-20261101-2128-director-effectcraft-integration 的 D-02 和
"安全与授权边界"一节）。job_id 用 secrets.token_urlsafe 生成，不可枚举。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.services.effects_worker.cli_driver import CliDriverError, run_effect_job

logger = logging.getLogger(__name__)

_JOB_TTL_SECONDS = 1800  # 产物下载后立即清理；这是兜底：被遗弃的任务最多保留这么久。
_SWEEP_INTERVAL_SECONDS = 300


@dataclass
class JobRecord:
    job_id: str
    status: str  # "queued" | "running" | "ready" | "failed"
    workdir: Path
    output_path: Path | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class JobManager:
    def __init__(self, *, concurrency: int, job_timeout_s: float) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._semaphore = asyncio.Semaphore(concurrency)
        self._job_timeout_s = job_timeout_s
        self._base_dir = Path(tempfile.gettempdir()) / "effects-jobs"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._sweep_task: asyncio.Task | None = None

    def start_background_sweep(self) -> None:
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            self._sweep_abandoned()

    def _sweep_abandoned(self) -> None:
        now = time.time()
        stale = [
            job_id
            for job_id, rec in self._jobs.items()
            if rec.finished_at is not None and (now - rec.finished_at) > _JOB_TTL_SECONDS
        ]
        for job_id in stale:
            self._cleanup(job_id)

    def _cleanup(self, job_id: str) -> None:
        rec = self._jobs.pop(job_id, None)
        if rec is not None:
            shutil.rmtree(rec.workdir, ignore_errors=True)

    def submit(
        self, *, input_bytes: bytes, filename: str, asset_type: str, effectcraft_effect: str,
        params: dict[str, Any], output_ext: str,
    ) -> str:
        job_id = secrets.token_urlsafe(18)
        workdir = self._base_dir / job_id
        workdir.mkdir(parents=True, exist_ok=False)
        input_path = workdir / f"input_{filename}"
        input_path.write_bytes(input_bytes)

        rec = JobRecord(job_id=job_id, status="queued", workdir=workdir)
        self._jobs[job_id] = rec
        asyncio.create_task(self._run(rec, input_path, asset_type, effectcraft_effect, params, output_ext))
        return job_id

    async def _run(
        self, rec: JobRecord, input_path: Path, asset_type: str, effectcraft_effect: str,
        params: dict[str, Any], output_ext: str,
    ) -> None:
        async with self._semaphore:
            rec.status = "running"
            try:
                output_path = await run_effect_job(
                    input_path=input_path,
                    asset_type=asset_type,
                    effectcraft_effect=effectcraft_effect,
                    params=params,
                    output_ext=output_ext,
                    workdir=rec.workdir,
                    timeout_s=self._job_timeout_s,
                )
            except CliDriverError as exc:
                rec.status = "failed"
                rec.error = str(exc)
                logger.warning("[effects-worker] job %s failed: %s", rec.job_id, exc)
            except Exception as exc:  # noqa: BLE001 - 任何未预料异常都按失败收尾，不让任务卡死
                rec.status = "failed"
                rec.error = f"内部错误: {exc}"
                logger.exception("[effects-worker] job %s raised unexpectedly", rec.job_id)
            else:
                rec.status = "ready"
                rec.output_path = output_path
            finally:
                rec.finished_at = time.time()

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        rec = self._jobs.get(job_id)
        if rec is None:
            return None
        return {"status": rec.status, "error": rec.error}

    def get_result_path(self, job_id: str) -> Path | None:
        rec = self._jobs.get(job_id)
        if rec is None or rec.status != "ready" or rec.output_path is None:
            return None
        return rec.output_path

    def cleanup_after_download(self, job_id: str) -> None:
        """产物被下载后立即清理，不等 TTL 兜底扫描——正常路径应该总是走这里。"""
        self._cleanup(job_id)
