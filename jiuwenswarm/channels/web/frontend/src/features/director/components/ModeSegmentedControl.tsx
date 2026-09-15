import { useTranslation } from 'react-i18next';
import type { ComposerMode } from '../types';
import { ENABLED_COMPOSER_MODES } from '../types';

interface ModeSegmentedControlProps {
  mode: ComposerMode;
  onChange: (mode: ComposerMode) => void;
}

const ALL_MODES: ComposerMode[] = ['video', 'image', 'audio', 'character', 'world'];

export function ModeSegmentedControl({ mode, onChange }: ModeSegmentedControlProps) {
  const { t } = useTranslation();
  return (
    <div className="director-composer-modes">
      {ALL_MODES.map((m) => {
        const enabled = ENABLED_COMPOSER_MODES.includes(m);
        return (
          <button
            key={m}
            type="button"
            disabled={!enabled}
            className={`director-composer-mode ${mode === m ? 'director-composer-mode--active' : ''} ${
              !enabled ? 'director-composer-mode--disabled' : ''
            }`}
            onClick={() => enabled && onChange(m)}
          >
            {t(`director.composer.modes.${m}`)}
          </button>
        );
      })}
    </div>
  );
}
