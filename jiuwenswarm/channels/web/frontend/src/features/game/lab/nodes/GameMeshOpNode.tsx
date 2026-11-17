import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameMeshOpNodeData, GameModelPartNodeData } from '../labTypes';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

function useConnectedModel(nodeId: string): { assetId: string; filePath: string; name: string } | null {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId: 'model' });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  if (!sourceId || !sourceData) return null;
  const data = sourceData.data as unknown as GameModelPartNodeData;
  return { assetId: data.assetId, filePath: data.filePath, name: data.name };
}

/** "网格补全"/"拓扑重构"处理卡片共用同一个组件——两者都是"接一个 model/
 *  model_part 节点 -> 提交一次 Tripo3D 任务 -> 轮询 -> 产出 1 个新 model
 *  素材"这同一个形状，只有 i18n 文案和调用的 RPC 不同，用 `type` 区分（与
 *  GameModelPartNode.tsx 用 `type` 区分 model/model_part 同一个取舍）。
 *  网格补全在后端是两段式流水线（先 mesh/segment 再 mesh/complete），但
 *  那个状态机完全由后端的 check_status 推进（见
 *  game_manager.handle_game_assets_mesh_complete_check_status），这里不用
 *  关心，照样只是"提交一次、一轮轮 poll 同一个 RPC"。 */
export function GameMeshOpNode({ id, type, data }: NodeProps & { data: GameMeshOpNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedModel = useConnectedModel(id);
  const busy = data.status === 'submitting' || data.status === 'pending';
  const isDecimate = type === 'mesh_decimate';
  const kind = isDecimate ? 'meshDecimate' : 'meshComplete';
  const variant = isDecimate ? 'mesh-decimate' : 'mesh-complete';
  const testid = (suffix: string) => `game-workshop-lab-${variant}-node${suffix}`;

  return (
    <div className={`game-lab-node game-lab-node--${variant}`} data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="model" />
      <div className="game-lab-node-header">
        <span>{t(`gameWorkshop.lab.${kind}.title`)}</span>
        <button
          type="button"
          title={t('common.delete')}
          onClick={() => actions.deleteNode(id)}
          data-testid={testid('-delete')}
        >
          {trashIcon}
        </button>
      </div>
      <div className="game-lab-node-body nodrag">
        <div className="game-lab-node-connected-hint">
          {connectedModel
            ? t(`gameWorkshop.lab.${kind}.sourceConnected`, { name: connectedModel.name })
            : t(`gameWorkshop.lab.${kind}.sourceMissing`)}
        </div>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedModel || busy}
          onClick={() => (isDecimate ? actions.submitMeshDecimate(id) : actions.submitMeshComplete(id))}
          data-testid={testid('-submit')}
        >
          {busy ? t(`gameWorkshop.lab.${kind}.statusRunning`) : t(`gameWorkshop.lab.${kind}.action`)}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t(`gameWorkshop.lab.${kind}.statusSuccess`)}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t(`gameWorkshop.lab.${kind}.statusFailedGeneric`)}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="result" />
    </div>
  );
}
