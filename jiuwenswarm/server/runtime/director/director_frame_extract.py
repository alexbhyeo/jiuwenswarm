# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从一段视频文件里抓首帧/尾帧，供"视频连视频"（FLF2V 桥接）使用。

抓帧复用 director_clip_compose 里已经验证过的 ffmpeg 发现逻辑
（resolve_ffmpeg）与时长探测（probe_media）——没有再造一遍轮子的必要。

首帧：直接 ``-frames:v 1``，从文件开头解码一帧即可，不依赖时长探测。

尾帧：ffmpeg 没有"直接跳到最后一帧"的开关，标准做法是从接近末尾的时间点
开始解码，配合 ``-update 1`` 让输出文件被连续覆写，ffmpeg 跑完时文件里留下
的就是解码到的最后一帧。跳到哪个时间点由 probe_media 探测到的时长决定——
短片（< 抓取窗口）回落到从头解码，避免负数 seek 越界到文件开头之前。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from jiuwenswarm.server.runtime.director.director_clip_compose import (
    ComposeError,
    probe_media,
    resolve_ffmpeg,
)

logger = logging.getLogger(__name__)

# 尾帧抓取从文件末尾往前多少秒开始解码——留足够的缓冲吸收容器时长读数的
# 误差，又不至于短片直接 seek 到负数时间（那种情况下面会回落到从头解码）。
_TAIL_SEEK_SECONDS = 3.0


async def _run_ffmpeg(exe: str, args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        exe, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise ComposeError(
            "FRAME_EXTRACT_FAILED",
            f"抓帧失败（ffmpeg 退出码 {process.returncode}）: "
            f"{stderr.decode('utf-8', errors='replace')[-500:]}",
        )


async def extract_frame(video_path: str, output_path: Path, *, position: Literal["first", "last"]) -> Path:
    """从 ``video_path`` 抓一帧存成 ``output_path``（png），返回 ``output_path``。

    失败（文件不是视频、ffmpeg 缺失/非零退出、没能产出有效文件）抛
    ``ComposeError``，调用方按"生成失败"的套路转成 DirectorRpcError 即可，
    不需要再细分错误类型。
    """
    exe = resolve_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if position == "first":
        args = ["-y", "-i", video_path, "-frames:v", "1", "-q:v", "2", str(output_path)]
    else:
        info = await probe_media(exe, video_path)
        duration = info.duration or 0.0
        seek = min(_TAIL_SEEK_SECONDS, max(duration - 0.05, 0.0))
        args = (
            ["-y", "-sseof", f"-{seek:.3f}", "-i", video_path, "-update", "1", "-q:v", "2", str(output_path)]
            if seek > 0
            else ["-y", "-i", video_path, "-update", "1", "-q:v", "2", str(output_path)]
        )
    await _run_ffmpeg(exe, args)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ComposeError("FRAME_EXTRACT_FAILED", f"抓帧失败：{video_path} 没能生成有效的帧图片")
    return output_path
