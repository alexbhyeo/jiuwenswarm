// 实验室（Lab）节点画布的数据模型。拖入/生成的素材本身是真实的
// DirectorAsset（写入 director_state.json）；画布的卡片摆放/连线/参数
// 快照随 DirectorProject.lab_nodes/lab_edges 一起持久化（见 LabCanvas.tsx
// 的 debounce 保存），切换 tab 或刷新页面都不会丢。

export type ProcessKind = 'text2image' | 'text2video' | 'image2video' | 'imageRef';

export const PROCESS_KIND_MODE: Record<ProcessKind, 'image' | 'video'> = {
  text2image: 'image',
  text2video: 'video',
  image2video: 'video',
  imageRef: 'image',
};

/** 每种处理节点接受的图片输入上限——与后端 generate_video（首帧+尾帧）/
 *  generate_visual（单张参考图）的真实能力一一对应，不做画布端的虚假承诺。 */
export const PROCESS_KIND_MAX_IMAGES: Record<ProcessKind, number> = {
  text2image: 0,
  text2video: 0,
  image2video: 2,
  imageRef: 1,
};

export interface ImageNodeData {
  [key: string]: unknown;
  assetId: string | null;
  filePath: string;
  name: string;
}

export interface VideoNodeData {
  [key: string]: unknown;
  assetId: string | null;
  filePath: string;
  name: string;
}

export interface TextNodeData {
  [key: string]: unknown;
  text: string;
}

export type ProcessStatus = 'idle' | 'generating' | 'error';

export interface ProcessNodeData {
  [key: string]: unknown;
  kind: ProcessKind;
  status: ProcessStatus;
  error: string | null;
  aspectRatio: string;
  resolution: string;
  durationSeconds: number;
}
