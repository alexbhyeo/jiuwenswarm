// 视频卡片也能"替换"生成出来的片子。
//
// 之前换图按钮只对图片产物开放（`PROCESS_KIND_MODE[kind] === 'image'`），视频
// 卡片只能靠"重新生成"换内容，用户没法把素材库里已经满意的一段片子换进来。
// 而下游卡片（视频连视频 / 视频生视频 / 视频生音频）拿的正是上游卡片**当前
// 展示的那一条**产物，所以换掉这一条要能一路传下去。
//
// 分两层验证：
//  1. 行为层：filterSwappableAssets 直接跑真实实现（候选类型、排除当前项、
//     只看 ready 且有文件、按更新时间倒序）；
//  2. 契约层：读源码钉死视频卡片确实渲染了替换入口、上传按 video 分类、
//     以及换的时候同时改 history[activeHistoryIndex] 和扁平镜像字段。
//
// 真正的端到端行为由浏览器 E2E 验证（换完之后视频连视频片段一显示的就是换
// 上去的那一段）。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import { filterSwappableAssets, swappedEntryName } from '../node_modules/.cache/director-video-swap/swapAssets.js';

const src = (rel) => readFileSync(fileURLToPath(new URL(`../src/features/director/${rel}`, import.meta.url)), 'utf8');
const PROCESS_NODE = src('lab/nodes/ProcessNode.tsx');
const LAB_CANVAS = src('lab/LabCanvas.tsx');

const asset = (id, type, extra = {}) => ({
  asset_id: id,
  type,
  status: 'ready',
  file_path: `/p/${id}.${type === 'video' ? 'mp4' : 'png'}`,
  updated_at: 1,
  ...extra,
});

test('视频卡片只列视频素材', () => {
  const assets = [asset('v1', 'video'), asset('i1', 'image'), asset('c1', 'character'), asset('a1', 'audio')];
  const got = filterSwappableAssets(assets, 'video', null);
  assert.deepEqual(got.map((a) => a.asset_id), ['v1']);
});

test('图片卡片列图片和角色素材，不列视频', () => {
  const assets = [asset('v1', 'video'), asset('i1', 'image'), asset('c1', 'character')];
  const got = filterSwappableAssets(assets, 'image', null);
  assert.deepEqual(got.map((a) => a.asset_id).sort(), ['c1', 'i1']);
});

test('排除当前正展示的那一条', () => {
  const assets = [asset('v1', 'video'), asset('v2', 'video')];
  const got = filterSwappableAssets(assets, 'video', 'v1');
  assert.deepEqual(got.map((a) => a.asset_id), ['v2']);
});

test('未就绪 / 没有文件路径的素材不能当候选', () => {
  const assets = [
    asset('v1', 'video'),
    asset('v2', 'video', { status: 'pending' }),
    asset('v3', 'video', { file_path: null }),
    asset('v4', 'video', { status: 'failed' }),
  ];
  const got = filterSwappableAssets(assets, 'video', null);
  assert.deepEqual(got.map((a) => a.asset_id), ['v1']);
});

test('按最近更新排在前面', () => {
  const assets = [
    asset('old', 'video', { updated_at: 10 }),
    asset('new', 'video', { updated_at: 30 }),
    asset('mid', 'video', { updated_at: 20 }),
  ];
  const got = filterSwappableAssets(assets, 'video', null);
  assert.deepEqual(got.map((a) => a.asset_id), ['new', 'mid', 'old']);
});

test('视频卡片上确实渲染了替换入口（不是只有图片才有）', () => {
  assert.match(
    PROCESS_NODE,
    /const canSwapMedia = mode === 'image' \|\| mode === 'video'/,
    '替换入口的条件没有包含视频产物'
  );
  assert.match(PROCESS_NODE, /\{swapControl\}/, '媒体区没有渲染共用的替换控件');
  assert.match(
    PROCESS_NODE,
    /mode === 'video' \? 'lab-process-node-swap-video' : 'lab-process-node-swap-image'/,
    '视频卡片没有自己的替换按钮 testid'
  );
  assert.match(PROCESS_NODE, /const swapAccept = mode === 'video' \? 'video\/\*' : 'image\/\*'/, '本地替换的文件过滤没有按产物类型切换');
});

test('候选列表按产物类型取，弹层里视频用 <video> 当缩略图', () => {
  assert.match(PROCESS_NODE, /filterSwappableAssets\(project\.assets, PROCESS_KIND_MODE\[data\.kind\], data\.assetId\)/, '候选列表没有按产物类型过滤');
  assert.match(PROCESS_NODE, /<video src=\{`\$\{rawFileUrl\(asset\.file_path as string\)\}#t=0\.1`\} muted playsInline preload="metadata" \/>/, '视频素材缩略图没有用 <video>（不加 #t=0.1 会是一片黑）');
});

test('上传替换按视频分类提交', () => {
  assert.match(
    LAB_CANVAS,
    /mergedMode === 'video' \? 'video' : originalAsset\?\.type === 'character' \? 'character' : 'image'/,
    '上传时没有按视频产物传 asset_type=video（服务端会因类型不匹配直接 400）'
  );
});

test('两个替换入口都放开了视频卡片，并且改的是当前历史条目 + 镜像字段', () => {
  assert.match(LAB_CANVAS, /const handleSwapMedia = useCallback\(/, '没有上传替换的处理函数');
  assert.match(LAB_CANVAS, /const handleSwapMediaFromAsset = useCallback\(/, '没有素材库替换的处理函数');
  const gates = LAB_CANVAS.match(/const isMergedSwappableCard = mergedMode === 'image' \|\| mergedMode === 'video';/g) ?? [];
  assert.equal(gates.length, 2, '两个替换入口都应该同时接受图片和视频产物');
  // 合并卡片换内容时必须同时改 history[activeHistoryIndex] 和扁平镜像
  assert.match(LAB_CANVAS, /i === pd\.activeHistoryIndex[\s\S]{0,240}assetId: newAsset\.asset_id, filePath: newAsset\.file_path as string/, '上传替换没有写回历史条目');
  assert.match(
    LAB_CANVAS,
    /i === pd\.activeHistoryIndex \? \{ \.\.\.entry, assetId: asset\.assetId, filePath: asset\.filePath, name \} : entry/,
    '素材库替换没有写回历史条目'
  );
});

test('两种语言的文案都补上了视频版', () => {
  for (const loc of ['zh', 'en']) {
    const dict = JSON.parse(readFileSync(fileURLToPath(new URL(`../src/i18n/locales/${loc}.json`, import.meta.url)), 'utf8'));
    assert.ok(dict.director.lab.swapVideo, `${loc} 缺少 swapVideo`);
    assert.ok(dict.director.lab.swapNoVideoAssets, `${loc} 缺少 swapNoVideoAssets`);
  }
});

// 视频卡片换片之后必须保住卡片自己的名字：卡片名字是它在流程图里的身份，
// 换成素材库里那一段的名字之后，卡片就"变成"了素材库那一条，用户和下游
// 卡片都认不出"我这张卡片换了哪一段"。本地/素材库两条替换路径必须一致。
test('视频换片：保住卡片自己的名字', () => {
  assert.equal(swappedEntryName('Scene1 Shot1-2', '剪辑成片 11-28-15', 'video'), 'Scene1 Shot1-2');
});

test('视频卡片还没有名字时，退回用素材自己的名字（不能把名字清空）', () => {
  assert.equal(swappedEntryName('', '剪辑成片 11-28-15', 'video'), '剪辑成片 11-28-15');
});

test('图片换片仍改用素材自己的名字（@名称 引用的是素材）', () => {
  assert.equal(swappedEntryName('旧卡片名', '小水獭', 'image'), '小水獭');
});

test('替换沿用 swappedEntryName，不再直接写死素材名', () => {
  assert.match(LAB_CANVAS, /swappedEntryName\(pd\.name, asset\.name, mergedMode as 'image' \| 'video'\)/, '合并卡片换片没有走命名规则');
  const block = LAB_CANVAS.slice(LAB_CANVAS.indexOf('const handleSwapMediaFromAsset'), LAB_CANVAS.indexOf('const handleDeleteNode'));
  assert.ok(
    !/i === pd\.activeHistoryIndex \? \{ \.\.\.entry, assetId: asset\.assetId, filePath: asset\.filePath, name: asset\.name \}/.test(block),
    '合并卡片的换片仍然直接把卡片名改成素材名'
  );
});
