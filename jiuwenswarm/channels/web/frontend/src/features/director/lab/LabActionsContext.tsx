import { createContext, useContext } from 'react';
import type { ProcessKind } from './labTypes';

/** 生成时实际要用到的、已解析好的连接数据——直接复用 ProcessNode 自己算
 *  对勾（useNodeConnections/useNodesData）时得到的同一份结果，而不是让
 *  LabCanvas 再用 edges/nodes 独立算一遍。两套独立实现的连接解析逻辑正是
 *  之前"界面打勾、生成却读到空"这个 bug 的根源——统一成一份即可从根上
 *  避免两边算出不一致的结果。 */
export interface ResolvedGenerateInput {
  prompt: string;
  image1: { assetId: string | null; filePath: string } | null;
  image2: { assetId: string | null; filePath: string } | null;
}

export interface LabActions {
  updateText: (nodeId: string, text: string) => void;
  generate: (nodeId: string, resolved: ResolvedGenerateInput) => void;
  deleteNode: (nodeId: string) => void;
  addProcessNode: (kind: ProcessKind, position: { x: number; y: number }) => void;
}

const LabActionsContext = createContext<LabActions | null>(null);

export const LabActionsProvider = LabActionsContext.Provider;

export function useLabActions(): LabActions {
  const ctx = useContext(LabActionsContext);
  if (!ctx) throw new Error('useLabActions must be used within LabActionsProvider');
  return ctx;
}
