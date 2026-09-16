import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { TextNodeData } from '../labTypes';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

export function TextNode({ id, data }: NodeProps & { data: TextNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();

  return (
    <div className="lab-node lab-node--text">
      <div className="lab-node-label">
        {t('director.lab.textPrompt')}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>
      <textarea
        className="lab-node-textarea nodrag"
        value={data.text}
        placeholder={t('director.lab.samplePrompt')}
        onChange={(e) => actions.updateText(id, e.target.value)}
      />
      <Handle type="source" position={Position.Right} id="text" />
    </div>
  );
}
