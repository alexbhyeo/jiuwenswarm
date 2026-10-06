import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { AudioNodeData } from '../labTypes';
import { NodeNameLabel } from './NodeNameLabel';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 文生音频的输出节点。与 VideoNode 同构，只是把播放器换成 <audio>。 */
export function AudioNode({ id, data }: NodeProps & { data: AudioNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();

  return (
    <div className="lab-node lab-node--audio">
      <div className="lab-node-label">{t('director.categories.audio')}</div>
      <div className="lab-node-media lab-node-media--audio">
        <audio src={rawFileUrl(data.filePath)} controls preload="metadata" />
        <div className="lab-node-media-actions">
          <button type="button" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
            {trashIcon}
          </button>
        </div>
      </div>
      <NodeNameLabel name={data.name} fallback={t('director.categories.audio')} onRename={(name) => actions.renameNode(id, name)} />
      {/* 音频输出节点目前没有下游用途（还不能作为别的卡片的输入），保留
       *  target 句柄只是为了让"生成结果自动连线"的视觉逻辑保持一致——与
       *  VideoNode 的处理相同。 */}
      <Handle type="target" position={Position.Left} id="in" />
    </div>
  );
}
