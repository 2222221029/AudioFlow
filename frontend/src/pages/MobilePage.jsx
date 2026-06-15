import '../styles/mobile.css';
import {useEffect, useMemo, useState} from 'react';
import {Icon, IconSprite} from '../components/Icons.jsx';
import {AppLogo} from '../components/AppLogo.jsx';
import {MiniPlayer} from '../components/Player.jsx';
import {useAudioFlowApp} from '../hooks/useAudioFlowApp.js';
import {isStandalonePwa, promptInstall, setupInstallPrompt} from '../utils/pwa.js';
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

const TABS = [
  ['discover', 'i-search', '发现'],
  ['downloads', 'i-download', '下载'],
  ['subscriptions', 'i-bookmark', '订阅'],
  ['personal', 'i-user', '个人'],
  ['more', 'i-more', '更多'],
];

const VIEW_META = {
  discover: {title: '发现', subtitle: '跨平台搜索有声书', icon: 'i-search'},
  downloads: {title: '下载', subtitle: '任务队列与进度', icon: 'i-download'},
  subscriptions: {title: '订阅', subtitle: '追更与自动补全', icon: 'i-bookmark'},
  cookies: {title: 'Cookie', subtitle: '平台登录态管理', icon: 'i-cookie', parent: 'more'},
  personal: {title: '个人中心', subtitle: '历史、收藏与书架', icon: 'i-user'},
  notifications: {title: '通知', subtitle: '下载、订阅与系统推送', icon: 'i-bell', parent: 'more'},
  themes: {title: '主题', subtitle: '界面主题与外观', icon: 'i-palette', parent: 'more'},
  settings: {title: '设置', subtitle: '目录、音质与日志', icon: 'i-settings', parent: 'more'},
  meta: {title: '元数据刮削', subtitle: '写入标题、作者、封面等元数据', icon: 'i-tag', parent: 'more'},
  more: {title: '更多', subtitle: '账号、设置与系统工具', icon: 'i-more'},
};

function MobileHeader({app, installable, switchView, searchAndShowResults}) {
  const {mobileView, query, setQuery, platform, setPlatform, busy, actions} = app;
  const meta = VIEW_META[mobileView] || VIEW_META.discover;
  const showBack = Boolean(meta.parent);
  const title = meta.title;

  return (
    <header className="native-topbar">
      <div className="native-status-row">
        <button className="native-brand" onClick={() => switchView('discover')} aria-label="回到发现">
          <AppLogo className="native-brand-logo" />
          <span>AudioFlow</span>
        </button>
        <div className="native-top-actions">
          <button className="native-icon-btn" onClick={() => switchView('settings')} title="系统设置"><Icon id="i-settings" /></button>
        </div>
      </div>

      <div className="native-title-row">
        {showBack && <button className="native-back" onClick={() => switchView(meta.parent)}><Icon id="i-arrow-left" /></button>}
        <div className="native-title-copy">
          <h1>{title}</h1>
          <p>{meta.subtitle}</p>
        </div>
        {mobileView === 'downloads' && <button className="native-chip-btn" onClick={actions.loadDownloads}><Icon id="i-refresh" />刷新</button>}
        {mobileView === 'subscriptions' && <button className="native-chip-btn" onClick={() => actions.loadSubscriptions({refreshLocal: true})}><Icon id="i-refresh" />刷新</button>}
        {mobileView === 'cookies' && <button className="native-chip-btn" onClick={actions.loadCookies}><Icon id="i-refresh" />刷新</button>}
        {mobileView === 'notifications' && <button className="native-chip-btn" onClick={actions.loadNotifications}><Icon id="i-refresh" />刷新</button>}
        {mobileView === 'settings' && <button className="native-chip-btn" onClick={actions.loadDiagnostics}><Icon id="i-refresh" />诊断</button>}
        {mobileView === 'more' && (installable || !isStandalonePwa()) && (
          <button className="native-chip-btn" onClick={installable ? promptInstall : undefined}><Icon id="i-mobile" />安装</button>
        )}
      </div>

      {mobileView === 'discover' && (
        <>
          <div className="native-search">
            <Icon id="i-search" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && searchAndShowResults()}
              type="search"
              placeholder="书名、主播、专辑 ID 或分享链接"
            />
            <button disabled={busy.search} onClick={searchAndShowResults}>{busy.search ? <span className="loading" /> : '搜索'}</button>
          </div>
          <PlatformSelect platform={platform} setPlatform={setPlatform} mobile />
        </>
      )}
    </header>
  );
}

function DiscoverView({app, switchView, searchAndShowResults}) {
  const {query, setQuery, results, actions, metrics, subscriptions, status, busy, searchHistory} = app;
  const handleHistoryClick = (keyword) => {
    setQuery(keyword);
    setTimeout(actions.doSearch, 0);
  };
  const quickStats = [
    ['搜索结果', results.length],
    ['活跃下载', metrics.activeDownloads],
    ['已订阅', subscriptions.length],
    ['状态', status],
  ];

  return (
    <section className="view native-view active">
      <div className="native-stats">
        {quickStats.map(([label, value]) => (
          <div className="native-stat" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>

      <div className="native-shortcuts">
        <button onClick={() => switchView('personal')}><Icon id="i-user" /><span>个人</span></button>
        <button onClick={() => switchView('downloads')}><Icon id="i-download" /><span>任务</span></button>
        <button onClick={() => switchView('subscriptions')}><Icon id="i-bookmark" /><span>追更</span></button>
        <button onClick={() => switchView('notifications')}><Icon id="i-bell" /><span>通知</span></button>
      </div>

      {searchHistory.length > 0 && (
        <div className="native-section">
          <div className="native-section-head">
            <span>最近搜索</span>
            <button onClick={actions.clearSearchHistory}>清除</button>
          </div>
          <div className="native-token-row">
            {searchHistory.map((kw) => <button key={kw} onClick={() => handleHistoryClick(kw)}>{kw}</button>)}
          </div>
        </div>
      )}

      <div className="native-section">
        <div className="native-section-head">
          <span>搜索结果</span>
          <em>{results.length ? `${results.length} 条` : '输入关键词后搜索'}</em>
        </div>
        {busy.search
          ? <div className="empty"><span className="loading" /> 正在搜索</div>
          : !results.length
            ? <div className="empty"><Icon id="i-search" />开始搜索发现有声书</div>
            : results.map((album, index) => (
              <ResultCard key={`${album.platform}-${album.id || album.title}-${index}`} album={album} mobile onOpen={() => actions.openAlbum(album)} />
            ))}
      </div>
    </section>
  );
}

function MoreView({app, installable, switchView}) {
  const {metrics, subscriptions, config, status, actions} = app;
  const cards = [
    {id: 'cookies', icon: 'i-cookie', title: 'Cookie 管理', sub: '扫码、浏览器抓取、手动粘贴'},
    {id: 'notifications', icon: 'i-bell', title: '通知系统', sub: '下载、订阅与外部推送'},
    {id: 'themes', icon: 'i-palette', title: '主题外观', sub: '界面主题与配色'},
    {id: 'settings', icon: 'i-settings', title: '系统设置', sub: '目录、音质、日志'},
    {id: 'meta', icon: 'i-tag', title: '元数据刮削', sub: '写入有声书标题、作者、封面'},
    {id: 'downloads', icon: 'i-download', title: '下载管理', sub: `${metrics.activeDownloads} 个活跃任务`},
    {id: 'subscriptions', icon: 'i-bookmark', title: '订阅管理', sub: `${subscriptions.length} 个订阅专辑`},
  ];
  return (
    <section className="view native-view active">
      <div className="native-profile-card">
        <div className="native-profile-logo"><AppLogo /></div>
        <div>
          <strong>AudioFlow</strong>
          <span>{status} · v{config.version || '-'}</span>
        </div>
      </div>

      <div className="native-menu-list">
        {cards.map((item) => (
          <button key={item.id} onClick={() => switchView(item.id)}>
            <span className="native-menu-icon"><Icon id={item.icon} /></span>
            <span><strong>{item.title}</strong><em>{item.sub}</em></span>
            <Icon id="i-arrow-right" className="icon icon-sm" />
          </button>
        ))}
        <a href="/?v=d" className="native-menu-link">
          <span className="native-menu-icon"><Icon id="i-monitor" /></span>
          <span><strong>桌面版</strong><em>切换到完整宽屏界面</em></span>
          <Icon id="i-arrow-right" className="icon icon-sm" />
        </a>
        {(installable || !isStandalonePwa()) && (
          <button onClick={installable ? promptInstall : undefined}>
            <span className="native-menu-icon"><Icon id="i-mobile" /></span>
            <span><strong>安装到主屏幕</strong><em>{installable ? '作为独立 App 打开' : 'iOS 请用分享菜单添加'}</em></span>
            <Icon id="i-arrow-right" className="icon icon-sm" />
          </button>
        )}
      </div>

      <div className="native-danger-row">
        <button onClick={actions.logoutAccount}><Icon id="i-close" />退出登录</button>
      </div>
    </section>
  );
}

function RoutedContent({app, installable, switchView, searchAndShowResults}) {
  const {mobileView} = app;
  if (mobileView === 'discover') return <DiscoverView app={app} switchView={switchView} searchAndShowResults={searchAndShowResults} />;
  if (mobileView === 'downloads') return <section className="view native-view active"><DownloadsPage app={app} /></section>;
  if (mobileView === 'subscriptions') return <section className="view native-view active"><SubscriptionsPage app={app} /></section>;
  if (mobileView === 'cookies') return <section className="view native-view active"><CookiesPage app={app} /></section>;
  if (mobileView === 'personal') return <section className="view native-view active"><PersonalPage app={app} mobile /></section>;
  if (mobileView === 'notifications') return <section className="view native-view active"><NotificationsPage app={app} /></section>;
  if (mobileView === 'themes') return <section className="view native-view active"><ThemesPage /></section>;
  if (mobileView === 'settings') return <section className="view native-view active"><SettingsPage app={app} /></section>;
  if (mobileView === 'meta') return <section className="view native-view active"><MetaScraperPage /></section>;
  return <MoreView app={app} installable={installable} switchView={switchView} />;
}

export default function MobilePage() {
  const app = useAudioFlowApp();
  const [installable, setInstallable] = useState(false);
  const {mobileView, setMobileView, actions} = app;
  useEffect(() => setupInstallPrompt(setInstallable), []);

  const activeTab = useMemo(() => {
    const meta = VIEW_META[mobileView];
    return meta?.parent || mobileView;
  }, [mobileView]);

  const switchView = (id) => {
    const next = id === 'accounts' ? 'cookies' : id;
    setMobileView(next);
    if (next === 'downloads') actions.loadDownloads();
    if (next === 'subscriptions') actions.loadSubscriptions();
    if (next === 'cookies') actions.loadCookies();
    if (next === 'notifications') actions.loadNotifications();
    if (next === 'settings') {
      actions.loadConfig();
      actions.loadLogs(100);
      actions.loadEvents();
    }
  };

  const searchAndShowResults = () => {
    setMobileView('discover');
    setTimeout(actions.doSearch, 0);
  };

  return (
    <>
      <IconSprite />
      <div className="shell native-shell">
        <MobileHeader app={app} installable={installable} switchView={switchView} searchAndShowResults={searchAndShowResults} />
        <RoutedContent app={app} installable={installable} switchView={switchView} searchAndShowResults={searchAndShowResults} />
      </div>

      {app.selectedAlbum && (
        <div className="detail-page show native-detail-page">
          <div className="detail-head">
            <button className="icon-btn" onClick={app.actions.closeAlbum}><Icon id="i-arrow-left" /></button>
            <div className="title">专辑详情</div>
            <button className="icon-btn" onClick={() => { app.actions.closeAlbum(); switchView('more'); }} title="更多"><Icon id="i-more" /></button>
          </div>
          <AlbumDetail app={app} mobile />
        </div>
      )}

      <nav className="tabbar native-tabbar">
        {TABS.map(([id, icon, label]) => (
          <button key={id} className={`tab-item ${activeTab === id ? 'active' : ''}`} onClick={() => switchView(id)}>
            <span className="tab-icon-wrap"><Icon id={icon} /></span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <MiniPlayer app={app} mobile />
      {app.loginVisible && <LoginModal onSubmit={app.submitLogin} error={app.loginError} loading={app.loginLoading} />}
      <Modal modal={app.modal} onClose={app.closeModal} />
      <Toast toast={app.toast} />
    </>
  );
}
