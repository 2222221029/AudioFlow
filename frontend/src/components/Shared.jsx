import {useEffect, useRef, useState} from 'react';
import {Icon} from './Icons.jsx';
import {AppLogo} from './AppLogo.jsx';
import PlatformLogo, {PlatformTag} from './PlatformLogo.jsx';
import {albumEpisodeText, chapterId, chapterTitle, coverOf, fmtDuration, taskStatusText} from '../utils/format.js';
import {COOKIE_PLATFORMS, NO_COOKIE_KEYS, PERSONAL_FEATURES, SEARCH_PLATFORMS} from '../utils/platforms.js';
import {applyTheme, persistTheme, savedTheme, THEMES} from '../utils/themes.js';
import {api} from '../services/api.js';

export function Toast({toast}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return undefined;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), 2400);
    return () => clearTimeout(timer);
  }, [toast]);
  return <div className={`toast ${visible ? 'show' : ''} ${toast?.kind || 'ok'}`}>{toast?.message || ''}</div>;
}

export function Modal({modal, onClose}) {
  if (!modal) return null;
  return (
    <div className="modal-backdrop show" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal">
        {modal.close !== false && (
          <button className="modal-close-btn" onClick={onClose} title="关闭">
            <Icon id="i-close" className="icon icon-sm" />
          </button>
        )}
        {modal.content}
      </div>
    </div>
  );
}

export function ConfirmModal({icon = 'i-alert', title, message, okText = '确定', danger, onOk, onClose}) {
  return (
    <>
      <div className="modal-title"><Icon id={icon} />{title}</div>
      <div className="modal-sub">{message}</div>
      <div className="modal-actions">
        <button className="btn btn-ghost btn-sm" onClick={onClose}><Icon id="i-close" className="icon icon-sm" />取消</button>
        <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'} btn-sm`} onClick={onOk}><Icon id="i-check" className="icon icon-sm" />{okText}</button>
      </div>
    </>
  );
}

export function LoginModal({onSubmit, error, loading}) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  return (
    <div className="login-overlay show">
      <form className="login-card" onSubmit={(event) => { event.preventDefault(); onSubmit({username, password}); }}>
        <div className="login-brand">
          <div className="login-logo"><AppLogo /></div>
          <div>
            <div className="login-title">AudioFlow</div>
            <div className="login-sub">登录后继续管理下载与订阅</div>
          </div>
        </div>
        <label className="login-field">
          <span>账号</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label className="login-field">
          <span>密码</span>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" placeholder="默认密码 admin" />
        </label>
        <div className="login-error">{error}</div>
        <button className="btn btn-primary login-submit" disabled={loading} type="submit">{loading ? '登录中...' : '登录'}</button>
        <div className="login-hint">默认账号 admin，默认密码 admin。登录后请在系统设置中修改密码。</div>
      </form>
    </div>
  );
}

export function PlatformSelect({platform, setPlatform, mobile = false}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const selected = SEARCH_PLATFORMS.find((item) => item.value === platform) || SEARCH_PLATFORMS[0];

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (!wrapRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);

  if (mobile) {
    return (
      <div className="chip-row" id="platformChips">
        {SEARCH_PLATFORMS.map((item) => (
          <button key={item.value} type="button" className={`chip platform-chip ${platform === item.value ? 'active' : ''}`} onClick={() => setPlatform(item.value)}>
            {item.value === 'all' ? <Icon id="i-layers" className="icon icon-sm" /> : <PlatformLogo value={item.value} name={item.label} className="platform-logo platform-logo-sm" />}
            <span>{item.label}</span>
          </button>
        ))}
      </div>
    );
  }
  return (
    <div ref={wrapRef} className={`platform-select-wrap ${open ? 'open' : ''}`}>
      <button type="button" className="platform-select" onClick={() => setOpen((value) => !value)} aria-haspopup="listbox" aria-expanded={open}>
        <span className="platform-name">
          {selected.value === 'all' ? <Icon id="i-layers" className="icon icon-sm" /> : <PlatformLogo value={selected.value} name={selected.label} className="platform-logo platform-logo-sm" />}
          <span>{selected.label}</span>
        </span>
        <Icon id="i-arrow-right" className="icon icon-sm" />
      </button>
      <div className="platform-menu" role="listbox">
        {SEARCH_PLATFORMS.map((item) => (
          <button
            key={item.value}
            type="button"
            role="option"
            aria-selected={platform === item.value}
            className={`platform-option ${platform === item.value ? 'active' : ''}`}
            onClick={() => {
              setPlatform(item.value);
              setOpen(false);
            }}
          >
            <span className="platform-name">
              {item.value === 'all' ? <Icon id="i-layers" className="icon icon-sm" /> : <PlatformLogo value={item.value} name={item.label} className="platform-logo platform-logo-sm" />}
              <span>{item.label}</span>
            </span>
            <Icon id="i-check" className="icon icon-sm" />
          </button>
        ))}
      </div>
    </div>
  );
}

export function ResultCard({album, onOpen, mobile = false}) {
  const cover = coverOf(album);
  return (
    <button className={mobile ? 'result-card' : 'result-row'} onClick={onOpen}>
      <div className="result-cover" style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>
        {cover ? '' : <Icon id="i-headphone" className="icon icon-lg" />}
      </div>
      <div className="result-info">
        <div className="result-title">{album.title || '未知专辑'}</div>
        <div className="result-platform"><PlatformTag value={album.platform} /></div>
        <div className="result-meta">{album.author || album.anchor || '未知作者'} · {albumEpisodeText(album)}</div>
      </div>
    </button>
  );
}

export function ChapterList({chapters, selected, onToggle, onPlay, mobile = false}) {
  if (!chapters.length) return <div className="empty"><Icon id="i-list" />暂无章节</div>;
  return (
    <div className={mobile ? 'detail-chapters' : 'chapter-list'}>
      {chapters.map((chapter, index) => {
        const id = chapterId(chapter, String(index + 1));
        const checked = selected.has(id);
        const title = chapterTitle(chapter);
        return (
          <div key={id} className={`chapter-row ${checked ? 'selected' : ''}`} onClick={() => onToggle(id)}>
            <input type="checkbox" checked={checked} onChange={() => onToggle(id)} onClick={(event) => event.stopPropagation()} />
            <span className="chapter-index">{chapter.order_num || index + 1}</span>
            <span className="chapter-title" title={title}>{title}</span>
            <span className="chapter-duration">{fmtDuration(chapter.duration || chapter.duration_sec)}</span>
            <button className="icon-btn" onClick={(event) => { event.stopPropagation(); onPlay(chapter); }} title="试听"><Icon id="i-play" /></button>
          </div>
        );
      })}
    </div>
  );
}

function BusyIcon({busy, icon}) {
  return busy ? <span className="loading" /> : <Icon id={icon} className="icon icon-sm" />;
}

function formatCheckTime(value, fallback = '从未') {
  if (!value) return fallback;
  const numeric = typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value));
  const time = new Date(numeric ? Number(value) * 1000 : value);
  if (Number.isNaN(time.getTime())) return String(value);
  return time.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function ChapterToolbar({loading, busy, chapters, viewChapters, selectedChapterList, chapterSort, setChapterSort, downloadRange, setDownloadRange, actions}) {
  const [showRange, setShowRange] = useState(false);
  return (
    <div className="chapter-toolbar">
      {/* 主操作 */}
      <button className="btn btn-primary btn-sm" disabled={busy.download || loading} onClick={() => actions.startDownload()}><BusyIcon busy={busy.download} icon="i-download" />下载选中</button>
      <button className="btn btn-ghost btn-sm" disabled={busy.download || loading || !viewChapters.length} onClick={() => actions.startDownload(viewChapters)}><Icon id="i-bolt" className="icon icon-sm" />下载全部</button>
      <button className="btn btn-ghost btn-sm" disabled={busy.subscribe || loading} onClick={actions.subscribeAlbum}><BusyIcon busy={busy.subscribe} icon="i-star" />订阅追更</button>
      <div className="toolbar-sep" />
      {/* 排序 */}
      <div className="seg-control">
        <button className={chapterSort === 'asc' ? 'active' : ''} disabled={loading || !chapters.length} onClick={() => setChapterSort('asc')}>正序</button>
        <button className={chapterSort === 'desc' ? 'active' : ''} disabled={loading || !chapters.length} onClick={() => setChapterSort('desc')}>倒序</button>
      </div>
      {/* 选择操作 */}
      <button className="btn btn-ghost btn-sm" disabled={loading || !chapters.length} onClick={() => actions.selectAllChapters(true)}>全选</button>
      <button className="btn btn-ghost btn-sm" disabled={loading || !selectedChapterList.length} onClick={() => actions.selectAllChapters(false)}>清空</button>
      <button className="btn btn-ghost btn-sm" disabled={loading || !chapters.length} onClick={actions.invertChapterSelection}>反选</button>
      <span className="ch-summary">{loading ? '加载中...' : `${selectedChapterList.length}/${viewChapters.length}`}</span>
      {/* 折叠：范围下载 */}
      <button className="btn btn-ghost btn-sm" disabled={loading || !chapters.length} onClick={() => setShowRange((v) => !v)}>
        <Icon id="i-list" className="icon icon-sm" />{showRange ? '收起范围' : '范围下载'}
      </button>
      {showRange && (
        <div className="range-control">
          <input type="text" className="range-input" value={downloadRange} disabled={loading || !chapters.length} onChange={(event) => setDownloadRange(event.target.value)} placeholder="例：1-20, 25" />
          <button className="btn btn-ghost btn-sm" disabled={loading || !chapters.length || !downloadRange.trim()} onClick={() => actions.applyDownloadRange('select')}>选中范围</button>
          <button className="btn btn-primary btn-sm" disabled={busy.download || loading || !chapters.length || !downloadRange.trim()} onClick={() => actions.applyDownloadRange('download')}>下载范围</button>
        </div>
      )}
    </div>
  );
}

export function AlbumDetail({app, mobile = false}) {
  const {selectedAlbum, displayChapters, chapters, selectedChapters, selectedChapterList, voices, selectedVoice, chapterSort, setChapterSort, downloadRange, setDownloadRange, actions, busy} = app;
  if (!selectedAlbum) return <div className="empty" id="detailEmpty"><Icon id="i-music" />选择结果查看详情</div>;
  const cover = coverOf(selectedAlbum);
  const loading = busy.album || busy.voice;
  const viewChapters = displayChapters || chapters;
  return (
    <div className={mobile ? 'detail-content' : 'album-detail'} style={{display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1}}>
      <div className={mobile ? 'detail-hero' : 'album-hero'}>
        <div className={mobile ? 'detail-cover' : 'album-cover'} style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>{cover ? '' : <Icon id="i-music" />}</div>
        <div className={mobile ? 'detail-info' : 'album-info'}>
          <div className={mobile ? 'detail-title' : 'album-title'}>{selectedAlbum.title || '未知专辑'}</div>
          <div className={mobile ? 'detail-meta' : 'album-meta'}><PlatformTag value={selectedAlbum.platform} /> {selectedAlbum.author || selectedAlbum.anchor || '未知作者'}<br />{albumEpisodeText(selectedAlbum)} · {selectedAlbum.status || '连载中'}</div>
        </div>
      </div>
      {!!voices.length && (
        <div className={mobile ? 'detail-voice-bar' : 'voice-bar'}>
          {voices.map((voice, index) => (
            <button key={voice.id || voice.name || index} disabled={busy.voice} className={`chip ${selectedVoice === voice ? 'active' : ''}`} onClick={() => actions.changeVoice(voice)}>{voice.category ? `${voice.category} · ` : ''}{voice.name || voice.title || `音色 ${index + 1}`}</button>
          ))}
        </div>
      )}
      <ChapterToolbar
        loading={loading}
        busy={busy}
        chapters={chapters}
        viewChapters={viewChapters}
        selectedChapterList={selectedChapterList}
        chapterSort={chapterSort}
        setChapterSort={setChapterSort}
        downloadRange={downloadRange}
        setDownloadRange={setDownloadRange}
        actions={actions}
      />
      {loading ? <div className="empty"><span className="loading" /> 正在加载章节</div> : <ChapterList chapters={viewChapters} selected={selectedChapters} onToggle={actions.toggleChapter} onPlay={actions.playChapter} mobile={mobile} />}
    </div>
  );
}

export function DownloadsPage({app}) {
  const {downloads, metrics, actions, setModal, closeModal, busy} = app;
  const [statusFilter, setStatusFilter] = useState('all');
  const confirmDelete = (id) => setModal({content: <ConfirmModal icon="i-trash" title="清除任务记录" message="只清除历史记录，不会删除已下载文件。" okText="清除" danger onClose={closeModal} onOk={() => { closeModal(); actions.deleteDownload(id); }} />});
  const confirmCleanup = (statuses) => setModal({content: <ConfirmModal icon="i-trash" title="批量清理任务" message="将清理符合条件的历史任务记录，不会删除已下载文件。" okText="清理" danger onClose={closeModal} onOk={() => { closeModal(); actions.cleanupDownloads(statuses); }} />});

  const STATUS_FILTERS = [
    {key: 'all', label: '全部'},
    {key: 'active', label: '活跃', match: (s) => ['running', 'queued', 'paused'].includes(s)},
    {key: 'completed', label: '已完成', match: (s) => s === 'completed'},
    {key: 'failed', label: '失败/中断', match: (s) => ['failed', 'partial', 'interrupted', 'stopped'].includes(s)},
  ];
  const filteredDownloads = statusFilter === 'all'
    ? downloads
    : downloads.filter((t) => STATUS_FILTERS.find((f) => f.key === statusFilter)?.match?.(t.status));

  const hasRunning = downloads.some((t) => t.status === 'running');
  const hasStoppable = downloads.some((t) => ['queued', 'running', 'paused'].includes(t.status));

  return (
    <>
      <div className="metrics">
        <div className="metric"><div className="metric-label">活跃任务</div><div className="metric-value">{metrics.activeDownloads}</div><div className="metric-foot">运行中 / 排队中</div></div>
        <div className="metric"><div className="metric-label">已完成</div><div className="metric-value">{metrics.completedDownloads}</div><div className="metric-foot">下载完成</div></div>
        <div className="metric"><div className="metric-label">失败</div><div className="metric-value">{metrics.failedDownloads}</div><div className="metric-foot">失败 / 部分完成</div></div>
        <div className="metric"><div className="metric-label">合计</div><div className="metric-value">{downloads.length}</div><div className="metric-foot">所有任务</div></div>
      </div>
      <div className="glass glass-pad compact-tools">
        <div className="seg-control" style={{marginRight: 'auto'}}>
          {STATUS_FILTERS.map((f) => (
            <button key={f.key} className={statusFilter === f.key ? 'active' : ''} onClick={() => setStatusFilter(f.key)}>{f.label}</button>
          ))}
        </div>
        {hasRunning && <button className="btn btn-ghost btn-sm" disabled={busy['batchDownload:pause']} onClick={() => actions.batchControlDownloads('pause')}><BusyIcon busy={busy['batchDownload:pause']} icon="i-pause" />全部暂停</button>}
        {hasStoppable && <button className="btn btn-danger btn-sm" disabled={busy['batchDownload:stop']} onClick={() => actions.batchControlDownloads('stop')}><BusyIcon busy={busy['batchDownload:stop']} icon="i-close" />全部停止</button>}
        <button className="btn btn-ghost btn-sm" disabled={busy.cleanupDownloads} onClick={() => confirmCleanup(['completed'])}><BusyIcon busy={busy.cleanupDownloads} icon="i-trash" />清理已完成</button>
        <button className="btn btn-ghost btn-sm" disabled={busy.cleanupDownloads} onClick={() => confirmCleanup(['failed', 'partial', 'interrupted', 'stopped'])}><Icon id="i-trash" className="icon icon-sm" />清理失败/中断</button>
      </div>
      <div id="downloadList">
        {!filteredDownloads.length
          ? <div className="empty"><Icon id="i-download" />{downloads.length ? '该筛选条件下暂无任务' : '暂无下载任务'}</div>
          : filteredDownloads.map((task) => <TaskCard key={task.id} task={task} actions={actions} busy={busy} onDelete={confirmDelete} />)}
      </div>
    </>
  );
}

export function PersonalPage({app, mobile = false}) {
  const [platform, setPlatform] = useState('ximalaya');
  const [feature, setFeature] = useState('');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [personalCookies, setPersonalCookies] = useState({});
  const platformMeta = COOKIE_PLATFORMS.find((item) => item.key === (platform === 'ximalaya' ? 'xmly' : platform)) || {};
  const personalCookieInfo = personalCookies[platform] || {};
  const personalLoggedIn = !!personalCookieInfo.has_cookie;
  const loadPersonalCookies = async () => {
    try {
      const data = await api('/api/personal/cookies');
      setPersonalCookies(data.cookies || {});
    } catch (error) {
      app.actions.showToast?.(`个人中心登录状态加载失败：${error.message}`, 'err');
    }
  };
  useEffect(() => {
    loadPersonalCookies();
  }, []);
  const load = async (feat) => {
    setFeature(feat);
    setLoading(true);
    try {
      const data = await api(`/api/personal/${platform}/${feat}`);
      setItems(data.items || []);
    } catch (error) {
      app.actions.showToast?.(`加载失败：${error.message}`, 'err');
    } finally {
      setLoading(false);
    }
  };
  const features = PERSONAL_FEATURES[platform] || [];
  const openAlbum = (album) => {
    if (mobile) app.setMobileView?.('discover');
    else app.setPage?.('search');
    app.actions.openAlbum(album);
  };
  const savePersonalCookie = async (cookie) => {
    const trimmed = String(cookie || '').trim();
    if (!trimmed) return;
    await api('/api/personal/cookies', {method: 'POST', body: {platform, cookie: trimmed}});
    await loadPersonalCookies();
    app.actions.showToast?.(`${platformMeta.name || platform}个人中心 Cookie 已保存`, 'ok');
  };
  const deletePersonalCookie = async () => {
    await api(`/api/personal/cookies/${encodeURIComponent(platform)}`, {method: 'DELETE'});
    await loadPersonalCookies();
    setItems([]);
    setFeature('');
    app.actions.showToast?.(`${platformMeta.name || platform}个人中心 Cookie 已删除`, 'ok');
  };
  const openPersonalLogin = () => {
    app.setModal?.({
      content: <QrLoginModal
        platform={platformMeta}
        scope="personal"
        onDone={loadPersonalCookies}
        onClose={app.closeModal}
      />,
    });
  };
  const openPersonalCookieScript = () => {
    app.setModal?.({
      content: <CookieScriptModal
        platform={platformMeta}
        onSave={savePersonalCookie}
        onClose={app.closeModal}
      />,
    });
  };
  const authPanel = (
    <div className="personal-auth">
      <div className="personal-auth-main">
        <span className={`personal-auth-dot ${personalLoggedIn ? 'online' : ''}`} />
        <div className="personal-auth-copy">
          <span className="personal-auth-label">{personalLoggedIn ? '已连接个人中心账号' : '连接个人中心账号'}</span>
          <span className="personal-auth-sub">
            {personalLoggedIn
              ? (personalCookieInfo.account_name || personalCookieInfo.account_id || '凭证已保存，仅用于个人中心')
              : `${platformMeta.name || platform} 的历史、收藏和书架将使用这里的独立登录`}
          </span>
        </div>
      </div>
      <div className="personal-auth-actions">
        {platformMeta.qr && <button className="icon-btn personal-auth-btn primary" onClick={openPersonalLogin} title={platformMeta.qr === 'lrts' ? '验证码登录' : '扫码登录'}><Icon id={platformMeta.qr === 'lrts' ? 'i-mobile' : 'i-qr'} /></button>}
        {platform !== 'lrts' && <button className="icon-btn personal-auth-btn" onClick={openPersonalCookieScript} title="手动输入 Cookie"><Icon id="i-globe" /></button>}
        {personalLoggedIn && <button className="icon-btn personal-auth-btn danger" onClick={deletePersonalCookie} title="删除个人中心登录"><Icon id="i-trash" /></button>}
      </div>
    </div>
  );
  if (mobile) {
    const platformNames = {
      ximalaya: '喜马拉雅',
      xmly: '喜马拉雅',
      lrts: '懒人听书',
      qidian: '起点听书',
      qtfm: '蜻蜓FM',
      fanqie: '番茄畅听',
      fanqie_tingshu: '番茄听书',
      qimao: '七猫听书',
      yuntu: '云听FM',
      kuwo: '酷我听书',
      netease: '网易云听书',
      lizhi: '荔枝FM',
    };
    return (
      <div className="mobile-personal-app">
        <div className="mobile-personal-title">个人中心</div>
        <div className="mobile-personal-platforms">
          {Object.keys(PERSONAL_FEATURES).map((key) => (
            <button
              key={key}
              className={`mobile-platform-pill ${platform === key ? 'active' : ''}`}
              onClick={() => { setPlatform(key); setFeature(''); setItems([]); }}
            >
              {platformNames[key] || key}
            </button>
          ))}
        </div>
        {authPanel}
        <div className="mobile-personal-card">
          {features.map((item) => (
            <button key={item.key} className="mobile-personal-row" onClick={() => load(item.key)}>
              <Icon id={item.icon} />
              <span>{item.name}</span>
              <Icon id="i-arrow-right" className="icon icon-sm" />
            </button>
          ))}
        </div>
        {(loading || feature) && (
          <div className="mobile-personal-results">
            {loading
              ? <div className="empty"><span className="loading" /> 加载中...</div>
              : !items.length
                ? <div className="empty"><Icon id="i-user" />暂无数据</div>
                : items.map((album, index) => <ResultCard key={`${album.platform}-${album.id || album.title}-${index}`} album={album} mobile onOpen={() => openAlbum(album)} />)}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className={mobile ? 'mobile-personal' : ''}>
      <div className="tabs">
        {Object.entries(PERSONAL_FEATURES).map(([key]) => {
          const nameMap = {ximalaya: '喜马拉雅', lrts: '懒人听书', qidian: '起点听书', lizhi: '荔枝', xmly: '喜马拉雅', kuwo: '酷我', qtfm: '蜻蜓FM', netease: '网易云音乐', yuntu: '云听', fanqie: '番茄畅听'};
          return (
            <button key={key} className={`tab ${platform === key ? 'active' : ''}`} onClick={() => { setPlatform(key); setFeature(''); setItems([]); }}>
              {nameMap[key] || key}
            </button>
          );
        })}
      </div>
      {authPanel}
      <div className="tabs feature-tabs">{features.map((item) => <button key={item.key} className={`tab ${feature === item.key ? 'active' : ''}`} onClick={() => load(item.key)}>{item.name}</button>)}</div>
      <div className="sub-grid personal-grid">{loading ? <div className="empty"><span className="loading" /> 加载中...</div> : !items.length ? <div className="empty"><Icon id="i-user" />选择上方功能加载</div> : items.map((album, index) => <ResultCard key={`${album.platform}-${album.id || album.title}-${index}`} album={album} mobile={mobile} onOpen={() => openAlbum(album)} />)}</div>
    </div>
  );
}

function TaskCard({task, actions, busy, onDelete}) {
  const pct = Math.max(0, Math.min(100, task.percent || 0));
  const status = task.status || 'queued';
  const canPause = status === 'running';
  const canResume = status === 'paused';
  const canStop = ['queued', 'running', 'paused'].includes(status);
  const canRetry = (task.failed_chapters && task.failed_chapters.length) || ['failed', 'partial', 'interrupted', 'stopped'].includes(status);
  const canDelete = !['queued', 'running', 'paused', 'stopping'].includes(status);
  const busyPrefix = `download:${task.id}:`;
  return (
    <div className="task-card">
      <div className="task-head"><div className="task-title" title={task.title || task.id}>{task.title || task.id}</div><span className={`task-state state-${status}`}>{taskStatusText(status)}</span></div>
      <div className="progress-bar"><div className="progress-fill" style={{width: `${pct}%`}} /></div>
      <div className="task-meta"><span>{task.completed || 0}/{task.total || 0} 章</span><span>{pct}%</span>{task.error ? <span style={{color: 'var(--danger)'}}>{task.error}</span> : null}</div>
      {task.warning ? <div className="task-meta"><span style={{color: 'var(--warning)'}}>{task.warning}</span></div> : null}
      <div className="task-actions">
        {canPause && <button className="btn btn-ghost btn-tiny" disabled={busy[`${busyPrefix}pause`]} onClick={() => actions.controlDownload(task.id, 'pause')}><BusyIcon busy={busy[`${busyPrefix}pause`]} icon="i-pause" />暂停</button>}
        {canResume && <button className="btn btn-primary btn-tiny" disabled={busy[`${busyPrefix}resume`]} onClick={() => actions.controlDownload(task.id, 'resume')}><BusyIcon busy={busy[`${busyPrefix}resume`]} icon="i-play" />继续</button>}
        {canStop && <button className="btn btn-danger btn-tiny" disabled={busy[`${busyPrefix}stop`]} onClick={() => actions.controlDownload(task.id, 'stop')}><BusyIcon busy={busy[`${busyPrefix}stop`]} icon="i-close" />停止</button>}
        {canRetry && <button className="btn btn-ghost btn-tiny" disabled={busy[`${busyPrefix}retry-failed`]} onClick={() => actions.controlDownload(task.id, 'retry-failed')}><BusyIcon busy={busy[`${busyPrefix}retry-failed`]} icon="i-refresh" />重试失败</button>}
        {canDelete && <button className="btn btn-ghost btn-tiny" onClick={() => onDelete(task.id)}><Icon id="i-trash" className="icon icon-sm" />清除记录</button>}
      </div>
    </div>
  );
}

export function SubscriptionsPage({app}) {
  const {subscriptions, subscriptionSettings, subscriptionScheduler = {}, subscriptionJobs, actions, setModal, closeModal, busy} = app;
  const [enabled, setEnabled] = useState(true);
  const [autoDownload, setAutoDownload] = useState(true);
  const [hours, setHours] = useState(6);
  useEffect(() => {
    setEnabled(subscriptionSettings.enabled !== false);
    setAutoDownload(subscriptionSettings.auto_download_missing !== false);
    setHours(Number(subscriptionSettings.interval_hours || 6));
  }, [subscriptionSettings]);
  const cancel = (id) => setModal({content: <ConfirmModal icon="i-trash" title="取消订阅" message="后续不会再自动检测新章节。" okText="取消订阅" danger onClose={closeModal} onOk={() => { closeModal(); actions.cancelSubscription(id); }} />});
  const schedulerRunning = Boolean(subscriptionScheduler.running);
  const schedulerStarted = Boolean(subscriptionScheduler.started);
  const schedulerLastRun = formatCheckTime(subscriptionScheduler.last_run_at, '等待首次轮询');
  const dueCount = Number(subscriptionScheduler.current_due_count || 0);
  return (
    <>
      <div className="glass glass-pad subscription-controls">
        <label className="check-row"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /><span>启用自动检测</span></label>
        <label className="check-row"><input type="checkbox" checked={autoDownload} onChange={(e) => setAutoDownload(e.target.checked)} /><span>发现缺失后自动下载</span></label>
        <label className="check-row interval-row"><span>检测间隔（小时）</span><input className="field-input interval-input" type="number" min="1" max="720" value={hours} onChange={(e) => setHours(e.target.value)} /></label>
        <button className="btn btn-primary btn-sm" disabled={busy.subscriptionSettings} onClick={() => actions.saveSubscriptionSettings({enabled, auto_download_missing: autoDownload, interval_hours: Number(hours) || 6})}><BusyIcon busy={busy.subscriptionSettings} icon="i-check" />保存</button>
        <button className="btn btn-ghost btn-sm" disabled={busy.runSubscriptions} onClick={actions.runSubscriptionsNow}><BusyIcon busy={busy.runSubscriptions} icon="i-refresh" />立即检测并补全</button>
        <button className="btn btn-ghost btn-sm" disabled={busy.rebuildIndex} onClick={actions.rebuildSubscriptionIndex}><BusyIcon busy={busy.rebuildIndex} icon="i-folder" />重建本地索引</button>
        <div className="subscription-scheduler">
          <span className={schedulerStarted ? 'ok' : 'muted'}>调度器：{schedulerRunning ? '检测中' : schedulerStarted ? '待命' : '未启动'}</span>
          <span>最近轮询：{schedulerLastRun}</span>
          <span>到期专辑：{dueCount}</span>
        </div>
      </div>
      <div className="sub-grid">
        {!subscriptions.length ? <div className="empty"><Icon id="i-star" />暂无订阅<br />在专辑详情点击“订阅追更”</div> : subscriptions.map((sub) => {
          const album = sub.album || sub;
          const stats = sub.stats || {};
          const jobBusy = Object.values(subscriptionJobs).some((job) => job.sid === sub.id && ['queued', 'running'].includes(job.status));
          const cover = coverOf(sub) || coverOf(album);
          const checkBusy = jobBusy || busy[`subscription:${sub.id}:check`];
          const completeBusy = jobBusy || busy[`subscription:${sub.id}:complete`];
          const title = sub.title || album.title || '未知专辑';
          const platform = sub.platform || album.platform;
          const author = sub.author || sub.anchor || album.author || album.anchor || '未知作者';
          const total = Number(stats.total || sub.total || album.episodes || album.chapter_count || album.track_count || 0);
          const downloaded = Number(stats.downloaded || 0);
          const restricted = Number(stats.restricted || sub.last_diff?.restricted_count || 0);
          const missing = Number(stats.missing || Math.max(total - downloaded - restricted, 0));
          const progress = total > 0 ? Math.max(0, Math.min(100, Math.round((downloaded / total) * 100))) : 0;
          const lastCheck = formatCheckTime(sub.last_check_at);
          const nextCheck = formatCheckTime(sub.next_check_at, '等待首次检测');
          return (
            <div className="sub-card" key={sub.id}>
              <div className="sub-cover-wrap">
                <div className="sub-cover" style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>{cover ? '' : <Icon id="i-music" />}</div>
                {jobBusy && <span className="sub-live"><span className="loading" />检测中</span>}
              </div>
              <div className="sub-info">
                <div className="sub-main">
                  <div className="sub-title" title={title}>{title}</div>
                  <div className="sub-meta"><PlatformTag value={platform} /> <span>{author}</span></div>
                  <div className="sub-progress"><span style={{width: `${progress}%`}} /></div>
                  <div className="sub-stats">
                    <span>共 {total || 0} 章</span>
                    <span>已下载 {downloaded}</span>
                    <span>缺失 {missing}</span>
                    {restricted > 0 && <span>受限 {restricted}</span>}
                    <span>上次检测：{lastCheck}</span>
                    <span>下次检测：{nextCheck}</span>
                  </div>
                </div>
                <div className="sub-actions">
                  <button className="btn btn-ghost btn-sm" disabled={checkBusy} onClick={() => actions.checkSubscription(sub.id, false)}><BusyIcon busy={checkBusy} icon="i-refresh" />立即检测</button>
                  <button className="btn btn-primary btn-sm" disabled={completeBusy} onClick={() => actions.checkSubscription(sub.id, true)}><BusyIcon busy={completeBusy} icon="i-download" />补全缺失</button>
                  <button className="btn btn-ghost btn-sm" disabled={jobBusy} onClick={() => cancel(sub.id)}><Icon id="i-trash" className="icon icon-sm" />取消订阅</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function CookiesPage({app}) {
  const {cookies, actions, setModal, closeModal, busy} = app;
  return (
    <div id="cookieList" className="cookie-grid">
      {COOKIE_PLATFORMS.map((platform) => <CookieCard key={platform.key} platform={platform} info={cookies[platform.key] || {}} actions={actions} busy={busy} setModal={setModal} closeModal={closeModal} />)}
    </div>
  );
}

function CookieCard({platform, info, actions, busy, setModal, closeModal}) {
  const [value, setValue] = useState('');
  const noCookie = NO_COOKIE_KEYS.includes(platform.key);
  const ok = info.has_cookie || info.has_server;
  const scanText = platform.qr === 'lrts' ? '验证码登录' : '扫码';
  const saveText = platform.key === 'lrts' ? '保存手动凭证' : '保存粘贴的 Cookie';
  const textareaPlaceholder = platform.key === 'lrts'
    ? '粘贴懒人听书 App 凭证 JSON，或 token=...; imei=...'
    : '粘贴 Cookie 字符串';
  return (
    <div className="cookie-card">
      <div className="cookie-head">
        <span className="name">
          <PlatformLogo value={platform.key} name={platform.name} />
          <span className="cookie-platform-title">{platform.name}</span>
          {!noCookie && ok && info.account_name && <span className="cookie-account" title={info.account_id ? `${info.account_name} (${info.account_id})` : info.account_name}>{info.account_name}</span>}
        </span>
        <span className={`cookie-status ${ok || noCookie ? 'cookie-yes' : 'cookie-no'}`}>{noCookie ? '免登录' : ok ? '已设置' : '未设置'}</span>
      </div>
      {noCookie ? (
        <>
          <div className="cookie-note ok">已内置规则，可直接搜索。</div>
          <div className="cookie-desc">{platform.name} 使用公开接口或内置抓取策略。</div>
        </>
      ) : (
        <>
          <div className="cookie-actions">
            {platform.qr && <button className="btn btn-primary btn-tiny" onClick={() => setModal({content: <QrLoginModal platform={platform} onDone={actions.loadCookies} onClose={closeModal} />})}><Icon id="i-qr" className="icon icon-sm" />{scanText}</button>}
            {platform.key !== 'lrts' && <button className="btn btn-ghost btn-tiny" onClick={() => setModal({content: <CookieScriptModal platform={platform} onSave={(cookie) => actions.saveCookie(platform.key, cookie)} onClose={closeModal} />})}><Icon id="i-globe" className="icon icon-sm" />浏览器获取</button>}
            {ok && <button className="btn btn-danger btn-tiny" disabled={busy[`cookieDelete:${platform.key}`]} onClick={() => actions.deleteCookie(platform.key)}><BusyIcon busy={busy[`cookieDelete:${platform.key}`]} icon="i-trash" />删除</button>}
          </div>
          <textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={textareaPlaceholder}
          />
          <button className="btn btn-primary btn-tiny" disabled={busy[`cookie:${platform.key}`]} onClick={() => { actions.saveCookie(platform.key, value); setValue(''); }}>
            <BusyIcon busy={busy[`cookie:${platform.key}`]} icon="i-check" />{saveText}
          </button>
        </>
      )}
    </div>
  );
}

function QrLoginModal({platform, scope = 'cookies', onDone, onClose}) {
  const [message, setMessage] = useState('正在初始化...');
  const [qr, setQr] = useState('');
  const [error, setError] = useState('');
  const [phone, setPhone] = useState('');
  const [smsCode, setSmsCode] = useState('');
  const [sendingCode, setSendingCode] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [lrtsLoginState, setLrtsLoginState] = useState({imei: '', tempToken: ''});
  const [lrtsMode, setLrtsMode] = useState('sms');
  const [manualCredential, setManualCredential] = useState('');
  const [savingManualCredential, setSavingManualCredential] = useState(false);
  const sessionRef = useRef('');

  useEffect(() => {
    if (platform.qr === 'lrts') {
      setMessage('输入手机号获取验证码后登录');
      return undefined;
    }
    let timer = null;
    let stopped = false;
    async function start() {
      try {
        const data = await api('/api/qr/start', {method: 'POST', body: {platform: platform.qr}});
        sessionRef.current = data.session_id;
        timer = setInterval(async () => {
          try {
            const pollPath = scope === 'personal' ? `/api/personal/qr/poll/${sessionRef.current}` : `/api/qr/poll/${sessionRef.current}`;
            const poll = await api(pollPath);
            const session = poll.session || {};
            if (stopped) return;
            setMessage(session.message || '');
            if (session.qr_image) setQr(session.qr_image);
            // 懒人听书：账号密码输入模式
            if (session.status === 'success') {
              clearInterval(timer);
              // 懒人听书：需要额外调保存接口
              onDone?.();
              onClose();
            } else if (['failed', 'expired', 'cancelled'].includes(session.status)) {
              clearInterval(timer);
              setError(session.message || session.status);
            }
          } catch (err) {
            clearInterval(timer);
            setError(err.message);
          }
        }, 1500);
      } catch (err) {
        setError(err.message);
      }
    }
    start();
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
      if (sessionRef.current) api(`/api/qr/cancel/${sessionRef.current}`, {method: 'POST'}).catch(() => {});
    };
  }, [onClose, onDone, platform.qr, scope]);

;

;

  // 启动浏览器代理登录
;

  // 手动保存 Cookie

  const sendLrtsCode = async () => {
    if (!phone.trim()) return;
    setSendingCode(true);
    setError('');
    try {
      const data = await api('/api/lrts/send-code', {method: 'POST', body: {phone: phone.trim()}});
      setLrtsLoginState({imei: data.imei || '', tempToken: data.temp_token || ''});
      setMessage(data.message || '验证码已发送');
    } catch (err) {
      setError(err.message);
    } finally {
      setSendingCode(false);
    }
  };

  const loginLrtsWithCode = async () => {
    if (!phone.trim() || !smsCode.trim()) return;
    setLoggingIn(true);
    setError('');
    try {
      const loginPath = scope === 'personal' ? '/api/personal/lrts/login' : '/api/lrts/login';
      const data = await api(loginPath, {
        method: 'POST',
        body: {
          phone: phone.trim(),
          code: smsCode.trim(),
          imei: lrtsLoginState.imei,
          temp_token: lrtsLoginState.tempToken,
        },
      });
      setMessage(data.message || '登录成功');
      onDone?.();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoggingIn(false);
    }
  };

  const saveLrtsManualCredential = async () => {
    if (!manualCredential.trim()) return;
    setSavingManualCredential(true);
    setError('');
    try {
      const savePath = scope === 'personal' ? '/api/personal/cookies' : '/api/cookies';
      await api(savePath, {method: 'POST', body: {platform: 'lrts', cookie: manualCredential.trim()}});
      setMessage('手动凭证已保存');
      onDone?.();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingManualCredential(false);
    }
  };

  // LRTS SMS login.
  if (platform.qr === 'lrts') {
    return (
      <div className="lrts-login">
        <div className="lrts-login-head">
          <div className="lrts-login-icon"><Icon id="i-user" /></div>
          <div className="lrts-login-copy">
            <div className="modal-title lrts-title">{platform.name}</div>
            <div className="modal-sub lrts-sub">{error || message}</div>
          </div>
        </div>

        <div className="lrts-mode-tabs" role="tablist" aria-label="懒人听书登录方式">
          <button type="button" className={lrtsMode === 'sms' ? 'active' : ''} onClick={() => { setLrtsMode('sms'); setError(''); }}>
            <Icon id="i-mobile" className="icon icon-sm" />验证码登录
          </button>
          <button type="button" className={lrtsMode === 'manual' ? 'active' : ''} onClick={() => { setLrtsMode('manual'); setError(''); }}>
            <Icon id="i-key" className="icon icon-sm" />手动凭证
          </button>
        </div>

        {lrtsMode === 'sms' ? (
          <div className="lrts-panel">
            <div className="lrts-inline">
              <input
                className="field-input lrts-input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="手机号"
                inputMode="tel"
                autoComplete="tel"
              />
              <button className="btn btn-ghost btn-sm lrts-send-btn" disabled={sendingCode || !phone.trim()} onClick={sendLrtsCode}>
                <BusyIcon busy={sendingCode} icon="i-mobile" />发送验证码
              </button>
            </div>
            <input
              className="field-input lrts-input"
              value={smsCode}
              onChange={(e) => setSmsCode(e.target.value)}
              placeholder="短信验证码"
              inputMode="numeric"
              autoComplete="one-time-code"
            />
            {error && <div className="field-hint err">{error}</div>}
            <div className="lrts-note">登录成功后会保存 App API 凭证：imei + token。{scope === 'personal' ? '仅用于个人中心。' : ''}</div>
            <div className="modal-actions">
              <button className="btn btn-primary btn-sm" disabled={loggingIn || !phone.trim() || !smsCode.trim()} onClick={loginLrtsWithCode}>
                <BusyIcon busy={loggingIn} icon="i-check" />登录并保存
              </button>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
            </div>
          </div>
        ) : (
          <div className="lrts-panel">
            <textarea
              className="cookie-modal-textarea lrts-credential-input"
              value={manualCredential}
              onChange={(event) => setManualCredential(event.target.value)}
              placeholder={'粘贴 {"imei":"...","token":"..."}\n也支持 token=...; imei=...'}
            />
            {error && <div className="field-hint err">{error}</div>}
            <div className="lrts-note">这里保存的是懒人听书 App API 凭证，不会当作网页 Cookie 发送。{scope === 'personal' ? '仅用于个人中心。' : ''}</div>
            <div className="modal-actions">
              <button className="btn btn-primary btn-sm" disabled={savingManualCredential || !manualCredential.trim()} onClick={saveLrtsManualCredential}>
                <BusyIcon busy={savingManualCredential} icon="i-check" />保存凭证
              </button>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="modal-title"><Icon id="i-qr" />{platform.name}扫码登录</div>
      <div className="modal-sub">{error || message}</div>
      <div className="qr-box">
        {qr ? <img className="qr-img" src={qr} alt="QR code" /> : <span className="loading" />}
      </div>
      <div className="modal-sub modal-note">使用对应 App 扫码，登录成功后会自动保存 Cookie。{scope === 'personal' ? '此 Cookie 仅用于个人中心。' : ''}</div>
    </>
  );
}

function CookieScriptModal({platform, onSave, onClose}) {
  const [script, setScript] = useState('');
  const [loginUrl, setLoginUrl] = useState('');
  const [cookie, setCookie] = useState('');
  useEffect(() => {
    api(`/api/cookies/script/${platform.key}`).then((data) => {
      setScript(data.script || '');
      setLoginUrl(data.login_url || '');
    }).catch((error) => setScript(`加载失败：${error.message}`));
  }, [platform.key]);
  return (
    <>
      <div className="modal-title"><Icon id="i-globe" />{platform.name} 浏览器获取</div>
      <div className="modal-sub">打开登录页完成登录后，在目标网站控制台运行脚本，再把 Cookie 粘贴到下方保存。</div>
      <div className="modal-toolbar">
        {loginUrl && <a className="btn btn-ghost btn-sm" href={loginUrl} target="_blank" rel="noopener noreferrer"><Icon id="i-extlink" className="icon icon-sm" />打开登录页</a>}
        <button className="btn btn-primary btn-sm" onClick={() => navigator.clipboard?.writeText(script)}><Icon id="i-copy" className="icon icon-sm" />复制脚本</button>
      </div>
      <pre className="code" style={{maxHeight: 140}}>{script || '加载中...'}</pre>
      <textarea className="cookie-modal-textarea" value={cookie} onChange={(event) => setCookie(event.target.value)} placeholder="粘贴 Cookie 字符串" />
      <div className="modal-actions">
        <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
        <button className="btn btn-primary btn-sm" onClick={() => { onSave(cookie); onClose(); }}>保存 Cookie</button>
      </div>
    </>
  );
}

const NOTIFICATION_SCENES = [
  ['download_completed', '下载完成'],
  ['download_failed', '下载失败/部分完成'],
  ['subscription_queued', '订阅发现并加入下载'],
  ['subscription_checked', '订阅检测发现缺失'],
];

const NOTIFICATION_CHANNELS = [
  ['telegram', 'Telegram'],
  ['bark', 'Bark'],
  ['serverchan', 'Server 酱'],
  ['pushplus', 'PushPlus'],
  ['wecom_robot', '企业微信机器人'],
  ['webhook', '通用 Webhook'],
];

function notificationServiceTemplate(type = 'telegram') {
  const label = NOTIFICATION_CHANNELS.find(([key]) => key === type)?.[1] || '通知渠道';
  return {
    id: `${type}-${Date.now().toString(36)}`,
    name: label,
    type,
    enabled: true,
    switchs: [],
    config: {},
  };
}

function NotificationSettings({notificationConfig, actions, busy}) {
  const [draft, setDraft] = useState(notificationConfig || {});
  const services = draft.services || [];
  useEffect(() => setDraft(notificationConfig || {}), [notificationConfig]);
  const update = (patch) => setDraft((prev) => ({...prev, ...patch}));
  const updateScene = (key, checked) => setDraft((prev) => ({...prev, scenes: {...(prev.scenes || {}), [key]: checked}}));
  const updateService = (id, patch) => setDraft((prev) => ({
    ...prev,
    services: (prev.services || []).map((item) => item.id === id ? {...item, ...patch} : item),
  }));
  const updateServiceConfig = (id, key, value) => setDraft((prev) => ({
    ...prev,
    services: (prev.services || []).map((item) => item.id === id ? {...item, config: {...(item.config || {}), [key]: value}} : item),
  }));
  const addService = () => update({services: [...services, notificationServiceTemplate('telegram')]});
  const removeService = (id) => update({services: services.filter((item) => item.id !== id)});
  return (
    <div className="glass glass-pad notification-card">
      <div className="panel-head">
        <h4>通知系统</h4>
        <div className="panel-actions">
          <button className="btn btn-ghost btn-tiny" disabled={busy.notifications} onClick={addService}><Icon id="i-plus" className="icon icon-sm" />添加渠道</button>
          <button className="btn btn-primary btn-tiny" disabled={busy.notifications} onClick={() => actions.saveNotifications(draft)}><BusyIcon busy={busy.notifications} icon="i-check" />保存通知</button>
        </div>
      </div>
      <label className="check-row"><input type="checkbox" checked={!!draft.enabled} onChange={(e) => update({enabled: e.target.checked})} /><span>启用通知系统</span></label>
      <div className="notification-scenes">
        {NOTIFICATION_SCENES.map(([key, label]) => (
          <label className="check-row" key={key}><input type="checkbox" checked={!!draft.scenes?.[key]} onChange={(e) => updateScene(key, e.target.checked)} /><span>{label}</span></label>
        ))}
      </div>
      <div className="notification-list">
        {services.length ? services.map((service) => (
          <NotificationServiceCard
            key={service.id}
            service={service}
            busy={busy}
            onChange={(patch) => updateService(service.id, patch)}
            onConfig={(key, value) => updateServiceConfig(service.id, key, value)}
            onRemove={() => removeService(service.id)}
            onTest={() => actions.testNotifications(service.id, service)}
          />
        )) : <div className="empty small"><Icon id="i-bell" />暂无通知渠道</div>}
      </div>
    </div>
  );
}

export function NotificationsPage({app}) {
  const {notificationConfig, actions, busy} = app;
  return <NotificationSettings notificationConfig={notificationConfig} actions={actions} busy={busy} />;
}

function NotificationServiceCard({service, busy, onChange, onConfig, onRemove, onTest}) {
  const cfg = service.config || {};
  const type = service.type || 'telegram';
  const channelOptions = NOTIFICATION_CHANNELS.map(([key, label]) => <option value={key} key={key}>{label}</option>);
  return (
    <div className="notification-service">
      <div className="notification-service-head">
        <input className="field-input" value={service.name || ''} onChange={(e) => onChange({name: e.target.value})} placeholder="渠道名称" />
        <select className="field-select" value={type} onChange={(e) => onChange({type: e.target.value, name: NOTIFICATION_CHANNELS.find(([key]) => key === e.target.value)?.[1] || service.name, config: {}})}>{channelOptions}</select>
        <label className="check-row compact"><input type="checkbox" checked={service.enabled !== false} onChange={(e) => onChange({enabled: e.target.checked})} /><span>启用</span></label>
      </div>
      <NotificationChannelFields type={type} config={cfg} onConfig={onConfig} />
      <div className="notification-actions">
        <button className="btn btn-ghost btn-tiny" disabled={busy[`notificationTest:${service.id}`]} onClick={onTest}><BusyIcon busy={busy[`notificationTest:${service.id}`]} icon="i-bell" />测试</button>
        <button className="btn btn-danger btn-tiny" onClick={onRemove}><Icon id="i-trash" className="icon icon-sm" />删除</button>
      </div>
    </div>
  );
}

function NotificationChannelFields({type, config, onConfig}) {
  const input = (key, label, placeholder = '') => (
    <div className="field-row"><label className="field-label">{label}</label><input className="field-input" value={config[key] || ''} onChange={(e) => onConfig(key, e.target.value)} placeholder={placeholder || config[`${key}_masked`] || ''} /></div>
  );
  if (type === 'telegram') return <>{input('bot_token', 'Bot Token')}{input('chat_id', 'Chat ID')}</>;
  if (type === 'bark') return <>{input('key', 'Bark Key')}{input('server', '服务器', 'https://api.day.app')}</>;
  if (type === 'serverchan') return <>{input('send_key', 'SendKey')}</>;
  if (type === 'pushplus') return <>{input('token', 'Token')}{input('topic', '群组编码', '可选')}</>;
  if (type === 'wecom_robot') return <>{input('key', '机器人 Key / Webhook URL')}</>;
  return (
    <>
      {input('url', 'Webhook URL')}
      <div className="field-row"><label className="field-label">Method</label><select className="field-select" value={config.method || 'POST'} onChange={(e) => onConfig('method', e.target.value)}><option value="POST">POST</option><option value="PUT">PUT</option><option value="GET">GET</option></select></div>
    </>
  );
}

export function SettingsPage({app}) {
  const {config, logs, actions, setModal, closeModal, busy, diagnostics} = app;
  const [downloadDir, setDownloadDir] = useState('');
  const [quality, setQuality] = useState('M4A 96K');
  const [downloadThreads, setDownloadThreads] = useState(16);
  useEffect(() => {
    setDownloadDir(config.download_dir || '');
    setQuality(config.quality || 'M4A 96K');
    setDownloadThreads(config.download_threads || 16);
  }, [config]);
  const openPassword = () => setModal({content: <PasswordModal onSubmit={actions.changePassword} onClose={closeModal} />});
  const confirmClear = () => setModal({content: <ConfirmModal icon="i-trash" title="清空服务端日志" message="会清空 logs 目录下的 .log 文件。服务端已启用日志轮转。" okText="清空日志" danger onClose={closeModal} onOk={() => { closeModal(); actions.clearLogs(); }} />});
  return (
    <>
      <div className="glass glass-pad settings-card">
        <div className="field-row"><label className="field-label">下载目录</label><input className="field-input" value={downloadDir} onChange={(e) => setDownloadDir(e.target.value)} placeholder="/path/to/downloads" /></div>
        <div className="field-row"><label className="field-label">默认音质</label><select className="field-select" value={quality} onChange={(e) => setQuality(e.target.value)}><option value="M4A 64K">M4A 64K（番茄畅听）</option><option value="M4A 96K">M4A 96K（标准）</option><option value="M4A 128K">M4A 128K（高品质）</option><option value="无损真人录制">无损真人录制（最高）</option></select></div>
        <div className="field-row"><label className="field-label">并发线程数</label><input className="field-input" type="number" min="1" max="64" value={downloadThreads} onChange={(e) => setDownloadThreads(Math.max(1, Math.min(64, parseInt(e.target.value) || 1)))} placeholder="1-64，默认16" style={{maxWidth:'120px'}} /></div>
        <div className="field-row"><label className="field-label">登录账号</label><div className="settings-account-actions"><button className="btn btn-ghost btn-sm" onClick={openPassword}><Icon id="i-key" className="icon icon-sm" />修改密码</button><button className="btn btn-danger btn-sm" onClick={actions.logoutAccount}><Icon id="i-close" className="icon icon-sm" />退出登录</button></div></div>
        <button className="btn btn-primary" disabled={busy.settings} onClick={() => actions.saveSettings({downloadDir, quality, downloadThreads})}><BusyIcon busy={busy.settings} icon="i-check" />保存设置</button>
      </div>
      <DiagnosticsPanel config={config} diagnostics={diagnostics} loading={busy.diagnostics} onLoad={actions.loadDiagnostics} />
      <div className="glass glass-pad settings-log-card">
        <div className="panel-head"><h4>最近日志</h4><div className="panel-actions"><button className="btn btn-ghost btn-tiny" onClick={() => actions.loadLogs()}><Icon id="i-refresh" className="icon icon-sm" />刷新</button><button className="btn btn-danger btn-tiny" onClick={confirmClear}><Icon id="i-trash" className="icon icon-sm" />清空</button></div></div>
        <pre className="code log-code">{logs.length ? logs.join('\n') : '切换到系统设置后自动加载。'}</pre>
      </div>
    </>
  );
}

export function ThemesPage() {
  return <ThemePicker />;
}

function ThemePicker() {
  const [theme, setTheme] = useState(savedTheme());
  const choose = (value) => {
    setTheme(value);
    persistTheme(value);
    applyTheme(value);
  };
  return (
    <div className="glass glass-pad theme-picker-wrap">
      <div className="panel-head"><h4>主题外观</h4></div>
      <div className="theme-picker">
        {THEMES.map((item) => (
          <button
            key={item.value}
            className={`theme-card ${theme === item.value ? 'active' : ''}`}
            style={{'--tc-a': item.colors[0], '--tc-b': item.colors[1], '--tc-c': item.colors[2]}}
            onClick={() => choose(item.value)}
          >
            <div className="tc-preview" />
            <div className="tc-name">{item.name}</div>
            <div className="tc-sub">{item.mode === 'light' ? '浅色' : '深色'}</div>
            <div className="tc-check">✓</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function DiagnosticsPanel({config, diagnostics, loading, onLoad}) {
  const data = diagnostics;
  const pathEntries = data?.paths ? Object.entries(data.paths) : [];
  return (
    <div className="glass glass-pad diagnostics-card">
      <div className="panel-head">
        <h4>服务诊断</h4>
        <button className="btn btn-ghost btn-tiny" disabled={loading} onClick={onLoad}><BusyIcon busy={loading} icon="i-refresh" />刷新诊断</button>
      </div>
      <div className="diag-summary">
        应用：{config.app || 'AudioFlow'} v{config.version || '-'}<br />
        访问保护：{config.auth_required ? '已启用' : '未启用'} · PWA：{config.pwa_enabled ? '已启用' : '未启用'} · Cookie 加密：{config.cookie_encryption_enabled ? '已启用' : '未启用'}
      </div>
      {data && (
        <div className="diag-grid">
          <div>ffmpeg：{data.binaries?.ffmpeg?.available ? '可用' : '不可用'}</div>
          <div>前端构建：{data.frontend?.index_exists ? '已生成' : '未生成'}</div>
          <div>任务记录：{data.runtime?.tasks_count || 0} 条</div>
          <div>订阅调度：{data.runtime?.scheduler?.started ? '已启动' : '未启动'}</div>
          {pathEntries.map(([key, item]) => <div key={key}>{key}：{item.exists && item.writable ? '可写' : '异常'} · {item.path}</div>)}
        </div>
      )}
    </div>
  );
}

function PasswordModal({onSubmit, onClose}) {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const tooShort = newPassword.length > 0 && newPassword.length < 6;
  const mismatch = confirm.length > 0 && newPassword !== confirm;
  const valid = newPassword.length >= 6 && newPassword === confirm;
  const save = () => {
    if (!valid) return;
    onSubmit({oldPassword, newPassword});
    onClose();
  };
  return (
    <>
      <div className="modal-title"><Icon id="i-key" />修改登录密码</div>
      <div className="modal-sub">默认密码为 admin。修改成功后会自动退出登录，请使用新密码重新进入。</div>
      <div className="field-row"><label className="field-label">当前密码</label><input className="field-input" type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} /></div>
      <div className="field-row"><label className="field-label">新密码</label><input className="field-input" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="至少 6 位" /></div>
      {tooShort && <div className="field-hint err">密码不能少于 6 位</div>}
      <div className="field-row"><label className="field-label">确认新密码</label><input className="field-input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></div>
      {mismatch && <div className="field-hint err">两次输入的密码不一致</div>}
      <div className="modal-actions"><button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button><button className="btn btn-primary btn-sm" disabled={!valid} onClick={save}>保存密码</button></div>
    </>
  );
}
