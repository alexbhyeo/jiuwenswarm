import { useEffect, useState } from 'react';
import { useDirectorStore } from '../directorStore';
import { ComposerCard } from './ComposerCard';
import { DirectorTabs } from './DirectorTabs';
import { NewProjectDialog } from './NewProjectDialog';
import { ProjectGrid } from './ProjectGrid';
import { QuickStartChips } from './QuickStartChips';

export function CreationHomeTab() {
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
      <div style={{ margin: '0 0 24px' }}>
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      <ComposerCard />
      <QuickStartChips />
      <ProjectGrid projects={projects} onNewProject={() => setDialogOpen(true)} />

      {dialogOpen && <NewProjectDialog onClose={() => setDialogOpen(false)} />}
    </div>
  );
}
