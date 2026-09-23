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
import { TextNode } from './nodes/TextNode';
import { ProcessNode } from './nodes/ProcessNode';
import type { ProcessKind, ProcessNodeData } from './labTypes';
import { PROCESS_KIND_MODE, PROCESS_KIND_MULTI_REF } from './labTypes';

const nodeTypes: NodeTypes = {
  image: ImageNode,
  video: VideoNode,
  text: TextNode,
  process: ProcessNode,
};

let nodeIdCounter = 0;
function nextNodeId(prefix: string): string {
  nodeIdCounter += 1;
  return `${prefix}_${Date.now()}_${nodeIdCounter}`;
}

// 一个处理节点开了多个输出（ProcessNodeData.outputCount）时，每个输出槽位
// 各自独立生成、各自可能独立进入"视频还在生成中，需要轮询"的状态——用
// "节点id:槽位序号" 而不是单纯的节点 id 做 pollTimers 的 key，避免槽位 1
// 的轮询定时器把槽位 0 那条覆盖掉。
function timerKey(nodeId: string, slotIndex: number): string {
  return `${nodeId}:${slotIndex}`;
}

const IMAGE_PROCESS_KINDS: ProcessKind[] = ['text2image', 'imageRef'];
const VIDEO_PROCESS_KINDS: ProcessKind[] = ['text2video', 'image2video'];

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

const plusIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

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

type ToolrailMenu = 'image' | 'video' | null;

// 画布持久化的 debounce 间隔：节点拖拽/连线时会连续触发多次
// onNodesChange/onEdgesChange，等操作停下来一小段时间再落盘一次，避免
// 拖动过程中每一帧都发一次保存请求。
const LAB_CANVAS_SAVE_DEBOUNCE_MS = 800;

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

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      if (!sourceNode) return false;
      if (connection.targetHandle === 'text') return sourceNode.type === 'text';
      if (connection.targetHandle === 'image1' || connection.targetHandle === 'image2') {
        return sourceNode.type === 'image';
      }
      return true;
    },
    [nodes]
  );

  const handleUpdateText = useCallback(
    (nodeId: string, text: string) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, text } } : n)));
    },
    [setNodes]
  );

  const handleRenameNode = useCallback(
    (nodeId: string, name: string) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, name } } : n)));
      // 有真实素材（assetId）的输出节点，改名要同步回后端的 DirectorAsset——
      // 不然画布上看到的名字和素材面板/composer 的 "@名称" 引用两边会对不
      // 上（画布这边改了，素材那边还是旧名字，"@新名字" 反而引用不到）。
      const assetId = (nodes.find((n) => n.id === nodeId)?.data as { assetId?: string | null } | undefined)?.assetId;
      if (assetId && selectedProjectId) {
        void useDirectorStore.getState().renameAsset(selectedProjectId, assetId, name);
      }
    },
    [setNodes, nodes, selectedProjectId]
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
      // 处理节点可能同时有多个槽位（timerKey(nodeId, slot)）各自在轮询——
      // 删除这个节点要把它名下所有槽位的定时器都清掉，不只是精确匹配
      // nodeId 本身那一个 key（那是旧的单输出实现遗留的写法）。
      for (const key of Array.from(pollTimers.current.keys())) {
        if (key === nodeId || key.startsWith(`${nodeId}:`)) {
          clearTimeout(pollTimers.current.get(key));
          pollTimers.current.delete(key);
        }
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
        outputCount: 1,
      };
      addNode({ id, type: 'process', position, data });
    },
    [addNode]
  );

  const setProcessNodeState = useCallback(
    (nodeId: string, patch: Partial<ProcessNodeData>) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  const pollOutputNode = useCallback(
    (processNodeId: string, outputNodeId: string, projectId: string, assetId: string, jobId: string, slotIndex: number) => {
      const key = timerKey(processNodeId, slotIndex);
      const timer = setTimeout(async () => {
        try {
          const { directorGenerateCheckStatus } = await import('../directorApi');
          const { status, project } = await directorGenerateCheckStatus(projectId, assetId, jobId);
          const asset = project.assets.find((a) => a.asset_id === assetId);
          if (status === 'pending') {
            pollOutputNode(processNodeId, outputNodeId, projectId, assetId, jobId, slotIndex);
            return;
          }
          pollTimers.current.delete(key);
          void useDirectorStore.getState().loadProjects();
          if (status === 'failed') {
            setProcessNodeState(processNodeId, { status: 'error', error: asset?.error || t('director.lab.generateFailed') });
            return;
          }
          setProcessNodeState(processNodeId, { status: 'idle' });
          setNodes((nds) =>
            nds.map((n) =>
              n.id === outputNodeId && asset?.file_path
                ? { ...n, data: { ...n.data, filePath: asset.file_path, name: asset.name || asset.prompt } }
                : n
            )
          );
        } catch (e) {
          pollTimers.current.delete(key);
          const message = e instanceof Error ? e.message : String(e);
          setProcessNodeState(processNodeId, { status: 'error', error: message });
        }
      }, 5000);
      pollTimers.current.set(key, timer);
    },
    [setNodes, setProcessNodeState, t]
  );

  // 按 slotIndex 取出这个处理节点当前已有的第 N 个输出节点——多个输出时，
  // "out" 端口会扇出多条边，边本身不带顺序，靠每个输出节点 data.slotIndex
  // （创建时写入）排序后按位置取。
  const getExistingOutputNodesSorted = useCallback(
    (nodeId: string): Node[] => {
      const outEdges = getEdges().filter((e) => e.source === nodeId && e.sourceHandle === 'out');
      return outEdges
        .map((e) => getNodes().find((n) => n.id === e.target))
        .filter((n): n is Node => !!n)
        .sort((a, b) => (((a.data as { slotIndex?: number }).slotIndex ?? 0) - ((b.data as { slotIndex?: number }).slotIndex ?? 0)));
    },
    [getEdges, getNodes]
  );

  // outputCount 调小时，把多出来的那些槽位对应的旧输出节点删掉——比如从
  // 5 改成 2，槽位 2/3/4 的三张旧卡片不该继续留在画布上。
  const pruneExtraOutputSlots = useCallback(
    (nodeId: string, keepCount: number) => {
      const existing = getExistingOutputNodesSorted(nodeId);
      for (const node of existing.slice(keepCount)) {
        handleDeleteNode(node.id);
      }
    },
    [getExistingOutputNodesSorted, handleDeleteNode]
  );

  // 把"生成结果"接到处理节点第 slotIndex 个输出槽位——同一个槽位再次生成
  // 时就地替换已有的输出节点，而不是每次都在画布上再摞一张新卡片；不同
  // 槽位各自独立成一个输出节点，从同一个 "out" 端口扇出多条连线。
  // handleGenerate 的正常成功路径、和下面 recoverFromGenerateTimeout 从
  // 超时里"捞回"迟到结果的路径，都要做这同一件事，所以抽成共享函数。
  const applyGenerateResult = useCallback(
    (nodeId: string, node: Node, outputAsset: DirectorAsset | undefined, slotIndex: number): string => {
      const outputType = outputAsset?.type === 'video' ? 'video' : 'image';

      // 按"这个节点自己记的 slotIndex 是否等于我要找的槽位号"查找，不能用
      // 排序后数组的下标当槽位号——并发时各槽位落定的先后顺序是任意的
      // （比如槽位 1 先落定、槽位 2 其次、槽位 0 最后），这时"排序后数组的
      // 第 0 项"只是"目前已存在的节点里 slotIndex 最小的那个"（可能是槽位
      // 1 的节点），根本不是槽位 0 自己的节点——按下标取会把槽位 0 的结果
      // 错误地写进槽位 1 的卡片里，凭空少一张卡。
      const existingAtSlot = getExistingOutputNodesSorted(nodeId).find(
        (n) => ((n.data as { slotIndex?: number }).slotIndex ?? 0) === slotIndex
      );
      const isInPlaceUpdate = !!existingAtSlot && existingAtSlot.type === outputType;

      // 就地替换同一个槽位时，沿用这张卡片已经有的名字（不管是用户手动
      // 改的还是之前哪次生成留下的），不能被这次生成结果自己的
      // name/prompt 顶掉——否则每次点"生成"重跑同一张卡片，名字就跟着
      // 新一轮的提示词/空名字变来变去，用户之前起的名字（以及别处 "@名字"
      // 的引用）就对不上了。只有真正新建一个槽位（不是就地替换）时才用
      // 生成结果自身的 name/prompt 当默认名字。
      const preservedName = isInPlaceUpdate ? ((existingAtSlot!.data as { name?: string }).name ?? '') : '';
      const outputData = {
        assetId: outputAsset?.asset_id ?? null,
        filePath: outputAsset?.file_path ?? '',
        name: preservedName || outputAsset?.name || outputAsset?.prompt || '',
        slotIndex,
      };

      // 沿用的名字如果和这次生成结果本身的名字不一样（每次生成在后端都是
      // 一条全新的 DirectorAsset，name 默认是空的），把新素材也同步改成
      // 同一个名字——"@名字" 引用（find_asset_by_name 同名取 updated_at
      // 最新的一条）才能落到刚生成的这张新图，而不是继续指向旧图。
      if (preservedName && outputAsset?.asset_id && preservedName !== outputAsset.name && selectedProjectId) {
        void useDirectorStore.getState().renameAsset(selectedProjectId, outputAsset.asset_id, preservedName);
      }

      let outputNodeId: string;
      if (isInPlaceUpdate) {
        outputNodeId = existingAtSlot!.id;
        setNodes((nds) => nds.map((n) => (n.id === outputNodeId ? { ...n, data: outputData } : n)));
      } else {
        if (existingAtSlot) {
          handleDeleteNode(existingAtSlot.id);
        }
        outputNodeId = nextNodeId('out');
        addNode({
          id: outputNodeId,
          type: outputType,
          // 多个输出纵向错开摆放，避免互相重叠——图片/视频输出卡片实际
          // 高度约 220px（标签行 + 4:3 媒体预览 + 名称行），间距必须大于
          // 卡片高度，否则相邻输出会视觉重叠，下层卡片的按钮也会被上层
          // 卡片的 DOM 遮住而无法点击。
          position: { x: node.position.x + 360, y: node.position.y + slotIndex * 240 },
          data: outputData,
        });
        setEdges((eds) =>
          addEdge(
            {
              id: nextNodeId('edge'),
              source: nodeId,
              sourceHandle: 'out',
              target: outputNodeId,
              targetHandle: 'in',
            },
            eds
          )
        );
      }
      return outputNodeId;
    },
    [getExistingOutputNodesSorted, setNodes, addNode, setEdges, handleDeleteNode, selectedProjectId]
  );

  // director.generate 客户端超时后的"找回"轮询：定期重新拉这个项目的素材
  // 列表，找一个 priorAssetIds 里没有、类型和这次生成模式一致的新素材——
  // 后端那次调用没有被取消，迟早会把结果（成功或失败）写进
  // director_state.json，这里只是换一种方式重新读到它。找到 ready 就按
  // 正常成功路径接上输出节点；找到 failed 才真正报错；重试到
  // GENERATE_RECOVERY_MAX_ATTEMPTS 次还是一无所获，才当作超时失败展示
  // 给用户——避免用户明明看到画布上"生成完成"了、进程节点却还卡在一个
  // 过时的"请求超时"错误上。
  const recoverFromGenerateTimeout = useCallback(
    (
      nodeId: string,
      node: Node,
      projectId: string,
      priorAssetIds: Set<string>,
      mode: 'video' | 'image',
      slotIndex: number,
      attempt = 0
    ) => {
      const key = timerKey(nodeId, slotIndex);
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
          pollTimers.current.delete(key);
          // 这个素材接下来会被这个槽位占用——同一轮里后面的槽位再找"新
          // 出现的素材"时不能又找到它，否则多个槽位会抢同一份结果。
          priorAssetIds.add(newAsset.asset_id);
          if (newAsset.status === 'failed') {
            setProcessNodeState(nodeId, { status: 'error', error: newAsset.error || t('director.lab.generateFailed') });
            return;
          }
          const outputNodeId = applyGenerateResult(nodeId, node, newAsset, slotIndex);
          if (newAsset.status === 'pending' && newAsset.job_id) {
            pollOutputNode(nodeId, outputNodeId, projectId, newAsset.asset_id, newAsset.job_id, slotIndex);
          } else {
            setProcessNodeState(nodeId, { status: 'idle' });
          }
          return;
        }

        if (attempt + 1 >= GENERATE_RECOVERY_MAX_ATTEMPTS) {
          pollTimers.current.delete(key);
          setProcessNodeState(nodeId, { status: 'error', error: t('director.lab.generateTimeoutGaveUp') });
          return;
        }
        recoverFromGenerateTimeout(nodeId, node, projectId, priorAssetIds, mode, slotIndex, attempt + 1);
      }, GENERATE_RECOVERY_POLL_INTERVAL_MS);
      pollTimers.current.set(key, timer);
    },
    [applyGenerateResult, pollOutputNode, setProcessNodeState, t]
  );

  // 单个输出槽位的一次生成——outputCount > 1 时 handleGenerate 会依次对
  // 0..outputCount-1 各调一次，参数（提示词/参考图/宽高比等）完全一样，
  // 只是各自独立起一次真实的生成请求、各自落到自己的输出槽位上。
  const runOneGenerate = useCallback(
    async (
      nodeId: string,
      node: Node,
      data: ProcessNodeData,
      mode: 'video' | 'image',
      prompt: string,
      resolved: ResolvedGenerateInput,
      projectId: string,
      priorAssetIds: Set<string>,
      slotIndex: number
    ): Promise<'ok' | 'failed'> => {
      try {
        const { directorGenerate } = await import('../directorApi');
        const params: GenerateParams = {
          projectId,
          mode,
          prompt,
          aspectRatio: data.aspectRatio,
          resolution: data.resolution,
          durationSeconds: data.durationSeconds,
        };
        if (mode === 'video') {
          if (resolved.image1?.assetId) params.firstFrameAssetId = resolved.image1.assetId;
          if (resolved.image2?.assetId) params.lastFrameAssetId = resolved.image2.assetId;
        } else {
          const referenceAssetIds = resolved.images.map((img) => img.assetId).filter((v): v is string => !!v);
          if (referenceAssetIds.length > 0) params.referenceAssetIds = referenceAssetIds;
        }

        const result = await directorGenerate(params);
        void useDirectorStore.getState().loadProjects();
        const outputAsset = result.project.assets.find((a) => a.asset_id === result.assetId);
        if (outputAsset) priorAssetIds.add(outputAsset.asset_id);
        const outputNodeId = applyGenerateResult(nodeId, node, outputAsset, slotIndex);

        if (outputAsset?.status === 'pending' && outputAsset.job_id) {
          pollOutputNode(nodeId, outputNodeId, projectId, outputAsset.asset_id, outputAsset.job_id, slotIndex);
        }
        return outputAsset?.status === 'failed' ? 'failed' : 'ok';
      } catch (e) {
        // "请求超时"只是前端等不下去了主动放弃这条 WS 请求，不代表生成
        // 服务商真的失败了——generate_video/generate_visual 那次调用仍在
        // 后端继续跑，很可能过一会儿就会正常写出结果。这种情况下不能立刻
        // 报错，转成轮询项目素材列表去把这个结果找回来；真正的失败信息
        // 只应该来自服务商/后端明确返回的错误，或者轮询到期还是一无所获。
        if (e instanceof DirectorApiError && e.code === 'REQUEST_TIMEOUT') {
          recoverFromGenerateTimeout(nodeId, node, projectId, priorAssetIds, mode, slotIndex);
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
      const outputCount = Math.max(1, data.outputCount || 1);

      // outputCount 调小过的话，先把多出来的旧输出槽位清掉。
      pruneExtraOutputSlots(nodeId, outputCount);

      // 超时后要去项目素材列表里"找回"迟到的结果，得先知道生成前有哪些
      // 素材——找一个这个集合里没有的、类型匹配的新素材，就是这次生成
      // 迟到的那份结果。多个槽位共享同一份集合并随着每个槽位落定持续
      // 往里加，避免槽位之间抢到同一份刚出现的结果。
      const priorAssetIds = new Set(
        useDirectorStore.getState().projects.find((p) => p.project_id === selectedProjectId)?.assets.map((a) => a.asset_id) ??
          []
      );

      setProcessNodeState(nodeId, { status: 'generating', error: null });

      // 并发对每个槽位发起一次真实生成——后端 director.generate 不再用锁
      // 串行化 generate_visual/generate_video 调用（那把锁本来保护的是
      // director_state.json 的读改写，而 DirectorStore 的同步方法在
      // asyncio 单线程调度下天然不会被打断，不需要靠这把锁；见
      // director_manager.py 的说明），所以这里并发发起是真的能让多份输出
      // 一起跑、而不是排队等最慢的那一份。每个槽位各自的 slotIndex 在
      // 调用时就定死，不依赖谁先完成，落到哪个输出卡片不会因为并发而错位。
      const outcomes = await Promise.all(
        Array.from({ length: outputCount }, (_, slotIndex) =>
          runOneGenerate(nodeId, node, data, mode, prompt, resolved, selectedProjectId, priorAssetIds, slotIndex)
        )
      );
      const anyFailed = outcomes.includes('failed');

      // 只要没有槽位在轮询中（pollTimers 里还留着这个节点的 key），说明
      // 全部槽位都已经同步落定，可以把状态收个尾；还在轮询的槽位会在自己
      // 完成时各自把状态改成 idle/error（见 pollOutputNode /
      // recoverFromGenerateTimeout），这里不用等它们。
      const stillPolling = Array.from(pollTimers.current.keys()).some((key) => key.startsWith(`${nodeId}:`));
      if (!stillPolling) {
        setProcessNodeState(nodeId, anyFailed ? { status: 'error' } : { status: 'idle' });
      }
    },
    [getNodes, selectedProjectId, setProcessNodeState, pruneExtraOutputSlots, runOneGenerate]
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

    // 从画布现有节点里预先建好两张索引表，新摆的内容也会持续写入这两张
    // 表——之后同一次调用里更晚的素材，既能查到"这个素材本身摆过没"，
    // 也能查到"这个文件路径对应哪个可以复用的图片节点"（同一张设计图，
    // 既是自己那一行的输出，也可能是下一行的参考图输入）。
    const outputNodeIdByAsset = new Map<string, string>();
    const imageNodeIdByPath = new Map<string, string>();
    // 素材 id -> 它在画布上的 y 坐标——用来给"依赖上一个输出"的素材算摆放
    // 位置（取它依赖的那些素材的 y 均值），既覆盖这次新摆的素材，也覆盖
    // 已经在画布上、来自更早一次 build 的素材。
    const assetY = new Map<string, number>();
    for (const n of getNodes()) {
      const d = n.data as { assetId?: string | null; filePath?: string } | undefined;
      if (d?.assetId) {
        outputNodeIdByAsset.set(d.assetId, n.id);
        assetY.set(d.assetId, n.position.y);
      }
      if (n.type === 'image' && d?.filePath) imageNodeIdByPath.set(d.filePath, n.id);
    }

    const newAssetIds = orderedAssetIds.filter((id) => !outputNodeIdByAsset.has(id));
    if (newAssetIds.length === 0) return; // 已经全部摆过，不重复摆

    const ROW_HEIGHT = 260;
    const COL_TEXT_X = 60;
    const COL_REF_X = 60;
    const COL_PROCESS_X = 420;
    const COL_OUTPUT_X = 780;
    // 不依赖任何上一个输出的素材，各自独立占一整组（文字/处理/输出三列）
    // 纵向摞下去；依赖别的素材输出的（引用了它的 图片参考/首尾帧），改成
    // 在依赖的那一组右边再开一组横向摆——GROUP_STEP 是一整组三列的宽度
    // 再加安全间距，保证下一组的文字列不会撞到上一组输出列的卡片。
    const GROUP_STEP = 1080;
    // 接到已有画布的下方，不覆盖用户手动摆放的内容。
    const existingMaxY = getNodes().reduce((max, n) => Math.max(max, n.position.y), 0);
    let rowY = existingMaxY > 0 ? existingMaxY + ROW_HEIGHT : 60;

    const ensureImageRefNode = (filePath: string, x: number, y: number): string => {
      const existing = imageNodeIdByPath.get(filePath);
      if (existing) return existing;
      const matchingAsset = project.assets.find((a) => a.file_path === filePath);
      const id = nextNodeId('ref');
      addNode({
        id,
        type: 'image',
        position: { x, y },
        data: {
          assetId: matchingAsset?.asset_id ?? null,
          filePath,
          name: matchingAsset?.name || matchingAsset?.prompt || '',
        },
      });
      imageNodeIdByPath.set(filePath, id);
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
      kind: ProcessKind;
      mode: 'image' | 'video';
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

      const isVideo = asset.type === 'video';
      const kind: ProcessKind = isVideo
        ? firstFramePath || lastFramePath
          ? 'image2video'
          : 'text2video'
        : referenceImagePaths.length > 0
          ? 'imageRef'
          : 'text2image';
      const mode = PROCESS_KIND_MODE[kind];

      const depAssetIds = Array.from(
        new Set(
          [...referenceImagePaths, firstFramePath, lastFramePath]
            .filter((p): p is string => !!p)
            .map((p) => assetIdByFilePath.get(p))
            .filter((id): id is string => !!id && id !== assetId)
        )
      );

      plans.set(assetId, { asset, referenceImagePaths, firstFramePath, lastFramePath, kind, mode, depAssetIds });
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

    // 同一层内避免多个素材的 y 算出来太接近而叠在一起——逐个登记已经用过
    // 的 y，冲突就顺延一整行高度。
    const usedYByDepth = new Map<number, number[]>();
    const claimY = (depth: number, desired: number): number => {
      const used = usedYByDepth.get(depth) ?? [];
      let y = desired;
      while (used.some((u) => Math.abs(u - y) < ROW_HEIGHT)) {
        y += ROW_HEIGHT;
      }
      used.push(y);
      usedYByDepth.set(depth, used);
      return y;
    };

    const orderedForLayout = Array.from(plans.keys())
      .map((id) => ({ id, depth: depthOf(id) }))
      .sort((a, b) => a.depth - b.depth);

    for (const { id: assetId, depth } of orderedForLayout) {
      const plan = plans.get(assetId);
      if (!plan) continue;
      const { asset, referenceImagePaths, firstFramePath, lastFramePath, kind, mode, depAssetIds } = plan;

      const y =
        depth === 0
          ? (() => {
              const placed = rowY;
              rowY += ROW_HEIGHT;
              return placed;
            })()
          : claimY(
              depth,
              (() => {
                const depYs = depAssetIds.map((depId) => assetY.get(depId)).filter((v): v is number => v !== undefined);
                return depYs.length > 0 ? depYs.reduce((a, b) => a + b, 0) / depYs.length : rowY;
              })()
            );

      const xOffset = depth * GROUP_STEP;
      const textX = COL_TEXT_X + xOffset;
      const refX = COL_REF_X + xOffset;
      const processX = COL_PROCESS_X + xOffset;
      const outputX = COL_OUTPUT_X + xOffset;

      const textNodeId = nextNodeId('text');
      addNode({ id: textNodeId, type: 'text', position: { x: textX, y }, data: { text: asset.prompt } });

      const processNodeId = nextNodeId('process');
      const params = (asset.params ?? {}) as Record<string, unknown>;
      const processData: ProcessNodeData = {
        kind,
        status: 'idle',
        error: null,
        aspectRatio: typeof params.aspect_ratio === 'string' ? params.aspect_ratio : '16:9',
        resolution:
          typeof params.resolution === 'string' ? params.resolution : mode === 'video' ? '720p' : '512',
        durationSeconds: typeof params.duration_seconds === 'number' ? params.duration_seconds : 5,
        outputCount: 1,
      };
      addNode({ id: processNodeId, type: 'process', position: { x: processX, y }, data: processData });
      setEdges((eds) =>
        addEdge({ id: nextNodeId('edge'), source: textNodeId, sourceHandle: 'text', target: processNodeId, targetHandle: 'text' }, eds)
      );

      // imageRef 的 image1 端口允许多条连线（见 onConnect 里的
      // PROCESS_KIND_MULTI_REF 特判）——依赖的素材大多已经在上一层摆过
      // 自己的输出节点，ensureImageRefNode 会直接复用那个节点（按文件
      // 路径查），不会重复摆一张；只有真正找不到已有节点时才会在这一层
      // 的参考列新开一张卡片。
      referenceImagePaths.forEach((refPath, i) => {
        const refId = ensureImageRefNode(refPath, refX, y - 150 + i * 240);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image1' }, eds)
        );
      });
      if (firstFramePath) {
        const refId = ensureImageRefNode(firstFramePath, refX, y - 150);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image1' }, eds)
        );
      }
      if (lastFramePath) {
        const refId = ensureImageRefNode(lastFramePath, refX, y + 150);
        setEdges((eds) =>
          addEdge({ id: nextNodeId('edge'), source: refId, sourceHandle: 'image', target: processNodeId, targetHandle: 'image2' }, eds)
        );
      }

      const outputNodeId = nextNodeId('out');
      const outputType = asset.type === 'video' ? 'video' : 'image';
      addNode({
        id: outputNodeId,
        type: outputType,
        position: { x: outputX, y },
        data: { assetId: asset.asset_id, filePath: asset.file_path, name: asset.name || asset.prompt, slotIndex: 0 },
      });
      setEdges((eds) =>
        addEdge({ id: nextNodeId('edge'), source: processNodeId, sourceHandle: 'out', target: outputNodeId, targetHandle: 'in' }, eds)
      );

      outputNodeIdByAsset.set(asset.asset_id, outputNodeId);
      assetY.set(asset.asset_id, y);
      if (outputType === 'image') imageNodeIdByPath.set(asset.file_path as string, outputNodeId);
    }
  }, [selectedProjectId, getNodes, addNode, setEdges]);

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

  const handleAddText = () => {
    const { dx, dy } = nextSpawnOffset();
    const center = wrapperRef.current
      ? screenToFlowPosition({ x: wrapperRef.current.clientWidth / 2 + dx, y: wrapperRef.current.clientHeight / 2 + dy })
      : { x: 200 + dx, y: 200 + dy };
    addNode({ id: nextNodeId('text'), type: 'text', position: center, data: { text: '' } });
    setOpenMenu(null);
  };

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
          <button
            type="button"
            className="lab-toolrail-btn lab-toolrail-btn--text"
            title={t('director.lab.addText')}
            onClick={handleAddText}
          >
            {plusIcon}
          </button>

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
