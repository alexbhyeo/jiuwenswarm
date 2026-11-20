---
id: F_20261120_1453_mxc-jiuwenswarm-frontend-e2e
title: MXC 接入 jiuwenbox 并从 jiuwenswarm 真实前端驱动——实施与验证记录
type: F
author: alexbhyeo
author_email: yeo.boon.hong@huawei.com
created: 2026-11-20
updated: 2026-11-20
designs: [D-20261120-1025-mxc-process-isolation]
design_note:
modules:
  - jiuwenbox/src/jiuwenbox/server/runtime
  - jiuwenbox/src/jiuwenbox/server
  - jiuwenbox/src/jiuwenbox/supervisor
  - jiuwenswarm/server/runtime/agent_adapter
  - jiuwenswarm/common
files:
  - jiuwenbox/src/jiuwenbox/server/runtime/mxc.py
  - jiuwenbox/src/jiuwenbox/server/runtime/mxc_daemon.py
  - jiuwenbox/src/jiuwenbox/server/sandbox_manager.py
  - jiuwenbox/src/jiuwenbox/server/workspace.py
  - jiuwenbox/src/jiuwenbox/supervisor/posix_id.py
  - jiuwenbox/src/jiuwenbox/server/runtime/process.py
  - jiuwenbox/src/jiuwenbox/server/policy_reader.py
  - jiuwenbox/src/jiuwenbox/server/policy_engine.py
  - jiuwenbox/src/jiuwenbox/models/policy.py
  - jiuwenswarm/server/runtime/agent_adapter/sysop_builder.py
  - jiuwenswarm/common/config.py
tags: [jiuwenbox, jiuwenswarm, mxc, sandbox, windows, e2e]
---

# MXC 接入 jiuwenbox 并从 jiuwenswarm 真实前端驱动——实施与验证记录

## 范围

在 [[D-20261120-1025-mxc-process-isolation]] 的 spike(`F_20261120_1107_mxc-process-isolation-spike.md`)基础上,
本次把 spike 验证过的 daemon-in-sandbox 架构落成 jiuwenbox 的真实 `RuntimeAdapter`
实现,接入 `sandbox_manager.py`,并在 jiuwenswarm 侧新增 `jiuwenbox-mxc` sandbox
provider 类型,最终用真实 Chrome(headless,CDP 直连,不用 Claude in Chrome——
该工具需要人工在场批准站点权限,不适合非交互场景)驱动 jiuwenswarm 的真实 Web
前端,确认"每个 session 隔离、可并行、互不干扰"这条验收标准在真实系统里成立。

## 产出

### jiuwenbox 侧(新代码)

- `server/runtime/mxc.py`——`MxcRuntime(RuntimeAdapter)`。架构与 spike 一致
  (一次性 `wxc-exec.exe` create-and-run 启动沙箱内 daemon,后续 `exec`/
  `exec_background` 走共享工作目录的文件轮询 IPC),补了 spike 没做的部分:
  后台任务跟踪(`job-*.json`)、`sys.base_prefix` 授权(见下)、Git bash 路径
  授权、bash/PowerShell → `cmd` 的命令翻译(见下)、从通用 `filesystem_policy.
  read_only`/`read_write` 里过滤出真正的 Windows 绝对路径。
  `write_file`/`read_file`/`list_dir`/`search_files` 仍是 `RuntimeAdapter`
  基类的"unsupported"默认(调用方退回 `exec`),这是有意的 MVP 范围,不是遗漏。
- `server/runtime/mxc_daemon.py`——沙箱内长驻 daemon(无 jiuwenbox 依赖的独立
  脚本),比 spike 版本多了后台任务(`exec_background`/`bg_kill`)和"请求的
  workdir 不可达时回退到会话自己的工作目录"的容错(见下"问题与修复" 第 5 条)。
- `sandbox_manager.py`:新增 `RUNTIME_MXC` 常量、`normalize_sandbox_runtime`
  接受 `"mxc"`、`_runtime_for` 分发、把网络策略更新路径的报错文案改成按
  runtime 区分(原文案假设"非 process 就是 conch",现在 mxc 也有自己的措辞)。

### jiuwenswarm 侧(新代码)

- `server/runtime/agent_adapter/sysop_builder.py`:新增
  `normalized_type == "jiuwenbox-mxc"` 分支,仿照已有的 `jiuwenbox-conch`
  分支——设置 `extra_params["api_sandbox_runtime"] = "mxc"`,并从 append-merge
  的策略片段里**剔除 `filesystem_policy`**(原因见下"问题与修复" 第 3 条)。
- `common/config.py`:`resolve_sandbox_enabled` 的类型集合加入
  `"jiuwenbox-mxc"`。

### 本机运行环境配置(非代码,记录以便复现)

- `~/.jiuwenswarm/config/config.yaml` 新增 `sandbox:` 段:
  `url: http://127.0.0.1:18321`、`type: jiuwenbox-mxc`、
  `startup_mode: external`、`enabled: true`。
- jiuwenbox 启动时设置 `JIUWENBOX_MXC_EXEC_PATH`(指向本机构建的
  `wxc-exec.exe`)和 `JIUWENBOX_WARM_POOL=0`(见下"问题与修复" 第 7 条)。

## 问题与修复(按发现顺序)

每一条都是本次执行中**真实遇到并复现**的问题,不是预先设计的测试用例。

1. **`workspace.py` 在 Windows 上导入期崩溃**:无条件 `import pwd`(POSIX 专有)。
   改为 `sys.platform == "win32"` 时跳过导入,`_effective_user_home()` 在
   Windows 上直接用 `Path.home()`。不影响 Linux 行为。

2. **`supervisor/posix_id.py` 同样的问题**:`import grp`/`import pwd` 在模块
   级无条件导入。改为函数内按需延迟导入(只有真的调用到非数字 user/group 解析
   时才需要,且只会在 bwrap 路径上发生)。

3. **`server/runtime/process.py` 模块级 `ctypes.CDLL("libc.so.6", ...)`**:
   同样是无条件执行,导致 `app.py` 一 import 就挂。改成 `_libc()` 懒加载函数,
   第一次真正调用 `setns` 时才加载,Linux 行为不变。

4. **`policy_reader.py`/`policy_engine.py` 读写 YAML 时不带 `encoding="utf-8"`**:
   本机 Windows 默认 locale 是 GBK,读取 UTF-8 编码的策略文件直接
   `UnicodeDecodeError`。三处 `open()` 都补上显式 `encoding="utf-8"`。这个问题
   与 MXC 无关,任何中文 Windows 机器跑 jiuwenbox 都会遇到。

5. **`models/policy.py` 的 `_expand_path` 用 `str(Path(...))` 而不是
   `.as_posix()`**:在 Windows 上 `str(WindowsPath("/home"))` 会把 POSIX 风格
   的沙箱路径静默改写成 `\home`,导致所有 bwrap 导向的策略路径在 Windows 上
   校验失败("Filesystem policy paths must be absolute sandbox paths")。改成
   `.as_posix()` 后,Linux 行为完全不变(PosixPath 本来就用正斜杠),Windows 上
   不再篡改分隔符。**这一条不是 MXC 专属 bug,是任何在 Windows 上跑 jiuwenbox
   控制面的场景都会踩到的通用 bug。**

6. **jiuwenswarm 侧 `build_filesystem_policy()` 产出的 `bind_mounts` 用真实
   Windows 路径(`C:\Users\...`),jiuwenbox 的 `PolicyEngine.validate_policy`
   对 `bind_mounts.host_path`/`sandbox_path` 统一按 POSIX 绝对路径校验**:
   两边各自是对的(jiuwenswarm 发真实主机路径合理,jiuwenbox 按 bwrap 的沙箱
   路径约定校验也合理),只是放在一起不兼容。`MxcRuntime` 本来就不消费
   `bind_mounts`(每个会话有自己的私有工作目录),所以修复点放在
   `sysop_builder.py` 的 `jiuwenbox-mxc` 分支——直接把 `filesystem_policy`
   从发给 jiuwenbox 的策略片段里删掉,而不是改 jiuwenbox 通用校验器的语义。

7. **`ProcessRuntime` 的 warm pool 后台线程无条件尝试 `ip netns add`**:
   在 Windows 上找不到 `ip` 二进制,`FileNotFoundError` 后不停重试(约 1 秒/次),
   把 `jiuwenbox/.venv/server.log` 写到 20 万行并最终拖垮了那个 jiuwenbox 进程
   (观察到的现象是进程直接消失,retry 循环本身应该不会 crash 主进程,具体
   死因未深挖,但停掉 warm pool 后问题不再出现)。本次用
   `JIUWENBOX_WARM_POOL=0` 规避,**没有修代码**——这是 `ProcessRuntime`(bwrap
   专属子系统)的既有行为,与 `MxcRuntime` 无关,只是它在 Windows 上会一直
   failing-retry 而不是因为"没启用"就跳过。是否要让 warm pool 在非 Linux 上
   自动跳过,留给以后决定是否需要修。

8. **`MxcRuntime.create()` 只授权 `sys.prefix`,venv 场景下 daemon 启动不了**:
   venv 的 `python.exe` 启动器要读 `pyvenv.cfg` 里记录的 base 解释器路径
   (`sys.base_prefix`),只给 venv 自己的目录授权时,daemon 进程直接报
   `No Python at '<base_prefix>\python.exe'`。改成同时授权
   `sys.prefix` 和 `sys.base_prefix`。

9. **真实 shell 工具调用的 bash/PowerShell 在沙箱里启动即失败
   (`STATUS_DLL_INIT_FAILED`,0xC0000142)**:jiuwenswarm 的
   `command_tools._resolve_execution_plan` 在 Windows 上对"看起来像 POSIX"
   的命令会去找主机自己 PATH 上的 bash(典型是 Git for Windows 的
   MSYS2 bash,`[<bash路径>, "-lc", script]`),找不到时退回 PowerShell
   (`[<powershell路径>, "-NoProfile", "-NonInteractive", "-Command",
   script]`)。这两种可执行文件在 ProcessContainer 的受限 token 下,哪怕
   已经授权读取其所在目录,仍会在自己的初始化阶段失败(怀疑是访问注册表/
   桌面堆/会话对象等文件系统授权覆盖不到的资源,没有继续往下挖)。
   `cmd.exe` 没有这个问题,且已经被 ProcessContainer 的基线 ACL 覆盖,不需要
   额外授权。修复:`MxcRuntime._command_line()` 识别这两种 argv 形状(按
   basename 匹配,兼容绝对路径;参数匹配 `-c`/`-lc` 或
   `-NoProfile -NonInteractive -Command`),把内层脚本改用 `cmd /c "<script>"`
   执行。这是**按场景做的翻译,不是通用兼容层**——脚本里真用到 bash/PowerShell
   专属语法的部分,翻译后交给 `cmd.exe` 还是会失败,行为和脚本本身在裸
   `cmd.exe` 里跑是否能过完全一致。

10. **`MxcRuntime.exec()` 透传的 `workdir` 在沙箱里不可达时,子进程创建直接
    报 `[WinError 267] The directory name is invalid`**:jiuwenswarm 的
    bash 工具默认会带一个按会话算出来的工作目录(形如
    `...\JiuwenSwarm\<date>\chat-N\work`),这个目录在 MXC 沙箱的授权范围外
    (且经常在主机上也还没被创建)。修复:`mxc_daemon.py` 的
    `_run_foreground` 捕获 `OSError`,在请求的 `workdir` 不可用时回退到会话
    自己的工作目录重跑一次,并在 stderr 里明确记录"回退"这件事(不是静默
    降级)。这条不是伪造成功——回退后命令是在真实、确实存在、确实属于本
    会话的目录里真正执行的。

11. **配置迁移遗留的 `symphony` 字段形状冲突(与 MXC 完全无关,纯粹是
    uv sync 升级包版本后暴露的既有问题)**:`uv sync` 把本机 venv 从
    `workswarm==0.2.8` 升级到 `0.2.9b5` 后,`~/.jiuwenswarm/config/config.yaml`
    里残留的旧版 `symphony` 字段形状(`enabled`/`paths`/`fingerprint`/`build`/
    `evolution`/`orchestration`(旧形)/`skill_retrieval`)被新版
    `SymphonyConfig` pydantic 模型按 `extra_forbidden` 拒绝,导致**所有**
    `session.create` 请求失败(`AgentServer runtime failed` /
    `RUNTIME_FAILED`),不分 runtime,包括默认的 `process`。根因是
    `migrate_config_from_template` 只做新增字段的加法合并,不处理被新模板
    重命名/重结构化的旧字段,新旧字段因此共存冲突。修复:直接用
    `jiuwenswarm/resources/config.yaml` 模板里当前的 `symphony` 形状
    (`capability`/`storage`/`retrieval`/`orchestration`(新形))替换本机
    config.yaml 里的旧内容。**没有改任何代码**——纯粹是本机配置文件的数据
    修复,迁移脚本本身是否该在未来处理"字段改名"这类情况,留给以后决定。

## 验证结果(真实浏览器,CDP 直连,不是模拟)

### 单会话:端到端成功

通过真实 headless Chrome 打开 `http://localhost:5173`,在真实聊天输入框里输入
"Run the shell command `echo hello-from-browser-session-4` and show me the
exact output.",点击真实的发送按钮。Jiuwen(agent)的回复:

```
命令已成功执行。以下是原样输出：

Command: echo hello-from-browser-session-4
Stdout: hello-from-browser-session-4
Stderr: (empty)
Exit Code: 0
```

同时 `GET http://127.0.0.1:18321/api/v1/sandboxes` 确认存在一个真实的
`"runtime":"mxc","phase":"ready"` 沙箱。

### 两个并行会话:互不干扰

打开第二个 Chrome 标签页(同一 Chrome 实例,独立 CDP 页面目标),两个标签页
几乎同时各发一条不同的消息(`echo parallel-tab-A` / `echo parallel-tab-B`)。
两边的 Jiuwen 回复都在各自的时间线里正确显示了**自己那条**命令的输出,没有
串台:

- 标签页 A(`3233CF1C...`):`parallel-tab-A`,exit 0
- 标签页 B(`A899F8A0...`):`parallel-tab-B`,exit 0

两个会话实际共享同一个底层 jiuwenbox 沙箱(`a4d4bb26-205`)——这是
jiuwenswarm 自己的按项目共享沙箱设计(`isolation_custom_id` 按项目算,不是
按会话),不是本次新引入的行为。真正验证的是:**同一个 MXC 沙箱内,两个
真实前端会话并发发起的 `exec` 请求,通过 daemon 的按请求 UUID 文件轮询
IPC,互不覆盖、互不串数据**——这正是 spike 阶段 Phase B 验证过的属性,
现在在真实前端负载下复现成立。

## 遗留问题

- 未验证"每个浏览器会话各拿一个独立 MXC 沙箱"这个更强的隔离模型,因为
  jiuwenswarm 当前架构按项目而不是按会话复用沙箱。如果未来需要会话级而非
  项目级的沙箱隔离,那是 jiuwenswarm 自己 `isolation_custom_id` 策略的产品
  决策,不是 `MxcRuntime` 的限制。
- Warm pool 在 Windows 上的 failing-retry 行为(问题 7)只是被环境变量绕开,
  没有修代码;如果以后有人在不知道这个变量的情况下跑 jiuwenbox,会重新踩到
  日志爆炸 / 进程不稳定的问题。
- `write_file`/`read_file`/`list_dir`/`search_files` 仍未实现,真实对话里
  观察到 jiuwenswarm 会尝试通过 download 接口读取技能文件(`SKILL.md`)并
  收到 409——这是已知、已接受的 MVP 范围,不是本次才发现的新 gap。
- bash/PowerShell→`cmd` 的翻译只覆盖 `_resolve_execution_plan` 产出的两种
  已知形状;更复杂的真实工具调用(尤其是依赖 bash/PowerShell 专属语法的
  脚本)仍可能失败,且会以"cmd 语法错误"而不是"DLL 初始化失败"的方式失败——
  更容易诊断,但没有被这次修复变成"能跑"。
