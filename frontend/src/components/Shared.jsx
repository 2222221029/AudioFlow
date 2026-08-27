import {useEffect, useMemo, useRef, useState} from 'react';
import {Icon} from './Icons.jsx';
import {AppLogo} from './AppLogo.jsx';
import PlatformLogo, {PlatformTag} from './PlatformLogo.jsx';
import {albumEpisodeText, chapterId, chapterStatusText, chapterTitle, coverOf, fmtDuration, taskStatusText} from '../utils/format.js';
import {COOKIE_PLATFORMS, NO_COOKIE_KEYS, PERSONAL_FEATURES, SEARCH_PLATFORMS} from '../utils/platforms.js';
import {applyTheme, persistTheme, savedTheme, THEMES} from '../utils/themes.js';
import {api} from '../services/api.js';

const XMLY_MOBILE_INTERFACE = '喜马拉雅移动端接口（自动最高音质）';
const XMLY_WEB_INTERFACE = '喜马拉雅网页版接口';
const XMLY_MOBILE_QUALITY_OPTIONS = [
  {value: XMLY_MOBILE_INTERFACE, label: '自动最佳（无损 → 128/64/24K）'},
  {value: '杜比全景声优先（自动降级）', label: '杜比全景声优先（推荐）'},
  {value: 'Audio Vivid 优先（自动降级）', label: 'Audio Vivid 优先（推荐）'},
  {value: '无损优先（自动降级）', label: '无损优先（推荐）'},
  {value: '杜比全景声', label: '仅杜比全景声（严格）'},
  {value: 'Audio Vivid 菁彩声', label: '仅 Audio Vivid（严格）'},
  {value: '无损真人录制', label: '仅无损音质（严格）'},
  {value: 'M4A 128K', label: 'M4A 128/96K（level 2）'},
  {value: 'M4A 64K', label: 'M4A 64K（level 1）'},
  {value: 'M4A 24K', label: 'M4A 24K（level 0）'},
];
const XMLY_SUBSCRIPTION_QUALITY_OPTIONS = [
  {value: XMLY_WEB_INTERFACE, label: '网页版接口（默认）'},
  {value: XMLY_MOBILE_INTERFACE, label: '移动端 V4 · 自动最高音质'},
  {value: '杜比全景声优先（自动降级）', label: '移动端 V4 · 杜比全景声优先'},
  {value: '无损优先（自动降级）', label: '移动端 V4 · 无损优先'},
];
const XMLY_MOBILE_QUALITY_HELP = {
  [XMLY_MOBILE_INTERFACE]: '按无损、128/96K、64K、24K 的顺序选择该曲目可用的最高传统音质；不会自动改选空间音频。',
  '杜比全景声优先（自动降级）': '每集按杜比全景声 → 无损 → 128/96K → 64K → 24K 下载。专辑中没有全景声的单集会立即降级，文件名按实际音质标记。',
  'Audio Vivid 优先（自动降级）': '每集按 Audio Vivid → 杜比全景声 → 无损 → 128/96K → 64K → 24K 下载。文件名按实际音质标记。',
  '无损优先（自动降级）': '每集按无损 → 128/96K → 64K → 24K 下载。没有无损的单集会立即降级。',
  '杜比全景声': '严格请求 level 12，不可用时不会降级。文件为 E-AC-3 M4A，Windows 默认播放器可能不支持，请使用兼容播放器。',
  'Audio Vivid 菁彩声': '严格请求 level 13，不可用时不会降级。需要支持 Audio Vivid / AVS3-P3 的播放器。',
  '无损真人录制': '严格请求 level 3，不可用时不会降级；实际文件可能是 WAV、FLAC 或 M4A。',
  'M4A 128K': '严格请求移动端 level 2；部分旧资源可能标记为约 96K。',
  'M4A 64K': '严格请求移动端 level 1。',
  'M4A 24K': '严格请求移动端 level 0。',
};

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement('textarea');
  input.value = text;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  if (!copied) throw new Error('浏览器不支持剪贴板写入');
}

export function Toast({toast}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return undefined;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), 2400);
    return () => clearTimeout(timer);
  }, [toast]);
  const isError = toast?.kind === 'err';
  return <div className={`toast ${visible ? 'show' : ''} ${toast?.kind || 'ok'}`} role={isError ? 'alert' : 'status'} aria-live={isError ? 'assertive' : 'polite'} aria-atomic="true">{toast?.message || ''}</div>;
}

export function Modal({modal, onClose}) {
  const dialogRef = useRef(null);
  const previousFocusRef = useRef(null);
  useEffect(() => {
    if (!modal) return undefined;
    previousFocusRef.current = document.activeElement;
    const dialog = dialogRef.current;
    const title = dialog?.querySelector('.modal-title');
    if (title) title.id = 'audioflow-modal-title';
    const focusable = () => [...(dialog?.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])];
    (focusable()[0] || dialog)?.focus();
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && modal.close !== false) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previousFocusRef.current?.focus?.();
    };
  }, [modal, onClose]);
  if (!modal) return null;
  return (
    <div className="modal-backdrop show" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`modal ${modal.className || ''}`} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="audioflow-modal-title" aria-label="操作对话框" tabIndex={-1}>
        {modal.close !== false && (
          <button className="modal-close-btn" onClick={onClose} title="关闭" aria-label="关闭对话框">
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
    <div className="login-overlay show" role="dialog" aria-modal="true" aria-labelledby="audioflow-login-title">
      <form className="login-card" onSubmit={(event) => { event.preventDefault(); onSubmit({username, password}); }}>
        <div className="login-brand">
          <div className="login-logo"><AppLogo /></div>
          <div>
            <div className="login-title" id="audioflow-login-title">AudioFlow</div>
            <div className="login-sub">登录后继续管理下载与订阅</div>
          </div>
        </div>
        <label className="login-field">
          <span>账号</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" autoFocus />
        </label>
        <label className="login-field">
          <span>密码</span>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" placeholder="默认密码 admin" />
        </label>
        <div className="login-error" role="alert" aria-live="polite">{error}</div>
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
  const library = album.library || {};
  const localTotal = Number(library.total || album.episodes || album.chapter_count || 0);
  const localDownloaded = Number(library.downloaded || 0);
  const localText = localTotal > 0 && localDownloaded >= localTotal
    ? '本地已下载'
    : `本地 ${localDownloaded}/${localTotal || '?'}`;
  return (
    <button className={mobile ? 'result-card' : 'result-row'} onClick={onOpen}>
      <div className="result-cover" style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>
        {cover ? '' : <Icon id="i-headphone" className="icon icon-lg" />}
      </div>
      <div className="result-info">
        <div className="result-title">{album.title || '未知专辑'}</div>
        <div className="result-platform">
          <PlatformTag value={album.platform} />
          {library.subscribed && <span className="library-badge subscribed">已订阅</span>}
          {(library.subscribed || localDownloaded > 0) && <span className={`library-badge ${localTotal > 0 && localDownloaded >= localTotal ? 'complete' : ''}`}>{localText}</span>}
        </div>
        <div className="result-meta">{album.author || album.anchor || '未知作者'} · {albumEpisodeText(album)}</div>
      </div>
    </button>
  );
}

function ChapterStatusBadge({status = 'pending', error = ''}) {
  return <span className={`chapter-status-badge chapter-status-${status}`} title={error || chapterStatusText(status)}>{chapterStatusText(status)}</span>;
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
            <ChapterStatusBadge status={chapter.download_status || 'pending'} error={chapter.download_error} />
            <button className="icon-btn" onClick={(event) => { event.stopPropagation(); onPlay(chapter); }} title="试听" aria-label={`试听 ${chapterTitle(chapter)}`}><Icon id="i-play" /></button>
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

function ChapterPageJump({page, totalPages, loading, onSelect}) {
  const [draft, setDraft] = useState(String(page));

  useEffect(() => setDraft(String(page)), [page, totalPages]);

  const jump = () => {
    const target = Math.min(totalPages, Math.max(1, Number.parseInt(draft, 10) || page));
    setDraft(String(target));
    if (target !== page) onSelect(target);
  };

  return (
    <form className="chapter-page-jump" onSubmit={(event) => { event.preventDefault(); jump(); }}>
      <span>第</span>
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={draft}
        disabled={loading}
        aria-label={`当前第 ${page} 页，输入目标页码`}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => setDraft(event.target.value.replace(/\D/g, ''))}
        onBlur={jump}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setDraft(String(page));
            event.currentTarget.select();
          }
        }}
      />
      <span>页</span>
    </form>
  );
}

function ChapterToolbar({loading, busy, chapters, viewChapters, selectedChapterList, chapterPagination, chapterSort, setChapterSort, downloadRange, setDownloadRange, subscribed, actions}) {
  const [showRange, setShowRange] = useState(false);
  const pg = chapterPagination || {page: 1, total_pages: 1, total: chapters.length, has_more: false};
  const totalPages = Math.max(1, Number(pg.total_pages) || 1);
  const totalKnown = pg.total_known !== false;
  const canNext = pg.has_more || pg.page < totalPages;
  return (
    <div className="chapter-toolbar">
      {/* 主操作 */}
      <button className="btn btn-primary btn-sm" disabled={busy.download || loading} onClick={() => actions.startDownload()}><BusyIcon busy={busy.download} icon="i-download" />下载选中</button>
      <button className="btn btn-ghost btn-sm" disabled={busy.download || loading || !viewChapters.length} onClick={() => actions.startDownload([], {all: true})}><Icon id="i-bolt" className="icon icon-sm" />下载全部</button>
      <button className="btn btn-ghost btn-sm" disabled={busy.subscribe || loading || subscribed} onClick={actions.subscribeAlbum}><BusyIcon busy={busy.subscribe} icon={subscribed ? 'i-check' : 'i-star'} />{subscribed ? '已订阅' : '订阅追更'}</button>
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
      {(pg.total_pages > 1 || pg.has_more) && (
        <div className="chapter-pager">
          <button className="icon-btn" disabled={loading || pg.page <= 1} onClick={() => actions.loadChapterPage(pg.page - 1)} title="上一页" aria-label="上一页"><Icon id="i-arrow-left" /></button>
          {totalKnown ? (
            <>
              <ChapterPageJump page={pg.page} totalPages={totalPages} loading={loading} onSelect={actions.loadChapterPage} />
              <span className="chapter-page-total">/ {totalPages}</span>
            </>
          ) : <span className="chapter-page-unknown">第 {pg.page} 页 / 更多</span>}
          <button className="icon-btn" disabled={loading || !canNext} onClick={() => actions.loadChapterPage(pg.page + 1)} title="下一页" aria-label="下一页"><Icon id="i-arrow-right" /></button>
        </div>
      )}
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
  const {selectedAlbum, displayChapters, chapters, chapterPagination, selectedChapters, selectedChapterList, voices, selectedVoice, downloadQuality, setDownloadQuality, ximalayaInterface, setXimalayaInterface, subscriptionQuality, chapterSort, setChapterSort, downloadRange, setDownloadRange, actions, busy} = app;
  if (!selectedAlbum) return <div className="empty" id="detailEmpty"><Icon id="i-music" />选择结果查看详情</div>;
  const cover = coverOf(selectedAlbum);
  const library = selectedAlbum.library || {};
  const libraryTotal = Number(library.total || chapters.length || selectedAlbum.episodes || 0);
  const libraryDownloaded = Number(library.downloaded || 0);
  const loading = busy.album || busy.voice;
  const viewChapters = displayChapters || chapters;
  return (
    <div className={mobile ? 'detail-content' : 'album-detail'} style={{display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1}}>
      <div className={mobile ? 'detail-hero' : 'album-hero'}>
        <div className={mobile ? 'detail-cover' : 'album-cover'} style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>{cover ? '' : <Icon id="i-music" />}</div>
        <div className={mobile ? 'detail-info' : 'album-info'}>
          <div className={mobile ? 'detail-title' : 'album-title'}>{selectedAlbum.title || '未知专辑'}</div>
          <div className={mobile ? 'detail-meta' : 'album-meta'}><PlatformTag value={selectedAlbum.platform} /> {selectedAlbum.author || selectedAlbum.anchor || '未知作者'}<br />{albumEpisodeText(selectedAlbum)} · {selectedAlbum.status || '连载中'}</div>
          {(library.subscribed || libraryDownloaded > 0) && (
            <div className="album-library-state">
              {library.subscribed && <span className="library-badge subscribed"><Icon id="i-check" className="icon icon-sm" />已订阅</span>}
              <span className={`library-badge ${libraryTotal > 0 && libraryDownloaded >= libraryTotal ? 'complete' : ''}`}>{libraryTotal > 0 && libraryDownloaded >= libraryTotal ? '本地已全部下载' : `本地已下载 ${libraryDownloaded}/${libraryTotal || '?'}`}</span>
            </div>
          )}
        </div>
      </div>
      {!!voices.length && (
        <div className={mobile ? 'detail-voice-bar' : 'voice-bar'}>
          {voices.map((voice, index) => (
            <button key={voice.id || voice.name || index} disabled={busy.voice} className={`chip ${selectedVoice === voice ? 'active' : ''}`} onClick={() => actions.changeVoice(voice)}>{voice.category ? `${voice.category} · ` : ''}{voice.name || voice.title || `音色 ${index + 1}`}</button>
          ))}
        </div>
      )}
      {selectedAlbum.platform === '喜马拉雅' && (
        <div className={mobile ? 'detail-quality-bar' : 'quality-bar'}>
          <label htmlFor="xmlyDownloadInterface">下载接口</label>
          <select id="xmlyDownloadInterface" value={ximalayaInterface} onChange={(event) => setXimalayaInterface(event.target.value)}>
            <option value={XMLY_WEB_INTERFACE}>网页版接口（稳定推荐）</option>
            <option value={XMLY_MOBILE_INTERFACE}>移动端 V4（高音质）</option>
          </select>
          {ximalayaInterface !== XMLY_WEB_INTERFACE && (
            <>
              <label htmlFor="xmlyDownloadQuality">移动端音质</label>
              <select id="xmlyDownloadQuality" value={downloadQuality} onChange={(event) => setDownloadQuality(event.target.value)}>
                {XMLY_MOBILE_QUALITY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </>
          )}
          <label htmlFor="xmlySubscriptionQuality">订阅下载方式</label>
          <select
            id="xmlySubscriptionQuality"
            value={subscriptionQuality}
            disabled={loading || busy.subscribe || busy[`subscription:${library.subscription_id}:quality`]}
            onChange={(event) => actions.chooseSubscriptionQuality(event.target.value)}
          >
            {XMLY_SUBSCRIPTION_QUALITY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <span>{ximalayaInterface === XMLY_WEB_INTERFACE
            ? '默认使用稳定的网页版下载链路，由接口自动提供可用音频；无需额外选择音质，适合连续批量下载。'
            : XMLY_MOBILE_QUALITY_HELP[downloadQuality] || XMLY_MOBILE_QUALITY_HELP[XMLY_MOBILE_INTERFACE]}</span>
        </div>
      )}
      <ChapterToolbar
        loading={loading}
        busy={busy}
        chapters={chapters}
        viewChapters={viewChapters}
        selectedChapterList={selectedChapterList}
        chapterPagination={chapterPagination}
        chapterSort={chapterSort}
        setChapterSort={setChapterSort}
        downloadRange={downloadRange}
        setDownloadRange={setDownloadRange}
        subscribed={Boolean(library.subscribed)}
        actions={actions}
      />
      {loading && !viewChapters.length ? <div className="empty"><span className="loading" /> 正在加载章节</div> : <ChapterList chapters={viewChapters} selected={selectedChapters} onToggle={actions.toggleChapter} onPlay={actions.playChapter} mobile={mobile} />}
    </div>
  );
}

export function DownloadsPage({app, onNavigate}) {
  const {downloads, downloadPagination, downloadStatusFilter, metrics, actions, setModal, closeModal, busy} = app;
  const confirmDelete = (id) => setModal({content: <ConfirmModal icon="i-trash" title="清除任务记录" message="只清除历史记录，不会删除已下载文件。" okText="清除" danger onClose={closeModal} onOk={() => { closeModal(); actions.deleteDownload(id); }} />});
  const confirmCleanup = (statuses) => setModal({content: <ConfirmModal icon="i-trash" title="批量清理任务" message="将清理符合条件的历史任务记录，不会删除已下载文件。" okText="清理" danger onClose={closeModal} onOk={() => { closeModal(); actions.cleanupDownloads(statuses); }} />});
  const openDetails = (id) => setModal({className: 'modal-wide', content: <DownloadTaskDetailModal taskId={id} />});

  // 状态筛选改由后端分页：切换即回到第 1 页并带上 status 重新拉取
  const STATUS_FILTERS = [
    {key: 'all', label: '全部'},
    {key: 'active', label: '活跃'},
    {key: 'completed', label: '已完成'},
    {key: 'failed', label: '失败/中断'},
  ];
  const pg = downloadPagination || {page: 1, total_pages: 1, total: 0};

  // 批量操作作用于当前页可见任务（活跃任务按时间倒序天然在前页）
  const hasRunning = downloads.some((t) => t.status === 'running');
  const hasStoppable = downloads.some((t) => ['queued', 'running', 'paused'].includes(t.status));

  return (
    <>
      <div className="metrics">
        <div className="metric"><div className="metric-label">活跃任务</div><div className="metric-value">{metrics.activeDownloads}</div><div className="metric-foot">运行中 / 排队中</div></div>
        <div className="metric"><div className="metric-label">已完成</div><div className="metric-value">{metrics.completedDownloads}</div><div className="metric-foot">下载完成</div></div>
        <div className="metric"><div className="metric-label">失败</div><div className="metric-value">{metrics.failedDownloads}</div><div className="metric-foot">失败 / 部分完成</div></div>
        <div className="metric"><div className="metric-label">合计</div><div className="metric-value">{pg.total}</div><div className="metric-foot">所有任务</div></div>
      </div>
      <div className="glass glass-pad download-controls">
        <div className="download-controls-main">
        <div className="seg-control">
          {STATUS_FILTERS.map((f) => (
            <button key={f.key} className={downloadStatusFilter === f.key ? 'active' : ''} onClick={() => actions.loadDownloads(1, f.key)}>{f.label}</button>
          ))}
        </div>
        <div className="download-primary-actions">
        <button className="btn btn-primary btn-sm" disabled={busy.retryUnfinishedDownloads || (!metrics.failedDownloads && !metrics.interruptedDownloads)} onClick={actions.retryUnfinishedDownloads}><BusyIcon busy={busy.retryUnfinishedDownloads} icon="i-refresh" />重试未完成</button>
        {hasRunning && <button className="btn btn-ghost btn-sm" disabled={busy['batchDownload:pause']} onClick={() => actions.batchControlDownloads('pause')}><BusyIcon busy={busy['batchDownload:pause']} icon="i-pause" />全部暂停</button>}
        </div>
        </div>
        <details className="secondary-actions">
          <summary><Icon id="i-more" className="icon icon-sm" />更多操作</summary>
          <div className="secondary-actions-menu">
        {hasStoppable && <button className="btn btn-danger btn-sm" disabled={busy['batchDownload:stop']} onClick={() => actions.batchControlDownloads('stop')}><BusyIcon busy={busy['batchDownload:stop']} icon="i-close" />全部停止</button>}
        <button className="btn btn-ghost btn-sm" disabled={busy.cleanupDownloads} onClick={() => confirmCleanup(['completed'])}><BusyIcon busy={busy.cleanupDownloads} icon="i-trash" />清理已完成</button>
        <button className="btn btn-ghost btn-sm" disabled={busy.cleanupDownloads} onClick={() => confirmCleanup(['failed', 'partial', 'interrupted', 'stopped'])}><Icon id="i-trash" className="icon icon-sm" />清理失败/中断</button>
          </div>
        </details>
      </div>
      <div id="downloadList">
        {!downloads.length
          ? <div className="empty empty-action"><Icon id="i-download" /><span>{pg.total ? '该筛选条件下暂无任务' : '暂无下载任务'}</span><button className="btn btn-primary btn-sm" onClick={pg.total ? () => actions.loadDownloads(1, 'all') : onNavigate}>{pg.total ? '清除筛选' : '前往搜索'}</button></div>
          : downloads.map((task) => <TaskCard key={task.id} task={task} actions={actions} busy={busy} onDelete={confirmDelete} onDetails={openDetails} />)}
      </div>
      {pg.total_pages > 1 && (
        <div className="glass glass-pad" style={{display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '14px'}}>
          <button className="btn btn-ghost btn-sm" disabled={pg.page <= 1} onClick={() => actions.loadDownloads(pg.page - 1)}><Icon id="i-arrow-left" className="icon icon-sm" />上一页</button>
          <span style={{color: 'var(--text-dim)', fontSize: '13px'}}>第 {pg.page} / {pg.total_pages} 页 · 共 {pg.total} 条</span>
          <button className="btn btn-ghost btn-sm" disabled={pg.page >= pg.total_pages} onClick={() => actions.loadDownloads(pg.page + 1)}>下一页<Icon id="i-arrow-right" className="icon icon-sm" /></button>
        </div>
      )}
    </>
  );
}

export function PersonalPage({app, mobile = false}) {
  const [platform, setPlatform] = useState('ximalaya');
  const [feature, setFeature] = useState('subscriptions');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [personalCookies, setPersonalCookies] = useState({});
  const personalRequestRef = useRef(0);
  const platformMeta = COOKIE_PLATFORMS.find((item) => item.key === (platform === 'ximalaya' ? 'xmly' : platform)) || {};
  const personalCookieInfo = personalCookies[platform] || {};
  const personalLoggedIn = !!personalCookieInfo.has_cookie;
  const loadPersonalCookies = async () => {
    try {
      const data = await api('/api/personal/cookies');
      const nextCookies = data.cookies || {};
      setPersonalCookies(nextCookies);
      return nextCookies;
    } catch (error) {
      app.actions.showToast?.(`个人中心登录状态加载失败：${error.message}`, 'err');
      return {};
    }
  };
  const load = async (feat, targetPlatform = platform, {quiet = false} = {}) => {
    const requestId = ++personalRequestRef.current;
    setFeature(feat);
    setItems([]);
    setLoadError('');
    setLoading(true);
    try {
      const data = await api(`/api/personal/${targetPlatform}/${feat}`);
      if (requestId !== personalRequestRef.current) return;
      setItems(data.items || []);
    } catch (error) {
      if (requestId !== personalRequestRef.current) return;
      setLoadError(error.message || '加载失败');
      if (!quiet) app.actions.showToast?.(`加载失败：${error.message}`, 'err');
    } finally {
      if (requestId === personalRequestRef.current) setLoading(false);
    }
  };
  useEffect(() => {
    let active = true;
    const initialize = async () => {
      const cookieData = await loadPersonalCookies();
      if (active && cookieData.ximalaya?.has_cookie) {
        await load('subscriptions', 'ximalaya', {quiet: true});
      }
    };
    initialize();
    return () => { active = false; };
  }, []);
  const features = PERSONAL_FEATURES[platform] || [];
  const activeFeature = features.find((item) => item.key === feature);
  const personalEmptyText = loadError || (!personalLoggedIn
    ? `连接${platformMeta.name || platform}个人账号后即可查看${activeFeature?.name || '个人内容'}`
    : feature === 'subscriptions'
      ? '账号暂未订阅专辑'
      : feature ? '暂无数据' : '选择上方功能加载');
  const changePlatform = (key) => {
    personalRequestRef.current += 1;
    setPlatform(key);
    setItems([]);
    setLoadError('');
    setLoading(false);
    if (key === 'ximalaya') {
      setFeature('subscriptions');
      if (personalCookies.ximalaya?.has_cookie) load('subscriptions', key, {quiet: true});
    } else {
      setFeature('');
    }
  };
  const openAlbum = (album) => {
    if (mobile) app.setMobileView?.('discover');
    else app.setPage?.('search');
    app.actions.openAlbum(album);
  };
  const savePersonalCookie = async (cookie) => {
    const trimmed = String(cookie || '').trim();
    if (!trimmed) return;
    await api('/api/personal/cookies', {method: 'POST', body: {platform, cookie: trimmed}});
    const cookieData = await loadPersonalCookies();
    if (platform === 'ximalaya' && cookieData.ximalaya?.has_cookie) {
      await load('subscriptions', 'ximalaya', {quiet: true});
    }
    app.actions.showToast?.(`${platformMeta.name || platform}个人中心 Cookie 已保存`, 'ok');
  };
  const deletePersonalCookie = async () => {
    await api(`/api/personal/cookies/${encodeURIComponent(platform)}`, {method: 'DELETE'});
    personalRequestRef.current += 1;
    await loadPersonalCookies();
    setItems([]);
    setLoadError('');
    setLoading(false);
    setFeature(platform === 'ximalaya' ? 'subscriptions' : '');
    app.actions.showToast?.(`${platformMeta.name || platform}个人中心 Cookie 已删除`, 'ok');
  };
  const refreshPersonalAccount = async () => {
    const cookieData = await loadPersonalCookies();
    if (platform === 'ximalaya' && cookieData.ximalaya?.has_cookie) {
      await load('subscriptions', 'ximalaya', {quiet: true});
    }
  };
  const openPersonalLogin = () => {
    app.setModal?.({
      content: <QrLoginModal
        platform={platformMeta}
        scope="personal"
        onDone={refreshPersonalAccount}
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
        {platformMeta.qr && <button className="icon-btn personal-auth-btn primary" onClick={openPersonalLogin} title={platformMeta.qr === 'lrts' ? '验证码登录' : '扫码登录'} aria-label={platformMeta.qr === 'lrts' ? '验证码登录' : '扫码登录'}><Icon id={platformMeta.qr === 'lrts' ? 'i-mobile' : 'i-qr'} /></button>}
        {platform !== 'lrts' && <button className="icon-btn personal-auth-btn" onClick={openPersonalCookieScript} title="手动输入 Cookie" aria-label="手动输入 Cookie"><Icon id="i-globe" /></button>}
        {personalLoggedIn && <button className="icon-btn personal-auth-btn danger" onClick={deletePersonalCookie} title="删除个人中心登录" aria-label="删除个人中心登录"><Icon id="i-trash" /></button>}
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
              onClick={() => changePlatform(key)}
            >
              {platformNames[key] || key}
            </button>
          ))}
        </div>
        {authPanel}
        <div className="mobile-personal-card">
          {features.map((item) => (
            <button key={item.key} className={`mobile-personal-row ${feature === item.key ? 'active' : ''}`} onClick={() => load(item.key)}>
              <Icon id={item.icon} />
              <span>{item.name}</span>
              <Icon id="i-arrow-right" className="icon icon-sm" />
            </button>
          ))}
        </div>
        {(loading || feature) && (
          <div className="mobile-personal-results">
            {feature && <div className="personal-results-head"><strong>{platformMeta.name || platform} · {activeFeature?.name || '个人内容'}</strong><span>{loading ? '加载中' : `${items.length} 个专辑`}</span></div>}
            {loading
              ? <div className="empty"><span className="loading" /> 加载中...</div>
              : !items.length
                ? <div className="empty"><Icon id={feature === 'subscriptions' ? 'i-bookmark' : 'i-user'} />{personalEmptyText}</div>
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
            <button key={key} className={`tab ${platform === key ? 'active' : ''}`} onClick={() => changePlatform(key)}>
              {nameMap[key] || key}
            </button>
          );
        })}
      </div>
      {authPanel}
      <div className="tabs feature-tabs">{features.map((item) => <button key={item.key} className={`tab ${feature === item.key ? 'active' : ''}`} onClick={() => load(item.key)}>{item.name}</button>)}</div>
      {feature && <div className="personal-results-head"><strong>{platformMeta.name || platform} · {activeFeature?.name || '个人内容'}</strong><span>{loading ? '加载中' : `${items.length} 个专辑`}</span></div>}
      <div className="sub-grid personal-grid">{loading ? <div className="empty"><span className="loading" /> 加载中...</div> : !items.length ? <div className="empty"><Icon id={feature === 'subscriptions' ? 'i-bookmark' : 'i-user'} />{personalEmptyText}</div> : items.map((album, index) => <ResultCard key={`${album.platform}-${album.id || album.title}-${index}`} album={album} mobile={mobile} onOpen={() => openAlbum(album)} />)}</div>
    </div>
  );
}

const TASK_DETAIL_FILTERS = [
  {key: 'all', label: '全部'},
  {key: 'success', label: '成功'},
  {key: 'failed', label: '失败'},
  {key: 'downloading', label: '下载中'},
  {key: 'pending', label: '待下载'},
];
function DownloadTaskDetailModal({taskId}) {
  const [detail, setDetail] = useState(null);
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      try {
        const data = await api('/api/downloads/' + encodeURIComponent(taskId));
        if (cancelled) return;
        setDetail(data.task || null);
        setError('');
        if (['queued', 'running', 'paused', 'stopping'].includes(data.task?.status)) {
          timer = setTimeout(load, 2000);
        }
      } catch (loadError) {
        if (!cancelled) setError(loadError.message || '加载失败');
      }
    };
    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [taskId]);

  if (error) {
    return <><div className="modal-title"><Icon id="i-alert" />任务详情</div><div className="empty small">{error}</div></>;
  }
  if (!detail) {
    return <><div className="modal-title"><Icon id="i-list" />任务详情</div><div className="empty small"><span className="loading" />正在加载</div></>;
  }

  const album = detail.album || {title: detail.title, platform: detail.platform};
  const cover = coverOf(album);
  const counts = detail.counts || {};
  const chapters = detail.chapters || [];
  const visibleChapters = filter === 'all'
    ? chapters
    : chapters.filter((chapter) => (chapter.download_status || 'pending') === filter);
  return (
    <div className="task-detail-modal">
      <div className="modal-title"><Icon id="i-list" />专辑下载详情</div>
      <div className="task-detail-hero">
        <div className="task-detail-cover" style={cover ? {backgroundImage: `url("${cover}")`} : undefined}>{cover ? '' : <Icon id="i-music" />}</div>
        <div className="task-detail-heading">
          <strong title={detail.title || taskId}>{detail.title || taskId}</strong>
          <span><PlatformTag value={album.platform || detail.platform} />{taskStatusText(detail.status)}</span>
        </div>
      </div>
      <div className="task-count-grid">
        <div><span>成功</span><strong className="status-text-ok">{counts.success || 0}</strong></div>
        <div><span>失败</span><strong className="status-text-danger">{counts.failed || 0}</strong></div>
        <div><span>下载中</span><strong>{counts.downloading || 0}</strong></div>
        <div><span>待下载</span><strong>{counts.pending || 0}</strong></div>
      </div>
      {detail.detail_available === false && <div className="task-detail-notice"><Icon id="i-alert" className="icon icon-sm" />完整章节明细已过保留期，仅保留失败摘要。</div>}
      {detail.failure_reason && <div className="task-detail-notice danger">{detail.failure_reason}</div>}
      <div className="task-detail-toolbar">
        <div className="seg-control">
          {TASK_DETAIL_FILTERS.map((item) => <button key={item.key} className={filter === item.key ? 'active' : ''} onClick={() => setFilter(item.key)}>{item.label}</button>)}
        </div>
        <span>{visibleChapters.length}/{chapters.length} 章</span>
      </div>
      <div className="task-detail-list">
        {!visibleChapters.length
          ? <div className="empty small">该状态下暂无章节</div>
          : visibleChapters.map((chapter, index) => {
            const status = chapter.download_status || 'pending';
            return (
              <div className="task-detail-row" key={chapterId(chapter, String(index + 1))}>
                <span className="task-detail-index">{chapter.order_num || index + 1}</span>
                <div><strong title={chapterTitle(chapter)}>{chapterTitle(chapter)}</strong>{chapter.download_error && <small title={chapter.download_error}>{chapter.download_error}</small>}</div>
                <ChapterStatusBadge status={status} error={chapter.download_error} />
              </div>
            );
          })}
      </div>
    </div>
  );
}

function TaskCard({task, actions, busy, onDelete, onDetails}) {
  const pct = Math.max(0, Math.min(100, task.percent || 0));
  const status = task.status || 'queued';
  const successCount = Number(task.success || 0);
  const failedCount = Number(task.failed ?? task.failed_chapters?.length ?? 0) || 0;
  const downloadingCount = Number(task.downloading || 0);
  const pendingCount = Number(task.pending ?? Math.max(Number(task.total || 0) - successCount - failedCount - downloadingCount, 0));
  const canPause = status === 'running';
  const canResume = ['paused', 'stopping'].includes(status);
  const canStop = ['queued', 'running', 'paused', 'stopping'].includes(status);
  const canRetry = failedCount > 0 || ['failed', 'partial', 'interrupted', 'stopped'].includes(status);
  const canDelete = !['queued', 'running', 'paused'].includes(status);
  const busyPrefix = `download:${task.id}:`;
  const hasDiagnostics = Boolean(task.failure_reason || task.error || task.warning || task.failed_chapters?.length);
  const diagnosticsText = JSON.stringify({
    task_id: task.id,
    title: task.title || '',
    status,
    progress: `${task.completed || 0}/${task.total || 0}`,
    failed_count: failedCount,
    failure_reason: task.failure_reason || '',
    error: task.error || '',
    warning: task.warning || '',
    failed_chapters: task.failed_chapters || [],
  }, null, 2);
  const copyDiagnostics = async () => {
    try {
      await copyText(diagnosticsText);
      actions.showToast('已复制任务诊断信息', 'ok');
    } catch (error) {
      actions.showToast('复制失败：' + error.message, 'err');
    }
  };
  return (
    <div className={`task-card task-card-${status}`}>
      <div className="task-head">
        <div className="task-heading">
          <div className="task-title" title={task.title || task.id}>{task.title || task.id}</div>
          <div className="task-meta task-counts">
            <span className="ok">成功 <strong>{successCount}</strong></span>
            <span className="danger">失败 <strong>{failedCount}</strong></span>
            {downloadingCount > 0 && <span>下载中 <strong>{downloadingCount}</strong></span>}
            <span>待下载 <strong>{pendingCount}</strong></span>
          </div>
        </div>
        <div className="task-status-group">
          <strong className="task-progress-value">{pct}%</strong>
          <span className={`task-state state-${status}`}>{taskStatusText(status)}</span>
        </div>
      </div>
      <div className="progress-bar" aria-label={`下载进度 ${pct}%`}><div className="progress-fill" style={{width: `${pct}%`}} /></div>
      {hasDiagnostics && (
        <details className="task-error-details">
          <summary><Icon id="i-alert" className="icon icon-sm" />查看失败详情</summary>
          <div className="task-error-content">
            {task.failure_reason && <p><strong>失败原因</strong>{task.failure_reason}</p>}
            {task.error && <p><strong>错误信息</strong>{task.error}</p>}
            {task.warning && <p><strong>提示</strong>{task.warning}</p>}
            {task.failed_chapters?.length ? <pre>{JSON.stringify(task.failed_chapters, null, 2)}</pre> : null}
            <button className="btn btn-ghost btn-tiny" type="button" onClick={copyDiagnostics}><Icon id="i-copy" className="icon icon-sm" />复制诊断信息</button>
          </div>
        </details>
      )}
      <div className="task-actions">
        <button className="btn btn-ghost btn-tiny" onClick={() => onDetails(task.id)}><Icon id="i-list" className="icon icon-sm" />专辑详情</button>
        {canPause && <button className="btn btn-ghost btn-tiny" disabled={busy[`${busyPrefix}pause`]} onClick={() => actions.controlDownload(task.id, 'pause')}><BusyIcon busy={busy[`${busyPrefix}pause`]} icon="i-pause" />暂停</button>}
        {canResume && <button className="btn btn-primary btn-tiny" disabled={busy[`${busyPrefix}resume`]} onClick={() => actions.controlDownload(task.id, 'resume')}><BusyIcon busy={busy[`${busyPrefix}resume`]} icon="i-play" />继续</button>}
        {canStop && <button className="btn btn-danger btn-tiny" disabled={busy[`${busyPrefix}stop`]} onClick={() => actions.controlDownload(task.id, 'stop')}><BusyIcon busy={busy[`${busyPrefix}stop`]} icon="i-close" />停止</button>}
        {canRetry && <button className="btn btn-primary btn-tiny" disabled={busy[`${busyPrefix}retry-failed`]} onClick={() => actions.controlDownload(task.id, 'retry-failed')}><BusyIcon busy={busy[`${busyPrefix}retry-failed`]} icon="i-refresh" />重试失败</button>}
        {canDelete && <button className="btn btn-ghost btn-tiny task-record-action icon-action" title="清除记录" aria-label={`清除《${task.title || task.id}》任务记录`} onClick={() => onDelete(task.id)}><Icon id="i-trash" className="icon icon-sm" /></button>}
      </div>
    </div>
  );
}

export function SubscriptionsPage({app, onNavigate}) {
  const {subscriptions, subscriptionSettings, subscriptionScheduler = {}, subscriptionJobs, actions, setModal, closeModal, busy} = app;
  const [enabled, setEnabled] = useState(true);
  const [autoDownload, setAutoDownload] = useState(true);
  const [hours, setHours] = useState(6);
  const [personalSyncEnabled, setPersonalSyncEnabled] = useState(false);
  const [personalSyncUnit, setPersonalSyncUnit] = useState('hours');
  const [personalSyncInterval, setPersonalSyncInterval] = useState(1);
  const sortedSubscriptions = useMemo(() => subscriptions
    .map((item, index) => ({item, index}))
    .sort((a, b) => {
      const aTime = Date.parse(a.item.created_at || '') || 0;
      const bTime = Date.parse(b.item.created_at || '') || 0;
      return bTime - aTime || a.index - b.index;
    })
    .map(({item}) => item), [subscriptions]);
  useEffect(() => {
    setEnabled(subscriptionSettings.enabled !== false);
    setAutoDownload(subscriptionSettings.auto_download_missing !== false);
    setHours(Number(subscriptionSettings.interval_hours || 6));
    setPersonalSyncEnabled(!!subscriptionSettings.personal_sync_enabled);
    const syncMinutes = Number(subscriptionSettings.personal_sync_interval_minutes || 0);
    setPersonalSyncUnit(syncMinutes > 0 ? 'minutes' : 'hours');
    setPersonalSyncInterval(syncMinutes > 0 ? syncMinutes : Number(subscriptionSettings.personal_sync_interval_hours || 1));
  }, [subscriptionSettings]);
  const cancel = (id) => setModal({content: <ConfirmModal icon="i-trash" title="取消订阅" message="后续不会再自动检测新章节。" okText="取消订阅" danger onClose={closeModal} onOk={() => { closeModal(); actions.cancelSubscription(id); }} />});
  const openSubscriptionAlbum = (album) => {
    onNavigate?.();
    actions.openAlbum(album);
  };
  const cancelAll = () => setModal({content: <ConfirmModal icon="i-trash" title="批量取消订阅" message="会取消当前列表里的全部订阅，后续不会自动检测。" okText="批量取消" danger onClose={closeModal} onOk={() => { closeModal(); actions.batchSubscriptions('cancel', subscriptions.map((item) => item.id)); }} />});
  const schedulerRunning = Boolean(subscriptionScheduler.running);
  const schedulerStarted = Boolean(subscriptionScheduler.started);
  const schedulerLastRun = formatCheckTime(subscriptionScheduler.last_run_at, '等待首次轮询');
  const personalSyncLastRun = formatCheckTime(subscriptionScheduler.personal_sync_last_run_at, '等待首次同步');
  const dueCount = Number(subscriptionScheduler.current_due_count || 0);
  const saveSettings = () => {
    const syncValue = Math.max(1, Number(personalSyncInterval) || 1);
    actions.saveSubscriptionSettings({
      enabled,
      auto_download_missing: autoDownload,
      interval_hours: Number(hours) || 6,
      personal_sync_enabled: personalSyncEnabled,
      personal_sync_platform: 'ximalaya',
      ...(personalSyncUnit === 'minutes'
        ? {personal_sync_interval_minutes: syncValue}
        : {personal_sync_interval_hours: syncValue}),
    });
  };
  const doExportSubs = async () => {
    try {
      const data = await actions.exportSubscriptions();
      const text = JSON.stringify(data, null, 2);
      const blob = new Blob([text], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audioflow-subscriptions-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      actions.showToast(`已导出 ${(data.subscriptions || []).length} 个订阅`, 'ok');
    } catch (error) {
      actions.showToast('导出失败：' + error.message, 'err');
    }
  };
  return (
    <>
      <div className="glass glass-pad subscription-overview">
        <div className="subscription-overview-main">
          <div>
            <strong>{subscriptions.length} 个订阅专辑</strong>
            <span>{schedulerRunning ? '正在检测更新' : schedulerStarted ? '自动检测已就绪' : '自动检测尚未启动'}</span>
          </div>
          <div className="subscription-primary-actions">
            <button className="btn btn-ghost btn-sm" disabled={busy.runSubscriptions} onClick={actions.runSubscriptionsNow}><BusyIcon busy={busy.runSubscriptions} icon="i-refresh" />立即检测</button>
            <button className="btn btn-primary btn-sm" disabled={!subscriptions.length || busy['subscriptionBatch:complete']} onClick={() => actions.batchSubscriptions('complete', subscriptions.map((item) => item.id))}><BusyIcon busy={busy['subscriptionBatch:complete']} icon="i-download" />批量补全</button>
          </div>
        </div>
        <div className="subscription-scheduler">
          <span className={schedulerStarted ? 'ok' : 'muted'}>调度器：{schedulerRunning ? '检测中' : schedulerStarted ? '待命' : '未启动'}</span>
          <span>最近轮询：{schedulerLastRun}</span>
          <span>到期专辑：{dueCount}</span>
          <span>个人订阅同步：{subscriptionScheduler.personal_sync_running ? '同步中' : personalSyncEnabled ? '已启用' : '未启用'}</span>
          <span>最近同步：{personalSyncLastRun}</span>
          <span>上次新增：{Number(subscriptionScheduler.personal_sync_last_added || 0)}</span>
          <span>喜马拉雅默认：网页版接口</span>
          {subscriptionScheduler.personal_sync_last_error && <span style={{color: 'var(--danger)'}}>同步错误：{subscriptionScheduler.personal_sync_last_error}</span>}
        </div>
      </div>
      <div className="sub-grid">
        {!subscriptions.length ? <div className="empty empty-action"><Icon id="i-star" /><span>暂无订阅，在专辑详情可添加追更</span><button className="btn btn-primary btn-sm" onClick={onNavigate}>前往搜索</button></div> : sortedSubscriptions.map((sub) => {
          const storedAlbum = sub.album || {};
          const album = {
            ...sub,
            ...storedAlbum,
            id: storedAlbum.id || storedAlbum.album_id || storedAlbum.book_id || sub.album_id || sub.book_id || '',
            album_id: storedAlbum.album_id || storedAlbum.id || sub.album_id || '',
            platform: storedAlbum.platform || sub.platform,
            title: storedAlbum.title || sub.title,
            author: storedAlbum.author || storedAlbum.anchor || sub.author || sub.anchor,
            cover: coverOf(storedAlbum) || coverOf(sub),
            episodes: storedAlbum.episodes || storedAlbum.chapter_count || sub.total || 0,
          };
          const stats = sub.stats || {};
          const activeJob = Object.values(subscriptionJobs).find((job) => job.sid === sub.id && ['queued', 'running'].includes(job.status));
          const jobBusy = Boolean(activeJob);
          const jobMessage = activeJob?.message || '检测中';
          const cover = coverOf(sub) || coverOf(album);
          const checkBusy = jobBusy || busy[`subscription:${sub.id}:check`];
          const completeBusy = jobBusy || busy[`subscription:${sub.id}:complete`];
          const title = sub.title || album.title || '未知专辑';
          const platform = sub.platform || album.platform;
          const subscriptionQuality = sub.subscription_quality || XMLY_WEB_INTERFACE;
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
                <button
                  type="button"
                  className="sub-cover sub-cover-button"
                  style={cover ? {backgroundImage: `url("${cover}")`} : undefined}
                  onClick={() => openSubscriptionAlbum(album)}
                  title={`查看《${title}》专辑详情`}
                  aria-label={`查看《${title}》专辑详情`}
                >
                  {cover ? '' : <Icon id="i-music" />}
                  <span className="sub-cover-open"><Icon id="i-arrow-right" className="icon icon-sm" /></span>
                </button>
                {jobBusy && <span className="sub-live"><span className="loading" />{jobMessage}</span>}
              </div>
              <div className="sub-info">
                <div className="sub-main">
                  <div className="sub-title" title={title}>{title}</div>
                  <div className="sub-meta"><PlatformTag value={platform} /> <span>{author}</span></div>
                  <div className="sub-progress"><span style={{width: `${progress}%`}} /></div>
                  <div className="sub-stats">
                    <span>共 {total || 0} 章</span>
                    <span className="ok">已下载 {downloaded}</span>
                    {missing > 0 && <span className="warn">缺失 {missing}</span>}
                    {restricted > 0 && <span>受限 {restricted}</span>}
                  </div>
                  <div className="sub-times">
                    <span>上次检测 {lastCheck}</span>
                    <span>下次检测 {nextCheck}</span>
                  </div>
                  <div className="sub-download-method">
                    <span className="sub-download-method-label">自动下载</span>
                    {platform === '喜马拉雅' ? (
                      <select
                        value={subscriptionQuality}
                        disabled={jobBusy || busy[`subscription:${sub.id}:quality`]}
                        onChange={(event) => actions.updateSubscriptionQuality(sub.id, event.target.value)}
                        aria-label={`《${title}》自动下载方式`}
                      >
                        {XMLY_SUBSCRIPTION_QUALITY_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="sub-download-method-default">平台默认接口</span>
                    )}
                  </div>
                </div>
                <div className="sub-actions">
                  <button className="btn btn-ghost btn-sm" disabled={checkBusy} onClick={() => actions.checkSubscription(sub.id, false)}><BusyIcon busy={checkBusy} icon="i-refresh" />检测</button>
                  <button className="btn btn-primary btn-sm" disabled={completeBusy} onClick={() => actions.checkSubscription(sub.id, true)}><BusyIcon busy={completeBusy} icon="i-download" />补全缺失</button>
                  <button className="btn btn-ghost btn-sm sub-cancel-action icon-action" title="取消订阅" aria-label={`取消订阅《${title}》`} disabled={jobBusy} onClick={() => cancel(sub.id)}><Icon id="i-trash" className="icon icon-sm" /></button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <details className="glass subscription-options">
        <summary><span><Icon id="i-settings" />自动化设置</span><small>检测频率、自动下载与个人订阅同步</small></summary>
        <div className="subscription-options-body">
          <div className="subscription-settings-grid">
            <div className="subscription-control-group">
              <span className="control-group-title">订阅检测</span>
              <label className="check-row"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /><span>启用自动检测</span></label>
              <label className="check-row"><input type="checkbox" checked={autoDownload} onChange={(e) => setAutoDownload(e.target.checked)} /><span>发现缺失后自动下载</span></label>
              <label className="check-row interval-row"><span>检测间隔（小时）</span><input className="field-input interval-input" type="number" min="1" max="720" value={hours} onChange={(e) => setHours(e.target.value)} /></label>
            </div>
            <div className="subscription-control-group">
              <span className="control-group-title">个人订阅同步</span>
              <label className="check-row"><input type="checkbox" checked={personalSyncEnabled} onChange={(e) => setPersonalSyncEnabled(e.target.checked)} /><span>同步喜马拉雅个人订阅</span></label>
              <label className="check-row interval-row">
                <span>同步频率</span>
                <input className="field-input interval-input" type="number" min="1" max="43200" value={personalSyncInterval} onChange={(e) => setPersonalSyncInterval(e.target.value)} />
                <select className="field-select interval-input" value={personalSyncUnit} onChange={(e) => setPersonalSyncUnit(e.target.value)}>
                  <option value="minutes">分钟</option>
                  <option value="hours">小时</option>
                </select>
              </label>
            </div>
          </div>
          <div className="subscription-options-actions">
            <button className="btn btn-primary btn-sm" disabled={busy.subscriptionSettings} onClick={saveSettings}><BusyIcon busy={busy.subscriptionSettings} icon="i-check" />保存设置</button>
            <button className="btn btn-ghost btn-sm" disabled={busy.personalSubscriptionSync} onClick={actions.runPersonalSubscriptionSyncNow}><BusyIcon busy={busy.personalSubscriptionSync} icon="i-refresh" />立即同步个人订阅</button>
          </div>
        </div>
      </details>
      <details className="glass subscription-options subscription-maintenance">
        <summary><span><Icon id="i-more" />数据与批量操作</span><small>导入导出、索引维护与取消订阅</small></summary>
        <div className="subscription-options-body subscription-maintenance-actions">
          <button className="btn btn-ghost btn-sm" disabled={busy.rebuildIndex} onClick={actions.rebuildSubscriptionIndex}><BusyIcon busy={busy.rebuildIndex} icon="i-folder" />重建本地索引</button>
          <button className="btn btn-ghost btn-sm" disabled={!subscriptions.length || busy['subscriptionBatch:check']} onClick={() => actions.batchSubscriptions('check', subscriptions.map((item) => item.id))}><BusyIcon busy={busy['subscriptionBatch:check']} icon="i-refresh" />批量检测</button>
          <button className="btn btn-ghost btn-sm" disabled={!subscriptions.length} onClick={doExportSubs}><Icon id="i-download" className="icon icon-sm" />导出订阅</button>
          <button className="btn btn-ghost btn-sm" disabled={busy.importSubscriptions} onClick={() => setModal({content: <SubscriptionImportModal actions={actions} onClose={closeModal} />})}><Icon id="i-folder" className="icon icon-sm" />导入订阅</button>
          <button className="btn btn-danger btn-sm" disabled={!subscriptions.length || busy['subscriptionBatch:cancel']} onClick={cancelAll}><BusyIcon busy={busy['subscriptionBatch:cancel']} icon="i-trash" />批量取消</button>
        </div>
      </details>
    </>
  );
}

function SubscriptionImportModal({actions, onClose}) {
  const [text, setText] = useState('');
  const onFile = (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ''));
    reader.readAsText(file);
  };
  const doImport = async () => {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      actions.showToast('内容不是合法的 JSON', 'err');
      return;
    }
    try {
      await actions.importSubscriptions(parsed);
      onClose();
    } catch (error) {
      actions.showToast('导入失败：' + error.message, 'err');
    }
  };
  return (
    <>
      <div className="modal-title"><Icon id="i-folder" />导入订阅</div>
      <div className="modal-sub">上传导出的 .json 文件或粘贴 JSON。按订阅合并（同名覆盖），章节会在首次检测时自动重新拉取。</div>
      <div className="modal-toolbar">
        <label className="btn btn-ghost btn-sm" style={{cursor: 'pointer'}}>
          <Icon id="i-folder" className="icon icon-sm" />选择文件
          <input type="file" accept="application/json,.json" onChange={onFile} style={{display: 'none'}} />
        </label>
      </div>
      <textarea className="cookie-modal-textarea" value={text} onChange={(event) => setText(event.target.value)} placeholder='{"subscriptions": [ ... ]}' style={{minHeight: 160}} />
      <div className="modal-actions">
        <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
        <button className="btn btn-primary btn-sm" disabled={!text.trim()} onClick={doImport}>导入</button>
      </div>
    </>
  );
}

export function CookiesPage({app}) {
  const {cookies, actions, setModal, closeModal, busy} = app;
  const [selectedKey, setSelectedKey] = useState(COOKIE_PLATFORMS[0]?.key || 'xmly');
  const selectedPlatform = COOKIE_PLATFORMS.find((platform) => platform.key === selectedKey) || COOKIE_PLATFORMS[0];
  const loginPlatforms = COOKIE_PLATFORMS.filter((platform) => !NO_COOKIE_KEYS.includes(platform.key));
  const freePlatforms = COOKIE_PLATFORMS.filter((platform) => NO_COOKIE_KEYS.includes(platform.key));
  const configuredPlatforms = loginPlatforms.filter((platform) => {
    const info = cookies[platform.key] || {};
    return info.has_cookie || info.has_server;
  });
  const pendingPlatforms = loginPlatforms.filter((platform) => {
    const info = cookies[platform.key] || {};
    return !info.has_cookie && !info.has_server;
  });
  const doExport = async (mode) => {
    try {
      const data = await actions.exportCookies();
      if (!Object.keys(data || {}).length) { actions.showToast('当前没有可导出的平台凭证', 'err'); return; }
      const text = JSON.stringify(data, null, 2);
      if (mode === 'copy') {
        await copyText(text);
        actions.showToast('已复制全部平台凭证到剪贴板', 'ok');
      } else {
        const blob = new Blob([text], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audioflow-credentials-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }
    } catch (error) {
      actions.showToast('导出失败：' + error.message, 'err');
    }
  };
  return (
    <div className="cookie-page-layout">
      <div className="cookie-overview">
        <div className="cookie-summary" aria-label="账号配置概览">
          <div><span>已连接</span><strong>{configuredPlatforms.length}</strong></div>
          <div><span>待登录</span><strong>{pendingPlatforms.length}</strong></div>
          <div><span>免登录平台</span><strong>{freePlatforms.length}</strong></div>
        </div>
        <div className="cookie-toolbar-actions">
          <button className="btn btn-ghost btn-sm" onClick={() => doExport('file')} title="导出全部平台凭证"><Icon id="i-download" className="icon icon-sm" />导出</button>
          <button className="btn btn-ghost btn-sm" onClick={() => doExport('copy')} title="复制全部平台凭证 JSON"><Icon id="i-copy" className="icon icon-sm" />复制</button>
          <button className="btn btn-primary btn-sm" disabled={busy.importCookies} onClick={() => setModal({content: <CookieImportModal actions={actions} onClose={closeModal} />})}><Icon id="i-folder" className="icon icon-sm" />导入凭证</button>
        </div>
      </div>
      <div className="cookie-security-note"><Icon id="i-alert" className="icon icon-sm" /><span>凭证仅用于对应平台请求；导出的文件包含明文，请存放在安全位置。</span></div>
      <div id="cookieList" className="cookie-workspace">
        <aside className="cookie-platform-nav" aria-label="平台账号列表">
          <CookiePlatformGroup title="已连接" platforms={configuredPlatforms} cookies={cookies} selectedKey={selectedKey} onSelect={setSelectedKey} />
          <CookiePlatformGroup title="待登录" platforms={pendingPlatforms} cookies={cookies} selectedKey={selectedKey} onSelect={setSelectedKey} />
          <CookiePlatformGroup title="无需登录" platforms={freePlatforms} cookies={cookies} selectedKey={selectedKey} onSelect={setSelectedKey} free />
        </aside>
        <section className="cookie-detail-pane" aria-label="平台凭证详情">
          {selectedPlatform && (
            <CookieCard
              key={selectedPlatform.key}
              platform={selectedPlatform}
              info={cookies[selectedPlatform.key] || {}}
              actions={actions}
              busy={busy}
              setModal={setModal}
              closeModal={closeModal}
            />
          )}
        </section>
      </div>
    </div>
  );
}

function CookiePlatformGroup({title, platforms, cookies, selectedKey, onSelect, free = false}) {
  if (!platforms.length) return null;
  return (
    <section className="cookie-platform-group">
      <div className="cookie-platform-group-title"><span>{title}</span><em>{platforms.length}</em></div>
      <div className="cookie-platform-list">
        {platforms.map((platform) => {
          const info = cookies[platform.key] || {};
          const ok = free || info.has_cookie || info.has_server;
          return (
            <button key={platform.key} className={`cookie-platform-item ${selectedKey === platform.key ? 'active' : ''}`} onClick={() => onSelect(platform.key)}>
              <PlatformLogo value={platform.key} name={platform.name} />
              <span className="cookie-platform-copy">
                <strong>{platform.name}</strong>
                <small>{free ? '可直接使用' : info.account_name || (ok ? '凭证已保存' : '尚未配置凭证')}</small>
              </span>
              <span className={`cookie-platform-dot ${ok ? 'ready' : ''}`} aria-label={ok ? '可用' : '未配置'} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function CookieImportModal({actions, onClose}) {
  const [text, setText] = useState('');
  const onFile = (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ''));
    reader.readAsText(file);
  };
  const doImport = async () => {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      actions.showToast('内容不是合法的 JSON', 'err');
      return;
    }
    try {
      await actions.importCookies(parsed);
      onClose();
    } catch (error) {
      actions.showToast('导入失败：' + error.message, 'err');
    }
  };
  return (
    <>
      <div className="modal-title"><Icon id="i-folder" />导入 Cookie</div>
      <div className="modal-sub">上传之前导出的 .json 文件，或直接粘贴 JSON（格式：{'{ "xmly": "...", "lrts": "..." }'}）。导入会覆盖同名平台的现有 Cookie。</div>
      <div className="modal-toolbar">
        <label className="btn btn-ghost btn-sm" style={{cursor: 'pointer'}}>
          <Icon id="i-folder" className="icon icon-sm" />选择文件
          <input type="file" accept="application/json,.json" onChange={onFile} style={{display: 'none'}} />
        </label>
      </div>
      <textarea className="cookie-modal-textarea" value={text} onChange={(event) => setText(event.target.value)} placeholder='{"xmly": "...", "lrts": "..."}' style={{minHeight: 160}} />
      <div className="modal-actions">
        <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
        <button className="btn btn-primary btn-sm" disabled={!text.trim()} onClick={doImport}>导入</button>
      </div>
    </>
  );
}

function CookieCard({platform, info, actions, busy, setModal, closeModal}) {
  const [value, setValue] = useState('');
  const [mobileCookie, setMobileCookie] = useState('');
  const noCookie = NO_COOKIE_KEYS.includes(platform.key);
  const ok = info.has_cookie || info.has_server;
  const scanText = platform.qr === 'lrts' ? '验证码登录' : '扫码';
  const saveText = platform.key === 'lrts' ? '保存手动凭证' : '保存粘贴的 Cookie';
  const textareaPlaceholder = platform.key === 'lrts'
    ? '粘贴懒人听书 App 凭证 JSON，或 token=...; imei=...'
    : platform.key === 'xmly'
      ? '粘贴喜马拉雅网页登录 Cookie'
      : '粘贴 Cookie 字符串';
  const mobileCredential = info.mobile_credential || {};
  return (
    <div className={`cookie-card cookie-detail-card ${noCookie ? 'cookie-free' : ''}`}>
      <div className="cookie-detail-kicker">{noCookie ? '平台接入说明' : '登录凭证'}</div>
      <div className="cookie-head">
        <span className="name">
          <PlatformLogo value={platform.key} name={platform.name} />
          <span className="cookie-platform-title">{platform.name}</span>
          {!noCookie && ok && info.account_name && <span className="cookie-account" title={info.account_id ? `${info.account_name} (${info.account_id})` : info.account_name}>{info.account_name}</span>}
          {!noCookie && ok && info.vip_label && !['普通用户', '未登录', ''].includes(info.vip_label) && (
            <span title="喜马拉雅会员状态" style={{
              fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 99, marginLeft: 4, whiteSpace: 'nowrap',
              background: String(info.vip_label).includes('白金') ? 'linear-gradient(135deg,#d4d4d8,#a1a1aa)' : 'linear-gradient(135deg,#fbbf24,#d97706)',
              color: '#fff',
            }}>{info.vip_label}</span>
          )}
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
          {platform.key === 'xmly' && (
            <div className="xmly-credential-status" aria-label="喜马拉雅凭证状态">
              <span className={`xmly-credential-pill ${info.has_web_cookie ? 'ready' : ''}`}>网页登录：{info.has_web_cookie ? '已设置' : '未设置'}</span>
              <span
                className={`xmly-credential-pill ${info.has_mobile_ticket ? 'ready' : (mobileCredential.has_ticket ? 'warning' : '')}`}
                title={mobileCredential.message || ''}
              >移动版 V4：{mobileCredential.local_ticket_ready
                ? '本地出票就绪'
                : (info.has_mobile_ticket ? '格式完整' : (mobileCredential.has_mobile_cookie || mobileCredential.has_ticket ? '凭证不完整' : '未设置'))}</span>
            </div>
          )}
          <div className="cookie-actions">
            {platform.qr && <button className="btn btn-primary btn-tiny" onClick={() => setModal({content: <QrLoginModal platform={platform} onDone={actions.loadCookies} onClose={closeModal} />})}><Icon id="i-qr" className="icon icon-sm" />{scanText}</button>}
            {platform.key !== 'lrts' && <button className="btn btn-ghost btn-tiny" onClick={() => setModal({content: <CookieScriptModal platform={platform} onSave={(cookie) => actions.saveCookie(platform.key, cookie)} onClose={closeModal} />})}><Icon id="i-globe" className="icon icon-sm" />浏览器获取</button>}
            {ok && <button className="btn btn-danger btn-tiny" disabled={busy[`cookieDelete:${platform.key}`]} onClick={() => actions.deleteCookie(platform.key)}><BusyIcon busy={busy[`cookieDelete:${platform.key}`]} icon="i-trash" />删除</button>}
          </div>
          {platform.key === 'xmly' && <div className="xmly-ticket-label">网页登录 Cookie</div>}
          <textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={textareaPlaceholder}
          />
          <button className="btn btn-primary btn-tiny" disabled={busy[`cookie:${platform.key}`]} onClick={() => { actions.saveCookie(platform.key, value); setValue(''); }}>
            <BusyIcon busy={busy[`cookie:${platform.key}`]} icon="i-check" />{saveText}
          </button>
          {platform.key === 'xmly' && (
            <div className="xmly-ticket-editor">
              <label className="field-label" htmlFor="xmlyMobileV4Cookie">移动版 V4 App Cookie</label>
              <div className="cookie-desc">粘贴实体 Android App 的完整 Cookie，也支持粘贴同一次 <code>baseInfo</code> 请求头或导出的 cURL。该凭证独立保存且不会回显，不会修改网页登录 Cookie。</div>
              <textarea
                id="xmlyMobileV4Cookie"
                value={mobileCookie}
                onChange={(event) => setMobileCookie(event.target.value)}
                placeholder={'Cookie: channel=android; 1&_device=android&稳定设备ID&App版本; 1&*token=账号UID&登录令牌; ...\n\n也可以直接粘贴完整 baseInfo 请求头或 cURL'}
                autoComplete="off"
                spellCheck="false"
              />
              <div className="xmly-ticket-actions">
                <button
                  className="btn btn-primary btn-tiny"
                  disabled={busy.xmlyMobileTicket || !mobileCookie.trim()}
                  onClick={async () => {
                    if (await actions.saveXimalayaMobileTicket(mobileCookie)) setMobileCookie('');
                  }}
                >
                  <BusyIcon busy={busy.xmlyMobileTicket} icon="i-key" />保存 V4 App Cookie
                </button>
                {(info.has_mobile_ticket || mobileCredential.has_ticket || mobileCredential.has_mobile_cookie) && (
                  <button className="btn btn-danger btn-tiny" disabled={busy.xmlyMobileTicketDelete} onClick={actions.deleteXimalayaMobileTicket}>
                    <BusyIcon busy={busy.xmlyMobileTicketDelete} icon="i-trash" />删除 V4 App Cookie
                  </button>
                )}
              </div>
              {mobileCredential.message && mobileCredential.state !== 'missing_ticket' && (
                <div className={`cookie-note ${info.has_mobile_ticket ? 'ok' : 'warn'}`}>{mobileCredential.message}</div>
              )}
              <div className="cookie-desc">Cookie 必须包含已登录账号 token，以及稳定的 <code>1&amp;_device=android&amp;设备ID</code>。保存成功后应显示“本地出票就绪”；AudioFlow 会为每次 V4 请求本地生成 <code>x-tk</code>，无需 Bridge/ReDroid。请勿随机更换设备 ID。</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function XimalayaMobileLoginModal({actions, onDone, onClose}) {
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('输入喜马拉雅账号绑定的手机号');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);

  const sendCode = async () => {
    setSending(true); setError('');
    try {
      const data = await actions.sendXimalayaMobileCode(phone.trim());
      setMessage(data.message || '验证码已发送');
    } catch (err) { setError(err.message); }
    finally { setSending(false); }
  };
  const login = async () => {
    setLoggingIn(true); setError('');
    try {
      const data = await actions.loginXimalayaMobile(phone.trim(), code.trim());
      setMessage(data.message || '登录成功');
      onDone?.();
      if (!data.needs_playback) onClose();
    } catch (err) { setError(err.message); }
    finally { setLoggingIn(false); }
  };

  return (
    <div className="lrts-login">
      <div className="lrts-login-head">
        <div className="lrts-login-icon"><Icon id="i-mobile" /></div>
        <div className="lrts-login-copy">
          <div className="modal-title lrts-title">喜马拉雅移动端登录</div>
          <div className="modal-sub lrts-sub">{error || message}</div>
        </div>
      </div>
      <div className="lrts-panel">
        <div className="lrts-inline">
          <input className="field-input lrts-input" value={phone} onChange={(e) => setPhone(e.target.value.replace(/\D/g, '').slice(0, 11))} placeholder="手机号" inputMode="tel" autoComplete="tel" />
          <button className="btn btn-ghost btn-sm lrts-send-btn" disabled={sending || phone.length !== 11} onClick={sendCode}>
            <BusyIcon busy={sending} icon="i-mobile" />发送验证码
          </button>
        </div>
        <input className="field-input lrts-input" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))} placeholder="短信验证码" inputMode="numeric" autoComplete="one-time-code" />
        {error && <div className="field-hint err">{error}</div>}
        <div className="lrts-note">验证码由官方喜马拉雅 App 发送和校验。若账号触发人机验证，必须先在 App 中完成验证；界面明确显示“验证码已发送”前，短信尚未发出。登录成功后自动保存 Cookie、User-Agent 和动态 Ticket 所需账号信息。</div>
        <div className="modal-actions">
          <button className="btn btn-primary btn-sm" disabled={loggingIn || phone.length !== 11 || code.length < 4} onClick={login}>
            <BusyIcon busy={loggingIn} icon="i-check" />登录并保存
          </button>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
        </div>
      </div>
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
      <div className="modal-sub modal-note">
        使用对应 App 扫码，登录成功后会自动保存 Cookie。
        {platform.key === 'xmly' && ' 喜马拉雅扫码只授权网页会话，不会导出手机 App 请求头；此前单独保存的移动端凭证会保留。'}
        {scope === 'personal' ? '此 Cookie 仅用于个人中心。' : ''}
      </div>
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
  ['download_completed', '下载完成', '任务完成后推送结果摘要', 'i-check'],
  ['download_failed', '下载异常', '失败或部分完成时及时提醒', 'i-alert'],
  ['rename_confirmation', '重命名确认', '下载完成后发送文件改名计划', 'i-edit'],
  ['subscription_queued', '自动追更', '发现新章节并加入下载队列', 'i-download'],
  ['subscription_checked', '订阅检查', '检测到缺失章节时发送通知', 'i-search'],
];

const NOTIFICATION_CHANNELS = [
  ['telegram', 'Telegram'],
  ['bark', 'Bark'],
  ['serverchan', 'Server 酱'],
  ['pushplus', 'PushPlus'],
  ['wecom_app', '企业微信应用'],
  ['wecom_robot', '企业微信机器人'],
  ['feishu', '飞书 Agent'],
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
  const activeServices = services.filter((service) => service.enabled !== false).length;
  const activeScenes = NOTIFICATION_SCENES.filter(([key]) => !!draft.scenes?.[key]).length;
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
    <div className="notification-page-layout">
      <div className="notification-overview">
        <label className={`notification-master ${draft.enabled ? 'active' : ''}`}>
          <span className="notification-master-icon"><Icon id="i-bell" /></span>
          <span className="notification-master-copy">
            <strong>{draft.enabled ? '通知系统已启用' : '通知系统未启用'}</strong>
            <small>{draft.enabled ? '已按下方规则监听并推送事件' : '配置会保留，但不会发送任何通知'}</small>
          </span>
          <input type="checkbox" checked={!!draft.enabled} onChange={(e) => update({enabled: e.target.checked})} />
          <span className="notification-switch" aria-hidden="true" />
        </label>
        <div className="notification-stats" aria-label="通知配置概览">
          <div><span>已启用渠道</span><strong>{activeServices}</strong><em>/ {services.length}</em></div>
          <div><span>触发场景</span><strong>{activeScenes}</strong><em>/ {NOTIFICATION_SCENES.length}</em></div>
        </div>
        <div className="notification-overview-actions">
          <button className="btn btn-ghost btn-sm" disabled={busy.notifications} onClick={addService}><Icon id="i-plus" className="icon icon-sm" />添加渠道</button>
          <button className="btn btn-primary btn-sm" disabled={busy.notifications} onClick={() => actions.saveNotifications(draft)}><BusyIcon busy={busy.notifications} icon="i-check" />保存配置</button>
        </div>
      </div>
      <div className="notification-workspace">
        <aside className="notification-scene-panel">
          <div className="notification-section-head">
            <div><strong>触发场景</strong><span>选择需要推送的事件</span></div>
            <em>{activeScenes}/{NOTIFICATION_SCENES.length}</em>
          </div>
          <div className="notification-scenes">
            {NOTIFICATION_SCENES.map(([key, label, description, icon]) => (
              <label className={`notification-scene-item ${draft.scenes?.[key] ? 'active' : ''}`} key={key}>
                <span className="notification-scene-icon"><Icon id={icon} /></span>
                <span><strong>{label}</strong><small>{description}</small></span>
                <input type="checkbox" checked={!!draft.scenes?.[key]} onChange={(e) => updateScene(key, e.target.checked)} />
              </label>
            ))}
          </div>
        </aside>
        <section className="notification-channel-panel" aria-label="通知渠道配置">
          <div className="notification-section-head">
            <div><strong>通知渠道</strong><span>配置接收消息的服务</span></div>
            <em>{services.length}</em>
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
            )) : (
              <div className="notification-empty">
                <span><Icon id="i-bell" /></span>
                <strong>还没有通知渠道</strong>
                <small>添加 Telegram、Bark、企业微信或 Webhook 后即可发送提醒。</small>
                <button className="btn btn-primary btn-sm" onClick={addService}><Icon id="i-plus" className="icon icon-sm" />添加第一个渠道</button>
              </div>
            )}
          </div>
        </section>
      </div>
      {services.some((s) => s.type === 'wecom_app') && <WecomTemplates />}
    </div>
  );
}

function WecomTemplates() {
  const [fields, setFields] = useState([]);
  const [templates, setTemplates] = useState({});
  const [defaults, setDefaults] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  useEffect(() => {
    setLoading(true);
    api('/api/wecom/templates').then((r) => {
      if (r.ok) { setFields(r.fields || []); setTemplates(r.templates || {}); setDefaults(r.defaults || {}); }
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);
  const setField = (key, value) => setTemplates((p) => ({...p, [key]: value}));
  const save = () => {
    setSaving(true); setMsg('');
    api('/api/wecom/templates', {method: 'POST', body: JSON.stringify({templates})})
      .then((r) => { if (r.ok) { setTemplates(r.templates || templates); setMsg('已保存'); } else setMsg(r.error || '保存失败'); })
      .catch((e) => setMsg(String(e)))
      .finally(() => { setSaving(false); setTimeout(() => setMsg(''), 2500); });
  };
  const ipt = {width: '100%', background: 'var(--panel-hi)', border: '1px solid var(--border)', borderRadius: 8, padding: '7px 10px', color: 'var(--text)', fontSize: 12.5, fontFamily: 'inherit', resize: 'vertical', boxSizing: 'border-box'};
  return (
    <div style={{marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 14}}>
      <div className="panel-head" style={{marginBottom: 8}}>
        <h4 style={{fontSize: 14}}>企业微信 · 消息模板</h4>
        <div className="panel-actions">
          <button className="btn btn-ghost btn-tiny" onClick={() => setTemplates({...defaults})}>全部恢复默认</button>
          <button className="btn btn-primary btn-tiny" disabled={saving} onClick={save}><BusyIcon busy={saving} icon="i-check" />保存模板</button>
        </div>
      </div>
      <div style={{fontSize: 12, color: 'var(--text-mute)', marginBottom: 10}}>
        交互指令的卡片 / 文本内容。变量用 <code>{'{名称}'}</code> 占位，点下方变量可插入；留空则用默认。
      </div>
      {loading ? <div className="empty small"><span className="loading" />加载中</div> : (
        <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
          {fields.map((f) => (
            <div key={f.key}>
              <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4}}>
                <label style={{fontSize: 12.5, fontWeight: 600}}>{f.label}</label>
                {defaults[f.key] !== undefined && <button className="btn btn-ghost btn-tiny" style={{fontSize: 11}} onClick={() => setField(f.key, defaults[f.key])}>恢复默认</button>}
              </div>
              <textarea
                value={templates[f.key] ?? ''}
                onChange={(e) => setField(f.key, e.target.value)}
                rows={(f.key.includes('desc') || f.key.includes('item')) ? 2 : 1}
                placeholder={defaults[f.key] || ''}
                style={ipt}
              />
              {f.vars && f.vars.length > 0 && (
                <div style={{display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4}}>
                  {f.vars.map((v) => (
                    <code key={v} onClick={() => setField(f.key, (templates[f.key] ?? '') + `{${v}}`)}
                      style={{fontSize: 11, padding: '1px 6px', borderRadius: 5, background: 'var(--pre-bg)', border: '1px solid var(--border)', color: 'var(--primary)', cursor: 'pointer'}}>{`{${v}}`}</code>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {msg && <div style={{marginTop: 8, fontSize: 12, color: msg === '已保存' ? 'var(--success)' : 'var(--danger)'}}>{msg}</div>}
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
  const typeLabel = NOTIFICATION_CHANNELS.find(([key]) => key === type)?.[1] || '通知渠道';
  const channelOptions = NOTIFICATION_CHANNELS.map(([key, label]) => <option value={key} key={key}>{label}</option>);
  return (
    <div className={`notification-service ${service.enabled === false ? 'disabled' : ''}`}>
      <div className="notification-service-titlebar">
        <span className="notification-service-icon"><Icon id="i-bell" /></span>
        <span className="notification-service-copy"><strong>{service.name || typeLabel}</strong><small>{typeLabel}</small></span>
        <label className="notification-service-toggle"><input type="checkbox" checked={service.enabled !== false} onChange={(e) => onChange({enabled: e.target.checked})} /><span>启用</span></label>
        <div className="notification-service-actions">
          <button className="btn btn-ghost btn-tiny" disabled={busy[`notificationTest:${service.id}`]} onClick={onTest}><BusyIcon busy={busy[`notificationTest:${service.id}`]} icon="i-bell" />测试</button>
          <button className="btn btn-danger btn-tiny" onClick={onRemove} title="删除渠道"><Icon id="i-trash" className="icon icon-sm" /><span>删除</span></button>
        </div>
      </div>
      <div className="notification-service-head">
        <input className="field-input" value={service.name || ''} onChange={(e) => onChange({name: e.target.value})} placeholder="渠道名称" />
        <select className="field-select" value={type} onChange={(e) => onChange({type: e.target.value, name: NOTIFICATION_CHANNELS.find(([key]) => key === e.target.value)?.[1] || service.name, config: {}})}>{channelOptions}</select>
      </div>
      <div className="notification-service-fields">
        <NotificationChannelFields type={type} config={cfg} onConfig={onConfig} />
        {type === 'wecom_app' && (
          <div className="field-row">
            <label className="field-label">回调 URL</label>
            <input
              className="field-input"
              readOnly
              value={`${window.location.origin}/api/wecom/callback/${service.id}`}
              onFocus={(event) => event.target.select()}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function NotificationChannelFields({type, config, onConfig}) {
  const input = (key, label, placeholder = '') => (
    <div className="field-row"><label className="field-label">{label}</label><input className="field-input" value={config[key] || ''} onChange={(e) => onConfig(key, e.target.value)} placeholder={placeholder || config[`${key}_masked`] || ''} /></div>
  );
  const textarea = (key, label, placeholder = '') => (
    <div className="field-row"><label className="field-label">{label}</label><textarea className="field-input" rows="2" value={Array.isArray(config[key]) ? config[key].join('\n') : (config[key] || '')} onChange={(e) => onConfig(key, e.target.value)} placeholder={placeholder} /></div>
  );
  if (type === 'telegram') return <>{input('bot_token', 'Bot Token')}{input('chat_id', 'Chat ID')}</>;
  if (type === 'bark') return <>{input('key', 'Bark Key')}{input('server', '服务器', 'https://api.day.app')}</>;
  if (type === 'serverchan') return <>{input('send_key', 'SendKey')}</>;
  if (type === 'pushplus') return <>{input('token', 'Token')}{input('topic', '群组编码', '可选')}</>;
  if (type === 'wecom_app') return (
    <>
      {input('corp_id', '企业 ID')}
      {input('agent_id', '应用 AgentId')}
      {input('secret', '应用 Secret')}
      {input('to_user', '默认接收人', '@all')}
      {input('token', '回调 Token')}
      {input('encoding_aes_key', 'EncodingAESKey')}
      {input('api_base', 'API 地址', 'https://qyapi.weixin.qq.com')}
    </>
  );
  if (type === 'wecom_robot') return <>{input('key', '机器人 Key / Webhook URL')}</>;
  if (type === 'feishu') return (
    <>
      {input('app_id', 'App ID')}
      {input('app_secret', 'App Secret')}
      <div className="field-row"><label className="field-label">接收目标类型</label><select className="field-select" value={config.receive_id_type || 'open_id'} onChange={(e) => onConfig('receive_id_type', e.target.value)}><option value="open_id">用户 Open ID</option><option value="chat_id">群聊 Chat ID</option><option value="user_id">用户 User ID</option><option value="union_id">用户 Union ID</option><option value="email">用户邮箱</option></select></div>
      {input('receive_id', '默认接收目标')}
      {textarea('allowed_users', '允许的用户 Open ID', '每行一个；用户和群聊均留空时拒绝所有入站消息')}
      {textarea('allowed_chats', '允许的群聊 Chat ID', '每行一个；同时填写时需同时匹配')}
      {input('api_base', 'API 地址', 'https://open.feishu.cn')}
    </>
  );
  return (
    <>
      {input('url', 'Webhook URL')}
      <div className="field-row"><label className="field-label">Method</label><select className="field-select" value={config.method || 'POST'} onChange={(e) => onConfig('method', e.target.value)}><option value="POST">POST</option><option value="PUT">PUT</option><option value="GET">GET</option></select></div>
    </>
  );
}

function BackupImportModal({actions, onClose}) {
  const [text, setText] = useState('');
  const onFile = (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ''));
    reader.readAsText(file);
  };
  const doImport = async () => {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      actions.showToast('内容不是合法的 JSON', 'err');
      return;
    }
    try {
      await actions.importBackup(parsed);
      onClose();
    } catch (error) {
      actions.showToast('导入失败：' + error.message, 'err');
    }
  };
  return (
    <>
      <div className="modal-title"><Icon id="i-folder" />导入全量备份</div>
      <div className="modal-sub">上传导出的备份 .json 文件或粘贴内容。会恢复 Cookie + 订阅 + 订阅设置（同名覆盖），章节首次检测时重新拉取。</div>
      <div className="modal-toolbar">
        <label className="btn btn-ghost btn-sm" style={{cursor: 'pointer'}}>
          <Icon id="i-folder" className="icon icon-sm" />选择文件
          <input type="file" accept="application/json,.json" onChange={onFile} style={{display: 'none'}} />
        </label>
      </div>
      <textarea className="cookie-modal-textarea" value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴 audioflow-backup-*.json 的内容" style={{minHeight: 160}} />
      <div className="modal-actions">
        <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
        <button className="btn btn-primary btn-sm" disabled={!text.trim()} onClick={doImport}>导入恢复</button>
      </div>
    </>
  );
}

const AGENT_TOOL_LABELS = {
  list_downloads: '读取下载任务',
  list_rename_plans: '读取重命名计划',
  get_rename_plan: '查看计划详情',
  create_rename_plan: '生成待确认计划',
  analyze_rename_plan_with_ai: 'AI 复核风险文件',
  apply_ai_rename_suggestions: '应用 AI 建议',
  create_rename_rule_draft: '生成规则草稿',
  resolve_rename_plan_safe: '保留风险文件',
  confirm_rename_plan: '确认并执行整理',
  cancel_rename_plan: '取消整理计划',
};

const RENAME_STATUS_TEXT = {
  needs_review: '需要复核',
  pending_confirmation: '等待确认',
  completed: '已完成',
  no_changes: '无需整理',
  cancelled: '已取消',
  failed: '执行失败',
  expired: '已过期',
};

const splitRuleLines = (value) => String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
const joinRuleLines = (value) => Array.isArray(value) ? value.join('\n') : '';
const cloneRuleData = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const mergeRuleData = (base, override) => {
  const result = cloneRuleData(base || {});
  Object.entries(override || {}).forEach(([key, value]) => {
    result[key] = value && typeof value === 'object' && !Array.isArray(value)
      ? mergeRuleData(result[key] || {}, value)
      : cloneRuleData(value);
  });
  return result;
};

function RenameRulesModal({rulesState, actions, busy, onClose}) {
  const initialPacks = rulesState?.packs || [];
  const effectiveRules = rulesState?.effective?.rules || initialPacks[0]?.rules || {};
  const [packs, setPacks] = useState(initialPacks);
  const [selectedId, setSelectedId] = useState(initialPacks.find((item) => item.status === 'draft')?.id || initialPacks.find((item) => item.status === 'active' && !item.readonly)?.id || initialPacks[0]?.id || '');
  const selected = selectedId === '__new__' ? null : (packs.find((item) => item.id === selectedId) || packs[0]);
  const makeDraft = (pack) => {
    const rules = mergeRuleData(effectiveRules, pack?.rules || {});
    const effectiveCleanup = effectiveRules?.cleanup || {};
    const cleanup = rules.cleanup || {};
    cleanup.ad_keywords = [...new Set([...(effectiveCleanup.ad_keywords || []), ...(cleanup.ad_keywords || [])])];
    cleanup.ad_patterns = [...new Set([...(effectiveCleanup.ad_patterns || []), ...(cleanup.ad_patterns || [])])];
    if (Number(pack?.schema_version || 1) < 2 && cleanup.split_ad_after_first_space === false) {
      cleanup.split_ad_after_first_space = effectiveCleanup.split_ad_after_first_space !== false;
    }
    rules.cleanup = cleanup;
    return {
      ...(pack?.status === 'draft' ? {id: pack.id} : {}),
      name: pack?.readonly ? '我的全局重命名规则' : (pack?.name || '我的重命名规则'),
      description: pack?.description || '',
      scope: pack?.readonly ? 'global' : (pack?.scope || 'global'),
      selector: pack?.readonly ? '' : (pack?.selector || ''),
      rules,
    };
  };
  const [draft, setDraft] = useState(makeDraft(selected));
  const [samples, setSamples] = useState('0001-第1集 正文（求订阅）.m4a\n0002-第2回 义务救援.m4a\n片花---每天更新.mp3');
  const [sampleAlbum, setSampleAlbum] = useState('示例书名');
  const [testResults, setTestResults] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => { setDraft(makeDraft(selected)); setError(''); setTestResults([]); }, [selected?.id]);
  const updateRule = (section, field, value) => setDraft((prev) => ({
    ...prev,
    rules: {...(prev.rules || {}), [section]: {...((prev.rules || {})[section] || {}), [field]: value}},
  }));
  const format = draft.rules?.format || {};
  const cleanup = draft.rules?.cleanup || {};
  const special = draft.rules?.special_files || {};
  const validation = draft.rules?.validation || {};
  const save = async () => {
    setError('');
    try {
      const saved = await actions.saveRenameRuleDraft(draft);
      setPacks((prev) => [saved, ...prev.filter((item) => item.id !== saved.id)]);
      setSelectedId(saved.id);
      setDraft(makeDraft(saved));
    } catch (err) { setError(err.message); }
  };
  const activate = async () => {
    setError('');
    try {
      const active = await actions.activateRenameRule(selected.id);
      setPacks((prev) => prev.map((item) => {
        if (item.id === active.id) return active;
        if (item.scope === active.scope && item.selector === active.selector && item.status === 'active') return {...item, status: 'archived'};
        return item;
      }));
    } catch (err) { setError(err.message); }
  };
  const remove = async () => {
    setError('');
    try {
      await actions.deleteRenameRuleDraft(selected.id);
      const next = packs.filter((item) => item.id !== selected.id);
      setPacks(next);
      setSelectedId(next[0]?.id || '');
    } catch (err) { setError(err.message); }
  };
  const test = async () => {
    setError('');
    try { setTestResults(await actions.testRenameRules(draft.rules, sampleAlbum, splitRuleLines(samples))); }
    catch (err) { setError(err.message); }
  };
  return <div className="rename-rules-modal">
    <div className="rename-plans-head">
      <div className="modal-title"><Icon id="i-settings" />重命名规则中心</div>
      <div className="modal-sub">规则更新只影响之后生成的新计划；已有计划继续使用创建时的规则快照。</div>
      <div className="field-row-inline rule-pack-picker">
        <select className="field-select" value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{selectedId === '__new__' && <option value="__new__">新规则草稿</option>}{packs.map((pack) => <option value={pack.id} key={pack.id}>{pack.name} · {pack.status === 'active' ? '已启用' : pack.status === 'draft' ? '草稿' : pack.status === 'archived' ? '历史版本' : '内置'}</option>)}</select>
        <button className="btn btn-ghost btn-sm" onClick={() => { setSelectedId('__new__'); setDraft(makeDraft({rules: effectiveRules})); }}>新建</button>
      </div>
    </div>
    <div className="rename-rules-content">
      <section className="rule-section">
        <h3>规则范围</h3>
        <div className="rule-grid"><label className="field"><span>名称</span><input value={draft.name || ''} onChange={(event) => setDraft((prev) => ({...prev, name: event.target.value}))} /></label><label className="field"><span>生效范围</span><select value={draft.scope || 'global'} onChange={(event) => setDraft((prev) => ({...prev, scope: event.target.value, selector: event.target.value === 'global' ? '' : prev.selector}))}><option value="global">全部平台</option><option value="platform">指定平台</option><option value="album">指定专辑</option></select></label>{draft.scope !== 'global' && <label className="field"><span>{draft.scope === 'platform' ? '平台名称' : '专辑 ID 或名称'}</span><input value={draft.selector || ''} onChange={(event) => setDraft((prev) => ({...prev, selector: event.target.value}))} /></label>}</div>
      </section>
      <section className="rule-section">
        <h3>文件名格式</h3>
        <div className="rule-grid"><label className="field rule-span"><span>章节模板</span><input value={format.chapter_template || ''} onChange={(event) => updateRule('format', 'chapter_template', event.target.value)} /></label><label className="field rule-span"><span>特殊文件模板</span><input value={format.special_template || ''} onChange={(event) => updateRule('format', 'special_template', event.target.value)} /></label><label className="field"><span>章节单位</span><select value={format.chapter_unit || 'auto'} onChange={(event) => updateRule('format', 'chapter_unit', event.target.value)}><option value="auto">跟随专辑</option><option value="集">集</option><option value="章">章</option><option value="回">回</option></select></label><label className="field"><span>序号策略</span><select value={format.prefix_strategy || 'chapter'} onChange={(event) => updateRule('format', 'prefix_strategy', event.target.value)}><option value="chapter">按章节号并保留缺号</option><option value="continuous">按文件连续编号</option><option value="original">保留原序号</option></select></label><label className="field"><span>序号位数</span><input type="number" min="1" max="8" value={format.prefix_width || 4} onChange={(event) => updateRule('format', 'prefix_width', Number(event.target.value))} /></label><label className="field"><span>章节号位数</span><input type="number" min="1" max="8" value={format.chapter_width || 3} onChange={(event) => updateRule('format', 'chapter_width', Number(event.target.value))} /></label><label className="field"><span>起始序号</span><input type="number" min="1" value={format.prefix_start || 1} onChange={(event) => updateRule('format', 'prefix_start', Number(event.target.value))} /></label><label className="check-row"><input type="checkbox" checked={format.smart_title_separator !== false} onChange={(event) => updateRule('format', 'smart_title_separator', event.target.checked)} /><span>标题前智能空格</span></label></div>
        <div className="cookie-desc">可用字段：{'{prefix} {book} {chapter} {unit} {title_sep} {title} {quality_sep} {quality} {label} {ext}'}，扩展名必须位于末尾。</div>
      </section>
      <section className="rule-section">
        <h3>标题清理</h3>
        <div className="rule-grid"><label className="field"><span>广告关键词，每行一个</span><textarea rows="7" value={joinRuleLines(cleanup.ad_keywords)} onChange={(event) => updateRule('cleanup', 'ad_keywords', splitRuleLines(event.target.value))} /></label><label className="field"><span>安全正则，按顺序执行</span><textarea rows="7" value={joinRuleLines(cleanup.ad_patterns)} onChange={(event) => updateRule('cleanup', 'ad_patterns', splitRuleLines(event.target.value))} placeholder="每行一个，只填写有明确边界的模式" /></label><label className="field"><span>必须保留的结尾标识</span><textarea rows="4" value={joinRuleLines(cleanup.preserve_keywords)} onChange={(event) => updateRule('cleanup', 'preserve_keywords', splitRuleLines(event.target.value))} /></label><label className="field"><span>多段真实标题例外</span><textarea rows="4" value={joinRuleLines(cleanup.title_exceptions)} onChange={(event) => updateRule('cleanup', 'title_exceptions', splitRuleLines(event.target.value))} /></label></div>
        <div className="rule-grid"><label className="check-row"><input type="checkbox" checked={cleanup.split_ad_after_first_space !== false} onChange={(event) => updateRule('cleanup', 'split_ad_after_first_space', event.target.checked)} /><span>自动删除空格或标点后的广告尾注</span></label><label className="check-row"><input type="checkbox" checked={validation.scan_residual_ads !== false} disabled /><span>检测到残留广告时阻止执行（强制）</span></label></div>
        <div className="cookie-desc">内置广告库始终生效；这里添加的关键词和正则用于补充新广告变体。真实多段标题可加入例外列表。</div>
      </section>
      <section className="rule-section">
        <h3>特殊文件</h3>
        <div className="rule-grid"><label className="field"><span>内容文件标签</span><textarea rows="4" value={joinRuleLines(special.content_labels)} onChange={(event) => updateRule('special_files', 'content_labels', splitRuleLines(event.target.value))} /></label><label className="field"><span>运营文件标签</span><textarea rows="4" value={joinRuleLines(special.operational_labels)} onChange={(event) => updateRule('special_files', 'operational_labels', splitRuleLines(event.target.value))} /></label><label className="field"><span>内容文件建议</span><select value={special.content_default || 'organize'} onChange={(event) => updateRule('special_files', 'content_default', event.target.value)}><option value="organize">按书名整理</option><option value="keep">保持原名</option><option value="quarantine">隔离</option></select></label><label className="field"><span>运营文件建议</span><select value={special.operational_default || 'quarantine'} onChange={(event) => updateRule('special_files', 'operational_default', event.target.value)}><option value="organize">按书名整理</option><option value="keep">保持原名</option><option value="quarantine">隔离</option></select></label></div>
      </section>
      <section className="rule-section rule-test">
        <h3>样例测试</h3>
        <div className="rule-grid"><label className="field"><span>示例书名</span><input value={sampleAlbum} onChange={(event) => setSampleAlbum(event.target.value)} /></label><label className="field rule-span"><span>原文件名，每行一个</span><textarea rows="5" value={samples} onChange={(event) => setSamples(event.target.value)} /></label></div>
        <button className="btn btn-ghost btn-sm" onClick={test}>运行测试</button>
        {!!testResults.length && <div className="rule-test-results">{testResults.map((item) => <div key={item.source_name}><strong>{item.source_name}</strong><span>{item.target_name}</span>{!!item.issues?.length && <em>{item.issues.join('；')}</em>}</div>)}</div>}
      </section>
      {!!error && <div className="login-error" role="alert">{error}</div>}
    </div>
    <div className="modal-actions rename-plans-actions"><button className="btn btn-ghost btn-sm" onClick={onClose}>关闭</button>{selected?.status === 'draft' && <button className="btn btn-danger btn-sm" disabled={busy.renameRules} onClick={remove}><Icon id="i-trash" className="icon icon-sm" />删除草稿</button>}<button className="btn btn-ghost btn-sm" disabled={busy.renameRules} onClick={save}><Icon id="i-check" className="icon icon-sm" />保存为草稿</button>{selected?.status === 'draft' && <button className="btn btn-primary btn-sm" disabled={busy.renameRules} onClick={activate}><Icon id="i-bolt" className="icon icon-sm" />启用此版本</button>}</div>
  </div>;
}

function RenamePlansModal({plans, renameFolders, agentStatus, actions, busy, onClose}) {
  const [planList, setPlanList] = useState(plans || []);
  const [folderList, setFolderList] = useState(renameFolders || []);
  const [showFolders, setShowFolders] = useState(false);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const visiblePlans = planList;
  const [selectedId, setSelectedId] = useState(visiblePlans[0]?.id || '');
  const selected = visiblePlans.find((item) => item.id === selectedId) || visiblePlans[0];
  const [configuration, setConfiguration] = useState(selected?.configuration || {});
  const [specialActions, setSpecialActions] = useState({});
  const [itemActions, setItemActions] = useState({});
  const [itemOverrides, setItemOverrides] = useState({});
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState([]);
  const [operation, setOperation] = useState('');
  const [operationError, setOperationError] = useState('');
  const operationRef = useRef(false);
  const contentRef = useRef(null);
  const loadRenameFolders = actions.loadRenameFolders;
  useEffect(() => {
    setFolderList(renameFolders || []);
  }, [renameFolders]);
  useEffect(() => {
    let active = true;
    if (loadRenameFolders) loadRenameFolders().then((folders) => active && setFolderList(folders || [])).catch(() => {});
    return () => { active = false; };
  }, [loadRenameFolders]);
  useEffect(() => {
    setConfiguration(selected?.configuration || {});
    setSpecialActions(Object.fromEntries((selected?.items || []).filter((item) => item.kind === 'special').map((item) => {
      const issue = (selected?.issues || []).find((entry) => entry.type === 'special_file' && (entry.relative_source || entry.file) === (item.relative_source || item.source_name));
      return [item.relative_source || item.source_name, item.action === 'undecided' ? (issue?.suggested_action || 'keep') : item.action];
    })));
    setItemActions({});
    setItemOverrides({});
    setSelectedSuggestionIds([]);
    setOperationError('');
    contentRef.current?.scrollTo({top: 0});
  }, [selected?.id]);
  useEffect(() => {
    const suggestions = selected?.ai_analysis?.suggestions || [];
    setSelectedSuggestionIds(suggestions.filter((item) => item.action === 'rename').map((item) => item.id));
  }, [selected?.id, selected?.ai_analysis?.created_at]);
  const run = async (name, fn, closeAfter = true) => {
    if (operationRef.current) return;
    operationRef.current = true;
    setOperation(name);
    setOperationError('');
    try {
      const result = await fn();
      if (result?.id) setPlanList((prev) => prev.map((item) => item.id === result.id ? result : item));
      if (closeAfter) onClose();
    } catch (error) {
      setOperationError(error?.message || '操作失败，请刷新计划后重试');
    } finally {
      operationRef.current = false;
      setOperation('');
    }
  };
  const activeFolderPlan = (relativePath) => planList.find((plan) => plan.task_id === `folder:${relativePath}` && !['cancelled', 'expired', 'failed', 'completed'].includes(plan.status));
  const organizeFolder = (folder) => run('folder:' + folder.relative_path, async () => {
    const plan = await actions.analyzeRenameFolder(folder.relative_path);
    if (plan?.id) {
      setPlanList((prev) => [plan, ...prev.filter((item) => item.id !== plan.id)]);
      setSelectedId(plan.id);
      setShowFolders(false);
    }
    return plan;
  }, false);
  const folderPanel = showFolders && <section className="rename-folder-panel">
    <div className="rename-review-heading"><label className="field-label">下载目录中的专辑文件夹</label><button className="icon-btn" title="刷新文件夹列表" aria-label="刷新文件夹列表" onClick={() => loadRenameFolders && loadRenameFolders().then((folders) => setFolderList(folders || [])).catch((error) => setOperationError(error.message))}><Icon id="i-refresh" /></button></div>
    {!folderList.length ? <div className="empty small">没有找到含音频文件的专辑文件夹</div> : <div className="rename-folder-list">{folderList.map((folder) => {
      const active = activeFolderPlan(folder.relative_path);
      const key = folder.relative_path;
      return <div className="rename-folder-row" key={key}><div><strong>{folder.name}</strong><span>{folder.relative_path}</span></div><span className="rename-folder-count">{folder.audio_count} 个音频</span><button className="btn btn-ghost btn-sm" disabled={!!active || !!operation} onClick={() => organizeFolder(folder)}><Icon id="i-edit" className="icon icon-sm" />{active ? '已有计划' : '生成计划'}</button></div>;
    })}</div>}
  </section>;
  const planHeader = <div className="rename-plans-head">
    <div className="rename-review-heading"><div className="modal-title"><Icon id="i-edit" />有声书整理计划</div><button className="btn btn-ghost btn-sm" onClick={() => setShowFolders((value) => !value)}><Icon id="i-folder" className="icon icon-sm" />整理本地文件夹</button></div>
    {!!visiblePlans.length && <><div className="field-row"><label className="field-label">计划</label><select className="field-select" value={selected?.id || ''} onChange={(event) => setSelectedId(event.target.value)}>{visiblePlans.map((plan) => <option value={plan.id} key={plan.id}>{(plan.album || {}).title || plan.id} · {RENAME_STATUS_TEXT[plan.status] || plan.status}</option>)}</select></div><div className="modal-sub">{selected?.id} · 章节 {selected?.summary?.chapters || 0} · 特殊文件 {selected?.summary?.special_files || 0} · 缺号 {selected?.summary?.missing_chapters || 0} · 待处理 {selected?.summary?.planned || 0}</div></>}
  </div>;
  if (!selected) return <div className="rename-plans-modal"><>{planHeader}{folderPanel}<div className="empty small">暂无计划</div></></div>;
  const summary = selected.summary || {};
  const unresolved = (selected.issues || []).filter((issue) => issue.blocking !== false && !issue.resolved);
  const riskyItems = (selected.items || []).filter((item) => item.kind === 'chapter' && unresolved.some((issue) => (issue.relative_source || issue.file) === (item.relative_source || item.source_name) || issue.file === item.source_name));
  const aiSuggestions = selected.ai_analysis?.suggestions || [];
  const aiMode = selected.ai_analysis?.mode === 'full_clean' ? '全量清洗' : '风险复核';
  const aiState = selected.ai_clean || {};
  const provider = agentStatus?.config?.providers?.[agentStatus?.config?.provider] || {};
  const aiConfigured = Boolean(agentStatus?.config?.enabled && (provider.configured || provider.api_key || provider.base_url));
  const fullCleanRows = (selected.items || []).filter((item) => item.kind === 'chapter').map((item) => ({
    item,
    suggestion: aiSuggestions.find((entry) => entry.relative_source === (item.relative_source || item.source_name)),
  })).filter(({suggestion}) => suggestion && (!onlyChanged || suggestion.action === 'rename'));
  const fullCleanIds = fullCleanRows.map(({suggestion}) => suggestion.id).filter(Boolean);
  const allFullSelected = fullCleanIds.length > 0 && fullCleanIds.every((id) => selectedSuggestionIds.includes(id));
  return (
    <div className="rename-plans-modal">
      {planHeader}
      {folderPanel}
      <div className="rename-plans-content" ref={contentRef}>
        {['needs_review', 'pending_confirmation'].includes(selected.status) && <>
          <div className="field-row"><label className="field-label">书名</label><input className="field-input" value={configuration.album_title || ''} onChange={(event) => setConfiguration((prev) => ({...prev, album_title: event.target.value}))} /></div>
          <div className="field-row"><label className="field-label">章节单位与序号</label><div className="field-row-inline"><select className="field-select" value={configuration.chapter_unit || '集'} onChange={(event) => setConfiguration((prev) => ({...prev, chapter_unit: event.target.value}))}><option value="集">集</option><option value="章">章</option><option value="回">回</option></select><select className="field-select" value={configuration.prefix_strategy || 'chapter'} onChange={(event) => setConfiguration((prev) => ({...prev, prefix_strategy: event.target.value}))}><option value="chapter">按章节号并保留缺号</option><option value="continuous">连续编号</option><option value="original">保留原序号</option></select></div></div>
          <div className="field-row"><label className="field-label">补零位数</label><div className="field-row-inline"><input className="field-input" type="number" min="1" max="8" value={configuration.prefix_width || 4} onChange={(event) => setConfiguration((prev) => ({...prev, prefix_width: Number(event.target.value) || 4}))} /><span className="field-suffix">序号</span><input className="field-input" type="number" min="1" max="8" value={configuration.chapter_width || 3} onChange={(event) => setConfiguration((prev) => ({...prev, chapter_width: Number(event.target.value) || 3}))} /><span className="field-suffix">章节号</span></div></div>
          <details className="rename-format-details"><summary>本专辑命名模板</summary><div className="field-row"><label className="field-label">章节模板</label><input className="field-input" value={configuration.chapter_template || ''} onChange={(event) => setConfiguration((prev) => ({...prev, chapter_template: event.target.value}))} /></div><div className="field-row"><label className="field-label">特殊文件模板</label><input className="field-input" value={configuration.special_template || ''} onChange={(event) => setConfiguration((prev) => ({...prev, special_template: event.target.value}))} /></div></details>
          {selected.volume_count > 1 && Object.entries(configuration.volumes || {}).map(([index, name]) => <div className="field-row" key={index}><label className="field-label">第 {index} 册书名</label><input className="field-input" value={name} onChange={(event) => setConfiguration((prev) => ({...prev, volumes: {...(prev.volumes || {}), [index]: event.target.value}}))} placeholder="总书名·分册名" /></div>)}
        </>}
        {!!unresolved.length && <div className="field-row"><div className="rename-review-heading"><label className="field-label">待确认问题（{unresolved.length}）</label><button className="btn btn-ghost btn-sm" disabled={!!operation || busy['renameAI:' + selected.id]} onClick={() => run('ai', () => actions.analyzeRenamePlanAI(selected.id), false)}><BusyIcon busy={operation === 'ai'} icon="i-agent" />{operation === 'ai' ? 'AI 正在复核' : 'AI 复核风险项'}</button></div><div className="event-list">{unresolved.map((issue) => <div className="event-row" key={issue.id}><strong>{issue.file || issue.type}</strong><span>{issue.message}</span></div>)}</div></div>}
        {['needs_review', 'pending_confirmation'].includes(selected.status) && <div className="ai-review-summary rename-full-clean"><div className="rename-review-heading"><strong>AI 全量清洗</strong><span className="rename-ai-mode">{aiMode}</span></div><span>{aiState.status === 'running' ? `正在处理 ${aiState.done || 0}/${aiState.total || summary.chapters || 0}` : aiState.status === 'failed' ? `处理失败：${aiState.error || '请重试'}` : aiState.status === 'completed' ? `已完成 ${aiState.done || aiState.total || 0} 条建议，可逐项勾选应用` : '对全部章节标题进行第二轮清洗，结果只作为建议，不会自动执行。'}</span><button className="btn btn-ghost btn-sm" disabled={!aiConfigured || !!operation || aiState.status === 'running'} title={aiConfigured ? '启动全量 AI 清洗' : '请先在 Agent 设置配置模型'} onClick={() => run('aiClean', () => actions.startRenameAIClean(selected.id), false)}><BusyIcon busy={operation === 'aiClean' || aiState.status === 'running'} icon="i-agent" />{aiState.status === 'running' ? 'AI 清洗中' : aiConfigured ? '全量 AI 清洗' : '未配置模型'}</button>{aiState.status === 'running' && <div className="rename-progress"><span style={{width: `${Math.min(100, Math.round(100 * Number(aiState.done || 0) / Math.max(1, Number(aiState.total || 1)) ))}%`}} /></div>}</div>}
        {selected.ai_analysis?.mode === 'full_clean' && !!aiSuggestions.length && <div className="rename-ai-table-wrap"><div className="rename-review-heading"><label className="field-label">全量清洗建议（{fullCleanRows.length}{onlyChanged ? `/${aiSuggestions.length}` : ''}）</label><div className="field-row-inline"><label className="ai-table-toggle"><input type="checkbox" checked={onlyChanged} onChange={(event) => setOnlyChanged(event.target.checked)} />仅看有改动</label><button className="btn btn-ghost btn-sm" onClick={() => setSelectedSuggestionIds((prev) => allFullSelected ? prev.filter((id) => !fullCleanIds.includes(id)) : [...new Set([...prev, ...fullCleanIds])])}>全选</button><button className="btn btn-primary btn-sm" disabled={!selectedSuggestionIds.length || !!operation} onClick={() => run('aiApply', () => actions.applyAIRenameSuggestions(selected.id, selectedSuggestionIds), false)}><Icon id="i-check" className="icon icon-sm" />应用所选</button></div></div><div className="rename-ai-table"><div className="rename-ai-row rename-ai-head"><span>原始文件名</span><span>规则清洗标题</span><span>AI 建议标题</span><span>理由 / 置信度</span><span>选择</span></div>{fullCleanRows.map(({item, suggestion}) => <label className="rename-ai-row" key={suggestion.id}><span title={item.source_name}>{item.source_name}</span><span>{item.clean_title || '（空）'}</span><span className={suggestion.action === 'rename' ? 'changed' : ''}>{suggestion.clean_title || item.clean_title || '（保留）'}</span><span>{suggestion.reason || '未提供理由'}<em>{Math.round(Number(suggestion.confidence || 0) * 100)}%</em></span><span><input type="checkbox" checked={selectedSuggestionIds.includes(suggestion.id)} onChange={(event) => setSelectedSuggestionIds((prev) => event.target.checked ? [...new Set([...prev, suggestion.id])] : prev.filter((id) => id !== suggestion.id))} /></span></label>)}</div></div>}
        {!!aiSuggestions.length && <div className="ai-review-summary"><strong>AI 建议 {aiSuggestions.length} 条</strong><span>{selected.ai_analysis?.summary || 'AI 建议不会自动执行，请逐项选择。'}</span><div className="field-row-inline"><button className="btn btn-ghost btn-sm" disabled={!selectedSuggestionIds.length || !!operation} onClick={() => run('aiApply', () => actions.applyAIRenameSuggestions(selected.id, selectedSuggestionIds), false)}>应用选中建议</button><button className="btn btn-ghost btn-sm" disabled={!!operation} onClick={() => run('aiRule', () => actions.createAIRenameRuleDraft(selected.id), false)}>生成规则草稿</button></div></div>}
        {(selected.items || []).filter((item) => item.kind === 'special').map((item) => {
          const key = item.relative_source || item.source_name;
          const reason = unresolved.find((issue) => (issue.relative_source || issue.file) === key);
          return <div className="field-row rename-special-item" key={key}><div className="rename-review-name"><span>原文件</span><strong>{item.source_name}</strong></div><div className="rename-special-meta"><span>{item.special_type === 'content' ? '内容文件' : item.special_type === 'operational' ? '运营文件' : '未分类'}</span><span>{Math.max(0.01, Number(item.size || 0) / 1024 / 1024).toFixed(2)} MB</span></div><div className="rename-review-name suggested"><span>整理后</span><strong>{item.target_name || '保持原名'}</strong></div>{reason && <div className="rename-review-reason">建议原因：{reason.message}</div>}<select className="field-select" value={specialActions[key] || 'keep'} onChange={(event) => setSpecialActions((prev) => ({...prev, [key]: event.target.value}))}><option value="organize">保留并按书名整理</option><option value="keep">保持原名不动</option><option value="quarantine">移入可恢复隔离目录</option></select></div>;
        })}
        {riskyItems.map((item) => {
          const key = item.relative_source || item.source_name;
          const reasons = unresolved.filter((issue) => (issue.relative_source || issue.file) === key || issue.file === item.source_name);
          const ai = aiSuggestions.find((entry) => entry.relative_source === key);
          return <div className="field-row rename-review-item" key={key}>
            <div className="rename-review-name"><span>原文件</span><strong>{item.source_name}</strong></div>
            <div className="rename-review-name suggested"><span>建议改为</span><strong>{item.target_name || '保持原名不动'}</strong></div>
            {!!reasons.length && <div className="rename-review-reason">复核原因：{reasons.map((issue) => issue.message).join('；')}</div>}
            {ai && <label className="ai-suggestion"><input type="checkbox" checked={selectedSuggestionIds.includes(ai.id)} onChange={(event) => setSelectedSuggestionIds((prev) => event.target.checked ? [...prev, ai.id] : prev.filter((id) => id !== ai.id))} /><span><strong>AI：{ai.action === 'rename' ? `建议标题“${ai.clean_title}”` : ai.action === 'accept' ? '接受当前建议' : '保持原名'}</strong><em>{Math.round((ai.confidence || 0) * 100)}% · {ai.reason}</em></span></label>}
            <label className="field rename-manual-title"><span>手工修正纯标题</span><input className="field-input" value={itemOverrides[key]?.clean_title ?? item.clean_title ?? ''} onChange={(event) => { setItemOverrides((prev) => ({...prev, [key]: {...(prev[key] || {}), clean_title: event.target.value, action: 'accept'}})); setItemActions((prev) => ({...prev, [key]: 'accept'})); }} /></label>
            <select className="field-select" aria-label={`${item.source_name} 的处理方式`} value={itemActions[key] || 'keep'} onChange={(event) => setItemActions((prev) => ({...prev, [key]: event.target.value}))}><option value="keep">保持原名不动</option><option value="accept">按上方建议重命名</option></select>
          </div>;
        })}
        {selected.status === 'completed' && selected.verification && <section className="rename-verification"><div className="rename-review-heading"><strong>收尾验证</strong><span className={selected.verification.passed ? 'ok' : 'warning'}>{selected.verification.passed ? '通过' : '发现问题'}</span></div>{(selected.verification.checks || []).map((check) => <div className="rename-verification-check" key={check.name}><span>{check.passed ? '✓' : '!'}</span><strong>{check.name}</strong><em>{check.passed ? '通过' : `${(check.details || []).length} 项`}</em>{!check.passed && <small>{(check.details || []).slice(0, 5).join('；')}</small>}</div>)}</section>}
        <details><summary>查看完整映射（{(selected.items || []).length}）</summary><pre className="code log-code rename-full-mapping">{(selected.items || []).map((item) => `${item.source_name} -> ${item.target_name}`).join('\n') || '无变更'}</pre></details>
        {operation === 'confirm' && <div className="cookie-desc" role="status">正在校验并执行 {summary.planned || 0} 个文件...</div>}
        {!!operationError && <div className="login-error" role="alert">{operationError}</div>}
      </div>
      <div className="modal-actions rename-plans-actions">
        <button className="btn btn-ghost btn-sm" disabled={!!operation} onClick={onClose}>关闭</button>
        {['needs_review', 'pending_confirmation', 'no_changes'].includes(selected.status) && <button className="btn btn-ghost btn-sm" disabled={!!operation || busy['renamePlan:' + selected.id]} onClick={() => run('regenerate', () => actions.regenerateRenamePlan(selected))}><BusyIcon busy={operation === 'regenerate'} icon="i-refresh" />{operation === 'regenerate' ? '正在生成' : '重新生成'}</button>}
        {!['completed', 'cancelled', 'no_changes'].includes(selected.status) && <button className="btn btn-danger btn-sm" disabled={!!operation || busy['renamePlan:' + selected.id]} onClick={() => run('cancel', () => actions.cancelRenamePlan(selected.id))}><Icon id="i-close" className="icon icon-sm" />取消计划</button>}
        {selected.status === 'needs_review' && <button className="btn btn-ghost btn-sm" disabled={!!operation || busy['renamePlan:' + selected.id]} onClick={() => run('resolve', () => actions.resolveRenamePlanSafe(selected.id))}>风险项保持不动</button>}
        {selected.status === 'needs_review' && <button className="btn btn-primary btn-sm" disabled={!!operation || busy['renamePlan:' + selected.id]} onClick={() => run('review', () => actions.reviewRenamePlan(selected.id, {configuration, special_actions: specialActions, item_actions: itemActions, item_overrides: itemOverrides}))}><BusyIcon busy={operation === 'review'} icon="i-check" />{operation === 'review' ? '正在保存' : '保存复核'}</button>}
        {selected.status === 'pending_confirmation' && <button className="btn btn-primary btn-sm" disabled={!!operation || busy['renamePlan:' + selected.id]} onClick={() => run('confirm', async () => { await actions.reviewRenamePlan(selected.id, {configuration, special_actions: specialActions, item_actions: itemActions, item_overrides: itemOverrides}); return actions.confirmRenamePlan(selected.id); })}><BusyIcon busy={operation === 'confirm'} icon="i-bolt" />{operation === 'confirm' ? '正在执行' : '确认格式并执行'}</button>}
      </div>
    </div>
  );
}

export function AgentPage({app, mobile = false}) {
  const {agentStatus, agentSessions, agentSession, renamePlans, renameFolders, renameRules, busy, actions, setModal, closeModal} = app;
  const remote = agentStatus.config || {};
  const [draft, setDraft] = useState(remote);
  const [message, setMessage] = useState('');
  const messagesRef = useRef(null);

  useEffect(() => setDraft(remote), [remote]);
  useEffect(() => {
    messagesRef.current?.scrollTo({top: messagesRef.current.scrollHeight, behavior: 'smooth'});
  }, [agentSession?.messages?.length]);

  const providerId = draft.provider || 'deepseek';
  const providers = draft.providers || {};
  const provider = providers[providerId] || {};
  const developer = draft.developer_agent || {};
  const updateProvider = (field, value) => setDraft((prev) => ({
    ...prev,
    providers: {...(prev.providers || {}), [providerId]: {...((prev.providers || {})[providerId] || {}), [field]: value}},
  }));
  const updateDeveloper = (field, value) => setDraft((prev) => ({
    ...prev,
    developer_agent: {...(prev.developer_agent || {}), [field]: value},
  }));
  const submit = async (event) => {
    event.preventDefault();
    const value = message.trim();
    if (!value || busy.agentChat) return;
    setMessage('');
    await actions.sendAgentMessage(value).catch(() => setMessage(value));
  };
  const save = () => actions.saveAgentConfig(draft);
  const configured = Boolean(provider.configured || provider.api_key);

  return (
    <div className={`agent-workspace ${mobile ? 'agent-mobile' : ''}`}>
      <aside className="agent-rail">
        <button className="btn btn-primary agent-new" onClick={() => actions.loadAgentSession(null)}><Icon id="i-plus" className="icon icon-sm" />新会话</button>
        <div className="agent-session-list">
          {agentSessions.map((session) => (
            <div className={`agent-session-row ${agentSession?.id === session.id ? 'active' : ''}`} key={session.id}>
              <button onClick={() => actions.loadAgentSession(session.id)}>
                <strong>{session.title || '新会话'}</strong><span>{session.preview || '暂无回复'}</span>
              </button>
              <button className="agent-session-delete" title="删除会话" aria-label="删除会话" onClick={() => actions.deleteAgentSession(session.id)}><Icon id="i-trash" /></button>
            </div>
          ))}
          {!agentSessions.length && <div className="agent-session-empty">暂无会话</div>}
        </div>
      </aside>

      <section className="agent-chat-panel">
        <div className="agent-chat-head">
          <div className="agent-avatar"><AppLogo title="AudioFlow Agent" /></div>
          <div><strong>AudioFlow Agent</strong><span>{remote.enabled && configured ? `${provider.name || providerId} · ${provider.model || '未选择模型'}${agentSession?.last_latency_ms != null ? ` · ${(agentSession.last_latency_ms / 1000).toFixed(1)} 秒` : ''}` : '等待模型配置'}</span></div>
          <button className="btn btn-ghost btn-sm" onClick={() => setModal({className: 'modal-wide', content: <RenamePlansModal plans={renamePlans} renameFolders={renameFolders} agentStatus={agentStatus} actions={actions} busy={busy} onClose={closeModal} />})}><Icon id="i-edit" className="icon icon-sm" />整理计划{renamePlans.filter((plan) => ['needs_review', 'pending_confirmation'].includes(plan.status)).length ? ` (${renamePlans.filter((plan) => ['needs_review', 'pending_confirmation'].includes(plan.status)).length})` : ''}</button>
          <button className="btn btn-ghost btn-sm" title="管理重命名规则" onClick={() => setModal({className: 'modal-wide', content: <RenameRulesModal rulesState={renameRules} actions={actions} busy={busy} onClose={closeModal} />})}><Icon id="i-settings" className="icon icon-sm" />规则</button>
          <details className="agent-config">
            <summary className="btn btn-ghost btn-sm"><Icon id="i-settings" className="icon icon-sm" />模型设置</summary>
            <div className="agent-config-popover">
              <label className="field"><span>启用 Agent</span><input type="checkbox" checked={Boolean(draft.enabled)} onChange={(event) => setDraft((prev) => ({...prev, enabled: event.target.checked}))} /></label>
              <label className="field"><span>AI 平台</span><select value={providerId} onChange={(event) => setDraft((prev) => ({...prev, provider: event.target.value}))}>{Object.entries(providers).map(([id, item]) => <option value={id} key={id}>{item.name || id}</option>)}</select></label>
              <label className="field"><span>Agent 运行时</span><select value={draft.runner || 'native'} onChange={(event) => setDraft((prev) => ({...prev, runner: event.target.value}))}><option value="native">AudioFlow 原生</option><option value="deepseek-harness" disabled={!agentStatus.harness?.available}>deepseek-harness{agentStatus.harness?.available ? '' : '（未安装）'}</option></select></label>
              <label className="field"><span>快速响应</span><input type="checkbox" checked={draft.fast_mode !== false} onChange={(event) => setDraft((prev) => ({...prev, fast_mode: event.target.checked}))} /></label>
              <label className="field"><span>模型</span><input value={provider.model || ''} onChange={(event) => updateProvider('model', event.target.value)} placeholder="模型或 Endpoint ID" /></label>
              <label className="field"><span>API 地址</span><input value={provider.base_url || ''} onChange={(event) => updateProvider('base_url', event.target.value)} placeholder="https://.../v1" /></label>
              <label className="field"><span>API Key</span><input type="password" value={provider.api_key || ''} onChange={(event) => updateProvider('api_key', event.target.value)} placeholder={provider.api_key_masked || (provider.api_key_optional ? '可留空' : '输入新密钥')} autoComplete="new-password" /></label>
              <div className="agent-runtime-status"><span className={agentStatus.harness?.available ? 'ok' : ''}>deepseek-harness</span><em>{agentStatus.harness?.message || '检测中'}</em></div>
              <div className="agent-runtime-status"><span className="ok">模型密钥</span><em>{agentStatus.security?.message || '本地自动加密管理'}</em></div>
              <div className="agent-config-divider">飞书完整代码 Agent</div>
              <label className="field"><span>启用高权限模式</span><input type="checkbox" checked={Boolean(developer.enabled)} onChange={(event) => updateDeveloper('enabled', event.target.checked)} /></label>
              {developer.enabled && <>
                <label className="field"><span>专用飞书 App ID</span><input value={developer.feishu_app_id || ''} onChange={(event) => updateDeveloper('feishu_app_id', event.target.value)} placeholder="必须与通知机器人不同" /></label>
                <label className="field"><span>专用 App Secret</span><input type="password" value={developer.feishu_app_secret || ''} onChange={(event) => updateDeveloper('feishu_app_secret', event.target.value)} placeholder={developer.feishu_app_secret_masked || '输入新密钥'} autoComplete="new-password" /></label>
                <label className="field"><span>默认工作目录</span><input value={developer.default_cwd || '/workspace'} onChange={(event) => updateDeveloper('default_cwd', event.target.value)} placeholder="/workspace" /></label>
                <label className="field"><span>允许的项目根目录</span><textarea rows="2" value={Array.isArray(developer.repo_roots) ? developer.repo_roots.join('\n') : (developer.repo_roots || '/workspace')} onChange={(event) => updateDeveloper('repo_roots', event.target.value)} placeholder="每行一个绝对路径" /></label>
                <label className="field"><span>允许的用户 Open ID</span><textarea rows="2" value={developer.allowed_users || ''} onChange={(event) => updateDeveloper('allowed_users', event.target.value)} placeholder="每行一个，不能与群聊白名单同时留空" /></label>
                <label className="field"><span>允许的群聊 Chat ID</span><textarea rows="2" value={developer.allowed_chats || ''} onChange={(event) => updateDeveloper('allowed_chats', event.target.value)} placeholder="每行一个" /></label>
                <label className="field"><span>进入工作目录后运行</span><input type="checkbox" checked={developer.require_working_dir !== false} onChange={(event) => updateDeveloper('require_working_dir', event.target.checked)} /></label>
              </>}
              <div className="agent-runtime-status"><span className={agentStatus.developer?.running ? 'ok' : ''}>{agentStatus.developer?.running ? '运行中' : '已停止'}</span><em>{agentStatus.developer?.last_error || (agentStatus.developer?.available ? '原生 dsh-feishu 运行时已安装' : '需要重新构建 Docker 镜像')}</em></div>
              <div className="agent-config-actions">{agentStatus.developer?.running ? <button className="btn btn-danger btn-sm" disabled={busy.developerAgent} onClick={actions.stopDeveloperAgent}><Icon id="i-stop" className="icon icon-sm" />停止代码 Agent</button> : <button className="btn btn-ghost btn-sm" disabled={busy.agentConfig || !developer.enabled} onClick={save}><Icon id="i-bolt" className="icon icon-sm" />保存并启动</button>}</div>
              <div className="agent-config-actions"><button className="btn btn-ghost btn-sm" disabled={busy.agentTest || !remote.enabled} onClick={actions.testAgent}>{busy.agentTest ? <span className="loading" /> : <Icon id="i-bolt" className="icon icon-sm" />}测试</button><button className="btn btn-primary btn-sm" disabled={busy.agentConfig} onClick={save}><Icon id="i-check" className="icon icon-sm" />保存</button></div>
            </div>
          </details>
        </div>

        <div className="agent-messages" ref={messagesRef}>
          {!agentSession?.messages?.length && (
            <div className="agent-welcome">
              <div className="agent-welcome-mark"><AppLogo title="AudioFlow Agent" /></div>
              <strong>今天要整理哪本有声书？</strong>
              <div className="agent-prompts">
                {['查看最近完成的下载', '列出等待确认的整理计划', '为最近完成的手动下载生成整理计划'].map((text) => <button key={text} onClick={() => setMessage(text)}>{text}</button>)}
              </div>
            </div>
          )}
          {(agentSession?.messages || []).map((item, index) => (
            <article className={`agent-message ${item.role} ${item.pending ? 'pending' : ''}`} key={`${item.created_at}-${index}`}>
              {item.role === 'assistant' && <div className="agent-message-avatar"><AppLogo title="Agent" /></div>}
              <div className="agent-message-body">
                <p>{item.content}</p>
                {(item.tool_events || []).map((event, eventIndex) => <div className={`agent-tool-event ${event.status}`} key={eventIndex}><Icon id={event.status === 'success' ? 'i-check' : 'i-alert'} className="icon icon-sm" /><span>{AGENT_TOOL_LABELS[event.name] || event.name}</span><em>{event.status === 'success' ? '完成' : event.error}</em></div>)}
              </div>
            </article>
          ))}
        </div>

        <form className="agent-composer" onSubmit={submit}>
          <textarea rows="2" value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(event); } }} placeholder="给 Agent 发消息" disabled={!remote.enabled || busy.agentChat} />
          <button type="submit" className="btn btn-primary" disabled={!message.trim() || !remote.enabled || busy.agentChat} title="发送" aria-label="发送消息">{busy.agentChat ? <span className="loading" /> : <Icon id="i-send" />}</button>
        </form>
      </section>
    </div>
  );
}

export function SettingsPage({app}) {
  const {config, logs, events, actions, setModal, closeModal, busy, diagnostics} = app;
  const [downloadDir, setDownloadDir] = useState('');
  const [quality, setQuality] = useState('M4A 96K');
  const [downloadThreads, setDownloadThreads] = useState(4);
  const [organizeByPlatformEnabled, setOrganizeByPlatformEnabled] = useState(false);
  const [splitChaptersEnabled, setSplitChaptersEnabled] = useState(false);
  const [chaptersPerFolder, setChaptersPerFolder] = useState(200);
  const [filenamePrefixFormat, setFilenamePrefixFormat] = useState('0001-');
  const [manualOrganizeMode, setManualOrganizeMode] = useState('review');
  const [taskHistoryMaxKeep, setTaskHistoryMaxKeep] = useState(100);
  const [taskHistoryMaxAgeDays, setTaskHistoryMaxAgeDays] = useState(30);
  const [taskDetailRetentionDays, setTaskDetailRetentionDays] = useState(7);
  const [taskFailureChapterLimit, setTaskFailureChapterLimit] = useState(20);
  const [taskHistoryMaxMB, setTaskHistoryMaxMB] = useState(10);
  const [backgroundEventsMaxKeep, setBackgroundEventsMaxKeep] = useState(10);
  useEffect(() => {
    setDownloadDir(config.download_dir || '');
    setQuality(config.quality || 'M4A 96K');
    setDownloadThreads(config.download_threads || 4);
    setOrganizeByPlatformEnabled(!!config.organize_by_platform_enabled);
    setSplitChaptersEnabled(!!config.split_chapters_enabled);
    setChaptersPerFolder(config.chapters_per_folder || 200);
    setFilenamePrefixFormat(config.filename_prefix_format || '0001-');
    setManualOrganizeMode(config.manual_organize_mode || 'review');
    setTaskHistoryMaxKeep(config.task_history_max_keep || 100);
    setTaskHistoryMaxAgeDays(config.task_history_max_age_days || 30);
    setTaskDetailRetentionDays(config.task_detail_retention_days ?? 7);
    setTaskFailureChapterLimit(config.task_failure_chapter_limit || 20);
    setTaskHistoryMaxMB(Math.max(1, Math.round((config.task_history_max_bytes || 10 * 1024 * 1024) / 1024 / 1024)));
    setBackgroundEventsMaxKeep(config.background_events_max_keep || 10);
  }, [config]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') actions.loadLogs().catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [actions.loadLogs]);
  const openPassword = () => setModal({content: <PasswordModal onSubmit={actions.changePassword} onClose={closeModal} />});
  const confirmClear = () => setModal({content: <ConfirmModal icon="i-trash" title="清空服务端日志" message="会清空 logs 目录下的 .log 文件。服务端已启用日志轮转。" okText="清空日志" danger onClose={closeModal} onOk={() => { closeModal(); actions.clearLogs(); }} />});
  const doExportBackup = async () => {
    try {
      const data = await actions.exportBackup();
      const text = JSON.stringify(data, null, 2);
      const blob = new Blob([text], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audioflow-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      actions.showToast(`已导出全量备份：Cookie ${Object.keys(data.cookies || {}).length} 个 · 订阅 ${(data.subscriptions || []).length} 个`, 'ok');
    } catch (error) {
      actions.showToast('导出失败：' + error.message, 'err');
    }
  };
  return (
    <>
      <div className="glass glass-pad settings-card">
        <div className="settings-section">
          <div className="settings-section-head"><div><h4>下载设置</h4><span>常用的保存位置、音质与下载性能</span></div></div>
          <div className="settings-grid">
            <div className="field-row settings-span-2"><label className="field-label">下载目录</label><input className="field-input" value={downloadDir} onChange={(e) => setDownloadDir(e.target.value)} placeholder="/path/to/downloads" /></div>
            <div className="field-row"><label className="field-label">默认音质</label><select className="field-select" value={quality} onChange={(e) => setQuality(e.target.value)}><option value="M4A 64K">M4A 64K（番茄畅听）</option><option value="M4A 96K">M4A 96K（标准）</option><option value="M4A 128K">M4A 128K（高品质）</option><option value="无损真人录制">无损 / 真人录制（平台最高）</option></select></div>
            <div className="field-row"><label className="field-label">并发线程数</label><input className="field-input" type="number" min="1" max="64" value={downloadThreads} onChange={(e) => setDownloadThreads(Math.max(1, Math.min(64, parseInt(e.target.value) || 1)))} placeholder="1-64" /></div>
            <div className="field-row settings-span-2">
              <label className="field-label">自动整理（仅手动下载）</label>
              <select className="field-select" value={manualOrganizeMode} onChange={(e) => setManualOrganizeMode(e.target.value)}>
                <option value="off">关闭</option>
                <option value="review">完成后生成计划并等待确认（推荐）</option>
                <option value="auto_safe">已确认的同一专辑无风险时自动执行</option>
              </select>
              <div className="settings-help">自动任务不会进入整理流程，新专辑仍会先确认书名、格式和特殊文件。</div>
            </div>
          </div>
        </div>

        <details className="settings-advanced">
          <summary><span><Icon id="i-settings" className="icon icon-sm" />高级设置</span><small>目录规则、文件名与记录维护</small><Icon id="i-arrow-right" className="icon settings-advanced-arrow" /></summary>
          <div className="settings-section">
            <div className="settings-section-head"><div><h4>目录与文件名</h4><span>控制下载后的文件夹结构</span></div></div>
            <div className="settings-grid">
              <div className="settings-toggle-row"><label className="check-row"><input type="checkbox" checked={organizeByPlatformEnabled} onChange={(e) => setOrganizeByPlatformEnabled(e.target.checked)} /><span>按平台创建文件夹</span></label><small>下载目录 / 平台 / 专辑</small></div>
              <div className="settings-toggle-row"><label className="check-row"><input type="checkbox" checked={splitChaptersEnabled} onChange={(e) => setSplitChaptersEnabled(e.target.checked)} /><span>按数量拆分文件夹</span></label><small>适合章节很多的专辑</small></div>
              <div className="field-row"><label className="field-label">每个文件夹</label><div className="field-row-inline"><input className="field-input" type="number" min="1" max="10000" value={chaptersPerFolder} disabled={!splitChaptersEnabled} onChange={(e) => setChaptersPerFolder(Math.max(1, Math.min(10000, parseInt(e.target.value) || 200)))} /><span className="field-suffix">个文件</span></div></div>
              <div className="field-row"><label className="field-label">文件名前缀</label><select className="field-select" value={filenamePrefixFormat} onChange={(e) => setFilenamePrefixFormat(e.target.value)}><option value="0001-">0001-章节名</option><option value="001-">001-章节名</option><option value="01-">01-章节名</option><option value="1-">1-章节名</option><option value="0001.">0001.章节名</option><option value="001.">001.章节名</option><option value="01.">01.章节名</option><option value="1.">1.章节名</option><option value="none">不添加序号前缀</option></select></div>
            </div>
          </div>
          <div className="settings-section settings-maintenance">
            <div className="settings-section-head"><div><h4>记录维护</h4><span>限制历史记录占用的空间</span></div></div>
            <div className="settings-grid">
              <div className="field-row"><label className="field-label">下载记录保留</label><div className="field-row-inline"><input className="field-input" type="number" min="10" max="10000" value={taskHistoryMaxKeep} onChange={(e) => setTaskHistoryMaxKeep(Math.max(10, Math.min(10000, parseInt(e.target.value) || 10)))} /><span className="field-suffix">条</span><input className="field-input" type="number" min="1" max="3650" value={taskHistoryMaxAgeDays} onChange={(e) => setTaskHistoryMaxAgeDays(Math.max(1, Math.min(3650, parseInt(e.target.value) || 1)))} /><span className="field-suffix">天</span></div></div>
              <div className="field-row"><label className="field-label">详情压缩</label><div className="field-row-inline"><input className="field-input" type="number" min="0" max="3650" value={taskDetailRetentionDays} onChange={(e) => setTaskDetailRetentionDays(Math.max(0, Math.min(3650, parseInt(e.target.value) || 0)))} /><span className="field-suffix">天后</span><input className="field-input" type="number" min="1" max="1000" value={taskFailureChapterLimit} onChange={(e) => setTaskFailureChapterLimit(Math.max(1, Math.min(1000, parseInt(e.target.value) || 1)))} /><span className="field-suffix">条失败</span></div></div>
              <div className="field-row"><label className="field-label">记录文件上限</label><div className="field-row-inline"><input className="field-input" type="number" min="1" max="1024" value={taskHistoryMaxMB} onChange={(e) => setTaskHistoryMaxMB(Math.max(1, Math.min(1024, parseInt(e.target.value) || 1)))} /><span className="field-suffix">MB</span></div></div>
              <div className="field-row"><label className="field-label">后台任务记录</label><div className="field-row-inline"><input className="field-input" type="number" min="10" max="5000" value={backgroundEventsMaxKeep} onChange={(e) => setBackgroundEventsMaxKeep(Math.max(10, Math.min(5000, parseInt(e.target.value) || 10)))} /><span className="field-suffix">条</span></div></div>
            </div>
          </div>
        </details>

        <div className="settings-footer">
          <div><strong>账号与安全</strong><div className="settings-account-actions"><button className="btn btn-ghost btn-sm" onClick={openPassword}><Icon id="i-key" className="icon icon-sm" />修改密码</button><button className="btn btn-danger btn-sm" onClick={actions.logoutAccount}><Icon id="i-close" className="icon icon-sm" />退出登录</button></div></div>
          <button className="btn btn-primary settings-save" disabled={busy.settings} onClick={() => actions.saveSettings({downloadDir, quality, downloadThreads, organizeByPlatformEnabled, splitChaptersEnabled, chaptersPerFolder, filenamePrefixFormat, manualOrganizeMode, taskHistoryMaxKeep, taskHistoryMaxAgeDays, taskDetailRetentionDays, taskFailureChapterLimit, taskHistoryMaxMB, backgroundEventsMaxKeep})}><BusyIcon busy={busy.settings} icon="i-check" />保存设置</button>
        </div>
      </div>
      <div className="glass glass-pad settings-card">
        <div className="panel-head"><h4>备份与恢复</h4></div>
        <div className="cookie-desc">一个文件打包全部 Cookie + 订阅 + 订阅设置，换机/重装时一键恢复。文件含明文登录凭证，请妥善保管。</div>
        <div className="cookie-toolbar" style={{marginTop: 10}}>
          <button className="btn btn-ghost btn-sm" onClick={doExportBackup}><Icon id="i-download" className="icon icon-sm" />导出全量备份</button>
          <button className="btn btn-primary btn-sm" disabled={busy.importBackup} onClick={() => setModal({content: <BackupImportModal actions={actions} onClose={closeModal} />})}><Icon id="i-folder" className="icon icon-sm" />导入备份</button>
        </div>
      </div>
      <DiagnosticsPanel config={config} diagnostics={diagnostics} loading={busy.diagnostics} onLoad={actions.loadDiagnostics} />
      <div className="glass glass-pad settings-log-card">
        <div className="panel-head"><h4>后台任务记录 <span className="panel-count">{events.length}/{backgroundEventsMaxKeep}</span></h4><div className="panel-actions"><button className="btn btn-ghost btn-tiny" onClick={() => actions.loadEvents()}><Icon id="i-refresh" className="icon icon-sm" />刷新</button><button className="btn btn-danger btn-tiny" onClick={actions.clearEvents}><Icon id="i-trash" className="icon icon-sm" />清空</button></div></div>
        <div className="event-list">{events.length ? events.map((event) => <div className="event-row" key={event.id}><strong>{event.title || event.kind}</strong><span>{event.detail || ''}</span></div>) : <div className="empty small"><Icon id="i-list" />暂无后台记录</div>}</div>
      </div>
      <div className="glass glass-pad settings-log-card">
        <div className="panel-head"><h4>最近日志 <span className="panel-count">{logs.length} 行</span></h4><div className="panel-actions"><button className="btn btn-ghost btn-tiny" onClick={() => actions.loadLogs()}><Icon id="i-refresh" className="icon icon-sm" />刷新</button><button className="btn btn-danger btn-tiny" onClick={confirmClear}><Icon id="i-trash" className="icon icon-sm" />清空</button></div></div>
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
