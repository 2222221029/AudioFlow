#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄听书管理器 — 封装 core/vendor/fanqie_portable.py 的听书搜索/音色/章节/下载逻辑
（番茄小说 App 听书 tab，含 Gorgon 签名与 CENC 解密）
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .app_paths import app_root

PLATFORM_NAME = "番茄听书"

_BOOK_ID_URL_RE = re.compile(
    r"(?:fanqienovel\.com/page/|fanqie(?:novel|audio)\.com/book/|book_id[=:])(\d{5,})",
    re.I,
)


def parse_book_id(keyword: str) -> Optional[str]:
    """从纯数字或番茄小说链接中解析书籍 ID。"""
    text = (keyword or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    m = _BOOK_ID_URL_RE.search(text)
    if m:
        return m.group(1)
    m = re.search(r"/page/(\d{5,})", text)
    return m.group(1) if m else None

_module_cache = None
_module_lock = threading.Lock()


def _load_wanzheng_module():
    global _module_cache
    if _module_cache is not None:
        return _module_cache
    with _module_lock:
        if _module_cache is not None:
            return _module_cache
        root = app_root()
        candidates = [
            root / "core" / "vendor" / "fanqie_portable.py",
        ]
        script = next((p for p in candidates if p.is_file()), None)
        if script is None:
            raise FileNotFoundError("未找到番茄参考脚本: core/vendor/fanqie_portable.py")
        spec = importlib.util.spec_from_file_location("fanqie_wanzhengban", script)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules["fanqie_wanzhengban"] = mod
        spec.loader.exec_module(mod)
        _patch_search_extract(mod)
        _module_cache = mod
        return mod


def _fmt_duration(seconds: int) -> str:
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        s = 0
    if s <= 0:
        return "00:00"
    return f"{s // 60:02d}:{s % 60:02d}"


def _fmt_size(size: int) -> str:
    try:
        n = int(size or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return "0MB"
    if n < 1024 * 1024:
        return f"{n // 1024}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _normalize_cover_url(url: str) -> str:
    if not url:
        return ""
    u = str(url).strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _cover_from_detail(detail: Optional[Dict]) -> str:
    if not detail:
        return ""
    for key in (
        "thumb_url", "cover", "audio_thumb_uri", "thumb_uri",
        "book_cover", "cover_url", "horiz_thumb_url",
    ):
        val = detail.get(key)
        if val:
            return _normalize_cover_url(val)
    return ""


def _decode_json_escaped_string(value: str) -> str:
    if not value:
        return ""
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        return value.replace("\\/", "/")


def _patch_search_extract(mod) -> None:
    """搜索 API 的 book_data 含封面字段，补全提取逻辑。"""
    if getattr(mod, "_tingshu_extract_patched", False):
        return

    def _extract_books_from_tab(self, tab: dict) -> list:
        rows: list = []
        seen: set = set()
        tab_title = tab.get("title") or ""
        for cell in tab.get("data") or []:
            if not isinstance(cell, dict):
                continue
            stack = [cell]
            while stack:
                c = stack.pop()
                for b in c.get("book_data") or []:
                    if not isinstance(b, dict):
                        continue
                    bid = str(b.get("book_id") or "")
                    if not bid or bid in seen:
                        continue
                    seen.add(bid)
                    thumb = (
                        b.get("thumb_url") or b.get("thumbUrl")
                        or b.get("audio_thumb_uri") or b.get("thumb_uri")
                        or b.get("cover") or b.get("horiz_thumb_url") or ""
                    )
                    rows.append({
                        "book_id": bid,
                        "book_name": (b.get("book_name") or b.get("title") or "?").strip(),
                        "author": (b.get("author") or b.get("author_name") or "").strip(),
                        "tab_title": tab_title,
                        "thumb_url": str(thumb).strip(),
                    })
                for sub in c.get("cell_data") or []:
                    if isinstance(sub, dict):
                        stack.append(sub)
        return rows

    mod.FanqieClient._extract_books_from_tab = _extract_books_from_tab
    mod._tingshu_extract_patched = True


def _title_dedupe_key(title: str) -> str:
    """书名去重键：去空白，统一标点。"""
    t = str(title or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", "", t)
    t = t.replace("，", ",").replace("：", ":").replace("？", "?").replace("！", "!")
    return t.lower()


def _pick_better_book(a: Dict, b: Dict) -> Dict:
    """同名书保留信息更全的一条（优先听书、有封面）。"""
    score = lambda x: (
        (2 if x.get("tingshu_kind") in ("audio", "both") else 0)
        + (1 if x.get("cover") else 0)
        + (1 if x.get("author") and x.get("author") != "未知作者" else 0)
    )
    return a if score(a) >= score(b) else b


def _dedupe_books_by_title(books: List[Dict]) -> List[Dict]:
    order: List[str] = []
    merged: Dict[str, Dict] = {}
    for book in books:
        key = _title_dedupe_key(book.get("title") or book.get("book_name") or "")
        if not key:
            order.append(f"id:{book.get('id')}")
            merged[f"id:{book.get('id')}"] = book
            continue
        if key not in merged:
            merged[key] = book
            order.append(key)
        else:
            merged[key] = _pick_better_book(merged[key], book)
    return [merged[k] for k in order]


class FanqieTingshuManager:
    """番茄听书（番茄小说 App 听书）"""

    def __init__(self):
        self._client = None
        self._client_lock = threading.Lock()
        self._voices_cache: Dict[str, List[Dict]] = {}
        self.current_voice_config: Optional[Dict] = None

    def _mod(self):
        return _load_wanzheng_module()

    def reset_client(self) -> None:
        """丢弃当前客户端（下次 API 调用时从文件加载或重新注册）。"""
        with self._client_lock:
            self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                mod = self._mod()
                self._client = mod.FanqieClient(use_capture=False)
            return self._client

    def _listen_choice_to_voice(self, choice, book_id: str) -> Dict:
        return {
            "name": choice.label,
            "tone_id": str(choice.tone_id),
            "is_real_person": "1" if choice.kind == "real" else "0",
            "play_book_id": str(choice.play_book_id),
            "kind": choice.kind,
            "book_id": str(book_id),
            "platform": PLATFORM_NAME,
            "use_tingshu_content_api": True,
        }

    def _fallback_voices(self, book_id: str, tone_data: Optional[Dict] = None) -> List[Dict]:
        """书籍 Tab 结果无真人/AI 菜单时，仍走听书 directory + playinfo"""
        mod = self._mod()
        book_id = str(book_id)
        play_id = book_id
        tone_id = "4"
        name = "听书默认音色"
        if tone_data:
            try:
                play_id = mod._novel_id_for_tts(tone_data, book_id)
            except Exception:
                play_id = book_id
            rec = tone_data.get("recommend_tone")
            if rec is not None:
                tone_id = str(int(rec))
            elif tone_data.get("tts_tones"):
                tone_id = str(int(tone_data["tts_tones"][0].get("id", 4)))
        return [{
            "name": name,
            "tone_id": tone_id,
            "is_real_person": "0",
            "play_book_id": str(play_id),
            "kind": "tts",
            "book_id": book_id,
            "platform": PLATFORM_NAME,
            "use_tingshu_content_api": True,
            "is_fallback_voice": True,
        }]

    def fetch_voices(self, book_id: str, *, force_refresh: bool = False) -> List[Dict]:
        book_id = str(book_id)
        if not force_refresh and book_id in self._voices_cache:
            return self._voices_cache[book_id]
        client = self._get_client()
        mod = self._mod()
        print(f"🎭 番茄听书加载音色（听书 API）: {book_id}")
        tone_data: Dict = {}
        try:
            tone_data = client.audio_toneinfo(book_id) or {}
        except Exception as e:
            print(f"⚠️ audio_toneinfo 失败，使用默认听书音色: {e}")
        menu = mod.build_listen_menu(tone_data, book_id) if tone_data else []
        voices = [self._listen_choice_to_voice(c, book_id) for c in menu]
        if not voices:
            voices = self._fallback_voices(book_id, tone_data or None)
            print(f"📖 书籍条目回退听书 API 音色: {voices[0].get('name')}")
        self._voices_cache[book_id] = voices
        print(f"✅ 番茄听书音色: {len(voices)} 个")
        return voices

    def get_voice_by_name(self, book_id: str, name: str) -> Optional[Dict]:
        for v in self.fetch_voices(book_id):
            if v.get("name") == name:
                return v
        voices = self.fetch_voices(book_id)
        return voices[0] if voices else None

    def resolve_voice_config(
        self, book_id: str, voice_config: Optional[Dict] = None
    ) -> Optional[Dict]:
        """解析当前应使用的音色（切换真人/AI 时保留用户选择）。"""
        voices = self.fetch_voices(book_id)
        if not voices:
            return None
        if not voice_config:
            return voices[0]

        name = (voice_config.get("name") or "").strip()
        tone_id = str(voice_config.get("tone_id") or "")
        play_book_id = str(voice_config.get("play_book_id") or "")
        kind = voice_config.get("kind") or (
            "real" if voice_config.get("is_real_person") == "1" else "tts"
        )

        if name:
            for v in voices:
                if v.get("name") == name:
                    return v

        for v in voices:
            if (
                str(v.get("tone_id") or "") == tone_id
                and str(v.get("play_book_id") or "") == play_book_id
            ):
                return v

        for v in voices:
            if str(v.get("tone_id") or "") == tone_id and v.get("kind") == kind:
                return v

        print(f"⚠️ 未匹配到音色 {name!r}，使用列表首项")
        return voices[0]

    def load_chapters_for_voice(
        self, book_id: str, voice_config: Optional[Dict] = None
    ) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
        """按指定音色加载章节目录（真人/AI 对应不同 play_book_id）。"""
        voices = self.fetch_voices(book_id)
        active = self.resolve_voice_config(book_id, voice_config)
        if not active:
            return [], voices, None
        self.current_voice_config = active
        chapters = self.get_chapters(book_id, active)
        return chapters, voices, active

    def _convert_chapters(self, items: List[dict], album_id: str) -> List[Dict]:
        chapters = []
        for order_num, ch in enumerate(items, 1):
            item_id = str(ch.get("item_id") or "")
            if not item_id:
                continue
            dur = ch.get("duration") or ch.get("audio_duration") or 0
            size = ch.get("size") or ch.get("audio_size") or 0
            chapters.append({
                "id": f"chapter-{item_id}",
                "title": ch.get("title") or ch.get("item_title") or f"第{order_num}章",
                "duration": _fmt_duration(dur),
                "size": _fmt_size(size),
                "plays": int(ch.get("play_count") or 0),
                "album": album_id,
                "order_num": order_num,
                "_item_id": item_id,
            })
        return chapters

    def get_chapters(self, book_id: str, voice_config: Dict) -> List[Dict]:
        client = self._get_client()
        play_book_id = str(voice_config.get("play_book_id") or book_id)
        print(f"📚 番茄听书加载目录: book={book_id}, play_book={play_book_id}")
        items = client.directory(play_book_id)
        return self._convert_chapters(items, book_id)

    def load_chapters_with_voices(self, book_id: str) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
        return self.load_chapters_for_voice(book_id, self.current_voice_config)

    def _row_to_book(self, row: Dict, kind: str) -> Dict:
        bid = str(row.get("book_id") or "")
        label = "书籍" if kind == "book" else "听书"
        cover = _cover_from_detail(row) or _normalize_cover_url(
            row.get("thumb_url") or row.get("cover") or ""
        )
        return {
            "id": bid,
            "title": row.get("book_name") or row.get("title") or "未知",
            "author": row.get("author") or "未知作者",
            "platform": PLATFORM_NAME,
            "cover": cover,
            "plays": 0,
            "episodes": 0,
            "status": "连载中",
            "description": f"番茄{label} · 正文走听书 API",
            "category": label,
            "tags": [],
            "created_at": "",
            "updated_at": "",
            "tingshu_kind": kind,
            "use_tingshu_content_api": True,
        }

    def _fetch_cover_from_page(self, book_id: str) -> str:
        url = f"https://fanqienovel.com/page/{book_id}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://fanqienovel.com/",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                return ""
            html = resp.text
            for pattern in (
                r'"thumbUrl"\s*:\s*"((?:\\.|[^"\\])*)"',
                r'"thumb_url"\s*:\s*"((?:\\.|[^"\\])*)"',
            ):
                match = re.search(pattern, html)
                if match:
                    return _normalize_cover_url(_decode_json_escaped_string(match.group(1)))
        except Exception as e:
            print(f"⚠️ 番茄听书网页封面获取失败 {book_id}: {e}")
        return ""

    def _fetch_cover(self, book_id: str) -> str:
        cover = ""
        try:
            detail = self._get_client().book_detail(str(book_id))
            cover = _cover_from_detail(detail)
        except Exception:
            pass
        if not cover:
            cover = self._fetch_cover_from_page(book_id)
        return cover

    def _enrich_covers(self, books: List[Dict]) -> None:
        need = [b for b in books if not b.get("cover")]
        if not need:
            return
        print(f"🖼️ 番茄听书补全封面: {len(need)} 本")

        def _fill(book: Dict) -> None:
            cover = self._fetch_cover(book["id"])
            if cover:
                book["cover"] = cover

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(_fill, need))

    def _rows_to_books(self, rows: List[Dict], kind: str) -> List[Dict]:
        """单 Tab 内按 API 顺序去重（同 book_id 保留首次）。"""
        items: List[Dict] = []
        seen: set = set()
        for row in rows:
            bid = str(row.get("book_id") or "")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            items.append(self._row_to_book(row, kind))
        return items

    def _merge_same_book_entry(self, existing: Dict, incoming: Dict) -> None:
        """同一 book_id 在书籍/听书 Tab 均出现时合并元数据。"""
        prev_kind = existing.get("tingshu_kind", "book")
        new_kind = incoming.get("tingshu_kind", "audio")
        if prev_kind != new_kind:
            existing["tingshu_kind"] = "both"
            existing["category"] = "书籍·听书"
            existing["description"] = "番茄书籍·听书 · 正文走听书 API"
        if not existing.get("cover") and incoming.get("cover"):
            existing["cover"] = incoming["cover"]
        if (existing.get("author") in ("", "未知作者") and incoming.get("author")):
            existing["author"] = incoming["author"]

    def _interleave_search_results(
        self, book_items: List[Dict], audio_items: List[Dict]
    ) -> List[Dict]:
        """
        按 API 原始顺序交错展示：
        书籍#1 → 听书#1 → 书籍#2 → 听书#2 → …
        """
        ordered: List[Dict] = []
        id_index: Dict[str, int] = {}
        n = max(len(book_items), len(audio_items))

        def _append(item: Dict) -> None:
            bid = str(item.get("id") or "")
            if not bid:
                return
            if bid in id_index:
                self._merge_same_book_entry(ordered[id_index[bid]], item)
                return
            id_index[bid] = len(ordered)
            ordered.append(item)

        for i in range(n):
            if i < len(book_items):
                _append(book_items[i])
            if i < len(audio_items):
                _append(audio_items[i])
        return ordered

    def search_by_id(self, book_id: str) -> Optional[Dict]:
        """通过书籍 ID 直接获取详情。"""
        bid = parse_book_id(book_id) or str(book_id).strip()
        if not bid.isdigit():
            return None
        return self.get_book_detail(bid)

    def search_books(self, keyword: str, max_pages: int = 30) -> List[Dict]:
        """并行搜索书籍 Tab + 听书 Tab，按 API 顺序交错展示，再按书名去重"""
        bid = parse_book_id(keyword)
        if bid:
            print(f"🔍 番茄听书 ID 搜索: {bid}")
            detail = self.get_book_detail(bid)
            return [detail] if detail else []

        client = self._get_client()
        print(f"🔍 番茄听书并行搜索（书籍+听书）: {keyword}")

        def _run(kind: str) -> List[Dict]:
            try:
                return client.search_by_kind(keyword, kind, max_pages=max_pages)
            except Exception as e:
                print(f"⚠️ 番茄听书 [{kind}] 搜索失败: {e}")
                return []

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_book = pool.submit(_run, "book")
            fut_audio = pool.submit(_run, "audio")
            book_rows = fut_book.result()
            audio_rows = fut_audio.result()

        book_items = self._rows_to_books(book_rows, "book")
        audio_items = self._rows_to_books(audio_rows, "audio")
        books = self._interleave_search_results(book_items, audio_items)

        before = len(books)
        books = _dedupe_books_by_title(books)
        if before != len(books):
            print(f"📋 书名去重: {before} → {len(books)} 本")
        self._enrich_covers(books)
        print(
            f"✅ 番茄听书搜索完成: 书籍 Tab {len(book_items)} + 听书 Tab {len(audio_items)}"
            f" → 交错 {before} 本 → 去重后 {len(books)} 本"
        )
        return books

    def get_book_detail(self, book_id: str) -> Optional[Dict]:
        try:
            book_id = str(book_id)
            client = self._get_client()
            title, author, cover, desc = f"书籍ID_{book_id}", "未知作者", "", ""
            try:
                detail = client.book_detail(book_id)
                title = (detail.get("book_name") or detail.get("title") or title).strip()
                author = (detail.get("author") or detail.get("author_name") or author).strip()
                cover = _cover_from_detail(detail)
                desc = (detail.get("abstract") or detail.get("description") or "").strip()
            except Exception as e:
                print(f"⚠️ 番茄听书 book_detail 失败: {e}")

            if not cover:
                cover = self._fetch_cover_from_page(book_id)

            voices = self.fetch_voices(book_id)
            default_voice = voices[0] if voices else None
            total = 0
            if default_voice:
                try:
                    print(f"📚 详情章节统计走听书 directory API: {default_voice['play_book_id']}")
                    items = client.directory(str(default_voice["play_book_id"]))
                    total = len(items)
                except Exception as e:
                    print(f"⚠️ 番茄听书目录统计失败: {e}")

            return {
                "id": book_id,
                "title": title,
                "author": author or "未知作者",
                "platform": PLATFORM_NAME,
                "cover": str(cover).strip(),
                "plays": 0,
                "episodes": total,
                "status": "连载中",
                "description": desc or f"番茄听书 · {title}",
                "category": "",
                "tags": [],
                "tingshu_voices": voices,
                "tingshu_default_voice": default_voice,
                "use_tingshu_content_api": True,
            }
        except Exception as e:
            print(f"❌ 番茄听书详情失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _chapter_id_raw(self, chapter_id: str) -> str:
        cid = str(chapter_id or "")
        return cid.replace("chapter-", "") if cid.startswith("chapter-") else cid

    def get_play_info(self, chapter_id: str, voice_config: Dict) -> Optional[Dict]:
        client = self._get_client()
        raw_id = self._chapter_id_raw(chapter_id)
        tone_id = int(voice_config.get("tone_id") or 0)
        plays = client.audio_playinfo([raw_id], tone_id)
        if not plays:
            return None
        return plays[0]

    def _http_headers(self) -> Dict[str, str]:
        client = self._get_client()
        mod = self._mod()
        return mod._http_dl_headers(client)

    def get_audio_download_info(self, chapter_id: str, voice_config: Dict) -> Optional[Dict]:
        play = self.get_play_info(chapter_id, voice_config)
        if not play:
            return None
        url = play.get("main_url") or play.get("backup_url") or ""
        encrypted = bool(
            play.get("is_encrypt")
            or (url and not url.startswith("http"))
        )
        ext = ".m4a"
        if url and ".mp3" in url.lower():
            ext = ".mp3"
        return {
            "url": url,
            "extension": ext,
            "format": ext.lstrip("."),
            "play": play,
            "encrypted": encrypted,
            "voice_config": voice_config,
        }

    def refresh_cookie(self) -> None:
        """下载失败时重新计算 Cookie 并覆盖保存。"""
        client = self._get_client()
        with client._api_lock:
            client._recalculate_cookie(None)

    def download_chapter(
        self,
        chapter_id: str,
        voice_config: Dict,
        output_path: str,
        *,
        max_retries: int = 3,
    ) -> bool:
        """番茄听书专用：playinfo → CENC 解密下载；失败重试，仍失败则刷新设备 Cookie。

        与番茄畅听无关（畅听走 FanqieManager.download_changting_chapter）。
        """
        last_err = ""

        def _try_once() -> bool:
            nonlocal last_err
            for attempt in range(1, max_retries + 1):
                try:
                    info = self.get_audio_download_info(chapter_id, voice_config)
                    if not info or not info.get("play"):
                        last_err = "无 playinfo"
                        raise RuntimeError(last_err)
                    play = info["play"]
                    if not (play.get("main_url") or play.get("backup_url")):
                        last_err = "无播放地址"
                        raise RuntimeError(last_err)
                    mod = self._mod()
                    out = Path(output_path)
                    if out.is_file() and out.stat().st_size > 1024:
                        return True
                    mod.download_chapter_audio(play, out, self._http_headers())
                    if out.is_file() and out.stat().st_size > 1024:
                        print(f"✅ 番茄听书下载完成: {out.name} ({out.stat().st_size // 1024} KB)")
                        return True
                    last_err = "文件过小或解密失败"
                    raise RuntimeError(last_err)
                except Exception as e:
                    last_err = str(e)
                    if attempt < max_retries:
                        print(
                            f"⚠️ 番茄听书重试 {attempt}/{max_retries} "
                            f"章={chapter_id}: {last_err[:80]}"
                        )
            return False

        if _try_once():
            return True

        print(f"🔄 番茄听书 3 次失败，重新获取 Cookie 后重试 章={chapter_id}")
        self.refresh_cookie()
        if _try_once():
            return True

        print(f"❌ 番茄听书下载失败: {last_err}")
        return False

    def prepare_playback(self, chapter_id: str, voice_config: Dict) -> Optional[str]:
        """返回可播放 URL 或本地临时文件路径（CENC 加密时下载解密）"""
        try:
            info = self.get_audio_download_info(chapter_id, voice_config)
            if not info:
                return None
            play = info["play"]
            url = play.get("main_url") or play.get("backup_url") or ""
            mod = self._mod()
            need_decrypt = bool(
                play.get("is_encrypt")
                or mod.spade_from_play(play)
            )
            if url.startswith("http") and not need_decrypt:
                return url
            if not url:
                return None
            suffix = ".m4a"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="fqts_")
            import os
            os.close(fd)
            path = mod.download_chapter_audio(play, Path(tmp), self._http_headers())
            return str(path)
        except Exception as e:
            print(f"❌ 番茄听书播放准备失败: {e}")
            import traceback
            traceback.print_exc()
            return None


_manager_instance: Optional[FanqieTingshuManager] = None
_manager_lock = threading.Lock()


def get_fanqie_tingshu_manager() -> FanqieTingshuManager:
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = FanqieTingshuManager()
    return _manager_instance
