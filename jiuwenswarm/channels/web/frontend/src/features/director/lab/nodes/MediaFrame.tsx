import { forwardRef } from 'react';

export function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 换图弹层（ProcessNode/ImageNode 共用）要 Portal 到 document.body 才能
 *  逃出 .lab-node-media 的 overflow:hidden 裁剪，脱离卡片 DOM 子树之后就
 *  不能再靠 CSS 相对定位贴着触发按钮了，改成在点开的这一刻用触发按钮的
 *  屏幕坐标（getBoundingClientRect，不受画布 pan/zoom 的 CSS transform
 *  影响——那只影响画布内部坐标系，不影响元素在视口里的真实像素位置）
 *  算一个 fixed 定位的左上角，贴着按钮右下方展开，并夹在视口范围内避免
 *  右边/下边出屏幕。 */
export function popoverPositionFromTrigger(
  trigger: HTMLElement,
  popoverWidth = 212,
  margin = 8
): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  const left = Math.min(Math.max(margin, rect.right - popoverWidth), window.innerWidth - popoverWidth - margin);
  const top = Math.min(rect.bottom + 6, window.innerHeight - margin);
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
