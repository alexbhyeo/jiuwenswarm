# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tags the reason an LLM call was made, for token-usage attribution in logs.

A single user turn can trigger several model calls beyond the main answer:
an A2UI repair attempt (fixing invalid `<a2ui-json>` output) or a full plain-text
retry when repair gives up. These share the same request_id/session_id as the
main call, so usage logs can't tell them apart without an explicit tag. This
context var is set for the duration of a non-main call; anything logged while
it's set (including nested calls several `await`s deep, since contextvars
propagate through the same asyncio task) picks it up. Falls back to "main" for
everything else — normal responses need no changes anywhere.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

call_purpose_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "a2ui_call_purpose", default="main"
)


@contextmanager
def call_purpose(purpose: str):
    """Tag LLM calls made within this block with `purpose` (e.g. "a2ui_repair")."""
    token = call_purpose_var.set(purpose)
    try:
        yield
    finally:
        call_purpose_var.reset(token)


__all__ = ["call_purpose", "call_purpose_var"]
