#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .platform_config import config_dir, download_dir


PLATFORM_COOKIE_ALIASES = {
    "喜马拉雅": "xmly",
    "ximalaya": "xmly",
    "xmly": "xmly",
    "懒人听书": "lrts",
    "lrts": "lrts",
    "起点听书": "qidian",
    "起点": "qidian",
    "qidian": "qidian",
    "蜻蜓FM": "qtfm",
    "蜻蜓fm": "qtfm",
    "qtfm": "qtfm",
    "番茄畅听": "fanqie",
    "fanqie": "fanqie",
    "番茄听书": "fanqie_tingshu",
    "fanqie_tingshu": "fanqie_tingshu",
    "七猫听书": "qimao",
    "qimao": "qimao",
    "云听FM": "yuntu",
    "云听fm": "yuntu",
    "yuntu": "yuntu",
    "酷我听书": "kuwo",
    "kuwo": "kuwo",
    "网易云听书": "netease",
    "网易云音乐": "netease",
    "netease": "netease",
    "荔枝FM": "lizhi",
    "荔枝fm": "lizhi",
    "lizhi": "lizhi",
}


def normalize_cookie_platform(platform):
    key = str(platform or "").strip()
    return PLATFORM_COOKIE_ALIASES.get(key, key)


def _prepare_console_output():
    """Avoid UnicodeEncodeError when legacy logs run in a GBK console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_prepare_console_output()


def _fernet_from_secret(secret):
    if not secret:
        return None
    digest = hashlib.sha256(str(secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class CookieManager:
    """本地Cookie和设置管理器。"""
    
    def __init__(self):
        self.config_dir = config_dir()
        self.config_file = self.config_dir / 'cookies.json'
        # 使用用户实际的下载目录，而不是测试目录
        self.download_dir = str(download_dir())
        self.download_dir_custom = False
        self.cookies = {}
        self.theme_settings = {}  # 添加主题设置存储
        self.encryption_enabled = bool(os.getenv("AUDIOFLOW_COOKIE_SECRET"))
        self._fernet = _fernet_from_secret(os.getenv("AUDIOFLOW_COOKIE_SECRET"))
        
        # 旧版本兼容字段：自用版不再请求或保存服务器代持Cookie。
        self.server_cookie_cache = {}
        
        # 旧版本兼容字段：自用版不再请求服务器Cookie。
        self.requesting_cookies = {}
        
        self.load()
        
    def load(self):
        """加载Cookies和设置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    encrypted = data.get("encrypted_cookies")
                    encrypted_server = data.get("encrypted_server_cookie_cache")
                    self.cookies = self._decrypt_mapping(encrypted) if encrypted else data.get('cookies', {})
                    self.server_cookie_cache = self._decrypt_mapping(encrypted_server) if encrypted_server else data.get('server_cookie_cache', {})
                    # 加载下载目录设置
                    saved_download_dir = data.get('download_dir')
                    self.download_dir_custom = bool(data.get('download_dir_custom'))
                    if saved_download_dir and self.download_dir_custom:
                        self.download_dir = saved_download_dir
                    else:
                        # 如果没有保存的下载目录，使用默认目录
                        self.download_dir = str(download_dir())
                    
                    # 加载主题设置
                    self.theme_settings = data.get('theme_settings', {})
                    if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                        print(f"🎨 加载主题设置: {self.theme_settings}")
            except Exception as e:
                print(f"❌ 加载配置文件失败: {e}")
                self.cookies = {}
                self.download_dir = str(download_dir())
                self.theme_settings = {}
        else:
            self.cookies = {}
            self.download_dir = str(download_dir())
            self.theme_settings = {}
            # 创建默认配置文件
            self.save()
            
    def save(self):
        """保存Cookies和设置"""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            now = time.time()
            data = {
                'download_dir': self.download_dir,
                'download_dir_custom': self.download_dir_custom,
                'theme_settings': self.theme_settings,  # 保存主题设置
                'updated_at': now,
            }
            if self._fernet:
                data["cookies"] = {}
                data["server_cookie_cache"] = {}
                data["encrypted_cookies"] = self._encrypt_mapping(self.cookies)
                data["encrypted_server_cookie_cache"] = self._encrypt_mapping(self.server_cookie_cache)
                data["cookie_encrypted"] = True
            else:
                data["cookies"] = self.cookies
                data["server_cookie_cache"] = self.server_cookie_cache
                data["cookie_encrypted"] = False
            tmp_file = self.config_file.with_suffix(self.config_file.suffix + ".tmp")
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_file, self.config_file)
            if os.getenv("AUDIOFLOW_DEBUG_API") == "1":
                print("✅ 配置文件保存成功")
        except Exception as e:
            print(f"❌ 保存配置文件失败: {e}")

    def _encrypt_mapping(self, value):
        if not self._fernet:
            return {}
        raw = json.dumps(value or {}, ensure_ascii=False).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def _decrypt_mapping(self, value):
        if not value:
            return {}
        if not self._fernet:
            print("⚠️ cookies.json 已加密，请设置 AUDIOFLOW_COOKIE_SECRET 后再读取 Cookie")
            return {}
        try:
            raw = self._fernet.decrypt(str(value).encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"⚠️ Cookie 解密失败: {exc}")
            return {}
            
    def get_cookie(self, platform):
        """获取指定平台的Cookie"""
        platform = normalize_cookie_platform(platform)
        return self.cookies.get(platform, '')
        
    def set_cookie(self, platform, cookie):
        """设置指定平台的Cookie"""
        platform = normalize_cookie_platform(platform)
        if cookie:
            # 支持字典和字符串格式
            if isinstance(cookie, dict):
                self.cookies[platform] = cookie
            elif isinstance(cookie, str) and cookie.strip():
                self.cookies[platform] = cookie.strip()
            else:
                print(f"⚠️ 无效的Cookie: {platform}")
                return
            
            # 每次设置Cookie后自动保存
            self.save()
            if isinstance(cookie, dict):
                print(f"🍪 Cookie已保存: {platform} (字典格式，{len(cookie)}个字段)")
            else:
                print(f"🍪 Cookie已保存: {platform} ({len(str(cookie))} 字符)")
        else:
            print(f"⚠️ 无效的Cookie: {platform}")
    
    def delete_cookie(self, platform):
        """删除指定平台的Cookie"""
        platform = normalize_cookie_platform(platform)
        if platform in self.cookies:
            del self.cookies[platform]
            self.save()
            print(f"🗑️ 已删除Cookie: {platform}")
        else:
            print(f"ℹ️ Cookie不存在: {platform}")
    
    def has_valid_cookie(self, platform):
        """检查平台是否有有效的Cookie"""
        platform = normalize_cookie_platform(platform)
        cookie = self.cookies.get(platform, '')
        if isinstance(cookie, dict):
            return bool(cookie)
        return bool(cookie and cookie.strip() != '')
    
    def clear_all_cookies(self):
        """清除所有本地平台Cookie。"""
        self.cookies = {}
        self.server_cookie_cache.clear()
        self.requesting_cookies.clear()
        print("🧹 已清除所有本地Cookie")
        # 清除Cookie后自动保存
        self.save()
    
    def clear_server_cookies_only(self, clear_managers_callback=None):
        """兼容旧调用：清空遗留的服务器Cookie缓存字段。"""
        cleared_count = len(self.server_cookie_cache)
        self.server_cookie_cache.clear()
        self.requesting_cookies.clear()
        
        if clear_managers_callback:
            try:
                clear_managers_callback(self)
            except Exception as e:
                print(f"⚠️ 清空遗留Cookie状态时出错: {e}")
        
        if cleared_count > 0:
            print(f"🧹 已清除遗留服务器Cookie缓存，清空了 {cleared_count} 个平台的缓存")
        else:
            print("🧹 遗留服务器Cookie缓存已清空")
    
    def clear_server_cookie_cache(self, platform=None):
        """兼容旧调用：清空遗留服务器Cookie缓存。"""
        if platform:
            platform = normalize_cookie_platform(platform)
            if platform in self.server_cookie_cache:
                del self.server_cookie_cache[platform]
            if platform in self.requesting_cookies:
                del self.requesting_cookies[platform]
            print(f"🧹 已清空平台遗留服务器Cookie缓存: {platform}")
        else:
            self.server_cookie_cache.clear()
            self.requesting_cookies.clear()
            print("🧹 已清空所有遗留服务器Cookie缓存")
    
    def get_server_cookie_cache(self, platform):
        """读取本地保留的平台代持Cookie缓存；不请求旧授权服务器。"""
        platform = normalize_cookie_platform(platform)
        return self.server_cookie_cache.get(platform, '')
    
    def set_server_cookie_cache(self, platform, cookie):
        """保存本地平台代持Cookie缓存；不请求旧授权服务器。"""
        platform = normalize_cookie_platform(platform)
        if cookie:
            self.server_cookie_cache[platform] = cookie
            self.save()
            print(f"🍪 已保存本地平台代持Cookie缓存: {platform}")
    
    def is_requesting_cookie(self, platform):
        """检查是否正在请求Cookie（与app一致）"""
        platform = normalize_cookie_platform(platform)
        return self.requesting_cookies.get(platform, False)
    
    def set_requesting_cookie(self, platform, requesting):
        """设置正在请求Cookie的状态（与app一致）"""
        platform = normalize_cookie_platform(platform)
        if requesting:
            self.requesting_cookies[platform] = True
        elif platform in self.requesting_cookies:
            del self.requesting_cookies[platform]
        
    def get_download_dir(self):
        """获取下载目录。

        重要：旧版本在目录暂不可写时会回退到 Path.home()/'audioflow'（容器内即 /app/audioflow）
        或临时目录，并改写、持久化 self.download_dir。但这些目录不是 Docker 挂载卷，
        容器重启即丢失数据，且一次偶发的写测试失败（NAS 挂载延迟/瞬时权限）会永久污染配置。

        现行为：
        - 自动修正历史污染：若 self.download_dir 指向 home/temp 下的 audioflow，强制回到
          环境变量指定的挂载下载目录（DOWNLOAD_DIR，默认 /app/downloads）。
        - 自定义目录暂不可写时，本次降级到挂载下载目录（持久卷），不再写非挂载卷，
          且不持久化覆盖用户配置——目录恢复后自动用回。
        """
        import tempfile
        polluted = {
            str(Path.home() / 'audioflow'),
            str(Path(tempfile.gettempdir()) / 'audioflow'),
        }
        if self.download_dir in polluted:
            self.download_dir = str(download_dir())
            self.download_dir_custom = False
        if not self.download_dir_custom:
            self.download_dir = str(download_dir())

        def _usable(p):
            try:
                path = Path(p)
                path.mkdir(parents=True, exist_ok=True)
                test_file = path / ".test_write_permission"
                test_file.touch()
                test_file.unlink()
                return True
            except Exception:
                return False

        target = self.download_dir
        if _usable(target):
            return target
        # 降级到环境变量指定的挂载下载目录（持久卷，避免数据丢失），仅本次生效、不持久化
        env_dir = str(download_dir())
        if env_dir != target and _usable(env_dir):
            print(f"⚠️ 下载目录 {target} 暂不可写，本次降级到挂载下载目录: {env_dir}")
            return env_dir
        # 兜底临时目录（仅本次，不持久化）
        temp_dir = str(Path(tempfile.gettempdir()) / 'audioflow')
        try:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        print(f"⚠️ 下载目录与挂载目录均不可写，本次临时使用: {temp_dir}")
        return temp_dir
        
    def set_download_dir(self, value):
        """设置下载目录"""
        value = str(value or "").strip()
        if value:
            self.download_dir = value
            self.download_dir_custom = True
        else:
            self.download_dir = str(download_dir())
            self.download_dir_custom = False
    
        self.save()

    def get_download_threads(self):
        """获取并发下载线程数，默认4个（NAS 友好）"""
        try:
            threads = self.get_cookie('download_threads')
            if threads:
                thread_count = int(threads)
                return max(1, min(64, thread_count))
            return 4
        except Exception:
            return 4
            
    # 添加主题设置相关方法
    def get_theme_setting(self, key, default=None):
        """获取主题设置"""
        return self.theme_settings.get(key, default)
        
    def set_theme_setting(self, key, value):
        """设置主题设置"""
        self.theme_settings[key] = value
        
    def get_all_theme_settings(self):
        """获取所有主题设置"""
        return self.theme_settings.copy()
        
    def set_all_theme_settings(self, theme_settings):
        """设置所有主题设置"""
        self.theme_settings = theme_settings.copy() if theme_settings else {}
        # 设置主题后自动保存
        self.save()
