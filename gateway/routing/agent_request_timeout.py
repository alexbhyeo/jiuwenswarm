"""Timeout policy for Gateway -> AgentServer unary requests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

AGENT_SERVER_TIMEOUT_CODE = "AGENT_SERVER_TIMEOUT"
AGENT_SERVER_TIMEOUT_ERROR = "AgentServer request timed out"

_TUI_CHANNEL_ID = "tui"
_TUI_DEFAULT_UNARY_TIMEOUT_SECONDS = 25.0
_TUI_MAX_UNARY_TIMEOUT_SECONDS = 55.0
_TUI_CLIENT_TIMEOUT_GRACE_SECONDS = 5.0
_TUI_EXTENDED_UNARY_METHOD_PREFIXES = (
    "permissions.tools.",
    "permissions.rules.",
)
# Snapshot RPCs that may wait behind a busy TUI outbound writer (workflow.updated
# / chat stream). Prefer the 55s window when the client does not pass timeout_ms,
# so Gateway does not cut them at the default 25s.
_TUI_EXTENDED_UNARY_METHODS = frozenset({
    "command.workflows",
})
# Methods exempt from the TUI unary cap — they are long-lived by design and
# must not be cut by the gateway-side 25s/55s limit. A swarmflow human reply
# is an async deliver (thin-route publish to wake a pending future) whose
# latency tracks the human-turn timeout (agent-core default 600s); capping it
# at 55s aborts legitimate replies while the person is still typing.
#
# schedule.run / schedule.check_config: the task execution itself is
# asyncio.create_task'd (non-blocking); the synchronous wait is dominated by
# first-time get_agent() + start_scheduler() warmup, which can exceed 25s on a
# cold AgentServer. Capping at 25/55s aborts a legitimate cold-start. Let the
# TUI client's own timeout_ms be the ceiling here.
_TUI_UNARY_TIMEOUT_EXEMPT_METHODS = frozenset({
    "chat.swarmflow_reply",
    "schedule.run",
    "schedule.check_config",
    # harmonyos.dev_init runs npm install (up to minutes); its lifecycle is
    # governed by the TUI handler's task-cancellation, not the unary cap.
    "harmonyos.dev_init",
})

# Web-channel unary methods whose latency is bounded by slow, serialized
# background work rather than by network round-trips, so they legitimately
# outlive AgentClient's 600s default. Without this, a full Co-Director team run
# (剪辑助手 with the ads-codirector base skill: ~13 isolated member calls plus a
# batch of image/video generations that the provider serializes) drops the
# response at 600s even though the backend keeps working and persists the
# result — the user sees a timeout for a turn that actually succeeded.
#
# Only these methods get the longer lease; every other web request keeps
# returning None here and therefore keeps AgentClient's default.
_WEB_LONG_RUNNING_UNARY_TIMEOUT_SECONDS = 3600.0
_WEB_LONG_RUNNING_UNARY_METHODS = frozenset({
    "director.edit_chat.send",
})


class AgentRequestTimeoutError(TimeoutError):
    """Raised when Gateway stops waiting for a slow AgentServer unary request."""

    def __init__(self, message: str = AGENT_SERVER_TIMEOUT_ERROR) -> None:
        super().__init__(message)
        self.code = AGENT_SERVER_TIMEOUT_CODE


def coerce_client_timeout_ms(value: Any) -> int | None:
    """Parse client-provided timeout metadata without accepting invalid values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        timeout_ms = value
    elif isinstance(value, float) and value.is_integer():
        timeout_ms = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        timeout_ms = int(value.strip())
    else:
        return None
    return timeout_ms if timeout_ms > 0 else None


def resolve_agent_request_timeout_seconds(
    *,
    channel_id: str | None,
    method: str | None,
    is_stream: bool,
    client_timeout_ms: Any = None,
) -> float | None:
    """Return a Gateway-side wait limit, or None to keep AgentClient's default."""
    if is_stream:
        return None

    method_value = str(method or "")
    if channel_id != _TUI_CHANNEL_ID:
        # Web (and any non-TUI) channel: only the explicitly allow-listed
        # long-running methods get a longer lease; everything else keeps
        # AgentClient's default by returning None.
        if method_value in _WEB_LONG_RUNNING_UNARY_METHODS:
            return _WEB_LONG_RUNNING_UNARY_TIMEOUT_SECONDS
        return None

    # Exempt long-lived methods (e.g. chat.swarmflow_reply whose latency tracks
    # the human-turn timeout) from the TUI unary cap.
    if method_value in _TUI_UNARY_TIMEOUT_EXEMPT_METHODS:
        return None

    parsed_client_timeout_ms = coerce_client_timeout_ms(client_timeout_ms)
    if parsed_client_timeout_ms is not None:
        client_timeout_seconds = parsed_client_timeout_ms / 1000.0
        # Snapshot RPCs often wait behind a busy outbound writer; keep most of
        # the client window instead of cutting a flat 5s (10s client → 5s wait).
        grace = (
            2.0
            if method_value in _TUI_EXTENDED_UNARY_METHODS
            else _TUI_CLIENT_TIMEOUT_GRACE_SECONDS
        )
        timeout_seconds = max(1.0, client_timeout_seconds - grace)
        return min(timeout_seconds, _TUI_MAX_UNARY_TIMEOUT_SECONDS)

    if (
        method_value.startswith(_TUI_EXTENDED_UNARY_METHOD_PREFIXES)
        or method_value in _TUI_EXTENDED_UNARY_METHODS
    ):
        return _TUI_MAX_UNARY_TIMEOUT_SECONDS

    return _TUI_DEFAULT_UNARY_TIMEOUT_SECONDS


def request_timeout_from_envelope(env: Any) -> float | None:
    channel_context = getattr(env, "channel_context", None)
    if not isinstance(channel_context, dict):
        channel_context = {}
    metadata = getattr(env, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    return resolve_agent_request_timeout_seconds(
        channel_id=getattr(env, "channel", None),
        method=getattr(env, "method", None),
        is_stream=bool(getattr(env, "is_stream", False)),
        client_timeout_ms=channel_context.get(
            "client_timeout_ms",
            metadata.get("client_timeout_ms"),
        ),
    )


async def send_agent_request_with_timeout(
    agent_client: Any,
    env: Any,
    *,
    label: str,
    timeout_seconds: float | None = None,
) -> Any:
    timeout = request_timeout_from_envelope(env) if timeout_seconds is None else timeout_seconds
    if timeout is None:
        return await agent_client.send_request(env)

    # 必须把同一个上限**透传给 AgentClient**：send_request 自己也有一个 600s 的
    # 内层默认值，只在外面套 asyncio.wait_for 是没用的——内层会先超时，调用方看到
    # 的还是 600s（这正是"给导演团队放了长租约却仍然 600s 超时"的原因）。
    try:
        pending = agent_client.send_request(env, timeout=timeout)
    except TypeError:
        # 老客户端/测试替身不接受 timeout 关键字，退回只看外层上限
        pending = agent_client.send_request(env)

    try:
        return await asyncio.wait_for(pending, timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.warning(
            "[%s] AgentServer unary request timed out: request_id=%s method=%s "
            "channel=%s timeout=%ss",
            label,
            getattr(env, "request_id", ""),
            getattr(env, "method", ""),
            getattr(env, "channel", ""),
            timeout,
        )
        raise AgentRequestTimeoutError() from exc
