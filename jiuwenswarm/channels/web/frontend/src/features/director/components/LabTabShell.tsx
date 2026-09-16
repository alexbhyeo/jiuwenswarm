import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { LabCanvas } from '../lab/LabCanvas';
import { DirectorTabs } from './DirectorTabs';

const backIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="m15 18-6-6 6-6" />
  </svg>
);

export function LabTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="director-lab-topbar">
        <div className="director-lab-breadcrumb">
          <button type="button" className="director-lab-back" onClick={() => setActiveTab('create')}>
            {backIcon}
          </button>
          <span className="director-lab-title">{t('director.lab.breadcrumb')}</span>
        </div>
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
