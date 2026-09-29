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
        {/* key 强制在切换项目时整个重新挂载——EditTabShell 的时间线状态
            按项目 ID 存在 directorStore.editTimelineByProject 里（见
            EditTabShell.tsx），没有这个 key 的话切换项目但留在 剪辑 tab
            会显示上一个项目的时间线，直到手动切一次 tab 才刷新过来。 */}
        {activeTab === 'edit' && <EditTabShell key={selectedProject?.project_id ?? 'none'} />}
      </div>
      {railDialogOpen && <NewProjectDialog onClose={() => setRailDialogOpen(false)} />}
    </div>
  );
}
