---
id: F_20261120_1728_mxc-latency-optimization
title: MxcRuntime 延迟优化——保持隔离的前提下降低冷启动与单次 exec 开销
type: F
author: alexbhyeo
author_email: yeo.boon.hong@huawei.com
created: 2026-11-20
updated: 2026-11-20
designs: [D-20261120-1025-mxc-process-isolation]
design_note:
modules:
  - jiuwenbox/src/jiuwenbox/server/runtime
files:
  - jiuwenbox/src/jiuwenbox/server/runtime/mxc.py
  - jiuwenbox/src/jiuwenbox/server/runtime/mxc_daemon.py
tags: [jiuwenbox, mxc, sandbox, windows, latency, benchmark]
---

# MxcRuntime 延迟优化——保持隔离的前提下降低冷启动与单次 exec 开销

## 背景

[[D-20261120-1025-mxc-process-isolation]] 的 `MxcRuntime` 实现验证通过后,
量化基准(方法同本文档的实测结果)显示冷 `create()` 约 860ms、热
`exec()` 约 266ms,相对直接在主机上跑同样命令(无沙箱)的 ~47ms /
~63ms 有明显放大。本次在**不改变隔离边界**的前提下逐项验证并落地优化,
每一项都先实测再决定是否采纳,不采用未验证的假设。

## 逐项结果

### Fix 1——降低轮询间隔(采纳)

`mxc.py`/`mxc_daemon.py` 的文件轮询 IPC 原来每 50ms(exec 结果)/100ms
(daemon 就绪)检查一次。改成 5ms/10ms。零风险,因为只是检查更频繁,
不改变任何授权或通信内容。

### Fix 2——事件驱动 IPC(验证后放弃,不是没做,是测过不可行)

设想用 Windows 命名 Event 对象取代文件轮询。先做可行性 spike:在沙箱内
通过 `ctypes` 调用 `OpenEventW`/`SetEvent`。结果:

```
ImportError: DLL load failed while importing _ctypes:
A dynamic link library (DLL) initialization routine failed.
```

`_ctypes.pyd` 自身在 ProcessContainer 的受限 token 下无法完成 DLL 初始化
(与之前发现的 PowerShell `STATUS_DLL_INIT_FAILED` 同一类问题)。补发
`System32`/`WinSxS` 只读授权后复测,结果不变——确认不是缺授权,是更底层
的限制,MXC 策略 schema 里也没有"注册表/DLL 加载"这类授权项可用。
**结论:放弃,不勉强做。**

### Fix 3——Warm pool(采纳)

`MxcRuntime` 新增 `_warm_pool`:后台异步任务维持 `JIUWENBOX_MXC_WARM_POOL`
(默认 2)个预先起好、还没分配逻辑 `sandbox_id` 的 daemon-in-sandbox 实例;
`create()` 优先从池里取,池空时才走原来的冷启动路径,并在取用/冷启动后
异步补货。只有当调用方没有要求任何额外策略路径时才用池(有自定义
`readonly`/`readwrite` 需求的请求总是冷启动,避免池子里的通用实例授权
不够)。

**实测的真实限制**:池子对"突发、连续、无间隔"的创建请求帮助有限——
8 个连续创建里只有第 1 个吃到池子,后面 7 个池子还没补上就被取空了,
退化成和冷启动一样慢。对"有间隔的到达"(本次用 1.5s 间隔模拟真实会话
创建节奏)效果非常明显。这是池子大小 vs 到达速率的正常权衡,不是实现
缺陷,如实记录,没有挑有利的数字展示。

### Fix 5——沙箱内长驻 shell(采纳,过程中发现并修了两个正确性 bug)

`mxc_daemon.py` 新增一个长驻 `cmd.exe`(仅当请求没有自定义
`workdir`/`env` 时启用——这两者需要用 `cd`/`set` 才能生效,而那些设置会
残留到同一个 shell 上后续不相关的命令里,为了延迟收益冒这个状态污染的
风险不值得)。命令通过 stdin 写入,用一个带 token 的 `echo` 哨兵行
(`echo MXC_PERSISTENT_SHELL_DONE_<token>_%errorlevel%`)标记命令结束并
带出 exit code,daemon 的读线程把输出推进队列,`exec` 调用方阻塞读到
哨兵为止。

验证阶段连续踩到两个真实 bug,都是先跑测试发现、再修、再重新验证,
不是直接假设能用:

1. 第一版直接把 `mxc.py` 已经包好的 `cmd /c "<script>"` 整串原样写进
   长驻 shell——这个长驻 shell 本身就是 cmd.exe,相当于在它里面又嵌套
   起了一个新的 cmd.exe 子进程去解释这层包装,完全没省到"每条命令都要
   重新起一次 cmd.exe"这个开销。改成识别并剥掉这层包装,把裸脚本直接
   写进长驻 shell。
2. 剥掉包装后,长驻 shell 是以"交互式"方式常驻的,它自己的版本 banner、
   命令提示符、命令回显全部混进了 stdout。先用 `/Q` 关掉命令回显,再用
   `prompt $_` 把提示符改成固定的一行空行(而不是依赖 `PROMPT=` 环境变量
   置空——实测环境变量法不生效,cmd 会退回默认的 `$P$G` 提示符),
   daemon 侧按"每条命令前后各有定长的空行"做 strip,而不是瞎猜行数。
   加了一个多行输出(`echo line1 && echo line2`)的测试用例确认 strip
   逻辑不会误删真实输出中间的空行。

**已知权衡**:长驻 shell 把 stdout/stderr 合并成一路(spawn 时
`stderr=STDOUT`),所以走这条快路径时 `stderr` 字段总是空字符串——这是
设计决定,不是 bug,在代码注释和本文档里都写明了。

## 实测结果(本机,与 fix 之前使用同一套脚本,方法一致可比)

隔离结果:每一步(基线 MXC、+fix1、+fix3、+fix5)都是 6/6 全部通过,
三项 fix 都不触碰文件系统授权、网络策略或进程可见性,这是预期之内、
不是本次才验证的新结论。

延迟(全部为中位数):

| 阶段 | 冷 `create()` | 热 `exec()` |
|---|---|---|
| 无沙箱(参照) | ~47ms | ~63ms |
| MXC 基线(未优化) | 860ms | 266ms |
| + fix 1 | 633ms | 203ms |
| + fix 3(到达间隔 1.5s) | 141ms | (fix3 不影响 exec,不变) |
| + fix 5 | (fix5 不影响 create,不变) | 62ms |

`exec()` 从 266ms 降到 62ms(降 77%,只比无沙箱基线慢约 1.3 倍);
`create()` 在非突发到达模式下从 860ms 降到 141ms(降 84%)。

## 遗留问题

- Warm pool 在突发创建场景下的实际收益有限,如果未来的真实使用模式是
  "短时间内大量并发创建"而不是"有间隔的会话到达",需要重新评估池子
  大小策略(例如按观测到的到达速率动态调整 `JIUWENBOX_MXC_WARM_POOL`),
  而不是假设固定值 2 永远够用。
- 长驻 shell 的"无自定义 workdir/env 才启用"限制意味着真实工具调用里
  只要带了 workdir(常见——jiuwenswarm 的工具默认会算一个会话工作目录)
  就不会走这条快路径,回到每次重新 spawn 的旧开销。本次 benchmark 用的
  是不带 workdir 的简单命令,**实际收益上限取决于真实工具调用里有多少
  比例的请求不带 workdir/env**,这个比例本次没有测量,是后续如果要验证
  "真实工作负载下的收益"需要补的数据。
- fix 2 的放弃结论只针对"从 Python 用 ctypes 调 WinAPI"这条路径;没有
  测试过其他语言/技术栈(例如一个独立编译的小工具,不依赖 Python 的
  ctypes)是否能绕开同样的 DLL 初始化限制,留给以后决定是否值得再探索。
