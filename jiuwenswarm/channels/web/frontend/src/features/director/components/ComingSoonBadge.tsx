import { useTranslation } from 'react-i18next';

export function ComingSoonBadge() {
  const { t } = useTranslation();
  return <span className="director-coming-soon-badge">{t('director.comingSoon')}</span>;
}
