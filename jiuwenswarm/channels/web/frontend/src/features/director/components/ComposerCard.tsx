import { useMemo, useRef, useState } from 'react';
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

const imageIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

const ASPECT_OPTIONS = ['16:9', '9:16', '1:1', '4:3'];
const IMAGE_RESOLUTION_OPTIONS = ['512', '768', '1024'];
const VIDEO_RESOLUTION_OPTIONS = ['480p', '720p', '1080p'];
const DURATION_OPTIONS = [5, 10, 15];

interface AtMenuState {
  start: number;
  query: string;
}

/** 光标之前最近的 "@token"（token 内不含空白，"@" 前须是行首或空白）. */
function detectAtToken(value: string, cursor: number): AtMenuState | null {
  const upto = value.slice(0, cursor);
  const atIndex = upto.lastIndexOf('@');
  if (atIndex === -1) return null;
  const query = upto.slice(atIndex + 1);
  if (/\s/.test(query)) return null;
  const before = atIndex > 0 ? upto[atIndex - 1] : '';
  if (before && !/\s/.test(before)) return null;
  return { start: atIndex, query };
}

export function ComposerCard() {
  const { t } = useTranslation();
  const composerMode = useDirectorStore((s) => s.composerMode);
  const composerPrompt = useDirectorStore((s) => s.composerPrompt);
  const composerParams = useDirectorStore((s) => s.composerParams);
  const generating = useDirectorStore((s) => s.generating);
  const pendingGeneration = useDirectorStore((s) => s.pendingGeneration);
  const generateError = useDirectorStore((s) => s.generateError);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const selectedProject = useDirectorStore(
    (s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null
  );
  const setComposerMode = useDirectorStore((s) => s.setComposerMode);
  const setComposerPrompt = useDirectorStore((s) => s.setComposerPrompt);
  const patchComposerParams = useDirectorStore((s) => s.patchComposerParams);
  const generate = useDirectorStore((s) => s.generate);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [atMenu, setAtMenu] = useState<AtMenuState | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);

  const busy = generating || pendingGeneration !== null;
  const canSend = !!selectedProjectId && composerPrompt.trim().length > 0 && !busy;

  const resolutionOptions = composerMode === 'video' ? VIDEO_RESOLUTION_OPTIONS : IMAGE_RESOLUTION_OPTIONS;

  // "@" 引用跨类别：无论当前是图片还是视频生成模式，素材来源永远是该项目
  // "素材 · 图片"分类里已命名、已就绪的图片（见 director_manager.
  // _resolve_at_references）——图片模式最多用 1 个当参考图，视频模式最多用
  // 2 个，按输入顺序分别对应 generate_video 的首帧/尾帧。
  const namedImageNames = useMemo(() => {
    if (!selectedProject) return [];
    const names = selectedProject.assets
      .filter((a) => a.type === 'image' && a.status === 'ready' && a.name)
      .map((a) => a.name as string);
    return Array.from(new Set(names));
  }, [selectedProject]);

  const filteredNames = useMemo(() => {
    if (!atMenu) return [];
    const query = atMenu.query.toLowerCase();
    return namedImageNames.filter((name) => name.toLowerCase().includes(query));
  }, [atMenu, namedImageNames]);

  const closeAtMenu = () => setAtMenu(null);

  const insertAtName = (name: string) => {
    if (!atMenu) return;
    const before = composerPrompt.slice(0, atMenu.start);
    const after = composerPrompt.slice(atMenu.start + 1 + atMenu.query.length);
    const inserted = `@${name} `;
    setComposerPrompt(before + inserted + after);
    closeAtMenu();
    requestAnimationFrame(() => {
      const pos = before.length + inserted.length;
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(pos, pos);
      }
    });
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setComposerPrompt(value);
    const cursor = e.target.selectionStart ?? value.length;
    setAtMenu(detectAtToken(value, cursor));
    setHighlightIndex(0);
  };

  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!atMenu || filteredNames.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIndex((i) => (i + 1) % filteredNames.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIndex((i) => (i - 1 + filteredNames.length) % filteredNames.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      insertAtName(filteredNames[highlightIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAtMenu();
    }
  };

  return (
    <div className="director-composer">
      <ModeSegmentedControl mode={composerMode} onChange={setComposerMode} />

      <div className="director-composer-textarea-wrap">
        <textarea
          ref={textareaRef}
          className="director-composer-textarea"
          placeholder={
            composerMode === 'video' ? t('director.composer.placeholderVideo') : t('director.composer.placeholderImage')
          }
          value={composerPrompt}
          onChange={handleTextareaChange}
          onKeyDown={handleTextareaKeyDown}
          onBlur={() => {
            // 延迟关闭，留出时间让下拉项的 onMouseDown 先触发选中。
            window.setTimeout(closeAtMenu, 150);
          }}
        />
        {atMenu && filteredNames.length > 0 && (
          <div className="director-at-menu">
            {filteredNames.map((name, i) => (
              <button
                key={name}
                type="button"
                className={`director-at-menu-item ${i === highlightIndex ? 'director-at-menu-item--active' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertAtName(name);
                }}
                onMouseEnter={() => setHighlightIndex(i)}
              >
                {imageIcon}
                <span>@{name}</span>
              </button>
            ))}
          </div>
        )}
      </div>

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
