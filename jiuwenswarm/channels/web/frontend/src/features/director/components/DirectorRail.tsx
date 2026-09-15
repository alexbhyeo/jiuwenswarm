import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { DirectorAssetCounts, DirectorProject } from '../types';

interface DirectorRailProps {
  projects: DirectorProject[];
  assetCounts: DirectorAssetCounts;
  onNewProject: () => void;
}

type ExpandedCategory = 'video' | 'image' | null;

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

const videoIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="2.5" y="6" width="14" height="12" rx="2" />
    <path d="m16.5 10 5-3v10l-5-3z" />
  </svg>
);

const imageIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

const characterIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="8" r="3.2" />
    <path d="M5 20c0-3.6 3.1-6.4 7-6.4s7 2.8 7 6.4" />
  </svg>
);

const chevronDown = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="m6 9 6 6 6-6" />
  </svg>
);

const chevronRight = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="m9 6 6 6-6 6" />
  </svg>
);

const plusIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export function DirectorRail({ projects, assetCounts, onNewProject }: DirectorRailProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<ExpandedCategory>('image');

  const assetsByType = useMemo(() => {
    const video: Array<{ projectId: string; asset: DirectorProject['assets'][number] }> = [];
    const image: Array<{ projectId: string; asset: DirectorProject['assets'][number] }> = [];
    for (const project of projects) {
      for (const asset of project.assets) {
        if (asset.status !== 'ready') continue;
        if (asset.type === 'video') video.push({ projectId: project.project_id, asset });
        else if (asset.type === 'image') image.push({ projectId: project.project_id, asset });
      }
    }
    const byRecency = (
      a: { asset: DirectorProject['assets'][number] },
      b: { asset: DirectorProject['assets'][number] }
    ) => b.asset.updated_at - a.asset.updated_at;
    return { video: video.sort(byRecency), image: image.sort(byRecency) };
  }, [projects]);

  const toggle = (category: ExpandedCategory) => {
    setExpanded((prev) => (prev === category ? null : category));
  };

  return (
    <div className="director-rail">
      <div className="director-rail-title">{t('director.title')}</div>

      <button type="button" className="director-new-project-btn" onClick={onNewProject}>
        {plusIcon}
        {t('director.newProject')}
      </button>

      <div className="director-rail-section-label">{t('director.assets')}</div>

      <div className="director-rail-categories">
        <button
          type="button"
          className={`director-category-row ${expanded === 'video' ? 'director-category-row--active' : ''}`}
          onClick={() => toggle('video')}
        >
          <span className="director-category-icon">{videoIcon}</span>
          <span className="director-category-label">{t('director.categories.video')}</span>
          <span className="director-category-count">{assetCounts.video}</span>
          {expanded === 'video' ? chevronDown : chevronRight}
        </button>
        {expanded === 'video' && (
          <div className="director-asset-grid">
            {assetsByType.video.length === 0 && (
              <div className="director-empty-hint">{t('director.assetsEmpty')}</div>
            )}
            {assetsByType.video.map(({ asset }) => (
              <div key={asset.asset_id}>
                <div className="director-asset-thumb">
                  {asset.file_path && (
                    <video src={rawFileUrl(asset.file_path)} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  )}
                </div>
                <div className="director-asset-name">{asset.prompt || t('director.categories.video')}</div>
                <div className="director-asset-time">{relativeTime(t, asset.updated_at)}</div>
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          className={`director-category-row ${expanded === 'image' ? 'director-category-row--active' : ''}`}
          onClick={() => toggle('image')}
        >
          <span className="director-category-icon">{imageIcon}</span>
          <span className="director-category-label">{t('director.categories.image')}</span>
          <span className="director-category-count">{assetCounts.image}</span>
          {expanded === 'image' ? chevronDown : chevronRight}
        </button>
        {expanded === 'image' && (
          <div className="director-asset-grid">
            {assetsByType.image.length === 0 && (
              <div className="director-empty-hint">{t('director.assetsEmpty')}</div>
            )}
            {assetsByType.image.map(({ asset }) => (
              <div key={asset.asset_id}>
                <div
                  className="director-asset-thumb"
                  style={asset.file_path ? { backgroundImage: `url(${rawFileUrl(asset.file_path)})` } : undefined}
                />
                <div className="director-asset-name">{asset.prompt || t('director.categories.image')}</div>
                <div className="director-asset-time">{relativeTime(t, asset.updated_at)}</div>
              </div>
            ))}
          </div>
        )}

        <div className="director-category-row director-category-row--disabled">
          <span className="director-category-icon">{characterIcon}</span>
          <span className="director-category-label">{t('director.categories.character')}</span>
          <span className="director-category-soon">{t('director.comingSoon')}</span>
        </div>
      </div>

      <div className="director-rail-spacer" />

      <div className="director-rail-footer">{t('director.poweredBy')}</div>
    </div>
  );
}
