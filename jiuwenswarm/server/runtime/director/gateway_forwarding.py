# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""让 Gateway 把 ``director.*`` 方法转发给 AgentServer.

导演模式是母仓独有的功能。Gateway 拆分为独立包 ``agent-gateway`` 之后，母仓
fork 的 ``gateway/`` 目录被整体删除，其中 ``_FORWARD_REQ_METHODS`` /
``_FORWARD_NO_LOCAL_HANDLER_METHODS`` 里的 ``director.*`` 一并消失，于是所有
导演模式请求都变成 ``unknown method``。

这两个集合在 ``agent_gateway.channel_manager.web.app_web_handlers`` 里是模块级
``frozenset``，而 ``app_gateway._run`` 是在**运行期**用 ``from ... import`` 取的，
所以只要在 ``_run`` 执行前把模块属性换成带导演模式方法的新集合，Web 通道就会
按原有链路转发（归一化、凭据刷新、MessageHandler 投递、响应回传全部沿用）。

补丁必须在网关进程启动时、``app_gateway`` 主流程之前打上，入口见
``jiuwenswarm/server/gateway_entry.py``。打不上时不影响其它功能，导演模式
退回到 ``unknown method``（与本补丁引入前一致）。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Iterable

# 与 jiuwenswarm/common/schema/message.py 里补齐的 DIRECTOR_* ReqMethod 一一对应。
DIRECTOR_FORWARD_METHODS: frozenset[str] = frozenset({
    "director.projects.list",
    "director.projects.create",
    "director.projects.get",
    "director.projects.rename",
    "director.projects.delete",
    "director.generate",
    "director.generate.check_status",
    "director.asset.rename",
    "director.asset.bind_name",
    "director.asset.delete",
    "director.edit_chat.send",
    "director.edit_chat.skill_get",
    "director.edit_chat.skill_set",
    "director.lab_canvas.save",
    "director.clip.compose",
})

# 剪辑助手的团队轮次（ads-codirector：十几个隔离成员调用 + 一批串行生成）合法地
# 超过 AgentClient 的 600s 默认值；超时丢响应会让用户看到"失败"，而 AgentServer
# 其实已经把结果落盘了。
LONG_RUNNING_METHODS: frozenset[str] = frozenset({"director.edit_chat.send"})
LONG_RUNNING_TIMEOUT_SECONDS = 3600.0

_FORWARD_SET_NAMES = ("_FORWARD_REQ_METHODS", "_FORWARD_NO_LOCAL_HANDLER_METHODS")

logger = logging.getLogger(__name__)

_MARKER_PATH = Path.home() / ".jiuwenswarm" / "agent" / ".logs" / "director-forwarding.log"


def _mark(message: str) -> None:
    """把补丁结果落到固定文件：网关进程的 stdlib logger 未配置，日志会丢."""
    try:
        _MARKER_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _MARKER_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} {message}\n")
    except OSError:
        pass


def install_director_forward_methods(
    extra: Iterable[str] = DIRECTOR_FORWARD_METHODS,
) -> bool:
    """把 ``extra`` 并进 Web 通道的两个转发方法集合；可重复调用."""
    try:
        from agent_gateway.channel_manager.web import app_web_handlers
    except ImportError:
        logger.warning("[director] 未找到 agent_gateway Web 通道处理器，导演模式转发未启用")
        return False

    added = frozenset(extra)
    patched = 0
    for name in _FORWARD_SET_NAMES:
        current = getattr(app_web_handlers, name, None)
        if isinstance(current, frozenset):
            setattr(app_web_handlers, name, current | added)
            patched += 1
    if patched == 0:
        logger.warning("[director] Gateway 转发方法集合结构与预期不符，导演模式转发未启用")
        _mark("forward: 未找到转发方法集合")
        return False
    _mark(f"forward: 命中 {patched} 个集合，追加 {len(added)} 个方法")
    logger.info(
        "[director] 已让 Gateway 转发导演模式方法（%d 个，命中 %d 个集合）",
        len(added),
        patched,
    )
    return True


def _send_with_director_lease(original):  # noqa: ANN001, ANN202 - 透传原函数签名
    async def wrapper(agent_client, env, *, label, timeout_seconds=None):  # noqa: ANN001, ANN202
        method = str(getattr(env, "method", "") or "")
        if timeout_seconds is None and method in LONG_RUNNING_METHODS:
            # 必须把上限透传给 AgentClient：它自己也有一个 600s 的内层默认值，
            # 只在外层套 asyncio.wait_for 是没用的——内层会先超时。
            try:
                return await agent_client.send_request(
                    env, timeout=LONG_RUNNING_TIMEOUT_SECONDS,
                )
            except TypeError:
                # 老客户端/测试替身不接受 timeout 关键字，退回网关原有的外层计时
                timeout_seconds = LONG_RUNNING_TIMEOUT_SECONDS
        return await original(
            agent_client, env, label=label, timeout_seconds=timeout_seconds,
        )

    return wrapper


def install_long_running_edit_chat() -> bool:
    """放宽 ``director.edit_chat.send`` 的等待上限；可重复调用."""
    try:
        from agent_gateway.message_handler import message_handler
        from agent_gateway.routing import agent_request_timeout
    except ImportError:
        logger.warning("[director] 未找到 Gateway 请求超时模块，剪辑助手长轮次未放宽")
        return False

    if getattr(agent_request_timeout, "_director_lease_enabled", False):
        return True

    original = agent_request_timeout.send_agent_request_with_timeout
    patched = _send_with_director_lease(original)
    # message_handler 用 from ... import 绑定了原函数，两个位置都要替换。
    agent_request_timeout.send_agent_request_with_timeout = patched
    if hasattr(message_handler, "send_agent_request_with_timeout"):
        message_handler.send_agent_request_with_timeout = patched
    agent_request_timeout._director_lease_enabled = True
    _mark("lease: director.edit_chat.send -> 3600s")
    logger.info(
        "[director] 已为 %s 设置 %ss 等待上限",
        ", ".join(sorted(LONG_RUNNING_METHODS)),
        int(LONG_RUNNING_TIMEOUT_SECONDS),
    )
    return True


def install() -> None:
    """在网关主流程之前安装全部导演模式兼容补丁."""
    install_director_forward_methods()
    install_long_running_edit_chat()
