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
        
    def _safe_set_cookie(self, name: str, value: str, domain: str = ".kuwo.cn", path: str = "/"):
        """安全地设置 Cookie"""
        try:
            # 先删除所有同名的 Cookie
            cookies_to_remove = []
            for cookie in list(self.session.cookies):
                if cookie.name == name:
                    cookies_to_remove.append((cookie.domain or ".kuwo.cn", cookie.path or "/", cookie.name))
            
            for domain_rm, path_rm, name_rm in cookies_to_remove:
                try:
                    self.session.cookies.clear(domain_rm, path_rm, name_rm)
                except Exception:
                    pass
            
            # 设置新 Cookie
            self.session.cookies.set_cookie(create_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path,
            ))
        except Exception as e:
            print(f"[酷我听书] 设置 Cookie 失败: {name}, 错误: {e}")
    
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
    
    def search_books(self, keyword: str) -> List[Dict]:
        """搜索书籍"""
        try:
            print(f"🔍 酷我听书搜索: {keyword}")
            
            # 构建搜索URL
            search_url = f"https://search.kuwo.cn/r.s?client=kt&all={quote(keyword)}&pn=0&rn=100&uid=2740589762&ver=kwplayer_ar_11.1.6.1&ft=album&correct=1&vipver=1&show_copyright_off=1&isstar=1&starver=1&newsearch=1&newver=3&searchNo=2740589762{quote(keyword)}{int(time.time() * 1000)}&cluster=0&encoding=utf8&rformat=json&mobi=1&strategy=2012&presell=1&q36=2687b446c2e07697f0f33fb510001a41920e&spPrivilege=0&sortby=0"
            
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
                for album in albums:
                    books.append({
                        'id': str(album.get('albumid', '')),
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
    
    def _fetch_single_page(self, album_id: str, page_num: int) -> Dict:
        """获取单页章节数据（用于并发请求）"""
        try:
            req_id = str(uuid.uuid4()).replace('-', '')
            timestamp = int(time.time() * 1000)
            url = f"https://www.kuwo.cn/api/www/album/albumInfo?albumId={album_id}&pn={page_num}&rn=24&reqId={req_id}&httpsStatus=1&plat=web_www&from=&_={timestamp}"
            
            headers = self._kuwo_api_headers("https://www.kuwo.cn")
            response = self.session.get(url, headers=headers, timeout=15)
            
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
    
    def get_chapters(self, album_id: str, page: int = 1, page_size: int = 50) -> List[Dict]:
        """获取章节列表 - 并发优化版：使用多线程并行加载"""
        try:
            print(f"📚 获取酷我听书章节: {album_id}, 页码: {page}")
            
            # 重置 Cookie 为初始值
            self._safe_set_cookie(
                name="Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324",
                value=self._fixed_cookie_value,
                domain=".kuwo.cn"
            )
            
            # 第一步：获取第一页，确定总章节数
            first_page_result = self._fetch_single_page(album_id, 1)
            if not first_page_result['success']:
                print(f"❌ 获取第一页失败")
                return []
            
            total_chapters = first_page_result['total']
            required_pages = (total_chapters + 23) // 24  # 向上取整
            print(f"📊 总章节数: {total_chapters}，需要 {required_pages} 页，开始并发加载...")
            
            # 存储所有页面的结果
            page_results = {1: first_page_result}
            
            # 第二步：并发获取剩余页面
            if required_pages > 1:
                # 使用线程池并发获取，最大并发数为10
                max_workers = min(10, required_pages - 1)
                remaining_pages = list(range(2, required_pages + 1))
                
                start_time = time.time()
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # 提交所有任务
                    future_to_page = {
                        executor.submit(self._fetch_single_page, album_id, p): p 
                        for p in remaining_pages
                    }
                    
                    # 收集结果
                    completed = 0
                    for future in concurrent.futures.as_completed(future_to_page):
                        page_num = future_to_page[future]
                        try:
                            result = future.result()
                            page_results[page_num] = result
                            completed += 1
                            if completed % 20 == 0:  # 每20页打印一次进度
                                print(f"⏳ 并发加载进度: {completed}/{len(remaining_pages)} 页")
                        except Exception as e:
                            print(f"❌ 第 {page_num} 页获取异常: {e}")
                            page_results[page_num] = {'page': page_num, 'total': 0, 'music_list': [], 'success': False}
                
                elapsed = time.time() - start_time
                print(f"⚡ 并发加载完成！耗时: {elapsed:.2f}秒，平均 {elapsed/len(remaining_pages)*1000:.1f}ms/页")
            
            # 第三步：按页码顺序合并所有章节
            all_chapters = []
            for page_num in sorted(page_results.keys()):
                result = page_results[page_num]
                if result['success']:
                    music_list = result['music_list']
                    base_index = (page_num - 1) * 24  # 计算起始序号
                    
                    for idx, chapter in enumerate(music_list):
                        duration = chapter.get('duration', 0)
                        duration_formatted = f"{duration // 60:02d}:{duration % 60:02d}" if duration > 0 else "00:00"
                        
                        all_chapters.append({
                            'id': str(chapter.get('rid', '')),
                            'title': chapter.get('name', ''),
                            'duration': duration_formatted,
                            'size': '',
                            'plays': 0,
                            'album': album_id,
                            'order_num': base_index + idx + 1,  # 正确计算序号
                            # 酷我特有字段
                            'kuwo_rid': chapter.get('rid', ''),
                        })
            
            print(f"✅ 酷我听书章节加载完成，共 {len(all_chapters)}/{total_chapters} 章")
            
            # 分页返回
            if page > 1 or page_size < len(all_chapters):
                start_index = (page - 1) * page_size
                end_index = start_index + page_size
                return all_chapters[start_index:end_index]
            
            return all_chapters
            
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
                    return dict(cached_data) if cached_data else None

            # 根据首选格式和比特率选择 br 参数
            if preferred_format.lower() == 'mp3' and bitrate:
                if bitrate == 320:
                    br_param = '320kmp3'
                elif bitrate == 192:
                    br_param = '192kmp3'
                elif bitrate == 128:
                    br_param = '128kmp3'
                else:
                    br_param = '320kmp3'
            else:
                format_map = {
                    'flac': '2000kflac',
                    'mp3': '320kmp3',
                }
                br_param = format_map.get(preferred_format.lower(), '2000kflac')
            
            url = f"https://mobi.kuwo.cn/mobi.s?f=web&user=1008611&source=kwplayerhd_ar_4.3.0.8_tianbao_T1A_qirui.apk&type=convert_url_with_sign&rid={rid}&br={br_param}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = self.session.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
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
            
            with self._download_info_cache_lock:
                self._download_info_cache[cache_key] = {"time": now, "data": None}
            return None
            
        except Exception as e:
            print(f"[酷我] 获取下载URL失败: {e}")
            return None
    
    def download_audio(self, url: str, save_path: str, progress_callback=None) -> bool:
        """下载音频（使用当前线程的 session，支持并发）"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = self.session.get(url, headers=headers, stream=True, timeout=60)
            
            if response.status_code == 200:
                file_size = 0
                total_size = int(response.headers.get('Content-Length') or 0)
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=262144):
                        if chunk:
                            f.write(chunk)
                            file_size += len(chunk)
                            if progress_callback:
                                progress_callback(file_size, total_size)
                
                if file_size > 1024 * 10:  # 大于10KB认为下载成功
                    print(f"✅ 下载成功: {file_size // 1024}KB")
                    return True
                else:
                    os.remove(save_path)
                    return False
            
            return False
            
        except Exception as e:
            print(f"❌ 下载异常: {e}")
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
                return {
                    'url': download_info['url'],
                    'format': file_format,
                    'bitrate': download_info.get('bitrate', 0),
                    'extension': f'.{file_format}' if file_format else '.mp3'
                }
            
            return None
            
        except Exception as e:
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
