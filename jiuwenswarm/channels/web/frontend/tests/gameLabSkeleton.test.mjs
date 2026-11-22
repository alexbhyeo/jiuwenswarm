// 骨骼覆盖层的几何收集规则（自动绑定产出的"模型 + 骨架"预览）。
//
// three.js 场景里骨头是带 isBone 标记的普通节点，网格/分组混在同一棵树里，
// 所以这里钉住三件事：
// 1. 只把"骨头 -> 骨头子节点"当线段，网格与分组不作为端点；
// 2. 叶子骨头没有子骨头，仍然是关节（collectBoneNodes 收全）；
// 3. 遍历是深度优先的，骨头挂在非骨头节点下面（骨架根）也能找到。

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  collectBoneNodes,
  collectBoneSegments,
} from '../node_modules/.cache/game-lab-skeleton/skeletonOverlay.js';

const traversal = {
  isBone: (node) => node.isBone === true,
  children: (node) => node.children ?? [],
};

test('骨头之间的父子关系变成线段，叶子骨头没有线段但有节点', () => {
  const hip = { isBone: true, name: 'hip' };
  const spine = { isBone: true, name: 'spine' };
  const head = { isBone: true, name: 'head' };
  hip.children = [spine];
  spine.children = [head];
  head.children = [];

  const segments = collectBoneSegments(hip, traversal);
  assert.deepEqual(
    segments.map(([parent, child]) => `${parent.name}->${child.name}`),
    ['hip->spine', 'spine->head']
  );

  const bones = collectBoneNodes(hip, traversal);
  assert.deepEqual(
    bones.map((bone) => bone.name),
    ['hip', 'spine', 'head']
  );
});

test('网格和分组节点不作为骨骼线段端点', () => {
  const root = { name: 'scene', children: [] };
  const armature = { name: 'armature', children: [] };
  const mesh = { name: 'body-mesh', children: [] };
  const boneA = { isBone: true, name: 'a', children: [] };
  const boneB = { isBone: true, name: 'b', children: [] };
  const boneUnderMesh = { isBone: true, name: 'c', children: [] };
  armature.children = [boneA];
  boneA.children = [mesh, boneB];
  mesh.children = [boneUnderMesh];
  root.children = [armature, mesh];

  const segments = collectBoneSegments(root, traversal);
  // 只有 boneA->boneB 是骨头之间的连线；mesh 被跳过
  assert.deepEqual(
    segments.map(([parent, child]) => `${parent.name}->${child.name}`),
    ['a->b']
  );
  // 但挂在 mesh 下面的骨头仍要算关节（Tripo3D 的骨架根有时就挂在网格下）
  const names = collectBoneNodes(root, traversal).map((bone) => bone.name);
  assert.deepEqual(names.sort(), ['a', 'b', 'c']);
});

test('没有骨头的模型（普通 2D 转 3D 产出）返回空集合', () => {
  const root = { name: 'scene', children: [{ name: 'mesh', children: [] }] };
  assert.deepEqual(collectBoneSegments(root, traversal), []);
  assert.deepEqual(collectBoneNodes(root, traversal), []);
});
