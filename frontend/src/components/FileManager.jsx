import {useEffect, useRef, useState} from 'react';
import {Icon} from './Icons.jsx';

// ============ 工具函数 ============

function api(path, opts) {
  return fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts}).then(r => r.json());
}

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(1) + ' GB';
  if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

// 客户端模拟模板渲染（仅用于实时预览前3条）
function simulateTemplate(template, bookMeta, fileName, index) {
  const stem = fileName.replace(/\.[^.]+$/, '');
  const ext = (fileName.match(/\.([^.]+)$/) || ['', ''])[1].toLowerCase();
  const idx = index + 1;
  const vars = {
    book_title: bookMeta.book_title || '',
    author: bookMeta.author || '',
    narrator: bookMeta.narrator || '',
    category: bookMeta.category || '',
    series: bookMeta.series || '',
    volume: bookMeta.volume || '',
    chapter_index: String(idx),
    chapter_index_2: String(idx).padStart(2, '0'),
    chapter_index_3: String(idx).padStart(3, '0'),
    chapter_index_4: String(idx).padStart(4, '0'),
    chapter_title: stem,
    chapter_full: String(idx).padStart(3, '0') + '-' + stem,
    name: stem,
    ext: ext,
    date: new Date().toISOString().slice(0, 10).replace(/-/g, ''),
  };
  let result = template;
  for (const [k, v] of Object.entries(vars)) {
    result = result.replaceAll(`{${k}}`, v);
  }
  return result;
}

// ============ 主组件 ============

const TABS = [
  ['scan',      'i-folder',  '文件扫描'],
  ['rename',    'i-edit',    '智能重命名'],
  ['templates', 'i-file',    '模板管理'],
  ['history',   'i-clock',   '历史回滚'],
  ['settings',  'i-settings','配置设置'],
];

export function FileManagerPage() {
  const [tab, setTab] = useState('scan');

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 0, height: '100%'}}>
      {/* Tab 栏 */}
      <div style={{display: 'flex', gap: 2, borderBottom: '1px solid var(--border)', paddingBottom: 0, flexShrink: 0, overflowX: 'auto'}}>
        {TABS.map(([id, icon, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '10px 16px',
              background: 'none', border: 'none', cursor: 'pointer',
              color: tab === id ? 'var(--primary)' : 'var(--text-mute)',
              borderBottom: tab === id ? '2px solid var(--primary)' : '2px solid transparent',
              fontWeight: tab === id ? 600 : 400, fontSize: 13.5, whiteSpace: 'nowrap',
            }}
          >
            <Icon id={icon} className="icon icon-sm" />{label}
          </button>
        ))}
      </div>

      {/* Tab 内容 */}
      <div style={{flex: 1, overflow: 'auto', padding: '16px 0'}}>
        {tab === 'scan'      && <ScanTab />}
        {tab === 'rename'    && <RenameTab onGotoHistory={() => setTab('history')} />}
        {tab === 'templates' && <TemplatesTab />}
        {tab === 'history'   && <HistoryTab />}
        {tab === 'settings'  && <SettingsTab />}
      </div>
    </div>
  );
}

// ============ Tab 1: 文件扫描 ============

function ScanTab() {
  const [root, setRoot] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('name');
  const [expanded, setExpanded] = useState({});
  const [selected, setSelected] = useState({});

  async function doScan() {
    setLoading(true); setError('');
    try {
      const url = '/api/file-manager/scan' + (root ? `?root=${encodeURIComponent(root)}` : '');
      const r = await api(url);
      if (!r.ok) throw new Error(r.error);
      setResult(r);
      setSelected({});
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const books = result ? [...result.books] : [];

  // 过滤
  const filtered = books.filter(b => !search || b.folder_name.toLowerCase().includes(search.toLowerCase()));

  // 排序
  filtered.sort((a, b) => {
    if (sortBy === 'name') return a.folder_name.localeCompare(b.folder_name);
    if (sortBy === 'files') return b.file_count - a.file_count;
    if (sortBy === 'size') return b.total_size - a.total_size;
    return 0;
  });

  function toggleBook(fp) {
    setExpanded(p => ({...p, [fp]: !p[fp]}));
  }

  function toggleSelect(fp) {
    setSelected(p => ({...p, [fp]: !p[fp]}));
  }

  function toggleAll() {
    if (Object.values(selected).filter(Boolean).length === filtered.length) {
      setSelected({});
    } else {
      const s = {};
      filtered.forEach(b => { s[b.folder_path] = true; });
      setSelected(s);
    }
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  // 格式标签（收集所有 ext）
  function getExts(book) {
    const exts = [...new Set(book.files.map(f => f.ext))];
    return exts;
  }

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      {/* 搜索目录 */}
      <div className="glass glass-pad" style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap'}}>
        <input
          value={root} onChange={e => setRoot(e.target.value)}
          placeholder="根目录路径（留空使用下载目录）"
          style={{flex: 1, minWidth: 200, padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13}}
        />
        <button className="btn btn-primary" onClick={doScan} disabled={loading}>
          {loading ? <span className="loading" /> : <Icon id="i-folder" className="icon icon-sm" />}扫描
        </button>
      </div>

      {error && <div style={{color: 'var(--danger)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--danger)'}}>{error}</div>}

      {result && (
        <>
          {/* 统计 */}
          <div style={{display: 'flex', gap: 16, flexWrap: 'wrap'}}>
            {[
              ['共', result.total_books + ' 本书'],
              ['文件', result.total_files + ' 个'],
              ['大小', result.total_size_fmt],
              ['目录', result.root],
            ].map(([k, v]) => (
              <div key={k} style={{background: 'var(--bg-0)', borderRadius: 6, padding: '6px 14px', border: '1px solid var(--border)', fontSize: 13}}>
                <span style={{color: 'var(--text-mute)'}}>{k}：</span>
                <span style={{color: 'var(--text)', fontWeight: 600}}>{v}</span>
              </div>
            ))}
          </div>

          {/* 过滤 + 排序 */}
          <div style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap'}}>
            <input
              value={search} onChange={e => setSearch(e.target.value)}
              placeholder="搜索书名..."
              style={{flex: 1, minWidth: 160, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13}}
            />
            <span style={{fontSize: 12.5, color: 'var(--text-mute)'}}>排序：</span>
            {[['name','书名'],['files','文件数'],['size','大小']].map(([v,l]) => (
              <button key={v} onClick={() => setSortBy(v)}
                style={{padding: '5px 10px', borderRadius: 5, border: '1px solid var(--border)', background: sortBy === v ? 'var(--primary)' : 'var(--bg-0)', color: sortBy === v ? '#fff' : 'var(--text)', fontSize: 12.5, cursor: 'pointer'}}>
                {l}
              </button>
            ))}
          </div>

          {/* 批量操作 */}
          {selectedCount > 0 && (
            <div style={{display: 'flex', gap: 8, alignItems: 'center', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--primary)'}}>
              <span style={{fontSize: 13, color: 'var(--primary)'}}>已选 {selectedCount} 本</span>
              <span style={{flex: 1}} />
            </div>
          )}

          {/* 全选 */}
          <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
            <input type="checkbox" checked={selectedCount === filtered.length && filtered.length > 0} onChange={toggleAll} id="select-all" />
            <label htmlFor="select-all" style={{fontSize: 13, color: 'var(--text-mute)', cursor: 'pointer'}}>全选 ({filtered.length})</label>
          </div>

          {/* 书籍列表 */}
          <div style={{display: 'flex', flexDirection: 'column', gap: 6}}>
            {filtered.length === 0 && <div style={{color: 'var(--text-mute)', fontSize: 13, padding: 12}}>没有找到匹配的书籍</div>}
            {filtered.map(book => (
              <div key={book.folder_path} className="glass" style={{borderRadius: 8, overflow: 'hidden'}}>
                {/* 书籍行 */}
                <div style={{display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', cursor: 'pointer'}}
                  onClick={() => toggleBook(book.folder_path)}>
                  <input type="checkbox" checked={!!selected[book.folder_path]}
                    onClick={e => e.stopPropagation()}
                    onChange={() => toggleSelect(book.folder_path)} />
                  <Icon id="i-folder" className="icon icon-sm" style={{color: 'var(--primary)'}} />
                  <span style={{flex: 1, fontWeight: 600, fontSize: 13.5}}>{book.folder_name}</span>
                  <div style={{display: 'flex', gap: 6, flexWrap: 'wrap'}}>
                    {getExts(book).map(ext => (
                      <span key={ext} style={{padding: '1px 7px', borderRadius: 10, background: 'var(--bg-1)', fontSize: 11, color: 'var(--text-mute)', border: '1px solid var(--border)'}}>
                        {ext}
                      </span>
                    ))}
                  </div>
                  <span style={{fontSize: 12.5, color: 'var(--text-mute)', marginLeft: 4}}>{book.file_count} 个文件</span>
                  <span style={{fontSize: 12.5, color: 'var(--text-mute)'}}>{book.total_size_fmt}</span>
                  {book.total_duration_fmt && <span style={{fontSize: 12.5, color: 'var(--text-mute)'}}>{book.total_duration_fmt}</span>}
                  <Icon id={expanded[book.folder_path] ? 'i-arrow-left' : 'i-arrow-right'} className="icon icon-sm" style={{opacity: 0.5}} />
                </div>

                {/* 展开文件列表 */}
                {expanded[book.folder_path] && (
                  <div style={{borderTop: '1px solid var(--border)'}}>
                    <table style={{width: '100%', borderCollapse: 'collapse', fontSize: 12.5}}>
                      <thead>
                        <tr style={{background: 'var(--bg-1)', color: 'var(--text-mute)'}}>
                          {['文件名','大小','时长','修改时间'].map(h => (
                            <th key={h} style={{padding: '6px 12px', textAlign: 'left', fontWeight: 500}}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {book.files.map(f => (
                          <tr key={f.path} style={{borderTop: '1px solid var(--border)'}}>
                            <td style={{padding: '6px 12px', color: 'var(--text)', wordBreak: 'break-all'}}>{f.name}</td>
                            <td style={{padding: '6px 12px', color: 'var(--text-mute)', whiteSpace: 'nowrap'}}>{f.size_fmt}</td>
                            <td style={{padding: '6px 12px', color: 'var(--text-mute)', whiteSpace: 'nowrap'}}>{f.duration_fmt || '-'}</td>
                            <td style={{padding: '6px 12px', color: 'var(--text-mute)', whiteSpace: 'nowrap'}}>{f.mtime ? f.mtime.slice(0, 10) : '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {!result && !loading && (
        <div className="glass glass-pad" style={{color: 'var(--text-mute)', textAlign: 'center', padding: 40}}>
          <Icon id="i-folder" style={{width: 48, height: 48, opacity: 0.3}} />
          <div style={{marginTop: 12, fontSize: 14}}>点击"扫描"按钮开始扫描有声书目录</div>
        </div>
      )}
    </div>
  );
}

// ============ Tab 2: 智能重命名 ============

function RenameTab({onGotoHistory}) {
  const [step, setStep] = useState(1);
  const [folderPath, setFolderPath] = useState('');
  const [folderFiles, setFolderFiles] = useState([]);
  const [bookMeta, setBookMeta] = useState({book_title: '', author: '', narrator: '', category: '', series: '', volume: ''});
  const [template, setTemplate] = useState('{chapter_index_3}-{chapter_title}.{ext}');
  const [templates, setTemplates] = useState([]);
  const [previews, setPreviews] = useState([]);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [scrapeMode, setScrapeMode] = useState('');
  const [scrapeInput, setScrapeInput] = useState({api_source: '', api_id: '', link_url: '', link_platform: '起点听书'});

  useEffect(() => {
    api('/api/file-manager/templates').then(r => {
      if (r.ok) setTemplates(r.templates);
    });
  }, []);

  async function browseFolderPath() {
    try {
      const r = await api(`/api/meta/browse?path=${encodeURIComponent(folderPath)}`);
      if (r.ok && r.browser) {
        // 显示当前路径下文件
        setFolderPath(r.browser.current || folderPath);
      }
    } catch {}
  }

  async function loadFolderFiles() {
    if (!folderPath) return;
    setLoading(true); setError('');
    try {
      const r = await api(`/api/file-manager/scan?root=${encodeURIComponent(folderPath)}`);
      if (!r.ok) throw new Error(r.error);
      // 只取直接子文件（或根目录文件）
      const allFiles = r.books.flatMap(b => b.files);
      setFolderFiles(allFiles);
      if (allFiles.length > 0) setStep(2);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function doAiAnalyze() {
    if (!folderFiles.length) return;
    setAiLoading(true); setError('');
    try {
      const r = await api('/api/file-manager/ai-analyze', {
        method: 'POST',
        body: JSON.stringify({file_names: folderFiles.map(f => f.name)}),
      });
      if (!r.ok) throw new Error(r.error);
      const res = r.result;
      setBookMeta(prev => ({
        ...prev,
        book_title: res.book_title || prev.book_title,
        author: res.author || prev.author,
        narrator: res.narrator || prev.narrator,
        category: res.category || prev.category,
        series: res.series || prev.series,
        volume: res.volume || prev.volume,
      }));
    } catch (e) {
      setError('AI 识别失败: ' + e.message);
    } finally {
      setAiLoading(false);
    }
  }

  async function doScrape() {
    setLoading(true); setError('');
    try {
      const r = await api('/api/file-manager/scrape', {
        method: 'POST',
        body: JSON.stringify(scrapeInput),
      });
      if (!r.ok) throw new Error(r.error);
      const meta = r.metadata || {};
      setBookMeta(prev => ({
        ...prev,
        book_title: meta.title || meta.book_title || prev.book_title,
        author: meta.author || prev.author,
        narrator: meta.narrator || meta.anchor || prev.narrator,
        category: meta.category || prev.category,
      }));
      setScrapeMode('');
    } catch (e) {
      setError('刮削失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  }

  async function doPreview() {
    if (!folderPath || !template) return;
    setLoading(true); setError('');
    try {
      const r = await api('/api/file-manager/rename-preview', {
        method: 'POST',
        body: JSON.stringify({folder_path: folderPath, template, book_meta: bookMeta}),
      });
      if (!r.ok) throw new Error(r.error);
      setPreviews(r.previews);
      setStep(4);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function doApply() {
    if (!previews.length) return;
    setLoading(true); setError('');
    try {
      const r = await api('/api/file-manager/rename-apply', {
        method: 'POST',
        body: JSON.stringify({previews, note}),
      });
      if (!r.ok) throw new Error(r.error);
      alert(`重命名完成：成功 ${r.success} 个，失败 ${r.failed} 个`);
      onGotoHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  // 客户端实时预览（前3条）
  const livePreview = folderFiles.slice(0, 3).map((f, i) =>
    simulateTemplate(template, bookMeta, f.name, i)
  );

  const STEP_LABELS = ['选择文件夹', '填写元数据', '选择模板', '预览确认'];

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 14}}>
      {/* 步骤指示 */}
      <div style={{display: 'flex', gap: 0, alignItems: 'center'}}>
        {STEP_LABELS.map((label, i) => (
          <div key={i} style={{display: 'flex', alignItems: 'center'}}>
            <div
              onClick={() => i + 1 <= step && setStep(i + 1)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                borderRadius: 20, fontSize: 13, cursor: i + 1 <= step ? 'pointer' : 'default',
                background: step === i + 1 ? 'var(--primary)' : i + 1 < step ? 'var(--bg-1)' : 'var(--bg-0)',
                color: step === i + 1 ? '#fff' : i + 1 < step ? 'var(--text)' : 'var(--text-mute)',
                border: '1px solid var(--border)',
              }}
            >
              <span style={{
                width: 20, height: 20, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: step === i + 1 ? 'rgba(255,255,255,0.3)' : 'var(--border)', fontSize: 11, fontWeight: 700,
              }}>{i + 1}</span>
              {label}
            </div>
            {i < 3 && <div style={{width: 16, height: 1, background: 'var(--border)'}} />}
          </div>
        ))}
      </div>

      {error && <div style={{color: 'var(--danger)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--danger)'}}>{error}</div>}

      {/* 步骤1：文件夹 */}
      {step === 1 && (
        <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 10}}>
          <div style={{fontWeight: 600, fontSize: 14}}>选择目标文件夹</div>
          <div style={{display: 'flex', gap: 8}}>
            <input
              value={folderPath} onChange={e => setFolderPath(e.target.value)}
              placeholder="输入文件夹路径..."
              style={{flex: 1, padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13}}
            />
            <button className="btn btn-ghost" onClick={browseFolderPath}>浏览</button>
            <button className="btn btn-primary" onClick={loadFolderFiles} disabled={loading || !folderPath}>
              {loading ? <span className="loading" /> : '确认'}
            </button>
          </div>
          <div style={{fontSize: 12.5, color: 'var(--text-mute)'}}>输入包含音频文件的文件夹路径，支持 mp3、m4a、m4b、flac 等格式</div>
        </div>
      )}

      {/* 步骤2：元数据 */}
      {step >= 2 && step <= 3 && (
        <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
          <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
            <div style={{fontWeight: 600, fontSize: 14}}>填写书籍元数据</div>
            <div style={{display: 'flex', gap: 8}}>
              <button className="btn btn-ghost btn-sm" onClick={doAiAnalyze} disabled={aiLoading}>
                {aiLoading ? <span className="loading" /> : <Icon id="i-bolt" className="icon icon-sm" />}AI 识别
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setScrapeMode(scrapeMode ? '' : 'scrape')}>
                <Icon id="i-search" className="icon icon-sm" />从刮削获取
              </button>
            </div>
          </div>

          {scrapeMode === 'scrape' && (
            <div style={{background: 'var(--bg-1)', borderRadius: 8, padding: 12, display: 'flex', flexDirection: 'column', gap: 8}}>
              <div style={{fontSize: 13, fontWeight: 500}}>刮削搜索</div>
              <div style={{display: 'flex', gap: 6, flexWrap: 'wrap'}}>
                <input placeholder="API Source" value={scrapeInput.api_source}
                  onChange={e => setScrapeInput(p => ({...p, api_source: e.target.value}))}
                  style={{flex: 1, minWidth: 100, padding: '6px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 12.5}} />
                <input placeholder="API ID" value={scrapeInput.api_id}
                  onChange={e => setScrapeInput(p => ({...p, api_id: e.target.value}))}
                  style={{flex: 1, minWidth: 100, padding: '6px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 12.5}} />
              </div>
              <input placeholder="或输入链接 URL" value={scrapeInput.link_url}
                onChange={e => setScrapeInput(p => ({...p, link_url: e.target.value}))}
                style={{padding: '6px 8px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 12.5}} />
              <button className="btn btn-primary btn-sm" onClick={doScrape} disabled={loading}>
                {loading ? <span className="loading" /> : '获取元数据'}
              </button>
            </div>
          )}

          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10}}>
            {[
              ['book_title', '书名'],
              ['author', '作者'],
              ['narrator', '主播'],
              ['category', '分类'],
              ['series', '系列'],
              ['volume', '卷号'],
            ].map(([k, label]) => (
              <div key={k}>
                <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>{label}</div>
                <input value={bookMeta[k]} onChange={e => setBookMeta(p => ({...p, [k]: e.target.value}))}
                  placeholder={label}
                  style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
              </div>
            ))}
          </div>

          {step === 2 && (
            <button className="btn btn-primary" onClick={() => setStep(3)} style={{alignSelf: 'flex-start'}}>
              下一步：选择模板
            </button>
          )}
        </div>
      )}

      {/* 步骤3：模板 */}
      {step === 3 && (
        <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
          <div style={{fontWeight: 600, fontSize: 14}}>选择重命名模板</div>

          <div>
            <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>选择预设模板</div>
            <select value={template} onChange={e => setTemplate(e.target.value)}
              style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13}}>
              {templates.map(t => (
                <option key={t.id} value={t.template}>{t.name} — {t.template}</option>
              ))}
            </select>
          </div>

          <div>
            <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>自定义模板</div>
            <input value={template} onChange={e => setTemplate(e.target.value)}
              placeholder="{chapter_index_3}-{chapter_title}.{ext}"
              style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box', fontFamily: 'monospace'}} />
          </div>

          <div style={{fontSize: 12, color: 'var(--text-mute)', lineHeight: 1.7}}>
            可用变量：{'{book_title}'} {'{author}'} {'{narrator}'} {'{chapter_index_3}'} {'{chapter_title}'} {'{ext}'} {'{date}'}
          </div>

          {/* 实时预览 */}
          {folderFiles.length > 0 && (
            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 6}}>实时预览（前3个文件）</div>
              {livePreview.map((name, i) => (
                <div key={i} style={{display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, marginBottom: 4}}>
                  <span style={{color: 'var(--text-mute)', flex: '0 0 auto'}}>{folderFiles[i]?.name}</span>
                  <span style={{color: 'var(--text-mute)'}}>→</span>
                  <span style={{color: 'var(--primary)'}}>{name}</span>
                </div>
              ))}
            </div>
          )}

          <div style={{display: 'flex', gap: 8}}>
            <button className="btn btn-ghost" onClick={() => setStep(2)}>上一步</button>
            <button className="btn btn-primary" onClick={doPreview} disabled={loading || !template}>
              {loading ? <span className="loading" /> : '生成完整预览'}
            </button>
          </div>
        </div>
      )}

      {/* 步骤4：预览确认 */}
      {step === 4 && (
        <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
          <div style={{fontWeight: 600, fontSize: 14}}>预览确认</div>

          <div style={{overflowX: 'auto'}}>
            <table style={{width: '100%', borderCollapse: 'collapse', fontSize: 12.5}}>
              <thead>
                <tr style={{background: 'var(--bg-1)'}}>
                  <th style={{padding: '7px 10px', textAlign: 'left', color: 'var(--text-mute)', fontWeight: 500}}>原文件名</th>
                  <th style={{padding: '7px 10px', textAlign: 'left', color: 'var(--text-mute)', fontWeight: 500}}>新文件名</th>
                  <th style={{padding: '7px 10px', textAlign: 'left', color: 'var(--text-mute)', fontWeight: 500}}>状态</th>
                </tr>
              </thead>
              <tbody>
                {previews.map((p, i) => (
                  <tr key={i} style={{borderTop: '1px solid var(--border)', background: p.conflict ? 'rgba(255,0,0,0.04)' : 'transparent'}}>
                    <td style={{padding: '6px 10px', color: 'var(--text-mute)', wordBreak: 'break-all'}}>{p.original_name}</td>
                    <td style={{padding: '6px 10px', color: p.conflict ? 'var(--danger)' : 'var(--primary)', wordBreak: 'break-all'}}>{p.new_name}</td>
                    <td style={{padding: '6px 10px', whiteSpace: 'nowrap'}}>
                      {p.conflict
                        ? <span style={{color: 'var(--danger)', fontSize: 11}}>冲突</span>
                        : p.original_name === p.new_name
                        ? <span style={{color: 'var(--text-mute)', fontSize: 11}}>未变</span>
                        : <span style={{color: 'var(--success)', fontSize: 11}}>正常</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>操作备注</div>
            <input value={note} onChange={e => setNote(e.target.value)}
              placeholder="可选：记录此次操作的备注"
              style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
          </div>

          <div style={{display: 'flex', gap: 8}}>
            <button className="btn btn-ghost" onClick={() => setStep(3)}>上一步</button>
            <button className="btn btn-primary" onClick={doApply} disabled={loading}>
              {loading ? <span className="loading" /> : <Icon id="i-check" className="icon icon-sm" />}执行重命名
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ============ Tab 3: 模板管理 ============

function TemplatesTab() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [editItem, setEditItem] = useState(null); // null | {id, name, template}
  const [deleteId, setDeleteId] = useState(null);
  const [importJson, setImportJson] = useState('');
  const [showImport, setShowImport] = useState(false);

  const DEFAULT_TEMPLATES = [
    {id: 't1', name: '章节序号-标题', template: '{chapter_index_3}-{chapter_title}.{ext}'},
    {id: 't2', name: '书名-序号-标题', template: '{book_title}-{chapter_index_3}-{chapter_title}.{ext}'},
    {id: 't3', name: '作者-书名-序号', template: '[{author}]{book_title}-{chapter_index_3}.{ext}'},
    {id: 't4', name: '纯序号', template: '{chapter_index_4}.{ext}'},
    {id: 't5', name: '完整章节', template: '第{chapter_index_3}章 {chapter_title}.{ext}'},
  ];

  useEffect(() => { loadTemplates(); }, []);

  async function loadTemplates() {
    setLoading(true);
    try {
      const r = await api('/api/file-manager/templates');
      if (r.ok) setTemplates(r.templates);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function saveTemplates(tpls) {
    setLoading(true);
    try {
      const r = await api('/api/file-manager/templates', {method: 'POST', body: JSON.stringify({templates: tpls})});
      if (!r.ok) throw new Error(r.error);
      setTemplates(r.templates);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  function startEdit(item) {
    setEditItem({...item});
  }

  function startNew() {
    setEditItem({id: 'new-' + Date.now(), name: '', template: ''});
  }

  async function commitEdit() {
    if (!editItem.name || !editItem.template) return;
    const exists = templates.find(t => t.id === editItem.id);
    let updated;
    if (exists) {
      updated = templates.map(t => t.id === editItem.id ? editItem : t);
    } else {
      updated = [...templates, editItem];
    }
    await saveTemplates(updated);
    setEditItem(null);
  }

  async function doDelete(id) {
    await saveTemplates(templates.filter(t => t.id !== id));
    setDeleteId(null);
  }

  function doExport() {
    const json = JSON.stringify(templates, null, 2);
    navigator.clipboard.writeText(json).then(() => alert('已复制到剪贴板'));
  }

  async function doImport() {
    try {
      const parsed = JSON.parse(importJson);
      if (!Array.isArray(parsed)) throw new Error('必须是数组');
      await saveTemplates(parsed);
      setShowImport(false);
      setImportJson('');
    } catch (e) {
      setError('导入失败：' + e.message);
    }
  }

  async function resetDefaults() {
    if (!confirm('确定要重置为默认模板吗？当前自定义模板将丢失')) return;
    await saveTemplates(DEFAULT_TEMPLATES);
  }

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      {error && <div style={{color: 'var(--danger)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--danger)'}}>{error}</div>}

      <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 10}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
          <span style={{fontWeight: 600, fontSize: 14, flex: 1}}>模板列表</span>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowImport(!showImport)}>导入</button>
          <button className="btn btn-ghost btn-sm" onClick={doExport}>导出</button>
          <button className="btn btn-ghost btn-sm" onClick={resetDefaults}>重置默认</button>
          <button className="btn btn-primary btn-sm" onClick={startNew}><Icon id="i-plus" className="icon icon-sm" />新建</button>
        </div>

        {loading && <div style={{textAlign: 'center', padding: 20}}><span className="loading" /></div>}

        {showImport && (
          <div style={{background: 'var(--bg-1)', borderRadius: 8, padding: 12, display: 'flex', flexDirection: 'column', gap: 8}}>
            <div style={{fontSize: 13, fontWeight: 500}}>粘贴 JSON 导入</div>
            <textarea value={importJson} onChange={e => setImportJson(e.target.value)}
              rows={6} placeholder='[{"id":"t1","name":"模板名","template":"..."}]'
              style={{padding: 8, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 12, fontFamily: 'monospace', resize: 'vertical'}} />
            <div style={{display: 'flex', gap: 8}}>
              <button className="btn btn-ghost btn-sm" onClick={() => setShowImport(false)}>取消</button>
              <button className="btn btn-primary btn-sm" onClick={doImport}>导入</button>
            </div>
          </div>
        )}

        <div style={{display: 'flex', flexDirection: 'column', gap: 6}}>
          {templates.map(t => (
            <div key={t.id} style={{display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'var(--bg-0)', borderRadius: 7, border: '1px solid var(--border)'}}>
              <div style={{flex: 1}}>
                <div style={{fontWeight: 500, fontSize: 13.5}}>{t.name}</div>
                <div style={{fontFamily: 'monospace', fontSize: 12, color: 'var(--text-mute)', marginTop: 2}}>{t.template}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => startEdit(t)}>编辑</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setDeleteId(t.id)} style={{color: 'var(--danger)'}}>删除</button>
            </div>
          ))}
          {templates.length === 0 && !loading && (
            <div style={{color: 'var(--text-mute)', textAlign: 'center', padding: 20, fontSize: 13}}>暂无模板，点击"新建"添加</div>
          )}
        </div>
      </div>

      {/* 编辑弹窗 */}
      {editItem && (
        <div style={{position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000}}>
          <div style={{background: 'var(--panel)', borderRadius: 12, padding: 24, width: 480, maxWidth: '90vw', display: 'flex', flexDirection: 'column', gap: 14}}>
            <div style={{fontWeight: 600, fontSize: 16}}>{templates.find(t => t.id === editItem.id) ? '编辑模板' : '新建模板'}</div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>模板名称</div>
              <input value={editItem.name} onChange={e => setEditItem(p => ({...p, name: e.target.value}))}
                placeholder="如：章节序号-标题"
                style={{width: '100%', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
            </div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>模板字符串</div>
              <input value={editItem.template} onChange={e => setEditItem(p => ({...p, template: e.target.value}))}
                placeholder="{chapter_index_3}-{chapter_title}.{ext}"
                style={{width: '100%', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box', fontFamily: 'monospace'}} />
            </div>

            <div style={{fontSize: 12, color: 'var(--text-mute)', lineHeight: 1.7, background: 'var(--bg-1)', borderRadius: 6, padding: 10}}>
              <strong>可用变量：</strong><br />
              书籍：{'{book_title}'} {'{author}'} {'{narrator}'} {'{category}'} {'{series}'} {'{volume}'}<br />
              章节：{'{chapter_index}'} {'{chapter_index_2}'} {'{chapter_index_3}'} {'{chapter_index_4}'} {'{chapter_title}'}<br />
              文件：{'{name}'} {'{ext}'} {'{date}'}
            </div>

            <div style={{display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
              <button className="btn btn-ghost" onClick={() => setEditItem(null)}>取消</button>
              <button className="btn btn-primary" onClick={commitEdit} disabled={!editItem.name || !editItem.template}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteId && (
        <div style={{position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000}}>
          <div style={{background: 'var(--panel)', borderRadius: 12, padding: 24, width: 360, maxWidth: '90vw', display: 'flex', flexDirection: 'column', gap: 14}}>
            <div style={{fontWeight: 600, fontSize: 15}}>确认删除</div>
            <div style={{fontSize: 13, color: 'var(--text-mute)'}}>确定要删除此模板吗？此操作不可撤销。</div>
            <div style={{display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
              <button className="btn btn-ghost" onClick={() => setDeleteId(null)}>取消</button>
              <button className="btn btn-primary" style={{background: 'var(--danger)'}} onClick={() => doDelete(deleteId)}>删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============ Tab 4: 历史回滚 ============

function HistoryTab() {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState({});
  const [rollbackId, setRollbackId] = useState(null);
  const [rolling, setRolling] = useState(false);

  useEffect(() => { loadHistory(); }, []);

  async function loadHistory() {
    setLoading(true);
    try {
      const r = await api('/api/file-manager/history');
      if (r.ok) setHistory(r.history);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function doRollback() {
    if (!rollbackId) return;
    setRolling(true);
    try {
      const r = await api('/api/file-manager/rollback', {method: 'POST', body: JSON.stringify({history_id: rollbackId})});
      if (!r.ok) throw new Error(r.error);
      alert(`回滚完成：成功 ${r.success} 个，失败 ${r.failed} 个`);
      setRollbackId(null);
      loadHistory();
    } catch (e) {
      setError('回滚失败：' + e.message);
    } finally {
      setRolling(false);
    }
  }

  const successOps = (item) => item.ops.filter(op => op.status === 'success').length;

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      {error && <div style={{color: 'var(--danger)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--danger)'}}>{error}</div>}

      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
        <span style={{fontSize: 13, color: 'var(--text-mute)'}}>共 {history.length} 条操作记录</span>
        <button className="btn btn-ghost btn-sm" onClick={loadHistory} disabled={loading}>
          <Icon id="i-refresh" className="icon icon-sm" />刷新
        </button>
      </div>

      {loading && <div style={{textAlign: 'center', padding: 40}}><span className="loading" /></div>}

      {!loading && history.length === 0 && (
        <div className="glass glass-pad" style={{color: 'var(--text-mute)', textAlign: 'center', padding: 40, fontSize: 13}}>
          暂无操作历史
        </div>
      )}

      <div style={{display: 'flex', flexDirection: 'column', gap: 8}}>
        {history.map(item => {
          const sc = successOps(item);
          const isRollback = (item.note || '').startsWith('[回滚]');
          return (
            <div key={item.history_id} className="glass" style={{borderRadius: 8, overflow: 'hidden'}}>
              <div
                style={{display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px', cursor: 'pointer'}}
                onClick={() => setExpanded(p => ({...p, [item.history_id]: !p[item.history_id]}))}
              >
                <Icon id="i-clock" className="icon icon-sm" style={{color: isRollback ? 'var(--warning)' : 'var(--primary)', flexShrink: 0}} />
                <div style={{flex: 1}}>
                  <div style={{fontSize: 13.5, fontWeight: 500}}>{item.note || '（无备注）'}</div>
                  <div style={{fontSize: 12, color: 'var(--text-mute)', marginTop: 2}}>
                    {item.timestamp ? item.timestamp.slice(0, 19).replace('T', ' ') : ''} · 成功 {sc} 个文件
                  </div>
                </div>
                {!isRollback && (
                  <button className="btn btn-ghost btn-sm" onClick={e => { e.stopPropagation(); setRollbackId(item.history_id); }}
                    style={{flexShrink: 0}}>
                    回滚
                  </button>
                )}
                <Icon id={expanded[item.history_id] ? 'i-arrow-left' : 'i-arrow-right'} className="icon icon-sm" style={{opacity: 0.4}} />
              </div>

              {expanded[item.history_id] && (
                <div style={{borderTop: '1px solid var(--border)'}}>
                  <table style={{width: '100%', borderCollapse: 'collapse', fontSize: 12}}>
                    <thead>
                      <tr style={{background: 'var(--bg-1)', color: 'var(--text-mute)'}}>
                        <th style={{padding: '5px 10px', textAlign: 'left', fontWeight: 500}}>原文件名</th>
                        <th style={{padding: '5px 10px', textAlign: 'left', fontWeight: 500}}>新文件名</th>
                        <th style={{padding: '5px 10px', textAlign: 'left', fontWeight: 500}}>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {item.ops.map((op, i) => (
                        <tr key={i} style={{borderTop: '1px solid var(--border)'}}>
                          <td style={{padding: '5px 10px', color: 'var(--text-mute)', wordBreak: 'break-all'}}>
                            {op.original_path.split(/[\\/]/).pop()}
                          </td>
                          <td style={{padding: '5px 10px', color: 'var(--text)', wordBreak: 'break-all'}}>
                            {op.new_path.split(/[\\/]/).pop()}
                          </td>
                          <td style={{padding: '5px 10px', whiteSpace: 'nowrap', color: op.status === 'success' ? 'var(--success)' : 'var(--danger)'}}>
                            {op.status}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 回滚确认 */}
      {rollbackId && (
        <div style={{position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000}}>
          <div style={{background: 'var(--panel)', borderRadius: 12, padding: 24, width: 380, maxWidth: '90vw', display: 'flex', flexDirection: 'column', gap: 14}}>
            <div style={{fontWeight: 600, fontSize: 15}}>确认回滚</div>
            <div style={{fontSize: 13, color: 'var(--text-mute)'}}>回滚将把文件名恢复到操作前的状态，此操作也会记录到历史中。</div>
            <div style={{display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
              <button className="btn btn-ghost" onClick={() => setRollbackId(null)} disabled={rolling}>取消</button>
              <button className="btn btn-primary" onClick={doRollback} disabled={rolling} style={{background: 'var(--warning)'}}>
                {rolling ? <span className="loading" /> : '确认回滚'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============ Tab 5: 配置设置 ============

function SettingsTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [customRules, setCustomRules] = useState('');
  const [testing, setTesting] = useState(false);

  useEffect(() => { loadConfig(); }, []);

  async function loadConfig() {
    setLoading(true);
    try {
      const r = await api('/api/file-manager/config');
      if (r.ok) {
        setConfig(r.config);
        setCustomRules((r.config.custom_ad_rules || []).join('\n'));
      }
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function saveConfig() {
    setSaving(true); setError(''); setSuccess('');
    try {
      const payload = {
        ai_enabled: config.ai_enabled,
        ai_base_url: config.ai_base_url,
        ai_model: config.ai_model,
        custom_ad_rules: customRules.split('\n').map(s => s.trim()).filter(Boolean),
      };
      if (apiKey) payload.ai_api_key = apiKey;
      const r = await api('/api/file-manager/config', {method: 'POST', body: JSON.stringify(payload)});
      if (!r.ok) throw new Error(r.error);
      setSuccess('配置已保存');
      setApiKey('');
      loadConfig();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function testAi() {
    setTesting(true); setError(''); setSuccess('');
    try {
      const r = await api('/api/file-manager/ai-analyze', {
        method: 'POST',
        body: JSON.stringify({file_names: ['001-第一章 序章.mp3', '002-第二章 开始.mp3']}),
      });
      if (!r.ok) throw new Error(r.error);
      setSuccess('AI 连接测试成功！');
    } catch (e) {
      setError('AI 测试失败：' + e.message);
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <div style={{textAlign: 'center', padding: 40}}><span className="loading" /></div>;

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 14}}>
      {error && <div style={{color: 'var(--danger)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--danger)'}}>{error}</div>}
      {success && <div style={{color: 'var(--success)', padding: '8px 12px', background: 'var(--bg-0)', borderRadius: 6, border: '1px solid var(--success)'}}>{success}</div>}

      {config && (
        <>
          {/* DeepSeek AI 配置 */}
          <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
            <div style={{fontWeight: 600, fontSize: 14}}>DeepSeek AI 配置</div>

            <div style={{display: 'flex', alignItems: 'center', gap: 10}}>
              <span style={{fontSize: 13}}>启用 AI 识别</span>
              <div
                onClick={() => setConfig(p => ({...p, ai_enabled: !p.ai_enabled}))}
                style={{
                  width: 40, height: 22, borderRadius: 11, cursor: 'pointer', position: 'relative',
                  background: config.ai_enabled ? 'var(--primary)' : 'var(--border)',
                  transition: 'background 0.2s',
                }}
              >
                <div style={{
                  position: 'absolute', top: 2, left: config.ai_enabled ? 20 : 2, width: 18, height: 18,
                  borderRadius: '50%', background: '#fff', transition: 'left 0.2s',
                }} />
              </div>
            </div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>
                API Key {config.ai_api_key_masked && <span style={{color: 'var(--text-mute)'}}>（当前：{config.ai_api_key_masked}）</span>}
              </div>
              <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                placeholder="输入新 Key 以更新（留空保持不变）"
                style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
            </div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>Base URL</div>
              <input value={config.ai_base_url} onChange={e => setConfig(p => ({...p, ai_base_url: e.target.value}))}
                style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
            </div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>模型</div>
              <input value={config.ai_model} onChange={e => setConfig(p => ({...p, ai_model: e.target.value}))}
                style={{width: '100%', padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 13, boxSizing: 'border-box'}} />
            </div>

            <div style={{display: 'flex', gap: 8}}>
              <button className="btn btn-ghost" onClick={testAi} disabled={testing || !config.ai_enabled}>
                {testing ? <span className="loading" /> : <Icon id="i-bolt" className="icon icon-sm" />}测试连接
              </button>
            </div>
          </div>

          {/* 广告清理规则 */}
          <div className="glass glass-pad" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
            <div style={{fontWeight: 600, fontSize: 14}}>广告清理规则</div>

            <div style={{background: 'var(--bg-1)', borderRadius: 6, padding: 10, fontSize: 12, color: 'var(--text-mute)', lineHeight: 1.7}}>
              <strong>内置规则（只读）：</strong><br />
              www网址 / https链接 / QQ号 / 微信号 / 公众号 / 听书推广 / 下载推广
            </div>

            <div>
              <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 4}}>自定义规则（每行一个正则表达式）</div>
              <textarea value={customRules} onChange={e => setCustomRules(e.target.value)}
                rows={5} placeholder="每行输入一个正则表达式，如：\[.*?广告.*?\]"
                style={{width: '100%', padding: 8, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-0)', color: 'var(--text)', fontSize: 12.5, fontFamily: 'monospace', resize: 'vertical', boxSizing: 'border-box'}} />
            </div>
          </div>

          <button className="btn btn-primary" onClick={saveConfig} disabled={saving} style={{alignSelf: 'flex-start'}}>
            {saving ? <span className="loading" /> : <Icon id="i-check" className="icon icon-sm" />}保存配置
          </button>
        </>
      )}
    </div>
  );
}
