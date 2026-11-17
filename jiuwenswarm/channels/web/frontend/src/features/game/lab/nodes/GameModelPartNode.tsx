import '@google/model-viewer';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useGameLabActions } from '../GameLabActionsContext';
import type { GameModelPartNodeData } from '../labTypes';
import { rawFileUrl } from './gameRawFileUrl';
import { ModelViewerModal } from './ModelViewerModal';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

const downloadIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16" />
  </svg>
);

/** 单个 3D 模型结果节点：缩略图直接用 <model-viewer> 渲染模型本身（见
 *  modelViewerJsx.d.ts 的类型声明），点击弹出可拖拽旋转查看的大图
 *  （ModelViewerModal）+ 下载/重命名/删除。同时服务两种产出——分解卡片
 *  产出的部件（type="model_part"）和 2D 转 3D 卡片产出的单体模型
 *  （type="model"）——两者数据形状、交互完全一致，只用 `type` 区分
 *  testid/类名，不拆成两个组件。 */
export function GameModelPartNode({ id, type, data }: NodeProps & { data: GameModelPartNodeData }) {
  const { t } = useTranslation();
  const actions = useGameLabActions();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(data.name);
  const [viewerOpen, setViewerOpen] = useState(false);
  const modelUrl = rawFileUrl(data.filePath);
  const variant = type === 'model' ? 'model' : 'model-part';
  const testid = (suffix: string) => `game-workshop-lab-${variant}-node${suffix}`;

  return (
    <div className={`game-lab-node game-lab-node--${variant}`} data-testid={testid('')}>
      <Handle type="target" position={Position.Left} id="in" />
      <div className="game-lab-node-media game-lab-node-media--model">
        <model-viewer
          src={modelUrl}
          alt={data.name}
          className="nodrag game-lab-node-model-thumb"
          onClick={() => setViewerOpen(true)}
          data-testid={testid('-thumb')}
        />
        <div className="game-lab-node-media-actions">
          <a href={modelUrl} download title={t('common.download')} data-testid={testid('-download')}>
            {downloadIcon}
          </a>
          <button
            type="button"
            title={t('common.delete')}
            onClick={() => actions.deleteNode(id)}
            data-testid={testid('-delete')}
          >
            {trashIcon}
          </button>
        </div>
      </div>
      {editing ? (
        <input
          className="game-lab-node-name-input nodrag"
          autoFocus
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onBlur={() => {
            setEditing(false);
            if (draftName.trim() && draftName !== data.name) actions.renameNode(id, draftName.trim());
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          }}
          data-testid={testid('-name-input')}
        />
      ) : (
        <div className="game-lab-node-name" onClick={() => setEditing(true)} data-testid={testid('-name')}>
          {data.name}
        </div>
      )}
      {viewerOpen && <ModelViewerModal src={modelUrl} alt={data.name} onClose={() => setViewerOpen(false)} />}
    </div>
  );
}
