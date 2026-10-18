# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``mcp_config.call_mcp_tool``.

This wraps ``call_mcp_tool_via_temp_connection`` with two behaviors that
matter for a card (like 图音生视频) that calls an MCP tool once per
generation: (1) reuse an already-connected client from
``Runner.resource_mgr`` instead of paying a fresh subprocess spawn +
handshake every call, and (2) bound the whole thing with a timeout so a
stalled network call or wedged subprocess can't hang the caller forever.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jiuwenswarm.common import mcp_config as mc


class _StubTool:
    """Mimics ``MCPTool.invoke`` (openjiuwen's mcp/base.py): a plain
    successful value comes back wrapped as ``{"result": value}``, not as the
    plain value itself — ``call_mcp_tool`` must unwrap that, which is
    exactly the bug this stub exists to catch (an earlier version of this
    stub returned the plain value directly and the unwrap bug went
    unnoticed)."""

    def __init__(self, result: Any = None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.result = result
        self.delay = delay
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, inputs: dict[str, Any]) -> Any:
        self.calls.append(inputs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return {"result": self.result}


class _FakeResourceMgr:
    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self.lookup_calls: list[dict[str, Any]] = []

    async def get_mcp_tool(self, *, name: str, server_name: str) -> Any:
        self.lookup_calls.append({"name": name, "server_name": server_name})
        return self._tool


def _patch_runner(monkeypatch: pytest.MonkeyPatch, resource_mgr: Any) -> None:
    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)


ENTRY = {"name": "azure-blob-storage", "transport": "stdio"}


@pytest.mark.asyncio
async def test_reuses_a_cached_connection_instead_of_a_temp_one(monkeypatch: pytest.MonkeyPatch):
    tool = _StubTool(result="https://example.blob.core.windows.net/c/audio/a.wav")
    _patch_runner(monkeypatch, _FakeResourceMgr(tool))

    called_temp = False

    async def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        nonlocal called_temp
        called_temp = True
        raise AssertionError("should not fall back to a temp connection when one is cached")

    monkeypatch.setattr(mc, "call_mcp_tool_via_temp_connection", _fail_if_called)

    result = await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/a.wav"})

    assert result == "https://example.blob.core.windows.net/c/audio/a.wav"
    assert not called_temp
    assert tool.calls == [{"local_path": "/tmp/a.wav"}]


@pytest.mark.asyncio
async def test_falls_back_to_a_temp_connection_when_nothing_is_cached(monkeypatch: pytest.MonkeyPatch):
    _patch_runner(monkeypatch, _FakeResourceMgr(None))

    temp_calls: list[dict[str, Any]] = []

    async def _temp(entry: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> Any:
        temp_calls.append({"entry": entry, "tool_name": tool_name, "arguments": arguments})
        return "https://example.blob.core.windows.net/c/audio/b.wav"

    monkeypatch.setattr(mc, "call_mcp_tool_via_temp_connection", _temp)

    result = await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/b.wav"})

    assert result == "https://example.blob.core.windows.net/c/audio/b.wav"
    assert len(temp_calls) == 1
    assert temp_calls[0]["tool_name"] == "upload_file"


@pytest.mark.asyncio
async def test_falls_back_to_a_temp_connection_when_the_cache_returns_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
):
    """``get_mcp_tool`` returns a bare list (not a single Tool) for some call
    shapes; an empty one means "not registered", same as None."""
    _patch_runner(monkeypatch, _FakeResourceMgr([]))

    async def _temp(entry: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> Any:
        return "used-temp-connection"

    monkeypatch.setattr(mc, "call_mcp_tool_via_temp_connection", _temp)

    result = await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/c.wav"})
    assert result == "used-temp-connection"


@pytest.mark.asyncio
async def test_a_stalled_cached_tool_call_times_out_instead_of_hanging_forever(
    monkeypatch: pytest.MonkeyPatch,
):
    tool = _StubTool(result="unused", delay=10.0)
    _patch_runner(monkeypatch, _FakeResourceMgr(tool))

    with pytest.raises(asyncio.TimeoutError):
        await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/d.wav"}, timeout_s=0.05)


@pytest.mark.asyncio
async def test_a_stalled_cache_lookup_times_out_instead_of_hanging_forever(
    monkeypatch: pytest.MonkeyPatch,
):
    class _SlowResourceMgr:
        async def get_mcp_tool(self, *, name: str, server_name: str) -> Any:
            await asyncio.sleep(10.0)
            return None

    _patch_runner(monkeypatch, _SlowResourceMgr())

    async def _temp(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("should have timed out before reaching the temp-connection fallback")

    monkeypatch.setattr(mc, "call_mcp_tool_via_temp_connection", _temp)

    with pytest.raises(asyncio.TimeoutError):
        await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/e.wav"}, timeout_s=0.05)


@pytest.mark.asyncio
async def test_cached_tool_errors_propagate_without_a_spurious_temp_retry(monkeypatch: pytest.MonkeyPatch):
    tool = _StubTool(error=RuntimeError("blob write failed: 403"))
    _patch_runner(monkeypatch, _FakeResourceMgr(tool))

    async def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a real tool error should not silently retry via a temp connection")

    monkeypatch.setattr(mc, "call_mcp_tool_via_temp_connection", _fail_if_called)

    with pytest.raises(RuntimeError, match="blob write failed"):
        await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/f.wav"})


@pytest.mark.asyncio
async def test_a_cached_tools_wrapped_success_value_is_unwrapped(monkeypatch: pytest.MonkeyPatch):
    """Regression test: MCPTool.invoke() wraps a plain success value as
    {"result": value} (see _StubTool's docstring) — call_mcp_tool must hand
    callers the plain value, matching what the temp-connection path already
    returns, or callers that check `isinstance(result, str)` silently treat
    a real success as a failure and fall back incorrectly."""
    tool = _StubTool(result="https://example.blob.core.windows.net/c/audio/g.wav")
    _patch_runner(monkeypatch, _FakeResourceMgr(tool))

    result = await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/g.wav"})

    assert result == "https://example.blob.core.windows.net/c/audio/g.wav"
    assert not isinstance(result, dict)


@pytest.mark.asyncio
async def test_a_cached_tools_mcp_error_result_passes_through_unwrapped(monkeypatch: pytest.MonkeyPatch):
    """An explicit MCP-level error comes back from MCPTool.invoke() as an
    McpToolResult object (not wrapped in {"result": ...}) — call_mcp_tool
    must NOT try to unwrap it as if it were the plain-value shape."""
    from openjiuwen.core.foundation.tool.mcp.base import McpToolResult

    error_result = McpToolResult(success=False, data={"result": None}, error="blob write failed: 403")

    class _ErrorTool:
        async def invoke(self, inputs: dict[str, Any]) -> Any:
            return error_result

    _patch_runner(monkeypatch, _FakeResourceMgr(_ErrorTool()))

    result = await mc.call_mcp_tool(ENTRY, "upload_file", {"local_path": "/tmp/h.wav"})

    assert result is error_result
    assert result.success is False
