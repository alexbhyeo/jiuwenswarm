import '@google/model-viewer';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { SkeletonModelViewer } from './SkeletonModelViewer';

const closeIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);

/** 3D 部件/模型的大图弹窗——拖拽旋转查看不同视角，点卡片缩略图触发。
 *  用 createPortal 挂到 body 上，不然画布的缩放/平移变换会把它也一起
 *  缩放、裁切（见 GameLabCanvas 的 react-flow viewport transform）。
 *
 *  `showSkeleton` 为自动绑定的产出打开：大图里同样是模型 + 骨架，并且
 *  可以自由旋转查看骨骼是否贴合。 */
export function ModelViewerModal({
  src,
  alt,
  showSkeleton = false,
  onClose,
}: {
  src: string;
  alt: string;
  showSkeleton?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return createPortal(
    <div
      className="game-lab-model-viewer-overlay"
      onClick={onClose}
      data-testid="game-workshop-lab-model-viewer-modal"
    >
      <div className="game-lab-model-viewer-modal" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="game-lab-model-viewer-close"
          title={t('common.close')}
          onClick={onClose}
          data-testid="game-workshop-lab-model-viewer-modal-close"
        >
          {closeIcon}
        </button>
        {showSkeleton ? (
          <SkeletonModelViewer
            src={src}
            alt={alt}
            className="game-lab-skeleton-viewer"
            showSkeleton
            interactive
            testId="game-workshop-lab-model-viewer-modal-skeleton"
          />
        ) : (
          <model-viewer src={src} alt={alt} camera-controls style={{ width: '100%', height: '100%' }} />
        )}
      </div>
    </div>,
    document.body
  );
}
