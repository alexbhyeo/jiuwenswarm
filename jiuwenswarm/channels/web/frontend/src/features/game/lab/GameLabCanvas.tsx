/**
 * 游戏工坊 · 实验室画布——与导演模式 LabCanvas 同一种形态（React Flow
 * 节点图），范围小得多：只有"角色/世界图片 -> 素材分解处理卡片 -> 3D
 * 部件"这一条链路（见 D-20261116-1047-game-workshop-lab §5.5）。
 */
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
import { gameApi, type GameAsset, type GameProject } from '../gameApi';
import { GameLabActionsProvider } from './GameLabActionsContext';
import {
  GAME_ASSET_DRAG_MIME,
  type GameAssetDragPayload,
  type GameDecomposeNodeData,
  type GameMeshOpNodeData,
  type GameModelPartNodeData,
  type GameToModelNodeData,
} from './labTypes';
import { GameImageNode } from './nodes/GameImageNode';
import { GameModelPartNode } from './nodes/GameModelPartNode';
import { GameDecomposeNode } from './nodes/GameDecomposeNode';
import { GameToModelNode } from './nodes/GameToModelNode';
import { GameMeshOpNode } from './nodes/GameMeshOpNode';
import './gameLab.css';

const nodeTypes: NodeTypes = {
  image: GameImageNode,
  model_part: GameModelPartNode,
  // "model"（2D 转 3D/网格补全/拓扑重构的单体产出）复用同一个组件——见
  // GameModelPartNode.tsx 顶部注释，这几种产出数据形状、交互完全一致。
  model: GameModelPartNode,
  decompose: GameDecomposeNode,
  to_model: GameToModelNode,
  // 网格补全/拓扑重构也共用同一个组件——见 GameMeshOpNode.tsx 顶部注释。
  mesh_complete: GameMeshOpNode,
  mesh_decimate: GameMeshOpNode,
};

// 与导演模式同一个取舍（LabCanvas.tsx 的 LAB_CANVAS_SAVE_DEBOUNCE_MS）：
// 拖拽/连线会连续触发多次节点变化，等操作停下来一小段时间再落盘一次。
const LAB_CANVAS_SAVE_DEBOUNCE_MS = 800;
const DECOMPOSE_POLL_INTERVAL_MS = 5000;

// 工具栏"图片"分组圆形按钮——镜像导演模式 LabCanvas.tsx 的 imageGroupIcon
// 同一个 SVG，保持两边视觉一致。
const imageGroupIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="4" width="18" height="16" rx="2.5" />
    <circle cx="9" cy="10" r="1.8" />
    <path d="m4 18 5.5-5.5a2 2 0 0 1 2.8 0L15 15.2M15.5 12.5l1-1a2 2 0 0 1 2.8 0L21 13.3" />
  </svg>
);

// 工具栏"模型"分组圆形按钮——网格补全/拓扑重构这两张处理卡片接的是已有
// 模型（不是图片），所以跟"图片处理"分开单放一个按钮，不是塞进同一个
// 侧边菜单里；图标画个立方体，跟图片组的图片框区分开。
const modelGroupIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z" />
    <path d="M4 7.5 12 12m0 0 8-4.5M12 12v9" />
  </svg>
);

type ToolrailMenu = 'image' | 'model' | null;
type ImageProcessKind = 'decompose' | 'toModel';
const IMAGE_PROCESS_KINDS: ImageProcessKind[] = ['toModel', 'decompose'];
type ModelProcessKind = 'meshComplete' | 'meshDecimate';
const MODEL_PROCESS_KINDS: ModelProcessKind[] = ['meshComplete', 'meshDecimate'];

function nextNodeId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

interface GameLabCanvasProps {
  project: GameProject;
  onProjectUpdate: (next: GameProject) => void;
}

function GameLabCanvasInner({ project, onProjectUpdate }: GameLabCanvasProps) {
  const { t } = useTranslation();
  const initial = useRef({ nodes: (project.lab_nodes as Node[]) ?? [], edges: (project.lab_edges as Edge[]) ?? [] })
    .current;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [openMenu, setOpenMenu] = useState<ToolrailMenu>(null);
  const { screenToFlowPosition } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const projectIdRef = useRef(project.project_id);
  projectIdRef.current = project.project_id;
  const pollTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // 与导演模式同一个取舍（LabCanvas.tsx 的 nextSpawnOffset），但步长按本模块
  // 卡片的实际尺寸放大：分解卡产出结果后会纵向长到 ~250px 高（标题+连线提示
  // +提示词框+粒度选择+按钮+结果文案一次性全展开，不像导演模式那样把结果
  // 拆成另外的小节点），卡片本身宽 220px（gameLab.css 的
  // .game-lab-node--decompose/--to-model）。横向错开一张卡的宽度+间距，
  // 纵向错开一点，避免后加的卡片被前一张放大后的卡片完全盖住。
  const spawnOffset = useRef(0);
  const nextSpawnOffset = useCallback(() => {
    const step = spawnOffset.current % 6;
    spawnOffset.current += 1;
    return { dx: step * 260, dy: step * 40 };
  }, []);

  useEffect(
    () => () => {
      pollTimers.current.forEach((timer) => clearTimeout(timer));
      pollTimers.current.clear();
    },
    []
  );

  // 防抖落盘——与导演模式同一套取舍，见上面常量注释。
  useEffect(() => {
    const timer = setTimeout(() => {
      void gameApi.labCanvasSave(projectIdRef.current, nodes, edges).catch(() => {});
    }, LAB_CANVAS_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [nodes, edges]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => {
        const filtered = eds.filter(
          (e) => !(e.target === connection.target && e.targetHandle === connection.targetHandle)
        );
        return addEdge({ ...connection, animated: false }, filtered);
      });
    },
    [setEdges]
  );

  const deleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
    },
    [setNodes, setEdges]
  );

  const renameNode = useCallback(
    (nodeId: string, name: string) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, name } } : n)));
    },
    [setNodes]
  );

  const patchDecomposeNode = useCallback(
    (nodeId: string, patch: Partial<GameDecomposeNodeData>) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  const patchToModelNode = useCallback(
    (nodeId: string, patch: Partial<GameToModelNodeData>) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  const patchMeshOpNode = useCallback(
    (nodeId: string, patch: Partial<GameMeshOpNodeData>) => {
      setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
    },
    [setNodes]
  );

  // 分解成功后，把产出的部件直接摆到画布上——以分解卡片为锚点，向下方铺开
  // 一个宽而矮的网格（而不是往右，右边是工具栏加卡片时新卡片的落点，见
  // nextSpawnOffset），并用边把每个部件连回分解卡片的 "parts" 输出口
  // （镜像用户给的参考图：产出的部件是接在处理卡片上的、而不是只出现在
  // 素材库里）。
  // 分解/2D 转 3D 成功后，把产出的模型直接摆到画布上——以来源处理卡片为
  // 锚点，向下方铺开一个宽而矮的网格（而不是往右，右边是工具栏加卡片时
  // 新卡片的落点，见 nextSpawnOffset），并用边把每个产出连回来源卡片的
  // 输出口（镜像用户给的参考图：产出是接在处理卡片上的、而不是只出现在
  // 素材库里）。分解产出 N 个部件、2D 转 3D 产出 1 个模型，共用这一套
  // 布局/连线逻辑，只是 assets 数组长度不同。
  const spawnModelResultNodes = useCallback(
    (sourceNodeId: string, sourceHandle: string, nodeType: 'model_part' | 'model', assets: GameAsset[]) => {
      if (assets.length === 0) return;
      const COLS = 4;
      const GAP_X = 200;
      const GAP_Y = 170;
      setNodes((nds) => {
        const sourceNode = nds.find((n) => n.id === sourceNodeId);
        const baseX = sourceNode?.position.x ?? 0;
        const baseY = (sourceNode?.position.y ?? 0) + 320;
        const newNodes: Node[] = assets.map((asset, i) => ({
          id: nextNodeId(nodeType),
          type: nodeType,
          position: {
            x: baseX + (i % COLS) * GAP_X,
            y: baseY + Math.floor(i / COLS) * GAP_Y,
          },
          data: {
            assetId: asset.asset_id,
            filePath: asset.file_path ?? '',
            name: asset.name,
            status: asset.status,
          } satisfies GameModelPartNodeData,
        }));
        setEdges((eds) =>
          eds.concat(
            newNodes.map((n) => ({
              id: `e_${sourceNodeId}_${n.id}`,
              source: sourceNodeId,
              sourceHandle,
              target: n.id,
              targetHandle: 'in',
            }))
          )
        );
        return nds.concat(newNodes);
      });
    },
    [setNodes, setEdges]
  );

  const pollDecompose = useCallback(
    (nodeId: string, placeholderAssetId: string, jobId: string, decomposeBatchId: string) => {
      const tick = async () => {
        try {
          const result = await gameApi.checkDecomposeStatus(
            projectIdRef.current,
            placeholderAssetId,
            jobId,
            decomposeBatchId
          );
          onProjectUpdate(result.project);
          if (result.status === 'pending') {
            pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
            return;
          }
          pollTimers.current.delete(nodeId);
          if (result.status === 'success') {
            const parts = result.assets ?? [];
            patchDecomposeNode(nodeId, { status: 'success', resultCount: parts.length });
            spawnModelResultNodes(nodeId, 'parts', 'model_part', parts);
          } else {
            patchDecomposeNode(nodeId, { status: 'failed', error: result.error });
          }
        } catch (err) {
          pollTimers.current.delete(nodeId);
          patchDecomposeNode(nodeId, { status: 'failed', error: err instanceof Error ? err.message : String(err) });
        }
      };
      pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
    },
    [onProjectUpdate, patchDecomposeNode, spawnModelResultNodes]
  );

  const submitDecompose = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const connection = edges.find((e) => e.target === nodeId && e.targetHandle === 'image');
      const sourceNode = connection ? nodes.find((n) => n.id === connection.source) : null;
      const assetId = (sourceNode?.data as { assetId?: string } | undefined)?.assetId;
      if (!assetId) return;
      const data = node.data as GameDecomposeNodeData;
      patchDecomposeNode(nodeId, { status: 'submitting', error: undefined });
      void gameApi
        .decomposeAsset(projectIdRef.current, assetId, data.hint, data.granularity)
        .then((result) => {
          onProjectUpdate(result.project);
          patchDecomposeNode(nodeId, {
            status: 'pending',
            placeholderAssetId: result.placeholder_asset_id,
            jobId: result.job_id,
            decomposeBatchId: result.decompose_batch_id,
          });
          pollDecompose(nodeId, result.placeholder_asset_id, result.job_id, result.decompose_batch_id);
        })
        .catch((err) => {
          patchDecomposeNode(nodeId, {
            status: 'failed',
            error: err instanceof Error ? err.message : String(err),
          });
        });
    },
    [nodes, edges, onProjectUpdate, patchDecomposeNode, pollDecompose]
  );

  const pollToModel = useCallback(
    (nodeId: string, placeholderAssetId: string, jobId: string) => {
      const tick = async () => {
        try {
          const result = await gameApi.checkToModelStatus(projectIdRef.current, placeholderAssetId, jobId);
          onProjectUpdate(result.project);
          if (result.status === 'pending') {
            pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
            return;
          }
          pollTimers.current.delete(nodeId);
          if (result.status === 'success') {
            patchToModelNode(nodeId, { status: 'success', resultAssetId: result.asset?.asset_id });
            if (result.asset) spawnModelResultNodes(nodeId, 'model', 'model', [result.asset]);
          } else {
            patchToModelNode(nodeId, { status: 'failed', error: result.error });
          }
        } catch (err) {
          pollTimers.current.delete(nodeId);
          patchToModelNode(nodeId, { status: 'failed', error: err instanceof Error ? err.message : String(err) });
        }
      };
      pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
    },
    [onProjectUpdate, patchToModelNode, spawnModelResultNodes]
  );

  const submitToModel = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const connection = edges.find((e) => e.target === nodeId && e.targetHandle === 'image');
      const sourceNode = connection ? nodes.find((n) => n.id === connection.source) : null;
      const assetId = (sourceNode?.data as { assetId?: string } | undefined)?.assetId;
      if (!assetId) return;
      patchToModelNode(nodeId, { status: 'submitting', error: undefined });
      void gameApi
        .toModel(projectIdRef.current, assetId)
        .then((result) => {
          onProjectUpdate(result.project);
          patchToModelNode(nodeId, {
            status: 'pending',
            placeholderAssetId: result.placeholder_asset_id,
            jobId: result.job_id,
          });
          pollToModel(nodeId, result.placeholder_asset_id, result.job_id);
        })
        .catch((err) => {
          patchToModelNode(nodeId, {
            status: 'failed',
            error: err instanceof Error ? err.message : String(err),
          });
        });
    },
    [nodes, edges, onProjectUpdate, patchToModelNode, pollToModel]
  );

  // 网格补全/拓扑重构的连线源是一个 model/model_part 节点（不是 image），
  // 对应处理卡片的目标端口 id 是 "model"（见 GameMeshOpNode.tsx），其余跟
  // submitToModel/pollToModel 完全同构——两段式状态机全在后端，前端只管
  // 一轮轮 poll 同一个 RPC（见 game_item_mesh_ops.py 模块注释）。
  const pollMeshComplete = useCallback(
    (nodeId: string, placeholderAssetId: string, jobId: string) => {
      const tick = async () => {
        try {
          const result = await gameApi.checkMeshCompleteStatus(projectIdRef.current, placeholderAssetId, jobId);
          onProjectUpdate(result.project);
          if (result.status === 'pending') {
            pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
            return;
          }
          pollTimers.current.delete(nodeId);
          if (result.status === 'success') {
            patchMeshOpNode(nodeId, { status: 'success', resultAssetId: result.asset?.asset_id });
            if (result.asset) spawnModelResultNodes(nodeId, 'result', 'model', [result.asset]);
          } else {
            patchMeshOpNode(nodeId, { status: 'failed', error: result.error });
          }
        } catch (err) {
          pollTimers.current.delete(nodeId);
          patchMeshOpNode(nodeId, { status: 'failed', error: err instanceof Error ? err.message : String(err) });
        }
      };
      pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
    },
    [onProjectUpdate, patchMeshOpNode, spawnModelResultNodes]
  );

  const submitMeshComplete = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const connection = edges.find((e) => e.target === nodeId && e.targetHandle === 'model');
      const sourceNode = connection ? nodes.find((n) => n.id === connection.source) : null;
      const assetId = (sourceNode?.data as { assetId?: string } | undefined)?.assetId;
      if (!assetId) return;
      patchMeshOpNode(nodeId, { status: 'submitting', error: undefined });
      void gameApi
        .meshComplete(projectIdRef.current, assetId)
        .then((result) => {
          onProjectUpdate(result.project);
          patchMeshOpNode(nodeId, {
            status: 'pending',
            placeholderAssetId: result.placeholder_asset_id,
            jobId: result.job_id,
          });
          pollMeshComplete(nodeId, result.placeholder_asset_id, result.job_id);
        })
        .catch((err) => {
          patchMeshOpNode(nodeId, {
            status: 'failed',
            error: err instanceof Error ? err.message : String(err),
          });
        });
    },
    [nodes, edges, onProjectUpdate, patchMeshOpNode, pollMeshComplete]
  );

  const pollMeshDecimate = useCallback(
    (nodeId: string, placeholderAssetId: string, jobId: string) => {
      const tick = async () => {
        try {
          const result = await gameApi.checkMeshDecimateStatus(projectIdRef.current, placeholderAssetId, jobId);
          onProjectUpdate(result.project);
          if (result.status === 'pending') {
            pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
            return;
          }
          pollTimers.current.delete(nodeId);
          if (result.status === 'success') {
            patchMeshOpNode(nodeId, { status: 'success', resultAssetId: result.asset?.asset_id });
            if (result.asset) spawnModelResultNodes(nodeId, 'result', 'model', [result.asset]);
          } else {
            patchMeshOpNode(nodeId, { status: 'failed', error: result.error });
          }
        } catch (err) {
          pollTimers.current.delete(nodeId);
          patchMeshOpNode(nodeId, { status: 'failed', error: err instanceof Error ? err.message : String(err) });
        }
      };
      pollTimers.current.set(nodeId, setTimeout(tick, DECOMPOSE_POLL_INTERVAL_MS));
    },
    [onProjectUpdate, patchMeshOpNode, spawnModelResultNodes]
  );

  const submitMeshDecimate = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const connection = edges.find((e) => e.target === nodeId && e.targetHandle === 'model');
      const sourceNode = connection ? nodes.find((n) => n.id === connection.source) : null;
      const assetId = (sourceNode?.data as { assetId?: string } | undefined)?.assetId;
      if (!assetId) return;
      patchMeshOpNode(nodeId, { status: 'submitting', error: undefined });
      void gameApi
        .meshDecimate(projectIdRef.current, assetId)
        .then((result) => {
          onProjectUpdate(result.project);
          patchMeshOpNode(nodeId, {
            status: 'pending',
            placeholderAssetId: result.placeholder_asset_id,
            jobId: result.job_id,
          });
          pollMeshDecimate(nodeId, result.placeholder_asset_id, result.job_id);
        })
        .catch((err) => {
          patchMeshOpNode(nodeId, {
            status: 'failed',
            error: err instanceof Error ? err.message : String(err),
          });
        });
    },
    [nodes, edges, onProjectUpdate, patchMeshOpNode, pollMeshDecimate]
  );

  // 画布从持久化快照挂载时，任何还停在 "pending" 的处理卡片轮询循环早就
  // 随上一次页面会话一起没了——setTimeout 链是纯内存状态，刷新/重新打开
  // 项目不会恢复它，卡片就会永远显示"提交中…/xx中…"，即使后端那边任务
  // 其实已经跑完（成功或失败）。这里在挂载时扫一遍初始快照，把还带着
  // placeholderAssetId/jobId 的 "pending" 卡片重新接上轮询——check_status
  // 这几个 RPC 都是用 job_id 直接查 Tripo3D，占位素材在后端是否还在都无
  // 所谓（mesh_decimate_check_status 甚至不要求占位素材还存在），所以能
  // 安全地在任意时刻重新开始轮询。"submitting" 状态的卡片没法恢复——
  // submit 请求本身是否真的到达后端、后端是否已经建好占位素材，前端全都
  // 无从得知，与其让它永远卡在"提交中…"，不如直接判失败，用户可以重新
  // 点一次提交。
  useEffect(() => {
    initial.nodes.forEach((node) => {
      const data = node.data as
        | (GameDecomposeNodeData & { status?: string })
        | (GameToModelNodeData & { status?: string })
        | (GameMeshOpNodeData & { status?: string })
        | undefined;
      if (!data || typeof data.status !== 'string') return;

      if (data.status === 'pending') {
        if (node.type === 'decompose') {
          const d = data as GameDecomposeNodeData;
          if (d.placeholderAssetId && d.jobId && d.decomposeBatchId) {
            pollDecompose(node.id, d.placeholderAssetId, d.jobId, d.decomposeBatchId);
          }
        } else if (node.type === 'to_model') {
          const d = data as GameToModelNodeData;
          if (d.placeholderAssetId && d.jobId) pollToModel(node.id, d.placeholderAssetId, d.jobId);
        } else if (node.type === 'mesh_complete') {
          const d = data as GameMeshOpNodeData;
          if (d.placeholderAssetId && d.jobId) pollMeshComplete(node.id, d.placeholderAssetId, d.jobId);
        } else if (node.type === 'mesh_decimate') {
          const d = data as GameMeshOpNodeData;
          if (d.placeholderAssetId && d.jobId) pollMeshDecimate(node.id, d.placeholderAssetId, d.jobId);
        }
      } else if (data.status === 'submitting') {
        const error = t('gameWorkshop.lab.resumeLostError');
        if (node.type === 'decompose') patchDecomposeNode(node.id, { status: 'failed', error });
        else if (node.type === 'to_model') patchToModelNode(node.id, { status: 'failed', error });
        else if (node.type === 'mesh_complete' || node.type === 'mesh_decimate') {
          patchMeshOpNode(node.id, { status: 'failed', error });
        }
      }
    });
    // 只在挂载时跑一次：initial.nodes 是挂载时的快照（见顶部 useRef），
    // 不应该随 nodes 状态变化反复"恢复"。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData(GAME_ASSET_DRAG_MIME);
      if (!raw) return;
      let payload: GameAssetDragPayload;
      try {
        payload = JSON.parse(raw);
      } catch {
        return;
      }
      if (payload.type !== 'character' && payload.type !== 'world') return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      setNodes((nds) =>
        nds.concat({
          id: nextNodeId('image'),
          type: 'image',
          position,
          data: { assetId: payload.assetId, filePath: payload.filePath, name: payload.name },
        })
      );
    },
    [screenToFlowPosition, setNodes]
  );

  const addProcessNode = useCallback(
    (kind: ImageProcessKind | ModelProcessKind) => {
      const { dx, dy } = nextSpawnOffset();
      const center = wrapperRef.current
        ? screenToFlowPosition({
            x: wrapperRef.current.clientWidth / 2 + dx,
            y: wrapperRef.current.clientHeight / 2 + dy,
          })
        : { x: 300 + dx, y: 200 + dy };
      if (kind === 'decompose') {
        setNodes((nds) =>
          nds.concat({
            id: nextNodeId('decompose'),
            type: 'decompose',
            position: center,
            data: { hint: '', granularity: 'medium', status: 'idle' } satisfies GameDecomposeNodeData,
          })
        );
      } else if (kind === 'toModel') {
        setNodes((nds) =>
          nds.concat({
            id: nextNodeId('to_model'),
            type: 'to_model',
            position: center,
            data: { status: 'idle' } satisfies GameToModelNodeData,
          })
        );
      } else {
        // meshComplete / meshDecimate 共用 GameMeshOpNode 组件，只是 node
        // type 不同（见 nodeTypes 顶部的映射）。
        const nodeType = kind === 'meshComplete' ? 'mesh_complete' : 'mesh_decimate';
        setNodes((nds) =>
          nds.concat({
            id: nextNodeId(nodeType),
            type: nodeType,
            position: center,
            data: { status: 'idle' } satisfies GameMeshOpNodeData,
          })
        );
      }
      setOpenMenu(null);
    },
    [screenToFlowPosition, setNodes, nextSpawnOffset]
  );

  return (
    <GameLabActionsProvider
      value={{
        deleteNode,
        renameNode,
        patchDecomposeNode,
        submitDecompose,
        patchToModelNode,
        submitToModel,
        patchMeshOpNode,
        submitMeshComplete,
        submitMeshDecimate,
      }}
    >
      <div className="game-lab-canvas-wrap" ref={wrapperRef}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          onDrop={onDrop}
          onDragOver={onDragOver}
          fitView={false}
          defaultViewport={{ x: 80, y: 40, zoom: 0.85 }}
        >
          <Background />
          <Controls />
        </ReactFlow>

        {/* 悬浮圆形工具栏——镜像导演模式 LabCanvas.tsx 的 lab-toolrail。
            "图片处理"接一张 image 节点，点开侧边菜单选"2D转3D"或"分解图"；
            "模型处理"接一个 model/model_part 节点，点开侧边菜单选"网格
            补全"或"拓扑重构"——两组各自独立的按钮+菜单，往画布中心加一张
            对应的处理卡片。 */}
        <div className="game-lab-toolrail">
          <div className="game-lab-toolrail-item">
            <button
              type="button"
              className={`game-lab-toolrail-btn ${openMenu === 'image' ? 'game-lab-toolrail-btn--active' : ''}`}
              title={t('gameWorkshop.lab.imageGroup')}
              onClick={() => setOpenMenu((v) => (v === 'image' ? null : 'image'))}
              data-testid="game-workshop-lab-toolrail-button"
              data-variant="image"
            >
              {imageGroupIcon}
            </button>
            {openMenu === 'image' && (
              <div className="game-lab-add-menu game-lab-add-menu--side" data-testid="game-workshop-lab-add-menu">
                {IMAGE_PROCESS_KINDS.map((kind) => (
                  <button
                    type="button"
                    key={kind}
                    onClick={() => addProcessNode(kind)}
                    data-testid="game-workshop-lab-add-menu-item"
                    data-variant={kind}
                  >
                    {t(`gameWorkshop.lab.process.${kind}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="game-lab-toolrail-item">
            <button
              type="button"
              className={`game-lab-toolrail-btn ${openMenu === 'model' ? 'game-lab-toolrail-btn--active' : ''}`}
              title={t('gameWorkshop.lab.modelGroup')}
              onClick={() => setOpenMenu((v) => (v === 'model' ? null : 'model'))}
              data-testid="game-workshop-lab-toolrail-button"
              data-variant="model"
            >
              {modelGroupIcon}
            </button>
            {openMenu === 'model' && (
              <div className="game-lab-add-menu game-lab-add-menu--side" data-testid="game-workshop-lab-add-menu">
                {MODEL_PROCESS_KINDS.map((kind) => (
                  <button
                    type="button"
                    key={kind}
                    onClick={() => addProcessNode(kind)}
                    data-testid="game-workshop-lab-add-menu-item"
                    data-variant={kind}
                  >
                    {t(`gameWorkshop.lab.process.${kind}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </GameLabActionsProvider>
  );
}

export function GameLabCanvas(props: GameLabCanvasProps) {
  const [mountKey, setMountKey] = useState(props.project.project_id);
  useEffect(() => setMountKey(props.project.project_id), [props.project.project_id]);
  return (
    <ReactFlowProvider key={mountKey}>
      <GameLabCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
