/**
 * "替换"弹层里该列出哪些素材。
 *
 * 候选类型必须跟着卡片的**产物类型**走：视频卡片只列视频素材、图片卡片只
 * 列图片/角色素材。列错类型的素材"选中了也放不出来"——卡片会显示一段视频
 * 却按图片渲染（或者反过来），而且下游端口拿到的 assetId 类型也对不上。
 */

export type SwappableMode = 'image' | 'video' | 'audio';

export interface SwappableAssetLike {
  asset_id: string;
  type: string;
  status: string;
  file_path?: string | null;
  updated_at?: number | string;
}

export function filterSwappableAssets<T extends SwappableAssetLike>(
  assets: readonly T[],
  mode: SwappableMode,
  currentAssetId: string | null | undefined
): T[] {
  const wanted = (a: T): boolean => {
    if (mode === 'video') return a.type === 'video';
    if (mode === 'audio') return a.type === 'audio';
    return a.type === 'image' || a.type === 'character';
  };
  return assets
    .filter(
      (a) =>
        wanted(a) &&
        a.status === 'ready' &&
        !!a.file_path &&
        // 当前正展示的这一条不算候选——选它等于什么都没换。
        a.asset_id !== currentAssetId
    )
    .sort((a, b) => Number(b.updated_at ?? 0) - Number(a.updated_at ?? 0));
}

/**
 * 换完之后卡片（那条历史记录）该叫什么名字。
 *
 * 视频卡片要**保住卡片自己的名字**：卡片的名字是它在流程图里的身份，下游
 * 卡片、历史记录、重建流程图都按这个名字认它；换成被挑中那段片子的名字之后，
 * 卡片就"变成"了素材库里那一条，用户再也说不出"我这张卡片换成了哪一段"。
 * 而且视频名字本来也不会被解析成 `@名称` 参考（后端只把 image/character 的
 * 名字当参考图解析），改用素材名一点好处都没有，只有损失。
 *
 * 图片卡片相反，沿用原来的行为（改用素材自己的名字）：挑中一张已经起好名字
 * 的素材（比如"小水獭"）本来就是为了之后用 `@小水獭` 引用它。
 *
 * 本地上传那条路径本来就是保住卡片名字的（handleSwapMedia 上传完会把新素材
 * 改名成卡片原来的名字），这里让"从素材库挑"跟它对齐。
 */
export function swappedEntryName(previousName: string, assetName: string, mode: SwappableMode): string {
  if (mode === 'video') return previousName || assetName || '';
  return assetName || previousName || '';
}
