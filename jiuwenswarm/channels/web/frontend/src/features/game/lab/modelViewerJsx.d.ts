/**
 * `<model-viewer>`（@google/model-viewer，自定义元素）的 JSX 类型声明——
 * 这个包只注册浏览器自定义元素，不附带 React JSX 类型，直接用会被 TS
 * 当成未知标签报错，所以在这里补一份最小的属性声明（够本模块用的那几个
 * 属性，不是对官方全部属性的完整覆盖）。
 */
import type * as React from 'react';

declare global {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        alt?: string;
        poster?: string;
        exposure?: string | number;
        'shadow-intensity'?: string | number;
        'camera-controls'?: boolean;
        'auto-rotate'?: boolean;
        'disable-zoom'?: boolean;
        ar?: boolean;
      };
    }
  }
}

export {};
