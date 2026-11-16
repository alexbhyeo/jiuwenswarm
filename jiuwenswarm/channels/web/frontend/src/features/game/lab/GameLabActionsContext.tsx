import { createContext, useContext } from 'react';
import type { GameDecomposeNodeData } from './labTypes';

/** 实验室画布节点可以调用的动作，由 GameLabCanvas 提供实现——与导演模式
 *  LabActionsContext 同一个取舍（节点组件本身不直接碰 React Flow 的
 *  nodes/edges 状态，只通过这几个动作间接改）。 */
export interface GameLabActions {
  deleteNode: (nodeId: string) => void;
  renameNode: (nodeId: string, name: string) => void;
  patchDecomposeNode: (nodeId: string, patch: Partial<GameDecomposeNodeData>) => void;
  submitDecompose: (nodeId: string) => void;
}

const GameLabActionsContext = createContext<GameLabActions | null>(null);

export const GameLabActionsProvider = GameLabActionsContext.Provider;

export function useGameLabActions(): GameLabActions {
  const ctx = useContext(GameLabActionsContext);
  if (!ctx) throw new Error('useGameLabActions must be used within GameLabActionsProvider');
  return ctx;
}
