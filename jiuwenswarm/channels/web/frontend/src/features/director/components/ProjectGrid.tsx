import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import type { DirectorProject } from '../types';
import { ProjectCard } from './ProjectCard';

interface ProjectGridProps {
  projects: DirectorProject[];
  onNewProject: () => void;
}

const plusIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export function ProjectGrid({ projects, onNewProject }: ProjectGridProps) {
  const { t } = useTranslation();
  const selectProject = useDirectorStore((s) => s.selectProject);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);

  return (
    <>
      <div className="director-section-header">
        <div className="director-section-title" style={{ marginBottom: 0 }}>
          {t('director.myProjects.title')}
        </div>
      </div>
      <div className="director-project-grid">
        <button type="button" className="director-project-new" onClick={onNewProject}>
          <span className="director-project-new-icon">{plusIcon}</span>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{t('director.newProject')}</span>
        </button>
        {projects.map((project) => (
          <ProjectCard
            key={project.project_id}
            project={project}
            onSelect={() => {
              selectProject(project.project_id);
              // 点开一个项目就是要开始干活了：直接进「实验室」，而不是留在
              // 创作页继续看卡片——创作页是「挑项目／建项目」的地方，实验室
              // 才是这个项目的工作区。项目数据（含 lab_nodes/lab_edges）已经
              // 由 CreationHomeTab 的 loadProjects 拿在手上，切过去不会空一帧。
              setActiveTab('lab');
            }}
          />
        ))}
      </div>
    </>
  );
}
