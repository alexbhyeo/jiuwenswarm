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
// 网格补全/拓扑重构的 submit 要上传的是模型文件（.glb）——实测一个完整
// 角色模型（贴图烘焙后常见 40+MB）就命中过 60s、甚至 120s 超时，体量级别
// 跟分解/2D转3D 上传的源图完全不同，所以不能照搬 GAME_DECOMPOSE_SUBMIT_
// TIMEOUT_MS；直接复用生成类调用同一档 300s 预算（GAME_GENERATE_TIMEOUT_MS）
// 更合适。check_status 仍复用分解/2D转3D 的值（它只在成功那一次做下载，
// 跟分解/2D转3D 同一套下载体量级别，不需要跟着放宽）。
const GAME_MESH_OP_SUBMIT_TIMEOUT_MS = GAME_GENERATE_TIMEOUT_MS;
// 实验室助手一轮里可能连跑多张处理卡片（每张卡片后端最多等 600s），整轮
// 远超前端默认 15s。这里给到后端单卡片预算的两倍，避免前端先把还在正常
// 排队的轮次判成超时；真正的上限由后端 MAX_TOOL_ITERATIONS 与单卡片超时约束。
const GAME_LAB_CHAT_TIMEOUT_MS = 1_200_000;

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
  lab_chat_messages: GameLabChatMessage[];
};

/** 实验室助手的一条消息：与导演模式 EditChatMessage 同构，`applied` 记录
 * 这一轮真正跑过的处理卡片（用于在气泡里显示"调用了哪张卡"）。*/
export type GameLabChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  applied?: string[];
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

export type GameMeshOpSubmitResult = {
  project: GameProject;
  placeholder_asset_id: string;
  job_id: string;
};

export type GameMeshOpStatusResult = {
  project: GameProject;
  status: 'pending' | 'success' | 'failed';
  error?: string;
  asset?: GameAsset;
};

/** 实验室「文生图 / 图生图」卡片提交参数。`source` 只用于标记素材由哪张
 *  卡片产出（后端写进 params.source），「生成实验室流程图」靠它还原卡片；
 *  `referenceAssetIds` 只在图生图时出现，后端会解析成参考图路径。 */
export type GameLabImageRequest = {
  prompt: string;
  assetType: 'character' | 'world';
  source: 'text2image' | 'image_ref';
  referenceAssetIds?: string[];
  name?: string;
};

/** 骨骼检查的提交/轮询结果：只回"能不能绑定 + 推荐骨架"，不产出素材，
 *  所以没有 placeholder_asset_id / asset。 */
export type GameRigCheckSubmitResult = {
  project: GameProject;
  job_id: string;
};

export type GameRigCheckStatusResult = {
  status: 'pending' | 'success' | 'failed';
  riggable?: boolean;
  rig_type?: string;
  error?: string;
  project?: GameProject;
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
  assetsMeshComplete: 'game.assets.mesh_complete',
  assetsMeshCompleteCheckStatus: 'game.assets.mesh_complete_check_status',
  assetsMeshDecimate: 'game.assets.mesh_decimate',
  assetsMeshDecimateCheckStatus: 'game.assets.mesh_decimate_check_status',
  assetsTexture: 'game.assets.texture',
  assetsTextureCheckStatus: 'game.assets.texture_check_status',
  assetsRigCheck: 'game.assets.rig_check',
  assetsRigCheckStatus: 'game.assets.rig_check_status',
  assetsRig: 'game.assets.rig',
  assetsRigStatus: 'game.assets.rig_status',
  assetsRetarget: 'game.assets.retarget',
  assetsRetargetStatus: 'game.assets.retarget_status',
  labCanvasSave: 'game.lab_canvas.save',
  labChatSend: 'game.lab_chat.send',
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
  /** 实验室「文生图 / 图生图」卡片：与创作页共用 game.assets.generate，
   *  只是多带一个 `source` 标记（供流程图还原）和图生图的参考素材 id。 */
  generateLabImage: async (
    projectId: string,
    req: GameLabImageRequest
  ): Promise<{ project: GameProject; asset: GameAsset; error?: string }> => {
    const resp = await webRequest<{ project: GameProject; asset: GameAsset; error?: string }>(
      METHOD.assetsGenerate,
      {
        project_id: projectId,
        prompt: req.prompt,
        type: req.assetType,
        name: req.name,
        source: req.source,
        reference_asset_ids: req.referenceAssetIds,
      },
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
  /** 提交一次网格补全（源素材须是已就绪的 3D 模型/部件）；两段式流水线
   *  （先 mesh/segment 再 mesh/complete）完全由后端的 check_status 推进，
   *  前端只管一轮轮调同一个 RPC，不需要知道现在处于哪一段，见
   *  game_manager.handle_game_assets_mesh_complete_check_status 的注释。 */
  meshComplete: async (projectId: string, sourceAssetId: string): Promise<GameMeshOpSubmitResult> =>
    webRequest<GameMeshOpSubmitResult>(
      METHOD.assetsMeshComplete,
      { project_id: projectId, source_asset_id: sourceAssetId },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkMeshCompleteStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameMeshOpStatusResult> =>
    webRequest<GameMeshOpStatusResult>(
      METHOD.assetsMeshCompleteCheckStatus,
      { project_id: projectId, placeholder_asset_id: placeholderAssetId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 提交一次拓扑重构（源素材须是已就绪的 3D 模型/部件）；单段流水线，
   *  与 2D 转 3D 同构。 */
  meshDecimate: async (projectId: string, sourceAssetId: string): Promise<GameMeshOpSubmitResult> =>
    webRequest<GameMeshOpSubmitResult>(
      METHOD.assetsMeshDecimate,
      { project_id: projectId, source_asset_id: sourceAssetId },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkMeshDecimateStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameMeshOpStatusResult> =>
    webRequest<GameMeshOpStatusResult>(
      METHOD.assetsMeshDecimateCheckStatus,
      { project_id: projectId, placeholder_asset_id: placeholderAssetId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 提交一次纹理贴图（源素材须是已就绪的 3D 模型/部件）；Tripo3D
   *  models/texture 是单段异步任务，与拓扑重构同构。prompt 可选，
   *  quality 取 fast/standard/detailed/extreme。 */
  texture: async (
    projectId: string,
    sourceAssetId: string,
    prompt?: string,
    quality?: string
  ): Promise<GameMeshOpSubmitResult> =>
    webRequest<GameMeshOpSubmitResult>(
      METHOD.assetsTexture,
      { project_id: projectId, source_asset_id: sourceAssetId, prompt, quality },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkTextureStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameMeshOpStatusResult> =>
    webRequest<GameMeshOpStatusResult>(
      METHOD.assetsTextureCheckStatus,
      { project_id: projectId, placeholder_asset_id: placeholderAssetId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 骨骼检查：只回 riggable / rig_type，不产出素材（Tripo3D 文档里这一步
   *  0 credits，结果纯信息）。 */
  rigCheck: async (projectId: string, sourceAssetId: string): Promise<GameRigCheckSubmitResult> =>
    webRequest<GameRigCheckSubmitResult>(
      METHOD.assetsRigCheck,
      { project_id: projectId, source_asset_id: sourceAssetId },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkRigCheckStatus: async (projectId: string, jobId: string): Promise<GameRigCheckStatusResult> =>
    webRequest<GameRigCheckStatusResult>(
      METHOD.assetsRigCheckStatus,
      { project_id: projectId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 自动绑定：上传模型并提交 Tripo3D animations/rig，产出带骨架的模型。 */
  rig: async (
    projectId: string,
    sourceAssetId: string,
    rigType: string,
    spec: string
  ): Promise<GameMeshOpSubmitResult> =>
    webRequest<GameMeshOpSubmitResult>(
      METHOD.assetsRig,
      { project_id: projectId, source_asset_id: sourceAssetId, rig_type: rigType, spec },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkRigStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameMeshOpStatusResult> =>
    webRequest<GameMeshOpStatusResult>(
      METHOD.assetsRigStatus,
      { project_id: projectId, placeholder_asset_id: placeholderAssetId, job_id: jobId },
      { timeoutMs: GAME_DECOMPOSE_STATUS_TIMEOUT_MS }
    ),
  /** 动画重定向：给已绑定的模型套预设动画，产出带动画的模型。 */
  retarget: async (
    projectId: string,
    sourceAssetId: string,
    animation: string,
    animateInPlace: boolean
  ): Promise<GameMeshOpSubmitResult> =>
    webRequest<GameMeshOpSubmitResult>(
      METHOD.assetsRetarget,
      {
        project_id: projectId,
        source_asset_id: sourceAssetId,
        animation,
        animate_in_place: animateInPlace,
      },
      { timeoutMs: GAME_MESH_OP_SUBMIT_TIMEOUT_MS }
    ),
  checkRetargetStatus: async (
    projectId: string,
    placeholderAssetId: string,
    jobId: string
  ): Promise<GameMeshOpStatusResult> =>
    webRequest<GameMeshOpStatusResult>(
      METHOD.assetsRetargetStatus,
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
  /** 实验室助手：发一条消息，服务端跑完处理卡片工具循环后返回整个项目
   * （项目里带 lab_chat_messages，打开项目即可恢复历史，不单开取历史接口）。*/
  labChatSend: async (projectId: string, text: string): Promise<GameProject> => {
    const resp = await webRequest<{ project: GameProject }>(
      METHOD.labChatSend,
      { project_id: projectId, text },
      { timeoutMs: GAME_LAB_CHAT_TIMEOUT_MS }
    );
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
