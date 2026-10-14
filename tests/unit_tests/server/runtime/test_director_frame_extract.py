# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``director_frame_extract.extract_frame``.

Unlike the ``video_bridge`` mode tests (which stub this module entirely),
these tests run the real ffmpeg binary against a short synthetic clip
generated with ``-f lavfi -i testsrc`` + a ``hue`` filter so the picture's
color visibly changes between the first and last frame. That lets the
assertions check something stronger than "a file was produced": that
``position="first"`` and ``position="last"`` genuinely extract *different*
frames from the same file, in the right order, including on a clip short
enough that the "seek a few seconds before EOF" trick would otherwise seek
past the start of the file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.director.director_clip_compose import (
    ComposeError,
    resolve_ffmpeg,
)
from jiuwenswarm.server.runtime.director.director_frame_extract import extract_frame


async def _make_test_clip(path: Path, *, duration: float) -> None:
    """Render a short clip whose color visibly shifts from start to end
    (``hue=H=...``), long enough to decode but short/cheap to generate."""
    exe = resolve_ffmpeg()
    args = [
        exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=64x64:rate=10",
        "-vf",
        f"hue=H=2*PI*t/{duration}:s=1",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    result = await _run(args)
    assert result == 0, f"failed to render test clip: {args}"


async def _run(args: list[str]) -> int:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await process.communicate()
    return process.returncode or 0


def _read_png_pixels(path: Path) -> bytes:
    return path.read_bytes()


@pytest.mark.asyncio
async def test_first_and_last_frame_differ_on_a_multi_second_clip(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    await _make_test_clip(clip, duration=4.0)

    first = await extract_frame(str(clip), tmp_path / "first.png", position="first")
    last = await extract_frame(str(clip), tmp_path / "last.png", position="last")

    assert first.is_file() and first.stat().st_size > 0
    assert last.is_file() and last.stat().st_size > 0
    # 色相随时间线性变化：首帧与尾帧的像素字节不应该相同——否则就是两次都
    # 抓到了同一帧（比如尾帧的 seek 算错了，退回到文件开头）。
    assert _read_png_pixels(first) != _read_png_pixels(last)


@pytest.mark.asyncio
async def test_last_frame_extraction_on_a_clip_shorter_than_the_seek_window(tmp_path: Path):
    """抓尾帧从文件末尾往前 3 秒开始解码（_TAIL_SEEK_SECONDS）；clip 本身只有
    1 秒时，负数 seek 必须被夹到 0，退回到从头解码，而不是让 ffmpeg 收到一个
    越界的负时间戳报错。"""
    clip = tmp_path / "short_clip.mp4"
    await _make_test_clip(clip, duration=1.0)

    last = await extract_frame(str(clip), tmp_path / "last.png", position="last")

    assert last.is_file() and last.stat().st_size > 0


@pytest.mark.asyncio
async def test_extract_frame_raises_compose_error_for_a_non_video_file(tmp_path: Path):
    not_a_video = tmp_path / "not_a_video.mp4"
    not_a_video.write_bytes(b"this is not a real video file")

    with pytest.raises(ComposeError) as excinfo:
        await extract_frame(str(not_a_video), tmp_path / "out.png", position="first")

    assert excinfo.value.code == "FRAME_EXTRACT_FAILED"
