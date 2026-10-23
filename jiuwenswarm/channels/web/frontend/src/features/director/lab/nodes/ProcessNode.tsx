import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../../directorStore';
import { useLabActions } from '../LabActionsContext';
import { AUDIO_VOICE_OPTIONS, DEFAULT_AUDIO_VOICE } from '../../audioVoices';
import { AUDIO_STYLE_OPTIONS, DEFAULT_AUDIO_STYLE, audioStyleLabelKey } from '../../audioStyles';
import type { AudioNodeData, ImageNodeData, ProcessNodeData, TextNodeData, VideoNodeData } from '../labTypes';
import type { DirectorAsset } from '../../types';
import {
  OUTPUT_COUNT_OPTIONS,
  PROCESS_KIND_MAX_AUDIOS,
  PROCESS_KIND_MAX_IMAGES,
  PROCESS_KIND_MAX_VIDEOS,
  PROCESS_KIND_MODE,
  PROCESS_KIND_MULTI_REF,
  supportsAudioOutput,
} from '../labTypes';
import { MediaFrame, popoverPositionFromTrigger } from './MediaFrame';
import { filterSwappableAssets } from '../swapAssets';
import { NodeNameLabel } from './NodeNameLabel';
import { PromptTextarea } from './PromptTextarea';
import { renderWithImageChips } from './promptChips';

const ASPECT_RATIO_OPTIONS = ['16:9', '9:16', '1:1', '4:3'];
const IMAGE_RESOLUTION_OPTIONS = ['512', '768', '1024'];
const VIDEO_RESOLUTION_OPTIONS = ['480p', '720p', '1080p'];
const DURATION_OPTIONS = [5, 10, 15];

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

const generateIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M13 2 4 14h6l-1 8 9-12h-6z" />
  </svg>
);

const chevronDown = (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
    <path d="m6 9 6 6 6-6" />
  </svg>
);

const closeIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);

const chevronLeftIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
    <path d="m15 18-6-6 6-6" />
  </svg>
);

const chevronRightIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
    <path d="m9 18 6-6-6-6" />
  </svg>
);

const swapIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 3l4 4-4 4M21 7H7M7 21l-4-4 4-4M3 17h14" />
  </svg>
);

const enlargeIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
  </svg>
);

const downloadIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v12M7 10l5 5 5-5M4 19h16" />
  </svg>
);

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 下载按钮的建议文件名——跟 DirectorRail.tsx 的 downloadFileName 同一个
 *  取舍（主文件名用素材名/提示词，后缀照抄磁盘上那份文件自己的后缀），
 *  这里单独写一份而不是跨模块共享，是因为两边拿到的输入形状不一样
 *  （这里是 GenerationEntry，那边是 DirectorAsset），为了这一个小差异
 *  去抽象反而更绕。 */
function downloadFileName(name: string, filePath: string, fallback: string): string {
  const base = (name || fallback).trim() || fallback;
  const match = /\.[a-zA-Z0-9]+$/.exec(filePath);
  return match ? `${base}${match[0]}` : base;
}

function useConnectedText(nodeId: string, handleId: string): string {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  return sourceId ? ((sourceData?.data as unknown as TextNodeData)?.text ?? '') : '';
}

function useConnectedImage(nodeId: string, handleId: string): { assetId: string | null; filePath: string } | null {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  if (!sourceId || !sourceData) return null;
  const data = sourceData.data as unknown as ImageNodeData;
  return { assetId: data.assetId, filePath: data.filePath };
}

/** 与 useConnectedImage 相同，但收集这个端口上全部的连线（不只是第一条）
 *  ——供 imageRef（"图片参考"）多参考图合成使用，image1 端口在这种卡片上
 *  可以同时接多张参考图。useNodesData 支持传数组直接批量取，不需要在
 *  循环里逐个调用 hook（违反 hooks 规则）。 */
function useConnectedImages(nodeId: string, handleId: string): { assetId: string | null; filePath: string }[] {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId });
  const sourceIds = connections.map((c) => c.source);
  const sourcesData = useNodesData(sourceIds);
  return sourcesData.map((s) => {
    const data = s.data as unknown as ImageNodeData;
    return { assetId: data.assetId, filePath: data.filePath };
  });
}

/** video2audio（视频生音频）与 video2video（视频生视频）的 video1 端口：取
 *  那一路输入视频。与 useConnectedImage 同构，只是读的是视频输出节点的 data。 */
function useConnectedVideo(nodeId: string, handleId: string): { assetId: string | null; filePath: string } | null {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  if (!sourceId || !sourceData) return null;
  const data = sourceData.data as unknown as VideoNodeData;
  return { assetId: data.assetId, filePath: data.filePath };
}

/** imageAudio2video（图音生视频）的 audio1 端口：取那一路参考音频——模型
 *  按它做口型同步。与 useConnectedVideo 同构，读的是音频输出节点的 data。 */
function useConnectedAudio(nodeId: string, handleId: string): { assetId: string | null; filePath: string } | null {
  const connections = useNodeConnections({ id: nodeId, handleType: 'target', handleId });
  const sourceId = connections[0]?.source;
  const sourceData = useNodesData(sourceId ?? '');
  if (!sourceId || !sourceData) return null;
  const data = sourceData.data as unknown as AudioNodeData;
  return { assetId: data.assetId, filePath: data.filePath };
}

export function ProcessNode({ id, data }: NodeProps & { data: ProcessNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const project = useDirectorStore((s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null);
  const maxImages = PROCESS_KIND_MAX_IMAGES[data.kind];
  const maxVideos = PROCESS_KIND_MAX_VIDEOS[data.kind];
  const maxAudios = PROCESS_KIND_MAX_AUDIOS[data.kind];
  const mode = PROCESS_KIND_MODE[data.kind];
  const outputCount = data.outputCount || 1;
  const isMultiRef = !!PROCESS_KIND_MULTI_REF[data.kind];

  // 卡片与它的产物合并成一张之后，"已生成过"就是 history 非空——空＝渲染
  // 态跟今天的处理卡片一样（提示词/参数/生成按钮）；非空＝额外展示
  // history[activeHistoryIndex] 这一条的预览图/视频/音频 + 切换条 +
  // "重新生成"。
  const isGenerated = data.history.length > 0;
  const activeEntry = data.history[data.activeHistoryIndex] ?? data.history[data.history.length - 1] ?? null;

  // 卡片自带的文字提示非空时优先于"文字提示"端口连线——跟下面 audioUrl/
  // videoUrl 同一个取舍，见 labTypes.ts 的 ProcessNodeData.prompt 注释。
  const connectedText = useConnectedText(id, 'text');
  const inlinePrompt = (data.prompt ?? '').trim();
  const text = inlinePrompt || connectedText;
  const promptFromPort = !inlinePrompt && connectedText.length > 0;

  const namedImageAssets = useMemo(() => {
    if (!project) return [];
    const seen = new Set<string>();
    const result: DirectorAsset[] = [];
    for (const asset of project.assets) {
      if ((asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.name && !seen.has(asset.name)) {
        seen.add(asset.name);
        result.push(asset);
      }
    }
    return result;
  }, [project]);

  // "替换"弹层里挑素材用的列表——跟上面 namedImageAssets 不同，这里不按
  // 名字去重（@ 提及自动补全要去重是为了候选不重复，这里是要把素材库里
  // 每一张图/每一段视频都列出来让用户选），排除当前卡片正展示的这一条
  // （选它等于什么都没换），按最近更新排在前面，方便找到刚生成/刚上传的那
  // 几张。候选类型跟着卡片产物类型走：图片卡片列图片/角色素材，视频卡片列
  // 视频素材——列错类型的素材换了也放不出来。
  const swappableAssets = useMemo(
    () => (project ? filterSwappableAssets(project.assets, PROCESS_KIND_MODE[data.kind], data.assetId) : []),
    [project, data.assetId, data.kind]
  );

  const image1 = useConnectedImage(id, maxImages >= 1 && !isMultiRef ? 'image1' : '__none__');
  const image2 = useConnectedImage(id, maxImages >= 2 ? 'image2' : '__none__');
  const images = useConnectedImages(id, isMultiRef ? 'image1' : '__none__');
  const video1 = useConnectedVideo(id, maxVideos >= 1 ? 'video1' : '__none__');
  const video2 = useConnectedVideo(id, maxVideos >= 2 ? 'video2' : '__none__');
  const audio1 = useConnectedAudio(id, maxAudios >= 1 ? 'audio1' : '__none__');

  // 参考音频的两条来路：端口连线，或卡片上填的 HTTPS 直链。填了链接就以
  // 链接为准（输入行上会标出来），避免"连了素材却在用链接"这种看不见的替换。
  const audioUrl = (data.audioUrl ?? '').trim();
  const hasAudioUrl = audioUrl.length > 0;
  const audioConnected = !!audio1;

  // 源视频同理：video2video 的源片可以从"源视频"端口连进来，也可以直接填
  // 一条 HTTPS 地址（生视频接口只收 HTTPS 的 video_url 引用，本地素材送不
  // 进去，所以这条链接不是可有可无的便利功能，而是本地片段唯一可行的替代）。
  const videoUrl = (data.videoUrl ?? '').trim();
  const hasVideoUrl = videoUrl.length > 0;
  // "视频链接"只有 video2video 有——video2audio 的输入视频是本地做理解用的，
  // 不发给生视频接口，给个填了也没人读的输入框只会误导用户。
  const hasVideoUrlOption = data.kind === 'video2video';

  const requiresImage = maxImages > 0;
  // 视频生音频必须先连一路视频（解说文案是从它写出来的）；文本节点可选，
  // 连了就当作用户对解说的额外要求。
  const requiresVideo = maxVideos > 0;
  // 视频连视频需要两段源片都连上——只有片段一或只有片段二都抓不出完整的
  // 首尾帧对，FLF2V 没法生成。
  const isVideoBridge = data.kind === 'videoBridge';
  // 图音生视频必须拿到一路参考音频——连了音频素材、或者在卡片上填了音频
  // 链接都算，两条都没有时这张卡片就不再是"图音生视频"了。
  const requiresAudio = maxAudios > 0;
  const hasAnyImage = isMultiRef ? images.length > 0 : !!image1;
  // 视频生视频/视频连视频"改成什么样"完全由提示词指定，没有提示词就没有
  // 生成目标，所以这两张卡片的提示词是必填的（其余卡片允许只靠输入素材
  // 出片）。视频连视频落到画布时会把默认过渡提示词写进卡片自带的
  // 「文字提示」弹窗（见 LabCanvas.handleAddProcessNode 的 data.prompt），
  // 正常情况下这条校验不会被卡到；用户清空提示词后才会。
  const requiresPrompt = data.kind === 'video2video' || isVideoBridge;
  const canGenerate =
    data.status !== 'generating' &&
    (!requiresImage || hasAnyImage) &&
    (!requiresVideo ||
      (isVideoBridge ? !!video1 && !!video2 : !!video1 || (hasVideoUrlOption && hasVideoUrl))) &&
    (!requiresAudio || audioConnected || hasAudioUrl) &&
    (!requiresPrompt || text.trim().length > 0) &&
    (text.trim().length > 0 || hasAnyImage || !!video1 || !!video2 || audioConnected || hasAudioUrl);

  const [paramsOpen, setParamsOpen] = useState(false);
  const paramsRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [swapOpen, setSwapOpen] = useState(false);
  // 换图弹层改成 Portal 到 document.body（见下面渲染处的注释）之后，触发
  // 按钮和弹层本身不再是同一棵 DOM 子树——点击空白处关闭要同时认这两个
  // ref，缺一个都会导致点按钮本身也被误判成"点了外面"。
  const swapWrapRef = useRef<HTMLDivElement>(null);
  const swapPopoverRef = useRef<HTMLDivElement>(null);
  const [swapPos, setSwapPos] = useState<{ top: number; left: number } | null>(null);
  // "放大"按钮调浏览器 Fullscreen API 时要拿到这张 <img> 本身——同
  // DirectorRail.tsx 的 EnlargeableImage，只是那边是独立的参考卡片，
  // 这里是合并卡片"已生成"态里嵌的那张图。
  const mediaImgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (!paramsOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (paramsRef.current && !paramsRef.current.contains(e.target as Node)) setParamsOpen(false);
    };
    // 用捕获阶段而不是默认的冒泡阶段——React Flow 的画布 pane 自己会在
    // mousedown 上 stopPropagation()（用于框选/平移），冒泡阶段监听器永远
    // 等不到点在画布空白处的这次事件，弹层就关不掉了；捕获阶段先于画布
    // 自己的处理跑，不受它 stopPropagation() 影响。
    document.addEventListener('mousedown', handleClick, true);
    return () => document.removeEventListener('mousedown', handleClick, true);
  }, [paramsOpen]);

  // 换图弹层点画布空白处关闭——跟上面 paramsOpen 同一个取舍（捕获阶段，
  // 不受 React Flow pane 自己 stopPropagation() 影响）。弹层本身 Portal 到
  // body 之后已经脱离 swapWrapRef 这棵子树，两个 ref 都要查一遍。
  useEffect(() => {
    if (!swapOpen) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (swapWrapRef.current?.contains(target)) return;
      if (swapPopoverRef.current?.contains(target)) return;
      setSwapOpen(false);
    };
    document.addEventListener('mousedown', handleClick, true);
    return () => document.removeEventListener('mousedown', handleClick, true);
  }, [swapOpen]);

  // 文字提示弹窗用 Portal 挂到 document.body（见下面渲染处的注释），不是
  // .lab-node-params-popover 那种贴着卡片的内联浮层，所以不需要跟它一样
  // 的"点击画布空白处关闭"逻辑——遮罩本身的 onClick 已经处理了关闭。
  const [promptOpen, setPromptOpen] = useState(false);
  // 弹窗默认展示"渲染态"（@名称 带缩略图），点一下才切成可编辑的原始文本
  // ——跟 TextNode 卡片同一套取舍（textarea 没法同时又能编辑又能在文字
  // 中间插入图片），每次重新打开弹窗都先回到渲染态。
  const [promptEditing, setPromptEditing] = useState(false);
  useEffect(() => {
    if (promptOpen) setPromptEditing(false);
  }, [promptOpen]);

  const resolutionOptions = mode === 'video' ? VIDEO_RESOLUTION_OPTIONS : IMAGE_RESOLUTION_OPTIONS;
  // 文生音频没有宽高比/分辨率/时长这些画面参数，改成音色选择。
  const isAudio = mode === 'audio';
  const voice = data.voice || DEFAULT_AUDIO_VOICE;
  // 风格是"怎么说"（空串=未指定，请求里就不发这个字段），与音色正交。
  const audioStyle = data.style ?? DEFAULT_AUDIO_STYLE;
  // 视频类卡片（文生视频/图生视频/图音生视频）都要能选"成片有没有声音"——
  // 判据是产物是不是视频，不是"有没有音频输入端口"，见 supportsAudioOutput。
  const hasAudioOption = supportsAudioOutput(data.kind);
  // "音频链接"是"参考音频"的替代输入，只有本来就吃参考音频的卡片（图音生视频）
  // 才有意义——文生视频/图生视频没有参考音频，给一个填了也没人读的输入框只会
  // 误导用户。所以它单独判据，跟上面的声音选项不是一回事。
  const hasAudioUrlOption = maxAudios >= 1;
  const audioOutput = data.generateAudio !== false;
  const audioOutputLabel = t(audioOutput ? 'director.lab.audioOn' : 'director.lab.audioOff');
  // 文生视频/图生视频没有参考音频，"有声音"的含义是让生成模型自己配一条音轨；
  // 图音生视频的音轨来自连进来的参考音频；视频生视频的音轨来自源片本身。三者
  // 的说明文案不同。
  const audioHintKey =
    data.kind === 'video2video'
      ? 'director.lab.audioOutputFromSourceHint'
      : maxAudios >= 1
        ? 'director.lab.audioOutputFromRefHint'
        : 'director.lab.audioOutputGeneratedHint';

  // "替换"按钮 + 贴着它的素材弹层——图片卡片和视频卡片共用同一套交互，只是
  // 候选素材的类型、缩略图标签、本地文件过滤、提示文案不同。抽成一个变量在
  // 两处各渲染一次，避免复制一整块弹层 JSX（那是之前换图弹层两侧各写一份、
  // 尺寸改了漏改一边的同类问题）。
  //
  // 为什么视频卡片也要能替换：下游卡片（比如视频连视频）是拿上游卡片当前
  // 展示的那一条素材去生成桥接片的，上游只有"重新生成"出来的候选时用户没法
  // 把一段已有的片子换进来。换掉的是 history[activeHistoryIndex] 这一条（连同
  // 扁平镜像），所以端口读数、下游引用、画布持久化全都跟着一起变。
  const canSwapMedia = mode === 'image' || mode === 'video';
  const swapAccept = mode === 'video' ? 'video/*' : 'image/*';
  const swapTitle = t(mode === 'video' ? 'director.lab.swapVideo' : 'director.lab.swapImage');
  const swapEmptyText = t(mode === 'video' ? 'director.lab.swapNoVideoAssets' : 'director.lab.swapNoAssets');
  const swapControl = canSwapMedia ? (
    <div className="lab-node-media-actions">
      <div className="lab-node-swap-wrap nodrag" ref={swapWrapRef}>
        <button
          type="button"
          title={swapTitle}
          onClick={(e) => {
            if (swapOpen) {
              setSwapOpen(false);
              return;
            }
            setSwapPos(popoverPositionFromTrigger(e.currentTarget));
            setSwapOpen(true);
          }}
          data-testid={mode === 'video' ? 'lab-process-node-swap-video' : 'lab-process-node-swap-image'}
        >
          {swapIcon}
        </button>
        {/* .lab-node-media 有 overflow:hidden 裁掉超出卡片的内容（给图片裁
            圆角用），弹层自己比卡片高得多，不 Portal 出去会被从中间切掉——
            同 promptOpen 那个弹窗一样挂到 body，只是那个是居中遮罩，这个是
            贴着按钮的浮层，所以用 popoverPositionFromTrigger 算 fixed 定位
            而不是遮罩居中。 */}
        {swapOpen &&
          swapPos &&
          createPortal(
            <div
              className="lab-node-swap-popover nowheel"
              ref={swapPopoverRef}
              style={{ top: swapPos.top, left: swapPos.left }}
              data-testid="lab-process-node-swap-popover"
              data-variant={mode}
            >
              <button
                type="button"
                className="lab-node-swap-popover-upload"
                onClick={() => {
                  setSwapOpen(false);
                  fileInputRef.current?.click();
                }}
                data-testid="lab-process-node-swap-upload-local"
              >
                {t('director.lab.swapUploadLocal')}
              </button>
              <div className="lab-node-swap-popover-grid">
                {swappableAssets.length === 0 ? (
                  <div className="lab-node-swap-popover-empty">{swapEmptyText}</div>
                ) : (
                  swappableAssets.map((asset) => (
                    <button
                      type="button"
                      key={asset.asset_id}
                      className="lab-node-swap-popover-item"
                      title={asset.name || asset.prompt || ''}
                      onClick={() => {
                        setSwapOpen(false);
                        actions.swapMediaFromAsset(id, {
                          assetId: asset.asset_id,
                          filePath: asset.file_path as string,
                          name: asset.name || asset.prompt || '',
                        });
                      }}
                      data-testid="lab-process-node-swap-asset"
                      data-variant={asset.asset_id}
                    >
                      {/* 视频素材用 <video> 当缩略图：#t=0.1 让浏览器解码出
                          第一帧当封面，不加这个片段时多数浏览器只显示一片黑。 */}
                      {mode === 'video' ? (
                        <video src={`${rawFileUrl(asset.file_path as string)}#t=0.1`} muted playsInline preload="metadata" />
                      ) : (
                        <img src={rawFileUrl(asset.file_path as string)} alt={asset.name || ''} />
                      )}
                    </button>
                  ))
                )}
              </div>
            </div>,
            document.body
          )}
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept={swapAccept}
        className="lab-node-swap-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) actions.swapMedia(id, file);
          e.target.value = '';
        }}
      />
    </div>
  ) : null;

  return (
    <div
      className={`lab-node lab-node--process lab-node--process-${data.kind}`}
      data-testid="lab-process-node"
      data-variant={isGenerated ? 'generated' : 'idle'}
    >
      <div className="lab-node-label">
        {t(`director.lab.process.${data.kind}`)}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>

      <div className="lab-node-ports">
        <div
          className="lab-node-input-row lab-node-input-row--clickable nodrag"
          role="button"
          tabIndex={0}
          title={t('director.lab.textPromptEditHint')}
          onClick={() => setPromptOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              setPromptOpen(true);
            }
          }}
          data-testid="lab-process-node-prompt-row"
        >
          <Handle type="target" position={Position.Left} id="text" />
          <span className="lab-node-input-dot" />
          {t('director.lab.textPrompt')}
          {text && (
            <span className="lab-node-input-filled" title={promptFromPort ? undefined : text}>
              ✓
            </span>
          )}
        </div>

        {maxImages >= 1 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="image1" />
            <span className="lab-node-input-dot" />
            {maxImages >= 2 ? t('director.lab.firstFrame') : t('director.lab.refImage')}
            {isMultiRef
              ? images.length > 0 && (
                  <span className="lab-node-input-filled">✓{images.length > 1 ? ` ×${images.length}` : ''}</span>
                )
              : image1 && <span className="lab-node-input-filled">✓</span>}
          </div>
        )}
        {maxImages >= 2 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="image2" />
            <span className="lab-node-input-dot" />
            {t('director.lab.lastFrame')}
            {image2 && <span className="lab-node-input-filled">✓</span>}
          </div>
        )}
        {maxVideos >= 1 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="video1" />
            <span className="lab-node-input-dot" />
            {maxVideos >= 2 ? t('director.lab.clipOne') : t('director.lab.inputVideo')}
            {hasVideoUrl ? (
              <span className="lab-node-input-filled" title={videoUrl}>
                {t('director.lab.videoFromUrl')}
              </span>
            ) : (
              video1 && <span className="lab-node-input-filled">✓</span>
            )}
          </div>
        )}
        {maxVideos >= 2 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="video2" />
            <span className="lab-node-input-dot" />
            {t('director.lab.clipTwo')}
            {video2 && <span className="lab-node-input-filled">✓</span>}
          </div>
        )}
        {maxAudios >= 1 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="audio1" />
            <span className="lab-node-input-dot" />
            {t('director.lab.inputAudio')}
            {hasAudioUrl ? (
              <span className="lab-node-input-filled" title={audioUrl}>
                {t('director.lab.audioFromUrl')}
              </span>
            ) : (
              audioConnected && <span className="lab-node-input-filled">✓</span>
            )}
          </div>
        )}
      </div>

      <div className="lab-node-params" ref={paramsRef}>
        <div className="lab-node-param-row">
          <span>{t('director.lab.model')}</span>
          <span className="lab-node-param-value">
            {isAudio
              ? 'gemini-3.8-flash-lite-tts'
              : mode === 'video'
                ? 'seedance-2.0-fast'
                : 'gemini-3.1-flash-image'}{' '}
            {chevronDown}
          </span>
        </div>
        <div
          className="lab-node-param-row lab-node-param-row--clickable nodrag"
          role="button"
          tabIndex={0}
          onClick={() => setParamsOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              setParamsOpen((v) => !v);
            }
          }}
        >
          <span>{t('director.lab.genParams')}</span>
          <span className="lab-node-param-value">
            {isAudio
              ? voice
              : `${data.aspectRatio} · ${data.resolution}${
                  mode === 'video' ? ` · ${data.durationSeconds}s` : ''
                }${hasAudioOption ? ` · ${audioOutputLabel}` : ''}`}
            {outputCount > 1 ? ` · ×${outputCount}` : ''}
            {chevronDown}
          </span>
        </div>

        {paramsOpen && (
          <div className="lab-node-params-popover nodrag">
            {!isAudio && (
              <>
                <div className="lab-node-params-popover-section">
                  <div className="lab-node-params-popover-label">{t('director.lab.aspectRatioLabel')}</div>
                  <div className="lab-node-params-popover-pills">
                    {ASPECT_RATIO_OPTIONS.map((v) => (
                      <button
                        type="button"
                        key={v}
                        className={`lab-node-param-pill ${data.aspectRatio === v ? 'lab-node-param-pill--active' : ''}`}
                        onClick={() => actions.patchProcessNode(id, { aspectRatio: v })}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="lab-node-params-popover-section">
                  <div className="lab-node-params-popover-label">{t('director.lab.resolutionLabel')}</div>
                  <div className="lab-node-params-popover-pills">
                    {resolutionOptions.map((v) => (
                      <button
                        type="button"
                        key={v}
                        className={`lab-node-param-pill ${data.resolution === v ? 'lab-node-param-pill--active' : ''}`}
                        onClick={() => actions.patchProcessNode(id, { resolution: v })}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}

            {isAudio && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.voiceLabel')}</div>
                <div className="lab-node-params-popover-pills">
                  {AUDIO_VOICE_OPTIONS.map((v) => (
                    <button
                      type="button"
                      key={v}
                      className={`lab-node-param-pill ${voice === v ? 'lab-node-param-pill--active' : ''}`}
                      onClick={() => actions.patchProcessNode(id, { voice: v })}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* 风格与音色正交：音色决定"谁在说"，风格决定"怎么说"。与创意页
                共用 audioStyles.ts 一份列表，避免两边选项分叉。 */}
            {isAudio && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.audioStyle.label')}</div>
                <div className="lab-node-params-popover-pills" data-testid="director-lab-audio-style-pills">
                  {AUDIO_STYLE_OPTIONS.map((o) => (
                    <button
                      type="button"
                      key={o.id}
                      className={`lab-node-param-pill ${audioStyle === o.instruction ? 'lab-node-param-pill--active' : ''}`}
                      onClick={() => actions.patchProcessNode(id, { style: o.instruction })}
                      data-testid={`director-lab-audio-style-${o.id}`}
                    >
                      {t(audioStyleLabelKey(o.instruction))}
                    </button>
                  ))}
                </div>
                <div className="lab-node-params-popover-hint">{t('director.audioStyle.hint')}</div>
              </div>
            )}

            {mode === 'video' && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.durationLabel')}</div>
                <div className="lab-node-params-popover-pills">
                  {DURATION_OPTIONS.map((v) => (
                    <button
                      type="button"
                      key={v}
                      className={`lab-node-param-pill ${data.durationSeconds === v ? 'lab-node-param-pill--active' : ''}`}
                      onClick={() => actions.patchProcessNode(id, { durationSeconds: v })}
                    >
                      {v}s
                    </button>
                  ))}
                </div>
              </div>
            )}

            {hasAudioUrlOption && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.audioUrlLabel')}</div>
                <input
                  type="text"
                  className="lab-node-param-input nodrag"
                  value={data.audioUrl ?? ''}
                  placeholder={t('director.lab.audioUrlPlaceholder')}
                  onChange={(e) => actions.patchProcessNode(id, { audioUrl: e.target.value })}
                  // 画布/节点上的 pointerdown 会开始拖动整张卡片、click 会切换
                  // 播放头，输入框里的交互不能顺带触发它们。
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  data-testid="director-lab-audio-url-input"
                />
                <div className="lab-node-params-popover-hint">{t('director.lab.audioUrlHint')}</div>
              </div>
            )}

            {hasVideoUrlOption && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.videoUrlLabel')}</div>
                <input
                  type="text"
                  className="lab-node-param-input nodrag"
                  value={data.videoUrl ?? ''}
                  placeholder={t('director.lab.videoUrlPlaceholder')}
                  onChange={(e) => actions.patchProcessNode(id, { videoUrl: e.target.value })}
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  data-testid="director-lab-video-url-input"
                />
                <div className="lab-node-params-popover-hint">{t('director.lab.videoUrlHint')}</div>
              </div>
            )}

            {hasAudioOption && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.audioOutputLabel')}</div>
                <div className="lab-node-params-popover-pills">
                  <button
                    type="button"
                    className={`lab-node-param-pill ${audioOutput ? 'lab-node-param-pill--active' : ''}`}
                    onClick={() => actions.patchProcessNode(id, { generateAudio: true })}
                    data-testid="director-lab-audio-on"
                  >
                    {t('director.lab.audioOn')}
                  </button>
                  <button
                    type="button"
                    className={`lab-node-param-pill ${!audioOutput ? 'lab-node-param-pill--active' : ''}`}
                    onClick={() => actions.patchProcessNode(id, { generateAudio: false })}
                    data-testid="director-lab-audio-off"
                  >
                    {t('director.lab.audioOff')}
                  </button>
                </div>
                <div className="lab-node-params-popover-hint">{t(audioHintKey)}</div>
              </div>
            )}

            {/* 一次生成几份独立结果只在第一次生成之前有意义——生成过以后
                "要几份"就是画布上留几张卡片（删掉多余的即可），这个字段
                不再被读取，继续显示只会让人以为改了它能拆分/合并已有产物。 */}
            {!isGenerated && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.outputCountLabel')}</div>
                <div className="lab-node-params-popover-pills">
                  {OUTPUT_COUNT_OPTIONS.map((v) => (
                    <button
                      type="button"
                      key={v}
                      className={`lab-node-param-pill ${outputCount === v ? 'lab-node-param-pill--active' : ''}`}
                      onClick={() => actions.patchProcessNode(id, { outputCount: v })}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {data.status === 'error' && data.error && <div className="lab-node-error">{data.error}</div>}

      {isGenerated && activeEntry && (
        <div className="lab-node-process-output">
          <div className={`lab-node-media${mode === 'video' ? ' lab-node-media--video' : mode === 'audio' ? ' lab-node-media--audio' : ''}`}>
            <MediaFrame ref={mediaImgRef} mode={mode} filePath={activeEntry.filePath} name={activeEntry.name} className={mode === 'image' ? 'nodrag' : undefined} />
            {/* 放大/下载只对图片有意义（视频靠原生播放器控件自带的全屏/下载），
                替换则图片和视频都有——见下面 swapControl 的注释。 */}
            {mode === 'image' && (
              <div className="lab-node-media-actions">
                <button
                  type="button"
                  title={t('director.enlarge')}
                  onClick={() => mediaImgRef.current?.requestFullscreen?.()}
                  data-testid="lab-process-node-enlarge-image"
                >
                  {enlargeIcon}
                </button>
                <a
                  href={rawFileUrl(activeEntry.filePath)}
                  download={downloadFileName(activeEntry.name, activeEntry.filePath, t(`director.lab.process.${data.kind}`))}
                  title={t('director.download')}
                  className="nodrag"
                  onClick={(e) => e.stopPropagation()}
                  data-testid="lab-process-node-download-image"
                >
                  {downloadIcon}
                </a>
              </div>
            )}
            {swapControl}
          </div>
          {data.history.length > 1 && (
            <div className="lab-node-history-bar nodrag" data-testid="lab-process-node-history-bar">
              <button
                type="button"
                className="lab-node-history-btn"
                disabled={data.activeHistoryIndex <= 0}
                title={t('director.lab.historyPrev')}
                onClick={() => actions.swapHistory(id, data.activeHistoryIndex - 1)}
                data-testid="lab-process-node-history-prev"
              >
                {chevronLeftIcon}
              </button>
              <span className="lab-node-history-position">
                {data.activeHistoryIndex + 1}/{data.history.length}
              </span>
              <button
                type="button"
                className="lab-node-history-btn"
                disabled={data.activeHistoryIndex >= data.history.length - 1}
                title={t('director.lab.historyNext')}
                onClick={() => actions.swapHistory(id, data.activeHistoryIndex + 1)}
                data-testid="lab-process-node-history-next"
              >
                {chevronRightIcon}
              </button>
            </div>
          )}
          <NodeNameLabel
            name={activeEntry.name}
            fallback={t(`director.lab.process.${data.kind}`)}
            onRename={(name) => actions.renameNode(id, name)}
          />
        </div>
      )}

      <button
        type="button"
        className="lab-node-generate-btn"
        disabled={!canGenerate}
        onClick={() => actions.generate(id, { prompt: text, image1, image2, images, video1, video2, audio1 })}
      >
        {data.status === 'generating' ? (
          <span className="lab-node-spinner" />
        ) : (
          <>
            {generateIcon}
            {t(isGenerated ? 'director.lab.regenerate' : 'director.lab.generate')}
          </>
        )}
      </button>

      <Handle type="source" position={Position.Right} id={mode} />

      {/* 用 Portal 挂到 document.body：React Flow 画布层带 CSS transform
          （平移/缩放），transform 祖先会把 position:fixed 的遮罩"收编"进
          自己的坐标系——遮罩不再真正贴满视口，而是跟着画布一起缩放/跑位。
          挂到 body 下彻底脱离这个祖先链，同 ConnectorMarket/ConfirmDialog
          的写法（bug 2026091001-001 同一类问题）。 */}
      {promptOpen &&
        createPortal(
          <div className="director-dialog-overlay" onClick={() => setPromptOpen(false)}>
            <div
              className="director-prompt-dialog"
              onClick={(e) => e.stopPropagation()}
              data-testid="lab-process-node-prompt-dialog"
            >
              <div className="director-prompt-dialog-title">
                {t('director.lab.textPrompt')}
                <button
                  type="button"
                  className="director-prompt-dialog-close"
                  title={t('common.close')}
                  onClick={() => setPromptOpen(false)}
                  data-testid="lab-process-node-prompt-dialog-close"
                >
                  {closeIcon}
                </button>
              </div>
              {promptFromPort && (
                <div className="director-prompt-dialog-hint">{t('director.lab.textPromptPortHint')}</div>
              )}
              {promptEditing ? (
                <PromptTextarea
                  value={data.prompt ?? ''}
                  onChange={(value) => actions.patchProcessNode(id, { prompt: value })}
                  namedImageAssets={namedImageAssets}
                  textareaClassName="director-prompt-dialog-textarea"
                  placeholder={t('director.lab.samplePrompt')}
                  autoFocus
                  onBlur={() => setPromptEditing(false)}
                  data-testid="lab-process-node-prompt-dialog-textarea"
                />
              ) : (
                <div
                  className="director-prompt-dialog-display nodrag"
                  data-testid="lab-process-node-prompt-dialog-display"
                  onClick={() => setPromptEditing(true)}
                >
                  {data.prompt ? (
                    renderWithImageChips(data.prompt, namedImageAssets)
                  ) : (
                    <span className="lab-node-text-placeholder">{t('director.lab.samplePrompt')}</span>
                  )}
                </div>
              )}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
