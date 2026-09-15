// "从模板开始" 静态模板画廊内容（无对应 RPC，选中后仅回填 composer 草稿）。

import type { ComposerMode } from './types';

export interface DirectorTemplate {
  id: string;
  titleKey: string;
  descKey: string;
  mode: ComposerMode;
  prompt: string;
}

export const DIRECTOR_TEMPLATES: DirectorTemplate[] = [
  {
    id: 'storyboard',
    titleKey: 'director.templates.storyboard.title',
    descKey: 'director.templates.storyboard.desc',
    mode: 'video',
    prompt: '',
  },
  {
    id: 'productAd',
    titleKey: 'director.templates.productAd.title',
    descKey: 'director.templates.productAd.desc',
    mode: 'video',
    prompt: '',
  },
  {
    id: 'portrait',
    titleKey: 'director.templates.portrait.title',
    descKey: 'director.templates.portrait.desc',
    mode: 'video',
    prompt: '',
  },
  {
    id: 'multiRef',
    titleKey: 'director.templates.multiRef.title',
    descKey: 'director.templates.multiRef.desc',
    mode: 'image',
    prompt: '',
  },
  {
    id: 'poster',
    titleKey: 'director.templates.poster.title',
    descKey: 'director.templates.poster.desc',
    mode: 'image',
    prompt: '',
  },
];
