# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""把「剪辑」时间线渲染成一条成片（素材 · 视频）。

用户在剪辑标签页里把素材拖到若干条轨道上、裁剪、分割、摆到任意时间点，点
「保存到项目」时由这里把这些片段真正压成一条 mp4 落成项目素材。

渲染语义与预览一致的那一条**必须**说清楚：预览区同一时刻只显示一个片段，
从上往下第一条"覆盖这一刻"的轨道胜出（``locateActive``），都没有就是空隙。
这里的 ``flatten_timeline`` 就是同一条规则的服务端实现——先是上层轨道胜出，
再把整条时间线切成首尾相接、互不重叠的段（含空隙段），于是拼接只需要一次
``concat``，不需要在滤镜图里表达"谁盖住谁"。

与预览**故意不同**的一点：预览把 ``<video>`` 静音了（只有音频片段出声），
成片却保留每个片段自己的音轨——视频片段的原声也要留下来，否则一段带口播的
镜头保存出来会变成哑的，那不是用户点「保存到项目」想要的结果。所有音源
（视频片段自带的音轨 + 音频片段）按各自在时间线上的位置对齐后叠加。

ffmpeg 用 ``imageio-ffmpeg`` 随包安装的那一份（``uv add imageio-ffmpeg``），
找不到时退回系统 PATH 上的 ``ffmpeg``——因此不需要用户额外装什么东西。
那一份**不带 ffprobe**，所以尺寸探测走 ``ffmpeg -i`` 的 stderr 解析。
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 输出画布与帧率：画布取第一个**视频**片段的分辨率（没有视频片段时取第一个
# 图片片段），其余片段按比例缩放 + 居中补边（信箱模式）。这样"以视频为准"
# 符合直觉——竖版广告里的图片不会被拿去当画布，把视频裁成竖的。
DEFAULT_CANVAS = (1280, 720)
DEFAULT_FPS = 30
# 音频统一重采样到这个采样率再叠加，避免不同采样率的音源在 amix 里出问题。
AUDIO_SAMPLE_RATE = 48000
# 单个片段的合法时长下限，与前端 MIN_CLIP_DURATION 同量级：小于它的片段在
# 时间线上本来就看不见，这里当非法输入拒掉，避免生出一堆零长度段。
MIN_SEGMENT_SECONDS = 0.05
# 整条时间线的时长上限，同时也是 ffmpeg 的超时保护依据（见 compose_timeline）：
# 超过这个长度的"成片"不会是广告片，更像是误拖进来的素材堆。
MAX_TIMELINE_SECONDS = 3600.0

_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}

_RE_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_RE_VIDEO_SIZE = re.compile(r"Stream #\d+:\d+.*?: Video: [^\n]*?, (\d{2,5})x(\d{2,5})")
_RE_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*?: Audio:")


class ComposeError(RuntimeError):
    """渲染失败（输入非法、ffmpeg 缺失、ffmpeg 非零退出）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_ffmpeg() -> str:
    """返回可用的 ffmpeg 可执行文件路径。

    优先用 ``imageio-ffmpeg`` 随包安装的那一份：项目的依赖里带上它，用户装完
    项目就有 ffmpeg，不必自己装。依赖不可用（例如被裁剪过的部署）时退回系统
    PATH 上的 ``ffmpeg``，两条路都走不通才报错——错误里要说清怎么办。
    """
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - 依赖缺失要能被下面的 except 接住

        exe = str(imageio_ffmpeg.get_ffmpeg_exe())
        if exe and Path(exe).is_file():
            return exe
    except Exception:  # noqa: BLE001 - 任何导入/取路径失败都走系统 ffmpeg
        logger.info("[clip_compose] imageio-ffmpeg 不可用，改用系统 ffmpeg", exc_info=True)
    system = shutil.which("ffmpeg")
    if system:
        return system
    raise ComposeError(
        "FFMPEG_UNAVAILABLE",
        "找不到可用的 ffmpeg（imageio-ffmpeg 未安装且系统 PATH 里也没有 ffmpeg），无法合成视频。",
    )


# ---------------------------------------------------------------------------
# 片段模型
# ---------------------------------------------------------------------------


@dataclass
class TimelineClip:
    """时间线上的一个片段（与前端 EditClip 一一对应）。"""

    file_path: str
    type: str  # video | image | audio
    trim_in: float
    trim_out: float
    start: float
    name: str = ""
    track_index: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.trim_out - self.trim_in)


@dataclass
class _Segment:
    """渲染的基本单位：时间线上首尾相接的一段，非空、非重叠。"""

    clip: TimelineClip | None  # None = 空隙（黑场）
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class TimelinePlan:
    """渲染计划：整条视频轨由这些段顺序拼成，音频则各归各的层。"""

    duration: float
    visuals: list[_Segment] = field(default_factory=list)
    # 每个音源：(输入文件, 源窗口起点, 时长, 时间线上的落点)
    audios: list[tuple[str, float, float, float]] = field(default_factory=list)


def parse_clip(raw: dict[str, Any], *, track_index: int) -> TimelineClip:
    """把前端传来的一段 JSON 收成一个 :class:`TimelineClip`，顺带校验。

    路径与类型都由前端给（它本来就是从素材拖过来的），但长度/位置不能是负数
    或 NaN——这些值会直接进 ffmpeg 的参数，非法值要么让 ffmpeg 报出晦涩的错，
    要么生成一条莫名其妙的成片，所以在边界上收干净。
    """
    if not isinstance(raw, dict):
        raise ComposeError("INVALID_PARAMS", "片段必须是对象")
    file_path = str(raw.get("file_path") or "").strip()
    if not file_path:
        raise ComposeError("INVALID_PARAMS", "片段缺少 file_path")
    clip_type = str(raw.get("type") or "").strip().lower()
    if clip_type not in {"video", "image", "audio"}:
        raise ComposeError("INVALID_PARAMS", f"不支持的片段类型: {clip_type!r}")

    def _num(key: str, default: float = 0.0) -> float:
        value = raw.get(key, default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ComposeError("INVALID_PARAMS", f"片段字段 {key} 不是数字: {value!r}") from None
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            raise ComposeError("INVALID_PARAMS", f"片段字段 {key} 不是有限数字: {value!r}")
        return number

    trim_in = max(0.0, _num("trim_in"))
    trim_out = max(0.0, _num("trim_out"))
    start = max(0.0, _num("start"))
    if trim_out - trim_in < MIN_SEGMENT_SECONDS:
        raise ComposeError("INVALID_PARAMS", f"片段 {raw.get('name') or file_path} 的裁剪长度过短")
    return TimelineClip(
        file_path=file_path,
        type=clip_type,
        trim_in=trim_in,
        trim_out=trim_out,
        start=start,
        name=str(raw.get("name") or ""),
        track_index=track_index,
    )


def parse_tracks(raw_tracks: Any) -> list[list[TimelineClip]]:
    """把 ``tracks``（轨道数组的数组）收成片段模型。"""
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise ComposeError("INVALID_PARAMS", "时间线没有任何轨道")
    tracks: list[list[TimelineClip]] = []
    for track_index, raw_track in enumerate(raw_tracks):
        if not isinstance(raw_track, list):
            raise ComposeError("INVALID_PARAMS", "轨道必须是片段数组")
        tracks.append([parse_clip(item, track_index=track_index) for item in raw_track])
    return tracks


def flatten_timeline(tracks: list[list[TimelineClip]]) -> TimelinePlan:
    """按"上层轨道胜出"把时间线切成首尾相接的段，并收集所有音源。

    做法是把时间线切成边界点决定的若干小区间：这些边界来自每个片段的
    起点与终点。每个小区间里"覆盖它的片段集合"是不变的，于是取其中轨道号
    最小（最靠上）的那个作为这一段画面；一个都没有就是空隙（黑场）。

    这样得到的是**非重叠、按时间排序、且完整覆盖 [0, duration)** 的段序列，
    直接 concat 就是成片，不需要在滤镜图里表达遮挡关系。
    """
    clips = [clip for track in tracks for clip in track]
    if not clips:
        raise ComposeError("INVALID_PARAMS", "时间线上还没有任何片段")

    duration = max((clip.start + clip.duration) for clip in clips)
    if duration > MAX_TIMELINE_SECONDS:
        raise ComposeError("INVALID_PARAMS", f"时间线过长（{duration:.0f}s），超过 {MAX_TIMELINE_SECONDS:.0f}s 上限")

    boundaries = {0.0, duration}
    for clip in clips:
        boundaries.add(clip.start)
        boundaries.add(clip.start + clip.duration)
    points = sorted(boundaries)

    visuals: list[_Segment] = []
    for left, right in zip(points, points[1:]):
        if right - left < MIN_SEGMENT_SECONDS / 2:
            continue
        covering = [
            clip
            for clip in clips
            if clip.start <= left and clip.start + clip.duration >= right and clip.type in {"video", "image"}
        ]
        # 轨道号最小的胜出 = 时间线上最靠上的那条轨道优先（与预览 locateActive 一致）。
        winner = min(covering, key=lambda c: c.track_index) if covering else None
        if visuals and winner is not None and visuals[-1].clip is winner:
            # 同一片段连续覆盖好几个小区间：并成一段，少一次 concat。
            visuals[-1].duration = right - visuals[-1].start
            continue
        visuals.append(_Segment(clip=winner, start=left, duration=right - left))

    audios: list[tuple[str, float, float, float]] = []
    for clip in clips:
        # 音频片段自己出声；视频片段自带的音轨也保留下来（图片没有音轨）。
        if clip.type in {"audio", "video"}:
            audios.append((clip.file_path, clip.trim_in, clip.duration, clip.start))
    audios.sort(key=lambda item: item[3])
    return TimelinePlan(duration=duration, visuals=visuals, audios=audios)


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


async def _run_ffmpeg_capture(exe: str, args: list[str]) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        exe,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    return process.returncode or 0, stderr.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class MediaInfo:
    """``ffmpeg -i`` 能读到的东西——够渲染用了，不必依赖 ffprobe。"""

    size: tuple[int, int] | None = None
    duration: float | None = None
    has_audio: bool = False


async def probe_media(exe: str, file_path: str) -> MediaInfo:
    """用 ``ffmpeg -i`` 的 stderr 读分辨率/时长/有没有音轨；读不出来的字段为 None。

    随包安装的那份 ffmpeg 不带 ffprobe，所以这里必须靠 stderr。探测失败不是
    致命错误——调用方会退回默认画布，真正的失败留给渲染那一步报出来。
    ``has_audio`` 是必须的：**没有音轨的视频文件不能进音频滤镜图**，否则
    ``[n:a]`` 匹配不到任何流，ffmpeg 直接报错，整条成片都合不出来。
    """
    try:
        _, stderr = await _run_ffmpeg_capture(exe, ["-hide_banner", "-i", file_path])
    except OSError:
        return MediaInfo()
    size_match = _RE_VIDEO_SIZE.search(stderr)
    size = (int(size_match.group(1)), int(size_match.group(2))) if size_match else None
    duration_match = _RE_DURATION.search(stderr)
    duration = None
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return MediaInfo(size=size, duration=duration, has_audio=bool(_RE_AUDIO_STREAM.search(stderr)))


async def _pick_canvas(exe: str, plan: TimelinePlan) -> tuple[int, int]:
    """画布 = 第一个视频片段的分辨率；没有视频片段时用第一张图片；再没有就用默认。

    "优先视频" 是为了避免一张竖版产品图把整条 16:9 广告的成片变成竖版。
    """
    ordered = [
        seg.clip
        for seg in sorted(plan.visuals, key=lambda s: s.start)
        if seg.clip is not None
    ]
    for wanted in ("video", "image"):
        for clip in ordered:
            if clip.type != wanted:
                continue
            info = await probe_media(exe, clip.file_path)
            if info.size:
                return info.size
    return DEFAULT_CANVAS


def _container_extension(file_path: str) -> str:
    return Path(file_path).suffix.lower()


def is_image_file(file_path: str) -> bool:
    """图片要从扩展名判断，因为 ffmpeg 的 stderr 里图片也会报 "Video: png"。"""
    return _container_extension(file_path) in _IMAGE_EXT


# ---------------------------------------------------------------------------
# 命令构造
# ---------------------------------------------------------------------------


def build_command(
    exe: str,
    plan: TimelinePlan,
    out_path: str,
    *,
    canvas: tuple[int, int],
    fps: int = DEFAULT_FPS,
) -> list[str]:
    """把渲染计划翻译成一条 ffmpeg 命令（纯函数，便于单测）。

    每个片段用**输入级** ``-ss/-t`` 截取自己那段源窗口，这样：
    * 视频片段的音轨天然就是同一段窗口，不需要在滤镜里再对齐一次；
    * 长素材上只解码需要的那几秒，比在滤镜里 trim 快得多。
    之后每路画面统一 ``scale + pad`` 到画布（保持比例、居中补边），再一次性
    ``concat``；音频各自 ``adelay`` 到自己的时间点后与一条等长静音底噪 ``amix``，
    这样即使最后一个音源提前结束，成片长度也仍然由画面决定。
    """
    width, height = canvas
    args: list[str] = [exe, "-y", "-hide_banner", "-loglevel", "error"]
    inputs: list[list[str]] = []
    filters: list[str] = []
    labels: list[str] = []
    audio_labels: list[str] = []
    index = 0

    for seg_index, segment in enumerate(plan.visuals):
        label = f"v{seg_index}"
        if segment.clip is None:
            # 空隙 = 黑场，直接生成一段等长的黑画面。
            inputs.append(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    f"{segment.duration:.3f}",
                    "-i",
                    f"color=c=black:s={width}x{height}:r={fps}",
                ]
            )
            filters.append(f"[{index}:v]setsar=1,setpts=PTS-STARTPTS[{label}]")
        else:
            clip = segment.clip
            extra: list[str] = []
            if is_image_file(clip.file_path):
                # 图片没有内在时长：循环成一段静态画面，长度就是这个片段的时长。
                extra += ["-loop", "1", "-t", f"{segment.duration:.3f}"]
            else:
                # 输入级截取：-ss 放在 -i 前面，只解码用得到的窗口。
                extra += ["-ss", f"{clip.trim_in:.3f}", "-t", f"{segment.duration:.3f}"]
            inputs.append([*extra, "-i", clip.file_path])
            filters.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
                f"setpts=PTS-STARTPTS[{label}]"
            )
        labels.append(f"[{label}]")
        index += 1

    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]")

    if plan.audios:
        for audio_index, (file_path, src_in, duration, start) in enumerate(plan.audios):
            label = f"a{audio_index}"
            inputs.append(["-ss", f"{src_in:.3f}", "-t", f"{duration:.3f}", "-i", file_path])
            delay_ms = max(0, int(round(start * 1000)))
            filters.append(
                f"[{index}:a]aresample={AUDIO_SAMPLE_RATE},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[{label}]"
            )
            audio_labels.append(f"[{label}]")
            index += 1
        # 等长静音兜底：amix 的长度取最长输入，没有它就可能在最后一个音源结束处
        # 提前收尾，成片尾部丢掉一段画面。
        inputs.append(
            [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration:.3f}",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}",
            ]
        )
        audio_labels.append(f"[{index}:a]")
        index += 1
        # normalize=0：保留各音源原始电平，不做自动增益（默认会把每个输入都
        # 按输入数衰减，人声会被背景音乐拖小）。
        filters.append(
            f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0[aout]"
        )

    for item in inputs:
        args.extend(item)
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
    ]
    if plan.audios:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-r",
        str(fps),
        "-t",
        f"{plan.duration:.3f}",
        "-movflags",
        "+faststart",
        out_path,
    ]
    return args


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def _timeout_for(duration: float) -> float:
    """渲染超时：按成片时长给，最少 120s、最多 20 分钟。

    x264 veryfast 在普通机器上远快于实时，留这么宽是因为首次调用还要加载
    ffmpeg 二进制、且可能同时有别的东西在跑。
    """
    return max(120.0, min(1200.0, duration * 4 + 120.0))


async def compose_timeline(
    tracks: list[list[TimelineClip]],
    out_dir: Path,
    *,
    filename: str | None = None,
) -> tuple[Path, TimelinePlan, tuple[int, int]]:
    """把时间线渲染成一条 mp4，返回 (输出路径, 渲染计划, 画布)。"""
    exe = resolve_ffmpeg()
    plan = flatten_timeline(tracks)

    # 先把"真的带音轨"的音源挑出来：没有音轨的视频片段（例如生成时就选了
    # 无声的那些）不能进音频滤镜图，否则 [n:a] 匹配不到流，ffmpeg 会直接失败。
    # 同一个文件只探一次（一条时间线上经常反复用到同一段素材）。
    sound_cache: dict[str, bool] = {}
    sounding: list[tuple[str, float, float, float]] = []
    for file_path, src_in, duration, start in plan.audios:
        if file_path not in sound_cache:
            info = await probe_media(exe, file_path)
            sound_cache[file_path] = info.has_audio
        if sound_cache[file_path]:
            sounding.append((file_path, src_in, duration, start))
    plan.audios = sounding

    canvas = await _pick_canvas(exe, plan)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = filename or f"timeline_{int(time.time())}_{secrets.token_hex(3)}.mp4"
    out_path = out_dir / name

    args = build_command(exe, plan, str(out_path), canvas=canvas)
    logger.info(
        "[clip_compose] 渲染成片：%d 段 / %.2fs / 画布 %dx%d / %d 个音源",
        len(plan.visuals),
        plan.duration,
        canvas[0],
        canvas[1],
        len(plan.audios),
    )
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_timeout_for(plan.duration)
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        out_path.unlink(missing_ok=True)
        raise ComposeError("COMPOSE_TIMEOUT", "合成超时，请缩短时间线后重试") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        logger.warning("[clip_compose] ffmpeg 退出码 %s: %s", process.returncode, detail)
        out_path.unlink(missing_ok=True)
        raise ComposeError("COMPOSE_FAILED", f"合成失败：{detail or 'ffmpeg 返回非零退出码'}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-300:]
        out_path.unlink(missing_ok=True)
        raise ComposeError("COMPOSE_FAILED", f"合成没有产出文件：{detail}")
    logger.info("[clip_compose] 成片已生成：%s（%d 字节）", out_path.name, out_path.stat().st_size)
    return out_path, plan, canvas
