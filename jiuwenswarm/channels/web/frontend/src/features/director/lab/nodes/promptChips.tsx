import type { ReactNode } from 'react';
import type { DirectorAsset } from '../../types';
import { escapeRegExp } from '../atMention';

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

/** 把 "@名称" 边界的判定规则跟后端 handle_director_generate 的
 *  prompt 清洗逻辑（director_manager.py）对齐：不按 \S+ 切词（中文场景
 *  "@道具名放在@场景名上"没有空格分隔），而是直接用项目里已有的素材名称
 *  构造 "@(名称1|名称2|…)" 联合正则，名称按长度降序排列，保证互为前缀的
 *  两个名称优先命中更长的那个。只有图片/角色类型的就绪素材才渲染缩略图
 *  chip——命中了别的类型（比如视频）的素材名也算一次合法引用边界，但
 *  只按纯文本展示，不是这个节点能当参考图用的东西。
 *
 *  TextNode（卡片未编辑时的渲染态）和 ProcessNode（文字提示弹窗未编辑时的
 *  渲染态）共用这一份，而不是各自复制——两处都是"同一段文字的只读展示"，
 *  复制过一次就有后续改动两边不同步的风险。 */
export function renderWithImageChips(text: string, namedAssets: DirectorAsset[]): ReactNode {
  if (!text || namedAssets.length === 0) return text;
  const byName = new Map<string, DirectorAsset>();
  for (const asset of namedAssets) {
    if (asset.name) byName.set(asset.name, asset);
  }
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`@(${names.map(escapeRegExp).join('|')})`, 'g');

  const nodes: ReactNode[] = [];
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
