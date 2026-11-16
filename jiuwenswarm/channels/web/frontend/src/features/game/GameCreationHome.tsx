/**
 * 游戏工坊 · 创作 tab 主面板——镜像导演模式 CreationHomeTab：生成输入框
 * （GameComposer）之下是"我的项目"卡片网格，点击项目卡片直接进实验室
 * （而不是留在创作页），与 Director 的 ProjectGrid.onSelect 同一个道理：
 * 创作页是"挑项目/建项目"的地方，实验室才是这个项目的工作区。
 *
 * 用 .game-create-scroll 这个独立的、非 flex 子项的滚动容器包住内容，
 * 而不是让 GameComposer 直接当 .game-page-tab-content（display:flex 的行
 * 容器）的子项——那样 GameComposer 没有 flex:1/width:100%，会按
 * shrink-to-fit 收窄成内容宽度，这正是之前"聊天框没有撑满"那个 bug 的
 * 根因。
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GameComposer } from './GameComposer';
import { GameNewProjectDialog } from './GameNewProjectDialog';
import { rawFileUrl } from './lab/nodes/gameRawFileUrl';
import type { GameProject } from './gameApi';
import './gameCreationHome.css';

const plusIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

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

interface GameProjectCardProps {
  project: GameProject;
  onSelect: () => void;
}

function GameProjectCard({ project, onSelect }: GameProjectCardProps) {
  const { t } = useTranslation();
  // model_part 是 .glb 文件，不能当 <img> 渲染——只有角色/世界/视频三类
  // 素材才有可用作封面的媒体文件。
  const readyAssets = project.assets.filter(
    (a) => a.status === 'ready' && a.file_path && (a.type === 'character' || a.type === 'world' || a.type === 'video')
  );
  const cover = readyAssets[readyAssets.length - 1];
  const isVideoCover = cover?.type === 'video';
  const isImageCover = !!cover && !isVideoCover;

  return (
    <div
      className="game-project-card"
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      data-testid="game-workshop-project-card"
      data-variant={project.project_id}
    >
      <div className={`game-project-thumb ${!cover ? 'game-project-thumb--empty' : ''} ${isVideoCover ? 'game-project-thumb--video' : ''}`}>
        {isImageCover && cover.file_path && (
          <img src={rawFileUrl(cover.file_path)} alt={cover.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        )}
        {isVideoCover && cover.file_path && (
          <video
            src={rawFileUrl(cover.file_path)}
            controls
            playsInline
            preload="metadata"
            onClick={(e) => e.stopPropagation()}
            style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#000' }}
          />
        )}
      </div>
      <div className="game-project-meta">
        <div className="game-project-name">{project.name}</div>
        <div className="game-project-sub">
          {t('gameWorkshop.myProjects.assetCount', { count: project.assets.length })} · {relativeTime(t, project.updated_at)}
        </div>
      </div>
    </div>
  );
}

interface GameCreationHomeProps {
  projects: GameProject[];
  selectedProject: GameProject | null;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (name: string) => Promise<void>;
  onProjectUpdate: (next: GameProject) => void;
  onOpenLab: () => void;
}

export function GameCreationHome({
  projects,
  selectedProject,
  onSelectProject,
  onCreateProject,
  onProjectUpdate,
  onOpenLab,
}: GameCreationHomeProps) {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <div className="game-create-scroll">
      {selectedProject && <GameComposer project={selectedProject} onProjectUpdate={onProjectUpdate} />}

      <div className="game-section-header">
        <div className="game-section-title">{t('gameWorkshop.myProjects.title')}</div>
      </div>
      <div className="game-project-grid">
        <button type="button" className="game-project-new" onClick={() => setDialogOpen(true)} data-testid="game-workshop-project-grid-new">
          <span className="game-project-new-icon">{plusIcon}</span>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{t('gameWorkshop.rail.newProjectButton')}</span>
        </button>
        {projects.map((project) => (
          <GameProjectCard
            key={project.project_id}
            project={project}
            onSelect={() => {
              onSelectProject(project.project_id);
              // 点开一个项目就是要开始干活了：直接进实验室，而不是留在创作页
              // 继续看卡片——与导演模式 ProjectGrid.onSelect 同一个道理。
              onOpenLab();
            }}
          />
        ))}
      </div>

      {dialogOpen && <GameNewProjectDialog onCreate={onCreateProject} onClose={() => setDialogOpen(false)} />}
    </div>
  );
}
