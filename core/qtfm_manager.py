#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
蜻蜓FM管理器 - 官方API优化版
基于官方API实现搜索、分页、下载功能
保留扫码登录功能
"""

import requests
import json
import time
import os
import hmac
import hashlib
import math
import concurrent.futures
from typing import Dict, List, Optional, Any
from core.qt_compat import QObject, pyqtSignal, QTimer


# ==================== API 配置 ====================
GRAPHQL_URL = "https://webbff.qtfm.cn/www"
CHANNEL_API = "https://i.qtfm.cn/capi/v3/channel"
PROGRAMS_API = "https://i.qtfm.cn/capi/channel"
AUDIO_BASE = "https://audio.qtfm.cn"
AUDIO_SIGN_KEY = "7l8CZ)SgZgM_bkrw"

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.qtfm.cn",
    "Referer": "https://www.qtfm.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


class QtfmManager(QObject):
    """蜻蜓FM管理器 - 官方API优化版"""
    
    # 信号定义
    login_qr_generated = pyqtSignal(str, str)  # 二维码生成成功：code_id, qr_url
    login_status_changed = pyqtSignal(str)  # 登录状态变化：status
    login_success = pyqtSignal(str, str)  # 登录成功：access_token, qingting_id
    search_finished = pyqtSignal(list)  # 搜索完成：results
    chapters_loaded = pyqtSignal(list)  # 章节加载完成：chapters
    audio_url_ready = pyqtSignal(str)  # 音频URL准备就绪：url
    download_progress = pyqtSignal(int)  # 下载进度：percentage
    download_finished = pyqtSignal(str)  # 下载完成：filename
    error_occurred = pyqtSignal(str)  # 错误发生：error_message
    
    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        
        self.current_book = None
        self.current_chapters = []
        self.access_token = ''
        self.qingting_id = ''
        self.is_logged_in = False
        self.cookie_str = ''  # 存储Cookie字符串，用于下载
        
        # 用户信息
        self.user_info = {
            'nick_name': '',
            'userName': '',
            'avatar': '',
            'qingting_id': '',
            'level': 0,
        }
        
        # 登录状态检查定时器
        self.login_check_timer = QTimer()
        self.login_check_timer.timeout.connect(self._auto_check_login)
        self._current_code_id = ''
        
        print("🎧 蜻蜓FM管理器已初始化（官方API优化版）")
    
    # ==================== Cookie 管理 ====================
    def set_cookie(self, cookie_str: str):
        """设置Cookie（用于下载付费内容）"""
        self.cookie_str = cookie_str
        if cookie_str:
            self.session.headers["Cookie"] = cookie_str
            # 解析Cookie获取认证信息
            self._parse_cookie_for_auth(cookie_str)
        elif "Cookie" in self.session.headers:
            del self.session.headers["Cookie"]
        print(f"🍪 Cookie已更新: {'已设置' if cookie_str else '已清除'}")
    
    def _parse_cookie_for_auth(self, cookie_str: str):
        """从Cookie解析认证信息"""
        if not cookie_str:
            return
        
        cookies = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        
        # 提取access_token和qingting_id
        self.access_token = (
            cookies.get("access_token") or 
            cookies.get("ACCESS_TOKEN") or 
            cookies.get("AccessToken") or ""
        )
        self.qingting_id = (
            cookies.get("qingting_id") or 
            cookies.get("QINGTING_ID") or 
            cookies.get("QingtingId") or ""
        )
        
        if self.access_token and self.qingting_id:
            self.is_logged_in = True
            print("✅ 从Cookie解析到蜻蜓FM认证信息")
    
    # ==================== GraphQL 请求 ====================
    def _graphql_request(self, query: str, timeout: int = 30) -> Optional[Dict]:
        """发送GraphQL请求"""
        payload = {"query": query}
        try:
            response = self.session.post(GRAPHQL_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ GraphQL请求错误: {e}")
            return None
    
    # ==================== 搜索功能 ====================
    def search_books(self, keyword: str, max_pages: int = 50) -> List[Dict]:
        """搜索书籍 - 使用官方GraphQL API，支持并发分页"""
        print(f"🔍 正在搜索: {keyword}")
        
        try:
            # 第一步：获取第一页，确定总数
            first_result = self._search_single_page(keyword, 1)
            if not first_result:
                print(f"❌ 第一页搜索无结果")
                return []
            
            all_results = list(first_result.get('results', []))
            total_found = first_result.get('total', 0)
            
            print(f"📊 搜索结果: 总共 {total_found} 个，第一页获取 {len(all_results)} 个")
            
            if not all_results:
                print(f"❌ 第一页无有效结果")
                return []
            
            # 计算需要的总页数 (每页约15个结果)
            page_size = 15
            total_pages = min(math.ceil(total_found / page_size), max_pages)
            
            print(f"📊 需要加载 {total_pages} 页")
            
            # 第二步：并发获取剩余页面
            if total_pages > 1:
                remaining_pages = list(range(2, total_pages + 1))
                print(f"⏳ 开始并发加载剩余 {len(remaining_pages)} 页...")
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {
                        executor.submit(self._search_single_page, keyword, p): p 
                        for p in remaining_pages
                    }
                    
                    completed = 0
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            result = future.result()
                            if result and result.get('results'):
                                all_results.extend(result['results'])
                            completed += 1
                            if completed % 10 == 0:
                                print(f"⏳ 搜索进度: {completed + 1}/{total_pages} 页 ({len(all_results)} 个结果)")
                        except Exception as e:
                            print(f"❌ 搜索分页错误: {e}")
            
            # 去重 (根据id)
            seen_ids = set()
            unique_results = []
            for r in all_results:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    unique_results.append(r)
            
            books = self._convert_search_results(unique_results)
            print(f"✅ 搜索完成，共 {len(books)} 本书籍（去重后）")
            self.search_finished.emit(books)
            return books
            
        except Exception as e:
            print(f"❌ 搜索失败: {e}")
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(f"搜索失败: {e}")
            return []
    
    def _search_single_page(self, keyword: str, page: int) -> Optional[Dict]:
        """搜索单页"""
        query = f'''{{
            searchResultsPage(keyword:"{keyword}", page:{page}, include:"channel_ondemand") {{
                tdk,
                searchData,
                numFound
            }}
        }}'''
        
        result = self._graphql_request(query)
        if not result or "data" not in result:
            return None
        
        search_page = result["data"].get("searchResultsPage", {})
        search_data = search_page.get("searchData", [])
        num_found = search_page.get("numFound", 0)
        
        # 过滤只保留频道类型
        channels = [r for r in search_data if "channel" in r.get("type", "")]
        
        return {
            'results': channels,
            'total': num_found
        }
    
    def _convert_search_results(self, results: List[Dict]) -> List[Dict]:
        """转换搜索结果为统一格式"""
        books = []
        for item in results:
            book_info = {
                'id': str(item.get('id', '')),
                'title': item.get('title', '未知标题'),
                'author': item.get('podcaster', '未知作者'),
                'play_count': str(item.get('playcount', 0)),
                'description': item.get('description', ''),
                'cover': item.get('cover', ''),
                'episodes': item.get('program_count', 0),
                'platform': '蜻蜓FM'
            }
            books.append(book_info)
        return books
    
    # ==================== 书籍详情 ====================
    def get_book_detail(self, book_id: str) -> Optional[Dict]:
        """获取书籍详情"""
        return self.get_book_details(book_id)
    
    def get_book_details(self, book_id: str) -> Optional[Dict]:
        """获取书籍详情 - 使用官方API"""
        print(f"📚 获取书籍详情: {book_id}")
        
        try:
            url = f"{CHANNEL_API}/{book_id}"
            params = {'user_id': self.qingting_id if self.qingting_id else 'null'}
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if result.get('errorno') == 0 and 'data' in result:
                data = result['data']
                
                # 获取作者信息
                podcasters = data.get('podcasters', [])
                author = podcasters[0].get('nick_name', '未知作者') if podcasters else '未知作者'
                
                book_details = {
                    'id': book_id,
                    'title': data.get('title', ''),
                    'author': author,
                    'description': data.get('description', ''),
                    'play_count': str(data.get('playcount', 0)),
                    'cover': data.get('cover', ''),
                    'version': data.get('v', ''),  # 重要：用于获取章节列表
                    'total_chapters': data.get('program_count', 0),
                    'platform': '蜻蜓FM'
                }
                
                self.current_book = book_details
                print(f"✅ 获取详情成功: {book_details['title']}, 版本号: {book_details['version']}")
                return book_details
            else:
                error_msg = result.get('errormsg', '未知错误')
                print(f"❌ 获取详情失败: {error_msg}")
                return None
                
        except Exception as e:
            print(f"❌ 获取详情异常: {e}")
            return None
    
    # ==================== 章节列表 ====================
    def get_chapters(self, book_id: str, version: str = None, page: int = 1, page_size: int = 50) -> List[Dict]:
        """获取章节列表 - 使用官方API，支持并发分页"""
        print(f"📖 获取章节列表: book_id={book_id}, version={version}")
        
        # 如果没有version，先获取书籍详情
        if not version:
            details = self.get_book_details(book_id)
            if details:
                version = details.get('version', '')
            if not version:
                print("❌ 无法获取版本号")
                return []
        
        try:
            # 第一步：获取第一页，确定总数
            first_result = self._get_programs_page(book_id, version, 1, 50)
            if not first_result:
                return []
            
            all_programs = first_result.get('programs', [])
            total = first_result.get('total', 0)
            
            print(f"📊 总章节数: {total}")
            
            # 计算需要的总页数
            total_pages = math.ceil(total / 50)
            
            if total_pages > 1:
                print(f"⏳ 开始并发加载 {total_pages} 页...")
                remaining_pages = list(range(2, total_pages + 1))
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {
                        executor.submit(self._get_programs_page, book_id, version, p, 50): p
                        for p in remaining_pages
                    }
                    
                    completed = 0
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            result = future.result()
                            if result and result.get('programs'):
                                all_programs.extend(result['programs'])
                            completed += 1
                            if completed % 10 == 0:
                                print(f"⏳ 加载进度: {completed}/{len(remaining_pages)} 页")
                        except Exception as e:
                            print(f"❌ 加载章节页错误: {e}")
            
            # 按sequence排序
            all_programs.sort(key=lambda x: x.get('sequence', 0))
            
            # 转换为统一格式
            chapters = self._convert_programs_to_chapters(all_programs)
            
            print(f"✅ 章节加载完成，共 {len(chapters)} 章")
            self.chapters_loaded.emit(chapters)
            return chapters
            
        except Exception as e:
            print(f"❌ 获取章节失败: {e}")
            self.error_occurred.emit(f"获取章节失败: {e}")
            return []
    
    def _get_programs_page(self, book_id: str, version: str, page: int, pagesize: int = 50) -> Optional[Dict]:
        """获取单页节目列表"""
        try:
            url = f"{PROGRAMS_API}/{book_id}/programs/{version}"
            params = {
                'curpage': page,
                'pagesize': pagesize,
                'order': 'asc'
            }
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if 'data' in result:
                return {
                    'programs': result['data'].get('programs', []),
                    'total': result['data'].get('total', 0)
                }
            return None
            
        except Exception as e:
            print(f"❌ 获取第{page}页节目失败: {e}")
            return None
    
    def _convert_programs_to_chapters(self, programs: List[Dict]) -> List[Dict]:
        """转换节目为章节格式"""
        chapters = []
        for idx, prog in enumerate(programs, 1):
            duration = prog.get('duration', 0)
            minutes = duration // 60
            seconds = duration % 60
            
            chapter = {
                'id': str(prog.get('id', '')),
                'title': prog.get('title', f'第{idx}章'),
                'duration': f"{minutes:02d}:{seconds:02d}",
                'duration_seconds': duration,
                'sequence': prog.get('sequence', idx),
                'is_free': prog.get('isfree', True),
                'fee': prog.get('fee', 0),
                'order_num': idx,
                'platform': '蜻蜓FM'
            }
            chapters.append(chapter)
        return chapters
    
    # ==================== 音频下载 ====================
    def get_audio_url(self, book_id: str, program_id: str) -> Optional[str]:
        """获取音频URL - 使用官方签名算法"""
        print(f"🎵 获取音频URL: channel={book_id}, program={program_id}")
        
        timestamp = int(time.time() * 1000)
        path = f"/audiostream/redirect/{book_id}/{program_id}"
        query = f"access_token={self.access_token}&device_id=MOBILESITE&qingting_id={self.qingting_id}&t={timestamp}"
        sign_str = f"{path}?{query}"
        sign = hmac.new(AUDIO_SIGN_KEY.encode("utf-8"), sign_str.encode("utf-8"), hashlib.md5).hexdigest()
        
        audio_url = f"{AUDIO_BASE}{path}?{query}&sign={sign}"
        print(f"✅ 生成音频URL: {audio_url[:80]}...")
        
        self.audio_url_ready.emit(audio_url)
        return audio_url
    
    def download_audio(self, book_id: str, program_id: str, title: str, save_path: str = None, progress_callback=None) -> bool:
        """下载音频文件"""
        print(f"🎵 下载音频: {title}")
        
        try:
            # 获取音频URL
            audio_url = self.get_audio_url(book_id, program_id)
            if not audio_url:
                return False
            
            # 设置下载请求头
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.qtfm.cn/"
            }
            if self.cookie_str:
                headers["Cookie"] = self.cookie_str
            
            # 第一步：获取重定向后的真实URL
            r1 = requests.get(audio_url, headers=headers, allow_redirects=False, timeout=30)
            m4a_url = r1.headers.get("Location")
            
            if not m4a_url:
                print("❌ 未获取到音频地址")
                return False
            
            print(f"🔗 真实音频URL: {m4a_url[:80]}...")
            
            # 第二步：下载音频文件
            headers_m4a = {
                "User-Agent": headers["User-Agent"],
                "Referer": audio_url,
                "Accept": "*/*"
            }
            if self.cookie_str:
                headers_m4a["Cookie"] = self.cookie_str
            
            response = requests.get(m4a_url, headers=headers_m4a, stream=True, timeout=120)
            response.raise_for_status()
            total_size = int(response.headers.get('Content-Length') or 0)
            
            # 保存文件
            if not save_path:
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_', '.')).rstrip()
                save_path = f"{safe_title}.m4a"
            
            downloaded = 0
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            
            size_mb = downloaded / (1024 * 1024)
            print(f"✅ 下载完成: {save_path} ({size_mb:.2f}MB)")
            self.download_finished.emit(save_path)
            return True
            
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            self.error_occurred.emit(f"下载失败: {e}")
            return False
    
    # ==================== 扫码登录 ====================
    def generate_qr_code(self) -> bool:
        """生成登录二维码"""
        print("🔑 生成登录二维码...")
        
        try:
            url = "https://user.qtfm.cn/u2/api/v4/users/qrcode/generate"
            response = self.session.get(url, timeout=10)
            result = response.json()
            
            if result.get('errorno') == 0 and 'data' in result:
                code_id = result['data']['code_id']
                qr_url = result['data']['qrcode_url']
                
                self._current_code_id = code_id
                print(f"✅ 二维码生成成功: {code_id}")
                
                self.login_qr_generated.emit(code_id, qr_url)
                return True
            else:
                print(f"❌ 二维码生成失败: {result.get('errormsg', '未知错误')}")
                return False
                
        except Exception as e:
            print(f"❌ 二维码生成异常: {e}")
            return False
    
    def start_login_process(self) -> bool:
        """开始登录流程"""
        if not self.generate_qr_code():
            return False
        
        # 开始检查登录状态
        self.login_check_timer.start(3000)  # 每3秒检查一次
        return True
    
    def _auto_check_login(self):
        """自动检查登录状态"""
        if self._current_code_id:
            self.check_login_status(self._current_code_id)
    
    def check_login_status(self, code_id: str = None) -> bool:
        """检查登录状态"""
        if not code_id:
            code_id = self._current_code_id
        if not code_id:
            self.login_check_timer.stop()
            return False
        
        try:
            url = f"https://user.qtfm.cn/u2/api/v4/users/qrcode/status_query?code_id={code_id}"
            response = self.session.get(url, timeout=10)
            result = response.json()
            
            if result.get('errorno') == 0 and 'data' in result:
                data = result['data']
                status = data.get('qrcode_status', '')
                
                print(f"📊 登录状态: {status}")
                self.login_status_changed.emit(status)
                
                if status in ['success', 'confirmed', 'authorize']:
                    access_token = data.get('access_token', '')
                    qingting_id = data.get('qingting_id', '')
                    
                    if access_token and qingting_id:
                        self.access_token = access_token
                        self.qingting_id = qingting_id
                        self.is_logged_in = True
                        
                        # 构建Cookie字符串
                        self.cookie_str = f"access_token={access_token}; qingting_id={qingting_id}"
                        self.session.headers["Cookie"] = self.cookie_str
                        
                        print(f"✅ 扫码登录成功！")
                        self.login_check_timer.stop()
                        self._current_code_id = ''
                        
                        # 获取用户信息
                        self.get_user_profile()
                        
                        self.login_success.emit(access_token, qingting_id)
                        return True
                        
                elif status == 'expired':
                    print("⌛ 二维码已过期")
                    self.login_check_timer.stop()
                    self._current_code_id = ''
                    self.error_occurred.emit("二维码已过期，请重新生成")
                    return False
            
            return False
            
        except Exception as e:
            print(f"❌ 检查登录状态异常: {e}")
            return False
    
    def get_user_profile(self) -> Optional[Dict]:
        """获取用户详细信息"""
        if not self.qingting_id:
            return None
        
        try:
            url = f"https://user.qtfm.cn/u2/api/v4/user/{self.qingting_id}"
            headers = {
                'Authorization': f'Bearer {self.access_token}' if self.access_token else ''
            }
            
            response = self.session.get(url, headers=headers, timeout=10)
            result = response.json()
            
            if result.get('errorno') == 0 and 'data' in result:
                user_data = result['data']
                self.user_info.update({
                    'nick_name': user_data.get('nick_name', ''),
                    'userName': user_data.get('userName', ''),
                    'avatar': user_data.get('avatar', ''),
                    'qingting_id': user_data.get('qingting_id', ''),
                    'level': user_data.get('level', 0),
                })
                print(f"✅ 用户信息: {self.user_info['nick_name']}")
                return self.user_info
                
        except Exception as e:
            print(f"❌ 获取用户信息失败: {e}")
        
        return None
    
    # ==================== 状态管理 ====================
    def is_authenticated(self) -> bool:
        """检查是否已认证"""
        return self.is_logged_in and bool(self.access_token and self.qingting_id)
    
    def set_auth_info(self, access_token: str, qingting_id: str):
        """设置认证信息"""
        self.access_token = access_token
        self.qingting_id = qingting_id
        self.is_logged_in = True
        self.cookie_str = f"access_token={access_token}; qingting_id={qingting_id}"
        self.session.headers["Cookie"] = self.cookie_str
        print("✅ 蜻蜓FM认证信息已设置")
    
    def get_user_info(self) -> Dict:
        """获取用户信息"""
        return {
            'platform': '蜻蜓FM',
            'qingting_id': self.qingting_id,
            'nick_name': self.user_info.get('nick_name', ''),
            'is_logged_in': self.is_logged_in
        }
    
    def logout(self):
        """登出"""
        self.access_token = ''
        self.qingting_id = ''
        self.is_logged_in = False
        self.cookie_str = ''
        self.current_book = None
        self.current_chapters = []
        self.login_check_timer.stop()
        self._current_code_id = ''
        if "Cookie" in self.session.headers:
            del self.session.headers["Cookie"]
        print("👋 蜻蜓FM已登出")


# 单例模式
_qtfm_manager_instance = None

def get_qtfm_manager() -> QtfmManager:
    """获取蜻蜓FM管理器单例"""
    global _qtfm_manager_instance
    if _qtfm_manager_instance is None:
        _qtfm_manager_instance = QtfmManager()
    return _qtfm_manager_instance
