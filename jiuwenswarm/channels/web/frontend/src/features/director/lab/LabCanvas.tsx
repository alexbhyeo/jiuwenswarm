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
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { DIRECTOR_ASSET_DRAG_MIME } from '../types';
import type { DirectorAssetDragPayload, GenerateParams } from '../types';
import { LabActionsProvider } from './LabActionsContext';
import { ImageNode } from './nodes/ImageNode';
import { VideoNode } from './nodes/VideoNode';
import { TextNode } from './nodes/TextNode';
import { ProcessNode } from './nodes/ProcessNode';
import type { ImageNodeData, ProcessKind, ProcessNodeData, TextNodeData } from './labTypes';
import { PROCESS_KIND_MAX_IMAGES, PROCESS_KIND_MODE } from './labTypes';

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

function LabCanvasInner() {
  const { t } = useTranslation();
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { screenToFlowPosition } = useReactFlow();
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

  const handleGenerate = useCallback(
    async (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node || !selectedProjectId) return;
      const data = node.data as ProcessNodeData;
      const mode = PROCESS_KIND_MODE[data.kind];
      const maxImages = PROCESS_KIND_MAX_IMAGES[data.kind];

      const textEdge = edges.find((e) => e.target === nodeId && e.targetHandle === 'text');
      const textNode = textEdge ? nodes.find((n) => n.id === textEdge.source) : undefined;
      const prompt = textNode ? ((textNode.data as TextNodeData).text || '').trim() : '';

      const image1Edge = maxImages >= 1 ? edges.find((e) => e.target === nodeId && e.targetHandle === 'image1') : undefined;
      const image1Node = image1Edge ? nodes.find((n) => n.id === image1Edge.source) : undefined;
      const image2Edge = maxImages >= 2 ? edges.find((e) => e.target === nodeId && e.targetHandle === 'image2') : undefined;
      const image2Node = image2Edge ? nodes.find((n) => n.id === image2Edge.source) : undefined;

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
          const img1Data = image1Node?.data as ImageNodeData | undefined;
          const img2Data = image2Node?.data as ImageNodeData | undefined;
          if (img1Data?.assetId) params.firstFrameAssetId = img1Data.assetId;
          if (img2Data?.assetId) params.lastFrameAssetId = img2Data.assetId;
        } else {
          const img1Data = image1Node?.data as ImageNodeData | undefined;
          if (img1Data?.assetId) params.referenceAssetId = img1Data.assetId;
        }

        const result = await directorGenerate(params);
        void useDirectorStore.getState().loadProjects();
        const outputAsset = result.project.assets.find((a) => a.asset_id === result.assetId);

        const outputNodeId = nextNodeId('out');
        const outputType = outputAsset?.type === 'video' ? 'video' : 'image';
        addNode({
          id: outputNodeId,
          type: outputType,
          position: { x: node.position.x + 360, y: node.position.y },
          data: {
            assetId: outputAsset?.asset_id ?? null,
            filePath: outputAsset?.file_path ?? '',
            name: outputAsset?.name || outputAsset?.prompt || '',
          },
        });
        setEdges((eds) =>
          addEdge(
            {
              id: nextNodeId('edge'),
              source: nodeId,
              sourceHandle: 'out',
              target: outputNodeId,
              targetHandle: outputType === 'video' ? 'in' : undefined,
            },
            eds
          )
        );

        if (outputAsset?.status === 'pending' && outputAsset.job_id) {
          pollOutputNode(nodeId, outputNodeId, selectedProjectId, outputAsset.asset_id, outputAsset.job_id);
        } else {
          setProcessNodeState(nodeId, { status: 'idle' });
        }
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setProcessNodeState(nodeId, { status: 'error', error: message });
      }
    },
    [nodes, edges, selectedProjectId, addNode, setEdges, setProcessNodeState, pollOutputNode]
  );

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
      addNode({
        id: nextNodeId(payload.type),
        type: payload.type,
        position,
        data: { assetId: payload.assetId, filePath: payload.filePath, name: payload.name },
      });
    },
    [screenToFlowPosition, addNode]
  );

  const handleAddText = () => {
    const center = wrapperRef.current
      ? screenToFlowPosition({ x: wrapperRef.current.clientWidth / 2, y: wrapperRef.current.clientHeight / 2 })
      : { x: 200, y: 200 };
    addNode({ id: nextNodeId('text'), type: 'text', position: center, data: { text: '' } });
    setOpenMenu(null);
  };

  const handleAddProcess = (kind: ProcessKind) => {
    const center = wrapperRef.current
      ? screenToFlowPosition({ x: wrapperRef.current.clientWidth / 2 + 80, y: wrapperRef.current.clientHeight / 2 })
      : { x: 400, y: 200 };
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
