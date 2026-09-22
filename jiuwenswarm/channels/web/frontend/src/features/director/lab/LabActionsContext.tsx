import { createContext, useContext } from 'react';
import type { ProcessKind, ProcessNodeData } from './labTypes';

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
  /** 修改处理节点自身的生成参数（宽高比/分辨率/时长/输出数量）——点开
   *  "生成参数" 弹层后各个选项按钮调用。 */
  patchProcessNode: (nodeId: string, patch: Partial<ProcessNodeData>) => void;
  /** 把 剪辑助手 对话里已经生成的设计图/关键帧/视频，按生成时用的参数
   *  （提示词、参考图、首尾帧）自动摆成一条处理节点流程——已经摆过的
   *  素材（按 asset_id 判定）不会被重摆一遍。 */
  buildFlowFromChat: () => void;
  /** 重命名一个图片/视频输出节点——同时更新画布节点自己的 data.name 和
   *  背后真正的 DirectorAsset.name（节点有 assetId 时），两边保持一致，
   *  "@名称" 引用和素材面板显示的名字才不会和画布上看到的对不上。 */
  renameNode: (nodeId: string, name: string) => void;
}

const LabActionsContext = createContext<LabActions | null>(null);

export const LabActionsProvider = LabActionsContext.Provider;

export function useLabActions(): LabActions {
  const ctx = useContext(LabActionsContext);
  if (!ctx) throw new Error('useLabActions must be used within LabActionsProvider');
  return ctx;
}
