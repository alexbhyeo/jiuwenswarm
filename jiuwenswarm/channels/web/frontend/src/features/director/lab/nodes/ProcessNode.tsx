import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { ImageNodeData, ProcessNodeData, TextNodeData } from '../labTypes';
import { PROCESS_KIND_MAX_IMAGES, PROCESS_KIND_MODE } from '../labTypes';

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

export function ProcessNode({ id, data }: NodeProps & { data: ProcessNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const maxImages = PROCESS_KIND_MAX_IMAGES[data.kind];
  const mode = PROCESS_KIND_MODE[data.kind];

  const text = useConnectedText(id, 'text');
  const image1 = useConnectedImage(id, maxImages >= 1 ? 'image1' : '__none__');
  const image2 = useConnectedImage(id, maxImages >= 2 ? 'image2' : '__none__');

  const requiresImage = maxImages > 0;
  const canGenerate = data.status !== 'generating' && (!requiresImage || !!image1) && (text.trim().length > 0 || !!image1);

  return (
    <div className={`lab-node lab-node--process lab-node--process-${data.kind}`}>
      <div className="lab-node-label">
        {t(`director.lab.process.${data.kind}`)}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>

      <div className="lab-node-ports">
        <div className="lab-node-input-row">
          <Handle type="target" position={Position.Left} id="text" />
          <span className="lab-node-input-dot" />
          {t('director.lab.textPrompt')}
          {text && <span className="lab-node-input-filled">✓</span>}
        </div>

        {maxImages >= 1 && (
          <div className="lab-node-input-row">
            <Handle type="target" position={Position.Left} id="image1" />
            <span className="lab-node-input-dot" />
            {maxImages >= 2 ? t('director.lab.firstFrame') : t('director.lab.refImage')}
            {image1 && <span className="lab-node-input-filled">✓</span>}
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
      </div>

      <div className="lab-node-params">
        <div className="lab-node-param-row">
          <span>{t('director.lab.model')}</span>
          <span className="lab-node-param-value">
            {mode === 'video' ? 'seedance-2.0-fast' : 'gemini-3.1-flash-image'} {chevronDown}
          </span>
        </div>
        <div className="lab-node-param-row">
          <span>{t('director.lab.genParams')}</span>
          <span className="lab-node-param-value">
            {data.aspectRatio} · {data.resolution}
            {mode === 'video' ? ` · ${data.durationSeconds}s` : ''}
          </span>
        </div>
      </div>

      {data.status === 'error' && data.error && <div className="lab-node-error">{data.error}</div>}

      <button
        type="button"
        className="lab-node-generate-btn"
        disabled={!canGenerate}
        onClick={() => actions.generate(id, { prompt: text, image1, image2 })}
      >
        {data.status === 'generating' ? (
          <span className="lab-node-spinner" />
        ) : (
          <>
            {generateIcon}
            {t('director.lab.generate')}
          </>
        )}
      </button>

      <Handle type="source" position={Position.Right} id="out" />
    </div>
  );
}
