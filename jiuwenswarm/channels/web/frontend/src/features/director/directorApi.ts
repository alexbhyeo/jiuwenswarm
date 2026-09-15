/**
 * 导演模式（Director Mode）Web API 客户端。
 *
 * 组件只调用这里的 typed API。RPC 失败时统一抛出 DirectorApiError —— 网关会把
 * 后端 DirectorRpcError 的 code 提升到 WS 消息顶层 error.code
 * （见 app_web_handlers.py / web_connect.py 对 payload.get("code") 的转发），
 * webClient 的 createWebError 再把它挂到被 reject 的 WebError.code 上，
 * 因此这里直接读 err.code，无需下钻 err.payload。
 */

import { webRequest } from '../../services/webClient';
import type { WebError } from '../../types/websocket';
import { DirectorApiError } from './types';
import type {
  DirectorAssetCounts,
  DirectorProject,
  GenerateParams,
} from './types';

const METHOD = {
  projectsList: 'director.projects.list',
  projectsCreate: 'director.projects.create',
  projectsGet: 'director.projects.get',
  generate: 'director.generate',
  generateCheckStatus: 'director.generate.check_status',
  assetRename: 'director.asset.rename',
} as const;

// generate_video 内部轮询上限是 120s（12 次 * 10s sleep），但每次 sleep 之间还有一次
// GET 轮询请求的真实网络耗时，加上提交任务的首个 POST——实测总耗时可达 130-150s+，
// 130s 的前端超时边际太薄，会在正常（非异常）情况下就先于后端返回而超时。
const GENERATE_TIMEOUT_MS = 200_000;

function toDirectorError(err: unknown): DirectorApiError {
  if (err instanceof DirectorApiError) return err;
  const webErr = err as Partial<WebError> | undefined;
  const code = (webErr && typeof webErr.code === 'string' && webErr.code) || 'UNKNOWN';
  const message = (webErr && webErr.message) || String(err);
  return new DirectorApiError(code, message);
}

export interface ProjectsListResult {
  projects: DirectorProject[];
  assetCounts: DirectorAssetCounts;
}

function normalizeProjectsList(raw: unknown): ProjectsListResult {
  const payload = (raw ?? {}) as { projects?: DirectorProject[]; asset_counts?: DirectorAssetCounts };
  return {
    projects: payload.projects ?? [],
    assetCounts: payload.asset_counts ?? { video: 0, image: 0 },
  };
}

export function directorProjectsList(): Promise<ProjectsListResult> {
  return webRequest<unknown>(METHOD.projectsList, {})
    .then(normalizeProjectsList)
    .catch((err) => {
      throw toDirectorError(err);
    });
}

export interface ProjectResult {
  project: DirectorProject;
  assetCounts?: DirectorAssetCounts;
}

function normalizeProjectResult(raw: unknown): ProjectResult {
  const payload = (raw ?? {}) as { project: DirectorProject; asset_counts?: DirectorAssetCounts };
  return { project: payload.project, assetCounts: payload.asset_counts };
}

export function directorProjectsCreate(name: string): Promise<ProjectResult> {
  return webRequest<unknown>(METHOD.projectsCreate, { name })
    .then(normalizeProjectResult)
    .catch((err) => {
      throw toDirectorError(err);
    });
}

export function directorProjectsGet(projectId: string): Promise<ProjectResult> {
  return webRequest<unknown>(METHOD.projectsGet, { project_id: projectId })
    .then(normalizeProjectResult)
    .catch((err) => {
      throw toDirectorError(err);
    });
}

export interface GenerateResult {
  project: DirectorProject;
  assetId: string;
  assetCounts: DirectorAssetCounts;
}

function normalizeGenerateResult(raw: unknown): GenerateResult {
  const payload = raw as { project: DirectorProject; asset_id: string; asset_counts: DirectorAssetCounts };
  return { project: payload.project, assetId: payload.asset_id, assetCounts: payload.asset_counts };
}

export function directorGenerate(params: GenerateParams): Promise<GenerateResult> {
  const wire = {
    project_id: params.projectId,
    mode: params.mode,
    prompt: params.prompt,
    aspect_ratio: params.aspectRatio,
    resolution: params.resolution,
    duration_seconds: params.durationSeconds,
    generate_audio: params.generateAudio,
  };
  return webRequest<unknown>(METHOD.generate, wire, { timeoutMs: GENERATE_TIMEOUT_MS })
    .then(normalizeGenerateResult)
    .catch((err) => {
      throw toDirectorError(err);
    });
}

export interface CheckStatusResult {
  project: DirectorProject;
  assetId: string;
  status: 'ready' | 'pending' | 'failed';
  assetCounts: DirectorAssetCounts;
}

function normalizeCheckStatusResult(raw: unknown): CheckStatusResult {
  const payload = raw as {
    project: DirectorProject;
    asset_id: string;
    status: 'ready' | 'pending' | 'failed';
    asset_counts: DirectorAssetCounts;
  };
  return {
    project: payload.project,
    assetId: payload.asset_id,
    status: payload.status,
    assetCounts: payload.asset_counts,
  };
}

export function directorGenerateCheckStatus(
  projectId: string,
  assetId: string,
  jobId: string
): Promise<CheckStatusResult> {
  return webRequest<unknown>(METHOD.generateCheckStatus, {
    project_id: projectId,
    asset_id: assetId,
    job_id: jobId,
  })
    .then(normalizeCheckStatusResult)
    .catch((err) => {
      throw toDirectorError(err);
    });
}

export function directorAssetRename(projectId: string, assetId: string, name: string): Promise<ProjectResult> {
  return webRequest<unknown>(METHOD.assetRename, {
    project_id: projectId,
    asset_id: assetId,
    name,
  })
    .then(normalizeProjectResult)
    .catch((err) => {
      throw toDirectorError(err);
    });
}
