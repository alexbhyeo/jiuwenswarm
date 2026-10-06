// 语音合成（text-to-speech）可选音色。
//
// 单独放一个模块，而不是在 ComposerCard / ProcessNode / directorStore 里各写
// 一份：音色列表一旦分叉，就会出现"创意页能选的音色、实验室卡片选不到"这种
// 两边不一致的问题。Gemini TTS 系列公开的音色名，直接作为请求里的 voice
// 字段值传给服务商；不支持的音色由服务商返回明确错误。

export const AUDIO_VOICE_OPTIONS = [
  'Zephyr',
  'Puck',
  'Charon',
  'Kore',
  'Fenrir',
  'Leda',
  'Orus',
  'Aoede',
  'Callirrhoe',
  'Autonoe',
  'Enceladus',
  'Iapetus',
  'Umbriel',
  'Algieba',
  'Despina',
  'Erinome',
  'Algenib',
  'Rasalgethi',
  'Laomedeia',
  'Achernar',
  'Alnilam',
  'Schedar',
  'Gacrux',
  'Pulcherrima',
  'Achird',
  'Zubenelgenubi',
  'Vindemiatrix',
] as const;

export const DEFAULT_AUDIO_VOICE = 'Kore';
