import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../../directorStore';
import { useLabActions } from '../LabActionsContext';
import type { ImageNodeData } from '../labTypes';
import { MediaFrame, popoverPositionFromTrigger, rawFileUrl } from './MediaFrame';
import { NodeNameLabel } from './NodeNameLabel';

const enlargeIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
  </svg>
);

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

const swapIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 3l4 4-4 4M21 7H7M7 21l-4-4 4-4M3 17h14" />
  </svg>
);

export function ImageNode({ id, data }: NodeProps & { data: ImageNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const project = useDirectorStore((s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null);
  const imgRef = useRef<HTMLImageElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [swapOpen, setSwapOpen] = useState(false);
  // 跟 ProcessNode 同一个取舍——弹层 Portal 到 document.body 之后，触发
  // 按钮和弹层本身不再是同一棵 DOM 子树，点空白处关闭要同时认这两个 ref。
  const swapWrapRef = useRef<HTMLDivElement>(null);
  const swapPopoverRef = useRef<HTMLDivElement>(null);
  const [swapPos, setSwapPos] = useState<{ top: number; left: number } | null>(null);

  // 跟 ProcessNode 的"替换"弹层同一个取舍——见那边 swappableAssets 的注释。
  const swappableAssets = useMemo(() => {
    if (!project) return [];
    return project.assets
      .filter(
        (a) =>
          (a.type === 'image' || a.type === 'character') &&
          a.status === 'ready' &&
          a.file_path &&
          a.asset_id !== data.assetId
      )
      .sort((a, b) => b.updated_at - a.updated_at);
  }, [project, data.assetId]);

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

  return (
    <div className="lab-node lab-node--image">
      <div className="lab-node-label">{t('director.lab.refImage')}</div>
      <div className="lab-node-media">
        <MediaFrame ref={imgRef} mode="image" filePath={data.filePath} name={data.name} className="nodrag" />
        <div className="lab-node-media-actions">
          <button type="button" title={t('director.enlarge')} onClick={() => imgRef.current?.requestFullscreen?.()}>
            {enlargeIcon}
          </button>
          <div className="lab-node-swap-wrap nodrag" ref={swapWrapRef}>
            <button
              type="button"
              title={t('director.lab.swapImage')}
              onClick={(e) => {
                if (swapOpen) {
                  setSwapOpen(false);
                  return;
                }
                setSwapPos(popoverPositionFromTrigger(e.currentTarget));
                setSwapOpen(true);
              }}
              data-testid="lab-process-node-image-ref-swap"
            >
              {swapIcon}
            </button>
            {/* .lab-node-media 有 overflow:hidden 裁掉超出卡片的内容（给图片
                裁圆角用），弹层自己比卡片高得多，不 Portal 出去会被从中间
                切掉——同 ProcessNode 换图弹层同一个修法。 */}
            {swapOpen &&
              swapPos &&
              createPortal(
                <div
                  className="lab-node-swap-popover nowheel"
                  ref={swapPopoverRef}
                  style={{ top: swapPos.top, left: swapPos.left }}
                  data-testid="lab-process-node-image-ref-swap-popover"
                >
                  <button
                    type="button"
                    className="lab-node-swap-popover-upload"
                    onClick={() => {
                      setSwapOpen(false);
                      fileInputRef.current?.click();
                    }}
                    data-testid="lab-process-node-image-ref-swap-upload-local"
                  >
                    {t('director.lab.swapUploadLocal')}
                  </button>
                  <div className="lab-node-swap-popover-grid">
                    {swappableAssets.length === 0 ? (
                      <div className="lab-node-swap-popover-empty">{t('director.lab.swapNoAssets')}</div>
                    ) : (
                      swappableAssets.map((asset) => (
                        <button
                          type="button"
                          key={asset.asset_id}
                          className="lab-node-swap-popover-item"
                          title={asset.name || asset.prompt || ''}
                          onClick={() => {
                            setSwapOpen(false);
                            actions.swapImageFromAsset(id, {
                              assetId: asset.asset_id,
                              filePath: asset.file_path as string,
                              name: asset.name || asset.prompt || '',
                            });
                          }}
                          data-testid="lab-process-node-image-ref-swap-asset"
                          data-variant={asset.asset_id}
                        >
                          <img src={rawFileUrl(asset.file_path as string)} alt={asset.name || ''} />
                        </button>
                      ))
                    )}
                  </div>
                </div>,
                document.body
              )}
          </div>
          <button type="button" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
            {trashIcon}
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="lab-node-swap-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) actions.swapImage(id, file);
            e.target.value = '';
          }}
        />
      </div>
      <NodeNameLabel name={data.name} fallback={t('director.lab.refImage')} onRename={(name) => actions.renameNode(id, name)} />
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="source" position={Position.Right} id="image" />
    </div>
  );
}
