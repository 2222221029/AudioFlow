#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
增强搜索管理器
整合喜马拉雅、懒人听书、番茄畅听、酷我听书的完善API
"""

import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent))

# 暂时使用现有的管理器
from .ximalaya_manager import XimalayaManager
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


class EnhancedSearchManager:
    """增强搜索管理器"""
    
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
        if "plays" not in book:
            book["plays"] = book.get("play_count", 0)
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
    
    def search_books(self, keyword: str, platform: str = 'all') -> List[Dict]:
        """搜索书籍（支持关键词搜索和ID搜索）"""
        results = []
        
        # 判断是否为ID搜索（纯数字，或番茄/七猫分享链接）
        keyword_stripped = keyword.strip()
        parsed_tingshu_id = parse_book_id(keyword_stripped) if platform in ('番茄听书', 'all') else None
        parsed_qimao_id = parse_qimao_book_id(keyword_stripped) if platform in ('七猫听书', 'all') else None
        is_id_search = keyword_stripped.isdigit() or (
            platform == '番茄听书' and parsed_tingshu_id is not None
        ) or (
            platform == '七猫听书' and parsed_qimao_id is not None
        )
        
        if is_id_search:
            if platform == '七猫听书' and parsed_qimao_id:
                id_keyword = parsed_qimao_id
            elif platform == '番茄听书' and parsed_tingshu_id:
                id_keyword = parsed_tingshu_id
            else:
                id_keyword = keyword_stripped
            print(f"🔍 检测到ID搜索模式: {id_keyword}")
            # 直接调用ID搜索，不经过复杂的搜索逻辑
            id_results = self.search_by_id(id_keyword, platform)
            print(f"🎯 ID搜索返回 {len(id_results)} 个结果")
            return id_results
        else:
            print(f"🔍 关键词搜索模式: {keyword}")
        
        try:
            if platform == 'all' or platform == '喜马拉雅':
                print(f"🔍 喜马拉雅搜索: {keyword}")
                try:
                    xmly_books = self.ximalaya_manager.search_albums(keyword, page=1, page_size=50)
                    for book in xmly_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '喜马拉雅'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = 0
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '喜马拉雅')
                            results.append(book)
                        else:
                            # 是对象，需要转换
                            results.append(self._convert_xmly_book_to_dict(book, '喜马拉雅'))
                    print(f"✅ 喜马拉雅找到 {len(xmly_books)} 本书")
                except Exception as e:
                    print(f"❌ 喜马拉雅搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
                    # 继续搜索其他平台
            
            if platform == 'all' or platform == '懒人听书':
                try:
                    lrts_books = self.lrts_manager.search_books(keyword)
                    self._enrich_search_result_details(lrts_books, '懒人听书', limit=4 if platform == 'all' else 10)
                    for book in lrts_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '懒人听书'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = 0
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '懒人听书')
                            results.append(book)
                        else:
                            # 是对象，需要转换
                            results.append(self._convert_lrts_book_to_dict(book, '懒人听书'))
                    print(f"✅ 懒人听书找到 {len(lrts_books)} 本书")
                except Exception as e:
                    print(f"❌ 懒人听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '番茄畅听':
                print(f"🔍 番茄畅听搜索: {keyword}")
                try:
                    fanqie_books = self.fanqie_manager.search_books(keyword, max_pages=3)
                    self._enrich_search_result_details(fanqie_books, '番茄畅听', limit=4 if platform == 'all' else 10)
                    for book in fanqie_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '番茄畅听'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = 0
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '番茄畅听')
                            results.append(book)
                        else:
                            # 是对象，需要转换
                            results.append(self._convert_fanqie_book_to_dict(book, '番茄畅听'))
                    print(f"✅ 番茄畅听找到 {len(fanqie_books)} 本书")
                except Exception as e:
                    print(f"❌ 番茄畅听搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '番茄听书':
                print(f"🔍 番茄听书搜索（书籍+听书）: {keyword}")
                try:
                    tingshu_books = self.fanqie_tingshu_manager.search_books(keyword)
                    for book in tingshu_books:
                        if isinstance(book, dict):
                            book = self._ensure_book_fields(book, '番茄听书')
                            results.append(book)
                    print(f"✅ 番茄听书找到 {len(tingshu_books)} 本")
                except Exception as e:
                    print(f"❌ 番茄听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '七猫听书':
                print(f"🔍 七猫听书搜索（书籍+听书）: {keyword}")
                try:
                    qimao_books = self.qimao_manager.search_books(keyword)
                    for book in qimao_books:
                        if isinstance(book, dict):
                            book = self._ensure_book_fields(book, '七猫听书')
                            results.append(book)
                    print(f"✅ 七猫听书找到 {len(qimao_books)} 本")
                except Exception as e:
                    print(f"❌ 七猫听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 酷我听书搜索
            if platform == 'all' or platform == '酷我听书':
                print(f"🔍 酷我听书搜索: {keyword}")
                try:
                    kuwo_books = self.kuwo_manager.search_books(keyword)
                    for book in kuwo_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '酷我听书'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = 0
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '酷我听书')
                            results.append(book)
                    print(f"✅ 酷我听书找到 {len(kuwo_books)} 本书")
                except Exception as e:
                    print(f"❌ 酷我听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 起点听书搜索
            if platform == 'all' or platform == '起点听书':
                print(f"🔍 起点听书搜索: {keyword}")
                try:
                    qidian_books = self.search_manager.search_qidian(keyword)
                    self._enrich_search_result_details(qidian_books, '起点听书', limit=4 if platform == 'all' else 10)
                    for book in qidian_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '起点听书'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = 0
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '起点听书')
                            results.append(book)
                    print(f"✅ 起点听书找到 {len(qidian_books)} 本书")
                except Exception as e:
                    print(f"❌ 起点听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 蜻蜓FM搜索
            if platform == 'all' or platform == '蜻蜓FM':
                print(f"🎧 蜻蜓FM搜索: {keyword}")
                try:
                    # 使用官方API分页搜索（最多50页，约750个结果）
                    qtfm_books = self.qtfm_manager.search_books(keyword, max_pages=50)
                    for book in qtfm_books:
                        if isinstance(book, dict):
                            # 已经是字典格式，直接添加平台信息
                            book['platform'] = '蜻蜓FM'
                            # 确保必要字段存在
                            if 'cover' not in book:
                                book['cover'] = ''
                            if 'plays' not in book:
                                book['plays'] = book.get('play_count', 0)
                            if 'episodes' not in book:
                                book['episodes'] = 0
                            if 'status' not in book:
                                book['status'] = '连载中'
                            book = self._ensure_book_fields(book, '蜻蜓FM')
                            results.append(book)
                    print(f"✅ 蜻蜓FM找到 {len(qtfm_books)} 本书")
                except Exception as e:
                    print(f"❌ 蜻蜓FM搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '网易云听书':
                print(f"🔍 网易云听书搜索: {keyword}")
                try:
                    netease_books = self.netease_manager.search_books(keyword, limit=30)
                    for book in netease_books:
                        if isinstance(book, dict):
                            book = self._ensure_book_fields(book, '网易云听书')
                            results.append(book)
                    print(f"✅ 网易云听书找到 {len(netease_books)} 个播客")
                except Exception as e:
                    print(f"❌ 网易云听书搜索失败: {e}")
                    import traceback
                    traceback.print_exc()

            if platform == 'all' or platform == '荔枝FM':
                print(f"🔍 荔枝FM搜索/解析: {keyword}")
                try:
                    lizhi_books = self.lizhi_manager.search_books(keyword, limit=20)
                    for book in lizhi_books:
                        if isinstance(book, dict):
                            book = self._ensure_book_fields(book, '荔枝FM')
                            results.append(book)
                    if lizhi_books:
                        print(f"✅ 荔枝FM找到 {len(lizhi_books)} 个播客")
                    elif platform == '荔枝FM':
                        print("⚠️ 荔枝FM请使用主播主页链接或用户ID，例如 https://www.lizhi.fm/user/742")
                except Exception as e:
                    print(f"❌ 荔枝FM搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
            
            if platform == 'all' or platform == '云听FM':
                kw = (keyword or '').strip()
                is_link = kw.startswith('http://') or kw.startswith('https://') or 'radio.cn' in kw
                is_id = kw.isdigit() and len(kw) >= 6
                print(f"☁️ 云听FM搜索: {keyword}")
                try:
                    if is_link or is_id:
                        album_info = self.yuntu_manager.search_by_link_or_id(keyword)
                        if album_info:
                            album_info = self._ensure_book_fields(album_info, '云听FM')
                            results.append(album_info)
                            print(f"✅ 云听FM找到 1 个专辑")
                        else:
                            print(f"⚠️ 云听FM未找到专辑（请确保输入的是有效的分享链接或专辑ID）")
                    else:
                        yuntu_books = self.yuntu_manager.search_books(keyword, page=0, page_size=20)
                        for book in yuntu_books:
                            if isinstance(book, dict):
                                book = self._ensure_book_fields(book, '云听FM')
                                results.append(book)
                        print(f"✅ 云听FM找到 {len(yuntu_books)} 个专辑")
                        if not yuntu_books and platform == '云听FM':
                            print("⚠️ 云听FM关键词搜索暂不可用，可先使用分享链接或专辑ID")
                except Exception as e:
                    print(f"❌ 云听FM搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
                
        except Exception as e:
            print(f"❌ 搜索异常: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"🎯 总共找到 {len(results)} 本书")
        return results
    
    def search_by_id(self, book_id: str, platform: str = 'all') -> List[Dict]:
        """通过ID搜索书籍（支持喜马拉雅、懒人听书、番茄畅听）"""
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
            print(f"🔍 喜马拉雅ID搜索: {album_id}")
            
            # 直接调用喜马拉雅搜索API，但是用ID作为关键词
            # 这样可以得到完整的书籍信息格式
            search_results = self.ximalaya_manager.search_albums(album_id, page=1, page_size=1)
            
            if search_results:
                # 从搜索结果中找到匹配的专辑
                for album in search_results:
                    if str(album.get('id', '')) == str(album_id):
                        print(f"✅ 喜马拉雅找到匹配专辑: {album.get('title', '未知')}")
                        return album
                
                # 如果没找到精确匹配，返回第一个结果
                if search_results:
                    result = search_results[0]
                    print(f"✅ 喜马拉雅返回第一个搜索结果: {result.get('title', '未知')}")
                    return result
            
            # 如果搜索没找到，尝试直接调用专辑详情API
            print(f"🔍 搜索未找到，尝试直接获取专辑详情: {album_id}")
            album_info = self.ximalaya_manager.get_album_detail(album_id)
            
            if album_info:
                print(f"✅ 喜马拉雅专辑详情获取成功: {album_info.get('title', '未知')}")
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
            else:
                return None
        except Exception as e:
            print(f"❌ 获取专辑详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_album_chapters(self, album_id: str, platform: str) -> List[Dict]:
        """获取专辑章节列表"""
        try:
            chapters = []
            # 🔧 统一platform处理：支持英文代码和中文名称
            if platform in ['喜马拉雅', 'ximalaya']:
                # 喜马拉雅管理器在 page_size > 1000 时会走大页/并发加载，避免 50 条一页串行拉取。
                chapters = self.ximalaya_manager.get_album_chapters(album_id, 1, 2000)
                if not chapters:
                    page = 1
                    page_size = 200
                    while True:
                        page_chapters = self.ximalaya_manager.get_album_chapters(album_id, page, page_size)
                        if not page_chapters:
                            break
                        chapters.extend(page_chapters)
                        if len(page_chapters) < page_size:
                            break
                        page += 1
                        if page > 200:
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
                print(f"🎧 蜻蜓FM获取章节: {album_id}")
                
                # 先尝试一次性获取所有章节（使用大page_size）
                chapters = self.qtfm_manager.get_chapters(album_id, version=None, page=1, page_size=10000)
                
                # 如果获取的章节数量很多，检查是否可能还有更多章节
                # 获取书籍详情以获取总章节数（如果API提供）
                book_detail = self.qtfm_manager.get_book_details(album_id)
                total_programs = 0
                if book_detail:
                    total_programs = book_detail.get('total_programs', 0)
                    print(f"📊 书籍详情显示总章节数: {total_programs}")
                
                # 如果获取的章节数少于总章节数，尝试分页获取
                if total_programs > 0 and len(chapters) < total_programs:
                    print(f"⚠️ 检测到章节数不完整: 已获取 {len(chapters)}/{total_programs}，尝试分页获取...")
                    all_chapters = list(chapters)  # 保存已获取的章节
                    page = 2
                    page_size = 100
                    
                    while len(all_chapters) < total_programs:
                        print(f"📖 正在获取第 {page} 页章节...")
                        page_chapters = self.qtfm_manager.get_chapters(album_id, version=None, page=page, page_size=page_size)
                        if not page_chapters or len(page_chapters) == 0:
                            print(f"✅ 已获取所有章节，共 {len(all_chapters)} 个")
                            break
                        all_chapters.extend(page_chapters)
                        print(f"📊 当前已获取 {len(all_chapters)} 个章节")
                        
                        # 如果获取的章节少于请求的数量，说明已经到最后一页
                        if len(page_chapters) < page_size:
                            print(f"✅ 已获取所有章节，共 {len(all_chapters)} 个")
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
                    print(f"✅ 蜻蜓FM分页获取完成，共 {len(chapters)} 个章节")
                elif chapters:
                    print(f"✅ 蜻蜓FM成功获取 {len(chapters)} 个章节")
                else:
                    print(f"⚠️ 蜻蜓FM获取章节失败，尝试使用version参数")
                    # 如果失败，尝试获取version后再获取章节
                    if book_detail:
                        version = book_detail.get('version')
                        if version:
                            chapters = self.qtfm_manager.get_chapters(album_id, version=version, page=1, page_size=10000)
                            if chapters:
                                print(f"✅ 使用version参数成功获取 {len(chapters)} 个章节")
                            else:
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
                        
                        print(f"☁️ 第{page}页获取到 {len(page_chapters)} 个章节")
                        
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
                        print(f"☁️ 分页获取完成，总共 {len(chapters)} 个章节")
            elif platform in ['起点听书', 'qidian']:
                # 起点听书章节加载
                chapters = self.search_manager.get_album_chapters(album_id, platform)
            elif platform in ['酷我听书', 'kuwo']:
                # 酷我听书获取全部章节
                print(f"🎵 酷我听书获取章节: {album_id}")
                chapters = self.kuwo_manager.get_chapters(album_id, page=1, page_size=10000)
                print(f"✅ 酷我听书获取到 {len(chapters)} 个章节")
            elif platform in ['网易云听书', 'netease']:
                print(f"🎧 网易云听书获取章节: {album_id}")
                chapters = self.netease_manager.get_chapters(album_id, page=1, page_size=1000)
                print(f"✅ 网易云听书获取到 {len(chapters)} 个章节")
            elif platform in ['荔枝FM', 'lizhi']:
                print(f"🍥 荔枝FM获取章节: {album_id}")
                chapters = self.lizhi_manager.get_chapters(album_id, page=1, page_size=500, max_pages=20)
                print(f"✅ 荔枝FM获取到 {len(chapters)} 个章节")
            else:
                return []
            
            print(f"📚 获取到 {len(chapters)} 个章节")
            return chapters
            
        except Exception as e:
            print(f"❌ 获取章节失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_audio_urls(self, track_id: str, platform: str, book_id: Optional[str] = None, voice_name: Optional[str] = None) -> Dict[str, str]:
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
