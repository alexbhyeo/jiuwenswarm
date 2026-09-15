import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { DirectorTabs } from './DirectorTabs';

const backIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="m15 18-6-6 6-6" />
  </svg>
);

const cursorIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 7V4h3M20 7V4h-3M4 17v3h3M20 17v3h-3" />
  </svg>
);

const imageIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

const videoIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="2.5" y="6" width="14" height="12" rx="2" />
    <path d="m16.5 10 5-3v10l-5-3z" />
  </svg>
);

const plusIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

const generateIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M13 2 4 14h6l-1 8 9-12h-6z" />
  </svg>
);

const chevronDown = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="m9 6 6 6-6 6" />
  </svg>
);

const REFERENCE_NODES = [
  { labelKey: 'director.lab.refImage', gradient: 'linear-gradient(160deg,#bfe0fb,#5fa8ea 60%,#e8f4ff)', height: 190, top: 70 },
  { labelKey: 'director.lab.refImage', gradient: 'linear-gradient(180deg,#bcd9f7,#1c4f8a)', height: 110, top: 300 },
  { labelKey: 'director.lab.refImage', gradient: 'linear-gradient(135deg,#e8effd,#c3d7fb 40%,#a9c98b)', height: 110, top: 450 },
];

export function LabTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="director-lab-topbar">
        <div className="director-lab-breadcrumb">
          <button type="button" className="director-lab-back" onClick={() => setActiveTab('create')}>
            {backIcon}
          </button>
          <span className="director-lab-title">{t('director.lab.breadcrumb')}</span>
          <span className="director-coming-soon-badge">{t('director.comingSoon')}</span>
        </div>
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      <div className="director-lab-canvas">
        <div className="director-lab-toolrail">
          <button type="button" className="director-lab-tool-btn" style={{ background: 'var(--d-send-active)', borderRadius: 'var(--d-radius-full)', width: 34, height: 34, color: '#fff' }}>
            {plusIcon}
          </button>
          <div className="director-lab-tool-divider" />
          <button type="button" className="director-lab-tool-btn">{imageIcon}</button>
          <button type="button" className="director-lab-tool-btn">{videoIcon}</button>
          <button type="button" className="director-lab-tool-btn">{cursorIcon}</button>
        </div>

        {REFERENCE_NODES.map((node, idx) => (
          <div key={idx} className="director-lab-node" style={{ left: 200, top: node.top }}>
            <div className="director-lab-node-label">
              {t(node.labelKey)} {idx + 1}
            </div>
            <div className="director-lab-node-thumb" style={{ height: node.height, background: node.gradient }} />
          </div>
        ))}

        <div className="director-lab-node" style={{ left: 200, top: 600 }}>
          <div className="director-lab-node-label">{t('director.lab.textPrompt')}</div>
          <div className="director-lab-node-text">{t('director.lab.samplePrompt')}</div>
        </div>

        <div className="director-lab-panel">
          <div className="director-lab-panel-title">
            <span>{t('director.lab.settingsTitle')}</span>
          </div>
          <div className="director-lab-panel-rows">
            <div className="director-lab-panel-row">
              <span className="director-lab-panel-row-label">{t('director.lab.model')}</span>
              <span className="director-lab-panel-row-value">gemini-3.1-flash-image {chevronDown}</span>
            </div>
            <div className="director-lab-panel-row">
              <span className="director-lab-panel-row-label">{t('director.lab.refMode')}</span>
              <span className="director-lab-panel-row-value">{t('director.lab.refModeValue')} {chevronDown}</span>
            </div>
            <div className="director-lab-panel-row">
              <span className="director-lab-panel-row-label">{t('director.lab.genParams')}</span>
              <span className="director-lab-panel-row-value">{t('director.lab.genParamsValue')} {chevronDown}</span>
            </div>
          </div>
          <div className="director-lab-panel-row">
            <span style={{ color: 'var(--d-accent)', fontSize: 11.5 }}>{t('director.comingSoon')}</span>
            <button type="button" className="director-lab-generate-btn" disabled title={t('director.comingSoon')}>
              {generateIcon}
              {t('director.lab.generate')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
