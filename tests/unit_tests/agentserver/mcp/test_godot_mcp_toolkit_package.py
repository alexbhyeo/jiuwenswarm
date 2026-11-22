# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract tests for the in-repo ``connectors/godot-mcp-toolkit`` package source.

The directory is the package source for the Godot MCP Toolkit Hub connector
(stdio-mcp launching ``npx -y @npgamedev/godot-mcp-server``; the npm bridge
dials the Godot editor plugin's local WebSocket, so there is no credential of
any kind). These tests guard the package contract against drift: a broken
manifest / mcp.json / skill fails here instead of surfacing as a broken zip at
Hub-publish or connect time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jiuwenswarm.server.runtime.mcp.credential import extract_placeholders
from jiuwenswarm.server.runtime.mcp.package_manifest import load_mcp_package
from jiuwenswarm.server.runtime.mcp.registry import _normalize_transport

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_DIR = REPO_ROOT / "connectors" / "godot-mcp-toolkit"
SKILL_MD = PACKAGE_DIR / "skills" / "godot-mcp-toolkit-guide" / "SKILL.md"


def _load_package():
    return load_mcp_package(PACKAGE_DIR)


def test_package_contract() -> None:
    pkg = _load_package()
    assert pkg.package_id == "godot-mcp-toolkit"
    assert pkg.version == "1.0.0"
    assert pkg.integration_type == "stdio-mcp"
    # 全本机运行：npm 桥接 + 编辑器插件的本地 WebSocket，没有 token/oauth。
    assert pkg.credentials_type == "none"
    assert pkg.credentials_file is None
    assert pkg.server_name == "godot-mcp-toolkit"
    assert pkg.icon_file is not None and pkg.icon_file.name == "icon.png"
    assert [d.name for d in pkg.skill_dirs] == ["godot-mcp-toolkit-guide"]


def test_mcp_json_shape() -> None:
    pkg = _load_package()
    cfg = pkg.server_config
    assert cfg is not None
    # command 存在 -> 归一化为 stdio；一定不能带 url（那是 remote-mcp 的形态）。
    assert _normalize_transport(cfg.get("type", ""), cfg) == "stdio"
    assert "url" not in cfg
    assert cfg["command"] == "npx"
    # 固定用 npx 拉官方 npm 包，用户无需全局安装；-y 让首次连接免交互。
    assert cfg["args"] == ["-y", "@npgamedev/godot-mcp-server"]
    # 官方 .mcp.json.template 会写这个迁移标记；缺了桥接会以为配置来自旧版本，
    # 每次连接都在 stderr 打一条 WARNING。
    assert cfg["env"] == {"GODOT_MCP_CONFIG_VERSION": "1"}
    # timeout 单位是秒（build_config_entry 直接写入 timeout_s），600 = 10 分钟：
    # 试玩启动、资源导入这类操作会超过默认超时。
    assert cfg["timeout"] == 600


def test_no_credentials_are_declared() -> None:
    pkg = _load_package()
    assert pkg.server_config is not None
    # 声明零凭证时，integration 里也不能出现 ${VAR} 占位符，否则发布侧会
    # 以 UNDEFINED_PLACEHOLDER 拒绝这个包。
    assert extract_placeholders(pkg.server_config) == set()
    assert not (PACKAGE_DIR / "token-schema.json").exists()


def test_manifest_metadata_is_bilingual() -> None:
    manifest = json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))
    for field in ("display_name", "display_description"):
        assert set(manifest[field]) == {"zh", "en"}
    assert manifest["category"] == "游戏开发"
    assert manifest["examples"]["zh"] and manifest["examples"]["en"]
    # 版本要求写进用户可见描述里，避免装完才发现 Node 太旧。
    assert "Node.js 22" in manifest["display_description"]["zh"]
    assert "Node.js 22" in manifest["display_description"]["en"]


def test_bundled_skill_frontmatter() -> None:
    skill_md = SKILL_MD.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*(\S+)", skill_md, re.MULTILINE)
    assert match is not None and match.group(1) == "godot-mcp-toolkit-guide"
    # 这是最容易踩的两个坑：插件没启用、运行时工具在试玩前调用。指引里必须
    # 保留这两节，否则助手会在连接失败时反复重试而不是让用户去开插件。
    assert "编辑器插件" in skill_md
    assert "编辑器通道 vs 运行时通道" in skill_md
    # 写操作确认是行为需求的验收点，指引中必须保留该章节。
    assert "预览 → 确认" in skill_md
