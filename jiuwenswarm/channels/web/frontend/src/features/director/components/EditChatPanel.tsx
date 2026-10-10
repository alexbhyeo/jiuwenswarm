import { useCallback, useEffect, useMemo, useRef, useState, type ComponentPropsWithoutRef } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useDirectorStore } from '../directorStore';
import { useLabActions } from '../lab/LabActionsContext';
import type { DirectorSkillOption } from '../directorApi';
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

const flowIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="4" width="6" height="6" rx="1.2" />
    <rect x="15" y="4" width="6" height="6" rx="1.2" />
    <rect x="9" y="15" width="6" height="6" rx="1.2" />
    <path d="M6 10v3a2 2 0 0 0 2 2h1M18 10v3a2 2 0 0 1-2 2h-1" />
  </svg>
);

const skillIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3 4 7v6c0 4.4 3.4 7.4 8 8 4.6-.6 8-3.6 8-8V7z" />
    <path d="m9 12 2 2 4-4" />
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

// 分镜列表这类结构化回复里模型经常用 markdown 表格（关键帧/镜头/画面要点/
// 引用设计图……），聊天面板本身很窄，表格原生宽度撑不下时浏览器会把单元格
// 文字整词打散换行（"@Rainy Dark Alley" 断成 "@Rai" / "ny Dark Alley"），
// 完全不可读。这里不改表格本身的列宽策略，而是包一层可以横向滚动的容器：
// 单元格内文字保持整词不折断，宽度不够时滚动查看，而不是把字拆开。
function MarkdownTable({ children }: ComponentPropsWithoutRef<'table'>) {
  return (
    <div className="director-edit-chat-table-wrap">
      <table>{children}</table>
    </div>
  );
}

const markdownComponents = { table: MarkdownTable };

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
          {images.map((a) =>
            a.type === 'video' ? (
              <video key={a.asset_id} src={rawFileUrl(a.file_path as string)} controls playsInline preload="metadata" />
            ) : (
              <img key={a.asset_id} src={rawFileUrl(a.file_path as string)} alt={a.name || a.prompt} />
            )
          )}
        </div>
      )}
      <div className="director-edit-chat-msg-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {displayContent}
        </ReactMarkdown>
      </div>
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
  const actions = useLabActions();
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
  const skillNames = useDirectorStore((s) => s.editChatSkillNames);
  const installedSkills = useDirectorStore((s) => s.installedSkills);
  const skillsLoading = useDirectorStore((s) => s.installedSkillsLoading);
  const loadInstalledSkills = useDirectorStore((s) => s.loadInstalledSkills);
  const setSkillNames = useDirectorStore((s) => s.setEditChatSkillNames);

  const [expandedIndexes, setExpandedIndexes] = useState<Set<number>>(new Set());
  const [atMenu, setAtMenu] = useState<AtMenuState | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [skillFilter, setSkillFilter] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const skillMenuRef = useRef<HTMLDivElement>(null);

  const toggleSkill = (name: string) => {
    setSkillNames(skillNames.includes(name) ? skillNames.filter((n) => n !== name) : [...skillNames, name]);
  };

  // 技能目录里第 0 项是剪辑助手的默认技能（基础说明就是它的正文，后端每轮都
  // 会加载）：它固定在列表最上方、始终勾选状态、点了也不取消——真正生效的是
  // 后端的系统提示词，而不是用户这一轮的勾选，把它做成可取消只会骗人。
  const defaultSkill = useMemo(() => installedSkills.find((s) => s.isDefault) ?? null, [installedSkills]);
  const selectableSkills = useMemo(() => installedSkills.filter((s) => !s.isDefault), [installedSkills]);

  // 技能列表的文本过滤：名称/展示名/描述任一命中（大小写不敏感）即可。技能
  // 描述往往是唯一能区分两个同名近义技能的地方，所以描述也参与匹配，而不是
  // 只看名字。默认技能也参与过滤——用户搜"director"应该能看到助手加载的是谁。
  const matchesSkillFilter = useCallback((skill: DirectorSkillOption, key: string) => {
    if (!key) return true;
    return (
      skill.name.toLowerCase().includes(key) ||
      skill.displayName.toLowerCase().includes(key) ||
      skill.description.toLowerCase().includes(key)
    );
  }, []);

  const filterKey = skillFilter.trim().toLowerCase();
  const filteredSkills = useMemo(
    () => selectableSkills.filter((s) => matchesSkillFilter(s, filterKey)),
    [selectableSkills, filterKey, matchesSkillFilter]
  );
  const showDefaultSkill = defaultSkill ? matchesSkillFilter(defaultSkill, filterKey) : false;
  // 默认技能永远在列表里显示（除非被过滤词排除），所以"有没有可显示的行"不能
  // 只看勾选项；两者都空才提示。
  const hasListedSkill = showDefaultSkill || filteredSkills.length > 0;

  // 默认技能正文的来源——直接写在描述行里，用户一眼能确认助手加载的是哪一份。
  const defaultSkillHint = (skill: DirectorSkillOption) => {
    if (skill.defaultSource === 'builtin') return t('director.editChat.skillDefaultBuiltin');
    if (skill.defaultSource === 'missing') return t('director.editChat.skillDefaultMissing');
    return t('director.editChat.skillDefaultHint');
  };

  // 面板一挂载就把技能目录拉下来（store 里有缓存，一个会话只跑一次）：默认技能
  // 那一行/那枚 chip 是"助手当前加载的是哪份技能"的唯一可见凭据，不能等用户先点
  // 开「技能」下拉才出现。
  useEffect(() => {
    void loadInstalledSkills();
  }, [loadInstalledSkills]);

  // 关掉下拉时清掉过滤词——下次打开应该是完整列表，而不是上次搜过的残留。
  useEffect(() => {
    if (!skillMenuOpen) setSkillFilter('');
  }, [skillMenuOpen]);

  // 点面板其它地方就把技能下拉收起来——与 LabCanvas 节点参数弹层同一套做法
  // （捕获阶段监听，避免被内层 stopPropagation 吃掉）。
  useEffect(() => {
    if (!skillMenuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (skillMenuRef.current && !skillMenuRef.current.contains(e.target as Node)) setSkillMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClick, true);
    return () => document.removeEventListener('mousedown', handleClick, true);
  }, [skillMenuOpen]);

  const messages = useMemo(() => project?.edit_chat_messages ?? [], [project]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages.length, sending]);

  // 重置每次切换项目时残留的草稿/展开状态，避免串项目。
  useEffect(() => {
    setExpandedIndexes(new Set());
    setAtMenu(null);
    setSkillMenuOpen(false);
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
      <div className="director-edit-chat-header">
        <span>{t('director.editChat.title')}</span>
        {/* 技能选择与「生成实验室流程图」并排放在标题行右侧。 */}
        <div className="director-edit-chat-header-actions" ref={skillMenuRef}>
          <button
            type="button"
            className={`director-edit-chat-skills-btn ${skillMenuOpen ? 'director-edit-chat-skills-btn--active' : ''}`}
            title={t('director.editChat.skillsHint')}
            onClick={() => {
              setSkillMenuOpen((v) => !v);
              void loadInstalledSkills();
            }}
            data-testid="director-edit-chat-skills-btn"
          >
            {skillIcon}
            {t('director.editChat.skills')}
            {skillNames.length > 0 && <span className="director-edit-chat-skills-count">{skillNames.length}</span>}
          </button>
          <button
            type="button"
            className="director-edit-chat-build-flow-btn"
            title={t('director.lab.buildFlowFromChat')}
            onClick={() => actions.buildFlowFromChat()}
          >
            {flowIcon}
          </button>
          {skillMenuOpen && (
            <div className="director-edit-chat-skills-menu" data-testid="director-edit-chat-skills-menu">
              <input
                type="text"
                className="director-edit-chat-skills-filter"
                placeholder={t('director.editChat.skillsFilterPlaceholder')}
                value={skillFilter}
                autoFocus
                onChange={(e) => setSkillFilter(e.target.value)}
                onKeyDown={(e) => {
                  // Esc 只收起这个下拉，不要冒泡出去（外层输入框有自己的按键
                  // 处理），也不要清掉用户正在打的草稿。
                  if (e.key === 'Escape') {
                    e.stopPropagation();
                    setSkillMenuOpen(false);
                  }
                }}
                data-testid="director-edit-chat-skills-filter"
              />
              {skillsLoading && <div className="director-edit-chat-skills-empty">{t('director.editChat.skillsLoading')}</div>}
              {!skillsLoading && !hasListedSkill && (
                <div className="director-edit-chat-skills-empty">
                  {skillFilter.trim()
                    ? t('director.editChat.skillsNoMatch', { key: skillFilter.trim() })
                    : t('director.editChat.skillsEmpty')}
                </div>
              )}
              {!skillsLoading && showDefaultSkill && defaultSkill && (
                <div
                  className="director-edit-chat-skill-item director-edit-chat-skill-item--default"
                  title={defaultSkill.description || defaultSkill.name}
                  data-testid="director-edit-chat-skill-default"
                  data-skill-name={defaultSkill.name}
                  data-default-source={defaultSkill.defaultSource}
                >
                  <span className="director-edit-chat-skill-item-name">
                    {defaultSkill.displayName}
                    <span className="director-edit-chat-skill-item-badge">
                      {t('director.editChat.skillDefaultBadge')}
                    </span>
                    <span className="director-edit-chat-skill-item-check">✓</span>
                  </span>
                  <span className="director-edit-chat-skill-item-desc">{defaultSkillHint(defaultSkill)}</span>
                </div>
              )}
              {!skillsLoading &&
                filteredSkills.map((skill) => {
                  const selected = skillNames.includes(skill.name);
                  return (
                    <button
                      key={skill.name}
                      type="button"
                      className={`director-edit-chat-skill-item ${selected ? 'director-edit-chat-skill-item--active' : ''}`}
                      onClick={() => toggleSkill(skill.name)}
                      data-testid="director-edit-chat-skill-item"
                      data-skill-name={skill.name}
                      data-selected={selected ? 'true' : 'false'}
                    >
                      <span className="director-edit-chat-skill-item-name">
                        {skill.displayName}
                        {selected && <span className="director-edit-chat-skill-item-check">✓</span>}
                      </span>
                      {skill.description && <span className="director-edit-chat-skill-item-desc">{skill.description}</span>}
                    </button>
                  );
                })}
              {!skillsLoading && !skillFilter.trim() && selectableSkills.length === 0 && showDefaultSkill && (
                <div className="director-edit-chat-skills-empty">{t('director.editChat.skillsEmpty')}</div>
              )}
            </div>
          )}
        </div>
      </div>

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

      {(defaultSkill || skillNames.length > 0) && (
        <div className="director-edit-chat-skills">
          {/* 默认技能固定在最前面、没有删除按钮——它每轮都由后端加载，不是这一
              轮的勾选项。 */}
          {defaultSkill && (
            <span
              className="director-edit-chat-skill-chip director-edit-chat-skill-chip--default"
              title={defaultSkill.description || defaultSkill.name}
              data-testid="director-edit-chat-skill-default-chip"
              data-skill-name={defaultSkill.name}
            >
              {defaultSkill.displayName}
              <span className="director-edit-chat-skill-chip-badge">{t('director.editChat.skillDefaultBadge')}</span>
            </span>
          )}
          {skillNames.map((name) => {
            const option = installedSkills.find((s) => s.name === name);
            return (
              <span key={name} className="director-edit-chat-skill-chip" title={option?.description || name}>
                {option?.displayName || name}
                <button type="button" onClick={() => toggleSkill(name)} title={t('director.editChat.skillRemove')}>
                  {closeIcon}
                </button>
              </span>
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
