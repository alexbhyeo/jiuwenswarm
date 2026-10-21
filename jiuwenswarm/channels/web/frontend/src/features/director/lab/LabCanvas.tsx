import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  addEdge,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { EditChatPanel } from '../components/EditChatPanel';
import { DIRECTOR_ASSET_DRAG_MIME, DirectorApiError } from '../types';
import type { DirectorAsset, DirectorAssetDragPayload, GenerateParams } from '../types';
import { LabActionsProvider, type ResolvedGenerateInput } from './LabActionsContext';
import { ImageNode } from './nodes/ImageNode';
import { VideoNode } from './nodes/VideoNode';
import { AudioNode } from './nodes/AudioNode';
import { TextNode } from './nodes/TextNode';
import { ProcessNode } from './nodes/ProcessNode';
import { DEFAULT_AUDIO_VOICE } from '../audioVoices';
import { DEFAULT_AUDIO_STYLE } from '../audioStyles';
import type { GenerationEntry, ImageNodeData, ProcessKind, ProcessMode, ProcessNodeData } from './labTypes';
import {
  DEFAULT_VIDEO_BRIDGE_PROMPT,
  PROCESS_KIND_MODE,
  PROCESS_KIND_MULTI_REF,
  defaultGenerateAudio,
  supportsAudioOutput,
} from './labTypes';

const nodeTypes: NodeTypes = {
  image: ImageNode,
  video: VideoNode,
  audio: AudioNode,
  text: TextNode,
  process: ProcessNode,
};

let nodeIdCounter = 0;
function nextNodeId(prefix: string): string {
  nodeIdCounter += 1;
  return `${prefix}_${Date.now()}_${nodeIdCounter}`;
}

const IMAGE_PROCESS_KINDS: ProcessKind[] = ['text2image', 'imageRef'];
const VIDEO_PROCESS_KINDS: ProcessKind[] = [
  'text2video',
  'image2video',
  'imageAudio2video',
  'video2video',
  'videoBridge',
];
const AUDIO_PROCESS_KINDS: ProcessKind[] = ['text2audio', 'video2audio'];

/** 是不是一条能被服务商直接抓取的远端地址（相对于本地文件路径、data: URI）。
 *  生成接口只收 HTTPS 的参考地址，本地文件送不进去，所以重构流程图时要用它
 *  区分"还原成一条素材连线"还是"填回卡片上的链接输入框"。 */
const isRemoteUrl = (value: string): boolean => /^https?:\/\//i.test(value);

// director.generate 的客户端超时（GENERATE_TIMEOUT_MS，见 directorApi.ts）
// 只是前端等不下去了主动放弃——WS 请求本身没有取消机制，后端那次
// generate_video/generate_visual 调用仍在继续跑，跑完照样会把结果写进
// director_state.json，只是这条响应到达时前端已经把这个请求从 pending
// 表里删掉、不会再被处理。"请求超时"因此不等于"生成失败"：这里超时后
// 转入用项目素材列表的轮询去把这个迟到的结果找回来，而不是直接报错——
// 只有轮询到期还是没等到结果，或者真的等到一个 status=failed 的素材，
// 才算成真正的失败。
const GENERATE_RECOVERY_POLL_INTERVAL_MS = 5000;
const GENERATE_RECOVERY_MAX_ATTEMPTS = 120; // 120 * 5s = 10 分钟，覆盖比 5 分钟客户端超时更长的真实生成耗时

const imageGroupIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="4" width="18" height="16" rx="2.5" />
    <circle cx="9" cy="10" r="1.8" />
    <path d="m4 18 5.5-5.5a2 2 0 0 1 2.8 0L15 15.2M15.5 12.5l1-1a2 2 0 0 1 2.8 0L21 13.3" />
  </svg>
);

const videoGroupIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="6" width="14" height="12" rx="2.2" />
    <path d="m17 10 4-2.3v8.6L17 14" />
  </svg>
);

const audioGroupIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18V6l10-2v12" />
    <circle cx="6.5" cy="18" r="2.5" />
    <circle cx="16.5" cy="16" r="2.5" />
  </svg>
);

type ToolrailMenu = 'image' | 'video' | 'audio' | null;

// 画布持久化的 debounce 间隔：节点拖拽/连线时会连续触发多次
// onNodesChange/onEdgesChange，等操作停下来一小段时间再落盘一次，避免
// 拖动过程中每一帧都发一次保存请求。
const LAB_CANVAS_SAVE_DEBOUNCE_MS = 800;

// 处理卡片与它的产物合并成一张之后，"已生成"态的卡片（标签+端口区+参数区+
// 媒体预览+命名行+按钮）实测高度约 445px（文字提示弹窗 close 掉之后量的，
// 图片/视频/多参考图三种 kind 高度一致——.lab-node-ports 的 min-height:78px
// 统一撑住端口区，不随端口行数变化；媒体预览走固定 4:3 画框，也不随 kind
// 变化）。buildFlowFromChat 纵向摆放同一组里的卡片（ROW_HEIGHT）、以及
// handleGenerate 一次生成多份时纵向摆放兄弟卡片，都要按这个高度留间距，
// 不能再沿用合并前"卡片+独立输出卡片"更矮的旧间距——沿用旧间距的话，新卡片
// 比间距高出将近一倍，纵向相邻的两张卡片会重叠在一起。留一点余量到 480。
const PROCESS_CARD_ROW_HEIGHT = 480;

// LabTabShell 用 key={selectedProjectId} 强制在切换项目/切换 tab 时整个
// 重新挂载 LabCanvas，所以这里只需要在"挂载那一刻"读一次初始状态。
//
// 优先读 directorStore.labCanvasByProject（同步写入的会话内权威副本），
// 只有它还没有这个项目的记录时（比如这个项目在当前会话里第一次打开
// 实验室 tab、或者页面整个刷新过）才回退到 project.lab_nodes/lab_edges
// ——后者来自 directorProjectsList/Get 的响应，只在页面加载时或个别地方
// 手动 loadProjects() 时刷新，不能保证和"刚保存完"这件事同步：剪辑 tab
// 压根不会重新拉取 projects 列表，创作 tab 拉取的时机和画布保存请求
// 完成的时机也是两条独立的异步链路，谁先谁后没有保证。这正是最初只依赖
// project.lab_nodes 时，切到 剪辑/创作 卡片会丢的根因。
function readInitialCanvas(projectId: string | null): { nodes: Node[]; edges: Edge[] } {
  if (!projectId) return { nodes: [], edges: [] };
  const state = useDirectorStore.getState();
  const cached = state.labCanvasByProject[projectId];
  if (cached) {
    return { nodes: cached.nodes as Node[], edges: cached.edges as Edge[] };
  }
  const project = state.projects.find((p) => p.project_id === projectId);
  return {
    nodes: (project?.lab_nodes as Node[] | undefined) ?? [],
    edges: (project?.lab_edges as Edge[] | undefined) ?? [],
  };
}

// 保存前把"生成中"状态归一成 idle——切换 tab 会连轮询定时器一起卸载，
// 保存下来的快照如果还带着 generating，下次打开画布时既没有真实在跑的
// 轮询、卡片又会一直显示转圈，不如落盘时就还原成用户可以直接重新点
// "生成"的正常状态。
function sanitizeNodesForSave(nodes: Node[]): Node[] {
  return nodes.map((n) =>
    n.type === 'process' && (n.data as ProcessNodeData)?.status === 'generating'
      ? { ...n, data: { ...n.data, status: 'idle' } }
      : n
  );
}

function LabCanvasInner() {
  const { t } = useTranslation();
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const initialCanvas = useRef(readInitialCanvas(selectedProjectId)).current;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialCanvas.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialCanvas.edges);
  const { screenToFlowPosition, getNodes, getEdges } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [openMenu, setOpenMenu] = useState<ToolrailMenu>(null);
  const pollTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const addNode = useCallback((node: Node) => setNodes((nds) => nds.concat(node)), [setNodes]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => {
        // 一般情况下一个 target handle 只接受一条连线：先把该 (target,
        // targetHandle) 上已有的旧连线去掉，再接入新的。例外是 imageRef
        // （"图片参考"）处理卡片的 image1 端口——多参考图合成，允许同时接
        // 多条线，不做去重替换（见 PROCESS_KIND_MULTI_REF）。
        const targetNode = nodes.find((n) => n.id === connection.target);
        const targetKind = (targetNode?.data as { kind?: ProcessKind } | undefined)?.kind;
        const allowMulti = connection.targetHandle === 'image1' && targetKind && PROCESS_KIND_MULTI_REF[targetKind];
        const filtered = allowMulti
          ? eds
          : eds.filter((e) => !(e.target === connection.target && e.targetHandle === connection.targetHandle));
        return addEdge({ ...connection, animated: false }, filtered);
      });
    },
    [setEdges, nodes]
  );

  // 独立的输出卡片（image/video/audio）用自己的节点类型表明产物类型；合并
  // 卡片（process）没有独立类型，产物类型要从它的 kind 反查
  // PROCESS_KIND_MODE——两种卡片都能作为下游 image1/image2/video1/audio1
  // 端口的连线来源，缺了这一分支，拖线连到一张已经生成过的合并卡片会被
  // 判定为非法连线（已保存的连线不受影响，这只是拖线那一刻的校验）。
  const sourceMediaType = useCallback((node: Node): ProcessMode | null => {
    if (node.type === 'image' || node.type === 'video' || node.type === 'audio') return node.type;
    if (node.type === 'process') return PROCESS_KIND_MODE[(node.data as ProcessNodeData).kind];
    return null;
  }, []);

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      if (!sourceNode) return false;
      if (connection.targetHandle === 'text') return sourceNode.type === 'text';
      if (connection.targetHandle === 'image1' || connection.targetHandle === 'image2') {
        return sourceMediaType(sourceNode) === 'image';
      }
      // 视频生音频/视频生视频/视频连视频的输入视频端口：只接受视频产物。
      if (connection.targetHandle === 'video1' || connection.targetHandle === 'video2') {
        return sourceMediaType(sourceNode) === 'video';
      }
      // 图音生视频的参考音频端口：只接受音频产物。
      if (connection.targetHandle === 'audio1') return sourceMediaType(sourceNode) === 'audio';
      return true;
    },
    [nodes, sourceMediaType]
  );

  const handleUpdateText = useCallback(
    (nodeId: string, text: string) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, text } } : n)));
    },
    [setNodes]
  );

  const handleRenameNode = useCallback(
    (nodeId: string, name: string) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== nodeId) return n;
          if (n.type === 'process') {
            // 合并卡片改名要同时改两处：扁平镜像字段（下游连线/这里立刻
            // 读到的都是它）和 history 里那一条自己的 name——不然切换到
            // 另一条历史记录再切回来，名字又变回生成时的旧名字。
            const data = n.data as ProcessNodeData;
            const history = data.history.map((entry, i) => (i === data.activeHistoryIndex ? { ...entry, name } : entry));
            return { ...n, data: { ...data, name, history } };
          }
          return { ...n, data: { ...n.data, name } };
        })
      );
      // 有真实素材（assetId）的卡片，改名要同步回后端的 DirectorAsset——
      // 不然画布上看到的名字和素材面板/composer 的 "@名称" 引用两边会对不
      // 上（画布这边改了，素材那边还是旧名字，"@新名字" 反而引用不到）。
      const assetId = (nodes.find((n) => n.id === nodeId)?.data as { assetId?: string | null } | undefined)?.assetId;
      if (assetId && selectedProjectId) {
        void useDirectorStore.getState().renameAsset(selectedProjectId, assetId, name);
      }
    },
    [setNodes, nodes, selectedProjectId]
  );

  // 用本地上传的图片文件替换一张图片卡片（独立的参考卡片，或者合并卡片
  // 展示的那一条历史记录）的实际内容——保留这张卡片原来的名字不变，并把
  // 新上传的素材同步改成同一个名字，这样后续处理卡片的 "@名字" 引用
  // （find_asset_by_name 同名取 updated_at 最新的一条）会落到刚换上来的
  // 这张图，画布上直接连线引用它的下游卡片也会因为节点 id 不变、只是
  // filePath 更新而自动生效——两条路径都不需要用户再手动去重新接一次线。
  //
  // "替换"只对图片类卡片有意义——独立卡片一直只有 ImageNode 带这个按钮
  // （VideoNode/AudioNode 没有），合并卡片这里同样只在 mode==='image' 时
  // 才生效（kind 决定 mode，见 PROCESS_KIND_MODE）。
  const handleSwapImage = useCallback(
    async (nodeId: string, file: File) => {
      if (!selectedProjectId) return;
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const isMergedImageCard =
        node.type === 'process' && PROCESS_KIND_MODE[(node.data as ProcessNodeData).kind] === 'image';
      if (node.type !== 'image' && !isMergedImageCard) return;
      const data = node.data as ImageNodeData | ProcessNodeData;
      const project = useDirectorStore.getState().projects.find((p) => p.project_id === selectedProjectId);
      const originalAsset = data.assetId ? project?.assets.find((a) => a.asset_id === data.assetId) : undefined;
      const assetType = originalAsset?.type === 'character' ? 'character' : 'image';

      try {
        const { directorAssetUpload } = await import('../directorApi');
        const result = await directorAssetUpload(selectedProjectId, file, assetType);
        useDirectorStore.setState((s) => ({
          projects: s.projects.map((p) => (p.project_id === result.project.project_id ? result.project : p)),
        }));
        const newAsset = result.project.assets.find((a) => a.asset_id === result.assetId);
        if (!newAsset?.file_path) return;

        setNodes((nds) =>
          nds.map((n) => {
            if (n.id !== nodeId) return n;
            if (isMergedImageCard) {
              // 换的是合并卡片当前展示的那一条历史记录——同时改
              // history[activeHistoryIndex] 和扁平镜像字段，不然切到别的
              // 历史记录再切回来，换上去的图又变回原来生成的那张。
              const pd = n.data as ProcessNodeData;
              const history = pd.history.map((entry, i) =>
                i === pd.activeHistoryIndex
                  ? { ...entry, assetId: newAsset.asset_id, filePath: newAsset.file_path as string }
                  : entry
              );
              return {
                ...n,
                data: { ...pd, history, assetId: newAsset.asset_id, filePath: newAsset.file_path as string },
              };
            }
            return { ...n, data: { ...n.data, assetId: newAsset.asset_id, filePath: newAsset.file_path as string } };
          })
        );

        if (data.name) {
          await useDirectorStore.getState().renameAsset(selectedProjectId, newAsset.asset_id, data.name);
        }
      } catch {
        // 上传/改名各自已经把出错信息写进 store 的 uploadError/
        // projectsError，画布这里不需要再弹一次。
      }
    },
    [selectedProjectId, nodes, setNodes]
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
      // 卡片与产物合并成一张之后，一个节点 id 只对应它自己这一次正在跑
      // 的轮询（outputCount>1 时每份独立输出各自克隆成一张独立卡片，各有
      // 各的 id——见 handleGenerate），不会再有同一个 id 下多个槽位各自
      // 轮询的情况，直接按 nodeId 精确清一条就够。
      const timer = pollTimers.current.get(nodeId);
      if (timer) {
        clearTimeout(timer);
        pollTimers.current.delete(nodeId);
      }
    },
    [setNodes, setEdges]
  );

  const handleAddProcessNode = useCallback(
    (kind: ProcessKind, position: { x: number; y: number }) => {
      const id = nextNodeId('process');
      const mode = PROCESS_KIND_MODE[kind];
      const data: ProcessNodeData = {
        kind,
        status: 'idle',
        error: null,
        aspectRatio: '16:9',
        resolution: mode === 'video' ? '720p' : '512',
        durationSeconds: 5,
        // 仅 text2audio（文生音频）会读取；图片/视频卡片忽略它。
        voice: DEFAULT_AUDIO_VOICE,
        // 同理：新卡片默认不指定风格（请求里不发 instructions）。
        style: DEFAULT_AUDIO_STYLE,
        // 视频类卡片的"有声音/无声音"：以音频为输入的卡片（图音生视频）默认
        // 开声，其余视频卡片沿用历史上的静音默认；非视频卡片恒为 false 且不
        // 参与生成参数。见 defaultGenerateAudio / supportsAudioOutput。
        generateAudio: defaultGenerateAudio(kind),
        outputCount: 1,
        history: [],
        activeHistoryIndex: 0,
        assetId: null,
        filePath: '',
        name: '',
      };
      addNode({ id, type: 'process', position, data });
      // 视频连视频：FLF2V 只拿到两张静态图，没有提示词模型无从得知该怎么
      // 从第一张"动"到第二张（见 requiresPrompt），所以这张卡片落到画布时
      // 自动带一个预填好默认过渡提示词的文本节点，直接连到它的 text 端口
      // ——用户仍然可以在文本节点里改写或清空它，这里只是给一个已经验证过
      // 能用的起点，不是强制唯一的文案。摆在卡片左侧，与 text 端口在左边
      // 的朝向一致（同 handleAddText 的默认摆放习惯）。
      if (kind === 'videoBridge') {
        const textNodeId = nextNodeId('text');
        addNode({
          id: textNodeId,
          type: 'text',
          position: { x: position.x - 280, y: position.y },
          data: { text: DEFAULT_VIDEO_BRIDGE_PROMPT },
        });
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: textNodeId, sourceHandle: 'text', target: id, targetHandle: 'text' }, eds)
        );
      }
    },
    [addNode, setEdges]
  );

  const setProcessNodeState = useCallback(
    (nodeId: string, patch: Partial<ProcessNodeData>) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  // 卡片与它的产物合并成一张之后，轮询/超时找回都是在往同一张卡片自己的
  // history 里追加/改写一条记录，不再是另起一张独立的输出节点——pollTimers
  // 因此直接用 nodeId 当 key（outputCount>1 时每份独立输出各自克隆成一张
  // 独立卡片，各有各的 id，见 handleGenerate，不会有同一个 id 下多条并发
  // 轮询互相覆盖的问题）。
  const pollOutputNode = useCallback(
    (nodeId: string, historyIndex: number, projectId: string, assetId: string, jobId: string) => {
      const timer = setTimeout(async () => {
        try {
          const { directorGenerateCheckStatus } = await import('../directorApi');
          const { status, project } = await directorGenerateCheckStatus(projectId, assetId, jobId);
          const asset = project.assets.find((a) => a.asset_id === assetId);
          if (status === 'pending') {
            pollOutputNode(nodeId, historyIndex, projectId, assetId, jobId);
            return;
          }
          pollTimers.current.delete(nodeId);
          void useDirectorStore.getState().loadProjects();
          if (status === 'failed') {
            setProcessNodeState(nodeId, { status: 'error', error: asset?.error || t('director.lab.generateFailed') });
            return;
          }
          setProcessNodeState(nodeId, { status: 'idle' });
          if (!asset?.file_path) return;
          setNodes((nds) =>
            nds.map((n) => {
              if (n.id !== nodeId) return n;
              const data = n.data as ProcessNodeData;
              const history = data.history.map((entry, i) =>
                i === historyIndex ? { ...entry, filePath: asset.file_path as string, name: asset.name || asset.prompt || entry.name } : entry
              );
              // 轮询这段时间里用户可能已经切到了另一条历史记录——这时只
              // 更新 history 里那一条自己的记录，不去动当前正展示的扁平
              // 镜像字段（否则预览会在用户切走之后又被偷偷切回来）。
              const stillActive = data.activeHistoryIndex === historyIndex;
              return {
                ...n,
                data: {
                  ...data,
                  history,
                  ...(stillActive ? { filePath: asset.file_path, name: asset.name || asset.prompt || data.name } : {}),
                },
              };
            })
          );
        } catch (e) {
          pollTimers.current.delete(nodeId);
          const message = e instanceof Error ? e.message : String(e);
          setProcessNodeState(nodeId, { status: 'error', error: message });
        }
      }, 5000);
      pollTimers.current.set(nodeId, timer);
    },
    [setNodes, setProcessNodeState, t]
  );

  // 把一次生成结果追加进这张卡片自己的 history，指针指向新追加的这一条
  // ——不再是"找/建一个独立输出节点"，卡片自己就是产物。handleGenerate
  // 的正常成功路径、和下面 recoverFromGenerateTimeout 从超时里"捞回"迟到
  // 结果的路径，都要做这同一件事，所以抽成共享函数。返回新记录在 history
  // 里的下标，供调用方传给 pollOutputNode 继续跟踪这条记录。
  const applyGenerateResult = useCallback(
    (nodeId: string, outputAsset: DirectorAsset | undefined): number => {
      const current = getNodes().find((n) => n.id === nodeId);
      const data = current?.data as ProcessNodeData | undefined;
      // 沿用这张卡片已经有的名字（不管是用户手动改的还是之前哪次生成留下
      // 的），不能被这次生成结果自己的 name/prompt 顶掉——否则每次点"重新
      // 生成"，名字就跟着新一轮的提示词/空名字变来变去，用户之前起的名字
      // （以及别处 "@名字" 的引用）就对不上了。
      const pinnedName = data?.name || '';
      const entry: GenerationEntry = {
        assetId: outputAsset?.asset_id ?? null,
        filePath: outputAsset?.file_path ?? '',
        name: pinnedName || outputAsset?.name || outputAsset?.prompt || '',
      };
      // 沿用的名字如果和这次生成结果本身的名字不一样（每次生成在后端都是
      // 一条全新的 DirectorAsset，name 默认是空的），把新素材也同步改成
      // 同一个名字——"@名字" 引用（find_asset_by_name 同名取 updated_at
      // 最新的一条）才能落到刚生成的这条新记录，而不是继续指向旧的。
      if (pinnedName && outputAsset?.asset_id && pinnedName !== outputAsset.name && selectedProjectId) {
        void useDirectorStore.getState().renameAsset(selectedProjectId, outputAsset.asset_id, pinnedName);
      }
      const history = [...(data?.history ?? []), entry];
      const newIndex = history.length - 1;
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? { ...n, data: { ...n.data, history, activeHistoryIndex: newIndex, assetId: entry.assetId, filePath: entry.filePath, name: entry.name } }
            : n
        )
      );
      return newIndex;
    },
    [getNodes, setNodes, selectedProjectId]
  );

  // 切换这张卡片当前展示/生效的产物——只挪指针，不删除其它历史记录，
  // 可以来回切换；下游连到这张卡片 image1/image2/video1/audio1 端口的
  // 卡片会因为扁平镜像字段变了而自动读到新切到的这一条。
  const handleSwapHistory = useCallback(
    (nodeId: string, newIndex: number) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== nodeId) return n;
          const data = n.data as ProcessNodeData;
          const clamped = Math.max(0, Math.min(newIndex, data.history.length - 1));
          const entry = data.history[clamped];
          if (!entry) return n;
          return { ...n, data: { ...data, activeHistoryIndex: clamped, assetId: entry.assetId, filePath: entry.filePath, name: entry.name } };
        })
      );
    },
    [setNodes]
  );

  // director.generate 客户端超时后的"找回"轮询：定期重新拉这个项目的素材
  // 列表，找一个 priorAssetIds 里没有、类型和这次生成模式一致的新素材——
  // 后端那次调用没有被取消，迟早会把结果（成功或失败）写进
  // director_state.json，这里只是换一种方式重新读到它。找到 ready 就按
  // 正常成功路径追加进 history；找到 failed 才真正报错；重试到
  // GENERATE_RECOVERY_MAX_ATTEMPTS 次还是一无所获，才当作超时失败展示
  // 给用户——避免用户明明看到画布上"生成完成"了、卡片却还卡在一个
  // 过时的"请求超时"错误上。
  const recoverFromGenerateTimeout = useCallback(
    (nodeId: string, projectId: string, priorAssetIds: Set<string>, mode: ProcessMode, attempt = 0) => {
      const timer = setTimeout(async () => {
        let newAsset: DirectorAsset | undefined;
        try {
          const { directorProjectsGet } = await import('../directorApi');
          const { project } = await directorProjectsGet(projectId);
          useDirectorStore.setState((s) => ({
            projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
          }));
          newAsset = project.assets.find((a) => !priorAssetIds.has(a.asset_id) && a.type === mode);
        } catch {
          // 网络抖动一类的临时错误——安静地重试，不提前把这个中间态暴露
          // 给用户；真正的失败信息只来自实际找到的、status=failed 的素材，
          // 或者下面重试到期还是一无所获。
        }

        if (newAsset) {
          pollTimers.current.delete(nodeId);
          // 这个素材接下来会被这张卡片占用——同一轮里其它兄弟卡片再找"新
          // 出现的素材"时不能又找到它，否则多张卡片会抢同一份结果。
          priorAssetIds.add(newAsset.asset_id);
          if (newAsset.status === 'failed') {
            setProcessNodeState(nodeId, { status: 'error', error: newAsset.error || t('director.lab.generateFailed') });
            return;
          }
          const historyIndex = applyGenerateResult(nodeId, newAsset);
          if (newAsset.status === 'pending' && newAsset.job_id) {
            pollOutputNode(nodeId, historyIndex, projectId, newAsset.asset_id, newAsset.job_id);
          } else {
            setProcessNodeState(nodeId, { status: 'idle' });
          }
          return;
        }

        if (attempt + 1 >= GENERATE_RECOVERY_MAX_ATTEMPTS) {
          pollTimers.current.delete(nodeId);
          setProcessNodeState(nodeId, { status: 'error', error: t('director.lab.generateTimeoutGaveUp') });
          return;
        }
        recoverFromGenerateTimeout(nodeId, projectId, priorAssetIds, mode, attempt + 1);
      }, GENERATE_RECOVERY_POLL_INTERVAL_MS);
      pollTimers.current.set(nodeId, timer);
    },
    [applyGenerateResult, pollOutputNode, setProcessNodeState, t]
  );

  // 一张卡片的一次生成——outputCount > 1 时 handleGenerate 会对原卡片和
  // 它克隆出来的每张兄弟卡片各调一次，参数（提示词/参考图/宽高比等）
  // 完全一样，只是各自独立起一次真实的生成请求、各自追加进自己的 history。
  const runOneGenerate = useCallback(
    async (
      nodeId: string,
      data: ProcessNodeData,
      mode: ProcessMode,
      prompt: string,
      resolved: ResolvedGenerateInput,
      projectId: string,
      priorAssetIds: Set<string>
    ): Promise<'ok' | 'failed'> => {
      try {
        const { directorGenerate } = await import('../directorApi');
        // video2audio 是后端 _SUPPORTED_MODES 里一个独立模式（先视频理解写解说
        // 再 TTS 配音），不是"audio 加一个可选输入"：走线上必须如实说明，否则
        // 后端走 audio 分支，把空提示词直接喂给 TTS（实测报 "text is required"）。
        // 在 ProcessMode 这一层它仍然是 audio（输出卡片/参数弹层都一样）。
        const params: GenerateParams = {
          projectId,
          mode:
            data.kind === 'video2audio'
              ? 'video2audio'
              : data.kind === 'imageAudio2video'
                ? 'image_audio2video'
                : data.kind === 'video2video'
                  ? 'video2video'
                  : data.kind === 'videoBridge'
                    ? 'video_bridge'
                    : mode,
          prompt,
          aspectRatio: data.aspectRatio,
          resolution: data.resolution,
          durationSeconds: data.durationSeconds,
        };
        if (mode === 'audio') {
          // 文生音频只吃文本 + 音色 + 风格；视频生音频（连了 video1）在后端会
          // 先用视频理解写解说文案再配音，这里只要把输入视频、音色和风格带上。
          params.voice = data.voice || DEFAULT_AUDIO_VOICE;
          // 风格留空时不带上这个字段——请求体里就不会出现 instructions，
          // 与改造前的行为逐字节一致（不能传空串，那会变成一个显式的空风格）。
          const style = (data.style ?? '').trim();
          if (style) params.style = style;
          if (resolved.video1?.assetId) params.inputVideoAssetId = resolved.video1.assetId;
        } else if (data.kind === 'imageAudio2video') {
          // 图音生视频：参考图 + 参考音频都是"多模态参考"，后端一次
          // reference-to-video 调用完成，参考图不进首帧槽位（同时带
          // frame_images 与 input_references 时服务商会丢弃后者）。
          if (resolved.image1?.assetId) params.referenceAssetIds = [resolved.image1.assetId];
          // 参考音频优先用卡片上填的链接（与卡片上"参考音频"行的显示一致），
          // 没填链接才回落到端口连进来的音频素材。
          const audioUrl = (data.audioUrl ?? '').trim();
          if (audioUrl) params.inputAudioUrl = audioUrl;
          else if (resolved.audio1?.assetId) params.inputAudioAssetId = resolved.audio1.assetId;
          // 卡片上的"有声音/无声音"选项：未设置过按有声音处理（与后端默认一致）。
          params.generateAudio = data.generateAudio !== false;
        } else if (data.kind === 'video2video') {
          // 视频生视频：源片优先用卡片上填的 HTTPS 链接（与卡片上"输入视频"
          // 行的显示一致），没填链接才回落到端口连进来的视频素材。
          const videoUrl = (data.videoUrl ?? '').trim();
          if (videoUrl) params.inputVideoUrl = videoUrl;
          else if (resolved.video1?.assetId) params.inputVideoAssetId = resolved.video1.assetId;
          // 视频生视频的"声音"选项默认开声（见 defaultGenerateAudio）：源片
          // 自带的音轨不该在重绘画面时被顺手抹掉。
          params.generateAudio = data.generateAudio !== false;
        } else if (data.kind === 'videoBridge') {
          // 视频连视频：片段一 / 片段二都是必连的端口（没有 HTTPS 链接的
          // 替代输入——抓帧在服务端的本地文件上做，不经过生成接口本身的
          // 参考视频校验），两段都要带上 asset_id。
          if (resolved.video1?.assetId) params.clip1AssetId = resolved.video1.assetId;
          if (resolved.video2?.assetId) params.clip2AssetId = resolved.video2.assetId;
          // 桥接片是新合成出来的过渡镜头，没有"源片音轨"可沿用，默认静音，
          // 与文生视频/图生视频的默认一致。
          params.generateAudio = data.generateAudio === true;
        } else if (mode === 'video') {
          if (resolved.image1?.assetId) params.firstFrameAssetId = resolved.image1.assetId;
          if (resolved.image2?.assetId) params.lastFrameAssetId = resolved.image2.assetId;
          // 文生视频/图生视频的"声音"选项：显式带上，未设置过按无声音处理
          // （与后端 `bool(params.get("generate_audio") or False)` 的默认一致，
          // 也就与本卡片历史上的行为一致）。不能省——这张映射表是唯一的
          // camelCase → snake_case 出口，漏掉就等于用户选了也没用。
          params.generateAudio = data.generateAudio === true;
        } else {
          const referenceAssetIds = resolved.images.map((img) => img.assetId).filter((v): v is string => !!v);
          if (referenceAssetIds.length > 0) params.referenceAssetIds = referenceAssetIds;
        }

        const result = await directorGenerate(params);
        void useDirectorStore.getState().loadProjects();
        const outputAsset = result.project.assets.find((a) => a.asset_id === result.assetId);
        if (outputAsset) priorAssetIds.add(outputAsset.asset_id);
        const historyIndex = applyGenerateResult(nodeId, outputAsset);

        if (outputAsset?.status === 'pending' && outputAsset.job_id) {
          pollOutputNode(nodeId, historyIndex, projectId, outputAsset.asset_id, outputAsset.job_id);
        }
        return outputAsset?.status === 'failed' ? 'failed' : 'ok';
      } catch (e) {
        // "请求超时"只是前端等不下去了主动放弃这条 WS 请求，不代表生成
        // 服务商真的失败了——generate_video/generate_visual 那次调用仍在
        // 后端继续跑，很可能过一会儿就会正常写出结果。这种情况下不能立刻
        // 报错，转成轮询项目素材列表去把这个结果找回来；真正的失败信息
        // 只应该来自服务商/后端明确返回的错误，或者轮询到期还是一无所获。
        if (e instanceof DirectorApiError && e.code === 'REQUEST_TIMEOUT') {
          recoverFromGenerateTimeout(nodeId, projectId, priorAssetIds, mode);
          return 'ok';
        }
        const message = e instanceof Error ? e.message : String(e);
        setProcessNodeState(nodeId, { status: 'error', error: message });
        return 'failed';
      }
    },
    [applyGenerateResult, pollOutputNode, recoverFromGenerateTimeout, setProcessNodeState]
  );

  const handleGenerate = useCallback(
    async (nodeId: string, resolved: ResolvedGenerateInput) => {
      // resolved 直接来自 ProcessNode 自己算对勾时用的那份数据
      // (useNodeConnections/useNodesData) —— 这里不再用 edges/nodes
      // 自己另外算一遍"谁连了谁"，从根上避免两份独立实现算出不一致的
      // 结果（之前"界面打勾、生成却读到空"的 bug 正是出在这里）。
      const node = getNodes().find((n) => n.id === nodeId);
      if (!node || !selectedProjectId) return;
      const data = node.data as ProcessNodeData;
      const mode = PROCESS_KIND_MODE[data.kind];
      const prompt = resolved.prompt.trim();
      // 一次出几份独立结果只在第一次生成之前有意义——生成过以后"要几份"
      // 就是画布上留几张卡片（见 ProcessNodeData.outputCount 注释）。
      const isFirstGenerate = data.history.length === 0;
      const outputCount = isFirstGenerate ? Math.max(1, data.outputCount || 1) : 1;

      // 首次生成且要一次出多份：克隆出 outputCount-1 张兄弟卡片，每张各自
      // 复制一份这张卡片当前的全部输入连线（文字提示/参考图/视频/音频，
      // 不只是 image1），独立各生成各的——跟用户手动在画布上多摆几张一样
      // 的处理卡片、各自接好同样的输入，没有任何需要持久化的"同一组"关系；
      // 以后要减少份数，直接删掉对应的兄弟卡片即可。
      const targets: { id: string; data: ProcessNodeData }[] = [{ id: nodeId, data }];
      if (isFirstGenerate && outputCount > 1) {
        const incomingEdges = getEdges().filter((e) => e.target === nodeId);
        for (let i = 1; i < outputCount; i++) {
          const siblingId = nextNodeId('process');
          const siblingData: ProcessNodeData = {
            ...data,
            status: 'idle',
            error: null,
            history: [],
            activeHistoryIndex: 0,
            assetId: null,
            filePath: '',
            name: '',
            // 这个字段生成过一次之后就不再被读取（见上面 isFirstGenerate 的
            // 注释），但"生成参数"这一行的摘要文字还是会显示 ×outputCount
            // ——克隆出来的每张卡片各自都只代表"一份"，继续顶着原来的份数
            // 会让人误以为这张卡片自己还要再生成好几份。
            outputCount: 1,
          };
          addNode({
            id: siblingId,
            type: 'process',
            position: { x: node.position.x, y: node.position.y + i * PROCESS_CARD_ROW_HEIGHT },
            data: siblingData,
          });
          for (const e of incomingEdges) {
            setEdges((eds) =>
              addEdge(
                { id: nextNodeId('edge'), source: e.source, sourceHandle: e.sourceHandle, target: siblingId, targetHandle: e.targetHandle },
                eds
              )
            );
          }
          targets.push({ id: siblingId, data: siblingData });
        }
        // 原卡片同理——它也只代表"一份"，不该继续显示 ×outputCount。
        setProcessNodeState(nodeId, { outputCount: 1 });
        targets[0].data = { ...targets[0].data, outputCount: 1 };
      }

      // 超时后要去项目素材列表里"找回"迟到的结果，得先知道生成前有哪些
      // 素材——找一个这个集合里没有的、类型匹配的新素材，就是这次生成
      // 迟到的那份结果。多张卡片共享同一份集合并随着每张卡片落定持续
      // 往里加，避免互相抢到同一份刚出现的结果。
      const priorAssetIds = new Set(
        useDirectorStore.getState().projects.find((p) => p.project_id === selectedProjectId)?.assets.map((a) => a.asset_id) ??
          []
      );

      for (const target of targets) setProcessNodeState(target.id, { status: 'generating', error: null });

      // 并发对每张卡片发起一次真实生成——后端 director.generate 不再用锁
      // 串行化 generate_visual/generate_video 调用（那把锁本来保护的是
      // director_state.json 的读改写，而 DirectorStore 的同步方法在
      // asyncio 单线程调度下天然不会被打断，不需要靠这把锁；见
      // director_manager.py 的说明），所以这里并发发起是真的能让多份输出
      // 一起跑、而不是排队等最慢的那一份。
      const outcomes = await Promise.all(
        targets.map((target) => runOneGenerate(target.id, target.data, mode, prompt, resolved, selectedProjectId, priorAssetIds))
      );

      // 每张卡片按自己的结果收尾，不是整批共用一个成败——兄弟卡片之间互不
      // 连带，一张失败不该把另一张本来成功了的也标红。还在轮询中
      // （pollTimers 里还留着这个 id）的卡片会在自己完成时各自收尾（见
      // pollOutputNode / recoverFromGenerateTimeout），这里不用等它们。
      targets.forEach((target, i) => {
        if (!pollTimers.current.has(target.id)) {
          setProcessNodeState(target.id, outcomes[i] === 'failed' ? { status: 'error' } : { status: 'idle' });
        }
      });
    },
    [getNodes, getEdges, selectedProjectId, setProcessNodeState, addNode, setEdges, runOneGenerate]
  );

  // 把 剪辑助手 对话里已经真实生成过的设计图/关键帧/视频（助手消息的
  // image_asset_ids）按生成时用的参数摆成一条"文字提示 -> 处理节点 ->
  // 输出"的真实流程——处理节点的类型（文生图/图片参考/文生视频/图生
  // 视频）、宽高比/分辨率/时长，都是从素材自己的 params 里原样带回来的，
  // 不是瞎猜的默认值；有参考图/首尾帧的话，把那张图也摆成一个真正的输入
  // 节点接上，而不是只留一句提示词。
  //
  // 幂等：每个素材是否已经摆过，看画布上有没有一个 data.assetId 等于它
  // 的节点——已经摆过的直接跳过，不会点第二次就摞出重复的一整条流程。
  const buildFlowFromChat = useCallback(() => {
    if (!selectedProjectId) return;
    const project = useDirectorStore.getState().projects.find((p) => p.project_id === selectedProjectId);
    if (!project) return;

    // 按助手消息出现顺序收集它真正生成过的素材 id（同一个素材可能被后面
    // 的消息复用/引用，去重但保留第一次出现的顺序）。
    const orderedAssetIds: string[] = [];
    const seenAssetIds = new Set<string>();
    for (const message of project.edit_chat_messages) {
      if (message.role !== 'assistant') continue;
      for (const assetId of message.image_asset_ids) {
        if (!seenAssetIds.has(assetId)) {
          seenAssetIds.add(assetId);
          orderedAssetIds.push(assetId);
        }
      }
    }
    if (orderedAssetIds.length === 0) return;

    const assetById = new Map(project.assets.map((a) => [a.asset_id, a]));

    // 每次点"生成实验室流程图"都先把画布上全部的素材卡片/处理卡片清空，
    // 再按聊天记录整体重建一遍——不再是"只摆没摆过的素材"的增量模式，
    // 这样聊天记录里素材被删掉/重新生成之后，画布不会留下一张对不上的
    // 旧卡片。
    setNodes([]);
    setEdges([]);

    // 画布已经清空，下面几张索引表不需要再从旧节点里预先填——全部由这
    // 次重建过程中新摆出来的节点现填（见下面 ensureImageRefNode 等函数），
    // 同一次调用里更晚的素材既能查到"这个素材本身摆过没"，也能查到"这个
    // 文件路径对应哪个可以复用的图片节点"（同一张设计图，既是自己那一行
    // 的输出，也可能是下一行的参考图输入）。
    const outputNodeIdByAsset = new Map<string, string>();
    const imageNodeIdByPath = new Map<string, string>();
    // 视频卡片也能当输入（视频生音频的 video1 端口），所以要单独一张
    // 文件路径 -> 视频节点 id 的表：imageNodeIdByPath 只收 type==='image'
    // 的节点，拿视频去查它永远是空。
    const videoNodeIdByPath = new Map<string, string>();
    // 音频卡片也能当输入（图音生视频的 audio1 端口），同样单独一张表。
    const audioNodeIdByPath = new Map<string, string>();

    // 画布已经整体清空，聊天记录里出现过的素材这次都要重新摆一遍。
    const newAssetIds = orderedAssetIds;

    const ROW_HEIGHT = PROCESS_CARD_ROW_HEIGHT;
    const COL_REF_X = 60;
    const COL_PROCESS_X = 420;
    // 卡片与它的产物合并成一张之后，不再需要单独的"输出列"——处理卡片
    // 自己就是产物，只剩 参考图/处理 两列。不依赖任何上一张卡片的素材各自
    // 独立占一整组纵向摞下去；依赖别的素材（引用了它的 图片参考/首尾帧）
    // 的，改成在依赖的那一组右边再开一组横向摆。下一组的参考列绝大多数
    // 时候不会真的摆出卡片——ensureImageRefNode 会直接复用依赖的那张卡片
    // 本身（见该函数注释），所以 GROUP_STEP 取 360（= COL_PROCESS_X -
    // COL_REF_X，组内参考→处理这一列的宽度），让"被复用的那张卡片"与
    // "下一组的参考列"算出来正好落在同一个 x——参考复用时直接接上、不留
    // 空列，各组间距因此跟组内一样均匀；真有全新参考图需要新开一列的少数
    // 情况，就接受它跟上一组的卡片共用同一列 x（不同 y，不会互相遮挡）。
    const GROUP_STEP = 360;
    // 每一列（深度）各自独立、从画布顶部同一个起点往下顺序摞——不按"依赖
    // 的素材排在第几行"对齐，保证不管一张卡片依赖的是上一列第几行，第二
    // 列及以后的第一张卡片都跟第一列的第一张卡片一样从顶部开始，各列顶部
    // 对齐，不会因为依赖关系而错位摆到比第一列更靠下的位置。
    const rowYByDepth = new Map<number, number>();
    const takeRowY = (depth: number): number => {
      const next = rowYByDepth.get(depth) ?? 60;
      rowYByDepth.set(depth, next + ROW_HEIGHT);
      return next;
    };

    // columnKey 复用上面 processColumnKey/refColumnKey 的同一张登记表——
    // "复用不到、要新开一张参考卡片"这个分支此前完全不经过任何避让检查，
    // 直接拿调用方传来的 y 摆上去；这张新卡片的列（refX）在 GROUP_STEP 的
    // 设计下往往跟另一个深度的处理列是同一个物理 x（见上面 GROUP_STEP 和
    // refColumnKey 注释），不经避让检查就可能和那一列已经摆好的处理卡片
    // 叠在一起。
    const ensureImageRefNode = (filePath: string, x: number, y: number, columnKey: string): string => {
      const existing = imageNodeIdByPath.get(filePath);
      if (existing) return existing;
      const matchingAsset = project.assets.find((a) => a.file_path === filePath);
      const id = nextNodeId('ref');
      addNode({
        id,
        type: 'image',
        position: { x, y: claimY(columnKey, y) },
        data: {
          assetId: matchingAsset?.asset_id ?? null,
          filePath,
          name: matchingAsset?.name || matchingAsset?.prompt || '',
        },
      });
      imageNodeIdByPath.set(filePath, id);
      return id;
    };

    // 视频输入（视频生音频的 video1 端口）复用该视频素材自己的输出卡片：
    // 同一次 build 里刚摆的那张（见下面的 outputNodeIdByAsset.set）或更早
    // 一次 build 留下的那张。实在找不到才新建一张视频卡片。
    const ensureVideoNode = (filePath: string, x: number, y: number, columnKey: string): string => {
      const existing = videoNodeIdByPath.get(filePath);
      if (existing) return existing;
      const placedAssetId = assetIdByFilePath.get(filePath);
      const placedNodeId = placedAssetId ? outputNodeIdByAsset.get(placedAssetId) : undefined;
      if (placedNodeId) {
        videoNodeIdByPath.set(filePath, placedNodeId);
        return placedNodeId;
      }
      const matchingAsset = project.assets.find((a) => a.file_path === filePath);
      const id = nextNodeId('video');
      addNode({
        id,
        type: 'video',
        position: { x, y: claimY(columnKey, y) },
        data: {
          assetId: matchingAsset?.asset_id ?? null,
          filePath,
          name: matchingAsset?.name || matchingAsset?.prompt || '',
        },
      });
      videoNodeIdByPath.set(filePath, id);
      return id;
    };

    // 音频输入（图音生视频的 audio1 端口）与 ensureVideoNode 同构：优先复用
    // 该音频素材自己的输出卡片，找不到才新建一张音频卡片。
    const ensureAudioNode = (filePath: string, x: number, y: number, columnKey: string): string => {
      const existing = audioNodeIdByPath.get(filePath);
      if (existing) return existing;
      const placedAssetId = assetIdByFilePath.get(filePath);
      const placedNodeId = placedAssetId ? outputNodeIdByAsset.get(placedAssetId) : undefined;
      if (placedNodeId) {
        audioNodeIdByPath.set(filePath, placedNodeId);
        return placedNodeId;
      }
      const matchingAsset = project.assets.find((a) => a.file_path === filePath);
      const id = nextNodeId('audio');
      addNode({
        id,
        type: 'audio',
        position: { x, y: claimY(columnKey, y) },
        data: {
          assetId: matchingAsset?.asset_id ?? null,
          filePath,
          name: matchingAsset?.name || matchingAsset?.prompt || '',
        },
      });
      audioNodeIdByPath.set(filePath, id);
      return id;
    };

    // 文件路径 -> 素材 id，用来把一个素材的"参考图/首尾帧路径"反查回是
    // 引用了项目里哪个素材（这个项目里全部素材，不限于这次新摆的）——
    // 从而判断它是不是"依赖上一个输出"。
    const assetIdByFilePath = new Map<string, string>();
    for (const a of project.assets) {
      if (a.file_path) assetIdByFilePath.set(a.file_path, a.asset_id);
    }

    interface AssetPlan {
      asset: DirectorAsset;
      referenceImagePaths: string[];
      firstFramePath: string | null;
      lastFramePath: string | null;
      /** video2audio（视频生音频）当成解说素材的那路输入视频。 */
      inputVideoPath: string | null;
      /** imageAudio2video（图音生视频）用来做口型同步的那路参考音频。 */
      inputAudioPath: string | null;
      /** videoBridge（视频连视频）要桥接的片段一 / 片段二源片路径。 */
      bridgeClip1Path: string | null;
      bridgeClip2Path: string | null;
      kind: ProcessKind;
      mode: ProcessMode;
      /** 这个素材实际依赖的其它素材 id（不管是这次新摆的还是已经在画布上
       *  的老素材），按引用顺序去重。 */
      depAssetIds: string[];
    }
    const plans = new Map<string, AssetPlan>();
    for (const assetId of newAssetIds) {
      const asset = assetById.get(assetId);
      if (!asset || !asset.file_path) continue;

      const params = (asset.params ?? {}) as Record<string, unknown>;
      // reference_image_paths（列表）是当前的存法；reference_image_path
      // （单数）是旧数据留下的格式（这个改造之前生成的素材），两种都要
      // 认得，不然重建流程图时历史素材会被误判成 text2image。
      const referenceImagePaths = Array.isArray(params.reference_image_paths)
        ? (params.reference_image_paths as unknown[]).filter((p): p is string => typeof p === 'string')
        : typeof params.reference_image_path === 'string'
          ? [params.reference_image_path]
          : [];
      const firstFramePath = typeof params.first_frame_path === 'string' ? params.first_frame_path : null;
      const lastFramePath = typeof params.last_frame_path === 'string' ? params.last_frame_path : null;
      // 视频生音频把自己的输入视频路径也存进了 params，这里照旧还原成一条
      // 依赖连线（与首帧/尾帧/参考图同一套路子）；视频生视频走同一个字段，
      // 区别只在产物类型（video vs audio），见下面的 kind 判定。
      const inputVideoPath = typeof params.input_video_path === 'string' ? params.input_video_path : null;
      // 图音生视频把参考图和参考音频的路径也存了下来——两者都要还原成
      // 依赖连线，否则重建出来的流程图会退化成一张没有输入的"文生视频"。
      const inputAudioPath = typeof params.input_audio_path === 'string' ? params.input_audio_path : null;
      // 视频连视频把两段源片的路径都存了下来，各自还原成一条依赖连线
      // （video1/video2 端口），与其它卡片同一套路子。
      const bridgeClip1Path = typeof params.bridge_clip1_path === 'string' ? params.bridge_clip1_path : null;
      const bridgeClip2Path = typeof params.bridge_clip2_path === 'string' ? params.bridge_clip2_path : null;

      const isAudio = asset.type === 'audio';
      const isVideo = asset.type === 'video';
      const kind: ProcessKind = isAudio
        ? inputVideoPath
          ? 'video2audio'
          : 'text2audio'
        : isVideo
          ? bridgeClip1Path && bridgeClip2Path
            ? 'videoBridge'
            : inputVideoPath
              ? 'video2video'
              : inputAudioPath
                ? 'imageAudio2video'
                : firstFramePath || lastFramePath
                  ? 'image2video'
                  : 'text2video'
          : referenceImagePaths.length > 0
            ? 'imageRef'
            : 'text2image';
      const mode = PROCESS_KIND_MODE[kind];

      const depAssetIds = Array.from(
        new Set(
          [
            ...referenceImagePaths,
            firstFramePath,
            lastFramePath,
            inputVideoPath,
            inputAudioPath,
            bridgeClip1Path,
            bridgeClip2Path,
          ]
            .filter((p): p is string => !!p)
            .map((p) => assetIdByFilePath.get(p))
            .filter((id): id is string => !!id && id !== assetId)
        )
      );

      plans.set(assetId, {
        asset,
        referenceImagePaths,
        firstFramePath,
        lastFramePath,
        inputVideoPath,
        inputAudioPath,
        bridgeClip1Path,
        bridgeClip2Path,
        kind,
        mode,
        depAssetIds,
      });
    }

    // 按依赖深度分层：完全不依赖别的素材的是第 0 层，纵向摞；依赖了第 N
    // 层素材的排到第 N+1 层，横向摆到它右边一组。依赖的是已经在画布上的
    // 老素材（不在这次 plans 里）时按第 0 层处理——同样让依赖方至少排到
    // 第 1 层，从老素材右边开一组，而不是继续纵向摞在最下面。
    const depthCache = new Map<string, number>();
    const resolving = new Set<string>();
    const depthOf = (assetId: string): number => {
      const cached = depthCache.get(assetId);
      if (cached !== undefined) return cached;
      const plan = plans.get(assetId);
      if (!plan || plan.depAssetIds.length === 0) {
        depthCache.set(assetId, 0);
        return 0;
      }
      if (resolving.has(assetId)) return 0; // 循环依赖兜底，理论上不会出现
      resolving.add(assetId);
      let maxDepDepth = -1;
      for (const depId of plan.depAssetIds) {
        maxDepDepth = Math.max(maxDepDepth, plans.has(depId) ? depthOf(depId) : 0);
      }
      resolving.delete(assetId);
      const depth = maxDepDepth + 1;
      depthCache.set(assetId, depth);
      return depth;
    };

    // 避免同一列里多张卡片的 y 算出来太接近而叠在一起——按"离这一列上一张
    // 卡片至少一个 ROW_HEIGHT"单调往下顺延，而不是拿 desired 挨个跟所有
    // 已登记的 y 比对、冲突就各自反复 +ROW_HEIGHT（那种写法在多图参考
    // 场景——好几张卡片的依赖均值彼此很接近——会连续顺延好几次，叠加出比
    // ROW_HEIGHT 大得多、而且彼此不一致的间距）。只登记"这一列上一次分到
    // 的 y"，新的一张只要不比它再往下一个 ROW_HEIGHT 就直接顺延到这个
    // 下限，离得够远（desired 本来就比它靠后）就还是用 desired——既保证
    // 不重叠，也保证间距稳定。
    //
    // 这里按"列"（columnKey）登记，不是按"深度"——GROUP_STEP 的设计让
    // 深度 D 处理列与深度 D+1 参考列算出来是同一个物理 x（见 GROUP_STEP
    // 注释），如果分别用深度 D 和深度 D+1 当 key，两边各记各的、互不知道
    // 对方占了哪些 y，碰巧算出接近的 y 时就会在同一列里撞在一起。
    // processColumnKey/refColumnKey 把这两种"同一列"的情况映射到同一个
    // key，新摆一张卡片前都先过一遍 claimY，不管它是处理卡片本身，还是
    // ensureImageRefNode 等函数里"复用不到、要新开一张"的参考卡片。
    const lastYByColumn = new Map<string, number>();
    const claimY = (columnKey: string, desired: number): number => {
      const last = lastYByColumn.get(columnKey);
      const y = last === undefined ? desired : Math.max(desired, last + ROW_HEIGHT);
      lastYByColumn.set(columnKey, y);
      return y;
    };
    const processColumnKey = (depth: number): string => `proc:${depth}`;
    // 深度 0 的参考列（COL_REF_X，没有再往前一层的处理列）单独给一个不会
    // 跟任何处理列撞上的 key。
    const refColumnKey = (depth: number): string => (depth > 0 ? processColumnKey(depth - 1) : 'ref:0');

    const orderedForLayout = Array.from(plans.keys())
      .map((id) => ({ id, depth: depthOf(id) }))
      .sort((a, b) => a.depth - b.depth);

    for (const { id: assetId, depth } of orderedForLayout) {
      const plan = plans.get(assetId);
      if (!plan) continue;
      const {
        asset,
        referenceImagePaths,
        firstFramePath,
        lastFramePath,
        inputVideoPath,
        inputAudioPath,
        bridgeClip1Path,
        bridgeClip2Path,
        kind,
        mode,
      } = plan;

      // 每一列都从同一个顶部起点（takeRowY 的初始值）往下摞，不管这张卡片
      // 依赖的是上一列第几行——之前按"依赖素材的 y 均值"摆放，好几张卡片
      // 依赖同一批上一列素材（比如都引用了同几张设计图做参考）时，算出来
      // 的 y 均值完全一样，claimY 的避让逻辑就把它们一个接一个往下顺延，
      // 整列相对第一列一起往下错开一整行，看着像"从第二列开始矮一截"。
      // 改成每一列各自独立、从顶部开始顺序摞下去，跟第一列的起点对齐，
      // 不再按依赖对齐——claimY 仍然保留（避免同一列里挨太近的卡片叠加，
      // 以及和下一列复用这一列物理 x 的参考卡片冲突）。
      const y = claimY(processColumnKey(depth), takeRowY(depth));
      const refColKey = refColumnKey(depth);

      const xOffset = depth * GROUP_STEP;
      const refX = COL_REF_X + xOffset;
      const processX = COL_PROCESS_X + xOffset;

      const processNodeId = nextNodeId('process');
      const params = (asset.params ?? {}) as Record<string, unknown>;
      // 卡片与它的产物合并成一张之后，这里不再摆一张单独的输出卡片——
      // 这个素材本身就是这张卡片的第一条（也是唯一一条）历史记录，直接
      // 写进 history，指针指向它自己（见 labTypes.ts 的 ProcessNodeData
      // 注释：assetId/filePath/name 是 history[activeHistoryIndex] 的扁平
      // 镜像，下游卡片的 image1/image2/video1/audio1 连线原样读这几个
      // 顶层字段）。
      const entry: GenerationEntry = {
        assetId: asset.asset_id,
        filePath: asset.file_path as string,
        name: asset.name || asset.prompt || '',
      };
      const processData: ProcessNodeData = {
        kind,
        status: 'idle',
        error: null,
        aspectRatio: typeof params.aspect_ratio === 'string' ? params.aspect_ratio : '16:9',
        resolution:
          typeof params.resolution === 'string' ? params.resolution : mode === 'video' ? '720p' : '512',
        durationSeconds: typeof params.duration_seconds === 'number' ? params.duration_seconds : 5,
        outputCount: 1,
        // 文字提示不再落成一张独立的"文字提示"卡片+连线——直接写进卡片
        // 自带的文字提示弹窗（见 ProcessNodeData.prompt 的取舍说明），
        // 画布因此少一张卡片、少一条线，也不需要再摆文字列。
        prompt: asset.prompt,
        // 音频卡片重建后音色不能变回默认值——params 里存了当时用的 voice。
        voice: typeof params.voice === 'string' ? params.voice : DEFAULT_AUDIO_VOICE,
        // 风格同理（没有这个字段的旧素材重建出来就是不指定风格）。
        style: typeof params.style === 'string' ? params.style : DEFAULT_AUDIO_STYLE,
        // 视频类卡片重建后"有声音/无声音"要跟着素材走，否则重摆一遍流程就把
        // 用户当初的选择改回去了；音频链接同理（填了链接的素材 params 里存的
        // 是链接本身，没有可还原的素材连线）。
        generateAudio: supportsAudioOutput(kind) ? params.generate_audio === true : false,
        audioUrl:
          kind === 'imageAudio2video' && typeof params.input_audio_path === 'string' && /^https?:\/\//i.test(params.input_audio_path)
            ? params.input_audio_path
            : '',
        // 源片是一条 HTTPS 链接时没有可还原的素材连线，把链接填回卡片上；
        // 本地片段则由下面的 inputVideoPath 还原成一条连线。
        videoUrl:
          kind === 'video2video' && typeof params.input_video_path === 'string' && isRemoteUrl(params.input_video_path)
            ? params.input_video_path
            : '',
        history: [entry],
        activeHistoryIndex: 0,
        assetId: entry.assetId,
        filePath: entry.filePath,
        name: entry.name,
      };
      addNode({ id: processNodeId, type: 'process', position: { x: processX, y }, data: processData });

      // imageRef 的 image1 端口允许多条连线（见 onConnect 里的
      // PROCESS_KIND_MULTI_REF 特判）——依赖的素材大多已经在上一层摆过
      // 自己的输出节点，ensureImageRefNode 会直接复用那个节点（按文件
      // 路径查），不会重复摆一张；只有真正找不到已有节点时才会在这一层
      // 的参考列新开一张卡片。
      referenceImagePaths.forEach((refPath, i) => {
        const refId = ensureImageRefNode(refPath, refX, y - 150 + i * 240, refColKey);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image1' }, eds)
        );
      });
      if (firstFramePath) {
        const refId = ensureImageRefNode(firstFramePath, refX, y - 150, refColKey);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image1' }, eds)
        );
      }
      if (lastFramePath) {
        const refId = ensureImageRefNode(lastFramePath, refX, y + 150, refColKey);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image2' }, eds)
        );
      }
      if (inputVideoPath && !isRemoteUrl(inputVideoPath)) {
        // 视频生音频的输入视频：复用那路视频素材自己的卡片，连到 video1
        // 端口——与首帧/尾帧/参考图同一套路子。视频生视频填的是 HTTPS 链接
        // 时没有素材卡片可复用，链接已经填回卡片上，不再多摆一个节点。
        const refId = ensureVideoNode(inputVideoPath, refX, y, refColKey);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'video', target: processNodeId, targetHandle: 'video1' }, eds)
        );
      }
      if (inputAudioPath) {
        // 图音生视频的参考音频：复用那段音频素材自己的卡片，连到 audio1
        // 端口；参考图走上面的 referenceImagePaths（后端存的是单数
        // reference_image_path，这里已归一成列表）接到 image1。
        const refId = ensureAudioNode(inputAudioPath, refX, y + 150, refColKey);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'audio', target: processNodeId, targetHandle: 'audio1' }, eds)
        );
      }
      if (bridgeClip1Path && bridgeClip2Path) {
        // 视频连视频的两段源片：各自复用自己的视频素材卡片，分别连到
        // video1（片段一）/ video2（片段二）端口——与 video2audio 的单路
        // video1 同一套路子，只是这里要还原两条连线。
        const clip1RefId = ensureVideoNode(bridgeClip1Path, refX, y - 150, refColKey);
        setEdges((eds) =>
          addEdge(
            { id: nextNodeId('edge'), source: clip1RefId, sourceHandle: 'video', target: processNodeId, targetHandle: 'video1' },
            eds
          )
        );
        const clip2RefId = ensureVideoNode(bridgeClip2Path, refX, y + 150, refColKey);
        setEdges((eds) =>
          addEdge(
            { id: nextNodeId('edge'), source: clip2RefId, sourceHandle: 'video', target: processNodeId, targetHandle: 'video2' },
            eds
          )
        );
      }

      // 合并卡片自己就是产物——不再摆一张独立输出卡片、也不再需要
      // processNodeId → outputNodeId 的自连线。本次 build 刚摆出来的卡片
      // 立刻进索引表：排在后面的素材（比如引用这段视频写解说的那张音频
      // 卡片）要能把它当参考复用，而不是再摆一张独立的参考卡片。
      outputNodeIdByAsset.set(asset.asset_id, processNodeId);
      if (mode === 'image') imageNodeIdByPath.set(asset.file_path as string, processNodeId);
      if (mode === 'video') videoNodeIdByPath.set(asset.file_path as string, processNodeId);
      if (mode === 'audio') audioNodeIdByPath.set(asset.file_path as string, processNodeId);
    }
  }, [selectedProjectId, setNodes, addNode, setEdges]);

  // 画布持久化分两层：
  //
  // 1) 同步写一份到 directorStore.labCanvasByProject（会话内权威副本）。
  //    这是修复"切到 剪辑/创作 卡片就丢"的关键一步——切 tab 会立刻卸载
  //    LabCanvas 并在切回来时重新挂载读取初始状态，如果只靠下面 (2) 的
  //    异步落盘，组件早已经重新挂载、请求却可能还没落盘完成（尤其是
  //    剪辑 tab 根本不会重新拉取 projects 列表），读到的就是过时数据。
  //    这里的写入是同步的、不 debounce，不存在这个竞态。
  // 2) debounce 之后再调用后端 RPC 落盘到 director_state.json，只为了
  //    "整个页面刷新/重启应用后还能恢复"这一更强的持久化需求——静默
  //    失败：这是后台自动保存，不是用户主动发起的操作，弹错误提示只会
  //    打断正在画布上摆卡片的用户。
  const latestCanvasRef = useRef({ nodes, edges });
  latestCanvasRef.current = { nodes, edges };
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const skipNextBackendSaveRef = useRef(true); // 跳过挂载那一次——刚从存量数据读出来的没必要原样再存一遍

  useEffect(() => {
    if (selectedProjectId) {
      useDirectorStore.getState().setLabCanvas(selectedProjectId, nodes, edges);
    }

    if (skipNextBackendSaveRef.current) {
      skipNextBackendSaveRef.current = false;
      return;
    }
    if (!selectedProjectId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null;
      const { nodes: n, edges: e } = latestCanvasRef.current;
      void import('../directorApi').then(({ directorLabCanvasSave }) =>
        directorLabCanvasSave(selectedProjectId, sanitizeNodesForSave(n), e).catch(() => {})
      );
    }, LAB_CANVAS_SAVE_DEBOUNCE_MS);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [nodes, edges, selectedProjectId]);

  // 切换 tab 会卸载整个 LabCanvas，上面的 debounce 定时器还没触发就先被
  // 清掉了——离开前最后一次改动如果不在这里补一次同步保存就会丢。
  // labCanvasByProject 的同步写入已经在上面的 effect 里跟着每次
  // nodes/edges 变化即时更新了，这里只需要再补一次后端落盘。
  useEffect(() => {
    return () => {
      if (!selectedProjectId) return;
      const { nodes: n, edges: e } = latestCanvasRef.current;
      void import('../directorApi').then(({ directorLabCanvasSave }) =>
        directorLabCanvasSave(selectedProjectId, sanitizeNodesForSave(n), e).catch(() => {})
      );
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProjectId]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData(DIRECTOR_ASSET_DRAG_MIME);
      if (!raw) return;
      let payload: DirectorAssetDragPayload;
      try {
        payload = JSON.parse(raw);
      } catch {
        return;
      }
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      // "角色"素材本质上就是一张图片（同样有 file_path，可以直接接到
      // image1/image2 输入），画布里没有必要单独搞一种节点类型——按 image
      // 节点渲染即可，这样它才能像别的图片素材一样被拖进 实验室 连接使用。
      const nodeType = payload.type === 'character' ? 'image' : payload.type;
      addNode({
        id: nextNodeId(nodeType),
        type: nodeType,
        position,
        data: { assetId: payload.assetId, filePath: payload.filePath, name: payload.name },
      });
    },
    [screenToFlowPosition, addNode]
  );

  // 每次通过工具栏添加节点时错开一点位置，避免连续添加的节点完全重叠、
  // 让人误以为是同一张卡片上出现了多个连接点。
  const spawnOffset = useRef(0);
  const nextSpawnOffset = useCallback(() => {
    const step = spawnOffset.current % 6;
    spawnOffset.current += 1;
    return { dx: step * 36, dy: step * 28 };
  }, []);

  const handleAddProcess = (kind: ProcessKind) => {
    const { dx, dy } = nextSpawnOffset();
    const center = wrapperRef.current
      ? screenToFlowPosition({ x: wrapperRef.current.clientWidth / 2 + 80 + dx, y: wrapperRef.current.clientHeight / 2 + dy })
      : { x: 400 + dx, y: 200 + dy };
    handleAddProcessNode(kind, center);
    setOpenMenu(null);
  };

  return (
    <LabActionsProvider
      value={{
        updateText: handleUpdateText,
        generate: handleGenerate,
        deleteNode: handleDeleteNode,
        addProcessNode: handleAddProcessNode,
        patchProcessNode: setProcessNodeState,
        buildFlowFromChat,
        renameNode: handleRenameNode,
        swapImage: handleSwapImage,
        swapHistory: handleSwapHistory,
      }}
    >
      <div className="lab-canvas-wrap" ref={wrapperRef}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          nodeTypes={nodeTypes}
          onDrop={onDrop}
          onDragOver={onDragOver}
          fitView={false}
          defaultViewport={{ x: 80, y: 40, zoom: 0.85 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} color="var(--d-border-strong)" />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>

        <div className="lab-toolrail">
          <div className="lab-toolrail-item">
            <button
              type="button"
              className={`lab-toolrail-btn ${openMenu === 'image' ? 'lab-toolrail-btn--active' : ''}`}
              title={t('director.lab.imageGroup')}
              onClick={() => setOpenMenu((v) => (v === 'image' ? null : 'image'))}
            >
              {imageGroupIcon}
            </button>
            {openMenu === 'image' && (
              <div className="lab-add-menu lab-add-menu--side">
                {IMAGE_PROCESS_KINDS.map((kind) => (
                  <button type="button" key={kind} onClick={() => handleAddProcess(kind)}>
                    {t(`director.lab.process.${kind}`)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="lab-toolrail-item">
            <button
              type="button"
              className={`lab-toolrail-btn ${openMenu === 'video' ? 'lab-toolrail-btn--active' : ''}`}
              title={t('director.lab.videoGroup')}
              onClick={() => setOpenMenu((v) => (v === 'video' ? null : 'video'))}
            >
              {videoGroupIcon}
            </button>
            {openMenu === 'video' && (
              <div className="lab-add-menu lab-add-menu--side">
                {VIDEO_PROCESS_KINDS.map((kind) => (
                  <button type="button" key={kind} onClick={() => handleAddProcess(kind)}>
                    {t(`director.lab.process.${kind}`)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="lab-toolrail-item">
            <button
              type="button"
              className={`lab-toolrail-btn ${openMenu === 'audio' ? 'lab-toolrail-btn--active' : ''}`}
              title={t('director.lab.audioGroup')}
              onClick={() => setOpenMenu((v) => (v === 'audio' ? null : 'audio'))}
            >
              {audioGroupIcon}
            </button>
            {openMenu === 'audio' && (
              <div className="lab-add-menu lab-add-menu--side">
                {AUDIO_PROCESS_KINDS.map((kind) => (
                  <button type="button" key={kind} onClick={() => handleAddProcess(kind)}>
                    {t(`director.lab.process.${kind}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="lab-edit-chat-overlay">
          <EditChatPanel />
        </div>
      </div>
    </LabActionsProvider>
  );
}

export function LabCanvas() {
  return (
    <ReactFlowProvider>
      <LabCanvasInner />
    </ReactFlowProvider>
  );
}
