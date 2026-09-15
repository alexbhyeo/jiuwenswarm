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
  const selectedProject = useDirectorStore(
    (s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null
  );
  const loadProjects = useDirectorStore((s) => s.loadProjects);
  const selectProject = useDirectorStore((s) => s.selectProject);
  const [railDialogOpen, setRailDialogOpen] = useState(false);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  return (
    <div className="director-page">
      <DirectorRail
        projects={projects}
        selectedProject={selectedProject}
        onNewProject={() => setRailDialogOpen(true)}
        onSelectProject={selectProject}
      />
      <div className="director-detail">
        {activeTab === 'create' && <CreationHomeTab />}
        {activeTab === 'lab' && <LabTabShell />}
        {activeTab === 'edit' && <EditTabShell />}
      </div>
      {railDialogOpen && <NewProjectDialog onClose={() => setRailDialogOpen(false)} />}
    </div>
  );
}
