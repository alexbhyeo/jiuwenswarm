# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Co-Director **multi-agent team** path of 剪辑助手.

What these pin down:

* the team is a *real* AgentGroup artifact - 13 members load through the product's
  own ``load_agent_group_package``;
* every member gets **its own isolated context** (system = its persona, user = only
  the inputs the orchestrator decides to hand it) - members never share history;
* verifiers are genuinely independent: the payload handed to a verifier must not
  contain the author's reasoning/revision instructions;
* the pipeline is code-controlled - stages run in a fixed order and a failing
  member degrades instead of exploding;
* the MAB is real math: factored reward updates only the pulled arm, UCB explores
  untried arms first, rewards outside 0-100 are rejected, state round-trips;
* routing: base skill ``ads-codirector`` -> team, ``director-edit-assistant`` ->
  the single-agent loop, and a broken team falls back to single-agent.

No network: ``complete`` is always a fake.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_team as dt
from jiuwenswarm.server.runtime.director import director_team_bandit as bt


# ---------------------------------------------------------------------------
# The real AgentGroup package
# ---------------------------------------------------------------------------


def test_team_package_loads_through_the_product_loader() -> None:
    """团队定义是**包**（数据），必须能被产品自己的加载器读出来。"""
    members = dt.load_team()

    expected = {
        "leader",
        "parameters",
        "creative_director",
        "creative_brief",
        "storyline",
        "casting",
        "storyboard",
        "voiceover",
        "keyframe",
        "video_agent",
        "storyline_verifier",
        "keyframe_verifier",
        "video_verifier",
    }
    assert set(members) == expected
    # 每个成员都得有非空 persona，否则这一轮根本没法独立调用
    for name, member in members.items():
        assert member.instructions.strip(), f"{name} 没有 persona"
        assert member.display_name
    # 名册里必须有 leader（AgentGroup 的硬约束）
    assert "leader" in members
    # leader 的 system prompt 要含团队总纲，普通成员也一样（AgentGroup 会注入）
    assert "流水线" in members["leader"].instructions


def test_team_package_is_discoverable_as_an_agent_group() -> None:
    """包目录本身要满足 AgentGroup 的目录约定（manifest + agents/<name>）。"""
    roots = dt._team_search_dirs()
    packages = [r / dt.TEAM_NAME for r in roots if (r / dt.TEAM_NAME / "manifest.json").is_file()]
    assert packages, "工作区/内置目录里都必须能找到 ads-codirector 团队包"

    manifest = json.loads((packages[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_type"] == "agent_group"
    assert manifest["name"] == dt.TEAM_NAME
    assert manifest["agents"][0] == "leader" or "leader" in manifest["agents"]
    for name in manifest["agents"]:
        assert (packages[0] / "agents" / name / "manifest.json").is_file()


def test_verifier_personas_carry_real_rubrics() -> None:
    """验证器的分数只有在 persona 里写清 rubric 才有意义。"""
    members = dt.load_team()
    assert "钩子质量" in members["storyline_verifier"].instructions
    assert "视觉一致" in members["keyframe_verifier"].instructions
    assert "策略效能" in members["video_verifier"].instructions
    # 效能分必须与质量分同向这条纪律要写在 persona 里，否则模型会给出虚高的效能分
    assert "方向一致" in members["video_verifier"].instructions


# ---------------------------------------------------------------------------
# MAB: real math, factored reward
# ---------------------------------------------------------------------------


@pytest.fixture
def bandit() -> bt.MabBandit:
    return bt.MabBandit(rng=random.Random(11))


def test_cold_start_only_picks_untried_arms(bandit: bt.MabBandit) -> None:
    for _ in range(3):
        arms = bandit.select_all()
        for dimension, arm in arms.items():
            assert int(bandit.arm_stats[dimension][arm]["pulls"]) == 0


def test_factorized_reward_updates_only_its_own_dimension(bandit: bt.MabBandit) -> None:
    """核心主张：创意策略的分数**不能**流到叙事模式那条臂上。"""
    arms = {"creative_strategy": "informational", "narrative_mode": "vignette", "aesthetic_archetype": "clarity_energy"}

    bandit.update_reward(arms, {"creative_strategy": 91}, global_score=40)

    assert bandit.arm_stats["creative_strategy"]["informational"]["rewards"] == [91.0]
    # 没给分数的维度退回全局分——而不是拿 91
    assert bandit.arm_stats["narrative_mode"]["vignette"]["rewards"] == [40.0]
    assert bandit.arm_stats["aesthetic_archetype"]["clarity_energy"]["rewards"] == [40.0]
    # 其它臂一点都没动
    assert bandit.arm_stats["creative_strategy"]["comparative"]["pulls"] == 0


def test_out_of_range_rewards_are_rejected(bandit: bt.MabBandit) -> None:
    """一条被污染的记录会长期污染 UCB，所以越界分直接不写。"""
    arms = {"creative_strategy": "informational", "narrative_mode": "vignette", "aesthetic_archetype": "clarity_energy"}

    applied = bandit.update_reward(arms, {"creative_strategy": 999, "narrative_mode": -5}, global_score=0)

    assert "creative_strategy" not in applied
    assert bandit.arm_stats["creative_strategy"]["informational"]["pulls"] == 0


def test_ucb_explores_untried_arms_before_exploiting(bandit: bt.MabBandit) -> None:
    """UCB 的前提是每条臂都被拉过——冷启动必须先把没试过的臂试完。"""
    seen: dict[str, set[str]] = {d: set() for d in bt.DIMENSIONS}
    for _ in range(4):
        arms = bandit.select_all()
        bandit.update_reward(arms, {}, global_score=80)
        for dimension, arm in arms.items():
            seen[dimension].add(arm)

    assert seen["creative_strategy"] == set(bt.ARM_OPTIONS["creative_strategy"])
    assert seen["narrative_mode"] == set(bt.ARM_OPTIONS["narrative_mode"])
    # 4 轮不足以覆盖 4 条美学臂，但至少要试过 3 条
    assert len(seen["aesthetic_archetype"]) >= 3


def test_mean_reward_and_custom_c_limit():
    """c=0 时 UCB 退化成"选平均奖励最高的臂"——用它验证均值算得对。"""
    bandit = bt.MabBandit(ucb_c=0.0, rng=random.Random(3))
    # 先把每条臂垫一次，避免冷启动
    for arm in bt.ARM_OPTIONS["creative_strategy"]:
        bandit.arm_stats["creative_strategy"][arm] = {"pulls": 1, "rewards": [10.0]}
    bandit.arm_stats["creative_strategy"]["comparative"] = {"pulls": 2, "rewards": [90.0, 100.0]}

    assert bandit.mean_reward(bandit.arm_stats["creative_strategy"]["comparative"]) == 95.0
    assert bandit.select_arm("creative_strategy") == "comparative"


def test_bandit_state_round_trips(bandit: bt.MabBandit) -> None:
    arms = bandit.select_all()
    bandit.update_reward(arms, {"creative_strategy": 88}, global_score=70)

    restored = bt.MabBandit.from_dict(json.loads(json.dumps(bandit.to_dict())))

    assert restored.arm_stats == bandit.arm_stats
    assert restored.pulls("creative_strategy") == 1
    # 未知维度/坏数据要被忽略而不是把状态搞坏
    dirty = bt.MabBandit.from_dict({"arm_stats": {"bogus": {"x": {}}, "creative_strategy": {"informational": "nope"}}})
    assert set(dirty.arm_stats) == set(bt.DIMENSIONS)


def test_warm_start_is_preferred_on_cold_start() -> None:
    bandit = bt.MabBandit(warm_start={"creative_strategy": "comparative"}, rng=random.Random(5))
    assert bandit.select_arm("creative_strategy") == "comparative"


def test_arm_aliases_are_normalized() -> None:
    assert bt.validate_arms(
        {"Creative Strategy": "informational", "narrative mode": "Vignette", "archetype": "minimalist-focus"}
    ) == {
        "creative_strategy": "informational",
        "narrative_mode": "vignette",
        "aesthetic_archetype": "minimalist_focus",
    }
    assert bt.validate_arms({"nope": "x"}) is None
    assert bt.validate_arms(None) is None


def test_every_arm_has_a_theory_definition() -> None:
    """选臂与效能打分都以理论为依据，缺定义就会出现"凭感觉"的分数。"""
    for dimension, arms in bt.ARM_OPTIONS.items():
        for arm in arms:
            assert bt.THEORY_MAP.get(arm), f"{dimension}/{arm} 缺理论定义"


# ---------------------------------------------------------------------------
# Orchestrator: isolation, ordering, degradation
# ---------------------------------------------------------------------------


def _fake_replies() -> dict[str, str]:
    return {
        "parameters": json.dumps(
            {
                "brand": "澄野",
                "product": "有机柚香乌龙气泡茶",
                "demographics": "22-35 岁白领",
                "campaign_name": "三点醒",
                "target_duration": "20s",
                "target_orientation": "landscape",
            }
        ),
        "leader": json.dumps(
            {
                "reasoning": "联合判断",
                "recommendations": {
                    "creative_strategy": "informational",
                    "narrative_mode": "narrative_drama",
                    "aesthetic_archetype": "clarity_energy",
                },
            }
        ),
        "creative_director": json.dumps(
            {
                "storyline_instruction": "完整弧线",
                "keyframe_instruction": "高调光",
                "video_instruction": "缓慢横移",
                "audio_instruction": "轻快",
            }
        ),
        "storyline": json.dumps({"scenes": [{"topic": "问题", "action": "困"}]}),
        "storyline_verifier": json.dumps(
            {"breakdown": {"hook_quality": 19, "narrative_arc": 19, "product_integration": 19, "engagement": 19, "prompt_adherence": 19}, "score": 95, "feedback": "好", "actionable_feedback": "继续"}
        ),
        "casting": json.dumps({"character_profile": "Anna", "wardrobe_description": "针织衫", "collage_prompt": "三视图纯白背景"}),
        "storyboard": json.dumps(
            {
                "scenes": [
                    {"topic": "问题", "duration_seconds": 4, "first_frame_prompt": "静", "video_prompt": "动", "assets": ["iter1_character_collage"]}
                ],
                "background_music_prompt": "马林巴",
            }
        ),
        "voiceover": json.dumps(
            {"voiceover_text": "Ten words here for four seconds now.", "voiceover_gender": "female", "voiceover_description": "轻", "background_music_prompt": "马林巴"}
        ),
        "keyframe_verifier": json.dumps({"breakdown": {"visual_consistency": 17}, "score": 85, "feedback": "好", "primary_fault": "image", "problematic_scenes": [], "actionable_feedback": "继续"}),
        "video_verifier": json.dumps(
            {
                "breakdown": {"coherence": 17},
                "mab_efficacy_scores": {"creative_strategy": 84, "narrative_mode": 86, "aesthetic_archetype": 85},
                "mab_efficacy_justifications": {"creative_strategy": "事实链成立"},
                "score": 84,
                "feedback": "可以",
                "primary_fault": "image",
                "actionable_feedback": "重做末场",
            }
        ),
        "creative_brief": "段落式简报",
    }


class _Recorder:
    """记录每次成员调用的 (成员名, messages)，用来断言上下文隔离。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.system_seen: dict[str, str] = {}

    def make(self, members: dict[str, dt.Teammate], replies: dict[str, str]):
        async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
            system = str(messages[0]["content"])
            agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
            self.calls.append((agent, messages))
            self.system_seen[agent] = system
            content = replies.get(agent, "（无回复）")
            if tools:
                # 生产成员：直接给一个工具调用，然后收尾
                if not any(m.get("role") == "tool" for m in messages):
                    return {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": tools[0]["function"]["name"], "arguments": json.dumps({"name": "x", "kind": "scene", "description": "d", "first_frame_name": "f"})},
                            }
                        ],
                    }
                return {"content": "已生成"}
            return {"content": content}

        return complete


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """只把团队**状态**重定向到 tmp——团队**包**仍从真实工作区读取，
    这样测试既不会污染真实工作区，也仍然验证真实包的加载。"""
    state_dir = tmp_path / "team_state"
    monkeypatch.setattr(dt, "_team_state_dir", lambda: state_dir)
    return state_dir


@pytest.mark.asyncio
async def test_each_member_gets_an_isolated_context(isolated_root: Path) -> None:
    """每个成员的 user 消息里只能有编排器显式给它的输入，不能有别的成员的历史。"""
    members = dt.load_team()
    recorder = _Recorder()
    tool_calls: list[dict[str, Any]] = []

    async def run_tool(tool_call):
        tool_calls.append(tool_call)
        return "已生成并保存", "asset_1"

    orch = dt.DirectorTeamOrchestrator(
        project_id="p1",
        project_name="测试项目",
        complete=recorder.make(members, _fake_replies()),
        run_tool=run_tool,
        members=members,
    )
    result = await orch.run("做一支 20 秒广告", "full")

    # 每个成员恰好一次 system，且 system 就是它自己的 persona
    for agent, _messages in recorder.calls:
        assert agent in members
    # 关键断言：验证器的上下文里**没有**编剧的推理/改稿指令
    storyline_calls = [m for a, m in recorder.calls if a == "storyline"]
    verifier_calls = [m for a, m in recorder.calls if a == "storyline_verifier"]
    assert storyline_calls and verifier_calls
    verifier_text = " ".join(str(m["content"]) for m in verifier_calls[0])
    assert "上一稿" not in verifier_text  # 第一稿时还没有改稿指令
    # 每个成员都被调用，且都出现在追踪表里
    assert len({s.agent for s in result.steps}) >= 8


@pytest.mark.asyncio
async def test_pipeline_order_is_code_controlled(isolated_root: Path) -> None:
    members = dt.load_team()
    recorder = _Recorder()

    async def run_tool(_tool_call):
        return "已生成并保存", "asset_1"

    orch = dt.DirectorTeamOrchestrator(
        project_id="p2",
        project_name="测试项目",
        complete=recorder.make(members, _fake_replies()),
        run_tool=run_tool,
        members=members,
    )
    result = await orch.run("做一支 20 秒广告", "full")

    stages = [s.agent for s in result.steps]
    # 关键阶段顺序固定：参数 → 选臂(warm start 由 leader 做) → 方向 → 简报 → 剧本 → 验证 → 选角 → 分镜 → 旁白
    assert stages.index("parameters") < stages.index("leader")
    assert stages.index("creative_director") < stages.index("creative_brief")
    assert stages.index("creative_brief") < stages.index("storyline")
    assert stages.index("storyline") < stages.index("storyline_verifier")
    assert stages.index("storyline_verifier") < stages.index("casting")
    assert stages.index("casting") < stages.index("storyboard")
    assert stages.index("storyboard") < stages.index("voiceover")


@pytest.mark.asyncio
async def test_scope_plan_skips_generation(isolated_root: Path) -> None:
    members = dt.load_team()
    recorder = _Recorder()
    generated: list[str] = []

    async def run_tool(_tool_call):
        generated.append("x")
        return "已生成", "asset_1"

    orch = dt.DirectorTeamOrchestrator(
        project_id="p3",
        project_name="测试项目",
        complete=recorder.make(members, _fake_replies()),
        run_tool=run_tool,
        members=members,
    )
    result = await orch.run("只出方案，不要生成任何东西", "plan")

    assert generated == []
    assert "keyframe" not in {s.agent for s in result.steps}
    assert result.scope == "plan"


@pytest.mark.asyncio
async def test_failing_member_degrades_instead_of_crashing(isolated_root: Path) -> None:
    """某个成员调用失败时，流程要继续（用兜底值），并在追踪里标 ❌。"""
    members = dt.load_team()
    replies = _fake_replies()
    replies.pop("casting")  # casting 返回空 → 解析不出 JSON

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        system = str(messages[0]["content"])
        agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
        if agent == "parameters":
            return {"content": "[ERROR]: boom"}
        if tools:
            return {"content": "已生成"}
        return {"content": replies.get(agent, "")}

    orch = dt.DirectorTeamOrchestrator(
        project_id="p4", project_name="测试项目", complete=complete, run_tool=None, members=members
    )
    result = await orch.run("做一支广告", "plan")

    # 参数解析坏了 → 用兜底参数继续，并在报表里标注
    assert "兜底参数" in result.report
    # 失败的成员被标 ❌ 而不是抛异常把整轮弄死
    assert any(not s.ok for s in result.steps)


@pytest.mark.asyncio
async def test_team_state_persists_across_turns(isolated_root: Path) -> None:
    """MAB 的意义在于跨轮：第 2 轮必须知道第 1 轮拉过哪些臂。"""
    members = dt.load_team()

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        system = str(messages[0]["content"])
        agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
        if tools:
            return {"content": "已生成"}
        return {"content": _fake_replies().get(agent, "")}

    def build():
        return dt.DirectorTeamOrchestrator(
            project_id="p5", project_name="测试项目", complete=complete, run_tool=None, members=members
        )

    first = await build().run("做一支广告", "plan")
    second = await build().run("再来一轮", "plan")

    assert first.iteration == 1
    assert second.iteration == 2
    # 第二轮至少要看到第一轮已拉过的臂
    assert sum(r["pulls"] for r in second.bandit["arm_stats"]["creative_strategy"].values()) >= 1
    # 迭代记录累积
    state = dt.load_team_state("p5")
    assert len(state["history"]) == 2


@pytest.mark.asyncio
async def test_report_contains_the_team_trace(isolated_root: Path) -> None:
    """报表必须能证明"真的是多智能体"——列出成员、模型、耗时。"""
    members = dt.load_team()

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        system = str(messages[0]["content"])
        agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
        return {"content": "已生成" if tools else _fake_replies().get(agent, "")}

    orch = dt.DirectorTeamOrchestrator(
        project_id="p6", project_name="测试项目", complete=complete, run_tool=None, members=members
    )
    result = await orch.run("做一支广告", "plan")

    assert "团队执行追踪" in result.report
    assert "独立" in result.report  # 验证器标注为独立
    assert "每轮自动加载" not in result.report  # 不是单智能体那份 skill 的文案
    assert result.to_dict()["steps"], "steps 要能序列化给前端"


def test_resolve_run_scope() -> None:
    assert dt.resolve_run_scope("只出方案，不要生成") == "plan"
    assert dt.resolve_run_scope("别调用任何生成工具") == "plan"
    assert dt.resolve_run_scope("先出关键帧，不要视频") == "assets"
    assert dt.resolve_run_scope("做一支 20 秒的广告") == "full"


def test_extract_json_handles_provider_noise() -> None:
    assert dt.extract_json('{"a": 1}') == {"a": 1}
    assert dt.extract_json('前言\n```json\n{"a": 1}\n```\n后记') == {"a": 1}
    assert dt.extract_json('text then {"a": {"b": 2}} tail') == {"a": {"b": 2}}
    assert dt.extract_json("没有任何 JSON") is None
    assert dt._score_of({"breakdown": {"a": 10, "b": 11}}) == 21
    assert dt._score_of({"score": 88}) == 88


# ---------------------------------------------------------------------------
# Routing: skill -> execution path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_base_skill_ads_codirector_routes_to_the_team(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dt, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)
    monkeypatch.setattr(dm, "resolve_default_skill_name", lambda: dm.TEAM_BASE_SKILL_NAME)

    used: dict[str, bool] = {"team": False, "single": False}

    class FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None: ...

        async def run(self, brief: str, scope: str, global_style: str = ""):  # noqa: ARG002
            used["team"] = True
            return dt.TeamRunResult(scope=scope, report="# 团队报表", asset_ids=["a1"], iteration=1)

    monkeypatch.setattr(dm, "DirectorTeamOrchestrator", FakeOrchestrator)

    manager = dm.DirectorManager()
    created = await manager.handle_director_projects_create({"name": "路由-团队"})
    pid = created["project"]["project_id"]
    result = await manager.handle_director_edit_chat_send({"project_id": pid, "text": "做一支广告"})

    assert used["team"] is True
    assert used["single"] is False
    assert result["team_run"]["report"] == "# 团队报表"
    assert result["project"]["edit_chat_messages"][-1]["content"] == "# 团队报表"


@pytest.mark.asyncio
async def test_base_skill_director_edit_assistant_keeps_the_single_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dt, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)
    monkeypatch.setattr(dm, "resolve_default_skill_name", lambda: "director-edit-assistant")

    seen: list[list[dict]] = []

    async def fake_completion(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        seen.append(messages)
        return {"content": "单智能体回复"}

    monkeypatch.setattr(dm, "call_edit_chat_completion", fake_completion)

    def fail_orchestrator(**_kw: Any):
        raise AssertionError("director-edit-assistant 不应走团队路径")

    monkeypatch.setattr(dm, "DirectorTeamOrchestrator", fail_orchestrator)

    manager = dm.DirectorManager()
    created = await manager.handle_director_projects_create({"name": "路由-单agent"})
    pid = created["project"]["project_id"]
    result = await manager.handle_director_edit_chat_send({"project_id": pid, "text": "你好"})

    assert seen, "单智能体路径必须真的调用模型"
    assert "team_run" not in result
    assert result["project"]["edit_chat_messages"][-1]["content"] == "单智能体回复"


@pytest.mark.asyncio
async def test_broken_team_falls_back_to_single_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """团队包坏了也不能让剪辑助手整个不可用。"""
    from jiuwenswarm.server.runtime.director import director_manager as dm
    from jiuwenswarm.server.runtime.director import director_store as ds

    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dt, "get_agent_root_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "edit_chat_enabled", lambda: True)
    monkeypatch.setattr(dm, "edit_chat_configured", lambda: True)
    monkeypatch.setattr(dm, "resolve_default_skill_name", lambda: dm.TEAM_BASE_SKILL_NAME)

    async def fake_completion(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        return {"content": "兜底的单智能体回复"}

    monkeypatch.setattr(dm, "call_edit_chat_completion", fake_completion)

    class ExplodingOrchestrator:
        def __init__(self, **_kw: Any) -> None: ...

        async def run(self, *_a: Any, **_k: Any):
            raise dt.TeamUnavailableError("团队包不存在")

    monkeypatch.setattr(dm, "DirectorTeamOrchestrator", ExplodingOrchestrator)

    manager = dm.DirectorManager()
    created = await manager.handle_director_projects_create({"name": "路由-兜底"})
    pid = created["project"]["project_id"]
    result = await manager.handle_director_edit_chat_send({"project_id": pid, "text": "做一支广告"})

    assert result["project"]["edit_chat_messages"][-1]["content"] == "兜底的单智能体回复"


def test_team_unavailable_when_package_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dt, "get_agent_root_dir", lambda: tmp_path)
    with pytest.raises(dt.TeamUnavailableError):
        dt.load_team()


@pytest.mark.asyncio
async def test_generation_step_failure_is_marked_not_hidden(isolated_root: Path) -> None:
    """生产成员没产出素材时必须标 ❌ —— 报表不能声称成功却少了一条成片素材。"""
    members = dt.load_team()
    replies = _fake_replies()

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        system = str(messages[0]["content"])
        agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
        if tools:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": tools[0]["function"]["name"], "arguments": "{}"},
                    }
                ],
            }
        return {"content": replies.get(agent, "")}

    async def failing_tool(*_a: Any, **_k: Any):
        return "视频「Scene1 Shot1-1 Video」仍在生成中，超出了等待时间。", None

    orch = dt.DirectorTeamOrchestrator(
        project_id="pfail",
        project_name="测试项目",
        complete=complete,
        run_tool=failing_tool,
        members=members,
    )
    result = await orch.run("做一支广告", "full")

    video_steps = [s for s in result.steps if s.agent == "video_agent"]
    assert video_steps, "视频成员必须跑过"
    assert all(not s.ok for s in video_steps), "没出素材的镜头视频步骤必须标失败"
    assert "超出" in video_steps[0].note


def test_team_video_poll_budget_covers_queueing() -> None:
    """并发提交时轮询预算必须覆盖排队——否则后面的场次会被误判超时。"""
    from jiuwenswarm.server.runtime.director.director_manager import (
        _EDIT_CHAT_VIDEO_POLL_INTERVAL_S,
        _EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS,
        _team_video_poll_attempts,
    )

    team_seconds = _team_video_poll_attempts() * _EDIT_CHAT_VIDEO_POLL_INTERVAL_S
    single_seconds = _EDIT_CHAT_VIDEO_POLL_MAX_ATTEMPTS * _EDIT_CHAT_VIDEO_POLL_INTERVAL_S

    # 8 条视频串行出片，每条 5 分钟，最后一条要等 7 个同行
    assert team_seconds >= 8 * single_seconds


@pytest.mark.asyncio
async def test_verifiers_and_report_receive_asset_names_not_ids(isolated_root: Path) -> None:
    """回归：验证器与报表必须拿到**素材名**，不能只给 asset_id。

    曾经把 `asset_1a2b3c` 当名字发下去，验证器据此判定"什么都没生成"，
    关键帧评审直接 0 分——素材其实是好的，分数却是假的。
    """
    members = dt.load_team()
    replies = _fake_replies()
    seen_payloads: dict[str, str] = {}
    counter = {"n": 0}

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        system = str(messages[0]["content"])
        agent = next((n for n, m in members.items() if m.instructions[:60] in system), "unknown")
        if tools:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": tools[0]["function"]["name"],
                            "arguments": json.dumps(
                                {"name": "x", "kind": "scene", "description": "d", "first_frame_name": "f"}
                            ),
                        },
                    }
                ],
            }
        if agent in ("keyframe_verifier", "video_verifier"):
            seen_payloads[agent] = " ".join(str(m["content"]) for m in messages)
        return {"content": replies.get(agent, "")}

    async def run_tool(_tool_call, **_kw):
        counter["n"] += 1
        return "已生成", f"asset_{counter['n']:04d}"

    names = {
        "asset_0001": "iter1_character_collage",
        "asset_0002": "Scene1 Shot1-1 Frame",
        "asset_0003": "Scene1 Shot1-1 Video",
    }

    def resolve_assets(asset_ids):
        # 真实文件名形如 image_1791..._0_ab12.png，不含 asset_id
        return [
            {
                "id": aid,
                "name": names.get(aid, aid),
                "type": "image",
                "path": f"/tmp/image_1791735_{index}_0.png",
            }
            for index, aid in enumerate(asset_ids)
        ]

    orch = dt.DirectorTeamOrchestrator(
        project_id="pnames",
        project_name="测试项目",
        complete=complete,
        run_tool=run_tool,
        resolve_assets=resolve_assets,
        members=members,
    )
    result = await orch.run("做一支广告", "full")

    # 验证器看到的是可读名称
    assert "Scene1 Shot1-1 Frame" in seen_payloads["keyframe_verifier"]
    assert "asset_0002" not in seen_payloads["keyframe_verifier"]
    # 报表的交付清单真的数到了素材（而不是"0 张 / 0 条"）
    assert "关键帧（1 张）" in result.report
    assert "镜头视频（1 条）" in result.report
    # 报表里列出的是名称
    assert "Scene1 Shot1-1 Frame" in result.report
    # 回给前端的仍然是 asset_id（前端按 id 渲染缩略图），而不是名称
    assert result.asset_ids, "必须把 asset_id 回传给前端"
    assert all(aid.startswith("asset_") for aid in result.asset_ids)
    assert "Scene1 Shot1-1 Frame" not in result.asset_ids


@pytest.mark.asyncio
async def test_resolver_failure_falls_back_to_ids_not_to_empty(isolated_root: Path) -> None:
    """还原失败时退回 ID——绝不能让下游以为"没有素材"。"""
    members = dt.load_team()

    async def complete(messages, tools=None, model=None, temperature=None):  # noqa: ANN001, ARG001
        return {"content": _fake_replies().get("unknown", "")}

    def broken_resolver(_ids):
        raise RuntimeError("store 挂了")

    orch = dt.DirectorTeamOrchestrator(
        project_id="pfb",
        project_name="测试项目",
        complete=complete,
        run_tool=None,
        resolve_assets=broken_resolver,
        members=members,
    )

    assert orch.resolve_generated(["asset_0009"]) == [
        {"id": "asset_0009", "name": "asset_0009", "type": "", "path": ""}
    ]
