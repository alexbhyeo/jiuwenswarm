---
id: D-20261120-1025-mxc-process-isolation
title: 用 MXC 做跨平台 per-session 进程级隔离
slug: mxc-process-isolation
status: draft
stage: proposals
approved-date:
author: alexbhyeo
author_email: yeo.boon.hong@huawei.com
reviewers: []
created: 2026-11-20
updated: 2026-11-20
domain: jiuwenbox
modules:
  - jiuwenbox/src/jiuwenbox/server/runtime
tags: [jiuwenbox, mxc, sandbox, cross-platform, process-isolation]
scope: 评估用微软 MXC（Microsoft eXecution Container）作为 jiuwenbox 新增 RuntimeAdapter 后端，在 Windows/Linux/macOS 三端提供统一的进程级会话隔离，取代"每个 OS 手写一个 VM 后端"的方案。
related: [D-20261119-1518-microvm-guest-agent-protocol]
type: D
files: []
---

# 用 MXC 做跨平台 per-session 进程级隔离

> 架构图与流程图见 [architecture.md](architecture.md)。

## 0. 背景

[[D-20261119-1518-microvm-guest-agent-protocol]] 提出按 OS 分别接入
Firecracker（Linux/KVM）、`Virtualization.framework`（macOS）、Hyper-V
（Windows）三套 VM 后端，并为三者设计共用的 guest-agent 协议。这个方案的
代价是：三套完全不同的宿主机工程（jailer 参数生成、Swift/Go helper、
Windows Hypervisor Platform API），且 Windows 侧明确标注"没有 jailer 等价物，
需要自己写"。

本文档评估另一条路径：直接用微软开源的 **MXC**（本地已克隆到
`D:\git_projects\mxc`，仓库地址 `github.com/microsoft/mxc`）做隔离层。MXC
本身就是"一个 SDK，多种隔离后端，按平台选择"，与 jiuwenbox 的
`RuntimeAdapter` 抽象目标高度重合——只是 MXC 把这件事已经做完了，且三端
默认后端全部是**进程级隔离**，不是 VM，不需要 KVM/Hyper-V/WHP 这类宿主虚拟化
前提。

## 1. MXC 实际提供什么（读自本地仓库，非官网转述）

`README.md` 开头定义："a sandboxed code execution system for running
untrusted code … on Windows, Linux, and macOS … multiple containment
backends, from OS-native process sandboxes to full VMs"。

三端默认后端：

| 平台 | 默认 backend | 隔离机制 | 其他可选 backend |
|---|---|---|---|
| Linux | `bubblewrap`（稳定，现有默认） | Linux user namespace + seccomp，无需 root | `lxc`、`hyperlight`（实验） |
| macOS | `seatbelt` | Apple Seatbelt 内核沙箱（App Sandbox 同款） | 无 |
| Windows | `processcontainer` | Windows BaseContainer（`ALL_APPLICATION_PACKAGES` ACL 隔离） | `windows_sandbox`、`wslc`、`microvm`（NVX，实验，仅 x64）、`hyperlight`（实验）、`isolation_session` |

关键点：**Linux 默认后端就是 bubblewrap**——与 jiuwenbox 现有的
`server/runtime/process.py`（已确认为 bubblewrap-based）完全同源。MXC 不是
给 Linux 引入新技术，而是把 jiuwenbox 已经手写的那部分，和 Windows/macOS 的
等价实现，统一到一套 SDK 和一套 JSON 策略模型下。

## 2. 隔离能力到底有多强（三端分别核实）

### Linux — bubblewrap（`docs/backends/bwrap/bubblewrap-backend.md`）

- unshare user/PID/IPC/UTS namespace，`--unshare-net` 做网络隔离
- 默认 deny-by-default 文件系统：只挂载 `/bin /sbin /lib* /usr/* /etc`
  等系统基线，`$HOME`、`/opt`、`/var`、`/sys` 全部不可见，需显式
  `readonlyPaths`/`readwritePaths` 开放
- seccomp 过滤 `socket()`/`socketpair()`，仅允许 Unix/IPv4/IPv6/netlink，
  封堵 io_uring 绕过
- `--cap-drop ALL`，sandbox 进程启动前校验 capability 集合清空，5 秒内
  不满足则直接失败而不执行 workload
- 网络支持三种模式：完全隔离（`--unshare-net`）、地址级 allow/deny
  （slirp4netns + 单机命名空间内 iptables，无需 root）、经代理转发
  （cooperative，仅对守规矩的 client 生效）

### macOS — seatbelt（`docs/backends/seatbelt/seatbelt-backend.md`）

- 同一套 `sandbox_init()` 内核沙箱，App Store 应用用的就是这个
- 文件系统 read-only/read-write/denied 路径,内核强制，SIP 保护路径
  任何策略都无法覆盖
- 网络只有"开/关 + loopback 例外"的粒度，**没有** per-host/per-IP/per-CIDR
  过滤能力（这是 Seatbelt 本身的限制，不是 MXC 没做）
- 进程树隔离，没有"容器"概念，没有生命周期可管理——每次调用是一次性
  process tree

### Windows — processcontainer（`docs/backends/process-container/*.md`）

- 基于 Windows BaseContainer + `ALL_APPLICATION_PACKAGES` ACL，需要较新的
  Windows 11 版本（24H2 构建 26100.9278 起）
- 文件系统访问同时受 MXC 显式策略和 Windows ACL 隐式授权共同影响，两者
  叠加，不是单一来源
- `windows_sandbox`（基于 Hyper-V 的 Windows Sandbox）和 `microvm`（NVX，
  实验性，Windows x64 only，经签名 Firecracker/OpenVMM 变体承载 Linux guest）
  是更强隔离的可选后端，但都标注 experimental

**结论**：三端的默认路径都是**进程级隔离**（namespace/seccomp、Seatbelt、
BaseContainer），不是 VM 边界。比 bare 容器强（有强制的文件系统白名单和
capability drop），但比 Firecracker 弱（共享内核）。macOS 和 Linux 默认
路径的能力接近；Windows 默认路径的网络/文件系统隔离粒度不如另外两者
（依赖 Windows ACL 叠加,不是纯粹的显式策略）。

## 3. 生命周期模型——与"per-session"需求的对齐

`docs/container-lifecycle.md` 定义的持久化生命周期正好对应"会话期间复用
一个容器"的需求：

```
provision → start → exec(可多次) → stop → deprovision
```

- `provision_container` 返回不透明的 `ContainerId`，会话期间保留
- 会话内多次工具调用都走 `run_in_container`/`spawn_in_container`，
  在同一个 provisioned 容器里执行,不是每次调用重新建进程
- 会话结束调用 `stop_container` + `deprovision_container`

这与 jiuwenbox `RuntimeAdapter` 的 `create`/`exec`/`exec_background`/
`cleanup` 几乎是一一对应的映射，不需要像 [[D-20261119-1518-microvm-guest-agent-protocol]]
那样另外设计一套 vsock guest-agent 协议——因为 MXC 的 backend 都在**宿主侧**
执行（bubblewrap/seatbelt/processcontainer 都不是"远程 guest"，没有虚拟机
边界要跨），`run_in_container`/`spawn_in_container` 直接就是 host 进程边界
内的调用。

### 3.1 更正（2026-11-20 核实）：三个默认后端实际不支持这条生命周期

直接读 `docs/schema.md` 第 672 行附近原文确认：

> "Backends supporting container lifecycle operations today are
> `isolation_session`, `windows_sandbox`, and `wslc` (all Windows-only)."

即**只有** `isolation_session`、`windows_sandbox`、`wslc` 这三个
**仅限 Windows** 的后端实现了 `StatefulSandboxBackend`（真正的
provision→start→exec×N→stop→deprovision）。`bubblewrap`
（`docs/backends/bwrap/bubblewrap-backend.md` 明确写"No container lifecycle
operations: Bubblewrap's `ScriptRunner` supports create-and-run execution,
not `StatefulSandboxBackend` lifecycle calls"）、`seatbelt`（"No container
abstraction. No persistent container to attach to or destroy — every
invocation is a fresh process tree"）、以及 Windows 默认的
`processcontainer`，三者都只实现一次性的 `ScriptRunner`。上面"几乎一一
对应"的结论**只对 `isolation_session`/`windows_sandbox`/`wslc` 成立**，
对三个默认后端不成立。

**修正后的架构决策**（见 [architecture.md](architecture.md)）：会话内
"建一次、exec 多次"的复用，不能指望 MXC 原生生命周期提供，要在应用层
自己做——在一次性沙箱执行里启动一个**长驻 daemon 进程**，之后的 `exec`
调用绕开 MXC、直接找这个 daemon 通信（命名管道）。这正是 jiuwenbox 现有
`server/runtime/process.py` 对 bubblewrap 采用的同一套策略（`bwrap` 本身
也是一次性的，daemon 是 jiuwenbox 自己在沙箱里启动并保活的），只是把
"生成 bwrap argv"换成"生成 MXC JSON 请求"。

## 4. 集成到 jiuwenbox 的现实约束

### 4.1 没有 Python SDK——需要走 C ABI 或子进程

`README.md` 列出的官方 SDK 只有 Rust / .NET / Node 三种，**没有 Python**。
jiuwenbox 是 Python 实现，有两条路：

1. **子进程方式**（低风险，推荐起步）：直接调用平台可执行文件
   `wxc-exec.exe`（Windows）/ `lxc-exec`（Linux）/ `mxc-exec-mac`（macOS），
   喂 JSON（`schemas/stable/`），解析 stdout/退出码。README 明确支持这种
   "non-SDK consumption"路径,专门给"SDK 无法嵌入"的场景用。
2. **C ABI 方式**（`src/ffi/mxc_ffi/`，有 `lib.rs`/`pty.rs`/`streaming.rs`/
   `state_aware.rs`）：通过 `ctypes`/`cffi` 绑定,免去每次调用启动可执行文件
   的开销,但需要自己维护一层 Python binding 并随 MXC 版本升级同步。

建议：第一阶段用子进程 JSON 方式验证可行性和性能,确认值得投入后再考虑
C ABI 绑定。

### 4.2 Windows 版本门槛

`processcontainer` 需要 Windows 11 24H2 构建 26100.9278 以上才有完整
Process Isolation 支持(`docs/backends/process-container/os-version-support.md`)。
部署目标如果是较旧的 Windows Server 或 Windows 10,需要先确认兼容性,
MXC 对此没有兜底。

### 4.3 实验性后端的生产可用性

`microvm`（NVX）、`hyperlight`、`windows_sandbox`、`wslc`、`lxc`、
`isolation_session` 全部标注 experimental 或非默认——如果未来需要比进程级
隔离更强的边界,这些是候选但不是现在就能依赖的生产路径。`hyperlight` 值得
关注:它是 Linux(KVM)和 Windows(WHP)共用一条代码路径的 micro-VM 方案,
跑的是 Unikraft unikernel,每次请求从热快照恢复、不新建虚拟机,如果将来需要
比进程隔离更强但比完整 Firecracker guest 更轻的中间档,这是优先评估对象。

## 5. 与 [[D-20261119-1518-microvm-guest-agent-protocol]] 的关系

两份文档不是互斥的,是同一个问题的两个隔离强度档位:

| | MXC（本文档） | Firecracker/Hyper-V/Vz（D-20261119-1518） |
|---|---|---|
| 隔离边界 | 进程级（namespace/Seatbelt/ACL） | VM 级（独立内核） |
| 跨平台工作量 | 已由 MXC 做完,只需接入 SDK/可执行文件 | 需要分别实现三套 VMM 封装 + guest-agent |
| Windows 现实路径 | 有（`processcontainer`,生产可用） | 无（Hyper-V 路径仍需从零写,WSL2 不是捷径） |
| 适用场景 | 多租户边界要求不是"必须 VM 级"时的默认选择 | 需要抵御内核级逃逸、与宿主机完全隔离的场景 |

建议顺序:先用 MXC 的进程级隔离覆盖"per-session 隔离"的基本需求(尤其是
解决 Windows 当前完全没有方案的问题),把 VM 级隔离作为后续按需升级的选项,
而不是一开始就要求三端都做到 VM 边界。

## 6. 分阶段建议

1. **验证 spike(本次执行范围)**:不接入 jiuwenbox,单独写一个脚本/模块,
   在 Windows 上用 `wxc-exec.exe` 的一次性 create-and-run 启动"沙箱内
   daemon",验证 N 个会话并行创建、并发 exec、互不可见、正确清理——
   对应 [architecture.md](architecture.md) §3 的验收标准。选择这一步而不
   直接接入 jiuwenbox 的理由见下方"范围决定"。
2. **`MxcRuntimeAdapter` 子进程封装**:spike 验证通过后,用 Python
   `subprocess` 调用 `wxc-exec.exe`/`lxc-exec`/`mxc-exec-mac`,实现
   `RuntimeAdapter` 的 `create`/`exec`/`exec_background`/`cleanup`,
   `create` 对应"一次性 create-and-run 启动 daemon",`exec`/
   `exec_background` 对应"命名管道/Unix socket 发给 daemon",不再直接映射
   `provision_container`/`run_in_container` 这类持久化生命周期 API(§3.1
   已更正:三个默认后端不支持它)。
3. **接入 `sandbox_manager.py`**:新增 `RUNTIME_MXC` 常量和
   `_runtime_for` 分支(参照现有 `RUNTIME_CONCH` 的写法),但要注意
   `RUNTIME_CONCH` 目前在 `sandbox_manager.py` 里被至少 7 处独立
   `if ref.runtime == RUNTIME_CONCH` 分支特殊处理(reaper 资格、
   stop/cleanup 路径、策略校验等),新增第三个后端需要逐一检查这些分支
   是否也要覆盖 `RUNTIME_MXC`,不能假设"实现了 `RuntimeAdapter` 接口就
   自动接入"。这一步的工作量明显大于第 2 步,且因为 jiuwenbox 本身目前
   仅支持 Linux(`README.md`:"Requirements: Linux"),这一步大概率还牵涉
   "jiuwenbox 要不要先支持在 Windows/macOS 上跑起来"这个更大的问题,
   不在本文档范围内一并决定。
4. **按需评估 C ABI 绑定**:如果子进程方式的性能(进程启动开销、JSON
   序列化)成为瓶颈,再投入 `mxc_ffi` 的 Python 绑定。
5. **按需评估更强隔离档位**:如果进程级隔离在某些租户/工具场景下不够,
   评估 `hyperlight`(跨 Linux/Windows 一致)或 Firecracker(Linux 专用,
   `D-20261119-1518` 路线)作为可选的更强边界,而不是默认全量切换。

### 范围决定(2026-11-20)

讨论后确认本次只执行第 1 步(验证 spike),不做第 3 步(接入
`sandbox_manager.py`)。理由:

- §3.1 的更正说明"一次性沙箱 + 应用层 daemon"这套架构本身还没有验证过
  ——先证明它在 MXC 的 `processcontainer` 上能跑、能并行、能互相隔离,
  再谈接入生产代码,顺序更安全。
- `sandbox_manager.py` 对现有第二后端(`ConchRuntime`)的特殊处理分散在
  7+ 个调用点,且类里大量逻辑假设 Linux(cgroup、netns、iptables)。在
  没有 Linux 环境验证这些交互之前接入第三个后端,风险和返工概率都偏高。
- jiuwenbox 当前的硬性需求(`README.md`)是 Linux only;把 MXC 接进去
  意味着同时要决定"jiuwenbox 要不要变成跨平台服务",这是比"要不要用
  MXC"更大的产品决策,不应该在这份以 MXC 评估为主题的文档里顺带拍板。

## 7. 未决问题(已解决)

- **Windows 版本门槛(24H2+)**:本机(执行本次 spike 的开发机)Windows
  版本为 10.0.26200,落在 25H2 区间,满足
  `docs/backends/process-container/os-version-support.md` 列出的
  Process Isolation 构建号(26200.9278+)。**结论:本机可以验证,但生产
  部署目标机群的实际 Windows 版本仍需单独盘点,不能用本机结果代表全部
  目标机器。**
- **`processcontainer` 的 ACL 叠加 vs 默认拒绝**:spike 阶段用实际读写
  会话私有目录之外路径的测试用例验证(见 architecture.md §3 第 2 条
  验证项:"会话 1 尝试读取 W2 路径 → 失败"),不再只看文档描述。
  **结论:并入 spike 的验收标准,随 §6 第 1 步一起产出实测结果。**
- **MXC 遥测**:README 明确"Local open-source debug builds are not
  configured to route telemetry to Microsoft"。**结论:spike 和後续
  `MxcRuntimeAdapter` 都使用本地源码构建(`build.bat`,不链接签名遥测
  组件),规避这个问题;如果未来需要签名 release 构建分发给其他机器,
  需要重新评估这条——不在本次范围内发生。**
- **没有 Python SDK**:**结论:采用 §4.1 已给出的子进程 JSON 方案
  (调用 `wxc-exec.exe` 而不是绑定 SDK),不等待也不依赖上游提供 Python
  binding。是否向上游贡献 Python SDK 是独立的、优先级更低的后续决定,
  不阻塞本提案任何一步。**
