import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {api, login, logout, setAuthRequiredHandler} from '../services/api.js';
import {chapterId, chapterTitle, coverOf} from '../utils/format.js';
import {AGGREGATE_SEARCH_PLATFORMS, NO_COOKIE_KEYS, PLATFORM_COOKIE_KEY} from '../utils/platforms.js';

// ── 搜索历史
const SEARCH_HISTORY_KEY = 'audioflow_search_history';
const MAX_SEARCH_HISTORY = 12;
const DOWNLOADS_CACHE_KEY = 'audioflow_downloads_cache';
const SUBSCRIPTIONS_CACHE_KEY = 'audioflow_subscriptions_cache';
const VOICE_PLATFORMS = new Set(['番茄畅听', '番茄听书', '七猫听书']);
const DEFAULT_CHAPTER_PAGINATION = {page: 1, page_size: 100, total: 0, total_pages: 1, total_known: false, has_more: false};
function loadSearchHistory() {
  try { return JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY) || '[]'); } catch { return []; }
}
function saveSearchHistory(list) {
  try { localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(list)); } catch {}
}
function pushSearchHistory(keyword) {
  const list = loadSearchHistory().filter((item) => item !== keyword);
  list.unshift(keyword);
  saveSearchHistory(list.slice(0, MAX_SEARCH_HISTORY));
}
function loadCachedList(key) {
  try {
    const data = JSON.parse(localStorage.getItem(key) || '{}');
    return Array.isArray(data.items) ? data.items : [];
  } catch {
    return [];
  }
}
function saveCachedList(key, items) {
  try {
    localStorage.setItem(key, JSON.stringify({items: Array.isArray(items) ? items : [], updated_at: Date.now()}));
  } catch {}
}

// ── 音色记忆
const VOICE_MEMORY_KEY = 'audioflow_voice_memory';
function loadVoiceMemory() {
  try { return JSON.parse(localStorage.getItem(VOICE_MEMORY_KEY) || '{}'); } catch { return {}; }
}
function rememberVoice(albumId, voiceName) {
  if (!albumId) return;
  const map = loadVoiceMemory();
  map[albumId] = voiceName;
  try { localStorage.setItem(VOICE_MEMORY_KEY, JSON.stringify(map)); } catch {}
}
function recallVoice(albumId) {
  if (!albumId) return null;
  return loadVoiceMemory()[albumId] || null;
}

// ── 浏览器通知
function requestNotificationPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
}
function sendBrowserNotification(title, body) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  try { new Notification(title, {body, icon: '/favicon.ico'}); } catch {}
}

const DEFAULT_QUALITY = 'M4A 96K';
const XMLY_MOBILE_INTERFACE = '喜马拉雅移动端接口（自动最高音质）';
const XMLY_WEB_INTERFACE = '喜马拉雅网页版接口';
const XMLY_SUBSCRIPTION_QUALITIES = new Set([
  XMLY_WEB_INTERFACE,
  XMLY_MOBILE_INTERFACE,
  '杜比全景声优先（自动降级）',
  '无损优先（自动降级）',
]);
const XMLY_DOWNLOAD_QUALITIES = new Set([
  XMLY_MOBILE_INTERFACE,
  'M4A 128K',
  'M4A 64K',
  'M4A 24K',
  '无损真人录制',
  '杜比全景声',
  'Audio Vivid 菁彩声',
  '杜比全景声优先（自动降级）',
  'Audio Vivid 优先（自动降级）',
  '无损优先（自动降级）',
]);

function ximalayaDownloadQuality(value) {
  return XMLY_DOWNLOAD_QUALITIES.has(value) ? value : XMLY_MOBILE_INTERFACE;
}

function ximalayaSubscriptionQuality(value) {
  return XMLY_SUBSCRIPTION_QUALITIES.has(value) ? value : XMLY_WEB_INTERFACE;
}

function initialMobileView() {
  try {
    const view = new URLSearchParams(window.location.search).get('view');
    const map = {
      search: 'discover',
      discover: 'discover',
      downloads: 'downloads',
      subscriptions: 'subscriptions',
      cookies: 'cookies',
      accounts: 'cookies',
      personal: 'personal',
      notifications: 'notifications',
      agent: 'agent',
      themes: 'themes',
      settings: 'settings',
      more: 'more',
    };
    return map[view] || 'discover';
  } catch {
    return 'discover';
  }
}

function parseChapterRange(input, list) {
  const total = list.length;
  const text = String(input || '').trim();
  if (!text) return [];
  const picked = new Set();
  for (const part of text.split(/[，,\s]+/).map((item) => item.trim()).filter(Boolean)) {
    const match = part.match(/^(\d+)(?:\s*[-~至]\s*(\d+))?$/);
    if (!match) return null;
    let start = Number.parseInt(match[1], 10);
    let end = match[2] ? Number.parseInt(match[2], 10) : start;
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    if (start > end) [start, end] = [end, start];
    start = Math.max(1, start);
    end = Math.min(total, end);
    for (let index = start; index <= end; index += 1) picked.add(index - 1);
  }
  return [...picked].sort((a, b) => a - b).map((index) => list[index]).filter(Boolean);
}

function slimChapterForDownload(chapter, index) {
  const data = chapter || {};
  const slim = {
    id: chapterId(data, String(index + 1)),
    title: chapterTitle(data),
  };
  for (const key of [
    'track_id', 'trackId', 'chapter_id', 'chapterId', 'acid', 'cid', 'audioId', 'audio_id',
    'item_id', 'program_id', 'programId', 'song_id', 'duration', 'duration_sec', 'duration_str', 'order_num',
    'ui_display_index', 'album', 'book_id', 'album_id', 'qimao_book_id', 'is_paid', '_chapter_data',
    'lrts_data', 'netease_program_id', 'netease_song_id', 'audio_url', 'mediaUrl', 'playUrlHigh', 'playUrlLow', 'downloadUrl', 'url',
  ]) {
    if (data[key] !== undefined && data[key] !== null && data[key] !== '') {
      slim[key] = data[key];
    }
  }
  return slim;
}

export function useAudioFlowApp() {
  const [page, setPage] = useState('search');
  const [mobileView, setMobileView] = useState(() => initialMobileView());
  const [platform, setPlatform] = useState('all');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [selectedAlbum, setSelectedAlbum] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [chapterPagination, setChapterPagination] = useState(DEFAULT_CHAPTER_PAGINATION);
  const [chapterSort, setChapterSort] = useState('asc');
  const [downloadRange, setDownloadRange] = useState('');
  const [selectedChapters, setSelectedChapters] = useState(new Set());
  const [voices, setVoices] = useState([]);
  const [selectedVoice, setSelectedVoice] = useState(null);
  const [downloadQuality, setDownloadQuality] = useState(XMLY_MOBILE_INTERFACE);
  const [ximalayaInterface, setXimalayaInterface] = useState(XMLY_WEB_INTERFACE);
  const [subscriptionQuality, setSubscriptionQuality] = useState(XMLY_WEB_INTERFACE);
  const [downloads, setDownloads] = useState(() => loadCachedList(DOWNLOADS_CACHE_KEY));
  const [downloadPagination, setDownloadPagination] = useState({page: 1, limit: 20, total: 0, total_pages: 1});
  const [downloadSummary, setDownloadSummary] = useState({total: 0, active_count: 0, completed_count: 0, failed_count: 0, interrupted_count: 0});
  const [downloadStatusFilter, setDownloadStatusFilter] = useState('all');
  const [subscriptions, setSubscriptions] = useState(() => loadCachedList(SUBSCRIPTIONS_CACHE_KEY));
  const [subscriptionSettings, setSubscriptionSettings] = useState({});
  const [subscriptionScheduler, setSubscriptionScheduler] = useState({});
  const [subscriptionJobs, setSubscriptionJobs] = useState({});
  const [notificationConfig, setNotificationConfig] = useState({enabled: false, scenes: {}, services: [], available_channels: []});
  const [agentStatus, setAgentStatus] = useState({config: {enabled: false, provider: 'deepseek', runner: 'native', providers: {}, developer_agent: {}}, harness: {}, developer: {}, tools: []});
  const [agentSessions, setAgentSessions] = useState([]);
  const [agentSession, setAgentSession] = useState(null);
  const [renamePlans, setRenamePlans] = useState([]);
  const [cookies, setCookies] = useState({});
  const [config, setConfig] = useState({});
  const [logs, setLogs] = useState([]);
  const [events, setEvents] = useState([]);
  const [serviceState, setServiceState] = useState('checking');
  const [busy, setBusy] = useState({});
  const [diagnostics, setDiagnostics] = useState(null);
  const [toast, setToastState] = useState(null);
  const [modal, setModal] = useState(null);
  const [loginVisible, setLoginVisible] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [player, setPlayer] = useState({show: false, title: '未在播放', sub: '', album: '', artist: '', cover: '', url: '', chapterId: '', playing: false});
  const [searchHistory, setSearchHistory] = useState(() => loadSearchHistory());
  const loginResolveRef = useRef(null);
  const audioRef = useRef(null);
  const prevDownloadStatusRef = useRef({});
  const foregroundRefreshRef = useRef(0);
  const searchRequestRef = useRef(0);
  const albumRequestRef = useRef(0);
  const hasCachedDataRef = useRef(downloads.length > 0 || subscriptions.length > 0);
  const initializedRef = useRef(false);

  const status = useMemo(() => ({
    checking: '检测中',
    online: '在线',
    offline: '离线',
    cached: '缓存数据',
  }[serviceState] || '离线'), [serviceState]);

  const showToast = useCallback((message, kind = 'ok') => {
    setToastState({message, kind, id: Date.now()});
  }, []);

  const closeModal = useCallback(() => setModal(null), []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return undefined;
    const markPlaying = () => setPlayer((prev) => ({...prev, playing: true}));
    const markPaused = () => setPlayer((prev) => ({...prev, playing: false}));
    audio.addEventListener('play', markPlaying);
    audio.addEventListener('playing', markPlaying);
    audio.addEventListener('pause', markPaused);
    audio.addEventListener('ended', markPaused);
    audio.addEventListener('error', markPaused);
    return () => {
      audio.removeEventListener('play', markPlaying);
      audio.removeEventListener('playing', markPlaying);
      audio.removeEventListener('pause', markPaused);
      audio.removeEventListener('ended', markPaused);
      audio.removeEventListener('error', markPaused);
    };
  }, [player.show]);

  const runBusy = useCallback(async (key, fn) => {
    setBusy((prev) => ({...prev, [key]: true}));
    try {
      return await fn();
    } finally {
      setBusy((prev) => ({...prev, [key]: false}));
    }
  }, []);

  const requireLogin = useCallback(() => new Promise((resolve) => {
    loginResolveRef.current = resolve;
    setLoginError('');
    setLoginVisible(true);
  }), []);

  const submitLogin = useCallback(async ({username, password}) => {
    setLoginLoading(true);
    setLoginError('');
    try {
      await login(username, password);
      setLoginVisible(false);
      const resolve = loginResolveRef.current;
      loginResolveRef.current = null;
      if (resolve) resolve(true);
    } catch (error) {
      setLoginError(error.message || '登录失败');
    } finally {
      setLoginLoading(false);
    }
  }, []);

  // 下载统计改由后端 summary 提供（分页后前端只有当前页，不能再靠全量 filter 计算）

  const loadConfig = useCallback(async () => {
    const data = await api('/api/config');
    setConfig(data);
    return data;
  }, []);

  const loadDownloads = useCallback(async (page = 1, status) => {
    const useStatus = status ?? downloadStatusFilter;
    const data = await api(`/api/downloads?page=${page}&limit=20&status=${encodeURIComponent(useStatus)}`);
    const tasks = data.tasks || [];
    setDownloads(tasks);
    if (data.pagination) setDownloadPagination(data.pagination);
    if (data.summary) setDownloadSummary(data.summary);
    if (status !== undefined && status !== downloadStatusFilter) setDownloadStatusFilter(useStatus);
    saveCachedList(DOWNLOADS_CACHE_KEY, tasks);
    if (tasks.length) hasCachedDataRef.current = true;
    prevDownloadStatusRef.current = Object.fromEntries(tasks.map((t) => [t.id, t.status]));
    return tasks;
  }, [downloadStatusFilter]);

  const loadCookies = useCallback(async () => {
    const data = await api('/api/cookies');
    setCookies(data.cookies || {});
  }, []);

  const exportCookies = useCallback(async () => {
    const data = await api('/api/cookies/export');
    return data.cookies || {};
  }, []);

  const importCookies = useCallback(async (cookies) => runBusy('importCookies', async () => {
    const data = await api('/api/cookies/import', {method: 'POST', body: {cookies}});
    showToast(`已导入 ${data.count || 0} 个平台 Cookie` + ((data.skipped && data.skipped.length) ? `，跳过 ${data.skipped.length} 个` : ''), 'ok');
    await loadCookies();
    return data;
  }), [runBusy, showToast, loadCookies]);

  const loadLogs = useCallback(async (limit = 160) => {
    const data = await api('/api/logs?limit=' + limit);
    setLogs(data.lines || []);
  }, []);

  const loadEvents = useCallback(async (limit) => {
    const data = await api('/api/events' + (limit ? `?limit=${limit}` : ''));
    setEvents(data.events || []);
    return data.events || [];
  }, []);

  const loadSubscriptionSettings = useCallback(async () => {
    const data = await api('/api/subscriptions/settings');
    setSubscriptionSettings(data.settings || {});
    return data.settings || {};
  }, []);

  const loadSubscriptions = useCallback(async (options = {}) => {
    const path = options.refreshLocal ? '/api/subscriptions?refresh_local=1' : '/api/subscriptions?fast=1';
    const data = await api(path);
    const activeItems = (data.subscriptions || []).filter((item) => (item.status || 'active') === 'active');
    setSubscriptions(activeItems);
    saveCachedList(SUBSCRIPTIONS_CACHE_KEY, activeItems);
    if (activeItems.length) hasCachedDataRef.current = true;
    setSubscriptionSettings(data.settings || {});
    setSubscriptionScheduler(data.scheduler || {});
    return activeItems;
  }, []);

  const exportSubscriptions = useCallback(async () => {
    const data = await api('/api/subscriptions/export');
    return {subscriptions: data.subscriptions || [], settings: data.settings || {}};
  }, []);

  const importSubscriptions = useCallback(async (payload) => runBusy('importSubscriptions', async () => {
    const data = await api('/api/subscriptions/import', {method: 'POST', body: payload});
    showToast(`已导入 ${data.imported || 0} 个订阅`, 'ok');
    await loadSubscriptions();
    return data;
  }), [runBusy, showToast, loadSubscriptions]);

  const exportBackup = useCallback(async () => {
    const data = await api('/api/backup/export');
    return data.backup || {};
  }, []);

  const importBackup = useCallback(async (backup) => runBusy('importBackup', async () => {
    const data = await api('/api/backup/import', {method: 'POST', body: {backup}});
    showToast(`恢复完成：Cookie ${data.cookies || 0} 个 · 订阅 ${data.subscriptions || 0} 个`, 'ok');
    await Promise.all([loadCookies(), loadSubscriptions(), loadConfig()]);
    return data;
  }), [runBusy, showToast, loadCookies, loadSubscriptions, loadConfig]);

  const loadSubscriptionScheduler = useCallback(async () => {
    const data = await api('/api/subscriptions/scheduler');
    setSubscriptionScheduler(data.scheduler || {});
    return data.scheduler || {};
  }, []);

  const ensurePlatformLogin = useCallback(() => {
    if (!platform || platform === 'all') return true;
    const key = PLATFORM_COOKIE_KEY[platform];
    const info = cookies[key] || {};
    if (key && !NO_COOKIE_KEYS.includes(key) && !(info.has_cookie || info.has_server)) {
      showToast(platform + ' 需要先在账号管理中登录或设置 Cookie', 'err');
      setPage('cookies');
      setMobileView('cookies');
      return false;
    }
    return true;
  }, [cookies, platform, showToast]);

  const clearSearchHistory = useCallback(() => {
    saveSearchHistory([]);
    setSearchHistory([]);
  }, []);

  const doSearch = useCallback(async () => {
    const keyword = query.trim();
    if (!keyword) {
      showToast('请输入关键词', 'err');
      return;
    }
    if (!ensurePlatformLogin()) return;
    pushSearchHistory(keyword);
    setSearchHistory(loadSearchHistory());
    const requestId = ++searchRequestRef.current;
    await runBusy('search', async () => {
      if (platform !== 'all') {
        const data = await api('/api/search?q=' + encodeURIComponent(keyword) + '&platform=' + encodeURIComponent(platform));
        if (requestId === searchRequestRef.current) setResults(data.results || []);
        return;
      }

      const grouped = new Map();
      let completed = 0;
      let succeeded = 0;
      await Promise.all(AGGREGATE_SEARCH_PLATFORMS.map(async ({value}) => {
        try {
          const data = await api('/api/search?q=' + encodeURIComponent(keyword) + '&platform=' + encodeURIComponent(value));
          grouped.set(value, data.results || []);
          succeeded += 1;
        } catch {
          grouped.set(value, []);
        } finally {
          completed += 1;
          if (requestId === searchRequestRef.current && (succeeded > 0 || completed === AGGREGATE_SEARCH_PLATFORMS.length)) {
            setResults(AGGREGATE_SEARCH_PLATFORMS.flatMap(({value: item}) => grouped.get(item) || []));
          }
        }
      }));
      if (!succeeded) throw new Error('全部平台均未返回结果');
    }).catch((error) => {
      if (requestId === searchRequestRef.current) showToast('搜索失败：' + error.message, 'err');
    });
  }, [ensurePlatformLogin, platform, query, runBusy, showToast]);

  const openAlbum = useCallback(async (album) => {
    if (!album) {
      albumRequestRef.current += 1;
      setSelectedAlbum(null);
      setChapters([]);
      setChapterPagination(DEFAULT_CHAPTER_PAGINATION);
      setChapterSort('asc');
      setDownloadRange('');
      setSelectedChapters(new Set());
      return;
    }
    const requestId = ++albumRequestRef.current;
    setSelectedAlbum(album);
    setChapters([]);
    setChapterPagination(DEFAULT_CHAPTER_PAGINATION);
    setChapterSort('asc');
    setDownloadRange('');
    setSelectedChapters(new Set());
    setVoices([]);
    setSelectedVoice(null);
    setSubscriptionQuality(ximalayaSubscriptionQuality(
      album.library?.subscription_quality || album.subscription_quality,
    ));
    setDownloadQuality(
      album.platform === '喜马拉雅'
        ? ximalayaDownloadQuality(config.quality)
        : (config.quality || DEFAULT_QUALITY),
    );
    api('/api/album/detail', {method: 'POST', body: {album}}).then((detailData) => {
      if (requestId !== albumRequestRef.current || !detailData.album) return;
      setSelectedAlbum((current) => current ? {...current, ...detailData.album} : detailData.album);
      if (album.platform === '喜马拉雅') {
        setSubscriptionQuality(ximalayaSubscriptionQuality(
          detailData.album.library?.subscription_quality || detailData.album.subscription_quality,
        ));
      }
    }).catch(() => {});

    await runBusy('album', async () => {
      const voiceData = VOICE_PLATFORMS.has(album.platform)
        ? await api('/api/album/voices', {method: 'POST', body: {album}}).catch(() => ({voices: []}))
        : {voices: []};
      if (requestId !== albumRequestRef.current) return;
      const nextVoices = voiceData.voices || [];
      const albumKey = album.id || album.title;
      const remembered = recallVoice(albumKey);
      const voice = (remembered && nextVoices.find((v) => (v.name || v.title) === remembered)) || nextVoices[0] || null;
      setVoices(nextVoices);
      setSelectedVoice(voice);
      const data = await api('/api/album/chapters', {method: 'POST', body: {album, voice, page: 1, page_size: 100}});
      if (requestId !== albumRequestRef.current) return;
      setSelectedAlbum((current) => ({...(current || album), ...(data.album || {})}));
      if (album.platform === '喜马拉雅' && data.album) {
        setSubscriptionQuality(ximalayaSubscriptionQuality(
          data.album.library?.subscription_quality || data.album.subscription_quality,
        ));
      }
      setChapters(data.chapters || []);
      setChapterPagination(data.pagination || DEFAULT_CHAPTER_PAGINATION);
      if (data.warning) showToast(data.warning, 'err');
    }).catch((error) => {
      showToast('加载详情失败：' + error.message, 'err');
    });
  }, [config.quality, runBusy, showToast]);

  const changeVoice = useCallback(async (voice) => {
    if (!selectedAlbum) return;
    const requestId = ++albumRequestRef.current;
    setSelectedVoice(voice);
    setChapters([]);
    setChapterPagination(DEFAULT_CHAPTER_PAGINATION);
    setChapterSort('asc');
    setDownloadRange('');
    setSelectedChapters(new Set());
    const albumKey = selectedAlbum.id || selectedAlbum.title;
    rememberVoice(albumKey, (voice && (voice.name || voice.title)) || '');
    await runBusy('voice', async () => {
      const data = await api('/api/album/chapters', {method: 'POST', body: {album: selectedAlbum, voice, page: 1, page_size: 100}});
      if (requestId !== albumRequestRef.current) return;
      setChapters(data.chapters || []);
      setChapterPagination(data.pagination || DEFAULT_CHAPTER_PAGINATION);
      if (data.album) setSelectedAlbum((current) => ({...(current || selectedAlbum), ...data.album}));
      if (data.warning) showToast(data.warning, 'err');
    }).catch((error) => {
      showToast('切换音色失败：' + error.message, 'err');
    });
  }, [runBusy, selectedAlbum, showToast]);

  const loadChapterPage = useCallback(async (nextPage) => {
    if (!selectedAlbum) return;
    const targetPage = Math.max(1, Number(nextPage || 1));
    if (chapterPagination.total_known !== false && chapterPagination.total_pages && targetPage > chapterPagination.total_pages) return;
    const requestId = ++albumRequestRef.current;
    await runBusy('album', async () => {
      const data = await api('/api/album/chapters', {
        method: 'POST',
        body: {
          album: selectedAlbum,
          voice: selectedVoice || undefined,
          page: targetPage,
          page_size: chapterPagination.page_size || 100,
        },
      });
      if (requestId !== albumRequestRef.current) return;
      setChapters(data.chapters || []);
      setSelectedChapters(new Set());
      setChapterPagination(data.pagination || {...DEFAULT_CHAPTER_PAGINATION, page: targetPage});
      if (data.album) setSelectedAlbum((current) => ({...(current || selectedAlbum), ...data.album}));
      if (data.warning) showToast(data.warning, 'err');
    }).catch((error) => {
      if (requestId === albumRequestRef.current) showToast('加载章节失败：' + error.message, 'err');
    });
  }, [chapterPagination.page_size, chapterPagination.total_known, chapterPagination.total_pages, runBusy, selectedAlbum, selectedVoice, showToast]);

  const toggleChapter = useCallback((id) => {
    setSelectedChapters((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const displayChapters = useMemo(
    () => (chapterSort === 'desc' ? [...chapters].reverse() : chapters),
    [chapterSort, chapters],
  );

  const selectAllChapters = useCallback((checked) => {
    setSelectedChapters(checked ? new Set(displayChapters.map((chapter, index) => chapterId(chapter, String(index + 1)))) : new Set());
  }, [displayChapters]);

  const invertChapterSelection = useCallback(() => {
    setSelectedChapters((prev) => {
      const next = new Set();
      displayChapters.forEach((chapter, index) => {
        const id = chapterId(chapter, String(index + 1));
        if (!prev.has(id)) next.add(id);
      });
      return next;
    });
  }, [displayChapters]);

  const selectedChapterList = useMemo(
    () => displayChapters.filter((chapter, index) => selectedChapters.has(chapterId(chapter, String(index + 1)))),
    [displayChapters, selectedChapters],
  );

  const startDownload = useCallback(async (items = selectedChapterList, options = {}) => {
    const downloadAll = Boolean(options.all);
    if (!selectedAlbum || (!downloadAll && !items.length)) {
      showToast('请先选择章节', 'err');
      return;
    }
    const slimChapters = downloadAll ? [] : items.map((chapter, index) => slimChapterForDownload(chapter, index));
    await runBusy('download', async () => {
      const data = await api('/api/downloads', {
        method: 'POST',
        body: {
          album: selectedAlbum,
          chapters: slimChapters,
          all_chapters: downloadAll,
          options: {
            quality: selectedAlbum.platform === '喜马拉雅'
              ? (ximalayaInterface === XMLY_WEB_INTERFACE
                ? XMLY_WEB_INTERFACE
                : ximalayaDownloadQuality(downloadQuality))
              : (config.quality || DEFAULT_QUALITY),
            voice: selectedVoice || undefined,
            warning: selectedAlbum.catalog_warning || undefined,
          },
        },
      });

      if (data.task) {
        setDownloads((current) => [
          data.task,
          ...current.filter((task) => task.id !== data.task.id),
        ].slice(0, 20));
      }
      setDownloadStatusFilter('all');
      showToast(
        data.message || (
          downloadAll
            ? '下载任务已创建，正在后台加载完整目录'
            : (data.deduplicated ? '所选章节已在下载任务中' : '已加入下载 ' + items.length + ' 章')
        ),
        'ok',
      );
      setPage('downloads');
      setMobileView('downloads');
      loadDownloads(1, 'all').catch(() => null);
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [config.quality, downloadQuality, loadDownloads, runBusy, selectedAlbum, selectedChapterList, selectedVoice, showToast, ximalayaInterface]);

  const applyDownloadRange = useCallback((mode = 'select') => {
    const items = parseChapterRange(downloadRange, displayChapters);
    if (items === null) {
      showToast('范围格式错误，请输入 1-10 或 1,3,8-12', 'err');
      return [];
    }
    if (!items.length) {
      showToast('请输入有效下载范围', 'err');
      return [];
    }
    if (mode === 'download') {
      startDownload(items);
      return items;
    }
    const selectedIds = new Set();
    displayChapters.forEach((chapter, index) => {
      if (items.includes(chapter)) selectedIds.add(chapterId(chapter, String(index + 1)));
    });
    setSelectedChapters(selectedIds);
    showToast('已选中范围内 ' + items.length + ' 章', 'ok');
    return items;
  }, [displayChapters, downloadRange, showToast, startDownload]);

  const subscribeAlbum = useCallback(async () => {
    if (!selectedAlbum) return;
    if (!chapters || !chapters.length) {
      showToast(chapters ? "章节列表未加载完成，请稍后再试" : "章节列表未加载，请先打开专辑详情", "err");
      return;
    }
    await runBusy('subscribe', async () => {
      const data = await api('/api/subscriptions', {
        method: 'POST',
        body: {
          album: selectedAlbum,
          chapters,
          voice: selectedVoice || undefined,
          load_all: true,
          ...(selectedAlbum.platform === '喜马拉雅' ? {subscription_quality: subscriptionQuality} : {}),
        },
      });
      if (data.library) {
        setSelectedAlbum((current) => current ? {...current, library: data.library} : current);
        setResults((current) => current.map((album) => (
          album.library?.subscription_id === data.library.subscription_id
            ? {...album, library: data.library}
            : album
        )));
      }
      const savedQuality = data.subscription?.subscription_quality || data.library?.subscription_quality || subscriptionQuality;
      if (selectedAlbum.platform === '喜马拉雅') setSubscriptionQuality(ximalayaSubscriptionQuality(savedQuality));
      showToast(
        selectedAlbum.platform === '喜马拉雅'
          ? `订阅成功，自动下载使用${savedQuality === XMLY_WEB_INTERFACE ? '网页版接口' : savedQuality}`
          : '订阅成功',
        'ok',
      );
      if (data.job) setSubscriptionJobs((prev) => ({...prev, [data.job.id]: data.job}));
      setTimeout(loadSubscriptions, 800);
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [chapters, loadSubscriptions, runBusy, selectedAlbum, selectedVoice, showToast, subscriptionQuality]);

  const playChapter = useCallback(async (chapter) => {
    if (!selectedAlbum || !chapter) return;
    try {
      setPlayer({show: true, title: '准备播放...', sub: selectedAlbum.title || '', album: selectedAlbum.title || '', artist: selectedAlbum.author || selectedAlbum.anchor || '', cover: coverOf(selectedAlbum), url: '', chapterId: chapterId(chapter), playing: false});
      const data = await api('/api/album/audio', {
        method: 'POST',
        body: {album: selectedAlbum, chapter, voice: selectedVoice},
      });
      setPlayer({
        show: true,
        title: chapterTitle(chapter),
        sub: selectedAlbum.title || '',
        album: selectedAlbum.title || '',
        artist: selectedAlbum.author || selectedAlbum.anchor || '',
        cover: coverOf(selectedAlbum),
        url: data.url,
        chapterId: chapterId(chapter),
        playing: false,
      });
      setTimeout(() => audioRef.current && audioRef.current.play && audioRef.current.play().catch((error) => {
        setPlayer((prev) => ({...prev, playing: false}));
        showToast('播放失败：' + (error.message || '浏览器拒绝播放'), 'err');
      }), 50);
    } catch (error) {
      setPlayer((prev) => ({...prev, show: false}));
      showToast('播放失败：' + error.message, 'err');
    }
  }, [selectedAlbum, selectedVoice, showToast]);

  const playAdjacentChapter = useCallback((direction) => {
    const list = displayChapters || chapters;
    if (!list.length) return;
    const currentId = player.chapterId;
    const index = Math.max(0, list.findIndex((chapter, idx) => chapterId(chapter, String(idx + 1)) === currentId));
    const next = list[index + Number(direction || 0)];
    if (!next) {
      showToast(direction < 0 ? '已经是第一章' : '已经是最后一章', 'err');
      return;
    }
    playChapter(next);
  }, [chapters, displayChapters, playChapter, player.chapterId, showToast]);

  const controlDownload = useCallback(async (id, action) => {
    await runBusy('download:' + id + ':' + action, async () => {
      await api('/api/downloads/' + encodeURIComponent(id) + '/' + action, {method: 'POST'});
      showToast(action === 'retry-failed' ? '已重新尝试该任务' : '操作已发送', 'ok');
      loadDownloads();
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [loadDownloads, runBusy, showToast]);

  const batchControlDownloads = useCallback(async (action) => {
    await runBusy('batchDownload:' + action, async () => {
      const targets = downloads.filter((t) => {
        if (action === 'pause') return t.status === 'running';
        if (action === 'stop') return ['queued', 'running', 'paused'].includes(t.status);
        return false;
      });
      if (!targets.length) { showToast('没有可操作的任务', 'err'); return; }
      await Promise.allSettled(targets.map((t) => api('/api/downloads/' + encodeURIComponent(t.id) + '/' + action, {method: 'POST'})));
      showToast('已' + (action === 'pause' ? '暂停' : '停止') + ' ' + targets.length + ' 个任务', 'ok');
      loadDownloads();
    }).catch((error) => showToast(error.message, 'err'));
  }, [downloads, loadDownloads, runBusy, showToast]);

  const deleteDownload = useCallback(async (id) => {
    try {
      await api('/api/downloads/' + encodeURIComponent(id), {method: 'DELETE'});
      showToast('已清除', 'ok');
      loadDownloads();
    } catch (error) {
      showToast(error.message, 'err');
    }
  }, [loadDownloads, showToast]);

  const cleanupDownloads = useCallback(async (statuses) => {
    await runBusy('cleanupDownloads', async () => {
      const data = await api('/api/downloads/cleanup', {method: 'POST', body: {statuses}});
      showToast('已清理 ' + (data.count || 0) + ' 条任务记录', 'ok');
      await loadDownloads();
    }).catch((error) => showToast(error.message, 'err'));
  }, [loadDownloads, runBusy, showToast]);

  const retryUnfinishedDownloads = useCallback(async () => {
    await runBusy('retryUnfinishedDownloads', async () => {
      const data = await api('/api/downloads/retry-unfinished', {method: 'POST'});
      showToast('已重新尝试 ' + (data.count || 0) + ' 个未完成任务', 'ok');
      await loadDownloads();
      loadEvents().catch(() => {});
    }).catch((error) => showToast('重试失败：' + error.message, 'err'));
  }, [loadDownloads, loadEvents, runBusy, showToast]);

  const saveSubscriptionSettings = useCallback(async (settings) => {
    await runBusy('subscriptionSettings', async () => {
      await api('/api/subscriptions/settings', {method: 'POST', body: {...settings, run_now: true}});
      showToast('订阅设置已保存', 'ok');
      await loadSubscriptionSettings();
      await loadSubscriptionScheduler().catch(() => {});
      setTimeout(loadSubscriptions, 800);
    }).catch((error) => {
      showToast('保存失败：' + error.message, 'err');
    });
  }, [loadSubscriptionScheduler, loadSubscriptionSettings, loadSubscriptions, runBusy, showToast]);

  const runSubscriptionsNow = useCallback(async () => {
    await runBusy('runSubscriptions', async () => {
      const data = await api('/api/subscriptions/run', {method: 'POST'});
      const nextJobs = {};
      for (const job of data.jobs || []) nextJobs[job.id] = job;
      setSubscriptionJobs((prev) => ({...prev, ...nextJobs}));
      if (data.scheduler) setSubscriptionScheduler(data.scheduler);
      showToast('已开始后台检测', 'ok');
    }).catch((error) => {
      showToast('启动检测失败：' + error.message, 'err');
    });
  }, [runBusy, showToast]);

  const runPersonalSubscriptionSyncNow = useCallback(async () => {
    await runBusy('personalSubscriptionSync', async () => {
      const data = await api('/api/subscriptions/personal-sync/run', {method: 'POST'});
      const result = data.result || {};
      if (data.scheduler) setSubscriptionScheduler(data.scheduler);
      showToast(`个人订阅同步完成：新增 ${result.added || 0}，总计 ${result.total || 0}`, 'ok');
      setTimeout(loadSubscriptions, 800);
    }).catch((error) => {
      showToast('同步失败：' + error.message, 'err');
    });
  }, [loadSubscriptions, runBusy, showToast]);

  const rebuildSubscriptionIndex = useCallback(async () => {
    await runBusy('rebuildIndex', async () => {
      const data = await api('/api/subscriptions/index/rebuild', {method: 'POST'});
      showToast('本地索引已更新，共识别 ' + ((data.index && data.index.count) || 0) + ' 个音频文件', 'ok');
      loadSubscriptions({refreshLocal: true});
    }).catch((error) => {
      showToast('重建索引失败：' + error.message, 'err');
    });
  }, [loadSubscriptions, runBusy, showToast]);

  const checkSubscription = useCallback(async (id, complete = false) => {
    await runBusy('subscription:' + id + ':' + (complete ? 'complete' : 'check'), async () => {
      const data = await api('/api/subscriptions/' + encodeURIComponent(id) + '/' + (complete ? 'complete' : 'check'), {method: 'POST'});
      if (data.job) setSubscriptionJobs((prev) => ({...prev, [data.job.id]: data.job}));
      showToast(complete ? '补全任务已开始' : '检测已开始', 'ok');
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [runBusy, showToast]);

  const cancelSubscription = useCallback(async (id) => {
    try {
      await api('/api/subscriptions/' + encodeURIComponent(id), {method: 'DELETE'});
      showToast('已取消', 'ok');
      loadSubscriptions();
    } catch (error) {
      showToast(error.message, 'err');
    }
  }, [loadSubscriptions, showToast]);

  const updateSubscriptionQuality = useCallback(async (id, subscriptionQuality) => {
    await runBusy('subscription:' + id + ':quality', async () => {
      const data = await api('/api/subscriptions/' + encodeURIComponent(id), {
        method: 'PATCH',
        body: {subscription_quality: subscriptionQuality},
      });
      const updated = data.subscription || {};
      setSubscriptions((current) => current.map((item) => (
        item.id === id ? {...item, ...updated, subscription_quality: subscriptionQuality} : item
      )));
      setSelectedAlbum((current) => {
        if (!current || current.library?.subscription_id !== id) return current;
        return {...current, library: {...current.library, subscription_quality: subscriptionQuality}};
      });
      if (selectedAlbum?.library?.subscription_id === id) {
        setSubscriptionQuality(ximalayaSubscriptionQuality(subscriptionQuality));
      }
      showToast('该专辑的订阅下载方式已保存', 'ok');
    }).catch((error) => {
      showToast('保存失败：' + error.message, 'err');
    });
  }, [runBusy, selectedAlbum, showToast]);

  const chooseSubscriptionQuality = useCallback((quality) => {
    const normalized = ximalayaSubscriptionQuality(quality);
    const sid = selectedAlbum?.library?.subscription_id;
    if (selectedAlbum?.library?.subscribed && sid) {
      return updateSubscriptionQuality(sid, normalized);
    }
    setSubscriptionQuality(normalized);
    return Promise.resolve();
  }, [selectedAlbum, updateSubscriptionQuality]);

  const batchSubscriptions = useCallback(async (action, ids = []) => {
    await runBusy('subscriptionBatch:' + action, async () => {
      const data = await api('/api/subscriptions/batch', {method: 'POST', body: {action, ids}});
      if (data.jobs) {
        const nextJobs = {};
        for (const job of data.jobs || []) nextJobs[job.id] = job;
        setSubscriptionJobs((prev) => ({...prev, ...nextJobs}));
      }
      showToast('批量操作完成：' + (data.count || 0), 'ok');
      await loadSubscriptions();
      loadEvents().catch(() => {});
    }).catch((error) => showToast('批量操作失败：' + error.message, 'err'));
  }, [loadEvents, loadSubscriptions, runBusy, showToast]);

  const saveCookie = useCallback(async (platformKey, cookie) => {
    if (!cookie || !cookie.trim()) {
      showToast('Cookie 不能为空', 'err');
      return;
    }
    await runBusy('cookie:' + platformKey, async () => {
      await api('/api/cookies', {method: 'POST', body: {platform: platformKey, cookie: cookie.trim()}});
      showToast('Cookie 已保存到本地配置', 'ok');
      loadCookies();
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [loadCookies, runBusy, showToast]);

  const saveXimalayaMobileTicket = useCallback(async (credentials) => {
    if (!credentials || !credentials.trim()) {
      showToast('移动版 V4 App Cookie 不能为空', 'err');
      return false;
    }
    try {
      await runBusy('xmlyMobileTicket', async () => {
        await api('/api/cookies/xmly/mobile-ticket', {method: 'POST', body: {credentials: credentials.trim()}});
        showToast('移动版 V4 App Cookie 已保存，后续请求将本地生成 x-tk', 'ok');
        await loadCookies();
      });
      return true;
    } catch (error) {
      showToast(error.message, 'err');
      return false;
    }
  }, [loadCookies, runBusy, showToast]);

  const deleteXimalayaMobileTicket = useCallback(async () => {
    try {
      await runBusy('xmlyMobileTicketDelete', async () => {
        await api('/api/cookies/xmly/mobile-ticket', {method: 'DELETE'});
        showToast('已删除移动版 V4 App Cookie，网页登录保留', 'ok');
        await loadCookies();
      });
      return true;
    } catch (error) {
      showToast(error.message, 'err');
      return false;
    }
  }, [loadCookies, runBusy, showToast]);

  const sendXimalayaMobileCode = useCallback(async (phone) => {
    return api('/api/cookies/xmly/mobile-login/send-code', {
      method: 'POST', body: {phone},
    });
  }, []);

  const loginXimalayaMobile = useCallback(async (phone, code) => {
    const data = await api('/api/cookies/xmly/mobile-login/verify', {
      method: 'POST', body: {phone, code},
    });
    showToast(data.message || '喜马拉雅移动端登录成功', 'ok');
    await loadCookies();
    return data;
  }, [loadCookies, showToast]);

  const deleteCookie = useCallback(async (platformKey) => {
    await runBusy('cookieDelete:' + platformKey, async () => {
      await api('/api/cookies/' + encodeURIComponent(platformKey), {method: 'DELETE'});
      showToast('已删除并同步', 'ok');
      loadCookies();
    }).catch((error) => {
      showToast(error.message, 'err');
    });
  }, [loadCookies, runBusy, showToast]);

  const saveSettings = useCallback(async ({
    downloadDir,
    quality,
    downloadThreads,
    organizeByPlatformEnabled,
    splitChaptersEnabled,
    chaptersPerFolder,
    filenamePrefixFormat,
    manualOrganizeMode,
    taskHistoryMaxKeep,
    taskHistoryMaxAgeDays,
    taskDetailRetentionDays,
    taskFailureChapterLimit,
    taskHistoryMaxMB,
    backgroundEventsMaxKeep,
  }) => {
    await runBusy('settings', async () => {
      await api('/api/config', {
        method: 'POST',
        body: {
          download_dir: downloadDir,
          quality,
          download_threads: downloadThreads,
          organize_by_platform_enabled: organizeByPlatformEnabled,
          split_chapters_enabled: splitChaptersEnabled,
          chapters_per_folder: chaptersPerFolder,
          filename_prefix_format: filenamePrefixFormat,
          manual_organize_mode: manualOrganizeMode,
          task_history_max_keep: taskHistoryMaxKeep,
          task_history_max_age_days: taskHistoryMaxAgeDays,
          task_detail_retention_days: taskDetailRetentionDays,
          task_failure_chapter_limit: taskFailureChapterLimit,
          task_history_max_bytes: taskHistoryMaxMB * 1024 * 1024,
          background_events_max_keep: backgroundEventsMaxKeep,
        },
      });
      showToast('设置已保存', 'ok');
      await Promise.all([loadConfig(), loadEvents()]);
    }).catch((error) => {
      showToast('保存失败：' + error.message, 'err');
    });
  }, [loadConfig, loadEvents, runBusy, showToast]);

  const clearLogs = useCallback(async () => {
    try {
      await api('/api/logs', {method: 'DELETE'});
      showToast('日志已清空', 'ok');
      loadLogs();
    } catch (error) {
      showToast('清空失败：' + error.message, 'err');
    }
  }, [loadLogs, showToast]);

  const clearEvents = useCallback(async () => {
    try {
      await api('/api/events', {method: 'DELETE'});
      setEvents([]);
      showToast('后台记录已清空', 'ok');
    } catch (error) {
      showToast('清空失败：' + error.message, 'err');
    }
  }, [showToast]);


  const loadDiagnostics = useCallback(async () => {
    await runBusy('diagnostics', async () => {
      const data = await api('/api/diagnostics');
      setDiagnostics(data);
      return data;
    }).catch((error) => showToast('诊断失败：' + error.message, 'err'));
  }, [runBusy, showToast]);

  const loadNotifications = useCallback(async () => {
    const data = await api('/api/notifications');
    setNotificationConfig(data.config || {});
    return data.config || {};
  }, []);

  const loadAgent = useCallback(async () => {
    const [statusData, sessionsData, plansData] = await Promise.all([
      api('/api/agent/status'),
      api('/api/agent/sessions'),
      api('/api/rename-plans'),
    ]);
    setAgentStatus(statusData || {});
    setAgentSessions(sessionsData.sessions || []);
    setRenamePlans(plansData.plans || []);
    return statusData;
  }, []);

  const reloadRenamePlans = useCallback(async () => {
    const data = await api('/api/rename-plans');
    setRenamePlans(data.plans || []);
    return data.plans || [];
  }, []);

  const reviewRenamePlan = useCallback(async (planId, choices) => runBusy('renamePlan:' + planId, async () => {
    const data = await api(`/api/rename-plans/${encodeURIComponent(planId)}/review`, {method: 'POST', body: choices});
    await reloadRenamePlans();
    showToast(data.plan?.status === 'pending_confirmation' ? '复核完成，等待最终确认' : '复核选择已保存', 'ok');
    return data.plan;
  }), [reloadRenamePlans, runBusy, showToast]);

  const resolveRenamePlanSafe = useCallback(async (planId) => runBusy('renamePlan:' + planId, async () => {
    const data = await api(`/api/rename-plans/${encodeURIComponent(planId)}/resolve-safe`, {method: 'POST', body: {}});
    await reloadRenamePlans();
    showToast('风险和特殊文件将保持不动', 'ok');
    return data.plan;
  }), [reloadRenamePlans, runBusy, showToast]);

  const confirmRenamePlan = useCallback(async (planId) => runBusy('renamePlan:' + planId, async () => {
    const data = await api(`/api/rename-plans/${encodeURIComponent(planId)}/confirm`, {method: 'POST', body: {}});
    await reloadRenamePlans();
    showToast('有声书整理完成', 'ok');
    return data.plan;
  }), [reloadRenamePlans, runBusy, showToast]);

  const cancelRenamePlan = useCallback(async (planId) => runBusy('renamePlan:' + planId, async () => {
    const data = await api(`/api/rename-plans/${encodeURIComponent(planId)}/cancel`, {method: 'POST', body: {}});
    await reloadRenamePlans();
    showToast('整理计划已取消', 'ok');
    return data.plan;
  }), [reloadRenamePlans, runBusy, showToast]);

  const loadAgentSession = useCallback(async (sessionId) => {
    if (!sessionId) {
      setAgentSession(null);
      return null;
    }
    const data = await api('/api/agent/sessions/' + encodeURIComponent(sessionId));
    setAgentSession(data.session || null);
    return data.session || null;
  }, []);

  const saveAgentConfig = useCallback(async (nextConfig) => {
    return runBusy('agentConfig', async () => {
      const data = await api('/api/agent/config', {method: 'POST', body: nextConfig});
      setAgentStatus((prev) => ({...prev, config: data.config || {}, developer: data.developer || prev.developer || {}}));
      showToast(data.developer?.last_error ? `配置已保存，代码 Agent 启动失败：${data.developer.last_error}` : 'Agent 配置已保存', data.developer?.last_error ? 'err' : 'ok');
      return data.config;
    }).catch((error) => {
      showToast('保存失败：' + error.message, 'err');
      throw error;
    });
  }, [runBusy, showToast]);

  const startDeveloperAgent = useCallback(async () => {
    return runBusy('developerAgent', async () => {
      const data = await api('/api/agent/developer/start', {method: 'POST', body: {}});
      setAgentStatus((prev) => ({...prev, developer: data.status || {}}));
      showToast('飞书完整代码 Agent 已启动', 'ok');
      return data.status;
    }).catch((error) => {
      showToast('启动失败：' + error.message, 'err');
      throw error;
    });
  }, [runBusy, showToast]);

  const stopDeveloperAgent = useCallback(async () => {
    return runBusy('developerAgent', async () => {
      const data = await api('/api/agent/developer/stop', {method: 'POST', body: {}});
      setAgentStatus((prev) => ({...prev, developer: data.status || {}}));
      showToast('飞书完整代码 Agent 已停止', 'ok');
      return data.status;
    });
  }, [runBusy, showToast]);

  const testAgent = useCallback(async () => {
    return runBusy('agentTest', async () => {
      const data = await api('/api/agent/test', {method: 'POST', body: {}});
      showToast(data.result?.reply || '模型连接成功', 'ok');
      return data.result;
    }).catch((error) => {
      showToast('连接失败：' + error.message, 'err');
      throw error;
    });
  }, [runBusy, showToast]);

  const sendAgentMessage = useCallback(async (message) => {
    return runBusy('agentChat', async () => {
      const data = await api('/api/agent/chat', {method: 'POST', body: {message, session_id: agentSession?.id}});
      setAgentSession(data.session || null);
      const sessionsData = await api('/api/agent/sessions');
      setAgentSessions(sessionsData.sessions || []);
      return data.message;
    }).catch((error) => {
      showToast('Agent 请求失败：' + error.message, 'err');
      throw error;
    });
  }, [agentSession?.id, runBusy, showToast]);

  const deleteAgentSession = useCallback(async (sessionId) => {
    await api('/api/agent/sessions/' + encodeURIComponent(sessionId), {method: 'DELETE'});
    if (agentSession?.id === sessionId) setAgentSession(null);
    const data = await api('/api/agent/sessions');
    setAgentSessions(data.sessions || []);
  }, [agentSession?.id]);

  const checkServiceConnection = useCallback(async ({reload = false} = {}) => {
    setServiceState('checking');
    try {
      await api('/health');
      setServiceState('online');
      if (reload) {
        await Promise.allSettled([
          loadConfig(),
          loadCookies(),
          loadNotifications(),
          loadSubscriptions(),
          loadDownloads(),
          loadEvents(),
        ]);
      }
      return true;
    } catch (_error) {
      setServiceState(hasCachedDataRef.current ? 'cached' : 'offline');
      return false;
    }
  }, [loadConfig, loadCookies, loadDownloads, loadEvents, loadNotifications, loadSubscriptions]);

  const saveNotifications = useCallback(async (nextConfig) => {
    await runBusy('notifications', async () => {
      const data = await api('/api/notifications', {method: 'POST', body: nextConfig});
      setNotificationConfig(data.config || {});
      showToast('通知设置已保存', 'ok');
    }).catch((error) => showToast('保存失败：' + error.message, 'err'));
  }, [runBusy, showToast]);

  const testNotifications = useCallback(async (serviceId, service) => {
    await runBusy('notificationTest:' + (serviceId || 'all'), async () => {
      const body = service ? {service} : {service_id: serviceId};
      const data = await api('/api/notifications/test', {method: 'POST', body});
      const result = data.result || {};
      showToast(`测试完成：成功 ${result.sent || 0}，失败 ${result.failed || 0}`, result.failed ? 'err' : 'ok');
    }).catch((error) => showToast('测试失败：' + error.message, 'err'));
  }, [runBusy, showToast]);

  const changePassword = useCallback(async ({oldPassword, newPassword}) => {
    try {
      await api('/api/auth/password', {method: 'POST', body: {old_password: oldPassword, new_password: newPassword}});
      showToast('密码已修改，请重新登录', 'ok');
      setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      showToast('修改失败：' + error.message, 'err');
    }
  }, [showToast]);

  const logoutAccount = useCallback(async () => {
    await logout();
    await requireLogin();
  }, [requireLogin]);

  useEffect(() => {
    setAuthRequiredHandler(requireLogin);
    return () => setAuthRequiredHandler(null);
  }, [requireLogin]);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    checkServiceConnection({reload: true});
  }, [checkServiceConnection]);

  useEffect(() => {
    requestNotificationPermission();
  }, []);

  useEffect(() => {
    const refreshVisibleData = () => {
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - foregroundRefreshRef.current < 1200) return;
      foregroundRefreshRef.current = now;
      if (serviceState !== 'online') {
        checkServiceConnection({reload: true});
        return;
      }
      if (page === 'downloads' || mobileView === 'downloads') {
        loadDownloads().catch(() => {});
        return;
      }
      if (page === 'subscriptions' || mobileView === 'subscriptions') {
        loadSubscriptions().catch(() => {});
        loadSubscriptionScheduler().catch(() => {});
        return;
      }
      loadDownloads().catch(() => {});
      loadSubscriptions().catch(() => {});
    };
    window.addEventListener('focus', refreshVisibleData);
    window.addEventListener('pageshow', refreshVisibleData);
    window.addEventListener('online', refreshVisibleData);
    document.addEventListener('visibilitychange', refreshVisibleData);
    return () => {
      window.removeEventListener('focus', refreshVisibleData);
      window.removeEventListener('pageshow', refreshVisibleData);
      window.removeEventListener('online', refreshVisibleData);
      document.removeEventListener('visibilitychange', refreshVisibleData);
    };
  }, [checkServiceConnection, loadDownloads, loadSubscriptionScheduler, loadSubscriptions, mobileView, page, serviceState]);

  useEffect(() => {
    const timer = setInterval(async () => {
      if (serviceState !== 'online') return;
      if (page !== 'downloads' && mobileView !== 'downloads') return;
      const pg = downloadPagination.page || 1;
      const data = await api(`/api/downloads?page=${pg}&limit=20&status=${encodeURIComponent(downloadStatusFilter)}`).catch(() => null);
      if (!data) return;
      const tasks = data.tasks || [];
      for (const task of tasks) {
        const prev = prevDownloadStatusRef.current[task.id];
        if (prev && prev !== 'completed' && task.status === 'completed') {
          showToast('下载完成：' + (task.title || task.id), 'ok');
          sendBrowserNotification('AudioFlow下载完成', task.title || task.id);
        }
      }
      prevDownloadStatusRef.current = Object.fromEntries(tasks.map((t) => [t.id, t.status]));
      setDownloads(tasks);
      if (data.pagination) setDownloadPagination(data.pagination);
      if (data.summary) setDownloadSummary(data.summary);
      saveCachedList(DOWNLOADS_CACHE_KEY, tasks);
    }, 3000);
    return () => clearInterval(timer);
  }, [mobileView, page, serviceState, showToast, downloadStatusFilter, downloadPagination.page]);

  useEffect(() => {
    const timer = setInterval(async () => {
      if (serviceState !== 'online') return;
      if (page !== 'subscriptions' && mobileView !== 'subscriptions') return;
      const scheduler = await loadSubscriptionScheduler().catch(() => null);
      if (!scheduler) return;
      const lastRunChanged = scheduler.last_run_at && scheduler.last_run_at !== subscriptionScheduler.last_run_at;
      if (lastRunChanged || scheduler.running || scheduler.current_due_count > 0) {
        loadSubscriptions().catch(() => {});
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [loadSubscriptionScheduler, loadSubscriptions, mobileView, page, serviceState, subscriptionScheduler.last_run_at]);

  useEffect(() => {
    const timers = Object.values(subscriptionJobs)
      .filter((job) => job && job.id && ['queued', 'running'].includes(job.status))
      .map((job) => setTimeout(async () => {
        try {
          const data = await api('/api/subscriptions/jobs/' + encodeURIComponent(job.id));
          setSubscriptionJobs((prev) => {
            const next = {...prev};
            if (data.job && ['queued', 'running'].includes(data.job.status)) next[job.id] = data.job;
            else delete next[job.id];
            return next;
          });
          if (!data.job || !['queued', 'running'].includes(data.job.status)) {
            showToast((data.job && data.job.message) || '订阅处理完成', (data.job && data.job.status === 'failed') ? 'err' : 'ok');
            loadSubscriptions();
            loadDownloads();
          }
        } catch (_e) {
          setSubscriptionJobs((prev) => {
            const next = {...prev};
            delete next[job.id];
            return next;
          });
        }
      }, 1500));
    return () => timers.forEach(clearTimeout);
  }, [loadDownloads, loadSubscriptions, showToast, subscriptionJobs]);

  return {
    page,
    setPage,
    mobileView,
    setMobileView,
    platform,
    setPlatform,
    query,
    setQuery,
    results,
    selectedAlbum,
    chapters,
    chapterPagination,
    displayChapters,
    chapterSort,
    setChapterSort,
    downloadRange,
    setDownloadRange,
    selectedChapters,
    selectedChapterList,
    voices,
    selectedVoice,
    downloadQuality,
    setDownloadQuality,
    ximalayaInterface,
    setXimalayaInterface,
    subscriptionQuality,
    downloads,
    downloadPagination,
    downloadSummary,
    downloadStatusFilter,
    subscriptions,
    subscriptionSettings,
    subscriptionScheduler,
    subscriptionJobs,
    notificationConfig,
    agentStatus,
    agentSessions,
    agentSession,
    renamePlans,
    cookies,
    config,
    logs,
    events,
    busy,
    diagnostics,
    status,
    serviceState,
    toast,
    modal,
    loginVisible,
    loginError,
    loginLoading,
    submitLogin,
    setModal,
    closeModal,
    player,
    setPlayer,
    audioRef,
    searchHistory,
    metrics: {
      activeDownloads: downloadSummary.active_count || 0,
      completedDownloads: downloadSummary.completed_count || 0,
      failedDownloads: downloadSummary.failed_count || 0,
      interruptedDownloads: downloadSummary.interrupted_count || 0,
    },
    actions: {
      showToast,
      doSearch,
      clearSearchHistory,
      openAlbum,
      closeAlbum: () => {
        albumRequestRef.current += 1;
        setSelectedAlbum(null);
        setChapters([]);
        setChapterPagination(DEFAULT_CHAPTER_PAGINATION);
        setChapterSort('asc');
        setDownloadRange('');
        setSelectedChapters(new Set());
      },
      changeVoice,
      loadChapterPage,
      toggleChapter,
      selectAllChapters,
      invertChapterSelection,
      applyDownloadRange,
      startDownload,
      subscribeAlbum,
      playChapter,
      playAdjacentChapter,
      controlDownload,
      batchControlDownloads,
      deleteDownload,
      cleanupDownloads,
      retryUnfinishedDownloads,
      loadDownloads,
      loadSubscriptions,
      exportSubscriptions,
      importSubscriptions,
      exportBackup,
      importBackup,
      loadSubscriptionSettings,
      loadSubscriptionScheduler,
      saveSubscriptionSettings,
      runSubscriptionsNow,
      runPersonalSubscriptionSyncNow,
      rebuildSubscriptionIndex,
      checkSubscription,
      cancelSubscription,
      updateSubscriptionQuality,
      chooseSubscriptionQuality,
      batchSubscriptions,
      loadCookies,
      exportCookies,
      importCookies,
      saveCookie,
      saveXimalayaMobileTicket,
      deleteXimalayaMobileTicket,
      sendXimalayaMobileCode,
      loginXimalayaMobile,
      deleteCookie,
      loadConfig,
      saveSettings,
      loadLogs,
      clearLogs,
      loadEvents,
      clearEvents,
      loadDiagnostics,
      loadNotifications,
      loadAgent,
      loadAgentSession,
      reloadRenamePlans,
      reviewRenamePlan,
      resolveRenamePlanSafe,
      confirmRenamePlan,
      cancelRenamePlan,
      saveAgentConfig,
      startDeveloperAgent,
      stopDeveloperAgent,
      testAgent,
      sendAgentMessage,
      deleteAgentSession,
      checkServiceConnection,
      saveNotifications,
      testNotifications,
      changePassword,
      logoutAccount,
    },
  };
}
