import { useTranslation } from 'react-i18next';
import type { DirectorProject } from '../types';

interface ProjectCardProps {
  project: DirectorProject;
  onSelect: () => void;
}

const playIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="#191919">
    <path d="M8 5v14l11-7z" />
  </svg>
);

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

  return (
    <button type="button" className="director-project-card" onClick={onSelect}>
      <div
        className={`director-project-thumb ${!cover ? 'director-project-thumb--empty' : ''}`}
        style={cover?.type === 'image' && cover.file_path ? { backgroundImage: `url(${rawFileUrl(cover.file_path)})`, backgroundSize: 'cover', backgroundPosition: 'center' } : undefined}
      >
        {cover?.type === 'video' && <div className="director-project-play">{playIcon}</div>}
        {cover && (
          <div className="director-project-badge">
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
    </button>
  );
}
