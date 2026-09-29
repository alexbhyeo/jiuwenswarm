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

const zoomOutIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" />
    <path d="m21 21-4.3-4.3M8 11h6" />
  </svg>
);

const zoomInIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" />
    <path d="m21 21-4.3-4.3M11 8v6M8 11h6" />
  </svg>
);

/** 图片素材在时间线上默认占用的时长（秒）——图片本身没有内在时长，给一个
 *  固定值才能像视频片段一样参与拼接播放/分割。 */
const IMAGE_CLIP_DURATION = 3;
const MIN_CLIP_DURATION = 0.2;
/** 整条时间线还没有任何素材时，刻度尺/拖放定位用的占位时长——不然连"往
 *  第几秒拖"这个换算都没有基准。 */
const EMPTY_TIMELINE_SPAN = 10;
/** 时间线可拖放的范围要比"当前内容的总时长"多留一截——不然任何轨道一旦有
 *  了内容，可见范围就正好卡死在最后一个片段的结尾，没有任何空白像素可以
 *  拖放到"更晚的时间"，用户永远没法把新素材拖到已有内容之后去（拖动已有
 *  片段想往后挪同理会卡住）。这一截跟当前总时长成比例（内容越长，留白也
 *  跟着变宽），同时给一个固定下限，内容很短时也有够用的余量。 */
const MIN_TIMELINE_HEADROOM = 5;
const TIMELINE_HEADROOM_RATIO = 0.15;
/** 拖动片段贴近同一轨道上另一个片段的边缘时自动吸附的命中距离（像素）——
 *  按屏幕像素定义,不管当前时间刻度缩放到多大,手感都一样。 */
const SNAP_PIXELS = 10;

/** 缩放为 1 倍时，每秒对应的像素数——这是唯一的"基准密度"，缩放只是在它
 *  上面乘一个倍数。整条时间线的实际渲染宽度 = timeScale * BASE_PX_PER_SECOND
 *  * zoom；这个宽度小于可视区域时靠 CSS min-width:100% 撑满（跟没有缩放功能
 *  之前的观感一致），一旦放大到超出可视区域，靠横向滚动查看——刻度尺/轨道/
 *  播放头三者的时间-像素换算永远读的是这个渲染出来的真实宽度（不是某个
 *  缓存值），所以不管缩放到多大、滚动到哪，换算永远是对的。 */
const BASE_PX_PER_SECOND = 60;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 6;
const ZOOM_STEP = 0.5;

interface EditClip {
  id: string;
  assetId: string;
  type: 'image' | 'video';
  filePath: string;
  name: string;
  /** 视频片段在源文件里的裁剪窗口（秒）；图片片段固定 trimIn=0。 */
  trimIn: number;
  trimOut: number;
  /** 片段在所属轨道上的绝对起始时间（秒）——片段可以摆在轨道上任意时间点，
   *  彼此之间可以留空隙，不再是"挨个首尾相连"。 */
  start: number;
}

function clipDuration(clip: EditClip): number {
  return clip.type === 'image' ? IMAGE_CLIP_DURATION : Math.max(MIN_CLIP_DURATION, clip.trimOut - clip.trimIn);
}

function clipEnd(clip: EditClip): number {
  return clip.start + clipDuration(clip);
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

/** 整条时间线的总时长——所有轨道里最晚结束的那个片段决定，不只是主轨道。 */
function totalDurationAcrossTracks(tracks: EditClip[][]): number {
  let max = 0;
  for (const track of tracks) {
    for (const clip of track) max = Math.max(max, clipEnd(clip));
  }
  return max;
}

/** 播放头当前这一刻，从最上面的轨道开始找第一条"有片段覆盖这一刻"的轨道——
 *  最上面的轨道优先级最高，只有它在这一刻是空隙时才会往下一条轨道找，都没有
 *  就是真正的空隙（返回 null，预览区显示黑屏）。 */
function locateActive(tracks: EditClip[][], time: number): { trackIndex: number; clip: EditClip; localTime: number } | null {
  for (let t = 0; t < tracks.length; t += 1) {
    const hit = tracks[t].find((c) => time >= c.start && time < clipEnd(c));
    if (hit) return { trackIndex: t, clip: hit, localTime: time - hit.start };
  }
  return null;
}

/** 目标时间窗 [start, end) 是否跟这条轨道上（自己除外）的其它片段重叠。 */
function overlaps(track: EditClip[], start: number, end: number, excludeId?: string): boolean {
  return track.some((c) => c.id !== excludeId && start < clipEnd(c) && c.start < end);
}

/** 拖动一个片段（新素材或已有片段）快靠近同一轨道上另一个片段的边缘时，
 *  自动吸附对齐——起点贴到别的片段的终点（松手正好接在它后面），或者终点
 *  贴到别的片段的起点（松手正好接在它前面），不用再靠鼠标像素级精确对齐。
 *  在阈值范围内取距离最近的那个吸附点；范围外原样返回，不吸附。 */
function snapToNeighbors(track: EditClip[], rawStart: number, dur: number, thresholdSeconds: number, excludeId?: string): number {
  if (thresholdSeconds <= 0) return rawStart;
  const rawEnd = rawStart + dur;
  let best = rawStart;
  let bestDist = thresholdSeconds;
  for (const c of track) {
    if (c.id === excludeId) continue;
    const cEnd = clipEnd(c);
    const distStartToEnd = Math.abs(rawStart - cEnd);
    if (distStartToEnd <= bestDist) {
      bestDist = distStartToEnd;
      best = cEnd;
    }
    const distEndToStart = Math.abs(rawEnd - c.start);
    if (distEndToStart <= bestDist) {
      bestDist = distEndToStart;
      best = c.start - dur;
    }
  }
  return Math.max(0, best);
}

interface DropPreview {
  trackIndex: number;
  start: number;
  end: number;
  valid: boolean;
}

export function EditTabShell() {
  const { t } = useTranslation();
  const activeTab = useDirectorStore((s) => s.activeTab);
  const setActiveTab = useDirectorStore((s) => s.setActiveTab);
  const selectedProjectId = useDirectorStore((s) => s.selectedProjectId);
  const uploadAsset = useDirectorStore((s) => s.uploadAsset);

  // tracks[0] 是主轨——唯一没有"轨道 N"标签的那条，但不再是唯一参与播放的
  // 轨道：所有轨道都会参与播放，播放优先级按轨道从上到下（见 locateActive）。
  // tracks[1:] 是用户自己加的附加轨道。
  const [tracks, setTracks] = useState<EditClip[][]>([[]]);
  const [playheadTime, setPlayheadTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [draggingClipId, setDraggingClipId] = useState<string | null>(null);
  // 拖拽落点预览——新素材从 素材 面板拖进来、或者挪动一个已有片段，两种情况
  // 共用同一份状态：目标轨道 + 起止时间 + 跟该轨道其它片段是否重叠（重叠就
  // 不能放）。真正提交时从 pendingDropRef 同步读最后一次算出的值，不依赖这
  // 份仅用于渲染预览的 state（可能落后于最后一次指针移动）。
  const [dropPreview, setDropPreview] = useState<DropPreview | null>(null);
  const pendingDropRef = useRef<DropPreview | null>(null);
  // 拖拽结束（pointerup）时置位，让紧随其后的原生 click 事件（点击片段跳转
  // 播放头）被吞掉一次——不然一次拖拽松手后还会顺带触发一次"点击"。
  const suppressClipClickRef = useRef(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);
  const playheadRef = useRef(playheadTime);
  playheadRef.current = playheadTime;
  const tracksRef = useRef(tracks);
  tracksRef.current = tracks;

  const duration = useMemo(() => totalDurationAcrossTracks(tracks), [tracks]);
  const timeScale =
    duration > 0 ? duration + Math.max(MIN_TIMELINE_HEADROOM, duration * TIMELINE_HEADROOM_RATIO) : EMPTY_TIMELINE_SPAN;
  // 缩放只改变"这段时长用多少像素画出来"，不改变 timeScale 本身（可拖放的
  // 时间范围跟缩放无关）；内容比可视区域窄时靠 CSS min-width:100% 撑满，见
  // .director-edit-tracks-content。
  const pxPerSecond = BASE_PX_PER_SECOND * zoom;
  const contentWidthPx = timeScale * pxPerSecond;
  const active = useMemo(() => locateActive(tracks, playheadTime), [tracks, playheadTime]);
  const activeClip = active?.clip ?? null;
  const activeTrackIndex = active?.trackIndex ?? -1;
  const localTime = active?.localTime ?? 0;
  const hasAnyClip = useMemo(() => tracks.some((track) => track.length > 0), [tracks]);

  const showNotice = useCallback((kind: 'ok' | 'error', text: string) => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice((cur) => (cur?.text === text ? null : cur)), 3000);
  }, []);

  // 对指定轨道的片段数组做一次变换（增/删/挪位置都走这一个口子），变换后
  // 顺带把播放头夹回新的总时长以内，避免它悬空指向一个已经不存在的位置。
  const applyTrack = useCallback((trackIndex: number, updater: (arr: EditClip[]) => EditClip[]) => {
    setTracks((prev) => {
      const next = prev.map((track, i) => (i === trackIndex ? updater(track) : track));
      const total = totalDurationAcrossTracks(next);
      setPlayheadTime((time) => Math.min(time, total));
      return next;
    });
  }, []);

  const addClipToTrack = useCallback(
    (trackIndex: number, clip: EditClip) => {
      applyTrack(trackIndex, (arr) => [...arr, clip].sort((a, b) => a.start - b.start));
    },
    [applyTrack],
  );

  const removeClipFromTrack = useCallback(
    (trackIndex: number, clipId: string) => applyTrack(trackIndex, (arr) => arr.filter((c) => c.id !== clipId)),
    [applyTrack],
  );

  const deleteClip = useCallback((trackIndex: number, clipId: string) => removeClipFromTrack(trackIndex, clipId), [removeClipFromTrack]);

  // 把一个已有片段挪到目标轨道的目标起始时间；跟目标轨道其它片段（自己除外）
  // 重叠就拒绝、什么都不改，调用方负责在拒绝时提示用户。
  const moveClip = useCallback((clipId: string, fromTrack: number, toTrack: number, rawStart: number): boolean => {
    const clip = tracksRef.current[fromTrack]?.find((c) => c.id === clipId);
    if (!clip) return false;
    const start = Math.max(0, rawStart);
    const end = start + clipDuration(clip);
    if (overlaps(tracksRef.current[toTrack] ?? [], start, end, fromTrack === toTrack ? clipId : undefined)) return false;
    setTracks((prev) => {
      const moved = { ...clip, start };
      const next = prev.map((track, i) => {
        if (i === fromTrack && i === toTrack) return track.filter((c) => c.id !== clipId).concat(moved).sort((a, b) => a.start - b.start);
        if (i === fromTrack) return track.filter((c) => c.id !== clipId);
        if (i === toTrack) return [...track, moved].sort((a, b) => a.start - b.start);
        return track;
      });
      const total = totalDurationAcrossTracks(next);
      setPlayheadTime((time) => Math.min(time, total));
      return next;
    });
    return true;
  }, []);

  const addTrack = useCallback(() => setTracks((prev) => [...prev, []]), []);
  const removeTrack = useCallback((trackIndex: number) => {
    if (trackIndex === 0) return; // 主轨不能删
    setTracks((prev) => prev.filter((_, i) => i !== trackIndex));
  }, []);

  // 把一个新素材放进指定轨道的指定起始时间；跟该轨道现有片段重叠就拒绝并提示
  // （视频要等探测完真实时长才能确定终点，所以视频的重叠检查发生在探测完成
  // 之后，图片时长是固定值可以立即检查）。
  const appendClipAt = useCallback(
    (payload: DirectorAssetDragPayload, trackIndex: number, rawStart: number) => {
      const start = Math.max(0, rawStart);
      const commit = (clip: EditClip) => {
        if (overlaps(tracksRef.current[trackIndex] ?? [], clip.start, clip.start + clipDuration(clip))) {
          showNotice('error', t('director.edit.clipOverlap'));
          return;
        }
        addClipToTrack(trackIndex, clip);
      };
      if (payload.type === 'video') {
        const probe = document.createElement('video');
        probe.preload = 'metadata';
        probe.src = rawFileUrl(payload.filePath);
        probe.onloadedmetadata = () => {
          const dur = Number.isFinite(probe.duration) && probe.duration > 0 ? probe.duration : 5;
          commit({
            id: `clip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            assetId: payload.assetId,
            type: 'video',
            filePath: payload.filePath,
            name: payload.name,
            trimIn: 0,
            trimOut: dur,
            start,
          });
        };
      } else {
        commit({
          id: `clip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          assetId: payload.assetId,
          type: 'image',
          filePath: payload.filePath,
          name: payload.name,
          trimIn: 0,
          trimOut: IMAGE_CLIP_DURATION,
          start,
        });
      }
    },
    [addClipToTrack, showNotice, t],
  );

  // 刻度尺、轨道、播放头共用这一个容器的宽度做时间<->像素换算：三者必须严格
  // 对齐（播放头那条竖线要能同时穿过刻度尺和下面的片段轨道），换算基准就不能
  // 分别读刻度尺和轨道各自的宽度——哪怕两者理论上该一样宽，也经不起将来任何一
  // 边加了 padding/边框就悄悄错位。这一层也是所有轨道片段 left/width 百分比
  // 共用的同一套时间刻度（各轨道横向留白已经对齐，见 CSS）。
  const scrubAreaRef = useRef<HTMLDivElement>(null);
  // 刻度尺按"实际渲染出来有多少像素"决定疏密——缩放到内容比可视区域窄时，
  // CSS min-width:100% 会把它撑得比 timeScale*pxPerSecond 算出来的还宽，
  // 只按缩放倍数算刻度会比视觉上实际能放下的更稀疏；量出这层容器的真实宽度
  // 才准。初次渲染前用缩放算出的理论宽度兜底，观察到真实宽度后再纠正。
  const [renderedWidth, setRenderedWidth] = useState(0);
  useEffect(() => {
    const el = scrubAreaRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setRenderedWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const timeFromClientX = useCallback(
    (clientX: number) => {
      const el = scrubAreaRef.current;
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(0, ratio), 1) * timeScale;
    },
    [timeScale],
  );

  // 磁吸阈值换算成秒——固定按像素定义（不管当前时间刻度缩放到多大，屏幕上
  // 差不多这么近就该吸附），所以要用当前的像素/秒换算成对应的秒数。
  const snapThresholdSeconds = useCallback(() => {
    const el = scrubAreaRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return (SNAP_PIXELS / rect.width) * timeScale;
  }, [timeScale]);

  const trackIndexFromPoint = useCallback((clientX: number, clientY: number): number | null => {
    const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
    const trackEl = el?.closest('[data-track-row]') as HTMLElement | null;
    if (!trackEl) return null;
    const idx = Number(trackEl.getAttribute('data-track-row'));
    return Number.isFinite(idx) ? idx : null;
  }, []);

  const onDragOverGeneric = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  }, []);
  const onDragLeave = useCallback(() => {
    setDragOver(false);
    setDropPreview(null);
  }, []);

  // 素材面板拖新素材悬停在某条轨道上时，实时算出落点预览（用图片默认时长
  // 近似——视频的真实时长要等真正放下后探测才知道，悬停阶段只能给个近似宽度）。
  const onTrackDragOver = useCallback(
    (trackIndex: number) => (e: React.DragEvent) => {
      onDragOverGeneric(e);
      const raw = timeFromClientX(e.clientX);
      const start = snapToNeighbors(tracksRef.current[trackIndex] ?? [], raw, IMAGE_CLIP_DURATION, snapThresholdSeconds());
      const end = start + IMAGE_CLIP_DURATION;
      const valid = !overlaps(tracksRef.current[trackIndex] ?? [], start, end);
      setDropPreview({ trackIndex, start, end, valid });
    },
    [onDragOverGeneric, snapThresholdSeconds, timeFromClientX],
  );

  const handleTrackDrop = useCallback(
    (trackIndex: number) => (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      setDropPreview(null);
      const assetRaw = e.dataTransfer.getData(DIRECTOR_ASSET_DRAG_MIME);
      if (!assetRaw) return;
      try {
        const payload = JSON.parse(assetRaw) as DirectorAssetDragPayload;
        if (payload.type !== 'video' && payload.type !== 'image') return; // "角色" 素材本质是图片，但这里只接受显式的图片/视频，避免混淆
        const raw = timeFromClientX(e.clientX);
        const start = snapToNeighbors(tracksRef.current[trackIndex] ?? [], raw, IMAGE_CLIP_DURATION, snapThresholdSeconds());
        appendClipAt(payload, trackIndex, start);
      } catch {
        /* not a director asset drag payload */
      }
    },
    [appendClipAt, snapThresholdSeconds, timeFromClientX],
  );

  // 拖到预览舞台（时间线上方那块大预览区）而不是具体某条轨道上时，没有一个
  // 自然的"时间点"可用——落到主轨末尾，跟以前"直接追加"的简单交互保持一致。
  const stageDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const assetRaw = e.dataTransfer.getData(DIRECTOR_ASSET_DRAG_MIME);
      if (!assetRaw) return;
      try {
        const payload = JSON.parse(assetRaw) as DirectorAssetDragPayload;
        if (payload.type !== 'video' && payload.type !== 'image') return;
        const main = tracksRef.current[0] ?? [];
        const start = main.length ? Math.max(...main.map(clipEnd)) : 0;
        appendClipAt(payload, 0, start);
      } catch {
        /* not a director asset drag payload */
      }
    },
    [appendClipAt],
  );

  // 挪动一个已有片段：按下即开始跟踪指针，移动超过一点点距离才算"在拖"（不然
  // 原有的"点击跳转播放头"就没法用了）。拖动过程中片段起点跟着光标走，但保持
  // 抓取时光标相对片段起点的偏移（不是让光标对齐到片段最左边），手感更自然；
  // 目标轨道由光标当前悬停的轨道决定。松手那一刻从 pendingDropRef 同步读最后
  // 一次算出的落点并真正提交——不依赖 dropPreview 这个 state，它只用于渲染，
  // 读取时机可能落后于最后一次指针移动。
  const handleClipPointerDown = useCallback(
    (trackIndex: number, clipId: string) => (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      const clip = tracksRef.current[trackIndex]?.find((c) => c.id === clipId);
      if (!clip) return;
      const startClientX = e.clientX;
      const startClientY = e.clientY;
      const grabOffsetTime = timeFromClientX(startClientX) - clip.start;
      let dragging = false;
      const onMove = (ev: PointerEvent) => {
        if (!dragging) {
          if (Math.hypot(ev.clientX - startClientX, ev.clientY - startClientY) < 4) return;
          dragging = true;
          setDraggingClipId(clipId);
        }
        const targetTrack = trackIndexFromPoint(ev.clientX, ev.clientY) ?? trackIndex;
        const raw = Math.max(0, timeFromClientX(ev.clientX) - grabOffsetTime);
        const start = snapToNeighbors(
          tracksRef.current[targetTrack] ?? [],
          raw,
          clipDuration(clip),
          snapThresholdSeconds(),
          targetTrack === trackIndex ? clipId : undefined,
        );
        const end = start + clipDuration(clip);
        const valid = !overlaps(tracksRef.current[targetTrack] ?? [], start, end, targetTrack === trackIndex ? clipId : undefined);
        const preview: DropPreview = { trackIndex: targetTrack, start, end, valid };
        pendingDropRef.current = preview;
        setDropPreview(preview);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        if (dragging) {
          suppressClipClickRef.current = true;
          const preview = pendingDropRef.current;
          if (preview) {
            if (preview.valid) moveClip(clipId, trackIndex, preview.trackIndex, preview.start);
            else showNotice('error', t('director.edit.clipOverlap'));
          }
        }
        pendingDropRef.current = null;
        setDraggingClipId(null);
        setDropPreview(null);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    },
    [moveClip, showNotice, snapThresholdSeconds, t, timeFromClientX, trackIndexFromPoint],
  );

  // 播放：视频片段靠它自己的 <video> timeupdate 推进播放头；图片片段/轨道
  // 间的空隙没有媒体元素可以驱动，靠 rAF 按真实经过时间累加。谁是"当前活跃
  // 片段"由 locateActive 按轨道优先级现算，不再依赖固定的顺序数组。
  useEffect(() => {
    if (!playing) {
      lastTickRef.current = null;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      return;
    }
    const tick = (now: number) => {
      const ts = tracksRef.current;
      const total = totalDurationAcrossTracks(ts);
      const current = locateActive(ts, playheadRef.current);
      const isVideoDriven = current?.clip.type === 'video' && videoRef.current && !videoRef.current.paused;
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
  // 该视频自己的 timeupdate 再把（片段起始时间 + 片段内本地时间）写回播放
  // 头，让时间线随视频真实播放进度前进，而不是靠 rAF 空转估算。
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
    const current = locateActive(tracksRef.current, playheadRef.current);
    if (!el || !current || current.clip.type !== 'video' || !playing) return;
    const { clip } = current;
    if (el.currentTime >= clip.trimOut - 0.02) {
      const total = totalDurationAcrossTracks(tracksRef.current);
      const end = clipEnd(clip);
      if (end >= total - 0.01) {
        setPlaying(false);
        setPlayheadTime(total);
      } else {
        setPlayheadTime(end);
      }
      return;
    }
    setPlayheadTime(clip.start + (el.currentTime - clip.trimIn));
  }, [playing]);

  const togglePlay = useCallback(() => {
    if (!hasAnyClip) return;
    setPlaying((p) => {
      const next = !p;
      if (next && playheadRef.current >= totalDurationAcrossTracks(tracksRef.current) - 0.01) setPlayheadTime(0);
      return next;
    });
  }, [hasAnyClip]);

  const seekTo = useCallback(
    (time: number) => {
      setPlayheadTime(Math.min(Math.max(0, time), Math.max(0, duration)));
    },
    [duration],
  );

  const zoomIn = useCallback(() => setZoom((z) => Math.min(MAX_ZOOM, Number((z + ZOOM_STEP).toFixed(2)))), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(MIN_ZOOM, Number((z - ZOOM_STEP).toFixed(2)))), []);

  // Ctrl/Cmd + "+"/"-" 缩放时间线——跟工具栏里放大镜按钮走同一个口子，
  // preventDefault 是为了不要连带触发浏览器自己的整页面缩放。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        zoomIn();
      } else if (e.key === '-' || e.key === '_') {
        e.preventDefault();
        zoomOut();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [zoomIn, zoomOut]);

  const seekFromPointerEvent = useCallback(
    (e: React.MouseEvent) => {
      seekTo(timeFromClientX(e.clientX));
    },
    [seekTo, timeFromClientX],
  );
  const handlePlayheadPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
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
    [seekTo, timeFromClientX],
  );
  // 刻度尺按"大约每 90px 一个刻度"来定密度——放大后每秒占的像素变多，同样
  // 90px range 里能塞下的刻度数变多，看起来就是刻度变细了（更精细的时间
  // 粒度）；缩小同理变粗，不会在缩得很小时挤成一团。用实际渲染宽度（见
  // renderedWidth），内容被 CSS 撑满可视区域时也能正确算出足够的刻度数。
  const rulerMarks = useMemo(() => {
    const targetPxPerTick = 90;
    const effectiveWidth = renderedWidth > 0 ? renderedWidth : timeScale * pxPerSecond;
    const tickCount = Math.max(2, Math.round(effectiveWidth / targetPxPerTick));
    const step = timeScale / tickCount;
    return Array.from({ length: tickCount + 1 }, (_, i) => i * step);
  }, [pxPerSecond, renderedWidth, timeScale]);

  const handleSplit = useCallback(() => {
    if (!activeClip || activeTrackIndex < 0) return;
    const d = clipDuration(activeClip);
    // 播放头落在片段边界（几乎是起点/终点）就没有意义可分——两段里会有一段
    // 时长几乎为 0。
    if (localTime <= MIN_CLIP_DURATION || d - localTime <= MIN_CLIP_DURATION) {
      showNotice('error', t('director.edit.splitTooCloseToEdge'));
      return;
    }
    const splitAt = activeClip.start + localTime;
    const first: EditClip =
      activeClip.type === 'video'
        ? { ...activeClip, id: `${activeClip.id}_a`, trimOut: activeClip.trimIn + localTime }
        : { ...activeClip, id: `${activeClip.id}_a`, trimOut: localTime };
    const second: EditClip =
      activeClip.type === 'video'
        ? { ...activeClip, id: `${activeClip.id}_b`, trimIn: activeClip.trimIn + localTime, start: splitAt }
        : { ...activeClip, id: `${activeClip.id}_b`, trimIn: 0, trimOut: d - localTime, start: splitAt };
    applyTrack(activeTrackIndex, (arr) => {
      const next = arr.filter((c) => c.id !== activeClip.id);
      next.push(first, second);
      return next.sort((a, b) => a.start - b.start);
    });
  }, [activeClip, activeTrackIndex, applyTrack, localTime, showNotice, t]);

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

  const playheadPct = (playheadTime / timeScale) * 100;

  const renderClip = (trackIndex: number, clip: EditClip) => {
    const leftPct = (clip.start / timeScale) * 100;
    const widthPct = (clipDuration(clip) / timeScale) * 100;
    return (
      <div
        key={clip.id}
        className={[
          'director-edit-clip',
          activeClip?.id === clip.id ? 'director-edit-clip--active' : '',
          draggingClipId === clip.id ? 'director-edit-clip--dragging' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        style={{ left: `${leftPct}%`, width: `${widthPct}%`, touchAction: 'none' }}
        onPointerDown={handleClipPointerDown(trackIndex, clip.id)}
        onClick={() => {
          if (suppressClipClickRef.current) {
            suppressClipClickRef.current = false;
            return;
          }
          seekTo(clip.start + 0.01);
        }}
        data-testid={trackIndex === 0 ? 'director-edit-clip' : 'director-edit-extra-clip'}
        data-clip-type={clip.type}
        data-clip-id={clip.id}
        data-clip-row={trackIndex}
        data-clip-start={clip.start.toFixed(2)}
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
            deleteClip(trackIndex, clip.id);
          }}
          data-testid="director-edit-clip-delete"
        >
          {trashIcon}
        </button>
      </div>
    );
  };

  const renderTrack = (trackIndex: number) => {
    const track = tracks[trackIndex] ?? [];
    const isMain = trackIndex === 0;
    const preview = dropPreview && dropPreview.trackIndex === trackIndex ? dropPreview : null;
    return (
      <div
        className={`director-edit-track${isMain ? '' : ' director-edit-track--extra'}`}
        onDragOver={onTrackDragOver(trackIndex)}
        onDragLeave={onDragLeave}
        onDrop={handleTrackDrop(trackIndex)}
        data-track-row={trackIndex}
        data-testid={isMain ? 'director-edit-track' : 'director-edit-extra-track'}
      >
        {track.length === 0 ? (
          <span className="director-edit-track-hint">{t(isMain ? 'director.edit.trackHint' : 'director.edit.extraRowHint')}</span>
        ) : null}
        {track.map((clip) => renderClip(trackIndex, clip))}
        {preview ? (
          <div
            className={`director-edit-drop-preview${preview.valid ? '' : ' director-edit-drop-preview--invalid'}`}
            style={{
              left: `${(preview.start / timeScale) * 100}%`,
              width: `${((preview.end - preview.start) / timeScale) * 100}%`,
            }}
            data-testid="director-edit-drop-preview"
            data-valid={preview.valid}
          />
        ) : null}
      </div>
    );
  };

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
          onDragOver={onDragOverGeneric}
          onDragLeave={onDragLeave}
          onDrop={stageDrop}
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
          ) : hasAnyClip ? (
            <div className="director-edit-preview director-edit-preview--empty" data-testid="director-edit-preview-empty" />
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
                disabled={!hasAnyClip}
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
            <div className="director-edit-zoom" data-testid="director-edit-zoom">
              <button
                type="button"
                className="director-edit-zoom-btn"
                title={t('director.edit.zoomOut')}
                onClick={zoomOut}
                disabled={zoom <= MIN_ZOOM}
                data-testid="director-edit-zoom-out-btn"
              >
                {zoomOutIcon}
              </button>
              <input
                type="range"
                className="director-edit-zoom-slider"
                min={MIN_ZOOM}
                max={MAX_ZOOM}
                step={ZOOM_STEP}
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
                title={t('director.edit.zoomLevel', { level: zoom.toFixed(1) })}
                data-testid="director-edit-zoom-slider"
              />
              <button
                type="button"
                className="director-edit-zoom-btn"
                title={t('director.edit.zoomIn')}
                onClick={zoomIn}
                disabled={zoom >= MAX_ZOOM}
                data-testid="director-edit-zoom-in-btn"
              >
                {zoomInIcon}
              </button>
            </div>
          </div>

          {/* 横向滚动只包这一块——刻度尺/轨道/播放头按当前缩放渲染成一个可能
              比可视区域更宽的内容块；放大到超出可视宽度时靠这里滚动查看，
              工具栏和下面的"添加轨道"按钮留在外面，不跟着滚动。 */}
          <div className="director-edit-tracks-scroll" data-testid="director-edit-tracks-scroll">
            <div className="director-edit-tracks-content" style={{ width: `${contentWidthPx}px` }}>
              <div className="director-edit-scrub-area" ref={scrubAreaRef}>
                <div className="director-edit-ruler" onClick={seekFromPointerEvent} data-testid="director-edit-ruler">
                  {rulerMarks.map((mark) => (
                    <span key={mark} style={{ left: `${(mark / timeScale) * 100}%` }}>
                      {formatTime(mark)}
                    </span>
                  ))}
                </div>
                {renderTrack(0)}
                <div
                  className="director-edit-playhead"
                  style={{ left: `${playheadPct}%` }}
                  onPointerDown={handlePlayheadPointerDown}
                  data-testid="director-edit-playhead"
                >
                  <div className="director-edit-playhead-handle" />
                </div>
              </div>

              {tracks.slice(1).map((_, i) => {
                const trackIndex = i + 1;
                return (
                  <div key={trackIndex} className="director-edit-extra-row" data-testid="director-edit-extra-row">
                    <div className="director-edit-extra-row-header">
                      <span className="director-edit-extra-row-label">{t('director.edit.rowLabel', { index: trackIndex + 1 })}</span>
                      <button
                        type="button"
                        className="director-edit-toolbar-btn"
                        title={t('director.edit.removeRow')}
                        onClick={() => removeTrack(trackIndex)}
                        data-testid="director-edit-remove-row-btn"
                      >
                        {trashIcon}
                      </button>
                    </div>
                    {renderTrack(trackIndex)}
                  </div>
                );
              })}
            </div>
          </div>

          <button type="button" className="director-edit-add-row-btn" onClick={addTrack} data-testid="director-edit-add-row-btn">
            {addRowIcon}
            {t('director.edit.addRow')}
          </button>
        </div>
      </div>
    </div>
  );
}
