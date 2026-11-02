# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""驱动 effectcraft-cli 完成一次"导入素材 → 加特效 → 渲染"的任务。

下面这条流水线已经对照真实构建的 effectcraft-cli（0.19.0，本地 `cargo build
--release -p effectcraft-cli` 产物）逐步手动验证过，覆盖图片和视频两种输入：

    exec file.import {paths:[...]} --empty --save-as P --save --json
        → result.result.items: [item_id]（导入后的素材项目 id）
    run P project.select {items:[item_id]} file.newCompFromSelection {single:true} --save --json
        → result.result[1].result.comps: [comp_id]（新建合成；宽高/帧率/时长
          已验证会自动按素材推断，不需要显式传 dimensionsFrom/duration）
    exec comp.info {comp: comp_id} P --json
        → result.layers[0].id（合成里那条新图层的 id——file.newCompFromSelection
          的返回值里不含图层 id，必须再查一次才能拿到，没有办法省略这一步）
    run P effect.apply {effect, layers:[layer_id]} prop.set {layer, path, value} ... --save --json
        → effect.apply 的 result.paths[0] 总是 "effects/#1"（每个任务只在一条
          全新图层上加一个特效，实测确认索引恒为 #1，这里直接按此拼 prop.set
          的 path，不必先解析 effect.apply 的返回值再拼下一条命令）
    render-frame P --comp comp_id --time 0 --out F.png（图片素材）
    render P --comp comp_id --out F.<ext> --format <fmt>（视频/音频素材）

与此前的设计假设的真实差异（已在这份实现里修正）：
- `add_effect`/`get_project` 等是 MCP 工具名，不是 `exec` 认的引擎命令 id——
  CLI 侧必须用 `effect.apply` + `prop.set` 两步，不能直接套用 MCP 文档的
  便捷工具名。
- `file.import` 的参数是 `paths`（数组），没有 `newComp` 能一步建好合成这件事
  没有在实测里生效——仍需要显式的 `project.select` + `file.newCompFromSelection`
  两步。
- `render --comp -`（"当前激活合成"）对 `render` 子命令本身不生效（会报
  `no composition "-"`），必须传实际的合成 id；`render-frame`/`exec`/`run`
  则不受影响。

音频素材（Reverb/Parametric EQ）的流程在结构上与图片/视频完全一致（同一套
import → select → newCompFromSelection → effect.apply → prop.set → render
--format wav），但本次手动验证只跑过视频自带的音轨，没有单独验证纯音频文件
（wav/mp3）的 file.import 行为——按效果本身在视频轨道上验证通过的结果推断，
不是另一条独立确认过的路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EFFECTCRAFT_CLI_PATH = os.getenv("EFFECTCRAFT_CLI_PATH", "/usr/local/bin/effectcraft-cli")

# 渲染输出格式 token——effectcraft-cli 的 --format 参数。h264/wav 已通过真机
# render 实测验证（见模块文档）；prores/png 按 docs 示例和 render-frame 的
# 默认行为推断，未单独用 --format 显式验证。
FORMAT_BY_EXT: dict[str, str] = {
    "mp4": "h264",
    "mov": "prores",
    "wav": "wav",
}


class CliDriverError(Exception):
    """effectcraft-cli 子进程非零退出、超时，或输出无法解析——统一这一种
    异常类型，调用方（jobs.py）不区分子原因，一律把任务标记为 failed。
    """


async def _run_cli(args: list[str], *, cwd: Path, timeout_s: float, label: str) -> str:
    """跑一次 effectcraft-cli 子进程，返回 stdout；非零退出或超时都抛
    CliDriverError，错误信息里带 stdout/stderr 的尾部（截断，没有机密可泄露——
    这条链路里不涉及任何凭证）。
    """
    proc = await asyncio.create_subprocess_exec(
        EFFECTCRAFT_CLI_PATH, *args,
        cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise CliDriverError(f"{label} 超时（{timeout_s}s）") from None

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (stderr or stdout)[-2000:]
        raise CliDriverError(f"{label} 失败（exit {proc.returncode}）: {tail}")
    return stdout


async def _run_cli_json(args: list[str], *, cwd: Path, timeout_s: float, label: str) -> Any:
    stdout = await _run_cli(args, cwd=cwd, timeout_s=timeout_s, label=label)
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CliDriverError(f"{label} 输出不是合法 JSON: {stdout[-500:]}") from exc


async def run_effect_job(
    *,
    input_path: Path,
    asset_type: str,
    effectcraft_effect: str,
    params: dict[str, Any],
    output_ext: str,
    workdir: Path,
    timeout_s: float,
) -> Path:
    """在 workdir 里跑完整的一次任务，返回渲染产物的路径。

    workdir 由调用方（jobs.py）创建、用后即焚——这里不做任何清理，不持有
    任何跨任务状态，一次调用只认这一个 workdir。asset_type 决定渲染方式：
    "image" 用 render-frame 出一张静帧，"video"/"audio" 用 render 按
    FORMAT_BY_EXT 编码整段时长。
    """
    project_path = workdir / "job.ecproj"
    output_path = workdir / f"output.{output_ext}"

    # 1) 导入素材到一个空项目，取回导入后的素材项目 id。
    import_result = await _run_cli_json(
        [
            "exec", "file.import", json.dumps({"paths": [str(input_path)]}),
            "--empty", "--save-as", str(project_path), "--save", "--json",
        ],
        cwd=workdir, timeout_s=timeout_s, label="file.import",
    )
    items = (import_result.get("result") or {}).get("items") or []
    if not items:
        raise CliDriverError(f"file.import 未返回任何素材项目: {import_result}")
    item_id = items[0]

    # 2) 选中该素材、据其创建一个新合成，取回合成 id。
    select_steps_path = workdir / "select_steps.json"
    select_steps_path.write_text(
        json.dumps([
            {"command": "project.select", "params": {"items": [item_id]}},
            {"command": "file.newCompFromSelection", "params": {"single": True}},
        ]),
        encoding="utf-8",
    )
    select_result = await _run_cli_json(
        ["run", str(project_path), "--commands", str(select_steps_path), "--save", "--json"],
        cwd=workdir, timeout_s=timeout_s, label="project.select+file.newCompFromSelection",
    )
    steps_result = select_result.get("result") or []
    comps = next(
        (s.get("result", {}).get("comps") for s in steps_result if s.get("command") == "file.newCompFromSelection"),
        None,
    )
    if not comps:
        raise CliDriverError(f"file.newCompFromSelection 未创建合成: {select_result}")
    comp_id = comps[0]

    # 3) file.newCompFromSelection 的返回值里不含新图层的 id，必须单独查一次。
    comp_info = await _run_cli_json(
        ["exec", "comp.info", json.dumps({"comp": comp_id}), str(project_path), "--json"],
        cwd=workdir, timeout_s=timeout_s, label="comp.info",
    )
    layers = comp_info.get("layers") or []
    if not layers:
        raise CliDriverError(f"新合成里没有图层: {comp_info}")
    layer_id = layers[0]["id"]

    # 4) 加特效、设参数——每个任务只在这一条全新图层上加一个特效，实测确认
    #    索引恒为 "effects/#1"，不必先解析 effect.apply 的返回值再拼接。
    effect_steps = [{"command": "effect.apply", "params": {"effect": effectcraft_effect, "layers": [layer_id]}}]
    for key, value in params.items():
        effect_steps.append({
            "command": "prop.set",
            "params": {"layer": layer_id, "path": f"effects/#1/{key}", "value": value},
        })
    effect_steps_path = workdir / "effect_steps.json"
    effect_steps_path.write_text(json.dumps(effect_steps), encoding="utf-8")
    await _run_cli(
        ["run", str(project_path), "--commands", str(effect_steps_path), "--save", "--json"],
        cwd=workdir, timeout_s=timeout_s, label="effect.apply+prop.set",
    )

    # 5) 渲染——图片出单帧静帧，视频/音频按完整时长编码。
    if asset_type == "image":
        await _run_cli(
            ["render-frame", str(project_path), "--comp", str(comp_id), "--time", "0", "--out", str(output_path)],
            cwd=workdir, timeout_s=timeout_s, label="render-frame",
        )
    else:
        fmt = FORMAT_BY_EXT.get(output_ext, output_ext)
        await _run_cli(
            ["render", str(project_path), "--comp", str(comp_id), "--out", str(output_path), "--format", fmt],
            cwd=workdir, timeout_s=timeout_s, label="render",
        )

    if not output_path.is_file():
        raise CliDriverError("render 报告成功但未找到产物文件")
    return output_path
