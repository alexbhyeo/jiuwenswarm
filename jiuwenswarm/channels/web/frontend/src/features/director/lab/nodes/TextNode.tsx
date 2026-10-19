import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../../directorStore';
import { useLabActions } from '../LabActionsContext';
import type { TextNodeData } from '../labTypes';
import type { DirectorAsset } from '../../types';
import { PromptTextarea } from './PromptTextarea';
import { renderWithImageChips } from './promptChips';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

export function TextNode({ id, data }: NodeProps & { data: TextNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();
  const project = useDirectorStore((s) => s.projects.find((p) => p.project_id === s.selectedProjectId) ?? null);

  // 默认展示"渲染态"（@名称 带缩略图），点一下才切成可编辑的原始文本——
  // 跟 textarea 没法同时又能编辑又能在文字中间插入图片，两种状态只能二选一，
  // 不常在编辑的时候最适合用渲染态占着。
  const [focused, setFocused] = useState(false);

  const namedAssets = useMemo(() => project?.assets.filter((a) => a.name) ?? [], [project]);
  const rendered = useMemo(() => renderWithImageChips(data.text, namedAssets), [data.text, namedAssets]);

  // "@" 下拉只给图片/角色参考用——引用一段视频/音频素材对这个文本节点
  // 没有意义（它只喂给 process 卡的 image1/image2 端口），跟 EditChatPanel
  // 的 namedImageAssets 同一个过滤口径，按名称去重。
  const namedImageAssets = useMemo(() => {
    const seen = new Set<string>();
    const result: DirectorAsset[] = [];
    for (const asset of namedAssets) {
      if ((asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.name && !seen.has(asset.name)) {
        seen.add(asset.name);
        result.push(asset);
      }
    }
    return result;
  }, [namedAssets]);

  return (
    <div className="lab-node lab-node--text">
      <div className="lab-node-label">
        {t('director.lab.textPrompt')}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>
      {focused ? (
        <PromptTextarea
          value={data.text}
          onChange={(value) => actions.updateText(id, value)}
          namedImageAssets={namedImageAssets}
          textareaClassName="lab-node-textarea nodrag"
          placeholder={t('director.lab.samplePrompt')}
          autoFocus
          onBlur={() => setFocused(false)}
        />
      ) : (
        <div
          className="lab-node-text-display nodrag"
          data-testid="lab-node-text-display"
          onClick={() => setFocused(true)}
        >
          {data.text ? rendered : <span className="lab-node-text-placeholder">{t('director.lab.samplePrompt')}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} id="text" />
    </div>
  );
}
