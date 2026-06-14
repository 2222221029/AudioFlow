#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""荔枝FM播客管理器。

基于荔枝 Web/App 公开接口接入主播主页节目列表、播放直链和下载。
"""

import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter


class LizhiManager:
    """荔枝FM API 管理器。"""

    platform_name = "荔枝FM"

    def __init__(self):
        self.base_url = "https://m.lizhi.fm"
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=1)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "Accept": "*/*",
            "Referer": "https://m.lizhi.fm/",
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                "MicroMessenger/8.0.60 NetType/WIFI Language/zh_CN"
            ),
        })
        print("[荔枝FM] 管理器初始化完成")

    def parse_user_id(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.isdigit():
            return text
        match = re.search(r"(?:lizhi\.fm|lizhifm\.com)/(?:vod/)?user/(\d+)", text)
        if match:
            return match.group(1)
        match = re.search(r"(?:userId|jockeyId|uid)=([0-9]+)", text)
        if match:
            return match.group(1)
        return ""

    def _request_json(self, path: str, params: Optional[Dict] = None) -> Dict:
        url = path if str(path).startswith("http") else f"{self.base_url}{path}"
        response = self.session.get(url, params=params or {}, timeout=25)
        response.raise_for_status()
        return response.json()

    def _extract_user_from_page(self, user_id: str) -> Dict:
        try:
            response = self.session.get(f"https://www.lizhi.fm/user/{user_id}", timeout=25)
            response.raise_for_status()
            html = response.text
            user_match = re.search(
                r'\\"userInfo\\":\{.*?\\"name\\":\\"(?P<name>.*?)\\".*?\\"photo\\":\\"(?P<photo>.*?)\\".*?\\"userId\\":\\"(?P<user_id>\d+)\\".*?\\"band\\":\\"(?P<band>.*?)\\"',
                html,
            )
            if user_match:
                return {
                    "userId": user_match.group("user_id"),
                    "name": bytes(user_match.group("name"), "utf-8").decode("unicode_escape"),
                    "photo": user_match.group("photo").replace("\\/", "/"),
                    "band": user_match.group("band"),
                }
        except Exception as exc:
            print(f"[荔枝FM] 网页用户信息解析失败: {exc}")
        return {}

    def get_user_info(self, user_id: str) -> Dict:
        user_id = str(user_id or "").strip()
        if not user_id:
            return {}
        for params in ({"userId": user_id}, {"id": user_id}):
            try:
                data = self._request_json("/vodapi/user/infoById", params)
                payload = data.get("data") or {}
                if isinstance(payload, dict) and payload:
                    return payload
            except Exception:
                pass
        return self._extract_user_from_page(user_id)

    def _normalize_item(self, item: Dict, index: int = 0) -> Optional[Dict]:
        if not isinstance(item, dict):
            return None
        user = item.get("userInfo") or {}
        info = item.get("voiceInfo") or {}
        play = item.get("voicePlayProperty") or {}
        extra = item.get("voiceExProperty") or {}
        detail = item.get("voiceDetailProperty") or {}
        audio_url = (
            play.get("trackUrl")
            or play.get("voiceUrl")
            or info.get("trackUrl")
            or info.get("voiceTrack")
            or info.get("voiceUrl")
            or item.get("voiceTrack")
            or ""
        )
        voice_id = info.get("voiceId") or item.get("voiceId")
        title = info.get("name") or item.get("name") or item.get("voiceTitle")
        if not voice_id or not title:
            return None
        duration = int(info.get("duration") or item.get("duration") or 0)
        return {
            "id": str(voice_id),
            "chapter_id": str(voice_id),
            "title": str(title),
            "name": str(title),
            "index": index,
            "order": index,
            "duration": duration,
            "duration_str": self._format_duration(duration),
            "url": audio_url,
            "audio_url": audio_url,
            "downloadUrl": audio_url,
            "mediaUrl": audio_url,
            "cover": info.get("imageUrl") or info.get("voiceCover") or user.get("photo") or "",
            "created_at": info.get("createTime") or "",
            "plays": extra.get("replayCount") or item.get("playCount") or 0,
            "description": detail.get("text") or "",
            "label": info.get("lableName") or info.get("labelName") or "",
            "platform": self.platform_name,
            "_raw": item,
        }

    @staticmethod
    def _format_duration(seconds: int) -> str:
        try:
            seconds = int(seconds or 0)
        except Exception:
            seconds = 0
        if seconds <= 0:
            return ""
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def get_chapters(self, user_id: str, page: int = 1, page_size: int = 500, max_pages: Optional[int] = None) -> List[Dict]:
        user_id = self.parse_user_id(user_id) or str(user_id or "").strip()
        if not user_id:
            return []
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 500), 500))
        chapters: List[Dict] = []
        current = page
        while current <= 200:
            data = self._request_json(f"/vodapi/user/{user_id}", {"pageNo": current, "pageSize": page_size})
            if str(data.get("code")) not in ("0", ""):
                print(f"[荔枝FM] 章节接口返回异常: {data.get('msg') or data.get('message')}")
                break
            items = data.get("data") or []
            if not isinstance(items, list) or not items:
                break
            offset = len(chapters)
            for idx, item in enumerate(items, 1):
                chapter = self._normalize_item(item, offset + idx)
                if chapter:
                    chapters.append(chapter)
            if len(items) < page_size:
                break
            current += 1
            if max_pages is not None and (current - page) >= max_pages:
                break
            time.sleep(0.08)
        return chapters

    def get_book_detail(self, user_id: str) -> Optional[Dict]:
        user_id = self.parse_user_id(user_id) or str(user_id or "").strip()
        if not user_id:
            return None
        chapters = self.get_chapters(user_id, page=1, page_size=500, max_pages=1)
        first_raw = (chapters[0].get("_raw") or {}) if chapters else {}
        user = first_raw.get("userInfo") or self.get_user_info(user_id) or {}
        title = user.get("name") or f"荔枝FM播客_{user_id}"
        cover = user.get("photo") or (chapters[0].get("cover") if chapters else "")
        return {
            "id": str(user_id),
            "album_id": str(user_id),
            "title": title,
            "author": title,
            "anchor": title,
            "platform": self.platform_name,
            "cover": cover,
            "episodes": int(user.get("audioNum") or 0) or max(len(chapters), 0),
            "plays": user.get("playCnt") or 0,
            "status": "连载中",
            "description": user.get("signature") or "",
            "source_url": f"https://www.lizhi.fm/user/{user_id}",
            "lizhi_user_id": str(user_id),
            "band": user.get("band") or "",
        }

    def search_books(self, keyword: str, limit: int = 20) -> List[Dict]:
        text = str(keyword or "").strip()
        if not text:
            return []
        user_id = self.parse_user_id(text)
        if not user_id:
            # 荔枝当前 Web 接口没有通用关键词搜索；仅接入 EXE 使用的主页/用户 ID 分析方式。
            return []
        detail = self.get_book_detail(user_id)
        return [detail] if detail else []

    def get_audio_url(self, user_id: str, voice_id: str = "") -> str:
        target = str(voice_id or "").strip()
        if not target:
            return ""
        if target.startswith(("http://", "https://")):
            return target
        for chapter in self.get_chapters(user_id, page=1, page_size=500):
            if str(chapter.get("id")) == target:
                return chapter.get("audio_url") or ""
        return ""

    def download_audio(self, url: str, save_path: str, quality: Optional[str] = None, progress_callback=None) -> bool:
        if not url:
            return False
        headers = {
            "User-Agent": self.session.headers.get("User-Agent", ""),
            "Referer": "https://m.lizhi.fm/",
        }
        try:
            Path(os.path.dirname(save_path)).mkdir(parents=True, exist_ok=True)
            with self.session.get(url, headers=headers, stream=True, timeout=(10, 180)) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                done = 0
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if progress_callback:
                            progress_callback(done, total)
            return os.path.exists(save_path) and os.path.getsize(save_path) > 1024
        except Exception as exc:
            print(f"[荔枝FM] 下载失败: {exc}")
            try:
                if os.path.exists(save_path):
                    os.remove(save_path)
            except OSError:
                pass
            return False
