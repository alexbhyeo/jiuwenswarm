---
id: F_20261120_1601_game-lab-chat-agent
title: 游戏工坊实验室助手——只调处理卡片的对话智能体
type: F
author: alexbhyeo
author_email: alexbhyeo@users.noreply.github.com
created: 2026-11-20 16:01
updated: 2026-11-20 22:40
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
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/GameLabCanvas.tsx
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/GameLabActionsContext.tsx
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/labFlow.ts
  - jiuwenswarm/channels/web/frontend/src/features/game/lab/gameLabChat.css
  - jiuwenswarm/channels/web/frontend/package.json
  - jiuwenswarm/channels/web/frontend/tests/gameLabFlow.test.mjs
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

## 追加：生成实验室流程图（21:24）

把导演模式剪辑助手的「生成实验室流程图」搬到游戏模式实验室助手：标题行最左
一颗按钮，一键把项目里跑过的处理卡片还原成整张流程图。

### 数据源：工程素材，不是聊天记录（与导演模式的有意偏差）

导演模式从**聊天记录**还原（`LabCanvas.buildFlowFromChat` 扫
`edit_chat_messages` 里助手生成过的 `image_asset_ids`）。游戏模式没有走这条，
改成从**项目素材**还原：

- 卡片是异步任务，产出素材自己带着 `params.source`（decompose / to_model /
  mesh_complete / mesh_decimate）与 `source_asset_id`，链路信息已经在数据里；
- 因此助手调过的、用户手点跑的、刷新前跑的，都能画出来——导演模式那套只能
  画出"助手记录里出现过"的素材；
- 一套事实只维护一处（素材），不必让聊天记录和画布各存一份链路。

代价：**画不出来"意图"**。导演模式能还原提示词、宽高比这些生成参数；游戏模式
的卡片没有自由提示词，能还原的就是"谁产出谁"这张图——这正好是流程图要表达的。

### 实现

- 新纯函数 `lab/labFlow.ts`：`buildLabFlow(project) -> {nodes, edges, cardCount}`。
  - 一次分解（同 `source_asset_id` + `decompose_batch_id`）合并成**一张卡片**，
    卡片到 N 个部件各一条边；
  - 深度用 `resolveDepths` 算：不依赖别人的排第 0 列，其余排在上游右一列，
    连线因此总是横向的（同列会被竖线穿刺、卡片互相压住——第一版就是这个
    缺陷，见下）；
  - 层内摆位两条规则（按用户实际使用反馈定的）：**同一张卡产出的素材成组
    铺开**（每行最多 4 个、多的换行，与素材库 4 个一行的分组摆法一致）；
    **互不依赖的节点竖着排成一列**。只做前一条会变成"每层都铺满 4 列"，22 个
    节点铺到 16 列、整图 4510px 宽，缩到能看全时字已经没了；只做后一条就是
    一列排到底的老问题。两条合起来实测 3010x1070，`fitView` 缩到 0.44 即可
    看全；
  - 端口名照抄节点组件（`image` / `in` / `out` / `parts` / `model` / `result`），
    写错不报错、只会让连线悬空；分解产出用 `model_part` 节点类型、其余 3D 产出
    用 `model`，与手动摆卡片时 `spawnModelResultNodes` 传的 type 一致（两者渲染
    同一个组件，但 class / testid 不同——写成一类会让回归断言认不出部件）；
  - 不碰 React、不碰画布状态，单测直接跑。
- `GameLabCanvas`：接 `children`（助手卡片要渲染在 `GameLabActionsProvider`
  内部才能拿到画布动作）并新增 `buildFlow` 到 `GameLabActionsContext`。
  整张画布**清空重画**（与导演模式同一取舍：重复点结果一致，不做增量合并）；
  `cardCount === 0` 时返回 `false` 且**不动画布**，避免一次空操作抹掉用户手连的线。
- 落盘交给画布原有的 800ms debounce 自动保存，不额外发 RPC。
- 重建后 `fitView` 一次：图有 7 列、比视口大得多，不取景用户只看到左边一块；
  取景时按助手卡片是否展开留出右侧空间（展开留 392px），否则最右一列被面板
  盖住。为此画布 `minZoom` 从默认 0.5 降到 0.3——0.5 会把 fit 截断在半途。
- `GamePage.tsx` 把 `<GameLabChatPanel>` 改成 `<GameLabCanvas>` 的 children。
- i18n `gameWorkshop.labChat.buildFlow`（「生成实验室流程图」/ "Build flow in Lab"）。

### 实施中发现的真实缺陷：部件与卡片挤在同一列

`resolveDepths` 第一版把依赖表混用了两个命名空间：素材节点的依赖写的是卡片
key，而查生产者的表 `producerByKey` 用素材 id 做 key——查不到，于是所有依赖
卡片的素材都被判成第 1 层，和卡片同列。实测 `demo` 工程：一列堆了 17 个节点，
画布高 4420px，`fitView` 被 `minZoom` 截断后只能看到一条竖线。

修复：依赖表统一用节点 key（`asset:<id>` / 卡片自己的 key），去掉
`producerByKey`。修正后同一工程 7 列、2500x2600。回归断言补在
`tests/gameLabFlow.test.mjs`：部件必须严格在卡片右边、卡片在源图右边、任意两个
节点位置不重叠——第一版实现过不了这三条。

### 验证

纯函数单测 `tests/gameLabFlow.test.mjs`（`npm run test:game-lab-flow`，8 条全通过）：
空工程返回空图、分解批次合并、多批次各一张卡、2D转3D→补全→重构列递增、
未知来源/pending/上游已删的容错、循环依赖不溢出、**每条边都落在节点真实端口
上**（端口名表抄自 `nodes/*.tsx`）、幂等。

前端端到端（真实前端 + 真实后端 + 真实工程数据，17 项断言全通过）：用
`demo`（15 个素材、四类卡片齐、原有 17 个节点）与一个只有生成素材的工程。

| 检查 | 结果 |
|---|---|
| 按钮存在 + 文案取自 i18n | `title=生成实验室流程图` |
| 重建后节点/连线数 | 22 / 20，与纯函数算出的期望一致 |
| 卡片构成 | 分解 1、2D转3D 2、网格补全 1、拓扑重构 3、图片 1、模型 14 |
| 分解卡片 | 「已产出 8 个部件」 |
| 连线路径 | 20 条均有有效 `d`，端口集合覆盖 `image/in/out/parts/model/result` |
| 卡片仍可用 | 2D转3D 卡片认到源图；网格卡片按 `source_asset_id` 认到上游（3 张有源、1 张历史无源） |
| 取景 | 最右节点 `right=1288` < 面板 `left=1305`，未被遮挡 |
| 摆位 | 分解卡 + 2 张 2D转3D 卡同列（x 相同、y 不同）；8 个部件铺成 4 列 × 2 行且整体在卡片右侧；网格卡片在部件右侧 |
| 幂等 | 再点一次仍 22 / 20 |
| 持久化 | 刷新后仍是 22 / 20（autosave 生效） |
| 空工程 | 点击前后都是 2 个节点，不误清画布 |
| `pageerror` | 0 |

历史无源卡片：`gasset_41fc8ad1`「拓扑重构模型」是早期跑的，那时还没记
`source_asset_id`。它仍然画出来（产出还在、能继续用），只是没有入边、卡片
上显示"请从左侧连一个 3D 模型"——这是有意的容错，不是缺陷。

## 遗留

- 助手只覆盖实验室四张卡片。若需要"先在图里生成再处理"，需把创作页的生成
  卡片也纳入工具表——那是产品范围问题，本次按需求明确排除。
- 一轮内的卡片调用是串行的（等一张跑完再下一张），没有并发调度；与剪辑助手
  单智能体路径一致。
- 前端没有做"取消本轮"入口；长任务只能等后端超时。
- 流程图按"层"分区、层内成组铺开，整图仍需缩到 0.44 左右才能看全；行序按
  素材顺序，没有做"上下游尽量对齐"的交叉最小化。
- 没有还原"意图"：导演模式能带回提示词/参数，游戏模式的卡片本来就没有自由
  提示词，所以只还原链路结构。
