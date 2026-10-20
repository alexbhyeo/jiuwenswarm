import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { LabCanvas } from '../lab/LabCanvas';
import { DirectorTabs } from './DirectorTabs';

export function LabTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);

  return (
    <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="director-lab-topbar">
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      {selectedProjectId ? (
        <LabCanvas key={selectedProjectId} />
      ) : (
        <div className="director-lab-canvas" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div className="director-empty-hint">{t('director.lab.noProject')}</div>
        </div>
      )}
    </div>
  );
}
