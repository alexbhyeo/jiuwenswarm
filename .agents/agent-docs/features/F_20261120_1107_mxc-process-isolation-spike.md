---
id: F_20261120_1107_mxc-process-isolation-spike
title: MXC per-session 隔离验证 spike——实施记录
type: F
author: alexbhyeo
author_email: yeo.boon.hong@huawei.com
created: 2026-11-20
updated: 2026-11-20
designs: [D-20261120-1025-mxc-process-isolation]
design_note:
modules:
  - .agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/spike
files:
  - .agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/spike/session_daemon.py
  - .agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/spike/mxc_session_spike.py
  - .agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/design.md
  - .agents/agent-docs/designs/proposals/D-20261120-1025-mxc-process-isolation/architecture.md
tags: [jiuwenbox, mxc, sandbox, spike, verification]
---

# MXC per-session 隔离验证 spike——实施记录

## 范围

按 [[D-20261120-1025-mxc-process-isolation]] §6 的范围决定,本次只执行"阶段
1:验证 spike",**不接入 `jiuwenbox`/`sandbox_manager.py`**。产出是一个独立
脚本(`spike/` 目录),验证"每个 session 隔离、可并行、互不干扰"这条验收
标准在 MXC 的 Windows 默认后端(`processcontainer`)上是否成立,且不依赖
MXC 的持久化生命周期 API(因为核实后发现三个默认后端都不支持它,见
design.md §3.1)。

## 与设计的偏差

- **架构修正(执行中发现,已回写 design.md §3.1)**:原设计假设
  `provision_container`/`run_in_container`/`stop`/`deprovision` 可以直接
  映射 `RuntimeAdapter` 的 `create`/`exec`/`cleanup`。实际读
  `docs/schema.md` 第 672 行确认,只有 `isolation_session`/
  `windows_sandbox`/`wslc`(均为 Windows 专有、非默认)才支持这条生命周期;
  `processcontainer`/`bubblewrap`/`seatbelt` 都只是一次性 `ScriptRunner`。
  因此改为"一次性沙箱启动长驻 daemon + 文件轮询 IPC"的架构,细节见
  architecture.md §4。
- **IPC 简化**:architecture.md 的流程图画的是"命名管道",实际实现用
  **共享工作目录的文件轮询**(daemon 轮询 `cmd-*.json`,写
  `result-*.json`)。原因:文件轮询复用 MXC 已经为该会话开放的
  `readwritePaths`,不需要额外验证命名管道能否穿过 `processcontainer`
  的边界(这本身是未验证的假设,没必要在 spike 阶段引入第二个不确定性)。
  性能/延迟不是本次验收标准(验收标准是隔离性和并发正确性,不是吞吐),
  所以这个简化不影响结论。
- **测试命令全部用 Python 单行脚本,不用 `cmd.exe`**:避免引入"ACL 基线
  是否覆盖 `cmd.exe` 执行"这一额外未知变量;`python.exe` 的路径通过
  `readonlyPaths` 显式授权,行为确定。

## 构建 MXC

```powershell
# D:\git_projects\mxc,仓库已由用户预先克隆
Set-Location "D:\git_projects\mxc"
.\build.bat
```

默认构建(无参数)产出 `src/target/x86_64-pc-windows-msvc/release/wxc-exec.exe`
——这是本次唯一需要的二进制,不需要 `--with-microvm`/`--with-wslc`/
`--with-isolation-session`/`--with-hyperlight` 任何实验性 feature,因为
`processcontainer` 是默认 backend。

后续 `.NET SDK` 打包步骤因本机未安装 `dotnet` CLI 而失败
(`'dotnet' is not recognized`),**不影响** `wxc-exec.exe` 本身——Rust
编译阶段已经成功(`Finished 'dev' profile ... in 4m 37s`,随后
`Copying binaries into SDK package...` 明确打印 `Copied x64\wxc-exec.exe`)。
本次用的就是这个产物,没有安装 `dotnet` 或处理 NuGet 打包失败。

## 验证结果

执行 `spike/mxc_session_spike.py`(用本机系统 Python 3.11,不是
jiuwenswarm 的 `.venv`,避免把 venv 路径的可见性问题带进沙箱策略),
完整输出:

```
Using wxc-exec.exe: D:\git_projects\mxc\src\target\x86_64-pc-windows-msvc\release\wxc-exec.exe
Using python: C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe

=== Phase A: one-shot parallel isolation across 3 sessions ===
  session 1: exit=0 marker='session-1' expected='session-1' -> PASS
  session 0: exit=0 marker='session-0' expected='session-0' -> PASS
  session 2: exit=0 marker='session-2' expected='session-2' -> PASS
  session 0 reading session 1's marker: exit=1 -> PASS (denied as expected)
    stderr: Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File ".../pathlib.py", line 1058, in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:

=== Phase B: daemon-in-sandbox reuse, 3 sessions x 3 calls, concurrent ===
  all 3 sessions created (daemon.ready observed in each workspace)
  s0 call 0: exit=0 stdout='s0-0' expected='s0-0' -> PASS
  s0 call 1: exit=0 stdout='s0-1' expected='s0-1' -> PASS
  s0 call 2: exit=0 stdout='s0-2' expected='s0-2' -> PASS
  s2 call 0: exit=0 stdout='s2-0' expected='s2-0' -> PASS
  s2 call 1: exit=0 stdout='s2-1' expected='s2-1' -> PASS
  s2 call 2: exit=0 stdout='s2-2' expected='s2-2' -> PASS
  s1 call 0: exit=0 stdout='s1-0' expected='s1-0' -> PASS
  s1 call 1: exit=0 stdout='s1-1' expected='s1-1' -> PASS
  s1 call 2: exit=0 stdout='s1-2' expected='s1-2' -> PASS
  wall time: 2.44s (serial bound for n=3 would be ~4.50s) -> PASS (ran concurrently)
  all sessions cleaned up

=== Summary ===
Phase A (one-shot isolation):       PASS
Phase B (daemon reuse + parallel):   PASS
```

**结论对应设计文档的验收标准**(architecture.md §3):

| 验收项 | 验证方式 | 结果 |
|---|---|---|
| 数据隔离(会话间不串) | 3 个会话并发各写 marker,内容逐一核对 | PASS,三个 marker 内容均与对应会话一致 |
| 边界隔离(跨会话不可达) | 会话 0 在未被授权的情况下读取会话 1 的 marker | PASS,`exit=1`,Python 抛出文件不可读异常(ACL 拒绝,不是"文件不存在"那种误报) |
| 并发正确性(非串行) | 3 会话 × 3 次 exec,每次 0.5s 睡眠,总耗时应接近 0.5s×3 而不是 0.5s×3×3 | PASS,实测 2.44s,远低于串行化理论值 4.50s |
| 清理完整 | 每个会话 `cleanup()` 后工作目录和 daemon 进程被回收 | PASS,未发现残留进程或目录(本次用 `shutil.rmtree` + `Popen.wait`/`kill`,未做更严格的进程树扫描,见"遗留问题") |

## 遗留问题(未在本次验证范围内)

- **进程树清理的严格性**:本次 `cleanup()` 只等待 `wxc-exec.exe` 本身退出
  (`Popen.wait`/`kill`),没有像 seatbelt 文档描述的那样扫描并确认沙箱内
  所有子孙进程都已终止。如果未来 daemon 内部 fork 出不受 `wxc-exec.exe`
  直接管理的孙进程,本次的清理方式可能遗漏——生产化时需要补一个进程树
  扫描或依赖 Windows Job Object。
- **真实工具调用路径未覆盖**:本次测试命令都是简单的 Python 一行脚本
  (写文件、读文件、sleep+print),没有覆盖 jiuwenbox 实际工具调用会用到
  的路径模式(例如调用 `git`、`npm`、读取较深的项目目录树)。design.md
  §7 "`processcontainer` 的 ACL 叠加 vs 默认拒绝"这条未决问题的"结论"
  (并入 spike 验收标准)**只在本次简单场景下验证通过**,更复杂的真实
  工具调用路径仍需要单独补测。
- **未测试并发规模超过 3**:验证了 N=3 的并行度,没有测试更大 N(例如
  10+)下是否有隐藏的资源限制(每用户会话数、文件锁竞争等)。
- **没有测试异常路径**:daemon 被强制杀死后 `wxc-exec.exe` 是否正确报告、
  工作目录被外部删除后的行为等边界情况未覆盖。
- **是否接入 `sandbox_manager.py`**:按设计文档的范围决定,本次有意不做,
  仍是后续决定,取决于是否要让 jiuwenbox 变成跨平台服务(这个问题本身
  也不在本次范围内)。

## 代码位置

- [spike/session_daemon.py](../designs/proposals/D-20261120-1025-mxc-process-isolation/spike/session_daemon.py)
- [spike/mxc_session_spike.py](../designs/proposals/D-20261120-1025-mxc-process-isolation/spike/mxc_session_spike.py)

两个文件都明确标注"NOT part of jiuwenbox"/"standalone spike",不是
`jiuwenbox` 包的一部分,不会被其测试套件或构建流程捡到。
