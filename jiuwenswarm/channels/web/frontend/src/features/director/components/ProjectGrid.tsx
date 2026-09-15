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
          <ProjectCard key={project.project_id} project={project} onSelect={() => selectProject(project.project_id)} />
        ))}
      </div>
    </>
  );
}
