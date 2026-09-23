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

/** 每种处理节点渲染几个不同的图片输入端口——与后端 generate_video（首帧+
 *  尾帧两个语义不同的槽位）的真实能力一一对应，不做画布端的虚假承诺。
 *  imageRef 只有一个端口（image1），但见 PROCESS_KIND_MULTI_REF——那一个
 *  端口本身可以接多条线，不代表"只能引用一张图"。 */
export const PROCESS_KIND_MAX_IMAGES: Record<ProcessKind, number> = {
  text2image: 0,
  text2video: 0,
  image2video: 2,
  imageRef: 1,
};

/** 哪些处理节点的 image1 端口允许同时接多条连线——imageRef（"图片参考"）
 *  是多参考图合成，一张或多张参考图都合法，与后端 generate_visual 的
 *  reference_image_paths（列表）一一对应；image2video 的首帧/尾帧是两个
 *  语义不同的独立槽位，没有"多张首帧"的概念，仍然各自只接一条线。 */
export const PROCESS_KIND_MULTI_REF: Partial<Record<ProcessKind, boolean>> = {
  imageRef: true,
};

export interface ImageNodeData {
  [key: string]: unknown;
  assetId: string | null;
  filePath: string;
  name: string;
  /** 同一个处理节点开了多个输出（见 ProcessNodeData.outputCount）时，
   *  用来在这些输出节点之间保持稳定的先后顺序——按 sourceHandle 分组的
   *  多条边本身不带顺序信息，重新生成/局部替换时靠这个字段找到"第几个
   *  输出槽位"，而不是每次都全部推倒重建。旧数据/手动摆放的节点没有这个
   *  字段时按 0 处理。 */
  slotIndex?: number;
}

export interface VideoNodeData {
  [key: string]: unknown;
  assetId: string | null;
  filePath: string;
  name: string;
  slotIndex?: number;
}

export interface TextNodeData {
  [key: string]: unknown;
  text: string;
}

export type ProcessStatus = 'idle' | 'generating' | 'error';

export const OUTPUT_COUNT_OPTIONS = [1, 2, 3, 4, 5] as const;

export interface ProcessNodeData {
  [key: string]: unknown;
  kind: ProcessKind;
  status: ProcessStatus;
  error: string | null;
  aspectRatio: string;
  resolution: string;
  durationSeconds: number;
  /** 一次"生成"要产出几份独立结果（1-5），每份各自成一个输出节点，从
   *  同一个 "out" 端口扇出多条连线——不是同一份结果的多个帧，而是同样的
   *  提示词/参数各自独立生成 N 次，供用户挑选。旧数据没有这个字段时按 1
   *  处理（行为与改造前完全一致）。 */
  outputCount: number;
}
