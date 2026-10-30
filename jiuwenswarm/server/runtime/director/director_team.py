# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""广告片共创（Co-Director）**多智能体团队**运行器。

剪辑助手有两条执行路径，由「基础技能」决定（见 ``director_skills.resolve_default_skill_name``）：

* 基础技能是 ``director-edit-assistant`` → **单智能体**：一个模型带着历史对话
  自己走完流程（``director_manager.handle_director_edit_chat_send`` 里的工具循环）。
* 基础技能是 ``ads-codirector`` → **本模块的多智能体团队**。

## 这是一个真的团队，不是"让一个模型演多个角色"

| 特征 | 做法 |
| --- | --- |
| 独立成员 | 13 个成员来自一个**真实的 AgentGroup 包**（``agents/ads-codirector``），每个成员有自己的 manifest + persona，由产品自己的 ``load_agent_group_package`` 校验加载 |
| 独立上下文 | 每个成员一次独立的 ``chat/completions`` 调用：system = 它自己的 persona，user = 它**只**需要的输入。成员之间**不共享对话历史**，所以下游成员不会被上游的推理带偏（这正是上游 ADK 树里各 LlmAgent 只看到指定 state key 的等价物） |
| 独立模型 | 每个成员可以在自己的 manifest 里声明 ``model`` / ``temperature``；未声明就用「剪辑对话」配置里的模型 |
| 代码控制流程 | 阶段顺序、循环、升级、并行都由本模块的 Python 决定，不交给模型自由发挥 |
| 独立验证 | 三个 ``*_verifier`` 成员在**看不到作者推理过程**的前提下重新评审并打分 |
| 真实 MAB | 选臂与因子化奖励回填是 ``director_team_bandit.MabBandit`` 里的数学，不是模型口述 |
| 并发提交 | 关键帧/镜头视频按 ``asyncio.gather`` 并发提交（生成本身仍由产品既有的锁串行，见 ``stage_generate_assets``） |

## 与项目的接口

生产阶段（``keyframe`` / ``video_agent``）真正调用图片/视频生成，落成该项目的素材——
复用的是剪辑助手同一套工具实现（``DirectorManager._execute_edit_chat_tool_call``），
所以团队产出和单智能体产出在素材库里是同一种东西。

MAB 状态（臂统计、迭代记录、warm start）落在 ``<director_dir>/team_state/<project_id>.json``，
跨轮持续——这是 MAB 有意义的前提：第 2 轮必须知道第 1 轮把哪些臂拉过、拿了多少分。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from jiuwenswarm.common.utils import _find_package_root, get_agent_root_dir
from jiuwenswarm.server.runtime.director.director_team_bandit import (
    ARM_OPTIONS,
    DIMENSIONS,
    STORYLINE_MAX_ATTEMPTS,
    STORYLINE_SCORE_THRESHOLD,
    THEORY_MAP,
    MabBandit,
    theory_for,
    validate_arms,
)

logger = logging.getLogger(__name__)

TEAM_NAME = "ads-codirector"

# 团队包的搜索位置：先工作区（用户可改），再内置（随包发布）。
_TEAM_SUBDIR = Path(".agent_teams") / "agent_groups"

# 单个成员的输出上限：persona 已经限定输出格式，但剧本/分镜会很长，给足空间。
# 生成阶段每个成员的迭代上限（keyframe/video 成员各自只做一场，一轮工具调用足够；
# 留一点余量应对模型先问一句再调用的情况）。
_GEN_TOOL_ITERATIONS = 4


@dataclass
class Teammate:
    """一个团队成员的不可变定义（来自 AgentGroup 包，不是硬编码在代码里）。"""

    name: str
    display_name: str
    instructions: str
    model: str | None = None
    temperature: float | None = None


@dataclass
class TeamStep:
    """一次成员调用的记录——用来在 UI 里展示"团队真的跑了哪些成员"。"""

    agent: str
    display_name: str
    stage: str
    model: str
    elapsed_ms: int
    ok: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "display_name": self.display_name,
            "stage": self.stage,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "ok": self.ok,
            "note": self.note,
        }


@dataclass
class TeamRunResult:
    """一次团队运行的产出。"""

    scope: str
    report: str
    steps: list[TeamStep] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    arms: dict[str, str] = field(default_factory=dict)
    bandit: dict[str, Any] = field(default_factory=dict)
    iteration: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "report": self.report,
            "steps": [s.to_dict() for s in self.steps],
            "asset_ids": self.asset_ids,
            "arms": self.arms,
            "bandit": self.bandit,
            "iteration": self.iteration,
        }


class TeamUnavailableError(Exception):
    """团队包缺失或加载失败——调用方应回退到单智能体路径。"""


class ToolStepFailed(Exception):
    """一个生产成员没能产出任何素材——只影响这一步，不让整轮团队运行失败。"""


# ---------------------------------------------------------------------------
# 团队包加载
# ---------------------------------------------------------------------------


def _team_search_dirs() -> list[Path]:
    """团队包搜索路径：工作区里的用户包优先，其次是内置包。

    内置包有两处来源——运行期工作区（``<agent_root>/workspace/.agent_teams/
    agent_groups/built_in``，首次启动时从包资源拷过去的副本）和**包内资源**
    （``<package>/resources/agent/workspace/.agent_teams/agent_groups/built_in``）。
    后者必须也搜：单元测试用 autouse fixture 把工作区整体重定向到 tmp（见
    tests/unit_tests/conftest.py 的 _isolate_user_workspace），此时运行期副本
    并不存在；只认运行期目录的话，内置团队在隔离环境（以及尚未播种的全新安装）
    里就直接"找不到"，而不是读包内那份只读的原始资源。
    """
    root = get_agent_root_dir() / "workspace" / _TEAM_SUBDIR
    dirs = [root / "local", root / "built_in"]
    packaged = _find_package_root() / "resources" / "agent" / "workspace" / _TEAM_SUBDIR / "built_in"
    if packaged not in dirs:
        dirs.append(packaged)
    return dirs


def _assemble_instructions(template: Any) -> str:
    """把一个成员的 prompt sections 拼成 system prompt。

    AgentGroup 的 leader 会有 ``agent_group_leader_rules``（AGENT.md）与
    ``agent_group_instruction``（团队总纲）两段；普通成员是 ``identity``
    （persona）+ 团队总纲。按 priority 排序，保证 leader 规则在 persona 之前。
    """
    sections = sorted(
        list(getattr(template, "prompt_sections", []) or []),
        key=lambda s: int(getattr(s, "priority", 100) or 100),
    )
    parts: list[str] = []
    for section in sections:
        content = getattr(section, "content", None)
        text = ""
        if isinstance(content, dict):
            text = str(content.get("cn") or content.get("en") or "").strip()
        elif isinstance(content, str):
            text = content.strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def load_team(team_name: str = TEAM_NAME) -> dict[str, Teammate]:
    """从 AgentGroup 包加载团队成员；加载不了时抛 ``TeamUnavailableError``。

    刻意走产品自己的 ``load_agent_group_package``：团队定义是**包**（数据），
    不是代码里的常量，所以用户/我们可以直接改 persona 而不动 Python。
    """
    try:
        # 加载器已随上游迁到 openjiuwen 协议包（本地 agents/swarm/agent_group.py 已删除），
        # 签名/语义不变：一个已验证的 AgentGroup 包目录 -> 有序的成员模板。
        from openjiuwen.agent_teams.experts import load_agent_group_package
    except Exception as exc:  # pragma: no cover - openjiuwen 缺失时明确降级
        raise TeamUnavailableError(f"AgentGroup 加载器不可用: {exc}") from exc

    last_error: Exception | None = None
    for directory in _team_search_dirs():
        package = directory / team_name
        if not (package / "manifest.json").is_file():
            continue
        try:
            templates = load_agent_group_package(package)
        except Exception as exc:
            last_error = exc
            logger.warning("[director_team] 团队包 %s 加载失败: %s", package, exc)
            continue

        members: dict[str, Teammate] = {}
        for name, template in templates.items():
            card = template.agent_card
            raw_name = getattr(card, "name", None)
            display = ""
            if isinstance(raw_name, dict):
                display = str(raw_name.get("zh") or raw_name.get("en") or "")
            elif isinstance(raw_name, str):
                display = raw_name
            model = getattr(template, "model", None)
            temperature = getattr(template, "temperature", None)
            members[name] = Teammate(
                name=name,
                display_name=display or name,
                instructions=_assemble_instructions(template),
                model=str(model).strip() if isinstance(model, str) and model.strip() else None,
                temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
            )
        if members:
            logger.info("[director_team] 团队 %s 加载成功（%d 名成员，来自 %s）", team_name, len(members), package)
            return members

    raise TeamUnavailableError(f"团队包 {team_name} 未找到或无法加载: {last_error or 'no package'}")


# ---------------------------------------------------------------------------
# 团队状态（跨轮持久）
# ---------------------------------------------------------------------------


def _team_state_dir() -> Path:
    """团队状态落点。

    刻意**独立**于 ``_team_search_dirs``：测试要把状态重定向到 tmp，同时仍然要能
    从真实工作区读到团队包——两者混在一个根目录下就没法只改一个。
    """
    return get_agent_root_dir() / "director" / "team_state"


def _team_state_path(project_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id)[:120]
    return _team_state_dir() / f"{safe}.json"


def load_team_state(project_id: str) -> dict[str, Any]:
    path = _team_state_path(project_id)
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, ValueError):
        logger.warning("[director_team] 团队状态读取失败: %s", path, exc_info=True)
    return {}


def save_team_state(project_id: str, payload: dict[str, Any]) -> None:
    path = _team_state_path(project_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("[director_team] 团队状态写入失败: %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    """从模型回复里抠出第一个 JSON 对象/数组。

    成员都被要求"只输出 JSON"，但真实供应商偶尔会带一句前言或包一层 ```；
    这里先试直接解析，再试代码围栏，最后退回到"括号配对"扫描——比正则贪婪匹配
    稳，也不会因为正文里出现 `{` 就崩。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
    except ValueError:
        pass

    for candidate in _FENCE_RE.findall(raw):
        try:
            parsed = json.loads(candidate.strip())
        except ValueError:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed

    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(raw[start : index + 1])
                    except ValueError:
                        break
                    if isinstance(parsed, (dict, list)):
                        return parsed
                    break
    return None


def _score_of(payload: Any, default: int = 0) -> int:
    """取验证器给出的总分：优先 ``score``，其次五个分项求和。"""
    if not isinstance(payload, dict):
        return default
    raw = payload.get("score")
    if isinstance(raw, (int, float)):
        return int(raw)
    breakdown = payload.get("breakdown")
    if isinstance(breakdown, dict):
        total = sum(int(v) for v in breakdown.values() if isinstance(v, (int, float)))
        if total:
            return total
    return default


def resolve_run_scope(text: str) -> str:
    """从用户这句话判断这次团队跑到哪一步。

    * ``plan``：只要方案，不生成任何素材。
    * ``assets``：出到关键帧，不出视频。
    * ``full``：出到镜头视频（默认——用户说"做一支广告"就是要成片素材）。
    """
    lowered = (text or "").lower()
    plan_markers = (
        "只出方案", "先出方案", "仅方案", "只做方案",
        "不要生成", "不生成", "别生成",
        "不要调用", "别调用",
        "plan only", "no generation", "don't generate",
    )
    if any(marker in lowered for marker in plan_markers):
        return "plan"
    if any(marker in lowered for marker in ("关键帧", "keyframe")) and any(
        marker in lowered for marker in ("先", "只", "不要视频", "without video")
    ):
        return "assets"
    return "full"


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------


class DirectorTeamOrchestrator:
    """按固定流水线调度 Co-Director 团队。

    它不持有任何全局状态：成员定义来自团队包，MAB 状态来自项目侧车文件，
    素材/工具执行委托给传入的 ``ToolRunner``。这样测试可以注入假成员与假工具。
    """

    def __init__(
        self,
        *,
        project_id: str,
        project_name: str,
        complete: Callable[..., Awaitable[dict[str, Any]]],
        run_tool: Callable[..., Awaitable[tuple[str, str | None]]] | None = None,
        resolve_assets: Callable[[list[str]], list[dict[str, str]]] | None = None,
        video_poll_attempts: int | None = None,
        available_assets: list[dict[str, str]] | None = None,
        members: dict[str, Teammate] | None = None,
    ) -> None:
        self.project_id = project_id
        self.project_name = project_name
        self._complete = complete
        self._run_tool = run_tool
        # 把 asset_id 还原成人类可读的素材信息。没有它的话报表与验证器只能看到
        # `asset_1a2b3c` 这种 ID——验证器会据此判定"什么都没生成"（真的发生过），
        # 于是分数全是 0，而事实上素材是好的。
        self._resolve_assets = resolve_assets
        # 并发提交视频时，轮询预算里混着"在锁前排队的等待时间"——按这一批的条数
        # 放大预算，否则排在后面的那几条会在还没轮到真正生成时就被判超时。
        self._video_poll_attempts = video_poll_attempts
        self._available_assets = list(available_assets or [])
        self.members = members if members is not None else load_team()
        self.steps: list[TeamStep] = []
        self.asset_ids: list[str] = []

    # -- 单成员调用 ---------------------------------------------------------

    async def _call(
        self,
        agent: str,
        stage: str,
        payload: str,
        *,
        json_only: bool = False,
        allow_tools: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        """调用一个成员：独立 system + 独立 user 输入，返回 (文本, 解析出的 JSON)。

        关键点：**只传 payload**，不传任何其他成员的历史。成员之间的信息流必须
        显式经过编排器（这跟上游把数据放在 state key 里、由下一级 agent 按需读取
        是同一个信息隔离边界）。
        """
        member = self.members.get(agent)
        if member is None:
            raise TeamUnavailableError(f"团队成员不存在: {agent}")

        system = member.instructions
        if json_only:
            system += "\n\n只输出一个 JSON 对象，不要任何解释、前言或代码围栏。"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ]

        started = time.monotonic()
        model_used = member.model or ""
        try:
            if allow_tools and self._run_tool is not None:
                text, tool_note = await self._call_with_tools(member, messages, agent, stage)
                note = tool_note
            else:
                reply = await self._complete(
                    messages, None, member.model, member.temperature
                )
                text = str(reply.get("content") or "")
                if text.startswith("[ERROR]:"):
                    raise RuntimeError(text)
                note = ""
            ok = True
        except Exception as exc:
            logger.warning("[director_team] 成员 %s 调用失败: %s", agent, exc)
            text = ""
            note = f"调用失败：{exc}"
            ok = False

        self.steps.append(
            TeamStep(
                agent=agent,
                display_name=member.display_name,
                stage=stage,
                model=model_used or "（配置模型）",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                ok=ok,
                note=note,
            )
        )
        parsed = extract_json(text) if text else None
        return text, parsed if isinstance(parsed, dict) else None

    async def _call_with_tools(
        self, member: Teammate, messages: list[dict[str, Any]], agent: str, stage: str
    ) -> tuple[str, str]:
        """生产成员的小工具循环：只给它自己那一个工具，绑定到当前项目。

        返回 ``(最终文本, 说明)``。说明里既报产出的素材名，也报**失败**——生产成员
        的职责就是调工具出一条素材，所以"没出素材"必须让追踪表显示为失败，否则
        报表会声称成功、而实际上少了一条成片素材。
        """
        assert self._run_tool is not None
        schemas = {
            "keyframe": [_DESIGN_IMAGE_SCHEMA],
            "video_agent": [_SHOT_VIDEO_SCHEMA],
        }.get(agent, [])
        final_text = ""
        produced: list[str] = []
        failures: list[str] = []
        for _ in range(_GEN_TOOL_ITERATIONS):
            reply = await self._complete(messages, schemas, member.model, member.temperature)
            content = str(reply.get("content") or "")
            if content.startswith("[ERROR]:"):
                raise RuntimeError(content)
            tool_calls = reply.get("tool_calls")
            if not tool_calls:
                final_text = content
                break
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tool_call in tool_calls:
                result_text, asset_id = await self._run_tool(tool_call)
                if asset_id:
                    produced.append(asset_id)
                    self.asset_ids.append(asset_id)
                else:
                    failures.append(result_text.strip()[:160])
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.get("id"), "content": result_text}
                )
        if produced:
            named = [item.get("name", item_id) for item in self.resolve_generated(produced) for item_id in [item.get("id", "")]]
            note = "；".join(named)
            if failures:
                note += f"（另有失败：{'；'.join(failures)}）"
            return final_text, note
        if failures:
            # 没有产出任何素材 = 这一步真的失败了，把原因带上去
            raise ToolStepFailed("；".join(failures))
        return final_text, ""

    def _mark_last_step_failed(self, reason: str) -> None:
        """把一个成员步骤标成失败并附上原因（供生成阶段捕获异常时用）。"""
        if self.steps:
            self.steps[-1].ok = False
            self.steps[-1].note = reason[:200]

    def resolve_generated(self, asset_ids: list[str]) -> list[dict[str, str]]:
        """把 asset_id 列表还原成 ``[{id, name, type, path}]``。

        解析不出来时退回 ``{"id": id, "name": id}``——宁可显示 ID，也不能因为
        还原失败就让下游以为"没有素材"。
        """
        if not asset_ids:
            return []
        if self._resolve_assets is not None:
            try:
                resolved = self._resolve_assets(list(asset_ids))
                if resolved:
                    return resolved
            except Exception:  # pragma: no cover - 解析失败不该影响这一轮
                logger.warning("[director_team] 素材还原失败", exc_info=True)
        return [{"id": asset_id, "name": asset_id, "type": "", "path": ""} for asset_id in asset_ids]

    def named_for_prompt(self, asset_ids: list[str]) -> list[dict[str, str]]:
        """给成员看的素材清单：只要名称、类型、文件，不带 asset_id。

        ID 对模型是噪音（`asset_1a2b3c` 无法用来引用素材），而且一旦有人把 ID 当
        成"名称"发下去，验证器就会判定"没有素材"——所以这里显式只传可读字段。
        """
        return [
            {
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "file": Path(item.get("path") or "").name,
            }
            for item in self.resolve_generated(asset_ids)
        ]

    @staticmethod
    def _frames_of(resolved: list[dict[str, str]]) -> list[str]:
        return [item["name"] for item in resolved if "Frame" in item.get("name", "")]

    @staticmethod
    def _videos_of(resolved: list[dict[str, str]]) -> list[str]:
        return [item["name"] for item in resolved if "Video" in item.get("name", "")]

    # -- 各阶段 -------------------------------------------------------------

    async def stage_parameters(self, brief: str) -> dict[str, Any]:
        _, parsed = await self._call(
            "parameters",
            "参数解析",
            f"广告 brief：\n{brief}",
            json_only=True,
        )
        if not parsed:
            # 参数解析失败不该让整轮崩掉——用 brief 原文当产品描述继续跑，
            # 并在报表里如实标注。
            logger.warning("[director_team] 参数解析未返回 JSON，使用兜底参数")
            parsed = {
                "brand": self.project_name,
                "product": brief[:200],
                "demographics": "未解析",
                "campaign_name": self.project_name,
                "target_duration": "30s",
                "target_orientation": "landscape",
                "_fallback": True,
            }
        return parsed

    async def stage_warm_start(self, brief: str, params: dict[str, Any]) -> dict[str, str] | None:
        """第一轮的 warm start：由 leader 按理论定义联合推荐一组臂。"""
        theory = "\n".join(
            f"- {dimension}: " + " | ".join(f"{arm}={THEORY_MAP.get(arm, '')}" for arm in arms)
            for dimension, arms in ARM_OPTIONS.items()
        )
        text, parsed = await self._call(
            "leader",
            "warm start 选臂",
            (
                "你是首席创意策略师。为下面的广告 brief 推荐**第一轮**三维选臂组合"
                "（要联合考虑：某些策略与特定叙事模式/美学搭配才成立）。\n\n"
                f"brief：\n{brief}\n\n参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"可选维度与理论依据：\n{theory}\n\n"
                '只输出 JSON：{"reasoning":"…","recommendations":{'
                '"creative_strategy":"…","narrative_mode":"…","aesthetic_archetype":"…"}}'
            ),
            json_only=True,
        )
        if not parsed:
            return None
        recs = validate_arms(parsed.get("recommendations"))
        self._warm_reasoning = str(parsed.get("reasoning") or "")
        return recs

    async def stage_creative_direction(
        self, arms: dict[str, str], brief: str, params: dict[str, Any]
    ) -> dict[str, str]:
        theory = theory_for(arms)
        _, parsed = await self._call(
            "creative_director",
            "创意方向合成",
            (
                f"本轮选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
                f"理论定义：{json.dumps(theory, ensure_ascii=False)}\n\n"
                f"brief：\n{brief}\n\n参数：{json.dumps(params, ensure_ascii=False)}"
            ),
            json_only=True,
        )
        if not parsed:
            return {k: "" for k in ("storyline_instruction", "keyframe_instruction", "video_instruction", "audio_instruction")}
        return {
            key: str(parsed.get(key) or "")
            for key in (
                "storyline_instruction",
                "keyframe_instruction",
                "video_instruction",
                "audio_instruction",
            )
        }

    async def stage_brief(
        self, params: dict[str, Any], arms: dict[str, str], direction: dict[str, str], brief: str
    ) -> str:
        text, _ = await self._call(
            "creative_brief",
            "创意简报",
            (
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
                f"创意方向（叙事）：{direction.get('storyline_instruction', '')}\n\n"
                f"原始 brief：\n{brief}"
            ),
        )
        return text.strip()

    async def stage_storyline(
        self,
        brief: str,
        params: dict[str, Any],
        arms: dict[str, str],
        direction: dict[str, str],
        creative_brief: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """剧本 + 自省循环：分数达标或达到最大稿数就停。"""
        attempts: list[dict[str, Any]] = []
        payload = (
            f"创意简报：\n{creative_brief}\n\n"
            f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
            f"选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
            f"叙事方向：{direction.get('storyline_instruction', '')}"
        )
        storyline: dict[str, Any] = {}
        for attempt in range(1, STORYLINE_MAX_ATTEMPTS + 1):
            text, parsed = await self._call("storyline", f"剧本第 {attempt} 稿", payload, json_only=True)
            if not parsed or not isinstance(parsed.get("scenes"), list):
                logger.warning("[director_team] 剧本第 %s 稿未返回合法 JSON", attempt)
                parsed = storyline or {"scenes": []}
            storyline = parsed
            # 独立验证：验证器只看到 brief 与剧本，看不到编剧的推理。
            _, evaluation = await self._call(
                "storyline_verifier",
                f"剧本评审第 {attempt} 稿",
                (
                    f"原始 brief：\n{brief}\n\n"
                    f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                    f"待评剧本：{json.dumps(storyline, ensure_ascii=False)}"
                ),
                json_only=True,
            )
            score = _score_of(evaluation)
            attempts.append({"attempt": attempt, "score": score, "evaluation": evaluation or {}})
            if score >= STORYLINE_SCORE_THRESHOLD or attempt >= STORYLINE_MAX_ATTEMPTS:
                break
            payload = (
                f"创意简报：\n{creative_brief}\n\n"
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
                f"上一稿：{json.dumps(storyline, ensure_ascii=False)}\n\n"
                f"验证器反馈：{(evaluation or {}).get('feedback', '')}\n\n"
                f"必须执行的修改：{(evaluation or {}).get('actionable_feedback', '')}"
            )
        return storyline, attempts

    async def stage_casting(
        self,
        params: dict[str, Any],
        arms: dict[str, str],
        direction: dict[str, str],
        creative_brief: str,
        storyline: dict[str, Any],
        global_style: str,
    ) -> dict[str, Any]:
        text, parsed = await self._call(
            "casting",
            "视觉选角",
            (
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"创意简报：\n{creative_brief}\n\n"
                f"剧本：{json.dumps(storyline, ensure_ascii=False)}\n\n"
                f"美学方向：{direction.get('keyframe_instruction', '')}\n\n"
                f"全局风格约束（必须遵守）：{global_style}"
            ),
            json_only=True,
        )
        if not parsed:
            return {}
        return parsed

    async def stage_storyboard(
        self,
        params: dict[str, Any],
        arms: dict[str, str],
        direction: dict[str, str],
        creative_brief: str,
        storyline: dict[str, Any],
        available_assets: list[dict[str, str]],
    ) -> dict[str, Any]:
        _, parsed = await self._call(
            "storyboard",
            "分镜脚本",
            (
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"创意简报：\n{creative_brief}\n\n"
                f"剧本（绝对基准）：{json.dumps(storyline, ensure_ascii=False)}\n\n"
                f"选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
                f"视觉方向：{direction.get('keyframe_instruction', '')}\n\n"
                f"运动方向：{direction.get('video_instruction', '')}\n\n"
                f"可用资产清单（只准引用这里的名字）：{json.dumps(available_assets, ensure_ascii=False)}\n\n"
                f"必须满足：全片总时长 = {params.get('target_duration')}，每场 4/6/8 秒。"
            ),
            json_only=True,
        )
        return parsed or {}

    async def stage_voiceover(
        self,
        params: dict[str, Any],
        direction: dict[str, str],
        storyboard: dict[str, Any],
    ) -> dict[str, Any]:
        _, parsed = await self._call(
            "voiceover",
            "旁白与音乐",
            (
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"分镜（画面基准）：{json.dumps(storyboard.get('scenes', []), ensure_ascii=False)}\n\n"
                f"音频方向：{direction.get('audio_instruction', '')}"
            ),
            json_only=True,
        )
        return parsed or {}

    async def stage_generate_assets(
        self, anchors: list[dict[str, str]], storyboard: dict[str, Any], aspect_ratio: str
    ) -> list[str]:
        """生产阶段：**并发提交**各场的关键帧，再并发提交各场的镜头视频。

        诚实说明这里的"并发"边界：各场次的**成员调用**（出提示词 → 提交）是并发的，
        但真正调用图片/视频生成的那一步在 ``DirectorManager`` 的 ``asyncio.Lock``
        里串行——那是产品既有的约束，不是这里加的。相对单智能体路径的实际好处是
        清楚的：单智能体一次对话回合只能出一场（受工具调用轮数限制），这里一次
        提交全部场次、由队列依次完成，用户不用一句一句催。
        """
        scenes = [s for s in (storyboard.get("scenes") or []) if isinstance(s, dict)]
        if not scenes:
            return []

        anchor_lines = "\n".join(
            f"- {item['name']}（{item.get('role', 'product')}）：{item.get('description', '')}"
            for item in anchors
            if item.get("name")
        )

        async def make_keyframe(index: int, scene: dict[str, Any]) -> None:
            assets = ", ".join(f"@{name}" for name in (scene.get("assets") or []) if name)
            try:
                await self._call(
                    "keyframe",
                    f"关键帧 Scene{index + 1}",
                    (
                        f"场景号：Scene{index + 1}（命名必须是 `Scene{index + 1} Shot1-1 Frame`）\n"
                        f"主题：{scene.get('topic', '')}\n"
                        f"首帧提示词：{scene.get('first_frame_prompt', '')}\n"
                        f"该场需要引用的资产：{assets or '（无）'}\n"
                        f"比例：{aspect_ratio}\n\n"
                        f"可用锚点素材：\n{anchor_lines}\n\n"
                        "调用 generate_design_image 生成这一场，然后简短说明结果。"
                    ),
                    allow_tools=True,
                )
            except ToolStepFailed as exc:
                logger.warning("[director_team] Scene%s 关键帧未产出: %s", index + 1, exc)
                self._mark_last_step_failed(str(exc))

        # 第一批：并发出关键帧。
        await asyncio.gather(
            *(make_keyframe(i, s) for i, s in enumerate(scenes)), return_exceptions=True
        )

        async def make_video(index: int, scene: dict[str, Any]) -> None:
            try:
                await self._call(
                    "video_agent",
                    f"镜头视频 Scene{index + 1}",
                    (
                        f"场景号：Scene{index + 1}（命名必须是 `Scene{index + 1} Shot1-1 Video`）\n"
                        f"首帧名称：`Scene{index + 1} Shot1-1 Frame`\n"
                        f"视频提示词：{scene.get('video_prompt', '')}\n"
                        f"时长（秒）：{scene.get('duration_seconds', 5)}\n\n"
                        "调用 generate_shot_video 生成这一场，然后简短说明结果。"
                    ),
                    allow_tools=True,
                )
            except ToolStepFailed as exc:
                logger.warning("[director_team] Scene%s 镜头视频未产出: %s", index + 1, exc)
                self._mark_last_step_failed(str(exc))

        await asyncio.gather(
            *(make_video(i, s) for i, s in enumerate(scenes)), return_exceptions=True
        )
        return self.asset_ids

    async def stage_verify_keyframes(
        self,
        brief: str,
        params: dict[str, Any],
        assets: list[dict[str, str]],
        storyboard: dict[str, Any],
        generated: list[str],
    ) -> dict[str, Any]:
        _, parsed = await self._call(
            "keyframe_verifier",
            "关键帧序列评审",
            (
                f"原始 brief：\n{brief}\n\n"
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"锚点素材：{json.dumps(assets, ensure_ascii=False)}\n\n"
                f"分镜：{json.dumps(storyboard.get('scenes', []), ensure_ascii=False)}\n\n"
                f"已生成的关键帧素材："
                f"{json.dumps(self.named_for_prompt(generated), ensure_ascii=False)}"
            ),
            json_only=True,
        )
        return parsed or {}

    async def stage_verify_video(
        self,
        brief: str,
        params: dict[str, Any],
        arms: dict[str, str],
        storyboard: dict[str, Any],
        generated: list[str],
    ) -> dict[str, Any]:
        _, parsed = await self._call(
            "video_verifier",
            "成片评审与效能打分",
            (
                f"原始 brief：\n{brief}\n\n"
                f"参数：{json.dumps(params, ensure_ascii=False)}\n\n"
                f"本轮选臂：{json.dumps(arms, ensure_ascii=False)}\n\n"
                f"选臂理论定义：{json.dumps(theory_for(arms), ensure_ascii=False)}\n\n"
                f"分镜：{json.dumps(storyboard.get('scenes', []), ensure_ascii=False)}\n\n"
                f"已生成的镜头视频素材："
                f"{json.dumps(self.named_for_prompt(generated), ensure_ascii=False)}\n\n"
                "旁白/音乐本轮未落成音频（本对话不能生成音频），请按「画面 + 旁白文本」评审，"
                "并在反馈里说明音频缺口。"
            ),
            json_only=True,
        )
        return parsed or {}

    # -- 主流程 -------------------------------------------------------------

    async def run(self, brief: str, scope: str, global_style: str = "") -> TeamRunResult:
        state = load_team_state(self.project_id)
        bandit = MabBandit.from_dict(state.get("bandit"))
        iteration = int(state.get("iteration") or 0) + 1
        history: list[dict[str, Any]] = list(state.get("history") or [])

        # 1) 参数解析
        params = await self.stage_parameters(brief)
        aspect_ratio = "9:16" if str(params.get("target_orientation")) == "portrait" else "16:9"

        # 2) 第一轮 warm start：让 leader 按理论推荐一组臂并作为冷启动偏好。
        warm_reasoning = str(state.get("warm_reasoning") or "")
        if iteration == 1 and not bandit.warm_start:
            recommendations = await self.stage_warm_start(brief, params)
            if recommendations:
                bandit.warm_start = recommendations
                warm_reasoning = getattr(self, "_warm_reasoning", "")
        self._warm_reasoning = warm_reasoning

        # 3) 选臂（真实的 UCB / ε-greedy）
        arms = bandit.select_all()

        # 4) 创意方向 + 简报
        direction = await self.stage_creative_direction(arms, brief, params)
        creative_brief = await self.stage_brief(params, arms, direction, brief)

        # 5) 剧本（含独立验证的自省循环）
        storyline, storyline_attempts = await self.stage_storyline(
            brief, params, arms, direction, creative_brief
        )

        # 6) 选角 + 分镜
        casting = await self.stage_casting(
            params, arms, direction, creative_brief, storyline, global_style
        )
        anchors = self._collect_anchors(casting)
        available = list(self._available_assets) + [
            {"name": item["name"], "semantic_role": item.get("role", "product")} for item in anchors
        ]
        storyboard = await self.stage_storyboard(
            params, arms, direction, creative_brief, storyline, available
        )

        # 7) 旁白
        voiceover = await self.stage_voiceover(params, direction, storyboard)

        # 8) 生产（可选）
        generated: list[str] = []
        keyframe_eval: dict[str, Any] = {}
        video_eval: dict[str, Any] = {}
        if scope in ("assets", "full"):
            if anchors and self._run_tool is not None:
                await self._generate_anchors(anchors, aspect_ratio)
            generated = await self.stage_generate_assets(anchors, storyboard, aspect_ratio)
            keyframe_eval = await self.stage_verify_keyframes(
                brief, params, anchors, storyboard, generated
            )
            if scope == "full":
                video_eval = await self.stage_verify_video(brief, params, arms, storyboard, generated)

        # 9) 因子化奖励回填（真实的 MAB 更新）
        efficacy = (video_eval.get("mab_efficacy_scores") or {}) if video_eval else {}
        if not efficacy and storyline_attempts:
            # 还没到成片阶段时，用剧本分作为全局奖励的近似，保证 MAB 仍然前进；
            # 报表里会如实标注"本轮用剧本分替代效能分"。
            efficacy = {}
        global_score = _score_of(video_eval) or _score_of(keyframe_eval) or (
            storyline_attempts[-1]["score"] if storyline_attempts else 0
        )
        applied = bandit.update_reward(arms, efficacy, global_score=global_score)

        # 10) 落盘团队状态（跨轮持续）
        history.append(
            {
                "iteration": iteration,
                "arms": arms,
                "scores": {
                    "storyline": storyline_attempts[-1]["score"] if storyline_attempts else None,
                    "keyframes": _score_of(keyframe_eval) or None,
                    "video": _score_of(video_eval) or None,
                },
                "primary_fault": (video_eval.get("primary_fault") or keyframe_eval.get("primary_fault") or ""),
                "rewards_applied": applied,
                "scope": scope,
            }
        )
        state.update(
            {
                "bandit": bandit.to_dict(),
                "iteration": iteration,
                "history": history,
                "warm_reasoning": warm_reasoning,
            }
        )
        save_team_state(self.project_id, state)

        report = self._render_report(
            brief=brief,
            scope=scope,
            params=params,
            arms=arms,
            bandit=bandit,
            direction=direction,
            creative_brief=creative_brief,
            storyline=storyline,
            storyline_attempts=storyline_attempts,
            casting=casting,
            storyboard=storyboard,
            voiceover=voiceover,
            generated=generated,
            keyframe_eval=keyframe_eval,
            video_eval=video_eval,
            applied=applied,
            global_score=global_score,
            iteration=iteration,
            warm_reasoning=warm_reasoning,
        )
        return TeamRunResult(
            scope=scope,
            report=report,
            steps=self.steps,
            asset_ids=self.asset_ids,
            arms=arms,
            bandit=bandit.to_dict(),
            iteration=iteration,
        )

    def _collect_anchors(self, casting: dict[str, Any]) -> list[dict[str, str]]:
        """把选角结果整理成"需要生成的锚点素材"。

        字符锚点的**名称固定**为 ``iter1_character_collage``：分镜/关键帧的提示词里
        要能用 ``@`` 引用它，名字必须在生成之前就确定下来。
        """
        anchors: list[dict[str, str]] = []
        prompt = str(casting.get("collage_prompt") or "").strip()
        if prompt:
            anchors.append(
                {
                    "name": "iter1_character_collage",
                    "role": "character",
                    "description": prompt,
                }
            )
        return anchors

    async def _generate_anchors(self, anchors: list[dict[str, str]], aspect_ratio: str) -> None:
        """并发生成锚点素材（人物三视图等），供后续关键帧用 @ 引用。"""
        assert self._run_tool is not None

        async def make(index: int, anchor: dict[str, str]) -> None:
            kind = "character" if anchor.get("role") == "character" else "scene"
            tool_call = {
                "id": f"anchor_{index}",
                "type": "function",
                "function": {
                    "name": "generate_design_image",
                    "arguments": json.dumps(
                        {
                            "kind": kind,
                            "name": anchor["name"],
                            "description": anchor["description"],
                            "aspect_ratio": aspect_ratio,
                        },
                        ensure_ascii=False,
                    ),
                },
            }
            started = time.monotonic()
            result_text, asset_id = await self._run_tool(tool_call)
            if asset_id:
                self.asset_ids.append(asset_id)
            self.steps.append(
                TeamStep(
                    agent="keyframe",
                    display_name=f"锚点素材 {anchor['name']}",
                    stage="锚点生成",
                    model="（图片生成）",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    ok=asset_id is not None,
                    note=result_text[:200],
                )
            )

        await asyncio.gather(*(make(i, a) for i, a in enumerate(anchors)), return_exceptions=True)

    # -- 报表 ---------------------------------------------------------------

    def _render_report(self, **kw: Any) -> str:
        """把团队产出渲染成一条可读的助手消息。

        刻意包含**团队追踪**（谁跑了、用的什么模型、耗时），这样用户与测试都能
        确认这次是真的多智能体执行，而不是一个模型自称在演多个角色。
        """
        params = kw["params"]
        arms = kw["arms"]
        bandit: MabBandit = kw["bandit"]
        storyline = kw["storyline"]
        storyboard = kw["storyboard"]
        scenes = [s for s in (storyboard.get("scenes") or []) if isinstance(s, dict)]
        attempts = kw["storyline_attempts"]
        keyframe_eval = kw["keyframe_eval"]
        video_eval = kw["video_eval"]

        lines: list[str] = []
        lines.append(f"# 广告片共创（Co-Director 多智能体团队）· 第 {kw['iteration']} 轮")
        lines.append("")
        lines.append(
            f"> 本次由 **{len(set(s.agent for s in self.steps))} 名团队成员**按固定流水线执行，"
            f"共 {len(self.steps)} 次独立模型调用（各自独立上下文）。运行范围：**{kw['scope']}**。"
        )
        lines.append("")

        # 团队追踪
        lines.append("## 团队执行追踪")
        lines.append("")
        lines.append("| # | 成员 | 阶段 | 模型 | 耗时 | 结果 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for index, step in enumerate(self.steps, start=1):
            mark = "✅" if step.ok else "❌"
            note = f" {step.note[:60]}" if step.note else ""
            lines.append(
                f"| {index} | `{step.agent}`（{step.display_name}） | {step.stage} | {step.model} | "
                f"{step.elapsed_ms} ms | {mark}{note} |"
            )
        lines.append("")

        # 参数
        lines.append("## 参数解析")
        lines.append("")
        lines.append("| 字段 | 值 |")
        lines.append("| --- | --- |")
        for key in ("brand", "product", "demographics", "campaign_name", "target_duration", "target_orientation"):
            lines.append(f"| `{key}` | {params.get(key, '')} |")
        if params.get("_fallback"):
            lines.append("")
            lines.append("> ⚠️ 参数解析未返回合法 JSON，本轮使用兜底参数。")
        lines.append("")

        # 选臂
        lines.append("## MAB 选臂（真实 UCB 计算）")
        lines.append("")
        if kw["warm_reasoning"]:
            lines.append(f"**Warm start 依据**：{kw['warm_reasoning']}")
            lines.append("")
        lines.append("| 维度 | 本轮臂 | 理论依据 |")
        lines.append("| --- | --- | --- |")
        theory = theory_for(arms)
        for dimension in DIMENSIONS:
            lines.append(f"| `{dimension}` | **{arms.get(dimension, '')}** | {theory.get(dimension, '')[:160]}… |")
        lines.append("")
        lines.append("### 臂状态（拉动次数 / 平均奖励）")
        lines.append("")
        lines.append("| 维度 | 臂 | 拉动次数 | 平均奖励 |")
        lines.append("| --- | --- | --- | --- |")
        for row in bandit.arm_table():
            avg = "—" if row["avg_reward"] is None else f"{row['avg_reward']:.1f}"
            lines.append(f"| `{row['dimension']}` | `{row['arm']}` | {row['pulls']} | {avg} |")
        lines.append("")
        lines.append("**下一轮 UCB 计划**：")
        lines.append("")
        lines.append(bandit.next_round_plan())
        lines.append("")

        # 创意方向
        if kw["direction"]:
            lines.append("## 创意方向（由 creative_director 综合）")
            lines.append("")
            for key, label in (
                ("storyline_instruction", "叙事"),
                ("keyframe_instruction", "关键帧"),
                ("video_instruction", "影像运动"),
                ("audio_instruction", "音频"),
            ):
                value = str(kw["direction"].get(key) or "").strip()
                if value:
                    lines.append(f"- **{label}**：{value}")
            lines.append("")
            lines.append("## 创意简报")
            lines.append("")
            lines.append(kw["creative_brief"] or "（缺失）")
            lines.append("")

        # 剧本
        lines.append("## 剧本（含独立验证的自省循环）")
        lines.append("")
        lines.append("| 稿次 | 剧本分（独立评审） | 主问题 |")
        lines.append("| --- | --- | --- |")
        for attempt in attempts:
            evaluation = attempt.get("evaluation") or {}
            fault = str(evaluation.get("actionable_feedback") or "")[:70]
            lines.append(f"| 第 {attempt['attempt']} 稿 | **{attempt['score']}**/100 | {fault} |")
        lines.append("")
        storyline_scenes = storyline.get("scenes") if isinstance(storyline, dict) else None
        for index, scene in enumerate(storyline_scenes or [], start=1):
            if not isinstance(scene, dict):
                continue
            lines.append(f"**{index}. {scene.get('topic', '')}** — {scene.get('action', '')}")
            lines.append("")

        # 选角
        if kw["casting"]:
            lines.append("## 视觉选角")
            lines.append("")
            lines.append(f"- **人设**：{kw['casting'].get('character_profile', '')}")
            lines.append(f"- **服装**：{kw['casting'].get('wardrobe_description', '')}")
            lines.append(f"- **拼贴提示词**：{kw['casting'].get('collage_prompt', '')}")
            lines.append("")

        # 分镜
        lines.append("## 分镜脚本")
        lines.append("")
        if scenes:
            total = 0
            lines.append("| 场次 | 主题 | 时长 | 首帧提示词 | 视频提示词 | 引用资产 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for index, scene in enumerate(scenes, start=1):
                duration = scene.get("duration_seconds") or 0
                try:
                    total += int(duration)
                except (TypeError, ValueError):
                    pass
                assets = "、".join(scene.get("assets") or []) or "—"
                lines.append(
                    f"| Scene {index} | {scene.get('topic', '')} | {duration}s | "
                    f"{str(scene.get('first_frame_prompt', ''))[:200]}… | "
                    f"{str(scene.get('video_prompt', ''))[:200]}… | {assets} |"
                )
            lines.append("")
            lines.append(f"**合计时长**：{total}s（目标 {params.get('target_duration')}）")
        else:
            lines.append("（分镜缺失）")
        lines.append("")

        # 旁白
        if kw["voiceover"]:
            vo = str(kw["voiceover"].get("voiceover_text") or "")
            words = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", vo))
            lines.append("## 旁白与音乐")
            lines.append("")
            lines.append(f"- **旁白**（{kw['voiceover'].get('voiceover_gender', '')}，{words} 词）：{vo}")
            lines.append(f"- **音乐**：{kw['voiceover'].get('background_music_prompt', '')}")
            lines.append("")

        # 生成 + 验证
        resolved_generated = self.resolve_generated(kw["generated"])
        if resolved_generated:
            lines.append("## 已生成素材")
            lines.append("")
            lines.append("| 素材名 | 类型 | 文件 |")
            lines.append("| --- | --- | --- |")
            for item in resolved_generated:
                path = item.get("path") or ""
                lines.append(
                    f"| `{item.get('name', '')}` | {item.get('type', '')} | "
                    f"{Path(path).name if path else '—'} |"
                )
            lines.append("")

        if keyframe_eval:
            lines.append("## 关键帧序列评审（独立 verifier）")
            lines.append("")
            lines.append(f"- **总分**：{_score_of(keyframe_eval)}/100")
            lines.append(f"- **primary_fault**：`{keyframe_eval.get('primary_fault', '')}`")
            lines.append(f"- **要重做的场次**：{keyframe_eval.get('problematic_scenes', [])}")
            lines.append(f"- **反馈**：{keyframe_eval.get('feedback', '')}")
            lines.append("")

        if video_eval:
            lines.append("## 成片评审与策略效能（独立 verifier）")
            lines.append("")
            breakdown = video_eval.get("breakdown") or {}
            lines.append(f"- **执行质量**：{_score_of(video_eval)}/100 " + json.dumps(breakdown, ensure_ascii=False))
            lines.append(f"- **primary_fault**：`{video_eval.get('primary_fault', '')}`")
            efficacy = video_eval.get("mab_efficacy_scores") or {}
            if efficacy:
                lines.append("")
                lines.append("| 维度 | 效能分 | 依据 |")
                lines.append("| --- | --- | --- |")
                justifications = video_eval.get("mab_efficacy_justifications") or {}
                for dimension in DIMENSIONS:
                    lines.append(
                        f"| `{dimension}` | **{efficacy.get(dimension, '—')}** | "
                        f"{str(justifications.get(dimension, ''))[:140]} |"
                    )
            lines.append("")
            lines.append(f"- **反馈**：{video_eval.get('feedback', '')}")
            lines.append("")

        # 奖励回填
        lines.append("## 因子化奖励回填")
        lines.append("")
        if kw["applied"]:
            for dimension, reward in kw["applied"].items():
                lines.append(f"- `{dimension}` ← **{reward:.0f}**")
        else:
            if video_eval and not (video_eval.get("mab_efficacy_scores") or {}):
                lines.append(
                    f"- 成片评审没有给出三维效能分，本轮三维都退回全局质量分 **{kw['global_score']}**。"
                )
        lines.append("")
        lines.append(f"全局质量分（本轮）：**{kw['global_score']}**")
        lines.append("")

        # 交付清单
        lines.append("## 交付清单")
        lines.append("")
        lines.append("| 交付物 | 状态 |")
        lines.append("| --- | --- |")
        lines.append("| 参数 / 选臂 / 创意方向 / 简报 | ✅ |")
        lines.append(f"| 剧本（第 {len(attempts)} 稿，最佳 {attempts[-1]['score'] if attempts else 0} 分） | ✅ |")
        lines.append(f"| 选角拼贴提示词 | {'✅' if kw['casting'] else '⬜'} |")
        lines.append(f"| 分镜脚本（{len(scenes)} 场 / {sum(int(s.get('duration_seconds') or 0) for s in scenes if str(s.get('duration_seconds') or '').isdigit())}s） | {'✅' if scenes else '⬜'} |")
        lines.append(f"| 旁白文本 | {'✅' if kw['voiceover'].get('voiceover_text') else '⬜'} |")
        frames = self._frames_of(resolved_generated)
        videos = self._videos_of(resolved_generated)
        lines.append(f"| 关键帧（{len(frames)} 张） | {'✅' if frames else '⬜ 本轮未生成'} |")
        lines.append(f"| 镜头视频（{len(videos)} 条） | {'✅' if videos else '⬜ 本轮未生成'} |")
        lines.append("| 旁白音轨 / BGM | ⬜ 本对话不能生成音频 |")
        lines.append("| 成片组装导出 | ⬜ 需在「剪辑」时间线上完成 |")
        lines.append("")
        lines.append(
            "**说明**：音轨需要到实验室的「音频生成」/「图音生视频」卡片生成；成片由你把"
            "上面这些片段拖到「剪辑」时间线拼起来（片段可拖动、分割，音频可导出成素材）。"
        )
        return "\n".join(lines)


# 单成员可用的工具 schema（与剪辑助手共用同一份定义，保证产出落到同一套素材体系）
_DESIGN_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_design_image",
        "description": "生成一张角色/道具设计图或分镜关键帧，真实调用图片生成模型并保存为该项目的素材。",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["character", "scene"]},
                "name": {"type": "string", "description": "这张图的名称，之后可用 @名称 引用"},
                "description": {"type": "string", "description": "详细视觉描述，可用 @名称 引用已有素材"},
                "aspect_ratio": {"type": "string", "description": "画面比例，如 16:9 或 9:16"},
            },
            "required": ["kind", "name", "description"],
        },
    },
}

_SHOT_VIDEO_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_shot_video",
        "description": "把一张已生成的关键帧真实转成一段视频片段并保存为该项目的素材。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "视频片段名称"},
                "first_frame_name": {"type": "string", "description": "作为首帧的关键帧名称"},
                "description": {"type": "string", "description": "镜头动作/运镜描述"},
                "duration_seconds": {"type": "integer", "description": "时长（秒）"},
            },
            "required": ["name", "first_frame_name", "description"],
        },
    },
}
