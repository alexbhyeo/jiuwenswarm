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
      {/* target 句柄是"生成结果自动连线"的落点（处理卡片 → 音频输出）。
       *  source 句柄是音频作为下游输入的唯一出口——目前只有"图音生视频"
       *  （imageAudio2video）的 audio1 端口吃它（见 LabCanvas.isValidConnection），
       *  模型按这段音频做口型同步。少了这个 source 句柄，音频卡片在画布上
       *  根本拖不出连线。 */}
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="source" position={Position.Right} id="audio" />
    </div>
  );
}
