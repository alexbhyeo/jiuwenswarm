import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import type { DirectorAsset, DirectorProject } from '../types';

interface DirectorRailProps {
  projects: DirectorProject[];
  selectedProject: DirectorProject | null;
  onNewProject: () => void;
  onSelectProject: (projectId: string) => void;
}

type ExpandedCategory = 'projects' | 'video' | 'image' | null;

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

const projectIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
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

const enlargeIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
  </svg>
);

interface EnlargeableImageProps {
  src: string;
  alt: string;
}

/** 图片缩略图右下角的"放大"按钮，与视频原生控件的全屏按钮效果一致——
 *  都调用浏览器 Fullscreen API，而不是自建 lightbox。 */
function EnlargeableImage({ src, alt }: EnlargeableImageProps) {
  const { t } = useTranslation();
  const imgRef = useRef<HTMLImageElement>(null);

  return (
    <>
      <img ref={imgRef} src={src} alt={alt} className="director-asset-image" />
      <button
        type="button"
        className="director-asset-enlarge-btn"
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

const pencilIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
  </svg>
);

const trashIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
    <path d="M10 11v6M14 11v6" />
  </svg>
);

interface AssetNameLabelProps {
  asset: DirectorAsset;
  fallback: string;
  onRename: (name: string) => void;
  onDelete: () => void;
}

/** 素材名称：默认展示 name||prompt；点击铅笔图标进入行内编辑，
 *  回车/失焦保存，Esc 取消。图片重命名后可在 composer 里用 "@名称" 引用。
 *  垃圾桶图标：二次确认后删除该素材（元数据 + 磁盘文件）。 */
function AssetNameLabel({ asset, fallback, onRename, onDelete }: AssetNameLabelProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const displayName = asset.name || asset.prompt || fallback;

  if (editing) {
    return (
      <input
        autoFocus
        className="director-asset-name-input"
        value={draft}
        placeholder={t('director.rename.placeholder')}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={() => {
          setEditing(false);
          if (draft.trim() !== (asset.name || '')) onRename(draft.trim());
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <div className="director-asset-name">
      <span className="director-asset-name-text">{displayName}</span>
      <button
        type="button"
        className="director-asset-rename-btn"
        title={t('director.rename.action')}
        onClick={(e) => {
          e.stopPropagation();
          setDraft(asset.name || '');
          setEditing(true);
        }}
      >
        {pencilIcon}
      </button>
      <button
        type="button"
        className="director-asset-rename-btn director-asset-delete-btn"
        title={t('director.delete.action')}
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm(t('director.delete.confirm', { name: displayName }))) {
            onDelete();
          }
        }}
      >
        {trashIcon}
      </button>
    </div>
  );
}

export function DirectorRail({ projects, selectedProject, onNewProject, onSelectProject }: DirectorRailProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<ExpandedCategory>('image');
  const renameAsset = useDirectorStore((s) => s.renameAsset);
  const deleteAsset = useDirectorStore((s) => s.deleteAsset);

  // 素材面板只反映"当前选中项目"的产物 —— 未创建/选中项目前不展示该区块，
  // 已选中时也只列出该项目自己的 assets，而不是跨项目聚合。
  const assetsByType = useMemo(() => {
    const video: DirectorAsset[] = [];
    const image: DirectorAsset[] = [];
    for (const asset of selectedProject?.assets ?? []) {
      if (asset.status !== 'ready') continue;
      if (asset.type === 'video') video.push(asset);
      else if (asset.type === 'image') image.push(asset);
    }
    const byRecency = (a: DirectorAsset, b: DirectorAsset) => b.updated_at - a.updated_at;
    return { video: video.sort(byRecency), image: image.sort(byRecency) };
  }, [selectedProject]);

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

      {projects.length > 0 && (
        <>
          <div className="director-rail-categories director-rail-categories--projects">
            <button
              type="button"
              className={`director-category-row ${expanded === 'projects' ? 'director-category-row--active' : ''}`}
              onClick={() => toggle('projects')}
            >
              <span className="director-category-icon">{projectIcon}</span>
              <span className="director-category-label">{t('director.projectsSection')}</span>
              <span className="director-category-count">{projects.length}</span>
              {expanded === 'projects' ? chevronDown : chevronRight}
            </button>
            {expanded === 'projects' && (
              <div className="director-project-list">
                {projects.map((project) => (
                  <button
                    key={project.project_id}
                    type="button"
                    className={`director-project-row ${
                      project.project_id === selectedProject?.project_id ? 'director-project-row--active' : ''
                    }`}
                    onClick={() => onSelectProject(project.project_id)}
                  >
                    <span className="director-project-row-name">{project.name}</span>
                    <span className="director-category-count">{project.assets.length}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {selectedProject && (
        <>
          <div className="director-rail-section-label">{t('director.assets')}</div>

          <div className="director-rail-categories">
            <button
              type="button"
              className={`director-category-row ${expanded === 'video' ? 'director-category-row--active' : ''}`}
              onClick={() => toggle('video')}
            >
              <span className="director-category-icon">{videoIcon}</span>
              <span className="director-category-label">{t('director.categories.video')}</span>
              <span className="director-category-count">{assetsByType.video.length}</span>
              {expanded === 'video' ? chevronDown : chevronRight}
            </button>
            {expanded === 'video' && (
              <div className="director-asset-grid">
                {assetsByType.video.length === 0 && (
                  <div className="director-empty-hint">{t('director.assetsEmpty')}</div>
                )}
                {assetsByType.video.map((asset) => (
                  <div key={asset.asset_id} className="director-asset-item">
                    <div className="director-asset-thumb director-asset-thumb--video">
                      {asset.file_path && (
                        <video
                          src={rawFileUrl(asset.file_path)}
                          controls
                          playsInline
                          preload="metadata"
                          style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#000' }}
                        />
                      )}
                    </div>
                    <AssetNameLabel
                      asset={asset}
                      fallback={t('director.categories.video')}
                      onRename={(name) => renameAsset(selectedProject!.project_id, asset.asset_id, name)}
                      onDelete={() => deleteAsset(selectedProject!.project_id, asset.asset_id)}
                    />
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
              <span className="director-category-count">{assetsByType.image.length}</span>
              {expanded === 'image' ? chevronDown : chevronRight}
            </button>
            {expanded === 'image' && (
              <div className="director-asset-grid">
                {assetsByType.image.length === 0 && (
                  <div className="director-empty-hint">{t('director.assetsEmpty')}</div>
                )}
                {assetsByType.image.map((asset) => (
                  <div key={asset.asset_id} className="director-asset-item">
                    <div className="director-asset-thumb director-asset-thumb--image">
                      {asset.file_path && (
                        <EnlargeableImage src={rawFileUrl(asset.file_path)} alt={asset.name || asset.prompt} />
                      )}
                    </div>
                    <AssetNameLabel
                      asset={asset}
                      fallback={t('director.categories.image')}
                      onRename={(name) => renameAsset(selectedProject!.project_id, asset.asset_id, name)}
                      onDelete={() => deleteAsset(selectedProject!.project_id, asset.asset_id)}
                    />
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
        </>
      )}

      <div className="director-rail-spacer" />

      <div className="director-rail-footer">{t('director.poweredBy')}</div>
    </div>
  );
}
