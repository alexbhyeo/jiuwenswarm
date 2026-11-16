/** 本地素材文件的可访问 URL——与导演模式 MediaFrame.tsx 的 rawFileUrl 同构，
 * 各自独立一份而不是跨 feature 导入，保持游戏工坊与导演模式的模块边界。 */
export function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}
