/**
 * 游戏模式（游戏工坊）工作区。
 *
 * 按 D-20261116-1047-game-workshop-lab 重构：导航收敛为「创作 / 实验室」
 * 两个标签（原「工作流/素材/场景/代码/日志」下线，见该设计 Q1/D-01）。
 * GameRail（项目列表 + 角色/世界/视频/分解产物素材分类，均可展开/收起，
 * 镜像导演模式 DirectorRail）跨两个标签常驻，只有主面板内容随标签切换。
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gameApi, type GameBridgeStatus, type GameProject } from './gameApi';
import { GameCreationHome } from './GameCreationHome';
import { GameRail } from './GameRail';
import { GameLabCanvas } from './lab/GameLabCanvas';
import { GameLabChatPanel } from './lab/GameLabChatPanel';
import './gamePage.css';

type TabKey = 'create' | 'lab';

export function GamePage() {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<GameProject[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [tab, setTab] = useState<TabKey>('create');
  const [bridge, setBridge] = useState<GameBridgeStatus | null>(null);

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

  const createProject = useCallback(async (name: string) => {
    const project = await gameApi.createProject(name);
    await gameApi.scaffoldProject(project.project_id);
    await refresh();
    setSelectedId(project.project_id);
  }, [refresh]);

  const renameProject = useCallback(async (projectId: string, name: string) => {
    const project = await gameApi.renameProject(projectId, name);
    onProjectUpdate(project);
  }, [onProjectUpdate]);

  const deleteProject = useCallback(async (projectId: string) => {
    await gameApi.deleteProject(projectId);
    await refresh();
  }, [refresh]);

  return (
    <div className="game-page">
      <GameRail
        projects={projects}
        selectedProject={selected}
        onSelectProject={setSelectedId}
        onCreateProject={createProject}
        onRenameProject={renameProject}
        onDeleteProject={deleteProject}
        onProjectUpdate={onProjectUpdate}
        bridge={bridge}
      />

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
        <div className="game-page-tab-content">
          {tab === 'lab' ? (
            selected ? (
              <div className="game-page-lab">
                <div className="game-page-lab-canvas">
                  {/* 助手卡片作为画布的 children 传入，才能站在 GameLabActionsProvider
                      里面拿到 buildFlow（「生成实验室流程图」）。形态与导演模式实验室里的
                      剪辑助手一致：悬浮在画布右上。 */}
                  <GameLabCanvas project={selected} onProjectUpdate={onProjectUpdate}>
                    <GameLabChatPanel project={selected} onProjectUpdate={onProjectUpdate} />
                  </GameLabCanvas>
                </div>
              </div>
            ) : (
              <div className="game-rail-empty">{t('gameWorkshop.rail.noProjects')}</div>
            )
          ) : (
            <GameCreationHome
              projects={projects}
              selectedProject={selected}
              onSelectProject={setSelectedId}
              onCreateProject={createProject}
              onProjectUpdate={onProjectUpdate}
              onOpenLab={() => setTab('lab')}
            />
          )}
        </div>
      </div>
    </div>
  );
}
