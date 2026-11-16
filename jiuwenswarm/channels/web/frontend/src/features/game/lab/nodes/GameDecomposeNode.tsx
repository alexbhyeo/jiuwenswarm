import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameDecomposeNodeData, GameImageNodeData } from '../labTypes';

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

/** "素材分解"处理卡片：接一张角色/世界图片，调 Tripo3D 产出 N 个独立的
 *  3D 部件素材（见 D-20261116-1047-game-workshop-lab D-04/5.7）。产出的
 *  部件不会自动摆上这张画布——它们出现在创作面板的素材网格里，用户要用
 *  的话自己从那边再拖一次（与导演模式"从素材库拖进画布"同一套交互）。
 */
export function GameDecomposeNode({ id, data }: NodeProps & { data: GameDecomposeNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedImage = useConnectedImage(id);
  const busy = data.status === 'submitting' || data.status === 'pending';

  return (
    <div className="game-lab-node game-lab-node--decompose" data-testid="game-workshop-lab-decompose-node">
      <Handle type="target" position={Position.Left} id="image" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.decompose.title')}</span>
        <button
          type="button"
          title={t('common.delete')}
          onClick={() => actions.deleteNode(id)}
          data-testid="game-workshop-lab-decompose-node-delete"
        >
          {trashIcon}
        </button>
      </div>
      <div className="game-lab-node-body nodrag">
        <div className="game-lab-node-connected-hint">
          {connectedImage
            ? t('gameWorkshop.lab.decompose.sourceConnected', { name: connectedImage.name })
            : t('gameWorkshop.lab.decompose.sourceMissing')}
        </div>
        <textarea
          className="game-lab-node-hint-input"
          placeholder={t('gameWorkshop.lab.decompose.hintPlaceholder')}
          value={data.hint}
          disabled={busy}
          onChange={(e) => actions.patchDecomposeNode(id, { hint: e.target.value })}
          data-testid="game-workshop-lab-decompose-node-hint"
        />
        <select
          className="game-lab-node-granularity"
          value={data.granularity}
          disabled={busy}
          onChange={(e) =>
            actions.patchDecomposeNode(id, { granularity: e.target.value as GameDecomposeNodeData['granularity'] })
          }
          data-testid="game-workshop-lab-decompose-node-granularity"
        >
          <option value="coarse">{t('gameWorkshop.lab.decompose.granularityCoarse')}</option>
          <option value="medium">{t('gameWorkshop.lab.decompose.granularityMedium')}</option>
          <option value="fine">{t('gameWorkshop.lab.decompose.granularityFine')}</option>
        </select>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedImage || busy}
          onClick={() => actions.submitDecompose(id)}
          data-testid="game-workshop-lab-decompose-node-submit"
        >
          {busy ? t('gameWorkshop.lab.decompose.statusRunning') : t('gameWorkshop.lab.decompose.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t('gameWorkshop.lab.decompose.statusSuccess', { count: data.resultCount ?? 0 })}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.decompose.statusFailedGeneric')}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="parts" />
    </div>
  );
}
