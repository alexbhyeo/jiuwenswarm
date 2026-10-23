// 卡片"详情"的显示开关：全局默认 + 逐卡片覆盖。
//
// 详情＝两块纯展示的说明文字：「模型 / 生成参数」那一段，以及输入端口那一排
// 名字（文字提示 / 图片参考 / 首帧图片……）连同 ✓。
//
// 最重要的一条不变量：**收起详情不能把端口弄丢**。每个 <Handle> 都嵌在对应
// 的输入行里，若按"把这一段藏起来"的直觉去 display:none 整行，卡片就再也连
// 不上线了。所以收起时只压扁行、隐藏非 handle 的子元素，handle 必须原样留在
// DOM 里（只是退化成左边缘一排裸圆点，靠 title tooltip 辨认）。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import {
  DEFAULT_SHOW_CARD_DETAIL,
  getShowCardDetail,
  resolveDetailVisible,
  setShowCardDetail,
  stripDetailOverrides,
  subscribeShowCardDetail,
} from '../node_modules/.cache/director-card-detail/cardDetailPrefs.js';

const src = (rel) => readFileSync(fileURLToPath(new URL(`../src/features/director/${rel}`, import.meta.url)), 'utf8');
const PROCESS_NODE = src('lab/nodes/ProcessNode.tsx');
const LAB_CANVAS = src('lab/LabCanvas.tsx');
const CSS = src('styles/director.css');

const portsBlock = () => {
  const start = PROCESS_NODE.indexOf('<div className={`lab-node-ports');
  assert.ok(start > 0, '找不到端口区');
  return PROCESS_NODE.slice(start, PROCESS_NODE.indexOf('{detailVisible && (', start));
};

test('默认展示详情', () => {
  assert.equal(DEFAULT_SHOW_CARD_DETAIL, true);
  assert.equal(getShowCardDetail(), true);
});

test('单卡没设置时跟随全局（两个方向）', () => {
  assert.equal(resolveDetailVisible(undefined, true), true);
  assert.equal(resolveDetailVisible(undefined, false), false);
});

test('单卡的显式设置覆盖全局（两个方向）', () => {
  assert.equal(resolveDetailVisible(true, false), true, '全局收起时，单独展开的卡片要显示详情');
  assert.equal(resolveDetailVisible(false, true), false, '全局展开时，单独收起的卡片要隐藏详情');
});

test('改全局会通知订阅者（所有"跟随全局"的卡片一起变）', () => {
  let calls = 0;
  const unsubscribe = subscribeShowCardDetail(() => {
    calls += 1;
  });
  setShowCardDetail(false);
  assert.equal(getShowCardDetail(), false);
  assert.equal(calls, 1, '订阅者没有被通知，画布上的卡片不会跟着变');
  setShowCardDetail(true);
  assert.equal(calls, 2);
  unsubscribe();
  setShowCardDetail(false);
  assert.equal(calls, 2, '退订之后不该再收到通知');
  setShowCardDetail(true);
});

test('参数区只在显示详情时渲染', () => {
  assert.match(PROCESS_NODE, /const detailVisible = resolveDetailVisible\(data\.detailVisible, showCardDetail\)/);
  assert.match(PROCESS_NODE, /\{detailVisible && \(\s*<div className="lab-node-params"/);
});

test('收起后所有输入端口叠成左边缘的同一个点', () => {
  assert.match(
    CSS,
    /\.lab-node-ports--compact \.react-flow__handle-left \{\s*position: absolute !important;\s*top: 50% !important;\s*left: 0 !important;\s*transform: translate\(-50%, -50%\) !important;/,
    '输入端口没有被绝对定位到同一点——收起后还会看到一排点'
  );
  assert.match(
    CSS,
    /\.lab-node-ports--compact \.lab-node-input-row \{[\s\S]{0,120}position: static/,
    '行还是定位祖先，handle 会各自停在自己那一行上、散成多个点'
  );
  assert.match(CSS, /\.lab-node-ports--compact \{\s*position: relative;\s*gap: 0;/, '端口区没有成为统一的定位参照');
});

test('新建连线默认落到主端口（叠在最上的那个），其它端口需展开详情', () => {
  assert.match(
    PROCESS_NODE,
    /const primaryPortId = maxImages >= 1 \? 'image1' : maxVideos >= 1 \? 'video1' : maxAudios >= 1 \? 'audio1' : 'text'/,
    '没有选定主端口——叠在同一个点时新建连线会落到不可预期的端口上'
  );
  assert.match(PROCESS_NODE, /detailVisible \|\| portId !== primaryPortId \? undefined : 'lab-node-handle-primary'/, '主端口没有单独标记');
  assert.match(CSS, /\.lab-node-ports--compact \.lab-node-handle-primary \{\s*z-index: 3/, '主端口没有置顶');
  const tagged = (PROCESS_NODE.match(/className=\{portClass\('(text|image1|image2|video1|video2|audio1)'\)\}/g) ?? []).length;
  assert.equal(tagged, 6, '有端口没有参与主端口标记');
});

test('收起详情时端口退化成紧凑条（卡片与端口区都要有 compact 标记）', () => {
  assert.match(
    PROCESS_NODE,
    /\$\{detailVisible \? '' : ' lab-node--process-compact'\}/,
    '卡片根节点没有 compact 标记——输出连接点的位置就不会跟着上移'
  );
  assert.match(
    PROCESS_NODE,
    /\$\{detailVisible \? '' : ' lab-node-ports--compact'\}/,
    '端口区没有 compact 标记——那排标签不会被收起'
  );
  assert.match(
    CSS,
    /\.lab-node-ports--compact \.lab-node-input-row > \*:not\(\.react-flow__handle\)/,
    '收起时没有只隐藏非 handle 的子元素'
  );
  // 端口名是直接写在行里的文本节点，不是元素：只靠上面那条选择器收不起来，
  // 文字会从 0 高的行里溢出来继续显示。必须有把裸文本也归零的规则。
  assert.match(
    CSS,
    /\.lab-node-ports--compact \.lab-node-input-row \{[\s\S]{0,520}font-size: 0;/,
    '收起后行里的裸文本仍会显示（"文字提示 / 图片参考"挂在卡片上）'
  );
});

test('收起时绝不删掉 <Handle>：六个端口一个都不能少', () => {
  const block = portsBlock();
  for (const id of ['text', 'image1', 'image2', 'video1', 'video2', 'audio1']) {
    assert.ok(block.includes(`id="${id}"`), `端口 ${id} 的 <Handle> 不见了——这张卡片就再也连不上了`);
  }
  const handleCount = (block.match(/<Handle/g) ?? []).length;
  assert.ok(handleCount >= 6, `端口区里的 <Handle> 只剩 ${handleCount} 个`);
});

test('裸圆点有 tooltip 说明是哪一路端口', () => {
  const block = portsBlock();
  const titled = (block.match(/<Handle[^>]*title=\{t\(/g) ?? []).length;
  assert.equal(titled, 6, '有端口圆点没有 title，收起后用户无从分辨');
});

test('输出连接点的位置跟随端口区高度（不再写死 79px）', () => {
  assert.match(CSS, /--lab-process-ports-h:\s*78px/, '缺少默认端口区高度变量');
  assert.match(CSS, /\.lab-node--process-compact\s*\{[\s\S]{0,140}--lab-process-ports-h:\s*26px/, 'compact 时没有收窄端口区高度');
  assert.match(
    CSS,
    /\.lab-node--process > \.react-flow__handle-right \{\s*top: calc\(var\(--lab-process-header-h, 40px\) \+ var\(--lab-process-ports-h\) \/ 2\) !important/,
    '输出点仍用写死的 top——收起详情后会和左侧端口错开'
  );
  assert.ok(!/react-flow__handle-right \{\s*top: 79px/.test(CSS), '还留着写死的 79px');
});

test('逐卡开关写回节点数据，且就在删除图标旁边', () => {
  assert.match(PROCESS_NODE, /data-testid="lab-process-node-toggle-params"/);
  assert.match(PROCESS_NODE, /actions\.patchProcessNode\(id, \{ detailVisible: !detailVisible \}\)/);
  const headerStart = PROCESS_NODE.indexOf('<div className="lab-node-label">');
  const header = PROCESS_NODE.slice(headerStart, PROCESS_NODE.indexOf('<div className={`lab-node-ports', headerStart));
  assert.ok(headerStart > 0, '找不到标题栏');
  assert.match(header, /<span className="lab-node-label-actions">/, '标题栏没有右侧操作组');
  const toggleAt = header.indexOf('lab-process-node-toggle-params');
  const deleteAt = header.indexOf("t('director.delete.action')");
  assert.ok(toggleAt > 0 && deleteAt > toggleAt, '开关不在删除图标的左边紧邻位置');
  assert.match(CSS, /\.lab-node-label-actions \{[\s\S]{0,140}flex: 0 0 auto/, '右侧操作组缺少样式');
});

test('工具栏全局开关：切默认值的同时清掉逐卡覆盖', () => {
  assert.match(LAB_CANVAS, /data-testid="lab-toggle-card-params"/);
  assert.match(LAB_CANVAS, /onClick=\{handleToggleAllCardDetail\}/, '全局开关没有走统一处理');
  assert.match(
    LAB_CANVAS,
    /const handleToggleAllCardDetail = useCallback\(\(\) => \{\s*setShowCardDetail\(!getShowCardDetail\(\)\);\s*setNodes\(\(nds\) => stripDetailOverrides\(nds\)\);/,
    '全局开关只改了默认值、没清逐卡覆盖——单独点过开关的卡片会不跟着变'
  );
});

test('stripDetailOverrides：清掉逐卡覆盖，只动处理卡片', () => {
  const nodes = [
    { id: 'p1', type: 'process', data: { detailVisible: false, kind: 'text2image' } },
    { id: 'p2', type: 'process', data: { detailVisible: true, kind: 'text2video' } },
    { id: 'p3', type: 'process', data: { kind: 'text2audio' } },
    { id: 'r1', type: 'image', data: { detailVisible: false, filePath: '/a.png' } },
  ];
  const out = stripDetailOverrides(nodes);
  assert.equal(out[0].data.detailVisible, undefined, '逐卡覆盖没有被清掉');
  assert.equal(out[1].data.detailVisible, undefined);
  assert.equal(out[0].data.kind, 'text2image', '清覆盖时误删了其它字段');
  assert.equal(out[2].data.detailVisible, undefined);
  assert.equal(out[3].data.detailVisible, false, '非处理卡片不该被改动');
});

test('stripDetailOverrides：没有覆盖时原样返回（不触发无意义的重绘）', () => {
  const nodes = [{ id: 'p1', type: 'process', data: { kind: 'text2image' } }];
  assert.equal(stripDetailOverrides(nodes), nodes, '没有可清的东西却返回了新数组');
});

test('清空重建后逐卡覆盖仍然保留', () => {
  assert.match(
    LAB_CANVAS,
    /preservedHistoryByAssetId\.set\(data\.originAssetId, \{[\s\S]{0,220}detailVisible: data\.detailVisible,/
  );
  assert.match(LAB_CANVAS, /detailVisible: preserved\?\.detailVisible,/);
});

test('两种语言的文案都补齐了', () => {
  for (const loc of ['zh', 'en']) {
    const dict = JSON.parse(readFileSync(fileURLToPath(new URL(`../src/i18n/locales/${loc}.json`, import.meta.url)), 'utf8'));
    for (const key of ['cardDetailShow', 'cardDetailHide', 'cardDetailShowAll', 'cardDetailHideAll']) {
      assert.ok(dict.director.lab[key], `${loc} 缺少 ${key}`);
    }
  }
});
