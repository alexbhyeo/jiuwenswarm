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
  assetCounts: { video: 0, image: 0 },

  composerMode: 'video',
  composerPrompt: '',
  composerParams: DEFAULT_COMPOSER_PARAMS,

  generating: false,
  generateError: null,
  pendingGeneration: null,

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
    if (mode !== 'video' && mode !== 'image') return; // 音频/角色/世界：即将推出，无 RPC

    stopPolling();
    set({ generating: true, generateError: null, pendingGeneration: null });

    try {
      const { directorGenerate } = await import('./directorApi');
      const { project, assetId, assetCounts } = await directorGenerate({
        projectId,
        mode,
        prompt,
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
