/**
 * 游戏工坊实验室画布的节点数据类型。
 *
 * 画布只有两类节点（与导演模式"不为每种素材单开节点类型"的取舍一致，见
 * LabCanvas.tsx 对 character 的处理）：
 * - `image`：角色/世界类目下拖进画布的图片素材（分解输入）。
 * - `model_part`：分解产出的 3D 部件（分解输出）。
 * 以及一种处理卡片节点：
 * - `decompose`：素材分解卡片，接一张 image 节点，调 Tripo3D 产出 N 个
 *   model_part 素材（见 D-20261116-1047-game-workshop-lab D-04）。
 */

export const GAME_ASSET_DRAG_MIME = 'application/x-game-asset';

export type GameAssetDragPayload = {
  assetId: string;
  filePath: string;
  name: string;
  type: string;
};

export type GameImageNodeData = {
  assetId: string;
  filePath: string;
  name: string;
};

export type GameModelPartNodeData = {
  assetId: string;
  filePath: string;
  name: string;
  status: string;
};

export type GameDecomposeNodeData = {
  hint: string;
  granularity: 'coarse' | 'medium' | 'fine';
  status: 'idle' | 'submitting' | 'pending' | 'success' | 'failed';
  error?: string;
  placeholderAssetId?: string;
  jobId?: string;
  decomposeBatchId?: string;
  resultCount?: number;
};
