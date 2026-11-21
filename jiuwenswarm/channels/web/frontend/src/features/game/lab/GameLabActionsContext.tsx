import { createContext, useContext } from 'react';
import type {
  GameAutoRigNodeData,
  GameDecomposeNodeData,
  GameImageGenNodeData,
  GameMeshOpNodeData,
  GameRetargetNodeData,
  GameRigCheckNodeData,
  GameTextureNodeData,
  GameToModelNodeData,
} from './labTypes';

/** 实验室画布节点可以调用的动作，由 GameLabCanvas 提供实现——与导演模式
 *  LabActionsContext 同一个取舍（节点组件本身不直接碰 React Flow 的
 *  nodes/edges 状态，只通过这几个动作间接改）。 */
export interface GameLabActions {
  deleteNode: (nodeId: string) => void;
  renameNode: (nodeId: string, name: string) => void;
  patchDecomposeNode: (nodeId: string, patch: Partial<GameDecomposeNodeData>) => void;
  submitDecompose: (nodeId: string) => void;
  patchToModelNode: (nodeId: string, patch: Partial<GameToModelNodeData>) => void;
  submitToModel: (nodeId: string) => void;
  patchMeshOpNode: (nodeId: string, patch: Partial<GameMeshOpNodeData>) => void;
  submitMeshComplete: (nodeId: string) => void;
  submitMeshDecimate: (nodeId: string) => void;
  /** 文生图/图生图卡片：改提示词/归类的目标类目，以及提交一次生成。 */
  patchImageGenNode: (nodeId: string, patch: Partial<GameImageGenNodeData>) => void;
  submitImageGen: (nodeId: string) => void;
  /** 纹理贴图卡片：改风格提示词/贴图质量，以及提交一次贴图。 */
  patchTextureNode: (nodeId: string, patch: Partial<GameTextureNodeData>) => void;
  submitTexture: (nodeId: string) => void;
  /** 动画三件套：骨骼检查（纯信息）、自动绑定、动画重定向。 */
  patchRigCheckNode: (nodeId: string, patch: Partial<GameRigCheckNodeData>) => void;
  submitRigCheck: (nodeId: string) => void;
  patchAutoRigNode: (nodeId: string, patch: Partial<GameAutoRigNodeData>) => void;
  submitAutoRig: (nodeId: string) => void;
  patchRetargetNode: (nodeId: string, patch: Partial<GameRetargetNodeData>) => void;
  submitRetarget: (nodeId: string) => void;
  /** 「生成实验室流程图」：按项目里跑过的处理卡片整体重建画布（幂等，
   *  每次都是清空重画）。没有可画的卡片时返回 false 且不动画布。 */
  buildFlow: () => boolean;
}

const GameLabActionsContext = createContext<GameLabActions | null>(null);

export const GameLabActionsProvider = GameLabActionsContext.Provider;

export function useGameLabActions(): GameLabActions {
  const ctx = useContext(GameLabActionsContext);
  if (!ctx) throw new Error('useGameLabActions must be used within GameLabActionsProvider');
  return ctx;
}
