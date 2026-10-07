import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { VideoNodeData } from '../labTypes';
import { NodeNameLabel } from './NodeNameLabel';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

export function VideoNode({ id, data }: NodeProps & { data: VideoNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();

  return (
    <div className="lab-node lab-node--video">
      <div className="lab-node-label">{t('director.categories.video')}</div>
      <div className="lab-node-media lab-node-media--video">
        <video src={rawFileUrl(data.filePath)} controls playsInline preload="metadata" />
        <div className="lab-node-media-actions">
          <button type="button" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
            {trashIcon}
          </button>
        </div>
      </div>
      <NodeNameLabel name={data.name} fallback={t('director.categories.video')} onRename={(name) => actions.renameNode(id, name)} />
      {/* target 句柄是"生成结果自动连线"的落点（处理卡片 → 视频输出）。
       *  source 句柄是视频作为下游输入的唯一出口——目前只有"视频生音频"
       *  （video2audio）的 video1 端口吃它（见 LabCanvas.isValidConnection）。
       *  少了这个 source 句柄，视频卡片在画布上根本拖不出连线。 */}
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="source" position={Position.Right} id="video" />
    </div>
  );
}
