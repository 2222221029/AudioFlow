import '../styles/desktop.css';
import {Icon, IconSprite} from '../components/Icons.jsx';
import {AppLogo} from '../components/AppLogo.jsx';
import {MiniPlayer} from '../components/Player.jsx';
import {useAudioFlowApp} from '../hooks/useAudioFlowApp.js';
import {
  AlbumDetail,
  CookiesPage,
  DownloadsPage,
  LoginModal,
  Modal,
  NotificationsPage,
  PersonalPage,
  PlatformSelect,
  ResultCard,
  SettingsPage,
  SubscriptionsPage,
  ThemesPage,
  Toast,
} from '../components/Shared.jsx';
import {MetaScraperPage} from '../components/MetaScraper.jsx';
import {SEARCH_PLATFORMS} from '../utils/platforms.js';

const NAV = [
  ['search', 'i-search', '聚合搜索'],
  ['downloads', 'i-download', '下载管理'],
  ['subscriptions', 'i-star', '订阅管理'],
  ['personal', 'i-user', '个人中心'],
  ['cookies', 'i-cookie', '账号管理'],
  ['notifications', 'i-bell', '通知系统'],
  ['themes', 'i-palette', '主题外观'],
  ['meta', 'i-tag', '元数据刮削'],
  ['settings', 'i-settings', '系统设置'],
];

export default function DesktopPage() {
  const app = useAudioFlowApp();
  const {page, setPage, query, setQuery, platform, setPlatform, downloads, config, actions, busy, metrics, status} = app;
  const switchPage = (id) => {
    setPage(id);
    if (id === 'downloads') actions.loadDownloads();
    if (id === 'subscriptions') actions.loadSubscriptions();
    if (id === 'cookies') actions.loadCookies();
    if (id === 'notifications') actions.loadNotifications();
    if (id === 'settings') {
      actions.loadConfig();
      actions.loadLogs();
      actions.loadEvents();
    }
  };

  // 连接状态标签
  const statusLabel = status === '就绪' ? '在线' : status === '搜索中' ? '搜索中' : status === '错误' ? '离线' : status;
  const statusClass = status === '错误' ? 'danger' : status === '就绪' ? 'ok' : 'warn';

  return (
    <>
      <IconSprite />
      <div className="app">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-logo"><AppLogo /></div>
            <div className="brand-text"><div className="brand-title">AudioFlow</div><div className="brand-sub">多平台 · Web 版</div></div>
          </div>
          <nav className="nav">
            {NAV.map(([id, icon, label]) => (
              <button key={id} className={`nav-item ${page === id ? 'active' : ''}`} onClick={() => switchPage(id)}>
                <Icon id={icon} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
          <div className="nav-foot">
            <div><b>状态</b> <span className={`status-text-${statusClass}`}>{statusLabel} v{config.version || '-'}</span></div>
            <div style={{marginTop: 4, display: 'flex', gap: 8, flexWrap: 'wrap'}}>
              <span title="活跃">▶ {metrics.activeDownloads}</span>
              <span title="已完成" style={{color: 'var(--success)'}}>✓ {metrics.completedDownloads}</span>
              {metrics.failedDownloads > 0 && <span title="失败" style={{color: 'var(--danger)'}}>✗ {metrics.failedDownloads}</span>}
            </div>
            <div style={{marginTop: 8}}><a href="/?v=m" style={{color: 'var(--primary)', display: 'inline-flex', alignItems: 'center', gap: 4}}><Icon id="i-mobile" className="icon icon-sm" />切换移动版</a></div>
          </div>
        </aside>

        <header className="topbar">
          <div className="search-wrap">
            <span className="search-icon"><Icon id="i-search" /></span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && actions.doSearch()} className="search-input" placeholder="搜书名、主播、专辑 ID 或分享链接，回车搜索" />
          </div>
          <PlatformSelect platform={platform} setPlatform={setPlatform} />
          <button className="btn btn-primary" disabled={busy.search} onClick={actions.doSearch}>{busy.search ? <span className="loading" /> : <Icon id="i-search" className="icon icon-sm" />}搜索</button>
        </header>

        <main className="main">
          {page === 'search' && <SearchPage app={app} />}
          {page === 'downloads' && <PageShell title="下载管理" subtitle="实时下载进度、失败重试、并发与目录" action={<button className="btn btn-ghost btn-sm" onClick={actions.loadDownloads}><Icon id="i-refresh" className="icon icon-sm" />刷新</button>}><DownloadsPage app={app} /></PageShell>}
          {page === 'subscriptions' && <PageShell title="订阅管理" subtitle="追更喜欢的专辑，新章节自动加入下载队列" action={<button className="btn btn-ghost btn-sm" onClick={() => actions.loadSubscriptions({refreshLocal: true})}><Icon id="i-refresh" className="icon icon-sm" />刷新</button>}><SubscriptionsPage app={app} /></PageShell>}
          {page === 'personal' && <PageShell title="个人中心" subtitle="查看各平台的收听历史、收藏、订阅、已购"><PersonalPage app={app} /></PageShell>}
          {page === 'cookies' && <PageShell title="账号管理" subtitle="为各平台提供登录态，支持扫码、浏览器抓取与手动粘贴" action={<button className="btn btn-ghost btn-sm" onClick={actions.loadCookies}><Icon id="i-refresh" className="icon icon-sm" />刷新状态</button>}><CookiesPage app={app} /></PageShell>}
          {page === 'notifications' && <PageShell title="通知系统" subtitle="配置下载、订阅等事件的外部推送渠道" action={<button className="btn btn-ghost btn-sm" onClick={actions.loadNotifications}><Icon id="i-refresh" className="icon icon-sm" />刷新配置</button>}><NotificationsPage app={app} /></PageShell>}
          {page === 'themes' && <PageShell title="主题外观" subtitle="切换桌面端与移动端共用的界面主题"><ThemesPage /></PageShell>}
          {page === 'settings' && <PageShell title="系统设置" subtitle="下载目录、音质偏好、账号密码、服务端日志"><SettingsPage app={app} /></PageShell>}
          {page === 'meta' && <PageShell title="元数据刮削" subtitle="为本地有声书写入专辑标题、作者、封面、标签等元数据"><MetaScraperPage /></PageShell>}
        </main>
      </div>
      <MiniPlayer app={app} />
      {app.loginVisible && <LoginModal onSubmit={app.submitLogin} error={app.loginError} loading={app.loginLoading} />}
      <Modal modal={app.modal} onClose={app.closeModal} />
      <Toast toast={app.toast} />
    </>
  );
}

function PageShell({title, subtitle, action, children}) {
  return (
    <section className="page active">
      <div className="page-head"><div><div className="page-title">{title}</div><div className="page-sub">{subtitle}</div></div>{action}</div>
      {children}
    </section>
  );
}

function SearchPage({app}) {
  const {results, selectedAlbum, subscriptions, metrics, config, status, actions, searchHistory, query, setQuery} = app;
  const platformCount = Math.max(SEARCH_PLATFORMS.length - 1, 0);

  const handleHistoryClick = (keyword) => {
    setQuery(keyword);
    setTimeout(actions.doSearch, 0);
  };

  return (
    <PageShell title="聚合搜索" subtitle={`跨 ${platformCount} 个平台一次性搜索，按可下载状态与章节完整度排序`}>
      {/* 搜索历史 */}
      {searchHistory.length > 0 && (
        <div className="search-history">
          <span className="search-history-label">历史：</span>
          {searchHistory.map((kw) => (
            <button key={kw} className="history-chip" onClick={() => handleHistoryClick(kw)}>{kw}</button>
          ))}
          <button className="history-clear" onClick={actions.clearSearchHistory}>清除</button>
        </div>
      )}

      <div className="metrics">
        <div className="metric"><div className="metric-label">搜索结果</div><div className="metric-value">{results.length}</div><div className="metric-foot">本次返回条目</div></div>
        <div className="metric"><div className="metric-label">活跃下载</div><div className="metric-value">{metrics.activeDownloads}</div><div className="metric-foot">正在进行</div></div>
        <div className="metric"><div className="metric-label">已订阅</div><div className="metric-value">{subscriptions.length}</div><div className="metric-foot">自动追更专辑</div></div>
        <div className="metric"><div className="metric-label">服务端</div><div className="metric-value" style={{fontSize: 18}}>{status}</div><div className="metric-foot">版本 {config.version || '-'}</div></div>
      </div>
      <div className="results-grid">
        <div className="glass" style={{overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
          <div style={{padding: '14px 16px 6px', display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
            <strong style={{fontSize: 13.5}}>搜索结果</strong>
            <span style={{fontSize: 11.5, color: 'var(--text-mute)'}}>{results.length ? `共 ${results.length} 条` : '输入关键词开始'}</span>
          </div>
          <div className="result-list glass-pad" style={{paddingTop: 6}}>
            {!results.length ? (
              <div className="empty"><Icon id="i-search" />请输入关键词后回车<br />例如：凡人修仙传 / 主播名 / 链接</div>
            ) : results.map((album, index) => <ResultCard key={`${album.platform}-${album.id || album.title}-${index}`} album={album} onOpen={() => actions.openAlbum(album)} />)}
          </div>
        </div>
        <div className="glass glass-pad" style={{overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
          <AlbumDetail app={app} />
        </div>
        <div className="glass glass-pad detail-aside">
          {!selectedAlbum ? (
            <div className="empty"><Icon id="i-music" />选择专辑后<br />在此查看简介</div>
          ) : (
            <AlbumInfoPanel album={selectedAlbum} />
          )}
        </div>
      </div>
    </PageShell>
  );
}

function AlbumInfoPanel({album}) {
  const fields = [
    ['作者', album.author || album.anchor],
    ['状态', album.status],
    ['章节', album.episodes != null ? `${album.episodes} 章` : album.chapter_count != null ? `${album.chapter_count} 章` : album.track_count != null ? `${album.track_count} 章` : null],
    ['分类', album.category || album.classify],
    ['播放', album.play_count != null ? `${Number(album.play_count).toLocaleString()} 次` : null],
    ['评分', album.score || album.rating],
    ['更新', album.last_update || album.update_time],
  ].filter(([, v]) => v != null && v !== '');
  return (
    <div style={{fontSize: 13, lineHeight: 2}}>
      <div style={{fontWeight: 700, marginBottom: 8, fontSize: 14}}>{album.title || '专辑信息'}</div>
      {fields.map(([label, value]) => (
        <div key={label} style={{display: 'flex', gap: 8}}>
          <span style={{color: 'var(--text-faint)', flex: '0 0 3em'}}>{label}</span>
          <span style={{color: 'var(--text-mute)', wordBreak: 'break-all'}}>{String(value)}</span>
        </div>
      ))}
      {album.intro && (
        <div style={{marginTop: 10, color: 'var(--text-mute)', fontSize: 12.5, lineHeight: 1.7, display: '-webkit-box', WebkitLineClamp: 8, WebkitBoxOrient: 'vertical', overflow: 'hidden'}}>
          {album.intro}
        </div>
      )}
    </div>
  );
}
