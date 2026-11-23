---
id: B_20261124_0000_director-video-aspect-follows-input
title: 导演模式图生视频与视频连视频忽略竖屏输入、成片固定为横屏
type: B
author: alexbhyeo
author_email: alexbhyeo@users.noreply.github.com
designs: []
design_note: 针对卡片缺省比例导致的成片方向错误做定点修复，未新增设计文档，沿用导演模式实验室现有生成契约。
modules:
  - jiuwenswarm/channels/web/frontend
files:
  - jiuwenswarm/channels/web/frontend/src/features/director/lab/labMediaRatio.ts
  - jiuwenswarm/channels/web/frontend/src/features/director/lab/nodes/ProcessNode.tsx
  - jiuwenswarm/channels/web/frontend/src/features/director/lab/labTypes.ts
  - jiuwenswarm/channels/web/frontend/src/features/director/lab/LabCanvas.tsx
  - jiuwenswarm/channels/web/frontend/src/i18n/locales/zh.json
  - jiuwenswarm/channels/web/frontend/src/i18n/locales/en.json
  - jiuwenswarm/channels/web/frontend/tests/labMediaRatio.test.mjs
---

# 导演模式图生视频与视频连视频忽略竖屏输入、成片固定为横屏

## 现象与影响

导演模式实验室里的图生视频、视频连视频卡片，即使提示词描述的构图和输入素材都是 9:16 竖屏，生成的成片仍是 16:9 横屏，竖屏画面被裁掉两侧主体。用户无法通过卡片上的任何操作得到与素材同方向的成片，只能事后二次裁剪。

## 根因

两个层面叠加：

1. 服务商按帧决定成片几何。OpenRouter `/videos`（协议 bytedance，模型 seedance-2.0-fast）在有首帧/参考帧时，成片宽高跟着帧走，请求里的 `aspect_ratio` 只用于配合。后端 `video_gen_tools` 正是按请求比例居中裁切本地帧，所以请求比例与帧比例不一致时，被裁的是帧——横屏请求配竖屏帧，成片就是横屏。
2. 卡片比例永远停在默认值。`ProcessNodeData.aspectRatio` 建于建卡时的 `'16:9'`，之后只有用户手动点药丸才会变；卡片从输入素材推断比例的能力完全缺失。于是竖屏素材必然走到"横屏请求 + 竖屏帧"的分支。

实测确认后端无问题：竖屏帧配 `aspect_ratio=9:16` 得到 496×864 竖屏成片；同一帧配 16:9 得到横屏。

## 修复与决策

- 新增 `labMediaRatio.ts`：`SUPPORTED_ASPECT_RATIOS`（与卡片药丸同源）、`nearestAspectRatio`（按宽高比取对数距离归档，横竖对称）、`measureImageAspectRatio` / `measureVideoAspectRatio`（DOM 侧量元数据，8 秒超时兜底）。纯函数部分可单测。
- `ProcessNode` 对 image2video / imageAudio2video / video2video / videoBridge 增加"跟随输入"：量第一个可用输入（图生视频优先首帧图，视频类看片段一）的宽高比并写回 `aspectRatio`。
- 新增 `ProcessNodeData.aspectRatioAuto`（缺省视为 true，旧画布行为不变）。用户点任一比例药丸即置 false 并锁定手动值；点「自动」药丸恢复跟随。只有"输入能定比例"的卡片才显示「自动」药丸，文生图 / 文生视频不摆无效控件。
- 生成参数行在跟随时显示 `自动 · 9:16`，让用户直接看出当前比例不是自己选的。
- `LabCanvas` 从历史重建卡片时写 `aspectRatioAuto: false`：重建用的 `aspect_ratio` 是当初真正发出去的值（可能来自跟随），按手动值恢复，避免连上素材后被重新量成别的档位，导致重建结果与历史不一致。
- 输入路径为空时不发测量请求（`rawFileUrl('')` 会变成一次 `/file-api/raw-file?path=` 的 400）。

未改后端与会话契约：`aspect_ratio` 仍按卡片当前值下发，只是这个值不再固定是 16:9。未强行让后端忽略请求比例改按帧比例：请求比例与帧比例不一致时裁帧是既有产物裁剪语义，改动会牵动其他调用方。

## 验证

- `npm run test:lab-media-ratio`：4 项通过（归档对称、越界返回 null、非正尺寸返回 null）。
- `npx tsc --noEmit`、`npm run build` 通过。
- Playwright 真机（真 provider）端到端：文生图 9:16 出竖屏素材 → 图生视频卡片显示 `自动 · 9:16` → 成片 720×1280；手动点 16:9 后参数行变 `16:9`（未被测量覆盖），点回「自动」恢复 `自动 · 9:16`；视频连视频接两段竖屏片段显示 `自动 · 9:16` → 成片 720×1280。

## 遗留

- 剪辑助手侧 `_execute_generate_shot_video` 仍硬编码 `aspect_ratio="16:9"`，属于对话生成分镜的另一条链路，本次未动。
- 远程 / data-URI 帧不做本地裁切（后端仅裁本地帧），跟随逻辑只在前端选比例，方向正确但远程帧仍受服务商几何规则约束。
