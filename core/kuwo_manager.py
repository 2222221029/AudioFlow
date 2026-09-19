#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
酷我听书管理器 - 书籍搜索和下载工具
支持搜索书籍、查看详情、单章节下载和批量下载
"""

import requests
import json
import time
import random
import math
import os
import re
import uuid
import concurrent.futures
import threading
from typing import List, Dict, Optional
from urllib.parse import quote
from requests.cookies import create_cookie


class KuwoManager:
    """酷我听书管理器"""
    _download_info_cache = {}
    _download_info_cache_lock = threading.Lock()
    _download_info_cache_ttl = 600
    
    def __init__(self):
        # HTTP 会话（用于自动携带 Cookie）
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self._kw_token = None
        self.last_error = ""
        self.last_error_type = ""

        # 分页抓取参数。酷我 albumInfo 在并发分页请求下会偶发把相邻页的响应返给
        # 当前页请求（实测 10 线程时约 23% 的全量抓取会命中：同一批 rid 被重复下载、
        # 真实的那一页永远进不了下载列表）。并发越低越不容易触发，因此默认压到 3
        # 并加请求间隔，同时由下面的页校验与重抓兜底。
        try:
            self._page_concurrency = max(1, int(os.environ.get("KUWO_PAGE_CONCURRENCY", "3")))
        except (TypeError, ValueError):
            self._page_concurrency = 3
        try:
            self._page_request_interval = max(0.0, float(os.environ.get("KUWO_PAGE_INTERVAL", "0.15")))
        except (TypeError, ValueError):
            self._page_request_interval = 0.15
        # albumInfo 的 rn 上限实测为 100：传 500/1000 只返回 100 条，传 6127 直接 504。
        # 旧值 24 会让"整本目录"抓取（enhanced_search_manager 以 page_size=10000 调用）
        # 膨胀到约 417 个 API 页，参考实现同场景只需约 100 页。rn 变大也会放大单次
        # 响应错位的影响面，但页数减少同时降低了错位发生的次数，且下面的页校验与
        # 重抓兜底仍然生效。
        try:
            self._page_size = max(1, min(100, int(os.environ.get("KUWO_PAGE_SIZE", "100"))))
        except (TypeError, ValueError):
            self._page_size = 100
        
        # 写死的 Secret 和 Cookie（无需算法和登录）
        self._fixed_secret = "7363e89561110e6cb657c2fb7cedc85451a49cad02a8ce4d6bc236dce7ed52ce0144c917"
        self._fixed_cookie_value = "P3c7p6fGhrbj7WyyYkmz5RRJbBMEak7B"
        
        # 设置固定的 Cookie 到 session
        self._safe_set_cookie(
            name="Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324",
            value=self._fixed_cookie_value,
            domain=".kuwo.cn"
        )
        print(f"[酷我听书] 使用固定的 Cookie 和 Secret（无需登录）")

    def _record_error(self, message: str, error_type: str = "download_failed"):
        self.last_error = str(message or "酷我下载失败")[:300]
        self.last_error_type = str(error_type or "download_failed")

    def _clear_error(self):
        self.last_error = ""
        self.last_error_type = ""

    @classmethod
    def invalidate_download_info(cls, chapter_id: str):
        """Drop every cached signed media URL for one chapter."""
        rid = str(chapter_id)
        with cls._download_info_cache_lock:
            for key in [key for key in cls._download_info_cache if key[0] == rid]:
                cls._download_info_cache.pop(key, None)

    def normalize_download_quality(self, quality: str = "", voice_config: Optional[Dict] = None) -> str:
        """将 UI 的通用音质映射到酷我支持的音质档位。"""
        if isinstance(voice_config, dict) and voice_config.get("kuwo_quality"):
            quality = str(voice_config.get("kuwo_quality") or "")
        q = str(quality or "").strip().lower()
        if not q:
            return "lossless"
        if any(token in q for token in ("lossless", "flac", "无损", "無損")):
            return "lossless"
        if "kuwo:" in q:
            q = q.split("kuwo:", 1)[1].strip()
            if q in ("standard", "mp3_128", "128", "128k"):
                return "standard"
            if q in ("high", "mp3_320", "320", "320k", "192", "192k"):
                return "high"
        if q in ("standard", "mp3_128", "128", "128k"):
            return "standard"
        if q in ("high", "mp3_320", "320", "320k", "192", "192k"):
            return "high"
        # 酷我不支持项目里的通用 M4A 档位，默认仍按用户要求优先无损。
        return "lossless"
        
    def _safe_set_cookie(self, name: str, value: str, domain: str = ".kuwo.cn", path: str = "/", session=None):
        """安全地设置 Cookie（可指定目标 session，供错位页重抓时的独立会话使用）"""
        target = session if session is not None else self.session
        try:
            # 先删除所有同名的 Cookie
            cookies_to_remove = []
            for cookie in list(target.cookies):
                if cookie.name == name:
                    cookies_to_remove.append((cookie.domain or ".kuwo.cn", cookie.path or "/", cookie.name))

            for domain_rm, path_rm, name_rm in cookies_to_remove:
                try:
                    target.cookies.clear(domain_rm, path_rm, name_rm)
                except Exception:
                    pass

            # 设置新 Cookie
            target.cookies.set_cookie(create_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path,
            ))
        except Exception as e:
            print(f"[酷我听书] 设置 Cookie 失败: {name}, 错误: {e}")

    def _new_kuwo_session(self):
        """新建独立会话（仅带固定 Cookie），用于重抓错位页。

        复用原连接池重抓仍可能再次命中同一错配（错位发生在连接/网关层），
        因此重抓一律走全新会话。
        """
        session = requests.Session()
        session.headers.update(self.session.headers)
        self._safe_set_cookie(
            name="Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324",
            value=self._fixed_cookie_value,
            domain=".kuwo.cn",
            session=session,
        )
        return session

    def _kuwo_api_headers(self, referer: str = "https://www.kuwo.cn"):
        """生成请求头，使用固定的 Secret 和 Cookie"""
        return {
            "Connection": "Keep-Alive",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "www.kuwo.cn",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Secret": self._fixed_secret,
        }
    
    def search_books(self, keyword: str, limit: int = 20) -> List[Dict]:
        """搜索书籍"""
        try:
            print(f"🔍 酷我听书搜索: {keyword}")
            
            # 构建搜索URL
            result_limit = max(1, min(int(limit or 20), 100))
            search_url = f"https://search.kuwo.cn/r.s?client=kt&all={quote(keyword)}&pn=0&rn={result_limit}&uid=2740589762&ver=kwplayer_ar_11.1.6.1&ft=album&correct=1&vipver=1&show_copyright_off=1&isstar=1&starver=1&newsearch=1&newver=3&searchNo=2740589762{quote(keyword)}{int(time.time() * 1000)}&cluster=0&encoding=utf8&rformat=json&mobi=1&strategy=2012&presell=1&q36=2687b446c2e07697f0f33fb510001a41920e&spPrivilege=0&sortby=0"
            
            response = self.session.get(search_url, timeout=15)
            response.encoding = 'utf-8'
            
            if response.status_code == 200:
                data = json.loads(response.text)
                albums = data.get('albumlist', [])
                if not albums:
                    data_obj = data.get('data', {})
                    if isinstance(data_obj, dict):
                        albums = data_obj.get('albumlist', [])
                
                # 转换为统一格式
                books = []
                seen_album_ids = set()
                for album in albums:
                    album_id = str(album.get('albumid', ''))
                    if not album_id or album_id in seen_album_ids:
                        continue
                    seen_album_ids.add(album_id)
                    books.append({
                        'id': album_id,
                        'title': album.get('name', ''),
                        'author': album.get('artist', ''),
                        'platform': '酷我听书',
                        'cover': album.get('img', '') or album.get('hts_img', ''),
                        'plays': int(album.get('PLAYCNT', 0)),
                        'episodes': int(album.get('musiccnt', 0)),
                        'status': '连载中',
                        'description': album.get('info', ''),
                        'category': '',
                        'tags': [],
                        'created_at': '',
                        'updated_at': '',
                        # 酷我特有字段
                        'kuwo_albumid': album.get('albumid', ''),
                    })
                    if len(books) >= result_limit:
                        break
                
                print(f"✅ 酷我听书搜索完成，找到 {len(books)} 本书")
                return books
            else:
                print(f"❌ 搜索失败: HTTP {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ 酷我听书搜索异常: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_book_detail(self, book_id: str) -> Optional[Dict]:
        """获取书籍详情"""
        try:
            print(f"📚 获取酷我听书详情: {book_id}")
            page = self._fetch_single_page(book_id, 1)
            total_chapters = int(page.get("total") or 0)
            detail = page.get("album_info") or {}
            music_list = page.get("music_list") or []
            first_music = music_list[0] if music_list else {}
            cover = (
                detail.get("pic")
                or detail.get("img")
                or detail.get("hts_img")
                or first_music.get("pic")
                or first_music.get("albumpic")
                or first_music.get("web_albumpic_short")
                or ""
            )

            return {
                'id': book_id,
                'title': detail.get("album") or detail.get("name") or f'酷我书籍_{book_id}',
                'author': detail.get("artist") or detail.get("artistName") or '未知作者',
                'platform': '酷我听书',
                'cover': cover,
                'pic': cover,
                'plays': int(detail.get("playCnt") or 0),
                'episodes': total_chapters,
                'status': '连载中',
                'description': detail.get("albuminfo") or f'酷我听书书籍 (ID: {book_id})',
                'category': '',
                'tags': [],
                'created_at': '',
                'updated_at': '',
                'total_chapters': total_chapters,
                'kuwo_albumid': book_id,
            }
                
        except Exception as e:
            print(f"❌ 获取酷我听书详情失败: {e}")
            return None
    
    def _fetch_single_page(self, album_id: str, page_num: int, session=None) -> Dict:
        """获取单页章节数据（用于并发请求；session 可传入独立会话用于重抓）"""
        try:
            req_id = str(uuid.uuid4()).replace('-', '')
            timestamp = int(time.time() * 1000)
            url = f"https://www.kuwo.cn/api/www/album/albumInfo?albumId={album_id}&pn={page_num}&rn={self._page_size}&reqId={req_id}&httpsStatus=1&plat=web_www&from=&_={timestamp}"

            headers = self._kuwo_api_headers("https://www.kuwo.cn")
            http = session if session is not None else self.session
            response = http.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                code = data.get('code')
                success = data.get('success')
                
                if code == 200 or success is True:
                    data_obj = data.get('data', {})
                    return {
                        'page': page_num,
                        'total': data_obj.get('total', 0),
                        'music_list': data_obj.get('musicList', []),
                        'album_info': data_obj,
                        'success': True
                    }
            
            return {'page': page_num, 'total': 0, 'music_list': [], 'success': False}
        except Exception as e:
            print(f"❌ 获取第 {page_num} 页失败: {e}")
            return {'page': page_num, 'total': 0, 'music_list': [], 'success': False}
    
    # ------------------------------------------------------------------
    # 分页完整性与「响应错配」防护
    #
    # 背景：酷我 albumInfo 在并发分页请求下会偶发把相邻页的响应返给当前页请求。
    # 实测并发 10 线程抓取 1952 集（82 页）时，30 轮中有 7 轮出现错位（约 23%）；
    # 单线程顺序抓取 486 次请求 0 错位。错位响应的 success 仍为 True，所以只重试
    # success=False 的页根本发现不了它；而一次错位就是一整页 24 集：错位页的 24 个
    # 位置拿到了别的页的 rid（于是重复下载 24 集），真实的那 24 集永远进不了下载
    # 列表（于是永久缺失）。下面用「跨页 rid 重复 + 页内集号自洽性」把它检出来，
    # 并用独立会话串行重抓修复。
    # ------------------------------------------------------------------

    @staticmethod
    def _chapter_number_from_name(name) -> Optional[int]:
        """尽力从章节名里提取集号（兼容「第433集」「-0433-」「0433-」等写法）。"""
        text = str(name or "")
        for pattern in (r'第\s*(\d+)\s*[集章回节]', r'-\s*(\d{2,5})\s*-', r'^\s*(\d{2,5})\s*[-_\s]'):
            match = re.search(pattern, text)
            if not match:
                continue
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @classmethod
    def _result_rids(cls, result) -> List[str]:
        if not result or not result.get('success'):
            return []
        return [
            str(item.get('rid')) for item in (result.get('music_list') or [])
            if str(item.get('rid') or '').strip()
        ]

    @classmethod
    def _result_numbers(cls, result) -> List[int]:
        numbers = []
        for item in (result or {}).get('music_list') or []:
            value = cls._chapter_number_from_name(item.get('name'))
            if value is not None:
                numbers.append(value)
        return numbers

    def _find_misaligned_pages(self, page_results: Dict[int, Dict], pages) -> List[int]:
        """挑出疑似「拿到了别的页数据」的页。"""
        scope = set(pages or [])
        suspects = set()

        # 1) 跨页 rid 重复：同一 rid 只属于一页，重复即说明有页拿错了响应
        rid_owners = {}
        for page_num, result in page_results.items():
            for rid in self._result_rids(result):
                rid_owners.setdefault(rid, set()).add(page_num)
        for owners in rid_owners.values():
            if len(owners) > 1:
                suspects.update(owners)

        # 2) 页内集号只允许「非严格递增」：出现倒序说明整页数据被替换成了别页数据。
        #    不能把「集号重复」当异常——同一集拆成多段音频是正常结构（例如
        #    「-1836-祖相（二）-001」与「-1836-祖相（二）-002」，且 1837 缺号），
        #    按严格递增判定会让这类页每次抓取都固定误报并白白触发重抓。
        for page_num, result in page_results.items():
            numbers = self._result_numbers(result)
            if len(numbers) >= 2 and any(later < earlier for earlier, later in zip(numbers, numbers[1:])):
                suspects.add(page_num)

        # 2b) 同一页内出现完全同名的条目：整页数据被部分替换的典型特征
        for page_num, result in page_results.items():
            names = [str(item.get('name') or '') for item in (result or {}).get('music_list') or []]
            names = [name for name in names if name]
            if len(names) != len(set(names)):
                suspects.add(page_num)

        # 3) 非末页却不足一整页：该页被截断或混入了别页数据
        for page_num, result in page_results.items():
            rids = self._result_rids(result)
            if 0 < len(rids) < self._page_size and any(
                other > page_num and self._result_rids(page_results.get(other)) for other in scope
            ):
                suspects.add(page_num)

        return sorted(suspects & scope)

    def _fetch_pages_concurrently(self, album_id: str, pages) -> Dict[int, Dict]:
        """并发抓取一批页（并发数默认 3：并发越高越容易触发响应错配）。"""
        results: Dict[int, Dict] = {}
        pages = list(pages or [])
        if not pages:
            return results

        def fetch(page_num):
            # 请求间隔把并发请求在时间上摊开，进一步降低错配概率
            if self._page_request_interval:
                time.sleep(self._page_request_interval)
            return self._fetch_single_page(album_id, page_num)

        workers = max(1, min(self._page_concurrency, len(pages)))
        if workers == 1:
            for page_num in pages:
                results[page_num] = fetch(page_num)
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_page = {executor.submit(fetch, p): p for p in pages}
            for future in concurrent.futures.as_completed(future_to_page):
                page_num = future_to_page[future]
                try:
                    results[page_num] = future.result()
                except Exception as exc:
                    print(f"❌ 第 {page_num} 页获取异常: {exc}")
                    results[page_num] = {'page': page_num, 'total': 0, 'music_list': [], 'success': False}
        return results

    def _refetch_pages_serially(self, album_id: str, pages, reason: str = "") -> Dict[int, Dict]:
        """串行 + 独立会话重抓指定页（实测该组合 486 次请求 0 错位）。"""
        results: Dict[int, Dict] = {}
        targets = sorted(set(pages or []))
        for page_num in targets:
            session = self._new_kuwo_session()
            try:
                results[page_num] = self._fetch_single_page(album_id, page_num, session=session)
            except Exception as exc:
                print(f"❌ 第 {page_num} 页重抓异常: {exc}")
                results[page_num] = {'page': page_num, 'total': 0, 'music_list': [], 'success': False}
            finally:
                try:
                    session.close()
                except Exception:
                    pass
            if self._page_request_interval:
                time.sleep(self._page_request_interval)
        if targets and reason:
            print(f"🔁 已串行重抓 {len(targets)} 页（{reason}）")
        return results

    def _load_pages_verified(self, album_id: str, requested_pages, seed_results=None) -> Dict[int, Dict]:
        """抓取所需分页并修复响应错配页，最多重抓 2 轮。"""
        requested_pages = list(requested_pages or [])
        page_results = dict(seed_results or {})

        todo = [p for p in requested_pages if p not in page_results]
        if todo:
            page_results.update(self._fetch_pages_concurrently(album_id, todo))

        failed = [p for p in requested_pages if not (page_results.get(p) or {}).get('success')]
        if failed:
            page_results.update(self._refetch_pages_serially(album_id, failed, reason="失败页重试"))

        for round_no in range(3):
            suspects = self._find_misaligned_pages(page_results, requested_pages)
            if not suspects:
                return page_results
            if round_no == 2:
                print(
                    f"❌ 酷我听书分页重抓后仍异常：第 {suspects} 页；"
                    "本次目录可能仍缺少这些页的真实章节，建议稍后重试该下载任务"
                )
                return page_results
            print(f"⚠️ 酷我听书分页疑似响应错配：第 {suspects} 页（第 {round_no + 1} 次重抓）")
            page_results.update(self._refetch_pages_serially(album_id, suspects, reason="响应错配修复"))
        return page_results

    def get_chapters(self, album_id: str, page: int = 1, page_size: int = 50) -> List[Dict]:
        """按 UI 页范围获取章节；整本请求仍会并发抓取所需的全部 API 页。"""
        try:
            print(f"📚 获取酷我听书章节: {album_id}, 页码: {page}")
            self._safe_set_cookie(
                name="Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324",
                value=self._fixed_cookie_value,
                domain=".kuwo.cn"
            )

            page = max(1, int(page or 1))
            page_size = max(1, int(page_size or 50))
            first_page_result = self._fetch_single_page(album_id, 1)
            if not first_page_result['success']:
                print(f"❌ 获取第一页失败")
                return []

            api_page_size = self._page_size
            total_chapters = int(first_page_result.get('total') or len(first_page_result.get('music_list') or []))
            start_index = (page - 1) * page_size
            if total_chapters and start_index >= total_chapters:
                return []
            end_index = min(start_index + page_size, total_chapters) if total_chapters else start_index + page_size
            first_api_page = start_index // api_page_size + 1
            last_api_page = max(first_api_page, (max(end_index, 1) - 1) // api_page_size + 1)
            requested_pages = list(range(first_api_page, last_api_page + 1))
            seed = {1: first_page_result} if 1 in requested_pages else {}
            page_results = self._load_pages_verified(album_id, requested_pages, seed_results=seed)

            chapters = []
            seen_rids: Dict[str, int] = {}
            duplicate_chapters = 0
            for page_num in sorted(page_results.keys()):
                result = page_results[page_num]
                if not result.get('success'):
                    continue
                music_list = result['music_list']
                base_index = (page_num - 1) * api_page_size
                for idx, chapter in enumerate(music_list):
                    global_index = base_index + idx
                    if global_index < start_index or global_index >= end_index:
                        continue
                    rid = str(chapter.get('rid', '') or '')
                    if rid and rid in seen_rids:
                        # 响应错配残留：同一 rid 出现在两个位置。宁可少下这几集（订阅
                        # 检测会重新判定为缺失并可补全），也绝不重复下载成一堆重复文件。
                        duplicate_chapters += 1
                        continue
                    if rid:
                        seen_rids[rid] = page_num
                    duration = chapter.get('duration', 0)
                    duration_formatted = f"{duration // 60:02d}:{duration % 60:02d}" if duration > 0 else "00:00"
                    chapters.append({
                        'id': rid,
                        'title': chapter.get('name', ''),
                        'duration': duration_formatted,
                        'size': '',
                        'plays': 0,
                        'album': album_id,
                        'order_num': global_index + 1,
                        'kuwo_rid': chapter.get('rid', ''),
                    })

            if duplicate_chapters:
                print(f"⚠️ 酷我听书分页出现 {duplicate_chapters} 个重复 rid（响应错配残留），已跳过重复项")
            if total_chapters and len(chapters) < total_chapters:
                print(f"⚠️ 酷我听书章节不完整: {len(chapters)}/{total_chapters} 章（部分页获取失败）")
            print(f"✅ 酷我听书章节加载完成，本页 {len(chapters)}/{total_chapters} 章")
            return chapters
            
        except Exception as e:
            print(f"❌ 获取酷我听书章节失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_audio_url(self, chapter_id: str, quality: str = 'lossless') -> Optional[str]:
        """获取音频URL - 支持音质降级
        
        Args:
            chapter_id: 章节ID (rid)
            quality: 音质选择
                - 'lossless': 无损音频（优先FLAC，降级到MP3 320->192->128）
                - 'high': 高质量音频（MP3 320->192->128）
                - 'standard': 标准音频（MP3 128->192->320）
        """
        try:
            quality = self.normalize_download_quality(quality)
            print(f"🎵 获取酷我音频URL: {chapter_id}, 质量: {quality}")
            
            download_info = None
            
            if quality == 'lossless':
                # 无损音频：优先尝试 FLAC，如果没有则尝试多个 MP3 比特率
                print(f"[酷我] 无损音频模式：优先尝试 FLAC")
                download_info = self._get_download_url_internal(chapter_id, 'flac')
                if not download_info or download_info.get('format') != 'flac':
                    returned_format = download_info.get('format') if download_info else '无返回'
                    print(f"[酷我] FLAC 不可用（返回格式: {returned_format}），尝试 MP3 格式")
                    # 尝试多个 MP3 比特率
                    mp3_bitrates = [320, 192, 128]
                    for bitrate in mp3_bitrates:
                        print(f"[酷我] 尝试 MP3 {bitrate}kbps")
                        mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                        if mp3_info and mp3_info.get('format') == 'mp3':
                            print(f"[酷我] 找到 MP3 格式（{bitrate}kbps）")
                            download_info = mp3_info
                            break
                            
            elif quality == 'high':
                # 高质量音频：优先使用 MP3 (320kbps -> 192kbps -> 128kbps)
                print(f"[酷我] 高质量音频模式：尝试 MP3 格式")
                mp3_bitrates = [320, 192, 128]
                for bitrate in mp3_bitrates:
                    print(f"[酷我] 尝试 MP3 {bitrate}kbps")
                    mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                    if mp3_info and mp3_info.get('url'):
                        download_info = mp3_info
                        if mp3_info.get('format') == 'mp3':
                            print(f"[酷我] 找到 MP3 格式（{bitrate}kbps）")
                            break
                            
            else:  # standard
                # 标准音频：优先使用 MP3 128kbps
                print(f"[酷我] 标准音频模式：尝试 MP3 格式")
                mp3_bitrates = [128, 192, 320]
                for bitrate in mp3_bitrates:
                    print(f"[酷我] 尝试 MP3 {bitrate}kbps")
                    mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                    if mp3_info and mp3_info.get('url'):
                        download_info = mp3_info
                        if mp3_info.get('format') == 'mp3':
                            print(f"[酷我] 找到 MP3 格式（{bitrate}kbps）")
                            break
            
            if download_info and download_info.get('url'):
                print(f"✅ 获取音频URL成功: {download_info.get('format')}, {download_info.get('bitrate')}kbps")
                return download_info['url']
            
            return None
            
        except Exception as e:
            print(f"❌ 获取酷我音频URL失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_download_url_internal(self, rid: str, preferred_format: str = 'flac', bitrate: int = None) -> Optional[Dict]:
        """内部方法：获取下载URL和格式信息
        
        Args:
            rid: 音频ID
            preferred_format: 首选格式 ('flac', 'mp3')
            bitrate: 指定比特率（仅对 MP3 有效：320, 192, 128）
        
        Returns:
            dict: {'url': '...', 'format': 'flac', 'bitrate': 2000} 或 None
        """
        try:
            cache_key = (str(rid), str(preferred_format).lower(), int(bitrate or 0))
            now = time.time()
            with self._download_info_cache_lock:
                cached = self._download_info_cache.get(cache_key)
                if cached and now - cached.get("time", 0) < self._download_info_cache_ttl:
                    cached_data = cached.get("data")
                    if cached_data:
                        return dict(cached_data)

            # 档位（br）与容器（format）参数。
            #
            # 逆向依据：接口/酷我听书/kuwo_dl/config.py 与 work/re/API_INVENTORY.md
            #   * br 档位取自 APK classes8.dex 常量池，实测有效的是 2000kflac
            #     （唯一能真正返回 FLAC 的档位）/ 320kmp3 / 128kmp3。
            #     192kmp3 **不在**该表中，请求它没有额外收益。
            #   * format 决定容器：不钉 format 时 br=320kmp3 返回 aac/100k、
            #     br=2000kflac 返回 ogg/100k；钉成 mp3 才稳定拿到 mp3 容器，
            #     否则调用方会因 format != 'mp3' 反复降档重试。
            #   * FLAC 档必须保持 format 未指定：钉成 mp3 会强制服务端返回 mp3，
            #     即使该集存在无损音源也拿不到（参考实现写作 ("2000kflac", None, "flac")）。
            if preferred_format.lower() == 'mp3':
                br_param = '128kmp3' if bitrate == 128 else '320kmp3'
                fmt_param = 'mp3'
            else:
                br_param = '2000kflac'
                fmt_param = None

            url = (
                "https://mobi.kuwo.cn/mobi.s?f=web&user=1008611"
                "&source=kwplayerhd_ar_4.3.0.8_tianbao_T1A_qirui.apk"
                f"&type=convert_url_with_sign&rid={rid}&br={br_param}"
            )
            if fmt_param:
                url += f"&format={fmt_param}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = self.session.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                except (ValueError, json.JSONDecodeError) as exc:
                    self._record_error(f"酷我下载地址接口返回了无效 JSON: {exc}")
                    return None
                
                if data.get('code') == 200:
                    data_obj = data.get('data', {})
                    audio_url = data_obj.get('url', '')
                    file_format = data_obj.get('format', 'mp3')
                    actual_bitrate = data_obj.get('bitrate', 0)
                    
                    if audio_url:
                        result = {
                            'url': audio_url,
                            'format': file_format,
                            'bitrate': actual_bitrate,
                        }
                        with self._download_info_cache_lock:
                            self._download_info_cache[cache_key] = {"time": now, "data": dict(result)}
                        return result

                message = data.get('msg') or data.get('message') or '未返回音频地址'
                self._record_error(f"酷我下载地址接口拒绝请求: code={data.get('code')}, {message}")
            else:
                self._record_error(f"酷我下载地址接口 HTTP {response.status_code}")

            # Do not cache failures. Kuwo occasionally returns a transient empty
            # response; a ten-minute negative cache made all worker retries fail.
            return None
            
        except Exception as e:
            self._record_error(f"酷我下载地址请求异常: {e}")
            print(f"[酷我] 获取下载URL失败: {e}")
            return None
    
    def _get_play_restriction_message(self, rid: str) -> str:
        """Ask Kuwo's web API why a media URL is not playable."""
        if not str(rid or "").strip():
            return ""
        try:
            req_id = str(uuid.uuid4())
            url = (
                "https://www.kuwo.cn/api/v1/www/music/playUrl"
                f"?mid={quote(str(rid))}&type=music&httpsStatus=1&reqId={req_id}"
            )
            response = self.session.get(
                url,
                headers=self._kuwo_api_headers("https://www.kuwo.cn/"),
                timeout=15,
            )
            if response.status_code != 200:
                return ""
            payload = response.json()
            message = str(payload.get("msg") or payload.get("message") or "").strip()
            restricted_words = ("付费", "会员", "版权", "下架", "无权", "购买", "客户端")
            return message if any(word in message for word in restricted_words) else ""
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
            return ""

    def download_audio(self, url: str, save_path: str, progress_callback=None, chapter_id: str = "") -> bool:
        """下载音频（使用当前线程的 session，支持并发）"""
        temp_path = f"{save_path}.part"
        try:
            self._clear_error()
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = self.session.get(url, headers=headers, stream=True, timeout=60)
            
            if response.status_code == 200:
                content_type = str(response.headers.get('Content-Type') or '').lower()
                if 'text/html' in content_type or 'application/json' in content_type:
                    self._record_error(f"酷我媒体地址已失效或返回非音频内容: {content_type}")
                    return False
                file_size = 0
                total_size = int(response.headers.get('Content-Length') or 0)
                os.makedirs(os.path.dirname(save_path) or '.', exist_ok=True)
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=262144):
                        if chunk:
                            f.write(chunk)
                            file_size += len(chunk)
                            if progress_callback:
                                progress_callback(file_size, total_size)
                
                if file_size > 1024 * 10:  # 大于10KB认为下载成功
                    os.replace(temp_path, save_path)
                    self._clear_error()
                    print(f"下载成功: {file_size // 1024}KB")
                    return True
                else:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    self._record_error(f"酷我媒体文件过小: {file_size} 字节")
                    return False

            if response.status_code == 403:
                restriction = self._get_play_restriction_message(chapter_id)
                if restriction:
                    self._record_error(f"酷我章节受限：{restriction}", "restricted")
                else:
                    self._record_error(
                        "酷我媒体下载被 CDN 拒绝（HTTP 403），章节可能为付费、下架或版权受限",
                        "restricted",
                    )
            else:
                self._record_error(f"酷我媒体下载 HTTP {response.status_code}")
            return False
            
        except Exception as e:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            self._record_error(f"酷我媒体下载异常: {e}")
            print(f"下载异常: {e}")
            return False
    
    def get_download_info(self, chapter_id: str, quality: str = 'lossless') -> Optional[Dict]:
        """获取下载信息（包含URL和格式）- 支持音质降级
        
        Args:
            chapter_id: 章节ID (rid)
            quality: 音质选择
                - 'lossless': 无损音频（优先FLAC，降级到MP3）
                - 'high': 高质量音频（MP3 320->192->128）
                - 'standard': 标准音频（MP3 128->192->320）
        
        Returns:
            dict: {'url': '...', 'format': 'flac', 'bitrate': 2000, 'extension': '.flac'} 或 None
        """
        try:
            self._clear_error()
            quality = self.normalize_download_quality(quality)
            print(f"🎵 获取酷我下载信息: {chapter_id}, 质量: {quality}")
            
            download_info = None
            
            if quality == 'lossless':
                # 无损音频：优先尝试 FLAC
                print(f"[酷我] 无损音频模式：优先尝试 FLAC")
                download_info = self._get_download_url_internal(chapter_id, 'flac')
                if not download_info or download_info.get('format') != 'flac':
                    print(f"[酷我] FLAC 不可用，尝试 MP3")
                    mp3_bitrates = [320, 192, 128]
                    for bitrate in mp3_bitrates:
                        mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                        if mp3_info and mp3_info.get('format') == 'mp3':
                            download_info = mp3_info
                            break
                            
            elif quality == 'high':
                # 高质量音频
                mp3_bitrates = [320, 192, 128]
                for bitrate in mp3_bitrates:
                    mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                    if mp3_info and mp3_info.get('url'):
                        download_info = mp3_info
                        if mp3_info.get('format') == 'mp3':
                            break
                            
            else:  # standard
                # 标准音频
                mp3_bitrates = [128, 192, 320]
                for bitrate in mp3_bitrates:
                    mp3_info = self._get_download_url_internal(chapter_id, 'mp3', bitrate)
                    if mp3_info and mp3_info.get('url'):
                        download_info = mp3_info
                        if mp3_info.get('format') == 'mp3':
                            break
            
            if download_info and download_info.get('url'):
                file_format = download_info.get('format', 'mp3')
                self._clear_error()
                return {
                    'url': download_info['url'],
                    'format': file_format,
                    'bitrate': download_info.get('bitrate', 0),
                    'extension': f'.{file_format}' if file_format else '.mp3'
                }
            
            if not self.last_error:
                self._record_error(f"酷我未返回章节 {chapter_id} 的可下载音频地址")
            return None
            
        except Exception as e:
            self._record_error(f"获取酷我下载信息失败: {e}")
            print(f"❌ 获取酷我下载信息失败: {e}")
            import traceback
            traceback.print_exc()
            return None


def get_kuwo_manager():
    """获取管理器单例"""
    if not hasattr(get_kuwo_manager, '_instance'):
        get_kuwo_manager._instance = KuwoManager()
    return get_kuwo_manager._instance


def create_kuwo_platform():
    """创建酷我听书平台实例"""
    return get_kuwo_manager()
