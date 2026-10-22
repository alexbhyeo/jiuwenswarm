// 换图弹层（ProcessNode / ImageNode 共用的 .lab-node-swap-popover）尺寸与定位。
//
// 这套逻辑只有两个失败方式，而且都不会报错、只会「看起来怪」：
//
//  1. CSS 里写的宽度/高度和 MediaFrame.tsx 导出的常量对不上。弹层是 Portal
//     出去的 fixed 浮层，定位时要用宽高把坐标夹进视口；两边一旦分叉，就是
//     「按旧宽度算坐标、按新宽度渲染」——弹层跑到屏幕外或者右边被裁掉。
//  2. 视口比弹层还小（或者触发按钮贴着右下角）时没夹住，弹层大半个身子在
//     屏幕外。这一条在弹层放大之后才真正暴露：560px 高的弹层放在 600px 高
//     的窗口里，旧代码只夹 top 到 innerHeight - margin，整个弹层会被推到
//     屏幕下方之外。
//
// 所以这里既断言几何函数的行为，也直接读 CSS 断言两边的数字一致——防止的
// 正是「改了 CSS 忘了改常量」这种静默回归。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

import {
  popoverPositionFromTrigger,
  SWAP_POPOVER_MAX_HEIGHT,
  SWAP_POPOVER_WIDTH,
} from '../node_modules/.cache/director-swap-popover/MediaFrame.js';

const CSS_PATH = fileURLToPath(
  new URL('../src/features/director/styles/director.css', import.meta.url)
);

/** 伪造一个触发按钮：只需要 getBoundingClientRect。 */
function triggerAt({ right, bottom, width = 20, height = 20 }) {
  return {
    getBoundingClientRect: () => ({
      x: right - width,
      y: bottom - height,
      left: right - width,
      top: bottom - height,
      right,
      bottom,
      width,
      height,
    }),
  };
}

/** 在给定视口下运行几何函数（函数读的是 window.innerWidth/innerHeight）。 */
function positionIn(viewport, trigger, ...args) {
  const original = globalThis.window;
  globalThis.window = { innerWidth: viewport.w, innerHeight: viewport.h };
  try {
    return popoverPositionFromTrigger(trigger, ...args);
  } finally {
    globalThis.window = original;
  }
}

test('CSS 里弹层的宽高与 MediaFrame 导出的常量一致', () => {
  const css = readFileSync(CSS_PATH, 'utf8');
  const block = css.match(/\.lab-node-swap-popover\s*\{([\s\S]*?)\n\}/);
  assert.ok(block, '找不到 .lab-node-swap-popover 规则');

  const width = block[1].match(/(?<!max-)\bwidth:\s*(\d+)px/);
  const maxHeight = block[1].match(/max-height:\s*(\d+)px/);
  assert.ok(width, 'CSS 里没有写死 width');
  assert.ok(maxHeight, 'CSS 里没有写死 max-height');

  assert.equal(
    Number(width[1]),
    SWAP_POPOVER_WIDTH,
    `CSS 的 width 是 ${width[1]}px，而 SWAP_POPOVER_WIDTH 是 ${SWAP_POPOVER_WIDTH}：` +
      '定位会按后者算，弹层会错位'
  );
  assert.equal(
    Number(maxHeight[1]),
    SWAP_POPOVER_MAX_HEIGHT,
    `CSS 的 max-height 是 ${maxHeight[1]}px，而 SWAP_POPOVER_MAX_HEIGHT 是 ` +
      `${SWAP_POPOVER_MAX_HEIGHT}：纵向夹取会算错`
  );
});

test('弹层放大到用户要求的尺寸（不是退回旧的小尺寸）', () => {
  // 回归护栏：这次改动的要求就是「弹层和里面图片都变大」。旧值是 212×260，
  // 缩回去等于把功能改坏，所以把下限钉在这里。
  assert.ok(SWAP_POPOVER_WIDTH >= 400, `width ${SWAP_POPOVER_WIDTH} 太小`);
  assert.ok(SWAP_POPOVER_MAX_HEIGHT >= 520, `max-height ${SWAP_POPOVER_MAX_HEIGHT} 太小`);
});

test('空间充足时贴在按钮右下方', () => {
  const pos = positionIn({ w: 1440, h: 900 }, triggerAt({ right: 500, bottom: 200 }), 420, 560, 8);
  // left 贴着按钮右边往左对齐，bottom + 6。
  assert.equal(pos.left, 500 - 420);
  assert.equal(pos.top, 206);
});

test('贴右边缘时整体左移，不出右边', () => {
  // right=799 时按按钮对齐会算出 379，超出 800-420-8=372 的上限，必须夹到 372。
  const pos = positionIn({ w: 800, h: 900 }, triggerAt({ right: 799, bottom: 100 }), 420, 560, 8);
  assert.equal(pos.left, 800 - 420 - 8);
  assert.ok(pos.left + 420 <= 800, '弹层右边越界');
});

test('贴下边缘时整体上移，不出下边', () => {
  const pos = positionIn({ w: 1440, h: 600 }, triggerAt({ right: 300, bottom: 560 }), 420, 560, 8);
  assert.equal(pos.top, 600 - 560 - 8);
  assert.ok(pos.top + 560 <= 600, '弹层下边越界');
});

test('视口比弹层还窄时按视口收窄，不会出现负坐标', () => {
  // 430px 宽的窗口里塞 420px 弹层：留出两侧各 8px 的余量后只剩 414px。
  const pos = positionIn({ w: 430, h: 800 }, triggerAt({ right: 420, bottom: 100 }), 420, 560, 8);
  assert.ok(pos.left >= 0, `left 变成了 ${pos.left}，弹层会跑到屏幕左侧外面`);
  assert.ok(pos.left + 414 <= 430, '收窄后仍然越界');
  assert.equal(pos.left, 8);
});

test('任何视口下都夹在视口内（含弹层自身的宽高）', () => {
  const viewports = [
    { w: 1440, h: 900 },
    { w: 1280, h: 720 },
    { w: 800, h: 600 },
    { w: 430, h: 614 },
  ];
  const triggers = [
    { right: 20, bottom: 20 },
    { right: 700, bottom: 40 },
    { right: 40, bottom: 700 },
    { right: 1400, bottom: 880 },
  ];
  for (const vp of viewports) {
    for (const t of triggers) {
      const pos = positionIn(vp, triggerAt({ right: t.right, bottom: t.bottom }), 420, 560, 8);
      const width = Math.min(SWAP_POPOVER_WIDTH, vp.w - 16);
      assert.ok(pos.left >= 0, `viewport ${vp.w}x${vp.h} trigger ${t.right},${t.bottom}: left=${pos.left}`);
      assert.ok(pos.top >= 0, `viewport ${vp.w}x${vp.h} trigger ${t.right},${t.bottom}: top=${pos.top}`);
      assert.ok(
        pos.left + width <= vp.w,
        `viewport ${vp.w}x${vp.h} trigger ${t.right},${t.bottom}: 右边越界`
      );
      assert.ok(
        pos.top + SWAP_POPOVER_MAX_HEIGHT <= vp.h,
        `viewport ${vp.w}x${vp.h} trigger ${t.right},${t.bottom}: 下边越界`
      );
    }
  }
});
