import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { ModeSegmentedControl } from './ModeSegmentedControl';
import { ParamPillDropdown } from './ParamPillDropdown';

const attachIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
    <path d="M14 5v4M12 7h4" strokeWidth={1.6} />
  </svg>
);

const sendIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

const ASPECT_OPTIONS = ['16:9', '9:16', '1:1', '4:3'];
const IMAGE_RESOLUTION_OPTIONS = ['512', '768', '1024'];
const VIDEO_RESOLUTION_OPTIONS = ['480p', '720p', '1080p'];
const DURATION_OPTIONS = [5, 10, 15];

export function ComposerCard() {
  const { t } = useTranslation();
  const composerMode = useDirectorStore((s) => s.composerMode);
  const composerPrompt = useDirectorStore((s) => s.composerPrompt);
  const composerParams = useDirectorStore((s) => s.composerParams);
  const generating = useDirectorStore((s) => s.generating);
  const pendingGeneration = useDirectorStore((s) => s.pendingGeneration);
  const generateError = useDirectorStore((s) => s.generateError);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const setComposerMode = useDirectorStore((s) => s.setComposerMode);
  const setComposerPrompt = useDirectorStore((s) => s.setComposerPrompt);
  const patchComposerParams = useDirectorStore((s) => s.patchComposerParams);
  const generate = useDirectorStore((s) => s.generate);

  const busy = generating || pendingGeneration !== null;
  const canSend = !!selectedProjectId && composerPrompt.trim().length > 0 && !busy;

  const resolutionOptions = composerMode === 'video' ? VIDEO_RESOLUTION_OPTIONS : IMAGE_RESOLUTION_OPTIONS;

  return (
    <div className="director-composer">
      <ModeSegmentedControl mode={composerMode} onChange={setComposerMode} />

      <textarea
        className="director-composer-textarea"
        placeholder={
          composerMode === 'video' ? t('director.composer.placeholderVideo') : t('director.composer.placeholderImage')
        }
        value={composerPrompt}
        onChange={(e) => setComposerPrompt(e.target.value)}
      />

      <div className="director-composer-toolbar">
        <div className="director-composer-toolbar-left">
          <button type="button" className="director-attach-btn" disabled title={t('director.comingSoon')}>
            {attachIcon}
          </button>
          <div className="director-toolbar-divider" />
          <ParamPillDropdown
            value={composerParams.aspectRatio}
            label={composerParams.aspectRatio}
            options={ASPECT_OPTIONS.map((v) => ({ value: v, label: v }))}
            onChange={(v) => patchComposerParams({ aspectRatio: v })}
          />
          <ParamPillDropdown
            value={composerParams.resolution}
            label={composerParams.resolution}
            options={resolutionOptions.map((v) => ({ value: v, label: v }))}
            onChange={(v) => patchComposerParams({ resolution: v })}
          />
          {composerMode === 'video' && (
            <ParamPillDropdown
              value={String(composerParams.durationSeconds)}
              label={t('director.composer.durationLabel', { seconds: composerParams.durationSeconds })}
              options={DURATION_OPTIONS.map((v) => ({
                value: String(v),
                label: t('director.composer.durationLabel', { seconds: v }),
              }))}
              onChange={(v) => patchComposerParams({ durationSeconds: Number(v) })}
            />
          )}
        </div>
        <button
          type="button"
          className="director-send-btn"
          disabled={!canSend}
          onClick={() => generate()}
          title={t('director.composer.send')}
        >
          {sendIcon}
        </button>
      </div>

      {generateError && <div className="director-generate-error">{generateError}</div>}
    </div>
  );
}
