// 重建画布时的"排第几列"。
//
// 下游卡片必须排到上游卡片的右边一列，连线才是横向的；一旦算成同一列，连线
// 就变成一条竖线穿过同列的其它卡片，整片画布看起来就是一坨。
//
// 最容易踩的坑：下游引用的常常是上游"重新生成"出来的那一条素材，它不是一个
// 独立的卡片 key，而是某张卡片 history 里的一条记录。深度计算若只认"是不是
// 卡片 key"，这种依赖会被当成画布外的老素材、按第 0 层算，依赖方于是和自己
// 的上游落在同一列（就是 christmas 工程里那 9 条竖线的成因）。

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { resolveDepths } from '../node_modules/.cache/director-rebuild-layout/rebuildLayout.js';

const depths = (keys, deps, producers) =>
  resolveDepths(keys, new Map(Object.entries(deps)), new Map(Object.entries(producers)));

test('不依赖任何素材的卡片排第 0 列', () => {
  const d = depths(['a', 'b'], { a: [], b: [] }, { a: 'a', b: 'b' });
  assert.equal(d.get('a'), 0);
  assert.equal(d.get('b'), 0);
});

test('依赖另一张卡片本身的素材 -> 右边一列', () => {
  const d = depths(['a', 'b'], { a: [], b: ['asset_a'] }, { a: 'a', b: 'b', asset_a: 'a' });
  assert.equal(d.get('b'), 1);
});

test('依赖上游"重新生成"出来的素材（history 条目）也 -> 右边一列', () => {
  // mid 引用的是 root 的第 5 条历史记录（asset_root5 不是卡片 key，但由 root
  // 产出）；out 引用的是 mid 的第 2 条历史记录。上游 root 本身已经在第 1 列
  // 之外，所以"按 history 条目能不能算出依赖"会直接体现在 mid 的列号上——
  // 算不出来时 mid 会被当成"只依赖画布外老素材"，停在新的第 0 列起点。
  const d = depths(
    ['root', 'mid', 'out'],
    { root: [], mid: ['asset_root5'], out: ['asset_mid2'] },
    { root: 'root', mid: 'mid', out: 'out', asset_root5: 'root', asset_mid2: 'mid' },
  );
  assert.equal(d.get('mid'), d.get('root') + 1, '按 history 条目算不出依赖，mid 会跟 root 落在同一列');
  assert.equal(d.get('out'), d.get('mid') + 1, 'out 应该排在 mid 右边，而不是跟 mid 同列');
});

test('多层重新生成链条逐列往右', () => {
  // a -> b（引用 a 的 history）-> c（引用 b 的 history）
  const d = depths(
    ['a', 'b', 'c'],
    { a: [], b: ['asset_a9'], c: ['asset_b3'] },
    { a: 'a', b: 'b', c: 'c', asset_a9: 'a', asset_b3: 'b' },
  );
  assert.deepEqual([d.get('a'), d.get('b'), d.get('c')], [0, 1, 2]);
});

test('依赖画布外的老素材：仍至少排到第 1 列（不从第 0 列挤）', () => {
  const d = depths(['b'], { b: ['upload_leaf'] }, { b: 'b' });
  assert.equal(d.get('b'), 1);
});

test('取依赖里最深的那个作为上游', () => {
  const d = depths(
    ['a', 'b', 'c'],
    { a: [], b: ['asset_a'], c: ['asset_a', 'asset_b'] },
    { a: 'a', b: 'b', c: 'c', asset_a: 'a', asset_b: 'b' },
  );
  assert.equal(d.get('c'), 2, 'c 同时依赖 a 和 b，应该排在 b 的右边而不是 a 的右边');
});

test('自引用不会递归爆栈', () => {
  const d = depths(['a'], { a: ['asset_a_same'] }, { a: 'a', asset_a_same: 'a' });
  assert.equal(d.get('a'), 1);
});

test('循环依赖不会爆栈', () => {
  const d = depths(
    ['a', 'b'],
    { a: ['asset_b'], b: ['asset_a'] },
    { a: 'a', b: 'b', asset_a: 'a', asset_b: 'b' },
  );
  assert.equal(typeof d.get('a'), 'number');
  assert.equal(typeof d.get('b'), 'number');
});
