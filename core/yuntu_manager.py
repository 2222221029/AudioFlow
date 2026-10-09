#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
云听FM管理器
支持关键词搜索专辑（App通道签名已逆向复现），以及分享链接/专辑ID获取专辑信息和章节列表

搜索接口（2026-10 逆向成果，接口/云听/搜索接口分析报告.md）：
    GET https://ytmsout.radio.cn/search/search/findSearchResourceList
        ?keyWord=<词>&searchTabId=<tab>&contentType=<int>&pageNo=<int>&sortType=<int>
    - pageNo 为 0 基（第 0 页是首页，实测 page=1 起恒空，这是网关分页语义）
    - searchTabId: 2=专辑(见 /search/searchTab/allByProductId)；'' 为综合tab
    - productId 公共头决定服务端数据域，缺失时 totalNum 恒 0
    签名: MD5(参数按key排序 k=v 以&连接 + '&timestamp=' + 毫秒 + '&key=' + APP_SALT).upper()
          无参数时: MD5('timestamp=' + 毫秒 + '&key=' + APP_SALT)
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

    # ms 网关 App 通道（搜索模块盐，ApiConfig.SECRET_KEY_MAP.search 逆向所得）
    APP_SALT = "68e251be8b49f462be367df22b212ee3"
    # 云听安卓产品线标识；缺失时服务端数据域为空（totalNum 恒 0）
    APP_PRODUCT_ID = "1605403829833195520"
    APP_VERSION_ID = "7.9.0.24845"
    # 设备指纹：网关只校验「存在且非空」，任意唯一值均可；支持环境变量覆盖
    APP_EQUIPMENT_ID = os.getenv("AUDIOFLOW_YUNTING_EQUIPMENT_ID") or "3663906640007"
    APP_UUID = os.getenv("AUDIOFLOW_YUNTING_UUID") or "c7b2c22a-4b1b-4fc1-a09b-4543ab660b5a"
    # 搜索Tab配置（/search/searchTab/allByProductId）：2=专辑/1=主播/3=单集/4=电台/5=栏目/6=精选
    SEARCH_PATH = "/search/search/findSearchResourceList"

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

    # ------------------------------------------------------------------
    # App 通道（ms 网关裸路径）签名与请求
    # ------------------------------------------------------------------
    def _app_sign(self, params: Optional[Dict], timestamp: str) -> str:
        """App 通道签名。

        - 有参数：MD5(按key排序的 k=v 以&连接 + '&timestamp=' + ts + '&key=' + 盐).upper()
        - 无参数：MD5('timestamp=' + ts + '&key=' + 盐)，无前导 &
        """
        if params:
            base = self.sort_params(params)
            text = f"{base}&timestamp={timestamp}&key={self.APP_SALT}"
        else:
            text = f"timestamp={timestamp}&key={self.APP_SALT}"
        return self.md5_sign(text)

    def _app_headers(self, timestamp: str, sign: str) -> Dict:
        """App 公共头（HeaderCommonParamsInterceptor 注入的 16 项）。"""
        return {
            'User-Agent': 'okhttp/4.10.0',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'boxModel': '2509FPN0BC',
            'productId': self.APP_PRODUCT_ID,
            'hardNo': '9',
            'appSourceType': '1',
            'channel': 'cnrradio',
            'equipmentId': self.APP_EQUIPMENT_ID,
            'uuid': self.APP_UUID,
            'userId': '',
            'equipmentType': '1',
            'yaud': f'com.shinyv.cnr_{self.APP_EQUIPMENT_ID}',
            'versionId': self.APP_VERSION_ID,
            'appSourceId': '1',
            'platformCode': 'XIAOMI',
            'cid': '',
            'timestamp': timestamp,
            'sign': sign,
        }

    def _app_get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """请求 App 通道接口并返回 JSON，网关错误转成带原因的 RuntimeError。"""
        query_params = {k: v for k, v in (params or {}).items() if v is not None}
        # 每次请求使用唯一毫秒时间戳：网关有「重复访问随机数」(1051) 防护
        timestamp = get_timestamp_ms_str()
        sign = self._app_sign(query_params, timestamp)
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(
                url, params=query_params, headers=self._app_headers(timestamp, sign), timeout=20
            )
            if response.status_code == 404:
                raise RuntimeError(f"云听FM接口路由不存在: {path}")
            result = response.json()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"云听FM接口请求失败: {path} - {exc}")

        if isinstance(result, dict):
            code = result.get("code")
            if code == 0:
                return result
            message = str(result.get("message") or result.get("desc") or "")
            raise RuntimeError(f"云听FM接口返回错误(code={code}): {message}")
        raise RuntimeError(f"云听FM接口返回格式异常: {path}")

    def _search_page(self, keyword: str, search_tab_id: str, content_type: str, page_no: int) -> List[Dict]:
        """调用主搜索接口取一页原始条目（pageNo 0 基）。"""
        params = {
            "keyWord": keyword,
            "searchTabId": search_tab_id,
            "contentType": content_type,
            "pageNo": str(max(0, int(page_no))),
            "sortType": "0",
        }
        result = self._app_get(self.SEARCH_PATH, params)
        data = (result or {}).get("data") or {}
        items = data.get("data") or []
        if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
            print(f"🔍 云听FM搜索 tab={search_tab_id!r} ct={content_type} page={params['pageNo']}"
                  f" total={data.get('totalNum')} n={len(items)}")
        return [item for item in items if isinstance(item, dict)]

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
        author = (
            item.get("ownerNickName")
            or item.get("ownerName")
            or item.get("author")
            or item.get("anchorName")
            or item.get("announcer")
            or ""
        )
        episodes = item.get("childCount") or item.get("songCount") or item.get("programCount") or item.get("singleCount") or 0
        plays = item.get("listenCount") or item.get("listenNum") or item.get("playCount") or 0

        end_flag = item.get("endFlag")
        status = "已完结" if end_flag in (1, "1", True) else "连载中"

        return {
            "id": str(album_id),
            "title": str(title).strip(),
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
        关键词搜索云听FM专辑。

        走 App 通道主搜索接口 `/search/search/findSearchResourceList`（签名已复现）：
        1. 先请求「专辑」tab（searchTabId=2 & contentType=1）——官方 App 的专辑列表；
        2. 专辑 tab 没有可用条目时回落「综合」tab：直接取其中的专辑条目，并把命中的
           单集按 albumId 聚合后补详情转成专辑（综合tab常把有声书按单集返回）。

        page 为接口 0 基页码（实测 pageNo>=1 恒空，这是该网关的分页语义）；
        page_size 为返回条数上限。失败时返回空列表，不影响链接/ID搜索。
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        # 分享链接或专辑ID仍走稳定的专辑详情接口。
        album_id = self.parse_url_or_id(kw)
        if album_id and (kw.startswith("http") or kw.isdigit()):
            album_info = self.get_album_info(album_id)
            return [album_info] if album_info else []

        limit = max(1, int(page_size or 20))
        books: List[Dict] = []
        seen = set()

        def collect(items: List[Dict]) -> int:
            added = 0
            for item in items or []:
                if str(item.get("contentType")) not in ("1", "1.0"):
                    continue
                book = self._normalize_search_item(item)
                if not book or book["id"] in seen:
                    continue
                seen.add(book["id"])
                books.append(book)
                added += 1
            return added

        try:
            collect(self._search_page(kw, "2", "1", page))
        except Exception as exc:
            print(f"⚠️ 云听FM专辑搜索失败: {exc}")

        if books:
            print(f"✅ 云听FM关键词搜索找到 {len(books)} 个结果（专辑tab）")
            return books[:limit]

        # 兜底：综合tab（同样 0 基，返回混合内容类型）
        try:
            mixed = self._search_page(kw, "", "0", 0)
        except Exception as exc:
            print(f"⚠️ 云听FM综合搜索失败: {exc}")
            mixed = []

        collect(mixed)
        singles = [
            item for item in mixed
            if str(item.get("contentType")) == "3" and (item.get("albumId") or item.get("parentId"))
        ]
        if singles and len(books) < limit:
            books.extend(self._albums_from_singles(singles, seen, limit - len(books)))
        if books:
            print(f"✅ 云听FM关键词搜索找到 {len(books)} 个结果（综合tab兜底）")
            return books[:limit]

        print("⚠️ 云听FM关键词搜索暂未返回可用结果，仍可使用分享链接或专辑ID搜索")
        return []

    def _albums_from_singles(self, singles: List[Dict], seen: set, budget: int) -> List[Dict]:
        """把综合tab命中的单集按所属专辑去重后补专辑详情，转成专辑条目。"""
        albums: List[Dict] = []
        pending: List[tuple] = []
        for item in singles:
            parent = str(item.get("albumId") or item.get("parentId") or "")
            if not parent or parent in seen:
                continue
            seen.add(parent)
            pending.append((parent, item))
        for parent, item in pending[:max(0, budget)]:
            detail = None
            try:
                detail = self.get_album_detail(parent)
            except Exception as exc:
                print(f"⚠️ 云听FM专辑详情补全失败 {parent}: {exc}")
            if detail and detail.get("id"):
                if not detail.get("title") or detail["title"] == "未知专辑":
                    detail["title"] = str(item.get("title") or detail["title"])
                if not detail.get("cover"):
                    detail["cover"] = item.get("image") or ""
                if not detail.get("plays"):
                    detail["plays"] = item.get("listenCount") or 0
                albums.append(detail)
                continue
            # 详情不可用时退化为所属专辑ID的条目（仍能进入章节/下载流程）
            book = self._normalize_search_item(item)
            if book:
                book["id"] = parent
                albums.append(book)
        return albums

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
                
                print("❌ 无法从链接提取专辑ID")
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
            # 先取专辑详情（标题/封面/作者/状态），失败再从分集列表提取
            detail = self.get_album_detail(album_id)
            if detail and detail.get('title') not in (None, '', '未知专辑'):
                return {
                    'id': album_id,
                    'title': detail.get('title', '未知专辑'),
                    'author': detail.get('author') or '未知作者',
                    'cover': detail.get('cover', ''),
                    'episodes': detail.get('episodes', 0),
                    'plays': detail.get('plays', 0),
                    'status': detail.get('status', '连载中'),
                    'platform': '云听FM',
                    'description': detail.get('description', ''),
                }

            # 详情不可用时获取一页分集数据以提取专辑信息
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
                'status': '已完结',  # 默认完结
                'platform': '云听FM',
                'description': f"共{album_info.get('total', 0)}集"
            }

        except Exception as e:
            print(f"❌ 获取专辑信息失败: {e}")
            return None

    def get_album_detail(self, album_id: str) -> Optional[Dict]:
        """
        获取专辑详细信息（含封面/主播/状态）。

        ## 调用方式（2026-10 逆向修正）

        `/web/` 通道单参数 GET 的真实形态是「路径后缀 + 无参数签名」：
        `GET /web/appAlbum/detail/<albumId>`，sign = MD5('timestamp='+毫秒+'&key='+key)。
        旧实现用 `?id=<albumId>` + 参数签名，网关收下但服务端查不到资源（data 恒 null）。

        返回统一字段（id/title/author/cover/episodes/plays/status/description），
        同时保留 albumTitle/albumCover/total 等旧键，兼容 get_album_singles 等既有调用。
        """
        try:
            url = f"{self.base_url}/web/appAlbum/detail/{album_id}"

            # 无参数签名（路径后缀形态不携带 query 业务参数）
            timestamp = get_timestamp_ms_str()
            sign = self.md5_sign(f"timestamp={timestamp}&key={self.secret_key}")

            if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                print("🔍 云听FM签名生成调试已启用（敏感字段已隐藏）")
                print(f"   时间戳: {timestamp}")
                print(f"   签名结果: {sign[:8]}***")

            headers = {
                "Content-Type": "application/json",
                "equipmentId": "0000",
                "platformCode": "WEB",
                "timestamp": timestamp,
                "sign": sign
            }

            response = self.session.get(url, headers=headers, timeout=30)
            result = response.json()

            if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                print(f"🔍 专辑详情API响应字段: {list(result.keys()) if isinstance(result, dict) else type(result)}")

            if result.get('code') == 0:
                album_data = result.get('data') or {}
                if album_data:
                    return self._normalize_album_detail(album_id, album_data)
                print("⚠️ 专辑详情API返回空数据，尝试其他方法获取封面")
                # 尝试使用发现的图片API
                return self._try_get_cover_from_image_api(album_id)
            else:
                print(f"❌ 专辑详情API返回错误: {result.get('message', '未知错误')}")
                # 尝试使用发现的图片API
                return self._try_get_cover_from_image_api(album_id)

        except Exception as e:
            print(f"❌ 获取专辑详情失败: {e}")
            return None

    def _normalize_album_detail(self, album_id: str, album_data: Dict) -> Dict:
        """把 /web/appAlbum/detail 的 data 转成统一字段 + 旧键兼容。"""
        anchors = album_data.get('anchorList') or []
        anchor_names = []
        for anchor in anchors:
            if isinstance(anchor, dict):
                name = anchor.get('nickName') or anchor.get('name') or ''
                if name:
                    anchor_names.append(str(name))
        author = (
            album_data.get('ownerNickName')
            or '、'.join(anchor_names)
            or album_data.get('author')
            or ''
        )
        episodes = album_data.get('childCount') or album_data.get('total') or 0
        end_flag = album_data.get('endFlag')
        title = album_data.get('name') or '未知专辑'
        description = album_data.get('des') or album_data.get('desSimple') or ''
        return {
            # 统一字段（与搜索结果一致，供详情补全/前端使用）
            'id': str(album_id),
            'title': str(title),
            'author': str(author or ''),
            'cover': album_data.get('image') or '',
            'episodes': episodes,
            'plays': album_data.get('listenCount') or 0,
            'status': '已完结' if end_flag in (1, '1', True) else '连载中',
            'description': description,
            'platform': '云听FM',
            # 旧键（get_album_singles / search_by_link_or_id 依赖）
            'albumId': str(album_id),
            'albumTitle': str(title),
            'albumCover': album_data.get('image') or '',
            'des': description,
            'desSimple': album_data.get('desSimple') or '',
            'total': episodes,
            'pageTotal': album_data.get('pageTotal'),
        }

    def _try_get_cover_from_image_api(self, album_id: str) -> Optional[Dict]:
        """
        尝试使用发现的图片API获取封面
        """
        try:
            print("🔍 尝试使用图片API获取封面...")
            
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
            print("   尝试API2...")
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
            
            print("⚠️ 所有图片API都失败，返回默认信息")
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
                        
                        print("☁️ 云听FM专辑信息:")
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
                        
                        # 如果只有一页：不直接返回，交给末尾的 total_num 完整性校验
                        if total_pages == 1:
                            print(f"✅ 单页获取: {len(all_singles)}/{total_num} 集（等待完整性校验）")
                        
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
        
        # 完整性校验：服务端可能按请求 page_size 截断（如请求 10000 只回 200 且 totalPage=1），
        # 此时用较小的页大小重拉全量并去重合并，避免订阅/整本下载静默截断漏章。
        total_num = int((album_info or {}).get('total') or 0) if album_info else 0
        if total_num > 0 and len(all_singles) < total_num:
            print(f"⚠️ 云听FM章节不完整: {len(all_singles)}/{total_num}，改用小页(200)补拉...")
            retry_singles = []
            retry_seen = set()
            retry_page = 0
            retry_total_pages = max(1, (total_num + 199) // 200)
            while retry_page < retry_total_pages and len(retry_singles) < total_num and retry_page < 100:
                try:
                    data = {
                        "albumId": str(album_id),
                        "pageNo": str(retry_page),
                        "pageSize": "200",
                    }
                    timestamp = get_timestamp_ms_str()
                    params_str = self.sort_params(data)
                    sign_text = params_str + f"&timestamp={timestamp}&key={self.secret_key}"
                    sign = self.md5_sign(sign_text)
                    headers = {
                        "Content-Type": "application/json",
                        "equipmentId": "0000",
                        "platformCode": "WEB",
                        "timestamp": timestamp,
                        "sign": sign,
                    }
                    response = self.session.get(url, params=data, headers=headers, timeout=30)
                    result = response.json()
                    items = ((result.get('data') or {}).get('data') or []) if result.get('code') == 0 else []
                    added = 0
                    for item in items:
                        key = str(item.get('id') or '')
                        if key and key in retry_seen:
                            continue
                        if key:
                            retry_seen.add(key)
                        retry_singles.append(item)
                        added += 1
                    if not items or added == 0:
                        break
                except Exception as e:
                    print(f"⚠️ 云听FM补拉第{retry_page}页失败: {e}")
                    break
                retry_page += 1
                time.sleep(0.1)
            if len(retry_singles) > len(all_singles):
                print(f"✅ 云听FM补拉完成: {len(retry_singles)}/{total_num} 集")
                all_singles = retry_singles
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
                print("⚠️ 未获取到章节")
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
                    print("🔍 音频URL字段检查:")
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
            print("⚠️ 云听FM音频URL应从章节数据中的mediaUrl字段获取")
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
