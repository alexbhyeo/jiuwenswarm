import { forwardRef } from 'react';

export function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 换图弹层的尺寸——CSS 的 .lab-node-swap-popover 必须与此保持一致。
 *
 *  这三个数字只有一处能当权威：弹层是 Portal 出去的 fixed 浮层，宽度/高度
 *  在定位时就要用上（夹到视口内），而 CSS 只能用字面量写。两边各写一份很
 *  容易在改大改小时漏掉一边——漏了 CSS 就是定位按旧宽度算、实际按新宽度
 *  渲染，弹层会跑到屏幕外；漏了这里是 CSS 变宽但横坐标仍按窄弹层算，右边
 *  会被裁掉。所以统一从这里导出，改尺寸只改这一处 + CSS 里那三行。 */
export const SWAP_POPOVER_WIDTH = 420;
export const SWAP_POPOVER_MAX_HEIGHT = 560;

/** 换图弹层（ProcessNode/ImageNode 共用）要 Portal 到 document.body 才能
 *  逃出 .lab-node-media 的 overflow:hidden 裁剪，脱离卡片 DOM 子树之后就
 *  不能再靠 CSS 相对定位贴着触发按钮了，改成在点开的这一刻用触发按钮的
 *  屏幕坐标（getBoundingClientRect，不受画布 pan/zoom 的 CSS transform
 *  影响——那只影响画布内部坐标系，不影响元素在视口里的真实像素位置）
 *  算一个 fixed 定位的左上角，贴着按钮右下方展开，并夹在视口范围内避免
 *  右边/下边出屏幕。
 *
 *  纵向也要夹：弹层放大之后（SWAP_POPOVER_MAX_HEIGHT）比很多视口都高，
 *  只夹 top 到 innerHeight - margin 会让它整个探到屏幕下方。这里按弹层的
 *  实际高度预留出空间，空间不够就整体上移。 */
export function popoverPositionFromTrigger(
  trigger: HTMLElement,
  popoverWidth = SWAP_POPOVER_WIDTH,
  popoverMaxHeight = SWAP_POPOVER_MAX_HEIGHT,
  margin = 8
): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  // 窄视口下 420px 的弹层可能比窗口还宽：此时按视口宽度收窄，否则下面的
  // 夹取会算出负的 left（弹层整个跑到屏幕左边外面）。CSS 里对应有
  // max-width: calc(100vw - 16px)，两边口径一致。
  const width = Math.min(popoverWidth, window.innerWidth - margin * 2);
  const left = Math.min(Math.max(margin, rect.right - width), window.innerWidth - width - margin);
  const top = Math.min(
    Math.max(margin, rect.bottom + 6),
    window.innerHeight - popoverMaxHeight - margin
  );
  return { top, left };
}

interface MediaFrameProps {
  mode: 'image' | 'video' | 'audio';
  filePath: string;
  name: string;
  className?: string;
}

/** 三种产物类型共用的 <img>/<video>/<audio> 渲染——ImageNode/VideoNode/
 *  AudioNode（独立的参考卡片）与 ProcessNode 的"已生成"态（合并后的卡片）
 *  都用它展示同一份 filePath，只是外层容器/操作按钮各自不同（放大/替换只
 *  对独立参考卡片有意义，删除在合并卡片上已经有卡头的删除按钮），所以这里
 *  只抽最里面这一层、不含操作按钮。ref 只有 mode==='image' 时有意义——
 *  ImageNode 的"放大"按钮要靠它调 requestFullscreen()。 */
export const MediaFrame = forwardRef<HTMLImageElement, MediaFrameProps>(function MediaFrame(
  { mode, filePath, name, className },
  imgRef
) {
  const src = rawFileUrl(filePath);
  if (mode === 'video') {
    return <video className={className} src={src} controls playsInline preload="metadata" />;
  }
  if (mode === 'audio') {
    return <audio className={className} src={src} controls preload="metadata" />;
  }
  return <img ref={imgRef} className={className} src={src} alt={name} />;
});
