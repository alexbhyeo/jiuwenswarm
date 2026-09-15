import { useEffect, useState } from 'react';
import { useDirectorStore } from './directorStore';
import { DirectorRail } from './components/DirectorRail';
import { CreationHomeTab } from './components/CreationHomeTab';
import { LabTabShell } from './components/LabTabShell';
import { EditTabShell } from './components/EditTabShell';
import { NewProjectDialog } from './components/NewProjectDialog';
import './styles/director.css';

export function DirectorPage() {
  const activeTab = useDirectorStore((s) => s.activeTab);
  const projects = useDirectorStore((s) => s.projects);
  const assetCounts = useDirectorStore((s) => s.assetCounts);
  const loadProjects = useDirectorStore((s) => s.loadProjects);
  const [railDialogOpen, setRailDialogOpen] = useState(false);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  return (
    <div className="director-page">
      <DirectorRail projects={projects} assetCounts={assetCounts} onNewProject={() => setRailDialogOpen(true)} />
      <div className="director-detail">
        {activeTab === 'create' && <CreationHomeTab />}
        {activeTab === 'lab' && <LabTabShell />}
        {activeTab === 'edit' && <EditTabShell />}
      </div>
      {railDialogOpen && <NewProjectDialog onClose={() => setRailDialogOpen(false)} />}
    </div>
  );
}
