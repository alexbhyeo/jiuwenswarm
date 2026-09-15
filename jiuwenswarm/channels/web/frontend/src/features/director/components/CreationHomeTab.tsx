import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { ComposerCard } from './ComposerCard';
import { DirectorTabs } from './DirectorTabs';
import { NewProjectDialog } from './NewProjectDialog';
import { ProjectGrid } from './ProjectGrid';
import { QuickStartChips } from './QuickStartChips';
import { TemplateGallery } from './TemplateGallery';

export function CreationHomeTab() {
  const { t } = useTranslation();
  const projects = useDirectorStore((s) => s.projects);
  const loadProjects = useDirectorStore((s) => s.loadProjects);
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  return (
    <div className="director-detail-scroll">
      <div className="director-header" style={{ marginBottom: 0 }}>
        <div>
          <div className="director-header-title">{t('director.title')}</div>
          <div className="director-header-subtitle">{t('director.subtitle')}</div>
        </div>
      </div>

      <div style={{ margin: '24px 0 24px' }}>
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      <ComposerCard />
      <QuickStartChips />
      <ProjectGrid projects={projects} onNewProject={() => setDialogOpen(true)} />
      <TemplateGallery />

      {dialogOpen && <NewProjectDialog onClose={() => setDialogOpen(false)} />}
    </div>
  );
}
