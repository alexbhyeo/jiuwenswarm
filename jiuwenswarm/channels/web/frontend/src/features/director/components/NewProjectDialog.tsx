import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';

interface NewProjectDialogProps {
  onClose: () => void;
}

export function NewProjectDialog({ onClose }: NewProjectDialogProps) {
  const { t } = useTranslation();
  const createProject = useDirectorStore((s) => s.createProject);
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = async () => {
    if (!name.trim() || submitting) return;
    setSubmitting(true);
    const project = await createProject(name.trim());
    setSubmitting(false);
    if (project) onClose();
  };

  return (
    <div className="director-dialog-overlay" onClick={onClose}>
      <div className="director-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="director-dialog-title">{t('director.newProjectDialog.title')}</div>
        <input
          autoFocus
          className="director-dialog-input"
          placeholder={t('director.newProjectDialog.placeholder')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleConfirm();
          }}
        />
        <div className="director-dialog-actions">
          <button type="button" className="director-dialog-btn director-dialog-btn--cancel" onClick={onClose}>
            {t('director.newProjectDialog.cancel')}
          </button>
          <button
            type="button"
            className="director-dialog-btn director-dialog-btn--confirm"
            disabled={!name.trim() || submitting}
            onClick={() => void handleConfirm()}
          >
            {t('director.newProjectDialog.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
