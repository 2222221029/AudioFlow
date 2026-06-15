import {useEffect, useRef, useState, useCallback} from 'react';
import {Icon} from './Icons.jsx';
import {api} from '../services/api.js';

// ─── Sub-page nav tabs ───────────────────────────────────────────────────────
const TABS = [
  ['metadata', 'i-search', '元数据获取'],
  ['params', 'i-settings', '处理参数'],
  ['queue', 'i-download', '任务队列'],
  ['logs', 'i-file', '处理日志'],
  ['admin', 'i-cookie', '管理设置'],
];

const STATUS_COLORS = {
  pending: 'var(--text-mute)',
  processing: 'var(--primary)',
  done: 'var(--success)',
  failed: 'var(--danger)',
  stopped: 'var(--text-faint)',
};
const STATUS_LABELS = {
  pending: '等待',
  processing: '处理中',
  done: '完成',
  failed: '失败',
  stopped: '已停止',
};

function metaApi(path, opts = {}) {
  return api(`/api/meta${path}`, opts);
}

// ─── Main component ───────────────────────────────────────────────────────────
export function MetaScraperPage() {
  const [subPage, setSubPage] = useState('metadata');
  const [options, setOptions] = useState(null);

  // status / queue / logs from SSE or polling
  const [metaStatus, setMetaStatus] = useState(null);
  const evtRef = useRef(null);

  // metadata fetch state
  const [metaTab, setMetaTab] = useState('id'); // 'id' | 'link'
  const [apiSource, setApiSource] = useState('喜马拉雅');
  const [apiId, setApiId] = useState('');
  const [linkPlatform, setLinkPlatform] = useState('起点听书');
  const [linkUrl, setLinkUrl] = useState('');
  const [fetchedMeta, setFetchedMeta] = useState(null);
  const [fetchBusy, setFetchBusy] = useState(false);
  const [fetchError, setFetchError] = useState('');

  // params form state
  const [params, setParams] = useState(null);
  const [paramsDirty, setParamsDirty] = useState(false);
  const [paramsBusy, setParamsBusy] = useState(false);
  const [paramsMsg, setParamsMsg] = useState('');

  // file browser modal
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserData, setBrowserData] = useState(null);

  // cookies state
  const [cookies, setCookies] = useState({qidian: '', netease: ''});
  const [cookiesBusy, setCookiesBusy] = useState(false);
  const [cookiesMsg, setCookiesMsg] = useState('');

  // tag blacklist state
  const [blacklist, setBlacklist] = useState([]);
  const [blacklistInput, setBlacklistInput] = useState('');
  const [blacklistBusy, setBlacklistBusy] = useState(false);

  // toast
  const [toast, setToast] = useState('');
  const showToast = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  }, []);

  // ── Load options + config once ──────────────────────────────────────────────
  useEffect(() => {
    metaApi('/options').then(r => r.ok && setOptions(r.options)).catch(() => {});
    metaApi('/config').then(r => r.ok && setParams(r.params)).catch(() => {});
  }, []);

  // ── SSE for live status ─────────────────────────────────────────────────────
  useEffect(() => {
    const connect = () => {
      const token = localStorage.getItem('audioflow_token') || '';
      const url = `/api/meta/events`;
      const headers = token ? {Authorization: `Bearer ${token}`} : {};
      // Use fetch + ReadableStream for SSE with auth
      const ctrl = new AbortController();
      evtRef.current = ctrl;
      (async () => {
        try {
          const resp = await fetch(url, {headers, signal: ctrl.signal});
          if (!resp.ok) return;
          const reader = resp.body.getReader();
          const dec = new TextDecoder();
          let buf = '';
          while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            buf += dec.decode(value, {stream: true});
            const parts = buf.split('\n\n');
            buf = parts.pop();
            for (const chunk of parts) {
              const lines = chunk.split('\n');
              let eventType = 'message', data = '';
              for (const line of lines) {
                if (line.startsWith('event: ')) eventType = line.slice(7).trim();
                if (line.startsWith('data: ')) data = line.slice(6).trim();
              }
              if (eventType === 'status' || eventType === 'update') {
                try { setMetaStatus(prev => ({...prev, ...JSON.parse(data)})); } catch {}
              }
            }
          }
        } catch {}
      })();
      return ctrl;
    };
    const ctrl = connect();
    // Fallback poll if SSE fails
    const poll = setInterval(() => {
      metaApi('/status').then(r => r.ok && setMetaStatus(r.status)).catch(() => {});
    }, 5000);
    return () => {
      ctrl.abort();
      clearInterval(poll);
    };
  }, []);

  // ── Param helpers ────────────────────────────────────────────────────────────
  const setParam = (key, val) => {
    setParams(p => ({...p, [key]: val}));
    setParamsDirty(true);
  };

  const applyMetaToParams = () => {
    if (!fetchedMeta) return;
    setParams(p => ({
      ...p,
      title: fetchedMeta.title || p.title,
      author: fetchedMeta.author || p.author,
      anchor: fetchedMeta.anchor || p.anchor,
      year: fetchedMeta.year || p.year,
      finished: fetchedMeta.finished || p.finished,
      category: fetchedMeta.category || p.category,
      api_source: apiSource,
      api_id: apiId,
      manual_desc: fetchedMeta.desc || p.manual_desc,
      fetched_metadata: fetchedMeta.raw || {},
    }));
    setParamsDirty(true);
    setSubPage('params');
    showToast('元数据已应用到处理参数');
  };

  // ── Fetch metadata ─────────────────────────────────────────────────────────
  const doFetchById = async () => {
    if (!apiSource || !apiId.trim()) return;
    setFetchBusy(true); setFetchError(''); setFetchedMeta(null);
    try {
      const r = await metaApi('/fetch-metadata', {method: 'POST', body: JSON.stringify({api_source: apiSource, api_id: apiId.trim()})});
      if (r.ok) setFetchedMeta(r.metadata);
      else setFetchError(r.error || '获取失败');
    } catch(e) { setFetchError(String(e)); }
    setFetchBusy(false);
  };

  const doFetchByLink = async () => {
    if (!linkPlatform || !linkUrl.trim()) return;
    setFetchBusy(true); setFetchError(''); setFetchedMeta(null);
    try {
      const r = await metaApi('/fetch-link', {method: 'POST', body: JSON.stringify({platform: linkPlatform, url: linkUrl.trim()})});
      if (r.ok) setFetchedMeta(r.metadata);
      else setFetchError(r.error || '获取失败');
    } catch(e) { setFetchError(String(e)); }
    setFetchBusy(false);
  };

  // ── Process / Queue ─────────────────────────────────────────────────────────
  const doRun = async () => {
    if (!params) return;
    setParamsBusy(true); setParamsMsg('');
    try {
      const r = await metaApi('/run', {method: 'POST', body: JSON.stringify({params})});
      if (r.ok) { setParamsMsg('任务已启动'); showToast('任务已启动'); }
      else setParamsMsg(r.error || '启动失败');
    } catch(e) { setParamsMsg(String(e)); }
    setParamsBusy(false);
  };

  const doAddQueue = async () => {
    if (!params) return;
    setParamsBusy(true); setParamsMsg('');
    try {
      const r = await metaApi('/queue/add', {method: 'POST', body: JSON.stringify({params})});
      if (r.ok) { setParamsMsg('已加入队列'); showToast('已加入队列'); }
      else setParamsMsg(r.error || '加入队列失败');
    } catch(e) { setParamsMsg(String(e)); }
    setParamsBusy(false);
  };

  const doStop = async () => {
    await metaApi('/stop', {method: 'POST'});
    showToast('已发送停止信号');
  };

  const doQueueStart = async () => {
    const r = await metaApi('/queue/start', {method: 'POST'});
    if (!r.ok) showToast(r.error || '启动失败');
    else showToast('队列处理已启动');
  };

  const doQueueClear = async () => {
    await metaApi('/queue/clear', {method: 'POST'});
    showToast('队列已清空');
  };

  const doQueueRetry = async () => {
    await metaApi('/queue/retry-failed', {method: 'POST'});
    showToast('失败任务已重置为等待');
  };

  const doRemoveItem = async (id) => {
    await metaApi('/queue/remove', {method: 'POST', body: JSON.stringify({ids: [id]})});
  };

  // ── File browser ─────────────────────────────────────────────────────────────
  const openBrowser = async (path) => {
    const r = await metaApi(`/browse?path=${encodeURIComponent(path || '')}`);
    if (r.ok) setBrowserData(r.browser);
    setBrowserOpen(true);
  };

  const selectFolder = (path) => {
    setParam('input_folder', path);
    setBrowserOpen(false);
    // Try to load folder config
    metaApi('/folder-config', {method: 'POST', body: JSON.stringify({path})})
      .then(r => { if (r.ok && r.found) { setParams(p => ({...p, ...r.params})); showToast('已加载目录配置'); } })
      .catch(() => {});
  };

  // ── Cookies ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (subPage === 'admin') {
      metaApi('/cookies').then(r => r.ok && setCookies(r.cookies)).catch(() => {});
      metaApi('/tag-blacklist').then(r => r.ok && setBlacklist(r.patterns || [])).catch(() => {});
    }
  }, [subPage]);

  const saveCookies = async () => {
    setCookiesBusy(true);
    const r = await metaApi('/cookies', {method: 'POST', body: JSON.stringify({cookies})});
    setCookiesMsg(r.ok ? 'Cookie 已保存' : (r.error || '保存失败'));
    setCookiesBusy(false);
  };

  const saveBlacklist = async () => {
    setBlacklistBusy(true);
    const r = await metaApi('/tag-blacklist', {method: 'POST', body: JSON.stringify({patterns: blacklist})});
    if (r.ok) setBlacklist(r.patterns || []);
    setBlacklistBusy(false);
    showToast('标签黑名单已保存');
  };

  const addBlacklistItem = () => {
    const val = blacklistInput.trim();
    if (!val || blacklist.includes(val)) return;
    setBlacklist(l => [...l, val]);
    setBlacklistInput('');
  };

  if (!options || !params) {
    return <div style={{padding: 32, color: 'var(--text-mute)'}}>加载中...</div>;
  }

  const queue = metaStatus?.queue || [];
  const logs = metaStatus?.logs || [];
  const running = metaStatus?.running;
  const progress = metaStatus?.progress || 0;

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', gap: 0}}>
      {/* Sub-nav */}
      <div style={{display: 'flex', gap: 4, padding: '12px 16px 0', borderBottom: '1px solid var(--border)', flexShrink: 0}}>
        {TABS.map(([id, icon, label]) => (
          <button
            key={id}
            onClick={() => setSubPage(id)}
            style={{
              background: subPage === id ? 'var(--primary-bg)' : 'transparent',
              color: subPage === id ? 'var(--primary)' : 'var(--text-mute)',
              border: 'none',
              borderBottom: subPage === id ? '2px solid var(--primary)' : '2px solid transparent',
              borderRadius: '4px 4px 0 0',
              padding: '6px 14px',
              fontSize: 13,
              fontWeight: subPage === id ? 700 : 400,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 5,
            }}
          >
            <Icon id={icon} className="icon icon-sm" />{label}
          </button>
        ))}
        {running && (
          <div style={{marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--primary)'}}>
            <span className="loading" style={{width: 14, height: 14}} />
            {Math.round(progress)}% · {metaStatus?.message}
            <button className="btn btn-danger btn-sm" style={{padding: '2px 8px', minHeight: 24}} onClick={doStop}>停止</button>
          </div>
        )}
      </div>

      {/* Progress bar */}
      {running && (
        <div style={{height: 3, background: 'var(--border)', flexShrink: 0}}>
          <div style={{height: '100%', width: `${progress}%`, background: 'var(--primary)', transition: 'width .3s'}} />
        </div>
      )}

      {/* Content area */}
      <div style={{flex: 1, overflow: 'auto', padding: 16}}>
        {subPage === 'metadata' && (
          <MetadataPage
            options={options} metaTab={metaTab} setMetaTab={setMetaTab}
            apiSource={apiSource} setApiSource={setApiSource}
            apiId={apiId} setApiId={setApiId}
            linkPlatform={linkPlatform} setLinkPlatform={setLinkPlatform}
            linkUrl={linkUrl} setLinkUrl={setLinkUrl}
            fetchedMeta={fetchedMeta} fetchBusy={fetchBusy} fetchError={fetchError}
            doFetchById={doFetchById} doFetchByLink={doFetchByLink}
            applyMetaToParams={applyMetaToParams}
          />
        )}
        {subPage === 'params' && (
          <ParamsPage
            options={options} params={params} setParam={setParam}
            paramsBusy={paramsBusy} paramsMsg={paramsMsg}
            running={running} doRun={doRun} doAddQueue={doAddQueue} doStop={doStop}
            openBrowser={() => openBrowser(params.input_folder)}
          />
        )}
        {subPage === 'queue' && (
          <QueuePage
            queue={queue} running={running}
            doQueueStart={doQueueStart} doQueueClear={doQueueClear}
            doQueueRetry={doQueueRetry} doRemoveItem={doRemoveItem} doStop={doStop}
          />
        )}
        {subPage === 'logs' && (
          <LogsPage logs={logs} running={running} progress={progress} message={metaStatus?.message} />
        )}
        {subPage === 'admin' && (
          <AdminPage
            cookies={cookies} setCookies={setCookies} cookiesBusy={cookiesBusy}
            cookiesMsg={cookiesMsg} saveCookies={saveCookies}
            blacklist={blacklist} setBlacklist={setBlacklist}
            blacklistInput={blacklistInput} setBlacklistInput={setBlacklistInput}
            addBlacklistItem={addBlacklistItem} saveBlacklist={saveBlacklist}
            blacklistBusy={blacklistBusy}
          />
        )}
      </div>

      {/* File browser modal */}
      {browserOpen && (
        <FileBrowserModal
          data={browserData}
          onNav={(path) => openBrowser(path)}
          onSelect={selectFolder}
          onClose={() => setBrowserOpen(false)}
        />
      )}

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, background: 'var(--surface)',
          border: '1px solid var(--border)', borderRadius: 8, padding: '10px 18px',
          fontSize: 13, color: 'var(--text)', zIndex: 9999,
          boxShadow: '0 4px 20px rgba(0,0,0,.3)',
        }}>{toast}</div>
      )}
    </div>
  );
}

// ─── Metadata fetch page ──────────────────────────────────────────────────────
function MetadataPage({options, metaTab, setMetaTab, apiSource, setApiSource, apiId, setApiId, linkPlatform, setLinkPlatform, linkUrl, setLinkUrl, fetchedMeta, fetchBusy, fetchError, doFetchById, doFetchByLink, applyMetaToParams}) {
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 16}}>
      {/* Tabs */}
      <div className="glass" style={{padding: 16}}>
        <div style={{display: 'flex', gap: 8, marginBottom: 14}}>
          <button onClick={() => setMetaTab('id')} className={metaTab === 'id' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}>通过 ID 获取</button>
          <button onClick={() => setMetaTab('link')} className={metaTab === 'link' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}>通过分享链接</button>
        </div>

        {metaTab === 'id' && (
          <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
            <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
              <label style={{fontSize: 12, color: 'var(--text-mute)', minWidth: 56}}>平台</label>
              <select className="select" value={apiSource} onChange={e => setApiSource(e.target.value)} style={selectStyle}>
                {(options.api_sources || []).map(s => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
              <label style={{fontSize: 12, color: 'var(--text-mute)', minWidth: 56}}>专辑 ID</label>
              <input
                style={inputStyle} value={apiId} placeholder="输入专辑 ID..."
                onChange={e => setApiId(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doFetchById()}
              />
            </div>
            <button className="btn btn-primary" disabled={fetchBusy || !apiId.trim()} onClick={doFetchById} style={{alignSelf: 'flex-start'}}>
              {fetchBusy ? <span className="loading" /> : <Icon id="i-search" className="icon icon-sm" />}获取元数据
            </button>
          </div>
        )}

        {metaTab === 'link' && (
          <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
            <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
              <label style={{fontSize: 12, color: 'var(--text-mute)', minWidth: 56}}>平台</label>
              <select className="select" value={linkPlatform} onChange={e => setLinkPlatform(e.target.value)} style={selectStyle}>
                {(options.link_platforms || []).map(s => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
              <label style={{fontSize: 12, color: 'var(--text-mute)', minWidth: 56}}>链接</label>
              <input
                style={inputStyle} value={linkUrl} placeholder="粘贴分享链接..."
                onChange={e => setLinkUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doFetchByLink()}
              />
            </div>
            <button className="btn btn-primary" disabled={fetchBusy || !linkUrl.trim()} onClick={doFetchByLink} style={{alignSelf: 'flex-start'}}>
              {fetchBusy ? <span className="loading" /> : <Icon id="i-search" className="icon icon-sm" />}解析链接
            </button>
          </div>
        )}

        {fetchError && <div style={{marginTop: 10, color: 'var(--danger)', fontSize: 12}}>{fetchError}</div>}
      </div>

      {/* Result card */}
      {fetchedMeta && (
        <div className="glass" style={{padding: 16}}>
          <div style={{display: 'flex', gap: 16, alignItems: 'flex-start'}}>
            {fetchedMeta.cover_url && (
              <img src={fetchedMeta.cover_url} alt="封面" style={{width: 100, height: 100, objectFit: 'cover', borderRadius: 8, flexShrink: 0}} onError={e => e.target.style.display='none'} />
            )}
            <div style={{flex: 1, minWidth: 0}}>
              <div style={{fontWeight: 700, fontSize: 15, marginBottom: 6}}>{fetchedMeta.title || '（无标题）'}</div>
              {fetchedMeta.author && <div style={{fontSize: 13, color: 'var(--text-mute)'}}>作者：{fetchedMeta.author}</div>}
              {fetchedMeta.anchor && <div style={{fontSize: 13, color: 'var(--text-mute)'}}>演播：{fetchedMeta.anchor}</div>}
              {fetchedMeta.year && <div style={{fontSize: 13, color: 'var(--text-mute)'}}>年份：{fetchedMeta.year}</div>}
              {fetchedMeta.finished && <div style={{fontSize: 13, color: 'var(--text-mute)'}}>状态：{fetchedMeta.finished}</div>}
              {fetchedMeta.category_text && <div style={{fontSize: 13, color: 'var(--text-mute)'}}>分类：{fetchedMeta.category_text}</div>}
              {fetchedMeta.tags && fetchedMeta.tags.length > 0 && (
                <div style={{display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6}}>
                  {fetchedMeta.tags.slice(0, 10).map(t => (
                    <span key={t} style={{background: 'var(--primary-bg)', color: 'var(--primary)', fontSize: 11, padding: '1px 7px', borderRadius: 99}}>{t}</span>
                  ))}
                </div>
              )}
              {fetchedMeta.desc && (
                <div style={{marginTop: 8, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.7, maxHeight: 80, overflow: 'hidden'}}>
                  {fetchedMeta.desc}
                </div>
              )}
            </div>
          </div>
          <div style={{marginTop: 12}}>
            <button className="btn btn-primary btn-sm" onClick={applyMetaToParams}>
              <Icon id="i-check" className="icon icon-sm" />应用到参数
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Params form page ─────────────────────────────────────────────────────────
function ParamsPage({options, params, setParam, paramsBusy, paramsMsg, running, doRun, doAddQueue, doStop, openBrowser}) {
  const tagInput = useRef(null);

  const addTag = (val) => {
    val = val.trim();
    if (!val) return;
    const tags = params.album_tags || [];
    if (!tags.includes(val)) setParam('album_tags', [...tags, val]);
  };

  const removeTag = (t) => setParam('album_tags', (params.album_tags || []).filter(x => x !== t));

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      {/* Folder selector */}
      <div className="glass" style={{padding: 14}}>
        <FieldLabel>音频目录</FieldLabel>
        <div style={{display: 'flex', gap: 8}}>
          <input style={inputStyle} value={params.input_folder} onChange={e => setParam('input_folder', e.target.value)} placeholder="/path/to/audiobook/" />
          <button className="btn btn-ghost btn-sm" onClick={openBrowser}><Icon id="i-folder" className="icon icon-sm" />浏览</button>
        </div>
      </div>

      {/* Basic info */}
      <div className="glass" style={{padding: 14}}>
        <div style={{fontWeight: 700, fontSize: 13, marginBottom: 10}}>基本信息</div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10}}>
          <FormField label="专辑标题" value={params.title} onChange={v => setParam('title', v)} />
          <FormField label="副标题" value={params.subtitle} onChange={v => setParam('subtitle', v)} />
          <FormField label="原著作者" value={params.author} onChange={v => setParam('author', v)} />
          <FormField label="演播艺术家" value={params.anchor} onChange={v => setParam('anchor', v)} />
          <div>
            <FieldLabel>发布平台</FieldLabel>
            <select style={selectStyle} value={params.platform} onChange={e => setParam('platform', e.target.value)}>
              {(options.platforms || []).map(p => <option key={p}>{p}</option>)}
            </select>
          </div>
          <FormField label="发布年份" value={params.year} onChange={v => setParam('year', v)} />
          <div>
            <FieldLabel>专辑分类</FieldLabel>
            <select style={selectStyle} value={params.category} onChange={e => setParam('category', e.target.value)}>
              {(options.categories || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <FieldLabel>完结状态</FieldLabel>
            <select style={selectStyle} value={params.finished} onChange={e => setParam('finished', e.target.value)}>
              {(options.finished || []).map(f => <option key={f}>{f}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Audio format */}
      <div className="glass" style={{padding: 14}}>
        <div style={{fontWeight: 700, fontSize: 13, marginBottom: 10}}>音频格式</div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10}}>
          <div>
            <FieldLabel>目标格式</FieldLabel>
            <select style={selectStyle} value={params.target_format} onChange={e => setParam('target_format', e.target.value)}>
              {(options.target_formats || []).map(f => <option key={f}>{f}</option>)}
            </select>
          </div>
          <div>
            <FieldLabel>目标码率</FieldLabel>
            <select style={selectStyle} value={params.bitrate} onChange={e => setParam('bitrate', e.target.value)}>
              {(options.bitrates || []).map(b => <option key={b}>{b}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Series + tags */}
      <div className="glass" style={{padding: 14}}>
        <div style={{fontWeight: 700, fontSize: 13, marginBottom: 10}}>系列 & 标签</div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10}}>
          <FormField label="系列名称" value={params.series_name} onChange={v => setParam('series_name', v)} />
          <FormField label="系列编号" value={params.series_number} onChange={v => setParam('series_number', v)} />
          <FormField label="团队标识" value={params.team} onChange={v => setParam('team', v)} />
          <FormField label="封面路径" value={params.manual_cover_path} onChange={v => setParam('manual_cover_path', v)} placeholder="可选，本地封面路径" />
        </div>
        {/* tags */}
        <div style={{marginTop: 10}}>
          <FieldLabel>专辑标签</FieldLabel>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6}}>
            {(params.album_tags || []).map(t => (
              <span key={t} style={{background: 'var(--primary-bg)', color: 'var(--primary)', fontSize: 12, padding: '2px 8px', borderRadius: 99, cursor: 'pointer'}} onClick={() => removeTag(t)}>
                {t} ×
              </span>
            ))}
          </div>
          <div style={{display: 'flex', gap: 6}}>
            <input ref={tagInput} style={{...inputStyle, flex: 1}} placeholder="输入标签回车添加" onKeyDown={e => { if (e.key === 'Enter') { addTag(e.target.value); e.target.value = ''; } }} />
            <button className="btn btn-ghost btn-sm" onClick={() => { addTag(tagInput.current?.value || ''); if (tagInput.current) tagInput.current.value = ''; }}>添加</button>
          </div>
        </div>
      </div>

      {/* Description */}
      <div className="glass" style={{padding: 14}}>
        <FieldLabel>手动简介（留空则自动从 API 获取）</FieldLabel>
        <textarea
          style={{...inputStyle, height: 90, resize: 'vertical', fontFamily: 'inherit'}}
          value={params.manual_desc} onChange={e => setParam('manual_desc', e.target.value)}
          placeholder="专辑简介..."
        />
      </div>

      {/* Actions */}
      <div style={{display: 'flex', gap: 10, alignItems: 'center'}}>
        <button className="btn btn-primary" disabled={paramsBusy || running} onClick={doRun}>
          {paramsBusy ? <span className="loading" /> : <Icon id="i-play" className="icon icon-sm" />}立即处理
        </button>
        <button className="btn btn-ghost" disabled={paramsBusy} onClick={doAddQueue}>
          <Icon id="i-download" className="icon icon-sm" />加入队列
        </button>
        {running && (
          <button className="btn btn-danger btn-sm" onClick={doStop}>停止</button>
        )}
        {paramsMsg && <span style={{fontSize: 12, color: paramsMsg.includes('失败') ? 'var(--danger)' : 'var(--success)'}}>{paramsMsg}</span>}
      </div>
    </div>
  );
}

// ─── Queue page ────────────────────────────────────────────────────────────────
function QueuePage({queue, running, doQueueStart, doQueueClear, doQueueRetry, doRemoveItem, doStop}) {
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      <div style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
        <button className="btn btn-primary btn-sm" disabled={running || !queue.length} onClick={doQueueStart}>
          <Icon id="i-play" className="icon icon-sm" />开始队列
        </button>
        {running && <button className="btn btn-danger btn-sm" onClick={doStop}>停止</button>}
        <button className="btn btn-ghost btn-sm" onClick={doQueueRetry}>重试失败</button>
        <button className="btn btn-ghost btn-sm" onClick={doQueueClear}>清空队列</button>
        <span style={{fontSize: 12, color: 'var(--text-mute)', alignSelf: 'center'}}>共 {queue.length} 个任务</span>
      </div>
      {queue.length === 0 ? (
        <div style={{textAlign: 'center', padding: 40, color: 'var(--text-faint)', fontSize: 13}}>队列为空，在「处理参数」页加入任务</div>
      ) : (
        <div style={{display: 'flex', flexDirection: 'column', gap: 6}}>
          {queue.map(item => (
            <div key={item.id} className="glass" style={{padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10}}>
              <span style={{width: 8, height: 8, borderRadius: '50%', background: STATUS_COLORS[item.status] || 'var(--text-faint)', flexShrink: 0}} />
              <div style={{flex: 1, minWidth: 0}}>
                <div style={{fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>{item.title}</div>
                <div style={{fontSize: 11, color: 'var(--text-mute)'}}>
                  {item.author && `作者: ${item.author}`}{item.author && item.anchor ? ' · ' : ''}{item.anchor && `演播: ${item.anchor}`}
                </div>
                {item.error && <div style={{fontSize: 11, color: 'var(--danger)'}}>{item.error}</div>}
              </div>
              <span style={{fontSize: 11, color: STATUS_COLORS[item.status] || 'var(--text-faint)', flexShrink: 0}}>{STATUS_LABELS[item.status] || item.status}</span>
              {item.status !== 'processing' && (
                <button className="btn btn-ghost btn-sm" style={{padding: '2px 8px', minHeight: 24, fontSize: 11}} onClick={() => doRemoveItem(item.id)}>移除</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Logs page ────────────────────────────────────────────────────────────────
function LogsPage({logs, running, progress, message}) {
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({behavior: 'smooth'});
  }, [logs]);

  const levelColor = {error: 'var(--danger)', warning: 'var(--warning)', info: 'var(--text)', debug: 'var(--text-faint)'};

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', gap: 8}}>
      {running && <div style={{fontSize: 12, color: 'var(--primary)'}}>{Math.round(progress)}% · {message}</div>}
      <div style={{flex: 1, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 10, overflow: 'auto', minHeight: 300, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.8}}>
        {logs.length === 0 ? (
          <span style={{color: 'var(--text-faint)'}}>暂无日志，启动处理任务后将显示实时日志</span>
        ) : logs.map(log => (
          <div key={log.seq} style={{color: levelColor[log.level] || 'var(--text)'}}>
            {log.message}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ─── Admin / settings page ────────────────────────────────────────────────────
function AdminPage({cookies, setCookies, cookiesBusy, cookiesMsg, saveCookies, blacklist, setBlacklist, blacklistInput, setBlacklistInput, addBlacklistItem, saveBlacklist, blacklistBusy}) {
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 16}}>
      {/* Cookies */}
      <div className="glass" style={{padding: 14}}>
        <div style={{fontWeight: 700, fontSize: 13, marginBottom: 10}}>平台 Cookie 管理</div>
        <p style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 12}}>起点听书和网易云听书需要 Cookie 才能获取完整元数据。</p>
        <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
          <div>
            <FieldLabel>起点听书 Cookie</FieldLabel>
            <textarea style={{...inputStyle, height: 72, resize: 'vertical', fontFamily: 'monospace', fontSize: 11}} value={cookies.qidian || ''} onChange={e => setCookies(c => ({...c, qidian: e.target.value}))} placeholder="粘贴 Cookie 字符串..." />
          </div>
          <div>
            <FieldLabel>网易云听书 Cookie</FieldLabel>
            <textarea style={{...inputStyle, height: 72, resize: 'vertical', fontFamily: 'monospace', fontSize: 11}} value={cookies.netease || ''} onChange={e => setCookies(c => ({...c, netease: e.target.value}))} placeholder="粘贴 Cookie 字符串..." />
          </div>
          <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
            <button className="btn btn-primary btn-sm" disabled={cookiesBusy} onClick={saveCookies}>
              {cookiesBusy ? <span className="loading" /> : <Icon id="i-check" className="icon icon-sm" />}保存 Cookie
            </button>
            {cookiesMsg && <span style={{fontSize: 12, color: cookiesMsg.includes('失败') ? 'var(--danger)' : 'var(--success)'}}>{cookiesMsg}</span>}
          </div>
        </div>
      </div>

      {/* Tag blacklist */}
      <div className="glass" style={{padding: 14}}>
        <div style={{fontWeight: 700, fontSize: 13, marginBottom: 10}}>标签黑名单</div>
        <p style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 12}}>匹配黑名单正则的标签不会写入音频文件。</p>
        <div style={{display: 'flex', gap: 6, marginBottom: 8}}>
          <input style={{...inputStyle, flex: 1}} value={blacklistInput} onChange={e => setBlacklistInput(e.target.value)} placeholder="输入正则或关键词，回车添加" onKeyDown={e => e.key === 'Enter' && addBlacklistItem()} />
          <button className="btn btn-ghost btn-sm" onClick={addBlacklistItem}>添加</button>
        </div>
        <div style={{display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10}}>
          {blacklist.map(p => (
            <span key={p} style={{background: 'var(--surface-3)', fontSize: 12, padding: '2px 8px', borderRadius: 4, cursor: 'pointer', color: 'var(--text-mute)'}} onClick={() => setBlacklist(l => l.filter(x => x !== p))}>
              {p} ×
            </span>
          ))}
          {blacklist.length === 0 && <span style={{fontSize: 12, color: 'var(--text-faint)'}}>（暂无规则）</span>}
        </div>
        <button className="btn btn-primary btn-sm" disabled={blacklistBusy} onClick={saveBlacklist}>
          {blacklistBusy ? <span className="loading" /> : <Icon id="i-check" className="icon icon-sm" />}保存黑名单
        </button>
      </div>
    </div>
  );
}

// ─── File browser modal ───────────────────────────────────────────────────────
function FileBrowserModal({data, onNav, onSelect, onClose}) {
  return (
    <div style={{position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center'}} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{background: 'var(--surface)', borderRadius: 12, width: '90%', maxWidth: 560, maxHeight: '70vh', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
        <div style={{padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
          <span style={{fontWeight: 700}}>选择音频目录</span>
          <button className="btn btn-ghost btn-sm" onClick={onClose}><Icon id="i-close" className="icon icon-sm" /></button>
        </div>
        {data && (
          <>
            <div style={{padding: '8px 16px', fontSize: 11, color: 'var(--text-faint)', borderBottom: '1px solid var(--border)'}}>
              当前：{data.current}
            </div>
            <div style={{flex: 1, overflow: 'auto', padding: 8}}>
              {(data.items || []).map(item => (
                <div key={item.path} style={{display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13, color: item.has_audio ? 'var(--text)' : 'var(--text-mute)'}} onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-3)'} onMouseLeave={e => e.currentTarget.style.background = ''} onClick={() => onNav(item.path)}>
                  <Icon id="i-folder" className="icon icon-sm" style={{flexShrink: 0}} />
                  <span style={{flex: 1}}>{item.name}</span>
                  {item.has_audio && <span style={{fontSize: 10, color: 'var(--success)', background: 'var(--success-bg)', padding: '1px 6px', borderRadius: 99}}>有音频</span>}
                </div>
              ))}
            </div>
            <div style={{padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
              <button className="btn btn-primary btn-sm" onClick={() => onSelect(data.current)}>选择此目录</button>
            </div>
          </>
        )}
        {!data && <div style={{padding: 32, textAlign: 'center', color: 'var(--text-faint)'}}>加载中...</div>}
      </div>
    </div>
  );
}

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
function FieldLabel({children}) {
  return <div style={{fontSize: 11, color: 'var(--text-mute)', marginBottom: 4, fontWeight: 600}}>{children}</div>;
}

function FormField({label, value, onChange, placeholder}) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <input style={inputStyle} value={value || ''} onChange={e => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}

const inputStyle = {
  width: '100%', background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13, outline: 'none',
  boxSizing: 'border-box',
};
const selectStyle = {
  width: '100%', background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13, outline: 'none',
};
