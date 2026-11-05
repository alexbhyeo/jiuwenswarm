# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway 进程入口：先装导演模式兼容补丁，再进 ``agent_gateway`` 主流程.

网关拆仓后母仓无法再编辑网关源码，而导演模式的转发方法清单原本就写在被删掉的
``gateway/`` 目录里。补丁只需在 ``app_gateway._run`` 读取那两份方法集合之前生效，
所以最稳的位置是这里：进程刚起来、任何网关模块都还没进入主流程的时候。
"""

from __future__ import annotations

import sys


def main() -> None:
    # 导演模式的 ReqMethod 成员由母仓运行期补齐（协议包不收录）。网关进程不导入
    # jiuwenswarm 的话，app_gateway 在 route.forward_methods 命中后仍会因枚举里
    # 找不到该方法而回 unknown method，所以这里先把补齐逻辑跑起来。
    import jiuwenswarm.common.schema.message  # noqa: F401 - 导入即注册 DIRECTOR_* 成员

    from jiuwenswarm.server.runtime.director.gateway_forwarding import install

    install()

    from agent_gateway.app_gateway import main as gateway_main

    gateway_main()


if __name__ == "__main__":
    sys.exit(main())
