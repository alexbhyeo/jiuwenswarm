import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { DIRECTOR_TEMPLATES } from '../templateData';

const GRADIENTS = [
  'linear-gradient(135deg,#e8effd,#c3d7fb)',
  'linear-gradient(135deg,#fdece0,#fac99a)',
  'linear-gradient(135deg,#f1e8fd,#d4b8f7)',
  'linear-gradient(135deg,#e3f6ec,#a9e6c4)',
  'linear-gradient(135deg,#fde8ef,#f7b8ce)',
];

export function TemplateGallery() {
  const { t } = useTranslation();
  const setComposerMode = useDirectorStore((s) => s.setComposerMode);

  return (
    <>
      <div className="director-section-title">{t('director.templates.title')}</div>
      <div className="director-template-grid">
        {DIRECTOR_TEMPLATES.map((template, idx) => (
          <button
            key={template.id}
            type="button"
            className="director-template-card"
            onClick={() => setComposerMode(template.mode as 'video' | 'image')}
          >
            <div className="director-template-thumb" style={{ background: GRADIENTS[idx % GRADIENTS.length] }} />
            <div>
              <div className="director-template-name">{t(template.titleKey)}</div>
              <div className="director-template-desc">{t(template.descKey)}</div>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}
