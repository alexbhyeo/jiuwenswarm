// 视频类卡片的"跟随输入比例"规则（图生视频 / 视频连视频 / 视频生视频 / 图音生视频）。
//
// 这条规则来自一个真实故障：9:16 的素材 + 16:9 的请求，服务商按请求裁帧，
// 成片变成横屏、竖屏内容被切掉一半。所以卡片默认量输入素材的宽高比，这里把
// "量出来的像素比归到哪个档位"钉住。

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  SUPPORTED_ASPECT_RATIOS,
  nearestAspectRatio,
} from '../node_modules/.cache/lab-media-ratio/labMediaRatio.js';

test('常见竖屏/横屏分辨率各自归到正确档位', () => {
  const cases = [
    // 9:16 竖屏：手机视频、竖版生成结果
    [720, 1280, '9:16'],
    [1080, 1920, '9:16'],
    [496, 864, '9:16'], // 480p 下 9:16 的实际输出（实测值）
    // 16:9 横屏
    [1280, 720, '16:9'],
    [1920, 1080, '16:9'],
    [1376, 768, '16:9'], // 略宽的 16:9 也归 16:9
    // 1:1 与 4:3
    [1024, 1024, '1:1'],
    [512, 512, '1:1'],
    [1024, 768, '4:3'],
  ];
  for (const [w, h, expected] of cases) {
    assert.equal(nearestAspectRatio(w, h), expected, `${w}x${h} 应归为 ${expected}`);
  }
});

test('横竖对称：像素比正好相反时归到相反的档位', () => {
  for (const [w, h] of [[720, 1280], [1280, 720], [768, 1024]]) {
    const forward = nearestAspectRatio(w, h);
    const back = nearestAspectRatio(h, w);
    assert.ok(forward && back);
    assert.notEqual(forward, back, `${w}x${h} 与 ${h}x${w} 不能归成同一档`);
  }
});

test('量不出尺寸时返回 null，调用方保持原比例', () => {
  assert.equal(nearestAspectRatio(0, 0), null);
  assert.equal(nearestAspectRatio(-100, 200), null);
  assert.equal(nearestAspectRatio(Number.NaN, 100), null);
  assert.equal(nearestAspectRatio(Number.POSITIVE_INFINITY, 100), null);
});

test('返回值一定是可选项之一（不会造出卡片上选不中的值）', () => {
  for (const [w, h] of [[100, 900], [900, 100], [600, 601], [4000, 2000]]) {
    const ratio = nearestAspectRatio(w, h);
    assert.ok(SUPPORTED_ASPECT_RATIOS.includes(ratio), `${w}x${h} -> ${ratio}`);
  }
});
