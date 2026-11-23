/**
 * 视频类卡片的画面比例：从输入素材推断，而不是永远用卡片默认的 16:9。
 *
 * 背景：生视频接口在有首帧/参考帧时按**帧的比例**决定成片几何，请求里的
 * aspect_ratio 只是一个"配合项"——两者不一致时服务商裁切帧，成片比例跟着
 * 帧走（后端 video_gen_tools 也是这么做的：请求比例用于居中裁切本地帧）。
 * 所以卡片比例一旦停在 16:9，9:16 的素材就会被裁成横屏，用户看到的就是
 * "提示词和输入都是竖屏，出来的却是横屏"。
 *
 * 这里把"量素材宽高比 → 归到支持的档位"做成纯函数 + 两个 DOM 测量函数，
 * 纯函数部分可单测（见 tests/labMediaRatio.test.mjs）。
 */

/** 卡片上可选的画面比例，与 ProcessNode 的药丸顺序一致。 */
export const SUPPORTED_ASPECT_RATIOS = ['16:9', '9:16', '1:1', '4:3'] as const;
export type SupportedAspectRatio = (typeof SUPPORTED_ASPECT_RATIOS)[number];

/** 按宽/高把实际像素比归到最接近的支持档位（对数距离，横竖对称）。
 *
 *  取对数而不是线性差，是因为 2:1 与 16:9 的"观感差"跟 9:16 与 1:2 一样，
 *  而线性差会明显偏向宽比例。量不出来（宽高非正）时返回 null，调用方保持
 *  原值不动。 */
export function nearestAspectRatio(width: number, height: number): SupportedAspectRatio | null {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  const actual = Math.log(width / height);
  let best: SupportedAspectRatio | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const candidate of SUPPORTED_ASPECT_RATIOS) {
    const [w, h] = candidate.split(':').map(Number);
    const distance = Math.abs(Math.log(w / h) - actual);
    if (distance < bestDistance) {
      best = candidate;
      bestDistance = distance;
    }
  }
  return best;
}

function measureWith(create: (done: (width: number, height: number) => void, fail: () => void) => void): Promise<string | null> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ratio: string | null) => {
      if (settled) return;
      settled = true;
      resolve(ratio);
    };
    create(
      (width, height) => finish(nearestAspectRatio(width, height)),
      () => finish(null),
    );
    // 媒体元数据拿不到时不能让卡片一直等——保持原比例，用户仍可手动选。
    setTimeout(() => finish(null), 8000);
  });
}

/** 量一张图片（通常是 /file-api/raw-file 的本地素材地址）。 */
export function measureImageAspectRatio(url: string): Promise<string | null> {
  if (!url) return Promise.resolve(null);
  return measureWith((done, fail) => {
    const img = new Image();
    img.onload = () => done(img.naturalWidth, img.naturalHeight);
    img.onerror = fail;
    img.src = url;
  });
}

/** 量一段视频的首帧尺寸（videoWidth/videoHeight 只要元数据，不下载整段）。 */
export function measureVideoAspectRatio(url: string): Promise<string | null> {
  if (!url) return Promise.resolve(null);
  return measureWith((done, fail) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.onloadedmetadata = () => done(video.videoWidth, video.videoHeight);
    video.onerror = fail;
    video.src = url;
  });
}
