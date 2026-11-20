---
id: D-20261119-1518-microvm-guest-agent-protocol
title: 跨平台 per-session microVM：守护进程协议与运行时适配
slug: microvm-guest-agent-protocol
status: draft
stage: proposals
approved-date:
author: alexbhyeo
author_email: yeo.boon.hong@huawei.com
reviewers: []
created: 2026-11-19
updated: 2026-11-19
domain: jiuwenbox
modules:
  - jiuwenbox/src/jiuwenbox/server/runtime
  - jiuwenbox/src/jiuwenbox/supervisor
  - jiuwenbox/src/jiuwenbox/models
tags: [jiuwenbox, microvm, firecracker, sandbox, cross-platform, vsock]
scope: 为每个会话启动一个 microVM、会话结束即销毁，覆盖 Windows/Linux/macOS 三端；定义三端共用的 guest-agent 协议与 RuntimeAdapter 落地方式。
related: []
type: D
files: []
---

# 跨平台 per-session microVM：守护进程协议与运行时适配

## 1. 目标与约束

每个 jiuwenswarm 会话启动时创建一个 microVM，会话结束时销毁。开发/部署环境覆盖
Windows、Linux、macOS 三端。

**硬约束**：Firecracker 只支持 Linux + KVM（官方文档明确限定 Linux host），
Windows 没有 KVM（是 Hyper-V）、macOS 没有 KVM（是 `Virtualization.framework`）。
因此不存在单一工具覆盖三端——这是三个不同的 VMM 后端，共用一套上层协议。

## 2. 决策：按 OS 选择后端，统一 guest-agent 协议

复用 `jiuwenbox` 既有的 `RuntimeAdapter` 抽象（`server/runtime/base.py`），按
`platform.system()` 在进程启动时选定一个后端，上层（policy engine、warm pool、
routes）不感知具体后端：

| OS | Hypervisor | Guest↔host 通道 | 后端 |
|---|---|---|---|
| Linux | KVM（经 Firecracker） | `virtio-vsock` | `FirecrackerRuntime`（生产主目标） |
| macOS | `Virtualization.framework` | `VZVirtioSocketDevice` | `VzRuntime` |
| Windows | Hyper-V | Hyper-V Sockets（`AF_HYPERV`） | `HyperVRuntime` |

三者的 guest↔host 通道都是"vsock 语义"的 socket，没有网络栈参与——这是三端能
共用同一套协议、只需三份薄传输层绑定的原因。

## 3. Guest-agent 协议（三端共用的核心）

每个 guest 镜像内运行一个极简 agent，监听 vsock-等价通道上的固定端口/服务 ID，
实现 `RuntimeAdapter` 所需的能力。协议本身与传输层解耦：

```
Host (jiuwenbox)                    Guest (agent)
      │  connect(vsock, port=1024)        │
      ├──────────────────────────────────>│
      │  { "op": "exec", "command": [...], "workdir": ..., "env": {...}, "timeout": ... }
      ├──────────────────────────────────>│
      │  { "stdout": ..., "stderr": ..., "exit_code": ... }  (一次性) 或
      │  stream of { "stdout_chunk": ... } ... { "exit_code": ... }  (exec_background)
      │<──────────────────────────────────┤
```

- 传输：每端一条独立实现，但对上层暴露同一个 `connect()/send()/recv()` 接口
  （Linux 用 `AF_VSOCK`，macOS 用 `VZVirtioSocketConnection`，Windows 用
  `AF_HYPERV`）。
- 消息格式：换行分隔的 JSON（简单、易调试，带宽不是瓶疾——这是控制面，不是
  数据面）。大文件走 `write_file`/`read_file` 专用 op，用 base64 或分块传输，
  不复用 `exec` 的 stdin。
- 操作集合直接对应 `RuntimeAdapter` 的抽象方法：`exec`、`exec_background`、
  `get_background_job`、`list_background_jobs`、`kill_background_job`、
  `write_file`、`read_file`、`list_dir`、`search_files`。`create`/`stop`/
  `is_running`/`cleanup` 是宿主侧生命周期管理，不经过这条通道（它们管理的是
  VM 本身，而不是 VM 内的某个操作）。
- 健康检查：agent 启动后立即在固定 op（如 `"ping"`）上响应，host 侧用它判断
  "VM 已就绪可以接受请求"，而不是猜测 boot 完成的时间。

## 4. 各后端需要补的东西

- **Linux（`FirecrackerRuntime`）**：生产路径必须经 `jailer`（chroot、cgroup、
  降权、network namespace），不能直接跑裸 `firecracker` 二进制。现有
  `supervisor/cgroup.py`、`supervisor/network.py` 的策略翻译逻辑可以部分复用，
  但目标从"给 `bwrap` 生成参数"变成"给 `jailer`/Firecracker API 生成参数"。
- **macOS（`VzRuntime`）**：经小型 Swift/Go helper 驱动（`code-hex/vz` 一类的
  Go 绑定是现成参考）。启动耗时未实测，不能照搬 Firecracker 的数字，需要单独
  benchmark。
- **Windows（`HyperVRuntime`）**：经 Windows Hypervisor Platform API 或 Hyper-V
  自身管理面。Windows 没有 `jailer` 的等价物，这部分 sandboxing 脚手架需要自己
  写。WSL2 不是捷径——默认不暴露 `/dev/kvm`，Firecracker 进不去。
- **三端通用**：guest 镜像本身（最小 Linux + jiuwenswarm 工具需要的运行时，
  如 Python/Node）需要独立构建、版本化，并随 policy schema 一起演进。

## 5. 分阶段建议

1. Linux/Firecracker spike：手搭一个 Python-only 的 golden rootfs，直接走
   `jailer` + Firecracker API（先不接 `jiuwenbox`），实测真实冷启动/快照恢复
   耗时。
2. Guest-agent v0：只实现 §3 协议里 `RuntimeAdapter` 必需的最小子集。
3. `FirecrackerRuntime` 适配器：实现 ABC，接入现有 `warm_pool.py`（若 spike
   数据支持，用快照恢复取代 `bwrap` fork）。
4. macOS spike：复用同一份 guest-agent 协议，只换传输层绑定，验证"一套协议
   三端传输"的假设是否成立。
5. Windows 最后做：评估 Hyper-V 路径的实际工程量是否值得，或者 Windows 上先
   接受一个较弱的边界（如容器）作为过渡。

## 6. 未决问题

- "per-session" 的粒度是否就是一个会话一个 VM（会话期间不重建)？若是，VM 生命
  周期和会话生命周期需要显式挂钩（会话创建时 `create`，会话结束/超时/断开时
  `cleanup`），这部分目前在 `jiuwenbox` 里没有现成的"会话"概念，需要上层
  （agent server 还是 jiuwenbox 自己）决定谁来发起 `create`/`cleanup`。
- Windows/macOS 的真实驱动力是什么：是生产多租户隔离的硬需求，还是"本地开发
  环境行为一致"的便利性需求？两者对工程投入的合理程度完全不同，决定了 §5 第
  4、5 步要不要真的做到生产质量。
- `conch.py`（现有的远程 sandbox 后端）底层是否已经是 VM 级隔离——如果是，
  Linux 上 Firecracker 的核心卖点就从"比 bwrap 更安全"变成"本地、无厂商依赖、
  延迟更低"，需要找到 Conch 的负责人确认。
