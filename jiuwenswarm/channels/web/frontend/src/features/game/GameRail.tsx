/**
 * 游戏工坊 · 常驻 rail——项目列表 + 角色/世界/视频/分解产物四类素材，均以
 * 导演模式 DirectorRail 的"可展开分类行"为模板（项目/素材分组 + 数量角标 +
 * 展开箭头），跨「创作/实验室」两个 tab 常驻（见 GamePage.tsx）。
 * 角色/世界/视频三类展开后内联生成表单（与 DirectorRail 不同——Director
 * 的生成发生在主面板的 ComposerCard，这里为了不额外引入一套主面板生成
 * 组件，直接把生成表单放进对应分类的展开区）。分解产物没有生成表单，
 * 只展示分解流水线按批次产出的 model_part，按批删除。
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gameApi, type GameAsset, type GameBridgeStatus, type GameProject } from './gameApi';
import { GAME_ASSET_DRAG_MIME, type GameAssetDragPayload } from './lab/labTypes';
import { rawFileUrl } from './lab/nodes/gameRawFileUrl';
import './gameRail.css';

type AssetCategory = 'character' | 'world' | 'video';
type ExpandedSection = 'projects' | AssetCategory | 'model_part' | null;

function handleAssetDragStart(e: React.DragEvent, asset: GameAsset) {
  const payload: GameAssetDragPayload = {
    assetId: asset.asset_id,
    filePath: asset.file_path ?? '',
    name: asset.name,
    type: asset.type,
  };
  e.dataTransfer.setData(GAME_ASSET_DRAG_MIME, JSON.stringify(payload));
  e.dataTransfer.effectAllowed = 'copy';
}

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

const worldIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.4 2.5 3.8 5.8 3.8 9s-1.4 6.5-3.8 9c-2.4-2.5-3.8-5.8-3.8-9s1.4-6.5 3.8-9z" />
  </svg>
);

const videoIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="2.5" y="6" width="14" height="12" rx="2" />
    <path d="m16.5 10 5-3v10l-5-3z" />
  </svg>
);

const modelPartIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3 3 8v8l9 5 9-5V8z" />
    <path d="M3 8l9 5 9-5M12 13v8" />
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

const ASSET_CATEGORIES: { key: AssetCategory; labelKey: string; icon: JSX.Element }[] = [
  { key: 'character', labelKey: 'gameWorkshop.rail.categoryCharacter', icon: characterIcon },
  { key: 'world', labelKey: 'gameWorkshop.rail.categoryWorld', icon: worldIcon },
  { key: 'video', labelKey: 'gameWorkshop.rail.categoryVideo', icon: videoIcon },
];

interface GameProjectRowNameProps {
  project: GameProject;
  onRename: (name: string) => void;
  onDelete: () => void;
}

/** 项目行名称：镜像 DirectorRail 的 ProjectRowName——默认展示项目名，点铅笔
 *  行内编辑（回车/失焦保存，Esc 取消），点垃圾桶二次确认后删除整个项目。 */
function GameProjectRowName({ project, onRename, onDelete }: GameProjectRowNameProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  if (editing) {
    return (
      <input
        autoFocus
        className="game-rail-project-row-name-input"
        value={draft}
        placeholder={t('gameWorkshop.rail.renamePlaceholder')}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={() => {
          setEditing(false);
          const trimmed = draft.trim();
          if (trimmed && trimmed !== project.name) onRename(trimmed);
        }}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            setEditing(false);
          }
        }}
        data-testid="game-workshop-rail-project-rename-input"
      />
    );
  }

  return (
    <>
      <span className="game-rail-project-row-name">{project.name}</span>
      <button
        type="button"
        className="game-rail-project-rename-btn"
        title={t('gameWorkshop.rail.renameAction')}
        onClick={(e) => {
          e.stopPropagation();
          setDraft(project.name);
          setEditing(true);
        }}
        data-testid="game-workshop-rail-project-rename-btn"
      >
        {pencilIcon}
      </button>
      <button
        type="button"
        className="game-rail-project-rename-btn game-rail-project-delete-btn"
        title={t('gameWorkshop.rail.deleteAction')}
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm(t('gameWorkshop.rail.deleteConfirm', { name: project.name }))) {
            onDelete();
          }
        }}
        data-testid="game-workshop-rail-project-delete-btn"
      >
        {trashIcon}
      </button>
    </>
  );
}

interface GameRailProps {
  projects: GameProject[];
  selectedProject: GameProject | null;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (name: string) => Promise<void>;
  onRenameProject: (projectId: string, name: string) => Promise<void>;
  onDeleteProject: (projectId: string) => Promise<void>;
  onProjectUpdate: (next: GameProject) => void;
  bridge: GameBridgeStatus | null;
}

export function GameRail({
  projects,
  selectedProject,
  onSelectProject,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
  onProjectUpdate,
  bridge,
}: GameRailProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<ExpandedSection>('character');
  const [draftName, setDraftName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const assetsByCategory = useMemo(() => {
    const assets = selectedProject?.assets ?? [];
    const grouped: Record<AssetCategory, GameAsset[]> = { character: [], world: [], video: [] };
    for (const asset of assets) {
      if (asset.type === 'character' || asset.type === 'world' || asset.type === 'video') {
        grouped[asset.type].push(asset);
      }
    }
    return grouped;
  }, [selectedProject]);

  const decomposedBatches = useMemo(() => {
    const assets = (selectedProject?.assets ?? []).filter((a) => a.type === 'model_part' && a.status === 'ready');
    const byBatch = new Map<string, GameAsset[]>();
    for (const asset of assets) {
      const key = asset.decompose_batch_id ?? asset.asset_id;
      const list = byBatch.get(key) ?? [];
      list.push(asset);
      byBatch.set(key, list);
    }
    return Array.from(byBatch.entries());
  }, [selectedProject]);

  const decomposedCount = useMemo(
    () => decomposedBatches.reduce((sum, [, parts]) => sum + parts.length, 0),
    [decomposedBatches]
  );

  const toggle = (section: ExpandedSection) => {
    setExpanded((prev) => (prev === section ? null : section));
    setPrompt('');
    setNotice('');
  };

  const generate = async (cat: AssetCategory) => {
    if (!selectedProject || !prompt.trim()) return;
    setBusy(true);
    setNotice(t('gameWorkshop.rail.generating'));
    try {
      const resp = await gameApi.generateAsset(selectedProject.project_id, prompt.trim(), cat, prompt.trim().slice(0, 24));
      onProjectUpdate(resp.project);
      if (resp.asset.status === 'ready') {
        setNotice(t('gameWorkshop.rail.generateSuccess', { name: resp.asset.name }));
        setPrompt('');
      } else {
        setNotice(t('gameWorkshop.rail.generateFailed', { error: resp.asset.status }));
      }
    } catch (err) {
      setNotice(t('gameWorkshop.rail.generateFailed', { error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const scaffold = async () => {
    if (!selectedProject) return;
    setBusy(true);
    try {
      const resp = await gameApi.scaffoldProject(selectedProject.project_id);
      onProjectUpdate(resp.project);
      setNotice(
        resp.created.length
          ? t('gameWorkshop.rail.scaffoldSuccess', { files: resp.created.join('、') })
          : t('gameWorkshop.rail.scaffoldComplete')
      );
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const build2d = async () => {
    if (!selectedProject) return;
    setBusy(true);
    try {
      const resp = await gameApi.build2d(selectedProject.project_id);
      onProjectUpdate(resp.project);
      setNotice(t('gameWorkshop.rail.buildSuccess', { files: resp.created.join('、') }));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const deleteBatch = async (decomposeBatchId: string) => {
    if (!selectedProject) return;
    const next = await gameApi.deleteAssetBatch(selectedProject.project_id, decomposeBatchId);
    onProjectUpdate(next);
  };

  const handleCreateProject = async () => {
    const name = draftName.trim();
    if (!name) return;
    setBusy(true);
    try {
      await onCreateProject(name);
      setDraftName('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="game-rail">
      <div className="game-rail-title">{t('gameWorkshop.rail.title')}</div>

      <div className="game-rail-new-project">
        <input
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleCreateProject();
          }}
          placeholder={t('gameWorkshop.rail.newProjectPlaceholder')}
          data-testid="game-workshop-new-project-input"
        />
        <button
          type="button"
          className="game-rail-new-project-btn"
          onClick={() => void handleCreateProject()}
          disabled={busy || !draftName.trim()}
          data-testid="game-workshop-new-project-button"
        >
          {plusIcon}
          {t('gameWorkshop.rail.newProjectButton')}
        </button>
      </div>

      {projects.length > 0 && (
        <div className="game-rail-categories game-rail-categories--projects">
          <button
            type="button"
            className={`game-rail-category-row ${expanded === 'projects' ? 'game-rail-category-row--active' : ''}`}
            onClick={() => toggle('projects')}
            data-testid="game-workshop-rail-section-row"
            data-variant="projects"
          >
            <span className="game-rail-category-icon">{projectIcon}</span>
            <span className="game-rail-category-label">{t('gameWorkshop.rail.projectsSection')}</span>
            <span className="game-rail-category-count">{projects.length}</span>
            {expanded === 'projects' ? chevronDown : chevronRight}
          </button>
          {expanded === 'projects' && (
            <div className="game-rail-project-list">
              {projects.map((p) => (
                <div
                  key={p.project_id}
                  role="button"
                  tabIndex={0}
                  className={`game-rail-project-row ${
                    p.project_id === selectedProject?.project_id ? 'game-rail-project-row--active' : ''
                  }`}
                  onClick={() => onSelectProject(p.project_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectProject(p.project_id);
                    }
                  }}
                  data-testid="game-workshop-project-list-item"
                  data-variant={p.project_id}
                  aria-pressed={p.project_id === selectedProject?.project_id}
                >
                  <GameProjectRowName
                    project={p}
                    onRename={(name) => void onRenameProject(p.project_id, name)}
                    onDelete={() => void onDeleteProject(p.project_id)}
                  />
                  <span className="game-rail-category-count">{p.assets.length}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!selectedProject && <div className="game-rail-empty">{t('gameWorkshop.rail.noProjects')}</div>}

      {selectedProject && (
        <>
          <div className="game-rail-project-actions">
            <button type="button" disabled={busy} onClick={() => void scaffold()} data-testid="game-workshop-rail-scaffold-button">
              {t('gameWorkshop.rail.scaffoldButton')}
            </button>
            <button type="button" disabled={busy} onClick={() => void build2d()} data-testid="game-workshop-rail-build2d-button">
              {t('gameWorkshop.rail.buildButton')}
            </button>
            {bridge && !bridge.ready && (
              <div className="game-rail-bridge-notice" data-testid="game-workshop-rail-bridge-notice">
                {t('gameWorkshop.rail.bridgeUnavailable')}
              </div>
            )}
          </div>

          <div className="game-rail-section-label">{t('gameWorkshop.rail.assetsSection')}</div>

          <div className="game-rail-categories">
            {ASSET_CATEGORIES.map((c) => (
              <div key={c.key}>
                <div
                  role="button"
                  tabIndex={0}
                  className={`game-rail-category-row ${expanded === c.key ? 'game-rail-category-row--active' : ''}`}
                  onClick={() => toggle(c.key)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      toggle(c.key);
                    }
                  }}
                  data-testid="game-workshop-rail-section-row"
                  data-variant={c.key}
                >
                  <span className="game-rail-category-icon">{c.icon}</span>
                  <span className="game-rail-category-label">{t(c.labelKey)}</span>
                  <span className="game-rail-category-count">{assetsByCategory[c.key].length}</span>
                  {expanded === c.key ? chevronDown : chevronRight}
                </div>
                {expanded === c.key && (
                  <div className="game-rail-category-body" data-testid="game-workshop-rail-section-body" data-variant={c.key}>
                    <div className="game-rail-generate">
                      <textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder={t('gameWorkshop.rail.promptPlaceholder')}
                        disabled={busy}
                        data-testid="game-workshop-rail-prompt-input"
                      />
                      <button
                        type="button"
                        disabled={busy || !prompt.trim()}
                        onClick={() => void generate(c.key)}
                        data-testid="game-workshop-rail-generate-button"
                      >
                        {busy ? t('gameWorkshop.rail.generating') : t('gameWorkshop.rail.generateButton')}
                      </button>
                    </div>
                    {notice && (
                      <div className="game-rail-notice" data-testid="game-workshop-rail-notice">
                        {notice}
                      </div>
                    )}
                    <div className="game-rail-asset-grid" data-testid="game-workshop-rail-asset-grid">
                      {assetsByCategory[c.key].length === 0 && (
                        <div className="game-rail-empty-hint">{t('gameWorkshop.rail.noAssets')}</div>
                      )}
                      {assetsByCategory[c.key].map((asset) => (
                        <div
                          key={asset.asset_id}
                          className="game-rail-asset-item"
                          draggable={c.key !== 'video' && asset.status === 'ready'}
                          onDragStart={(e) => handleAssetDragStart(e, asset)}
                          title={c.key !== 'video' ? t('gameWorkshop.rail.dragHint') : undefined}
                          data-testid="game-workshop-rail-asset-item"
                          data-variant={asset.asset_id}
                        >
                          {c.key === 'video' ? (
                            asset.file_path ? (
                              <video src={rawFileUrl(asset.file_path)} controls playsInline />
                            ) : (
                              <div className="game-rail-asset-pending">{asset.status}</div>
                            )
                          ) : asset.file_path ? (
                            <img src={rawFileUrl(asset.file_path)} alt={asset.name} />
                          ) : (
                            <div className="game-rail-asset-pending">{asset.status}</div>
                          )}
                          <div className="game-rail-asset-name">{asset.name}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}

            <div>
              <div
                role="button"
                tabIndex={0}
                className={`game-rail-category-row ${expanded === 'model_part' ? 'game-rail-category-row--active' : ''}`}
                onClick={() => toggle('model_part')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggle('model_part');
                  }
                }}
                data-testid="game-workshop-rail-section-row"
                data-variant="model_part"
              >
                <span className="game-rail-category-icon">{modelPartIcon}</span>
                <span className="game-rail-category-label">{t('gameWorkshop.rail.decomposedSection')}</span>
                <span className="game-rail-category-count">{decomposedCount}</span>
                {expanded === 'model_part' ? chevronDown : chevronRight}
              </div>
              {expanded === 'model_part' && (
                <div className="game-rail-category-body" data-testid="game-workshop-rail-section-body" data-variant="model_part">
                  {decomposedBatches.length === 0 && (
                    <div className="game-rail-empty-hint">{t('gameWorkshop.rail.noAssets')}</div>
                  )}
                  {decomposedBatches.map(([batchId, parts]) => (
                    <div
                      key={batchId}
                      className="game-rail-decomposed-batch"
                      data-testid="game-workshop-rail-decomposed-batch"
                      data-variant={batchId}
                    >
                      <div className="game-rail-asset-grid">
                        {parts.map((part) => (
                          <div
                            key={part.asset_id}
                            className="game-rail-asset-item"
                            data-testid="game-workshop-rail-model-part-item"
                            data-variant={part.asset_id}
                          >
                            <a href={part.file_path ? rawFileUrl(part.file_path) : undefined} download>
                              <div className="game-rail-model-icon" aria-hidden>
                                3D
                              </div>
                            </a>
                            <div className="game-rail-asset-name">{part.name}</div>
                          </div>
                        ))}
                      </div>
                      <button
                        type="button"
                        className="game-rail-delete-batch"
                        onClick={() => void deleteBatch(batchId)}
                        data-testid="game-workshop-rail-delete-batch-button"
                      >
                        {t('gameWorkshop.rail.deleteBatchButton')}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
