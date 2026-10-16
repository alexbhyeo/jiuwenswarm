// 语音合成（text-to-speech）的"风格"选项——成片整体的语气/节奏/情绪。
//
// 与"音色"（audioVoices.ts 的 voice）是两个正交的维度：音色决定"谁在说"，
// 风格决定"怎么说"。同一个 Kore 可以温柔亲切，也可以低声急促。
//
// 单独放一个模块的理由与 audioVoices.ts 相同：创意页和实验室的文生音频卡片
// 各写一份，迟早会分叉成"创意页能选的风格、实验室卡片选不到"。
//
// 值必须是**英文的自然语言描述**（"warm and friendly" 这种），空串表示不指定。
// 服务商把它作为 style 发给模型——OpenRouter 的 /audio/speech 用顶层
// instructions 参数承载，再映射成 Gemini 的 speech_metadata.style，官方文档
// 的示例就是这个写法（见 https://ai.google.dev/gemini-api/docs/speech-generation）。
// 所以这里存的就是要原样发给模型的字符串，不在中间再套一层 id 映射：后端拿到
// 什么就发什么，自定义风格将来也能直接落到同一个字段上。
export interface AudioStyleOption {
  /** 语言无关的稳定标识，只用来查 i18n 文案（不发往任何接口）。 */
  id: string;
  /** 真正发给模型的风格描述；空串表示不指定风格。 */
  instruction: string;
}

export const AUDIO_STYLE_OPTIONS: AudioStyleOption[] = [
  { id: 'default', instruction: '' },
  { id: 'warm', instruction: 'warm and friendly' },
  { id: 'enthusiastic', instruction: 'enthusiastic and energetic' },
  { id: 'calm', instruction: 'calm and measured' },
  { id: 'cheerful', instruction: 'bright and cheerful' },
  { id: 'serious', instruction: 'serious and authoritative' },
  { id: 'soothing', instruction: 'gentle and soothing, slow paced' },
  { id: 'urgentWhisper', instruction: 'whispered urgently, barely audible' },
  { id: 'excited', instruction: 'excited and thrilled, fast paced' },
  { id: 'sad', instruction: 'sad and subdued, low energy' },
  { id: 'narration', instruction: 'documentary narration, neutral and clear' },
  { id: 'advertising', instruction: 'upbeat advertising voice-over, persuasive' },
];

/** 默认（不指定风格）——与后端"没有 style 就不发 instructions"的默认一致。 */
export const DEFAULT_AUDIO_STYLE = '';

/**
 * 按当前值取 i18n 文案 key；空值取"默认"那一档。
 *
 * 卡片/创意页直接显示这个文案（而不是英文原文），但发出去的始终是
 * instruction 本身——文案只是给人看的。
 */
export function audioStyleLabelKey(style: string): string {
  const hit = AUDIO_STYLE_OPTIONS.find((o) => o.instruction === style);
  return `director.audioStyle.${hit ? hit.id : 'custom'}`;
}
