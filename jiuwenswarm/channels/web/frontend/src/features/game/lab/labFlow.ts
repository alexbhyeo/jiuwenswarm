/**
 * 「生成实验室流程图」：把项目里已经跑过的处理卡片还原成一张流程图。
 *
 * 与导演模式 buildFlowFromChat 的差别：导演模式以聊天记录里"助手生成过哪些
 * 素材"为数据源（DirectorAsset 的 params 里记着生成参数）；游戏模式的实验室
 * 卡片是异步任务，产出素材自己带着 `params.source` 与 `source_asset_id`，
 * 所以这里**直接从项目素材还原整条链路**——不管卡片是助手调的、还是用户
 * 手点跑的，都能画出来，同一份数据也只维护一处。
 *
 * 纯函数：不碰 React、不碰画布状态，方便单测（见 tests/gameLabFlow.test.mjs）。
 * 节点形状与 GameLabCanvas 里手动摆出来的完全一致（image / model /
 * decompose / to_model / mesh_complete / mesh_decimate + 各端口 handle），
 * 所以重建出来的卡片可以继续手点、继续往上下游连线。
 */

export type FlowAsset = {
  asset_id: string;
  type: string;
  name: string;
  file_path?: string | null;
  status: string;
  params?: Record<string, unknown> | null;
  source_asset_id?: string | null;
  decompose_batch_id?: string | null;
};

export type LabFlowNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};

export type LabFlowEdge = {
  id: string;
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
};

export type LabFlow = {
  nodes: LabFlowNode[];
  edges: LabFlowEdge[];
  /** 画出来的处理卡片数（0 表示项目里还没有跑过卡片）。 */
  cardCount: number;
};

/**
 * 布局：一层一片区域，层内按表格排。
 *
 * 层内不是"一列排到底"——一次分解能产出 8 个部件，一列排下来是一条 2000px
 * 的竖线，既看不全也没法对比。改成层内按表格铺开：每层最多 `MAX_PER_ROW`
 * 个并排，多的换行往下摞（与素材库里 4 个一行的分组摆法一致）。
 *
 * `COLUMN_STEP` 取 250：卡片宽 220、素材节点宽 180（见 gameLab.css），留出
 * 20~70px 间隙；`ROW_HEIGHT` 取 270 是按卡片展开后约 250px 高留的余量，
 * 否则同一层里上下两张卡片会叠住。
 */
const ORIGIN_X = 40;
const ORIGIN_Y = 40;
const COLUMN_STEP = 250;
const ROW_HEIGHT = 270;
/** 一层最多并排几个节点。 */
const MAX_PER_ROW = 4;
/** 层与层之间的额外留白（除了最后一列节点自身的宽度）。 */
const DEPTH_GAP = 90;

/** 产出素材的卡片类型（与 GameLabCanvas 的 nodeTypes 对齐）。 */
const CARD_BY_SOURCE: Record<string, { nodeType: string; inputHandle: string; outputHandle: string }> = {
  // 文生图的输入只有提示词（没有素材输入），产出一张图片，出口同样是
  // `image`（见 nodes/GameImageGenNode.tsx）。
  text2image: { nodeType: 'text2image', inputHandle: '', outputHandle: 'image' },
  // 图生图吃一张或多张参考图，输入端口与导演模式的 imageRef 同名。
  image_ref: { nodeType: 'image_ref', inputHandle: 'image', outputHandle: 'image' },
  // 端口名照抄 nodes/GameToModelNode.tsx（它的出口叫 model）与
  // nodes/GameMeshOpNode.tsx（出口叫 result）；写错不会报错，只会让连线
  // 找不到落点、悬在卡片边缘外。
  to_model: { nodeType: 'to_model', inputHandle: 'image', outputHandle: 'model' },
  mesh_complete: { nodeType: 'mesh_complete', inputHandle: 'model', outputHandle: 'result' },
  mesh_decimate: { nodeType: 'mesh_decimate', inputHandle: 'model', outputHandle: 'result' },
  // 纹理贴图也是"接一个模型、产出一个模型"，端口与另外两张模型卡一致。
  texture: { nodeType: 'texture', inputHandle: 'model', outputHandle: 'result' },
  // 自动绑定 / 动画重定向同样是模型进、模型出；骨骼检查没有产出素材，
  // 不参与流程图还原（它只在卡片上给出能否绑定的结论）。
  auto_rig: { nodeType: 'auto_rig', inputHandle: 'model', outputHandle: 'result' },
  animation_retarget: { nodeType: 'retarget', inputHandle: 'model', outputHandle: 'result' },
};

const IMAGE_TYPES = new Set(['character', 'world', 'image']);
const ASSET_KEY_PREFIX = 'asset:';

export function isImageAssetType(type: string): boolean {
  return IMAGE_TYPES.has(type);
}

/** 素材 -> 节点类型。分解产出用 `model_part`、其余 3D 产出用 `model`——与手动
 *  摆卡片时 `spawnModelResultNodes` 传的 type 一致（两者都渲染成
 *  GameModelPartNode，只是 class / testid 的 `model-part` 与 `model` 之分）。 */
export function assetNodeType(type: string): 'image' | 'model' | 'model_part' {
  if (isImageAssetType(type)) return 'image';
  return type === 'model_part' ? 'model_part' : 'model';
}

function sourceOf(asset: FlowAsset): string {
  const params = asset.params ?? {};
  return String((params as Record<string, unknown>).source ?? '');
}

/** 卡片的输入素材 id 列表。图生图把多张参考图的 id 记在
 *  ``params.reference_asset_ids``（后端在 handle_game_assets_generate 里写入）；
 *  缺失时回退到 ``source_asset_id``（只有第一张）。其余卡片就是单个
 *  ``source_asset_id``，文生图没有输入。 */
function inputAssetIdsOf(asset: FlowAsset): string[] {
  const params = (asset.params ?? {}) as Record<string, unknown>;
  const refs = Array.isArray(params.reference_asset_ids)
    ? params.reference_asset_ids.map((v) => String(v ?? '')).filter(Boolean)
    : [];
  if (refs.length > 0) return refs;
  const single = String(asset.source_asset_id ?? '');
  return single ? [single] : [];
}

/**
 * 给每个节点 key 算"排第几列"：不依赖别人的是第 0 列，依赖了第 N 列的就
 * 排 N+1 列，连线因此总是横向的（同列会被竖线穿刺，整片画布糊成一坨）。
 * 依赖表里的 key 必须和 `keys` 是同一个命名空间（都是节点 key，素材节点
 * 写 `asset:<id>`、卡片写自己的 key），否则查不到依赖、所有节点挤在第 1
 * 列。循环依赖按 0 兜底（正常数据不该出现）。
 */
export function resolveDepths(keys: string[], depsByKey: Map<string, string[]>): Map<string, number> {
  const depthByKey = new Map<string, number>();
  const resolving = new Set<string>();

  const depthOf = (key: string): number => {
    const cached = depthByKey.get(key);
    if (cached !== undefined) return cached;
    const deps = (depsByKey.get(key) ?? []).filter((dep) => dep !== key && depsByKey.has(dep));
    if (deps.length === 0) {
      depthByKey.set(key, 0);
      return 0;
    }
    if (resolving.has(key)) return 0;
    resolving.add(key);
    let maxDepth = 0;
    for (const dep of deps) maxDepth = Math.max(maxDepth, depthOf(dep));
    resolving.delete(key);
    const depth = maxDepth + 1;
    depthByKey.set(key, depth);
    return depth;
  };

  for (const key of keys) depthOf(key);
  return depthByKey;
}

/** 卡片 key：分解按「源素材 + 批次」合并成一张卡（一次分解产出 N 个部件）。 */
function decomposeCardKey(asset: FlowAsset): string {
  return `card:decompose:${asset.source_asset_id ?? ''}:${asset.decompose_batch_id ?? ''}`;
}

export function buildLabFlow(project: { assets: FlowAsset[] }): LabFlow {
  const assets = (project.assets ?? []).filter((a) => a.status === 'ready');
  const byId = new Map(assets.map((a) => [a.asset_id, a]));

  // 1) 收集卡片：分解按批次合并，其余一张产出对应一张卡。
  type CardPlan = {
    key: string;
    nodeType: string;
    inputHandle: string;
    outputHandle: string;
    inputAssetIds: string[];
    outputAssetIds: string[];
    batchId?: string;
  };
  const cards = new Map<string, CardPlan>();
  const producedByCard = new Map<string, string>(); // 产出素材 id -> 卡片 key

  for (const asset of assets) {
    const source = sourceOf(asset);
    const isDecompose = source === 'decompose';
    const plan = CARD_BY_SOURCE[source];
    if (!isDecompose && !plan) continue;

    const key = isDecompose ? decomposeCardKey(asset) : `card:${source}:${asset.asset_id}`;
    let card = cards.get(key);
    if (!card) {
      card = {
        key,
        nodeType: isDecompose ? 'decompose' : plan.nodeType,
        inputHandle: isDecompose ? 'image' : plan.inputHandle,
        outputHandle: isDecompose ? 'parts' : plan.outputHandle,
        inputAssetIds: isDecompose ? [String(asset.source_asset_id ?? '')] : inputAssetIdsOf(asset),
        outputAssetIds: [],
        batchId: isDecompose ? String(asset.decompose_batch_id ?? '') : undefined,
      };
      cards.set(key, card);
    }
    card.outputAssetIds.push(asset.asset_id);
    producedByCard.set(asset.asset_id, key);
  }

  const cardList = [...cards.values()];

  // 2) 需要画出来的素材节点：卡片的输入 + 卡片的产出（产出也是下游的输入）。
  const assetNodeIds = new Set<string>();
  for (const card of cardList) {
    for (const inputId of card.inputAssetIds) {
      if (inputId && byId.has(inputId)) assetNodeIds.add(inputId);
    }
    for (const outputId of card.outputAssetIds) assetNodeIds.add(outputId);
  }

  // 3) 深度：素材节点依赖"产出它的卡片"，卡片依赖"它的输入素材"。
  //    两边都用节点 key（asset:<id> / 卡片自己的 key），保持同一个命名空间。
  const depsByKey = new Map<string, string[]>();
  for (const assetId of assetNodeIds) {
    const producerCard = producedByCard.get(assetId);
    depsByKey.set(`asset:${assetId}`, producerCard && cards.has(producerCard) ? [producerCard] : []);
  }
  for (const card of cardList) {
    depsByKey.set(
      card.key,
      card.inputAssetIds
        .filter((inputId) => inputId && assetNodeIds.has(inputId))
        .map((inputId) => `asset:${inputId}`)
    );
  }

  // 节点摆放顺序：按卡片成组（源图 -> 卡片 -> 这一批产出）走一遍。层内的表格
  // 就是照这个顺序填的，所以同一张分解卡产出的 8 个部件会连续占两行，不会被
  // 别的素材插进中间。
  const nodeKeys: string[] = [];
  const seenKeys = new Set<string>();
  const pushKey = (key: string) => {
    if (seenKeys.has(key)) return;
    seenKeys.add(key);
    nodeKeys.push(key);
  };
  for (const card of cardList) {
    for (const inputId of card.inputAssetIds) {
      if (inputId && assetNodeIds.has(inputId)) pushKey(`asset:${inputId}`);
    }
    pushKey(card.key);
    for (const outputId of card.outputAssetIds) pushKey(`asset:${outputId}`);
  }
  // 兜底：既没被当输入、也没被当产出领走的素材节点（数据结构上不该出现，
  // 出现了也不能丢）。
  for (const assetId of assetNodeIds) pushKey(`asset:${assetId}`);

  const depthByKey = resolveDepths(nodeKeys, depsByKey);

  // 4) 摆位置：一层一片区域，层内先按"同一个上游产出的那一组"分组——一次分解
  //    产出的 8 个部件就是一组，组内每行最多 MAX_PER_ROW 个、多的换行往下摞。
  //    不同组之间上下叠放：每层都铺成 4 列宽的话（实测 22 个节点铺到 16 列），
  //    整张图会变成一条 4500px 的横条，缩到能看全时字已经没了。
  const groupsByDepth = new Map<number, Map<string, string[]>>();
  for (const key of nodeKeys) {
    const depth = depthByKey.get(key) ?? 0;
    const groupKey = key.startsWith(ASSET_KEY_PREFIX)
      ? producedByCard.get(key.slice(ASSET_KEY_PREFIX.length)) ?? key
      : key;
    let groups = groupsByDepth.get(depth);
    if (!groups) {
      groups = new Map<string, string[]>();
      groupsByDepth.set(depth, groups);
    }
    const bucket = groups.get(groupKey);
    if (bucket) bucket.push(key);
    else groups.set(groupKey, [key]);
  }

  const depthX = new Map<number, number>();
  let cursorX = ORIGIN_X;
  const maxDepth = Math.max(0, ...groupsByDepth.keys());
  for (let depth = 0; depth <= maxDepth; depth += 1) {
    const columns = Math.max(1, ...[...(groupsByDepth.get(depth)?.values() ?? [])].map((keys) =>
      Math.min(Math.max(keys.length, 1), MAX_PER_ROW)
    ));
    depthX.set(depth, cursorX);
    cursorX += columns * COLUMN_STEP + DEPTH_GAP;
  }

  const positionByKey = new Map<string, { x: number; y: number }>();
  for (let depth = 0; depth <= maxDepth; depth += 1) {
    let rowOffset = 0;
    for (const keys of groupsByDepth.get(depth)?.values() ?? []) {
      keys.forEach((key, index) => {
        positionByKey.set(key, {
          x: (depthX.get(depth) ?? ORIGIN_X) + (index % MAX_PER_ROW) * COLUMN_STEP,
          y: ORIGIN_Y + (rowOffset + Math.floor(index / MAX_PER_ROW)) * ROW_HEIGHT,
        });
      });
      rowOffset += Math.ceil(keys.length / MAX_PER_ROW);
    }
  }
  const positionOf = (key: string): { x: number; y: number } =>
    positionByKey.get(key) ?? { x: ORIGIN_X, y: ORIGIN_Y };

  const nodes: LabFlowNode[] = [];
  const edges: LabFlowEdge[] = [];

  // 必须按 nodeKeys 的顺序取位置：层内表格是按这个顺序填的。
  for (const key of nodeKeys) {
    if (key.startsWith(ASSET_KEY_PREFIX)) {
      const asset = byId.get(key.slice(ASSET_KEY_PREFIX.length));
      if (!asset) continue;
      const nodeType = assetNodeType(asset.type);
      const data =
        nodeType === 'image'
          ? { assetId: asset.asset_id, filePath: asset.file_path ?? '', name: asset.name }
          : {
              assetId: asset.asset_id,
              filePath: asset.file_path ?? '',
              name: asset.name,
              status: asset.status,
            };
      nodes.push({ id: key, type: nodeType, position: positionOf(key), data });
      continue;
    }
    const card = cards.get(key);
    if (!card) continue;
    const outputAsset = byId.get(card.outputAssetIds[0]);
    const data =
      card.nodeType === 'decompose'
        ? {
            hint: '',
            granularity: 'medium',
            status: 'success',
            resultCount: card.outputAssetIds.length,
            decomposeBatchId: card.batchId ?? '',
          }
        : card.nodeType === 'text2image' || card.nodeType === 'image_ref'
          ? {
              prompt: String(((outputAsset?.params ?? {}) as Record<string, unknown>).prompt ?? ''),
              // 产出图片归到哪个类目（角色/世界），重画卡片时保持用户的原始选择。
              assetType: outputAsset?.type === 'world' ? 'world' : 'character',
              status: 'success',
              resultAssetId: card.outputAssetIds[0],
            }
          : card.nodeType === 'texture'
            ? {
                prompt: String(((outputAsset?.params ?? {}) as Record<string, unknown>).prompt ?? ''),
                quality: String(((outputAsset?.params ?? {}) as Record<string, unknown>).quality ?? 'standard'),
                status: 'success',
                resultAssetId: card.outputAssetIds[0],
              }
            : card.nodeType === 'auto_rig'
              ? {
                  rigType: String(((outputAsset?.params ?? {}) as Record<string, unknown>).rig_type ?? 'biped'),
                  spec: String(((outputAsset?.params ?? {}) as Record<string, unknown>).spec ?? 'tripo'),
                  status: 'success',
                  resultAssetId: card.outputAssetIds[0],
                }
              : card.nodeType === 'retarget'
                ? {
                    animation: String(
                      ((outputAsset?.params ?? {}) as Record<string, unknown>).animation ?? 'preset:idle'
                    ),
                    animateInPlace: Boolean(
                      ((outputAsset?.params ?? {}) as Record<string, unknown>).animate_in_place
                    ),
                    status: 'success',
                    resultAssetId: card.outputAssetIds[0],
                  }
                : { status: 'success', resultAssetId: card.outputAssetIds[0] };
    nodes.push({ id: card.key, type: card.nodeType, position: positionOf(card.key), data });
  }

  for (const card of cardList) {
    for (const inputAssetId of card.inputAssetIds) {
      const inputAsset = inputAssetId ? byId.get(inputAssetId) : undefined;
      if (!inputAsset) continue;
      const sourceId = `asset:${inputAssetId}`;
      edges.push({
        id: `e_${sourceId}__${card.key}`,
        source: sourceId,
        sourceHandle: assetNodeType(inputAsset.type) === 'image' ? 'image' : 'out',
        target: card.key,
        targetHandle: card.inputHandle,
      });
    }
    for (const outputId of card.outputAssetIds) {
      const targetId = `asset:${outputId}`;
      edges.push({
        id: `e_${card.key}__${targetId}`,
        source: card.key,
        sourceHandle: card.outputHandle,
        target: targetId,
        targetHandle: 'in',
      });
    }
  }

  return { nodes, edges, cardCount: cardList.length };
}
