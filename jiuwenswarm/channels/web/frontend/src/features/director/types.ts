// 导演模式（Director Mode）前端类型定义。
// 与后端 director_store.py 的 DirectorAsset / DirectorProject 结构对应。

export type DirectorAssetType = 'video' | 'image' | 'audio' | 'character';
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
  audio: number;
  character: number;
}

/** 创作 composer 支持的模式；video/image/audio/character 有真实后端，
 *  world 仍为 "即将推出"占位。 */
export type ComposerMode = 'video' | 'image' | 'audio' | 'character' | 'world';

export const ENABLED_COMPOSER_MODES: readonly ComposerMode[] = ['video', 'image', 'audio', 'character'];

export interface ComposerParams {
  aspectRatio: string;
  resolution: string;
  durationSeconds: number;
  generateAudio: boolean;
  /** 仅 音频 模式使用：TTS 音色名（如 "Kore"）。 */
  voice: string;
  /** 仅 角色 模式使用：拼进生成描述末尾的画风提示（如"写实摄影"），
   *  不改变 composer 里 "名称: 描述" 的原始文本。 */
  characterStyle: string;
}

export interface GenerateParams {
  projectId: string;
  /** `video2audio`（视频生音频）与 `image_audio2video`（图音生视频）都是后端
   *  独立模式：前者串了"视频理解写解说 → TTS 配音"两步，后者是一次带参考图
   *  + 参考音频的 reference-to-video 调用，光凭 mode=video/audio 外加几个
   *  输入素材是区分不出来的。 */
  mode: 'video' | 'image' | 'audio' | 'character' | 'video2audio' | 'image_audio2video';
  prompt: string;
  aspectRatio: string;
  resolution: string;
  durationSeconds?: number;
  generateAudio?: boolean;
  /** 仅 音频 模式使用：TTS 音色名。 */
  voice?: string;
  /** 仅 video2audio（视频生音频）使用：用作解说素材的输入视频素材 id。 */
  inputVideoAssetId?: string;
  /** 仅 image_audio2video（图音生视频）使用：作为口型同步依据的参考音频
   *  素材 id（参考图走 referenceAssetIds）。与 inputAudioUrl 二选一，两者
   *  都给出时以 inputAudioUrl 为准。 */
  inputAudioAssetId?: string;
  /** 仅 image_audio2video（图音生视频）使用：参考音频的公网 HTTPS 直链，
   *  无需先上传成素材。非空时优先于 inputAudioAssetId。 */
  inputAudioUrl?: string;
  /** 实验室节点画布：连线的显式引用（asset_id），绕开 composer 的
   *  "@名称" 文本解析。video 模式下最多首帧+尾帧两个；image 模式下可以是
   *  "图片参考" 卡片上连的一张或多张参考图（多参考图合成）。 */
  firstFrameAssetId?: string;
  lastFrameAssetId?: string;
  referenceAssetIds?: string[];
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
