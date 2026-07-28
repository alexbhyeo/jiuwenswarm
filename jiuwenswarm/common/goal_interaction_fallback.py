# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fallback shims for openjiuwen's goal-tracking and rich interaction-event
types (openjiuwen.harness.goal.schema / openjiuwen.harness.schema.interaction).

The currently pinned openjiuwen commit (see uv.lock) predates both modules -
importing them raises ModuleNotFoundError, which would otherwise crash
interface_deep.py at import time. Both features are genuinely absent from
this build's underlying Runner/agent classes too (not just the Python
types), so code paths that react to them (goal status checks, GOAL_UPDATED/
mid-execution-steer event handling) are inherently dormant on this build
regardless of whether the types below are stubbed or real - this shim's job
is only to let the surrounding, unrelated agent-execution code load and run,
not to actually implement goal tracking or live input steering. Re-export
the real symbols transparently once openjiuwen provides them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    from openjiuwen.harness.goal.schema import (  # type: ignore[import-not-found]
        GoalOperationError,
        GoalStatus,
    )
except ImportError:

    class GoalStatus(Enum):  # type: ignore[no-redef]
        ACTIVE = "active"
        PAUSED = "paused"
        BLOCKED = "blocked"

    class GoalOperationError(Exception):  # type: ignore[no-redef]
        pass


try:
    from openjiuwen.harness.schema.interaction import (  # type: ignore[import-not-found]
        InputDispatchMode,
        InteractionEventType,
        SendInputRequest,
    )
except ImportError:

    class InteractionEventType(Enum):  # type: ignore[no-redef]
        GOAL_UPDATED = "goal_updated"
        EXECUTION_ERROR = "execution_error"
        RUNTIME_ERROR = "runtime_error"

    class InputDispatchMode(Enum):  # type: ignore[no-redef]
        FOLLOW_UP = "follow_up"
        STEER = "steer"

    @dataclass
    class SendInputRequest:  # type: ignore[no-redef]
        request_id: str
        inputs: Any
        mode: Any


__all__ = [
    "GoalOperationError",
    "GoalStatus",
    "InputDispatchMode",
    "InteractionEventType",
    "SendInputRequest",
]
