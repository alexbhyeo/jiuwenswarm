/**
 * 游戏模式（游戏工坊）工作区。
 *
 * 按 D-20261116-1047-game-workshop-lab 重构：导航收敛为「创作 / 实验室」
 * 两个标签（原「工作流/素材/场景/代码/日志」下线，见该设计 Q1/D-01）。
 * 左侧项目栏（新建项目 + 项目列表）保留，不属于某一个标签，两个标签共用
 * 同一个已选中项目。
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gameApi, type GameBridgeStatus, type GameProject } from './gameApi';
import { GameRail } from './GameRail';
import { GameLabCanvas } from './lab/GameLabCanvas';
import './gamePage.css';

type TabKey = 'create' | 'lab';

export function GamePage() {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<GameProject[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [tab, setTab] = useState<TabKey>('create');
  const [bridge, setBridge] = useState<GameBridgeStatus | null>(null);
  const [draftName, setDraftName] = useState('');
  const [busy, setBusy] = useState(false);

  const selected = projects.find((p) => p.project_id === selectedId) ?? null;

  const refresh = useCallback(async () => {
    const list = await gameApi.listProjects();
    setProjects(list);
    setSelectedId((current) => (current && list.some((p) => p.project_id === current) ? current : (list[0]?.project_id ?? '')));
  }, []);

  useEffect(() => {
    void refresh();
    void gameApi.bridgeStatus().then(setBridge).catch(() => setBridge(null));
  }, [refresh]);

  const onProjectUpdate = useCallback((next: GameProject) => {
    setProjects((prev) => prev.map((p) => (p.project_id === next.project_id ? next : p)));
  }, []);

  const createProject = async () => {
    const name = draftName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const project = await gameApi.createProject(name);
      await gameApi.scaffoldProject(project.project_id);
      setDraftName('');
      await refresh();
      setSelectedId(project.project_id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="game-page">
      <aside className="game-page-sidebar">
        <div className="game-page-sidebar-card">
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void createProject();
            }}
            placeholder={t('gameWorkshop.rail.newProjectPlaceholder')}
            data-testid="game-workshop-new-project-input"
          />
          <button
            type="button"
            onClick={() => void createProject()}
            disabled={busy || !draftName.trim()}
            data-testid="game-workshop-new-project-button"
          >
            {t('gameWorkshop.rail.newProjectButton')}
          </button>
        </div>
        <div className="game-page-sidebar-list">
          <div className="game-page-sidebar-count">{t('gameWorkshop.rail.projectsCount', { count: projects.length })}</div>
          {projects.length === 0 && <div className="game-page-sidebar-empty">{t('gameWorkshop.rail.noProjects')}</div>}
          {projects.map((p) => (
            <button
              key={p.project_id}
              type="button"
              className="game-page-sidebar-item"
              aria-pressed={p.project_id === selectedId}
              onClick={() => setSelectedId(p.project_id)}
              data-testid="game-workshop-project-list-item"
              data-variant={p.project_id}
            >
              <div className="game-page-sidebar-item-name">{p.name}</div>
              <div className="game-page-sidebar-item-meta">
                {p.status} · {p.assets.length}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <div className="game-page-main">
        <div className="game-page-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-pressed={tab === 'create'}
            onClick={() => setTab('create')}
            data-testid="game-workshop-tab"
            data-variant="create"
          >
            {t('gameWorkshop.tabs.create')}
          </button>
          <button
            type="button"
            role="tab"
            aria-pressed={tab === 'lab'}
            onClick={() => setTab('lab')}
            data-testid="game-workshop-tab"
            data-variant="lab"
          >
            {t('gameWorkshop.tabs.lab')}
          </button>
        </div>
        {/* GameRail 跨两个 tab 常驻（镜像 DirectorPage.tsx 里 DirectorRail 常驻、
            只切换右侧主内容的布局）——实验室拖拽的素材来源就是这个 rail，
            放进 tab 互斥分支会导致切到实验室时 rail 卸载、拖拽源不存在。 */}
        <div className={`game-page-tab-content${tab === 'lab' ? ' game-page-tab-content--lab' : ''}`}>
          <GameRail project={selected} onProjectUpdate={onProjectUpdate} bridge={bridge} />
          {tab === 'lab' &&
            (selected ? (
              <GameLabCanvas project={selected} onProjectUpdate={onProjectUpdate} />
            ) : (
              <div className="game-rail-empty">{t('gameWorkshop.rail.noProjects')}</div>
            ))}
        </div>
      </div>
    </div>
  );
}
