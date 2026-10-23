// 点「生成实验室流程图」（buildFlowFromChat）重建画布时，下游卡片如果引用的是
// 上游卡片"重新生成"出来的素材，必须连回那张上游处理卡片、并让上游卡片显示
// 被引用的那一条——而不是在旁边凭空多摆一张一模一样的参考卡片。
//
// 卡片本身依赖 React Flow 运行时，纯单测跑不起来，所以这里分两层：
//  1. 行为层：rebuildSwitch.pickSwitchTargets 直接跑真实实现（冲突票数、
//     平票、空输入）；
//  2. 契约层：读 LabCanvas 源码，钉死"登记整条 history""路径先到先得"
//     "三条复用路径都要请求切换"这几个不写就退化的点。
//
// 真正的画布行为由浏览器端 E2E 验证（christmas 工程重建后 26 → 21 个节点、
// 消除 4 张重复卡片）。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import { pickSwitchTargets } from '../node_modules/.cache/director-rebuild-switch/rebuildSwitch.js';

const src = (rel) => readFileSync(fileURLToPath(new URL(`../src/features/director/${rel}`, import.meta.url)), 'utf8');

const LAB_CANVAS = src('lab/LabCanvas.tsx');

/** 截出 buildFlowFromChat 的函数体，避免命中文件里别处的同名写法。 */
function buildFlowBody() {
  const start = LAB_CANVAS.indexOf('const buildFlowFromChat = useCallback(');
  assert.ok(start > 0, '找不到 buildFlowFromChat');
  const end = LAB_CANVAS.indexOf('}, [selectedProjectId, setNodes, addNode, setEdges]);', start);
  assert.ok(end > start, '找不到 buildFlowFromChat 的结尾');
  return LAB_CANVAS.slice(start, end);
}

test('pickSwitchTargets: 票数最多的历史条目胜出', () => {
  const votes = new Map([
    ['process_a', new Map([[0, 2], [5, 4]])],
  ]);
  assert.equal(pickSwitchTargets(votes).get('process_a'), 5);
});

test('pickSwitchTargets: 平票时取更晚生成的那一条（与摆放顺序无关）', () => {
  const ascending = new Map([['p', new Map([[1, 3], [4, 3]])]]);
  const descending = new Map([['p', new Map([[4, 3], [1, 3]])]]);
  assert.equal(pickSwitchTargets(ascending).get('p'), 4);
  assert.equal(pickSwitchTargets(descending).get('p'), 4, '结果随 Map 插入顺序变化说明选法不确定');
});

test('pickSwitchTargets: 多条记录 / 空输入', () => {
  const votes = new Map([
    ['a', new Map([[0, 1], [2, 1]])],
    ['b', new Map([[7, 9]])],
    ['c', new Map()],
  ]);
  const targets = pickSwitchTargets(votes);
  assert.equal(targets.get('a'), 2);
  assert.equal(targets.get('b'), 7);
  assert.equal(targets.has('c'), false, '没有任何引用的卡片不该被切换');
  assert.equal(pickSwitchTargets(new Map()).size, 0);
});

test('登记整条 history（只登记原始素材会退化成重复卡片）', () => {
  const body = buildFlowBody();
  assert.match(
    body,
    /for \(const \[entryIndex, histEntry\] of history\.entries\(\)\)/,
    '没有遍历整条 history —— 重新生成出来的素材查不到归属，下游会多摆一张重复卡片'
  );
  assert.match(
    body,
    /outputNodeIdByAsset\.set\(histEntry\.assetId, processNodeId\)/,
    '素材 id → 卡片 的登记没有覆盖历史条目'
  );
  assert.match(
    body,
    /entryPointerByAssetId\.set\(histEntry\.assetId, \{ nodeId: processNodeId, index: entryIndex \}\)/,
    '没有记录"这条素材是哪张卡片的第几条历史"，下游无法把上游切过去'
  );
});

test('文件路径的登记先到先得（不抢别人那张图的主人）', () => {
  const body = buildFlowBody();
  for (const map of ['imageNodeIdByPath', 'videoNodeIdByPath', 'audioNodeIdByPath']) {
    assert.match(
      body,
      new RegExp(`!${map}\\.has\\(histEntry\\.filePath\\)`),
      `${map} 的登记没有先到先得判断——被"替换图片"借来的路径会抢走真正的主人`
    );
  }
});

test('三条复用路径（图片/视频/音频）都要请求切换上游卡片', () => {
  const body = buildFlowBody();
  const fnBody = (name, endMarker) => {
    const start = body.indexOf(`const ${name} = (`);
    assert.ok(start > 0, `找不到 ${name}`);
    return body.slice(start, body.indexOf(endMarker, start));
  };
  for (const name of ['ensureImageRefNode', 'ensureVideoNode', 'ensureAudioNode']) {
    const fn = fnBody(name, '};');
    const calls = fn.match(/requestEntrySwitch\(filePath\)/g) ?? [];
    assert.ok(
      calls.length >= 1,
      `${name} 复用了已有卡片却没请求切换 —— 上游卡片会停留在原始那一条，画面和下游引用对不上`
    );
  }
});

test('切换在循环结束后一次性应用，且同步三个镜像字段', () => {
  const body = buildFlowBody();
  assert.match(body, /pickSwitchTargets\(pendingSwitchVotes\)/, '没有走统一的选法');
  assert.match(
    body,
    /activeHistoryIndex: target, assetId: entry\.assetId, filePath: entry\.filePath, name: entry\.name/,
    '切换时没有同步 assetId / filePath / name 镜像 —— 下游端口读到的还是旧素材'
  );
  const loopEnd = body.indexOf('pendingSwitchVotes.size > 0');
  const loopStart = body.indexOf('for (const assetId of newAssetIds)');
  assert.ok(loopStart > 0 && loopEnd > loopStart, '切换必须在摆放循环之后统一应用');
});
