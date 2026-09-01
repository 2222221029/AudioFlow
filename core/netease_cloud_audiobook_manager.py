#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网易云听书（播客/电台）管理器。

实现网易云 Web ``weapi`` 请求，用于搜索播客、读取节目列表、解析播放地址和下载。
"""

import base64
import json
import os
import random
import re
import string
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


class NeteaseCloudAudiobookManager:
    """网易云播客/听书 API 管理器。"""

    base_url = "https://music.163.com"
    program_page_limit = 500
    max_program_pages = 200
    program_page_attempts = 3
    program_page_retry_delays = (0.35, 0.8)
    preset_key = "0CoJUm6Qyw8W8jud"
    iv = "0102030405060708"
    rsa_exponent = "010001"
    rsa_modulus = (
        "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
        "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
        "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee25593"
        "2575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935"
        "b3ece0462db0a22b8e7"
    )

    def __init__(self):
        self.cookie_string = ""
        self.csrf_token = ""
        self.session = requests.Session()
        self._session_lock = threading.RLock()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com/",
            "Content-Type": "application/x-www-form-urlencoded",
        })

    def set_cookie(self, cookie: str):
        self.cookie_string = str(cookie or "").strip()
        if self.cookie_string:
            self.session.headers["Cookie"] = self.cookie_string
        else:
            self.session.headers.pop("Cookie", None)
        self.csrf_token = self._extract_csrf_token(self.cookie_string)
        print(f"🍪 网易云听书 Cookie 已设置: {len(self.cookie_string)} 字符")

    def _require_cookie(self):
        if not self.cookie_string:
            raise RuntimeError("网易云听书需要先在账号管理中保存自己的网易云音乐 Cookie，会员/VIP播客会按该账号权限解析。")

    @staticmethod
    def _extract_csrf_token(cookie: str) -> str:
        match = re.search(r"__csrf=([^;]+)", cookie or "")
        return match.group(1) if match else ""

    @staticmethod
    def _aes_encrypt(text: str, key: str) -> str:
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, NeteaseCloudAudiobookManager.iv.encode("utf-8"))
        encrypted = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    def _rsa_encrypt(self, text: str) -> str:
        reversed_text = text[::-1]
        number = pow(int(reversed_text.encode("utf-8").hex(), 16), int(self.rsa_exponent, 16), int(self.rsa_modulus, 16))
        return format(number, "x").zfill(256)

    def _encrypt_params(self, payload: Dict) -> Dict[str, str]:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        sec_key = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
        enc_text = self._aes_encrypt(self._aes_encrypt(text, self.preset_key), sec_key)
        enc_sec_key = self._rsa_encrypt(sec_key)
        return {"params": enc_text, "encSecKey": enc_sec_key}

    def _post_weapi(self, path: str, payload: Dict, timeout: int = 20) -> Dict:
        if self.csrf_token and "csrf_token" not in payload:
            payload = dict(payload)
            payload["csrf_token"] = self.csrf_token
        url = self.base_url + path
        with self._session_lock:
            response = self.session.post(url, data=self._encrypt_params(payload), timeout=timeout)
            response.raise_for_status()
            return response.json()

    def validate_cookie(self) -> Dict:
        try:
            response = self.session.get(f"{self.base_url}/api/nuser/account/get", timeout=15)
            data = response.json()
            profile = data.get("profile") or {}
            return {
                "ok": bool(profile),
                "nickname": profile.get("nickname", ""),
                "user_id": profile.get("userId", ""),
                "raw": data,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def search_books(self, keyword: str, limit: int = 30) -> List[Dict]:
        self._require_cookie()
        keyword = str(keyword or "").strip()
        if not keyword:
            return []
        if self._looks_like_id(keyword):
            detail = self.get_book_detail(keyword)
            return [detail] if detail else []
        if keyword.startswith(("http://", "https://")):
            radio_id = self.parse_radio_id(keyword)
            if radio_id:
                detail = self.get_book_detail(radio_id)
                return [detail] if detail else []

        print(f"🔍 网易云听书搜索: {keyword}")
        data = self._post_weapi("/weapi/search/get", {
            "s": keyword,
            "type": 1009,
            "limit": limit,
            "offset": 0,
            "total": True,
        })
        result = data.get("result") or {}
        radios = result.get("djRadios") or result.get("djradios") or []
        books = [self._normalize_radio(item) for item in radios if isinstance(item, dict)]
        print(f"✅ 网易云听书找到 {len(books)} 个播客")
        return [book for book in books if book.get("id")]

    def get_book_detail(self, radio_id: str) -> Optional[Dict]:
        self._require_cookie()
        radio_id = str(radio_id or "").strip()
        if not radio_id:
            return None
        chapters, total = self.get_chapters_page(radio_id, page=1, page_size=1)
        if chapters:
            raw_radio = chapters[0].get("_radio") or {}
            return self._normalize_radio(raw_radio, fallback_id=radio_id, episode_count=total or len(chapters))
        return {
            "id": radio_id,
            "title": f"网易云播客_{radio_id}",
            "author": "",
            "platform": "网易云听书",
            "cover": "",
            "plays": 0,
            "episodes": 0,
            "status": "连载中",
            "description": f"网易云听书播客 ID: {radio_id}",
        }

    def get_chapters(
        self,
        radio_id: str,
        page: int = 1,
        page_size: int = 1000,
        expected_total: int = 0,
    ) -> List[Dict]:
        chapters, _total = self.get_chapters_page(
            radio_id,
            page=page,
            page_size=page_size,
            expected_total=expected_total,
        )
        return chapters

    def get_chapters_page(
        self,
        radio_id: str,
        page: int = 1,
        page_size: int = 100,
        expected_total: int = 0,
    ) -> tuple[List[Dict], int]:
        self._require_cookie()
        radio_id = str(radio_id or "").strip()
        if not radio_id:
            return [], 0
        print(f"📚 获取网易云听书节目列表: {radio_id}")
        page = max(1, int(page or 1))
        limit = min(max(int(page_size or 1), 1), self.program_page_limit)
        offset = (page - 1) * limit
        data = self._fetch_program_page(
            radio_id,
            offset=offset,
            limit=limit,
            expected_total=expected_total,
        )
        programs = list(data.get("programs") or [])
        chapters = [self._normalize_program(item, radio_id, offset + idx) for idx, item in enumerate(programs, 1)]
        total = self._program_total(data, programs)
        print(f"✅ 网易云听书章节加载完成，第 {page} 页 {len(chapters)} 章，总数 {total or '未知'}")
        return chapters, total

    def get_all_chapters(self, radio_id: str, page_size: int = 500) -> List[Dict]:
        """Load every program without treating NetEase's 500-item cap as EOF."""
        self._require_cookie()
        radio_id = str(radio_id or "").strip()
        if not radio_id:
            return []
        limit = min(max(int(page_size or self.program_page_limit), 1), self.program_page_limit)
        chapters = []
        seen_ids = set()
        offset = 0
        total = 0

        print(f"📚 获取网易云听书完整节目列表: {radio_id}")
        for page in range(1, self.max_program_pages + 1):
            data = self._fetch_program_page(radio_id, offset=offset, limit=limit)
            programs = [item for item in (data.get("programs") or []) if isinstance(item, dict)]
            if not programs:
                break
            total = max(total, self._program_total(data, programs))
            added = 0
            for index, item in enumerate(programs, start=1):
                chapter = self._normalize_program(item, radio_id, offset + index)
                identity = str(chapter.get("id") or chapter.get("netease_program_id") or "")
                if identity and identity in seen_ids:
                    continue
                if identity:
                    seen_ids.add(identity)
                chapters.append(chapter)
                added += 1
            if page == 1 or page % 10 == 0:
                print(f"📚 网易云听书目录扫描进度: 第 {page} 页，已加载 {len(chapters)}/{total or '?'} 章")
            if not added:
                print("⚠️ 网易云听书返回了重复分页，停止目录扫描以避免无限循环")
                break
            offset += len(programs)
            more = data.get("more") if "more" in data else None
            if total and len(chapters) >= total:
                break
            if more is False:
                break
            if len(programs) < limit and more is not True:
                break
        print(f"✅ 网易云听书完整目录加载完成: {len(chapters)} 章")
        return chapters

    @staticmethod
    def _program_total(data: Dict, programs: List[Dict]) -> int:
        candidates = [data.get("count"), data.get("total"), data.get("programCount")]
        if programs:
            radio = programs[0].get("radio") if isinstance(programs[0].get("radio"), dict) else {}
            candidates.extend((radio.get("programCount"), radio.get("programsCount")))
        for value in candidates:
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        return 0

    def _fetch_program_page(
        self,
        radio_id: str,
        offset: int = 0,
        limit: int = 1000,
        expected_total: int = 0,
    ) -> Dict:
        request_data = {
            "radioId": str(radio_id),
            "limit": int(limit),
            "offset": int(offset),
            "asc": True,
        }
        last_data = {}
        last_error = None
        for attempt in range(1, self.program_page_attempts + 1):
            try:
                data = self._post_weapi(
                    "/weapi/dj/program/byradio",
                    request_data,
                    timeout=30,
                )
                if not isinstance(data, dict):
                    raise RuntimeError("网易云节目接口返回格式异常")
                code = str(data.get("code") or "200")
                if code != "200":
                    message = str(data.get("message") or data.get("msg") or "未知错误")
                    raise RuntimeError(f"网易云节目接口返回 {code}：{message}")
                last_data = data
                programs = data.get("programs")
                if isinstance(programs, list) and programs:
                    return data
                if (
                    isinstance(programs, list)
                    and data.get("more") is False
                    and self._program_total(data, programs) == 0
                    and int(expected_total or 0) <= 0
                ):
                    return data
                last_error = None
            except requests.RequestException as exc:
                last_error = exc

            if attempt >= self.program_page_attempts:
                break
            delay = self.program_page_retry_delays[min(attempt - 1, len(self.program_page_retry_delays) - 1)]
            print(f"⚠️ 网易云节目页暂未返回有效数据，{delay:.2f} 秒后重试（{attempt + 1}/{self.program_page_attempts}）")
            time.sleep(delay)

        if last_error is not None:
            raise last_error
        if int(expected_total or 0) > 0 and not (last_data.get("programs") or []):
            raise RuntimeError(
                f"网易云专辑应有 {int(expected_total)} 期节目，但连续 {self.program_page_attempts} 次未返回章节"
            )
        return last_data

    def get_audio_url(self, program_id: str, level: str = "exhigh") -> Optional[str]:
        self._require_cookie()
        program_id = str(program_id or "").strip()
        if not program_id:
            return None
        song_id = self._get_program_song_id(program_id)
        if not song_id:
            print(f"❌ 网易云听书节目 {program_id} 未找到 mainSong")
            return None
        data = self._post_weapi("/weapi/song/enhance/player/url/v1", {
            "ids": f"[{song_id}]",
            "level": level or "exhigh",
            "encodeType": "mp3",
        })
        items = data.get("data") or []
        if items and isinstance(items[0], dict):
            return items[0].get("url") or ""
        return None

    def get_download_info(self, program_id: str, level: str = "exhigh") -> Optional[Dict]:
        url = self.get_audio_url(program_id, level)
        if not url:
            return None
        ext = ".mp3"
        lower = url.lower()
        if ".m4a" in lower:
            ext = ".m4a"
        elif ".flac" in lower:
            ext = ".flac"
        return {"url": url, "format": ext.lstrip("."), "extension": ext}

    def _get_program_song_id(self, program_id: str) -> str:
        data = self._post_weapi("/weapi/dj/program/detail", {"id": str(program_id)})
        program = data.get("program") or {}
        main_song = program.get("mainSong") or {}
        return str(main_song.get("id") or "")

    def download_audio(self, url: str, save_path: str, progress_callback=None) -> bool:
        self._require_cookie()
        try:
            Path(os.path.dirname(save_path)).mkdir(parents=True, exist_ok=True)
            response = self.session.get(url, stream=True, timeout=(10, 90), headers={
                "User-Agent": self.session.headers.get("User-Agent", ""),
                "Referer": "https://music.163.com/",
            })
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(save_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        fh.write(chunk)
                        done += len(chunk)
                        if progress_callback:
                            progress_callback(done, total)
            ok = os.path.getsize(save_path) > 1024
            if not ok:
                os.remove(save_path)
            return ok
        except Exception as exc:
            print(f"❌ 网易云听书下载失败: {exc}")
            return False

    @staticmethod
    def _looks_like_id(value: str) -> bool:
        return bool(re.fullmatch(r"\d{4,}", value or ""))

    @staticmethod
    def parse_radio_id(value: str) -> str:
        text = str(value or "")
        for pattern in (
            r"[?&]id=(\d+)",
            r"/djradio\?id=(\d+)",
            r"/djradio/(\d+)",
            r"/radio\?id=(\d+)",
            r"radioId=(\d+)",
        ):
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return text if NeteaseCloudAudiobookManager._looks_like_id(text) else ""

    @staticmethod
    def _normalize_radio(item: Dict, fallback_id: str = "", episode_count: int = 0) -> Dict:
        radio_id = str(item.get("id") or item.get("radioId") or item.get("djRadioId") or fallback_id or "")
        dj = item.get("dj") if isinstance(item.get("dj"), dict) else {}
        return {
            "id": radio_id,
            "title": item.get("name") or item.get("radioName") or item.get("title") or f"网易云播客_{radio_id}",
            "author": item.get("djName") or item.get("creatorName") or dj.get("nickname") or "",
            "platform": "网易云听书",
            "cover": item.get("picUrl") or item.get("picUrlStr") or item.get("coverUrl") or item.get("cover") or "",
            "plays": item.get("playCount") or item.get("subCount") or 0,
            "episodes": item.get("programCount") or item.get("programsCount") or episode_count or 0,
            "status": "连载中",
            "description": item.get("desc") or item.get("description") or "",
            "category": item.get("category") or "",
            "tags": item.get("secondCategory") or [],
            "netease_radio_id": radio_id,
        }

    @staticmethod
    def _normalize_program(item: Dict, radio_id: str, index: int) -> Dict:
        main_song = item.get("mainSong") if isinstance(item.get("mainSong"), dict) else {}
        radio = item.get("radio") if isinstance(item.get("radio"), dict) else {}
        duration = item.get("duration") or main_song.get("duration") or 0
        return {
            "id": str(item.get("id") or item.get("programId") or ""),
            "title": item.get("name") or main_song.get("name") or f"第 {index} 期",
            "duration": duration,
            "duration_ms": duration,
            "size": "",
            "plays": item.get("listenerCount") or item.get("shareCount") or 0,
            "album": radio_id,
            "order_num": index,
            "netease_program_id": str(item.get("id") or item.get("programId") or ""),
            "netease_song_id": str(main_song.get("id") or ""),
            "cover": item.get("coverUrl") or item.get("blurCoverUrl") or radio.get("picUrl") or "",
            "_radio": radio,
        }


def get_netease_cloud_audiobook_manager() -> NeteaseCloudAudiobookManager:
    return NeteaseCloudAudiobookManager()
