import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../../directorStore';
import { useLabActions } from '../LabActionsContext';
import type { TextNodeData } from '../labTypes';
import type { DirectorAsset } from '../../types';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

const imageIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

interface AtMenuState {
  start: number;
  query: string;
}

/** 光标之前最近的 "@token"（token 内不含空白，"@" 前须是行首或空白）——
 *  跟 ComposerCard / EditChatPanel 里的同名函数逻辑一致，三处独立复制而
 *  不提取共享工具：各自调用点都很轻量，没必要为此新增一层抽象。 */
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

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 转义正则特殊字符，构造"@名称1|名称2|…"联合正则时名称本身可能含有
 *  正则元字符（圆括号/点号等）。 */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 把 "@名称" 边界的判定规则跟后端 handle_director_generate 的
 *  prompt 清洗逻辑（director_manager.py）对齐：不按 \S+ 切词（中文场景
 *  "@道具名放在@场景名上"没有空格分隔），而是直接用项目里已有的素材名称
 *  构造 "@(名称1|名称2|…)" 联合正则，名称按长度降序排列，保证互为前缀的
 *  两个名称优先命中更长的那个。只有图片/角色类型的就绪素材才渲染缩略图
 *  chip——命中了别的类型（比如视频）的素材名也算一次合法引用边界，但
 *  只按纯文本展示，不是这个节点能当参考图用的东西。 */
function renderWithImageChips(text: string, namedAssets: DirectorAsset[]): React.ReactNode {
  if (!text || namedAssets.length === 0) return text;
  const byName = new Map<string, DirectorAsset>();
  for (const asset of namedAssets) {
    if (asset.name) byName.set(asset.name, asset);
  }
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`@(${names.map(escapeRegExp).join('|')})`, 'g');

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    const asset = byName.get(match[1]);
    const canShowThumb = asset && (asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.file_path;
    if (canShowThumb && asset) {
      nodes.push(
        <span key={`chip-${key++}`} className="lab-node-text-chip">
          <img src={rawFileUrl(asset.file_path as string)} alt={asset.name ?? ''} />
          {match[0]}
        </span>
      );
    } else {
      nodes.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export function TextNode({ id, data }: NodeProps & { data: TextNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const project = useDirectorStore((s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null);

  // 本地缓冲输入值，只有在没有正在进行的输入法组字（IME composition）时才
  // 把值同步给 React Flow 的节点状态。原来的写法是每敲一个键就直接把值写
  // 回受控的 textarea（数据经过整张画布的节点数组走一圈），拼音输入法组字
  // 中途 React 把 DOM 的 value 重新赋值回去，会打断正在进行中的组字——
  // 表现为中文打不出来或者输入错乱。缓冲之后，组字过程只更新本地 state，
  // 敲完这一个字（compositionend）才提交给节点数据，就不会再打断输入法了。
  const [localText, setLocalText] = useState(data.text);
  const composingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 默认展示"渲染态"（@名称 带缩略图），点一下才切成可编辑的原始文本——
  // 跟 textarea 没法同时又能编辑又能在文字中间插入图片，两种状态只能二选一，
  // 不常在编辑的时候最适合用渲染态占着。
  const [focused, setFocused] = useState(false);
  const [atMenu, setAtMenu] = useState<AtMenuState | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);

  useEffect(() => {
    if (!composingRef.current) setLocalText(data.text);
  }, [data.text]);

  const namedAssets = useMemo(() => project?.assets.filter((a) => a.name) ?? [], [project]);
  const rendered = useMemo(() => renderWithImageChips(localText, namedAssets), [localText, namedAssets]);

  // "@" 下拉只给图片/角色参考用——引用一段视频/音频素材对这个文本节点
  // 没有意义（它只喂给 process 卡的 image1/image2 端口），跟 EditChatPanel
  // 的 namedImageAssets 同一个过滤口径，按名称去重。
  const namedImageAssets = useMemo(() => {
    const seen = new Set<string>();
    const result: DirectorAsset[] = [];
    for (const asset of namedAssets) {
      if ((asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.name && !seen.has(asset.name)) {
        seen.add(asset.name);
        result.push(asset);
      }
    }
    return result;
  }, [namedAssets]);

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
    actions.updateText(id, next);
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
    <div className="lab-node lab-node--text">
      <div className="lab-node-label">
        {t('director.lab.textPrompt')}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>
      {focused ? (
        <div className="lab-node-text-edit-wrap">
          <textarea
            ref={textareaRef}
            className="lab-node-textarea nodrag"
            value={localText}
            placeholder={t('director.lab.samplePrompt')}
            autoFocus
            onChange={(e) => {
              const value = e.target.value;
              setLocalText(value);
              if (!composingRef.current) actions.updateText(id, value);
              const cursor = e.target.selectionStart ?? value.length;
              setAtMenu(detectAtToken(value, cursor));
              setHighlightIndex(0);
            }}
            onCompositionStart={() => {
              composingRef.current = true;
            }}
            onCompositionEnd={(e) => {
              composingRef.current = false;
              actions.updateText(id, (e.target as HTMLTextAreaElement).value);
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
              // 这里的延时只是兜底别的边界情况——跟 EditChatPanel 同一个写法。
              window.setTimeout(() => {
                closeAtMenu();
                setFocused(false);
              }, 150);
            }}
          />
          {atMenu && filteredNames.length > 0 && (
            <div className="director-at-menu nodrag" data-testid="lab-node-text-at-menu">
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
      ) : (
        <div
          className="lab-node-text-display nodrag"
          data-testid="lab-node-text-display"
          onClick={() => setFocused(true)}
        >
          {localText ? rendered : <span className="lab-node-text-placeholder">{t('director.lab.samplePrompt')}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} id="text" />
    </div>
  );
}
