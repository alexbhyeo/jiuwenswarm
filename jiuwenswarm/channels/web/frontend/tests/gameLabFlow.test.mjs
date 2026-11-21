// 「生成实验室流程图」的还原规则。
//
// 这张图是从游戏工程自己的素材数据反推出来的（素材带着 params.source 与
// source_asset_id），所以最值得钉住的是三件事：
// 1. 卡片类型由 params.source 决定，未知来源不画卡片；
// 2. 一次分解产出 N 个部件只画一张卡片，并且卡片到每个部件都有连线；
// 3. 下游卡片排在上游右边一列（同列会出现竖线穿刺，整片画布糊成一坨）。

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildLabFlow } from '../node_modules/.cache/game-lab-flow/labFlow.js';

const image = (id, name = '主角图') => ({
  asset_id: id,
  type: 'character',
  name,
  file_path: `/tmp/${id}.png`,
  status: 'ready',
  params: {},
});

const produced = (id, name, source, sourceAssetId, extra = {}) => ({
  asset_id: id,
  type: source === 'decompose' ? 'model_part' : 'model',
  name,
  file_path: `/tmp/${id}.glb`,
  status: 'ready',
  params: { source },
  source_asset_id: sourceAssetId,
  ...extra,
});

test('没有跑过卡片时返回空图（调用方据此不清空画布）', () => {
  const flow = buildLabFlow({ assets: [image('a')] });
  assert.equal(flow.cardCount, 0);
  assert.equal(flow.nodes.length, 0);
  assert.equal(flow.edges.length, 0);
});

test('分解：一张卡片接所有部件，部件各有一条入边', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('p1', 'head', 'decompose', 'img', { decompose_batch_id: 'b1' }),
      produced('p2', 'hair', 'decompose', 'img', { decompose_batch_id: 'b1' }),
    ],
  });

  assert.equal(flow.cardCount, 1);
  const card = flow.nodes.find((n) => n.type === 'decompose');
  assert.ok(card, '应有一张分解卡片');
  assert.equal(card.data.resultCount, 2, 'resultCount 应等于部件数');
  assert.equal(card.data.status, 'success');

  const imageNode = flow.nodes.find((n) => n.type === 'image');
  assert.ok(imageNode);
  const inEdge = flow.edges.find((e) => e.target === card.id);
  assert.equal(inEdge.source, imageNode.id);
  assert.equal(inEdge.sourceHandle, 'image');
  assert.equal(inEdge.targetHandle, 'image');

  const partEdges = flow.edges.filter((e) => e.source === card.id);
  assert.equal(partEdges.length, 2);
  for (const edge of partEdges) {
    assert.equal(edge.sourceHandle, 'parts');
    assert.equal(edge.targetHandle, 'in');
  }

  // 每一列只能放"同一层"的节点：图在卡片左边，部件在卡片右边。退化实现
  // 里部件会和卡片挤在同一列（连线竖着穿刺、卡片互相压住），这条能挡住。
  const partNodeIds = partEdges.map((e) => e.target);
  for (const partId of partNodeIds) {
    const part = flow.nodes.find((n) => n.id === partId);
    assert.ok(part.position.x > card.position.x, `部件 ${partId} 应排在卡片右边`);
  }
  assert.ok(card.position.x > imageNode.position.x, '卡片应排在源图右边');
  // 同一列里同一个节点不会出现两次，两个部件也不在同一格
  const slots = new Set(flow.nodes.map((n) => `${n.position.x},${n.position.y}`));
  assert.equal(slots.size, flow.nodes.length, '节点位置不能重叠');
});

test('文生图卡片：没有输入素材，产出的图片连在卡片的 image 出口上', () => {
  const flow = buildLabFlow({
    assets: [
      {
        asset_id: 'gen',
        type: 'character',
        name: '森林精灵',
        file_path: '/tmp/gen.png',
        status: 'ready',
        params: { source: 'text2image', prompt: 'chibi forest elf' },
      },
    ],
  });

  assert.equal(flow.cardCount, 1);
  const card = flow.nodes.find((n) => n.type === 'text2image');
  assert.ok(card);
  assert.equal(card.data.prompt, 'chibi forest elf');
  assert.equal(card.data.assetType, 'character');
  assert.equal(card.data.resultAssetId, 'gen');
  // 没有输入素材 -> 卡片没有入边
  assert.equal(flow.edges.filter((e) => e.target === card.id).length, 0);

  const outEdge = flow.edges.find((e) => e.source === card.id);
  assert.equal(outEdge.sourceHandle, 'image');
  assert.equal(outEdge.targetHandle, 'in');
  const imageNode = flow.nodes.find((n) => n.id === outEdge.target);
  assert.equal(imageNode.type, 'image');
});

test('图生图卡片：每张参考图都连到卡片的 image 端口', () => {
  const flow = buildLabFlow({
    assets: [
      image('ref1', '主角'),
      image('ref2', '武器'),
      {
        asset_id: 'gen',
        type: 'world',
        name: '主角战斗姿势',
        file_path: '/tmp/gen.png',
        status: 'ready',
        params: { source: 'image_ref', prompt: 'side-on battle pose', reference_asset_ids: ['ref1', 'ref2'] },
        source_asset_id: 'ref1',
      },
    ],
  });

  assert.equal(flow.cardCount, 1);
  const card = flow.nodes.find((n) => n.type === 'image_ref');
  assert.ok(card);
  assert.equal(card.data.assetType, 'world');

  const inEdges = flow.edges.filter((e) => e.target === card.id);
  assert.equal(inEdges.length, 2);
  assert.deepEqual(new Set(inEdges.map((e) => e.source)), new Set(['asset:ref1', 'asset:ref2']));
  for (const edge of inEdges) {
    assert.equal(edge.sourceHandle, 'image');
    assert.equal(edge.targetHandle, 'image');
  }
  // 参考图排在卡片左边
  for (const edge of inEdges) {
    const ref = flow.nodes.find((n) => n.id === edge.source);
    assert.ok(ref.position.x < card.position.x);
  }
});

test('纹理贴图：卡片接模型节点的 out 口，产出模型并带回提示词/质量', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('m1', '主角 · 2D转3D', 'to_model', 'img'),
      {
        asset_id: 'm2',
        type: 'model',
        name: '主角 · 贴图',
        file_path: '/tmp/m2.glb',
        status: 'ready',
        params: { source: 'texture', prompt: 'worn leather', quality: 'detailed' },
        source_asset_id: 'm1',
      },
    ],
  });

  assert.equal(flow.cardCount, 2);
  const card = flow.nodes.find((n) => n.type === 'texture');
  assert.ok(card);
  assert.equal(card.data.resultAssetId, 'm2');
  assert.equal(card.data.prompt, 'worn leather');
  assert.equal(card.data.quality, 'detailed');

  const inEdge = flow.edges.find((e) => e.target === card.id);
  assert.equal(inEdge.sourceHandle, 'out');
  assert.equal(inEdge.targetHandle, 'model');

  const outEdge = flow.edges.find((e) => e.source === card.id);
  assert.equal(outEdge.sourceHandle, 'result');
  assert.equal(outEdge.targetHandle, 'in');
});

test('自动绑定与动画重定向：各自一张卡片，串在模型后面', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('m1', '主角 · 2D转3D', 'to_model', 'img'),
      {
        asset_id: 'r1',
        type: 'model',
        name: '主角 · 自动绑定',
        file_path: '/tmp/r1.glb',
        status: 'ready',
        params: { source: 'auto_rig', rig_type: 'biped', spec: 'mixamo' },
        source_asset_id: 'm1',
      },
      {
        asset_id: 'a1',
        type: 'model',
        name: '主角 · 动画',
        file_path: '/tmp/a1.glb',
        status: 'ready',
        params: { source: 'animation_retarget', animation: 'preset:walk', animate_in_place: true },
        source_asset_id: 'r1',
      },
    ],
  });

  assert.equal(flow.cardCount, 3);
  const rig = flow.nodes.find((n) => n.type === 'auto_rig');
  const retarget = flow.nodes.find((n) => n.type === 'retarget');
  assert.ok(rig && retarget);
  assert.equal(rig.data.rigType, 'biped');
  assert.equal(rig.data.spec, 'mixamo');
  assert.equal(retarget.data.animation, 'preset:walk');
  assert.equal(retarget.data.animateInPlace, true);

  const rigIn = flow.edges.find((e) => e.target === rig.id);
  assert.equal(rigIn.sourceHandle, 'out');
  assert.equal(rigIn.targetHandle, 'model');
  const retargetIn = flow.edges.find((e) => e.target === retarget.id);
  assert.equal(retargetIn.source, 'asset:r1');
  assert.equal(retargetIn.sourceHandle, 'out');
  assert.equal(retargetIn.targetHandle, 'model');

  // 骨骼检查没有产出素材，不会被还原成卡片
  assert.equal(flow.nodes.filter((n) => n.type === 'rig_check').length, 0);
});

test('不同批次的分解各画一张卡片', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('p1', 'head', 'decompose', 'img', { decompose_batch_id: 'b1' }),
      produced('p2', 'head', 'decompose', 'img', { decompose_batch_id: 'b2' }),
    ],
  });
  assert.equal(flow.cardCount, 2);
});

test('2D 转 3D 与网格处理：卡片带 resultAssetId，下游排到右边一列', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('m1', '主角 · 2D转3D', 'to_model', 'img'),
      produced('m2', '主角 · 网格补全', 'mesh_complete', 'm1'),
      produced('m3', '主角 · 拓扑重构', 'mesh_decimate', 'm2'),
    ],
  });

  assert.equal(flow.cardCount, 3);
  const toModel = flow.nodes.find((n) => n.type === 'to_model');
  const complete = flow.nodes.find((n) => n.type === 'mesh_complete');
  const decimate = flow.nodes.find((n) => n.type === 'mesh_decimate');
  assert.ok(toModel && complete && decimate);
  assert.equal(toModel.data.resultAssetId, 'm1');
  assert.equal(complete.data.resultAssetId, 'm2');
  assert.equal(decimate.data.resultAssetId, 'm3');

  // 列递增：图 -> 2D转3D -> 网格补全 -> 拓扑重构
  assert.ok(toModel.position.x > 0);
  assert.ok(complete.position.x > toModel.position.x);
  assert.ok(decimate.position.x > complete.position.x);

  // 网格卡片的输入必须是模型节点的 out 口
  const completeIn = flow.edges.find((e) => e.target === complete.id);
  assert.equal(completeIn.targetHandle, 'model');
  assert.equal(completeIn.sourceHandle, 'out');
});

test('未知来源、pending 素材、指向已删素材的卡片都按规则跳过容错', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      { ...produced('weird', 'x', 'some_other_source', 'img') },
      { ...produced('pend', 'y', 'to_model', 'img'), status: 'pending' },
      produced('orphan', '孤儿模型', 'to_model', 'gone'),
    ],
  });
  // weird / pend 不出卡片；orphan 的源素材已被删，卡片仍画出来但没有入边
  assert.equal(flow.cardCount, 1);
  const card = flow.nodes.find((n) => n.type === 'to_model');
  assert.ok(card);
  assert.equal(flow.edges.filter((e) => e.target === card.id).length, 0);
});

test('循环依赖不会栈溢出', () => {
  const flow = buildLabFlow({
    assets: [
      produced('a', 'a', 'mesh_complete', 'b'),
      produced('b', 'b', 'mesh_complete', 'a'),
    ],
  });
  assert.equal(flow.cardCount, 2);
  assert.ok(flow.nodes.length >= 2);
});

// 端口名最容易写错，而且错了不报错——连线会找不到落点、悬在卡片外面。
// 这里把每个节点类型真正声明的 handle（抄自 nodes/*.tsx 的 <Handle id=...>）
// 钉死，重建出来的每条边都必须落在合法端口上。
const HANDLES = {
  image: { target: ['in'], source: ['image'] },
  model: { target: ['in'], source: ['out'] },
  model_part: { target: ['in'], source: ['out'] },
  decompose: { target: ['image'], source: ['parts'] },
  to_model: { target: ['image'], source: ['model'] },
  mesh_complete: { target: ['model'], source: ['result'] },
  mesh_decimate: { target: ['model'], source: ['result'] },
  texture: { target: ['model'], source: ['result'] },
  auto_rig: { target: ['model'], source: ['result'] },
  retarget: { target: ['model'], source: ['result'] },
  rig_check: { target: ['model'], source: [] },
  text2image: { target: [], source: ['image'] },
  image_ref: { target: ['image'], source: ['image'] },
};

test('每条边都落在节点真实存在的端口上', () => {
  const flow = buildLabFlow({
    assets: [
      image('img', '主角图'),
      {
        asset_id: 'gen',
        type: 'character',
        name: '生成图',
        file_path: '/tmp/gen.png',
        status: 'ready',
        params: { source: 'image_ref', prompt: 'p', reference_asset_ids: ['img'] },
        source_asset_id: 'img',
      },
      produced('p1', 'head', 'decompose', 'img', { decompose_batch_id: 'b1' }),
      produced('m1', '主角 · 2D转3D', 'to_model', 'img'),
      produced('m2', '主角 · 网格补全', 'mesh_complete', 'm1'),
      produced('m3', '主角 · 拓扑重构', 'mesh_decimate', 'm2'),
      {
        asset_id: 'm4',
        type: 'model',
        name: '主角 · 贴图',
        file_path: '/tmp/m4.glb',
        status: 'ready',
        params: { source: 'texture', prompt: '', quality: 'standard' },
        source_asset_id: 'm3',
      },
      {
        asset_id: 'm5',
        type: 'model',
        name: '主角 · 自动绑定',
        file_path: '/tmp/m5.glb',
        status: 'ready',
        params: { source: 'auto_rig', rig_type: 'biped', spec: 'tripo' },
        source_asset_id: 'm4',
      },
      {
        asset_id: 'm6',
        type: 'model',
        name: '主角 · 动画',
        file_path: '/tmp/m6.glb',
        status: 'ready',
        params: { source: 'animation_retarget', animation: 'preset:idle', animate_in_place: false },
        source_asset_id: 'm5',
      },
    ],
  });

  const typeById = new Map(flow.nodes.map((n) => [n.id, n.type]));
  assert.ok(flow.edges.length > 0);
  for (const edge of flow.edges) {
    const sourceType = typeById.get(edge.source);
    const targetType = typeById.get(edge.target);
    assert.ok(sourceType && targetType, `边的两端都必须是图里的节点: ${edge.id}`);
    assert.ok(
      HANDLES[sourceType].source.includes(edge.sourceHandle),
      `${sourceType} 没有 source 端口 ${edge.sourceHandle}（边 ${edge.id}）`
    );
    assert.ok(
      HANDLES[targetType].target.includes(edge.targetHandle),
      `${targetType} 没有 target 端口 ${edge.targetHandle}（边 ${edge.id}）`
    );
  }
});

test('生成流程图是幂等的：同一份素材跑两次得到同一张图', () => {
  const project = {
    assets: [
      image('img'),
      produced('m1', '主角 · 2D转3D', 'to_model', 'img'),
      produced('m2', '主角 · 网格补全', 'mesh_complete', 'm1'),
    ],
  };
  const first = buildLabFlow(project);
  const second = buildLabFlow(project);
  assert.deepEqual(
    second.nodes.map((n) => [n.id, n.position.x, n.position.y]),
    first.nodes.map((n) => [n.id, n.position.x, n.position.y])
  );
  assert.deepEqual(second.edges.map((e) => e.id), first.edges.map((e) => e.id));
});

// 摆位规则（用户实际提出的两条要求）：
// 1. 互不依赖的卡片竖着排成一列，好认也省宽度；
// 2. 同一张分解卡产出的部件成组铺开，不要一列排到底。
test('互不依赖的卡片竖着排在同一列', () => {
  const flow = buildLabFlow({
    assets: [
      image('img'),
      produced('m1', '2D转3D · a', 'to_model', 'img'),
      produced('m2', '2D转3D · b', 'to_model', 'img'),
      produced('m3', '2D转3D · c', 'to_model', 'img'),
    ],
  });
  const cards = flow.nodes.filter((n) => n.type === 'to_model');
  assert.equal(cards.length, 3);
  assert.equal(new Set(cards.map((n) => n.position.x)).size, 1, '三张卡片应占同一列');
  assert.equal(new Set(cards.map((n) => n.position.y)).size, 3, '三张卡片应各占一行');
});

test('一次分解的 8 个部件铺成 4 列 × 2 行', () => {
  const assets = [image('img')];
  for (let i = 0; i < 8; i += 1) {
    assets.push(produced(`p${i}`, `part${i}`, 'decompose', 'img', { decompose_batch_id: 'b1' }));
  }
  const flow = buildLabFlow({ assets });
  const parts = flow.nodes.filter((n) => n.type === 'model_part');
  assert.equal(parts.length, 8);
  const xs = [...new Set(parts.map((n) => n.position.x))].sort((a, b) => a - b);
  const ys = [...new Set(parts.map((n) => n.position.y))].sort((a, b) => a - b);
  assert.equal(xs.length, 4, '部件应铺成 4 列');
  assert.equal(ys.length, 2, '部件应铺成 2 行');
  // 先横着填：前 4 个在第一行、后 4 个在第二行
  const firstFour = parts.slice(0, 4);
  assert.equal(new Set(firstFour.map((n) => n.position.y)).size, 1);
  assert.deepEqual(
    firstFour.map((n) => n.position.x),
    xs
  );
});
