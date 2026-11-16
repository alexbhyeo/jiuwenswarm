import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameImageNodeData } from '../labTypes';
import { rawFileUrl } from './gameRawFileUrl';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

/** 画布里的一张"角色/世界"图片素材节点——分解卡片唯一支持的输入类型。 */
export function GameImageNode({ id, data }: NodeProps & { data: GameImageNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();

  return (
    <div className="game-lab-node game-lab-node--image" data-testid="game-workshop-lab-image-node">
      <div className="game-lab-node-media">
        <img src={rawFileUrl(data.filePath)} alt={data.name} className="nodrag" />
        <button
          type="button"
          className="game-lab-node-delete"
          title={t('common.delete')}
          onClick={() => actions.deleteNode(id)}
          data-testid="game-workshop-lab-image-node-delete"
        >
          {trashIcon}
        </button>
      </div>
      <div className="game-lab-node-name" data-testid="game-workshop-lab-image-node-name">
        {data.name}
      </div>
      <Handle type="source" position={Position.Right} id="image" />
    </div>
  );
}
