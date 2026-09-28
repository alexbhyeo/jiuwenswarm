import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDirectorStore } from '../directorStore';
import { DIRECTOR_ASSET_DRAG_MIME } from '../types';
import type { DirectorAssetDragPayload } from '../types';
import { DirectorTabs } from './DirectorTabs';

const backIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="m15 18-6-6 6-6" />
  </svg>
);

const saveIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
    <path d="M17 21v-8H7v8M7 3v5h8" />
  </svg>
);

const importIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 8v8M8 12h8" />
  </svg>
);

const playIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="#fff">
    <path d="M8 5v14l11-7z" />
  </svg>
);

const pauseIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="#fff">
    <path d="M6 5h4v14H6zM14 5h4v14h-4z" />
  </svg>
);

const splitIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 3v18M18 3v18M6 12h12" />
  </svg>
);

const captureIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
    <circle cx="12" cy="13" r="4" />
  </svg>
);

const trashIcon = (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6" />
  </svg>
);

const addRowIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

/** 时间线上的一行——主行（'main'，唯一驱动预览/播放的那一行）或者某条附加行
 *  （extraTracks 数组下标）。片段可以在任意两行之间互相拖动，删除同理。 */
type RowRef = 'main' | number;

/** 挪动一个已有片段（同一行内前后排序 / 挪到别的行）走的是自定义的 Pointer
 *  Events 拖拽——不是原生 HTML5 drag-and-drop（draggable/dragstart/drop）。
 *  实测发现原生拖拽即便每一步 dragover 都正确 preventDefault，松开鼠标时也
 *  经常直接以 dragend 收场、drop 根本不触发（真实浏览器里同样复现，不只是
 *  自动化测试的限制）——原生 HTML5 DnD 对"程序化/连续拖动"这类手势本来就不够
 *  可靠。播放头拖拽已经在用 Pointer Events 并且很稳，这里的片段拖拽照搬同一
 *  套模式。素材面板（DirectorRail）拖新素材进时间线仍然是原生 DnD，两者互不
 *  干扰，分别处理。*/

/** 图片素材在时间线上默认占用的时长（秒）——图片本身没有内在时长，给一个
 *  固定值才能像视频片段一样参与拼接播放/分割。 */
const IMAGE_CLIP_DURATION = 3;
const MIN_CLIP_DURATION = 0.2;

interface EditClip {
  id: string;
  assetId: string;
  type: 'image' | 'video';
  filePath: string;
  name: string;
  /** 视频片段在源文件里的裁剪窗口（秒）；图片片段固定 trimIn=0。 */
  trimIn: number;
  trimOut: number;
}

function clipDuration(clip: EditClip): number {
  return clip.type === 'image' ? IMAGE_CLIP_DURATION : Math.max(MIN_CLIP_DURATION, clip.trimOut - clip.trimIn);
}

function rawFileUrl(path: string): string {
  return `/file-api/raw-file?path=${encodeURIComponent(path)}`;
}

function formatTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${String(m).padStart(2, '0')}:${r.toFixed(2).padStart(5, '0')}`;
}

/** 播放头落在整条时间线的哪个片段、片段内部偏移多少秒。 */
function locate(clips: EditClip[], time: number): { index: number; localTime: number } {
  let elapsed = 0;
  for (let i = 0; i < clips.length; i += 1) {
    const d = clipDuration(clips[i]);
    if (time < elapsed + d || i === clips.length - 1) {
      return { index: i, localTime: Math.max(0, time - elapsed) };
    }
    elapsed += d;
  }
  return { index: -1, localTime: 0 };
}

function totalDuration(clips: EditClip[]): number {
  return clips.reduce((sum, c) => sum + clipDuration(c), 0);
}

export function EditTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const uploadAsset = useDirectorStore((s) => s.uploadAsset);

  const [clips, setClips] = useState<EditClip[]>([]);
  // 附加行：纯组织用途，不参与预览/播放——播放头、时长、播放/分割/截帧全部只
  // 认主行（clips）。挪一段过去就是把它从时间线上"请出去"暂存，互相之间也能
  // 再挪动/挪回主行。
  const [extraTracks, setExtraTracks] = useState<EditClip[][]>([]);
  const [playheadTime, setPlayheadTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  // clipId === null 表示落点是"这一行末尾/空行"(没有具体命中某个片段),仅
  // 用于渲染插入指示线/高亮;真正的落点在拖拽结束那一刻从 pendingDropRef 里
  // 同步读取,不依赖这份仅用于展示的 state。
  const [dropIndicator, setDropIndicator] = useState<{ row: RowRef; clipId: string | null; side: 'before' | 'after' } | null>(null);
  const [draggingClipId, setDraggingClipId] = useState<string | null>(null);
  const pendingDropRef = useRef<{ row: RowRef; clipId: string | null; side: 'before' | 'after' } | null>(null);
  // 拖拽结束(pointerup)时置位,让紧随其后的原生 click 事件(main 行片段点击
  // 会触发跳转播放头)被吞掉一次——不然一次拖拽松手后还会顺带触发一次"点击"。
  const suppressClipClickRef = useRef(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);
  const playheadRef = useRef(playheadTime);
  playheadRef.current = playheadTime;
  const clipsRef = useRef(clips);
  clipsRef.current = clips;
  const extraTracksRef = useRef(extraTracks);
  extraTracksRef.current = extraTracks;

  const duration = useMemo(() => totalDuration(clips), [clips]);
  const { index: activeIndex, localTime } = useMemo(() => locate(clips, playheadTime), [clips, playheadTime]);
  const activeClip = activeIndex >= 0 ? clips[activeIndex] : null;

  const showNotice = useCallback((kind: 'ok' | 'error', text: string) => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice((cur) => (cur?.text === text ? null : cur)), 3000);
  }, []);

  // 按行取当前那一行的片段数组——统一走 ref（clipsRef/extraTracksRef 每次渲染都
  // 会刷新成最新值），而不是分别现取 state，方便下面几个函数共用一套查找逻辑。
  const clipsInRow = useCallback((row: RowRef): EditClip[] => (row === 'main' ? clipsRef.current : extraTracksRef.current[row] ?? []), []);

  // 对指定行的片段数组做一次变换（增/删/挪位置都走这一个口子）。摘掉主行的
  // 片段会让总时长变短，顺带把播放头夹回新的总时长以内，避免它悬空指向一个
  // 已经不存在的位置；附加行不参与播放，不需要这一步。
  const applyRow = useCallback((row: RowRef, updater: (arr: EditClip[]) => EditClip[]) => {
    if (row === 'main') {
      setClips((prev) => {
        const next = updater(prev);
        if (next.length < prev.length) setPlayheadTime((t) => Math.min(t, totalDuration(next)));
        return next;
      });
    } else {
      setExtraTracks((prev) => prev.map((track, i) => (i === row ? updater(track) : track)));
    }
  }, []);

  // 往指定行（主行或某条附加行）插入一个新片段/挪入一个已有片段的公共写法；
  // 不传 atIndex 就插到末尾。
  const addClipToRow = useCallback(
    (row: RowRef, clip: EditClip, atIndex?: number) => {
      applyRow(row, (arr) => {
        const next = [...arr];
        const at = atIndex === undefined ? next.length : Math.max(0, Math.min(atIndex, next.length));
        next.splice(at, 0, clip);
        return next;
      });
    },
    [applyRow],
  );

  const removeClipFromRow = useCallback(
    (row: RowRef, clipId: string) => applyRow(row, (arr) => arr.filter((c) => c.id !== clipId)),
    [applyRow],
  );

  const deleteClip = useCallback((row: RowRef, clipId: string) => removeClipFromRow(row, clipId), [removeClipFromRow]);

  // 同一行内前后挪动：把片段从原位置摘出来，插回目标位置——目标位置是"摘除前"
  // 数组里的下标，所以摘除后如果目标在原位置之后，要往前借一位。
  const reorderClipInRow = useCallback(
    (row: RowRef, clipId: string, targetIndex: number) => {
      applyRow(row, (arr) => {
        const idx = arr.findIndex((c) => c.id === clipId);
        if (idx === -1) return arr;
        const next = [...arr];
        const [item] = next.splice(idx, 1);
        const insertAt = Math.max(0, Math.min(idx < targetIndex ? targetIndex - 1 : targetIndex, next.length));
        next.splice(insertAt, 0, item);
        return next;
      });
    },
    [applyRow],
  );

  // React 的 setState 更新函数是排队执行的，不是调用 setClips(...) 那一刻就同步
  // 跑完——不能指望从它的回调里用一个闭包变量"带出"删掉的那个片段给调用方接着
  // 用（那个闭包变量在 setClips 真正跑之前就已经 return 出去了，拿到的永远是
  // 初值 null）。要拿到片段本体，只能在调用任何 setState 之前，先从 ref 里读
  // 当前真正的数组——这两步（摘除、追加）各自独立触发一次 setState 即可，互相
  // 不需要等对方跑完。同一行内挪动（fromRow === toRow）则走 reorderClipInRow，
  // 不传 toIndex 时视为"没有具体落点"，不做任何事（比如拖回同一行的空白处）。
  const moveClipToRow = useCallback(
    (clipId: string, fromRow: RowRef, toRow: RowRef, toIndex?: number) => {
      if (fromRow === toRow) {
        if (toIndex !== undefined) reorderClipInRow(toRow, clipId, toIndex);
        return;
      }
      const clip = clipsInRow(fromRow).find((c) => c.id === clipId);
      if (!clip) return;
      removeClipFromRow(fromRow, clipId);
      addClipToRow(toRow, clip, toIndex);
    },
    [addClipToRow, clipsInRow, removeClipFromRow, reorderClipInRow],
  );

  const addExtraRow = useCallback(() => setExtraTracks((prev) => [...prev, []]), []);

  const appendClip = useCallback(
    (payload: DirectorAssetDragPayload, row: RowRef = 'main') => {
      if (payload.type === 'video') {
        // 时长要从真实视频文件探测——拖进来这一刻还不知道它有多长。
        const probe = document.createElement('video');
        probe.preload = 'metadata';
        probe.src = rawFileUrl(payload.filePath);
        probe.onloadedmetadata = () => {
          const dur = Number.isFinite(probe.duration) && probe.duration > 0 ? probe.duration : 5;
          addClipToRow(row, {
            id: `clip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            assetId: payload.assetId,
            type: 'video',
            filePath: payload.filePath,
            name: payload.name,
            trimIn: 0,
            trimOut: dur,
          });
        };
      } else {
        addClipToRow(row, {
          id: `clip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          assetId: payload.assetId,
          type: 'image',
          filePath: payload.filePath,
          name: payload.name,
          trimIn: 0,
          trimOut: IMAGE_CLIP_DURATION,
        });
      }
    },
    [addClipToRow],
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  }, []);
  const onDragLeave = useCallback(() => {
    setDragOver(false);
    setDropIndicator(null);
  }, []);
  // 每一行自己的 drop 目标：只处理"从 素材 面板拖一个新素材进来"（原生 HTML5
  // DnD）——时间线内部挪动已有片段走的是下面的 Pointer Events 方案，不再经过
  // 这里。
  const handleRowDrop = useCallback(
    (row: RowRef) => (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const assetRaw = e.dataTransfer.getData(DIRECTOR_ASSET_DRAG_MIME);
      if (!assetRaw) return;
      try {
        const payload = JSON.parse(assetRaw) as DirectorAssetDragPayload;
        if (payload.type !== 'video' && payload.type !== 'image') return; // "角色" 素材本质是图片，但这里只接受显式的图片/视频，避免混淆
        appendClip(payload, row);
      } catch {
        /* not a director asset drag payload */
      }
    },
    [appendClip],
  );
  const onDrop = useMemo(() => handleRowDrop('main'), [handleRowDrop]);

  // 拖拽经过的落点：命中某个片段就说明要插到它的左/右半边（同一行内重排，
  // 或者带着精确位置挪到别的行）；命中的是行/轨道容器本身（没有落在任何片段
  // 上，比如空行，或者片段列表末尾的空白）就说明是"追加到这一行末尾"。用
  // document.elementFromPoint 而不是 React 事件的 currentTarget——拖拽过程中
  // 鼠标经过的元素在 React 合成事件体系之外持续变化，Pointer Events 只在
  // “按下的那个元素”上收，中途移动到哪由这里主动查询当前坐标命中了什么。
  const resolveDropTarget = useCallback(
    (clientX: number, clientY: number, draggedClipId: string): { row: RowRef; clipId: string | null; side: 'before' | 'after' } | null => {
      const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      if (!el) return null;
      const clipEl = el.closest('[data-clip-id]') as HTMLElement | null;
      if (clipEl && clipEl.getAttribute('data-clip-id') !== draggedClipId) {
        const rowAttr = clipEl.getAttribute('data-clip-row') ?? 'main';
        const row: RowRef = rowAttr === 'main' ? 'main' : Number(rowAttr);
        const rect = clipEl.getBoundingClientRect();
        const side: 'before' | 'after' = clientX < rect.left + rect.width / 2 ? 'before' : 'after';
        return { row, clipId: clipEl.getAttribute('data-clip-id'), side };
      }
      const trackEl = el.closest('[data-track-row]') as HTMLElement | null;
      if (trackEl) {
        const rowAttr = trackEl.getAttribute('data-track-row') ?? 'main';
        const row: RowRef = rowAttr === 'main' ? 'main' : Number(rowAttr);
        return { row, clipId: null, side: 'after' };
      }
      return null;
    },
    [],
  );

  // 挪动一个已有片段：按下即开始跟踪指针，移动超过一点点距离才算"在拖"（不
  // 然主行片段原有的"点击跳转播放头"就没法用了），松手那一刻从
  // pendingDropRef 同步读最后一次算出的落点并真正提交——落点信息不经由
  // dropIndicator 这个 state 传递，因为它只用于渲染插入线，读取时机可能落后
  // 于最后一次指针移动。
  const handleClipPointerDown = useCallback(
    (row: RowRef, clipId: string) => (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      const startX = e.clientX;
      const startY = e.clientY;
      let dragging = false;
      const onMove = (ev: PointerEvent) => {
        if (!dragging) {
          if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < 4) return;
          dragging = true;
          setDraggingClipId(clipId);
        }
        const target = resolveDropTarget(ev.clientX, ev.clientY, clipId);
        pendingDropRef.current = target;
        setDropIndicator(target);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        if (dragging) {
          suppressClipClickRef.current = true;
          const target = pendingDropRef.current;
          if (target) {
            if (target.clipId) {
              const idx = clipsInRow(target.row).findIndex((c) => c.id === target.clipId);
              const toIndex = idx === -1 ? undefined : idx + (target.side === 'before' ? 0 : 1);
              moveClipToRow(clipId, row, target.row, toIndex);
            } else {
              moveClipToRow(clipId, row, target.row);
            }
          }
        }
        pendingDropRef.current = null;
        setDraggingClipId(null);
        setDropIndicator(null);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    },
    [clipsInRow, moveClipToRow, resolveDropTarget],
  );

  // 播放：视频片段靠它自己的 <video> timeupdate 推进播放头；图片片段没有
  // 媒体元素可以驱动，靠 rAF 按真实经过时间累加。片段边界（无论哪种）都在
  // 这个循环里检测并跳到下一段，播到最后一段自动停止。
  useEffect(() => {
    if (!playing) {
      lastTickRef.current = null;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      return;
    }
    const tick = (now: number) => {
      const cs = clipsRef.current;
      const total = totalDuration(cs);
      const { index } = locate(cs, playheadRef.current);
      const current = index >= 0 ? cs[index] : null;
      const isVideoDriven = current?.type === 'video' && videoRef.current && !videoRef.current.paused;
      if (!isVideoDriven) {
        const last = lastTickRef.current ?? now;
        const deltaSec = (now - last) / 1000;
        const next = playheadRef.current + deltaSec;
        if (next >= total) {
          setPlayheadTime(total);
          setPlaying(false);
          lastTickRef.current = null;
          return;
        }
        setPlayheadTime(next);
      }
      lastTickRef.current = now;
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [playing]);

  // 切到视频片段时把 <video> 定位到片段内应处的位置并按当前播放状态启停；
  // 该视频自己的 timeupdate 再把（片段内本地时间 + 之前片段累计时长）写回
  // 播放头，让时间线随视频真实播放进度前进，而不是靠 rAF 空转估算。
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !activeClip || activeClip.type !== 'video') return;
    const target = activeClip.trimIn + localTime;
    if (Math.abs(el.currentTime - target) > 0.35) el.currentTime = target;
    if (playing) void el.play().catch(() => undefined);
    else el.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeClip?.id, playing]);

  // 暂停状态下拖动播放头（或点击时间线/刻度尺跳转）时，把预览画面实时同步到
  // 拖到的那一帧——上面那个 effect 只在"切换片段"或"播放/暂停状态改变"时才
  // 跑，播放头在同一段视频内部移动并不会触发它，画面就会停在原处不跟着走。
  // 播放中不跑这一段：那时候画面已经交给视频自己的 timeupdate 在推进（见下面
  // handleVideoTimeUpdate），这里再抢着 seek 只会来回打架、造成卡顿。
  useEffect(() => {
    const el = videoRef.current;
    if (!el || playing || !activeClip || activeClip.type !== 'video') return;
    const target = activeClip.trimIn + localTime;
    if (Math.abs(el.currentTime - target) > 0.02) el.currentTime = target;
  }, [playing, activeClip?.id, localTime]);

  const handleVideoTimeUpdate = useCallback(() => {
    const el = videoRef.current;
    const cs = clipsRef.current;
    const { index } = locate(cs, playheadRef.current);
    const clip = index >= 0 ? cs[index] : null;
    if (!el || !clip || clip.type !== 'video' || !playing) return;
    let elapsedBefore = 0;
    for (let i = 0; i < index; i += 1) elapsedBefore += clipDuration(cs[i]);
    if (el.currentTime >= clip.trimOut - 0.02) {
      if (index >= cs.length - 1) {
        setPlaying(false);
        setPlayheadTime(totalDuration(cs));
      } else {
        setPlayheadTime(elapsedBefore + clipDuration(clip));
      }
      return;
    }
    setPlayheadTime(elapsedBefore + (el.currentTime - clip.trimIn));
  }, [playing]);

  const togglePlay = useCallback(() => {
    if (clips.length === 0) return;
    setPlaying((p) => {
      const next = !p;
      if (next && playheadRef.current >= totalDuration(clipsRef.current) - 0.01) setPlayheadTime(0);
      return next;
    });
  }, [clips.length]);

  const seekTo = useCallback(
    (time: number) => {
      setPlayheadTime(Math.min(Math.max(0, time), Math.max(0, duration)));
    },
    [duration],
  );

  // 刻度尺、轨道、播放头共用这一个容器的宽度做时间<->像素换算：三者必须严格
  // 对齐（播放头那条竖线要能同时穿过刻度尺和下面的片段轨道），换算基准就不能
  // 分别读刻度尺和轨道各自的宽度——哪怕两者理论上该一样宽，也经不起将来任何一
  // 边加了 padding/边框就悄悄错位。
  const scrubAreaRef = useRef<HTMLDivElement>(null);
  const timeFromClientX = useCallback(
    (clientX: number) => {
      const el = scrubAreaRef.current;
      if (!el || duration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(0, ratio), 1) * duration;
    },
    [duration],
  );
  const seekFromPointerEvent = useCallback(
    (e: React.MouseEvent) => {
      if (duration <= 0) return;
      seekTo(timeFromClientX(e.clientX));
    },
    [duration, seekTo, timeFromClientX],
  );
  const handlePlayheadPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (duration <= 0) return;
      e.preventDefault();
      e.stopPropagation();
      setPlaying(false); // 拖动播放头这个动作本身就是"我要去看某一帧"，先暂停免得画面一直在跑
      e.currentTarget.setPointerCapture(e.pointerId);
      seekTo(timeFromClientX(e.clientX));
      const onMove = (ev: PointerEvent) => seekTo(timeFromClientX(ev.clientX));
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    },
    [duration, seekTo, timeFromClientX],
  );
  // 刻度尺上均匀撒 ~10 个时间点；没有素材（duration=0）时退回一段固定范围的
  // 占位刻度，不然时间线还没拖进任何东西之前，刻度尺看起来像是坏掉了。
  const rulerMarks = useMemo(() => {
    const span = duration > 0 ? duration : 2.2;
    const step = span / 11;
    return Array.from({ length: 12 }, (_, i) => i * step);
  }, [duration]);

  const handleSplit = useCallback(() => {
    if (!activeClip || activeIndex < 0) return;
    const d = clipDuration(activeClip);
    // 播放头落在片段边界（几乎是起点/终点）就没有意义可分——两段里会有一段
    // 时长几乎为 0。
    if (localTime <= MIN_CLIP_DURATION || d - localTime <= MIN_CLIP_DURATION) {
      showNotice('error', t('director.edit.splitTooCloseToEdge'));
      return;
    }
    const first: EditClip =
      activeClip.type === 'video'
        ? { ...activeClip, id: `${activeClip.id}_a`, trimOut: activeClip.trimIn + localTime }
        : { ...activeClip, id: `${activeClip.id}_a`, trimOut: localTime };
    const second: EditClip =
      activeClip.type === 'video'
        ? { ...activeClip, id: `${activeClip.id}_b`, trimIn: activeClip.trimIn + localTime }
        : { ...activeClip, id: `${activeClip.id}_b`, trimIn: 0, trimOut: d - localTime };
    setClips((prev) => {
      const next = [...prev];
      next.splice(activeIndex, 1, first, second);
      return next;
    });
  }, [activeClip, activeIndex, localTime, showNotice, t]);

  const handleCapture = useCallback(async () => {
    const el = videoRef.current;
    if (!activeClip || activeClip.type !== 'video' || !el || !selectedProjectId) return;
    if (!el.videoWidth || !el.videoHeight) {
      showNotice('error', t('director.edit.captureFailed'));
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = el.videoWidth;
    canvas.height = el.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      showNotice('error', t('director.edit.captureFailed'));
      return;
    }
    ctx.drawImage(el, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(async (blob) => {
      if (!blob) {
        showNotice('error', t('director.edit.captureFailed'));
        return;
      }
      try {
        const file = new File([blob], `keyframe_${Date.now()}.png`, { type: 'image/png' });
        await uploadAsset(selectedProjectId, file, 'image');
        showNotice('ok', t('director.edit.captured'));
      } catch {
        showNotice('error', t('director.edit.captureFailed'));
      }
    }, 'image/png');
  }, [activeClip, selectedProjectId, showNotice, t, uploadAsset]);

  const importInputRef = useRef<HTMLInputElement>(null);

  const playheadPct = duration > 0 ? (playheadTime / duration) * 100 : 0;

  return (
    <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div className="director-lab-topbar">
        <div className="director-lab-breadcrumb">
          <button type="button" className="director-lab-back" onClick={() => setActiveTab('create')}>
            {backIcon}
          </button>
          <span className="director-lab-title">{t('director.edit.breadcrumb')}</span>
        </div>
        <DirectorTabs activeTab={activeTab} onChange={setActiveTab} />
      </div>

      <div className="director-edit-subheader">
        <span className="director-edit-subheader-title">{t('director.edit.timelineEditor')}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button type="button" className="director-edit-btn director-edit-btn--primary" disabled title={t('director.comingSoon')}>
            {saveIcon}
            {t('director.edit.saveToProject')}
          </button>
          <button type="button" className="director-edit-btn director-edit-btn--secondary" onClick={() => setActiveTab('create')}>
            {t('director.edit.exit')}
          </button>
        </div>
      </div>

      {notice ? (
        <div className={`director-edit-notice director-edit-notice--${notice.kind}`} data-testid="director-edit-notice">
          {notice.text}
        </div>
      ) : null}

      <div className="director-edit-body">
        <div
          className="director-edit-stage"
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          data-testid="director-edit-stage"
        >
          {activeClip ? (
            <div className="director-edit-preview" data-testid="director-edit-preview">
              {activeClip.type === 'video' ? (
                <video
                  ref={videoRef}
                  className="director-edit-preview-media"
                  src={rawFileUrl(activeClip.filePath)}
                  onTimeUpdate={handleVideoTimeUpdate}
                  playsInline
                  muted
                  data-testid="director-edit-preview-video"
                />
              ) : (
                <img className="director-edit-preview-media" src={rawFileUrl(activeClip.filePath)} alt={activeClip.name} data-testid="director-edit-preview-image" />
              )}
            </div>
          ) : (
            <div className={`director-edit-dropzone${dragOver ? ' director-edit-dropzone--over' : ''}`}>
              <div className="director-edit-dropzone-title">
                {importIcon}
                {t('director.edit.importMedia')}
              </div>
              <div className="director-edit-dropzone-hint">{t('director.edit.importHint')}</div>
            </div>
          )}
        </div>

        <div className="director-edit-timeline">
          <div className="director-edit-toolbar-row">
            <div className="director-edit-toolbar-left">
              <input
                ref={importInputRef}
                type="file"
                accept="image/*,video/*"
                multiple
                style={{ display: 'none' }}
                onChange={(e) => {
                  const files = e.target.files;
                  if (!files || !selectedProjectId) return;
                  Array.from(files).forEach((file) => {
                    void uploadAsset(selectedProjectId, file).catch(() => showNotice('error', t('director.edit.captureFailed')));
                  });
                  e.target.value = '';
                }}
                data-testid="director-edit-import-input"
              />
              <button
                type="button"
                className="director-edit-toolbar-btn"
                title={t('director.edit.importMedia')}
                onClick={() => importInputRef.current?.click()}
                data-testid="director-edit-add-btn"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </button>
              <button
                type="button"
                className="director-edit-toolbar-btn"
                title={t('director.edit.split')}
                disabled={!activeClip}
                onClick={handleSplit}
                data-testid="director-edit-split-btn"
              >
                {splitIcon}
              </button>
              <button
                type="button"
                className="director-edit-toolbar-btn"
                title={t('director.edit.captureKeyframe')}
                disabled={!activeClip || activeClip.type !== 'video'}
                onClick={() => void handleCapture()}
                data-testid="director-edit-capture-btn"
              >
                {captureIcon}
              </button>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button
                type="button"
                className="director-edit-play-btn"
                onClick={togglePlay}
                disabled={clips.length === 0}
                data-testid="director-edit-play-btn"
                data-state={playing ? 'playing' : 'paused'}
                title={playing ? t('director.edit.pause') : t('director.edit.play')}
              >
                {playing ? pauseIcon : playIcon}
              </button>
              <span className="director-edit-clock" data-testid="director-edit-clock">
                {formatTime(playheadTime)} / {formatTime(duration)}
              </span>
            </div>
            <div style={{ width: 90 }} />
          </div>
          <div className="director-edit-scrub-area" ref={scrubAreaRef}>
            <div className="director-edit-ruler" onClick={seekFromPointerEvent} data-testid="director-edit-ruler">
              {rulerMarks.map((mark) => (
                <span key={mark}>{formatTime(mark)}</span>
              ))}
            </div>
            <div
              className={`director-edit-track${dropIndicator?.row === 'main' && dropIndicator.clipId === null ? ' director-edit-track--drop-target' : ''}`}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              data-track-row="main"
              data-testid="director-edit-track"
            >
              {clips.length === 0 ? (
                <span className="director-edit-track-hint">{t('director.edit.trackHint')}</span>
              ) : (
                clips.map((clip, i) => (
                  <div
                    key={clip.id}
                    className={[
                      'director-edit-clip',
                      i === activeIndex ? 'director-edit-clip--active' : '',
                      draggingClipId === clip.id ? 'director-edit-clip--dragging' : '',
                      dropIndicator?.clipId === clip.id ? `director-edit-clip--drop-${dropIndicator.side}` : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    style={{ flexGrow: clipDuration(clip), touchAction: 'none' }}
                    onPointerDown={handleClipPointerDown('main', clip.id)}
                    onClick={() => {
                      if (suppressClipClickRef.current) {
                        suppressClipClickRef.current = false;
                        return;
                      }
                      let before = 0;
                      for (let j = 0; j < i; j += 1) before += clipDuration(clips[j]);
                      seekTo(before + 0.01);
                    }}
                    data-testid="director-edit-clip"
                    data-clip-type={clip.type}
                    data-clip-id={clip.id}
                    data-clip-row="main"
                    title={clip.name}
                  >
                    {clip.type === 'image' ? (
                      <img className="director-edit-clip-thumb" src={rawFileUrl(clip.filePath)} alt="" draggable={false} />
                    ) : (
                      <video className="director-edit-clip-thumb" src={rawFileUrl(clip.filePath)} muted preload="metadata" draggable={false} />
                    )}
                    <span className="director-edit-clip-name">{clip.name}</span>
                    <button
                      type="button"
                      className="director-edit-clip-delete"
                      title={t('director.edit.deleteClip')}
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteClip('main', clip.id);
                      }}
                      data-testid="director-edit-clip-delete"
                    >
                      {trashIcon}
                    </button>
                  </div>
                ))
              )}
            </div>
            <div
              className="director-edit-playhead"
              style={{ left: `${playheadPct}%` }}
              onPointerDown={handlePlayheadPointerDown}
              data-testid="director-edit-playhead"
            >
              <div className="director-edit-playhead-handle" />
            </div>
          </div>

          {extraTracks.map((track, rowIndex) => (
            <div key={rowIndex} className="director-edit-extra-row" data-testid="director-edit-extra-row">
              <div className="director-edit-extra-row-header">
                <span className="director-edit-extra-row-label">{t('director.edit.rowLabel', { index: rowIndex + 2 })}</span>
                <button
                  type="button"
                  className="director-edit-toolbar-btn"
                  title={t('director.edit.removeRow')}
                  onClick={() => setExtraTracks((prev) => prev.filter((_, i) => i !== rowIndex))}
                  data-testid="director-edit-remove-row-btn"
                >
                  {trashIcon}
                </button>
              </div>
              <div
                className={`director-edit-track director-edit-track--extra${dropIndicator?.row === rowIndex && dropIndicator.clipId === null ? ' director-edit-track--drop-target' : ''}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={handleRowDrop(rowIndex)}
                data-track-row={rowIndex}
                data-testid="director-edit-extra-track"
              >
                {track.length === 0 ? (
                  <span className="director-edit-track-hint">{t('director.edit.extraRowHint')}</span>
                ) : (
                  track.map((clip) => (
                    <div
                      key={clip.id}
                      className={[
                        'director-edit-clip',
                        draggingClipId === clip.id ? 'director-edit-clip--dragging' : '',
                        dropIndicator?.clipId === clip.id ? `director-edit-clip--drop-${dropIndicator.side}` : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      style={{ flexGrow: clipDuration(clip), touchAction: 'none' }}
                      onPointerDown={handleClipPointerDown(rowIndex, clip.id)}
                      data-testid="director-edit-extra-clip"
                      data-clip-type={clip.type}
                      data-clip-id={clip.id}
                      data-clip-row={rowIndex}
                      title={clip.name}
                    >
                      {clip.type === 'image' ? (
                        <img className="director-edit-clip-thumb" src={rawFileUrl(clip.filePath)} alt="" draggable={false} />
                      ) : (
                        <video className="director-edit-clip-thumb" src={rawFileUrl(clip.filePath)} muted preload="metadata" draggable={false} />
                      )}
                      <span className="director-edit-clip-name">{clip.name}</span>
                      <button
                        type="button"
                        className="director-edit-clip-delete"
                        title={t('director.edit.deleteClip')}
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteClip(rowIndex, clip.id);
                        }}
                        data-testid="director-edit-clip-delete"
                      >
                        {trashIcon}
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}

          <button type="button" className="director-edit-add-row-btn" onClick={addExtraRow} data-testid="director-edit-add-row-btn">
            {addRowIcon}
            {t('director.edit.addRow')}
          </button>
        </div>
      </div>
    </div>
  );
}
