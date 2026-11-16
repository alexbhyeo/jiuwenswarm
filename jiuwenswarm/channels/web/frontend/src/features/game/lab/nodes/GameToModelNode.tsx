import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameImageNodeData, GameToModelNodeData } from '../labTypes';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

function useConnectedImage(nodeId: string): { assetId: string; filePath: string; name: string } | null {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId: 'image' });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  if (!sourceId || !sourceData) return null;
  const data = sourceData.data as unknown as GameImageNodeData;
  return { assetId: data.assetId, filePath: data.filePath, name: data.name };
}

/** "2D 转 3D"处理卡片：接一张角色/世界图片，调 Tripo3D
 *  generation/image-to-model 产出一个单体模型（不分部件，与素材分解卡片
 *  的区别只在这一点）。产出的模型同样不会自动摆上画布——出现在创作面板
 *  「3D 模型」分类里，和分解产物同一套交互。 */
export function GameToModelNode({ id, data }: NodeProps & { data: GameToModelNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedImage = useConnectedImage(id);
  const busy = data.status === 'submitting' || data.status === 'pending';

  return (
    <div className="game-lab-node game-lab-node--to-model" data-testid="game-workshop-lab-to-model-node">
      <Handle type="target" position={Position.Left} id="image" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.toModel.title')}</span>
        <button
          type="button"
          title={t('common.delete')}
          onClick={() => actions.deleteNode(id)}
          data-testid="game-workshop-lab-to-model-node-delete"
        >
          {trashIcon}
        </button>
      </div>
      <div className="game-lab-node-body nodrag">
        <div className="game-lab-node-connected-hint">
          {connectedImage
            ? t('gameWorkshop.lab.toModel.sourceConnected', { name: connectedImage.name })
            : t('gameWorkshop.lab.toModel.sourceMissing')}
        </div>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedImage || busy}
          onClick={() => actions.submitToModel(id)}
          data-testid="game-workshop-lab-to-model-node-submit"
        >
          {busy ? t('gameWorkshop.lab.toModel.statusRunning') : t('gameWorkshop.lab.toModel.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t('gameWorkshop.lab.toModel.statusSuccess')}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.toModel.statusFailedGeneric')}
          </div>
        )}
      </div>
    </div>
  );
}
