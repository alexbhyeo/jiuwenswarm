/**
 * 游戏工坊 · 实验室助手。
 *
 * 视觉与导演模式的剪辑助手（components/EditChatPanel.tsx）一致：画布右上
 * 一张悬浮卡片，标题行 + 消息流 + chip 行 + 圆角输入框 + 圆形发送按钮，
 * 收起后只剩右下角一颗胶囊。设计 token 见 gamePage.css 的 `--g-*`（取值
 * 与导演模式 `.director-page` 的 `--d-*` 同一套）。
 *
 * 能力上与剪辑助手不同：它只能驱动实验室的四张处理卡片（素材分解 / 2D 转
 * 3D / 网格补全 / 拓扑重构），不能生成素材、不能改工程——所以没有技能选择器，
 * chip 行改成展示这条权限边界。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { gameApi, type GameLabChatMessage, type GameProject } from '../gameApi';
import { useGameLabActions } from './GameLabActionsContext';
import './gameLabChat.css';

export type GameLabChatPanelProps = {
  project: GameProject;
  onProjectUpdate: (project: GameProject) => void;
};

const sendIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

const minimizeIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
    <path d="M5 19h14" />
  </svg>
);

const flowIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="7" height="6" rx="1.5" />
    <rect x="14" y="15" width="7" height="6" rx="1.5" />
    <path d="M6.5 9v4a2 2 0 0 0 2 2h5.5" />
  </svg>
);

const lockIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="4" y="10" width="16" height="10" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </svg>
);

export function GameLabChatPanel({ project, onProjectUpdate }: GameLabChatPanelProps) {
  const { t } = useTranslation();
  const { buildFlow } = useGameLabActions();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);

  const messages: GameLabChatMessage[] = project.lab_chat_messages ?? [];

  useEffect(() => {
    const node = threadRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, busy]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    setBusy(true);
    setError('');
    try {
      const updated = await gameApi.labChatSend(project.project_id, text);
      onProjectUpdate(updated);
    } catch (err) {
      // 后端已经把用户输入落盘（与剪辑助手一致），这里只提示并把内容放回输入框。
      setError(String(err));
      setDraft(text);
    } finally {
      setBusy(false);
    }
  }, [busy, draft, onProjectUpdate, project.project_id]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  if (collapsed) {
    return (
      <div className="game-lab-chat-overlay game-lab-chat-overlay--collapsed">
        <button
          type="button"
          className="game-lab-chat-collapsed-pill"
          onClick={() => setCollapsed(false)}
          title={t('gameWorkshop.labChat.expand')}
        >
          {lockIcon}
          {t('gameWorkshop.labChat.title')}
        </button>
      </div>
    );
  }

  return (
    <div className="game-lab-chat-overlay">
      <div className="game-lab-chat" data-testid="game-lab-chat">
        <header className="game-lab-chat-header">
          <span className="game-lab-chat-title">{t('gameWorkshop.labChat.title')}</span>
          <div className="game-lab-chat-header-actions">
            {/* 对应导演模式剪辑助手标题行那颗「生成实验室流程图」：把项目里跑过的
                处理卡片还原成一整张流程图（幂等，重复点结果一致）。 */}
            <button
              type="button"
              className="game-lab-chat-icon-btn"
              onClick={() => buildFlow()}
              title={t('gameWorkshop.labChat.buildFlow')}
              aria-label={t('gameWorkshop.labChat.buildFlow')}
              data-testid="game-lab-chat-build-flow"
            >
              {flowIcon}
            </button>
            <button
              type="button"
              className="game-lab-chat-icon-btn"
              onClick={() => setCollapsed(true)}
              title={t('gameWorkshop.labChat.collapse')}
              aria-label={t('gameWorkshop.labChat.collapse')}
            >
              {minimizeIcon}
            </button>
          </div>
        </header>

        <div className="game-lab-chat-thread" ref={threadRef}>
          {messages.length === 0 && !busy ? (
            <p className="game-lab-chat-empty-hint">{t('gameWorkshop.labChat.empty')}</p>
          ) : null}
          {messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              className={`game-lab-chat-msg game-lab-chat-msg--${message.role}`}
            >
              <div className="game-lab-chat-msg-body">
                {message.role === 'assistant' ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                ) : (
                  <p>{message.content}</p>
                )}
              </div>
              {message.applied?.length ? (
                <ul className="game-lab-chat-applied">
                  {message.applied.map((item, appliedIndex) => (
                    <li key={`${index}-${appliedIndex}`}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
          {busy ? (
            <div className="game-lab-chat-msg game-lab-chat-msg--thinking">
              <span className="game-lab-chat-thinking-label">{t('gameWorkshop.labChat.thinking')}</span>
            </div>
          ) : null}
        </div>

        {error ? <p className="game-lab-chat-error">{error}</p> : null}

        <div className="game-lab-chat-chips">
          <span className="game-lab-chat-chip" title={t('gameWorkshop.labChat.scopeHint')}>
            {lockIcon}
            {t('gameWorkshop.labChat.scopeChip')}
          </span>
        </div>

        <div className="game-lab-chat-input-wrap">
          <textarea
            className="game-lab-chat-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('gameWorkshop.labChat.placeholder')}
            disabled={busy}
          />
          <button
            type="button"
            className="game-lab-chat-send-btn"
            onClick={() => void send()}
            disabled={busy || !draft.trim()}
            title={t('gameWorkshop.labChat.send')}
            aria-label={t('gameWorkshop.labChat.send')}
          >
            {sendIcon}
          </button>
        </div>
      </div>
    </div>
  );
}
