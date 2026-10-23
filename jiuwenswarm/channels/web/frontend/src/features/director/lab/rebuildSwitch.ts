/**
 * 重建画布时决定"每张处理卡片最终显示哪一条历史记录"。
 *
 * 背景：下游卡片连到某条"重新生成"出来的素材时，我们希望连线落在产出它的
 * 那张处理卡片上（而不是在旁边多摆一张一模一样的参考卡片），并且那张上游
 * 卡片要显示被引用的这一条。
 *
 * 冲突：一张卡片只能显示一条记录，但它的不同历史条目可能被不同下游分别
 * 引用（A 引用第 0 条、B 引用第 5 条）。此时无解，必须选一条。选法必须与
 * 摆放顺序无关，否则同一份工程每次重建出来的画面都可能不同——所以这里按
 * 引用票数取最多的那条，平票时取下标更大的那条（更晚生成，也就是用户点
 * "重新生成"之后卡片本来会停留的那一条）。
 */

/** nodeId -> (history 下标 -> 被下游引用次数) */
export type SwitchVotes = Map<string, Map<number, number>>;

/** nodeId -> 应该切到的 history 下标 */
export function pickSwitchTargets(votes: SwitchVotes): Map<string, number> {
  const targets = new Map<string, number>();
  for (const [nodeId, counts] of votes) {
    let bestIndex = -1;
    let bestVotes = -1;
    for (const [index, count] of counts) {
      if (count > bestVotes || (count === bestVotes && index > bestIndex)) {
        bestIndex = index;
        bestVotes = count;
      }
    }
    if (bestIndex >= 0) targets.set(nodeId, bestIndex);
  }
  return targets;
}
