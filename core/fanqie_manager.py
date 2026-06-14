#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄畅听管理器 - 真实API实现
从喜马拉雅源文件提取的完整实现
"""

import requests
import time
import json
import re
from urllib.parse import urlparse, parse_qs, unquote
import hashlib
import base64
import datetime
import binascii
import struct
import random
import contextlib
import io
from pathlib import Path
from collections import Counter
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs, unquote
from .time_api import get_timestamp_ms_str

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

def xxtea_encrypt(data, key):
    """XXTEA加密实现（基于真实逆向）"""
    def _long2str(v, w):
        n = (len(v) - 1) << 2
        if w:
            m = v[-1]
            if (m < n - 3) or (m > n): return b''
            n = m
        s = bytearray(struct.pack('<%iL' % len(v), *v))
        return s[0:n] if w else s

    def _str2long(s, w):
        n = len(s)
        m = (4 - (n & 3) & 3) + n
        s = s.ljust(m, b'\0')
        v = list(struct.unpack('<%iL' % (m >> 2), s))
        if w: v.append(n)
        return v

    v = _str2long(data, True)
    k = _str2long(key.ljust(16, b'\0'), False)
    n = len(v) - 1
    z = v[n]
    rounds = 6 + 52 // (n + 1)
    sum_ = 0
    for _ in range(rounds):
        sum_ += 0x9E3779B9
        sum_ &= 0xFFFFFFFF
        e = (sum_ >> 2) & 3
        for p in range(n):
            y = v[(p + 1) % (n + 1)]
            v[p] = (v[p] + (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4)) ^ (sum_ ^ y) + (k[p & 3 ^ e] ^ z))) & 0xFFFFFFFF
            z = v[p]
        y = v[0]
        v[n] = (v[n] + (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4)) ^ (sum_ ^ y) + (k[n & 3 ^ e] ^ z))) & 0xFFFFFFFF
        z = v[n]
    return _long2str(v, False)


def generate_x_gorgon(url_path, params, timestamp=None, extra_salt=b''):
    """生成X-Gorgon签名（基于文件4的优化版本，支持extra_salt）"""
    if timestamp is None:
        timestamp = int(time.time())
    
    # 步骤1: 排序并序列化参数
    sorted_params = sorted(params.items())
    query_str = '&'.join([f"{k}={v}" for k, v in sorted_params])
    
    # 步骤2: 计算MD5（优化：添加extra_salt）
    param_md5 = hashlib.md5(query_str.encode()).digest()
    url_md5 = hashlib.md5(url_path.encode()).digest()
    body_md5 = hashlib.md5(b'').digest()
    input_data = url_md5 + param_md5 + body_md5 + extra_salt + b'\x00\x00\x00\x00'[:4]
    
    # 步骤3: 加密（XXTEA）
    key = b'\xdf\xa5\xb2\x03\xd2\x81\xa4\xd3'  # 示例密钥
    encrypted = xxtea_encrypt(input_data[:20], key)  # 取20字节输入
    
    # 步骤4: 组装X-Gorgon
    version = '04'
    ts_hex = format(timestamp & 0xFFFFFFFF, '08x')
    enc_hex = binascii.hexlify(encrypted).decode()
    return version + ts_hex + enc_hex


def generate_device_fingerprint():
    """生成随机设备指纹（基于文件4）"""
    return {
        "device_id": str(random.randint(1000000000000000000, 9999999999999999999)),
        "iid": str(random.randint(1000000000000000000, 9999999999999999999)),
        "openudid": ''.join(random.choices('abcdef0123456789', k=16)),
    }


def get_ttwid():
    """获取真实ttwid（基于文件4，从字节站点获取）"""
    try:
        resp = requests.get("https://www.toutiao.com", allow_redirects=True, timeout=5)
        cookies = resp.cookies
        ttwid = cookies.get("ttwid", "1%7Cdefault_ttwid%7C")
        return ttwid
    except Exception:
        return "1%7Cdefault_ttwid%7C"  # 备用


class FanqieManager:
    """番茄畅听管理器"""
    
    def __init__(self):
        self.base_url = "https://api5-sinfonlinec.novelfm.com"
        self.audio_url = "https://reading.snssdk.com/reading/reader/audio/playinfo/"
        self.session = requests.Session()
        
        # 请求头
        self.headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'User-Agent': 'com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)'
        }
        
        # 基础参数
        self.base_params = {
            "device_platform": "android",
            "os": "android",
            "aid": "3040",
            "app_name": "novel_fm",
            "version_code": "608",
            "device_id": "3942194090368537",
            "iid": "1109875180222825",
        }
        
        # 音效配置
        self.voice_configs = self._get_default_voice_configs()
        self._voices_cache: Dict[str, List[Dict]] = {}
        self._tone_info_cache: Dict[str, Dict] = {}
        self._signed_client = None
    
    def search_books(self, keyword: str, max_pages: int = 3) -> List[Dict]:
        """搜索书籍 - 优化版本，减少请求次数提升速度
        
        Args:
            keyword: 搜索关键词
            max_pages: 最大获取页数，默认3页（60个结果足够展示）
        """
        try:
            print(f"🔍 番茄畅听搜索: {keyword}")
            
            all_books = []
            page_num = 1
            page_size = 30  # 增加每页数量，减少请求次数
            
            while page_num <= max_pages:
                print(f"📄 获取第 {page_num} 页搜索结果...")
                
                params = self.base_params.copy()
                params["_rticket"] = get_timestamp_ms_str()
                
                request_body = {
                    "query": keyword,
                    "limit": page_size,
                    "offset": (page_num - 1) * page_size
                }
                
                response = requests.post(
                    f"{self.base_url}/novelfm/bookmall/search/page/v1/",
                    params=params,
                    headers=self.headers,
                    json=request_body,
                    timeout=8,  # 减少超时时间，加快失败响应
                    verify=False  # 禁用SSL验证，避免证书验证失败
                )
                
                print(f"📊 响应状态: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    books = []
                    
                    if 'data' in result and 'search_data' in result['data']:
                        for book_item in result['data']['search_data']:
                            if 'books' in book_item and len(book_item['books']) > 0:
                                book_data = book_item['books'][0]
                                
                                books.append({
                                    'id': str(book_data.get('book_id', '')),
                                    'title': book_data.get('book_name', ''),
                                    'author': book_data.get('author', ''),
                                    'platform': '番茄畅听',
                                    'cover': book_data.get('thumb_url', ''),
                                    'plays': 0,  # 番茄搜索结果不显示播放量，提升速度
                                    'episodes': 0,  # 番茄搜索结果不显示集数，提升速度
                                    'status': '连载中',
                                    'description': book_data.get('abstract', ''),
                                    'category': book_data.get('category', ''),
                                    'tags': [],
                                    'created_at': '',
                                    'updated_at': ''
                                })
                    
                    print(f"📋 第 {page_num} 页找到 {len(books)} 本书")
                    all_books.extend(books)
                    
                    # 如果当前页结果少于page_size，说明已经是最后一页
                    if len(books) < page_size:
                        print(f"✅ 已获取到最后一页，总共找到 {len(all_books)} 本书")
                        break
                    
                    page_num += 1
                else:
                    print(f"❌ 搜索失败，停止获取")
                    break
            
            print(f"✅ 番茄畅听搜索完成，总共找到 {len(all_books)} 本书")
            return all_books
            
        except requests.exceptions.Timeout:
            print(f"⚠️ 番茄畅听搜索超时，返回已获取的 {len(all_books)} 本书")
            return all_books
        except Exception as e:
            print(f"❌ 搜索异常: {e}")
            return all_books if all_books else []
    
    _DIRECTORY_DETAIL_URL = "https://fanqienovel.com/api/reader/directory/detail"
    _FANQIE_PAGE_URL = "https://fanqienovel.com/page/{book_id}"

    def _fanqie_web_headers(self, accept_json: bool = False) -> Dict[str, str]:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://fanqienovel.com/',
        }
        headers['Accept'] = 'application/json' if accept_json else 'text/html,application/xhtml+xml'
        return headers

    @staticmethod
    def _decode_json_escaped_string(value: str) -> str:
        if not value:
            return ''
        try:
            return json.loads(f'"{value}"')
        except Exception:
            return value.replace('\\u002F', '/').replace('\\/', '/')

    def _count_chapters_in_directory(self, api_data: Dict) -> int:
        total = 0
        volumes = api_data.get('chapterListWithVolume') or []
        for volume in volumes:
            if isinstance(volume, list):
                total += len(volume)
            elif isinstance(volume, dict) and 'chapterList' in volume:
                total += len(volume['chapterList'])
        book_info = api_data.get('bookInfo')
        if isinstance(book_info, dict):
            try:
                total = max(total, int(book_info.get('chapter_count') or 0))
            except (TypeError, ValueError):
                pass
        return total

    def _normalize_book_meta_from_api(self, book_info: Dict) -> Dict:
        title = (
            book_info.get('book_name')
            or book_info.get('bookName')
            or book_info.get('title')
            or ''
        )
        author = book_info.get('author') or book_info.get('authorName') or book_info.get('anchor') or ''
        cover = (
            book_info.get('thumb_url')
            or book_info.get('thumbUrl')
            or book_info.get('cover')
            or book_info.get('coverUrl')
            or ''
        )
        description = book_info.get('abstract') or book_info.get('description') or ''
        plays = 0
        try:
            plays = int(book_info.get('play_count') or book_info.get('play_num') or 0)
        except (TypeError, ValueError):
            plays = 0
        status = '连载中'
        if str(book_info.get('creation_status', '')) == '0':
            status = '已完结'
        return {
            'title': str(title).strip(),
            'author': str(author).strip() or '未知作者',
            'cover': str(cover).strip(),
            'description': str(description).strip(),
            'plays': plays,
            'status': status,
            'category': book_info.get('category', '') or '',
            'tags': book_info.get('tags', []) if isinstance(book_info.get('tags'), list) else [],
        }

    def _fetch_directory_detail_data(self, book_id: str) -> Optional[Dict]:
        """调用番茄官方目录 API，返回 data 字段"""
        try:
            response = requests.get(
                self._DIRECTORY_DETAIL_URL,
                params={'bookId': book_id},
                headers=self._fanqie_web_headers(accept_json=True),
                timeout=12,
            )
            if response.status_code != 200:
                print(f"❌ 目录API请求失败，状态码: {response.status_code}")
                return None
            payload = response.json()
            if payload.get('code') == 0 and isinstance(payload.get('data'), dict):
                return payload['data']
            print(f"❌ 目录API返回错误: {payload.get('message', '未知错误')}")
            return None
        except Exception as e:
            print(f"⚠️ 目录API异常: {e}")
            return None

    def _fetch_book_meta_from_fanqie_page(self, book_id: str) -> Optional[Dict]:
        """从 fanqienovel.com 书籍页解析书名、作者、封面（ID 搜索时目录 API 常无 bookInfo）"""
        url = self._FANQIE_PAGE_URL.format(book_id=book_id)
        try:
            response = requests.get(url, headers=self._fanqie_web_headers(), timeout=12)
            if response.status_code != 200:
                return None
            html = response.text

            def _pick(patterns: List[str]) -> str:
                for pattern in patterns:
                    match = re.search(pattern, html)
                    if match:
                        return self._decode_json_escaped_string(match.group(1))
                return ''

            title = _pick([
                r'"bookName"\s*:\s*"((?:\\.|[^"\\])*)"',
                r'"book_name"\s*:\s*"((?:\\.|[^"\\])*)"',
            ])
            author = _pick([
                r'"author"\s*:\s*"((?:\\.|[^"\\])*)"',
                r'"authorName"\s*:\s*"((?:\\.|[^"\\])*)"',
            ])
            cover = _pick([
                r'"thumbUrl"\s*:\s*"((?:\\.|[^"\\])*)"',
                r'"thumb_url"\s*:\s*"((?:\\.|[^"\\])*)"',
            ])
            description = _pick([r'"abstract"\s*:\s*"((?:\\.|[^"\\])*)"'])

            if not (title or author or cover):
                return None
            return {
                'title': title,
                'author': author or '未知作者',
                'cover': cover,
                'description': description,
            }
        except Exception as e:
            print(f"⚠️ 番茄网页元数据获取失败: {e}")
            return None

    def _extract_book_meta_from_chapters(self, api_data: Dict) -> Optional[Dict]:
        """从章节目录标题推断书名、作者（听书类书籍网页常 404 且无 bookInfo）"""
        titles: List[str] = []
        for volume in api_data.get('chapterListWithVolume') or []:
            if not isinstance(volume, list):
                continue
            for chapter in volume:
                if isinstance(chapter, dict):
                    t = str(chapter.get('title') or '').strip()
                    if t:
                        titles.append(t)
        if not titles:
            return None

        name_patterns = [
            re.compile(r'^(.+?)\s+第?\d+集'),
            re.compile(r'^(.+?)\s+第\d+章'),
            re.compile(r'^(.+?)\s+第[零一二三四五六七八九十百千\d]+[集章]'),
            re.compile(r'^(.+?)\s+\d{3,4}集'),
        ]
        name_candidates: List[str] = []
        for title in titles:
            if title.startswith('作者'):
                continue
            for pattern in name_patterns:
                match = pattern.match(title)
                if match:
                    name = match.group(1).strip()
                    if len(name) >= 2:
                        name_candidates.append(name)
                    break

        book_title = ''
        if name_candidates:
            book_title = Counter(name_candidates).most_common(1)[0][0]

        author = ''
        for title in titles[:8]:
            match = re.search(r'作者\s*([^\s：:，,、原声寄语]+)', title)
            if match:
                author = match.group(1).strip()
                break

        if not book_title and not author:
            return None
        return {
            'title': book_title,
            'author': author or '未知作者',
            'cover': '',
            'description': '',
        }

    def _extract_novelfm_search_keywords_from_chapters(self, api_data: Dict) -> List[str]:
        """从章节标题提取 novelfm 搜索关键词（听书格式 001集 副标题 时书名不在标题里）"""
        titles: List[str] = []
        for volume in api_data.get('chapterListWithVolume') or []:
            if not isinstance(volume, list):
                continue
            for chapter in volume:
                if isinstance(chapter, dict):
                    t = str(chapter.get('title') or '').strip()
                    if t:
                        titles.append(t)
        if not titles:
            return []

        stop_words = {
            '人生', '无常', '真相', '意外', '发现', '秘密', '危险', '地方', '身份',
            '只身', '拼死', '一战', '活下去', '与虎', '谋皮', '不得不', '臣服',
            '出事', '反常', '必有', '妖', '大作', '必须', '杀死', '大白', '赴会',
            '要不要', '女人', '陪啊', '供词', '暴露', '赴死', '敌军', '拼死',
            '最危险', '的地方', '意外发现', '的秘密',
        }
        keywords: List[str] = []
        seen = set()

        hint_patterns = [
            re.compile(r'([\u4e00-\u9fff]{2,4})的(?:供词|遗言|嘱托|秘密|供状)'),
            re.compile(r'([\u4e00-\u9fff]{2,4})赴(?:死|宴|会)'),
            re.compile(r'必杀([\u4e00-\u9fff]{2,4})'),
            re.compile(r'与([\u4e00-\u9fff]{2,4})(?:合作|对峙|交手|谋皮)'),
            re.compile(r'([\u4e00-\u9fff]{2,3})(?:称王|称霸|登基)'),
            re.compile(r'(?:见|找|救|杀|见)([\u4e00-\u9fff]{2,4})'),
        ]
        for title in titles[:50]:
            body = re.sub(r'^\d+集\s*', '', title).strip()
            if body.startswith('作者'):
                continue
            for pattern in hint_patterns:
                for match in pattern.finditer(body):
                    word = match.group(1).strip()
                    if len(word) >= 2 and word not in stop_words and word not in seen:
                        seen.add(word)
                        keywords.append(word)

        token_counter: Counter = Counter()
        token_pat = re.compile(r'[\u4e00-\u9fff]{2,4}')
        for title in titles[:80]:
            body = re.sub(r'^\d+集\s*', '', title).strip()
            for tok in token_pat.findall(body):
                if len(tok) >= 2 and tok not in stop_words:
                    token_counter[tok] += 1
        for tok, cnt in token_counter.most_common(10):
            if cnt >= 2 and tok not in seen:
                seen.add(tok)
                keywords.append(tok)

        return keywords[:12]

    def _search_novelfm_book_by_query(self, book_id: str, query: str) -> Optional[Dict]:
        """novelfm 搜索并在结果中按 book_id 精确匹配"""
        if not query or not str(query).strip():
            return None
        try:
            for offset in (0, 30, 60):
                params = self.base_params.copy()
                params['_rticket'] = get_timestamp_ms_str()
                body = {'query': str(query).strip(), 'limit': 30, 'offset': offset}
                response = requests.post(
                    f'{self.base_url}/novelfm/bookmall/search/page/v1/',
                    params=params,
                    headers=self.headers,
                    json=body,
                    timeout=8,
                    verify=False,
                )
                if response.status_code != 200:
                    break
                result = response.json()
                for book_item in result.get('data', {}).get('search_data', []):
                    for book_data in book_item.get('books') or []:
                        if str(book_data.get('book_id', '')) == str(book_id):
                            return self._normalize_book_meta_from_api(book_data)
            return None
        except Exception:
            return None

    def _fetch_book_meta_from_novelfm_search(
        self, book_id: str, api_data: Optional[Dict] = None
    ) -> Optional[Dict]:
        """novelfm 搜索：先按 ID，再按章节提取的人名/关键词匹配书名"""
        queries: List[str] = [str(book_id)]
        if api_data:
            queries.extend(self._extract_novelfm_search_keywords_from_chapters(api_data))

        tried = set()
        for query in queries:
            q = str(query).strip()
            if not q or q in tried:
                continue
            tried.add(q)
            meta = self._search_novelfm_book_by_query(book_id, q)
            if meta and meta.get('title'):
                print(f"📖 novelfm 搜索命中 (关键词: {q}): {meta.get('title')} / {meta.get('author', '')}")
                return meta
        return None

    def _merge_book_meta(self, meta: Dict, extra: Optional[Dict]) -> None:
        """将补充元数据合并进 meta（不覆盖已有非空字段）"""
        if not extra:
            return
        for key, value in extra.items():
            if value and not meta.get(key):
                meta[key] = value

    def get_book_detail(self, book_id: str) -> Optional[Dict]:
        """获取书籍详情：目录 API 统计章节数，书名/封面/作者优先 bookInfo，否则解析官方网页"""
        try:
            print(f"📚 获取番茄畅听书籍详情: {book_id}")

            meta: Dict = {}
            total_chapters = 0

            print(f"🔍 调用番茄目录API: {self._DIRECTORY_DETAIL_URL}")
            api_data = self._fetch_directory_detail_data(book_id)
            if api_data:
                total_chapters = self._count_chapters_in_directory(api_data)
                print(f"📊 统计到 {total_chapters} 个章节")
                book_info = api_data.get('bookInfo')
                if isinstance(book_info, dict):
                    meta = self._normalize_book_meta_from_api(book_info)
                    print(f"📖 目录API bookInfo: {meta.get('title', '')}")

            if api_data and (not meta.get('title') or not meta.get('author')):
                print("🔍 从章节目录标题推断书名/作者...")
                chapter_meta = self._extract_book_meta_from_chapters(api_data)
                self._merge_book_meta(meta, chapter_meta)
                if chapter_meta:
                    print(f"📖 章节推断: {meta.get('title', '')} / {meta.get('author', '')}")

            if not meta.get('title') or not meta.get('author') or not meta.get('cover'):
                print(f"🔍 补充获取番茄网页元数据: {self._FANQIE_PAGE_URL.format(book_id=book_id)}")
                web_meta = self._fetch_book_meta_from_fanqie_page(book_id)
                self._merge_book_meta(meta, web_meta)
                if web_meta:
                    print(f"📖 网页元数据: {meta.get('title', '')} / {meta.get('author', '')}")

            if not meta.get('title') or not meta.get('author'):
                print("🔍 novelfm 搜索 API 补全书名（ID + 章节关键词）...")
                search_meta = self._fetch_book_meta_from_novelfm_search(book_id, api_data)
                self._merge_book_meta(meta, search_meta)

            if not api_data and not meta.get('title'):
                print(f"❌ 无法获取书籍 {book_id} 的详情")
                return None

            title = meta.get('title') or f'书籍ID_{book_id}'
            result = {
                'id': str(book_id),
                'title': title,
                'author': meta.get('author') or '未知作者',
                'platform': '番茄畅听',
                'cover': meta.get('cover') or '',
                'plays': meta.get('plays', 0),
                'episodes': total_chapters,
                'status': meta.get('status') or '连载中',
                'description': meta.get('description') or f'番茄畅听 · {title}',
                'category': meta.get('category') or '',
                'tags': meta.get('tags') or [],
                'created_at': '',
                'updated_at': '',
            }

            print(f"✅ 番茄畅听书籍详情: {result['title']} · {result['author']} · {total_chapters}章")
            return result

        except Exception as e:
            print(f"❌ 获取番茄畅听书籍详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_chapters_for_voice(self, book_id: str, voice_config: Optional[Dict] = None, page: int = 1, page_size: int = 10000) -> List[Dict]:
        """按番茄畅听音色加载目录。

        4.7.14 的修复点：真人演播和 AI 音色可能对应不同 play_book_id。
        真人音色使用 audio_tones[*].abook_id，AI 音色使用 relate_novel_bookid。
        """
        play_book_id = str(
            (voice_config or {}).get("play_book_id")
            or (voice_config or {}).get("book_id")
            or book_id
        )
        chapters = self.get_chapters(play_book_id, page=page, page_size=page_size)
        for chapter in chapters:
            chapter["album"] = play_book_id
            chapter["book_id"] = play_book_id
            if voice_config:
                chapter["voice"] = voice_config
        return chapters

    def get_chapters(self, book_id: str, page: int = 1, page_size: int = 10000) -> List[Dict]:
        """获取章节列表 - 支持分页"""
        try:
            print(f"📚 获取章节: {book_id}, 页码: {page}, 每页: {page_size}")
            
            # 番茄官方API - 获取所有章节
            chapter_url = "https://fanqienovel.com/api/reader/directory/detail"
            params = {'bookId': book_id}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://fanqienovel.com/',
                'Accept': 'application/json',
            }
            
            response = requests.get(chapter_url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('code') == 0 and 'data' in result:
                    chapters = []
                    data = result['data']
                    
                    # 调试：查看API返回的专辑元数据
                    print(f"📊 番茄章节API返回的数据键: {list(data.keys())}")
                    if 'bookInfo' in data:
                        book_info = data['bookInfo']
                        print(f"📖 专辑信息字段: {list(book_info.keys()) if isinstance(book_info, dict) else 'Not a dict'}")
                        print(f"   播放量: {book_info.get('play_count', 'N/A') if isinstance(book_info, dict) else 'N/A'}")
                        print(f"   总集数: {book_info.get('chapter_count', 'N/A') if isinstance(book_info, dict) else 'N/A'}")
                    
                    if 'chapterListWithVolume' in data:
                        # 使用order_num记录章节的原始顺序
                        order_num = 0
                        
                        for volume in data['chapterListWithVolume']:
                            if isinstance(volume, list):
                                for chapter in volume:
                                    try:
                                        order_num += 1  # 递增顺序号，从1开始
                                        
                                        # 格式化时长 - 确保转换为整数
                                        try:
                                            duration_seconds = int(chapter.get('duration', 0))
                                        except (ValueError, TypeError):
                                            duration_seconds = 0
                                        duration_formatted = f"{duration_seconds // 60:02d}:{duration_seconds % 60:02d}" if duration_seconds > 0 else "00:00"
                                        
                                        # 格式化文件大小 - 确保转换为整数
                                        try:
                                            file_size = int(chapter.get('size', 0))
                                        except (ValueError, TypeError):
                                            file_size = 0
                                        if file_size > 0:
                                            if file_size < 1024 * 1024:  # 小于1MB
                                                size_formatted = f"{file_size // 1024}KB"
                                            else:  # 大于等于1MB
                                                size_formatted = f"{file_size / (1024 * 1024):.1f}MB"
                                        else:
                                            size_formatted = "0MB"
                                        
                                        # 确保playCount是整数
                                        try:
                                            play_count = int(chapter.get('playCount', 0))
                                        except (ValueError, TypeError):
                                            play_count = 0
                                        
                                        chapters.append({
                                            'id': f"chapter-{chapter.get('itemId', '')}",
                                            'chapter_id': str(chapter.get('itemId', '')),
                                            'title': chapter.get('title', ''),
                                            'duration': duration_seconds,
                                            'duration_str': duration_formatted,
                                            'size': size_formatted,
                                            'plays': play_count,
                                            'album': book_id,
                                            'order_num': order_num,  # 添加原始顺序号
                                        })
                                    except Exception as e:
                                        print(f"❌ 处理章节数据失败: {e}")
                                        continue
                            elif isinstance(volume, dict) and 'chapterList' in volume:
                                for chapter in volume['chapterList']:
                                    try:
                                        order_num += 1  # 递增顺序号，从1开始
                                        
                                        # 格式化时长 - 确保转换为整数
                                        try:
                                            duration_seconds = int(chapter.get('duration', 0))
                                        except (ValueError, TypeError):
                                            duration_seconds = 0
                                        duration_formatted = f"{duration_seconds // 60:02d}:{duration_seconds % 60:02d}" if duration_seconds > 0 else "00:00"
                                        
                                        # 格式化文件大小 - 确保转换为整数
                                        try:
                                            file_size = int(chapter.get('size', 0))
                                        except (ValueError, TypeError):
                                            file_size = 0
                                        if file_size > 0:
                                            if file_size < 1024 * 1024:  # 小于1MB
                                                size_formatted = f"{file_size // 1024}KB"
                                            else:  # 大于等于1MB
                                                size_formatted = f"{file_size / (1024 * 1024):.1f}MB"
                                        else:
                                            size_formatted = "0MB"
                                        
                                        # 确保playCount是整数
                                        try:
                                            play_count = int(chapter.get('playCount', 0))
                                        except (ValueError, TypeError):
                                            play_count = 0
                                        
                                        chapters.append({
                                            'id': f"chapter-{chapter.get('itemId', '')}",
                                            'chapter_id': str(chapter.get('itemId', '')),
                                            'title': chapter.get('title', ''),
                                            'duration': duration_seconds,
                                            'duration_str': duration_formatted,
                                            'size': size_formatted,
                                            'plays': play_count,
                                            'album': book_id,
                                            'order_num': order_num,  # 添加原始顺序号
                                        })
                                    except Exception as e:
                                        print(f"❌ 处理章节数据失败: {e}")
                                        continue
                    
                    print(f"✅ 获取到 {len(chapters)} 个章节")
                    
                    # 实现客户端分页 - 根据请求的页码和页面大小返回对应章节
                    if page > 1 or page_size < len(chapters):
                        start_index = (page - 1) * page_size
                        end_index = start_index + page_size
                        paginated_chapters = chapters[start_index:end_index]
                        print(f"📄 分页返回: {start_index}-{end_index} (共{len(paginated_chapters)}个章节)")
                        return paginated_chapters
                    
                    return chapters
            
            print(f"❌ 获取章节失败")
            return []
            
        except Exception as e:
            print(f"❌ 获取章节异常: {e}")
            return []
    
    @staticmethod
    def resolve_audio_format(audio_url: str, api_format: Optional[str] = None) -> Dict[str, str]:
        """根据官方 playinfo/CDN URL 参数 mime_type、HEAD Content-Type 解析真实后缀"""
        api_fmt = (api_format or '').strip().lower()

        if audio_url:
            try:
                mime = unquote((parse_qs(urlparse(audio_url).query).get('mime_type') or [''])[0]).lower()
                if 'audio_mp4' in mime or mime == 'audio/mp4':
                    return {'format': 'm4a', 'extension': '.m4a'}
                if 'mpeg' in mime or 'mp3' in mime:
                    return {'format': 'mp3', 'extension': '.mp3'}
                if 'aac' in mime:
                    return {'format': 'aac', 'extension': '.aac'}
                if 'flac' in mime:
                    return {'format': 'flac', 'extension': '.flac'}
            except Exception:
                pass

        if api_fmt in ('mp3', 'm4a', 'aac', 'flac', 'ogg', 'wav'):
            return {'format': api_fmt, 'extension': f'.{api_fmt}'}
        if api_fmt == 'mp4':
            return {'format': 'm4a', 'extension': '.m4a'}

        if audio_url:
            try:
                head = requests.head(audio_url, timeout=10, allow_redirects=True)
                ct = (head.headers.get('Content-Type') or '').lower()
                if 'mp4' in ct or 'm4a' in ct:
                    return {'format': 'm4a', 'extension': '.m4a'}
                if 'mpeg' in ct or 'mp3' in ct:
                    return {'format': 'mp3', 'extension': '.mp3'}
                if 'aac' in ct:
                    return {'format': 'aac', 'extension': '.aac'}
            except Exception:
                pass

        return {'format': api_fmt or 'audio', 'extension': '.audio'}

    def get_audio_download_info(
        self, chapter_id: str, voice_name: str | Dict = "无损真人录制", book_id: Optional[str] = None
    ) -> Optional[Dict]:
        """获取官方下载信息：reading.snssdk.com audio/playinfo + play dict。"""
        try:
            print(f"\n{'='*60}")
            print(f"📡 番茄官方API - 获取音频播放信息")
            print(f"   章节ID: {chapter_id}")
            print(f"   请求音色: {voice_name.get('name') if isinstance(voice_name, dict) else voice_name}")
            normalized_chapter_id = str(chapter_id).replace("chapter-", "", 1)

            voice_config = (
                self.resolve_voice_config(book_id or "", voice_name)
                if isinstance(voice_name, dict)
                else self.get_voice_config_by_name(voice_name)
            )
            if not voice_config:
                voice_config = self.voice_configs.get("RealLossless", list(self.voice_configs.values())[0])

            tone_id = str(voice_config.get("tone_id", "0"))
            voice_kind = "真人" if voice_config.get("is_real_person") == "1" else "AI"
            print(f"🎭 {voice_kind}音色: tone_id={tone_id}")

            play = self._get_play_dict(normalized_chapter_id, voice_config)
            if play:
                url = play.get("main_url") or play.get("backup_url") or ""
                ext = ".mp3" if ".mp3" in str(url).lower() else ".m4a"
                encrypted = bool(play.get("is_encrypt") or (url and not str(url).startswith("http")))
                print(f"🎵 官方playinfo: encrypted={encrypted}, ext={ext}")
                return {
                    "url": url,
                    "format": ext.lstrip("."),
                    "extension": ext,
                    "api_type": "official_playinfo",
                    "official": True,
                    "play": play,
                    "encrypted": encrypted,
                    "voice_config": voice_config,
                }

            url = self._get_audio_url_original(normalized_chapter_id, voice_config.get("name") or "无损真人录制")
            if url:
                meta = self.resolve_audio_format(url)
                return {"url": url, "api_type": "official_playinfo_legacy", "official": True, **meta}
            return None
        except Exception as e:
            print(f"❌ 获取音频下载信息异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_audio_url(self, chapter_id: str, voice_name: str = "无损真人录制", book_id: Optional[str] = None) -> Optional[str]:
        """获取音频 URL（兼容旧调用）"""
        info = self.get_audio_download_info(chapter_id, voice_name, book_id)
        return info.get('url') if info else None

    def _get_audio_url_original(self, chapter_id: str, voice_name: str = "无损真人录制") -> Optional[str]:
        """使用原始API获取音频URL - 简化参数版本（根据用户提供的API格式）"""
        try:
            print(f"🎵 使用原始API获取音频URL: {chapter_id}")
            
            # 查找音效配置
            voice_config = self.get_voice_config_by_name(voice_name)
            
            if not voice_config:
                print(f"⚠️  音色 '{voice_name}' 未找到，使用默认音色")
                voice_config = self.voice_configs.get("RealLossless", list(self.voice_configs.values())[0])
                print(f"✅ 使用默认音色: {voice_config['name']}")
            else:
                print(f"✅ 找到音色配置: {voice_config['name']}")
            
            # 构建请求参数 - 与APP版本完全一致（参数顺序和内容）
            params = {
                "item_ids": str(chapter_id),
                "pv_player": "-1",
                "aid": "3040",
                "device_platform": "android",
                "_rticket": str(int(time.time() * 1000))  # APP版本中使用毫秒时间戳
            }
            
            # 根据音色类型设置参数 - 与APP版本完全一致
            if voice_config.get("is_real_person") == "0":
                # AI音色
                tone_id = voice_config.get("tone_id", "0")
                params["tone_id"] = tone_id
                params["is_real_person"] = "0"
                # 根据APP版本的逻辑：调用getAiVoiceId获取ai_voice_id
                ai_voice_id = self.get_ai_voice_id_by_tone_id(tone_id)
                if ai_voice_id:
                    params["ai_voice_id"] = ai_voice_id
                print(f"🎭 AI音色API参数:")
                print(f"   音色名称: {voice_name}")
                print(f"   tone_id: {tone_id}")
                print(f"   is_real_person: 0")
                if ai_voice_id:
                    print(f"   ai_voice_id: {ai_voice_id} (从tone_id映射)")
                else:
                    print(f"   ai_voice_id: 无 (此tone_id无映射)")
            else:
                # 真人音色
                params["tone_id"] = "0"
                params["is_real_person"] = "1"
                params["real_person_voice"] = "1"
                print(f"🎭 真人音色API参数:")
                print(f"   音色名称: {voice_name}")
                print(f"   tone_id: 0")
                print(f"   is_real_person: 1")
                print(f"   real_person_voice: 1")
            
            # APP版本中原始API不使用X-Gorgon签名，使用简单的HTTP请求
            # 与APP版本完全一致的请求头
            headers = {
                "User-Agent": "com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive"
            }
            
            # APP版本中原始API直接使用reading.snssdk.com域名（不使用多域名尝试）
            api_url = "https://reading.snssdk.com/reading/reader/audio/playinfo/"
            
            print(f"📤 发送原始API请求（与APP版本一致）:")
            print(f"   URL: {api_url}")
            print(f"   参数: {params}")
            
            # 直接发送请求（不使用X-Gorgon签名，不使用多域名尝试）
            try:
                response = requests.get(api_url, params=params, headers=headers, timeout=15, verify=False)
                print(f"📥 原始API响应: HTTP {response.status_code}")
            except Exception as e:
                print(f"❌ 原始API请求失败: {e}")
                return None
            
            if response is None:
                print(f"❌ API请求失败，无法获取音频URL")
                return None
            
            print(f"📥 收到原始API响应: HTTP {response.status_code}")
            print(f"   响应长度: {len(response.content)} 字节")
            print(f"   Content-Encoding: {response.headers.get('Content-Encoding', 'None')}")
            print(f"   Content-Type: {response.headers.get('Content-Type', 'None')}")
            
            if response.status_code == 200:
                # 检查响应是否为空
                if len(response.content) == 0:
                    print(f"⚠️ 原始API返回空响应")
                    print(f"   这可能是因为章节ID无效或需要更多参数")
                    return None
                
                # 处理压缩响应并解析JSON
                data = None
                content_encoding = response.headers.get('Content-Encoding', '').lower()
                
                # 首先尝试直接解析（requests通常会自动处理gzip/deflate）
                try:
                    data = response.json()
                    print(f"📥 原始API响应数据: {data}")
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"❌ 直接JSON解析失败: {e}")
                    
                    # 尝试处理压缩响应
                    response_data = None
                    
                    # 如果响应是Brotli压缩的，需要手动解压
                    if 'br' in content_encoding:
                        try:
                            import brotli
                            response_data = brotli.decompress(response.content)
                            print(f"✅ Brotli解压成功，解压后长度: {len(response_data)} 字节")
                        except ImportError:
                            print(f"⚠️ 需要brotli库来解压响应")
                            response_data = None
                        except Exception as e2:
                            print(f"⚠️ Brotli解压失败: {e2}")
                            response_data = None
                    
                    # 如果还没有数据，尝试检查是否是其他压缩格式
                    if response_data is None:
                        # 检查Gzip magic number
                        if response.content[:2] == b'\x1f\x8b':
                            print(f"   检测到Gzip压缩数据，尝试解压...")
                            try:
                                import gzip
                                response_data = gzip.decompress(response.content)
                                print(f"✅ Gzip解压成功")
                            except Exception as e2:
                                print(f"   Gzip解压失败: {e2}")
                        # 检查Zlib magic number
                        elif response.content[:2] == b'\x78\x9c' or response.content[:2] == b'\x78\x01':
                            print(f"   检测到Zlib压缩数据，尝试解压...")
                            try:
                                import zlib
                                response_data = zlib.decompress(response.content)
                                print(f"✅ Zlib解压成功")
                            except Exception as e2:
                                print(f"   Zlib解压失败: {e2}")
                    
                    # 如果解压成功，尝试解析JSON
                    if response_data:
                        try:
                            if isinstance(response_data, bytes):
                                # 尝试UTF-8解码
                                try:
                                    json_str = response_data.decode('utf-8')
                                except UnicodeDecodeError:
                                    # 如果UTF-8失败，尝试其他编码
                                    try:
                                        json_str = response_data.decode('gbk')
                                    except Exception:
                                        json_str = response_data.decode('latin-1', errors='ignore')
                                data = json.loads(json_str)
                            else:
                                data = json.loads(response_data)
                            print(f"✅ 解压后JSON解析成功")
                        except (json.JSONDecodeError, ValueError) as e2:
                            print(f"❌ 解压后JSON解析仍然失败: {e2}")
                            data = None
                    
                    # 如果仍然无法解析，尝试不使用压缩重新请求
                    if data is None:
                        print(f"🔄 尝试不使用压缩重新请求...")
                        headers_no_compression = headers.copy()
                        headers_no_compression.pop('Accept-Encoding', None)
                        try:
                            response2 = requests.get(self.audio_url, params=params, headers=headers_no_compression, timeout=15, verify=False)
                            if response2.status_code == 200:
                                try:
                                    data = response2.json()
                                    print(f"✅ 不使用压缩的请求成功解析JSON")
                                except (json.JSONDecodeError, ValueError) as e2:
                                    print(f"❌ 不使用压缩的请求仍然无法解析JSON: {e2}")
                                    # 输出调试信息
                                    print(f"   响应前20字节（hex）: {response2.content[:20].hex()}")
                                    print(f"   响应前20字节（repr）: {repr(response2.content[:20])}")
                                    if response2.text:
                                        print(f"   响应内容前200字符: {response2.text[:200]}")
                                    return None
                            else:
                                print(f"❌ 不使用压缩的请求失败: HTTP {response2.status_code}")
                                return None
                        except Exception as e2:
                            print(f"❌ 重新请求异常: {e2}")
                            return None
                
                # 如果仍然无法解析，返回None
                if data is None:
                    print(f"❌ 所有解析尝试都失败，无法获取音频URL")
                    return None
                
                # 检查响应格式
                if data.get('code') == 0 or data.get('code') == 200:
                    print(f"✅ 原始API返回成功 (code={data.get('code')})")
                    if 'data' in data:
                        audio_data = data.get('data', [])
                        
                        # 如果data是列表
                        if isinstance(audio_data, list) and len(audio_data) > 0:
                            audio_info = audio_data[0]
                            audio_url = audio_info.get('main_url')
                            is_encrypt = audio_info.get('is_encrypt', True)
                            
                            print(f"🎵 音频信息:")
                            print(f"   URL: {audio_url[:80] if audio_url else 'None'}...")
                            print(f"   加密状态: {is_encrypt}")
                            
                            if audio_url:
                                if not is_encrypt:
                                    print(f"✅ 获取未加密音频URL成功！")
                                    return audio_url
                                else:
                                    print(f"✅ 获取加密音频URL成功（支持播放和下载）！")
                                    return audio_url
                            else:
                                print(f"❌ 未获取到音频URL")
                        else:
                            print(f"❌ 原始API返回的音频数据为空")
                    else:
                        print(f"❌ 原始API响应中没有data字段")
                else:
                    print(f"❌ 原始API返回错误 (code={data.get('code')})")
                    if 'message' in data:
                        print(f"   错误信息: {data.get('message')}")
                    print(f"   完整响应: {data}")
            else:
                print(f"❌ 原始API HTTP请求失败: {response.status_code}")
                print(f"   响应内容: {response.text[:500] if response.text else 'None'}")
            
            return None
            
        except Exception as e:
            print(f"❌ 原始API获取音频URL异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_audio_url_new_api(self, chapter_id: str) -> Optional[str]:
        """使用新API获取音频URL"""
        try:
            print(f"📡 使用新API获取音频URL")
            print(f"   章节ID: {chapter_id}")
            
            # 新API地址
            api_url = f"https://api.cenguigui.cn/api/tomato/changdunovel/?id={chapter_id}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Connection': 'keep-alive'
            }
            
            print(f"📤 发送新API请求...")
            print(f"   URL: {api_url}")
            print(f"   请求头: {headers}")
            
            response = requests.get(api_url, headers=headers, timeout=15)
            
            print(f"📥 收到新API响应: HTTP {response.status_code}")
            print(f"   响应内容长度: {len(response.content)} 字节")
            
            if response.status_code == 200 and response.text:
                try:
                    data = response.json()
                    print(f"   JSON解析成功: {data}")
                    
                    # 检查返回码
                    if data.get('code') == 200 and 'data' in data:
                        # 获取音频URL
                        audio_url = data['data'].get('url')
                        if audio_url:
                            print(f"✅ 新API获取音频URL成功!")
                            print(f"   音频URL: {audio_url[:100]}...")
                            return audio_url
                        else:
                            print(f"❌ 新API响应中没有找到音频URL")
                            print(f"   完整响应: {data}")
                    else:
                        print(f"❌ 新API返回错误 (code={data.get('code')})")
                        print(f"   错误信息: {data.get('msg', '未知错误')}")
                        
                except json.JSONDecodeError as e:
                    print(f"❌ 新API JSON解析失败: {e}")
                    print(f"   响应内容: {response.text[:500] if response.text else 'None'}")
            else:
                print(f"❌ 新API请求失败: {response.status_code}")
                print(f"   响应内容: {response.text[:500] if response.text else 'None'}")
            
            return None
            
        except Exception as e:
            print(f"❌ 新API获取音频URL异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _get_audio_url_third_party(self, chapter_id: str) -> Optional[str]:
        """使用第三方API获取音频URL"""
        try:
            print(f"📡 使用第三方API获取音频URL")
            print(f"   章节ID: {chapter_id}")
            
            # 第三方API地址
            api_url = "https://v1.gyks.cf/content"
            params = {
                "item_id": str(chapter_id),
                "source": "番茄",
                "tab": "听书",
                "version": "4.6.29"
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Connection': 'keep-alive'
            }
            
            print(f"📤 发送第三方API请求...")
            print(f"   URL: {api_url}")
            print(f"   参数: {params}")
            print(f"   请求头: {headers}")
            
            response = requests.get(api_url, params=params, headers=headers, timeout=15)
            
            print(f"📥 收到第三方API响应: HTTP {response.status_code}")
            print(f"   响应内容长度: {len(response.content)} 字节")
            
            if response.status_code == 200 and response.text:
                try:
                    data = response.json()
                    print(f"   JSON解析成功: {data}")
                    
                    # 检查返回码
                    if data.get('code', -1) == 0:
                        # 获取音频URL
                        audio_url = data.get('content') or data.get('url') or data.get('audio_url') or data.get('play_url')
                        if audio_url:
                            print(f"✅ 第三方API获取音频URL成功!")
                            print(f"   音频URL: {audio_url[:100]}...")
                            return audio_url
                        else:
                            print(f"❌ 第三方API响应中没有找到音频URL")
                            print(f"   完整响应: {data}")
                    else:
                        print(f"❌ 第三方API返回错误 (code={data.get('code')})")
                        print(f"   错误信息: {data.get('msg', '未知错误')}")
                        
                except json.JSONDecodeError as e:
                    print(f"❌ 第三方API JSON解析失败: {e}")
                    print(f"   响应内容: {response.text[:500] if response.text else 'None'}")
            else:
                print(f"❌ 第三方API请求失败: {response.status_code}")
                print(f"   响应内容: {response.text[:500] if response.text else 'None'}")
            
            return None
            
        except Exception as e:
            print(f"❌ 第三方API获取音频URL异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _get_play_dict(self, chapter_id: str, voice_name: str | Dict = "无损真人录制") -> Optional[Dict]:
        """从 playinfo/ API 拿原始 play dict（含 is_encrypt / main_url / backup_url）。
        复用 _get_audio_url_original 的请求逻辑，但返回整个 play 对象而非只有 URL。
        """
        try:
            voice_config = (
                self.resolve_voice_config("", voice_name)
                if isinstance(voice_name, dict)
                else self.get_voice_config_by_name(voice_name)
            ) or \
                self.voice_configs.get("RealLossless", list(self.voice_configs.values())[0])
            tone_id = str(voice_config.get("tone_id", "0"))
            signed_client = self._signed_fanqie_client()
            if signed_client:
                plays = signed_client.audio_playinfo([str(chapter_id)], int(tone_id or 0))
                if plays:
                    return plays[0]
            params = {
                "item_ids": str(chapter_id),
                "pv_player": "-1",
                "aid": "3040",
                "device_platform": "android",
                "_rticket": str(int(time.time() * 1000)),
            }
            if voice_config.get("is_real_person") == "0":
                params["tone_id"] = tone_id
                params["is_real_person"] = "0"
                ai_voice_id = self.get_ai_voice_id_by_tone_id(params["tone_id"])
                if ai_voice_id:
                    params["ai_voice_id"] = ai_voice_id
            else:
                params["tone_id"] = "0"
                params["is_real_person"] = "1"
                params["real_person_voice"] = "1"

            headers = {
                "User-Agent": "com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)",
            }
            resp = requests.get(
                "https://reading.snssdk.com/reading/reader/audio/playinfo/",
                params=params, headers=headers, timeout=15, verify=False,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("code") != 0:
                return None
            audio_data = data.get("data", [])
            if isinstance(audio_data, list) and audio_data:
                return audio_data[0]
            if isinstance(audio_data, dict):
                return audio_data
            return None
        except Exception as e:
            print(f"⚠️ _get_play_dict 异常: {e}")
            return None

    def download_changting_chapter(
        self,
        chapter_id: str,
        voice_name: str | Dict,
        output_path: str,
        quality: Optional[str] = None,
    ) -> bool:
        """番茄畅听专用：走 playinfo → fanqie_portable.py CENC解密管线下载。

        与 FanqieTingshuManager.download_chapter 逻辑对齐：
        - 优先从 playinfo/ 取 play dict（含 is_encrypt）
        - 调用 fanqie_portable.py 的 download_chapter_audio 处理 CENC 解密与 ffmpeg 转码
        - 输出路径后缀为 .mp3 时自动转码（libmp3lame），为 .m4a 时保留 AAC
        """
        try:
            from core.fanqie_tingshu_manager import _load_wanzheng_module
            mod = _load_wanzheng_module()
        except Exception as e:
            print(f"⚠️ 无法加载完整版模块，回退普通下载: {e}")
            return False

        normalized_id = str(chapter_id).replace("chapter-", "", 1)
        play = self._get_play_dict(normalized_id, voice_name)
        if not play or not (play.get("main_url") or play.get("backup_url")):
            print(f"⚠️ 番茄畅听: 无 play dict，回退普通下载")
            return False

        out = Path(output_path)
        tmp_out = out.with_name(f"{out.stem}.part{out.suffix}")
        raw_out = out.with_name(f"{out.stem}.raw{out.suffix}")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            if tmp_out.exists():
                tmp_out.unlink()
            if raw_out.exists():
                raw_out.unlink()
            mod.download_chapter_audio(play, raw_out, {
                "User-Agent": "com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)",
            })
            wants_m4a_output = (
                out.suffix.lower() == ".m4a"
                and not str(quality or "").upper().startswith("MP3")
                and hasattr(mod, "_transcode_audio")
                and hasattr(mod, "find_ffmpeg")
            )
            if wants_m4a_output:
                ffmpeg = mod.find_ffmpeg()
                if ffmpeg:
                    mod._transcode_audio(raw_out, tmp_out, ffmpeg=ffmpeg, codec="m4a")
                else:
                    raw_out.replace(tmp_out)
            else:
                raw_out.replace(tmp_out)
            if raw_out.exists():
                raw_out.unlink()
            if tmp_out.is_file() and tmp_out.stat().st_size > 1024:
                tmp_out.replace(out)
                print(f"✅ 番茄畅听下载完成: {out} ({out.stat().st_size // 1024} KB)")
                return True
            print(f"❌ 番茄畅听: 文件过小或解密失败")
            if tmp_out.exists():
                tmp_out.unlink()
            if raw_out.exists():
                raw_out.unlink()
            return False
        except Exception as e:
            try:
                if tmp_out.exists():
                    tmp_out.unlink()
                if raw_out.exists():
                    raw_out.unlink()
            except OSError:
                pass
            print(f"❌ 番茄畅听 download_changting_chapter 异常: {e}")
            return False

    def download_audio(self, url: str, save_path: str, progress_callback=None) -> bool:
        """下载音频（简单 HTTP，适用于未加密音频）"""
        try:
            # 番茄音频URL需要特殊的请求头（字节跳动CDN）
            # 注意：不能使用https://novelfm.com/作为Referer，会导致403错误
            # 应该不使用Referer，避免触发CDN的referer访问规则
            headers = {
                'User-Agent': 'com.xs.fm/608 (Linux; U; Android 9; zh_CN; 2210132C; Build/PQ3A.190605.07021633;tt-ok/3.12.13.17)',
                'Accept': '*/*',
                'Connection': 'keep-alive'
                # 不设置Referer，避免触发CDN的referer访问规则
            }
            
            print(f"🍅 下载番茄音频，使用专用请求头")
            response = requests.get(url, headers=headers, stream=True, timeout=30)
            if response.status_code == 200:
                total_size = int(response.headers.get('Content-Length') or 0)
                downloaded_size = 0
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=262144):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded_size, total_size)
                
                import os
                file_size = os.path.getsize(save_path)
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

    def _signed_fanqie_client(self):
        """加载 fanqie_portable.py 的签名客户端，用于获取番茄官方动态音色。"""
        if self._signed_client is not None:
            return self._signed_client
        try:
            from core.fanqie_tingshu_manager import _load_wanzheng_module
            mod = _load_wanzheng_module()
            with contextlib.redirect_stdout(io.StringIO()):
                self._signed_client = mod.FanqieClient(use_capture=False)
            return self._signed_client
        except Exception as e:
            print(f"WARN: 番茄动态音色客户端初始化失败: {e}")
            return None

    def fetch_tone_info(self, book_id: str, force_refresh: bool = False) -> Dict:
        """获取番茄畅听官方 toneinfo。

        参照 4.7.14：优先读取 audio_tones / tts_tones / recommend_tone。
        """
        bid = str(book_id or "")
        if not bid:
            return {}
        if not force_refresh and bid in self._tone_info_cache:
            return self._tone_info_cache[bid]
        client = self._signed_fanqie_client()
        if not client:
            return {}
        try:
            data = client.audio_toneinfo(bid) or {}
            if isinstance(data, dict):
                self._tone_info_cache[bid] = data
                print(
                    f"✅ 番茄畅听动态音色: 真人 {len(data.get('audio_tones') or [])} 个, "
                    f"AI {len(data.get('tts_tones') or [])} 个"
                )
                return data
        except Exception as e:
            print(f"⚠️ 番茄畅听动态音色加载失败: {e}")
        return {}

    @staticmethod
    def _novel_id_for_tts(tone_data: Dict, book_id: str) -> str:
        if str(tone_data.get("req_book_genre_type") or "") in ("1", "AUDIOBOOK"):
            rel = tone_data.get("relate_novel_bookid") or tone_data.get("relate_novel_bookid_str")
            if rel:
                return str(rel)
        return str(book_id)

    def fetch_voices(self, book_id: str, force_refresh: bool = False) -> List[Dict]:
        """按书籍动态返回番茄畅听音色列表。失败时保留静态兜底。"""
        bid = str(book_id or "")
        if not bid:
            return []
        if not force_refresh and bid in self._voices_cache:
            return self._voices_cache[bid]

        tone_data = self.fetch_tone_info(bid, force_refresh=force_refresh)
        voices: List[Dict] = []
        if tone_data:
            for item in tone_data.get("audio_tones") or []:
                abook_id = item.get("abook_id")
                if not abook_id:
                    continue
                name = str(item.get("title") or item.get("tone_name") or "真人演播").strip()
                voices.append({
                    "id": f"real:{abook_id}",
                    "voice_id": f"real:{abook_id}",
                    "tone_id": "0",
                    "name": name,
                    "title": name,
                    "is_real_person": "1",
                    "real_person_voice": "1",
                    "kind": "real",
                    "category": "真人录制",
                    "book_id": str(abook_id),
                    "play_book_id": str(abook_id),
                    "download_book_id": str(abook_id),
                    "icon_url": item.get("icon_url", ""),
                    "raw": item,
                })

            novel_id = self._novel_id_for_tts(tone_data, bid)
            for item in tone_data.get("tts_tones") or []:
                tid = item.get("id") or item.get("tone_id")
                if tid in (None, ""):
                    continue
                name = str(item.get("title") or item.get("tone_name") or f"AI音色 {tid}").strip()
                desc = str(item.get("description") or "").strip()
                label = f"{name} · {desc}" if desc else name
                voices.append({
                    "id": f"tts:{tid}",
                    "voice_id": f"tts:{tid}",
                    "tone_id": str(tid),
                    "name": label,
                    "title": name,
                    "is_real_person": "0",
                    "kind": "ai",
                    "category": "AI 音色",
                    "book_id": str(novel_id),
                    "play_book_id": str(novel_id),
                    "download_book_id": str(novel_id),
                    "icon_url": item.get("icon_url", ""),
                    "raw": item,
                })

        if not voices:
            for key, config in self.voice_configs.items():
                fallback = dict(config)
                fallback.setdefault("id", key)
                fallback.setdefault("voice_id", key)
                fallback.setdefault("kind", "real" if fallback.get("is_real_person") == "1" else "ai")
                fallback.setdefault("book_id", bid)
                fallback.setdefault("play_book_id", bid)
                fallback.setdefault("download_book_id", bid)
                voices.append(fallback)

        self._voices_cache[bid] = voices
        return voices

    def resolve_voice_config(self, book_id: str, voice_config: Optional[Dict] = None) -> Optional[Dict]:
        """把前端传回的音色对象匹配为当前书籍的完整配置。"""
        if not isinstance(voice_config, dict):
            return None
        voices = self.fetch_voices(book_id) if book_id else []
        if not voices:
            return voice_config
        vid = str(voice_config.get("id") or voice_config.get("voice_id") or "")
        name = str(voice_config.get("name") or voice_config.get("title") or "")
        tone_id = str(voice_config.get("tone_id") or "")
        play_book_id = str(voice_config.get("play_book_id") or voice_config.get("book_id") or "")
        for candidate in voices:
            if vid and vid in {str(candidate.get("id") or ""), str(candidate.get("voice_id") or "")}:
                return candidate
        for candidate in voices:
            if play_book_id and play_book_id == str(candidate.get("play_book_id") or candidate.get("book_id") or "") and tone_id == str(candidate.get("tone_id") or ""):
                return candidate
        for candidate in voices:
            if name and name in {str(candidate.get("name") or ""), str(candidate.get("title") or "")}:
                return candidate
        for candidate in voices:
            if tone_id and tone_id == str(candidate.get("tone_id") or "") and str(voice_config.get("kind") or "") == str(candidate.get("kind") or ""):
                return candidate
        return voice_config
    
    def get_available_voices(self) -> List[str]:
        """获取可用音效列表"""
        return [config["name"] for config in self.voice_configs.values()]
    
    def get_voices_by_category(self) -> Dict[str, List[Dict]]:
        """获取按分类组织的音色列表"""
        categorized = {}
        for voice_id, config in self.voice_configs.items():
            category = config.get("category", "其他")
            if category not in categorized:
                categorized[category] = []
            categorized[category].append({
                "id": voice_id,
                "name": config["name"],
                "tone_id": config["tone_id"],
                "is_real_person": config.get("is_real_person", "0"),
                "ai_voice_id": config.get("ai_voice_id", ""),
                "quality": config.get("quality", "standard")
            })
        return categorized
    
    def get_voice_config_by_name(self, voice_name: str) -> Optional[Dict]:
        """根据音色名称获取配置"""
        if isinstance(voice_name, dict):
            return voice_name
        wanted = str(voice_name or "").strip()
        for voices in self._voices_cache.values():
            for config in voices:
                if wanted in {
                    str(config.get("name") or "").strip(),
                    str(config.get("title") or "").strip(),
                    str(config.get("id") or "").strip(),
                    str(config.get("voice_id") or "").strip(),
                }:
                    return config
        for config in self.voice_configs.values():
            if config["name"] == voice_name:
                return config
        return None
    
    def get_ai_voice_id_by_tone_id(self, tone_id: str) -> Optional[str]:
        """
        根据tone_id获取AI音色ID - 与APP版本完全一致
        注意：APP版本中default返回"sweet_girl"，但实际使用时如果返回null则不添加参数
        """
        if tone_id == "0" or tone_id is None:
            return None  # 真人音色不需要ai_voice_id
        
        # 根据APP版本的getAiVoiceId方法映射（完全一致）
        tone_to_ai_voice_map = {
            "1": "sweet_girl",           # AI甜美少女音
            "2": "clear_young_uncle",     # AI清亮青叔音
            "5": "magnetic_young_uncle",  # AI磁性青叔音
            "6": "refined_young_uncle",   # AI斯文青叔音
            "74": "elegant_uncle",        # AI儒雅大叔音
            "82": "mature_uncle_1",       # AI成熟大叔音1
            "85": "playful_bigsister",    # AI俏皮御姐音
        }
        
        # APP版本中default返回"sweet_girl"，但实际逻辑中如果getAiVoiceId返回null则不添加参数
        # 这里我们只返回映射表中存在的值，未映射的返回None（不添加ai_voice_id参数）
        return tone_to_ai_voice_map.get(tone_id)
    
    def _get_default_voice_configs(self) -> Dict:
        """获取默认音效配置 - 保持您的27个音色名称，使用已验证的有效ID"""
        return {
            # 真人录制类 (2个)
            "RealLossless": {
                "tone_id": "0",  # 修复：使用正确的数值ID
                "name": "无损真人录制",
                "is_real_person": "1",
                "real_person_voice": "1",
                "quality": "lossless",
                "category": "真人录制"
            },
            "RealDefault": {
                "tone_id": "0",  # 修复：使用正确的数值ID
                "name": "标准真人录制",
                "is_real_person": "1",
                "real_person_voice": "1",
                "quality": "standard",
                "category": "真人录制"
            },
            
            # AI多角色对话类 (5个) - 按照您提供的voiceAI映射
            "AI_Multi_Dialogue_1": {
                "tone_id": "1",
                "name": "AI多角色对话1",
                "is_real_person": "0",
                "ai_voice_id": "multi_dialogue_1",
                "category": "AI多角色"
            },
            "AI_Multi_Dialogue_2": {
                "tone_id": "2",
                "name": "AI多角色对话2",
                "is_real_person": "0",
                "ai_voice_id": "multi_dialogue_2",
                "category": "AI多角色"
            },
            "AI_Multi_Dialogue_3": {
                "tone_id": "80",
                "name": "AI多角色对话3",
                "is_real_person": "0",
                "ai_voice_id": "multi_dialogue_3",
                "category": "AI多角色"
            },
            "AI_Multi_Dialogue_4": {
                "tone_id": "82",
                "name": "AI多角色对话4",
                "is_real_person": "0",
                "ai_voice_id": "multi_dialogue_4",
                "category": "AI多角色"
            },
            "AI_Multi_Dialogue_5": {
                "tone_id": "85",
                "name": "AI多角色对话5",
                "is_real_person": "0",
                "ai_voice_id": "multi_dialogue_5",
                "category": "AI多角色"
            },
            
            # AI多角色升级类 (3个) - 按照您提供的voiceAI映射
            "AI_Multi_Upgrade_3": {
                "tone_id": "80",
                "name": "AI多角色升级3",
                "is_real_person": "0",
                "ai_voice_id": "multi_upgrade_3",
                "category": "AI多角色"
            },
            "AI_Multi_Upgrade_4": {
                "tone_id": "82",
                "name": "AI多角色升级4",
                "is_real_person": "0",
                "ai_voice_id": "multi_upgrade_4",
                "category": "AI多角色"
            },
            "AI_Multi_Upgrade_5": {
                "tone_id": "85",
                "name": "AI多角色升级5",
                "is_real_person": "0",
                "ai_voice_id": "multi_upgrade_5",
                "category": "AI多角色"
            },
            
            # AI双角色对话类 (3个) - 使用已验证的有效ID
            "AI_Dual_Dialogue_1": {
                "tone_id": "1",
                "name": "AI双角色对话1",
                "is_real_person": "0",
                "ai_voice_id": "dual_dialogue_1",
                "category": "AI双角色"
            },
            "AI_Dual_Dialogue_2": {
                "tone_id": "2",
                "name": "AI双角色对话2",
                "is_real_person": "0",
                "ai_voice_id": "dual_dialogue_2",
                "category": "AI双角色"
            },
            "AI_Dual_Dialogue_3": {
                "tone_id": "5",
                "name": "AI双角色对话3",
                "is_real_person": "0",
                "ai_voice_id": "dual_dialogue_3",
                "category": "AI双角色"
            },
            
            # AI人名音色类 (2个) - 使用已验证的有效ID
            "AI_WangMingjun": {
                "tone_id": "6",
                "name": "AI王明军音",
                "is_real_person": "0",
                "ai_voice_id": "wang_mingjun",
                "category": "AI人名音色"
            },
            "AI_ManChao": {
                "tone_id": "74",
                "name": "AI满超音",
                "is_real_person": "0",
                "ai_voice_id": "man_chao",
                "category": "AI人名音色"
            },
            
            # AI应用风格类 (2个) - 使用已验证的有效ID
            "AI_WebNovel_Narration": {
                "tone_id": "82",
                "name": "AI网文解说音",
                "is_real_person": "0",
                "ai_voice_id": "webnovel_narration",
                "category": "AI应用风格"
            },
            "AI_Cheerful_Youth": {
                "tone_id": "1",
                "name": "AI开朗青年音",
                "is_real_person": "0",
                "ai_voice_id": "cheerful_youth",
                "category": "AI应用风格"
            },
            "AI_Gentle_Female": {
                "tone_id": "3",
                "name": "AI温柔女声音",
                "is_real_person": "0",
                "ai_voice_id": "gentle_female",
                "category": "AI应用风格"
            },
            "AI_Calm_Male": {
                "tone_id": "4",
                "name": "AI沉稳男声音",
                "is_real_person": "0",
                "ai_voice_id": "calm_male",
                "category": "AI应用风格"
            },
            "AI_Lively_Female": {
                "tone_id": "5",
                "name": "AI活泼女声音",
                "is_real_person": "0",
                "ai_voice_id": "lively_female",
                "category": "AI应用风格"
            },
            "AI_Magnetic_Male": {
                "tone_id": "8",
                "name": "AI磁性男声音",
                "is_real_person": "0",
                "ai_voice_id": "magnetic_male",
                "category": "AI应用风格"
            },
            "AI_Sweet_Female": {
                "tone_id": "9",
                "name": "AI甜美女声音",
                "is_real_person": "0",
                "ai_voice_id": "sweet_female",
                "category": "AI应用风格"
            },
            "AI_Vigorous_Male": {
                "tone_id": "10",
                "name": "AI有力男声音",
                "is_real_person": "0",
                "ai_voice_id": "vigorous_male",
                "category": "AI应用风格"
            },
        }
