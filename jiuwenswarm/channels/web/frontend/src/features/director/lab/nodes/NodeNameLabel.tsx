import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const pencilIcon = (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
  </svg>
);

interface NodeNameLabelProps {
  name: string;
  fallback: string;
  onRename: (name: string) => void;
}

/** 画布上图片/视频输出节点的名称——点铅笔图标进入行内编辑，回车/失焦保存、
 *  Esc 取消，和 DirectorRail.tsx 里素材面板的 AssetNameLabel 是同一套交互，
 *  只是换了个更紧凑的样式塞进节点卡片。多个输出节点各自独立命名后，才能
 *  在连去其他处理节点时一眼分清"接的是哪一个"，也能在 composer/剪辑助手
 *  的 "@名称" 里精确引用其中某一个（而不是只能引用"最后生成的那个"）。 */
export function NodeNameLabel({ name, fallback, onRename }: NodeNameLabelProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const displayName = name || fallback;

  if (editing) {
    return (
      <input
        autoFocus
        className="lab-node-name-input nodrag"
        value={draft}
        placeholder={t('director.rename.placeholder')}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onBlur={() => {
          setEditing(false);
          if (draft.trim() !== (name || '')) onRename(draft.trim());
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <div className="lab-node-name">
      <span className="lab-node-name-text">{displayName}</span>
      <button
        type="button"
        className="lab-node-name-rename-btn nodrag"
        title={t('director.rename.action')}
        onClick={(e) => {
          e.stopPropagation();
          setDraft(name || '');
          setEditing(true);
        }}
      >
        {pencilIcon}
      </button>
    </div>
  );
}
