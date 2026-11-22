---
name: godot-mcp-toolkit-guide
description: "Godot MCP Toolkit 使用指引：在 Godot 4.2+ 编辑器里创建/编辑场景与节点、读写 GDScript、试玩与运行时检查的工具选择策略与安全规则。当用户要求在 Godot 工程里搭建场景、改脚本、跑试玩、截图或排查运行时报错时使用。"
description_zh: "Godot MCP Toolkit 使用指引：编辑器场景/节点操作、脚本读写、试玩与运行时检查的调用策略与确认规则"
description_en: "Godot MCP Toolkit guide: editor scene/node operations, script authoring, playtest and runtime inspection rules"
version: 1.0.0
allowed-tools: Read,Write,Bash
---

# Godot MCP Toolkit 使用指引

本技能配合 `godot-mcp-toolkit` MCP server 使用。它由两部分组成，缺一不可：

1. **编辑器插件**（Godot 侧）：在目标工程里安装并启用 "Godot MCP Toolkit"，编辑器输出日志会出现
   `[MCPServer] listening on 127.0.0.1:6550`（端口在 6550–6560 之间，以插件面板显示为准）。
2. **npm 桥接**（本连接器）：`npx -y @npgamedev/godot-mcp-server`，需要 Node.js 22+；它自动探测
   上述端口并连上编辑器。

两者都在本机运行，不上传工程数据。工具在首次连接后才会出现，工具数量随 Godot 版本不同（4.2 起逐版本增加）。

## 1. 连接前先确认环境（不要跳过）

- 先调用一个**只读**工具（例如读取项目设置 `project_get_settings`，或读取当前场景树）确认链路通。
- 连接失败时按顺序排查并如实告诉用户，不要反复重试同一调用：
  - 编辑器没开，或目标工程不是当前打开的工程；
  - 插件未启用（Project Settings → Plugins）；
  - Node.js 低于 22，或 `npx` 不在 PATH；
  - 端口被占用（插件面板会显示实际端口）。
- 用户还没装插件时，给出官方入口：[Godot Asset Store](https://store.godotengine.org/asset/npgamedev/godot-mcp-toolkit/) 或 [GitHub Releases](https://github.com/NPGameDev/godot-mcp-toolkit)（解压到工程 `addons/`），然后让用户启用插件后再连接。

## 2. 工具面：先发现，再调用

- 工具按"一个工具 + action 参数"组织，核心工具常驻，扩展工具按需加载。需要更多能力时调用 `discover_tools` 按关键词加载分组，不要凭记忆瞎猜工具名。
- 不确定工具是否存在时，先 `discover_tools` 查询；仍没有就说明该 Godot 版本不支持或需要扩展 API。
- 常驻核心能力大致覆盖：场景打开/保存、节点增删改与属性读写、脚本读写与校验、项目设置、控制台日志、试玩启停、截图。
- 每次只做用户要求的那一步；不确定就先问，不要顺手把整个场景重写一遍。

## 3. 编辑器通道 vs 运行时通道（最容易搞错的地方）

- **编辑器通道**改的是正在编辑的场景，会写进 `.tscn` 文件。属性改动用编辑器侧的设置工具。
- **运行时通道**只影响试玩中的游戏实例：读实时节点位置/速度、模拟输入、读游戏日志、执行临时代码。
- 试玩没启动时调用运行时工具一定失败；先启动试玩，确认游戏起来后再读。
- 需要"看效果"时：改完场景 → 启动试玩 → 截图/读运行时状态 → 停止试玩，按这个顺序做，别反过来。

## 4. 写操作必须「预览 → 确认」（强制）

以下操作执行前，先向用户展示将要发生的改动并获得明确确认：

- 删除/移动/重命名节点、删除文件或目录
- 覆盖或整体重写已有脚本（先展示 diff 或改动摘要）
- 修改项目设置、输入映射、autoload
- 批量操作（批量设置属性、批量改 Tilemap）

节点路径统一用相对编辑场景根的写法（`.` 是根，`./Player` 是子节点）；跨场景操作前先确认当前编辑的是哪个场景。

## 5. 脚本与资源

- 改脚本优先用"读 → 局部替换"，避免整文件重写；改完调用校验工具，语法/类型错误要在回复里点明。
- 资源路径一律 `res://`；外部资源用 `{type:'Resource', path:'res://...'}` 形式引用，不要拼绝对文件路径。
- 生成或替换贴图/音效时，先用占位资源验证流程，再让用户提供正式素材。

## 6. 失败与回读

- 任何工具返回错误，先读错误正文再决定下一步；不要静默重试。
- 运行时报错用编辑器控制台工具读取（支持级别过滤与分页），定位到具体脚本/行号再改。
- 大型操作（试玩、导入、构建）可能超过默认超时，如实说明"仍在进行"，不要假设已经成功。
