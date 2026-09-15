import { useTranslation } from 'react-i18next';
import type { DirectorTabKey } from '../types';

interface DirectorTabsProps {
  activeTab: DirectorTabKey;
  onChange: (tab: DirectorTabKey) => void;
}

const labIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 2h6M10 2v6.2L4.8 18a1.8 1.8 0 0 0 1.6 2.6h11.2a1.8 1.8 0 0 0 1.6-2.6L14 8.2V2" />
  </svg>
);

const editIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
    <path d="m9 6-6 6 6 6M15 6l6 6-6 6" />
  </svg>
);

const TABS: Array<{ key: DirectorTabKey; icon?: React.ReactNode }> = [
  { key: 'create' },
  { key: 'lab', icon: labIcon },
  { key: 'edit', icon: editIcon },
];

export function DirectorTabs({ activeTab, onChange }: DirectorTabsProps) {
  const { t } = useTranslation();
  return (
    <div className="director-tabs">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          type="button"
          className={`director-tab ${activeTab === tab.key ? 'director-tab--active' : ''}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.icon}
          {t(`director.tabs.${tab.key}`)}
        </button>
      ))}
    </div>
  );
}
