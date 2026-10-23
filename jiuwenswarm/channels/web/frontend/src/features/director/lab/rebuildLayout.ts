/**
 * 重建画布时给每张卡片定"排在第几列"。
 *
 * 规则：不依赖任何其它素材的是第 0 列；依赖了第 N 列的素材就排到第 N+1 列，
 * 于是下游卡片一定落在上游卡片的右边一列，连线是横向的。连线一旦变成同一列
 * 里的竖线（要穿过这一列中间的别的卡片），整片画布看起来就是一坨。
 *
 * 关键细节：下游引用的素材常常是上游点过"重新生成"之后的那一条，它**不是**
 * 一个独立的卡片 key，而是某张卡片 history 里的一条记录。如果只按"依赖的
 * 素材 id 是不是一个卡片 key"来判断，这种依赖会被当成"画布外的老素材"、
 * 深度按 0 算，依赖方就跟自己的上游落在同一列了。所以调用方要先把"任意素材
 * id -> 产出它的那张卡片 key"整理成一张表传进来（见 producerKeyByAssetId）。
 */

/** 第几列（0 起）。 */
export type DepthByKey = Map<string, number>;

/**
 * @param keys                这次要摆的全部卡片 key
 * @param depAssetIdsByKey    每张卡片引用到的素材 id（可以是别的卡片的
 *                            history 条目，也可以是不在画布上的老素材）
 * @param producerKeyByAssetId 任意素材 id -> 产出它的卡片 key
 */
export function resolveDepths(
  keys: Iterable<string>,
  depAssetIdsByKey: Map<string, string[]>,
  producerKeyByAssetId: Map<string, string>,
): DepthByKey {
  const depthByKey: DepthByKey = new Map();
  const resolving = new Set<string>();

  const depthOf = (key: string): number => {
    const cached = depthByKey.get(key);
    if (cached !== undefined) return cached;
    const depAssetIds = depAssetIdsByKey.get(key);
    if (!depAssetIds || depAssetIds.length === 0) {
      depthByKey.set(key, 0);
      return 0;
    }
    // 循环依赖兜底（理论上不该出现）：回到自己就先按 0 算，避免栈溢出。
    if (resolving.has(key)) return 0;
    resolving.add(key);
    let maxDepDepth = -1;
    for (const depAssetId of depAssetIds) {
      const producerKey = producerKeyByAssetId.get(depAssetId);
      // producerKey === key：依赖的是这张卡片自己的某条历史记录，已经算在
      // 自己身上，别再递归回自己。
      if (!producerKey || producerKey === key) {
        maxDepDepth = Math.max(maxDepDepth, 0);
        continue;
      }
      maxDepDepth = Math.max(maxDepDepth, depthOf(producerKey));
    }
    resolving.delete(key);
    const depth = maxDepDepth + 1;
    depthByKey.set(key, depth);
    return depth;
  };

  for (const key of keys) depthOf(key);
  return depthByKey;
}
