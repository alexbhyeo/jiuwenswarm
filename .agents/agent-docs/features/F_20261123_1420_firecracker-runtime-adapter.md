---
title: Firecracker per-session microVM 运行时适配器实现与验证
author: alexbhyeo
id: F_20261123_1420_firecracker-runtime-adapter
type: F
modules:
- jiuwenbox/src/jiuwenbox/server/runtime
- jiuwenswarm/server/runtime/agent_adapter
files:
- jiuwenbox/src/jiuwenbox/server/runtime/firecracker.py
- jiuwenbox/src/jiuwenbox/server/sandbox_manager.py
- jiuwenbox/src/jiuwenbox/server/routes/sandbox.py
- jiuwenbox/src/jiuwenbox/models/sandbox.py
- jiuwenbox/src/jiuwenbox/models/common.py
- jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py
- jiuwenswarm/common/config.py
- jiuwenbox/docker/Dockerfile.firecracker
- jiuwenbox/docker/firecracker/build-assets.sh
- jiuwenbox/docker/firecracker/guest_agent.py
- jiuwenbox/docker/firecracker/guest-agent.service
designs:
- D-20261123-1054-firecracker-microvm-per-session
author_email: yeo.boon.hong@huawei.com
---

# Firecracker per-session microVM 运行时适配器实现与验证

## 元信息

| 项 | 值 |
|---|---|
| 范围 | jiuwenbox 新增 `FirecrackerRuntime(RuntimeAdapter)` 及其 `sandbox_manager.py` 分发；jiuwenswarm `sysop_builder.py` 新增 `jiuwenbox-firecracker` sandbox 类型；`jiuwenbox-firecracker` Docker 镜像（内置 firecracker 二进制 + 内核 + 预装 guest-agent 的 golden rootfs） |
| 测试基线 | 无自动化单测（手动在 Docker 容器内对真实 jiuwenbox HTTP API 做端到端验证，见下）；`ast.parse` 语法检查全部通过 |
| 范围约束 | 按用户要求，本次仅实现 Firecracker；MXC/ProcessContainer 不移植到本分支 |

## 实施要点

| 主题 | 实际做法 |
|---|---|
| 控制面 | 手写最小 HTTP/1.1-over-AF_UNIX 客户端（`_http_over_uds`）调用 Firecracker API：`/logger`、`/boot-source`、`/drives/rootfs`、`/machine-config`、`/vsock`、`/actions`（InstanceStart）|
| 数据面 | 沿用设计文档验证过的 vsock 协议：host 侧纯 `AF_UNIX` 连接 `CONNECT <port>\n` → `OK <port>\n` 握手，之后该 socket 即为与 guest-agent 的数据通道 |
| guest-agent 协议扩展 | 在 spike 版 `guest_agent.py` 基础上新增 `exec_background`/`bg_status`/`bg_list`/`bg_kill`（`RuntimeAdapter` 的抽象方法要求）及 `exec` 的 `stdin_b64` 支持 |
| jiuwenbox 分发 | `sandbox_manager.py` 新增 `RUNTIME_FIRECRACKER = "firecracker"`，`normalize_sandbox_runtime` 接受 `"firecracker"`，`_runtime_for` 惰性构造 `FirecrackerRuntime()`（与既有 `RUNTIME_CONCH` 分支同构） |
| jiuwenswarm 分发 | `sysop_builder.py` 新增 `jiuwenbox-firecracker` 分支（镜像 `jiuwenbox-conch` 写法）：`extra_params["api_sandbox_runtime"] = "firecracker"`、`fallback_on_failure = False`；`config.py::resolve_sandbox_enabled` 补充该类型字符串 |
| Docker 镜像 | 新增 `jiuwenbox/docker/Dockerfile.firecracker`（多阶段：`fc-assets` 从 Firecracker 官方 CI S3 bucket 拉取最新内核 + Ubuntu squashfs rootfs 并注入 guest-agent systemd 服务重打包为 ext4；`fc-bin` 拉取官方 release 二进制；最终阶段基于现有 openEuler jiuwenbox 基础镶入二者），运行时需要 `--device=/dev/kvm` |

## 与设计的关系

对应 `D-20261123-1054-firecracker-microvm-per-session` 的阶段计划第 (1)(2)(4)(5) 步：
guest-agent v0（已在设计阶段验证）、`FirecrackerRuntime` 适配器、**pause/resume**、
**checkpoint/fork** 均已实现并端到端验证（见下）。(3) 凭证代理、(6) jailer 化仍未
实现，保持设计文档 §8/§9 中的未决状态。

### Phase 4/5 补充实施（pause/resume/checkpoint/fork）

| 主题 | 实际做法 |
|---|---|
| pause/resume | `FirecrackerRuntime.pause/resume` 调 `PATCH /vm {state: Paused/Resumed}`；`_FirecrackerSandbox.paused` 标记供 `exec`/`exec_background` 判断（暂停期间直接拒绝，而不是挂到 vsock 超时） |
| checkpoint | `PUT /snapshot/create`；为了让快照独立于源 sandbox 的生命周期，snapshot 前先把磁盘热切到 checkpoint 专属的冻结拷贝（`PATCH /drives/rootfs`），拍完快照后再切回源 sandbox 自己的文件 |
| fork | `PUT /snapshot/load {resume_vm: false}` 先加载不跑，`PATCH /drives/rootfs` 换成这次 fork 独立的磁盘拷贝，再 `PATCH /vm {state: Resumed}` 才真正跑起来——保证没有两个 fork（或 fork 与源）共享同一份可写磁盘 |
| jiuwenbox 分发 | `SandboxManager` 新增 `pause_sandbox`/`resume_sandbox`/`checkpoint_sandbox`/`fork_sandbox`，用 `getattr(runtime, "pause", None)` 之类的可选能力检测——非 Firecracker 运行时调用时明确报 `SandboxStateError`，不是静默忽略 |
| REST API | 新增 `POST /sandboxes/{id}/pause`、`/resume`、`/checkpoint`（返回 `checkpoint_id`）、`POST /sandboxes/checkpoints/{checkpoint_id}/fork` |
| 模型 | `SandboxPhase` 新增 `PAUSED`；`AuditEventType` 新增 `SANDBOX_PAUSED/RESUMED/CHECKPOINTED/FORKED` |

**实现中发现并修正的真实版本差异**：本镜像的 Firecracker v1.18.0 二进制的
`/snapshot/load` **不支持** swagger（firecracker 仓 `main` 分支文档）里的
`drive_overrides` 字段（实测 `PUT` 返回 400 `unknown field`）。改用
`PATCH /drives/{id}`（Firecracker 原生支持的 post-boot 热替换磁盘路径）达到
同等效果，而不是依赖一个这个版本还没有的字段。

## 设计偏差 / 已知缺口

| 主题 | 偏差 | 原因 |
|---|---|---|
| 主机目录挂载 | `jiuwenbox-firecracker` 直接拒绝 `sandbox.files` 的 allow/deny/upload，并丢弃 workspace/project_dir 的自动挂载（仅打印 warning） | microVM 有独立 rootfs，本次未实现 virtiofs/9p；设计文档 §9 已标注为空白项，本次不展开 |
| 容器权限 | Phase-1 镜像以 root 运行（未跟随主 Dockerfile 的 uid 1001 `app` 用户） | jailer 化 / 非 root 硬化是设计 §8 的 phase 6，本次范围只到 phase 2 |
| exec 环境变量 unset 语义 | `RuntimeExecRequest.env` 中值为 `None` 的键直接被过滤，不像 Conch 那样通过 `env -u` 真正从父进程环境里 unset | guest-agent 协议简化；新适配器不要求与 Conch 行为对齐 |
| 网络接口 | `create()` 未调用 `/network-interfaces`，microVM 启动时无网卡 | 本设计的数据面是 vsock，不需要 guest 出网；若后续需要出网需补一次独立评估 |

## 验证证据（容器内对真实 jiuwenbox HTTP API）

容器：`docker run --device=/dev/kvm -p 18321:8321 jiuwenbox-firecracker`（额外 `--privileged --cgroupns=host` 仅用于让对照组 `bwrap` 跑通 cgroup v2 委派，与 Firecracker 适配器本身无关）。

```
POST /api/v1/sandboxes {"sandbox_runtime":"firecracker"}
  -> phase=ready pid=20                                   (~3-7s：镜像内 ext4 拷贝 + microVM 冷启动)
POST /api/v1/sandboxes/<id>/exec {"command":["sh","-c","echo hi && pwd && whoami"]}
  -> {"exit_code":0,"stdout":"hello-from-firecracker-vm\n/\nroot\n","stderr":""}   (~0.01-0.03s)
POST /api/v1/sandboxes/<id>/exec_background {...sleep 2...}
  -> started=true; 轮询 /background/<job_id> 正确报告 running=false, exit_code=0
DELETE /api/v1/sandboxes/<id>
  -> 204                                                   (~0.2-0.5s)
```

实现过程中发现并修复一个真实 bug：`_http_over_uds` 最初用 `reader.read()` 等 EOF 判断响应结束，但 Firecracker 的 API server 不因客户端 `Connection: close` 而主动关闭连接，导致每次 API 调用都挂到超时。修复为解析响应头拿 `Content-Length` 再精确 `readexactly`。

## 性能对比（process/bwrap 对照 vs firecracker，同一容器，n=8，串行）

| 运行时 | create 均值 | exec 均值（热 sandbox） | delete 均值 |
|---|---|---|---|
| bwrap（process runtime，对照组） | 0.761s | 0.025s | 1.048s |
| firecracker | 4.651s | 0.020s | 0.304s |

说明：这是本机（Windows Docker Desktop / WSL2 KVM，单次串行测试、无 jailer、无网络设备配置）下的粗粒度对比，不是生产 SLA 测量；Firecracker 官方文档给出的裸机 boot 延迟（<125ms）在此环境下不适用——本次 create 耗时主要是每 sandbox 拷贝 1GB golden rootfs ext4（`shutil.copyfile`，phase-1 未做 COW/硬链接优化）+ microVM 冷启动，而非 KVM 本身的开销。exec 延迟两者相当（vsock 往返与 bwrap exec 量级相近）；firecracker 的 delete 明显更快，因为只是 kill 进程 + 删除工作目录，不像 bwrap 要拆 netns/cgroup。

## 验证证据：pause/resume/checkpoint/fork（真实 jiuwenbox REST API，n=1，17 项断言全过）

详见 `experiments.md` 实验四；关键点：checkpoint 对运行中的 sandbox 是非破坏性的
（checkpoint 后原 sandbox 仍可继续用）；对同一个 checkpoint fork 两次，两个 fork
与源 sandbox 三者磁盘完全独立（在 fork #1 上写文件，fork #2 和源 sandbox 都读不到）；
对 `bwrap` 类型调 pause 被正确拒绝（`SandboxStateError`，不是静默忽略）。

## 遗留

- rootfs 拷贝是本次观测到的 create 延迟主因，后续若要对齐设计文档 §3 的毫秒级目标，需要 overlay/COW（如 qcow2 backing file 或 reflink）而非整份拷贝。
- 未做 jiuwenswarm 侧的浏览器 E2E（按用户决定，本次范围限定在 jiuwenbox 容器化 + 直接 API 验证；jiuwenswarm 自身服务由用户自行启动）。
- 容器化本身目前要求 `--device=/dev/kvm`；bwrap 对照组额外需要 `--privileged --cgroupns=host`，这是本机 Docker Desktop 的 cgroup v2 委派限制，与 Firecracker 改动无关，但在决定如何部署 `jiuwenbox-firecracker` 镜像时需要一并考虑容器运行权限。
- checkpoint 文件目前落在宿主机 `JIUWENBOX_FIRECRACKER_WORKDIR/checkpoints/<id>/`，没有生命周期管理（谁来删、保留多久、按什么粒度计费），仍是设计文档 §9 标注的最大空白——本次只实现了"能 checkpoint/fork"，没有解决"checkpoint 该怎么治理"。
- `assembly.py` 对 team 成员沙箱的 `sandbox_type == "jiuwenbox"` 硬编码检查仍未放开，fork 对 team/子 agent 并行场景的价值目前用不上（见 `flow.md`）。
- idle reaper 尚未改为对 firecracker sandbox 用 pause 代替 destroy（设计文档阶段 4 提到的另一半），这次只做了"能手动 pause/resume"，没有接入空闲淘汰逻辑。
