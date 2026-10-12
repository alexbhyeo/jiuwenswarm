# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""广告片共创（Co-Director）团队的 MAB 选臂器：**真实的**多臂老虎机数学。

这里是对上游 ``ads_codirector/mab/bandit.py``（Apache-2.0, Google LLC）的移植，
不是"让模型在文字里维护一张表"——臂的拉动次数、奖励列表、平均奖励、UCB 分数
全部由这里的 Python 计算，模型只负责提供"这一轮这条臂表现如何"的评分。

三个维度各自独立选臂（3 × 3 × 4 = 36 种组合），每条臂独立记分：

* **因子化奖励（factored reward）**：奖励按维度分别回填——创意策略的分数只更新
  创意策略那条臂，叙事模式的分数只更新叙事模式那条臂。这样绕开了"最终成片质量
  该归功于三个维度里的哪一个"的功劳分配问题（上游论文的核心主张）。
* **UCB**：``mean + c·√(ln N / n)``，``N`` 是该维度总拉动次数、``n`` 是该臂拉动
  次数。上游 config.yaml 用的 ``c=50``，这里保持同样的强探索偏好。
* **冷启动 + warm start**：每条臂至少被拉一次（UCB 的前提）；未试过的臂里优先
  选 warm start 推荐的臂，其余随机。
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

logger = logging.getLogger(__name__)

# 三个维度与各自的臂（与上游 ads_codirector config.yaml 的 arms 完全一致）。
ARM_OPTIONS: dict[str, tuple[str, ...]] = {
    "creative_strategy": ("informational", "transformational", "comparative"),
    "narrative_mode": ("analytical", "vignette", "narrative_drama"),
    "aesthetic_archetype": (
        "clarity_energy",
        "cinematic_premium",
        "minimalist_focus",
        "kinetic_grit",
    ),
}

DIMENSIONS: tuple[str, ...] = tuple(ARM_OPTIONS)

# 臂的理论定义（Laskey/Day/Crask 1989；Escalas 2004 + Green & Brock 2000；
# Zettl 应用媒体美学 + Lang 2000）。这些字符串会作为"理论依据"喂给选臂综述与
# 效能打分，让"选臂"和"打效能分"都有明确的标准可依。
THEORY_MAP: dict[str, str] = {
    "informational": (
        "Focus on functional utility and verifiable product attributes. Provide factual "
        "evidence for product superiority and logical advantages."
    ),
    "transformational": (
        "Focus on the psychological experience, social meaning, or emotional state associated "
        "with the brand. Build a lifestyle connection rather than a spec-based one."
    ),
    "comparative": (
        "Define the product by positioning it against a known standard, common urban norm, or a "
        "generalized competitor to highlight a unique value proposition."
    ),
    "analytical": (
        "Utilize an argument-based structure. Use direct address or logical points without a "
        "temporal story arc or diegetic characters."
    ),
    "vignette": (
        "Present a series of brief, atmospheric 'slices of life'. Focus on a consistent 'vibe' "
        "and visual textures rather than a plot with a climax."
    ),
    "narrative_drama": (
        "Construct a full temporal sequence (beginning, middle, end) with a character-driven "
        "conflict that the product helps resolve, maximizing 'narrative transportation'."
    ),
    "clarity_energy": (
        "High-key illumination (low contrast) paired with high-temporal motion (fast cuts). Use "
        "upbeat, high-tempo audio to suggest transparency and vitality."
    ),
    "cinematic_premium": (
        "Chiaroscuro lighting (high contrast / heavy shadows) paired with low-temporal motion "
        "(smooth, sweeping perspectives and a reflective pace). Use orchestral or ambient "
        "cinematic audio for a premium, high-end feel."
    ),
    "minimalist_focus": (
        "Bright, clean, high-key backgrounds with static or micro-movements. Focus on extreme "
        "detail and texture, paired with clean foley / ASMR-style audio."
    ),
    "kinetic_grit": (
        "Low-key / shadowy lighting with dynamic, unstable motion (highly immersive, rapid "
        "first-person viewpoints with jittery or intense kinetic energy). Use driving electronic "
        "synth audio to suggest intensity and modern authenticity."
    ),
}

# 上游 config.yaml 的探索系数。保持同样的强探索偏好：4 轮迭代里三个维度各 5~12 条
# 臂，只有较大的 c 才会真的去试没试过的臂。
DEFAULT_UCB_C = 50.0
DEFAULT_EPSILON = 0.1
DEFAULT_NUM_ITERATIONS = 4

# 自省循环阈值（上游 config.yaml 的 self_refinement）。
STORYLINE_SCORE_THRESHOLD = 90
STORYLINE_MAX_ATTEMPTS = 2
KEYFRAME_SCORE_THRESHOLD = 90
KEYFRAME_MAX_ATTEMPTS = 2


def default_arm_stats() -> dict[str, dict[str, dict[str, Any]]]:
    """空的臂统计：每个维度下每条臂 ``{"pulls": 0, "rewards": []}``。

    预先铺满而不是"用到才建"——报表要能展示"哪些臂还没试过"，而"没试过"和
    "不存在"是两件事。
    """
    return {
        dimension: {arm: {"pulls": 0, "rewards": []} for arm in arms}
        for dimension, arms in ARM_OPTIONS.items()
    }


class MabBandit:
    """三维因子化 MAB（UCB 或 ε-greedy），可序列化、可跨轮持久。

    ``arm_stats`` 直接对应上游 ``MabExperimentState.arm_stats`` 的结构，
    因此报表和"下一轮计划"都能从同一份数据里算出来。
    """

    def __init__(
        self,
        *,
        algorithm: str = "ucb",
        ucb_c: float = DEFAULT_UCB_C,
        epsilon: float = DEFAULT_EPSILON,
        arm_stats: dict[str, dict[str, dict[str, Any]]] | None = None,
        warm_start: dict[str, str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.algorithm = (algorithm or "ucb").strip().lower()
        self.ucb_c = float(ucb_c)
        self.epsilon = float(epsilon)
        self.arm_stats = arm_stats or default_arm_stats()
        self.warm_start = dict(warm_start or {})
        self._rng = rng or random.Random()

    # -- 状态 ---------------------------------------------------------------

    def pulls(self, dimension: str) -> int:
        return sum(int(stats.get("pulls") or 0) for stats in self.arm_stats.get(dimension, {}).values())

    @staticmethod
    def mean_reward(stats: dict[str, Any]) -> float:
        pulls = int(stats.get("pulls") or 0)
        if pulls <= 0:
            return 0.0
        rewards = stats.get("rewards") or []
        if rewards:
            return sum(float(r) for r in rewards) / pulls
        return 0.0

    def best_arms(self) -> dict[str, str | None]:
        """每个维度平均奖励最高的臂（没有被拉过的臂不参与）。"""
        best: dict[str, str | None] = {}
        for dimension, arms in ARM_OPTIONS.items():
            candidate: str | None = None
            candidate_mean = -1.0
            for arm in arms:
                stats = self.arm_stats.get(dimension, {}).get(arm) or {}
                if int(stats.get("pulls") or 0) <= 0:
                    continue
                mean = self.mean_reward(stats)
                if mean > candidate_mean:
                    candidate_mean = mean
                    candidate = arm
            best[dimension] = candidate
        return best

    def arm_table(self) -> list[dict[str, Any]]:
        """报表用的长表：每行一条臂的拉动次数与平均奖励。"""
        rows: list[dict[str, Any]] = []
        for dimension, arms in ARM_OPTIONS.items():
            for arm in arms:
                stats = self.arm_stats.get(dimension, {}).get(arm) or {}
                pulls = int(stats.get("pulls") or 0)
                rows.append(
                    {
                        "dimension": dimension,
                        "arm": arm,
                        "pulls": pulls,
                        "avg_reward": round(self.mean_reward(stats), 2) if pulls else None,
                    }
                )
        return rows

    # -- 选臂 ---------------------------------------------------------------

    def select_arm(self, dimension: str) -> str:
        arms = ARM_OPTIONS[dimension]
        stage = self.arm_stats.setdefault(dimension, {})
        unpulled = [arm for arm in arms if int((stage.get(arm) or {}).get("pulls") or 0) <= 0]

        # 冷启动：每条臂至少拉一次。未试过的臂里优先 warm start 推荐的那条，
        # 否则随机——随机是为了不让"臂的定义顺序"变成隐式先验。
        if unpulled:
            recommended = self.warm_start.get(dimension)
            if recommended in unpulled:
                logger.info("[mab] %s 冷启动选 warm start 臂: %s", dimension, recommended)
                return str(recommended)
            chosen = self._rng.choice(unpulled)
            logger.info("[mab] %s 冷启动随机选未试臂: %s", dimension, chosen)
            return chosen

        if self.algorithm == "epsilon_greedy" and self._rng.random() < self.epsilon:
            chosen = self._rng.choice(list(arms))
            logger.info("[mab] %s ε-greedy 探索: %s", dimension, chosen)
            return chosen

        total = self.pulls(dimension)
        best_arm = arms[0]
        best_score = -1.0
        for arm in arms:
            stats = stage.get(arm) or {"pulls": 0, "rewards": []}
            pulls = int(stats.get("pulls") or 1) or 1
            mean = self.mean_reward(stats)
            if self.algorithm == "epsilon_greedy":
                score = mean
            else:
                # UCB1: mean + c·sqrt(ln(N) / n)
                score = mean + self.ucb_c * math.sqrt(math.log(total) / pulls)
            if score > best_score:
                best_score = score
                best_arm = arm
        logger.info("[mab] %s 选臂 %s (score=%.2f)", dimension, best_arm, best_score)
        return best_arm

    def select_all(self) -> dict[str, str]:
        return {dimension: self.select_arm(dimension) for dimension in DIMENSIONS}

    # -- 奖励回填 -----------------------------------------------------------

    def update_reward(
        self,
        arms: dict[str, str],
        efficacy_scores: dict[str, int | float],
        *,
        global_score: int | float = 0,
    ) -> dict[str, float]:
        """因子化回填：每个维度只吃自己那维的效能分，缺省时退回全局质量分。

        返回这一轮实际写进去的 ``{dimension: reward}``，便于报表如实展示"到底
        用了哪个分数"。
        """
        applied: dict[str, float] = {}
        for dimension, arm in (arms or {}).items():
            if dimension not in ARM_OPTIONS or not arm:
                continue
            raw = efficacy_scores.get(dimension)
            reward = float(raw) if raw is not None else float(global_score or 0)
            # 越界/脏数据不写进去——一条被污染的记录会一直影响后面的 UCB 分数。
            if not 0.0 <= reward <= 100.0:
                logger.warning("[mab] 忽略越界奖励 %s(%s)=%r", dimension, arm, raw)
                continue
            stats = self.arm_stats.setdefault(dimension, {}).setdefault(arm, {"pulls": 0, "rewards": []})
            stats["pulls"] = int(stats.get("pulls") or 0) + 1
            rewards = stats.setdefault("rewards", [])
            rewards.append(reward)
            applied[dimension] = reward
        return applied

    # -- 下一轮计划 ---------------------------------------------------------

    def next_round_plan(self) -> str:
        """给报表用：下一轮 UCB 会倾向哪些臂、哪些臂会被冷落。"""
        lines: list[str] = []
        for dimension in DIMENSIONS:
            total = self.pulls(dimension)
            untried = [
                arm
                for arm in ARM_OPTIONS[dimension]
                if int((self.arm_stats.get(dimension, {}).get(arm) or {}).get("pulls") or 0) <= 0
            ]
            if untried:
                lines.append(f"- `{dimension}`：仍是冷启动，下一轮会优先试未试臂 {untried}")
                continue
            scored = []
            for arm in ARM_OPTIONS[dimension]:
                stats = self.arm_stats[dimension][arm]
                pulls = int(stats.get("pulls") or 1) or 1
                mean = self.mean_reward(stats)
                score = mean + self.ucb_c * math.sqrt(math.log(total) / pulls)
                scored.append((score, arm, mean))
            scored.sort(reverse=True)
            lines.append(
                f"- `{dimension}`：UCB 最高 **{scored[0][1]}**（score={scored[0][0]:.1f}, mean={scored[0][2]:.1f}），"
                f"最低 {scored[-1][1]}（score={scored[-1][0]:.1f}）"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "ucb_c": self.ucb_c,
            "epsilon": self.epsilon,
            "warm_start": self.warm_start,
            "arm_stats": self.arm_stats,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> MabBandit:
        payload = payload or {}
        stats = payload.get("arm_stats")
        merged = default_arm_stats()
        if isinstance(stats, dict):
            for dimension, arms in stats.items():
                if dimension not in merged or not isinstance(arms, dict):
                    continue
                for arm, arm_stats in arms.items():
                    if arm in merged[dimension] and isinstance(arm_stats, dict):
                        merged[dimension][arm] = {
                            "pulls": int(arm_stats.get("pulls") or 0),
                            "rewards": [float(x) for x in (arm_stats.get("rewards") or [])],
                        }
        return cls(
            algorithm=str(payload.get("algorithm") or "ucb"),
            ucb_c=float(payload.get("ucb_c") or DEFAULT_UCB_C),
            epsilon=float(payload.get("epsilon") or DEFAULT_EPSILON),
            arm_stats=merged,
            warm_start=payload.get("warm_start") if isinstance(payload.get("warm_start"), dict) else None,
        )


def theory_for(arms: dict[str, str]) -> dict[str, str]:
    """把选中的臂映射到理论定义，作为提示词里的"依据"。"""
    return {
        dimension: THEORY_MAP.get(arm, "No theory defined.")
        for dimension, arm in (arms or {}).items()
        if dimension in ARM_OPTIONS
    }


def validate_arms(payload: Any) -> dict[str, str] | None:
    """校验模型给出的选臂/效能分维度是否合法；不合法返回 None。

    模型有可能给出 `"creative strategy"`、`"strategy"` 这类近义写法，这里做一次
    归一化，避免因为一个空格就让整轮退化成默认臂。
    """
    if not isinstance(payload, dict):
        return None
    aliases = {
        "creative_strategy": ("creative_strategy", "creative strategy", "strategy", "creative"),
        "narrative_mode": ("narrative_mode", "narrative mode", "mode", "narrative"),
        "aesthetic_archetype": (
            "aesthetic_archetype",
            "aesthetic archetype",
            "archetype",
            "aesthetic",
        ),
    }
    normalized: dict[str, Any] = {
        str(k).strip().lower().replace("-", "_"): v for k, v in payload.items()
    }
    out: dict[str, str] = {}
    for dimension, names in aliases.items():
        value = None
        for alias in names:
            if alias in normalized:
                value = normalized[alias]
                break
        if value is None:
            continue
        arm = str(value).strip().lower().replace("-", "_")
        if arm in ARM_OPTIONS[dimension]:
            out[dimension] = arm
    return out or None
