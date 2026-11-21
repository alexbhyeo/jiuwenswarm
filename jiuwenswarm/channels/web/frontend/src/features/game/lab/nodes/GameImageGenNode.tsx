import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameImageGenNodeData, GameImageNodeData } from '../labTypes';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

/** 图生图的参考图端口可以接多张（与导演模式 imageRef 的多参考图合成一致，
 *  generate_visual 的 reference_image_paths 本身就是一个列表）。 */
function useConnectedImages(nodeId: string): { assetId: string; name: string }[] {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId: 'image' });
  const sourcesData = useNodesData(connections.map((c) => c.source));
  return sourcesData
    .map((s) => s.data as unknown as GameImageNodeData)
    .filter((d): d is GameImageNodeData => Boolean(d?.assetId));
}

interface GameImageGenCardProps {
  id: string;
  data: GameImageGenNodeData;
  variant: 'text2image' | 'image_ref';
}

/** 文生图 / 图生图卡片（镜像导演模式实验室的同名卡片）。
 *
 *  两张卡走同一个后端 RPC（game.assets.generate）：文生图只带提示词，
 *  图生图额外把连进来的参考图素材 id 交给后端解析成 reference_image_paths。
 *  差别只有"有没有参考图端口"和几句文案，所以合成一个组件，用 `variant`
 *  区分 testid/类名。成功产出的图片会以 `image` 节点摆到卡片下方（见
 *  GameLabCanvas 的 spawnImageResultNode），可以继续连到分解/2D 转 3D。 */
function GameImageGenCard({ id, data, variant }: GameImageGenCardProps) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const isImageRef = variant === 'image_ref';
  // 无条件调用：text2image 没有这个端口，hook 只会拿到空列表；放在条件里
  // 会破坏 hooks 的调用顺序约定。
  const connectedImages = useConnectedImages(id);
  const busy = data.status === 'submitting';
  const canSubmit = data.prompt.trim().length > 0 && !busy && (!isImageRef || connectedImages.length > 0);
  // i18n 前缀用 camelCase 的 imageRef：i18next 默认以 "_" 作为复数分隔符，
  // key 里带 image_ref 且调用时传了 count，会把 key 拆坏、原样显示出来。
  const prefix = `gameWorkshop.lab.${isImageRef ? 'imageRef' : 'text2image'}`;
  const testid = (suffix: string) => `game-workshop-lab-${isImageRef ? 'image-ref' : 'text2image'}-node${suffix}`;

  return (
    <div className={`game-lab-node game-lab-node--${isImageRef ? 'image-ref' : 'text2image'}`} data-testid={testid('')}>
      {isImageRef && <Handle type="target" position={Position.Left} id="image" />}
      <div className="game-lab-node-header">
        <span>{t(`${prefix}.title`)}</span>
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
        {isImageRef && (
          <div className="game-lab-node-connected-hint" data-testid={testid('-reference-hint')}>
            {connectedImages.length > 0
              ? t(`${prefix}.sourceConnected`, { count: connectedImages.length })
              : t(`${prefix}.sourceMissing`)}
          </div>
        )}
        <textarea
          className="game-lab-node-hint-input"
          placeholder={t(`${prefix}.promptPlaceholder`)}
          value={data.prompt}
          disabled={busy}
          onChange={(e) => actions.patchImageGenNode(id, { prompt: e.target.value })}
          data-testid={testid('-prompt')}
        />
        <select
          className="game-lab-node-granularity"
          value={data.assetType}
          disabled={busy}
          onChange={(e) =>
            actions.patchImageGenNode(id, { assetType: e.target.value as GameImageGenNodeData['assetType'] })
          }
          data-testid={testid('-category')}
        >
          <option value="character">{t('gameWorkshop.rail.categoryCharacter')}</option>
          <option value="world">{t('gameWorkshop.rail.categoryWorld')}</option>
        </select>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!canSubmit}
          onClick={() => actions.submitImageGen(id)}
          data-testid={testid('-submit')}
        >
          {busy ? t(`${prefix}.statusRunning`) : t(`${prefix}.action`)}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t(`${prefix}.statusSuccess`)}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t(`${prefix}.statusFailedGeneric`)}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="image" />
    </div>
  );
}

export function GameText2ImageNode({ id, data }: NodeProps & { data: GameImageGenNodeData }) {
  return <GameImageGenCard id={id} data={data} variant="text2image" />;
}

export function GameImageRefNode({ id, data }: NodeProps & { data: GameImageGenNodeData }) {
  return <GameImageGenCard id={id} data={data} variant="image_ref" />;
}
