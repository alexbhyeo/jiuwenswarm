/** "@名称"引用的检测/匹配工具——被 PromptTextarea（@ 下拉）和 TextNode
 *  （渲染态的缩略图 chip）共用。两处都需要同一套边界判定规则，这次抽成
 *  共享文件而不是各自复制一份：复制的是检测规则本身（会跟后端
 *  director_manager.py 的 prompt 清洗正则对齐），两边一旦其中一处改了
 *  边界判定、另一处没跟着改，就会出现"下拉里选中的名字渲染不出缩略图"这
 *  种难查的不一致——比前面 TextNode/ComposerCard 之间各自独立复制的小工
 *  具函数更容易踩坑，值得提出来维护一份。 */

export interface AtMenuState {
  start: number;
  query: string;
}

/** 光标之前最近的 "@token"（token 内不含空白，"@" 前须是行首或空白）。 */
export function detectAtToken(value: string, cursor: number): AtMenuState | null {
  const upto = value.slice(0, cursor);
  const atIndex = upto.lastIndexOf('@');
  if (atIndex === -1) return null;
  const query = upto.slice(atIndex + 1);
  if (/\s/.test(query)) return null;
  const before = atIndex > 0 ? upto[atIndex - 1] : '';
  if (before && !/\s/.test(before)) return null;
  return { start: atIndex, query };
}

/** 转义正则特殊字符，构造"@名称1|名称2|…"联合正则时名称本身可能含有
 *  正则元字符（圆括号/点号等）。 */
export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
