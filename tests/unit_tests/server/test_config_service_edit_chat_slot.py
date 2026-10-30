# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The AgentServer config panel must expose the Director-mode "Edit chat" slot.

The settings page's 剪辑对话 switch, model card and config dialog read the
panel payload produced by ``server.control.config_service.get_panel``. When
that map lacks the ``edit_chat_*`` keys the slot looks unconfigured forever,
even though ``config.set`` (implemented in ``common.config_panel``) has already
persisted the values.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.control import config_service

EDIT_CHAT_ENV = {
    "EDIT_CHAT_API_BASE": "https://api.deepseek.com/v1",
    "EDIT_CHAT_API_KEY": "sk-unit-test",
    "EDIT_CHAT_MODEL_NAME": "deepseek-v4-flash",
    "EDIT_CHAT_PROVIDER": "DeepSeek",
    "EDIT_CHAT_PROTOCOL": "openAI",
    "EDIT_CHAT_ENABLED": "true",
}


@pytest.fixture
def panel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """Panel payload built from a saved .env instead of this process's env."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump({"preferred_language": "zh"}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f'{name}="{value}"\n' for name, value in EDIT_CHAT_ENV.items()),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_service, "get_config_file", lambda: config_file)
    monkeypatch.setattr(config_service, "get_env_file", lambda: env_file)
    monkeypatch.setattr(config_service, "_get_crypto_provider", lambda: None, raising=False)
    return config_service.get_panel()


def test_panel_exposes_edit_chat_slot(panel: dict) -> None:
    assert panel["edit_chat_api_base"] == EDIT_CHAT_ENV["EDIT_CHAT_API_BASE"]
    assert panel["edit_chat_api_key"] == EDIT_CHAT_ENV["EDIT_CHAT_API_KEY"]
    assert panel["edit_chat_model"] == EDIT_CHAT_ENV["EDIT_CHAT_MODEL_NAME"]
    assert panel["edit_chat_provider"] == EDIT_CHAT_ENV["EDIT_CHAT_PROVIDER"]
    assert panel["edit_chat_protocol"] == EDIT_CHAT_ENV["EDIT_CHAT_PROTOCOL"]
    assert panel["edit_chat_enabled"] == "true"


@pytest.mark.asyncio
async def test_config_get_request_includes_edit_chat_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump({}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{name}={value}\n" for name, value in EDIT_CHAT_ENV.items()), encoding="utf-8"
    )
    monkeypatch.setattr(config_service, "get_config_file", lambda: config_file)
    monkeypatch.setattr(config_service, "get_env_file", lambda: env_file)

    response = await config_service.handle_config_request(
        AgentRequest(request_id="panel-edit-chat", channel_id="web", req_method=ReqMethod.CONFIG_GET)
    )

    assert response.ok is True
    assert response.payload["edit_chat_enabled"] == "true"
    assert response.payload["edit_chat_model"] == EDIT_CHAT_ENV["EDIT_CHAT_MODEL_NAME"]
