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

/** 视频连视频（videoBridge）卡片落到画布时预填进「文字提示」弹窗的内容。
 *
 *  FLF2V（首帧+尾帧生视频）只拿到两张静态图，模型需要文字才知道该怎么从
 *  第一张"动"到第二张——这张卡片没有提示词就没有生成目标（见
 *  director_manager.py 的 video_bridge 分支）。文案落在卡片自带的
 *  `ProcessNodeData.prompt` 上（点「文字提示」那一行弹出的小窗口），与
 *  文生图/图生图等卡片同一处，不会额外摆一张文本卡片出来。
 *  默认文案参考了
 *  InfinityStory（arXiv:2603.03646）论文里 FLF2V 过渡镜头的训练设定：首帧
 *  精确匹配、尾帧尽量匹配、运动连续自然、两段画面差异较大时改用镜头运动或
 *  环境过渡来衔接、角色不能在画面里突然出现/消失或从画面边缘"抠"出半个身子，
 *  以及成片要能直接拼接在两段原片之间。用户可以直接改写它，
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

/** 处理卡片自带的一条历史产物记录——卡片不再需要单独一张输出节点，生成
 *  结果直接追加进卡片自己的 history。"重新生成"永远往后追加一条、指针
 *  指向最新；"切回上一个"只挪指针，不删记录，可以再切回来。 */
export interface GenerationEntry {
  assetId: string | null;
  filePath: string;
  name: string;
}

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
  /** 仅音频类卡片（文生音频 / 视频生音频）使用：TTS 风格描述
   *  （audioStyles.ts 的 instruction 文本）。旧数据没有这个字段时按"未指定"
   *  处理，即请求里不带风格，与改造前完全一致。 */
  style?: string;
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
  /** 卡片自带的文字提示——点"文字提示"这一行弹出的小窗口里填的内容，作为
   *  "文字提示"端口连线的替代输入，跟 audioUrl/videoUrl 同一个取舍：非空
   *  时优先于端口连线（见 ProcessNode 的 text 取值）。多数卡片不需要再手动
   *  拖一个文本节点出来连线了；"文字提示"端口依然保留——chat 生成流程
   *  （buildFlowFromChat）重建画布时仍然靠它接一个真实的文本节点，连了线
   *  的画布不受这个新字段影响（这里留空就行）。 */
  prompt?: string;
  /** 一次"生成"要产出几份独立结果（1-5）——只在第一次生成之前有意义：
   *  这时每一份各自克隆成一张独立的卡片（各自的 history 从空开始，共享
   *  同一批输入连线），而不是同一张卡片里的多份结果。生成过一次之后这个
   *  字段就不再被读取（画布上要几份，直接删/留对应的卡片即可），仅在参数
   *  弹窗里首次生成前可见。旧数据没有这个字段时按 1 处理。 */
  outputCount: number;
  /** 这张卡片的产物历史——卡片与它的"输出"合并成一张之后，generate 不再
   *  另开一张输出节点，而是往这里追加一条 GenerationEntry。空数组＝还没
   *  生成过（渲染态＝"idle"，跟今天的处理卡片一样只有提示词/参数/生成
   *  按钮）；非空＝渲染态＝"generated"，展示 history[activeHistoryIndex]
   *  这一条的预览图/视频/音频，外加"‹ N/M ›"切换条（entries 不止一条
   *  才显示）。 */
  history: GenerationEntry[];
  /** history 里当前生效的下标——决定预览里展示哪一条，也决定下游卡片的
   *  image1/image2/video1/audio1 连线实际读到哪一条产物（见下面的扁平
   *  镜像字段）。 */
  activeHistoryIndex: number;
  /** 镜像 history[activeHistoryIndex]——故意不用 getter：这几个字段要在
   *  LabCanvas.tsx 到处可见的 `{ ...n.data, ...patch }` 浅拷贝模式下原样
   *  存活，也要能直接存进 director_state.json（纯 JSON，不能是函数/
   *  getter）。ProcessNode.tsx 的 useConnectedImage/useConnectedVideo/
   *  useConnectedAudio/useConnectedImages 原样按 { assetId, filePath }
   *  读取顶层字段，合并卡片后这几个 hook 不需要改一行。 */
  assetId: string | null;
  filePath: string;
  name: string;
  /** 这张卡片最初对应的、剪辑助手聊天记录里那条素材的 asset_id——
   *  只在 buildFlowFromChat 第一次摆出这张卡片时写入一次，之后永远不会
   *  被"重新生成"（只追加 history，不改这个字段）或"替换图片"（改的是
   *  history[activeHistoryIndex] 自己的 assetId，不是这个字段）动过。
   *
   *  之所以需要这个单独字段：buildFlowFromChat 每次清空重建时，要靠它
   *  把"清空前画布上的这张卡片"和"这次重建、聊天记录里的哪个素材"对应
   *  起来，从而决定要不要保留清空前积累的 history（见该函数里
   *  preservedHistoryByAssetId 的用法）。一开始图省事直接拿
   *  history[0].assetId 当这个对应关系的 key，结果"替换图片"只换过一次、
   *  还没重新生成过的卡片（history 仍只有一条）会把 history[0].assetId
   *  本身覆盖成换上去的那张图的 id，跟聊天记录里的原始素材 id 对不上，
   *  一清空重建就找不到、换上去的图就丢了——这个字段固定不变，就不会有
   *  这个问题。手动在画布上新建的卡片（不是由聊天记录摆出来的）这个字段
   *  是 null。 */
  originAssetId: string | null;
  /** 这一张卡片要不要展示"详情"——「模型 / 生成参数」那一段，以及输入端口那
   *  一排名字（`undefined`＝跟随工具栏上的全局开关，见 cardDetailPrefs.ts）。
   *  收起后端口本身仍然存在（退化成卡片左边缘的裸圆点，见 ProcessNode 的
   *  lab-node-ports--compact），只是不再显示标签。
   *
   *  放在节点数据里而不是别的什么全局表：它描述的是"这一张卡片长什么样"，
   *  得跟着画布一起持久化，也要在清空重建（生成实验室流程图）后由
   *  preservedHistoryByAssetId 一起恢复，否则用户逐卡调好的显示状态每次
   *  重建都会被抹掉。 */
  detailVisible?: boolean;
}
