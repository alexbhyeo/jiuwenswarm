import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { ImageNodeData, ProcessNodeData, TextNodeData } from '../labTypes';
import { OUTPUT_COUNT_OPTIONS, PROCESS_KIND_MAX_IMAGES, PROCESS_KIND_MODE } from '../labTypes';

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
  const outputCount = data.outputCount || 1;

  const text = useConnectedText(id, 'text');
  const image1 = useConnectedImage(id, maxImages >= 1 ? 'image1' : '__none__');
  const image2 = useConnectedImage(id, maxImages >= 2 ? 'image2' : '__none__');

  const requiresImage = maxImages > 0;
  const canGenerate = data.status !== 'generating' && (!requiresImage || !!image1) && (text.trim().length > 0 || !!image1);

  const [paramsOpen, setParamsOpen] = useState(false);
  const paramsRef = useRef<HTMLDivElement>(null);

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

  const resolutionOptions = mode === 'video' ? VIDEO_RESOLUTION_OPTIONS : IMAGE_RESOLUTION_OPTIONS;

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

      <div className="lab-node-params" ref={paramsRef}>
        <div className="lab-node-param-row">
          <span>{t('director.lab.model')}</span>
          <span className="lab-node-param-value">
            {mode === 'video' ? 'seedance-2.0-fast' : 'gemini-3.1-flash-image'} {chevronDown}
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
            {data.aspectRatio} · {data.resolution}
            {mode === 'video' ? ` · ${data.durationSeconds}s` : ''}
            {outputCount > 1 ? ` · ×${outputCount}` : ''}
            {chevronDown}
          </span>
        </div>

        {paramsOpen && (
          <div className="lab-node-params-popover nodrag">
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
          </div>
        )}
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
