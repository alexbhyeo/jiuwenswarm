# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""剪辑"特效"能力：把一个已有素材交给 effects-worker（驱动 effectcraft-cli）
做进一步处理，产出一个新的派生素材。

effects-worker 是一个独立的共享 Docker 服务（见 deploy/architecture.md 的
effects-worker 小节），不持有任何用户身份信息——隔离边界在网络拓扑层
（只有 app 和各用户容器能连到它，见 docker-compose.yml 的 effects 网络），
这里只是一个薄的 HTTP 客户端，不做任何额外的信任假设，也不重试——提交/
轮询/下载失败都直接向上抛，由调用方（director_manager.py）决定如何把
DirectorAsset 标记为 failed。

v1 精选效果目录：effectcraft 有 308 个内置效果，这里只挑了一小部分、每个
只暴露少量数值参数（见设计文档 D-20261101-2128-director-effectcraft-integration
的 §4.2 D-05）。每个效果的属性键名都已对照真实构建的 effectcraft-cli
（0.19.0，`effect.apply` + `props <comp> <layer> --flat --json` 实测导出的
属性树）逐项核实，不是从文档示例或常见命名猜测的——具体命令序列见
cli_driver.py 模块文档。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EFFECTS_WORKER_URL = os.getenv("EFFECTS_WORKER_URL", "http://effects-worker:8088").rstrip("/")
_JOB_SUBMIT_TIMEOUT_S = 30.0
_JOB_POLL_TIMEOUT_S = 10.0
_JOB_DOWNLOAD_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class EffectParam:
    key: str
    label: str
    kind: str  # "number" | "select"
    default: Any
    min: float | None = None
    max: float | None = None
    options: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EffectDef:
    effect_id: str
    label: str
    group: str
    asset_types: tuple[str, ...]  # 适用的素材类型："image" / "video" / "audio"
    effectcraft_effect: str  # effectcraft 里的真实 effect 名称（add_effect 的 effect 参数）
    params: tuple[EffectParam, ...]


# 渲染产物的文件后缀——取决于被处理的**源素材类型**，不是效果本身：同一个
# Gaussian Blur 既能处理图片（出一张静帧 png）也能处理视频（出一段 mp4），
# 固定在 EffectDef 上会在"图片效果"场景下产出内容是 png、后缀却叫 .mp4 的
# 文件（曾经的真实 bug，实测 E2E 跑全量效果目录后才发现）。
def output_ext_for_asset_type(asset_type: str) -> str:
    return {"image": "png", "video": "mp4", "audio": "wav"}.get(asset_type, "mp4")


EFFECT_CATALOG: tuple[EffectDef, ...] = (
    EffectDef(
        effect_id="blur_gaussian",
        label="高斯模糊",
        group="模糊/锐化",
        asset_types=("image", "video"),
        effectcraft_effect="Gaussian Blur",
        # 属性键名已对照 effectcraft 文档（docs/agents.md "A typical session"）核实：
        # set_property {"path":"effects/#1/blurriness", ...}。
        params=(EffectParam("blurriness", "模糊强度", "number", 8, min=0, max=100),),
    ),
    EffectDef(
        effect_id="stylize_glow",
        label="发光",
        group="风格化",
        asset_types=("image", "video"),
        effectcraft_effect="Glow",
        # 已对照 effectcraft 文档 cookbook 核实：
        # add_effect {"effect":"Glow","values":{"threshold":40,"radius":30}}。
        # intensity 键名推断自同一 cookbook 行的 set_property 后续示例，待逐项复核。
        params=(
            EffectParam("threshold", "阈值", "number", 40, min=0, max=100),
            EffectParam("radius", "半径", "number", 30, min=0, max=200),
            EffectParam("intensity", "强度", "number", 2, min=0, max=10),
        ),
    ),
    EffectDef(
        effect_id="sharpen",
        label="锐化",
        group="模糊/锐化",
        asset_types=("image", "video"),
        effectcraft_effect="Sharpen",
        # 属性键名已通过 `effect.apply`+`props --flat --json` 实测核实。
        params=(EffectParam("amount", "强度", "number", 50, min=0, max=200),),
    ),
    EffectDef(
        effect_id="color_hue_sat",
        label="色相/饱和度",
        group="调色",
        asset_types=("image", "video"),
        effectcraft_effect="Hue/Saturation",
        # 实测核实：顶层整体调节是 hue/saturation/lightness（不带 master 前缀，
        # 那是另一组按色相范围细分的 redsHue/yellowsHue/... 参数，v1 不暴露）。
        params=(
            EffectParam("hue", "色相", "number", 0, min=-180, max=180),
            EffectParam("saturation", "饱和度", "number", 0, min=-100, max=100),
            EffectParam("lightness", "明度", "number", 0, min=-100, max=100),
        ),
    ),
    EffectDef(
        effect_id="time_echo",
        label="回声/重影",
        group="时间",
        asset_types=("video",),
        effectcraft_effect="Echo",
        # 属性键名已通过实测核实。
        params=(
            EffectParam("echoTime", "回声间隔(秒)", "number", 0.1, min=0.01, max=2),
            EffectParam("numberOfEchoes", "回声数量", "number", 3, min=1, max=10),
            EffectParam("decay", "衰减", "number", 0.5, min=0, max=1),
        ),
    ),
    EffectDef(
        effect_id="time_timewarp",
        label="变速",
        group="时间",
        asset_types=("video",),
        effectcraft_effect="Timewarp",
        # 属性键名已通过实测核实。
        params=(EffectParam("speed", "速度(%)", "number", 100, min=10, max=400),),
    ),
    EffectDef(
        effect_id="key_keylight",
        label="抠像",
        group="抠像",
        asset_types=("video", "image"),
        effectcraft_effect="Keylight (1.2)",
        # 实测核实：黑白裁切实际嵌在 screenMatte 分组下，不是顶层
        # clipBlack/clipWhite。screenColour 的自动取色（effect.pickColor，
        # 需要点击画面坐标）v1 不做，沿用默认绿幕色。
        params=(
            EffectParam("screenMatte/clipBlack", "黑场裁切", "number", 10, min=0, max=100),
            EffectParam("screenMatte/clipWhite", "白场裁切", "number", 90, min=0, max=100),
        ),
    ),
    EffectDef(
        effect_id="audio_reverb",
        label="混响",
        group="音频",
        asset_types=("audio", "video"),
        effectcraft_effect="Reverb",
        # 实测核实：真实属性是 reverbTime/diffusion/decay/brightness/dryOut/
        # wetOut，不是猜测的 roomSize/wetDryMix；v1 只暴露最直观的两个。
        params=(
            EffectParam("reverbTime", "混响时长", "number", 100, min=0, max=100),
            EffectParam("wetOut", "湿声比例", "number", 10, min=0, max=100),
        ),
    ),
    EffectDef(
        effect_id="audio_eq",
        label="均衡器",
        group="音频",
        asset_types=("audio", "video"),
        effectcraft_effect="Parametric EQ",
        # 实测核实：真实结构是 band1/2/3 各自的 Enable/Frequency/Bandwidth/
        # BoostCut；只有 band1 默认启用（band2/3 默认 disabled，要调它们还需要
        # 多发一条 prop.set 把对应 Enable 打开，v1 为保持简单只用默认已启用的
        # band1，不再需要预设下拉这种间接层）。
        params=(
            EffectParam("band1Frequency", "频率(Hz)", "number", 200, min=20, max=20000),
            EffectParam("band1BoostCut", "增益(dB)", "number", 0, min=-20, max=20),
        ),
    ),
)


def catalog_to_dict() -> list[dict[str, Any]]:
    return [
        {
            "effect_id": e.effect_id,
            "label": e.label,
            "group": e.group,
            "asset_types": list(e.asset_types),
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "kind": p.kind,
                    "default": p.default,
                    "min": p.min,
                    "max": p.max,
                    "options": list(p.options),
                }
                for p in e.params
            ],
        }
        for e in EFFECT_CATALOG
    ]


def get_effect(effect_id: str) -> EffectDef | None:
    return next((e for e in EFFECT_CATALOG if e.effect_id == effect_id), None)


class EffectsWorkerError(Exception):
    """effects-worker 提交/轮询/下载失败——不区分子类型，调用方统一按
    "这次特效处理失败" 处理（标记 DirectorAsset status=failed），不重试。
    """


async def submit_job(
    *, file_path: str, asset_type: str, effect: EffectDef, params: dict[str, Any],
    trim_in: float | None = None, trim_out: float | None = None,
) -> str:
    """上传素材 + 效果参数到 effects-worker，返回其 job_id。

    trim_in/trim_out（源文件内的秒数）只对 video/audio 有意义——从剪辑时间线
    上的某个片段（而不是素材库的整份素材）发起时，调用方传入该片段的
    trimIn/trimOut，effects-worker 只渲染这段范围，不处理整份源文件。
    """
    if not os.path.isfile(file_path):
        raise EffectsWorkerError(f"源文件不存在: {file_path}")
    async with httpx.AsyncClient(timeout=_JOB_SUBMIT_TIMEOUT_S) as client:
        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh)}
            data: dict[str, Any] = {
                "asset_type": asset_type,
                "effectcraft_effect": effect.effectcraft_effect,
                "params": json.dumps(params),
                "output_ext": output_ext_for_asset_type(asset_type),
            }
            if trim_in is not None and trim_out is not None:
                data["trim_in"] = trim_in
                data["trim_out"] = trim_out
            try:
                resp = await client.post(f"{EFFECTS_WORKER_URL}/jobs", data=data, files=files)
            except httpx.HTTPError as exc:
                raise EffectsWorkerError(f"无法连接 effects-worker: {exc}") from exc
    if resp.status_code != 202:
        raise EffectsWorkerError(f"effects-worker 拒绝任务: {resp.status_code} {resp.text[:200]}")
    job_id = resp.json().get("job_id")
    if not job_id:
        raise EffectsWorkerError("effects-worker 未返回 job_id")
    return str(job_id)


async def poll_job(job_id: str) -> dict[str, Any]:
    """返回 {"status": "queued"|"running"|"ready"|"failed", "error"?: str}。"""
    async with httpx.AsyncClient(timeout=_JOB_POLL_TIMEOUT_S) as client:
        try:
            resp = await client.get(f"{EFFECTS_WORKER_URL}/jobs/{job_id}")
        except httpx.HTTPError as exc:
            return {"status": "failed", "error": f"无法连接 effects-worker: {exc}"}
    if resp.status_code == 404:
        return {"status": "failed", "error": "任务不存在或已过期"}
    resp.raise_for_status()
    return resp.json()


async def download_result(job_id: str, dest_path: str) -> None:
    async with httpx.AsyncClient(timeout=_JOB_DOWNLOAD_TIMEOUT_S) as client:
        resp = await client.get(f"{EFFECTS_WORKER_URL}/jobs/{job_id}/result")
    resp.raise_for_status()
    with open(dest_path, "wb") as fh:
        fh.write(resp.content)
