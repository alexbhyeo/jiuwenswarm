/**
 * 游戏工坊实验室画布的节点数据类型。
 *
 * 画布的素材节点（与导演模式"不为每种素材单开节点类型"的取舍一致，见
 * LabCanvas.tsx 对 character 的处理）：
 * - `image`：角色/世界类目下拖进画布的图片素材，也是文生图/图生图的产出
 *   （分解/2D 转 3D/图生图的输入）。
 * - `model_part`：分解产出的 3D 部件（分解的输出）。
 * - `model`：2D 转 3D 产出的单体模型（2D 转 3D 的输出）。
 * 十种处理卡片节点（镜像导演模式 lab-toolrail 的分组，见 GameLabCanvas.tsx
 * 的圆形工具栏——"图片处理"接一张 image 节点（文生图不接），"模型处理"
 * 接一个 model/model_part 节点）：
 * - `decompose`：素材分解卡片，接一张 image 节点，调 Tripo3D
 *   mesh/smartsegment 产出 N 个 model_part 素材。
 * - `to_model`：2D 转 3D 卡片，接一张 image 节点，调 Tripo3D
 *   generation/image-to-model 产出 1 个 model 素材（不分部件）。
 * - `mesh_complete`：网格补全卡片，接一个 model/model_part 节点，调 Tripo3D
 *   mesh/segment + mesh/complete 修复模型上的破洞，产出 1 个 model 素材。
 * - `mesh_decimate`：拓扑重构卡片，接一个 model/model_part 节点，调 Tripo3D
 *   mesh/decimate 做减面重拓扑，产出 1 个 model 素材。
 * - `texture`：纹理贴图卡片，接一个 model/model_part 节点，调 Tripo3D
 *   models/texture 重新生成贴图，产出 1 个带贴图的 model 素材。
 * - `rig_check`：骨骼检查卡片，接一个模型，调 Tripo3D animations/rig-check
 *   回答"能否绑定 + 推荐骨架"，**不产出素材**（结果只在卡片上展示）。
 * - `auto_rig`：自动绑定卡片，接一个模型，调 Tripo3D animations/rig 加骨架，
 *   产出 1 个带骨架的 model 素材（并记下绑定任务 id）。
 * - `retarget`：动画重定向卡片，接一个已绑定的模型，调 Tripo3D
 *   animations/retarget 套预设动画，产出 1 个带动画的 model 素材。
 * - `text2image`：文生图卡片（镜像导演模式实验室的同一张卡），只吃文字
 *   提示，调 generate_visual 产出一张角色/世界图片素材。
 * - `image_ref`：图生图卡片（镜像导演模式实验室的"图片参考"），吃文字提示
 *   + 一张或多张参考图，调 generate_visual 的 reference_image_paths 产出
 *   一张角色/世界图片素材。
 *
 * model/model_part 节点除了接收处理卡片产出时用的 `in` 入口（Position.Left）
 * 外，还有一个 `out` 出口（Position.Right），好让 mesh_complete/mesh_decimate
 * 这类"输入也是模型"的处理卡片能接到它们身上，形成链式处理
 * （分解/2D转3D的产出 -> 网格补全 -> 拓扑重构 ...）。
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

/** 网格补全/拓扑重构卡片共用的数据形状——跟 GameToModelNodeData 完全一样
 *  （都是"接一个输入节点、提交一次、轮询到成功产出一个结果"），只是输入
 *  节点换成了模型而不是图片，见 GameMeshOpNode.tsx。*/
export type GameMeshOpNodeData = GameToModelNodeData;

/** 纹理贴图卡片的数据形状：与网格处理同为"接一个模型、提交一次、轮询到
 *  成功产出一个模型"，只是多了可选的风格提示词与贴图质量两个参数。 */
export type GameTextureNodeData = GameMeshOpNodeData & {
  prompt: string;
  quality: 'fast' | 'standard' | 'detailed' | 'extreme';
};

/** 骨骼检查卡片：不产出素材，结果（riggable / 推荐骨架）直接落在节点上。
 *  因为后端不会为它建占位素材，刷新后只能按 jobId 重新接上轮询。 */
export type GameRigCheckNodeData = {
  status: 'idle' | 'submitting' | 'pending' | 'success' | 'failed';
  error?: string;
  jobId?: string;
  riggable?: boolean;
  rigType?: string;
};

/** 自动绑定卡片：接一个模型、产出一个带骨架的模型；rigType/spec 是提交时
 *  的可选项，成功后由后端写进素材 params，供流程图还原。 */
export type GameAutoRigNodeData = GameMeshOpNodeData & {
  rigType: string;
  spec: string;
};

/** 动画重定向卡片：接一个已绑定的模型，套用 animation 预设。 */
export type GameRetargetNodeData = GameMeshOpNodeData & {
  animation: string;
  animateInPlace: boolean;
};

/** 文生图/图生图卡片共用的数据形状：两张卡都是"读文字提示 + 可选参考图，
 *  同步调一次 game.assets.generate，成功后产出一张图片素材"。与分解/
 *  2D 转 3D 的差别是后端调用是同步的（generate_visual 自己等到出图），
 *  所以没有 placeholderAssetId/jobId 这套轮询字段。 */
export type GameImageGenNodeData = {
  prompt: string;
  /** 产出图片归到「角色」还是「世界」——游戏工坊的图片素材只有这两个类目
   *  （见 GAME_ASSET_TYPES），分解/2D 转 3D 也只吃这两个类目。 */
  assetType: 'character' | 'world';
  status: 'idle' | 'submitting' | 'success' | 'failed';
  error?: string;
  resultAssetId?: string;
};
