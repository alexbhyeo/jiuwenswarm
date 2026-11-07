/**
 * 游戏模式（游戏工坊）工作区。
 *
 * 布局对齐设计稿：左侧项目栏（新建项目 + 项目列表），中间按「创作 / 工作流 /
 * 素材 / 场景 / 代码 / 日志」分页，右侧是游戏开发助手与桥接状态。
 *
 * P0 只接通后端已有的契约（项目、脚手架、素材、代码文件、桥接状态）；
 * 助手对话与场景树编辑按设计 D-20261107-1637 在 P1/P2 落地，这里以明确的
 * 占位说明呈现，不假装功能已存在。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  gameApi,
  type GameAsset,
  type GameBridgeStatus,
  type GameChange,
  type GameFileEntry,
  type GameProject,
} from './gameApi';

type TabKey = 'create' | 'workflow' | 'assets' | 'scene' | 'code' | 'logs';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'create', label: '创作' },
  { key: 'workflow', label: '工作流' },
  { key: 'assets', label: '素材' },
  { key: 'scene', label: '场景' },
  { key: 'code', label: '代码' },
  { key: 'logs', label: '日志' },
];

const WORKFLOW_STEPS = [
  { title: '需求分析', detail: '确定玩法、场景与目标平台' },
  { title: '素材生成', detail: '角色 / 道具 / 场景 / 音频' },
  { title: '游戏搭建', detail: '工程脚手架 + 场景与脚本' },
  { title: '游戏测试', detail: '运行、检查日志与运行期错误' },
  { title: '导出发布', detail: '打包可运行版本' },
];

const ASSET_GROUPS: { type: string; label: string }[] = [
  { type: 'character', label: '角色' },
  { type: 'prop', label: '道具' },
  { type: 'scene', label: '场景' },
  { type: 'model', label: '模型 (3D)' },
  { type: 'texture', label: '纹理' },
  { type: 'audio', label: '音频' },
  { type: 'animation', label: '动画' },
];

const card: React.CSSProperties = {
  background: 'var(--surface, #fff)',
  border: '1px solid var(--border, #e5e7eb)',
  borderRadius: 12,
  padding: 16,
};

export function GamePage() {
  const [projects, setProjects] = useState<GameProject[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [tab, setTab] = useState<TabKey>('create');
  const [bridge, setBridge] = useState<GameBridgeStatus | null>(null);
  const [files, setFiles] = useState<GameFileEntry[]>([]);
  const [activeFile, setActiveFile] = useState<string>('');
  const [fileContent, setFileContent] = useState<string>('');
  const [draftName, setDraftName] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [genPrompt, setGenPrompt] = useState('');
  const [genType, setGenType] = useState('character');
  const [chatText, setChatText] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [lastChanges, setLastChanges] = useState<GameChange[]>([]);

  const selected = useMemo(
    () => projects.find((p) => p.project_id === selectedId) ?? null,
    [projects, selectedId]
  );

  const refresh = useCallback(async () => {
    const list = await gameApi.listProjects();
    setProjects(list);
    setSelectedId((current) =>
      current && list.some((p) => p.project_id === current) ? current : (list[0]?.project_id ?? '')
    );
  }, []);

  useEffect(() => {
    void refresh().catch((err) => setNotice(String(err)));
    void gameApi
      .bridgeStatus()
      .then(setBridge)
      .catch(() => setBridge(null));
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setFiles([]);
      return;
    }
    void gameApi
      .filesTree(selected.project_id)
      .then(setFiles)
      .catch((err) => setNotice(String(err)));
  }, [selected]);

  const createProject = async () => {
    const name = draftName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const project = await gameApi.createProject(name);
      await gameApi.scaffoldProject(project.project_id);
      setDraftName('');
      await refresh();
      setSelectedId(project.project_id);
      setNotice(`已创建「${name}」并生成 Godot 工程脚手架`);
    } catch (err) {
      setNotice(`创建失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const scaffold = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const { created } = await gameApi.scaffoldProject(selected.project_id);
      await refresh();
      setNotice(created.length ? `已补充文件：${created.join('、')}` : '工程结构已完整');
    } catch (err) {
      setNotice(`脚手架失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const generateAsset = async () => {
    if (!selected || !genPrompt.trim()) return;
    setBusy(true);
    setNotice('正在生成素材…（这一步会调用图片生成服务）');
    try {
      const { asset } = await gameApi.generateAsset(
        selected.project_id,
        genPrompt.trim(),
        genType,
        genPrompt.trim().slice(0, 12)
      );
      await refresh();
      setNotice(asset.status === 'ready' ? `素材已生成：${asset.name}` : `素材状态：${asset.status}`);
    } catch (err) {
      setNotice(`生成失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const build2d = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const { created } = await gameApi.build2d(selected.project_id);
      await refresh();
      setNotice(`已生成 2D 主城工程：${created.join('、')}`);
    } catch (err) {
      setNotice(`构建失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const sendChat = async () => {
    if (!selected || !chatText.trim()) return;
    setChatBusy(true);
    setNotice('助手正在改工程…');
    try {
      const { project, reply, changes } = await gameApi.chatSend(selected.project_id, chatText.trim());
      setChatText('');
      setLastChanges(changes);
      setProjects((prev) => prev.map((p) => (p.project_id === project.project_id ? project : p)));
      await gameApi.filesTree(project.project_id).then(setFiles).catch(() => undefined);
      setNotice(`助手已回：${reply.slice(0, 80)}${reply.length > 80 ? '…' : ''}`);
    } catch (err) {
      setNotice(`助手失败：${String(err)}`);
    } finally {
      setChatBusy(false);
    }
  };

  const revertChanges = async () => {
    if (!selected) return;
    try {
      const { project, restored } = await gameApi.revertChanges(selected.project_id);
      setLastChanges([]);
      setProjects((prev) => prev.map((p) => (p.project_id === project.project_id ? project : p)));
      await gameApi.filesTree(project.project_id).then(setFiles).catch(() => undefined);
      setNotice(restored.length ? `已回退：${restored.join('、')}` : '没有需要回退的改动');
    } catch (err) {
      setNotice(`回退失败：${String(err)}`);
    }
  };

  const openFile = async (path: string) => {
    if (!selected) return;
    try {
      setActiveFile(path);
      setFileContent(await gameApi.fileRead(selected.project_id, path));
    } catch (err) {
      setNotice(`读取失败：${String(err)}`);
    }
  };

  const saveFile = async () => {
    if (!selected || !activeFile) return;
    try {
      await gameApi.fileWrite(selected.project_id, activeFile, fileContent);
      setNotice(`已保存 ${activeFile}`);
    } catch (err) {
      setNotice(`保存失败：${String(err)}`);
    }
  };

  const assetsByType = (type: string): GameAsset[] =>
    (selected?.assets ?? []).filter((a) => a.type === type);

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0, gap: 16, padding: 16 }}>
      {/* 项目栏 */}
      <aside style={{ width: 260, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>游戏工坊</div>
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void createProject();
            }}
            placeholder="新游戏项目名称"
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid #d1d5db' }}
          />
          <button
            type="button"
            onClick={() => void createProject()}
            disabled={busy || !draftName.trim()}
            style={{
              marginTop: 8,
              width: '100%',
              padding: '8px 10px',
              borderRadius: 8,
              border: 'none',
              background: '#6366f1',
              color: '#fff',
              cursor: busy ? 'progress' : 'pointer',
            }}
          >
            新建游戏项目
          </button>
        </div>

        <div style={{ ...card, flex: 1, overflowY: 'auto' }}>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 8 }}>
            项目 · {projects.length}
          </div>
          {projects.length === 0 && <div style={{ fontSize: 13, color: '#9ca3af' }}>还没有游戏项目</div>}
          {projects.map((p) => (
            <button
              key={p.project_id}
              type="button"
              onClick={() => setSelectedId(p.project_id)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '8px 10px',
                marginBottom: 6,
                borderRadius: 8,
                border: '1px solid ' + (p.project_id === selectedId ? '#6366f1' : '#e5e7eb'),
                background: p.project_id === selectedId ? '#eef2ff' : '#fff',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 500 }}>{p.name}</div>
              <div style={{ fontSize: 11, color: '#6b7280' }}>
                {p.status} · {p.assets.length} 个素材
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* 主区 */}
      <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{selected ? selected.name : '游戏工坊'}</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 8,
                  border: 'none',
                  cursor: 'pointer',
                  background: tab === t.key ? '#eef2ff' : 'transparent',
                  color: tab === t.key ? '#4338ca' : '#4b5563',
                  fontSize: 13,
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            <button type="button" onClick={() => void scaffold()} disabled={!selected || busy} style={btn}>
              修复工程结构
            </button>
          </div>
        </div>

        {notice && (
          <div style={{ ...card, padding: '8px 12px', fontSize: 12, color: '#374151' }}>{notice}</div>
        )}

        <div style={{ ...card, flex: 1, minHeight: 0, overflow: 'auto' }}>
          {tab === 'create' && (
            <div>
              <div style={{ ...card, marginBottom: 12, background: '#f9fafb' }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>生成素材并搭建 2D 主城</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <select
                    value={genType}
                    onChange={(e) => setGenType(e.target.value)}
                    style={{ padding: '6px 8px', borderRadius: 8, border: '1px solid #d1d5db' }}
                  >
                    {ASSET_GROUPS.map((g) => (
                      <option key={g.type} value={g.type}>
                        {g.label}
                      </option>
                    ))}
                  </select>
                  <input
                    value={genPrompt}
                    onChange={(e) => setGenPrompt(e.target.value)}
                    placeholder="素材描述，例如：卡通小鸡骑士，黄色圆身，手持木剑"
                    style={{ flex: 1, minWidth: 240, padding: '6px 10px', borderRadius: 8, border: '1px solid #d1d5db' }}
                  />
                  <button type="button" onClick={() => void generateAsset()} disabled={!selected || busy || !genPrompt.trim()} style={btn}>
                    生成素材
                  </button>
                  <button type="button" onClick={() => void build2d()} disabled={!selected || busy} style={btn}>
                    搭建 2D 主城
                  </button>
                </div>
              </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
              {WORKFLOW_STEPS.map((step, index) => (
                <div key={step.title} style={{ ...card, background: '#f9fafb' }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {index + 1}. {step.title}
                  </div>
                  <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>{step.detail}</div>
                </div>
              ))}
            </div>
            </div>
          )}

          {tab === 'workflow' && (
            <ol style={{ paddingLeft: 20, fontSize: 13, lineHeight: 2 }}>
              {WORKFLOW_STEPS.map((step) => (
                <li key={step.title}>
                  <strong>{step.title}</strong> — {step.detail}
                </li>
              ))}
            </ol>
          )}

          {tab === 'assets' && (
            <div>
              {!selected && <div style={{ fontSize: 13, color: '#6b7280' }}>先创建或选择一个项目</div>}
              {selected &&
                ASSET_GROUPS.map((group) => {
                  const items = assetsByType(group.type);
                  return (
                    <div key={group.type} style={{ marginBottom: 16 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                        {group.label} ({items.length})
                      </div>
                      {items.length === 0 ? (
                        <div style={{ fontSize: 12, color: '#9ca3af' }}>暂无素材</div>
                      ) : (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                          {items.map((asset) => (
                            <div key={asset.asset_id} style={{ ...card, padding: '6px 10px', fontSize: 12 }}>
                              {asset.name}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          )}

          {tab === 'scene' && (
            <div style={{ fontSize: 13, color: '#4b5563', lineHeight: 1.9 }}>
              <p>场景编辑需要 Godot 编辑器经由 Godot-MCP 连接。</p>
              <p>
                当前桥接状态：
                <strong> {bridge?.ready ? '可用' : '不可用'}</strong>
                {bridge && !bridge.ready && (
                  <>
                    （Godot {bridge.godot ? bridge.godot_version || '已安装' : '未检测到'}，
                    .NET {bridge.dotnet ? bridge.dotnet_version : '未检测到'}，
                    MCP {bridge.mcp ? '已配置' : '未配置'}）
                  </>
                )}
              </p>
              <p style={{ color: '#6b7280' }}>
                按设计 D-20261107-1637，场景树与属性编辑在 P2 通过 MCP 的 RQL 落地。
              </p>
            </div>
          )}

          {tab === 'code' && (
            <div style={{ display: 'flex', gap: 12, height: '100%', minHeight: 320 }}>
              <div style={{ width: 220, borderRight: '1px solid #e5e7eb', paddingRight: 8 }}>
                <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>项目文件</div>
                {files.length === 0 && <div style={{ fontSize: 12, color: '#9ca3af' }}>暂无文件</div>}
                {files.map((file) => (
                  <button
                    key={file.path}
                    type="button"
                    onClick={() => void openFile(file.path)}
                    style={{
                      display: 'block',
                      width: '100%',
                      textAlign: 'left',
                      border: 'none',
                      background: file.path === activeFile ? '#eef2ff' : 'transparent',
                      padding: '4px 6px',
                      borderRadius: 6,
                      fontSize: 12,
                      cursor: 'pointer',
                    }}
                  >
                    {file.path}
                  </button>
                ))}
              </div>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{ fontSize: 12, color: '#6b7280' }}>{activeFile || '选择左侧文件'}</div>
                  <button
                    type="button"
                    onClick={() => void saveFile()}
                    disabled={!activeFile}
                    style={{ ...btn, marginLeft: 'auto' }}
                  >
                    保存
                  </button>
                </div>
                <textarea
                  value={fileContent}
                  onChange={(e) => setFileContent(e.target.value)}
                  spellCheck={false}
                  style={{
                    flex: 1,
                    minHeight: 260,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    fontSize: 12,
                    padding: 10,
                    borderRadius: 8,
                    border: '1px solid #d1d5db',
                    resize: 'vertical',
                  }}
                />
              </div>
            </div>
          )}

          {tab === 'logs' && (
            <div style={{ fontSize: 13, color: '#4b5563' }}>
              运行日志在 P2 接入（`console` 与 `runtime-errors` 两个 MCP 工具）。
            </div>
          )}
        </div>
      </main>

      {/* 助手栏 */}
      <aside style={{ width: 300, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0, flex: 1 }}>
          <div style={{ fontWeight: 600 }}>游戏开发助手</div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 120, fontSize: 12, lineHeight: 1.7 }}>
            {((selected?.chat_messages ?? []) as { role: string; content: string }[]).slice(-8).map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                style={{
                  marginBottom: 8,
                  padding: '6px 8px',
                  borderRadius: 8,
                  background: m.role === 'user' ? '#eef2ff' : '#f3f4f6',
                }}
              >
                {m.content}
              </div>
            ))}
            {!selected?.chat_messages?.length && (
              <div style={{ color: '#9ca3af' }}>描述你想改的玩法，例如「按空格让主角跳一下」。</div>
            )}
          </div>
          {lastChanges.length > 0 && (
            <div style={{ borderTop: '1px solid #e5e7eb', paddingTop: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                本轮改动（{lastChanges.length}）
              </div>
              {lastChanges.map((c) => (
                <div key={c.path} style={{ fontSize: 12, color: '#374151' }}>
                  {c.status === 'added' ? '＋' : c.status === 'removed' ? '－' : '±'} {c.path}
                </div>
              ))}
              <button type="button" onClick={() => void revertChanges()} style={{ ...btn, marginTop: 6, width: '100%' }}>
                回退本轮改动
              </button>
            </div>
          )}
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              value={chatText}
              onChange={(e) => setChatText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void sendChat();
              }}
              placeholder="描述想修改的玩法…"
              disabled={!selected || chatBusy}
              style={{ flex: 1, padding: '6px 8px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 12 }}
            />
            <button type="button" onClick={() => void sendChat()} disabled={!selected || chatBusy || !chatText.trim()} style={btn}>
              {chatBusy ? '…' : '发送'}
            </button>
          </div>
        </div>
        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>运行环境</div>
          {bridge ? (
            <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12, lineHeight: 1.9, color: '#374151' }}>
              <li>Godot：{bridge.godot ? bridge.godot_version || '已安装' : '未检测到'}</li>
              <li>Mono 构建：{bridge.godot_mono ? '是' : '否'}</li>
              <li>.NET SDK：{bridge.dotnet ? bridge.dotnet_version : '未检测到'}</li>
              <li>Godot MCP：{bridge.mcp ? '已配置' : '未配置'}</li>
            </ul>
          ) : (
            <div style={{ fontSize: 12, color: '#9ca3af' }}>状态读取中…</div>
          )}
        </div>
      </aside>
    </div>
  );
}

const btn: React.CSSProperties = {
  padding: '6px 12px',
  borderRadius: 8,
  border: '1px solid #d1d5db',
  background: '#fff',
  cursor: 'pointer',
  fontSize: 13,
};
