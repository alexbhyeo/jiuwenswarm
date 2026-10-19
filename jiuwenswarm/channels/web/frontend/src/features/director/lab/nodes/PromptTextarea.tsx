import { useEffect, useMemo, useRef, useState } from 'react';
import type { DirectorAsset } from '../../types';
import { detectAtToken, type AtMenuState } from '../atMention';

const imageIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

interface PromptTextareaProps {
  value: string;
  onChange: (value: string) => void;
  namedImageAssets: DirectorAsset[];
  placeholder?: string;
  textareaClassName: string;
  autoFocus?: boolean;
  onBlur?: () => void;
  'data-testid'?: string;
}

/** 带 "@名称" 自动补全下拉的文本框——TextNode 的编辑态、ProcessNode 卡片
 *  自带的文字提示弹窗共用这一份：组字期间本地缓冲（见 onCompositionEnd
 *  注释）、"@" 菜单的检测/过滤/键盘导航/点选插入，两处需求完全一致，没必
 *  要各自维护一份 ~80 行的重复实现。 */
export function PromptTextarea({
  value,
  onChange,
  namedImageAssets,
  placeholder,
  textareaClassName,
  autoFocus,
  onBlur,
  'data-testid': testId,
}: PromptTextareaProps) {
  // 本地缓冲输入值，只有在没有正在进行的输入法组字（IME composition）时才
  // 把值提交给调用方。敲中文期间如果受控 value 被外部重新赋值会打断正在
  // 进行中的组字——表现为中文打不出来或者输入错乱，见 TextNode 原来的注释。
  const [localText, setLocalText] = useState(value);
  const composingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [atMenu, setAtMenu] = useState<AtMenuState | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);

  useEffect(() => {
    if (!composingRef.current) setLocalText(value);
  }, [value]);

  const filteredNames = useMemo(() => {
    if (!atMenu) return [];
    const query = atMenu.query.toLowerCase();
    return namedImageAssets.filter((a) => (a.name as string).toLowerCase().includes(query));
  }, [atMenu, namedImageAssets]);

  const closeAtMenu = () => setAtMenu(null);

  const insertAtName = (asset: DirectorAsset) => {
    if (!atMenu || !asset.name) return;
    const before = localText.slice(0, atMenu.start);
    const after = localText.slice(atMenu.start + 1 + atMenu.query.length);
    const inserted = `@${asset.name} `;
    const next = before + inserted + after;
    setLocalText(next);
    onChange(next);
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

  return (
    <div className="director-at-menu-anchor">
      <textarea
        ref={textareaRef}
        className={textareaClassName}
        value={localText}
        placeholder={placeholder}
        autoFocus={autoFocus}
        data-testid={testId}
        onChange={(e) => {
          const next = e.target.value;
          setLocalText(next);
          if (!composingRef.current) onChange(next);
          const cursor = e.target.selectionStart ?? next.length;
          setAtMenu(detectAtToken(next, cursor));
          setHighlightIndex(0);
        }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={(e) => {
          composingRef.current = false;
          onChange((e.target as HTMLTextAreaElement).value);
        }}
        onKeyDown={(e) => {
          if (atMenu && filteredNames.length > 0) {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setHighlightIndex((i) => (i + 1) % filteredNames.length);
              return;
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault();
              setHighlightIndex((i) => (i - 1 + filteredNames.length) % filteredNames.length);
              return;
            }
            if (e.key === 'Enter') {
              e.preventDefault();
              insertAtName(filteredNames[highlightIndex]);
              return;
            }
            if (e.key === 'Escape') {
              e.preventDefault();
              closeAtMenu();
            }
          }
        }}
        onBlur={() => {
          // 延迟关闭：下拉选项的 onMouseDown 已经 preventDefault 保住了焦点，
          // 这里的延时只是兜底别的边界情况。
          window.setTimeout(() => {
            closeAtMenu();
            onBlur?.();
          }, 150);
        }}
      />
      {atMenu && filteredNames.length > 0 && (
        <div className="director-at-menu nodrag" data-testid="director-prompt-textarea-at-menu">
          {filteredNames.map((asset, i) => (
            <button
              key={asset.asset_id}
              type="button"
              className={`director-at-menu-item ${i === highlightIndex ? 'director-at-menu-item--active' : ''}`}
              onMouseDown={(e) => {
                e.preventDefault();
                insertAtName(asset);
              }}
              onMouseEnter={() => setHighlightIndex(i)}
            >
              {imageIcon}
              <span>@{asset.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
