/**
 * 游戏模式（游戏工坊）Web API 客户端。
 *
 * 组件只调用这里的 typed API；错误沿用导演模式的约定：网关把后端
 * `GameRpcError.code` 提升到 WS 消息顶层，`webClient` 再挂到 `WebError.code`。
 */

import { webRequest } from '../../services/webClient';

// 生成/分解都真的调用外部模型接口，耗时远超 webClient 15s 的默认超时——
// 与导演模式 directorApi.ts 的 GENERATE_TIMEOUT_MS 同一个取舍（实测角色图
// 生成就要 16-20s，视频更久；分解要先上传图片再调 Tripo3D，同样不能用默认值）。
const GAME_GENERATE_TIMEOUT_MS = 300_000;
const GAME_DECOMPOSE_SUBMIT_TIMEOUT_MS = 60_000;
// check_status 在 Tripo3D 任务 success 的那一次轮询里，会同步下载合并模型
// 并用 trimesh 拆分（game_item_decompose.py GameItemDecompose.poll），不是
// 一次纯状态查询；后端自己给下载开了 120s 的 httpx 超时，这里要盖过它，
// 否则真的跑到下载那一步时，前端会比后端先超时、把本可轮询成功的任务判
// 成失败（实测踩过一次：30s 超时在下载阶段命中，卡片显示"请求超时"）。
const GAME_DECOMPOSE_STATUS_TIMEOUT_MS = 150_000;

export type GameAsset = {
  asset_id: string;
  type: string;
  name: string;
  file_path: string | null;
  status: string;
  params: Record<string, unknown>;
  created_at: number;
  source_asset_id: string | null;
  decompose_batch_id: string | null;
};

export type GameProject = {
  project_id: string;
  name: string;
  engine: string;
  status: string;
  root_dir: string;
  created_at: number;
  updated_at: number;
  assets: GameAsset[];
  build: Record<string, unknown>;
  params: Record<string, unknown>;
  lab_nodes: Record<string, unknown>[];
  lab_edges: Record<string, unknown>[];
};

export type GameDecomposeSubmitResult = {
  project: GameProject;
  placeholder_asset_id: string;
  decompose_batch_id: string;
  job_id: string;
};

export type GameDecomposeStatusResult = {
  project: GameProject;
  status: 'pending' | 'success' | 'failed';
  error?: string;
  assets?: GameAsset[];
};

export type GameToModelSubmitResult = {
  project: GameProject;
  placeholder_asset_id: string;
  job_id: string;
};

export type GameToModelStatusResult = {
  project: GameProject;
  status: 'pending' | 'success' | 'failed';
  error?: string;
  asset?: GameAsset;
};

export type GameBridgeStatus = {
  godot: boolean;
  godot_binary: string;
  godot_version: string;
  godot_mono: boolean;
  dotnet: boolean;
  dotnet_version: string;
  mcp: boolean;
  ready: boolean;
  tools: string[];
};

export type GameFileEntry = { path: string; size: number; editable: boolean };

const METHOD = {
  projectsList: 'game.projects.list',
  projectsCreate: 'game.projects.create',
  projectsGet: 'game.projects.get',
  projectsRename: 'game.projects.rename',
  projectsDelete: 'game.projects.delete',
  projectsScaffold: 'game.projects.scaffold',
  projectsBuild2d: 'game.projects.build_2d',
  assetsGenerate: 'game.assets.generate',
  assetsList: 'game.assets.list',
  assetsImport: 'game.assets.import',
  assetsDelete: 'game.assets.delete',
  assetsDecompose: 'game.assets.decompose',
  assetsDecomposeCheckStatus: 'game.assets.decompose_check_status',
  assetsToModel: 'game.assets.to_model',
  assetsToModelCheckStatus: 'game.assets.to_model_check_status',
  labCanvasSave: 'game.lab_canvas.save',
  filesTree: 'game.files.tree',
  filesRead: 'game.files.read',
  filesWrite: 'game.files.write',
  bridgeStatus: 'game.bridge.status',
} as const;

export const gameApi = {
  listProjects: async (): Promise<GameProject[]> => {
    const resp = await webRequest<{ projects?: GameProject[] }>(METHOD.projectsList);
    return resp?.projects ?? [];
  },
  createProject: async (name: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.projectsCreate, { name });
    return resp.project;
  },
  getProject: async (projectId: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.projectsGet, {
      project_id: projectId,
    });
    return resp.project;
  },
  renameProject: async (projectId: string, name: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.projectsRename, {
      project_id: projectId,
      name,
    });
    return resp.project;
  },
  deleteProject: (projectId: string) =>
    webRequest<{ deleted: string }>(METHOD.projectsDelete, { project_id: projectId }),
  scaffoldProject: async (projectId: string): Promise<{ project: GameProject; created: string[] }> => {
    const resp = await webRequest<{ project: GameProject; created: string[] }>(
      METHOD.projectsScaffold,
      { project_id: projectId }
    );
    return { project: resp.project, created: resp.created ?? [] };
  },
  generateAsset: async (
    projectId: string,
    prompt: string,
    type: string,
    name?: string
  ): Promise<{ project: GameProject; asset: GameAsset }> => {
    const resp = await webRequest<{ project: GameProject; asset: GameAsset }>(
      METHOD.assetsGenerate,
      { project_id: projectId, prompt, type, name },
      { timeoutMs: GAME_GENERATE_TIMEOUT_MS }
    );
    return resp;
  },
  build2d: async (projectId: string): Promise<{ project: GameProject; created: string[] }> => {
    const resp = await webRequest<{ project: GameProject; created: string[] }>(
      METHOD.projectsBuild2d,
      { project_id: projectId }
    );
    return { project: resp.project, created: resp.created ?? [] };
  },
  listAssets: async (projectId?: string): Promise<GameAsset[]> => {
    const resp = await webRequest<{ assets?: GameAsset[] }>(METHOD.assetsList, {
      project_id: projectId ?? '',
    });
    return resp?.assets ?? [];
  },
  importAsset: async (
    projectId: string,
    filePath: string,
    type: string,
    name?: string
  ): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.assetsImport, {
      project_id: projectId,
      file_path: filePath,
      type,
      name,
    });
    return resp.project;
  },
  deleteAsset: async (projectId: string, assetId: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.assetsDelete, {
      project_id: projectId,
      asset_id: assetId,
    });
    return resp.project;
  },
  /** 整批删除同一次分解产出的所有部件（D-06），不必逐个点掉。 */
  deleteAssetBatch: async (projectId: string, decomposeBatchId: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.assetsDelete, {
      project_id: projectId,
      decompose_batch_id: decomposeBatchId,
    });
    return resp.project;
  },
  /** 提交一次素材分解（源素材须是已就绪的「角色」或「世界」图片）；真正的
   *  产物由 checkDecomposeStatus 轮询拿回来，见 D-20261116-1047 §5.7。 */
  decomposeAsset: async (
    projectId: string,
    sourceAssetId: string,
    hint?: string,
    granularity?: 'coarse' | 'medium' | 'fine'
  ): Promise<GameDecomposeSubmitResult> =>
    webRequest<GameDecomposeSubmitResult>(
      METHOD.assetsDecompose,
      { project_id: projectId, source_asset_id: sourceAssetId, hint, granularity },
      { timeoutMs: GAME_DECOMPOSE_SUBMIT_TIMEOUT_MS }
    ),
  checkDecomposeStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string,
    decomposeBatchId: string
  ): Promise<GameDecomposeStatusResult> =>
    webRequest<GameDecomposeStatusResult>(
      METHOD.assetsDecomposeCheckStatus,
      {
        project_id: projectId,
        placeholder_asset_id: placeholderAssetId,
        job_id: jobId,
        decompose_batch_id: decomposeBatchId,
      },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 提交一次 2D 转 3D（源素材须是已就绪的「角色」或「世界」图片）；产出
   *  一个单体模型，不分部件，真正的产物由 checkToModelStatus 轮询拿回来。 */
  toModel: async (projectId: string, sourceAssetId: string): Promise<GameToModelSubmitResult> =>
    webRequest<GameToModelSubmitResult>(
      METHOD.assetsToModel,
      { project_id: projectId, source_asset_id: sourceAssetId },
      { timeoutMs: GAME_DECOMPOSE_SUBMIT_TIMEOUT_MS }
    ),
  checkToModelStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameToModelStatusResult> =>
    webRequest<GameToModelStatusResult>(
      METHOD.assetsToModelCheckStatus,
      { project_id: projectId, placeholder_asset_id: placeholderAssetId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 实验室画布持久化：整份节点/连线快照覆盖式保存，与导演模式
   *  directorLabCanvasSave 同构，不做增量 diff。 */
  labCanvasSave: async (
    projectId: string,
    nodes: unknown[],
    edges: unknown[]
  ): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(METHOD.labCanvasSave, {
      project_id: projectId,
      nodes,
      edges,
    });
    return resp.project;
  },
  filesTree: async (projectId: string): Promise<GameFileEntry[]> => {
    const resp = await webRequest<{ files?: GameFileEntry[] }>(METHOD.filesTree, {
      project_id: projectId,
    });
    return resp?.files ?? [];
  },
  fileRead: async (projectId: string, path: string): Promise<string> => {
    const resp = await webRequest<{ content: string }>(METHOD.filesRead, {
      project_id: projectId,
      path,
    });
    return resp.content;
  },
  fileWrite: (projectId: string, path: string, content: string) =>
    webRequest<{ path: string; bytes: number }>(METHOD.filesWrite, {
      project_id: projectId,
      path,
      content,
    }),
  bridgeStatus: async (force = false): Promise<GameBridgeStatus> => {
    const resp = await webRequest<{ bridge: GameBridgeStatus }>(METHOD.bridgeStatus, { force });
    return resp.bridge;
  },
};
