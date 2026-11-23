---
title: Firecracker 实验结果
author: null
author_note: 历史作者待确认；缺少原 owner 和可靠创建署名
id: experiments
type: D
modules: []
files: []
---

# Firecracker 实验结果

本次 Firecracker 适配器开发过程中跑的三组实验，全部针对真实容器
(`jiuwenbox-firecracker` 镜像、`--device=/dev/kvm`) 和真实 jiuwenbox
HTTP API/Firecracker 原生 API，不是模拟数据。

## 实验一：create/exec/delete 延迟对比（bwrap vs firecracker）

容器：单实例，n=8，串行调用 jiuwenbox 真实 HTTP API
(`POST /sandboxes`、`/exec`、`DELETE /sandboxes/{id}`)。

### 均值

| 运行时 | create | exec（热 sandbox） | delete |
|---|---|---|---|
| bwrap（process runtime，对照组） | 0.761s | 0.025s | 1.048s |
| firecracker | 4.651s | 0.020s | 0.304s |

### 单次明细

| Run | bwrap create | bwrap exec | bwrap delete | firecracker create | firecracker exec | firecracker delete |
|---|---|---|---|---|---|---|
| 1 | 0.613s | 0.027s | 1.358s | 7.299s | 0.024s | 0.481s |
| 2 | 0.697s | 0.017s | 1.381s | 6.597s | 0.026s | 0.373s |
| 3 | 0.642s | 0.018s | 0.258s | 7.095s | 0.026s | 0.363s |
| 4 | 0.631s | 0.017s | 0.240s | 3.555s | 0.012s | 0.249s |
| 5 | 0.877s | 0.024s | 1.333s | 3.442s | 0.014s | 0.209s |
| 6 | 1.473s | 0.064s | 1.266s | 2.850s | 0.029s | 0.299s |
| 7 | 0.614s | 0.016s | 1.245s | 2.739s | 0.014s | 0.220s |
| 8 | 0.538s | 0.015s | 1.307s | 3.633s | 0.015s | 0.235s |

结论：create 慢 ~6x，主因是每个 sandbox 拷贝 1GB golden rootfs（`shutil.copyfile`，
未做 COW），不是 KVM 本身的开销；exec 两者量级相当；delete 反而更快（只是
kill 进程 + 删工作目录，不像 bwrap 要拆 netns/cgroup）。

## 实验二：内核隔离（`/proc/sys/kernel/random/boot_id`）

验证 bwrap 与 Firecracker 对"内核"的隔离程度是否不同——`boot_id` 是每次
内核启动生成的唯一值，同一个运行中内核实例的所有进程读到的值必然相同。

| 对象 | boot_id |
|---|---|
| 容器自身（= 宿主共享内核） | `375ddaf7-d180-4c74-87e6-d91c31d6c4ee` |
| bwrap sandbox | `375ddaf7-d180-4c74-87e6-d91c31d6c4ee`（与宿主**完全相同**） |
| firecracker sandbox #1 | `cb6f5a98-87aa-461e-98dc-039b86a61775`（不同） |
| firecracker sandbox #2 | `fb7006bd-6c0d-4a8f-a221-2f5540a60d5f`（不同，且与 #1 也不同） |

结论：bwrap sandbox 与宿主共享同一个内核实例（内核级 0 隔离——内核漏洞可
直接穿透）；每个 Firecracker sandbox 都启动了自己独立的内核实例，互相之间
也不共享。

## 实验三：跨进程存档/恢复（原生 Firecracker API，尚未接入 jiuwenbox 适配器）

验证 bwrap/`ProcessRuntime` 完全不具备、Firecracker 原生具备的能力：把一个
正在运行的 VM 完整状态存盘，杀掉原进程，在**全新的 OS 进程**里恢复，确认
执行状态（含后台任务的运行点）原样延续。

| 步骤 | 结果 |
|---|---|
| VM A 内启动后台计数循环，快照前读数 | `8` |
| `PATCH /vm {state: Paused}` | `HTTP 204` |
| `PUT /snapshot/create` | `HTTP 204` |
| `kill -9` 原 firecracker 进程（PID 285） | 确认已死 |
| 全新 firecracker 进程（PID 305）`PUT /snapshot/load {resume_vm: true}` | `HTTP 204` |
| 恢复后立即读数 | `12`（接着 8 继续，不是从 0 重来） |
| 恢复后 1 秒再读 | `16`（确认仍在真实运行，不是静态内存快照） |

结论：完全独立的新进程，从快照精确恢复了一个仍在运行的后台任务，这是
`ProcessRuntime` 在没有 CRIU 的情况下做不到的能力（已用 grep 确认
`process.py`/`base.py` 里没有任何 checkpoint/CRIU 代码）。

**注意**：本实验直接操作裸 `firecracker` 二进制的原生 API，尚未接入
`FirecrackerRuntime` 适配器或 jiuwenbox 的 REST API——这正是设计文档
阶段 4（pause/resume）、阶段 5（checkpoint/fork）要做的事，已在下面实现并验证。

## 实验四：阶段 4/5 接入 `FirecrackerRuntime` 适配器后的端到端验证

与实验三不同，这次直接打 jiuwenbox 真实 REST API（`/sandboxes/{id}/pause`、
`/resume`、`/checkpoint`、`/sandboxes/checkpoints/{id}/fork`），走的是产品化
的 `FirecrackerRuntime.pause/resume/checkpoint/fork` 代码路径，不是手搓
裸 API。过程中发现并修正了一个真实的版本差异：本镜像用的 Firecracker
v1.18.0 二进制的 `/snapshot/load` **不支持** swagger（仓内 `main` 分支）里
写的 `drive_overrides` 字段（实测报 400，`unknown field`）。改用
`PATCH /drives/{id}`（post-boot 热替换磁盘路径）替代：

- `checkpoint()`：snapshot 前先把原 sandbox 的磁盘热切到一份 checkpoint 专属的
  冻结拷贝再 `snapshot/create`（这样 snapshot 里记录的路径不依赖原 sandbox
  是否后续被删除），然后再把原 sandbox 的磁盘切回它自己的文件。
- `fork()`：`snapshot/load` 时 `resume_vm: false`（先不跑），紧接着
  `PATCH /drives/rootfs` 换成这次 fork 自己独立的磁盘拷贝，再 `PATCH /vm
  {state: Resumed}`——保证任何 guest 代码跑起来之前磁盘已经换成独立的，
  不会有两个 fork 共享同一份可写磁盘的问题。

n=1 完整流程，全部针对真实 jiuwenbox HTTP API：

| # | 测试点 | 结果 |
|---|---|---|
| 1 | 创建 firecracker sandbox | `phase=ready` |
| 2 | 基线 exec | `exit_code=0, stdout="baseline-ok"` |
| 3 | 写入标记文件 + 启动后台计数循环 | — |
| 4 | `POST .../pause` | `phase=paused` |
| 5 | 暂停期间 exec | HTTP 409（正确拒绝，不是挂起到超时） |
| 6 | `POST .../resume` | `phase=ready` |
| 7 | resume 后 exec + 计数器读数 | `exit_code=0`；计数器非零（暂停期间状态保留） |
| 8 | `POST .../checkpoint`（sandbox 仍在运行，非破坏性） | 返回 `checkpoint_id` |
| 9 | checkpoint 之后原 sandbox 仍可用 | `phase=ready` |
| 10 | checkpoint **之后**修改原 sandbox 的标记文件 | — |
| 11 | `POST checkpoints/{id}/fork` 生成 fork #1 | `phase=ready` |
| 12 | fork #1 读标记文件 | 读到 checkpoint 时刻的内容，不是原 sandbox 之后改的内容 |
| 13 | fork #1 读计数器 | 非零（从 checkpoint 时刻的状态继续，不是从 0 开始） |
| 14 | 对同一个 checkpoint 再 fork 一次，生成 fork #2 | `phase=ready` |
| 15 | 在 fork #1 上改标记文件，分别读 fork #2 和原 sandbox | 两者都没看到 fork #1 的改动——三者磁盘完全独立 |
| 16 | 对 `bwrap` 类型 sandbox 调 `pause` | 正确拒绝："uses runtime 'process', which does not support pause" |

**结果：17 项断言全部通过（PASS=17 FAIL=0）**，包括最关键的跨 fork 隔离性
（15 号）——证明每个 fork、以及 fork 的源 sandbox，各自持有独立可写磁盘，
互不干扰，不是共享同一份文件的假隔离。
