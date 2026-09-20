import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import type { DirectorAsset, DirectorProject, EditChatMessage } from '../types';

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

const sendIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

const closeIcon = (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round">
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);

const imageIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="9" cy="9" r="1.6" />
    <path d="m4 17 5-5 3 3 4-5 4 5" />
  </svg>
);

// 超过这个字符数的助手消息默认折叠，提供"展开/收起"——与参考截图里长消息
// 的处理方式一致，避免一次规划性长回复把对话区撑得很长。
const EXPAND_THRESHOLD = 260;

interface AtMenuState {
  start: number;
  query: string;
}

/** 光标之前最近的 "@token"（token 内不含空白，"@" 前须是行首或空白）.
 *  与 ComposerCard.detectAtToken 逻辑相同——两处独立复制而非提取共享
 *  工具，两个调用点各自轻量、没有必要为此新增一层抽象。 */
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

interface ChatMessageBubbleProps {
  message: EditChatMessage;
  project: DirectorProject;
  expanded: boolean;
  onToggleExpand: () => void;
}

function ChatMessageBubble({ message, project, expanded, onToggleExpand }: ChatMessageBubbleProps) {
  const { t } = useTranslation();
  const isLong = message.content.length > EXPAND_THRESHOLD;
  const displayContent = isLong && !expanded ? `${message.content.slice(0, EXPAND_THRESHOLD)}…` : message.content;
  const images = message.image_asset_ids
    .map((id) => project.assets.find((a) => a.asset_id === id))
    .filter((a): a is DirectorAsset => !!a && !!a.file_path);

  return (
    <div className={`director-edit-chat-msg director-edit-chat-msg--${message.role}`}>
      {images.length > 0 && (
        <div className="director-edit-chat-msg-images">
          {images.map((a) => (
            <img key={a.asset_id} src={rawFileUrl(a.file_path as string)} alt={a.name || a.prompt} />
          ))}
        </div>
      )}
      <div className="director-edit-chat-msg-body">{displayContent}</div>
      {isLong && (
        <button type="button" className="director-edit-chat-expand-btn" onClick={onToggleExpand}>
          {expanded ? t('director.editChat.collapse') : t('director.editChat.expand')}
        </button>
      )}
    </div>
  );
}

export function EditChatPanel() {
  const { t } = useTranslation();
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const project = useDirectorStore((s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null);
  const draft = useDirectorStore((s) => s.editChatDraft);
  const pendingImageIds = useDirectorStore((s) => s.editChatPendingImageIds);
  const sending = useDirectorStore((s) => s.editChatSending);
  const error = useDirectorStore((s) => s.editChatError);
  const setDraft = useDirectorStore((s) => s.setEditChatDraft);
  const addPendingImage = useDirectorStore((s) => s.addEditChatPendingImage);
  const removePendingImage = useDirectorStore((s) => s.removeEditChatPendingImage);
  const sendMessage = useDirectorStore((s) => s.sendEditChatMessage);

  const [expandedIndexes, setExpandedIndexes] = useState<Set<number>>(new Set());
  const [atMenu, setAtMenu] = useState<AtMenuState | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const messages = useMemo(() => project?.edit_chat_messages ?? [], [project]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages.length, sending]);

  // 重置每次切换项目时残留的草稿/展开状态，避免串项目。
  useEffect(() => {
    setExpandedIndexes(new Set());
    setAtMenu(null);
  }, [selectedProjectId]);

  const namedImageAssets = useMemo(() => {
    if (!project) return [];
    const seen = new Set<string>();
    const result: DirectorAsset[] = [];
    for (const asset of project.assets) {
      if ((asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.name && !seen.has(asset.name)) {
        seen.add(asset.name);
        result.push(asset);
      }
    }
    return result;
  }, [project]);

  const filteredNames = useMemo(() => {
    if (!atMenu) return [];
    const query = atMenu.query.toLowerCase();
    return namedImageAssets.filter((a) => (a.name as string).toLowerCase().includes(query));
  }, [atMenu, namedImageAssets]);

  const toggleExpand = (index: number) => {
    setExpandedIndexes((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const closeAtMenu = () => setAtMenu(null);

  const insertAtName = (asset: DirectorAsset) => {
    if (!atMenu || !asset.name) return;
    const before = draft.slice(0, atMenu.start);
    const after = draft.slice(atMenu.start + 1 + atMenu.query.length);
    const inserted = `@${asset.name} `;
    setDraft(before + inserted + after);
    addPendingImage(asset.asset_id);
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
    setDraft(value);
    const cursor = e.target.selectionStart ?? value.length;
    setAtMenu(detectAtToken(value, cursor));
    setHighlightIndex(0);
  };

  const handleSend = () => {
    if (!selectedProjectId || !draft.trim() || sending) return;
    void sendMessage(selectedProjectId);
  };

  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
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
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!project) {
    return (
      <div className="director-edit-chat director-edit-chat--empty">
        <div className="director-empty-hint">{t('director.editChat.noProject')}</div>
      </div>
    );
  }

  return (
    <div className="director-edit-chat">
      <div className="director-edit-chat-header">{t('director.editChat.title')}</div>

      <div className="director-edit-chat-thread" ref={threadRef}>
        {messages.length === 0 && !sending && (
          <div className="director-edit-chat-empty-hint">{t('director.editChat.emptyHint')}</div>
        )}
        {messages.map((message, index) => (
          <ChatMessageBubble
            key={index}
            message={message}
            project={project}
            expanded={expandedIndexes.has(index)}
            onToggleExpand={() => toggleExpand(index)}
          />
        ))}
        {sending && (
          <div className="director-edit-chat-msg director-edit-chat-msg--assistant director-edit-chat-msg--thinking">
            <span className="director-edit-chat-thinking-label">{t('director.editChat.thinking')}</span>
          </div>
        )}
      </div>

      {error && <div className="director-edit-chat-error">{error}</div>}

      {pendingImageIds.length > 0 && (
        <div className="director-edit-chat-pending-images">
          {pendingImageIds.map((id) => {
            const asset = project.assets.find((a) => a.asset_id === id);
            if (!asset?.file_path) return null;
            return (
              <div key={id} className="director-edit-chat-pending-image">
                <img src={rawFileUrl(asset.file_path)} alt={asset.name || asset.prompt} />
                <button type="button" onClick={() => removePendingImage(id)}>
                  {closeIcon}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="director-edit-chat-input-wrap">
        <textarea
          ref={textareaRef}
          className="director-edit-chat-input"
          placeholder={t('director.editChat.placeholder')}
          value={draft}
          disabled={sending}
          onChange={handleTextareaChange}
          onKeyDown={handleTextareaKeyDown}
          onBlur={() => {
            window.setTimeout(closeAtMenu, 150);
          }}
        />
        {atMenu && filteredNames.length > 0 && (
          <div className="director-at-menu director-edit-chat-at-menu">
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
        <button
          type="button"
          className="director-edit-chat-send-btn"
          disabled={!draft.trim() || sending}
          title={t('director.editChat.send')}
          onClick={handleSend}
        >
          {sendIcon}
        </button>
      </div>
    </div>
  );
}
