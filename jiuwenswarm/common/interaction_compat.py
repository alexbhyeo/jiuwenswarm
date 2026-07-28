# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fallback for DeepAgent's goal/interaction ``attach_output``/``send_input``
API when the installed openjiuwen build predates it (see
goal_interaction_fallback.py for the related schema-type gap).

jiuwenswarm's message dispatch was rewritten around attach_output/send_input
to support concurrent goal management and mid-run input injection (commit
"feat(harness): adapt backend goal interaction flow"), replacing a direct
``Runner.run_agent_streaming(agent=self._instance, inputs=inputs)`` call.
The currently pinned openjiuwen commit has neither ``attach_output`` nor
``send_input`` on DeepAgent, but ``Runner.run_agent_streaming`` still works
exactly as it did before that rewrite - so the plain "run a fresh message,
no goal/injection semantics" case can fall back to it directly. Goal
management and injecting into an already-running interaction have no
equivalent on this build and are not attempted here.
"""

from __future__ import annotations

from typing import Any, AsyncIterator


def supports_attach_output(instance: Any) -> bool:
    return hasattr(instance, "attach_output")


async def run_plain_streaming(instance: Any, inputs: dict) -> AsyncIterator[Any]:
    from openjiuwen.core.runner import Runner

    async for chunk in Runner.run_agent_streaming(agent=instance, inputs=inputs):
        yield chunk
