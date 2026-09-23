import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { ImageNodeData } from '../labTypes';
import { NodeNameLabel } from './NodeNameLabel';

const enlargeIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
  </svg>
);

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

const swapIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 3l4 4-4 4M21 7H7M7 21l-4-4 4-4M3 17h14" />
  </svg>
);

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

export function ImageNode({ id, data }: NodeProps & { data: ImageNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const imgRef = useRef<HTMLImageElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="lab-node lab-node--image">
      <div className="lab-node-label">{t('director.lab.refImage')}</div>
      <div className="lab-node-media">
        <img ref={imgRef} src={rawFileUrl(data.filePath)} alt={data.name} className="nodrag" />
        <div className="lab-node-media-actions">
          <button type="button" title={t('director.enlarge')} onClick={() => imgRef.current?.requestFullscreen?.()}>
            {enlargeIcon}
          </button>
          <button type="button" title={t('director.lab.swapImage')} className="nodrag" onClick={() => fileInputRef.current?.click()}>
            {swapIcon}
          </button>
          <button type="button" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
            {trashIcon}
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="lab-node-swap-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) actions.swapImage(id, file);
            e.target.value = '';
          }}
        />
      </div>
      <NodeNameLabel name={data.name} fallback={t('director.lab.refImage')} onRename={(name) => actions.renameNode(id, name)} />
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="source" position={Position.Right} id="image" />
    </div>
  );
}
