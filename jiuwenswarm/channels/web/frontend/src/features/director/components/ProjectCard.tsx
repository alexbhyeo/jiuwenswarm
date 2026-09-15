import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { DirectorProject } from '../types';

interface ProjectCardProps {
  project: DirectorProject;
  onSelect: () => void;
}

const enlargeIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
  </svg>
);

interface EnlargeableCoverImageProps {
  src: string;
  alt: string;
}

/** 与 素材 面板的图片缩略图一致：真实 <img> + 右下角放大按钮，点击调用
 *  浏览器原生 Fullscreen API；stopPropagation 避免同时触发卡片的选中。 */
function EnlargeableCoverImage({ src, alt }: EnlargeableCoverImageProps) {
  const { t } = useTranslation();
  const imgRef = useRef<HTMLImageElement>(null);

  return (
    <>
      <img
        ref={imgRef}
        src={src}
        alt={alt}
        onClick={(e) => e.stopPropagation()}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
      <button
        type="button"
        className="director-project-enlarge-btn"
        title={t('director.enlarge')}
        onClick={(e) => {
          e.stopPropagation();
          imgRef.current?.requestFullscreen?.();
        }}
      >
        {enlargeIcon}
      </button>
    </>
  );
}

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

function relativeTime(t: (key: string, opts?: Record<string, unknown>) => string, ts: number): string {
  const diffMs = Date.now() - ts * 1000;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return t('director.time.justNow');
  if (diffMin < 60) return t('director.time.minutesAgo', { count: diffMin });
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return t('director.time.hoursAgo', { count: diffHour });
  const diffDay = Math.floor(diffHour / 24);
  return t('director.time.daysAgo', { count: diffDay });
}

export function ProjectCard({ project, onSelect }: ProjectCardProps) {
  const { t } = useTranslation();
  const readyAssets = project.assets.filter((a) => a.status === 'ready');
  const cover = readyAssets[readyAssets.length - 1];
  const isVideoCover = cover?.type === 'video' && !!cover.file_path;
  const isImageCover = cover?.type === 'image' && !!cover.file_path;

  return (
    <div
      className="director-project-card"
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div
        className={`director-project-thumb ${!cover ? 'director-project-thumb--empty' : ''} ${isVideoCover ? 'director-project-thumb--video' : ''}`}
      >
        {isImageCover && (
          <EnlargeableCoverImage src={rawFileUrl(cover.file_path!)} alt={cover.name || cover.prompt} />
        )}
        {isVideoCover && (
          // 视频封面：用真实 <video> 展示首帧并可直接播放（与 素材 面板的
          // 视频缩略图一致），而不是装饰性播放图标 —— 点击视频/其控件时
          // stopPropagation，避免同时触发卡片的"选中项目"点击。
          <video
            src={rawFileUrl(cover.file_path!)}
            controls
            playsInline
            preload="metadata"
            onClick={(e) => e.stopPropagation()}
            style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#000' }}
          />
        )}
        {cover && (
          <div className={`director-project-badge ${isVideoCover || isImageCover ? 'director-project-badge--top' : ''}`}>
            {String(cover.params.aspect_ratio || '')}
            {cover.type === 'video' && cover.params.duration_seconds ? ` · ${cover.params.duration_seconds}s` : ''}
          </div>
        )}
      </div>
      <div className="director-project-meta">
        <div className="director-project-name">{project.name}</div>
        <div className="director-project-sub">
          {t('director.myProjects.assetCount', { count: project.assets.length })} · {relativeTime(t, project.updated_at)}
        </div>
      </div>
    </div>
  );
}
