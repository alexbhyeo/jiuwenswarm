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
import { gameApi, type GameProject } from '../gameApi';
import { GameLabActionsProvider } from './GameLabActionsContext';
import { GAME_ASSET_DRAG_MIME, type GameAssetDragPayload, type GameDecomposeNodeData } from './labTypes';
import { GameImageNode } from './nodes/GameImageNode';
import { GameModelPartNode } from './nodes/GameModelPartNode';
import { GameDecomposeNode } from './nodes/GameDecomposeNode';
import './gameLab.css';

const nodeTypes: NodeTypes = {
  image: GameImageNode,
  model_part: GameModelPartNode,
  decompose: GameDecomposeNode,
};

// 与导演模式同一个取舍（LabCanvas.tsx 的 LAB_CANVAS_SAVE_DEBOUNCE_MS）：
// 拖拽/连线会连续触发多次节点变化，等操作停下来一小段时间再落盘一次。
const LAB_CANVAS_SAVE_DEBOUNCE_MS = 800;
const DECOMPOSE_POLL_INTERVAL_MS = 5000;

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
  const { screenToFlowPosition } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const projectIdRef = useRef(project.project_id);
  projectIdRef.current = project.project_id;
  const pollTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

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
            patchDecomposeNode(nodeId, { status: 'success', resultCount: result.assets?.length ?? 0 });
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
    [onProjectUpdate, patchDecomposeNode]
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

  const addDecomposeNode = useCallback(() => {
    const center = wrapperRef.current
      ? screenToFlowPosition({ x: wrapperRef.current.clientWidth / 2, y: wrapperRef.current.clientHeight / 2 })
      : { x: 300, y: 200 };
    setNodes((nds) =>
      nds.concat({
        id: nextNodeId('decompose'),
        type: 'decompose',
        position: center,
        data: { hint: '', granularity: 'medium', status: 'idle' } satisfies GameDecomposeNodeData,
      })
    );
  }, [screenToFlowPosition, setNodes]);

  return (
    <GameLabActionsProvider value={{ deleteNode, renameNode, patchDecomposeNode, submitDecompose }}>
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
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
        <button
          type="button"
          className="game-lab-add-decompose"
          onClick={addDecomposeNode}
          data-testid="game-workshop-lab-add-decompose-button"
        >
          {t('gameWorkshop.lab.addDecomposeCard')}
        </button>
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
