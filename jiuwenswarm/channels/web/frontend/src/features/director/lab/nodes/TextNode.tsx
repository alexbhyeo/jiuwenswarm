import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLabActions } from '../LabActionsContext';
import type { TextNodeData } from '../labTypes';

const trashIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-.9 14a2 2 0 0 1-2 1.9H6.9a2 2 0 0 1-2-1.9L4 6" />
  </svg>
);

export function TextNode({ id, data }: NodeProps & { data: TextNodeData }) {
  const { t } = useTranslation();
  const actions = useLabActions();

  // 本地缓冲输入值，只有在没有正在进行的输入法组字（IME composition）时才
  // 把值同步给 React Flow 的节点状态。原来的写法是每敲一个键就直接把值写
  // 回受控的 textarea（数据经过整张画布的节点数组走一圈），拼音输入法组字
  // 中途 React 把 DOM 的 value 重新赋值回去，会打断正在进行中的组字——
  // 表现为中文打不出来或者输入错乱。缓冲之后，组字过程只更新本地 state，
  // 敲完这一个字（compositionend）才提交给节点数据，就不会再打断输入法了。
  const [localText, setLocalText] = useState(data.text);
  const composingRef = useRef(false);

  useEffect(() => {
    if (!composingRef.current) setLocalText(data.text);
  }, [data.text]);

  return (
    <div className="lab-node lab-node--text">
      <div className="lab-node-label">
        {t('director.lab.textPrompt')}
        <button type="button" className="lab-node-text-delete" title={t('director.delete.action')} onClick={() => actions.deleteNode(id)}>
          {trashIcon}
        </button>
      </div>
      <textarea
        className="lab-node-textarea nodrag"
        value={localText}
        placeholder={t('director.lab.samplePrompt')}
        onChange={(e) => {
          const value = e.target.value;
          setLocalText(value);
          if (!composingRef.current) actions.updateText(id, value);
        }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={(e) => {
          composingRef.current = false;
          actions.updateText(id, (e.target as HTMLTextAreaElement).value);
        }}
      />
      <Handle type="source" position={Position.Right} id="text" />
    </div>
  );
}
