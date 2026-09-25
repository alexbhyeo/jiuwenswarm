import type { SessionAsset } from './sessionAssets';

const WORD_CHAR = /[\p{L}\p{N}_]/u;

/** 文本里被 @名称 引用到的素材。名称可以含空格/中文，所以按名称长度从长到短匹配，
 *  并要求 @名称 后面不是字母数字（避免 "@fox" 误匹配 "@foxes"）。 */
export function findReferencedAssets(text: string, assets: readonly SessionAsset[]): SessionAsset[] {
  if (!text.includes('@') || assets.length === 0) return [];
  const lowered = text.toLowerCase();
  const found: SessionAsset[] = [];
  const byLength = [...assets].sort((a, b) => b.name.length - a.name.length);
  const consumed: Array<[number, number]> = [];
  for (const asset of byLength) {
    const token = `@${asset.name.toLowerCase()}`;
    let from = 0;
    while (from < lowered.length) {
      const index = lowered.indexOf(token, from);
      if (index < 0) break;
      const end = index + token.length;
      const next = lowered.charAt(end);
      const overlaps = consumed.some(([s, e]) => index < e && end > s);
      if (!overlaps && (next === '' || !WORD_CHAR.test(next))) {
        consumed.push([index, end]);
        if (!found.includes(asset)) found.push(asset);
      }
      from = end;
    }
  }
  return found;
}

/** 提交给 agent 的文本：在原文后面附上每个被引用素材对应的文件路径，agent 据此把文件当作
 *  参考图/首帧等传给生成工具。没有引用时原样返回。 */
export function withAssetReferenceNote(text: string, assets: readonly SessionAsset[]): string {
  const referenced = findReferencedAssets(text, assets);
  if (referenced.length === 0) return text;
  const lines = referenced.map((asset) => `@${asset.name} = ${asset.path} (${asset.kind})`);
  return `${text}\n\n[引用素材]\n${lines.join('\n')}`;
}
