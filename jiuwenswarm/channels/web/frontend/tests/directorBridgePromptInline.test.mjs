// 视频连视频（videoBridge）的默认过渡提示词必须落在卡片自带的「文字提示」里。
//
// 这张卡片的提示词是必填的（FLF2V 只有两张静态首尾帧，没有文字说明就不知道
// 该怎么从第一张"动"到第二张），所以落到画布时要预填一份默认文案。文案放在
// 哪里有两种写法，而这两种的用户体验完全不同：
//
//  * 摆一张独立的文本卡片 + 连一条线（旧行为）：画布上凭空多一张卡、多一条线，
//    用户得在两张卡片之间来回看才知道提示词是什么；
//  * 写进卡片自己的 `data.prompt`（与文生图/图生图等卡片同一个「文字提示」
//    弹窗）：卡片自己就是完整的，点开就能改。
//
// 这里钉死第二种，并防止旧写法被无意中改回来。断言分两层：
//  1. LabCanvas.handleAddProcessNode —— 建卡片时把默认文案写进 data.prompt，
//     且**不再**创建 text 节点；
//  2. ProcessNode —— 提示词取值是 data.prompt 优先于端口连线，必填校验也认它
//     （否则预填了文案、生成按钮却还是灰的）。
//
// 组件本身依赖 React Flow 运行时，纯单测跑不起来，所以这里读源码做契约断言；
// 真正的行为由浏览器端 E2E 验证（新增卡片后节点数为 1、弹窗里有完整文案）。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import { DEFAULT_VIDEO_BRIDGE_PROMPT } from '../node_modules/.cache/director-bridge-prompt/labTypes.js';

const src = (rel) => readFileSync(fileURLToPath(new URL(`../src/features/director/${rel}`, import.meta.url)), 'utf8');

const LAB_CANVAS = src('lab/LabCanvas.tsx');
const PROCESS_NODE = src('lab/nodes/ProcessNode.tsx');

/** 截出 handleAddProcessNode 的函数体，避免命中文件里别的 addNode 调用。 */
function addProcessNodeBody() {
  const start = LAB_CANVAS.indexOf('handleAddProcessNode = useCallback(');
  assert.ok(start > 0, '找不到 handleAddProcessNode');
  const end = LAB_CANVAS.indexOf('const setProcessNodeState', start);
  assert.ok(end > start, '找不到 handleAddProcessNode 的结尾');
  return LAB_CANVAS.slice(start, end);
}

test('默认过渡提示词写进卡片自带的 data.prompt', () => {
  const body = addProcessNodeBody();
  assert.match(
    body,
    /prompt:\s*kind === 'videoBridge' \? DEFAULT_VIDEO_BRIDGE_PROMPT : ''/,
    'handleAddProcessNode 没有把默认提示词写进 data.prompt——卡片会变成没有提示词的空卡片'
  );
  assert.match(LAB_CANVAS, /DEFAULT_VIDEO_BRIDGE_PROMPT/, 'labTypes 的默认文案没有被引用');
});

test('不再额外摆一张文本卡片（也不连那条线）', () => {
  const body = addProcessNodeBody();
  assert.ok(
    !/type:\s*'text'/.test(body),
    'handleAddProcessNode 里又出现了 type: \'text\' 节点——视频连视频会重新多出一张独立提示词卡片'
  );
  assert.ok(
    !/sourceHandle:\s*'text'/.test(body),
    'handleAddProcessNode 里又给 text 端口连了线——提示词应该只存在于卡片的「文字提示」弹窗里'
  );
});

test('默认文案自己是一份非空、可用的过渡提示词', () => {
  assert.ok(DEFAULT_VIDEO_BRIDGE_PROMPT.length > 500, '默认提示词太短，FLF2V 拿不到足够的方向信息');
  for (const phrase of [
    'seamless cinematic transition',
    'first frame',
    'final frame',
    'Output only the transition video.',
  ]) {
    assert.ok(
      DEFAULT_VIDEO_BRIDGE_PROMPT.includes(phrase),
      `默认提示词缺少关键约束: ${phrase}`
    );
  }
});

test('卡片的提示词取值：data.prompt 优先于文字提示端口', () => {
  assert.match(
    PROCESS_NODE,
    /const inlinePrompt = \(data\.prompt \?\? ''\)\.trim\(\)/,
    'ProcessNode 没有读 data.prompt'
  );
  assert.match(
    PROCESS_NODE,
    /const text = inlinePrompt \|\| connectedText/,
    '提示词取值不再是 data.prompt 优先——预填的默认文案会被忽略'
  );
});

test('必填校验认 data.prompt（否则预填了文案生成按钮还是灰的）', () => {
  assert.match(
    PROCESS_NODE,
    /\(!requiresPrompt \|\| text\.trim\(\)\.length > 0\)/,
    'requiresPrompt 的校验没有走统一的 text（含 data.prompt），预填文案不会让生成按钮变可用'
  );
});

test('生成时把卡片上的提示词原样带上', () => {
  assert.match(
    PROCESS_NODE,
    /actions\.generate\(id, \{ prompt: text,/,
    'generate 调用没有把卡片提示词传出去'
  );
});
