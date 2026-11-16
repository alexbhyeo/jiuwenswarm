/**
 * 游戏工坊 · 创作面板——角色/世界/视频三类素材的生成 + 浏览合一（与导演模式
 * DirectorRail 同一种职责划分：创作 = 生成 + 浏览），外加分解产出的 3D
 * 部件展示。工程脚手架/2D 主城构建两个按钮沿用自原"创作"标签（D-20261107
 * -1637 的 Godot-MCP 部分继续有效，见 D-20261116-1047 §2.3 非目标）。
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gameApi, type GameAsset, type GameBridgeStatus, type GameProject } from './gameApi';
import { GAME_ASSET_DRAG_MIME, type GameAssetDragPayload } from './lab/labTypes';
import { rawFileUrl } from './lab/nodes/gameRawFileUrl';
import './gameRail.css';

type Category = 'character' | 'world' | 'video';

const CATEGORIES: { key: Category; labelKey: string }[] = [
  { key: 'character', labelKey: 'gameWorkshop.rail.categoryCharacter' },
  { key: 'world', labelKey: 'gameWorkshop.rail.categoryWorld' },
  { key: 'video', labelKey: 'gameWorkshop.rail.categoryVideo' },
];

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

interface GameRailProps {
  project: GameProject | null;
  onProjectUpdate: (next: GameProject) => void;
  bridge: GameBridgeStatus | null;
}

export function GameRail({ project, onProjectUpdate, bridge }: GameRailProps) {
  const { t } = useTranslation();
  const [category, setCategory] = useState<Category>('character');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const assetsByCategory = useMemo(() => {
    const assets = project?.assets ?? [];
    const grouped: Record<Category, GameAsset[]> = { character: [], world: [], video: [] };
    for (const asset of assets) {
      if (asset.type === 'character' || asset.type === 'world' || asset.type === 'video') {
        grouped[asset.type].push(asset);
      }
    }
    return grouped;
  }, [project]);

  const decomposedBatches = useMemo(() => {
    const assets = (project?.assets ?? []).filter((a) => a.type === 'model_part' && a.status === 'ready');
    const byBatch = new Map<string, GameAsset[]>();
    for (const asset of assets) {
      const key = asset.decompose_batch_id ?? asset.asset_id;
      const list = byBatch.get(key) ?? [];
      list.push(asset);
      byBatch.set(key, list);
    }
    return Array.from(byBatch.entries());
  }, [project]);

  const generate = async () => {
    if (!project || !prompt.trim()) return;
    setBusy(true);
    setNotice(t('gameWorkshop.rail.generating'));
    try {
      const resp = await gameApi.generateAsset(project.project_id, prompt.trim(), category, prompt.trim().slice(0, 24));
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
    if (!project) return;
    setBusy(true);
    try {
      const resp = await gameApi.scaffoldProject(project.project_id);
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
    if (!project) return;
    setBusy(true);
    try {
      const resp = await gameApi.build2d(project.project_id);
      onProjectUpdate(resp.project);
      setNotice(t('gameWorkshop.rail.buildSuccess', { files: resp.created.join('、') }));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const deleteBatch = async (decomposeBatchId: string) => {
    if (!project) return;
    const next = await gameApi.deleteAssetBatch(project.project_id, decomposeBatchId);
    onProjectUpdate(next);
  };

  if (!project) {
    return <div className="game-rail-empty">{t('gameWorkshop.rail.noProjects')}</div>;
  }

  return (
    <div className="game-rail">
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

      <div className="game-rail-categories" role="tablist">
        {CATEGORIES.map((c) => (
          <button
            key={c.key}
            type="button"
            role="tab"
            aria-pressed={category === c.key}
            className="game-rail-category-tab"
            data-variant={c.key}
            onClick={() => setCategory(c.key)}
            data-testid="game-workshop-rail-category-tab"
          >
            {t(c.labelKey)}
          </button>
        ))}
      </div>

      <div className="game-rail-generate">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t('gameWorkshop.rail.promptPlaceholder')}
          disabled={busy}
          data-testid="game-workshop-rail-prompt-input"
        />
        <button type="button" disabled={busy || !prompt.trim()} onClick={() => void generate()} data-testid="game-workshop-rail-generate-button">
          {busy ? t('gameWorkshop.rail.generating') : t('gameWorkshop.rail.generateButton')}
        </button>
      </div>

      {notice && (
        <div className="game-rail-notice" data-testid="game-workshop-rail-notice">
          {notice}
        </div>
      )}

      <div className="game-rail-asset-grid" data-testid="game-workshop-rail-asset-grid">
        {assetsByCategory[category].length === 0 && (
          <div className="game-rail-empty-hint">{t('gameWorkshop.rail.noAssets')}</div>
        )}
        {assetsByCategory[category].map((asset) => (
          <div
            key={asset.asset_id}
            className="game-rail-asset-item"
            draggable={category !== 'video' && asset.status === 'ready'}
            onDragStart={(e) => handleAssetDragStart(e, asset)}
            title={category !== 'video' ? t('gameWorkshop.rail.dragHint') : undefined}
            data-testid="game-workshop-rail-asset-item"
            data-variant={asset.asset_id}
          >
            {category === 'video' ? (
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

      {decomposedBatches.length > 0 && (
        <div className="game-rail-decomposed" data-testid="game-workshop-rail-decomposed-section">
          <div className="game-rail-decomposed-title">{t('gameWorkshop.rail.decomposedSection')}</div>
          {decomposedBatches.map(([batchId, parts]) => (
            <div key={batchId} className="game-rail-decomposed-batch" data-testid="game-workshop-rail-decomposed-batch" data-variant={batchId}>
              <div className="game-rail-asset-grid">
                {parts.map((part) => (
                  <div key={part.asset_id} className="game-rail-asset-item" data-testid="game-workshop-rail-model-part-item" data-variant={part.asset_id}>
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
  );
}
