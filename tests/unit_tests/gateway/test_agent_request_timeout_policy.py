import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gateway.routing.agent_request_timeout import (
    AGENT_SERVER_TIMEOUT_CODE,
    AGENT_SERVER_TIMEOUT_ERROR,
    AgentRequestTimeoutError,
    request_timeout_from_envelope,
    resolve_agent_request_timeout_seconds,
    send_agent_request_with_timeout,
)


def test_resolve_timeout_skips_stream_and_non_tui_requests():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=True,
    ) is None
    assert resolve_agent_request_timeout_seconds(
        channel_id="web",
        method="history.get",
        is_stream=False,
    ) is None


def test_resolve_timeout_gives_the_director_edit_chat_a_long_web_lease():
    """剪辑助手的团队回合（ads-codirector 基础技能）会超过 AgentClient 的 600s
    默认值——网关不能在后端还在正常工作时就把响应丢掉。"""
    from jiuwenswarm.gateway.routing.agent_request_timeout import (
        _WEB_LONG_RUNNING_UNARY_TIMEOUT_SECONDS,
    )

    timeout = resolve_agent_request_timeout_seconds(
        channel_id="web",
        method="director.edit_chat.send",
        is_stream=False,
    )

    assert timeout == _WEB_LONG_RUNNING_UNARY_TIMEOUT_SECONDS
    assert timeout > 600.0, "必须长于 AgentClient 的默认 600s"


def test_long_web_lease_is_scoped_to_the_allowlist():
    """只有白名单里的方法拿到长租约，其余 web 方法保持默认。"""
    for method in ("director.generate", "director.projects.list", "chat.send"):
        assert (
            resolve_agent_request_timeout_seconds(
                channel_id="web", method=method, is_stream=False
            )
            is None
        ), method
    # 流式请求永远不受影响
    assert (
        resolve_agent_request_timeout_seconds(
            channel_id="web", method="director.edit_chat.send", is_stream=True
        )
        is None
    )


def test_resolve_timeout_defaults_tui_unary_before_frontend_window():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=False,
    ) == 25.0


def test_resolve_timeout_allows_tui_explicit_sixty_second_window():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="permissions.tools.update",
        is_stream=False,
        client_timeout_ms=60_000,
    ) == 55.0


def test_resolve_timeout_clamps_tui_client_timeout_to_safe_upper_bound():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=False,
        client_timeout_ms=600_000,
    ) == 55.0


def test_resolve_timeout_extends_known_permissions_methods_for_older_clients():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="permissions.rules.create",
        is_stream=False,
    ) == 55.0


def test_resolve_timeout_extends_command_workflows_for_older_clients():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="command.workflows",
        is_stream=False,
    ) == 55.0


def test_resolve_timeout_keeps_most_of_client_window_for_command_workflows():
    # 30s client → 28s gateway (2s grace); not the flat 5s cut used elsewhere.
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="command.workflows",
        is_stream=False,
        client_timeout_ms=30_000,
    ) == 28.0
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="command.workflows",
        is_stream=False,
        client_timeout_ms=10_000,
    ) == 8.0


def test_resolve_timeout_exempts_swarmflow_reply_from_unary_cap():
    """A swarmflow human reply tracks the human-turn timeout (600s default),
    so it must not be capped by the TUI 25s/55s unary limit."""
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="chat.swarmflow_reply",
        is_stream=False,
    ) is None
    # Even when a client passes a short client_timeout_ms, the exempt method
    # stays uncapped (the reply is async and must outlive the cap).
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="chat.swarmflow_reply",
        is_stream=False,
        client_timeout_ms=1_000,
    ) is None


def test_resolve_timeout_exempts_schedule_run_from_unary_cap():
    """schedule.run's task execution is asyncio.create_task'd (non-blocking);
    the synchronous wait is dominated by first-time get_agent() +
    start_scheduler() warmup, which can exceed 25s on a cold AgentServer.
    Gateway must not cap it — the client timeout_ms is the ceiling."""
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="schedule.run",
        is_stream=False,
    ) is None
    # Client passes a short timeout: still uncapped so the client (not gateway)
    # decides when to give up.
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="schedule.run",
        is_stream=False,
        client_timeout_ms=5_000,
    ) is None


def test_resolve_timeout_exempts_schedule_check_config_from_unary_cap():
    """schedule.check_config shares the same cold-start warmup path as
    schedule.run (it is called right before run in the auto-harness flow)."""
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="schedule.check_config",
        is_stream=False,
    ) is None


def test_request_timeout_from_envelope_accepts_metadata_fallback():
    env = SimpleNamespace(
        channel="tui",
        method="history.get",
        is_stream=False,
        metadata={"client_timeout_ms": 60_000},
    )

    assert request_timeout_from_envelope(env) == 55.0


@pytest.mark.asyncio
async def test_send_agent_request_with_timeout_raises_stable_timeout_error(monkeypatch):
    class HangingAgentClient:
        async def send_request(self, env):
            await asyncio.Event().wait()

    env = SimpleNamespace(
        request_id="req-timeout-policy",
        channel="tui",
        method="history.get",
        is_stream=False,
        metadata={},
    )

    with pytest.raises(AgentRequestTimeoutError) as exc_info:
        await send_agent_request_with_timeout(
            HangingAgentClient(),
            env,
            label="test.policy",
            timeout_seconds=0.01,
        )

    assert str(exc_info.value) == AGENT_SERVER_TIMEOUT_ERROR
    assert exc_info.value.code == AGENT_SERVER_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_resolved_timeout_is_passed_into_agent_client():
    """回归：解析出的上限必须**透传**给 AgentClient。

    AgentClient.send_request 自己还有一个 600s 内层默认值；只在外层套
    asyncio.wait_for 时，内层会先超时，调用方看到的仍然是 600s——曾经就是这样让
    剪辑助手的团队回合（超过 10 分钟）被误判超时。
    """
    from jiuwenswarm.gateway.routing.agent_request_timeout import (
        send_agent_request_with_timeout,
    )

    seen: dict[str, Any] = {}

    class FakeClient:
        async def send_request(self, env, *, timeout=None):  # noqa: ANN001, ARG002
            seen["timeout"] = timeout
            return "ok"

    env = SimpleNamespace(
        request_id="r1",
        method="director.edit_chat.send",
        channel="web",
        is_stream=False,
        channel_context={},
        metadata={},
    )

    result = await send_agent_request_with_timeout(FakeClient(), env, label="t")

    assert result == "ok"
    assert seen["timeout"] is not None, "上限没有透传给 AgentClient（内层会提前 600s 超时）"
    assert seen["timeout"] > 600.0


@pytest.mark.asyncio
async def test_client_without_timeout_kwarg_still_works():
    """老客户端/测试替身不接受 timeout 关键字时不能炸。"""
    from jiuwenswarm.gateway.routing.agent_request_timeout import (
        send_agent_request_with_timeout,
    )

    class LegacyClient:
        async def send_request(self, env):  # noqa: ANN001, ARG002
            return "legacy-ok"

    env = SimpleNamespace(
        request_id="r2",
        method="director.edit_chat.send",
        channel="web",
        is_stream=False,
        channel_context={},
        metadata={},
    )

    assert await send_agent_request_with_timeout(LegacyClient(), env, label="t") == "legacy-ok"
