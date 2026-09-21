// 导演模式（Director Mode）前端类型定义。
// 与后端 director_store.py 的 DirectorAsset / DirectorProject 结构对应。

export type DirectorAssetType = 'video' | 'image' | 'character';
export type DirectorAssetStatus = 'ready' | 'pending' | 'failed';

export interface DirectorAsset {
  asset_id: string;
  type: DirectorAssetType;
  status: DirectorAssetStatus;
  prompt: string;
  params: Record<string, unknown>;
  file_path: string | null;
  job_id: string | null;
  error: string | null;
  /** 用户自定义名称；为空时前端展示 prompt。图片素材命名后可在同项目
   *  composer 提示词里用 "@名称" 引用为参考图。 */
  name: string | null;
  created_at: number;
  updated_at: number;
}

export type EditChatRole = 'user' | 'assistant';

export interface EditChatMessage {
  role: EditChatRole;
  content: string;
  image_asset_ids: string[];
  created_at: number;
}

export interface DirectorProject {
  project_id: string;
  name: string;
  created_at: number;
  updated_at: number;
  assets: DirectorAsset[];
  edit_chat_messages: EditChatMessage[];
  /** 实验室节点画布快照（卡片位置/连线/每张处理卡片的参数）。结构由
   *  @xyflow/react 的 Node/Edge 定义，这里按后端一样的不透明 dict 对待——
   *  LabCanvas.tsx 负责序列化/反序列化成真正的 Node[]/Edge[]。 */
  lab_nodes: Record<string, unknown>[];
  lab_edges: Record<string, unknown>[];
}

export interface DirectorAssetCounts {
  video: number;
  image: number;
  character: number;
}

/** 创作 composer 支持的模式；video/image/character 有真实后端，其余为
 *  "即将推出"占位。 */
export type ComposerMode = 'video' | 'image' | 'audio' | 'character' | 'world';

export const ENABLED_COMPOSER_MODES: readonly ComposerMode[] = ['video', 'image', 'character'];

export interface ComposerParams {
  aspectRatio: string;
  resolution: string;
  durationSeconds: number;
  generateAudio: boolean;
  /** 仅 角色 模式使用：拼进生成描述末尾的画风提示（如"写实摄影"），
   *  不改变 composer 里 "名称: 描述" 的原始文本。 */
  characterStyle: string;
}

export interface GenerateParams {
  projectId: string;
  mode: 'video' | 'image' | 'character';
  prompt: string;
  aspectRatio: string;
  resolution: string;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** 实验室节点画布：连线的显式引用（asset_id），绕开 composer 的
   *  "@名称" 文本解析。video 模式下最多首帧+尾帧两个；image 模式下最多一个。 */
  firstFrameAssetId?: string;
  lastFrameAssetId?: string;
  referenceAssetId?: string;
}

export interface GenerateResult {
  project: DirectorProject;
  assetId: string;
  assetCounts: DirectorAssetCounts;
}

export interface CheckStatusResult {
  project: DirectorProject;
  assetId: string;
  status: DirectorAssetStatus | 'pending';
  assetCounts: DirectorAssetCounts;
}

/** 后端 RPC 错误 code，见 director_manager.DirectorRpcError。 */
export type DirectorErrorCode =
  | 'INVALID_PARAMS'
  | 'NOT_SUPPORTED'
  | 'NOT_CONFIGURED'
  | 'PROJECT_NOT_FOUND'
  | 'ASSET_NOT_FOUND'
  | 'GENERATION_FAILED';

export class DirectorApiError extends Error {
  code: DirectorErrorCode | string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = 'DirectorApiError';
  }
}

export type DirectorTabKey = 'create' | 'lab' | 'edit';

/** 从 素材 面板拖拽资产到 实验室 画布时，dataTransfer 上携带的 MIME 类型
 *  与负载结构。 */
export const DIRECTOR_ASSET_DRAG_MIME = 'application/x-director-asset';

export interface DirectorAssetDragPayload {
  assetId: string;
  type: DirectorAssetType;
  filePath: string;
  name: string;
}
