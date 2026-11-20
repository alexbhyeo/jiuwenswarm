---
id: F_20261120_1601_game-lab-chat-agent
title: 游戏工坊实验室助手——只调处理卡片的对话智能体
type: F
author: alexbhyeo
author_email: alexbhyeo@users.noreply.github.com
created: 2026-11-20 16:01
updated: 2026-11-20 16:01
designs:
  - D-20261116-1047-game-workshop-lab
modules:
  - jiuwenswarm/server/runtime/game
  - jiuwenswarm/server/runtime/agent_adapter
  - jiuwenswarm/server/runtime/director
  - jiuwenswarm/channels/web/frontend/src/features/game
files:
  - jiuwenswarm/server/runtime/game/game_lab_chat.py
  - jiuwenswarm/server/runtime/game/game_manager.py
  - jiuwenswarm/server/runtime/game/game_store.py
  - jiuwenswarm/common/schema/message.py
  - jiuwenswarm/server/runtime/agent_adapter/interface.py
  - jiuwenswarm/server/runtime/director/gateway_forwarding.py
  - jiuwenswarm/channels/web/frontend/src/features/game/gameApi.ts
  - jiuwenswarm/channels/web/frontend/src/features/game/GamePage.tsx
  - jiuwenswarm/channels/web/frontend/src/features/game/gamePage.css
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/GameLabChatPanel.tsx
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/gameLabChat.css
  - jiuwenswarm/channels/web/frontend/src/i18n/locales/zh.json
  - jiuwenswarm/channels/web/frontend/src/i18n/locales/en.json
  - tests/unit_tests/server/runtime/test_game_lab_chat.py
---

# 游戏工坊实验室助手——只调处理卡片的对话智能体

## 需求与边界

在游戏工坊「实验室」里放一个对话助手，形态与导演模式剪辑助手一致（一轮对话里
模型自己走工具循环），但**只能调用实验室的四张处理卡片**：

| 工具 | 对应卡片 | 输入 |
|---|---|---|
| `decompose_asset` | 素材分解 | 角色/世界图片 |
| `image_to_model` | 2D 转 3D | 角色/世界图片 |
| `mesh_complete` | 网格补全 | 3D 模型/部件 |
| `mesh_decimate` | 拓扑重构 | 3D 模型/部件 |

明确不在权限内：生成角色/世界/视频（属「创作」页）、改工程代码、导演模式的
`generate_design_image` / `generate_shot_video` / `generate_video_bridge`。

## 实现

### 后端

- 新模块 `jiuwenswarm/server/runtime/game/game_lab_chat.py`：
  - `LAB_TOOLS_SCHEMA` 只有上述四项，`LAB_TOOL_NAMES` 供测试钉住边界；
  - 项目已有素材走 **system 上下文**（`build_asset_context`）而不是第五个"读素材"
    工具——工具表就是权限表，多一个读接口会把边界糊掉；空项目时明确告诉模型
    让用户先去「创作」页；
  - `GameLabChat.run_turn` 复用 `call_edit_chat_completion`：写用户消息 → 工具循环
    （上限 6 轮）→ 写助手消息，`applied` 记下真正成功的卡片产出；
  - 每张卡片 = 提交 + 轮询到终态（单卡片最多等 600 秒），产出名称回填进
    `role=tool` 消息，下一轮才可能引用新素材；
  - 卡片报错转成喂回模型的文本（不中断整轮），非卡片异常照抛。
- `GameProject.lab_chat_messages`：对话随项目持久化，打开项目即恢复历史，
  与导演模式一致不单开"取历史"RPC。
- 新 RPC `game.lab_chat.send`，按既有链路注册：`message.py` 的 ReqMethod →
  `interface.py` 的 `_GAME_ROUTES` → `gateway_forwarding.py` 的转发白名单。
  未配置剪辑对话时返回 `NOT_CONFIGURED`（复用 `edit_chat_enabled/configured`）。

### 前端

- `lab/GameLabChatPanel.tsx`：**视觉与剪辑助手一致**——画布右上悬浮卡片
  （360px、12px 圆角、投影、白底），标题行（标题 + 收起按钮）、消息流（用户
  气泡走强调色、助手气泡走浅灰底并渲染 Markdown）、间距一致的 chip 行、88px
  圆角输入框、34px 圆形发送按钮；收起后只剩右下角胶囊。
- 设计 token 在 `gamePage.css` 的 `.game-page` 作用域内定义 `--g-*`，取值与
  导演模式 `.director-page` 的 `--d-*` 同一套（`#191919` / `#777777` /
  `#f5f7f9` / `#e4e4e7` / `#2563eb` / `rgb(0,102,255)` 等），不跨 feature 复用
  class 名，避免两边样式互相串。
- 实验室 tab 不再左右分栏：画布 `position:absolute; inset:0` 铺满，助手以
  `.game-lab-chat-overlay` 覆盖在右上角（与导演模式 `.lab-edit-chat-overlay`
  同一处理，含"收起后容器只包住胶囊、不再挡住画布点击"的修正）。
- chip 行展示能力边界（「仅处理卡片」），不复用剪辑助手的技能选择器——游戏
  模式这条链路没有技能概念。
- `gameApi.labChatSend` 超时 1_200_000ms：一轮可能连跑多张卡片，默认 15s 不够。

## 实施中发现的真实缺陷

**拓扑重构的轮询少传 `job_id`**。`handle_game_assets_mesh_decimate_check_status`
要求 `placeholder_asset_id` + `job_id` 两个参数（网格补全的从占位素材里读
job_id，两者契约不同），而第一版按网格补全的形状只传了前者，实测一轮真实
调用直接失败：

```
INVALID_PARAMS：缺少 placeholder_asset_id / job_id
```

修复：`job_id` 有就一起传（网格补全忽略多余参数），并补
`test_mesh_decimate_poll_carries_job_id` 记录 check 参数来钉住。

**失败卡片留下 pending 占位**。参数不齐时 check_status 在删占位之前就抛错，
素材库里留下「拓扑重构中…（来自 sword）」永不终结（实测两次重试留下两条）。
修复：三个卡片路径在轮询异常时 `_discard_placeholder` 清掉自己建的占位，并补
`test_card_failure_cleans_up_placeholder`。

## 验证

单测 `tests/unit_tests/server/runtime/test_game_lab_chat.py`（12 条，全通过）：
工具表恰为四张卡片且不含导演模式工具、空项目/有素材两种上下文、按名解析（含
`@`、前缀、歧义返回 None）、卡片执行与消息落盘、非卡片工具被拒、未知素材回
上下文、轮询超时判失败、未配置报 `NOT_CONFIGURED`、decimate 轮询带 job_id、
失败清理占位。

真实链路（本机服务 + 浏览器 /ws）：

| 场景 | 结果 |
|---|---|
| 「帮我直接画一张新的角色立绘」 | 助手明确说明只能调四张卡片，并指向「创作」页；列出项目现有素材；消息落盘 2 条 |
| 「把 sword 这个部件做一次拓扑重构」 | 真实调用 Tripo3D 成功，`applied = 拓扑重构完成，产出模型：sword · 拓扑重构` |
| 实验室 tab UI | 面板可见、输入框与发送按钮就位、历史 6 条消息恢复、`pageerror 0` |

前端端到端（真实点击/输入，25 项断言全通过）：

| 检查 | 结果 |
|---|---|
| 空项目空态提示 + 次级灰文字色 | `rgb(119,119,119)`，居中 |
| 卡片外观 | 白底 `rgb(255,255,255)`、圆角 `12px`、有投影 |
| 标题行 | 「实验室助手」+ 1 个收起按钮 |
| chip | 「仅处理卡片」，描边 `rgb(37,99,235)` |
| 输入框 / 发送按钮 | 高 88px；发送按钮 `border-radius: 9999px`；空输入时禁用 |
| UI 内真实发送 | 出现思考中提示 → 收到回复（空项目上下文生效）→ 用户气泡为强调色 |
| 收起 / 展开 | 收起只剩右下角胶囊，展开恢复 |
| 刷新页面 | 历史消息仍在；项目里持久化 user + assistant 两条 |

## 遗留

- 助手只覆盖实验室四张卡片。若需要"先在图里生成再处理"，需把创作页的生成
  卡片也纳入工具表——那是产品范围问题，本次按需求明确排除。
- 一轮内的卡片调用是串行的（等一张跑完再下一张），没有并发调度；与剪辑助手
  单智能体路径一致。
- 前端没有做"取消本轮"入口；长任务只能等后端超时。
