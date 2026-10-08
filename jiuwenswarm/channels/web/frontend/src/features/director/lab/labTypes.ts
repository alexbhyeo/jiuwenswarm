// 实验室（Lab）节点画布的数据模型。拖入/生成的素材本身是真实的
// DirectorAsset（写入 director_state.json）；画布的卡片摆放/连线/参数
// 快照随 DirectorProject.lab_nodes/lab_edges 一起持久化（见 LabCanvas.tsx
// 的 debounce 保存），切换 tab 或刷新页面都不会丢。

export type ProcessKind =
  | 'text2image'
  | 'text2video'
  | 'image2video'
  | 'imageRef'
  | 'text2audio'
  | 'video2audio'
  | 'imageAudio2video';

/** 处理节点的产物类型。audio 走 generate_audio（文生音频）：与 image/video
 *  一样是一张处理卡片 + 一张输出节点，只是输出节点渲染成音频播放器。
 *  imageAudio2video（图音生视频）的产物仍然是视频。 */
export type ProcessMode = 'image' | 'video' | 'audio';

export const PROCESS_KIND_MODE: Record<ProcessKind, ProcessMode> = {
  text2image: 'image',
  text2video: 'video',
  image2video: 'video',
  imageRef: 'image',
  text2audio: 'audio',
  video2audio: 'audio',
  imageAudio2video: 'video',
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
  // 语音合成只吃文本（见 audio_gen_tools.generate_audio），没有参考图端口。
  text2audio: 0,
  // 视频生音频的输入是视频不是图（见 PROCESS_KIND_MAX_VIDEOS）。
  video2audio: 0,
  // 图音生视频的参考图是"多模态参考"里的图片参考（不是首帧），一个端口
  // 就够——参考音频另算，见 PROCESS_KIND_MAX_AUDIOS。
  imageAudio2video: 1,
};

/** 每种处理节点渲染几个音频输入端口。目前只有 imageAudio2video
 *  （图音生视频）吃一路参考音频：模型按这段音频做口型同步，其余卡片都
 *  不吃音频。 */
export const PROCESS_KIND_MAX_AUDIOS: Record<ProcessKind, number> = {
  text2image: 0,
  text2video: 0,
  image2video: 0,
  imageRef: 0,
  text2audio: 0,
  video2audio: 0,
  imageAudio2video: 1,
};

/** 每种处理节点渲染几个视频输入端口。目前只有 video2audio（视频生音频）
 *  需要一路输入视频（先用视频理解写成解说文案，再用 TTS 配音），其余
 *  卡片都不吃视频。 */
export const PROCESS_KIND_MAX_VIDEOS: Record<ProcessKind, number> = {
  text2image: 0,
  text2video: 0,
  image2video: 0,
  imageRef: 0,
  text2audio: 0,
  video2audio: 1,
  imageAudio2video: 0,
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

/** 音频输出节点（文生音频的产物）。字段与 VideoNodeData 一致——两者都只是
 *  “一个可播放的产物 + 名字”，渲染成 <audio> 还是 <video> 由节点类型决定。 */
export interface AudioNodeData {
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
  /** 仅 text2audio（文生音频）使用：TTS 音色名。旧数据没有这个字段时按
   *  DEFAULT_AUDIO_VOICE 处理。 */
  voice?: string;
  /** 仅 imageAudio2video（图音生视频）使用：成片是否带音轨。参考音频既是
   *  口型同步的依据、也是成片音轨的来源，但有时用户只想要一段无声的成片
   *  （比如当作纯画面素材），所以做成卡片上的显式选项。旧数据/未设置时按
   *  true（有声音）处理，与该卡片最初的行为一致。 */
  generateAudio?: boolean;
  /** 仅 imageAudio2video（图音生视频）使用：参考音频的 HTTPS 直链，作为
   *  "参考音频"端口连线的替代输入——手头只有一条可公网访问的音频地址（比如
   *  对象存储里的 wav）时，不必先把它上传成素材再连线。非空时优先于端口
   *  连线（见 ProcessNode 的 audioSourceInUse）。 */
  audioUrl?: string;
  /** 一次"生成"要产出几份独立结果（1-5），每份各自成一个输出节点，从
   *  同一个 "out" 端口扇出多条连线——不是同一份结果的多个帧，而是同样的
   *  提示词/参数各自独立生成 N 次，供用户挑选。旧数据没有这个字段时按 1
   *  处理（行为与改造前完全一致）。 */
  outputCount: number;
}
