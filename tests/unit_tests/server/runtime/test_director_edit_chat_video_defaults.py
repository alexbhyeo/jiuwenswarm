# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""剪辑助手 视频生成的默认值契约：有声音 + 时长 4-15 秒。

模型同时读两处说明：随包的 ``director-edit-assistant/SKILL.md``（系统提示词）
和 ``_EDIT_CHAT_TOOLS_SCHEMA``（工具 schema）。两处说的是同一件事，任何一边
没跟上就会自相矛盾——模型只会挑其中一条执行，用户看到的行为就成了掷硬币。
所以这里把三条契约钉死：

* 镜头视频与过渡视频的 ``generate_audio`` **缺省为 true**（schema 描述、
  SKILL.md 正文、执行分支三处一致）；
* 时长范围 4-15 秒同时出现在 schema 与 SKILL.md 里；
* 这个范围与 ``gen_toolkits`` 真正的夹取常量一致——否则技能会承诺一个
  代码根本生成不出来的时长。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.agents.harness.common.tools import gen_toolkits
from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_skills as dsk


def _tool_schema(name: str) -> dict[str, Any]:
    for entry in dm._EDIT_CHAT_TOOLS_SCHEMA:
        if entry["function"]["name"] == name:
            return entry["function"]
    raise AssertionError(f"工具 schema 里没有 {name}")


def _skill_text() -> str:
    path = Path(dsk.get_builtin_skills_dir()) / dsk.ASSISTANT_SKILL_NAME / "SKILL.md"
    assert path.is_file(), f"随包的技能正文缺失: {path}"
    return path.read_text(encoding="utf-8")


def test_video_tools_are_audio_on_by_default() -> None:
    """两个视频工具的 schema 描述都必须写明默认有声、且静音需要显式关闭。"""
    for tool in ("generate_shot_video", "generate_video_bridge"):
        props = _tool_schema(tool)["parameters"]["properties"]
        assert "generate_audio" in props, f"{tool} 缺少 generate_audio 参数"
        desc = props["generate_audio"]["description"]
        assert "默认 true" in desc, f"{tool} 的 generate_audio 没有写明默认 true"
        assert "false" in desc, f"{tool} 的 generate_audio 没有说明怎么关掉"


def test_video_tools_document_the_4_to_15_second_range() -> None:
    for tool in ("generate_shot_video", "generate_video_bridge"):
        desc = _tool_schema(tool)["parameters"]["properties"]["duration_seconds"]["description"]
        assert "4-15" in desc, f"{tool} 的 duration_seconds 没有写明 4-15 秒"


def test_documented_range_matches_the_real_clamp() -> None:
    """文档写的 4-15 必须就是代码真正夹取的范围。"""
    low = gen_toolkits._VIDEO_MIN_SECONDS
    high = gen_toolkits._VIDEO_MAX_SECONDS
    assert (low, high) == (4, 15), f"夹取范围变成了 {low}-{high}，技能里的 4-15 要跟着改"

    for tool in ("generate_shot_video", "generate_video_bridge"):
        desc = _tool_schema(tool)["parameters"]["properties"]["duration_seconds"]["description"]
        assert f"{low}-{high}" in desc


def test_skill_body_states_audio_by_default_and_the_range() -> None:
    text = _skill_text()
    # 镜头视频：默认有声 + 4-15 秒。
    assert "默认生成有声片段" in text
    assert "4-15" in text
    # 过渡视频同样默认有声。
    assert "过渡片段默认也有声" in text


def test_skill_body_does_not_promise_the_old_silent_default() -> None:
    """回归护栏：这次改动的要求就是"默认有声"。技能正文若退回"默认无声"，
    模型就会照着生成无声片段，等于把功能改回去。"""
    text = _skill_text()
    assert "默认生成**无声**" not in text
    assert "只有用户明确要求这一段要有声音" not in text


@pytest.mark.parametrize("tool", ["generate_shot_video", "generate_video_bridge"])
def test_execute_defaults_to_audio_on_when_flag_omitted(tool: str) -> None:
    """执行分支里"没传这个参数"必须落到 True——schema 说默认 true，代码也得是，
    否则模型省略参数时实际拿到的还是无声片段。"""
    func = {
        "generate_shot_video": dm.DirectorManager._execute_generate_shot_video,
        "generate_video_bridge": dm.DirectorManager._execute_generate_video_bridge,
    }[tool]
    source = inspect.getsource(func)
    assert 'True if raw_generate_audio is None else bool(raw_generate_audio)' in source, (
        f"{tool} 的 generate_audio 缺省值不是「未传就有声」"
    )
    # 旧写法（缺省无声）不能再出现。
    assert not re.search(r'bool\(args\.get\("generate_audio"\) or False\)', source), (
        f"{tool} 还留着「缺省无声」的旧写法"
    )
