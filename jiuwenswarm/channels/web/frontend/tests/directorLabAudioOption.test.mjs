import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PROCESS_KIND_MAX_AUDIOS,
  PROCESS_KIND_MODE,
  defaultGenerateAudio,
  supportsAudioOutput,
} from '../node_modules/.cache/director-lab-audio/labTypes.js';

// 实验室卡片的"有声音/无声音"选项：判据是**产物是不是视频**，而不是"有没有
// 音频输入端口"。文生视频/图生视频没有参考音频，选"有声音"就是让生成模型自己
// 配一条音轨；图音生视频的音轨来自连进来的参考音频。三者都得能选。
test('every video-producing card offers the audio option', () => {
  for (const kind of ['text2video', 'image2video', 'imageAudio2video', 'video2video', 'videoBridge']) {
    assert.equal(supportsAudioOutput(kind), true, `${kind} 应该能选声音`);
  }
});

test('non-video cards do not offer the audio option', () => {
  // 静态图（文生图/参考图合成）与音频（文生音频/视频生音频）都没有"成片音轨"
  // 这回事，给出这个选项只会让用户以为能控制什么。
  for (const kind of ['text2image', 'imageRef', 'text2audio', 'video2audio']) {
    assert.equal(supportsAudioOutput(kind), false, `${kind} 不该有声音选项`);
  }
});

test('the option is offered for exactly the video-producing kinds', () => {
  const withOption = Object.keys(PROCESS_KIND_MODE).filter((k) => supportsAudioOutput(k));
  const videoKinds = Object.keys(PROCESS_KIND_MODE).filter((k) => PROCESS_KIND_MODE[k] === 'video');
  // 从 kind 表推导而不是写死清单：之前写死三项，后来新增视频生视频/视频连视频
  // 两张视频卡片时这里就悄悄过期了。
  assert.deepEqual(withOption.sort(), videoKinds.sort());
  assert.ok(videoKinds.length >= 3, '至少要覆盖文生视频/图生视频/图音生视频');
});

test('audio-input cards default to sound-on, others keep the silent default', () => {
  // 图音生视频的输入就是一段音轨，默认给成片配上最符合预期。
  assert.equal(defaultGenerateAudio('imageAudio2video'), true);
  // 文生视频/图生视频历史上产出的都是无声片段：默认静音以免悄悄改变已有画布
  // 的行为（后端 `bool(params.get("generate_audio") or False)` 也是这个默认）。
  assert.equal(defaultGenerateAudio('text2video'), false);
  assert.equal(defaultGenerateAudio('image2video'), false);
  // 非视频卡片恒为 false，且不参与生成参数
  for (const kind of ['text2image', 'imageRef', 'text2audio', 'video2audio']) {
    assert.equal(defaultGenerateAudio(kind), false);
  }
});

test('the default never contradicts the option being offered', () => {
  for (const kind of Object.keys(PROCESS_KIND_MODE)) {
    if (!supportsAudioOutput(kind)) {
      assert.equal(
        defaultGenerateAudio(kind),
        false,
        `${kind} 不显示声音选项，默认值必须是 false，否则会静默改掉生成参数`
      );
    }
  }
});

// "音频链接"（参考音频的替代输入）与"声音"选项是两回事：前者只有本来就吃参考
// 音频的卡片才有意义。两者曾经共用同一个标志，导致文生视频卡片上多出一个填了
// 也没人读的"音频链接"输入框。
test('the audio-URL affordance is separate from the audio option', () => {
  const takesReferenceAudio = (kind) => PROCESS_KIND_MAX_AUDIOS[kind] > 0;
  for (const kind of Object.keys(PROCESS_KIND_MODE)) {
    if (supportsAudioOutput(kind) && !takesReferenceAudio(kind)) {
      assert.equal(
        takesReferenceAudio(kind),
        false,
        `${kind} 能选"声音"但不吃参考音频，不该显示"音频链接"`
      );
    }
  }
  // 图音生视频两者都有
  assert.equal(takesReferenceAudio('imageAudio2video'), true);
  assert.equal(supportsAudioOutput('imageAudio2video'), true);
  // 文生视频只有"声音"
  assert.equal(supportsAudioOutput('text2video'), true);
  assert.equal(takesReferenceAudio('text2video'), false);
});
