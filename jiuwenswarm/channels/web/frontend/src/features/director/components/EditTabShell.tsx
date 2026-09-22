import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { DirectorTabs } from './DirectorTabs';

const backIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="m15 18-6-6 6-6" />
  </svg>
);

const saveIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
    <path d="M17 21v-8H7v8M7 3v5h8" />
  </svg>
);

const importIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v8M8 12h8" />
  </svg>
);

const playIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="#fff">
    <path d="M8 5v14l11-7z" />
  </svg>
);

const RULER_MARKS = ['00:00.00', '00:00.20', '00:00.40', '00:00.60', '00:00.80', '00:01.00', '00:01.20', '00:01.40', '00:01.60', '00:01.80', '00:02.00', '00:02.20'];

export function EditTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);

  return (
    <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="director-lab-topbar">
        <div className="director-lab-breadcrumb">
          <button type="button" className="director-lab-back" onClick={() => setActiveTab('create')}>
            {backIcon}
          </button>
          <span className="director-lab-title">{t('director.edit.breadcrumb')}</span>
          <span className="director-coming-soon-badge">{t('director.comingSoon')}</span>
        </div>
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      <div className="director-edit-subheader">
        <span className="director-edit-subheader-title">{t('director.edit.timelineEditor')}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button type="button" className="director-edit-btn director-edit-btn--primary" disabled title={t('director.comingSoon')}>
            {saveIcon}
            {t('director.edit.saveToProject')}
          </button>
          <button type="button" className="director-edit-btn director-edit-btn--secondary" onClick={() => setActiveTab('create')}>
            {t('director.edit.exit')}
          </button>
        </div>
      </div>

      <div className="director-edit-body">
        <div className="director-edit-stage">
          <div className="director-edit-dropzone">
            <div className="director-edit-dropzone-title">
              {importIcon}
              {t('director.edit.importMedia')}
            </div>
            <div className="director-edit-dropzone-hint">{t('director.edit.importHint')}</div>
          </div>
        </div>

        <div className="director-edit-timeline">
          <div className="director-edit-toolbar-row">
            <div className="director-edit-toolbar-left">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button type="button" style={{ width: 28, height: 28, borderRadius: '9999px', background: 'var(--d-accent)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }} disabled>
                {playIcon}
              </button>
              <span style={{ fontSize: 12.5, color: 'var(--d-text-secondary)', fontVariantNumeric: 'tabular-nums' }}>00:00:00</span>
            </div>
            <div style={{ width: 90 }} />
          </div>
          <div className="director-edit-ruler">
            {RULER_MARKS.map((mark) => (
              <span key={mark}>{mark}</span>
            ))}
          </div>
          <div className="director-edit-track">
            <span className="director-edit-track-hint">{t('director.edit.trackHint')}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
