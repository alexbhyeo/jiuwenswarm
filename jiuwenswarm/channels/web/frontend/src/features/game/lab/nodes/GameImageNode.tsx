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

/** 画布里的一张"角色/世界"图片素材节点——分解/2D 转 3D/图生图的输入，
 *  同时也是文生图/图生图卡片产出的落点。`in` 入口只在图片是某张卡片的
 *  产出时被连上；从素材面板拖进来的图片没有入边，那个圆点不影响使用。 */
export function GameImageNode({ id, data }: NodeProps & { data: GameImageNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();

  return (
    <div className="game-lab-node game-lab-node--image" data-testid="game-workshop-lab-image-node">
      <Handle type="target" position={Position.Left} id="in" />
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
