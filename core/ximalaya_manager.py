#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
喜马拉雅管理器 - 完整的API集成
支持搜索、下载、封面展示、音质检测等功能
"""

import requests
import time
import base64
import json
import urllib.parse
from typing import List, Dict, Optional, Tuple
from .time_api import get_timestamp_ms_str

class XimalayaManager:
    """喜马拉雅管理器"""
    
    def __init__(self):
        self.base_url = "https://www.ximalaya.com"
        self.mobile_url = "https://m.ximalaya.com"
        self.mobwsa_url = "https://mobwsa.ximalaya.com"
        self.api_url = "https://www.ximalaya.com"
        self.cookie_string = ""
        self.session = requests.Session()
        self.user_id = None
        self.user_token = None
        
        # 设置默认请求头（基于您原有文件的配置）
        self.session.headers.update({
            'User-Agent': 'ting_9.4.2_AndroidPhone_2210132C_1440x2560',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.ximalaya.com/',
            'X-Requested-With': 'XMLHttpRequest',
        })
        
        # 添加xm-sign字段（从参考文件中获取）
        self.xm_sign = 'D256Y2bWKF5J/mrAkYx/IDBCUgI24xpXXFQE/gVJsU8b0X8a&&ptVWBRvUjLUvn9O8cP3uP9oALcOgOAzk0FOvKOLMRkM_1'
        
        # 音频URL解密密钥和S-box
        self.key_www2 = bytes([204, 53, 135, 197, 39, 73, 58, 160, 79, 24, 12, 83, 180, 250, 101, 60, 206, 30, 10, 227, 36, 95, 161, 16, 135, 150, 235, 116, 242, 116, 165, 171])
        self.sbox_www2 = bytes([183, 174, 108, 16, 131, 159, 250, 5, 239, 110, 193, 202, 153, 137, 251, 176, 119, 150, 47, 204, 97, 237, 1, 71, 177, 42, 88, 218, 166, 82, 87, 94, 14, 195, 69, 127, 215, 240, 225, 197, 238, 142, 123, 44, 219, 50, 190, 29, 181, 186, 169, 98, 139, 185, 152, 13, 141, 76, 6, 157, 200, 132, 182, 49, 20, 116, 136, 43, 155, 194, 101, 231, 162, 242, 151, 213, 53, 60, 26, 134, 211, 56, 28, 223, 107, 161, 199, 15, 229, 61, 96, 41, 66, 158, 254, 21, 165, 253, 103, 89, 3, 168, 40, 246, 81, 95, 58, 31, 172, 78, 99, 45, 148, 187, 222, 124, 55, 203, 235, 64, 68, 149, 180, 35, 113, 207, 118, 111, 91, 38, 247, 214, 7, 212, 209, 189, 241, 18, 115, 173, 25, 236, 121, 249, 75, 57, 216, 10, 175, 112, 234, 164, 70, 206, 198, 255, 140, 230, 12, 32, 83, 46, 245, 0, 62, 227, 72, 191, 156, 138, 248, 114, 220, 90, 84, 170, 128, 19, 24, 122, 146, 80, 39, 37, 8, 34, 22, 11, 93, 130, 63, 154, 244, 160, 144, 79, 23, 133, 92, 54, 102, 210, 65, 67, 27, 196, 201, 106, 143, 52, 74, 100, 217, 179, 48, 233, 126, 117, 184, 226, 85, 171, 167, 86, 2, 147, 17, 135, 228, 252, 105, 30, 192, 129, 178, 120, 36, 145, 51, 163, 77, 205, 73, 4, 188, 125, 232, 33, 243, 109, 224, 104, 208, 221, 59, 9])
        
        # 音质级别配置（基于实际测试结果）
        self.quality_levels = {
            'mobile': {
                '24k': {'level': 0, 'name': '24k标准音质', 'size': '约3.03MB', 'format': 'M4A'},
                '32k': {'level': 1, 'name': '32k标准音质', 'size': '约4.0MB', 'format': 'M4A'},
                '48k': {'level': 2, 'name': '48k高清音质', 'size': '约5.97MB', 'format': 'M4A'},
                '64k': {'level': 3, 'name': '64k高清音质', 'size': '约7.5MB', 'format': 'M4A'},
                '96k': {'level': 4, 'name': '96k超高音质', 'size': '约11.85MB', 'format': 'M4A'},
            },
            'web': {
                'MP3_24k': {'level': 0, 'name': '24k标准音质', 'size': '约2.5MB', 'format': 'MP3'},
                'MP3_32k': {'level': 1, 'name': '32k标准音质', 'size': '约3.0MB', 'format': 'MP3'},
                'MP3_48k': {'level': 2, 'name': '48k高清音质', 'size': '约4.5MB', 'format': 'MP3'},
                'MP3_64k': {'level': 3, 'name': '64k高清音质', 'size': '约6.0MB', 'format': 'MP3'},
                'MP3_96k': {'level': 4, 'name': '96k超高音质', 'size': '约8.5MB', 'format': 'MP3'},
                'MP3_128k': {'level': 5, 'name': '128k超高音质', 'size': '约11.0MB', 'format': 'MP3'},
                'M4A_24k': {'level': 0, 'name': '24k标准音质', 'size': '约3.03MB', 'format': 'M4A'},
                'M4A_32k': {'level': 1, 'name': '32k标准音质', 'size': '约4.0MB', 'format': 'M4A'},
                'M4A_48k': {'level': 2, 'name': '48k高清音质', 'size': '约5.97MB', 'format': 'M4A'},
                'M4A_64k': {'level': 3, 'name': '64k高清音质', 'size': '约7.5MB', 'format': 'M4A'},
                'M4A_96k': {'level': 4, 'name': '96k超高音质', 'size': '约11.85MB', 'format': 'M4A'},
            }
        }
        
        # UI音质选项到实际音质的映射
        # 根据实际测试，AAC_164(1.90MB) > AAC_224(0.96MB)，所以AAC_164音质更好
        # 修复后的优先级：AAC_296 > AAC_164 > AAC_224 > HQ
        self.ui_quality_mapping = {
            "M4A 96K": ["AAC_296", "HQ", "M4A_96k", "AAC_224", "AAC_164"],
            "M4A 48K": ["AAC_224", "AAC_164", "M4A_48k", "MP3_64"],
            "M4A 24K": ["M4A_24k", "AAC_224", "MP3_32", "M4A_32k"],  # 低音质，优先 M4A
            "MP3 64K": ["MP3_64"],  # 网页端MP3最高64K，level=1
            "MP3 48K": ["MP3_32"]   # 网页端MP3，level=0
        }

    def _extract_cover_url(self, item: Dict) -> str:
        """兼容喜马拉雅不同接口返回的封面字段。"""
        if not isinstance(item, dict):
            return ""
        keys = (
            "cover_path", "coverPath", "cover", "cover_url", "coverUrl",
            "coverLarge", "coverMiddle", "coverSmall", "largeCover", "smallCover",
            "albumCover", "albumCoverUrl", "album_cover", "album_cover_url",
            "pic", "picUrl", "image", "imageUrl", "img", "imgPath",
            "itemCoverUrl", "itemSquareCoverUrl", "trackCoverPath",
            "thumbnail", "thumbnailUrl", "poster", "posterUrl",
        )
        for key in keys:
            value = item.get(key)
            if value:
                cover_url = str(value).strip()
                if cover_url.startswith("//"):
                    return "https:" + cover_url
                if cover_url.startswith("http"):
                    return cover_url
                if cover_url.startswith("/"):
                    return "https://imagev2.xmcdn.com" + cover_url
                return "https://imagev2.xmcdn.com/" + cover_url
        for key in ("album", "item", "data", "raw"):
            nested = item.get(key)
            if isinstance(nested, dict):
                cover_url = self._extract_cover_url(nested)
                if cover_url:
                    return cover_url
        return ""

    def _extract_author_name(self, item: Dict) -> str:
        """兼容喜马拉雅不同接口里的专辑发布账号/主播字段。"""
        if not isinstance(item, dict):
            return ""
        keys = (
            "anchorNickName", "anchorNickname", "anchorName", "nickname", "nickName",
            "userName", "userNickname", "userNickName", "announcer", "authorName",
            "author", "speaker", "artist",
        )
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return str(value).strip()
        for key in ("anchor", "anchorInfo", "announcerInfo", "user", "userInfo", "creator", "album", "item", "data", "raw"):
            nested = item.get(key)
            if isinstance(nested, dict):
                value = self._extract_author_name(nested)
                if value:
                    return value
        return ""
    
    def set_cookie(self, cookie_string: str, is_server_cookie: bool = False):
        """设置Cookie
        
        Args:
            cookie_string: Cookie字符串
            is_server_cookie: 是否为服务器获取的Cookie（SVIP用户）
        """
        self.cookie_string = cookie_string
        self._is_server_cookie = is_server_cookie  # 标记是否为服务器Cookie
        
        if cookie_string:
            # 清理Cookie字符串，移除BOM和不可见字符
            cleaned_cookie = cookie_string.encode('utf-8').decode('utf-8-sig').strip()
            # 过滤非打印字符
            cleaned_cookie = ''.join(char for char in cleaned_cookie if ord(char) >= 32 or char in '\t\n\r')
            self.session.headers['Cookie'] = cleaned_cookie
            
            if is_server_cookie:
                print("🍪 服务器Cookie已设置（SVIP用户）")
            else:
                print("🍪 本地Cookie已设置")
            
            # 从Cookie中提取用户ID用于API请求
            self._extract_user_info_from_cookie(cleaned_cookie)
    
    def clear_server_cookie(self):
        """清空服务器Cookie（只清空服务器获取的Cookie，保留本地Cookie）"""
        if hasattr(self, '_is_server_cookie') and self._is_server_cookie:
            self.cookie_string = ""
            if 'Cookie' in self.session.headers:
                del self.session.headers['Cookie']
            self._is_server_cookie = False
            print("🧹 已清空服务器Cookie（保留本地Cookie）")
    
    def force_clear_server_cookie(self):
        """强制清空服务器Cookie（不依赖_is_server_cookie标志，用于SVIP到期或切换平台时）"""
        # 强制清空，不检查_is_server_cookie标志
        # 如果_is_server_cookie为True，说明是服务器Cookie，需要清空
        if hasattr(self, '_is_server_cookie') and self._is_server_cookie:
            if hasattr(self, 'cookie_string'):
                self.cookie_string = ""
            if 'Cookie' in self.session.headers:
                del self.session.headers['Cookie']
            self._is_server_cookie = False
            print("🧹 强制清空服务器Cookie（SVIP到期或切换平台）")
    
    def _validate_svip_before_use(self):
        """在使用服务器Cookie前验证SVIP状态（防止过期后继续使用）"""
        try:
            from core.license_manager import LicenseManager
            license_manager = LicenseManager()
            vip_result = license_manager.quick_validate()
            
            if vip_result and vip_result.get('valid', False):
                is_svip = vip_result.get('is_svip', False)
                svip_expire_time = vip_result.get('svip_expire_time', 0)
                
                if is_svip and svip_expire_time > 0:
                    # 检查SVIP是否过期
                    from datetime import datetime
                    expire_timestamp_seconds = svip_expire_time / 1000
                    expire_time = datetime.fromtimestamp(expire_timestamp_seconds)
                    current_time = datetime.now()
                    
                    if expire_time > current_time:
                        return True  # SVIP有效
                    else:
                        print("⚠️ SVIP已过期，无法使用服务器Cookie")
                        return False
            
            return False  # 不是SVIP或已过期
        except Exception as e:
            print(f"⚠️ 验证SVIP状态异常: {e}")
            return False  # 验证失败，不允许使用服务器Cookie
    
    def _fix_redirect_url_level(self, url: str, correct_level: int) -> str:
        """修正重定向URL中的level参数
        
        API返回的重定向URL的level参数可能不正确，需要根据音质手动修正
        例如：AAC_224的URL可能是level=0，但实际应该是level=96才能获取高音质
        """
        if not url or not isinstance(url, str):
            return url
        
        # 检查是否是重定向URL
        if '/mobile/redirect/free/play/' in url:
            # 提取trackId和当前level
            parts = url.split('/mobile/redirect/free/play/')
            if len(parts) == 2:
                rest = parts[1]
                track_parts = rest.split('/')
                if len(track_parts) >= 2:
                    track_id = track_parts[0]
                    old_level = track_parts[1].split('?')[0]  # 移除query参数
                    
                    # 重新构建URL，使用正确的level
                    new_url = f"http://mobile.ximalaya.com/mobile/redirect/free/play/{track_id}/{correct_level}"
                    
                    if old_level != str(correct_level):
                        print(f"   🔧 修正重定向URL level: {old_level} → {correct_level}")
                    
                    return new_url
        
        # 如果不是重定向URL，直接返回
        return url

    _WEB_QUALITY_ALIASES = {
        'M4A_24': 'M4A_24k',
        'M4A_64': 'M4A_64k',
        'MP3_32': 'MP3_32',
        'MP3_64': 'MP3_64',
        'AAC_24': 'AAC_24',
        'AAC_164': 'AAC_164',
        'AAC_224': 'AAC_224',
    }

    def _is_valid_cdn_url(self, url: str) -> bool:
        if not url or not isinstance(url, str) or not url.startswith('http'):
            return False
        lower = url.lower()
        if 'xmcdn.com' not in lower and 'ximalaya.com' not in lower:
            return False
        return any(x in lower for x in ('.mp3', '.m4a', '.aac', '/storages/', 'aod.cos'))

    def _decrypt_audio_url_aes(self, encrypted_url: str) -> Optional[str]:
        """网页端密文：CryptoJS AES-ECB（MP3/M4A 均适用）"""
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad

            aes_key = bytes.fromhex('aaad3e4fd540b0f79dca95606e72bf93')
            decoded = base64.urlsafe_b64decode(encrypted_url + '==')
            cipher = AES.new(aes_key, AES.MODE_ECB)
            raw = cipher.decrypt(decoded)
            try:
                raw = unpad(raw, AES.block_size)
            except Exception:
                if raw and raw[-1] <= 16:
                    raw = raw[:-raw[-1]]
            result = raw.decode('utf-8').strip()
            if self._is_valid_cdn_url(result):
                return result
        except Exception as e:
            print(f"   ⚠️ AES解密失败: {e}")
        return None

    def _decrypt_audio_url_xor(self, encrypted_url: str) -> str:
        """旧版 S-box/XOR（仅作兜底）"""
        try:
            if encrypted_url.startswith('http'):
                return encrypted_url

            audio_key = bytes([204, 53, 135, 197, 39, 73, 58, 160, 79, 24, 12, 83, 180, 250, 101, 60, 206, 30, 10, 227, 36, 95, 161, 16, 135, 150, 235, 116, 242, 116, 165, 171])
            s_box = bytes([183, 174, 108, 16, 131, 159, 250, 5, 239, 110, 193, 202, 153, 137, 251, 176, 119, 150, 47, 204, 97, 237, 1, 71, 177, 42, 88, 218, 166, 82, 87, 94, 14, 195, 69, 127, 215, 240, 225, 197, 238, 142, 123, 44, 219, 50, 190, 29, 181, 186, 169, 98, 139, 185, 152, 13, 141, 76, 6, 157, 200, 132, 182, 49, 20, 116, 136, 43, 155, 194, 101, 231, 162, 242, 151, 213, 53, 60, 26, 134, 211, 56, 28, 223, 107, 161, 199, 15, 229, 61, 96, 41, 66, 158, 254, 21, 165, 253, 103, 89, 3, 168, 40, 246, 81, 95, 58, 31, 172, 78, 99, 45, 148, 187, 222, 124, 55, 203, 235, 64, 68, 149, 180, 35, 113, 207, 118, 111, 91, 38, 247, 214, 7, 212, 209, 189, 241, 18, 115, 173, 25, 236, 121, 249, 75, 57, 216, 10, 175, 112, 234, 164, 70, 206, 198, 255, 140, 230, 12, 32, 83, 46, 245, 0, 62, 227, 72, 191, 156, 138, 248, 114, 220, 90, 84, 170, 128, 19, 24, 122, 146, 80, 39, 37, 8, 34, 22, 11, 93, 130, 63, 154, 244, 160, 144, 79, 23, 133, 92, 54, 102, 210, 65, 67, 27, 196, 201, 106, 143, 52, 74, 100, 217, 179, 48, 233, 126, 117, 184, 226, 85, 171, 167, 86, 2, 147, 17, 135, 228, 252, 105, 30, 192, 129, 178, 120, 36, 145, 51, 163, 77, 205, 73, 4, 188, 125, 232, 33, 243, 109, 224, 104, 208, 221, 59, 9])

            url = encrypted_url.replace('_', '/').replace('-', '+')
            missing_padding = len(url) % 4
            if missing_padding:
                url += '=' * (4 - missing_padding)
            decoded = base64.b64decode(url)
            if len(decoded) < 16:
                return ''

            data_length = len(decoded) - 16
            data = bytearray(decoded[:data_length])
            iv = bytearray(decoded[data_length:])
            for i in range(len(data)):
                data[i] = s_box[data[i]]
            for i in range(0, len(data), 16):
                for j in range(min(16, len(data) - i)):
                    data[i + j] ^= iv[j]
            for i in range(0, len(data), 32):
                for j in range(min(32, len(data) - i)):
                    data[i + j] ^= audio_key[j]

            try:
                result = data.decode('utf-8')
            except UnicodeDecodeError:
                result = data.decode('ascii', errors='ignore')

            if self._is_valid_cdn_url(result):
                return result
            if result.startswith('//'):
                return 'https:' + result
        except Exception as e:
            print(f"   ⚠️ XOR解密异常: {e}")
        return ''

    def _fetch_mobile_track_base_info(self, track_id: str) -> Optional[Dict]:
        """移动端 baseInfo：优先 ios/www2，android 常返回 924 并非真下架"""
        timestamp = get_timestamp_ms_str()
        headers = {
            'User-Agent': 'ting_9.4.2_iPhone_2210132C_1170x2532',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip',
            'Connection': 'keep-alive',
            'Host': 'mobile.ximalaya.com',
        }
        if self.cookie_string:
            headers['Cookie'] = self.cookie_string
            print("🍪 已添加Cookie到移动端API请求")

        for device in ('ios', 'www2', 'android'):
            mobile_api_url = (
                f"http://mobile.ximalaya.com/v1/track/baseInfo"
                f"?device={device}&trackId={track_id}&_={timestamp}"
            )
            print(f"📡 移动端API device={device} ...")
            response = self.session.get(mobile_api_url, headers=headers, timeout=10)
            if response.status_code != 200:
                continue
            data = response.json()
            ret = data.get('ret')
            print(f"   ret={ret}, msg={str(data.get('msg', ''))[:40]}")
            if ret == 0:
                self._last_api_response = data
                print(f"✅ 移动端 device={device} 获取成功")
                return data
        return None

    def _build_audio_urls_from_mobile_data(self, data: Dict) -> Dict:
        audio_urls = {}
        mapping = [
            ('playUrl32', 'MP3_32', 0, 'MP3', 'playUrl32Size', 0),
            ('playUrl64', 'MP3_64', 1, 'MP3', 'playUrl64Size', 1),
            ('playPathAacv164', 'AAC_164', 2, 'AAC', 'playPathAacv164Size', 1),
            ('playPathAacv224', 'AAC_224', 3, 'AAC', 'playPathAacv224Size', 96),
            ('playPathHq', 'HQ', 4, 'HQ', 'playHqSize', 96),
            ('playPathAacv296', 'AAC_296', 5, 'AAC', 'playPathAacv296Size', 96),
        ]
        for field, quality, qlevel, fmt, size_field, level_fix in mapping:
            raw_url = data.get(field)
            if raw_url:
                audio_urls[quality] = {
                    'url': self._fix_redirect_url_level(raw_url, level_fix),
                    'size_mb': data.get(size_field, 0) / 1024 / 1024 if data.get(size_field) else 0,
                    'quality_level': qlevel,
                    'type': fmt,
                    'port': 'mobile',
                }
        if 'AAC_224' in audio_urls and 'M4A_24k' not in audio_urls:
            audio_urls['M4A_24k'] = dict(audio_urls['AAC_224'])
        return audio_urls

    def _build_audio_urls_from_web_playlist(self, track_id: str, play_url_list: List) -> Dict:
        audio_urls = {}
        for url_info in play_url_list:
            url_type = url_info.get('type', 'Unknown')
            encrypted_url = url_info.get('url', '')
            file_size = url_info.get('fileSize', 0)
            quality_level = url_info.get('qualityLevel', 0)
            if not encrypted_url:
                continue

            decrypted_url = self._decrypt_audio_url_aes(encrypted_url)
            if not decrypted_url:
                decrypted_url = self._decrypt_audio_url_xor(encrypted_url)
            if not self._is_valid_cdn_url(decrypted_url):
                print(f"   ⚠️ {url_type} 解密失败，跳过")
                continue

            print(f"   🔓 {url_type} 直链: {decrypted_url[:80]}...")
            fmt = 'MP3' if 'MP3' in url_type.upper() else ('AAC' if 'AAC' in url_type.upper() else 'M4A')
            entry = {
                'url': decrypted_url,
                'size_mb': file_size / 1024 / 1024 if file_size else 0,
                'quality_level': quality_level,
                'type': fmt,
                'port': 'web_aes',
            }
            audio_urls[url_type] = entry
            alias = self._WEB_QUALITY_ALIASES.get(url_type)
            if alias and alias != url_type:
                audio_urls[alias] = dict(entry)
        return audio_urls
    
    def search_albums(self, keyword: str, page: int = 1, page_size: int = 20) -> List[Dict]:
        """搜索专辑 - 支持多页获取所有结果"""
        try:
            print(f"🔍 喜马拉雅搜索: {keyword}")
            
            all_books = []
            page_num = 1
            max_pages = 20  # 最多获取20页结果，获取更多搜索结果
            
            while page_num <= max_pages:
                print(f"📄 获取第 {page_num} 页搜索结果...")
                
                # 如果有Cookie，优先尝试带Cookie的搜索
                if self.cookie_string:
                    # 🔧 如果是服务器Cookie，先验证SVIP状态
                    if hasattr(self, '_is_server_cookie') and self._is_server_cookie:
                        if not self._validate_svip_before_use():
                            # SVIP已过期，强制清空服务器Cookie
                            self.force_clear_server_cookie()
                            print("⚠️ SVIP已过期，已强制清空服务器Cookie，使用无Cookie模式搜索")
                        else:
                            print(f"🍪 使用服务器Cookie进行搜索（SVIP用户）...")
                            real_results = self._search_with_cookie(keyword, page_num, page_size)
                    else:
                        print(f"🍪 使用本地Cookie进行搜索...")
                        real_results = self._search_with_cookie(keyword, page_num, page_size)
                    if real_results:
                        all_books.extend(real_results)
                        if len(real_results) < page_size:
                            print(f"✅ 已获取到最后一页，总共找到 {len(all_books)} 本书")
                            break
                        page_num += 1
                        continue
                
                # 无Cookie搜索每次只获取第一页，避免重复请求
                if page_num > 1:
                    print(f"✅ 无Cookie模式只获取第一页，总共找到 {len(all_books)} 个专辑")
                    break
                
                # 无Cookie搜索 - 这是正常情况，不需要Cookie也能搜索
                print(f"🔍 使用无Cookie模式进行搜索...")
                
                # 使用喜马拉雅.py中更有效的搜索API端点
                url = f"{self.base_url}/revision/search"
                params = {
                    'core': 'album',
                    'spellchecker': 'true',
                    'rows': page_size,
                    'condition': 'relation',
                    'device': 'iPhone',
                    'fq': '',
                    'paidFilter': 'false',
                    'kw': keyword,
                    'page': page_num
                }
                
                # 添加更多请求头模拟真实浏览器
                import random
                import time
                
                # 添加随机延迟以避免被检测
                time.sleep(random.uniform(0.5, 1.5))
                
                headers = dict(self.session.headers)
                headers.update({
                    'Referer': 'https://www.ximalaya.com/',
                    'Origin': 'https://www.ximalaya.com',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                    'DNT': '1',
                    'Upgrade-Insecure-Requests': '1',
                    'sec-ch-ua-platform': '"Android"'
                })
                
                try:
                    response = self.session.get(url, params=params, headers=headers, timeout=10)
                    
                    print(f"🔍 尝试URL: {url}")
                    print(f"📊 响应状态: {response.status_code}")
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            print(f"📋 响应数据键: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                            
                            # 检查是否被风险检测拦截
                            if isinstance(data, dict) and 'data' in data:
                                data_content = data['data']
                                if isinstance(data_content, dict) and 'reason' in data_content:
                                    reason = data_content.get('reason', '')
                                    if 'risk' in reason.lower():
                                        print(f"⚠️ 被风险检测拦截: {reason}")
                                        # 不再尝试其他URL，直接返回空结果
                                # 检查ret字段
                                elif data.get('ret') == 200 and 'result' in data_content:
                                    # 这是正常的数据结构
                                    pass
                                elif data.get('ret') != 0 and data.get('ret') != 200:
                                    print(f"❌ API返回错误码: {data.get('ret')}")
                            
                            albums = []
                            
                            # 解析搜索结果 - 尝试多种数据结构
                            if isinstance(data, dict):
                                # 方式1: data.result.response.docs (喜马拉雅.py中的数据结构)
                                if 'data' in data and isinstance(data['data'], dict):
                                    data_content = data['data']
                                    if 'result' in data_content and isinstance(data_content['result'], dict):
                                        result_content = data_content['result']
                                        if 'response' in result_content and isinstance(result_content['response'], dict):
                                            response_content = result_content['response']
                                            if 'docs' in response_content and isinstance(response_content['docs'], list):
                                                results = response_content['docs']
                                            else:
                                                results = []
                                        else:
                                            results = []
                                    else:
                                        results = data_content.get('result', [])
                                # 方式2: 直接在data中
                                elif 'result' in data:
                                    results = data['result']
                                else:
                                    # 方式3: 查找任何包含结果的字段
                                    results = []
                                    for key, value in data.items():
                                        if isinstance(value, list) and len(value) > 0:
                                            if isinstance(value[0], dict) and 'title' in value[0]:
                                                results = value
                                                break
                                
                                print(f"📝 找到 {len(results)} 个结果项")
                                
                                for item in results:
                                    if isinstance(item, dict):
                                        # 检查是否是专辑类型
                                        model_type = item.get('model_type', '')
                                        if model_type == 'album' or 'title' in item:
                                            # 调试：输出API返回的数据字段
                                            print(f"📊 喜马拉雅API返回数据字段: {list(item.keys())}")
                                            print(f"   播放量字段: play_count={item.get('play_count', 'N/A')}, plays={item.get('plays', 'N/A')}, play_count_unit={item.get('play_count_unit', 'N/A')}")
                                            print(f"   章节字段: track_count={item.get('track_count', 'N/A')}, episodes={item.get('episodes', 'N/A')}, include_track_count={item.get('include_track_count', 'N/A')}")
                                            
                                            # 判断连载状态
                                            status = "连载中"
                                            if item.get('is_finished', False) or item.get('status') == 'finished':
                                                status = "已完结"
                                            
                                            # 获取播放量和集数（API返回的是 'play' 和 'tracks' 字段）
                                            play_count = item.get('play', item.get('play_count', item.get('plays', 0)))
                                            track_count = item.get('tracks', item.get('track_count', item.get('episodes', 0)))
                                            
                                            cover_url = self._extract_cover_url(item)
                                            album = {
                                                'id': str(item.get('id', item.get('album_id', ''))),
                                                'title': item.get('title', ''),
                                                'author': self._extract_author_name(item),
                                                'platform': '喜马拉雅',
                                                'cover': cover_url,
                                                'plays': play_count,
                                                'episodes': track_count,
                                                'status': status,
                                                'description': item.get('intro', item.get('description', '')),
                                                'category': item.get('category_title', item.get('category', '')),
                                                'tags': item.get('tags', []),
                                                'created_at': item.get('created_at', ''),
                                                'updated_at': item.get('updated_at', '')
                                            }
                                            albums.append(album)
                                            print(f"   ✅ 添加专辑: {album['title']}, 播放量: {album['plays']}, 集数: {album['episodes']}")
                            
                            if albums:
                                print(f"📋 第 {page_num} 页找到 {len(albums)} 个专辑")
                                all_books.extend(albums)
                                
                                # 如果当前页结果少于page_size，说明已经是最后一页
                                if len(albums) < page_size:
                                    print(f"✅ 已获取到最后一页，总共找到 {len(all_books)} 个专辑")
                                    break
                                
                                page_num += 1
                            else:
                                print(f"❌ 第 {page_num} 页未找到结果，停止搜索")
                                break
                            
                        except Exception as json_error:
                            print(f"❌ JSON解析错误: {json_error}")
                            print(f"📄 响应内容: {response.text[:500]}...")
                            print(f"❌ 第 {page_num} 页解析失败，停止搜索")
                            break
                    else:
                        print(f"❌ 搜索失败: HTTP {response.status_code}")
                        print(f"❌ 第 {page_num} 页搜索失败，停止搜索")
                        break
                        
                except Exception as request_error:
                    print(f"❌ 请求错误: {request_error}")
                    print(f"❌ 第 {page_num} 页请求失败，停止搜索")
                    break
            
            print(f"✅ 喜马拉雅搜索完成，总共找到 {len(all_books)} 个专辑")
            return all_books
                
        except Exception as e:
            print(f"❌ 搜索异常: {e}")
            return []
    
    def get_album_detail(self, album_id: str) -> Optional[Dict]:
        """获取专辑详情"""
        try:
            url = f"{self.api_url}/revision/album/v1/getTracksList"
            params = {
                'albumId': album_id,
                'pageNum': 1,
                'pageSize': 1  # 只获取第一页来判断专辑信息
            }
            
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # 检查多种可能的成功状态码
                ret_code = data.get('ret', -1)
                if ret_code == 0 or ret_code == 200:
                    # 尝试多种数据结构
                    album_info = {}
                    if 'data' in data:
                        data_content = data['data']
                        if isinstance(data_content, dict):
                            if 'albumMainInfo' in data_content:
                                album_info = data_content['albumMainInfo']
                            else:
                                album_info = data_content
                        else:
                            album_info = {}
                    
                    album_detail = {
                        'id': album_id,
                        'title': album_info.get('title', ''),
                        'author': self._extract_author_name(album_info),
                        'platform': '喜马拉雅',
                        'cover': self._extract_cover_url(album_info),
                        'plays': album_info.get('play_count', 0),
                        'episodes': album_info.get('track_count', 0),
                        'description': album_info.get('intro', ''),
                        'category': album_info.get('category_title', ''),
                        'tags': album_info.get('tags', []),
                        'created_at': album_info.get('created_at', ''),
                        'updated_at': album_info.get('updated_at', ''),
                        'is_paid': album_info.get('is_paid', False),
                        'price': album_info.get('price', 0),
                        'is_finished': album_info.get('is_finished', False)
                    }
                    
                    print(f"📖 获取专辑详情成功: {album_detail['title']}")
                    return album_detail
                else:
                    print(f"❌ 获取专辑详情失败: {data.get('msg', '未知错误')}")
                    return None
            else:
                print(f"❌ 获取专辑详情失败: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ 获取专辑详情异常: {e}")
            return None
    
    def _fetch_chapters_multi_api(self, album_id: str, page: int, page_size: int) -> Dict[str, List[Dict]]:
        """顺序调用多个章节 API（避免在 QThread 内用线程池触发 interpreter shutdown）"""
        api_results = {}
        # old_api 多数专辑更稳定，放前面以减少无效请求
        fetchers = [
            ('old_api', self._fetch_chapters_old_api),
            ('new_api', self._fetch_chapters_new_api),
            ('web_api', self._fetch_chapters_web_api),
        ]
        for api_name, fetcher in fetchers:
            try:
                result = fetcher(album_id, page, page_size)
                if result:
                    api_results[api_name] = result
                    print(f"   📊 {api_name}: 获取到 {len(result)} 个章节")
                else:
                    print(f"   ⚠️ {api_name}: 返回空结果")
            except Exception as e:
                print(f"   ❌ {api_name}: 获取失败 - {e}")
        return api_results

    def _pick_best_chapter_list(self, api_results: Dict[str, List[Dict]]) -> List[Dict]:
        if not api_results:
            return []
        # 合并各 API 的章节并按 track_id 去重取并集（而非只取单一最多的一份）。
        # 原因：new_api 偶发被风控（WFP 校验失败）时只剩 old_api，会漏掉只在 new_api 的章节
        # （如专辑最新几集），导致订阅永远补不全。取并集让多个 API 互补，最大化完整性。
        apis_by_size = sorted(api_results.keys(), key=lambda k: len(api_results[k]), reverse=True)
        merged = []
        seen = set()
        for api_name in apis_by_size:
            for ch in api_results[api_name]:
                key = str(
                    ch.get('id') or ch.get('track_id') or ch.get('trackId')
                    or ch.get('order_num') or ch.get('title') or ''
                ).strip()
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                merged.append(ch)
        base_api = apis_by_size[0]
        base_len = len(api_results[base_api])
        if len(merged) > base_len:
            print(f"✅ 合并章节并集：基准 {base_api}={base_len} → 多 API 互补去重后 {len(merged)} 个")
        else:
            print(f"✅ 选择 {base_api} 的结果（{len(merged)} 个章节）")
        for api_name, chapters in api_results.items():
            print(f"   📊 {api_name}: {len(chapters)} 个章节")
        return merged

    def get_album_chapters(self, album_id: str, page: int = 1, page_size: int = 200) -> List[Dict]:
        """获取专辑章节列表 - 多 API 取章节数最多的一份
        
        分页加载（page_size<=1000）使用顺序请求，不在后台 QThread 里再开线程池。
        """
        try:
            print(f"📚 获取章节: {album_id}, 页码: {page}, 每页: {page_size}")

            if page_size > 1000:
                return self._fetch_chapters_concurrent(album_id, page_size)

            api_results = self._fetch_chapters_multi_api(album_id, page, page_size)
            best = self._pick_best_chapter_list(api_results)
            if best:
                return best

            print(f"❌ 所有章节API都失败了")
            print(f"💡 提示：喜马拉雅章节API可能需要有效的Cookie或被风险检测拦截")
            return []

        except RuntimeError as e:
            if 'shutdown' in str(e).lower():
                print(f"⚠️ 章节加载已中断（解释器关闭）: {e}")
                return []
            raise
        except Exception as e:
            print(f"❌ 获取章节列表异常: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _fetch_chapters_new_api(self, book_id: str, page: int = 1, page_size: int = 200) -> List[Dict]:
        """新API获取章节 (mobile/v1/album/track)"""
        try:
            current_time = get_timestamp_ms_str()
            new_api_url = f"https://mobile.ximalaya.com/mobile/v1/album/track/ts-{current_time}"
            
            params = {
                'albumId': book_id,
                'device': 'android',
                'pageId': page,
                'pageSize': page_size,
                'isAsc': True
            }
            
            headers = {
                'User-Agent': 'ting_7.3.58_c5(Xiaomi;Redmi K30 Pro;Android 11;1080x2400)',
                'Host': 'mobile.ximalaya.com',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }
            
            if self.cookie_string:
                headers['Cookie'] = self.cookie_string
            
            response = self.session.get(new_api_url, params=params, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('ret') == 0:
                    tracks = data.get('data', {}).get('list', [])
                    
                    if tracks:
                        chapter_list = []
                        for item in tracks:
                            chapter = {
                                'id': str(item.get('trackId', '')),
                                'title': item.get('title', ''),
                                'duration': self._format_duration(item.get('duration', 0)),
                                'size': self._format_size(item.get('playSize64', 0)),
                                'plays': item.get('playCount', 0),
                                'url': '',
                                'album': book_id,
                                'order_num': item.get('orderNum', 0),
                                'is_paid': item.get('isPaid', False),
                                'is_vip': item.get('isVip') or item.get('vip') or item.get('vipOnly'),
                                'is_platinum': item.get('isPlatinum') or item.get('platinumOnly'),
                                'is_authorized': item.get('isAuthorized'),
                                'is_free': item.get('isFree'),
                                'price': item.get('price', 0),
                                'created_at': item.get('createdAt', ''),
                                'is_finished': item.get('isFinished', False)
                            }
                            chapter_list.append(chapter)
                        return chapter_list
            
            return []
            
        except Exception as e:
            print(f"⚠️ 新API异常: {e}")
            return []
    
    def _fetch_chapters_old_api(self, book_id: str, page: int = 1, page_size: int = 200) -> List[Dict]:
        """旧API获取章节 (fmobile-album/album/track)"""
        try:
            current_time = get_timestamp_ms_str()
            url = (f"http://mobile.ximalaya.com/fmobile-album/album/track/ts-{current_time}"
                   f"?ac=4G&albumId={book_id}&device=android&isAsc=true"
                   f"&isQueryInvitationBrand=true&isVideoAsc=true&pageSize={page_size}"
                   f"&source=3&supportWebp=true&pageId={page}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.ximalaya.com/'
            }
            
            if self.cookie_string:
                headers['Cookie'] = self.cookie_string
            
            response = self.session.get(url, headers=headers, timeout=20)
            
            if response.status_code == 200:
                data = response.json()
                
                data_list = []
                if isinstance(data, dict) and 'data' in data:
                    data_obj = data['data']
                    if isinstance(data_obj, dict) and 'list' in data_obj:
                        list_items = data_obj['list']
                        if isinstance(list_items, list):
                            data_list = [item for item in list_items if isinstance(item, dict)]
                
                if data_list:
                    chapter_list = []
                    for item in data_list:
                        chapter = {
                            'id': str(item.get('trackId', '')),
                            'title': item.get('title', ''),
                            'duration': self._format_duration(item.get('duration', 0)),
                            'size': self._format_size(item.get('play_size_64', 0)),
                            'plays': item.get('play_count', 0),
                            'url': '',
                            'album': book_id,
                            'order_num': item.get('order_num', 0),
                            'is_paid': item.get('is_paid', False),
                            'price': item.get('price', 0),
                            'created_at': item.get('created_at', ''),
                            'is_finished': item.get('is_finished', False)
                        }
                        chapter_list.append(chapter)
                    return chapter_list
            
            return []
            
        except Exception as e:
            print(f"⚠️ 旧API异常: {e}")
            return []
    
    def _fetch_chapters_web_api(self, book_id: str, page: int = 1, page_size: int = 200) -> List[Dict]:
        """Web API获取章节 (revision/album/v1/getTracksList)"""
        try:
            url = f"{self.api_url}/revision/album/v1/getTracksList"
            params = {
                'albumId': book_id,
                'pageNum': page,
                'pageSize': page_size
            }
            
            response = self.session.get(url, params=params, timeout=20)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('ret') == 0:
                    tracks_data = data.get('data', {})
                    tracks = tracks_data.get('tracks', [])
                    
                    if tracks:
                        chapters = []
                        for track in tracks:
                            chapter = {
                                'id': str(track.get('trackId', '')),
                                'title': track.get('title', ''),
                                'duration': self._format_duration(track.get('duration', 0)),
                                'size': self._format_size(track.get('play_size_64', 0)),
                                'plays': track.get('play_count', 0),
                                'url': '',
                                'album': book_id,
                                'order_num': track.get('order_num', 0),
                                'is_paid': track.get('is_paid', False),
                                'price': track.get('price', 0),
                                'created_at': track.get('created_at', ''),
                                'is_finished': track.get('is_finished', False)
                            }
                            chapters.append(chapter)
                        return chapters
            
            return []
            
        except Exception as e:
            print(f"⚠️ Web API异常: {e}")
            return []
    
    def _fetch_chapters_concurrent(self, album_id: str, page_size: int = 2000) -> List[Dict]:
        """并发获取大量章节 - 用于专辑章节数很多的情况"""
        try:
            print(f"🔄 并发获取大量章节: {album_id}, 每页: {page_size}")
            
            # 首先获取专辑信息确定总章节数
            album_detail = self.get_album_detail(album_id)
            total_episodes = album_detail.get('episodes', 0) if album_detail else 0
            
            if total_episodes <= 0:
                print("⚠️ 无法获取专辑总章节数")
                return []
            
            print(f"📊 专辑总章节数: {total_episodes}")
            
            # 计算需要多少页
            total_pages = (total_episodes + page_size - 1) // page_size
            print(f"📊 需要获取 {total_pages} 页章节")
            
            # 使用线程池并发获取所有页面
            import concurrent.futures
            import time
            
            chapters = []
            max_workers = min(20, total_pages)  # 限制最大并发数
            
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_page = {}
                    for page in range(1, total_pages + 1):
                        future = executor.submit(self._fetch_chapters_optimized, album_id, page, page_size)
                        future_to_page[future] = page

                    for future in concurrent.futures.as_completed(future_to_page, timeout=120):
                        try:
                            page_chapters = future.result(timeout=30)
                            if page_chapters:
                                chapters.extend(page_chapters)
                                print(f"📚 获取到 {len(page_chapters)} 个章节 (总计: {len(chapters)})")
                        except Exception as e:
                            page = future_to_page[future]
                            print(f"❌ 获取第 {page} 页章节失败: {e}")
            except RuntimeError as e:
                if 'shutdown' in str(e).lower():
                    print(f"⚠️ 并发章节加载中断: {e}")
                    return chapters
                raise
            
            print(f"✅ 并发获取完成，总共获取到 {len(chapters)} 个章节")
            return chapters
            
        except Exception as e:
            print(f"❌ 并发获取章节失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _fetch_chapters_optimized(self, book_id: str, page: int = 1, page_size: int = 200) -> List[Dict]:
        """优化的移动端API章节获取 - 新API优先，失败自动降级"""
        # 🚀 优先尝试新API (mobile/v1/album/track)
        try:
            print(f"🚀 优先使用新API获取章节: {book_id}, 页码: {page}")
            
            current_time = get_timestamp_ms_str()
            new_api_url = f"https://mobile.ximalaya.com/mobile/v1/album/track/ts-{current_time}"
            
            params = {
                'albumId': book_id,
                'device': 'android',
                'pageId': page,
                'pageSize': page_size,
                'isAsc': True  # 正序
            }
            
            headers = {
                'User-Agent': 'ting_7.3.58_c5(Xiaomi;Redmi K30 Pro;Android 11;1080x2400)',
                'Host': 'mobile.ximalaya.com',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }
            
            # 添加Cookie
            if self.cookie_string:
                headers['Cookie'] = self.cookie_string
            
            print(f"🔍 新API请求: {new_api_url}")
            response = self.session.get(new_api_url, params=params, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('ret') == 0:
                    tracks = data.get('data', {}).get('list', [])
                    
                    if tracks:
                        print(f"✅ 新API成功获取 {len(tracks)} 个章节")
                        
                        # 转换为标准格式
                        chapter_list = []
                        for item in tracks:
                            chapter = {
                                'id': str(item.get('trackId', '')),
                                'title': item.get('title', ''),
                                'duration': self._format_duration(item.get('duration', 0)),
                                'size': self._format_size(item.get('playSize64', 0)),
                                'plays': item.get('playCount', 0),
                                'url': '',
                                'album': book_id,
                                'order_num': item.get('orderNum', 0),
                                'is_paid': item.get('isPaid', False),
                                'price': item.get('price', 0),
                                'created_at': item.get('createdAt', ''),
                                'is_finished': item.get('isFinished', False)
                            }
                            chapter_list.append(chapter)
                        
                        return chapter_list
                    else:
                        print(f"⚠️ 新API返回空列表，降级到旧API")
                else:
                    print(f"⚠️ 新API返回错误 ret={data.get('ret')}，降级到旧API")
            else:
                print(f"⚠️ 新API HTTP {response.status_code}，降级到旧API")
                
        except Exception as e:
            print(f"⚠️ 新API异常: {e}，降级到旧API")
        
        # 📱 降级到旧API (fmobile-album/album/track)
        try:
            print(f"📱 使用旧API获取章节: {book_id}, 页码: {page}")
            
            chapter_list = []
            current_time = get_timestamp_ms_str()
            
            # 构建URL，使用更大的pageSize提高效率
            url = (f"http://mobile.ximalaya.com/fmobile-album/album/track/ts-{current_time}"
                   f"?ac=4G&albumId={book_id}&device=android&isAsc=true"
                   f"&isQueryInvitationBrand=true&isVideoAsc=true&pageSize={page_size}"
                   f"&source=3&supportWebp=true&pageId={page}")
            
            print(f"🔍 旧API请求: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.ximalaya.com/'
            }
            
            # 添加Cookie
            if self.cookie_string:
                headers['Cookie'] = self.cookie_string
            
            # 设置更合理的超时时间
            response = self.session.get(url, headers=headers, timeout=20)
            
            if response.status_code != 200:
                print(f"❌ 旧API请求失败: {response.status_code}")
                return []
            
            data = response.json()
            
            # 解析数据
            data_list = []
            if isinstance(data, dict) and 'data' in data:
                data_obj = data['data']
                if isinstance(data_obj, dict) and 'list' in data_obj:
                    list_items = data_obj['list']
                    if isinstance(list_items, list):
                        data_list = [item for item in list_items if isinstance(item, dict)]
            
            if not data_list:
                print(f"🏁 旧API没有更多章节")
                return []
            
            # 转换为标准格式
            for item in data_list:
                chapter = {
                    'id': str(item.get('trackId', '')),
                    'title': item.get('title', ''),
                    'duration': self._format_duration(item.get('duration', 0)),
                    'size': self._format_size(item.get('play_size_64', 0)),
                    'plays': item.get('play_count', 0),
                    'url': '',
                    'album': book_id,
                    'order_num': item.get('order_num', 0),
                    'is_paid': item.get('is_paid', False),
                    'is_vip': item.get('is_vip') or item.get('isVip') or item.get('vip') or item.get('vipOnly'),
                    'is_platinum': item.get('is_platinum') or item.get('isPlatinum') or item.get('platinumOnly'),
                    'is_authorized': item.get('is_authorized') if 'is_authorized' in item else item.get('isAuthorized'),
                    'is_free': item.get('is_free') if 'is_free' in item else item.get('isFree'),
                    'price': item.get('price', 0),
                    'created_at': item.get('created_at', ''),
                    'is_finished': item.get('is_finished', False)
                }
                chapter_list.append(chapter)
            
            print(f"   📚 旧API第{page}页获取到 {len(data_list)} 个章节")
            return chapter_list
            
        except Exception as e:
            print(f"❌ 旧API获取章节失败: {e}")
            return []
    
    def get_audio_urls(self, track_id: str) -> Dict[str, str]:
        """获取音频URL（多种音质）"""
        try:
            print(f"🎵 获取音频URL: {track_id}")
            
            # 移动端：优先 ios/www2（与手机 App 一致），android 单独请求常误报 924
            mobile_data = self._fetch_mobile_track_base_info(track_id)
            if mobile_data:
                audio_urls = self._build_audio_urls_from_mobile_data(mobile_data)
                if audio_urls:
                    print(f"🎵 从移动端API获取到 {len(audio_urls)} 种音质直链")
                    for quality, info in sorted(
                        audio_urls.items(),
                        key=lambda x: x[1].get('quality_level', 0) if isinstance(x[1], dict) else 0,
                        reverse=True,
                    ):
                        if isinstance(info, dict):
                            print(f"   🎧 {quality}: {info.get('size_mb', 0):.2f}MB")
                    return audio_urls

            # 网页端：AES 解密 playUrlList 为 CDN 直链（MP3/M4A）
            print("⚠️ 移动端直链不可用，尝试网页端 AES 解密")
            timestamp = get_timestamp_ms_str()
            web_api_url = f"https://www.ximalaya.com/mobile-playpage/track/v3/baseInfo/{timestamp}?device=web&trackId={track_id}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                'Referer': 'https://www.ximalaya.com/',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }
            
            # 关键修复：如果有Cookie，添加到请求头中
            if self.cookie_string:
                headers['Cookie'] = self.cookie_string
                print(f"🍪 已添加Cookie到网页端API请求")
            
            response = self.session.get(web_api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 0 and data.get('trackInfo'):
                    track_info = data['trackInfo']
                    play_url_list = track_info.get('playUrlList', [])
                    
                    if play_url_list:
                        audio_urls = self._build_audio_urls_from_web_playlist(track_id, play_url_list)
                        if audio_urls:
                            print(f"🎵 从网页端 AES 解密得到 {len(audio_urls)} 种音质直链")
                            for quality, info in sorted(
                                audio_urls.items(),
                                key=lambda x: x[1].get('quality_level', 0) if isinstance(x[1], dict) else 0,
                                reverse=True,
                            ):
                                if isinstance(info, dict):
                                    print(f"   🎧 {quality}: {info.get('size_mb', 0):.2f}MB [CDN直链]")
                            return audio_urls
                        print(f"⚠️ 网页端密文 AES 解密均未得到有效直链")
            
            # 如果API都失败，使用带正确level参数的重定向URL作为备选
            print("⚠️ API未返回直接URL，生成重定向URL作为备选")
            audio_urls = {}
            
            # 动态确定最高音质的level参数
            # 检查API返回的playHqSize来判断是否有96K音质
            hq_size_mb = 0
            if hasattr(self, '_last_api_response') and self._last_api_response:
                hq_size = self._last_api_response.get('playHqSize', 0)
                if hq_size > 0:
                    hq_size_mb = hq_size / 1024 / 1024
            
            # 根据实际音质大小确定level参数
            if hq_size_mb > 6:
                # 有96K音质
                high_quality_level = 96
                high_quality_size = hq_size_mb
                print(f"✅ 检测到96K音质: {high_quality_size:.2f}MB")
            else:
                # 最高只有64K音质
                high_quality_level = 1
                high_quality_size = 3.0
                print(f"⚠️ 最高只有64K音质: {hq_size_mb:.2f}MB")
            
            # 根据实际音质大小配置
            quality_configs = [
                {'quality': 'M4A_24k', 'level': 0, 'format': 'M4A', 'size_mb': 1.5},
                {'quality': 'MP3_32', 'level': 0, 'format': 'MP3', 'size_mb': 2.0},
                {'quality': 'M4A_64k', 'level': 1, 'format': 'M4A', 'size_mb': 3.0},
                {'quality': 'MP3_64', 'level': 1, 'format': 'MP3', 'size_mb': 4.0},
                {'quality': 'AAC_164', 'level': 1, 'format': 'AAC', 'size_mb': 3.0},
                {'quality': 'M4A_96k', 'level': high_quality_level, 'format': 'M4A', 'size_mb': high_quality_size},
                {'quality': 'AAC_224', 'level': high_quality_level, 'format': 'AAC', 'size_mb': high_quality_size},
                {'quality': 'AAC_296', 'level': high_quality_level, 'format': 'AAC', 'size_mb': high_quality_size},
                {'quality': 'HQ', 'level': high_quality_level, 'format': 'M4A', 'size_mb': high_quality_size},
            ]
            
            for config in quality_configs:
                quality = config['quality']
                level = config['level']
                format_type = config['format']
                size_mb = config['size_mb']
                
                # 构建移动端重定向URL
                redirect_url = f"http://mobile.ximalaya.com/mobile/redirect/free/play/{track_id}/{level}"
                
                audio_urls[quality] = {
                    'url': redirect_url,
                    'size_mb': size_mb,
                    'quality_level': level if level != 96 else 5,  # level=96映射为质量级别5
                    'type': format_type,
                    'port': 'mobile_redirect'
                }
            
            print(f"🎵 生成 {len(audio_urls)} 种音质的重定向URL（包含正确的level参数）")
            for quality, info in audio_urls.items():
                if isinstance(info, dict):
                    print(f"   🎧 {quality}: level={info.get('url', '').split('/')[-1]} ({info.get('size_mb', 0):.2f}MB预估)")
            return audio_urls
                
        except Exception as e:
            print(f"❌ 获取音频URL异常: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def decrypt_audio_url(self, encrypted_url: str) -> str:
        """解密网页端音频密文：优先 AES-ECB，失败再尝试 XOR"""
        if not encrypted_url or encrypted_url.startswith('http'):
            return encrypted_url or ''
        aes_url = self._decrypt_audio_url_aes(encrypted_url)
        if aes_url:
            return aes_url
        xor_url = self._decrypt_audio_url_xor(encrypted_url)
        return xor_url if xor_url else encrypted_url
    
    def download_audio(self, url: str, save_path: str, quality: str | None = None, progress_callback=None) -> bool:
        """下载音频文件 - 增强版，支持多API备选下载"""
        try:
            # 导入增强版下载管理器
            from .ximalaya_download_manager import XimalayaDownloadManager
            
            # 提取章节ID（支持多种URL格式）
            track_id = None
            
            # 1. 如果是纯数字，直接作为track_id
            if url.isdigit():
                track_id = url
            # 2. 如果是喜马拉雅URL，尝试提取track_id
            elif url.startswith('http') and 'ximalaya.com' in url:
                import re
                # 从URL中提取trackId参数（如 ?trackId=123456）
                match = re.search(r'trackId=(\d+)', url)
                if match:
                    track_id = match.group(1)
                else:
                    # 从重定向URL提取ID（如 /play/123456/1 或 /free/play/123456/1）
                    match = re.search(r'/play/(\d+)', url)
                    if match:
                        track_id = match.group(1)
            
            # 如果成功提取到track_id，使用增强版下载管理器
            if track_id:
                print(f"🔄 使用增强版下载管理器下载 track_id: {track_id}")
                
                # 创建下载管理器实例，传入Cookie
                downloader = XimalayaDownloadManager(cookie_string=self.cookie_string)
                
                # 如果没有提供音质参数，从保存路径推断用户选择的音质
                if quality is None:
                    import os
                    file_extension = os.path.splitext(save_path)[1].lower()
                    if file_extension == '.m4a':
                        # 对于M4A文件，使用默认M4A音质
                        quality = "M4A_96K"  # 默认使用最高质量M4A音质
                    else:
                        # 对于MP3文件，使用默认MP3音质
                        quality = "MP3_64K"
                
                # 执行下载（添加必要的参数）
                success = downloader.download_audio_by_quality(track_id, quality, save_path, "", "", progress_callback=progress_callback)
                self.last_download_error = getattr(downloader, "last_error", "")
                self.last_download_error_type = getattr(downloader, "last_error_type", "")
                self.last_download_source = getattr(downloader, "last_download_source", "")
                self.last_download_size = getattr(downloader, "last_download_size", 0)
                self.last_download_expected_size = getattr(downloader, "last_download_expected_size", 0)
                self.last_download_quality_label = getattr(downloader, "last_download_quality_label", "")
                return success
            
            # 否则使用原有下载逻辑
            print(f"📥 使用原有下载逻辑: {url}")
            
            # 使用session的默认请求头，确保Cookie和User-Agent一致
            print(f"🍪 下载请求使用session默认请求头")
            
            response = self.session.get(url, stream=True, timeout=30)
            
            print(f"📡 下载响应状态: {response.status_code}")
            print(f"📊 Content-Length: {response.headers.get('Content-Length', 'N/A')}")
            
            if response.status_code == 200:
                # 确保保存目录存在
                import os
                from pathlib import Path
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                
                content_length = int(response.headers.get('Content-Length') or 0)
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=512000):
                        if chunk:
                            f.write(chunk)
                            if progress_callback:
                                progress_callback(f.tell(), content_length)
                
                # 检查文件大小
                file_size = os.path.getsize(save_path)
                if file_size > 1024 * 10:  # 大于10KB认为下载成功
                    size_mb = file_size / (1024 * 1024)
                    print(f"✅ 下载成功: {save_path} ({size_mb:.2f}MB)")
                    return True
                else:
                    print(f"❌ 文件大小异常: {file_size} 字节")
                    os.remove(save_path)
                    return False
            else:
                print(f"❌ 下载失败: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 下载异常: {e}")
            return False
    
    def _format_duration(self, seconds: int) -> str:
        """格式化时长"""
        if seconds <= 0:
            return "00:00"
        
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
    
    def _format_size(self, bytes_size: int) -> str:
        """格式化文件大小"""
        if bytes_size <= 0:
            return "0MB"
        
        mb = bytes_size / (1024 * 1024)
        if mb < 1:
            return f"{bytes_size // 1024}KB"
        else:
            return f"{mb:.1f}MB"
    
    def get_available_qualities(self, track_id: str) -> List[Dict]:
        """获取可用音质列表"""
        audio_urls = self.get_audio_urls(track_id)
        qualities = []
        
        for quality, info in audio_urls.items():
            # 确保info是字典类型
            if isinstance(info, dict):
                qualities.append({
                    'quality': quality,
                    'size_mb': info.get('size_mb', 0),
                    'type': info.get('type', ''),
                    'quality_level': info.get('quality_level', 0),
                    'url': info.get('url', '')
                })
        
        # 按音质排序
        quality_order = ['96k', '64k', '48k', '32k', '24k']
        qualities.sort(key=lambda x: quality_order.index(x['quality'].split('_')[1]) if '_' in x['quality'] and x['quality'].split('_')[1] in quality_order else 999)
        
        return qualities
    
    def map_ui_quality_to_actual(self, ui_quality: str) -> str:
        """将UI音质选项映射到实际音质标识符"""
        return self.ui_quality_mapping.get(ui_quality, "M4A_48k")  # 默认使用48k M4A音质
    
    def get_audio_url_by_quality(self, track_id: str, quality: str) -> str:
        """Return the highest available URL for the requested UI quality."""
        audio_urls = self.get_audio_urls(track_id)

        if not audio_urls:
            print("WARN: no audio qualities returned")
            return ""

        print(f"Audio quality requested: {quality}")
        print(f"Available qualities: {list(audio_urls.keys())}")

        def _score(item):
            _, info = item
            if not isinstance(info, dict):
                return (-1, -1.0)
            return (
                int(info.get('quality_level', 0) or 0),
                float(info.get('size_mb', 0) or 0),
            )

        def _best_from(keys):
            matches = []
            for key in keys:
                info = audio_urls.get(key)
                if isinstance(info, dict) and info.get('url'):
                    matches.append((key, info))
            if not matches:
                return None, None
            return max(matches, key=_score)

        if quality in self.ui_quality_mapping:
            best_quality, best_info = _best_from(self.ui_quality_mapping[quality])
            if best_info:
                print(
                    f"Selected best candidate: {best_quality} "
                    f"(level={best_info.get('quality_level', 'N/A')}, "
                    f"size={float(best_info.get('size_mb', 0) or 0):.2f}MB)"
                )
                return str(best_info.get('url', ''))

        if quality in audio_urls:
            info = audio_urls[quality]
            if isinstance(info, dict) and info.get('url'):
                print(
                    f"Selected exact quality: {quality} "
                    f"(level={info.get('quality_level', 'N/A')}, "
                    f"size={float(info.get('size_mb', 0) or 0):.2f}MB)"
                )
                return str(info.get('url', ''))

        fuzzy_matches = [
            (available_quality, info)
            for available_quality, info in audio_urls.items()
            if isinstance(info, dict)
            and info.get('url')
            and (quality.lower() in available_quality.lower() or available_quality.lower() in quality.lower())
        ]
        if fuzzy_matches:
            best_quality, best_info = max(fuzzy_matches, key=_score)
            print(
                f"Selected best fuzzy quality: {best_quality} "
                f"(level={best_info.get('quality_level', 'N/A')}, "
                f"size={float(best_info.get('size_mb', 0) or 0):.2f}MB)"
            )
            return str(best_info.get('url', ''))

        all_valid = [
            (available_quality, info)
            for available_quality, info in audio_urls.items()
            if isinstance(info, dict) and info.get('url')
        ]
        if all_valid:
            best_quality, best_info = max(all_valid, key=_score)
            print(
                f"Selected highest returned quality: {best_quality} "
                f"(level={best_info.get('quality_level', 'N/A')}, "
                f"size={float(best_info.get('size_mb', 0) or 0):.2f}MB)"
            )
            return str(best_info.get('url', ''))

        return ""
    def _search_with_cookie(self, keyword: str, page: int = 1, page_size: int = 20) -> List[Dict]:
        """使用Cookie进行搜索"""
        try:
            print(f"🔍 使用Cookie搜索: {keyword}")
            
            # 主要搜索配置 (只保留Web端和移动端)
            search_configs = [
                # Web端搜索
                {
                    'url': f"{self.base_url}/revision/search/main",
                    'params': {
                        'core': 'album',
                        'kw': keyword,
                        'page': page,
                        'spellchecker': 'true',
                        'device': 'web',
                        'fq': '',
                        'rows': page_size
                    },
                    'headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                        'Referer': 'https://www.ximalaya.com/',
                        'Sec-Fetch-Dest': 'empty',
                        'Sec-Fetch-Mode': 'cors',
                        'Sec-Fetch-Site': 'same-origin'
                    }
                },
                # 移动端搜索
                {
                    'url': f"{self.mobile_url}/revision/search",
                    'params': {
                        'core': 'album',
                        'spellchecker': 'true',
                        'rows': page_size,
                        'condition': 'relation',
                        'device': 'iPhone',
                        'fq': '',
                        'paidFilter': 'false',
                        'kw': keyword,
                        'page': page
                    },
                    'headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'zh-CN,zh;q=0.9',
                        'Referer': 'https://m.ximalaya.com/',
                        'Sec-Fetch-Dest': 'empty',
                        'Sec-Fetch-Mode': 'cors',
                        'Sec-Fetch-Site': 'same-origin'
                    }
                }
            ]
            
            # 添加Cookie到请求头
            for config in search_configs:
                if self.cookie_string:
                    config['headers']['Cookie'] = self.cookie_string
                
                try:
                    print(f"🔍 尝试搜索: {config['url']}")
                    print(f"📋 参数: {config['params']}")
                    
                    response = self.session.get(
                        config['url'], 
                        params=config['params'], 
                        headers=config['headers'], 
                        timeout=15
                    )
                    
                    print(f"📊 响应状态: {response.status_code}")
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            print(f"📋 响应数据键: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                            
                            # 检查是否被风险检测拦截
                            if isinstance(data, dict) and 'data' in data:
                                data_content = data['data']
                                if isinstance(data_content, dict) and 'reason' in data_content:
                                    reason = data_content.get('reason', '')
                                    if 'risk' in reason.lower():
                                        print(f"⚠️ 被风险检测拦截: {reason}")
                                        continue
                            
                            # 尝试解析搜索结果
                            albums = self._parse_search_results(data)
                            if albums:
                                print(f"✅ Cookie搜索成功: 找到 {len(albums)} 个专辑")
                                return albums
                                
                        except Exception as json_error:
                            print(f"❌ JSON解析错误: {json_error}")
                            print(f"📄 响应内容: {response.text[:500]}...")
                            continue
                    else:
                        print(f"❌ 请求失败: HTTP {response.status_code}")
                        continue
                        
                except Exception as request_error:
                    print(f"❌ 请求错误: {request_error}")
                    continue
            
            print("❌ 所有Cookie搜索尝试都失败了")
            return []
            
        except Exception as e:
            print(f"❌ Cookie搜索异常: {e}")
            return []
    
    def _parse_search_results(self, data: dict) -> List[Dict]:
        """解析搜索结果"""
        albums = []
        
        try:
            print(f"📋 解析搜索响应: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
            # 检查返回码
            if isinstance(data, dict):
                ret_code = data.get('ret', 0)
                msg = data.get('msg', '')
                print(f"📋 返回码: {ret_code}, 消息: {msg}")
                
                # 某些情况下ret=200也是成功的
                if ret_code != 0 and ret_code != 200:
                    print(f"❌ API返回错误: {msg}")
                    # 但某些错误消息可能是正常的，继续处理
            
            # 尝试多种数据结构
            results = []
            
            if isinstance(data, dict):
                # 方式1: data.data.result.response.docs (移动端)
                if 'data' in data and isinstance(data['data'], dict):
                    data_content = data['data']
                    if 'result' in data_content and isinstance(data_content['result'], dict):
                        result_content = data_content['result']
                        if 'response' in result_content and isinstance(result_content['response'], dict):
                            response_content = result_content['response']
                            if 'docs' in response_content and isinstance(response_content['docs'], list):
                                results = response_content['docs']
                                print(f"📱 移动端数据结构，找到 {len(results)} 个结果")
                
                # 方式2: data.data.result (Web端)
                if not results and 'data' in data and isinstance(data['data'], dict):
                    data_content = data['data']
                    if 'result' in data_content and isinstance(data_content['result'], list):
                        results = data_content['result']
                        print(f"🖥️ Web端数据结构，找到 {len(results)} 个结果")
                
                # 方式3: data.result (直接在data中)
                if not results and 'result' in data and isinstance(data['result'], list):
                    results = data['result']
                    print(f"📄 直接数据结构，找到 {len(results)} 个结果")
                
                # 方式4: 查找任何包含结果的字段
                if not results:
                    for key, value in data.items():
                        if isinstance(value, list) and len(value) > 0:
                            if isinstance(value[0], dict) and 'title' in value[0]:
                                results = value
                                print(f"🔍 通用搜索数据结构，找到 {len(results)} 个结果")
                                break
            
            print(f"📝 解析到 {len(results)} 个结果项")
            
            for item in results:
                if isinstance(item, dict):
                    # 检查是否是专辑类型
                    model_type = item.get('model_type', '')
                    if model_type == 'album' or 'title' in item:
                        # 处理封面URL
                        cover_url = self._extract_cover_url(item)
                        
                        album = {
                            'id': str(item.get('id', item.get('album_id', ''))),
                            'title': item.get('title', ''),
                            'author': self._extract_author_name(item),
                            'platform': '喜马拉雅',
                            'cover': cover_url,
                            'plays': item.get('play_count', item.get('plays', 0)),
                            'episodes': item.get('track_count', item.get('episodes', 0)),
                            'description': item.get('intro', item.get('description', '')),
                            'category': item.get('category_title', item.get('category', '')),
                            'tags': item.get('tags', []),
                            'created_at': item.get('created_at', ''),
                            'updated_at': item.get('updated_at', ''),
                            'status': '已完结' if item.get('is_finished', False) else '连载中'
                        }
                        albums.append(album)
                        print(f"   ✅ 解析专辑: {album['title']}")
            
        except Exception as e:
            print(f"❌ 解析搜索结果失败: {e}")
            import traceback
            traceback.print_exc()
        
        return albums

    
    def _extract_user_info_from_cookie(self, cookie_string: str):
        """从Cookie中提取用户信息"""
        try:
            # 提取用户ID
            import re
            user_id_match = re.search(r'_token=(\d+)&', cookie_string)
            if user_id_match:
                self.user_id = user_id_match.group(1)
                print(f"👤 提取到用户ID: {self.user_id}")
            
            # 提取token
            token_match = re.search(r'_token=\d+&([A-Z0-9]+)_', cookie_string)
            if token_match:
                self.user_token = token_match.group(1)
                print(f"🔐 提取到用户Token: {self.user_token[:20]}...")
                
        except Exception as e:
            print(f"⚠️ 提取用户信息失败: {e}")
            
    def _add_user_info_to_params(self, params: dict) -> dict:
        """向参数中添加用户信息"""
        if self.user_id:
            params['u'] = self.user_id
        return params

    def get_account_info(self, cookie_string: str = None) -> dict:
        """用 Cookie 调喜马拉雅官方接口获取账号昵称与 VIP 状态。

        使用 web 端 getCurrentUser 接口（与浏览器登录态完全一致，喜马拉雅官方公开接口），
        无需逆向任何第三方软件。返回:
        {logged_in, nickname, uid, is_vip, vip_label}
        """
        cookie = cookie_string if cookie_string is not None else self.cookie_string
        info = {"logged_in": False, "nickname": "", "uid": "", "is_vip": False, "vip_label": "未登录"}
        if not cookie:
            return info
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                'Referer': 'https://www.ximalaya.com/',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Cookie': cookie if isinstance(cookie, str) else str(cookie),
            }
            resp = self.session.get(
                'https://www.ximalaya.com/revision/main/getCurrentUser',
                headers=headers, timeout=10,
            )
            if resp.status_code != 200:
                return info
            data = resp.json()
            d = data.get('data') if isinstance(data, dict) else None
            if not (isinstance(data, dict) and data.get('ret') == 200 and isinstance(d, dict)):
                return info
            info['logged_in'] = True
            info['uid'] = str(d.get('uid') or d.get('userId') or '')
            info['nickname'] = str(d.get('nickname') or d.get('nickName') or d.get('userName') or '').strip()
            # VIP 判定：isVip 为基础会员；并尽量识别白金/超级会员等更高等级
            is_vip = bool(d.get('isVip') or d.get('vip') or d.get('isVipUser'))
            info['is_vip'] = is_vip
            vip_text = " ".join(
                str(d.get(k) or "") for k in
                ('vipResourceType', 'vipType', 'vipGrade', 'vipName', 'memberType', 'gradeName')
            )
            if is_vip:
                if '白金' in vip_text or 'platinum' in vip_text.lower() or str(d.get('vipResourceType') or '') == '2':
                    info['vip_label'] = '白金VIP'
                else:
                    info['vip_label'] = 'VIP会员'
            else:
                info['vip_label'] = '普通用户'
            print(f"👤 喜马拉雅账号: {info['nickname']} ({info['vip_label']})")
        except Exception as e:
            print(f"⚠️ 获取喜马拉雅账号信息失败: {e}")
        return info
