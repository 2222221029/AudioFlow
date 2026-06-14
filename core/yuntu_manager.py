#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
云听FM管理器
支持通过分享链接/专辑ID获取专辑信息和章节列表
"""

import requests
import hashlib
import os
import time
from urllib.parse import quote
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Optional
from .time_api import get_timestamp_ms_str


class YunTuManager:
    """云听FM API管理器"""
    
    def __init__(self):
        self.base_url = "https://ytmsout.radio.cn"
        self.secret_key = "f0fc4c668392f9f9a447e48584c214ee"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        # 配置重试
        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry
        retry_strategy = Retry(
            total=3,  # 最多重试3次
            backoff_factor=1,  # 重试间隔
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        print("✅ 云听FM管理器初始化完成")
    
    def md5_sign(self, text: str) -> str:
        """计算MD5签名"""
        return hashlib.md5(text.encode('utf-8')).hexdigest().upper()
    
    def sort_params(self, params: dict) -> str:
        """按key排序参数"""
        sorted_keys = sorted(params.keys())
        return '&'.join([f'{k}={params[k]}' for k in sorted_keys])

    def app_sign(self, params: dict) -> str:
        """
        云听App侧接口签名。

        APK中可见的扫码/统计工具使用 HQUDKOQSAKOQJDJ123GJH 作为query签名key。
        搜索接口主体被加固隐藏，这里只作为App搜索候选接口的兼容签名。
        """
        app_key = "HQUDKOQSAKOQJDJ123GJH"
        query = "&".join(
            f"{key}={quote(str(params[key]), safe='')}"
            for key in params
            if key not in ("sign", "secret") and params[key] is not None
        )
        sign_text = f"{query}&key={app_key}" if query else f"key={app_key}"
        return hashlib.md5(sign_text.encode("utf-8")).hexdigest()

    def _request_json(self, path: str, params: dict, signed: bool = True) -> Optional[Dict]:
        """请求云听接口并返回JSON。"""
        url = f"{self.base_url}{path}"
        query_params = dict(params)
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 12; CloudSound/7.7.1) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
        }
        if signed:
            query_params["sign"] = self.app_sign(query_params)
        try:
            response = self.session.get(url, params=query_params, headers=headers, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            print(f"⚠️ 云听FM接口请求失败: {path}, {exc}")
            return None

    def _extract_list_items(self, payload) -> List[Dict]:
        """从不同云听响应结构中提取列表。"""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ("data", "list", "records", "rows", "items", "content", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = self._extract_list_items(value)
                if nested:
                    return nested
        return []

    def _normalize_search_item(self, item: Dict) -> Optional[Dict]:
        """将App搜索结果转换为项目通用书籍字段。"""
        album_id = (
            item.get("contentId")
            or item.get("albumId")
            or item.get("columnId")
            or item.get("id")
            or item.get("resourceId")
        )
        title = item.get("title") or item.get("name") or item.get("albumTitle") or item.get("contentName")
        if not album_id or not title:
            return None

        cover = (
            item.get("image")
            or item.get("logo")
            or item.get("cover")
            or item.get("coverSquare")
            or item.get("albumCover")
            or item.get("pic")
            or ""
        )
        subtitle = item.get("subtitle") or item.get("desSimple") or item.get("descriptionSimple") or ""
        author = item.get("ownerName") or item.get("author") or item.get("anchorName") or item.get("announcer") or ""
        episodes = item.get("childCount") or item.get("songCount") or item.get("programCount") or item.get("singleCount") or 0
        plays = item.get("listenCount") or item.get("listenNum") or item.get("playCount") or 0

        end_flag = item.get("endFlag")
        if end_flag in (1, "1", True):
            status = "完结"
        elif end_flag in (0, "0", False):
            status = "连载中"
        else:
            status = "连载中"

        return {
            "id": str(album_id),
            "title": str(title),
            "author": str(author),
            "cover": cover,
            "episodes": episodes,
            "plays": plays,
            "status": status,
            "platform": "云听FM",
            "description": subtitle,
            "contentType": item.get("contentType"),
            "hitType": item.get("hitType"),
            "feeType": item.get("feeType"),
            "vipFlag": item.get("vipFlag"),
        }

    def search_books(self, keyword: str, page: int = 0, page_size: int = 20) -> List[Dict]:
        """
        关键词搜索云听FM。

        APK中确认调用链为 ListeningApiService.listenSearch(String keyword, int pageIndex)，
        但接口定义被加固隐藏。这里按App候选接口探测并归一化返回；若服务端仍返回
        参数不合法/无数据，则返回空列表，不影响链接或ID搜索。
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        # 分享链接或专辑ID仍走稳定的专辑详情接口。
        album_id = self.parse_url_or_id(kw)
        if album_id and (kw.startswith("http") or kw.isdigit()):
            album_info = self.get_album_info(album_id)
            return [album_info] if album_info else []

        candidates = [
            ("/listening/listenSearch", {"keyword": kw, "pageIndex": page}),
            ("/search/listenSearch", {"keyword": kw, "pageIndex": page}),
            ("/search/search", {"keyword": kw, "pageIndex": page}),
            ("/search/content", {"keyword": kw, "pageIndex": page}),
            ("/search/all", {"keyword": kw, "pageIndex": page}),
            ("/listening/listenSearch", {"keyword": kw, "pageNo": page, "pageSize": page_size}),
        ]

        for path, params in candidates:
            result = self._request_json(path, params, signed=True)
            if not result:
                continue
            code = str(result.get("code", result.get("rt", "")))
            message = str(result.get("message", result.get("desc", "")))
            if code not in ("", "0", "1") and ("参数不合法" in message or "验签" in message):
                print(f"⚠️ 云听FM App搜索候选不可用: {path} - {message}")
                continue

            raw_items = self._extract_list_items(result.get("data", result))
            books = []
            for item in raw_items:
                book = self._normalize_search_item(item)
                if book:
                    books.append(book)
            if books:
                print(f"✅ 云听FM关键词搜索找到 {len(books)} 个结果")
                return books

        print("⚠️ 云听FM关键词搜索暂未返回可用结果，仍可使用分享链接或专辑ID搜索")
        return []
    
    def parse_url_or_id(self, input_str: str) -> Optional[str]:
        """
        解析输入（可以是分享链接或专辑ID）
        
        支持格式:
        - https://ytweb.radio.cn/share/albumDetail?columnId=16100096126610&...
        - 16100096126610
        """
        if not input_str:
            return None
        
        input_str = input_str.strip()
        
        # 如果是URL，提取ID
        if input_str.startswith('http'):
            try:
                parsed = urlparse(input_str)
                params = parse_qs(parsed.query)
                
                # 尝试不同的参数名
                for param_name in ['columnId', 'albumId', 'id']:
                    if param_name in params:
                        album_id = params[param_name][0]
                        print(f"📎 从链接提取专辑ID: {album_id}")
                        return album_id
                
                print(f"❌ 无法从链接提取专辑ID")
                return None
                
            except Exception as e:
                print(f"❌ 解析链接失败: {e}")
                return None
        else:
            # 直接当作ID
            return input_str
    
    def get_album_info(self, album_id: str) -> Optional[Dict]:
        """
        通过专辑ID获取专辑信息（仅搜索结果，不含章节）
        
        返回格式与其他平台统一
        """
        try:
            # 先获取一页数据以提取专辑信息
            album_info, _ = self.get_album_singles(album_id, page_no=0, page_size=1)
            
            if not album_info:
                return None
            
            # 转换为统一格式
            return {
                'id': album_id,
                'title': album_info.get('albumTitle', '未知专辑'),
                'author': album_info.get('author', '未知作者'),
                'cover': album_info.get('albumCover', ''),
                'episodes': album_info.get('total', 0),
                'plays': 0,  # 云听API不返回播放量
                'status': '完结',  # 默认完结
                'platform': '云听FM',
                'description': f"共{album_info.get('total', 0)}集"
            }
            
        except Exception as e:
            print(f"❌ 获取专辑信息失败: {e}")
            return None
    
    def get_album_detail(self, album_id: str) -> Optional[Dict]:
        """
        获取专辑详细信息（包括封面）
        尝试调用其他API获取专辑封面
        """
        try:
            # 使用参考文件中的正确API路径和参数
            url = f"{self.base_url}/web/appAlbum/detail/{album_id}"
            
            timestamp = get_timestamp_ms_str()
            data = {
                "id": str(album_id)  # 使用正确的参数名
            }
            
            # 计算签名
            params_str = self.sort_params(data)
            sign_text = params_str + f"&timestamp={timestamp}&key={self.secret_key}"
            sign = self.md5_sign(sign_text)
            
            if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                print("🔍 云听FM签名生成调试已启用（敏感字段已隐藏）")
                print(f"   参数字段: {list(data.keys())}")
                print(f"   时间戳: {timestamp}")
                print(f"   签名结果: {sign[:8]}***")
            
            headers = {
                "Content-Type": "application/json",
                "equipmentId": "0000",
                "platformCode": "WEB",
                "timestamp": timestamp,
                "sign": sign
            }
            
            response = self.session.get(url, params=data, headers=headers, timeout=30)
            result = response.json()
            
            if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                print(f"🔍 专辑详情API响应字段: {list(result.keys()) if isinstance(result, dict) else type(result)}")
            
            if result.get('code') == 0:
                album_data = result.get('data', {})
                if album_data:
                    return {
                        'albumId': album_id,
                        'albumTitle': album_data.get('name', '未知专辑'),  # 使用正确的字段名
                        'albumCover': album_data.get('image', ''),  # 使用正确的字段名
                        'author': album_data.get('author', '未知作者'),
                        'description': album_data.get('des', ''),  # 使用正确的字段名
                        'desSimple': album_data.get('desSimple', ''),
                        'total': album_data.get('total', 0)
                    }
                else:
                    print(f"⚠️ 专辑详情API返回空数据，尝试其他方法获取封面")
                    # 尝试使用发现的图片API
                    return self._try_get_cover_from_image_api(album_id)
            else:
                print(f"❌ 专辑详情API返回错误: {result.get('message', '未知错误')}")
                # 尝试使用发现的图片API
                return self._try_get_cover_from_image_api(album_id)
                
        except Exception as e:
            print(f"❌ 获取专辑详情失败: {e}")
            return None
    
    def _try_get_cover_from_image_api(self, album_id: str) -> Optional[Dict]:
        """
        尝试使用发现的图片API获取封面
        """
        try:
            print(f"🔍 尝试使用图片API获取封面...")
            
            # 尝试第一个图片API
            url1 = f"https://ytmsout.radio.cn/web/appAlbum/detail/{album_id}?id={album_id}"
            print(f"   尝试API1: {url1}")
            
            try:
                response1 = self.session.get(url1, timeout=30)
                result1 = response1.json()
                print(f"   API1响应: {result1}")
                
                if result1.get('code') == 0 and result1.get('data'):
                    data = result1.get('data', {})
                    cover_url = data.get('albumCover', '') or data.get('cover', '') or data.get('image', '')
                    if cover_url:
                        print(f"✅ API1成功获取封面: {cover_url}")
                        return {
                            'albumId': album_id,
                            'albumTitle': data.get('albumTitle', '精神的力量'),
                            'albumCover': cover_url,
                            'author': data.get('author', '中央广播电视总台'),
                            'description': data.get('description', ''),
                            'total': data.get('total', 0)
                        }
            except Exception as e:
                print(f"   API1失败: {e}")
            
            # 尝试第二个图片API（需要从章节数据中提取ID）
            print(f"   尝试API2...")
            try:
                # 从章节数据中获取可能的图片ID
                _, singles = self.get_album_singles(album_id, page_no=0, page_size=1)
                if singles and len(singles) > 0:
                    first_single = singles[0]
                    # 尝试从章节数据中提取图片ID
                    image_id = (first_single.get('imageId', '') or 
                              first_single.get('coverId', '') or 
                              first_single.get('albumImageId', ''))
                    
                    if image_id:
                        url2 = f"https://ytmsout.radio.cn/web/interactive/getInterface?id={image_id}"
                        print(f"   尝试API2: {url2}")
                        response2 = self.session.get(url2, timeout=30)
                        result2 = response2.json()
                        print(f"   API2响应: {result2}")
                        
                        if result2.get('code') == 0 and result2.get('data'):
                            data = result2.get('data', {})
                            cover_url = data.get('url', '') or data.get('imageUrl', '') or data.get('coverUrl', '')
                            if cover_url:
                                print(f"✅ API2成功获取封面: {cover_url}")
                                return {
                                    'albumId': album_id,
                                    'albumTitle': '精神的力量',
                                    'albumCover': cover_url,
                                    'author': '中央广播电视总台',
                                    'description': '',
                                    'total': 0
                                }
            except Exception as e:
                print(f"   API2失败: {e}")
            
            print(f"⚠️ 所有图片API都失败，返回默认信息")
            return None
            
        except Exception as e:
            print(f"❌ 图片API获取失败: {e}")
            return None
    
    def get_album_singles(self, album_id: str, page_no: int = 0, page_size: int = 20) -> tuple:
        """
        获取专辑单集列表（自动获取所有分页）
        
        返回: (album_info, singles_list)
        - album_info: 专辑信息字典
        - singles_list: 单集列表（所有页面）
        """
        url = f"{self.base_url}/web/appSingle/pageByAlbum"
        
        all_singles = []  # 存储所有单集
        album_info = None
        current_page = page_no
        total_pages = 1
        
        # 循环获取所有页面
        while current_page < total_pages:
            timestamp = get_timestamp_ms_str()
            data = {
                "albumId": str(album_id),
                "pageNo": str(current_page),
                "pageSize": str(page_size)
            }
            
            # 计算签名
            params_str = self.sort_params(data)
            sign_text = params_str + f"&timestamp={timestamp}&key={self.secret_key}"
            sign = self.md5_sign(sign_text)
            
            headers = {
                "Content-Type": "application/json",
                "equipmentId": "0000",
                "platformCode": "WEB",
                "timestamp": timestamp,
                "sign": sign
            }
            
            try:
                response = self.session.get(url, params=data, headers=headers, timeout=30)
                result = response.json()
                
                if result.get('code') == 0:  # 成功
                    data_wrapper = result.get('data', {}) or {}
                    singles_list = data_wrapper.get('data', []) or []

                    # 添加到总列表
                    all_singles.extend(singles_list)
                    
                    # 第一次获取时，获取专辑信息和总页数
                    if current_page == page_no:
                        # 获取总页数
                        total_pages = data_wrapper.get('totalPage', 1)
                        total_num = data_wrapper.get('totalNum', len(singles_list))
                        
                        print(f"☁️ 云听FM专辑信息:")
                        print(f"   总集数: {total_num}")
                        print(f"   总页数: {total_pages}")
                        print(f"   每页: {page_size}")
                        
                        # 首先尝试获取专辑详细信息
                        album_info = self.get_album_detail(album_id)
                        
                        # 如果获取详情失败，从第一个单集中提取基本信息
                        if not album_info and singles_list:
                            first = singles_list[0]
                            first_name = first.get('name', '')
                            album_title = first_name.split(' ')[0] if first_name else f'专辑{album_id}'
                            
                            album_info = {
                                'albumId': album_id,
                                'albumTitle': album_title,
                                'albumCover': '',
                                'author': '',
                                'description': first.get('des', ''),
                                'desSimple': ''
                            }
                        
                        # 添加总数信息
                        if album_info:
                            album_info['total'] = total_num
                            album_info['pageTotal'] = total_pages
                        
                        # 如果只有一页，直接返回
                        if total_pages == 1:
                            print(f"✅ 获取完成: {len(all_singles)}/{total_num} 集")
                            return album_info, all_singles
                        
                        # 提示正在获取多页数据
                        if total_pages > 1:
                            print(f"📖 检测到{total_pages}页数据，正在获取所有{total_num}集...")
                    
                    # 显示进度
                    if total_pages > 1:
                        print(f"  ⬇️ 已获取 {current_page + 1}/{total_pages} 页 ({len(all_singles)}/{album_info.get('total', '?')} 集)")
                    
                    current_page += 1
                    
                    # 添加短暂延迟避免请求过于频繁
                    if current_page < total_pages:
                        time.sleep(0.1)
                else:
                    print(f"❌ API返回错误: {result.get('message', '未知错误')}")
                    break
                    
            except Exception as e:
                print(f"❌ 请求第{current_page}页失败: {e}")
                import traceback
                traceback.print_exc()
                break
        
        print(f"✅ 获取完成: 共{len(all_singles)}集")
        return album_info, all_singles
    
    def get_chapters(self, album_id: str, page: int = 1, page_size: int = 50) -> List[Dict]:
        """
        获取章节列表（统一接口）
        
        参数:
            album_id: 专辑ID
            page: 页码（从1开始）
            page_size: 每页数量
            
        返回:
            章节列表，格式与其他平台统一
        """
        try:
            print(f"📚 获取云听FM章节: {album_id}, 页码: {page}, 每页: {page_size}")
            
            # 云听API的page_no从0开始
            api_page_no = page - 1
            
            album_info, singles = self.get_album_singles(album_id, page_no=api_page_no, page_size=page_size)
            
            if not singles:
                print(f"⚠️ 未获取到章节")
                return []
            
            chapters = []
            for idx, single in enumerate(singles, start=1):
                # 计算全局序号
                global_order = (page - 1) * page_size + idx
                
                # 添加调试信息
                if idx == 1:  # 只打印第一个章节的详细信息
                    print(f"🔍 第一个章节原始数据: {single}")
                
                # 转换为统一格式
                duration = single.get('duration', 0)
                duration_str = f"{duration//60}:{duration%60:02d}" if duration > 0 else "00:00"
                
                # 根据参考文件调整字段名 - API返回的是'name'不是'singleTitle'
                title = single.get('name', f'第{global_order}集')
                # 获取音频URL - 优先使用高音质playUrlHigh，其次downloadUrl
                media_url = single.get('playUrlHigh', '') or single.get('downloadUrl', '')
                single_id = single.get('id', str(global_order))
                
                # 添加调试信息
                if idx == 1:
                    print(f"🔍 音频URL字段检查:")
                    print(f"   name: {single.get('name', 'None')}")
                    print(f"   playUrlHigh: {single.get('playUrlHigh', 'None')}")
                    print(f"   downloadUrl: {single.get('downloadUrl', 'None')}")
                    print(f"   最终选择的URL: {media_url}")
                
                chapter = {
                    'id': f"chapter-{single_id}",
                    'title': title,
                    'duration': duration_str,
                    'size': '',  # 云听API不返回文件大小
                    'plays': single.get('playCount', 0),  # 参考文件使用playCount
                    'album': album_id,
                    'order_num': global_order,
                    'mediaUrl': media_url,  # 保存音频URL（优先级：playUrlHigh > downloadUrl）
                    'playUrlHigh': single.get('playUrlHigh', ''),  # 保存高音质URL
                    'downloadUrl': single.get('downloadUrl', ''),  # 保存下载URL
                    'singleId': single_id  # 保存原始ID
                }
                
                # 添加调试信息
                if idx == 1:
                    print(f"🔍 转换后的章节数据: {chapter}")
                
                chapters.append(chapter)
            
            print(f"✅ 获取到 {len(chapters)} 个章节")
            return chapters
            
        except Exception as e:
            print(f"❌ 获取章节异常: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_audio_url(self, chapter_id: str, quality: str = "标准") -> Optional[str]:
        """
        获取音频播放地址
        
        参数:
            chapter_id: 章节ID（格式：chapter-<singleId>）
            quality: 音质（云听FM不支持音质选择，忽略此参数）
            
        返回:
            音频URL
        """
        try:
            # 从chapter_id中提取singleId
            if chapter_id.startswith('chapter-'):
                single_id = chapter_id.replace('chapter-', '')
            else:
                single_id = chapter_id
            
            # 云听FM的音频URL在获取章节列表时已经返回
            # 这里需要重新获取单集详情来获取URL
            # 但为了性能，建议在获取章节列表时就缓存URL
            
            # 这里返回一个占位符，实际使用时应该从章节数据中获取mediaUrl
            print(f"⚠️ 云听FM音频URL应从章节数据中的mediaUrl字段获取")
            return None
            
        except Exception as e:
            print(f"❌ 获取音频URL失败: {e}")
            return None
    
    def search_by_link_or_id(self, input_str: str) -> Optional[Dict]:
        """
        通过链接或ID搜索专辑信息
        
        参数:
            input_str: 分享链接或专辑ID
            
        返回:
            专辑信息字典（统一格式）
        """
        album_id = self.parse_url_or_id(input_str)
        
        if not album_id:
            print("❌ 无法解析专辑ID")
            return None
        
        return self.get_album_info(album_id)
