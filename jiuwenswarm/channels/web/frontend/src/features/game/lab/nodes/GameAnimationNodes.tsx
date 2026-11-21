import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type {
  GameAutoRigNodeData,
  GameModelPartNodeData,
  GameRetargetNodeData,
  GameRigCheckNodeData,
} from '../labTypes';

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

const RIG_TYPES = ['biped', 'quadruped', 'hexapod', 'octopod', 'avian', 'serpentine', 'aquatic', 'others'];
const RIG_SPECS = ['tripo', 'mixamo', 'actorcore', 'unity', 'unreal', 'vrm'];
// 与 game_item_animation.ANIMATION_PRESETS 对齐；v3.0 biped 的 11 个预设
// 在前，其它骨架各一条行走/行进预设。
const ANIMATION_PRESETS = [
  'preset:idle',
  'preset:walk',
  'preset:run',
  'preset:jump',
  'preset:dive',
  'preset:climb',
  'preset:slash',
  'preset:shoot',
  'preset:hurt',
  'preset:fall',
  'preset:turn',
  'preset:quadruped:walk',
  'preset:hexapod:walk',
  'preset:octopod:walk',
  'preset:serpentine:march',
  'preset:aquatic:march',
];

function animationLabelKey(preset: string): string {
  const short = preset.replace(/^preset:/, '').replace(/:/g, '_');
  return `gameWorkshop.lab.animations.${short}`;
}

/** 骨骼检查卡片：接一个模型，调 Tripo3D animations/rig-check。结果是纯信息
 *  （能否绑定 + 推荐骨架），不产出素材，所以没有输出端口。 */
export function GameRigCheckNode({ id, data }: NodeProps & { data: GameRigCheckNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedModel = useConnectedModel(id);
  const busy = data.status === 'submitting' || data.status === 'pending';
  const testid = (suffix: string) => `game-workshop-lab-rig-check-node${suffix}`;

  return (
    <div className="game-lab-node game-lab-node--rig-check" data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="model" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.rigCheck.title')}</span>
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
            ? t('gameWorkshop.lab.rigCheck.sourceConnected', { name: connectedModel.name })
            : t('gameWorkshop.lab.rigCheck.sourceMissing')}
        </div>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedModel || busy}
          onClick={() => actions.submitRigCheck(id)}
          data-testid={testid('-submit')}
        >
          {busy ? t('gameWorkshop.lab.rigCheck.statusRunning') : t('gameWorkshop.lab.rigCheck.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {data.riggable
              ? t('gameWorkshop.lab.rigCheck.statusRiggable', {
                  rigType: data.rigType
                    ? t(`gameWorkshop.lab.rigTypes.${data.rigType}`)
                    : t('gameWorkshop.lab.rigCheck.rigTypeUnknown'),
                })
              : t('gameWorkshop.lab.rigCheck.statusNotRiggable')}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.rigCheck.statusFailedGeneric')}
          </div>
        )}
      </div>
    </div>
  );
}

/** 自动绑定卡片：接一个模型，调 Tripo3D animations/rig，产出带骨架的模型。 */
export function GameAutoRigNode({ id, data }: NodeProps & { data: GameAutoRigNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedModel = useConnectedModel(id);
  const busy = data.status === 'submitting' || data.status === 'pending';
  const isBiped = data.rigType === 'biped';
  const testid = (suffix: string) => `game-workshop-lab-auto-rig-node${suffix}`;

  return (
    <div className="game-lab-node game-lab-node--auto-rig" data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="model" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.autoRig.title')}</span>
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
            ? t('gameWorkshop.lab.autoRig.sourceConnected', { name: connectedModel.name })
            : t('gameWorkshop.lab.autoRig.sourceMissing')}
        </div>
        <select
          className="game-lab-node-granularity"
          value={data.rigType}
          disabled={busy}
          onChange={(e) => actions.patchAutoRigNode(id, { rigType: e.target.value })}
          data-testid={testid('-rig-type')}
        >
          {RIG_TYPES.map((rigType) => (
            <option key={rigType} value={rigType}>
              {t(`gameWorkshop.lab.rigTypes.${rigType}`)}
            </option>
          ))}
        </select>
        <select
          className="game-lab-node-granularity"
          value={data.spec}
          disabled={busy || !isBiped}
          onChange={(e) => actions.patchAutoRigNode(id, { spec: e.target.value })}
          data-testid={testid('-spec')}
        >
          {RIG_SPECS.map((spec) => (
            <option key={spec} value={spec}>
              {t(`gameWorkshop.lab.rigSpecs.${spec}`)}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedModel || busy}
          onClick={() => actions.submitAutoRig(id)}
          data-testid={testid('-submit')}
        >
          {busy ? t('gameWorkshop.lab.autoRig.statusRunning') : t('gameWorkshop.lab.autoRig.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t('gameWorkshop.lab.autoRig.statusSuccess')}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.autoRig.statusFailedGeneric')}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="result" />
    </div>
  );
}

/** 动画重定向卡片：接一个已绑定的模型（后端会检查素材是否带绑定 task id），
 *  调 Tripo3D animations/retarget 套预设动画，产出带动画的模型。 */
export function GameRetargetNode({ id, data }: NodeProps & { data: GameRetargetNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const connectedModel = useConnectedModel(id);
  const busy = data.status === 'submitting' || data.status === 'pending';
  const testid = (suffix: string) => `game-workshop-lab-retarget-node${suffix}`;

  return (
    <div className="game-lab-node game-lab-node--retarget" data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="model" />
      <div className="game-lab-node-header">
        <span>{t('gameWorkshop.lab.retarget.title')}</span>
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
            ? t('gameWorkshop.lab.retarget.sourceConnected', { name: connectedModel.name })
            : t('gameWorkshop.lab.retarget.sourceMissing')}
        </div>
        <select
          className="game-lab-node-granularity"
          value={data.animation}
          disabled={busy}
          onChange={(e) => actions.patchRetargetNode(id, { animation: e.target.value })}
          data-testid={testid('-animation')}
        >
          {ANIMATION_PRESETS.map((preset) => (
            <option key={preset} value={preset}>
              {t(animationLabelKey(preset))}
            </option>
          ))}
        </select>
        <label className="game-lab-node-checkbox">
          <input
            type="checkbox"
            checked={data.animateInPlace}
            disabled={busy}
            onChange={(e) => actions.patchRetargetNode(id, { animateInPlace: e.target.checked })}
            data-testid={testid('-in-place')}
          />
          {t('gameWorkshop.lab.retarget.animateInPlace')}
        </label>
        <button
          type="button"
          className="game-lab-node-decompose-button"
          disabled={!connectedModel || busy}
          onClick={() => actions.submitRetarget(id)}
          data-testid={testid('-submit')}
        >
          {busy ? t('gameWorkshop.lab.retarget.statusRunning') : t('gameWorkshop.lab.retarget.action')}
        </button>
        {data.status === 'success' && (
          <div className="game-lab-node-status game-lab-node-status--success" data-variant="success">
            {t('gameWorkshop.lab.retarget.statusSuccess')}
          </div>
        )}
        {data.status === 'failed' && (
          <div className="game-lab-node-status game-lab-node-status--failed" data-variant="failed">
            {data.error || t('gameWorkshop.lab.retarget.statusFailedGeneric')}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id="result" />
    </div>
  );
}
