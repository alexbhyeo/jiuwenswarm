import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../../directorStore';
import { useLabActions } from '../LabActionsContext';
import type { TextNodeData } from '../labTypes';
import type { DirectorAsset } from '../../types';
import { escapeRegExp } from '../atMention';
import { PromptTextarea } from './PromptTextarea';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 把 "@名称" 边界的判定规则跟后端 handle_director_generate 的
 *  prompt 清洗逻辑（director_manager.py）对齐：不按 \S+ 切词（中文场景
 *  "@道具名放在@场景名上"没有空格分隔），而是直接用项目里已有的素材名称
 *  构造 "@(名称1|名称2|…)" 联合正则，名称按长度降序排列，保证互为前缀的
 *  两个名称优先命中更长的那个。只有图片/角色类型的就绪素材才渲染缩略图
 *  chip——命中了别的类型（比如视频）的素材名也算一次合法引用边界，但
 *  只按纯文本展示，不是这个节点能当参考图用的东西。 */
function renderWithImageChips(text: string, namedAssets: DirectorAsset[]): React.ReactNode {
  if (!text || namedAssets.length === 0) return text;
  const byName = new Map<string, DirectorAsset>();
  for (const asset of namedAssets) {
    if (asset.name) byName.set(asset.name, asset);
  }
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`@(${names.map(escapeRegExp).join('|')})`, 'g');

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    const asset = byName.get(match[1]);
    const canShowThumb = asset && (asset.type === 'image' || asset.type === 'character') && asset.status === 'ready' && asset.file_path;
    if (canShowThumb && asset) {
      nodes.push(
        <span key={`chip-${key++}`} className="lab-node-text-chip">
          <img src={rawFileUrl(asset.file_path as string)} alt={asset.name ?? ''} />
          {match[0]}
        </span>
      );
    } else {
      nodes.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

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
