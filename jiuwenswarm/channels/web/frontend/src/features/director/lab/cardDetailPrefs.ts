/**
 * 实验室卡片的"详情"显示偏好。
 *
 * 详情＝卡片上两块纯展示的说明文字：
 *  * 「模型 / 生成参数」那一段（模型一行还是纯展示，chevron 点不开任何东西）；
 *  * 输入端口那一排名字（文字提示 / 图片参考 / 首帧图片……）连同它们的 ✓。
 *
 * 收起之后端口仍然可用——见 ProcessNode 的 lab-node-ports--compact：每个
 * `<Handle>` 都还在，只是标签和状态标记被隐藏，端口退化成卡片左边缘的一排
 * 裸圆点（hover 有 tooltip 说明是哪一路）。连接能力不能因为"看起来更干净"
 * 而丢失。
 *
 * 两层开关：
 *  * 全局——工具栏上的一个开关，决定**默认**要不要展示详情；
 *  * 单卡——卡片标题栏上的小按钮，只改这一张，覆盖全局（`detailVisible`）。
 *    留空（undefined）表示"跟随全局"，这样以后改全局偏好时，没有单独设置
 *    过的卡片会跟着一起变，而不是被某次历史操作永久钉住。
 *
 * 全局偏好存在 localStorage（跟 workspace 的 workModeStorage 同一套做法）：
 * 这是纯展示偏好，跟具体项目无关，没必要写进 director_state.json 让它跟着
 * 项目走。单卡覆盖则必须进项目数据——它描述的是"这一张卡片长什么样"，
 * 要能跨刷新、跨重建恢复。
 */

import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'jiuwenswarm_director_lab_show_card_detail';

/** 默认展示——保持改造前的行为，不给老用户突然少一块信息。 */
export const DEFAULT_SHOW_CARD_DETAIL = true;

export function readStoredShowCardDetail(): boolean {
  if (typeof window === 'undefined') return DEFAULT_SHOW_CARD_DETAIL;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === 'hide') return false;
    if (raw === 'show') return true;
    return DEFAULT_SHOW_CARD_DETAIL;
  } catch {
    // 隐私模式 / 受限上下文里 localStorage 可能直接抛异常。
    return DEFAULT_SHOW_CARD_DETAIL;
  }
}

let current = readStoredShowCardDetail();
const listeners = new Set<() => void>();

export function getShowCardDetail(): boolean {
  return current;
}

/** 改全局偏好——所有"跟随全局"的卡片立刻跟着变（useSyncExternalStore 订阅）。 */
export function setShowCardDetail(next: boolean): void {
  if (next === current) return;
  current = next;
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(STORAGE_KEY, next ? 'show' : 'hide');
    } catch {
      // 同上，存不下去也不影响本次会话内的效果。
    }
  }
  for (const listener of listeners) listener();
}

export function subscribeShowCardDetail(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useShowCardDetail(): boolean {
  return useSyncExternalStore(subscribeShowCardDetail, getShowCardDetail, () => DEFAULT_SHOW_CARD_DETAIL);
}

/** 单卡最终是否展示这一段：有自己的设置就听自己的，否则跟随全局。 */
export function resolveDetailVisible(override: boolean | undefined, global: boolean): boolean {
  return override === undefined ? global : override;
}

/**
 * 清掉所有处理卡片上的逐卡覆盖，让它们重新"跟随全局"。
 *
 * 由来：逐卡覆盖的语义是"我就是要这一张跟别人不一样"，所以一旦某张卡片被单独
 * 点过，全局开关就再也管不到它了——用户按下全局开关，却看到那张卡片纹丝不动，
 * 会以为开关坏了。取舍是：**按下全局开关＝对每一张卡片生效**。具体做法不是让
 * 全局去"碾压"覆盖值（那样单卡就永远不能反过来设置），而是在按下的那一刻把
 * 覆盖清空、回到统一的默认值；之后用户仍可以逐卡再调，下一次按全局又会被统一。
 *
 * 回到 undefined（而不是写成当前全局值）是为了让项目数据里只留"用户真正做过
 * 的逐卡选择"——否则每按一次全局开关都会给画布上每张卡片写一个显式值，
 * 既污染存档，也让以后改全局默认再也追不上这些卡片。
 */
export function stripDetailOverrides<T extends { type?: string; data?: unknown }>(nodes: readonly T[]): T[] {
  let changed = false;
  const next = nodes.map((node) => {
    const data = node.data as Record<string, unknown> | undefined;
    if (node.type !== 'process' || !data || !('detailVisible' in data)) return node;
    changed = true;
    const { detailVisible: _dropped, ...rest } = data;
    return { ...node, data: rest };
  });
  return changed ? next : (nodes as T[]);
}
