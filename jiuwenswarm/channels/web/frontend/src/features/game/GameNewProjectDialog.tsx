/**
 * 新建游戏项目弹窗——镜像导演模式 NewProjectDialog（点遮罩/取消关闭，
 * 回车或点"创建"提交），替代之前 rail 里常驻的文本输入框。
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import './gameNewProjectDialog.css';

interface GameNewProjectDialogProps {
  onCreate: (name: string) => Promise<void>;
  onClose: () => void;
}

export function GameNewProjectDialog({ onCreate, onClose }: GameNewProjectDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = async () => {
    if (!name.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onCreate(name.trim());
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="game-dialog-overlay" onClick={onClose} data-testid="game-workshop-new-project-dialog-overlay">
      <div className="game-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="game-dialog-title">{t('gameWorkshop.newProjectDialog.title')}</div>
        <input
          autoFocus
          className="game-dialog-input"
          placeholder={t('gameWorkshop.newProjectDialog.placeholder')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleConfirm();
          }}
          data-testid="game-workshop-new-project-dialog-input"
        />
        <div className="game-dialog-actions">
          <button
            type="button"
            className="game-dialog-btn game-dialog-btn--cancel"
            onClick={onClose}
            data-testid="game-workshop-new-project-dialog-cancel"
          >
            {t('gameWorkshop.newProjectDialog.cancel')}
          </button>
          <button
            type="button"
            className="game-dialog-btn game-dialog-btn--confirm"
            disabled={!name.trim() || submitting}
            onClick={() => void handleConfirm()}
            data-testid="game-workshop-new-project-dialog-confirm"
          >
            {t('gameWorkshop.newProjectDialog.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
