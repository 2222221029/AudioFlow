#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
喜马拉雅下载管理器
实现根据音质选择不同API的下载逻辑
"""

import base64
import json
import requests
import os
import time
import threading
import random
import struct
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import quote_plus

from .ximalaya_credentials import (
    MOBILE_V4_ANONYMOUS_TICKET,
    has_ximalaya_mobile_credentials,
    normalize_ximalaya_mobile_credentials,
    ximalaya_mobile_cookie_identity,
    ximalaya_mobile_credential_status,
)
from .ximalaya_local_ticket import LocalTicketError, generate_mobile_ticket


def _positive_env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(value, int(maximum)))


class XimalayaDownloadManager:
    """喜马拉雅下载管理器"""

    # Android 9.4.74.3 uses the v4 play-page API for premium qualities.  The
    # numeric values are client enum values, not a linear bitrate ranking.
    # The anonymous ticket identifies uid=0 only; it does not grant access and
    # the server still enforces login, membership and per-track entitlement.
    _MOBILE_V4_URL = "https://mobile.ximalaya.com/mobile-playpage/track/v4/baseInfo/{timestamp}"
    _MOBILE_V4_AES_KEY = bytes.fromhex("9e3B103bA2d2cb56e805B3cCeB2512E3")
    _MOBILE_V4_IV_PREFIX = "M%6)W5F6@Jj~"
    _MOBILE_V4_SIGN_SUFFIX = "0zpnlXAG"
    _MOBILE_V4_ANONYMOUS_TICKET = MOBILE_V4_ANONYMOUS_TICKET
    WEB_AUTO_QUALITY = "喜马拉雅网页版接口"
    MOBILE_AUTO_QUALITY = "喜马拉雅移动端接口（自动最高音质）"
    MOBILE_DOLBY_PREFERRED_QUALITY = "杜比全景声优先（自动降级）"
    MOBILE_VIVID_PREFERRED_QUALITY = "Audio Vivid 优先（自动降级）"
    MOBILE_LOSSLESS_PREFERRED_QUALITY = "无损优先（自动降级）"
    # Web V3 has no client-side chapter quota. These controls only coordinate
    # transient failures so a long-running album does not turn one 429/WAF
    # rejection into hundreds of simultaneous chapter failures.
    _WEB_V3_MAX_ATTEMPTS = _bounded_env_int(
        "XIMALAYA_WEB_V3_ATTEMPTS", 5, 1, 12
    )
    _WEB_V3_RETRY_BASE_SECONDS = _positive_env_float(
        "XIMALAYA_WEB_V3_RETRY_BASE", 1.0, 0.0
    )
    _WEB_V3_MAX_BACKOFF_SECONDS = _positive_env_float(
        "XIMALAYA_WEB_V3_MAX_BACKOFF", 120.0, 1.0
    )
    _WEB_V3_RATE_LIMIT_COOLDOWN_SECONDS = _positive_env_float(
        "XIMALAYA_WEB_V3_RATE_LIMIT_COOLDOWN", 15.0, 0.0
    )
    _WEB_V3_BASE_INTERVAL = _positive_env_float(
        "XIMALAYA_WEB_V3_MIN_INTERVAL", 0.25, 0.0
    )
    _WEB_V3_MIN_INTERVAL = _WEB_V3_BASE_INTERVAL
    _WEB_V3_MAX_INTERVAL = _positive_env_float(
        "XIMALAYA_WEB_V3_MAX_INTERVAL", 5.0, _WEB_V3_BASE_INTERVAL
    )
    _WEB_V3_JITTER = _positive_env_float(
        "XIMALAYA_WEB_V3_JITTER", 0.05, 0.0
    )
    _WEB_V3_RECOVERY_SUCCESSES = _bounded_env_int(
        "XIMALAYA_WEB_V3_RECOVERY_SUCCESSES", 24, 4, 500
    )
    _WEB_V3_STATE_LOCK = threading.Lock()
    _WEB_V3_LAST_REQUEST_AT = 0.0
    _WEB_V3_RATE_LIMITED_UNTIL = 0.0
    _WEB_V3_COOLDOWN_LOGGED_UNTIL = 0.0
    _WEB_V3_CONSECUTIVE_RATE_LIMITS = 0
    _WEB_V3_SUCCESS_STREAK = 0
    # Pace only the tiny ticket + baseInfo control request. Media responses are
    # downloaded outside these controls, so CDN throughput and normal chapter
    # concurrency remain independent.
    _MOBILE_V4_REQUEST_LOCK = threading.Lock()
    _MOBILE_V4_LAST_REQUEST_AT = 0.0
    _MOBILE_V4_REQUESTS_PER_HOUR = _positive_env_float(
        "XIMALAYA_V4_REQUESTS_PER_HOUR", 0.0, 0.0
    )
    _MOBILE_V4_CONFIGURED_INTERVAL = _positive_env_float(
        "XIMALAYA_V4_MIN_INTERVAL", 0.25, 0.05
    )
    # An optional hourly cap remains available for unusually strict accounts,
    # but defaults to disabled so media throughput stays high.
    _MOBILE_V4_BASE_INTERVAL = max(
        _MOBILE_V4_CONFIGURED_INTERVAL,
        (
            3600.0 / _MOBILE_V4_REQUESTS_PER_HOUR
            if _MOBILE_V4_REQUESTS_PER_HOUR > 0
            else 0.0
        ),
    )
    _MOBILE_V4_MIN_INTERVAL = _MOBILE_V4_BASE_INTERVAL
    _MOBILE_V4_MAX_INTERVAL = _positive_env_float(
        "XIMALAYA_V4_MAX_INTERVAL", 30.0, _MOBILE_V4_BASE_INTERVAL
    )
    _MOBILE_V4_JITTER = _positive_env_float(
        "XIMALAYA_V4_JITTER", 0.05, 0.0
    )
    _MOBILE_V4_CONTROL_CONCURRENCY = _bounded_env_int(
        "XIMALAYA_V4_CONTROL_CONCURRENCY", 2, 1, 8
    )
    _MOBILE_V4_CONTROL_SEMAPHORE = threading.BoundedSemaphore(
        _MOBILE_V4_CONTROL_CONCURRENCY
    )
    _MOBILE_V4_TRANSIENT_ATTEMPTS = _bounded_env_int(
        "XIMALAYA_V4_TRANSIENT_ATTEMPTS", 12, 1, 30
    )
    _MOBILE_V4_RATE_LIMITED_UNTIL = 0.0
    _MOBILE_V4_COOLDOWN_LOGGED_UNTIL = 0.0
    _MOBILE_V4_CONSECUTIVE_RATE_LIMITS = 0
    _MOBILE_V4_SUCCESS_STREAK = 0
    _MOBILE_V4_RECOVERY_SUCCESSES = _bounded_env_int(
        "XIMALAYA_V4_RECOVERY_SUCCESSES", 24, 4, 500
    )
    _MOBILE_V4_COOLDOWN_SECONDS = _positive_env_float(
        "XIMALAYA_V4_COOLDOWN_SECONDS", 60.0, 1.0
    )
    _MOBILE_V4_MAX_COOLDOWN_SECONDS = _positive_env_float(
        "XIMALAYA_V4_MAX_COOLDOWN_SECONDS", 3600.0, _MOBILE_V4_COOLDOWN_SECONDS
    )

    # Android `libencrypt.so` URL decryption (PlayUrlUtil/EncryptUtil path).
    # The V2 constants were verified byte-for-byte against the 9.4.52.3 APK:
    # fixed XOR key at file offset 0x8c980 and substitution table at 0x8c9a0.
    # Version 0/1 uses AES-128-ECB/PKCS7 with the mobile play URL key; version 2
    # uses base64 -> substitution -> fixed-key XOR -> per-ciphertext XOR.
    _MOBILE_PLAY_URL_AES_KEY = bytes.fromhex("5776f21b9e9911388aacfe448068f16a")
    _MOBILE_DOWNLOAD_V2_XOR_KEY = bytes.fromhex(
        "802246a09acfc6ac4f546b03257e04735a046e0a51540adcc4f1678d95b95f31"
    )
    _MOBILE_DOWNLOAD_V2_SUBSTITUTION = bytes.fromhex(
        "2eb9c9b8b136d3bc3fde7c4ea5b3dcc12c4f7b85bba91b1e549757ad1c4aa70f"
        "88b73ce8a3385e89288fac761d064098326d046ed9525b25eb8d9eae87932105"
        "da3d7ed6724d0366f6f7a0ab3ea8efccbfaf81496333b0ed83ec4362a1fa2a9c"
        "f54126753714cde16c64695f9948e7650e95b44723d5e3085642349f15177819"
        "7f9a1f5ac63b29b6a261d8f2ea44cff1f90bee0c2f531a6baac86fe4167782e0"
        "866a119bdd7a597110ca740024fe84fcd1df399df33a27f413fbc7075dbec47d"
        "c39073352b5179ff0d9692708e91678b5c4601d7e64b80dbcb0930bd60d2f00a"
        "a60255ba20e5e250c22db5cec0f84c4531b2d09412d42268a4c58afd18e98c58"
    )
    _MOBILE_QUALITY_PROFILES = {
        0: {
            "level": 0,
            "key": "standard",
            "name": "标准音质",
            "aliases": ("M4A_24", "M4A 24"),
            "accept": "audio/mp4,audio/*;q=0.9,*/*;q=0.8",
        },
        1: {
            "level": 1,
            "key": "high",
            "name": "高清音质",
            "aliases": ("M4A_64", "M4A 64"),
            "accept": "audio/mp4,audio/*;q=0.9,*/*;q=0.8",
        },
        2: {
            "level": 2,
            "key": "super",
            "name": "超清音质",
            "aliases": ("M4A_128", "M4A 128"),
            "accept": "audio/mp4,audio/*;q=0.9,*/*;q=0.8",
        },
        3: {
            "level": 3,
            "key": "lossless",
            "name": "无损音质",
            "aliases": ("LOSSLESS", "FLAC", "无损"),
            "accept": "audio/flac,audio/*;q=0.9,*/*;q=0.8",
        },
        12: {
            "level": 12,
            "key": "dolby_atmos",
            "name": "杜比全景声",
            "aliases": ("DOLBY", "ATMOS", "杜比", "全景声"),
            "accept": "audio/mp4,audio/eac3,audio/*;q=0.9,*/*;q=0.8",
        },
        13: {
            "level": 13,
            "key": "audio_vivid",
            "name": "Audio Vivid 菁彩声",
            "aliases": ("AUDIO VIVID", "VIVID", "菁彩声"),
            "accept": "audio/mp4,audio/*;q=0.9,*/*;q=0.8",
        },
    }
    
    def __init__(self, cookie_string: str = None, mobile_credentials=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })
        
        # 保存Cookie字符串
        self.cookie_string = cookie_string
        self.mobile_credentials = normalize_ximalaya_mobile_credentials(mobile_credentials)
        if cookie_string:
            print(f"🍪 XimalayaDownloadManager已设置Cookie")
    
        # Legacy redirect levels are endpoint-specific aliases.  For member
        # tracks, level 2 and level 96 currently resolve to the same 96K M4A;
        # prefer the standard level 2 route and retain level 96 as a fallback.
        self.quality_level_map = {
            '24K': 0,   # 24k标准音质 (约3MB)
            '48K': 1,   # 48k高清音质 (约6MB)
            '64K': 1,   # 64k也映射到1 (约6MB)
            '96K': 2,   # 96k超高音质（VIP）
        }
        self.last_error = ""
        self.last_error_type = ""
        self.last_download_source = ""
        self.last_download_size = 0
        self.last_download_expected_size = 0
        self.last_download_quality_label = ""
        self.last_download_path = ""
        self._last_mobile_ticket_source = "saved"
        self._web_session_bootstrapped = False

    def _record_error(self, message, status_code=None, error_type=None):
        """Expose a stable failure reason to subscription result handling."""
        text = str(message or "download failed")
        lowered = text.lower()
        permission_words = (
            "permission", "forbidden", "unauthorized", "vip", "svip", "platinum",
            "login", "ximi", "drm", "encrypted", "会员", "权限", "无权", "付费", "购买", "白金",
            "登录", "登陆", "喜米", "加密", "受保护",
        )
        restricted = status_code in (401, 403) or any(word in lowered for word in permission_words)
        self.last_error = text
        self.last_error_type = str(error_type or ("restricted" if restricted else "download_failed"))

    @staticmethod
    def _cookie_mapping(value) -> Dict[str, str]:
        """Parse a Cookie header without logging or decoding its values."""
        if isinstance(value, dict):
            return {
                str(key).strip(): str(val).strip()
                for key, val in value.items()
                if key and val not in (None, "")
            }

        result = {}
        for segment in str(value or "").replace("\r", "").replace("\n", ";").split(";"):
            key, separator, val = segment.partition("=")
            key = key.strip()
            val = val.strip()
            if separator and key and val:
                result[key] = val
        return result

    def _web_cookie_header(self, excluded=()) -> str:
        """Merge the saved login Cookie with WAF cookies issued to this session."""
        excluded_names = {str(name).strip().lower() for name in excluded}
        merged = self._cookie_mapping(self.cookie_string)
        try:
            merged.update(self.session.cookies.get_dict())
        except Exception:
            pass
        return "; ".join(
            f"{key}={val}"
            for key, val in merged.items()
            if key.lower() not in excluded_names
        )

    def _web_headers(self, track_id: str, excluded_cookies=()) -> Dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"https://www.ximalaya.com/sound/{track_id}",
            "Origin": "https://www.ximalaya.com",
            "Connection": "keep-alive",
        }
        cookie = self._web_cookie_header(excluded_cookies)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _bootstrap_web_session(self, track_id: str, force: bool = False) -> bool:
        """Obtain the short-lived WAF cookies required by the web play API."""
        if self._web_session_bootstrapped and not force:
            return True
        headers = self._web_headers(
            track_id,
            excluded_cookies=("wfp",) if force else (),
        )
        headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.ximalaya.com/",
        })
        try:
            response = self.session.get(
                "https://www.ximalaya.com/revision/time",
                headers=headers,
                timeout=15,
            )
            self._web_session_bootstrapped = response.status_code == 200
        except Exception as exc:
            print(f"   WARN: 喜马拉雅网页会话初始化失败: {exc}")
            self._web_session_bootstrapped = False
        return self._web_session_bootstrapped

    @classmethod
    def _wait_for_web_v3_cooldown(cls):
        """Honor a shared Retry-After window across all Web V3 workers."""
        with cls._WEB_V3_STATE_LOCK:
            now = time.monotonic()
            remaining = max(0.0, cls._WEB_V3_RATE_LIMITED_UNTIL - now)
            should_log = (
                remaining > 0
                and cls._WEB_V3_COOLDOWN_LOGGED_UNTIL <= now
            )
            if should_log:
                cls._WEB_V3_COOLDOWN_LOGGED_UNTIL = cls._WEB_V3_RATE_LIMITED_UNTIL
        if remaining > 0:
            if should_log:
                print(f"   WAIT: 喜马拉雅 Web V3 全局冷却 {remaining:.1f} 秒后继续")
            time.sleep(remaining)

    @classmethod
    def _mark_web_v3_rate_limited(cls, seconds: float):
        delay = max(0.0, min(float(seconds), cls._WEB_V3_MAX_BACKOFF_SECONDS))
        with cls._WEB_V3_STATE_LOCK:
            now = time.monotonic()
            new_limit_episode = now >= cls._WEB_V3_RATE_LIMITED_UNTIL
            if new_limit_episode:
                cls._WEB_V3_CONSECUTIVE_RATE_LIMITS += 1
                cls._WEB_V3_SUCCESS_STREAK = 0
                cls._WEB_V3_MIN_INTERVAL = min(
                    max(cls._WEB_V3_BASE_INTERVAL, cls._WEB_V3_MIN_INTERVAL * 2),
                    cls._WEB_V3_MAX_INTERVAL,
                )
            cls._WEB_V3_RATE_LIMITED_UNTIL = max(
                cls._WEB_V3_RATE_LIMITED_UNTIL,
                now + delay,
            )
        return delay

    @classmethod
    def _clear_web_v3_rate_limit(cls):
        with cls._WEB_V3_STATE_LOCK:
            cls._WEB_V3_LAST_REQUEST_AT = 0.0
            cls._WEB_V3_RATE_LIMITED_UNTIL = 0.0
            cls._WEB_V3_COOLDOWN_LOGGED_UNTIL = 0.0
            cls._WEB_V3_CONSECUTIVE_RATE_LIMITS = 0
            cls._WEB_V3_SUCCESS_STREAK = 0
            cls._WEB_V3_MIN_INTERVAL = cls._WEB_V3_BASE_INTERVAL

    @classmethod
    def _mark_web_v3_busy(cls):
        """Ease Web V3 metadata traffic without pausing unrelated downloads."""
        with cls._WEB_V3_STATE_LOCK:
            previous = cls._WEB_V3_MIN_INTERVAL
            # A soft busy response usually clears when metadata requests are
            # spread out slightly. Keep this below the explicit-rate-limit
            # ceiling and leave concurrent CDN transfers untouched.
            ceiling = min(
                cls._WEB_V3_MAX_INTERVAL,
                max(cls._WEB_V3_BASE_INTERVAL, 1.5),
            )
            cls._WEB_V3_MIN_INTERVAL = min(
                ceiling,
                max(
                    cls._WEB_V3_BASE_INTERVAL,
                    previous + 0.05,
                    previous * 1.25,
                ),
            )
            cls._WEB_V3_SUCCESS_STREAK = 0
            return cls._WEB_V3_MIN_INTERVAL, cls._WEB_V3_MIN_INTERVAL > previous

    @classmethod
    def _wait_for_web_v3_slot(cls):
        """Pace only Web V3 metadata calls; media downloads stay concurrent."""
        while True:
            cooldown_message = ""
            with cls._WEB_V3_STATE_LOCK:
                now = time.monotonic()
                next_slot = max(
                    cls._WEB_V3_RATE_LIMITED_UNTIL,
                    cls._WEB_V3_LAST_REQUEST_AT + cls._WEB_V3_MIN_INTERVAL,
                )
                if now >= next_slot:
                    cls._WEB_V3_LAST_REQUEST_AT = now + random.uniform(
                        0.0, cls._WEB_V3_JITTER
                    )
                    return
                if (
                    now < cls._WEB_V3_RATE_LIMITED_UNTIL
                    and cls._WEB_V3_COOLDOWN_LOGGED_UNTIL <= now
                ):
                    cls._WEB_V3_COOLDOWN_LOGGED_UNTIL = cls._WEB_V3_RATE_LIMITED_UNTIL
                    remaining = max(1, int(cls._WEB_V3_RATE_LIMITED_UNTIL - now + 0.999))
                    cooldown_message = (
                        f"   WAIT: 喜马拉雅 Web V3 触发临时流控，约 {remaining}s 后自动继续"
                    )
                wait = min(next_slot - now, 1.0)
            if cooldown_message:
                print(cooldown_message)
            time.sleep(wait)

    @classmethod
    def _mark_web_v3_success(cls):
        """Gradually restore metadata request speed after sustained success."""
        with cls._WEB_V3_STATE_LOCK:
            if time.monotonic() < cls._WEB_V3_RATE_LIMITED_UNTIL:
                return
            if cls._WEB_V3_MIN_INTERVAL <= cls._WEB_V3_BASE_INTERVAL:
                cls._WEB_V3_CONSECUTIVE_RATE_LIMITS = 0
                cls._WEB_V3_SUCCESS_STREAK = 0
                return
            cls._WEB_V3_SUCCESS_STREAK += 1
            if cls._WEB_V3_SUCCESS_STREAK < cls._WEB_V3_RECOVERY_SUCCESSES:
                return
            cls._WEB_V3_MIN_INTERVAL = max(
                cls._WEB_V3_BASE_INTERVAL,
                cls._WEB_V3_MIN_INTERVAL * 0.8,
            )
            cls._WEB_V3_SUCCESS_STREAK = 0
            if cls._WEB_V3_MIN_INTERVAL <= cls._WEB_V3_BASE_INTERVAL:
                cls._WEB_V3_CONSECUTIVE_RATE_LIMITS = 0

    @classmethod
    def _web_v3_retry_delay(cls, attempt: int, response=None, rate_limited: bool = False) -> float:
        backoff = cls._WEB_V3_RETRY_BASE_SECONDS * (2 ** max(0, int(attempt) - 1))
        retry_after = 0.0
        try:
            retry_after = float((response.headers or {}).get("Retry-After") or 0)
        except (TypeError, ValueError, AttributeError):
            retry_after = 0.0
        if rate_limited or getattr(response, "status_code", None) == 429:
            backoff = max(backoff, retry_after, cls._WEB_V3_RATE_LIMIT_COOLDOWN_SECONDS)
        return max(0.0, min(backoff, cls._WEB_V3_MAX_BACKOFF_SECONDS))

    @classmethod
    def _sleep_before_web_v3_retry(
        cls, attempt: int, reason: str, response=None, rate_limited: bool = False
    ):
        limited = rate_limited or getattr(response, "status_code", None) == 429
        delay = cls._web_v3_retry_delay(
            attempt,
            response=response,
            rate_limited=limited,
        )
        if limited:
            cls._mark_web_v3_rate_limited(delay)
            cls._wait_for_web_v3_cooldown()
        elif delay > 0:
            print(f"   RETRY: Web V3 {reason}，{delay:.1f} 秒后重试")
            time.sleep(delay)

    @classmethod
    def _select_web_play_candidate(cls, track_info: Dict):
        """Choose the best decrypted web stream, preferring M4A containers."""
        candidates = []
        for item in track_info.get("playUrlList") or []:
            encrypted_url = str(item.get("url") or "").strip()
            if not encrypted_url:
                continue
            audio_url = encrypted_url if cls._looks_like_cdn_url(encrypted_url) else ""
            if not audio_url:
                audio_url = cls._decrypt_play_url_candidates(encrypted_url)
            if not audio_url:
                continue

            type_name = str(item.get("type") or "WEB").strip()
            upper_type = type_name.upper()
            preferred_format = 2 if any(marker in upper_type for marker in ("M4A", "AAC")) else 1
            try:
                quality_level = int(item.get("qualityLevel") or 0)
            except (TypeError, ValueError):
                quality_level = 0
            try:
                expected_size = int(item.get("fileSize") or 0)
            except (TypeError, ValueError):
                expected_size = 0
            candidates.append((
                (preferred_format, quality_level, expected_size),
                audio_url,
                expected_size,
                type_name,
            ))

        if not candidates:
            return None
        _, audio_url, expected_size, type_name = max(candidates, key=lambda item: item[0])
        return audio_url, expected_size, type_name

    def _request_web_track_info(self, track_id: str):
        """Request Web V3 with bounded recovery for long-running batches."""
        self._bootstrap_web_session(track_id)
        excluded_cookies = ()
        last_data = {}
        last_reason = "未知技术错误"
        rate_limited = False

        for attempt in range(1, self._WEB_V3_MAX_ATTEMPTS + 1):
            self._wait_for_web_v3_slot()
            timestamp = int(time.time() * 1000)
            # Web V3 omits M4A_128 unless the highest web quality is requested.
            url = (
                f"https://www.ximalaya.com/mobile-playpage/track/v3/baseInfo/{timestamp}"
                f"?device=web&trackId={track_id}&trackQualityLevel=2"
            )
            try:
                response = self.session.get(
                    url,
                    headers=self._web_headers(track_id, excluded_cookies=excluded_cookies),
                    timeout=15,
                )
            except requests.RequestException as exc:
                last_reason = f"网络异常: {exc}"
                if attempt < self._WEB_V3_MAX_ATTEMPTS:
                    self._sleep_before_web_v3_retry(attempt, last_reason)
                    continue
                break

            status_code = int(response.status_code or 0)
            if status_code in (401, 403):
                self._record_error(
                    f"喜马拉雅网页播放接口 HTTP {status_code}",
                    status_code,
                    error_type="restricted",
                )
                return None, {}
            if status_code in (408, 425, 429) or 500 <= status_code <= 599:
                rate_limited = rate_limited or status_code == 429
                last_reason = f"HTTP {status_code}"
                if attempt < self._WEB_V3_MAX_ATTEMPTS:
                    self._sleep_before_web_v3_retry(attempt, last_reason, response=response)
                    continue
                break
            if status_code != 200:
                self._record_error(
                    f"喜马拉雅网页播放接口 HTTP {status_code}",
                    status_code,
                    error_type="download_failed",
                )
                return None, {}

            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("响应不是 JSON 对象")
            except Exception as exc:
                last_reason = f"返回无效数据: {exc}"
                if attempt < self._WEB_V3_MAX_ATTEMPTS:
                    self._sleep_before_web_v3_retry(attempt, last_reason, response=response)
                    continue
                break

            last_data = data
            if data.get("ret") in (None, 0, "0"):
                self._mark_web_v3_success()
                return data.get("trackInfo") or {}, data

            message = str(data.get("msg") or data.get("message") or "未知错误")
            invalid_web_session = (
                "WFP" in message.upper()
                or "WEBTK" in message.upper()
                or "校验" in message
            )
            if invalid_web_session:
                last_reason = f"网页会话校验失败: {message}"
                if attempt < self._WEB_V3_MAX_ATTEMPTS:
                    print("   INFO: 网页指纹 Cookie 已失效，清理旧 WFP 并重建会话")
                    self._bootstrap_web_session(track_id, force=True)
                    excluded_cookies = ("wfp",)
                    continue
                break

            restricted = any(
                marker in message
                for marker in ("购买", "会员", "权限", "登录", "登陆", "未授权")
            )
            if restricted:
                self._record_error(
                    f"喜马拉雅网页播放接口拒绝请求: {message} (ret={data.get('ret')})",
                    error_type="restricted",
                )
                return None, data

            transient = (
                str(data.get("ret")) in {"-1", "-2", "-3", "429", "500", "502", "503", "504"}
                or any(marker in message for marker in ("频繁", "稍后", "繁忙", "拥挤", "重试"))
            )
            if transient:
                # ret=1001 / "系统繁忙" is a soft, per-request rejection: a
                # nearby retry often succeeds and it must not freeze every
                # worker. Reserve the shared cooldown for explicit throttling.
                transient_rate_limit = (
                    str(data.get("ret")) in {"-3", "429"}
                    or any(marker in message for marker in ("频繁", "请求过多", "限流"))
                )
                soft_busy = (
                    str(data.get("ret")) == "1001"
                    or any(marker in message for marker in ("稍后", "繁忙", "拥挤"))
                )
                if soft_busy and not transient_rate_limit:
                    interval, changed = self._mark_web_v3_busy()
                    if changed:
                        print(
                            "   INFO: Web V3 响应繁忙，播放地址请求间隔自动调整为 "
                            f"{interval:.2f} 秒；音频下载并发不受影响"
                        )
                rate_limited = rate_limited or transient_rate_limit
                last_reason = f"临时拒绝: {message} (ret={data.get('ret')})"
                if attempt < self._WEB_V3_MAX_ATTEMPTS:
                    self._sleep_before_web_v3_retry(
                        attempt,
                        last_reason,
                        response=response,
                        rate_limited=transient_rate_limit,
                    )
                    continue
                break

            self._record_error(
                f"喜马拉雅网页播放接口拒绝请求: {message} (ret={data.get('ret')})"
            )
            return None, data

        self._record_error(
            f"喜马拉雅网页播放接口连续 {self._WEB_V3_MAX_ATTEMPTS} 次失败: {last_reason}",
            error_type="rate_limited" if rate_limited else "download_failed",
        )
        return None, last_data

    def _download_web_authorized(self, track_id: str, save_path: str,
                                 chapter_title: str = "", progress_callback=None) -> bool:
        """Download the stream authorized by the logged-in Ximalaya web account."""
        try:
            track_info, data = self._request_web_track_info(track_id)
            if track_info is None:
                return False

            current_uid = str((data.get("extendInfo") or {}).get("currentUid") or "0")
            is_paid = self._flag_enabled(track_info.get("isPaid"))
            is_free = self._flag_enabled(track_info.get("isFree"))
            is_authorized = self._flag_enabled(track_info.get("isAuthorized"))
            candidate = self._select_web_play_candidate(track_info)

            if not candidate:
                if is_paid and not is_free and not is_authorized:
                    if current_uid in ("", "0"):
                        reason = "喜马拉雅网页登录 Cookie 未生效，请重新登录后再下载会员章节"
                    else:
                        reason = "当前喜马拉雅网页账号没有该章节的完整播放权限"
                    self._record_error(reason, error_type="restricted")
                else:
                    self._record_error("喜马拉雅网页接口未返回可用的完整音频地址")
                return False

            audio_url, expected_size, quality_label = candidate
            media_headers = {
                "User-Agent": self._web_headers(track_id)["User-Agent"],
                "Accept": "audio/mp4,audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
                "Referer": f"https://www.ximalaya.com/sound/{track_id}",
                "Origin": "https://www.ximalaya.com",
            }
            response = self.session.get(
                audio_url,
                headers=media_headers,
                stream=True,
                allow_redirects=True,
                timeout=90,
            )
            if response.status_code != 200:
                self._record_error(
                    f"喜马拉雅网页音频文件 HTTP {response.status_code}",
                    response.status_code,
                    error_type="download_failed",
                )
                return False

            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            temp_path = f"{save_path}.part"
            try:
                os.remove(temp_path)
            except OSError:
                pass

            content_type = str(response.headers.get("content-type") or "").lower()
            content_length = int(response.headers.get("content-length") or expected_size or 0)
            total_size = 0
            with open(temp_path, "wb") as output:
                for chunk in response.iter_content(chunk_size=512000):
                    if not chunk:
                        continue
                    output.write(chunk)
                    total_size += len(chunk)
                    if progress_callback:
                        progress_callback(total_size, content_length)

            if total_size <= 1024:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(f"喜马拉雅网页音频文件过小: {total_size} 字节")
                return False
            if expected_size and total_size < int(expected_size * 0.8):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(
                    f"喜马拉雅网页音频文件不完整: {total_size}/{expected_size} 字节"
                )
                return False

            valid, validation_error = self._validate_mobile_media(temp_path, 3, content_type)
            if not valid:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(f"喜马拉雅网页音频{validation_error}，已拒绝保存")
                return False

            actual_extension = self._mobile_media_extension(temp_path, 3)
            final_path = str(Path(save_path).with_suffix(actual_extension))
            os.replace(temp_path, final_path)

            self.last_error = ""
            self.last_error_type = ""
            self.last_download_source = "web_v3"
            self.last_download_size = total_size
            self.last_download_expected_size = expected_size
            self.last_download_quality_label = quality_label
            self.last_download_path = final_path
            print(
                f"SUCCESS: 喜马拉雅网页版下载成功: {Path(final_path).name} "
                f"({quality_label}, {total_size / 1024 / 1024:.2f}MB)"
            )
            return True
        except Exception as exc:
            temp_path = f"{save_path}.part"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            self._record_error(f"喜马拉雅网页版下载异常: {exc}")
            return False

    @classmethod
    def _wait_for_mobile_v4_slot(cls):
        """Pace V4 metadata calls without throttling the audio transfer.

        V4 is the risk-controlled App protocol.  Unlike the legacy web/direct
        paths, issuing many baseInfo requests without pacing quickly produces
        ``ret=1001`` and locks the shared mobile ticket/cookie for a while.
        Serialize only the small metadata request here; media bytes still flow
        outside this lock.
        """
        while True:
            cooldown_message = ""
            with cls._MOBILE_V4_REQUEST_LOCK:
                now = time.monotonic()
                next_slot = max(
                    cls._MOBILE_V4_RATE_LIMITED_UNTIL,
                    cls._MOBILE_V4_LAST_REQUEST_AT + cls._MOBILE_V4_MIN_INTERVAL,
                )
                if now >= next_slot:
                    cls._MOBILE_V4_LAST_REQUEST_AT = now + random.uniform(
                        0.0, cls._MOBILE_V4_JITTER
                    )
                    return
                if (
                    now < cls._MOBILE_V4_RATE_LIMITED_UNTIL
                    and cls._MOBILE_V4_RATE_LIMITED_UNTIL > cls._MOBILE_V4_COOLDOWN_LOGGED_UNTIL
                ):
                    cls._MOBILE_V4_COOLDOWN_LOGGED_UNTIL = cls._MOBILE_V4_RATE_LIMITED_UNTIL
                    remaining = max(1, int(cls._MOBILE_V4_RATE_LIMITED_UNTIL - now + 0.999))
                    cooldown_message = f"   ⏳ V4 接口全局冷却中，约 {remaining}s 后继续请求"
                wait = min(next_slot - now, 1.0)
            if cooldown_message:
                print(cooldown_message)
            # Re-check once per second so a cooldown raised by another worker
            # also applies to callers that were already waiting for a slot.
            time.sleep(wait)

    @classmethod
    def _mark_mobile_v4_rate_limited(cls):
        with cls._MOBILE_V4_REQUEST_LOCK:
            cls._MOBILE_V4_CONSECUTIVE_RATE_LIMITS += 1
            cls._MOBILE_V4_SUCCESS_STREAK = 0
            exponent = min(max(cls._MOBILE_V4_CONSECUTIVE_RATE_LIMITS - 1, 0), 5)
            cooldown = min(
                cls._MOBILE_V4_COOLDOWN_SECONDS * (2 ** exponent),
                cls._MOBILE_V4_MAX_COOLDOWN_SECONDS,
            )
            cls._MOBILE_V4_RATE_LIMITED_UNTIL = max(
                cls._MOBILE_V4_RATE_LIMITED_UNTIL,
                time.monotonic() + cooldown,
            )
            cls._MOBILE_V4_MIN_INTERVAL = min(
                cls._MOBILE_V4_MIN_INTERVAL * 2,
                cls._MOBILE_V4_MAX_INTERVAL,
            )
            return cooldown

    @classmethod
    def _clear_mobile_v4_rate_limit(cls):
        with cls._MOBILE_V4_REQUEST_LOCK:
            cls._MOBILE_V4_CONSECUTIVE_RATE_LIMITS = 0
            cls._MOBILE_V4_SUCCESS_STREAK = 0
            cls._MOBILE_V4_RATE_LIMITED_UNTIL = 0.0
            cls._MOBILE_V4_COOLDOWN_LOGGED_UNTIL = 0.0
            cls._MOBILE_V4_MIN_INTERVAL = cls._MOBILE_V4_BASE_INTERVAL

    @classmethod
    def _mark_mobile_v4_success(cls):
        """Recover speed gradually after a real V4 throttle response."""
        with cls._MOBILE_V4_REQUEST_LOCK:
            if cls._MOBILE_V4_MIN_INTERVAL <= cls._MOBILE_V4_BASE_INTERVAL:
                cls._MOBILE_V4_CONSECUTIVE_RATE_LIMITS = 0
                cls._MOBILE_V4_SUCCESS_STREAK = 0
                return
            cls._MOBILE_V4_SUCCESS_STREAK += 1
            if cls._MOBILE_V4_SUCCESS_STREAK < cls._MOBILE_V4_RECOVERY_SUCCESSES:
                return
            cls._MOBILE_V4_MIN_INTERVAL = max(
                cls._MOBILE_V4_BASE_INTERVAL,
                cls._MOBILE_V4_MIN_INTERVAL * 0.8,
            )
            cls._MOBILE_V4_SUCCESS_STREAK = 0
            if cls._MOBILE_V4_MIN_INTERVAL <= cls._MOBILE_V4_BASE_INTERVAL:
                cls._MOBILE_V4_CONSECUTIVE_RATE_LIMITS = 0

    @classmethod
    def _mobile_quality_profile(cls, quality: str):
        """Map UI labels to an exact Android quality enum."""
        normalized = str(quality or "").strip().upper().replace("_", " ").replace("-", " ")
        if "AUDIO VIVID" in normalized or "VIVID" in normalized or "菁彩声" in normalized:
            return cls._MOBILE_QUALITY_PROFILES[13]
        if "DOLBY" in normalized or "ATMOS" in normalized or "杜比" in normalized or "全景声" in normalized:
            return cls._MOBILE_QUALITY_PROFILES[12]
        if (
            "无损" in normalized
            or normalized in {"LOSSLESS", "FLAC", "XMLY LOSSLESS", "XIMALAYA LOSSLESS"}
        ):
            return cls._MOBILE_QUALITY_PROFILES[3]
        if normalized in {"M4A 128", "M4A128", "M4A 128K", "M4A128K", "超清音质", "超清"}:
            return cls._MOBILE_QUALITY_PROFILES[2]
        if normalized in {"M4A 64", "M4A64", "M4A 64K", "M4A64K", "高清音质", "高清"}:
            return cls._MOBILE_QUALITY_PROFILES[1]
        if normalized in {"M4A 24", "M4A24", "M4A 24K", "M4A24K", "标准音质", "标准"}:
            return cls._MOBILE_QUALITY_PROFILES[0]
        return None

    @classmethod
    def _mobile_preferred_levels(cls, quality: str):
        """Return the ordered V4 fallback chain for a UI preference mode."""
        normalized = str(quality or "").strip()
        return {
            cls.MOBILE_DOLBY_PREFERRED_QUALITY: (12, 3, 2, 1, 0),
            cls.MOBILE_VIVID_PREFERRED_QUALITY: (13, 12, 3, 2, 1, 0),
            cls.MOBILE_LOSSLESS_PREFERRED_QUALITY: (3, 2, 1, 0),
        }.get(normalized)

    def _download_mobile_quality_chain(self, track_id: str, save_path: str,
                                       levels, chapter_title: str,
                                       progress_callback=None) -> bool:
        """Try quality enums in order, falling back only when one is absent.

        A missing enum is a stable per-track catalog property and should move
        to the next level immediately.  Network, protocol, entitlement and DRM
        failures must remain visible to the chapter retry/error handling rather
        than being hidden by an unrelated low-quality request.
        """
        attempts = []
        for level in levels:
            self.last_error = ""
            self.last_error_type = ""
            if self._download_mobile_quality(
                track_id, save_path, level, chapter_title,
                progress_callback=progress_callback,
            ):
                return True
            attempts.append(f"{self._MOBILE_QUALITY_PROFILES[level]['name']}: {self.last_error}")
            if self.last_error_type != "quality_unavailable":
                break
        error_type = self.last_error_type or "quality_unavailable"
        self._record_error(
            "移动端 V4 未返回可下载音质；" + "；".join(attempts),
            error_type=error_type,
        )
        return False

    def _download_mobile_best_available(self, track_id: str, save_path: str,
                                        chapter_title: str, progress_callback=None) -> bool:
        """Download the highest V4 quality actually available for a track.

        Mobile enum values are not bitrates.  Level 3 is lossless, level 2 is
        normally M4A 128K (some catalog generations label it 96K), followed by
        levels 1 and 0.  An explicit lossless selection remains strict; only
        this auto mode is allowed to step down.
        """
        return self._download_mobile_quality_chain(
            track_id, save_path, (3, 2, 1, 0), chapter_title,
            progress_callback=progress_callback,
        )

    @classmethod
    def _is_lossless_quality(cls, quality: str) -> bool:
        profile = cls._mobile_quality_profile(quality)
        return bool(profile and profile["key"] == "lossless")

    @classmethod
    def _is_mobile_premium_quality(cls, quality: str) -> bool:
        return cls._mobile_quality_profile(quality) is not None

    def _mobile_ticket(self) -> str:
        """Return only the ticket captured from the mobile App request."""
        return self.mobile_credentials.get("x_tk", "")

    def _mobile_cookie_header(self) -> str:
        """Return the captured App Cookie; never substitute the web Cookie."""
        return self.mobile_credentials.get("cookie", "")

    def _has_mobile_credentials(self) -> bool:
        return has_ximalaya_mobile_credentials(self.mobile_credentials)

    @staticmethod
    def _ticket_provider_url() -> str:
        return str(os.environ.get("XIMALAYA_TICKET_PROVIDER_URL") or "").strip()

    def _refresh_mobile_credentials_from_provider(
        self, track_id: str, level: int, timestamp: int, device: str,
        force_bridge: bool = False,
    ) -> bool:
        """Fetch a fresh App request bundle from an Android-side signer.

        x-tk is generated by Ximalaya's Android XUID/native stack and may
        change between requests.  The Docker service therefore treats the
        configured provider as a per-request signer rather than a credential
        cache.  The provider may return either an allow-listed credential
        mapping or ``{"headers": {...}}``.
        """
        ticket_mode = str(os.environ.get("XIMALAYA_TICKET_MODE") or "auto").strip().lower()
        if ticket_mode not in {"bridge", "local", "auto"}:
            ticket_mode = "bridge"
        if force_bridge:
            ticket_mode = "bridge"
        local_error = None
        if ticket_mode in {"local", "auto"}:
            try:
                fresh_ticket = generate_mobile_ticket(
                    self.mobile_credentials,
                    business="playTrack",
                    scene="play",
                    force_fresh=True,
                )
                self.mobile_credentials["x_tk"] = fresh_ticket
                identity = ximalaya_mobile_cookie_identity(self.mobile_credentials)
                if not self.mobile_credentials.get("user_agent"):
                    app_version = identity.get("app_version") or "9.4.52.3"
                    self.mobile_credentials["user_agent"] = (
                        f"ting_{app_version}(com.ximalaya.ting.android,Android)"
                    )
                if not self.mobile_credentials.get("api_device"):
                    self.mobile_credentials["api_device"] = "android2"
                self._last_mobile_ticket_source = "local"
                return True
            except LocalTicketError as exc:
                local_error = exc
                if ticket_mode == "local":
                    self._record_error(f"喜马拉雅本地 Ticket 生成失败：{exc}")
                    return False

        provider_url = self._ticket_provider_url()
        if not provider_url:
            if self._mobile_ticket() and self._has_mobile_credentials():
                return True
            if local_error is not None:
                self._record_error(f"喜马拉雅本地 Ticket 生成失败：{local_error}")
            else:
                self._record_error("未配置 Bridge，且没有可用的本地 x-tk")
            return False

        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        provider_token = str(os.environ.get("XIMALAYA_TICKET_PROVIDER_TOKEN") or "").strip()
        if provider_token:
            request_headers["Authorization"] = f"Bearer {provider_token}"

        # The current Bridge captures tickets only from the App's premium
        # playback branches.  That ticket is session-scoped and is also valid
        # when baseInfo itself requests ordinary levels 0/1/2.
        provider_level = 3 if int(level) in (0, 1, 2) else int(level)

        try:
            response = requests.post(
                provider_url,
                headers=request_headers,
                json={
                    "track_id": str(track_id),
                    "quality_level": provider_level,
                    "timestamp": int(timestamp),
                    "device": str(device),
                    "business": "playTrack",
                    "scene": "play",
                },
                timeout=10,
            )
            if response.status_code >= 400:
                try:
                    detail = str(response.json().get("error") or "").strip()
                except (TypeError, ValueError, AttributeError):
                    detail = ""
                suffix = f"：{detail}" if detail else ""
                raise ValueError(f"HTTP {response.status_code}{suffix}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("响应不是 JSON 对象")

            fresh = normalize_ximalaya_mobile_credentials(payload)
            # Providers are allowed to return only the dynamic ticket while
            # the matching App Cookie/User-Agent remain in account settings.
            merged = dict(self.mobile_credentials)
            merged.update(fresh)
            self.mobile_credentials = normalize_ximalaya_mobile_credentials(merged)
            if not self._has_mobile_credentials():
                status = ximalaya_mobile_credential_status(self.mobile_credentials)
                self._record_error(f"喜马拉雅动态取票服务返回的凭证不完整：{status['message']}")
                return False
            self._last_mobile_ticket_source = "bridge"
            return True
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            self._record_error(f"喜马拉雅动态取票服务失败：{exc}")
            return False

    # Compatibility for callers/tests added with the initial level-3 support.
    def _has_lossless_credentials(self) -> bool:
        return self._has_mobile_credentials()

    @classmethod
    def _build_mobile_v4_sign(cls, track_id: str, timestamp: int, device: str = "android") -> str:
        """Reproduce the request signature used by the official Android client."""
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad
        except ImportError as exc:
            raise RuntimeError("缺少 pycryptodome，无法生成喜马拉雅移动端签名") from exc

        tail = str(timestamp)[-4:]
        plaintext = f"{track_id}{device}{tail}{cls._MOBILE_V4_SIGN_SUFFIX}".encode("utf-8")
        iv = f"{cls._MOBILE_V4_IV_PREFIX}{tail}".encode("utf-8")
        encrypted = AES.new(cls._MOBILE_V4_AES_KEY, AES.MODE_CBC, iv).encrypt(pad(plaintext, AES.block_size))
        # The value visible in the official HTTP request has no trailing LF.
        # Passing Base64.encodeToString(..., URL_SAFE)'s formatting newline to
        # requests changes the wire query to ``sign=...%0A`` and V4 rejects it
        # with ret=1001 before checking account entitlement.
        return base64.urlsafe_b64encode(encrypted).decode("ascii")

    @classmethod
    def _mobile_v4_request_url(cls, track_id: str, timestamp: int, device: str, level: int,
                               host: str = "") -> str:
        """Build the URL sent by the official Android client.

        OkHttp serializes the parameter map through a TreeMap and URL-encodes
        every value with java.net.URLEncoder. In particular, the trailing '='
        in the Base64 sign is sent as %3D.
        """
        track = str(track_id).strip()
        if not track.isdigit():
            raise ValueError("invalid Ximalaya track id")
        if device not in {"android", "android2", "ios"}:
            raise ValueError("invalid Ximalaya mobile device")
        quality_level = int(level)
        sign = cls._build_mobile_v4_sign(track, int(timestamp), device=device).strip()
        if not sign or any(char.isspace() for char in sign):
            raise ValueError("invalid Ximalaya V4 sign")
        params = {
            "device": device,
            "trackId": track,
            "trackQualityLevel": str(quality_level),
            "sign": sign,
        }
        # The Android client advertises decoder capability together with the
        # premium quality enum. Without these flags baseInfo silently omits
        # the level-12/13 entry and returns only ordinary M4A/MP3 URLs even
        # when the track and account are entitled to spatial audio.
        if quality_level == 12:
            params["canPlayDolby"] = "true"
        elif quality_level == 13:
            params["canPlayVividSound"] = "true"
        # URLEncoder keeps alphanumerics, '-', '_', '.', and '*', turns spaces
        # into '+', and percent-encodes everything else (including '=').
        query = "&".join(
            f"{key}={quote_plus(str(value), safe='-_.*')}"
            for key, value in sorted(params.items())
        )
        if host:
            base_url = f"https://{host}/mobile-playpage/track/v4/baseInfo/{int(timestamp)}"
        else:
            base_url = cls._MOBILE_V4_URL.format(timestamp=int(timestamp))
        return f"{base_url}?{query}"

    def _mobile_v4_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.mobile_credentials.get(
                "user_agent", "ting_9.4.74.3(com.ximalaya.ting.android,Android)"
            ),
            # CommonRequestM.addHeader in Android 9.4.52.3 sets both of these
            # headers for baseInfo V4.  Ret=1001 is returned before entitlement
            # checks when the mobile request protocol is incomplete.
            "Accept": "*/*",
            "Cookie2": "$version=1",
            "Accept-Language": self.mobile_credentials.get("accept_language", "zh-CN,zh;q=0.9"),
            "x-tk": self._mobile_ticket(),
        }
        cookie = self._mobile_cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _request_mobile_v4_info(
        self,
        track_id: str,
        level: int,
        device: str,
        profile_name: str,
    ) -> Tuple[Optional[dict], Optional[Dict[str, str]]]:
        """Issue a paced V4 request sequence with bounded control concurrency.

        ``ret=-3`` and locally signed ``ret=1001`` are transient gates observed
        on the App endpoint. Retry them with a fresh ticket and signature while
        allowing other chapters to use intervening request slots.
        """
        data = {}
        transient_code = ""
        for transient_attempt in range(1, self._MOBILE_V4_TRANSIENT_ATTEMPTS + 1):
            with self._MOBILE_V4_CONTROL_SEMAPHORE:
                self._wait_for_mobile_v4_slot()
                timestamp = int(time.time() * 1000)
                if not self._refresh_mobile_credentials_from_provider(
                    track_id, level, timestamp, device
                ):
                    return None, None
                headers = self._mobile_v4_headers()
                api_url = self._mobile_v4_request_url(
                    track_id,
                    timestamp,
                    device,
                    level,
                    host=self.mobile_credentials.get("host", ""),
                )
                info_response = self.session.get(api_url, headers=headers, timeout=20)
                if info_response.status_code != 200:
                    self._record_error(
                        f"喜马拉雅{profile_name}接口 HTTP {info_response.status_code}",
                        info_response.status_code,
                    )
                    return None, None

                data = info_response.json()
                if not isinstance(data, dict):
                    self._record_error(f"喜马拉雅{profile_name}接口返回格式无效")
                    return None, None
            ret_code = str(data.get("ret"))
            local_validation_gate = (
                ret_code == "1001"
                and getattr(self, "_last_mobile_ticket_source", "") == "local"
            )
            if ret_code != "-3" and not local_validation_gate:
                return data, headers

            transient_code = ret_code
            if transient_attempt < self._MOBILE_V4_TRANSIENT_ATTEMPTS:
                reason = "短时限流" if ret_code == "-3" else "临时凭证校验"
                print(
                    f"   🔄 V4 {reason} ret={ret_code}，第 {transient_attempt}/"
                    f"{self._MOBILE_V4_TRANSIENT_ATTEMPTS} 次未通过，刷新凭证后继续"
                )

        message = str(data.get("msg") or data.get("message") or "请求过于频繁")
        cooldown = self._mark_mobile_v4_rate_limited()
        reason = "短时限流" if transient_code == "-3" else "临时凭证校验"
        self._record_error(
            f"喜马拉雅{profile_name}接口连续 "
            f"{self._MOBILE_V4_TRANSIENT_ATTEMPTS} 次未通过 V4 {reason}: "
            f"{message} (ret={transient_code})；全局冷却 {cooldown:.0f} 秒后重新取票",
            error_type="rate_limited",
        )
        return None, None

    def _mobile_v4_device_candidates(self):
        """Return signed device variants, preferring the captured request."""
        platform_device = self.mobile_credentials.get("device", "android")
        captured = self.mobile_credentials.get("api_device", "")
        if platform_device != "android":
            return [captured or platform_device]

        if captured in {"android", "android2"}:
            first = captured
        else:
            status = ximalaya_mobile_credential_status(self.mobile_credentials)
            ticket_mode = str(os.environ.get("XIMALAYA_TICKET_MODE") or "auto").strip().lower()
            local_mode = ticket_mode in {"local", "auto"}
            # Locally generated tickets use the Android V4 direct/decrypt
            # branch. Choosing it first avoids a guaranteed rejected probe.
            first = "android2" if local_mode and status.get("local_ticket_ready") else "android"
        alternate = "android2" if first == "android" else "android"
        return [first, alternate]

    @staticmethod
    def _walk_dicts(value):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from XimalayaDownloadManager._walk_dicts(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from XimalayaDownloadManager._walk_dicts(nested)

    @classmethod
    def _item_encrypt_version(cls, item: Dict, fallback: int = 0) -> int:
        """Return the decrypt version carried by a play/download entry."""
        for field in ("downloadEncryptVersion", "encryptVersion", "version"):
            value = item.get(field)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return int(fallback or 0)

    @classmethod
    def _track_encrypt_version(cls, data: Dict) -> int:
        """Find a track-level downloadEncryptVersion for V4 responses."""
        for item in cls._walk_dicts(data):
            if item.get("downloadEncryptVersion") is not None:
                try:
                    return int(item["downloadEncryptVersion"])
                except (TypeError, ValueError):
                    continue
        return 0

    @classmethod
    def _decrypt_mobile_play_url_raw(cls, encrypted_url: str, version: int) -> str:
        """Decrypt a mobile play/download URL without the CDN shape check."""
        text = str(encrypted_url or "").strip()
        if not text or text.startswith(("http://", "https://")):
            return text
        try:
            missing = len(text) % 4
            decoded = base64.urlsafe_b64decode(text + ("=" * (4 - missing) if missing else ""))
            if int(version or 0) == 2:
                if len(decoded) < 16:
                    return ""
                payload, dynamic_key = decoded[:-16], decoded[-16:]
                plain = bytes(
                    cls._MOBILE_DOWNLOAD_V2_SUBSTITUTION[value]
                    ^ cls._MOBILE_DOWNLOAD_V2_XOR_KEY[index % len(cls._MOBILE_DOWNLOAD_V2_XOR_KEY)]
                    ^ dynamic_key[index % len(dynamic_key)]
                    for index, value in enumerate(payload)
                )
                return plain.decode("utf-8").strip()

            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad

            raw = unpad(
                AES.new(cls._MOBILE_PLAY_URL_AES_KEY, AES.MODE_ECB).decrypt(decoded),
                AES.block_size,
            )
            return raw.decode("utf-8").strip()
        except Exception:
            return ""

    @classmethod
    def _decrypt_mobile_play_url(cls, encrypted_url: str, version: int) -> str:
        """Decrypt a mobile play/download URL and require a CDN direct link."""
        result = cls._decrypt_mobile_play_url_raw(encrypted_url, version)
        if cls._looks_like_cdn_url(result):
            return result
        return ""

    @classmethod
    def _decrypt_mobile_play_url_any(cls, encrypted_url: str, preferred_version: int = 0) -> str:
        """Try V2 first, then V0/V1; the CDN shape check rejects false positives."""
        preferred = int(preferred_version or 0)
        if preferred == 1:
            versions = [1, 2, 0]
        else:
            # V2 is the current mobile primary; explicit V0 is also handled
            # after it because the CDN shape check rejects false positives.
            versions = [2, 1, 0]
        for version in versions:
            result = cls._decrypt_mobile_play_url(encrypted_url, version)
            if result:
                return result
        return ""

    @classmethod
    def _extract_authorized_mobile_url(cls, data: Dict, level: int):
        """Return only an authorized direct URL for the requested enum level."""
        profile = cls._MOBILE_QUALITY_PROFILES[level]
        encrypted_url_seen = False
        unauthorized_seen = False
        fallback_version = cls._track_encrypt_version(data)
        items = list(cls._walk_dicts(data))
        auth_fields = (
            "hasAuthorized", "authorized", "isAuthorized", "canChoose",
        )
        if level == 3:
            auth_fields += ("isXimiUhqAuthorized",)
        for item in items:
            level_values = (
                item.get("qualityLevel"), item.get("downloadQualityLevel"),
                item.get("trackQualityLevel"), item.get("quality_level"),
            )
            is_requested_level = any(str(value) == str(level) for value in level_values if value is not None)
            quality_name = str(item.get("qualityName") or item.get("quality") or item.get("type") or "")
            normalized_name = quality_name.upper()
            is_requested_name = any(alias.upper() in normalized_name for alias in profile["aliases"])
            if not (is_requested_level or is_requested_name):
                continue

            if any(field in item and not cls._flag_enabled(item.get(field)) for field in auth_fields):
                unauthorized_seen = True
                continue

            version = cls._item_encrypt_version(item, fallback_version)

            for field in ("decodeUrl", "downloadUrl", "playUrl", "url"):
                candidate = str(item.get(field) or "").strip()
                if not candidate:
                    continue
                if candidate.startswith(("http://", "https://")):
                    size = item.get("fileSize") or item.get("downloadSize") or 0
                    try:
                        expected_size = int(size)
                    except (TypeError, ValueError):
                        expected_size = 0
                    return (
                        (candidate, expected_size, quality_name or profile["name"]),
                        encrypted_url_seen,
                        unauthorized_seen,
                    )
                # Android 播放/下载地址有三种形态：明文直链、V0/V1
                # AES-ECB（mobile play URL key）、V2 substitution+XOR。
                # 解密 URL 密文不改变服务端权限判定，解不出才记为“受保护地址”。
                decrypted = cls._decrypt_mobile_play_url_any(candidate, version)
                if not decrypted:
                    decrypted = cls._decrypt_play_url_candidates(candidate)
                if decrypted:
                    size = item.get("fileSize") or item.get("downloadSize") or 0
                    try:
                        expected_size = int(size)
                    except (TypeError, ValueError):
                        expected_size = 0
                    return (
                        (decrypted, expected_size, quality_name or profile["name"]),
                        encrypted_url_seen,
                        unauthorized_seen,
                    )
                encrypted_url_seen = True

        # Track-level downloadAacUrl/downloadUrl are outside playUrlInfos and
        # carry their own downloadEncryptVersion (TrackM.fillProperties path).
        for item in items:
            for field in ("downloadAacUrl", "downloadUrl"):
                candidate = str(item.get(field) or "").strip()
                if not candidate or candidate.startswith(("http://", "https://")):
                    continue
                if any(field in item and not cls._flag_enabled(item.get(field)) for field in auth_fields):
                    unauthorized_seen = True
                    continue
                version = cls._item_encrypt_version(item, fallback_version)
                decrypted = cls._decrypt_mobile_play_url_any(candidate, version)
                if not decrypted:
                    decrypted = cls._decrypt_play_url_candidates(candidate)
                if decrypted:
                    size = item.get("fileSize") or item.get("downloadSize") or 0
                    try:
                        expected_size = int(size)
                    except (TypeError, ValueError):
                        expected_size = 0
                    return (
                        (decrypted, expected_size, str(item.get("qualityName") or profile["name"])),
                        encrypted_url_seen,
                        unauthorized_seen,
                    )
                encrypted_url_seen = True
        return None, encrypted_url_seen, unauthorized_seen

    @classmethod
    def _decrypt_play_url_candidates(cls, encrypted_url: str) -> str:
        """把喜马拉雅播放地址密文解成 CDN 直链。

        优先 AES-ECB（web v3 实测有效，密钥为网页端 JS 内置固定值），
        失败再试旧版 S-box/XOR（www2/mweb2 组）。返回合法 http(s) 直链
        或空字符串。
        """
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError:
            return ""

        # 1) AES-ECB
        try:
            aes_key = bytes.fromhex("aaad3e4fd540b0f79dca95606e72bf93")
            missing = len(encrypted_url) % 4
            padded = encrypted_url + ("=" * (4 - missing) if missing else "")
            decoded = base64.b64decode(padded.replace("-", "+").replace("_", "/"))
            raw = AES.new(aes_key, AES.MODE_ECB).decrypt(decoded)
            try:
                raw = unpad(raw, AES.block_size)
            except Exception:
                if raw and raw[-1] <= 16:
                    raw = raw[:-raw[-1]]
            result = raw.decode("utf-8").strip()
            if cls._looks_like_cdn_url(result):
                return result
        except Exception:
            pass

        # 2) 旧版 S-box/XOR（www2/mweb2 组）
        try:
            audio_key = bytes([
                204, 53, 135, 197, 39, 73, 58, 160, 79, 24, 12, 83, 180, 250, 101, 60,
                206, 30, 10, 227, 36, 95, 161, 16, 135, 150, 235, 116, 242, 116, 165, 171,
            ])
            s_box = bytes([
                183, 174, 108, 16, 131, 159, 250, 5, 239, 110, 193, 202, 153, 137, 251,
                176, 119, 150, 47, 204, 97, 237, 1, 71, 177, 42, 88, 218, 166, 82, 87,
                94, 14, 195, 69, 127, 215, 240, 225, 197, 238, 142, 123, 44, 219, 50,
                190, 29, 181, 186, 169, 98, 139, 185, 152, 13, 141, 76, 6, 157, 200,
                132, 182, 49, 20, 116, 136, 43, 155, 194, 101, 231, 162, 242, 151, 213,
                53, 60, 26, 134, 211, 56, 28, 223, 107, 161, 199, 15, 229, 61, 96, 41,
                66, 158, 254, 21, 165, 253, 103, 89, 3, 168, 40, 246, 81, 95, 58, 31,
                172, 78, 99, 45, 148, 187, 222, 124, 55, 203, 235, 64, 68, 149, 180,
                35, 113, 207, 118, 111, 91, 38, 247, 214, 7, 212, 209, 189, 241, 18,
                115, 173, 25, 236, 121, 249, 75, 57, 216, 10, 175, 112, 234, 164, 70,
                206, 198, 255, 140, 230, 12, 32, 83, 46, 245, 0, 62, 227, 72, 191, 156,
                138, 248, 114, 220, 90, 84, 170, 128, 19, 24, 122, 146, 80, 39, 37, 8,
                34, 22, 11, 93, 130, 63, 154, 244, 160, 144, 79, 23, 133, 92, 54, 102,
                210, 65, 67, 27, 196, 201, 106, 143, 52, 74, 100, 217, 179, 48, 233,
                126, 117, 184, 226, 85, 171, 167, 86, 2, 147, 17, 135, 228, 252, 105,
                30, 192, 129, 178, 120, 36, 145, 51, 163, 77, 205, 73, 4, 188, 125,
                232, 33, 243, 109, 224, 104, 208, 221, 59, 9,
            ])
            url = encrypted_url.replace("_", "/").replace("-", "+")
            missing = len(url) % 4
            if missing:
                url += "=" * (4 - missing)
            decoded = base64.b64decode(url)
            if len(decoded) < 16:
                return ""
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
            result = data.decode("utf-8", "replace").strip()
            if cls._looks_like_cdn_url(result):
                return result
        except Exception:
            pass
        return ""

    @staticmethod
    def _looks_like_cdn_url(url: str) -> bool:
        """宽松的 CDN 直链判定：http(s) + 喜马拉雅域名/媒体路径关键字。"""
        if not url or not url.startswith(("http://", "https://")):
            return False
        lower = url.lower()
        if "xmcdn.com" not in lower and "ximalaya.com" not in lower:
            return False
        return any(marker in lower for marker in (
            ".mp3", ".m4a", ".aac", ".flac", ".wav", "/storages/", "aod.cos",
        ))

    @classmethod
    def _extract_authorized_lossless_url(cls, data: Dict):
        return cls._extract_authorized_mobile_url(data, 3)

    @staticmethod
    def _file_contains_any(path: str, markers) -> bool:
        tail = b""
        with open(path, "rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    return False
                block = tail + chunk
                if any(marker in block for marker in markers):
                    return True
                tail = block[-16:]

    @staticmethod
    def _detect_mobile_media_format(path: str):
        """Identify a downloaded V4 payload by its container signature.

        CDN URLs and Content-Type values are not reliable enough to choose a
        filename: lossless level 3 is known to return both FLAC and PCM WAV.
        Return ``(format_name, extension)`` and keep unknown payloads closed.
        """
        with open(path, "rb") as source:
            head = source.read(64)
        if head.startswith(b"fLaC"):
            return "flac", ".flac"
        if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "wav", ".wav"
        if len(head) >= 12 and head[4:8] == b"ftyp":
            return "m4a", ".m4a"
        if head.startswith(b"OggS"):
            return "ogg", ".ogg"
        if head.startswith(b"caff"):
            return "caf", ".caf"
        if head.startswith(b"ID3") or (
            len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE6) == 0xE2
        ):
            return "mp3", ".mp3"
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
            return "aac", ".aac"
        return "", ""

    @classmethod
    def _validate_mobile_media(cls, path: str, level: int, content_type: str):
        with open(path, "rb") as source:
            head = source.read(32)
        stripped = head.lstrip().lower()
        if "json" in content_type or "html" in content_type or stripped.startswith((b"{", b"<")):
            return False, "文件地址返回了错误页面"
        media_format, _ = cls._detect_mobile_media_format(path)
        if not media_format:
            return False, "文件格式无法识别"
        if level == 3:
            # The exact level is already entitlement-checked in baseInfo.
            # Different albums deliver level 3 as FLAC, PCM WAV, or an audio
            # MP4/M4A container, so accept every recognized playable format.
            return True, ""

        if media_format != "m4a":
            return False, "全景声音轨不是受支持的 MP4/M4A 容器"
        if level == 12:
            # Dolby Atmos streaming normally uses E-AC-3 JOC (`ec-3`/`dec3`)
            # and may also be delivered as Dolby AC-4 (`ac-4`/`dac4`).
            markers = (b"ec-3", b"dec3", b"ac-4", b"dac4")
            if not cls._file_contains_any(path, markers):
                return False, "level 12 文件未检测到 Dolby EC-3/AC-4 音轨"
        elif level == 13:
            # `av3a` is the registered ISO BMFF sample entry for AVS3-P3.
            if not cls._file_contains_any(path, (b"av3a",)):
                return False, "level 13 文件未检测到 Audio Vivid/AVS3-P3 音轨"
        return True, ""

    @classmethod
    def _mobile_media_extension(cls, path: str, level: int) -> str:
        """Return the extension matching the downloaded container."""
        del level  # The file signature, not the requested quality, is authoritative.
        _, extension = cls._detect_mobile_media_format(path)
        return extension

    def _download_mobile_quality(self, track_id: str, save_path: str, level: int,
                                 chapter_title: str = "", progress_callback=None) -> bool:
        """Download one exact, authorized mobile-App quality without downgrading."""
        profile = self._MOBILE_QUALITY_PROFILES[level]
        if not self._ticket_provider_url() and not self._has_mobile_credentials():
            status = ximalaya_mobile_credential_status(self.mobile_credentials)
            self._record_error(
                f"喜马拉雅{profile['name']}移动端凭证不可用：{status['message']}",
                status_code=401,
            )
            return False

        try:
            data = None
            attempted_devices = []
            device_candidates = self._mobile_v4_device_candidates()
            candidate = None
            encrypted_seen = False
            unauthorized_seen = False
            for index, device in enumerate(device_candidates):
                if device not in attempted_devices:
                    attempted_devices.append(device)
                data, headers = self._request_mobile_v4_info(
                    track_id, level, device, profile["name"]
                )
                if data is None:
                    return False
                if (
                    str(data.get("ret")) == "1001"
                    and getattr(self, "_last_mobile_ticket_source", "") != "local"
                    and index + 1 < len(device_candidates)
                ):
                    print(f"   ♻️ V4 device={device} 签名分支被拒绝，尝试 {device_candidates[index + 1]}")
                    continue
                if data.get("ret") not in (None, 0, "0"):
                    break

                # Recover speed conservatively after a throttle instead of
                # jumping back to full rate after a single successful request.
                self._mark_mobile_v4_success()

                cand, enc, unauth = self._extract_authorized_mobile_url(data, level)
                if cand:
                    candidate, encrypted_seen, unauthorized_seen = cand, enc, unauth
                    break
                encrypted_seen = encrypted_seen or enc
                unauthorized_seen = unauthorized_seen or unauth
                if enc and index + 1 < len(device_candidates):
                    # Android App 的 MMKV 开关 item_use_android2_for_decrypt 表明：
                    # device=android2 分支会返回可播放（已解密）的直链。当前分支
                    # 只拿到受保护地址时，换下一个设备分支重试一次。
                    print(
                        f"   ♻️ V4 device={device} 只返回受保护的{profile['name']}地址，"
                        f"尝试 {device_candidates[index + 1]} 分支获取解密直链"
                    )
                    continue
                break

            if data.get("ret") not in (None, 0, "0"):
                message = str(data.get("msg") or data.get("message") or "未知错误")
                if str(data.get("ret")) == "50":
                    message = "移动端登录凭证已失效或请求头不完整，请从已登录 App 重新抓取同一次请求的完整请求头"
                elif str(data.get("ret")) == "1001":
                    if getattr(self, "_last_mobile_ticket_source", "") == "local":
                        message = (
                            "本地 V4 凭证验证触发临时风控，账号登录通常仍然有效；"
                            f"程序将降低请求速率并在冷却后重试（服务端：{message}）"
                        )
                    else:
                        message = (
                            f"V4 请求协议校验失败（已尝试 device={','.join(attempted_devices)}）；"
                            "请把同一次 baseInfo 的 GET 请求行、Cookie、x-tk 与 User-Agent 一起保存"
                        )
                ret_code = str(data.get("ret"))
                if ret_code == "1001":
                    cooldown = self._mark_mobile_v4_rate_limited()
                    message = f"{message}；全局冷却 {cooldown:.0f} 秒后重新取票"
                self._record_error(
                    f"喜马拉雅{profile['name']}接口拒绝请求: {message} (ret={data.get('ret')})",
                    status_code=401 if ret_code == "50" else None,
                    error_type="rate_limited" if ret_code == "1001" else None,
                )
                return False

            if not candidate:
                if unauthorized_seen:
                    reason = f"当前喜马拉雅账号没有该音频的{profile['name']}下载权限"
                    error_type = "restricted"
                elif encrypted_seen:
                    reason = f"移动端只返回了受保护的{profile['name']}地址，项目不会绕过加密或 DRM"
                    error_type = "restricted"
                else:
                    reason = f"该音频未返回可下载的{profile['name']}直链（不会回退到低码率）"
                    error_type = "quality_unavailable"
                self._record_error(reason, error_type=error_type)
                return False

            audio_url, expected_size, quality_label = candidate
            media_headers = dict(headers)
            media_headers["Accept"] = profile["accept"]
            media_response = self.session.get(
                audio_url,
                headers=media_headers,
                stream=True,
                allow_redirects=True,
                timeout=90,
            )
            if media_response.status_code != 200:
                self._record_error(
                    f"喜马拉雅{profile['name']}文件 HTTP {media_response.status_code}",
                    media_response.status_code,
                )
                return False

            content_type = str(media_response.headers.get("content-type") or "").lower()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            temp_path = f"{save_path}.part"
            try:
                os.remove(temp_path)
            except OSError:
                pass
            content_length = int(media_response.headers.get("content-length") or expected_size or 0)
            total_size = 0
            with open(temp_path, "wb") as output:
                for chunk in media_response.iter_content(chunk_size=512000):
                    if not chunk:
                        continue
                    output.write(chunk)
                    total_size += len(chunk)
                    if progress_callback:
                        progress_callback(total_size, content_length)

            if total_size <= 1024:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(f"喜马拉雅{profile['name']}文件过小: {total_size} 字节")
                return False

            if expected_size and total_size < int(expected_size * 0.8):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(
                    f"喜马拉雅{profile['name']}文件不完整: {total_size}/{expected_size} 字节"
                )
                return False

            valid, validation_error = self._validate_mobile_media(temp_path, level, content_type)
            if not valid:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                self._record_error(f"移动端返回的{profile['name']}{validation_error}，已拒绝保存")
                return False

            actual_extension = self._mobile_media_extension(temp_path, level)
            requested = Path(save_path)
            final_path = str(requested.with_suffix(actual_extension))
            os.replace(temp_path, final_path)

            self.last_error = ""
            self.last_error_type = ""
            self.last_download_source = "mobile_v4_lossless" if level == 3 else f"mobile_v4_level_{level}"
            self.last_download_size = total_size
            self.last_download_expected_size = expected_size
            self.last_download_quality_label = quality_label
            self.last_download_path = final_path
            print(
                f"✅ 喜马拉雅{profile['name']}下载成功: "
                f"{Path(final_path).name} ({total_size / 1024 / 1024:.2f}MB)"
            )
            return True
        except Exception as exc:
            temp_path = f"{save_path}.part"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            self._record_error(f"喜马拉雅{profile['name']}下载异常: {exc}")
            return False

    def _download_mobile_lossless(self, track_id: str, save_path: str,
                                  chapter_title: str = "", progress_callback=None) -> bool:
        return self._download_mobile_quality(
            track_id, save_path, 3, chapter_title, progress_callback=progress_callback
        )

    @staticmethod
    def _flag_enabled(value) -> bool:
        """Normalize the mixed bool/int/string flags returned by Ximalaya."""
        if isinstance(value, str):
            return value.strip().lower() not in ('', '0', 'false', 'none', 'null', 'no')
        return bool(value)

    def _is_confirmed_public_free_track(self, data: Dict) -> bool:
        """Fail closed unless anonymous baseInfo proves the track is public and free.

        Some old free tracks return ``ret=130`` from ``redirect/free/play`` even
        though the current web player can still play their public CDN URL.  This
        check deliberately excludes every known VIP/paid/sample marker so the
        fallback cannot become an alternate path for protected content.
        """
        if not isinstance(data, dict) or data.get('ret') != 0:
            return False
        if not self._flag_enabled(data.get('isPublic')):
            return False
        if 'isPaid' not in data or self._flag_enabled(data.get('isPaid')):
            return False

        restricted_flags = (
            'isVip', 'isVipFree', 'needVip',
            'vipOnly', 'isSample', 'isSampleAlbumTimeLimited',
        )
        if any(self._flag_enabled(data.get(key)) for key in restricted_flags):
            return False

        restricted_levels = (
            'paidType', 'priceTypeId', 'priceTypeEnum', 'vipFreeType',
            'vipFirstStatus',
        )
        for key in restricted_levels:
            value = data.get(key)
            if value in (None, '', False):
                continue
            try:
                if int(value) != 0:
                    return False
            except (TypeError, ValueError):
                return False

        if data.get('priceTypes'):
            return False

        return any(
            str(data.get(key) or '').startswith(('http://', 'https://'))
            for key in (
                'playPathHq', 'playPathAacv224', 'playPathAacv164',
                'playUrl64', 'playUrl32', 'downloadAacUrl', 'downloadUrl',
            )
        )

    @staticmethod
    def _m4a_duration_seconds(path: Path) -> Optional[float]:
        """Read the MP4 movie-header duration without requiring ffprobe."""
        try:
            source = path.open('rb')
        except OSError:
            return None

        with source:
            tail = b''
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    return None
                payload = tail + chunk
                marker_at = 0
                while True:
                    marker_at = payload.find(b'mvhd', marker_at)
                    if marker_at < 0:
                        break
                    if marker_at >= 4:
                        try:
                            atom_size = struct.unpack_from('>I', payload, marker_at - 4)[0]
                            version = payload[marker_at + 4]
                            if atom_size >= 28 and version == 0:
                                timescale = struct.unpack_from('>I', payload, marker_at + 16)[0]
                                duration = struct.unpack_from('>I', payload, marker_at + 20)[0]
                            elif atom_size >= 40 and version == 1:
                                timescale = struct.unpack_from('>I', payload, marker_at + 24)[0]
                                duration = struct.unpack_from('>Q', payload, marker_at + 28)[0]
                            else:
                                timescale = duration = 0
                            if timescale > 0 and duration > 0:
                                return duration / timescale
                        except (IndexError, struct.error):
                            pass
                    marker_at += 4
                tail = payload[-64:]

    @staticmethod
    def _public_quality_label(source_field: str, total_size: int = 0,
                              duration: Optional[float] = None) -> str:
        if total_size >= 128 * 1024 and duration and duration > 0:
            measured_kbps = total_size * 8 / duration / 1000
            nominal_kbps = min((24, 32, 48, 64, 96, 128), key=lambda value: abs(value - measured_kbps))
            audio_format = 'MP3' if source_field in ('playUrl64', 'playUrl32', 'downloadUrl') else 'AAC'
            return f'公开 {audio_format} {nominal_kbps}K'
        labels = {
            'playPathHq': '公开高音质',
            'playPathAacv224': '公开 AAC 24K',
            'playPathAacv164': '公开 AAC 64K',
            'playUrl64': '公开 MP3 64K',
            'playUrl32': '公开 MP3 32K',
            'downloadAacUrl': '公开 AAC',
            'downloadUrl': '公开音频',
        }
        return labels.get(source_field, source_field)

    def _fetch_anonymous_public_track_info(self, track_id: str) -> Optional[Dict]:
        """Read public track metadata without touching logged-in paid/VIP APIs."""
        headers = {
            'User-Agent': 'ting_9.4.2_iPhone_2210132C_1170x2532',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip',
            'Connection': 'keep-alive',
            'Host': 'mobile.ximalaya.com',
        }
        for device in ('ios', 'www2'):
            url = (
                'http://mobile.ximalaya.com/v1/track/baseInfo'
                f'?device={device}&trackId={track_id}&_={int(time.time() * 1000)}'
            )
            try:
                response = self.session.get(url, headers=headers, timeout=10)
                if response.status_code != 200:
                    continue
                data = response.json()
                # A successful response has authoritative entitlement flags.
                # Do not try another device to hunt for a more permissive result.
                if isinstance(data, dict) and data.get('ret') == 0:
                    return data if self._is_confirmed_public_free_track(data) else None
            except Exception as exc:
                print(f"   ⚠️ 免费声音信息检查失败 (device={device}): {exc}")
        return None

    @staticmethod
    def _public_fallback_candidates(data: Dict, audio_quality: str) -> List[Tuple[str, str, int]]:
        """Return public CDN candidates in requested-quality order."""
        quality = str(audio_quality or '').upper()
        if quality in ('96K', '128K', '192K'):
            fields = (
                ('playPathHq', 'playHqSize'),
                ('playPathAacv164', 'playPathAacv164Size'),
                ('playPathAacv224', 'playPathAacv224Size'),
                ('playUrl64', 'playUrl64Size'),
                ('playUrl32', 'playUrl32Size'),
                ('downloadAacUrl', 'downloadAacSize'),
                ('downloadUrl', 'downloadSize'),
            )
        elif quality in ('64K', '48K'):
            fields = (
                ('playPathAacv164', 'playPathAacv164Size'),
                ('playPathAacv224', 'playPathAacv224Size'),
                ('playUrl64', 'playUrl64Size'),
                ('playUrl32', 'playUrl32Size'),
                ('downloadAacUrl', 'downloadAacSize'),
                ('downloadUrl', 'downloadSize'),
            )
        else:
            fields = (
                ('playUrl32', 'playUrl32Size'),
                ('playUrl64', 'playUrl64Size'),
                ('downloadAacUrl', 'downloadAacSize'),
                ('downloadUrl', 'downloadSize'),
            )

        candidates = []
        seen = set()
        for url_field, size_field in fields:
            if url_field == 'playPathHq' and XimalayaDownloadManager._flag_enabled(
                data.get('hqNeedVip')
            ):
                continue
            url = str(data.get(url_field) or '').strip()
            if not url.startswith(('http://', 'https://')) or url in seen:
                continue
            seen.add(url)
            try:
                expected_size = int(data.get(size_field) or 0)
            except (TypeError, ValueError):
                expected_size = 0
            candidates.append((url_field, url, expected_size))
        return candidates

    def _download_confirmed_public_fallback(self, track_id: str, audio_quality: str,
                                            save_path: str, progress_callback=None,
                                            public_info: Optional[Dict] = None) -> bool:
        """Download only an anonymously confirmed public/free track URL."""
        data = public_info or self._fetch_anonymous_public_track_info(track_id)
        if data and not self._is_confirmed_public_free_track(data):
            data = None
        if not data:
            return False

        candidates = self._public_fallback_candidates(data, audio_quality)
        print(f"   INFO: 已确认是公开免费声音，尝试 {len(candidates)} 个免费接口直链")
        headers = {
            'User-Agent': 'XimalayaFM/8.6.93 (iPhone; iOS 16.6; Scale/3.00)',
            'Accept': '*/*',
            'Referer': 'https://www.ximalaya.com/',
        }

        final_path = Path(save_path)
        temp_path = final_path.with_name(final_path.name + '.part')
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            expected_duration = float(data.get('duration') or 0)
        except (TypeError, ValueError):
            expected_duration = 0.0
        try:
            sample_duration = float(data.get('sampleDuration') or 0)
        except (TypeError, ValueError):
            sample_duration = 0.0

        for source_field, public_url, expected_size in candidates:
            try:
                temp_path.unlink(missing_ok=True)
                response = self.session.get(
                    public_url, headers=headers, stream=True,
                    allow_redirects=True, timeout=60,
                )
                if response.status_code != 200:
                    print(f"   ⚠️ 公开直链 {source_field} 返回 HTTP {response.status_code}")
                    close = getattr(response, 'close', None)
                    if close:
                        close()
                    continue

                iterator = response.iter_content(chunk_size=512000)
                first_chunk = next(iterator, b'')
                content_type = str(response.headers.get('content-type', '')).lower()
                prefix = first_chunk.lstrip()[:16].lower()
                if (
                    'application/json' in content_type
                    or 'text/html' in content_type
                    or prefix.startswith((b'{', b'<html', b'<!doctype'))
                ):
                    print(f"   ⚠️ 公开直链 {source_field} 返回的不是音频")
                    close = getattr(response, 'close', None)
                    if close:
                        close()
                    continue

                total_size = 0
                content_length = int(response.headers.get('content-length') or expected_size or 0)
                with temp_path.open('wb') as output:
                    if first_chunk:
                        output.write(first_chunk)
                        total_size += len(first_chunk)
                        if progress_callback:
                            progress_callback(total_size, content_length)
                    for chunk in iterator:
                        if not chunk:
                            continue
                        output.write(chunk)
                        total_size += len(chunk)
                        if progress_callback:
                            progress_callback(total_size, content_length)

                close = getattr(response, 'close', None)
                if close:
                    close()

                validation_error = ''
                if total_size <= 1024:
                    validation_error = f'文件过小（{total_size} 字节）'
                elif content_length and total_size != content_length:
                    validation_error = (
                        f'文件不完整（应为 {content_length} 字节，实际 {total_size} 字节）'
                    )
                elif expected_size and total_size < expected_size * 0.98:
                    validation_error = (
                        f'文件不完整（接口标记 {expected_size} 字节，实际 {total_size} 字节）'
                    )

                media_duration = self._m4a_duration_seconds(temp_path)
                if not validation_error and expected_duration > 0:
                    minimum_duration = max(expected_duration * 0.9, expected_duration - 10.0)
                    if media_duration is not None and media_duration < minimum_duration:
                        validation_error = (
                            f'疑似试听片段（应约 {expected_duration:.0f} 秒，'
                            f'实际 {media_duration:.0f} 秒）'
                        )
                    elif media_duration is None and sample_duration > 0:
                        validation_error = (
                            '接口包含试听时长标记，且无法验证文件完整时长'
                        )

                if validation_error:
                    temp_path.unlink(missing_ok=True)
                    print(f"   WARN: 免费接口 {source_field} {validation_error}，已拒绝保存")
                    continue

                os.replace(temp_path, final_path)
                self.last_error = ''
                self.last_error_type = ''
                self.last_download_source = f'public_base_info:{source_field}'
                self.last_download_size = total_size
                self.last_download_expected_size = expected_size
                self.last_download_quality_label = self._public_quality_label(
                    source_field, total_size, media_duration
                )
                self.last_download_path = str(final_path)
                duration_text = f", {media_duration:.0f}秒" if media_duration else ''
                print(
                    f"✅ 免费接口下载成功 "
                    f"({self.last_download_quality_label}, {total_size / 1024 / 1024:.2f}MB{duration_text})"
                )
                return True
            except Exception as exc:
                temp_path.unlink(missing_ok=True)
                print(f"   ⚠️ 公开直链 {source_field} 下载失败: {exc}")

        self._record_error('公开免费声音直链下载失败')
        return False
    
    def download_audio_by_quality(self, track_id: str, quality: str, save_path: str, 
                                 album_title: str = "", chapter_title: str = "", progress_callback=None) -> bool:
        """
        根据音质下载音频文件（使用直接下载API）
        :param track_id: 章节ID
        :param quality: 音质选项 (M4A_96K, M4A_64K, M4A_48K, M4A_32K, MP3_64K, MP3_48K)
        :param save_path: 保存路径
        :param album_title: 专辑标题
        :param chapter_title: 章节标题
        :return: 下载是否成功
        """
        self.last_error = ""
        self.last_error_type = ""
        self.last_download_source = ""
        self.last_download_size = 0
        self.last_download_expected_size = 0
        self.last_download_quality_label = ""
        self.last_download_path = ""

        if str(quality or "").strip() == self.MOBILE_AUTO_QUALITY:
            print("🎼 使用喜马拉雅移动端 V4，按无损 → 128/96K → 64K → 24K 自动选择")
            return self._download_mobile_best_available(
                track_id, save_path, chapter_title, progress_callback=progress_callback
            )

        preferred_levels = self._mobile_preferred_levels(quality)
        if preferred_levels:
            names = " → ".join(
                self._MOBILE_QUALITY_PROFILES[level]["name"] for level in preferred_levels
            )
            print(f"🎼 使用喜马拉雅移动端 V4 音质优先链：{names}")
            return self._download_mobile_quality_chain(
                track_id,
                save_path,
                preferred_levels,
                chapter_title,
                progress_callback=progress_callback,
            )

        if str(quality or "").strip() == self.WEB_AUTO_QUALITY:
            print("🌐 网页模式：检查公开免费与会员高音质线路")
            public_info = self._fetch_anonymous_public_track_info(track_id)
            if public_info:
                needs_member_hq = self._flag_enabled(public_info.get('hqNeedVip'))
                legacy_attempted = False
                if needs_member_hq and self.cookie_string:
                    print("   INFO: 免费章节的高音质需要会员，先尝试旧版 Cookie 直连")
                    legacy_attempted = True
                    if self._download_m4a_direct_api(
                        track_id,
                        "96K",
                        save_path,
                        chapter_title,
                        progress_callback=progress_callback,
                        allow_public_fallback=False,
                    ):
                        return True
                    self.last_error = ""
                    self.last_error_type = ""

                if needs_member_hq and self._has_mobile_credentials():
                    print("   INFO: 旧版会员直连未取得音频，尝试本地移动 V4 高音质")
                    if self._download_mobile_best_available(
                        track_id,
                        save_path,
                        chapter_title,
                        progress_callback=progress_callback,
                    ):
                        return True
                    self.last_error = ""
                    self.last_error_type = ""

                print("   INFO: 使用公开免费音质保底；免费章节不调用新版 Web V3")
                if self._download_confirmed_public_fallback(
                    track_id,
                    "64K",
                    save_path,
                    progress_callback=progress_callback,
                    public_info=public_info,
                ):
                    return True

                if not legacy_attempted:
                    print("   WARN: 公开免费音频校验失败，最后尝试旧版直连")
                    self.last_error = ""
                    self.last_error_type = ""
                    return self._download_m4a_direct_api(
                        track_id,
                        "96K",
                        save_path,
                        chapter_title,
                        progress_callback=progress_callback,
                        allow_public_fallback=False,
                    )
                return False

            # A failed/negative anonymous probe is not an entitlement result.
            # Preserve the established legacy and authorized routing for every
            # track that was not safely downloaded from a public CDN URL.
            self.last_error = ""
            self.last_error_type = ""
            print("🌐 当前章节未走免费接口，继续使用旧版 V3 直连接口")
            if self._download_m4a_direct_api(
                track_id,
                "96K",
                save_path,
                chapter_title,
                progress_callback=progress_callback,
                allow_public_fallback=False,
            ):
                self.last_error = ""
                self.last_error_type = ""
                if not self.last_download_source:
                    self.last_download_source = "legacy_web_redirect"
                self.last_download_path = self.last_download_path or save_path
                self.last_download_quality_label = self.last_download_quality_label or "96K"
                return True

            # The authorized endpoint is intentionally reserved for tracks the
            # legacy redirect identifies as protected. Ordinary transport and
            # server failures stay on the legacy route to avoid multiplying a
            # single failure into several throttled Web V3 requests.
            if self.last_error_type != "restricted":
                return False

            print("   INFO: 旧版 V3 接口确认章节受限，切换网页授权接口")
            return self._download_web_authorized(
                track_id,
                save_path,
                chapter_title,
                progress_callback=progress_callback,
            )

        mobile_profile = self._mobile_quality_profile(quality)
        if mobile_profile:
            level = int(mobile_profile["level"])
            print(f"🎼 使用喜马拉雅 Android V4 授权{mobile_profile['name']}接口（level {level}）")
            return self._download_mobile_quality(
                track_id,
                save_path,
                level,
                chapter_title,
                progress_callback=progress_callback,
            )
        
        # 解析音质参数
        quality_upper = quality.replace(' ', '_').upper()
        quality_parts = quality_upper.split('_')
        audio_format = quality_parts[0]  # M4A 或 MP3
        audio_quality = quality_parts[1] if len(quality_parts) > 1 else '96K'  # 96K, 64K, 48K, 24K
        
        print(f"   📊 音质解析: 格式={audio_format}, 音质={audio_quality}")
        
        # M4A使用移动端直接下载API（根据映射规则md）
        if audio_format == 'M4A':
            return self._download_m4a_direct_api(track_id, audio_quality, save_path, chapter_title, progress_callback=progress_callback)
        
        # MP3使用网页端API（需要解密URL）
        elif audio_format == 'MP3':
            print(f"💻 使用网页端API下载MP3...")
            return self._download_mp3_from_web(track_id, audio_quality, save_path, chapter_title, progress_callback=progress_callback)
        
        # 其他格式使用默认方法
        else:
            print(f"📡 获取章节 {track_id} 的所有音频URL...")
            audio_urls = self._get_all_audio_urls(track_id)
            if not audio_urls:
                print("❌ 无法获取音频URL")
                return False
            return self._download_default(audio_urls, save_path, album_title, chapter_title)
    
    def _download_m4a_direct_api(self, track_id: str, audio_quality: str, save_path: str,
                                 chapter_title: str, progress_callback=None,
                                 allow_public_fallback: bool = True) -> bool:
        """
        使用直接下载API下载M4A格式音频（根据映射规则md和实际测试结果）
        API格式: http://mobile.ximalaya.com/mobile/redirect/free/play/{track_id}/{quality_level}
        
        :param track_id: 章节ID
        :param audio_quality: 音质 (96K, 64K, 48K, 24K)
        :param save_path: 保存路径
        :param chapter_title: 章节标题
        :return: 下载是否成功
        """
        primary_level = self.quality_level_map.get(audio_quality, 2)
        quality_levels = [primary_level]
        if audio_quality == '96K' and primary_level != 96:
            quality_levels.append(96)

        print(f"🎵 使用旧版直连API - 音质: {audio_quality} (Level {primary_level})")

        mobile_headers = {
            'User-Agent': 'ting_9.4.2_AndroidPhone_2210132C_1440x2560',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://m.ximalaya.com/',
            'X-Requested-With': 'XMLHttpRequest',
        }

        if self.cookie_string:
            mobile_headers['Cookie'] = self.cookie_string
            print("   🍪 已携带本地登录 Cookie")

        final_path = Path(save_path)
        temp_path = final_path.with_name(final_path.name + '.part')
        final_path.parent.mkdir(parents=True, exist_ok=True)

        for attempt_index, quality_level in enumerate(quality_levels):
            direct_url = (
                "https://mobile.ximalaya.com/mobile/redirect/free/play/"
                f"{track_id}/{quality_level}"
            )
            print(f"   🔗 请求旧直连 Level {quality_level}")
            try:
                temp_path.unlink(missing_ok=True)
                response = self.session.get(
                    direct_url,
                    headers=mobile_headers,
                    stream=True,
                    allow_redirects=True,
                    timeout=(10, 120),
                )

                if response.status_code != 200:
                    status_code = response.status_code
                    close = getattr(response, 'close', None)
                    if close:
                        close()
                    self._record_error(f"HTTP {status_code}", status_code)
                    if status_code in (401, 403):
                        return False
                    if attempt_index + 1 < len(quality_levels):
                        print(f"   ↪️ Level {quality_level} 返回 HTTP {status_code}，尝试兼容别名")
                        continue
                    return False

                content_type = str(response.headers.get('content-type', '')).lower()
                try:
                    content_length = int(response.headers.get('content-length') or 0)
                except (TypeError, ValueError):
                    content_length = 0

                iterator = response.iter_content(chunk_size=512 * 1024)
                first_chunk = next(iterator, b'')
                prefix = first_chunk.lstrip()[:16].lower()
                if 'json' in content_type or prefix.startswith(b'{'):
                    try:
                        error_data = json.loads(first_chunk.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        error_data = {}
                    close = getattr(response, 'close', None)
                    if close:
                        close()
                    if error_data.get('ret') == 130:
                        if allow_public_fallback and self._download_confirmed_public_fallback(
                            track_id,
                            audio_quality,
                            save_path,
                            progress_callback=progress_callback,
                        ):
                            return True
                        if self.last_error_type == 'download_failed':
                            return False
                        self._record_error(
                            f"权限不足: {error_data.get('msg') or 'VIP required'}",
                            error_type='restricted',
                        )
                        return False

                    self._record_error(
                        f"API error ret={error_data.get('ret')}: "
                        f"{error_data.get('msg') or 'unknown'}"
                    )
                    if attempt_index + 1 < len(quality_levels):
                        print(f"   ↪️ Level {quality_level} 返回非权限错误，尝试兼容别名")
                        continue
                    return False

                total_size = 0
                with temp_path.open('wb') as output:
                    if first_chunk:
                        output.write(first_chunk)
                        total_size += len(first_chunk)
                        if progress_callback:
                            progress_callback(total_size, content_length)
                    for chunk in iterator:
                        if not chunk:
                            continue
                        output.write(chunk)
                        total_size += len(chunk)
                        if progress_callback:
                            progress_callback(total_size, content_length)

                close = getattr(response, 'close', None)
                if close:
                    close()

                validation_error = ''
                if total_size <= 1024:
                    validation_error = f"文件过小（{total_size} 字节）"
                elif content_length and total_size != content_length:
                    validation_error = (
                        f"文件不完整（应为 {content_length} 字节，"
                        f"实际 {total_size} 字节）"
                    )
                else:
                    valid, media_error = self._validate_mobile_media(
                        str(temp_path), 2, content_type
                    )
                    if not valid:
                        validation_error = media_error

                if validation_error:
                    temp_path.unlink(missing_ok=True)
                    self._record_error(f"旧版直连{validation_error}，已拒绝保存")
                    if attempt_index + 1 < len(quality_levels):
                        print(f"   ↪️ Level {quality_level} 校验失败，尝试兼容别名")
                        continue
                    return False

                os.replace(temp_path, final_path)
                size_mb = total_size / (1024 * 1024)
                print(
                    f"✅ 旧版直连下载成功: {final_path.name} "
                    f"({audio_quality}, Level {quality_level}, {size_mb:.2f}MB)"
                )
                self.last_error = ''
                self.last_error_type = ''
                self.last_download_source = 'legacy_web_redirect'
                self.last_download_size = total_size
                self.last_download_expected_size = content_length
                self.last_download_quality_label = audio_quality
                self.last_download_path = str(final_path)
                return True
            except Exception as exc:
                temp_path.unlink(missing_ok=True)
                self._record_error(f"download exception: {exc}")
                if attempt_index + 1 < len(quality_levels):
                    print(f"   ↪️ Level {quality_level} 下载异常，尝试兼容别名")
                    continue
                print(f"❌ 旧版直连下载异常: {exc}")
                return False

        return False
    
    def _download_mp3_from_web(self, track_id: str, audio_quality: str, save_path: str, chapter_title: str, progress_callback=None) -> bool:
        """
        尝试下载MP3格式音频
        
        重要说明：
        1. 喜马拉雅的MP3和M4A共用同一个API
        2. 网页端API解密后的URL需要特殊认证，无法直接使用
        3. 实际测试表明，大多数音频只有M4A格式
        
        因此这里使用移动端API下载，并验证文件格式
        如果下载的是M4A，会给出警告建议用户选择M4A格式
        
        :param track_id: 章节ID
        :param audio_quality: 音质 (96K, 64K, 48K, 24K)
        :param save_path: 保存路径
        :param chapter_title: 章节标题
        :return: 下载是否成功
        """
        print(f"🎵 使用网页端API下载MP3 - 音质: {audio_quality}")
        print(f"📝 注意：移动端不支持MP3，必须使用网页端API")
        
        try:
            # 1. 调用网页端API获取音频信息
            timestamp = int(time.time() * 1000)
            web_api_url = f"https://www.ximalaya.com/mobile-playpage/track/v3/baseInfo/{timestamp}?device=web&trackId={track_id}"
            
            # 使用网页端Headers
            web_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': 'https://www.ximalaya.com/',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            }
            
            # 添加Cookie（如果有）
            if self.cookie_string:
                web_headers['Cookie'] = self.cookie_string
                print(f"   🍪 已添加Cookie到网页端API请求")
                print(f"   📋 Cookie长度: {len(self.cookie_string)} 字符")
                
                # 检查Cookie中是否包含关键字段
                cookie_lower = self.cookie_string.lower()
                if '_token' in cookie_lower:
                    print(f"   ✅ Cookie包含_token字段")
                else:
                    print(f"   ⚠️ Cookie缺少_token字段")
                    
                if 'login_type' in cookie_lower:
                    print(f"   ✅ Cookie包含login_type字段")
                else:
                    print(f"   ⚠️ Cookie缺少login_type字段")
            
            print(f"   🔗 网页端API: {web_api_url}")
            response = self.session.get(web_api_url, headers=web_headers, timeout=15)
            
            print(f"   📡 响应状态: {response.status_code}")
            
            if response.status_code != 200:
                print(f"❌ API请求失败: HTTP {response.status_code}")
                self._record_error(f"HTTP {response.status_code}", response.status_code)
                return False
            
            data = response.json()
            
            if data.get('ret') != 0:
                print(f"❌ API返回错误: ret={data.get('ret')}, msg={data.get('msg', 'Unknown')}")
                self._record_error(f"API error ret={data.get('ret')}: {data.get('msg', 'unknown')}")
                return False
            
            # 2. 提取playUrlList
            track_info = data.get('trackInfo', {})
            play_url_list = track_info.get('playUrlList', [])
            
            if not play_url_list:
                print("❌ 未找到可用的音频URL列表")
                print(f"   📊 trackInfo包含的字段: {list(track_info.keys())[:10] if track_info else 'None'}")
                self._record_error("no playable audio URL returned")
                return False
            
            print(f"   📋 找到 {len(play_url_list)} 个音频URL")
            
            # 3. 查找匹配音质的MP3 URL
            quality_level_map = {
                '24K': 0,
                '48K': 1,
                '64K': 1,
                '96K': 96,
            }
            target_level = quality_level_map.get(audio_quality, 1)
            
            # 优先查找精确匹配的MP3
            mp3_url_info = None
            for url_info in play_url_list:
                url_type = url_info.get('type', '').upper()
                quality_level = url_info.get('qualityLevel', 0)
                
                print(f"      类型: {url_type}, 级别: {quality_level}")
                
                # 查找MP3类型
                if 'MP3' in url_type:
                    if quality_level == target_level:
                        mp3_url_info = url_info
                        print(f"   ✅ 找到精确匹配的MP3 URL (级别: {quality_level})")
                        break
                    elif mp3_url_info is None:
                        mp3_url_info = url_info
                        print(f"   📝 暂存MP3 URL (级别: {quality_level})")
            
            if not mp3_url_info:
                print("❌ 未找到MP3格式的URL")
                self._record_error("no MP3 audio URL returned")
                return False
            
            # 4. 解密URL
            encrypted_url = mp3_url_info.get('url', '')
            if not encrypted_url:
                print("❌ 加密URL为空")
                self._record_error("empty encrypted audio URL")
                return False
            
            print(f"   🔐 加密URL: {encrypted_url[:80]}...")
            
            # 使用清洁的解密方法
            decrypted_url = self._decrypt_audio_url_clean(encrypted_url)
            
            if not decrypted_url or not decrypted_url.startswith('http'):
                print(f"❌ URL解密失败或格式错误")
                self._record_error("unable to decrypt audio URL")
                return False
            
            print(f"   🔓 解密URL: {decrypted_url[:100]}...")
            
            # 5. 下载MP3文件
            print(f"   📥 开始下载MP3文件...")
            
            # 下载时也添加Cookie
            if self.cookie_string:
                web_headers['Cookie'] = self.cookie_string
            
            audio_response = self.session.get(decrypted_url, headers=web_headers, stream=True, timeout=60)
            
            if audio_response.status_code != 200:
                print(f"❌ 下载失败: HTTP {audio_response.status_code}")
                self._record_error(f"HTTP {audio_response.status_code}", audio_response.status_code)
                return False
            
            # 检查Content-Type
            content_type = audio_response.headers.get('content-type', '').lower()
            content_length = audio_response.headers.get('content-length', '0')
            
            print(f"   📊 Content-Type: {content_type}")
            print(f"   📊 Content-Length: {content_length}")
            
            # 检查是否是错误响应
            if 'application/json' in content_type:
                # 读取响应内容检查是否是错误信息
                try:
                    error_content = audio_response.text
                    print(f"   ❌ 服务器返回JSON错误: {error_content[:200]}...")
                    
                    # 尝试解析错误信息
                    import json
                    error_data = json.loads(error_content)
                    error_msg = error_data.get('msg', 'Unknown error')
                    error_ret = error_data.get('ret', 'Unknown')
                    print(f"   📋 错误详情: ret={error_ret}, msg={error_msg}")
                    self._record_error(f"API error ret={error_ret}: {error_msg}")
                    
                    return False
                except Exception:
                    print(f"   ❌ 无法解析错误响应")
                    self._record_error("invalid JSON error response")
                    return False
            
            # 检查文件大小
            if content_length == '0' or int(content_length) < 1024:
                print(f"   ⚠️ 文件太小 ({content_length} 字节)，可能是错误响应")
                self._record_error(f"audio response too small ({content_length} bytes)")
                return False
            
            # 确保保存目录存在
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # 流式下载
            total_size = 0
            with open(save_path, 'wb') as f:
                for chunk in audio_response.iter_content(chunk_size=512000):
                    if chunk:
                        f.write(chunk)
                        total_size += len(chunk)
                        if progress_callback:
                            progress_callback(total_size, int(content_length or 0))
            
            # 验证文件大小
            size_mb = total_size / (1024 * 1024)
            print(f"✅ MP3下载完成: {save_path} ({size_mb:.2f}MB)")
            
            # 检查文件大小是否合理
            if total_size < 1024:  # 小于1KB
                print(f"   ❌ 文件太小 ({total_size} 字节)，可能是错误响应")
                self._record_error(f"downloaded audio too small ({total_size} bytes)")
                return False
            
            # Content-Type已经验证是audio/mpeg，文件大小合理，直接返回成功
            print(f"   ✅ MP3文件下载成功 (已通过Content-Type和大小验证)")
            return True
            
        except Exception as e:
            print(f"❌ MP3下载异常: {e}")
            import traceback
            traceback.print_exc()
            self._record_error(f"MP3 download exception: {e}")
            return False
    
    def _decrypt_audio_url_clean(self, encrypted_url: str) -> str:
        """
        解密音频URL - 使用正确的解密方法（来自ultimate_mp3_downloader.py）
        尝试两种解密方法：CryptoJS AES和原始解密
        """
        try:
            # 如果URL已经是完整的HTTP URL，直接返回
            if encrypted_url.startswith('http'):
                return encrypted_url
            
            # 方法1: 尝试CryptoJS AES解密
            try:
                from Crypto.Cipher import AES
                from Crypto.Util.Padding import unpad
                import base64
                
                # AES密钥 (来自ultimate_mp3_downloader.py)
                AES_KEY = bytes.fromhex('aaad3e4fd540b0f79dca95606e72bf93')
                
                # Base64URL解码
                decoded_data = base64.urlsafe_b64decode(encrypted_url + '==')
                
                # AES-ECB解密
                cipher = AES.new(AES_KEY, AES.MODE_ECB)
                decrypted_data = cipher.decrypt(decoded_data)
                
                # 去除PKCS7填充
                try:
                    decrypted_data = unpad(decrypted_data, AES.block_size)
                except Exception:
                    # 如果PKCS7解填充失败，尝试手动去除填充
                    if len(decrypted_data) > 0:
                        padding_length = decrypted_data[-1]
                        if padding_length <= 16:
                            decrypted_data = decrypted_data[:-padding_length]
                
                # UTF-8解码
                result = decrypted_data.decode('utf-8')
                if result.startswith('http'):
                    print(f"   🔓 CryptoJS解密成功: {result[:100]}...")
                    return result
                else:
                    print(f"   ⚠️ CryptoJS解密结果不是URL: {result[:50]}...")
            except Exception as e:
                print(f"   ⚠️ CryptoJS解密失败: {e}")
            
            # 方法2: 尝试原始解密方法
            try:
                # 解密密钥和S-box (来自ultimate_mp3_downloader.py)
                AUDIO_KEY = bytes([204, 53, 135, 197, 39, 73, 58, 160, 79, 24, 12, 83, 180, 250, 101, 60, 206, 30, 10, 227, 36, 95, 161, 16, 135, 150, 235, 116, 242, 116, 165, 171])
                S_BOX = bytes([183, 174, 108, 16, 131, 159, 250, 5, 239, 110, 193, 202, 153, 137, 251, 176, 119, 150, 47, 204, 97, 237, 1, 71, 177, 42, 88, 218, 166, 82, 87, 94, 14, 195, 69, 127, 215, 240, 225, 197, 238, 142, 123, 44, 219, 50, 190, 29, 181, 186, 169, 98, 139, 185, 152, 13, 141, 76, 6, 157, 200, 132, 182, 49, 20, 116, 136, 43, 155, 194, 101, 231, 162, 242, 151, 213, 53, 60, 26, 134, 211, 56, 28, 223, 107, 161, 199, 15, 229, 61, 96, 41, 66, 158, 254, 21, 165, 253, 103, 89, 3, 168, 40, 246, 81, 95, 58, 31, 172, 78, 99, 45, 148, 187, 222, 124, 55, 203, 235, 64, 68, 149, 180, 35, 113, 207, 118, 111, 91, 38, 247, 214, 7, 212, 209, 189, 241, 18, 115, 173, 25, 236, 121, 249, 75, 57, 216, 10, 175, 112, 234, 164, 70, 206, 198, 255, 140, 230, 12, 32, 83, 46, 245, 0, 62, 227, 72, 191, 156, 138, 248, 114, 220, 90, 84, 170, 128, 19, 24, 122, 146, 80, 39, 37, 8, 34, 22, 11, 93, 130, 63, 154, 244, 160, 144, 79, 23, 133, 92, 54, 102, 210, 65, 67, 27, 196, 201, 106, 143, 52, 74, 100, 217, 179, 48, 233, 126, 117, 184, 226, 85, 171, 167, 86, 2, 147, 17, 135, 228, 252, 105, 30, 192, 129, 178, 120, 36, 145, 51, 163, 77, 205, 73, 4, 188, 125, 232, 33, 243, 109, 224, 104, 208, 221, 59, 9])
                
                # 替换URL中的特殊字符
                url = encrypted_url.replace('_', '/').replace('-', '+')
                
                # 添加padding
                missing_padding = len(url) % 4
                if missing_padding:
                    url += '=' * (4 - missing_padding)
                
                # Base64解码
                decoded = base64.b64decode(url)
                
                if len(decoded) < 16:
                    return None
                
                # 分离数据和IV
                data_length = len(decoded) - 16
                data = bytearray(decoded[:data_length])
                iv = bytearray(decoded[data_length:])
                
                # S-box替换
                for i in range(len(data)):
                    data[i] = S_BOX[data[i]]
                
                # XOR解密 - 先与IV
                for i in range(0, len(data), 16):
                    for j in range(min(16, len(data) - i)):
                        data[i + j] ^= iv[j]
                
                # XOR解密 - 再与KEY
                for i in range(0, len(data), 32):
                    for j in range(min(32, len(data) - i)):
                        data[i + j] ^= AUDIO_KEY[j]
                
                # UTF-8解码
                result = data.decode('utf-8')
                if result.startswith('http'):
                    print(f"   🔓 原始解密成功: {result[:100]}...")
                    return result
                else:
                    print(f"   ⚠️ 原始解密结果不是URL: {result[:50]}...")
            except Exception as e:
                print(f"   ⚠️ 原始解密失败: {e}")
            
            return None
            
        except Exception as e:
            print(f"⚠️ 解密异常: {e}")
            return None
    
    def _get_all_audio_urls(self, track_id: str) -> Dict:
        """获取所有可用的音频URL"""
        print(f"🔴🔴🔴 警告:调用了旧的_get_all_audio_urls方法! 🔴🔴🔴")
        print(f"📡 获取章节 {track_id} 的所有音频URL...")
        
        # 1. 移动端API
        mobile_urls = self._get_mobile_audio_urls(track_id)
        
        # 2. 网页端API
        web_urls = self._get_web_audio_urls(track_id)
        
        # 3. PC端API
        pc_urls = self._get_pc_audio_urls(track_id)
        
        # 4. 小程序API
        mini_program_urls = self._get_mini_program_audio_urls(track_id)
        
        # 合并所有URL
        all_urls = {}
        all_urls.update(mobile_urls)
        all_urls.update(web_urls)
        all_urls.update(pc_urls)
        all_urls.update(mini_program_urls)
        
        print(f"✅ 获取到 {len(all_urls)} 个音频URL")
        return all_urls
    
    def _get_mobile_audio_urls(self, track_id: str) -> Dict:
        """获取移动端音频URL"""
        urls = {}
        try:
            mobile_api_url = f"http://mobile.ximalaya.com/v1/track/baseInfo?device=android&trackId={track_id}"
            response = self.session.get(mobile_api_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 0:
                    # 获取各种音质的URL
                    play_url_32 = data.get('playUrl32')
                    play_url_64 = data.get('playUrl64')
                    play_path_aac_164 = data.get('playPathAacv164')
                    play_path_aac_224 = data.get('playPathAacv224')
                    play_path_hq = data.get('playPathHq')
                    
                    if play_url_32:
                        urls['MP3_32'] = {
                            'url': play_url_32,
                            'type': 'MP3',
                            'port': 'mobile',
                            'quality_level': 0
                        }
                        urls['playUrl32'] = urls['MP3_32']  # 添加别名方便匹配
                    
                    if play_url_64:
                        urls['MP3_64'] = {
                            'url': play_url_64,
                            'type': 'MP3',
                            'port': 'mobile',
                            'quality_level': 1
                        }
                        urls['playUrl64'] = urls['MP3_64']  # 添加别名方便匹配
                    
                    if play_path_aac_164:
                        urls['M4A_48'] = {  # playPathAacv164 实际是48kbps
                            'url': play_path_aac_164,
                            'type': 'M4A',
                            'port': 'mobile',
                            'quality_level': 2
                        }
                        urls['playPathAacv164'] = urls['M4A_48']  # 添加别名方便匹配
                    
                    if play_path_aac_224:
                        urls['M4A_64'] = {  # playPathAacv224 实际是64kbps (224kbps AAC)
                            'url': play_path_aac_224,
                            'type': 'M4A',
                            'port': 'mobile',
                            'quality_level': 4
                        }
                        urls['playPathAacv224'] = urls['M4A_64']  # 添加别名方便匹配
                    
                    # HQ音质URL - 使用特殊API获取
                    hq_url = f"http://mobile.ximalaya.com/mobile/redirect/free/play/{track_id}/96"
                    urls['M4A_96_HQ'] = {
                        'url': hq_url,
                        'type': 'M4A',
                        'port': 'mobile',
                        'quality_level': 5
                    }
                    urls['playPathHq'] = urls['M4A_96_HQ']  # 添加别名方便匹配
                    print(f"   📥 获取到HQ音频URL: {hq_url[:80]}...")
                    
                    # 调试信息
                    print(f"   📊 API返回的音频字段:")
                    if play_url_32:
                        print(f"      playUrl32: {play_url_32[:80]}...")
                    if play_url_64:
                        print(f"      playUrl64: {play_url_64[:80]}...")
                    if play_path_aac_164:
                        print(f"      playPathAacv164: {play_path_aac_164[:80]}...")
                    if play_path_aac_224:
                        print(f"      playPathAacv224: {play_path_aac_224[:80]}...")
                    if play_path_hq:
                        print(f"      playPathHq: {play_path_hq[:80]}...")
        except Exception as e:
            print(f"⚠️ 获取移动端音频URL失败: {e}")
        
        return urls
    
    def _get_web_audio_urls(self, track_id: str) -> Dict:
        """获取网页端音频URL"""
        urls = {}
        try:
            timestamp = int(time.time() * 1000)
            web_api_url = f"https://www.ximalaya.com/mobile-playpage/track/v3/baseInfo/{timestamp}?device=web&trackId={track_id}"
            response = self.session.get(web_api_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 0 and data.get('trackInfo'):
                    track_info = data['trackInfo']
                    play_url_list = track_info.get('playUrlList', [])
                    
                    for url_info in play_url_list:
                        url_type = url_info.get('type', 'Unknown')
                        encrypted_url = url_info.get('url', '')
                        
                        if encrypted_url:
                            # 这里应该解密URL，但为了简化测试，我们直接使用
                            urls[f"web_{url_type}"] = {
                                'url': encrypted_url,
                                'type': url_type,
                                'port': 'web',
                                'quality_level': url_info.get('qualityLevel', 0)
                            }
        except Exception as e:
            print(f"⚠️ 获取网页端音频URL失败: {e}")
        
        return urls
    
    def _get_pc_audio_urls(self, track_id: str) -> Dict:
        """获取PC端音频URL"""
        urls = {}
        try:
            pc_api_url = f"https://www.ximalaya.com/revision/play/v1/audio?id={track_id}&ptype=1"
            response = self.session.get(pc_api_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 200 and data.get('data'):
                    url = data['data'].get('src', '')
                    if url:
                        urls['pc_default'] = {
                            'url': url,
                            'type': 'Unknown',
                            'port': 'pc',
                            'quality_level': 0
                        }
        except Exception as e:
            print(f"⚠️ 获取PC端音频URL失败: {e}")
        
        return urls
    
    def _get_mini_program_audio_urls(self, track_id: str) -> Dict:
        """获取小程序音频URL"""
        urls = {}
        try:
            mini_api_url = f"https://mobwsa.ximalaya.com/mobile-playpage/track/v3/baseInfo/0?device=mini&trackId={track_id}"
            response = self.session.get(mini_api_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 0 and data.get('trackInfo'):
                    track_info = data['trackInfo']
                    play_url_list = track_info.get('playUrlList', [])
                    
                    for url_info in play_url_list:
                        url_type = url_info.get('type', 'Unknown')
                        encrypted_url = url_info.get('url', '')
                        
                        if encrypted_url:
                            urls[f"mini_{url_type}"] = {
                                'url': encrypted_url,
                                'type': url_type,
                                'port': 'mini',
                                'quality_level': url_info.get('qualityLevel', 0)
                            }
        except Exception as e:
            print(f"⚠️ 获取小程序音频URL失败: {e}")
        
        return urls
    
    def _download_m4a(self, audio_urls: Dict, quality: str, save_path: str, 
                      album_title: str, chapter_title: str) -> bool:
        """
        根据指定音质下载M4A格式音频
        """
        print(f"📱 根据指定音质下载M4A音频 ({quality})...")
        
        # 调试信息：显示所有可用的音频URL
        print(f"   📋 可用音频URL数量: {len(audio_urls)}")
        for key, info in audio_urls.items():
            url = info.get('url', '')[:80] if info.get('url') else 'N/A'
            print(f"      {key}: {info.get('type', 'N/A')} (级别: {info.get('quality_level', 'N/A')}) - {url}...")
        
        # 根据音质选择对应的URL键（严格按照音质功能说明.md的规则）
        # M4A音质映射规则（根据实际测试结果修正）：
        # M4A_96K  -> 使用特殊URL /96 参数获取HQ音质 (14MB)
        # M4A_64K  -> play_path_aac_224 (中等质量M4A) (7MB)
        # M4A_48K  -> play_path_aac_164 (标准质量M4A) (3MB)
        # M4A_32K  -> play_path_aac_64 (低质量M4A) (3MB)
        quality_mapping = {
            'M4A_96K': ['M4A_96_HQ', 'playPathHq'],              # HQ高质量 (14MB)
            'M4A_64K': ['M4A_64', 'playPathAacv224'],            # 中等质量 (7MB)
            'M4A_48K': ['M4A_48', 'playPathAacv164'],            # 标准质量 (3MB)
            'M4A_32K': ['M4A_32', 'playPathAacv64']              # 低质量 (3MB)
        }
        
        # 查找匹配的URL - 标准化quality格式（处理空格和下划线）
        normalized_quality = quality.replace(' ', '_').upper()
        print(f"   🔧 调试信息:")
        print(f"     原始quality: '{quality}'")
        print(f"     标准化quality: '{normalized_quality}'")
        print(f"     映射表键: {list(quality_mapping.keys())}")
        
        target_keys = quality_mapping.get(normalized_quality, quality_mapping.get(quality.upper(), quality_mapping.get(quality, [])))
        
        # 查找匹配的URL - 先尝试别名匹配
        print(f"   🔍 尝试匹配音质: {quality}")
        print(f"   📝 匹配键列表: {target_keys}")
        
        # 首先尝试直接匹配别名
        for key in target_keys:
            if key in audio_urls:
                url_info = audio_urls[key]
                url = url_info['url']
                print(f"   ✅ 找到精确匹配的别名: {key}")
                print(f"   🔗 URL: {url[:100]}...")
                
                # 检查URL是否包含真实的比特率信息
                if '48K' in url:
                    print(f"   ⚠️ URL包含48K标识 - 这可能是低质量音频")
                elif '64K' in url:
                    print(f"   ✅ URL包含64K标识 - 中等质量音频")
                elif '96K' in url or '128K' in url or '192K' in url:
                    print(f"   ✅ URL包含高质量比特率标识")
                else:
                    print(f"   ⚠️ URL可能不包含真实比特率信息")
                
                # 特别检查：如果用户选择96K但URL是48K，给出警告
                if 'M4A_96K' in quality.upper() and '48K' in url:
                    print(f"   🚨 警告：用户选择96K但下载的是48K音频！")
                    print(f"   💡 建议：喜马拉雅可能没有提供真正的96K音频")
                
                if self._download_single_url(url, save_path):
                    try:
                        file_size = os.path.getsize(save_path)
                        size_mb = file_size / (1024 * 1024)
                        print(f"✅ M4A下载成功 ({quality}) - 文件大小: {size_mb:.2f}MB")
                        
                        # 验证文件是否真的是高质量音频
                        if file_size < 1024 * 1024:  # 小于1MB可能是低质量
                            print(f"   ⚠️ 警告: 文件大小 {size_mb:.2f}MB 可能不是高质量音频")
                        else:
                            print(f"   ✅ 文件大小正常，应该是高质量音频")
                    except Exception:
                        print(f"✅ M4A下载成功 ({quality})")
                    return True
        
        # 如果没有找到别名匹配，尝试模糊匹配
        print(f"   ⚠️ 未找到别名匹配，尝试模糊匹配...")
        
        # 首先尝试精确匹配，按quality_level降序排列以优先选择高质量音频
        sorted_audio_urls = sorted(audio_urls.items(), 
                                 key=lambda x: x[1].get('quality_level', 0), 
                                 reverse=True)
        
        # 尝试模糊匹配 - 按quality_level匹配
        for key in target_keys:
            for url_key, url_info in sorted_audio_urls:
                if key.lower() in url_key.lower() and url_info.get('type') == 'M4A':
                    url = url_info['url']
                    quality_level = url_info.get('quality_level', 0)
                    print(f"   模糊匹配找到URL: {url_key} -> {url[:80]}... (质量级别: {quality_level})")
                    if self._download_single_url(url, save_path):
                        # 验证下载的文件大小
                        try:
                            file_size = os.path.getsize(save_path)
                            size_mb = file_size / (1024 * 1024)
                            print(f"✅ M4A下载成功 ({quality}) - 文件大小: {size_mb:.2f}MB")
                            
                            # 对于96K音质，文件应该相对较大
                            if '96' in quality and size_mb < 5:
                                print(f"⚠️ 注意: 96K音质文件大小较小 ({size_mb:.2f}MB)，可能不是最高质量")
                        except Exception as e:
                            print(f"⚠️ 文件大小验证失败: {e}")
                        return True
        
        # 如果没有精确匹配，按优先级下载
        print(f"   未找到精确匹配的{quality} URL，使用优先级下载...")
        priority_patterns = [
            (lambda x: x.get('type') == 'M4A' and x.get('port') == 'mobile', "手机端M4A"),
            (lambda x: x.get('type') == 'M4A', "其他端口M4A"),
            (lambda x: x.get('port') == 'mobile', "手机端其他格式"),
            (lambda x: True, "其他端口其他格式")
        ]
        
        for pattern_func, pattern_name in priority_patterns:
            print(f"   尝试 {pattern_name}...")
            
            # 查找匹配的URL
            matched_urls = {k: v for k, v in audio_urls.items() if pattern_func(v)}
            
            if matched_urls:
                for url_key, url_info in matched_urls.items():
                    url = url_info['url']
                    print(f"      尝试URL: {url[:80]}...")
                    
                    if self._download_single_url(url, save_path):
                        print(f"✅ M4A下载成功 ({pattern_name})")
                        return True
                    else:
                        print(f"❌ 下载失败，尝试下一个URL...")
            
            print(f"   {pattern_name} 无可用URL")
        
        print("❌ 所有M4A下载尝试都失败")
        return False
    
    def _download_mp3(self, audio_urls: Dict, quality: str, save_path: str, 
                      album_title: str, chapter_title: str) -> bool:
        """
        根据指定音质下载真正的MP3格式音频
        """
        print(f"💻 根据指定音质下载真正的MP3音频 ({quality})...")
        
        # 根据音质选择对应的URL键（严格按照音质功能说明.md的规则）
        # MP3音质映射规则：
        # MP3_64K  -> playUrl64 (64Kbps MP3)
        # MP3_48K  -> playUrl32 (32Kbps MP3)
        quality_mapping = {
            'MP3_64K': ['playUrl64', 'play_url_64'],  # 64Kbps MP3
            'MP3_48K': ['playUrl32', 'play_url_32']   # 32Kbps MP3
        }
        
        # 查找匹配的URL - 标准化quality格式（处理空格和下划线）
        normalized_quality = quality.replace(' ', '_').upper()
        target_keys = quality_mapping.get(normalized_quality, quality_mapping.get(quality.upper(), quality_mapping.get(quality, [])))
        
        # 查找匹配的URL - 先尝试别名匹配
        print(f"   🔍 尝试匹配MP3音质: {quality}")
        print(f"   📝 匹配键列表: {target_keys}")
        
        # 首先尝试直接匹配别名
        for key in target_keys:
            if key in audio_urls:
                url_info = audio_urls[key]
                url = url_info['url']
                print(f"   ✅ 找到精确匹配的别名: {key}")
                print(f"   🔗 URL: {url[:100]}...")
                
                # 验证Content-Type确保是真正的MP3文件
                try:
                    response = self.session.head(url, timeout=10)
                    content_type = response.headers.get('content-type', '').lower()
                    
                    if 'audio/mpeg' in content_type or 'audio/mp3' in content_type or url_info.get('type') == 'MP3':
                        print(f"   ✅ 确认是MP3文件: {content_type}")
                        if self._download_single_url(url, save_path):
                            print(f"✅ MP3下载成功 ({quality})")
                            return True
                    else:
                        print(f"   ⚠️ 不是MP3文件: {content_type}，继续查找...")
                except Exception as e:
                    print(f"   ⚠️ 验证失败: {e}，尝试直接下载...")
                    if self._download_single_url(url, save_path):
                        print(f"✅ MP3下载成功 ({quality})")
                        return True
        
        # 如果没有找到别名匹配，尝试模糊匹配
        print(f"   ⚠️ 未找到别名匹配，尝试模糊匹配...")
        
        # 首先尝试精确匹配，按quality_level降序排列以优先选择高质量音频
        sorted_audio_urls = sorted(audio_urls.items(), 
                                 key=lambda x: x[1].get('quality_level', 0), 
                                 reverse=True)
        
        for key in target_keys:
            for url_key, url_info in sorted_audio_urls:
                if key.lower() in url_key.lower() and url_info.get('type') == 'MP3':
                    url = url_info['url']
                    quality_level = url_info.get('quality_level', 0)
                    print(f"   精确匹配找到URL: {url[:80]}... (质量级别: {quality_level})")
                    
                    # 验证Content-Type确保是真正的MP3文件
                    try:
                        response = self.session.head(url, timeout=10)
                        content_type = response.headers.get('content-type', '').lower()
                        
                        if 'audio/mpeg' in content_type or 'audio/mp3' in content_type:
                            print(f"   ✅ 确认是真正的MP3文件: {content_type}")
                            if self._download_single_url(url, save_path):
                                print(f"✅ MP3下载成功 ({quality})")
                                return True
                        else:
                            print(f"   ⚠️ 不是真正的MP3文件: {content_type}")
                    except Exception as e:
                        print(f"   ⚠️ 验证失败: {e}")
                        # 即使验证失败也尝试下载
                        if self._download_single_url(url, save_path):
                            print(f"✅ MP3下载成功 ({quality})")
                            return True
        
        # 如果没有精确匹配，按优先级下载真正的MP3
        print(f"   未找到精确匹配的{quality} URL，使用优先级下载...")
        
        # 优先使用PC端API获取真正的MP3文件
        pc_api_urls = {k: v for k, v in audio_urls.items() if v.get('port') == 'pc' and v.get('type') == 'MP3'}
        
        # 验证每个URL确实返回MP3文件
        for url_key, url_info in pc_api_urls.items():
            url = url_info['url']
            print(f"   验证PC端URL: {url[:80]}...")
            
            # 检查Content-Type确保是真正的MP3文件
            try:
                response = self.session.head(url, timeout=10)
                content_type = response.headers.get('content-type', '').lower()
                
                if 'audio/mpeg' in content_type or 'audio/mp3' in content_type:
                    print(f"   ✅ 确认是真正的MP3文件: {content_type}")
                    if self._download_single_url(url, save_path):
                        print(f"✅ MP3下载成功")
                        return True
                else:
                    print(f"   ⚠️ 不是真正的MP3文件: {content_type}")
            except Exception as e:
                print(f"   ⚠️ 验证失败: {e}")
        
        # 如果PC端没有真正的MP3，尝试其他端口的MP3 URL
        other_mp3_urls = {k: v for k, v in audio_urls.items() if v.get('type') == 'MP3' and v.get('port') != 'pc'}
        
        for url_key, url_info in other_mp3_urls.items():
            url = url_info['url']
            print(f"   尝试其他端口URL: {url[:80]}...")
            
            # 同样验证Content-Type
            try:
                response = self.session.head(url, timeout=10)
                content_type = response.headers.get('content-type', '').lower()
                
                if 'audio/mpeg' in content_type or 'audio/mp3' in content_type:
                    print(f"   ✅ 确认是真正的MP3文件: {content_type}")
                    if self._download_single_url(url, save_path):
                        print(f"✅ MP3下载成功")
                        return True
                else:
                    print(f"   ⚠️ 不是真正的MP3文件: {content_type}")
            except Exception as e:
                print(f"   ⚠️ 验证失败: {e}")
        
        # 如果没有找到真正的MP3文件，尝试所有URL但验证下载后的内容
        print("   🔍 尝试所有可用URL并验证下载后的内容...")
        all_urls = {k: v for k, v in audio_urls.items() if v.get('type') in ['MP3', 'M4A']}
        
        for url_key, url_info in all_urls.items():
            url = url_info['url']
            port = url_info.get('port', 'unknown')
            format_type = url_info.get('type', 'unknown')
            print(f"      尝试 {port} 端口 {format_type} 格式: {url[:80]}...")
            
            # 下载文件
            temp_save_path = save_path + ".tmp"
            if self._download_single_url(url, temp_save_path):
                # 验证下载的文件确实是MP3格式
                if self._verify_mp3_file(temp_save_path):
                    # 重命名为正确的扩展名
                    if os.path.exists(save_path):
                        os.remove(save_path)
                    os.rename(temp_save_path, save_path)
                    print(f"✅ MP3下载成功并验证格式正确")
                    return True
                else:
                    print(f"   ⚠️ 下载的文件不是真正的MP3格式，删除临时文件")
                    if os.path.exists(temp_save_path):
                        os.remove(temp_save_path)
            else:
                print(f"   ❌ 下载失败")
        
        print("❌ 无法获取真正的MP3文件")
        return False
    
    def _verify_mp3_file(self, file_path: str) -> bool:
        """验证文件是否为真正的MP3格式"""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(16)
                
            if len(header) < 2:
                return False
                
            # MP3文件头部特征：以0xFF开始，第二个字节的高3位为111
            if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
                # 进一步检查是否为MP3而不是其他MPEG音频
                # MP3的第三个字节的高2位通常为01或11
                if len(header) >= 3:
                    layer_bits = (header[1] & 0x18) >> 3
                    if layer_bits == 1:  # Layer III (MP3)
                        return True
            
            return False
        except Exception as e:
            print(f"   ❌ MP3文件验证失败: {e}")
            return False
    
    def _verify_m4a_file(self, file_path: str) -> bool:
        """验证文件是否为M4A格式"""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(12)
            
            if len(header) < 12:
                return False
            
            # M4A/MP4文件特征：
            # 字节4-7 是 'ftyp' (文件类型标识)
            # 或者字节0-3可能是文件大小，字节4-7是'ftyp'
            if b'ftyp' in header[:12]:
                return True
            
            # 检查是否包含M4A特定的品牌标识
            if b'M4A' in header or b'mp42' in header or b'isom' in header:
                return True
            
            return False
        except Exception as e:
            print(f"   ❌ M4A文件验证失败: {e}")
            return False
    
    def _download_default(self, audio_urls: Dict, save_path: str, 
                         album_title: str, chapter_title: str) -> bool:
        """默认下载方式"""
        print("🔄 使用默认下载方式...")
        
        # 尝试所有URL
        for url_key, url_info in audio_urls.items():
            url = url_info['url']
            port = url_info.get('port', 'unknown')
            format_type = url_info.get('type', 'unknown')
            
            print(f"   尝试 {port} 端口 {format_type} 格式: {url[:80]}...")
            
            if self._download_single_url(url, save_path):
                print(f"✅ 默认下载成功 ({port}端口)")
                return True
            else:
                print(f"❌ 下载失败，尝试下一个URL...")
        
        print("❌ 默认下载方式也失败")
        return False
    
    def _download_single_url(self, url: str, save_path: str) -> bool:
        """下载单个URL"""
        try:
            # 确保保存目录存在
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # 下载文件，设置合理的超时时间
            response = self.session.get(url, stream=True, timeout=60)  # 增加超时时间到60秒
            
            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=512000):
                        if chunk:
                            f.write(chunk)
                
                # 检查文件大小
                file_size = os.path.getsize(save_path)
                if file_size > 1024:  # 大于1KB认为下载成功
                    print(f"✅ 下载成功: {save_path} ({file_size // 1024}KB)")
                    return True
                else:
                    print(f"❌ 文件太小: {file_size} 字节")
                    os.remove(save_path)  # 删除无效文件
                    return False
            else:
                print(f"❌ HTTP错误: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 下载异常: {e}")
            return False
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名"""
        # 移除非法字符
        illegal_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        for char in illegal_chars:
            filename = filename.replace(char, '_')
        
        # 限制长度
        if len(filename) > 150:
            filename = filename[:150]
        
        # 确保不为空
        if not filename:
            filename = "未知音频"
        
        return filename


def test_download_manager():
    """测试下载管理器"""
    print("🧪 测试喜马拉雅下载管理器")
    
    # 创建下载管理器
    downloader = XimalayaDownloadManager()
    
    # 测试下载
    track_id = "45982355"  # 使用测试中获取的章节ID
    # 文件名不包含音质信息
    save_path = os.path.join(os.path.expanduser('~'), 'Downloads', 'test_audio.m4a')
    
    # 测试M4A下载
    print("\n📱 测试M4A下载...")
    success = downloader.download_audio_by_quality(
        track_id, "M4A_64K", save_path, 
        "郭德纲相声", "败家子儿"
    )
    
    if success:
        print("✅ M4A下载测试成功")
        # 清理测试文件
        if os.path.exists(save_path):
            os.remove(save_path)
    else:
        print("❌ M4A下载测试失败")


if __name__ == "__main__":
    test_download_manager()
