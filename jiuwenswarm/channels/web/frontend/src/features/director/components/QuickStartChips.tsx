import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';

const videoIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="2.5" y="6" width="14" height="12" rx="2" />
    <path d="m16.5 10 5-3v10l-5-3z" />
  </svg>
);

const imageIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

const CHIPS: Array<{ key: string; icon: React.ReactNode; mode?: 'video' | 'image'; tab?: 'lab' }> = [
  { key: 'textToVideo', icon: videoIcon, mode: 'video' },
  { key: 'imageToVideo', icon: videoIcon, mode: 'video' },
  { key: 'multiRef', icon: imageIcon, tab: 'lab' },
  { key: 'textToImage', icon: imageIcon, mode: 'image' },
];

export function QuickStartChips() {
  const { t } = useTranslation();
  const setComposerMode = useDirectorStore((s) => s.setComposerMode);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);

  return (
    <div className="director-quickstart">
      {CHIPS.map((chip) => (
        <button
          key={chip.key}
          type="button"
          className="director-chip"
          onClick={() => (chip.tab ? setActiveTab(chip.tab) : setComposerMode(chip.mode!))}
        >
          {chip.icon}
          {t(`director.quickstart.${chip.key}`)}
        </button>
      ))}
    </div>
  );
}
