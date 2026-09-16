import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { VideoNodeData } from '../labTypes';

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
      <div className="lab-node-name">{data.name}</div>
      {/* 视频输出节点没有下游用途（暂不支持视频再作为参考），保留 target
       *  句柄只是为了让"生成结果自动连线"的视觉逻辑保持一致。 */}
      <Handle type="target" position={Position.Left} id="in" />
    </div>
  );
}
