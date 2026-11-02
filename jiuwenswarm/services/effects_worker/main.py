# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""effects-worker：共享服务，驱动 effectcraft-cli 处理 Director 剪辑的特效任务。

不做任何身份校验——信任边界完全在网络拓扑层：只有 app 与各用户容器能连到
这个服务（见 docker-compose.yml 的 effects 网络、deploy/architecture.md），
nginx/postgres/公网均不可达。任务级隔离靠"一进程一任务一临时目录，用后即
焚"（jobs.py），不靠这里的任何权限检查——与 Tencent COS 连接器把隔离做进
调用边界本身、不信任共享工具自己分租户的思路一致。

启动：`uvicorn jiuwenswarm.services.effects_worker.main:app --host 0.0.0.0
--port 8088`（见 deploy/effects-worker/Dockerfile）。
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from jiuwenswarm.services.effects_worker.jobs import JobManager

logger = logging.getLogger(__name__)

_CONCURRENCY = int(os.getenv("EFFECTS_WORKER_CONCURRENCY", "2"))
_JOB_TIMEOUT_S = float(os.getenv("EFFECTS_WORKER_JOB_TIMEOUT_SECONDS", "600"))

app = FastAPI(title="effects-worker")
_jobs = JobManager(concurrency=_CONCURRENCY, job_timeout_s=_JOB_TIMEOUT_S)


@app.on_event("startup")
async def _on_startup() -> None:
    _jobs.start_background_sweep()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/jobs", status_code=202)
async def create_job(
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    effectcraft_effect: str = Form(...),
    params: str = Form("{}"),
    output_ext: str = Form(...),
    trim_in: float | None = Form(None),
    trim_out: float | None = Form(None),
) -> dict:
    try:
        parsed_params = json.loads(params)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="params 不是合法 JSON") from None
    if not isinstance(parsed_params, dict):
        raise HTTPException(status_code=400, detail="params 必须是 JSON 对象")
    if (trim_in is None) != (trim_out is None):
        raise HTTPException(status_code=400, detail="trim_in/trim_out 必须同时提供或同时省略")
    if trim_in is not None and trim_out is not None and trim_in >= trim_out:
        raise HTTPException(status_code=400, detail="trim_in 必须小于 trim_out")

    content = await file.read()
    job_id = _jobs.submit(
        input_bytes=content,
        filename=file.filename or "input",
        asset_type=asset_type,
        effectcraft_effect=effectcraft_effect,
        params=parsed_params,
        output_ext=output_ext,
        trim_in=trim_in,
        trim_out=trim_out,
    )
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    status = _jobs.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="job 不存在或已过期")
    return status


@app.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str) -> FileResponse:
    path = _jobs.get_result_path(job_id)
    if path is None:
        raise HTTPException(status_code=409, detail="任务尚未就绪或不存在")
    # 清理必须等响应体真正发完才能跑，否则大文件在传输途中就被删——用
    # BackgroundTask（响应发送完之后才执行），不是发送前/发送同时。
    return FileResponse(
        str(path), background=BackgroundTask(_jobs.cleanup_after_download, job_id)
    )
