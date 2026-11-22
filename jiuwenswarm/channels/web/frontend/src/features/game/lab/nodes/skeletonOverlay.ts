/**
 * 骨骼覆盖层的纯几何收集逻辑——与 three.js 解耦，方便单测
 * （见 tests/gameLabSkeleton.test.mjs）。
 *
 * `<model-viewer>` 只渲染网格，看不到自动绑定的结果"到底绑没绑上"；自动绑定
 * 产出的模型节点改用它自己算出来的骨骼线段/关节覆盖层（见
 * SkeletonModelViewer.tsx），这里只负责"从节点树里找出骨头的父子关系"。
 */

export type BoneTraversal<T> = {
  isBone: (node: T) => boolean;
  children: (node: T) => readonly T[];
};

/** 深度优先找出全部骨头节点（含叶子骨头——末梢骨骼没有子骨头，但关节
 *  标记仍要画出来）。 */
export function collectBoneNodes<T>(root: T, traversal: BoneTraversal<T>): T[] {
  const nodes: T[] = [];
  const seen = new Set<T>();
  const walk = (node: T) => {
    // 同一节点在一棵树里只该出现一次；真遇到重复引用（脏数据/被复用的
    // 骨架）也只算一次，否则关节会在同一位置叠画出更亮的一点。
    if (seen.has(node)) return;
    seen.add(node);
    if (traversal.isBone(node)) nodes.push(node);
    for (const child of traversal.children(node)) walk(child);
  };
  walk(root);
  return nodes;
}

/** 骨头之间的连线：每对"骨头 -> 它的骨头子节点"一段。非骨头节点（网格、
 *  分组）不作为线段端点，与 three.js 的 SkeletonHelper 同一个取舍。 */
export function collectBoneSegments<T>(root: T, traversal: BoneTraversal<T>): Array<[T, T]> {
  const segments: Array<[T, T]> = [];
  const seen = new Set<T>();
  const walk = (node: T) => {
    if (seen.has(node)) return;
    seen.add(node);
    const children = traversal.children(node);
    if (traversal.isBone(node)) {
      for (const child of children) {
        if (traversal.isBone(child)) segments.push([node, child]);
      }
    }
    for (const child of children) walk(child);
  };
  walk(root);
  return segments;
}
