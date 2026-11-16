/**
 * 游戏工坊 · 创作 tab 主面板的生成输入框——镜像导演模式 CreationHomeTab
 * 里的 ComposerCard：分类（角色/世界/视频）+ 文本框 + 发送按钮放在主面板，
 * 不再放进常驻 rail（rail 收窄回纯浏览，见 GameRail.tsx）。
 *
 * 范围比 Director 的 ComposerCard 小很多：Director 支持画幅/分辨率/音色/
 * 风格等每种模式专属的参数下拉，还有 "@素材名" 引用自动补全；
 * `game.assets.generate` 目前只接受 project_id/prompt/type/name，没有这些
 * 参数，所以这里只做"分类 + 文本框 + 发送"，不凭空加后端不支持的控件。
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { gameApi, type GameProject } from './gameApi';
import './gameComposer.css';

type AssetCategory = 'character' | 'world' | 'video';

const MODES: { key: AssetCategory; labelKey: string }[] = [
  { key: 'character', labelKey: 'gameWorkshop.rail.categoryCharacter' },
  { key: 'world', labelKey: 'gameWorkshop.rail.categoryWorld' },
  { key: 'video', labelKey: 'gameWorkshop.rail.categoryVideo' },
];

const sendIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

const spinnerIcon = <span className="game-composer-spinner" data-testid="game-workshop-composer-spinner" />;

interface GameComposerProps {
  project: GameProject;
  onProjectUpdate: (next: GameProject) => void;
}

export function GameComposer({ project, onProjectUpdate }: GameComposerProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<AssetCategory>('character');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const canSend = prompt.trim().length > 0 && !busy;

  const generate = async () => {
    if (!canSend) return;
    setBusy(true);
    setNotice('');
    try {
      const resp = await gameApi.generateAsset(project.project_id, prompt.trim(), mode, prompt.trim().slice(0, 24));
      onProjectUpdate(resp.project);
      if (resp.asset.status === 'ready') {
        setPrompt('');
      } else {
        setNotice(t('gameWorkshop.rail.generateFailed', { error: resp.asset.status }));
      }
    } catch (err) {
      setNotice(t('gameWorkshop.rail.generateFailed', { error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="game-composer" data-testid="game-workshop-composer">
      <div className="game-composer-modes">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            className={`game-composer-mode ${mode === m.key ? 'game-composer-mode--active' : ''}`}
            onClick={() => setMode(m.key)}
            data-testid="game-workshop-composer-mode"
            data-variant={m.key}
          >
            {t(m.labelKey)}
          </button>
        ))}
      </div>

      <textarea
        className="game-composer-textarea"
        placeholder={t('gameWorkshop.rail.promptPlaceholder')}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        disabled={busy}
        data-testid="game-workshop-composer-textarea"
      />

      <div className="game-composer-toolbar">
        <button
          type="button"
          className="game-composer-send-btn"
          disabled={!canSend}
          onClick={() => void generate()}
          title={busy ? t('gameWorkshop.rail.generating') : t('gameWorkshop.rail.generateButton')}
          data-testid="game-workshop-composer-send"
        >
          {busy ? spinnerIcon : sendIcon}
        </button>
      </div>

      {notice && (
        <div className="game-composer-error" data-testid="game-workshop-composer-notice">
          {notice}
        </div>
      )}
    </div>
  );
}
