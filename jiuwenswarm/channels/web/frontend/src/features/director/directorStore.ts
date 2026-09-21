// 导演模式（Director Mode）状态管理（Zustand）。
// 集中管理：项目列表 / 当前选中项目 / 素材计数 / 当前 tab / composer 草稿 / 生成中状态。

import { create } from 'zustand';
import type {
  ComposerMode,
  ComposerParams,
  DirectorAssetCounts,
  DirectorProject,
  DirectorTabKey,
} from './types';
import { DirectorApiError } from './types';

const DEFAULT_COMPOSER_PARAMS: ComposerParams = {
  aspectRatio: '16:9',
  resolution: '720p',
  durationSeconds: 15,
  generateAudio: false,
  characterStyle: '写实摄影',
};

interface PendingGeneration {
  projectId: string;
  assetId: string;
  jobId: string;
}

interface DirectorState {
  activeTab: DirectorTabKey;
  projects: DirectorProject[];
  projectsLoading: boolean;
  projectsError: string | null;
  selectedProjectId: string | null;
  assetCounts: DirectorAssetCounts;

  composerMode: ComposerMode;
  composerPrompt: string;
  composerParams: ComposerParams;

  generating: boolean;
  generateError: string | null;
  pendingGeneration: PendingGeneration | null;

  setActiveTab: (tab: DirectorTabKey) => void;
  loadProjects: () => Promise<void>;
  createProject: (name: string) => Promise<DirectorProject | null>;
  selectProject: (projectId: string | null) => void;

  setComposerMode: (mode: ComposerMode) => void;
  setComposerPrompt: (prompt: string) => void;
  patchComposerParams: (patch: Partial<ComposerParams>) => void;

  generate: () => Promise<void>;
  renameAsset: (projectId: string, assetId: string, name: string) => Promise<void>;
  deleteAsset: (projectId: string, assetId: string) => Promise<void>;
  uploadAsset: (projectId: string, file: File) => Promise<void>;
  uploading: boolean;
  uploadError: string | null;

  // 剪辑 tab 对话助手。draft/pendingImageIds 是输入框还没发送出去的草稿，
  // 消息历史本身随 DirectorProject.edit_chat_messages 一起存在 projects
  // 数组里，不需要另开一份。
  editChatDraft: string;
  editChatPendingImageIds: string[];
  editChatSending: boolean;
  editChatError: string | null;
  setEditChatDraft: (text: string) => void;
  addEditChatPendingImage: (assetId: string) => void;
  removeEditChatPendingImage: (assetId: string) => void;
  sendEditChatMessage: (projectId: string) => Promise<void>;

  // 实验室画布的"会话内权威副本"：LabCanvas 每次节点/连线变化都同步写
  // 一份到这里（不 debounce），切 tab 再切回来时优先从这里恢复。真正落盘
  // 到 director_state.json 走的是 debounce 后的 directorLabCanvasSave
  // RPC——那条路径是异步的，如果只依赖它，切 tab 时组件已经重新挂载、
  // 请求却还没落盘完成，会读到 project.lab_nodes 里那份过时的数据
  // （尤其是 剪辑 tab 根本不会重新拉取 projects 列表，创作 tab 拉取的
  // 时机也和这次保存请求完成的时机是两条独立的异步链路，谁先谁后没有
  // 保证）。这份内存副本是同步写入的，不存在这个竞态。
  labCanvasByProject: Record<string, { nodes: unknown[]; edges: unknown[] }>;
  setLabCanvas: (projectId: string, nodes: unknown[], edges: unknown[]) => void;
}

// 轮询中的 job 共享同一个定时器句柄，避免重复轮询同一个 asset。
let pollTimer: ReturnType<typeof setTimeout> | null = null;

function stopPolling(): void {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

export const useDirectorStore = create<DirectorState>((set, get) => ({
  activeTab: 'create',
  projects: [],
  projectsLoading: false,
  projectsError: null,
  selectedProjectId: null,
  assetCounts: { video: 0, image: 0, character: 0 },

  composerMode: 'video',
  composerPrompt: '',
  composerParams: DEFAULT_COMPOSER_PARAMS,

  generating: false,
  generateError: null,
  pendingGeneration: null,

  uploading: false,
  uploadError: null,

  editChatDraft: '',
  editChatPendingImageIds: [],
  editChatSending: false,
  editChatError: null,

  labCanvasByProject: {},
  setLabCanvas: (projectId, nodes, edges) =>
    set((s) => ({ labCanvasByProject: { ...s.labCanvasByProject, [projectId]: { nodes, edges } } })),

  setActiveTab: (tab) => set({ activeTab: tab }),

  loadProjects: async () => {
    set({ projectsLoading: true, projectsError: null });
    try {
      const { directorProjectsList } = await import('./directorApi');
      const { projects, assetCounts } = await directorProjectsList();
      set((state) => ({
        projects,
        assetCounts,
        projectsLoading: false,
        selectedProjectId: state.selectedProjectId ?? projects[0]?.project_id ?? null,
      }));

      // 恢复轮询：一个视频任务可能在上次会话/页面刷新前仍处于 pending
      // （generate_video 内部最多轮询 120s 才返回 job_id），此前只有刚发起
      // 生成的那次调用会排队轮询，导致刷新后卡在"生成中"永远不再更新。
      if (!get().pendingGeneration) {
        for (const project of projects) {
          const pendingAsset = project.assets.find((a) => a.status === 'pending' && a.job_id);
          if (pendingAsset?.job_id) {
            set({ pendingGeneration: { projectId: project.project_id, assetId: pendingAsset.asset_id, jobId: pendingAsset.job_id } });
            schedulePoll(get, set);
            break;
          }
        }
      }
    } catch (e) {
      set({
        projectsLoading: false,
        projectsError: e instanceof Error ? e.message : String(e),
      });
    }
  },

  createProject: async (name) => {
    try {
      const { directorProjectsCreate } = await import('./directorApi');
      const { project, assetCounts } = await directorProjectsCreate(name);
      set((state) => ({
        projects: [project, ...state.projects],
        assetCounts: assetCounts ?? state.assetCounts,
        selectedProjectId: project.project_id,
      }));
      return project;
    } catch (e) {
      set({ projectsError: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  selectProject: (projectId) => set({ selectedProjectId: projectId }),

  setComposerMode: (mode) => set({ composerMode: mode }),
  setComposerPrompt: (prompt) => set({ composerPrompt: prompt }),
  patchComposerParams: (patch) =>
    set((state) => ({ composerParams: { ...state.composerParams, ...patch } })),

  generate: async () => {
    const state = get();
    const projectId = state.selectedProjectId;
    const prompt = state.composerPrompt.trim();
    const mode = state.composerMode;

    if (!projectId || !prompt) return;
    if (mode !== 'video' && mode !== 'image' && mode !== 'character') return; // 音频/世界：即将推出，无 RPC

    stopPolling();
    set({ generating: true, generateError: null, pendingGeneration: null });

    try {
      const { directorGenerate } = await import('./directorApi');
      // 角色风格（写实摄影/动漫/3D…）拼进发给后端的描述末尾，而不是改写
      // composer 输入框里 "名称: 描述" 的原始文本——后端按第一个冒号拆
      // 名称/描述，追加的画风提示落在描述部分，不影响名称解析。
      const finalPrompt =
        mode === 'character' && state.composerParams.characterStyle
          ? `${prompt}，${state.composerParams.characterStyle}风格`
          : prompt;
      const { project, assetId, assetCounts } = await directorGenerate({
        projectId,
        mode,
        prompt: finalPrompt,
        aspectRatio: state.composerParams.aspectRatio,
        resolution: state.composerParams.resolution,
        durationSeconds: state.composerParams.durationSeconds,
        generateAudio: state.composerParams.generateAudio,
      });

      set((s) => ({
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
        assetCounts,
        composerPrompt: '',
      }));

      const asset = project.assets.find((a) => a.asset_id === assetId);
      if (asset?.status === 'pending' && asset.job_id) {
        set({ generating: false, pendingGeneration: { projectId, assetId, jobId: asset.job_id } });
        schedulePoll(get, set);
      } else {
        set({ generating: false });
      }
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ generating: false, generateError: message });
    }
  },

  renameAsset: async (projectId, assetId, name) => {
    try {
      const { directorAssetRename } = await import('./directorApi');
      const { project } = await directorAssetRename(projectId, assetId, name);
      set((s) => ({
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
      }));
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ projectsError: message });
    }
  },

  deleteAsset: async (projectId, assetId) => {
    try {
      const { directorAssetDelete } = await import('./directorApi');
      const { project, assetCounts } = await directorAssetDelete(projectId, assetId);
      set((s) => ({
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
        assetCounts,
      }));
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ projectsError: message });
    }
  },

  uploadAsset: async (projectId, file) => {
    set({ uploading: true, uploadError: null });
    try {
      const { directorAssetUpload } = await import('./directorApi');
      const { project, assetCounts } = await directorAssetUpload(projectId, file);
      set((s) => ({
        uploading: false,
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
        assetCounts,
      }));
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ uploading: false, uploadError: message });
    }
  },

  setEditChatDraft: (text) => set({ editChatDraft: text }),
  addEditChatPendingImage: (assetId) =>
    set((s) =>
      s.editChatPendingImageIds.includes(assetId)
        ? s
        : { editChatPendingImageIds: [...s.editChatPendingImageIds, assetId] }
    ),
  removeEditChatPendingImage: (assetId) =>
    set((s) => ({ editChatPendingImageIds: s.editChatPendingImageIds.filter((id) => id !== assetId) })),

  sendEditChatMessage: async (projectId) => {
    const state = get();
    const text = state.editChatDraft.trim();
    if (!projectId || !text || state.editChatSending) return;

    set({ editChatSending: true, editChatError: null });
    try {
      const { directorEditChatSend } = await import('./directorApi');
      const { project } = await directorEditChatSend(projectId, text, state.editChatPendingImageIds);
      set((s) => ({
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
        editChatSending: false,
        editChatDraft: '',
        editChatPendingImageIds: [],
      }));
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ editChatSending: false, editChatError: message });
    }
  },
}));

function schedulePoll(
  get: () => DirectorState,
  set: (partial: Partial<DirectorState> | ((state: DirectorState) => Partial<DirectorState>)) => void
): void {
  pollTimer = setTimeout(async () => {
    const pending = get().pendingGeneration;
    if (!pending) return;
    try {
      const { directorGenerateCheckStatus } = await import('./directorApi');
      const { project, status, assetCounts } = await directorGenerateCheckStatus(
        pending.projectId,
        pending.assetId,
        pending.jobId
      );
      set((s) => ({
        projects: s.projects.map((p) => (p.project_id === project.project_id ? project : p)),
        assetCounts,
      }));
      if (status === 'pending') {
        schedulePoll(get, set);
      } else {
        set({ pendingGeneration: null });
      }
    } catch (e) {
      const message = e instanceof DirectorApiError ? e.message : e instanceof Error ? e.message : String(e);
      set({ pendingGeneration: null, generateError: message });
    }
  }, 5000);
}
