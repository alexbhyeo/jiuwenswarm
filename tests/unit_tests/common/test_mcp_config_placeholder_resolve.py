"""``call_mcp_tool_via_temp_connection`` 必须把 ${VAR} 换成真实凭据。

合并 upstream/develop 时本文件改成经注入点取 ``mcp_credential``（R1 分层），
但临时连接路径里仍留着裸名 ``resolve_placeholders`` / ``CredentialStore``——
这里没有对应的 import，调用时抛 NameError 被下面的 ``except Exception`` 吞掉，
连接器于是收到字面量 ``${TENCENT_COS_REGION}``（表现为 tencent-cos 报
"region format is illegal"）。测试锁住"送进 client 的 entry 已解析"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.common import external_providers, mcp_config


class _FakeCredentialModule:
    CredentialStore = staticmethod(lambda: "fake-store")

    @staticmethod
    def resolve_placeholders(entry, store, name=None):
        assert store == "fake-store"
        return {
            **entry,
            "env": {k: v.replace("${", "").replace("}", "") for k, v in entry["env"].items()},
        }


@pytest.fixture
def captured(monkeypatch):
    external_providers.register("mcp_credential", _FakeCredentialModule)

    seen: dict[str, object] = {}

    class _Client:
        async def connect(self):
            return True

        async def call_tool(self, tool_name, arguments):
            return "ok"

        async def disconnect(self):
            return None

    def _create_client(cfg):
        seen["cfg"] = cfg
        return _Client()

    monkeypatch.setattr(
        "openjiuwen.core.runner.resources_manager.tool_manager.ToolMgr._create_client",
        staticmethod(_create_client),
    )
    return seen


def test_temp_connection_resolves_placeholders(captured):
    entry = {
        "name": "tencent-cos",
        "transport": "stdio",
        "command": "python3",
        "args": ["server.py"],
        "env": {"TENCENT_COS_REGION": "${TENCENT_COS_REGION}"},
    }
    result = pytest.importorskip("asyncio").run(
        mcp_config.call_mcp_tool_via_temp_connection(entry, "upload_file", {})
    )
    assert result == "ok"
    cfg = captured["cfg"]
    env = dict((cfg.params or {}).get("env") or {})
    assert env["TENCENT_COS_REGION"] == "TENCENT_COS_REGION", (
        "placeholder must be resolved before the connector is spawned"
    )


def test_temp_connection_survives_unregistered_provider(monkeypatch):
    """注入点没装（WebChannel 早期）也不能炸：退回字面量并继续。"""
    external_providers._providers.pop("mcp_credential", None)

    class _Client:
        async def connect(self):
            return True

        async def call_tool(self, tool_name, arguments):
            return "ok"

        async def disconnect(self):
            return None

    monkeypatch.setattr(
        "openjiuwen.core.runner.resources_manager.tool_manager.ToolMgr._create_client",
        staticmethod(lambda cfg: _Client()),
    )
    entry = {"name": "tencent-cos", "transport": "stdio", "command": "python3", "args": []}
    import asyncio

    assert asyncio.run(
        mcp_config.call_mcp_tool_via_temp_connection(entry, "upload_file", {})
    ) == "ok"
