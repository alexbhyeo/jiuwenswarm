import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameModelPartNodeData, GameTextureNodeData } from '../labTypes';

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

/** "纹理贴图"卡片：接一个 model/model_part 节点，调 Tripo3D
 *  models/texture 重新生成贴图，产出一个新的 model 素材。流水线形状与
 *  拓扑重构完全一样（提交一次、轮询到成功），只是多了"贴成什么样"的
 *  提示词与贴图质量两个可选项。 */
export function GameTextureNode({ id, data }: NodeProps & { data: GameTextureNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedModel = useConnectedModel(id);
  const busy = data.status === 'submitting' || data.status === 'pending';
  const testid = (suffix: string) => `game-workshop-lab-texture-node${suffix}`;

  return (
    <div className="game-lab-node game-lab-node--texture" data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="model" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.texture.title')}</span>
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
            ? t('gameWorkshop.lab.texture.sourceConnected', { name: connectedModel.name })
            : t('gameWorkshop.lab.texture.sourceMissing')}
        </div>
        <textarea
          className="game-lab-node-hint-input"
          placeholder={t('gameWorkshop.lab.texture.promptPlaceholder')}
          value={data.prompt}
          disabled={busy}
          onChange={(e) => actions.patchTextureNode(id, { prompt: e.target.value })}
          data-testid={testid('-prompt')}
        />
        <select
          className="game-lab-node-granularity"
          value={data.quality}
          disabled={busy}
          onChange={(e) =>
            actions.patchTextureNode(id, { quality: e.target.value as GameTextureNodeData['quality'] })
          }
          data-testid={testid('-quality')}
        >
          <option value="fast">{t('gameWorkshop.lab.texture.qualityFast')}</option>
          <option value="standard">{t('gameWorkshop.lab.texture.qualityStandard')}</option>
          <option value="detailed">{t('gameWorkshop.lab.texture.qualityDetailed')}</option>
          <option value="extreme">{t('gameWorkshop.lab.texture.qualityExtreme')}</option>
        </select>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedModel || busy}
          onClick={() => actions.submitTexture(id)}
          data-testid={testid('-submit')}
        >
          {busy ? t('gameWorkshop.lab.texture.statusRunning') : t('gameWorkshop.lab.texture.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t('gameWorkshop.lab.texture.statusSuccess')}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.texture.statusFailedGeneric')}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="result" />
    </div>
  );
}
