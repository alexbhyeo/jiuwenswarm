import { Handle, Position, useNodeConnections, useNodesData, type NodeProps } from '@xyflow/react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import { AUDIO_VOICE_OPTIONS, DEFAULT_AUDIO_VOICE } from '../../audioVoices';
import type { AudioNodeData, ImageNodeData, ProcessNodeData, TextNodeData, VideoNodeData } from '../labTypes';
import {
  OUTPUT_COUNT_OPTIONS,
  PROCESS_KIND_MAX_AUDIOS,
  PROCESS_KIND_MAX_IMAGES,
  PROCESS_KIND_MAX_VIDEOS,
  PROCESS_KIND_MODE,
  PROCESS_KIND_MULTI_REF,
} from '../labTypes';

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

/** video2audio（视频生音频）的 video1 端口：取那一路输入视频。与
 *  useConnectedImage 同构，只是读的是视频输出节点的 data。 */
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
  const maxImages = PROCESS_KIND_MAX_IMAGES[data.kind];
  const maxVideos = PROCESS_KIND_MAX_VIDEOS[data.kind];
  const maxAudios = PROCESS_KIND_MAX_AUDIOS[data.kind];
  const mode = PROCESS_KIND_MODE[data.kind];
  const outputCount = data.outputCount || 1;
  const isMultiRef = !!PROCESS_KIND_MULTI_REF[data.kind];

  const text = useConnectedText(id, 'text');
  const image1 = useConnectedImage(id, maxImages >= 1 && !isMultiRef ? 'image1' : '__none__');
  const image2 = useConnectedImage(id, maxImages >= 2 ? 'image2' : '__none__');
  const images = useConnectedImages(id, isMultiRef ? 'image1' : '__none__');
  const video1 = useConnectedVideo(id, maxVideos >= 1 ? 'video1' : '__none__');
  const audio1 = useConnectedAudio(id, maxAudios >= 1 ? 'audio1' : '__none__');

  // 参考音频的两条来路：端口连线，或卡片上填的 HTTPS 直链。填了链接就以
  // 链接为准（输入行上会标出来），避免"连了素材却在用链接"这种看不见的替换。
  const audioUrl = (data.audioUrl ?? '').trim();
  const hasAudioUrl = audioUrl.length > 0;
  const audioConnected = !!audio1;

  const requiresImage = maxImages > 0;
  // 视频生音频必须先连一路视频（解说文案是从它写出来的）；文本节点可选，
  // 连了就当作用户对解说的额外要求。
  const requiresVideo = maxVideos > 0;
  // 图音生视频必须拿到一路参考音频——连了音频素材、或者在卡片上填了音频
  // 链接都算，两条都没有时这张卡片就不再是"图音生视频"了。
  const requiresAudio = maxAudios > 0;
  const hasAnyImage = isMultiRef ? images.length > 0 : !!image1;
  const canGenerate =
    data.status !== 'generating' &&
    (!requiresImage || hasAnyImage) &&
    (!requiresVideo || !!video1) &&
    (!requiresAudio || audioConnected || hasAudioUrl) &&
    (text.trim().length > 0 || hasAnyImage || !!video1 || audioConnected || hasAudioUrl);

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
  // 文生音频没有宽高比/分辨率/时长这些画面参数，改成音色选择。
  const isAudio = mode === 'audio';
  const voice = data.voice || DEFAULT_AUDIO_VOICE;
  // 只有带音频输入端口的卡片（imageAudio2video）才需要选"成片有没有声音"。
  const hasAudioOption = maxAudios >= 1;
  const audioOutput = data.generateAudio !== false;
  const audioOutputLabel = t(audioOutput ? 'director.lab.audioOn' : 'director.lab.audioOff');

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
            {t('director.lab.inputVideo')}
            {video1 && <span className="lab-node-input-filled">✓</span>}
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

            {hasAudioOption && (
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

            {hasAudioOption && (
              <div className="lab-node-params-popover-section">
                <div className="lab-node-params-popover-label">{t('director.lab.audioOutputLabel')}</div>
                <div className="lab-node-params-popover-pills">
                  <button
                    type="button"
                    className={`lab-node-param-pill ${audioOutput ? 'lab-node-param-pill--active' : ''}`}
                    onClick={() => actions.patchProcessNode(id, { generateAudio: true })}
                  >
                    {t('director.lab.audioOn')}
                  </button>
                  <button
                    type="button"
                    className={`lab-node-param-pill ${!audioOutput ? 'lab-node-param-pill--active' : ''}`}
                    onClick={() => actions.patchProcessNode(id, { generateAudio: false })}
                  >
                    {t('director.lab.audioOff')}
                  </button>
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
        onClick={() => actions.generate(id, { prompt: text, image1, image2, images, video1, audio1 })}
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
