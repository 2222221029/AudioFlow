#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
增强搜索管理器
整合喜马拉雅、懒人听书、番茄畅听、酷我听书的完善API
"""

import sys
import threading
import time
import re
import math
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent))

# 暂时使用现有的管理器
from .ximalaya_manager import XimalayaManager, parse_ximalaya_album_id
from .lrts_manager import LRTSManager
from .fanqie_manager import FanqieManager
from .fanqie_tingshu_manager import get_fanqie_tingshu_manager, parse_book_id
from .qimao_manager import get_qimao_manager, parse_book_id as parse_qimao_book_id
from .yuntu_manager import YunTuManager
from .qtfm_manager import QtfmManager  # 导入蜻蜓FM管理器
from .search_manager import SearchManager  # 导入SearchManager用于起点搜索
from .kuwo_manager import KuwoManager  # 导入酷我听书管理器
from .netease_cloud_audiobook_manager import NeteaseCloudAudiobookManager
from .lizhi_manager import LizhiManager
from .safe_logging import log_context, log_event, platform_verbose_enabled


class EnhancedSearchManager:
    """增强搜索管理器"""

    KEYWORD_SEARCH_PLATFORMS = (
        '喜马拉雅', '懒人听书', '番茄畅听', '番茄听书', '七猫听书',
        '酷我听书', '起点听书', '蜻蜓FM', '网易云听书', '荔枝FM',
    )
    SEARCH_CACHE_TTL = 120
    SEARCH_CACHE_MAX_ITEMS = 64
    # These APIs accept a larger page size, so broader coverage still costs one
    # network round trip per platform. Multi-page-only providers stay on page 1.
    SEARCH_RESULT_LIMITS = {
        '喜马拉雅': 60,
        '懒人听书': 50,
        '酷我听书': 60,
        '起点听书': 50,
        '网易云听书': 60,
    }
    SEARCH_PAGE_LIMITS = {
        '番茄畅听': 2,
        '番茄听书': 2,
        '蜻蜓FM': 3,
    }
    
    def __init__(self, cookie_manager=None):
        """初始化搜索管理器"""
        # 如果没有传入Cookie管理器，创建一个新的
        if cookie_manager is None:
            from .cookie_manager import CookieManager
            cookie_manager = CookieManager()
        
        self.cookie_manager = cookie_manager
        
        # 初始化各个平台管理器，并传入Cookie
        self.ximalaya_manager = XimalayaManager()
        self.lrts_manager = LRTSManager()
        self.fanqie_manager = FanqieManager()
        self.fanqie_tingshu_manager = get_fanqie_tingshu_manager()
        self._qimao_manager = None
        self.yuntu_manager = YunTuManager()
        self.qtfm_manager = QtfmManager()  # 蜻蜓FM管理器
        self.search_manager = SearchManager(cookie_manager)  # 用于起点听书搜索
        self.kuwo_manager = KuwoManager()  # 酷我听书管理器
        self.netease_manager = NeteaseCloudAudiobookManager()
        self.lizhi_manager = LizhiManager()
        
        # 设置Cookie到各个管理器
        self._setup_cookies()
        
        # 当前选择的音质
        self.current_quality = "标准"
        self._keyword_search_cache = {}
        self._keyword_search_cache_lock = threading.Lock()
        self._chapter_list_cache = {}
        self._chapter_list_cache_lock = threading.Lock()
        
        print("🚀 增强搜索管理器已初始化（含酷我听书）")

    @property
    def qimao_manager(self):
        """首次使用七猫听书时再加载 qimao_portable.py/httpx，避免启动阶段做多余初始化。"""
        if self._qimao_manager is None:
            self._qimao_manager = get_qimao_manager()
        return self._qimao_manager
    
    def check_vip_status(self) -> bool:
        """兼容旧调用：自用版不做授权或额度校验。"""
        print("✅ 自用版：搜索/下载不经过授权或额度校验")
        return True
    
    def _setup_cookies(self):
        """设置Cookie到各个平台管理器"""
        try:
            # 设置喜马拉雅Cookie
            xmly_server_cookie = self.cookie_manager.get_server_cookie_cache('xmly')
            xmly_cookie = xmly_server_cookie or self.cookie_manager.get_cookie('xmly')
            if xmly_cookie and hasattr(self.ximalaya_manager, 'set_cookie'):
                if isinstance(xmly_cookie, dict):
                    xmly_cookie = '; '.join([f"{name}={value}" for name, value in xmly_cookie.items()])
                self.ximalaya_manager.set_cookie(xmly_cookie, is_server_cookie=bool(xmly_server_cookie))
                print("🍪 喜马拉雅Cookie已设置")
            xmly_mobile = self.cookie_manager.get_cookie('xmly_mobile')
            if xmly_mobile:
                self.set_ximalaya_mobile_credentials(xmly_mobile)
            
            # 设置懒人听书Cookie
            lrts_cookie = self.cookie_manager.get_cookie('lrts')
            if lrts_cookie and hasattr(self.lrts_manager, 'set_cookie'):
                self.lrts_manager.set_cookie(lrts_cookie)
                print("🍪 懒人听书Cookie已设置")
            
            # 设置起点听书Cookie
            qidian_cookie = self.cookie_manager.get_cookie('qidian')
            if qidian_cookie and hasattr(self.search_manager, 'set_cookie'):
                self.search_manager.set_cookie('起点听书', qidian_cookie)
                print("🍪 起点听书Cookie已设置")
            
            # 番茄畅听不需要Cookie
            print("🍅 番茄畅听无需Cookie")
            
            # 云听FM不需要Cookie
            print("☁️ 云听FM无需Cookie")
            
            # 设置蜻蜓FM登录信息
            qtfm_cookie = self.cookie_manager.get_cookie('qtfm')
            if qtfm_cookie and isinstance(qtfm_cookie, dict):
                access_token = qtfm_cookie.get('access_token', '')
                qingting_id = qtfm_cookie.get('qingting_id', '')
                if access_token and qingting_id:
                    self.qtfm_manager.set_auth_info(access_token, qingting_id)
                    self.qtfm_manager.get_user_profile()
                else:
                    print("🎧 蜻蜓FM登录信息不完整")
            elif qtfm_cookie and isinstance(qtfm_cookie, str):
                self.qtfm_manager.set_cookie(qtfm_cookie)
                print("🎧 蜻蜓FMCookie已设置")
            else:
                print("🎧 蜻蜓FM未登录")

            netease_cookie = self.cookie_manager.get_cookie('netease')
            if netease_cookie and hasattr(self.netease_manager, 'set_cookie'):
                if isinstance(netease_cookie, dict):
                    netease_cookie = '; '.join([f"{name}={value}" for name, value in netease_cookie.items()])
                self.netease_manager.set_cookie(netease_cookie)
                print("🍪 网易云听书Cookie已设置")

            print("🍥 荔枝FM使用公开播客接口，无需Cookie")

        except Exception as e:
            print(f"⚠️ 设置Cookie失败: {e}")
    
    def update_cookies(self):
        """更新Cookie到各个平台管理器"""
        self._setup_cookies()
        self.clear_search_cache()

    def clear_search_cache(self):
        with self._keyword_search_cache_lock:
            self._keyword_search_cache.clear()

    def _pick_cover_value(self, book: Dict) -> str:
        """从不同平台的搜索结果中尽量提取封面字段。"""
        if not isinstance(book, dict):
            return ""
        keys = (
            "cover", "cover_url", "coverUrl", "cover_path", "coverPath",
            "coverLarge", "coverMiddle", "coverSmall", "largeCover", "smallCover",
            "pic", "picUrl", "image", "imageUrl", "img", "imgPath",
            "album_cover", "albumCover", "albumCoverUrl", "album_cover_url",
            "thumb", "thumb_url", "thumbnail", "thumbnailUrl",
            "itemCoverUrl", "itemSquareCoverUrl", "trackCoverPath",
            "bookCover", "book_cover", "poster", "posterUrl",
            "hts_img", "albumpic", "albumPic", "web_albumpic_short",
        )
        for key in keys:
            value = book.get(key)
            if value:
                return str(value).strip()
        for key in ("album", "book", "item", "data", "detail", "raw"):
            nested = book.get(key)
            if isinstance(nested, dict):
                value = self._pick_cover_value(nested)
                if value:
                    return value
        return ""

    def _normalize_cover_url(self, url: str, platform: str = "") -> str:
        url = str(url or "").strip()
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http"):
            return url
        if platform == "喜马拉雅":
            return "https://imagev2.xmcdn.com" + (url if url.startswith("/") else f"/{url}")
        if platform == "懒人听书":
            return "https://m.lrts.me" + (url if url.startswith("/") else f"/{url}")
        if platform == "云听FM":
            return "https://www.radio.cn" + (url if url.startswith("/") else f"/{url}")
        return url

    def _first_value(self, book: Dict, *keys):
        for key in keys:
            value = book.get(key)
            if value not in (None, ""):
                return value
        for key in ("album", "book", "item", "data", "detail", "raw", "raw_data"):
            nested = book.get(key)
            if isinstance(nested, dict):
                value = self._first_value(nested, *keys)
                if value not in (None, ""):
                    return value
        return ""

    def _int_value(self, value, default: int = 0) -> int:
        if value in (None, ""):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _metric_value(value) -> int:
        """Parse provider counters such as 123456, 1.2万, 3亿 or 25K."""
        if value in (None, "") or isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            return max(0, int(value))
        text = unicodedata.normalize("NFKC", str(value)).strip().replace(",", "")
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return 0
        number = float(match.group(1))
        suffix = text[match.end():].strip().lower()
        if "亿" in suffix or suffix.startswith("b"):
            number *= 100_000_000 if "亿" in suffix else 1_000_000_000
        elif "万" in suffix or suffix.startswith("w"):
            number *= 10_000
        elif "千" in suffix or suffix.startswith("k"):
            number *= 1_000
        elif suffix.startswith("m"):
            number *= 1_000_000
        return max(0, int(number))

    @classmethod
    def _pick_popularity(cls, book: Dict) -> int:
        """Pick the strongest play/search heat counter exposed by a provider."""
        if not isinstance(book, dict):
            return 0
        play_keys = (
            "plays", "play_count", "playCount", "playcount", "PLAYCNT",
            "play_cnt", "playCnt", "listen_count", "listenCount",
            "listening_count", "listeningCount", "listener_count", "listenerCount",
            "view_count", "viewCount", "replayCount", "replay_count",
            "play_num", "playNum", "listen_num", "listenNum",
            "read_count", "readCount", "read_num", "readNum",
        )
        heat_keys = (
            "popularity", "heat", "hot", "hot_score", "hotScore",
            "search_heat", "searchHeat", "subscribe_count", "subscribeCount",
            "subscriber_count", "subscriberCount", "favorite_count", "favoriteCount",
            "collect_count", "collectCount", "follow_count", "followCount",
            "fans_count", "fansCount", "rank_score", "rankScore", "static_score",
        )

        def collect(mapping, keys):
            values = [cls._metric_value(mapping.get(key)) for key in keys if key in mapping]
            for nested_key in (
                "album", "book", "item", "data", "detail", "raw", "raw_data", "_raw",
            ):
                nested = mapping.get(nested_key)
                if isinstance(nested, dict):
                    values.extend(collect(nested, keys))
            return values

        play_values = collect(book, play_keys)
        best_play = max(play_values, default=0)
        if best_play > 0:
            return best_play
        return max(collect(book, heat_keys), default=0)

    def _pick_episode_count(self, book: Dict) -> int:
        return self._int_value(self._first_value(
            book,
            "episodes", "chapter_count", "chapterCount", "chapters", "track_count",
            "trackCount", "tracks", "tracks_count", "tracksCount", "total_chapters",
            "AllAudioChapters", "total_num", "totalNum", "total", "sections",
            "section_count", "entityCount", "programCount", "songCount",
        ))

    def _pick_author_value(self, book: Dict) -> str:
        return str(self._first_value(
            book,
            "anchorNickName", "anchorNickname", "anchorName", "AnchorName",
            "nickname", "nickName", "userName", "userNickname", "userNickName",
            "author", "authorName", "anchor", "announcer", "reader", "narrator",
            "artist", "speaker",
        ) or "").strip()

    def _ensure_book_fields(self, book: Dict, platform: str) -> Dict:
        book["platform"] = platform
        cover = self._pick_cover_value(book)
        book["cover"] = self._normalize_cover_url(cover, platform) if cover else ""
        author = self._pick_author_value(book)
        if author and (not book.get("author") or str(book.get("author")).strip() in ("未知", "未知作者")):
            book["author"] = author
        popularity = self._pick_popularity(book)
        if popularity > 0 or "plays" not in book:
            book["plays"] = popularity
        episodes = self._pick_episode_count(book)
        if episodes > 0:
            book["episodes"] = episodes
        elif "episodes" not in book:
            book["episodes"] = 0
        if "status" not in book:
            book["status"] = "连载中"
        return book

    def _enrich_search_result_details(self, books: List[Dict], platform: str, limit: int = 12) -> None:
        """对搜索接口不给章节数的平台做小批量详情补全，避免 UI 长期显示 0 章。"""
        if not books:
            return
        targets = [
            book for book in books[:limit]
            if self._pick_episode_count(book) <= 0 or not self._pick_cover_value(book) or not self._pick_author_value(book)
        ]
        if not targets:
            return

        def _fetch(book: Dict):
            album_id = str(book.get("id") or book.get("album_id") or book.get("book_id") or "").strip()
            if not album_id:
                return book, None
            try:
                if platform == "番茄畅听":
                    return book, self.fanqie_manager.get_book_detail(album_id)
                if platform == "懒人听书":
                    return book, self.lrts_manager.get_book_detail(album_id)
                if platform == "起点听书":
                    return book, self.search_manager.get_qidian_detail(album_id)
            except Exception as exc:
                print(f"⚠️ {platform} 详情补全失败 {album_id}: {exc}")
            return book, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_fetch, book) for book in targets]
            for future in as_completed(futures):
                book, detail = future.result()
                if not isinstance(detail, dict):
                    continue
                for key in ("title", "author", "cover", "description", "category", "status"):
                    value = detail.get(key)
                    if value and (not book.get(key) or str(book.get(key)).strip() in ("未知", "未知作者")):
                        book[key] = value
                episodes = self._pick_episode_count(detail)
                if episodes > 0:
                    book["episodes"] = episodes
    
    def set_cookie(self, platform: str, cookie: str):
        """设置特定平台的Cookie"""
        self.clear_search_cache()
        if platform == '喜马拉雅' or platform == 'xmly':
            if hasattr(self.ximalaya_manager, 'set_cookie'):
                if isinstance(cookie, dict):
                    cookie = '; '.join([f"{name}={value}" for name, value in cookie.items()])
                self.ximalaya_manager.set_cookie(cookie)
                print(f"🍪 已更新喜马拉雅Cookie: {len(str(cookie))} 字符")
        elif platform == '懒人听书' or platform == 'lrts':
            if hasattr(self.lrts_manager, 'set_cookie'):
                self.lrts_manager.set_cookie(cookie)
                print(f"🍪 已更新懒人听书Cookie: {len(cookie)} 字符")
        elif platform == '起点听书' or platform == 'qidian':
            if hasattr(self.search_manager, 'set_cookie'):
                self.search_manager.set_cookie('起点听书', cookie)
                print(f"🍪 已更新起点听书Cookie: {len(cookie)} 字符")
        elif platform == '蜻蜓FM' or platform == 'qtfm':
            if isinstance(cookie, dict):
                access_token = cookie.get('access_token', '')
                qingting_id = cookie.get('qingting_id', '')
                if access_token and qingting_id:
                    self.qtfm_manager.set_auth_info(access_token, qingting_id)
                    self.qtfm_manager.get_user_profile()
                else:
                    print("🎧 蜻蜓FM登录信息不完整")
            elif isinstance(cookie, str):
                self.qtfm_manager.set_cookie(cookie)
                print(f"🍪 已更新蜻蜓FMCookie: {len(cookie)} 字符")
            else:
                print("🎧 蜻蜓FM Cookie格式错误")
        elif platform == '酷我听书' or platform == 'kuwo':
            print("🎵 酷我听书使用授权版内置Cookie，无需设置登录Cookie")
        elif platform == '网易云听书' or platform == 'netease':
            if isinstance(cookie, dict):
                cookie = '; '.join([f"{name}={value}" for name, value in cookie.items()])
            self.netease_manager.set_cookie(cookie)
            print(f"🍪 已更新网易云听书Cookie: {len(str(cookie))} 字符")
        elif platform == '荔枝FM' or platform == 'lizhi':
            print("🍥 荔枝FM公开播客无需设置Cookie")

    def set_ximalaya_mobile_credentials(self, credentials):
        """Update premium-audio credentials without touching browser sessions."""
        self.ximalaya_manager.set_mobile_credentials(credentials)
        nested = getattr(self, 'search_manager', None)
        if nested and hasattr(nested, 'set_ximalaya_mobile_credentials'):
            nested.set_ximalaya_mobile_credentials(credentials)
    
    def _normalize_search_books(self, books, platform: str) -> List[Dict]:
        normalized = []
        converters = {
            '喜马拉雅': self._convert_xmly_book_to_dict,
            '懒人听书': self._convert_lrts_book_to_dict,
            '番茄畅听': self._convert_fanqie_book_to_dict,
        }
        for book in books or []:
            if isinstance(book, dict):
                normalized.append(self._ensure_book_fields(dict(book), platform))
            elif platform in converters:
                normalized.append(converters[platform](book, platform))
        return normalized

    @staticmethod
    def _normalize_search_title(value: Any) -> str:
        text = unicodedata.normalize('NFKC', str(value or '')).casefold()
        return re.sub(r'[\W_]+', '', text, flags=re.UNICODE)

    @classmethod
    def _dedupe_search_results(cls, books: List[Dict]) -> List[Dict]:
        """Deduplicate provider rows while retaining the strongest metrics."""
        deduped = []
        positions = {}
        for book in books or []:
            if not isinstance(book, dict):
                continue
            platform = str(book.get("platform") or "")
            item_id = str(book.get("id") or book.get("album_id") or book.get("book_id") or "")
            fallback = (
                cls._normalize_search_title(book.get("title")),
                cls._normalize_search_title(book.get("author")),
            )
            key = (platform, "id", item_id) if item_id else (platform, "title", *fallback)
            if key not in positions:
                positions[key] = len(deduped)
                deduped.append(book)
                continue

            existing = deduped[positions[key]]
            candidate_popularity = cls._pick_popularity(book)
            if candidate_popularity > cls._pick_popularity(existing):
                existing["plays"] = candidate_popularity
            for field in (
                "author", "cover", "episodes", "status", "description", "category", "tags",
            ):
                if existing.get(field) in (None, "", 0, [], "未知", "未知作者") and book.get(field):
                    existing[field] = book[field]
        return deduped

    @classmethod
    def _rank_search_results(cls, keyword: str, books: List[Dict]) -> List[Dict]:
        """Rank relevant albums by provider popularity, then title closeness.

        Clearly matching titles always stay ahead of unrelated popular albums.
        The provider order remains the final tie-breaker when metrics are absent.
        """
        books = cls._dedupe_search_results(list(books or []))
        query = cls._normalize_search_title(keyword)
        if not query or len(books or []) < 2:
            return list(books or [])

        popularities = [cls._pick_popularity(book or {}) for book in books or []]
        max_popularity_log = max((math.log1p(value) for value in popularities), default=0.0)

        def sort_key(index_and_book):
            index, book = index_and_book
            title = cls._normalize_search_title((book or {}).get('title'))
            if not title:
                return 2, 0.0, index
            if title == query:
                relevance = 1.0
                group = 0
            elif title.startswith(query):
                relevance = 0.95
                group = 0
            elif query in title:
                relevance = 0.90
                group = 0
            elif title in query:
                relevance = 0.85
                group = 0
            else:
                relevance = SequenceMatcher(None, query, title).ratio()
                group = 1 if relevance >= 0.5 else 2
            similarity = SequenceMatcher(None, query, title).ratio()
            popularity = cls._pick_popularity(book or {})
            popularity_score = (
                math.log1p(popularity) / max_popularity_log
                if popularity > 0 and max_popularity_log > 0
                else 0.0
            )
            # Within genuinely relevant results, popularity is the stronger
            # signal. Relevance groups prevent a viral unrelated album winning.
            score = popularity_score * 0.65 + relevance * 0.25 + similarity * 0.10
            return group, -score, index

        ranked = sorted(enumerate(books or []), key=sort_key)
        return [book for _index, book in ranked]

    def _search_platform(self, keyword: str, platform: str) -> List[Dict]:
        started = time.monotonic()
        with log_context(platform=platform, operation="搜索", query=keyword):
            results = self._search_platform_impl(keyword, platform)
            log_event(
                "INFO" if results else "WARN",
                "平台搜索完成" if results else "平台搜索无结果",
                results=len(results),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return results

    def _search_platform_impl(self, keyword: str, platform: str) -> List[Dict]:
        """Run one interactive search request without synchronous detail enrichment."""
        try:
            if platform == '喜马拉雅':
                books = self.ximalaya_manager.search_albums(
                    keyword, page=1, page_size=self.SEARCH_RESULT_LIMITS[platform], max_pages=1
                )
            elif platform == '懒人听书':
                books = self.lrts_manager.search_books(keyword, limit=self.SEARCH_RESULT_LIMITS[platform])
            elif platform == '番茄畅听':
                books = self.fanqie_manager.search_books(
                    keyword, max_pages=self.SEARCH_PAGE_LIMITS[platform]
                )
            elif platform == '番茄听书':
                books = self.fanqie_tingshu_manager.search_books(
                    keyword, max_pages=self.SEARCH_PAGE_LIMITS[platform], enrich_covers=False
                )
            elif platform == '七猫听书':
                books = self.qimao_manager.search_books(keyword, max_pages=1)
            elif platform == '酷我听书':
                books = self.kuwo_manager.search_books(keyword, limit=self.SEARCH_RESULT_LIMITS[platform])
            elif platform == '起点听书':
                books = self.search_manager.search_qidian(
                    keyword, page_size=self.SEARCH_RESULT_LIMITS[platform], enrich_details=False
                )
            elif platform == '蜻蜓FM':
                books = self.qtfm_manager.search_books(
                    keyword, max_pages=self.SEARCH_PAGE_LIMITS[platform]
                )
            elif platform == '网易云听书':
                books = self.netease_manager.search_books(keyword, limit=self.SEARCH_RESULT_LIMITS[platform])
            elif platform == '荔枝FM':
                books = self.lizhi_manager.search_books(keyword, limit=20)
            elif platform == '云听FM':
                value = str(keyword or '').strip()
                is_link_or_id = value.startswith(('http://', 'https://')) or 'radio.cn' in value or (
                    value.isdigit() and len(value) >= 6
                )
                if is_link_or_id:
                    detail = self.yuntu_manager.search_by_link_or_id(value)
                    books = [detail] if detail else []
                else:
                    books = self.yuntu_manager.search_books(value, page=0, page_size=20)
            else:
                return []
            results = self._normalize_search_books(books, platform)
            return results
        except Exception as exc:
            print(f"❌ 搜索请求失败: {exc}")
            return []

    def _search_platform_cached(self, keyword: str, platform: str) -> List[Dict]:
        key = (platform, str(keyword or '').strip().casefold())
        now = time.monotonic()
        with self._keyword_search_cache_lock:
            cached = self._keyword_search_cache.get(key)
            if cached and now - cached[0] < self.SEARCH_CACHE_TTL:
                return [dict(item) for item in cached[1]]
        results = self._search_platform(keyword, platform)
        with self._keyword_search_cache_lock:
            stale = [cache_key for cache_key, value in self._keyword_search_cache.items() if now - value[0] >= self.SEARCH_CACHE_TTL]
            for cache_key in stale:
                self._keyword_search_cache.pop(cache_key, None)
            if len(self._keyword_search_cache) >= self.SEARCH_CACHE_MAX_ITEMS:
                oldest = min(self._keyword_search_cache, key=lambda cache_key: self._keyword_search_cache[cache_key][0])
                self._keyword_search_cache.pop(oldest, None)
            self._keyword_search_cache[key] = (now, [dict(item) for item in results])
        return results

    def search_books(self, keyword: str, platform: str = 'all') -> List[Dict]:
        started = time.monotonic()
        scope_platform = "全部平台" if platform == "all" else platform
        with log_context(platform=scope_platform, operation="搜索", query=str(keyword or '').strip()):
            results = self._search_books(keyword, platform)
            log_event(
                "INFO" if results else "WARN",
                "聚合搜索完成" if platform == "all" else "搜索完成",
                results=len(results),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return results

    def _search_books(self, keyword: str, platform: str = 'all') -> List[Dict]:
        """Search one platform or aggregate first-page results concurrently."""
        keyword_stripped = str(keyword or '').strip()
        parsed_ximalaya_id = (
            parse_ximalaya_album_id(keyword_stripped)
            if platform in ('喜马拉雅', 'all')
            else None
        )
        parsed_tingshu_id = parse_book_id(keyword_stripped) if platform in ('番茄听书', 'all') else None
        parsed_qimao_id = parse_qimao_book_id(keyword_stripped) if platform in ('七猫听书', 'all') else None

        # A Ximalaya share URL identifies one exact album. Do not feed the full
        # URL to keyword search or fan it out to unrelated platforms.
        if parsed_ximalaya_id and not keyword_stripped.isdigit():
            results = self.search_by_id(parsed_ximalaya_id, '喜马拉雅')
            for album in results:
                album['requested_album_id'] = parsed_ximalaya_id
                album['source_url'] = keyword_stripped
            return results

        is_id_search = keyword_stripped.isdigit() or (
            platform == '番茄听书' and parsed_tingshu_id is not None
        ) or (
            platform == '七猫听书' and parsed_qimao_id is not None
        )
        if is_id_search:
            if platform == '七猫听书' and parsed_qimao_id:
                keyword_stripped = parsed_qimao_id
            elif platform == '番茄听书' and parsed_tingshu_id:
                keyword_stripped = parsed_tingshu_id
            return self.search_by_id(keyword_stripped, platform)

        if platform != 'all':
            results = self._search_platform_cached(keyword_stripped, platform)
            return self._rank_search_results(keyword_stripped, results)

        # 云听关键词能力不稳定，聚合搜索不调它；单独选择云听时仍保留链接/ID能力。
        grouped = {}
        with ThreadPoolExecutor(max_workers=min(8, len(self.KEYWORD_SEARCH_PLATFORMS))) as pool:
            futures = {
                pool.submit(self._search_platform_cached, keyword_stripped, item): item
                for item in self.KEYWORD_SEARCH_PLATFORMS
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    grouped[item] = future.result()
                except Exception as exc:
                    print(f"❌ {item} 聚合搜索失败: {exc}")
                    grouped[item] = []
        results = [book for item in self.KEYWORD_SEARCH_PLATFORMS for book in grouped.get(item, [])]
        return self._rank_search_results(keyword_stripped, results)
    
    def search_by_id(self, book_id: str, platform: str = 'all') -> List[Dict]:
        """通过ID搜索书籍（支持喜马拉雅、懒人听书、番茄畅听）"""
        if platform == 'all':
            id_platforms = (
                '喜马拉雅', '懒人听书', '番茄畅听', '番茄听书',
                '七猫听书', '酷我听书', '蜻蜓FM', '网易云听书',
            )
            grouped = {}
            with ThreadPoolExecutor(max_workers=len(id_platforms)) as pool:
                futures = {pool.submit(self.search_by_id, book_id, item): item for item in id_platforms}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        grouped[item] = future.result()
                    except Exception:
                        grouped[item] = []
            return [book for item in id_platforms for book in grouped.get(item, [])]

        results = []
        
        print(f"🎯 开始ID搜索: {book_id}, 平台: {platform}")
        
        try:
            if platform == 'all' or platform == '喜马拉雅':
                print(f"🔍 喜马拉雅ID搜索: {book_id}")
                try:
                    album_info = self.get_ximalaya_album_by_id(book_id)
                    if album_info:
                        results.append(album_info)
                        print(f"✅ 喜马拉雅找到专辑: {album_info.get('title', '未知')}")
                    else:
                        print(f"❌ 喜马拉雅ID搜索无结果")
                except Exception as e:
                    print(f"❌ 喜马拉雅ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '懒人听书':
                print(f"🔍 懒人听书ID搜索: {book_id}")
                try:
                    book_info = self.get_lrts_book_by_id(book_id)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 懒人听书找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 懒人听书ID搜索无结果")
                except Exception as e:
                    print(f"❌ 懒人听书ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '番茄畅听':
                print(f"🔍 番茄畅听ID搜索: {book_id}")
                try:
                    book_info = self.get_fanqie_book_by_id(book_id)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 番茄畅听找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 番茄畅听ID搜索无结果")
                except Exception as e:
                    print(f"❌ 番茄畅听ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '番茄听书':
                print(f"🔍 番茄听书ID搜索: {book_id}")
                try:
                    bid = parse_book_id(book_id) or str(book_id).strip()
                    book_info = self.fanqie_tingshu_manager.get_book_detail(bid)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 番茄听书找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 番茄听书ID搜索无结果")
                except Exception as e:
                    print(f"❌ 番茄听书ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '七猫听书':
                print(f"🔍 七猫听书ID搜索: {book_id}")
                try:
                    bid = parse_qimao_book_id(book_id) or str(book_id).strip()
                    book_info = self.qimao_manager.get_book_detail(bid)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 七猫听书找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 七猫听书ID搜索无结果")
                except Exception as e:
                    print(f"❌ 七猫听书ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '酷我听书':
                print(f"🔍 酷我听书ID搜索: {book_id}")
                try:
                    book_info = self.get_kuwo_book_by_id(book_id)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 酷我听书找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 酷我听书ID搜索无结果")
                except Exception as e:
                    print(f"❌ 酷我听书ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '蜻蜓FM':
                print(f"🎧 蜻蜓FMID搜索: {book_id}")
                try:
                    book_info = self.get_qtfm_book_by_id(book_id)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 蜻蜓FM找到书籍: {book_info.get('title', '未知')}")
                    else:
                        print(f"❌ 蜻蜓FMID搜索无结果")
                except Exception as e:
                    print(f"❌ 蜻蜓FMID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '网易云听书':
                print(f"🔍 网易云听书ID搜索: {book_id}")
                try:
                    book_info = self.netease_manager.get_book_detail(book_id)
                    if book_info:
                        results.append(book_info)
                        print(f"✅ 网易云听书找到播客: {book_info.get('title', '未知')}")
                    else:
                        print("❌ 网易云听书ID搜索无结果")
                except Exception as e:
                    print(f"❌ 网易云听书ID搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == '云听FM':
                try:
                    book_info = self.yuntu_manager.search_by_link_or_id(str(book_id))
                    if book_info:
                        results.append(self._ensure_book_fields(dict(book_info), '云听FM'))
                except Exception as e:
                    print(f"❌ 云听FM ID搜索失败: {e}")
            
            print(f"🎯 ID搜索完成，共找到 {len(results)} 本书")
            return results
            
        except Exception as e:
            print(f"❌ ID搜索异常: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_ximalaya_album_by_id(self, album_id: str) -> Optional[Dict]:
        """通过ID获取喜马拉雅专辑详情"""
        try:
            requested_id = parse_ximalaya_album_id(album_id) or str(album_id or '').strip()
            if not requested_id.isdigit():
                print(f"❌ 喜马拉雅ID格式无效: {album_id}")
                return None
            print(f"🔍 喜马拉雅ID搜索: {requested_id}")
            
            # 直接调用喜马拉雅搜索API，但是用ID作为关键词
            # 这样可以得到完整的书籍信息格式
            search_results = self.ximalaya_manager.search_albums(requested_id, page=1, page_size=20)
            
            if search_results:
                # 从搜索结果中找到匹配的专辑
                for album in search_results:
                    result_id = str(album.get('id') or album.get('album_id') or '').strip()
                    if result_id == requested_id:
                        print(f"✅ 喜马拉雅找到匹配专辑: {album.get('title', '未知')}")
                        album['id'] = requested_id
                        album['requested_album_id'] = requested_id
                        return album
                result_ids = [
                    str(album.get('id') or album.get('album_id') or '').strip()
                    for album in search_results
                ]
                print(
                    f"⚠️ 喜马拉雅ID搜索只返回了非精确结果 {result_ids}，"
                    f"不会替代请求的专辑 {requested_id}"
                )
            
            # 搜索没有精确命中时，只允许按原 ID 调详情接口，绝不返回同名首条。
            print(f"🔍 搜索未精确命中，尝试直接获取专辑详情: {requested_id}")
            album_info = self.ximalaya_manager.get_album_detail(requested_id)
            
            if album_info:
                print(f"✅ 喜马拉雅专辑详情获取成功: {album_info.get('title', '未知')}")
                album_info['id'] = requested_id
                album_info['requested_album_id'] = requested_id
                album_info['platform'] = '喜马拉雅'
                return album_info
            else:
                print(f"❌ 喜马拉雅专辑详情获取失败")
                return None
            
        except Exception as e:
            print(f"❌ 获取喜马拉雅专辑详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_lrts_book_by_id(self, book_id: str) -> Optional[Dict]:
        """通过ID获取懒人听书书籍详情"""
        try:
            print(f"🔍 懒人听书ID搜索: {book_id}")
            
            # 确保懒人听书Cookie已设置
            if self.cookie_manager:
                lrts_cookie = self.cookie_manager.get_cookie('lrts')
                if lrts_cookie:
                    self.lrts_manager.set_cookie(lrts_cookie)
                    print(f"🍪 懒人听书Cookie已设置: {len(lrts_cookie)} 字符")
                else:
                    print(f"⚠️ 未找到懒人听书Cookie，API可能无法获取书籍详情")
            
            # 懒人听书直接调用详情API，不需要搜索
            print(f"🔍 直接调用懒人听书书籍详情API: {book_id}")
            book_info = self.lrts_manager.get_book_detail(book_id)
            
            if book_info:
                print(f"✅ 懒人听书书籍详情获取成功: {book_info.get('title', '未知')}")
                return book_info
            else:
                print(f"❌ 懒人听书书籍详情获取失败")
                return None
            
        except Exception as e:
            print(f"❌ 获取懒人听书书籍详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_fanqie_book_by_id(self, book_id: str) -> Optional[Dict]:
        """通过ID获取番茄畅听书籍详情"""
        try:
            print(f"🔍 番茄畅听ID搜索: {book_id}")
            
            # 番茄畅听的搜索API不支持精确ID匹配，直接调用详情API
            print(f"🔍 直接调用番茄畅听书籍详情API: {book_id}")
            book_info = self.fanqie_manager.get_book_detail(book_id)
            
            if book_info:
                print(f"✅ 番茄畅听书籍详情获取成功: {book_info.get('title', '未知')}")
                return book_info
            else:
                print(f"❌ 番茄畅听书籍详情获取失败")
                return None
            
        except Exception as e:
            print(f"❌ 获取番茄畅听书籍详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_qtfm_book_by_id(self, book_id: str) -> Optional[Dict]:
        """通过ID获取蜻蜓FM书籍详情"""
        try:
            print(f"🎧 蜻蜓FMID搜索: {book_id}")
            
            # 蜻蜓FM直接调用详情API
            print(f"🔍 直接调用蜻蜓FM书籍详情API: {book_id}")
            book_info = self.qtfm_manager.get_book_details(book_id)
            
            if book_info:
                print(f"✅ 蜻蜓FM书籍详情获取成功: {book_info.get('title', '未知')}")
                return book_info
            else:
                print(f"❌ 蜻蜓FM书籍详情获取失败")
                return None
            
        except Exception as e:
            print(f"❌ 获取蜻蜓FM书籍详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_kuwo_book_by_id(self, book_id: str) -> Optional[Dict]:
        """通过ID获取酷我听书书籍详情"""
        try:
            print(f"🔍 酷我听书ID搜索: {book_id}")
            
            # 酷我听书直接调用详情API
            print(f"🔍 直接调用酷我听书书籍详情API: {book_id}")
            book_info = self.kuwo_manager.get_book_detail(book_id)
            
            if book_info:
                print(f"✅ 酷我听书书籍详情获取成功: {book_info.get('title', '未知')}")
                return book_info
            else:
                print(f"❌ 酷我听书书籍详情获取失败")
                return None
            
        except Exception as e:
            print(f"❌ 获取酷我听书书籍详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_album_detail(self, album_id: str, platform: str) -> Optional[Dict]:
        started = time.monotonic()
        with log_context(platform=platform, operation="专辑详情", album_id=album_id):
            detail = self._get_album_detail(album_id, platform)
            log_event(
                "INFO" if detail else "WARN",
                "专辑详情加载完成" if detail else "未获取到专辑详情",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return detail

    def _get_album_detail(self, album_id: str, platform: str) -> Optional[Dict]:
        """获取专辑详情"""
        try:
            # 🔧 统一platform处理：支持英文代码和中文名称
            if platform in ['喜马拉雅', 'ximalaya']:
                return self.ximalaya_manager.get_album_detail(album_id)
            elif platform in ['懒人听书', 'lrts']:
                return self.lrts_manager.get_book_detail(album_id)
            elif platform in ['番茄畅听', 'fanqie']:
                return self.fanqie_manager.get_book_detail(album_id)
            elif platform in ['番茄听书', 'fanqie_tingshu']:
                return self.fanqie_tingshu_manager.get_book_detail(album_id)
            elif platform in ['七猫听书', 'qimao']:
                return self.qimao_manager.get_book_detail(album_id)
            elif platform in ['蜻蜓FM', 'qtfm']:
                return self.qtfm_manager.get_book_details(album_id)
            elif platform in ['起点听书', 'qidian']:
                # 获取起点有声书详情
                return self.search_manager.get_album_detail(album_id, platform)
            elif platform in ['酷我听书', 'kuwo']:
                return self.kuwo_manager.get_book_detail(album_id)
            elif platform in ['网易云听书', 'netease']:
                return self.netease_manager.get_book_detail(album_id)
            elif platform in ['荔枝FM', 'lizhi']:
                return self.lizhi_manager.get_book_detail(album_id)
            elif platform in ['云听FM', 'yuntu']:
                return self.yuntu_manager.get_album_detail(album_id)
            else:
                return None
        except Exception as e:
            print(f"❌ 获取专辑详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _cached_full_chapters(self, cache_key, loader) -> List[Dict]:
        now = time.monotonic()
        with self._chapter_list_cache_lock:
            cached = self._chapter_list_cache.get(cache_key)
            if cached and now - cached[0] < 300:
                return [dict(item) for item in cached[1]]
        chapters = list(loader() or [])
        with self._chapter_list_cache_lock:
            if len(self._chapter_list_cache) >= 16:
                oldest = min(self._chapter_list_cache, key=lambda key: self._chapter_list_cache[key][0])
                self._chapter_list_cache.pop(oldest, None)
            self._chapter_list_cache[cache_key] = (now, [dict(item) for item in chapters])
        return chapters

    def get_album_chapters_page(
        self,
        album_id: str,
        platform: str,
        page: int = 1,
        page_size: int = 100,
        voice: Optional[Dict] = None,
    ):
        started = time.monotonic()
        with log_context(
            platform=platform,
            operation="章节目录",
            album_id=album_id,
            page=page,
            page_size=page_size,
        ):
            chapters, total = self._get_album_chapters_page(
                album_id, platform, page=page, page_size=page_size, voice=voice
            )
            log_event(
                "INFO" if chapters else "WARN",
                "章节分页加载完成" if chapters else "当前页没有章节",
                chapters=len(chapters),
                total=total or "unknown",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return chapters, total

    def _get_album_chapters_page(
        self,
        album_id: str,
        platform: str,
        page: int = 1,
        page_size: int = 100,
        voice: Optional[Dict] = None,
    ):
        """Return one UI page plus an exact total when the provider exposes only a full directory."""
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 100), 200))
        offset = (page - 1) * page_size
        voice_key = str((voice or {}).get('id') or (voice or {}).get('voice_id') or (voice or {}).get('name') or '')

        def sliced(loader):
            all_chapters = self._cached_full_chapters((platform, str(album_id), voice_key), loader)
            return all_chapters[offset:offset + page_size], len(all_chapters)

        if platform in ['喜马拉雅', 'ximalaya']:
            chapters, exact_total = self.ximalaya_manager.get_album_chapters_page(album_id, page, page_size)
            return list(chapters or [])[:page_size], max(0, int(exact_total or 0))
        elif platform in ['懒人听书', 'lrts']:
            return sliced(lambda: self.lrts_manager.get_chapters(album_id))
        elif platform in ['番茄畅听', 'fanqie']:
            chapters = self.fanqie_manager.get_chapters_for_voice(album_id, voice, page, page_size) if voice else self.fanqie_manager.get_chapters(album_id, page, page_size)
        elif platform in ['番茄听书', 'fanqie_tingshu']:
            return sliced(lambda: self.fanqie_tingshu_manager.get_chapters(album_id, voice) if voice else [])
        elif platform in ['七猫听书', 'qimao']:
            return sliced(lambda: self.qimao_manager.get_chapters(album_id, voice) if voice else self.qimao_manager.get_chapters(album_id))
        elif platform in ['蜻蜓FM', 'qtfm']:
            chapters = self.qtfm_manager.get_chapters(album_id, version=None, page=page, page_size=page_size)
        elif platform in ['云听FM', 'yuntu']:
            chapters = self.yuntu_manager.get_chapters(album_id, page=page, page_size=page_size)
        elif platform in ['起点听书', 'qidian']:
            return sliced(lambda: self.search_manager.get_album_chapters(album_id, platform))
        elif platform in ['酷我听书', 'kuwo']:
            chapters = self.kuwo_manager.get_chapters(album_id, page=page, page_size=page_size)
        elif platform in ['网易云听书', 'netease']:
            chapters, exact_total = self.netease_manager.get_chapters_page(
                album_id, page=page, page_size=page_size
            )
            return list(chapters or [])[:page_size], max(0, int(exact_total or 0))
        elif platform in ['荔枝FM', 'lizhi']:
            chapters = self.lizhi_manager.get_chapters(album_id, page=page, page_size=page_size, max_pages=1)
        else:
            return [], 0

        chapters = list(chapters or [])[:page_size]
        exact_total = offset + len(chapters) if len(chapters) < page_size else 0
        return chapters, exact_total
    
    def get_album_chapters(self, album_id: str, platform: str) -> List[Dict]:
        started = time.monotonic()
        with log_context(platform=platform, operation="完整目录", album_id=album_id):
            chapters = self._get_album_chapters(album_id, platform)
            log_event(
                "INFO" if chapters else "ERROR",
                "专辑目录加载完成" if chapters else "专辑目录加载失败",
                chapters=len(chapters),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return chapters

    def _get_album_chapters(self, album_id: str, platform: str) -> List[Dict]:
        """获取专辑章节列表"""
        try:
            chapters = []
            # 🔧 统一platform处理：支持英文代码和中文名称
            if platform in ['喜马拉雅', 'ximalaya']:
                # 喜马拉雅管理器在 page_size > 1000 时会走大页/并发加载，避免 50 条一页串行拉取。
                chapters = self.ximalaya_manager.get_album_chapters(album_id, 1, 2000)
                if not chapters:
                    log_event("INFO", "大页加载不可用，改用稳定分页扫描", scan_page_size=200)
                    page = 1
                    page_size = 200
                    verbose = platform_verbose_enabled()
                    while True:
                        page_chapters = self.ximalaya_manager.get_album_chapters(
                            album_id,
                            page,
                            page_size,
                            log_summary=verbose,
                        )
                        if not page_chapters:
                            break
                        chapters.extend(page_chapters)
                        is_last_page = len(page_chapters) < page_size
                        if verbose or page == 1 or page % 10 == 0 or is_last_page:
                            log_event(
                                "INFO",
                                "目录分页扫描进度",
                                current_page=page,
                                page_chapters=len(page_chapters),
                                loaded=len(chapters),
                            )
                        if is_last_page:
                            break
                        page += 1
                        if page > 200:
                            log_event("WARN", "目录分页扫描达到安全页数上限", max_pages=200)
                            break
                        
            elif platform in ['懒人听书', 'lrts']:
                chapters = self.lrts_manager.get_chapters(album_id)
            elif platform in ['番茄畅听', 'fanqie']:
                # 番茄畅听API一次返回所有章节，获取全部章节
                chapters = self.fanqie_manager.get_chapters(album_id, page=1, page_size=10000)
            elif platform in ['番茄听书', 'fanqie_tingshu']:
                voice = getattr(self.fanqie_tingshu_manager, 'current_voice_config', None)
                if not voice:
                    _, _, voice = self.fanqie_tingshu_manager.load_chapters_with_voices(album_id)
                    self.fanqie_tingshu_manager.current_voice_config = voice
                chapters = self.fanqie_tingshu_manager.get_chapters(album_id, voice) if voice else []
            elif platform in ['七猫听书', 'qimao']:
                chapters = self.qimao_manager.get_chapters(album_id)
            elif platform in ['蜻蜓FM', 'qtfm']:
                # 蜻蜓FM章节获取 - 新API可能支持分页
                # 先尝试一次性获取所有章节（使用大page_size）
                chapters = self.qtfm_manager.get_chapters(album_id, version=None, page=1, page_size=10000)
                
                # 如果获取的章节数量很多，检查是否可能还有更多章节
                # 获取书籍详情以获取总章节数（如果API提供）
                book_detail = self.qtfm_manager.get_book_details(album_id)
                total_programs = 0
                if book_detail:
                    total_programs = book_detail.get('total_programs', 0)
                
                # 如果获取的章节数少于总章节数，尝试分页获取
                if total_programs > 0 and len(chapters) < total_programs:
                    print(f"⚠️ 检测到章节数不完整: 已获取 {len(chapters)}/{total_programs}，尝试分页获取...")
                    all_chapters = list(chapters)  # 保存已获取的章节
                    page = 2
                    page_size = 100
                    
                    while len(all_chapters) < total_programs:
                        page_chapters = self.qtfm_manager.get_chapters(album_id, version=None, page=page, page_size=page_size)
                        if not page_chapters or len(page_chapters) == 0:
                            break
                        all_chapters.extend(page_chapters)
                        if platform_verbose_enabled() or page % 10 == 0:
                            log_event("INFO", "目录分页扫描进度", current_page=page, loaded=len(all_chapters))
                        
                        # 如果获取的章节少于请求的数量，说明已经到最后一页
                        if len(page_chapters) < page_size:
                            break
                        
                        page += 1
                        # 防止无限循环
                        if page > 1000:  # 最多1000页，支持最多100000集
                            print(f"⚠️ 达到最大页数限制，停止获取")
                            break
                        
                        # 添加短暂延迟避免请求过于频繁
                        import time
                        time.sleep(0.2)
                    
                    chapters = all_chapters
                elif not chapters:
                    print(f"⚠️ 蜻蜓FM获取章节失败，尝试使用version参数")
                    # 如果失败，尝试获取version后再获取章节
                    if book_detail:
                        version = book_detail.get('version')
                        if version:
                            chapters = self.qtfm_manager.get_chapters(album_id, version=version, page=1, page_size=10000)
                            if not chapters:
                                print(f"❌ 使用version参数仍然失败")
                    else:
                        print(f"❌ 无法获取书籍详情，章节获取失败")
            elif platform in ['云听FM', 'yuntu']:
                # 云听FM尝试获取所有章节
                # 先尝试一次性获取大量章节
                chapters = self.yuntu_manager.get_chapters(album_id, page=1, page_size=10000)
                
                # 如果获取的章节数量很少，尝试分页获取
                if len(chapters) < 100:
                    print(f"☁️ 云听FM首次获取到 {len(chapters)} 个章节，尝试分页获取更多...")
                    all_chapters = []
                    page = 1
                    page_size = 100  # 每页获取100个章节
                    
                    while True:
                        page_chapters = self.yuntu_manager.get_chapters(album_id, page, page_size)
                        if not page_chapters:
                            break
                        all_chapters.extend(page_chapters)
                        
                        if platform_verbose_enabled() or page % 10 == 0:
                            log_event(
                                "INFO",
                                "目录分页扫描进度",
                                current_page=page,
                                page_chapters=len(page_chapters),
                                loaded=len(all_chapters),
                            )
                        
                        # 如果获取的章节少于请求的数量，说明已经到最后一页
                        if len(page_chapters) < page_size:
                            break
                        page += 1
                        # 防止无限循环
                        if page > 100:  # 最多获取100页，支持最多10000集
                            break
                        # 添加短暂延迟避免请求过于频繁
                        import time
                        time.sleep(0.1)
                    
                    if len(all_chapters) > len(chapters):
                        chapters = all_chapters
            elif platform in ['起点听书', 'qidian']:
                # 起点听书章节加载
                chapters = self.search_manager.get_album_chapters(album_id, platform)
            elif platform in ['酷我听书', 'kuwo']:
                # 酷我听书获取全部章节
                chapters = self.kuwo_manager.get_chapters(album_id, page=1, page_size=10000)
            elif platform in ['网易云听书', 'netease']:
                chapters = self.netease_manager.get_all_chapters(album_id)
            elif platform in ['荔枝FM', 'lizhi']:
                chapters = self.lizhi_manager.get_chapters(album_id, page=1, page_size=500)
            else:
                return []
            
            return chapters
            
        except Exception as e:
            print(f"❌ 获取章节失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_audio_urls(self, track_id: str, platform: str, book_id: Optional[str] = None, voice_name: Optional[str] = None) -> Dict[str, str]:
        started = time.monotonic()
        with log_context(
            platform=platform,
            operation="音频地址",
            album_id=book_id,
            track_id=track_id,
        ):
            urls = self._get_audio_urls(track_id, platform, book_id, voice_name)
            available = bool(urls and any(urls.values()))
            log_event(
                "INFO" if available else "WARN",
                "音频地址解析完成" if available else "未解析到音频地址",
                variants=len(urls or {}),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return urls

    def _get_audio_urls(self, track_id: str, platform: str, book_id: Optional[str] = None, voice_name: Optional[str] = None) -> Dict[str, str]:
        """获取音频URL"""
        try:
            # 🔧 统一platform处理：支持英文代码和中文名称
            if platform in ['喜马拉雅', 'ximalaya']:
                return self.ximalaya_manager.get_audio_urls(track_id)
            elif platform in ['懒人听书', 'lrts']:
                url = self.lrts_manager.get_audio_url(book_id, track_id) if book_id else ''
                return {'default': url or ''}
            elif platform in ['番茄畅听', 'fanqie']:
                # 使用指定的音色，如果没有指定则使用默认的"无损真人录制"（传入 book_id 以便 AI 音色使用官方 playinfo API）
                if not voice_name:
                    voice_name = "无损真人录制"
                url = self.fanqie_manager.get_audio_url(track_id, voice_name, book_id)
                return {'default': url or ''}
            elif platform in ['番茄听书', 'fanqie_tingshu']:
                voice_cfg = getattr(self.fanqie_tingshu_manager, 'current_voice_config', None)
                if not voice_cfg and book_id:
                    voice_cfg = self.fanqie_tingshu_manager.get_voice_by_name(
                        book_id, voice_name or ''
                    ) or (self.fanqie_tingshu_manager.fetch_voices(book_id) or [None])[0]
                if voice_cfg:
                    path_or_url = self.fanqie_tingshu_manager.prepare_playback(track_id, voice_cfg)
                    return {'default': path_or_url or ''}
                return {'default': ''}
            elif platform in ['七猫听书', 'qimao']:
                voice = getattr(self.qimao_manager, 'current_voice', None)
                path_or_url = self.qimao_manager.prepare_playback(track_id, voice_config=voice)
                return {'default': path_or_url or ''}
            elif platform in ['蜻蜓FM', 'qtfm']:
                # 蜻蜓FM音频URL获取
                url = self.qtfm_manager.get_audio_url(book_id, track_id) if book_id else ''
                return {'default': url or ''}
            elif platform in ['云听FM', 'yuntu']:
                # 云听FM的音频URL需要从章节数据中获取
                # 这里返回空，实际URL在播放时从章节数据的mediaUrl字段获取
                print(f"☁️ 云听FM音频URL将从章节数据中获取")
                return {'default': ''}
            elif platform in ['起点听书', 'qidian']:
                # 🔧 起点听书音频URL获取
                print(f"📖 EnhancedSearchManager.get_audio_urls 路由到起点听书:")
                print(f"   book_id={book_id}, track_id={track_id}")
                return self.search_manager.get_qidian_audio_url(book_id, track_id)
            elif platform in ['酷我听书', 'kuwo']:
                # 酷我听书音频URL获取
                print(f"🎵 EnhancedSearchManager.get_audio_urls 路由到酷我听书:")
                print(f"   track_id={track_id}")
                url = self.kuwo_manager.get_audio_url(track_id, self.current_quality or 'standard')
                return {'default': url or ''}
            elif platform in ['网易云听书', 'netease']:
                print(f"🎧 EnhancedSearchManager.get_audio_urls 路由到网易云听书:")
                print(f"   program_id={track_id}")
                url = self.netease_manager.get_audio_url(track_id, 'exhigh')
                return {'default': url or ''}
            elif platform in ['荔枝FM', 'lizhi']:
                url = self.lizhi_manager.get_audio_url(book_id or "", track_id)
                return {'default': url or ''}
            else:
                return {}
                
        except Exception as e:
            print(f"❌ 获取音频URL失败: {e}")
            return {}
    
    def _convert_xmly_book_to_dict(self, book, platform: str) -> Dict:
        """将喜马拉雅Book对象转换为字典"""
        return {
            'id': getattr(book, 'id', ''),
            'title': getattr(book, 'title', ''),
            'author': getattr(book, 'author', ''),
            'platform': platform,
            'cover': getattr(book, 'cover_url', '') or '',
            'plays': getattr(book, 'play_count', 0),
            'episodes': getattr(book, 'chapter_count', 0),
            'status': '连载中',
            'description': getattr(book, 'description', ''),
            'category': getattr(book, 'category', ''),
            'tags': getattr(book, 'tags', []),
            'created_at': getattr(book, 'created_at', ''),
            'updated_at': getattr(book, 'updated_at', '')
        }
    
    def _convert_lrts_book_to_dict(self, book, platform: str) -> Dict:
        """将懒人听书Book对象转换为字典"""
        return {
            'id': book.book_id,
            'title': book.title,
            'author': book.author,
            'platform': platform,
            'cover': book.cover_url or '',
            'plays': getattr(book, 'play_count', 0),
            'episodes': getattr(book, 'chapter_count', 0),
            'status': '连载中',
            'description': book.description,
            'category': '',
            'tags': [],
            'created_at': '',
            'updated_at': ''
        }
    
    def _convert_fanqie_book_to_dict(self, book, platform: str) -> Dict:
        """将番茄畅听Book对象转换为字典"""
        return {
            'id': book.book_id,
            'title': book.title,
            'author': book.author,
            'platform': platform,
            'cover': book.cover_url or '',
            'plays': getattr(book, 'play_count', 0),
            'episodes': getattr(book, 'chapter_count', 0),
            'status': '连载中',
            'description': book.description,
            'category': '',
            'tags': [],
            'created_at': '',
            'updated_at': ''
        }
