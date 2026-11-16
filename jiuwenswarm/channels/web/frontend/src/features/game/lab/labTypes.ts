/**
 * 游戏工坊实验室画布的节点数据类型。
 *
 * 画布的素材节点（与导演模式"不为每种素材单开节点类型"的取舍一致，见
 * LabCanvas.tsx 对 character 的处理）：
 * - `image`：角色/世界类目下拖进画布的图片素材（分解/2D 转 3D 的输入）。
 * - `model_part`：分解产出的 3D 部件（分解的输出）。
 * - `model`：2D 转 3D 产出的单体模型（2D 转 3D 的输出）。
 * 两种处理卡片节点（镜像导演模式 lab-toolrail 的"图片"分组，见
 * GameLabCanvas.tsx 的圆形工具栏）：
 * - `decompose`：素材分解卡片，接一张 image 节点，调 Tripo3D
 *   mesh/smartsegment 产出 N 个 model_part 素材。
 * - `to_model`：2D 转 3D 卡片，接一张 image 节点，调 Tripo3D
 *   generation/image-to-model 产出 1 个 model 素材（不分部件）。
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

export type GameToModelNodeData = {
  status: 'idle' | 'submitting' | 'pending' | 'success' | 'failed';
  error?: string;
  placeholderAssetId?: string;
  jobId?: string;
  resultAssetId?: string;
};
