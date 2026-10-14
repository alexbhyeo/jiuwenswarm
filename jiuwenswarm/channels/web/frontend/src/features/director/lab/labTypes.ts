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
  | 'imageAudio2video'
  | 'video2video'
  | 'videoBridge';

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
  // 视频生视频的产物是视频——因此它自动获得"有声音/无声音"选项
  // （见 supportsAudioOutput），音轨默认沿用（见 defaultGenerateAudio）。
  video2video: 'video',
  // 视频连视频（FLF2V 桥接）的产物也是视频。
  videoBridge: 'video',
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
  // 视频生视频不吃图片（源片通过视频端口/HTTPS 直链进来），重绘完全由
  // 提示词驱动，见 PROCESS_KIND_MAX_VIDEOS。
  video2video: 0,
  // 视频连视频的首尾帧是从两段视频素材自动抓出来的，不接图片端口，见
  // PROCESS_KIND_MAX_VIDEOS。
  videoBridge: 0,
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
  // 源片（视频）本身的音轨就是"音频输入"，没有第二条音频来路。
  video2video: 0,
  // 桥接片没有参考音频——两段源片各自的音轨与过渡片无关。
  videoBridge: 0,
};

/** "有声音/无声音"这个成片选项适用于哪些卡片。
 *
 *  判据是**产物是不是视频**，而不是"有没有音频输入端口"：图音生视频
 *  （参考音频当口型依据）、文生视频/图生视频（让模型自己配一条音轨）都
 *  会产出带音轨的视频，所以都要能选；文生图/参考图合成产的是静态图，
 *  视频生音频产的是音频，都没有"成片音轨"这回事。 */
export function supportsAudioOutput(kind: ProcessKind): boolean {
  return PROCESS_KIND_MODE[kind] === 'video';
}

/** 新建卡片时"有声音/无声音"的初始值。
 *
 *  只有**输入本身就是音频**的卡片（图音生视频）默认开声——它的输入就是
 *  一段音轨，默认给成片配上最符合预期。文生视频/图生视频的输入是文字或
 *  图片，历史上产出的都是无声片段，默认沿用"无声音"以免悄悄改变已有
 *  画布的行为；想要音轨的用户在卡片上显式选"有声音"。
 *
 *  视频生视频的输入是一段**带音轨的源片**，默认开声——重绘画面不该顺手把
 *  源片的声音抹掉，静音是用户显式选择的结果。 */
export function defaultGenerateAudio(kind: ProcessKind): boolean {
  if (kind === 'video2video') return true;
  return PROCESS_KIND_MAX_AUDIOS[kind] > 0;
}

/** 每种处理节点渲染几个视频输入端口。目前 video2audio（视频生音频，先用视频
 *  理解写成解说文案再用 TTS 配音）与 video2video（视频生视频，拿源片当参考
 *  重绘）各需要一路输入视频，其余卡片都不吃视频。 */
export const PROCESS_KIND_MAX_VIDEOS: Record<ProcessKind, number> = {
  text2image: 0,
  text2video: 0,
  image2video: 0,
  imageRef: 0,
  text2audio: 0,
  video2audio: 1,
  imageAudio2video: 0,
  // 视频生视频必须拿到一路源视频——端口连线，或卡片上填的 HTTPS 直链。
  video2video: 1,
  // 视频连视频需要两路源视频：片段一（取最后一帧）+ 片段二（取第一帧）。
  videoBridge: 2,
};

/** 哪些处理节点的 image1 端口允许同时接多条连线——imageRef（"图片参考"）
 *  是多参考图合成，一张或多张参考图都合法，与后端 generate_visual 的
 *  reference_image_paths（列表）一一对应；image2video 的首帧/尾帧是两个
 *  语义不同的独立槽位，没有"多张首帧"的概念，仍然各自只接一条线。 */
export const PROCESS_KIND_MULTI_REF: Partial<Record<ProcessKind, boolean>> = {
  imageRef: true,
};

/** 视频连视频（videoBridge）卡片落到画布时自动带出的提示词节点内容。
 *
 *  FLF2V（首帧+尾帧生视频）只拿到两张静态图，模型需要文字才知道该怎么从
 *  第一张"动"到第二张——这张卡片没有提示词就没有生成目标（见
 *  director_manager.py 的 video_bridge 分支）。默认文案参考了
 *  InfinityStory（arXiv:2603.03646）论文里 FLF2V 过渡镜头的训练设定：首帧
 *  精确匹配、尾帧尽量匹配、运动连续自然、两段画面差异较大时改用镜头运动或
 *  环境过渡来衔接、角色不能在画面里突然出现/消失或从画面边缘"抠"出半个身子，
 *  以及成片要能直接拼接在两段原片之间。用户可以直接在文本节点里改写它，
 *  这里只是一个经过验证的起点，不是写死的唯一选项。 */
export const DEFAULT_VIDEO_BRIDGE_PROMPT =
  'Generate a seamless cinematic transition that bridges two video clips.\n\n' +
  'The first frame of this transition must match the provided start reference exactly (the last frame of the preceding clip). The final frame must match the provided end reference as closely as possible (the first frame of the following clip).\n\n' +
  'Create continuous, natural motion that connects the two visual states across the full duration of the clip, with a clear beginning, middle and end rather than motion that stalls or jumps. Maintain subject identity, object geometry, camera perspective, lighting, color grading and environmental consistency wherever the two reference frames allow it.\n\n' +
  'If the two reference frames depict clearly different scenes, locations or subjects, do not force an impossible continuity: instead use a natural camera movement (pan, push-in, pull-out, whip, or a brief environmental transition such as passing through a doorway, fog, or motion blur) to carry the viewer from one scene into the other.\n\n' +
  'Characters already visible in a reference frame must not vanish or appear only partially; characters entering the shot must enter naturally from the frame edge or background rather than fading or morphing in. Avoid abrupt cuts, frozen motion, flickering, ghosting, duplicated subjects, distorted faces, sudden object changes and unnatural morphing.\n\n' +
  'Do not add new characters, objects, text or visual effects that are not implied by the two reference frames.\n\n' +
  "The transition must be suitable for direct concatenation between the original clips: match their visual style, aspect ratio, frame rate feel and motion continuity so the join is invisible.\n\n" +
  'Output only the transition video.';

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
  /** 视频类卡片（文生视频 / 图生视频 / 图音生视频 / 视频生视频）的"声音"选项：
 *  成片是否带音轨。各种卡片的音轨来源不同——
   *  * imageAudio2video：参考音频既是口型同步的依据、也是成片音轨的来源；
   *  * video2video：源片自带的音轨；选"无声音"就是只要画面；
   *  * text2video / image2video：没有参考音频，选"有声音"就是让生成模型
   *    自己配一条音轨（环境音/人声等），选"无声音"则只要画面。
   *  所以取值由卡片上的显式选项决定，见 supportsAudioOutput / defaultGenerateAudio
   *  （默认值随卡片不同：以音频为输入的默认开声，其余默认静音）。 */
  generateAudio?: boolean;
  /** 仅 imageAudio2video（图音生视频）使用：参考音频的 HTTPS 直链，作为
   *  "参考音频"端口连线的替代输入——手头只有一条可公网访问的音频地址（比如
   *  对象存储里的 wav）时，不必先把它上传成素材再连线。非空时优先于端口
   *  连线（见 ProcessNode 的 audioSourceInUse）。 */
  audioUrl?: string;
  /** 仅 video2video（视频生视频）使用：源视频的 HTTPS 直链，作为"源视频"
   *  端口连线的替代输入。生视频接口只接受 **HTTPS** 的 video_url 引用
   *  （data: URI 与本地文件一律被拒），所以素材库里连过来的本地片段送不进
   *  去——那种情况后端会明确报错，而不是让用户在生成失败里猜原因。非空时
   *  优先于端口连线（同 audioUrl 的取舍，避免"连了素材却在用链接"）。 */
  videoUrl?: string;
  /** 一次"生成"要产出几份独立结果（1-5），每份各自成一个输出节点，从
   *  同一个 "out" 端口扇出多条连线——不是同一份结果的多个帧，而是同样的
   *  提示词/参数各自独立生成 N 次，供用户挑选。旧数据没有这个字段时按 1
   *  处理（行为与改造前完全一致）。 */
  outputCount: number;
}
