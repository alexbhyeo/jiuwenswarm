import { forwardRef } from 'react';

export function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
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
