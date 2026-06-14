#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
from .ximalaya_manager import XimalayaManager
from .lrts_manager import LRTSManager
from .fanqie_manager import FanqieManager
from .qtfm_manager import QtfmManager
from src.features.qidian.audio_system import QidianAudioSystem


class SearchManager:
    """搜索管理器 - 支持喜马拉雅、懒人听书、番茄畅听、起点听书"""
    
    def __init__(self, cookie_manager=None):
        self.ximalaya_manager = XimalayaManager()
        self.lrts_manager = LRTSManager()
        self.fanqie_manager = FanqieManager()
        self.qtfm_manager = QtfmManager()  # 蜻蜓FM管理器
        self.cookie_manager = cookie_manager
        self.qidian_cookies = {}  # 起点Cookie字典
        
        # 🔧 为起点听书创建持久的session和headers
        import requests
        self.qidian_session = requests.Session()
        self.qidian_session.verify = False
        self.qidian_headers = {}
        
        # 🔧 为兼容download_worker.py，添加session别名指向qidian_session
        self.session = self.qidian_session
        
        # 如果有cookie_manager，只加载用户本地保存的Cookie。
        # 自用版不使用服务器代持Cookie或云端授权状态。
        if cookie_manager:
            xmly_server_cookie = cookie_manager.get_server_cookie_cache('xmly')
            xmly_cookie = xmly_server_cookie or cookie_manager.get_cookie('xmly')
            if xmly_cookie:
                if isinstance(xmly_cookie, dict):
                    xmly_cookie = '; '.join([f"{name}={value}" for name, value in xmly_cookie.items()])
                self.ximalaya_manager.set_cookie(xmly_cookie, is_server_cookie=bool(xmly_server_cookie))
            
            lrts_cookie = cookie_manager.get_cookie('lrts')
            if lrts_cookie:
                self.lrts_manager.set_cookie(lrts_cookie)
            
            # 加载起点Cookie（起点听书不支持服务器Cookie）
            qidian_cookie = cookie_manager.get_cookie('qidian')
            if qidian_cookie:
                self.set_qidian_cookie(qidian_cookie)

            qtfm_cookie = cookie_manager.get_cookie('qtfm')
            if qtfm_cookie:
                self.set_cookie('蜻蜓FM', qtfm_cookie)
    
    def _validate_svip_before_use_cookie(self):
        """自用版不使用服务器Cookie。"""
        return False
    
    def set_cookie(self, platform: str, cookie: str, is_server_cookie: bool = False):
        """设置平台Cookie
        
        Args:
            platform: 平台名称
            cookie: Cookie字符串
            is_server_cookie: 旧版兼容参数，自用版会忽略
        """
        if platform == '喜马拉雅' or platform == 'xmly':
            if isinstance(cookie, dict):
                cookie = '; '.join([f"{name}={value}" for name, value in cookie.items()])
            self.ximalaya_manager.set_cookie(cookie, is_server_cookie=is_server_cookie)
            print(f"🍪 已更新喜马拉雅本地Cookie: {len(str(cookie))} 字符")
        elif platform == '懒人听书' or platform == 'lrts':
            self.lrts_manager.set_cookie(cookie)
            print(f"🍪 已更新懒人听书本地Cookie: {len(str(cookie))} 字符")
        elif platform == '起点听书' or platform == 'qidian':
            self.set_qidian_cookie(cookie)
            print(f"🍪 已更新起点听书Cookie: {len(cookie)} 字符")
        elif platform == '蜻蜓FM' or platform == 'qtfm':
            if isinstance(cookie, dict):
                access_token = cookie.get('access_token', '')
                qingting_id = cookie.get('qingting_id', '')
                if access_token and qingting_id:
                    self.qtfm_manager.set_auth_info(access_token, qingting_id)
                    print("🍪 已更新蜻蜓FM扫码登录信息")
                else:
                    print("⚠️ 蜻蜓FM登录信息不完整")
            elif isinstance(cookie, str):
                self.qtfm_manager.set_cookie(cookie)
                print(f"🍪 已更新蜻蜓FMCookie: {len(cookie)} 字符")
    
    def set_qidian_cookie(self, cookie_data):
        """设置起点Cookie（支持字典或字符串格式）"""
        if isinstance(cookie_data, dict):
            self.qidian_cookies = cookie_data
        elif isinstance(cookie_data, str):
            # 解析Cookie字符串为字典
            self.qidian_cookies = {}
            for item in cookie_data.split(';'):
                item = item.strip()
                if '=' in item:
                    key, value = item.split('=', 1)
                    self.qidian_cookies[key.strip()] = value.strip()
        
        # 🔧 每次设置cookies时，更新headers
        self.qidian_headers = {
            'Platform': '10',
            'AppId': '50',
            'AreaId': '501000',
            'YwGuid': self.qidian_cookies.get('ywguid', ''),
            'YwKey': self.qidian_cookies.get('ywkey', ''),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
        }
    
    def check_vip_status(self) -> bool:
        """兼容旧调用：自用版不做授权或额度校验。"""
        print("✅ 自用版：下载不经过授权或额度校验")
        return True
    
    def search(self, keyword, platform='all'):
        """搜索专辑 - 使用真实API"""
        results = []
        
        if platform == 'all':
            platforms = ['喜马拉雅', '懒人听书', '番茄畅听', '起点听书', '蜻蜓FM']
        elif platform == '喜马拉雅':
            platforms = ['喜马拉雅']
        elif platform == '懒人听书':
            platforms = ['懒人听书']
        elif platform == '番茄畅听':
            platforms = ['番茄畅听']
        elif platform == '起点听书':
            platforms = ['起点听书']
        elif platform == '蜻蜓FM':
            platforms = ['蜻蜓FM']
        else:
            platforms = [platform]
            
        for plat in platforms:
            if plat == '喜马拉雅':
                # 使用真实的喜马拉雅API搜索
                ximalaya_results = self.ximalaya_manager.search_albums(keyword)
                results.extend(ximalaya_results)
            elif plat == '懒人听书':
                # 使用真实的懒人听书API搜索
                lrts_results = self.lrts_manager.search_books(keyword)
                results.extend(lrts_results)
            elif plat == '番茄畅听':
                # 使用真实的番茄畅听API搜索
                fanqie_results = self.fanqie_manager.search_books(keyword)
                results.extend(fanqie_results)
            elif plat == '起点听书':
                # 使用起点听书API搜索
                qidian_results = self.search_qidian(keyword)
                results.extend(qidian_results)
            elif plat == '蜻蜓FM':
                # 使用蜻蜓FM API搜索
                qtfm_results = self.qtfm_manager.search_books(keyword)
                results.extend(qtfm_results)
                
        return results
    
    def search_qidian(self, keyword):
        """搜索起点听书"""
        try:
            if not self.qidian_cookies:
                print(f"❌ 未登录起点听书，无法搜索")
                return []
            
            system = QidianAudioSystem(self.qidian_cookies)
            items = system.search(keyword, site=3, page_index=1, page_size=50)
            
            if items is None:
                print(f"❌ 起点API返回None，搜索失败")
                return []
            
            if not items:
                print(f"⚠️ 起点搜索: 未找到相关结果")
                print(f"   建议: 1. 确保已正确登录")
                print(f"         2. 尝试其他关键词")
                print(f"         3. 检查网络连接")
                print(f"   Cookie信息: {len(self.qidian_cookies)} 个字段")
                return []
            
            print(f"✅ 起点搜索成功: 找到 {len(items)} 个结果")
            
            # 转换结果格式
            results = []
            for idx, item in enumerate(items, 1):
                book_id = item.get('bookId')
                book_name = item.get('bookName', '未知')
                author_name = item.get('authorName', '未知')
                
                print(f"   [{idx}] {book_name} - {author_name} (ID: {book_id})")
                
                # 🔧 新增：立即获取每个结果的详情来补充 cover
                cover_url = ''
                try:
                    detail = system.get_audio_detail(book_id)
                    if detail:
                        cover_url = detail.get('CoverUrl', '')
                        print(f"       ✅ 已获取封面: {cover_url[:60]}...")
                    else:
                        print(f"       ⚠️ 未获取到详情")
                except Exception as e:
                    print(f"       ⚠️ 获取详情失败: {str(e)}")
                
                results.append({
                    'platform': '起点听书',
                    'id': book_id,
                    'title': book_name,
                    'author': author_name,
                    'cover': cover_url,  # ✅ 现在有了cover
                    'category': item.get('categoryName', ''),
                    'raw_data': item
                })
            
            return results
        except ImportError as e:
            print(f"❌ 起点搜索错误: 无法导入起点模块 - {str(e)}")
            import traceback
            traceback.print_exc()
            return []
        except Exception as e:
            print(f"❌ 起点搜索错误: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
        
    def get_album_chapters(self, album_id, platform):
        """获取专辑章节列表 - 使用真实API"""
        # 🔧 统一platform处理：支持英文代码和中文名称
        if platform in ['喜马拉雅', 'ximalaya']:
            # 使用真实的喜马拉雅API获取章节
            return self.ximalaya_manager.get_album_chapters(album_id)
        elif platform in ['懒人听书', 'lrts']:
            # 使用真实的懒人听书API获取章节
            return self.lrts_manager.get_chapters(album_id)
        elif platform in ['番茄畅听', 'fanqie']:
            # 使用真实的番茄畅听API获取章节
            return self.fanqie_manager.get_chapters(album_id)
        elif platform in ['起点听书', 'qidian']:
            # 使用起点有声系统获取章节
            return self.get_qidian_chapters(album_id)
        elif platform in ['蜻蜓FM', 'qtfm']:
            # 使用蜻蜓FM API获取章节
            # 需要先获取书籍详情以获取version，但新API可能不需要version
            book_detail = self.qtfm_manager.get_book_details(album_id)
            version = book_detail.get('version', '') if book_detail else None
            return self.qtfm_manager.get_chapters(album_id, version)
        else:
            return []

    def get_album_detail(self, album_id, platform):
        """获取专辑详情 - 使用真实API"""
        # 🔧 统一platform处理：支持英文代码和中文名称
        if platform in ['喜马拉雅', 'ximalaya']:
            return self.ximalaya_manager.get_album_detail(album_id)
        elif platform in ['懒人听书', 'lrts']:
            return self.lrts_manager.get_book_detail(album_id)
        elif platform in ['番茄畅听', 'fanqie']:
            return self.fanqie_manager.get_book_detail(album_id)
        elif platform in ['起点听书', 'qidian']:
            # 获取起点有声书详情
            return self.get_qidian_detail(album_id)
        else:
            return None
    
    def get_qidian_chapters(self, album_id):
        """获取起点有声章节列表"""
        try:
            if not self.qidian_cookies:
                print("❌ 未登录起点听书，无法获取章节")
                return []
            
            # 🔧 确保 album_id 是字符串格式
            album_id = str(album_id)
            print(f"📚 获取起点章节列表: album_id={album_id} (type={type(album_id).__name__})")
            
            system = QidianAudioSystem(self.qidian_cookies)
            all_chapters = []
            page = 1
            
            # 逐页获取章节
            while True:
                print(f"   正在获取第 {page} 页...")
                chapters, has_next = system.get_chapter_list(album_id, page)
                # 🔧 关键：chapters 可能是 None（当API返回错误时）
                if chapters:
                    print(f"   ✅ 第 {page} 页获取到 {len(chapters)} 章")
                    all_chapters.extend(chapters)
                else:
                    print(f"   ❌ 第 {page} 页获取失败 (chapters={chapters}, has_next={has_next})")
                    # 如果获取失败，停止继续获取
                    if not has_next or chapters is None:
                        break
                
                if not has_next:
                    print(f"   ✅ 已到达最后一页")
                    break
                page += 1
            
            if not all_chapters:
                print(f"⚠️ 未找到任何章节（总共尝试了 {page} 页）")
                return []
            
            # 转换为统一格式
            results = []
            for idx, chapter in enumerate(all_chapters, 1):
                results.append({
                    'id': chapter.get('Acid', f'acid_{idx}'),
                    'title': chapter.get('AudioChapterName', f'第{idx}章'),
                    'duration': chapter.get('Duration', 0),
                    'play_count': 0,
                    'index': idx,
                    'raw_data': chapter,
                    'platform': '起点听书'
                })
            
            print(f"✅ 成功获取 {len(results)} 章节")
            return results
            
        except Exception as e:
            print(f"❌ 获取起点章节错误: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_qidian_detail(self, album_id):
        """获取起点有声书详情"""
        try:
            if not self.qidian_cookies:
                print("❌ 未登录起点听书，无法获取详情")
                return None
            
            print(f"📖 正在获取详情: album_id={album_id}")
            print(f"   qidian_cookies 字段: {list(self.qidian_cookies.keys())}")
            print(f"   ywguid: {self.qidian_cookies.get('ywguid', 'N/A')[:20] if self.qidian_cookies.get('ywguid') else 'N/A'}...")
            print(f"   ywkey: {self.qidian_cookies.get('ywkey', 'N/A')[:20] if self.qidian_cookies.get('ywkey') else 'N/A'}...")
            
            system = QidianAudioSystem(self.qidian_cookies)
            detail = system.get_audio_detail(album_id)
            
            if not detail:
                print(f"⚠️ 无法获取起点有声书详情（API 返回无数据）")
                return None
            
            # 获取 CoverUrl 并补全（如果不完整）
            cover_url = detail.get('CoverUrl', '')
            if cover_url:
                # 如果 URL 不以 .jpg 或 .png 结尾，说明不完整，需要补全
                if not cover_url.endswith(('.jpg', '.png', '.jpeg', '.webp')):
                    # 尝试补全 URL - 起点的图片通常是 .jpg 格式
                    if '/' in cover_url:
                        # 提取文件名部分并补全
                        cover_url = f"{cover_url}59be.jpg"
                        print(f"   🔧 补全CoverUrl: {cover_url[:80]}...")
            
            # 转换为统一格式
            result = {
                'id': detail.get('Adid', album_id),
                'title': detail.get('AudioName', ''),
                'author': detail.get('AnchorName', ''),
                'intro': detail.get('Intro', ''),
                'cover': cover_url,
                'total_chapters': detail.get('AllAudioChapters', 0),
                'platform': '起点听书',
                'raw_data': detail
            }
            
            print(f"✅ 成功获取起点有声书详情: {result['title']}")
            print(f"   封面URL: {result['cover'][:80] if result['cover'] else '(无)'}")
            return result
            
        except Exception as e:
            print(f"❌ 获取起点详情错误: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_audio_urls(self, track_id, platform, book_id=None, chapter_data=None):
        """获取音频URL - 使用真实API"""
        if platform == '喜马拉雅':
            return self.ximalaya_manager.get_audio_urls(track_id)
        elif platform == '懒人听书':
            # 懒人听书需要book_id和chapter_id
            if book_id:
                url = self.lrts_manager.get_audio_url(book_id, track_id, chapter_data)
                return {'default': {'url': url}} if url else {}
            return {}
        elif platform == '番茄畅听':
            # 番茄畅听返回音频URL（传入 book_id 以便 AI 音色使用官方 playinfo API）
            url = self.fanqie_manager.get_audio_url(track_id, "无损真人录制", book_id)
            return {'default': {'url': url}} if url else {}
        elif platform == '起点听书':
            # 起点需要album_id和acid
            print(f"🔧 get_audio_urls 路由到起点听书:")
            print(f"   book_id={book_id}, track_id={track_id}")
            return self.get_qidian_audio_url(book_id, track_id)
        else:
            return {}
    
    def get_available_qualities(self, track_id, platform):
        """获取可用音质列表"""
        if platform == '喜马拉雅':
            return self.ximalaya_manager.get_available_qualities(track_id)
        elif platform == '懒人听书':
            # 懒人听书通常只有一种音质
            return [{'quality': 'default', 'size_mb': 0, 'type': 'MP3'}]
        elif platform == '番茄畅听':
            # 番茄畅听通常只有一种音质
            return [{'quality': 'default', 'size_mb': 0, 'type': 'MP3'}]
        elif platform == '起点听书':
            # 起点通常只有一种音质
            return [{'quality': 'default', 'size_mb': 0, 'type': 'MP3'}]
        else:
            return []
    
    def get_qidian_audio_url(self, album_id, acid):
        """获取起点有声音频URL - 直接调用API"""
        try:
            if not self.qidian_cookies:
                print("❌ 未登录起点听书")
                return {}
            
            if not album_id or not acid:
                print(f"❌ 缺少必要参数: album_id={album_id}, acid={acid}")
                return {}
            
            print(f"🔍 直接调用起点API获取音频:")
            print(f"   album_id(adid): {album_id}")
            print(f"   acid: {acid}")
            
            # 调试：检查cookies中的关键字段
            print(f"🔑 Cookies字段检查:")
            print(f"   ywguid: {'✅ 存在' if 'ywguid' in self.qidian_cookies else '❌ 缺失'}")
            print(f"   ywkey: {'✅ 存在' if 'ywkey' in self.qidian_cookies else '❌ 缺失'}")
            print(f"   ywOpenId: {'✅ 存在' if 'ywOpenId' in self.qidian_cookies else '❌ 缺失'}")
            print(f"   Cookies总数: {len(self.qidian_cookies)}")
            
            # 直接调用API，使用持久的session
            base_url = "https://qdcg.qidian.com"
            url = f"{base_url}/api/audio/play"
            params = {'adid': album_id, 'acid': acid, '_csrfToken': ''}
            
            print(f"📡 API请求（使用起点API headers）:")
            print(f"   URL: {url}")
            print(f"   参数: {params}")
            print(f"   YwGuid: {self.qidian_headers.get('YwGuid', 'N/A')[:20]}...")
            print(f"   YwKey: {self.qidian_headers.get('YwKey', 'N/A')[:20]}...")
            
            # 使用持久的session而不是创建新的
            response = self.qidian_session.get(
                url,
                params=params,
                headers=self.qidian_headers,
                cookies=self.qidian_cookies,
                timeout=10
            )
            
            print(f"📤 API响应: 状态码={response.status_code}")
            data = response.json()
            print(f"   响应数据: {data}")
            
            if data.get('Result') == 0:
                audio_url = data.get('Data', {}).get('AudioUrl')
                if audio_url:
                    print(f"✅ 成功获取音频URL: {audio_url[:100]}...")
                    return {'default': {'url': audio_url}}
                else:
                    print(f"⚠️ 响应中没有AudioUrl字段")
                    return {}
            else:
                print(f"❌ API返回错误: Result={data.get('Result')}, Message={data.get('Message')}")
                return {}
            
        except Exception as e:
            print(f"❌ 获取起点音频URL异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {}
    
    def download_audio(self, url, save_path, platform):
        """下载音频 - 使用真实API"""
        if platform == '喜马拉雅':
            return self.ximalaya_manager.download_audio(url, save_path)
        elif platform == '懒人听书':
            return self.lrts_manager.download_audio(url, save_path)
        elif platform == '番茄畅听':
            return self.fanqie_manager.download_audio(url, save_path)
        elif platform == '起点听书':
            return self.download_qidian_audio(url, save_path)
        else:
            return False
    
    def download_qidian_audio(self, url, save_path, progress_callback=None):
        """下载起点有声音频"""
        try:
            from pathlib import Path
            
            if not url or not save_path:
                print("❌ 缺少URL或保存路径")
                return False
            
            print(f"📥 开始下载起点音频...")
            print(f"   URL: {url[:80]}...")
            print(f"   保存位置: {save_path}")
            
            # 创建目录
            save_path_obj = Path(save_path)
            save_path_obj.parent.mkdir(parents=True, exist_ok=True)
            
            # 🔧 使用持久的session下载，参考参考文件的做法
            # 先发送HEAD请求检查
            head_resp = self.qidian_session.head(
                url,
                timeout=10,
                verify=False,
                allow_redirects=True
            )
            if head_resp.status_code != 200:
                print(f"❌ HEAD请求失败: {head_resp.status_code}")
                return False
            
            # 发送GET请求下载
            get_resp = self.qidian_session.get(
                url,
                timeout=60,
                verify=False,
                allow_redirects=True,
                stream=True
            )
            
            if get_resp.status_code not in [200, 206]:
                print(f"❌ 下载失败，状态码: {get_resp.status_code}")
                return False
            
            # 获取文件大小
            total_size = int(get_resp.headers.get('content-length', 0))
            
            # 保存文件
            with open(save_path, 'wb') as f:
                downloaded = 0
                for chunk in get_resp.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            print(f"\r   进度: {progress:.1f}%", end='', flush=True)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            
            size = save_path_obj.stat().st_size / (1024 * 1024)
            print(f"\n✅ 下载完成: {size:.2f}MB")
            return True
            
        except Exception as e:
            print(f"❌ 下载起点音频失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_qidian_user_account(self):
        """获取起点听书账户信息"""
        try:
            url = "https://wxapp.qidian.com/api/bookShelf/account"
            response = self.qidian_session.get(
                url,
                headers=self.qidian_headers or None,
                cookies=self.qidian_cookies or None,
                timeout=10,
                verify=False,
            )
            data = response.json()
            
            if data.get('code') == 0:
                print("✅ 获取起点账户信息成功")
                return data.get('data', {})
            else:
                print(f"❌ 获取账户失败: {data.get('msg', '未知错误')}")
                return {}
        except Exception as e:
            print(f"❌ 获取起点账户异常: {str(e)}")
            return {}
    
    def get_qidian_bookshelf(self, page=1, pageSize=20):
        """获取起点听书的书架列表 - 只提取有声书"""
        try:
            if not self.qidian_cookies:
                print(f"⚠️ 警告：qidian_cookies为空！当前cookies: {self.qidian_cookies}")
                print(f"⚠️ 尝试从cookie_manager重新加载...")
                if self.cookie_manager:
                    qidian_cookie = self.cookie_manager.get_cookie('qidian')
                    if qidian_cookie:
                        print(f"✅ 从cookie_manager重新加载成功")
                        self.set_qidian_cookie(qidian_cookie)
                    else:
                        print(f"❌ cookie_manager中没有qidian cookies")
                        return [], {}
            
            url = "https://wxapp.qidian.com/api/bookShelf/list"
            params = {
                'page': page,
                'pageSize': pageSize,
            }
            
            # 🔧 关键：完整的headers配置
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-site',
            }
            
            print(f"📡 发送API请求:")
            print(f"   URL: {url}")
            print(f"   Params: {params}")
            print(f"   Cookies: {list(self.qidian_cookies.keys()) if self.qidian_cookies else '空'}")
            if self.qidian_cookies:
                print(f"   关键cookies: ywguid={self.qidian_cookies.get('ywguid', 'N/A')[:20] if self.qidian_cookies.get('ywguid') else 'N/A'}")
            
            # 使用更新的headers，而不是仅使用qidian_headers
            response = self.qidian_session.get(
                url, 
                params=params, 
                headers=headers,
                cookies=self.qidian_cookies,
                timeout=10,
                verify=False
            )
            
            print(f"📤 API响应状态码: {response.status_code}")
            
            try:
                data = response.json()
                print(f"📊 API响应: {data}")
            except Exception:
                print(f"📊 API响应（非JSON）: {response.text[:200]}")
                return [], {}
            
            if data.get('code') == 0:
                books = data.get('data', {}).get('booksInfo', [])
                page_info = data.get('data', {}).get('pageInfo', {})
                
                print(f"📚 原始书籍数据: 总共 {len(books)} 本")
                print(f"🔍 筛选有声书（bookType == 2）:")
                
                # 🔧 关键：只提取有声书（bookType == 2），过滤掉文字小说（bookType == 1）
                audio_books = []
                seen_book_ids = set()  # 🔧 用于去重
                
                for book in books:
                    book_type = book.get('bookType', 0)
                    book_name = book.get('bookName', '未知')
                    book_id = book.get('bookId')
                    
                    # 🔧 去重检查
                    if book_id in seen_book_ids:
                        print(f"   ⏭️  {book_name} (重复，已跳过)")
                        continue
                    
                    if book_type == 2:
                        # ✅ 这是有声书
                        seen_book_ids.add(book_id)
                        audio_books.append(book)
                        print(f"   ✅ {book_name} (bookType={book_type})")
                    else:
                        # ❌ 过滤掉文字小说
                        print(f"   ❌ {book_name} (bookType={book_type})")
                
                print(f"✅ 获取起点书架成功: 总共 {len(books)} 本，有声书 {len(audio_books)} 本")
                
                # 转换为统一格式（只返回有声书）
                result = []
                for book in audio_books:
                    # 尝试获取章节数和主播名称
                    total_chapters = 0
                    anchor_name = book.get('authorName') or '未知作者'  # 先用书籍的authorName
                    
                    try:
                        # 🔧 使用get_qidian_detail来获取详情
                        detail = self.get_qidian_detail(str(book.get('bookId')))
                        if detail:
                            # 获取章节数
                            if detail.get('total_chapters'):
                                total_chapters = detail.get('total_chapters', 0)
                                print(f"   ✅ 获取到 {book.get('bookName')} 的章节数: {total_chapters}")
                            
                            # 🔧 如果没有作者名，从主播名称获取
                            if not book.get('authorName'):
                                anchor_name = detail.get('author', '未知作者')
                                print(f"   ✅ 获取到 {book.get('bookName')} 的主播: {anchor_name}")
                    except Exception as e:
                        print(f"   ⚠️ 获取详情失败: {e}")
                    
                    result.append({
                        'id': book.get('bookId'),
                        'title': book.get('bookName', '未知'),
                        'author': anchor_name,  # 🔧 使用提取的主播/作者名
                        'cover': book.get('coverUrl', ''),
                        'last_chapter': book.get('lastChapterName', '未知'),
                        'update_time': book.get('updateTime', '未知'),
                        'track_count': total_chapters,  # 🔧 添加章节数
                        'platform': '起点听书',
                        'raw_data': book
                    })
                
                return result, page_info
            else:
                error_msg = data.get('msg', '未知错误')
                print(f"❌ 获取书架失败: {error_msg}")
                print(f"🔍 完整响应: {data}")
                return [], {}
        except Exception as e:
            print(f"❌ 获取起点书架异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return [], {}
    
    def diagnose_qidian_status(self):
        """诊断起点听书状态"""
        print("\n" + "="*60)
        print("🔍 起点听书诊断信息")
        print("="*60)
        
        print(f"\n1️⃣ Cookies状态:")
        print(f"   qidian_cookies类型: {type(self.qidian_cookies)}")
        print(f"   qidian_cookies为空: {not self.qidian_cookies}")
        if self.qidian_cookies:
            print(f"   cookies字段数: {len(self.qidian_cookies)}")
            print(f"   cookies字段名: {list(self.qidian_cookies.keys())}")
            print(f"   ywguid: {self.qidian_cookies.get('ywguid', 'N/A')[:30] if self.qidian_cookies.get('ywguid') else 'N/A'}")
            print(f"   ywkey: {self.qidian_cookies.get('ywkey', 'N/A')[:30] if self.qidian_cookies.get('ywkey') else 'N/A'}")
            print(f"   ywOpenId: {self.qidian_cookies.get('ywOpenId', 'N/A')[:30] if self.qidian_cookies.get('ywOpenId') else 'N/A'}")
        
        print(f"\n2️⃣ Session状态:")
        print(f"   qidian_session: {self.qidian_session}")
        print(f"   qidian_session.cookies: {dict(self.qidian_session.cookies)}")
        
        print(f"\n3️⃣ Headers状态:")
        print(f"   qidian_headers字段数: {len(self.qidian_headers)}")
        if self.qidian_headers:
            print(f"   qidian_headers: {list(self.qidian_headers.keys())}")
        
        print(f"\n4️⃣ Cookie Manager状态:")
        if self.cookie_manager:
            print(f"   cookie_manager存在: ✅")
            qidian_cookie = self.cookie_manager.get_cookie('qidian')
            print(f"   从cookie_manager获取的cookie类型: {type(qidian_cookie)}")
            if isinstance(qidian_cookie, dict):
                print(f"   从cookie_manager获取的cookie字段: {list(qidian_cookie.keys())}")
        else:
            print(f"   cookie_manager存在: ❌")
        
        print("\n" + "="*60 + "\n")
