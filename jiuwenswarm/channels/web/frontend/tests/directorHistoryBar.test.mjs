// 卡片上的 "‹ N/M ›" 切换条要**恒定**出现（只要已经生成过），即使只有一份产物
// 也显示 "1/1"。
//
// 之前是 history.length > 1 才渲染，于是同一列里"只有一份结果"的卡片比"有多份
// 结果"的卡片矮一截，名字行和生成按钮跟着往上错，整列看起来不齐。这里钉死两点：
//  1. 渲染条件是"生成过"而不是"多于一条"；
//  2. 1/1 时两个箭头都是禁用态（而不是看起来可点却没反应）。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const src = (rel) => readFileSync(fileURLToPath(new URL(`../src/features/director/${rel}`, import.meta.url)), 'utf8');
const PROCESS_NODE = src('lab/nodes/ProcessNode.tsx');
const CSS = src('styles/director.css');

const barBlock = () => {
  const start = PROCESS_NODE.indexOf('lab-node-history-bar');
  assert.ok(start > 0, '找不到切换条');
  return PROCESS_NODE.slice(start, PROCESS_NODE.indexOf('<NodeNameLabel', start));
};

test('只有一份产物时也渲染切换条（1/1）', () => {
  assert.match(PROCESS_NODE, /\{data\.history\.length > 0 && \(/, '渲染条件不是"生成过"，只有一份产物的卡片仍会缺这一条');
  assert.ok(
    !/\{data\.history\.length > 1 && \(\s*<div className="lab-node-history-bar/.test(PROCESS_NODE),
    '又退回成"多于一条才显示"了——卡片高度会再次不一致'
  );
});

test('位置标签显示 当前/总数', () => {
  assert.match(barBlock(), /\{data\.activeHistoryIndex \+ 1\}\/\{data\.history\.length\}/, '位置标签不是 N/M 形式');
});

test('1/1 时两个箭头都是禁用态', () => {
  const block = barBlock();
  assert.match(block, /disabled=\{data\.activeHistoryIndex <= 0\}/, '第一个箭头在只有一条时没有禁用');
  assert.match(block, /disabled=\{data\.activeHistoryIndex >= data\.history\.length - 1\}/, '最后一个箭头在只有一条时没有禁用');
  // 禁用态必须有可见的样式差异，否则 1/1 的两个箭头看起来能点
  assert.match(CSS, /\.lab-node-history-btn:disabled \{[\s\S]{0,120}opacity:/, '禁用态没有视觉区分');
});

test('切换条只在已生成状态里渲染', () => {
  const generatedAt = PROCESS_NODE.indexOf('{isGenerated && activeEntry && (');
  const barAt = PROCESS_NODE.indexOf('lab-node-history-bar');
  assert.ok(generatedAt > 0 && barAt > generatedAt, '切换条跑到了"已生成"条件之外——空卡片上也会出现 0/0');
});
