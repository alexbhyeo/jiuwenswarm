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
import { PROCESS_KIND_MODE } from './labTypes';

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
        // 一个 target handle 只接受一条连线：先把该 (target, targetHandle) 上
        // 已有的旧连线去掉，再接入新的。
        const filtered = eds.filter(
          (e) => !(e.target === connection.target && e.targetHandle === connection.targetHandle)
        );
        return addEdge({ ...connection, animated: false }, filtered);
      });
    },
    [setEdges]
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

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
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
    (processNodeId: string, outputNodeId: string, projectId: string, assetId: string, jobId: string) => {
      const timer = setTimeout(async () => {
        try {
          const { directorGenerateCheckStatus } = await import('../directorApi');
          const { status, project } = await directorGenerateCheckStatus(projectId, assetId, jobId);
          const asset = project.assets.find((a) => a.asset_id === assetId);
          if (status === 'pending') {
            pollOutputNode(processNodeId, outputNodeId, projectId, assetId, jobId);
            return;
          }
          pollTimers.current.delete(processNodeId);
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
          pollTimers.current.delete(processNodeId);
          const message = e instanceof Error ? e.message : String(e);
          setProcessNodeState(processNodeId, { status: 'error', error: message });
        }
      }, 5000);
      pollTimers.current.set(processNodeId, timer);
    },
    [setNodes, setProcessNodeState, t]
  );

  // 把"生成结果"接到处理节点的输出口——同一个节点再次点"生成"时就地替换
  // 已有的输出节点，而不是每次都在画布上再摞一张新卡片。handleGenerate
  // 的正常成功路径、和下面 recoverFromGenerateTimeout 从超时里"捞回"迟到
  // 结果的路径，都要做这同一件事，所以抽成共享函数。
  const applyGenerateResult = useCallback(
    (nodeId: string, node: Node, outputAsset: DirectorAsset | undefined): string => {
      const outputType = outputAsset?.type === 'video' ? 'video' : 'image';
      const outputData = {
        assetId: outputAsset?.asset_id ?? null,
        filePath: outputAsset?.file_path ?? '',
        name: outputAsset?.name || outputAsset?.prompt || '',
      };

      const existingOutEdge = getEdges().find((e) => e.source === nodeId && e.sourceHandle === 'out');
      const existingOutputNode = existingOutEdge
        ? getNodes().find((n) => n.id === existingOutEdge.target)
        : undefined;

      let outputNodeId: string;
      if (existingOutputNode && existingOutputNode.type === outputType) {
        outputNodeId = existingOutputNode.id;
        setNodes((nds) => nds.map((n) => (n.id === outputNodeId ? { ...n, data: outputData } : n)));
      } else {
        if (existingOutputNode) {
          handleDeleteNode(existingOutputNode.id);
        }
        outputNodeId = nextNodeId('out');
        addNode({
          id: outputNodeId,
          type: outputType,
          position: { x: node.position.x + 360, y: node.position.y },
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
    [getEdges, getNodes, setNodes, addNode, setEdges, handleDeleteNode]
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
    (nodeId: string, node: Node, projectId: string, priorAssetIds: Set<string>, mode: 'video' | 'image', attempt = 0) => {
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
          if (newAsset.status === 'failed') {
            setProcessNodeState(nodeId, { status: 'error', error: newAsset.error || t('director.lab.generateFailed') });
            return;
          }
          const outputNodeId = applyGenerateResult(nodeId, node, newAsset);
          if (newAsset.status === 'pending' && newAsset.job_id) {
            pollOutputNode(nodeId, outputNodeId, projectId, newAsset.asset_id, newAsset.job_id);
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
        recoverFromGenerateTimeout(nodeId, node, projectId, priorAssetIds, mode, attempt + 1);
      }, GENERATE_RECOVERY_POLL_INTERVAL_MS);
      pollTimers.current.set(nodeId, timer);
    },
    [applyGenerateResult, pollOutputNode, setProcessNodeState, t]
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

      // 超时后要去项目素材列表里"找回"迟到的结果，得先知道生成前有哪些
      // 素材——找一个这个集合里没有的、类型匹配的新素材，就是这次生成
      // 迟到的那份结果。
      const priorAssetIds = new Set(
        useDirectorStore.getState().projects.find((p) => p.project_id === selectedProjectId)?.assets.map((a) => a.asset_id) ??
          []
      );

      setProcessNodeState(nodeId, { status: 'generating', error: null });

      try {
        const { directorGenerate } = await import('../directorApi');
        const params: GenerateParams = {
          projectId: selectedProjectId,
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
          if (resolved.image1?.assetId) params.referenceAssetId = resolved.image1.assetId;
        }

        const result = await directorGenerate(params);
        void useDirectorStore.getState().loadProjects();
        const outputAsset = result.project.assets.find((a) => a.asset_id === result.assetId);
        const outputNodeId = applyGenerateResult(nodeId, node, outputAsset);

        if (outputAsset?.status === 'pending' && outputAsset.job_id) {
          pollOutputNode(nodeId, outputNodeId, selectedProjectId, outputAsset.asset_id, outputAsset.job_id);
        } else {
          setProcessNodeState(nodeId, { status: 'idle' });
        }
      } catch (e) {
        // "请求超时"只是前端等不下去了主动放弃这条 WS 请求，不代表生成
        // 服务商真的失败了——generate_video/generate_visual 那次调用仍在
        // 后端继续跑，很可能过一会儿就会正常写出结果。这种情况下不能立刻
        // 报错，转成轮询项目素材列表去把这个结果找回来；真正的失败信息
        // 只应该来自服务商/后端明确返回的错误，或者轮询到期还是一无所获。
        if (e instanceof DirectorApiError && e.code === 'REQUEST_TIMEOUT') {
          recoverFromGenerateTimeout(nodeId, node, selectedProjectId, priorAssetIds, mode);
          return;
        }
        const message = e instanceof Error ? e.message : String(e);
        setProcessNodeState(nodeId, { status: 'error', error: message });
      }
    },
    [
      getNodes,
      selectedProjectId,
      setProcessNodeState,
      applyGenerateResult,
      pollOutputNode,
      recoverFromGenerateTimeout,
    ]
  );

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
