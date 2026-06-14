#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
七猫听书管理器 — 封装 xmly/qm 参考脚本（书籍 Tab + 听书 Tab 并行搜索）

参考文件默认路径（按顺序尝试）：
  <应用根目录>/qm
  <应用根目录>/../xmly/qm
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .app_paths import app_root

PLATFORM_NAME = "七猫听书"

_BOOK_ID_RE = re.compile(
    r"(?:book[_-]?id[=:]|/book/|id=)(\d{5,})",
    re.I,
)

_module_cache = None
_module_lock = threading.Lock()


def parse_book_id(keyword: str) -> Optional[str]:
    text = (keyword or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    m = _BOOK_ID_RE.search(text)
    return m.group(1) if m else None


def _resolve_qm_script() -> Path:
    root = app_root()
    candidates = [
        root / "core" / "vendor" / "qimao_portable.py",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "未找到七猫参考脚本: core/vendor/qimao_portable.py"
    )


def _load_qm_module():
    global _module_cache
    if _module_cache is not None:
        return _module_cache
    with _module_lock:
        if _module_cache is not None:
            return _module_cache
        script = _resolve_qm_script()
        spec = importlib.util.spec_from_file_location("qimao_portable", script)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载七猫脚本: {script}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["qimao_portable"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop("qimao_portable", None)
            raise
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


def _normalize_duration(value) -> str:
    if value is None:
        return "00:00"
    text = str(value).strip()
    if not text:
        return "00:00"
    if ":" in text:
        return text
    try:
        return _fmt_duration(int(text))
    except (TypeError, ValueError):
        return "00:00"


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


def _normalize_cover(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    return u


def _is_topic_junk(raw: dict) -> bool:
    """过滤社区帖子/书荒话题等非书籍、非听书专辑条目。"""
    if raw.get("topic_type") not in (None, "", "0", 0):
        return True
    if str(raw.get("show_type") or "") == "7":
        return True
    sub = str(raw.get("sub_title") or "")
    if "帖子" in sub or "书荒" in sub:
        return True
    return False


def _title_dedupe_key(title: str) -> str:
    t = re.sub(r"\s+", "", str(title or "").strip())
    t = t.replace("，", ",").replace("：", ":")
    return t.lower()


def _pick_better(a: Dict, b: Dict) -> Dict:
    score = lambda x: (
        (2 if x.get("qimao_kind") in ("album", "both") else 0)
        + (1 if x.get("cover") else 0)
        + (1 if x.get("author") and x.get("author") != "未知作者" else 0)
    )
    return a if score(a) >= score(b) else b


def _dedupe_by_title(books: List[Dict]) -> List[Dict]:
    order: List[str] = []
    merged: Dict[str, Dict] = {}
    for book in books:
        key = _title_dedupe_key(book.get("title") or "")
        if not key:
            oid = f"id:{book.get('id')}"
            order.append(oid)
            merged[oid] = book
            continue
        if key not in merged:
            merged[key] = book
            order.append(key)
        else:
            merged[key] = _pick_better(merged[key], book)
    return [merged[k] for k in order]


class QimaoManager:
    def __init__(self) -> None:
        self._client = None
        self._client_lock = threading.Lock()
        self._api_lock = threading.Lock()
        self.current_voice: Optional[Dict[str, Any]] = None
        self.available_voices: List[Dict[str, Any]] = []
        self._last_book_id: str = ""
        self._last_album_id: str = ""
        self._last_kind: str = "book"
        self._search_cache: Dict[str, Dict] = {}

    def _mod(self):
        return _load_qm_module()

    def _get_client(self):
        with self._client_lock:
            if self._client is not None:
                return self._client
            mod = self._mod()
            cfg = mod.QimaoConfig.tourist_login()
            self._client = mod.QimaoClient(cfg)
            return self._client

    def _raw_book_items(self, sr: dict) -> List[dict]:
        mod = self._mod()
        return mod.QimaoClient.search_books(sr)

    def _search_book_tab(self, keyword: str) -> List[dict]:
        mod = self._mod()
        c = self._get_client()
        with self._api_lock:
            # 与 core/vendor/qimao_portable.py 参考实现 search_books(c, kw) 一致
            return mod.search_books(c, keyword)

    def _search_listen_tab(self, keyword: str) -> List[dict]:
        mod = self._mod()
        c = self._get_client()
        with self._api_lock:
            # 与 core/vendor/qimao_portable.py 参考实现 search_listen_items(c, kw) 一致
            return mod.search_listen_items(c, keyword)

    def _item_to_dict(self, raw: dict, kind: str) -> Dict:
        bid = str(raw.get("id") or raw.get("book_id") or "").strip()
        aid = str(raw.get("album_id") or "").strip()
        is_audio = str(raw.get("is_audio", "0")) == "1"
        title = (raw.get("title") or raw.get("book_name") or "?").strip()
        author = (raw.get("author") or raw.get("author_name") or "未知作者").strip()
        cover = _normalize_cover(
            raw.get("image_link")
            or raw.get("cover")
            or raw.get("avatar")
            or raw.get("horiz_thumb_url")
            or ""
        )
        episodes = int(raw.get("chapter_count") or raw.get("total_num") or 0)
        # 听书专辑：listen tab，或书籍 tab 里 is_audio=1 的条目
        as_album = kind == "album" or (is_audio and bool(aid))
        if as_album:
            primary = aid or bid
            return {
                "id": primary,
                "title": title,
                "author": author,
                "platform": PLATFORM_NAME,
                "cover": cover,
                "plays": 0,
                "episodes": episodes,
                "status": "连载中",
                "description": f"七猫听书专辑 · {title}",
                "category": "听书专辑",
                "tags": [],
                "qimao_kind": "album",
                "book_id": bid,
                "album_id": primary,
            }
        return {
            "id": bid,
            "title": title,
            "author": author,
            "platform": PLATFORM_NAME,
            "cover": cover,
            "plays": 0,
            "episodes": episodes,
            "status": "连载中",
            "description": f"七猫书籍 · {title}",
            "category": "书籍",
            "tags": [],
            "qimao_kind": "book",
            "book_id": bid,
            "album_id": aid,
        }

    def _interleave(self, book_items: List[Dict], listen_items: List[Dict]) -> List[Dict]:
        out: List[Dict] = []
        by_id: Dict[str, int] = {}
        n = max(len(book_items), len(listen_items))

        def _add(item: Dict) -> None:
            pid = str(item.get("id") or "")
            if not pid:
                return
            if pid in by_id:
                prev = out[by_id[pid]]
                pk = prev.get("qimao_kind")
                nk = item.get("qimao_kind")
                if pk != nk:
                    prev["qimao_kind"] = "both"
                    prev["category"] = "书籍·听书"
                if not prev.get("cover") and item.get("cover"):
                    prev["cover"] = item["cover"]
                if not prev.get("album_id") and item.get("album_id"):
                    prev["album_id"] = item["album_id"]
                return
            by_id[pid] = len(out)
            out.append(item)

        for i in range(n):
            if i < len(book_items):
                _add(book_items[i])
            if i < len(listen_items):
                _add(listen_items[i])
        return out

    def search_books(self, keyword: str, max_pages: int = 1) -> List[Dict]:
        _ = max_pages
        bid = parse_book_id(keyword)
        if bid:
            detail = self.get_book_detail(bid)
            return [detail] if detail else []

        print(f"🔍 七猫听书搜索（书籍+听书）: {keyword}")

        try:
            book_items = [
                self._item_to_dict(x, "book")
                for x in self._search_book_tab(keyword)
                if not _is_topic_junk(x)
            ]
        except Exception as e:
            print(f"⚠️ 七猫书籍 Tab 搜索失败: {e}")
            import traceback
            traceback.print_exc()
            book_items = []

        try:
            listen_items = [
                self._item_to_dict(x, "album")
                for x in self._search_listen_tab(keyword)
                if not _is_topic_junk(x)
            ]
        except Exception as e:
            print(f"⚠️ 七猫听书 Tab 搜索失败: {e}")
            import traceback
            traceback.print_exc()
            listen_items = []

        merged = self._interleave(book_items, listen_items)
        before = len(merged)
        merged = _dedupe_by_title(merged)
        for item in merged:
            for key in (
                str(item.get("id") or ""),
                str(item.get("book_id") or ""),
                str(item.get("album_id") or ""),
            ):
                if key:
                    self._search_cache[key] = item
        print(
            f"✅ 七猫听书: 书籍 {len(book_items)} + 听书 {len(listen_items)}"
            f" → 交错 {before} → 去重 {len(merged)}"
        )
        return merged

    def get_book_detail(self, book_id: str) -> Optional[Dict]:
        bid = parse_book_id(book_id) or str(book_id).strip()
        if not bid.isdigit():
            return None
        cached = self._search_cache.get(bid)
        if cached:
            self._last_book_id = str(cached.get("book_id") or cached.get("id") or bid)
            self._last_album_id = str(cached.get("album_id") or "")
            return dict(cached)
        self._last_book_id = bid
        c = self._get_client()
        mod = self._mod()
        try:
            sr = c.search_words(bid, tab=mod.TAB_BOOK)
            for raw in mod.QimaoClient.search_books(sr):
                if _is_topic_junk(raw):
                    continue
                rid = str(raw.get("id") or raw.get("book_id") or "")
                raid = str(raw.get("album_id") or "")
                if rid == bid or raid == bid:
                    item = self._item_to_dict(raw, "book")
                    self._last_book_id = str(item.get("book_id") or item.get("id") or bid)
                    self._last_album_id = str(item.get("album_id") or "")
                    return item
            for raw in mod.QimaoClient.filter_listen_items(mod.QimaoClient.search_books(sr)):
                if _is_topic_junk(raw):
                    continue
                aid = str(raw.get("album_id") or raw.get("id") or "")
                if aid == bid:
                    item = self._item_to_dict(raw, "album")
                    self._last_album_id = aid
                    self._last_book_id = str(item.get("book_id") or "")
                    return item
        except Exception as e:
            print(f"⚠️ 七猫 ID 搜索详情失败: {e}")
        try:
            sr = c.search_words(bid, tab=mod.TAB_LISTEN)
            for raw in mod.QimaoClient.filter_listen_items(mod.QimaoClient.search_books(sr)):
                if _is_topic_junk(raw):
                    continue
                aid = str(raw.get("album_id") or raw.get("id") or "")
                if aid == bid:
                    item = self._item_to_dict(raw, "album")
                    self._last_album_id = aid
                    self._last_book_id = str(item.get("book_id") or "")
                    return item
        except Exception as e:
            print(f"⚠️ 七猫听书 Tab ID 搜索失败: {e}")
        try:
            data = c.album_chapter_list(bid)
            raw_list = data.get("chapter_list") or (data.get("data") or {}).get("chapter_list") or []
            if raw_list:
                meta = data.get("meta") or (data.get("data") or {}).get("album") or {}
                item = self._item_to_dict(
                    {
                        "id": bid,
                        "album_id": bid,
                        "title": meta.get("title") or meta.get("album_title") or f"七猫专辑_{bid}",
                        "author": meta.get("author") or meta.get("anchor") or "未知作者",
                        "image_link": meta.get("cover") or meta.get("image_link") or "",
                        "is_audio": "1",
                    },
                    "album",
                )
                self._last_album_id = bid
                return item
        except Exception:
            pass
        try:
            cl = c.book_chapter_list(bid)
            flat = mod.QimaoClient.iter_chapters(cl)
            if flat:
                first_id = str(flat[0].get("id") or "")
                player_meta = self._book_meta_from_player(bid, first_id) or {}
                info = (cl.get("data") or {}).get("book") or cl.get("book") or {}
                item = self._item_to_dict(
                    {
                        "id": bid,
                        "title": (
                            player_meta.get("title")
                            or info.get("title")
                            or info.get("book_name")
                            or f"七猫书籍_{bid}"
                        ),
                        "author": (
                            player_meta.get("author")
                            or info.get("author")
                            or info.get("author_name")
                            or "未知作者"
                        ),
                        "image_link": (
                            player_meta.get("cover")
                            or info.get("image_link")
                            or info.get("cover")
                            or ""
                        ),
                    },
                    "book",
                )
                self._last_book_id = bid
                for key in (str(item.get("id") or ""), bid):
                    if key:
                        self._search_cache[key] = item
                return item
        except Exception:
            pass
        item = self._item_to_dict(
            {"id": bid, "title": f"七猫_{bid}", "author": "未知作者"},
            "book",
        )
        self._last_book_id = bid
        return item

    def _book_meta_from_player(
        self, book_id: str, first_chapter_id: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """通过 get-player-info 补全书籍标题、作者、封面（ID 直搜时 chapter-list 常无 meta）。"""
        c = self._get_client()
        mod = self._mod()
        try:
            first_id = str(first_chapter_id or "").strip()
            if not first_id:
                cl = c.book_chapter_list(book_id)
                flat = mod.QimaoClient.iter_chapters(cl)
                if not flat:
                    return None
                first_id = str(flat[0].get("id") or "")
            if not first_id:
                return None
            pi = c.book_player_info(book_id, first_id)
            title = str(pi.get("title") or "").strip()
            author = str(pi.get("author") or "").strip()
            cover = _normalize_cover(pi.get("image_link") or "")
            if not title and not cover:
                return None
            return {
                "title": title,
                "author": author or "未知作者",
                "cover": cover,
            }
        except Exception as e:
            print(f"⚠️ 七猫播放器信息获取失败: {e}")
            return None

    def _normalize_voice(self, raw: dict) -> Dict[str, Any]:
        mod = self._mod()
        mode = mod.QimaoClient.voice_download_mode(raw)
        vid = str(raw.get("voice_id") or "")
        name = str(raw.get("voice_name") or f"音色{vid}").strip()
        return {
            "name": name,
            "voice_id": vid,
            "tone_id": vid,
            "voice_type": str(raw.get("voice_type") or ""),
            "kind": "real" if mode == "album" else "ai",
            "is_real_person": "1" if mode == "album" else "0",
            "mode": mode,
            "raw": raw,
        }

    def _match_voice(
        self, voices: List[Dict], voice_config: Optional[Dict]
    ) -> Optional[Dict]:
        if not voice_config or not voices:
            return None
        vid = str(
            voice_config.get("voice_id")
            or voice_config.get("tone_id")
            or (voice_config.get("raw") or {}).get("voice_id")
            or ""
        )
        name = str(voice_config.get("name") or "").strip()
        raw = voice_config.get("raw")
        for v in voices:
            if vid and str(v.get("voice_id")) == vid:
                return v
            if name and str(v.get("name")) == name:
                return v
            if raw and v.get("raw") is raw:
                return v
        return None

    def _pick_default_voice(
        self, voices: List[Dict], album_id_hint: str = ""
    ) -> Optional[Dict]:
        if not voices:
            return None
        if album_id_hint:
            for v in voices:
                if str(v.get("voice_id")) == str(album_id_hint):
                    return v
        for v in voices:
            if v.get("mode") == "ai":
                return v
        return voices[0]

    def fetch_voices(self, book_id: str) -> List[Dict]:
        bid = str(book_id or "").strip()
        if not bid.isdigit():
            return []
        c = self._get_client()
        mod = self._mod()
        try:
            cl = c.book_chapter_list(bid)
            flat = mod.QimaoClient.iter_chapters(cl)
            if not flat:
                return []
            first_id = str(flat[0].get("id") or "")
            raw_voices = c.book_voice_list(bid, first_id)
            voices = [self._normalize_voice(v) for v in raw_voices]
            self.available_voices = voices
            return voices
        except Exception as e:
            print(f"⚠️ 七猫音色列表失败: {e}")
            return []

    def load_chapters_for_voice(
        self,
        book_id: str,
        voice_config: Optional[Dict] = None,
        *,
        album_id_hint: str = "",
    ) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
        """按所选音色加载章节目录（AI 书籍章节 / 真人专辑章节）。"""
        bid = str(book_id or "").strip()
        self._last_book_id = bid
        voices = self.fetch_voices(bid)
        active = self._match_voice(voices, voice_config) or self._pick_default_voice(
            voices, album_id_hint
        )
        if not active:
            return [], voices, None

        self.current_voice = active.get("raw")
        mod = self._mod()
        if active.get("mode") == "album":
            aid = str(active.get("voice_id") or "")
            chapters = self._album_chapters(aid)
            self._last_album_id = aid
            self._last_kind = "album"
            print(
                f"📚 七猫听书：真人专辑 album_id={aid}（{len(chapters)} 集）"
                f" · 音色「{active.get('name')}」"
            )
        else:
            chapters, _ = self._book_ai_chapters(bid, voice=active.get("raw"))
            self._last_kind = "book"
            print(
                f"📚 七猫听书：书籍 AI 章节 book_id={bid}（{len(chapters)} 章）"
                f" · 音色「{active.get('name')}」"
            )
        return chapters, voices, active

    def _album_chapters(self, album_id: str) -> List[Dict]:
        c = self._get_client()
        data = c.album_chapter_list(album_id)
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        raw = (
            payload.get("chapter_list")
            or payload.get("chapters")
            or payload.get("list")
            or data.get("chapter_list")
            or []
        )
        chapters: List[Dict] = []
        for i, ch in enumerate(raw, 1):
            cid = str(ch.get("id") or ch.get("chapter_id") or "")
            if not cid:
                continue
            dur = _normalize_duration(ch.get("duration"))
            chapters.append({
                "id": cid,
                "title": ch.get("title") or ch.get("chapter_title") or f"第{i}集",
                "duration": dur,
                "size": _fmt_size(int(ch.get("audio_size") or 0)),
                "plays": 0,
                "album": album_id,
                "order_num": i,
                "qimao_album_id": album_id,
            })
        return chapters

    def _book_ai_chapters(
        self, book_id: str, voice: Optional[Dict] = None
    ) -> Tuple[List[Dict], Optional[Dict]]:
        c = self._get_client()
        mod = self._mod()
        cl = c.book_chapter_list(book_id)
        flat = mod.QimaoClient.iter_chapters(cl)
        if not flat:
            return [], None
        first_id = str(flat[0].get("id") or "")
        voices = []
        try:
            voices = c.book_voice_list(book_id, first_id)
        except Exception as e:
            print(f"⚠️ 七猫音色列表失败: {e}")
        if voice is None:
            voice = voices[0] if voices else None
        else:
            vid = str(voice.get("voice_id") or "")
            for v in voices:
                if str(v.get("voice_id")) == vid:
                    voice = v
                    break
        self.current_voice = voice
        chapters: List[Dict] = []
        if voice and mod.QimaoClient.voice_download_mode(voice) == "album":
            return self._album_chapters(str(voice.get("voice_id"))), voice
        for i, ch in enumerate(flat, 1):
            cid = str(ch.get("id") or "")
            if not cid:
                continue
            chapters.append({
                "id": cid,
                "title": ch.get("title") or ch.get("chapter_title") or f"第{i}章",
                "duration": "00:00",
                "size": "0MB",
                "plays": 0,
                "album": book_id,
                "order_num": i,
                "qimao_book_id": book_id,
            })
        return chapters, voice

    def get_chapters(
        self, album_id: str, voice_config: Optional[Dict] = None
    ) -> List[Dict]:
        """album_id 为主键：书籍 id 或听书 album_id（见 qimao_kind）。"""
        detail = self.get_book_detail(album_id) or {}
        kind = detail.get("qimao_kind") or "book"
        bid = str(detail.get("book_id") or detail.get("id") or album_id)
        aid = str(detail.get("album_id") or "")

        if kind == "album":
            self._last_book_id = str(detail.get("book_id") or "")
            self._last_album_id = aid or str(album_id)
            self._last_kind = "album"
            return self._album_chapters(self._last_album_id)

        chapters, _, _ = self.load_chapters_for_voice(
            bid,
            voice_config=voice_config or (
                self._match_voice(self.available_voices, {"raw": self.current_voice})
                if self.current_voice
                else None
            ),
            album_id_hint=aid,
        )
        return chapters

    def download_chapter(
        self,
        chapter_id: str,
        voice_config: Optional[Dict] = None,
        output_path: str = "",
        *,
        book_id: Optional[str] = None,
    ) -> bool:
        try:
            c = self._get_client()
            mod = self._mod()
            bid = book_id or self._last_book_id
            voice = voice_config or self.current_voice
            out = Path(output_path)

            if self._last_kind == "album":
                aid = self._last_album_id or bid
                c.download_album_audio(aid, chapter_id, out, overwrite=True)
                return out.is_file() and out.stat().st_size > 512

            if not voice:
                _, voice = self._book_ai_chapters(bid)
            if not voice:
                print("❌ 七猫：无可用音色")
                return False
            c.download_voice_audio(
                book_id=bid,
                chapter_id=chapter_id,
                voice=voice,
                out_path=out,
                overwrite=True,
            )
            return out.is_file() and out.stat().st_size > 512
        except Exception as e:
            print(f"❌ 七猫听书下载失败: {e}")
            return False

    def prepare_playback(self, chapter_id: str, voice_config: Optional[Dict] = None) -> Optional[str]:
        try:
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="qm_")
            import os

            os.close(fd)
            ok = self.download_chapter(
                chapter_id,
                voice_config=voice_config,
                output_path=tmp,
                book_id=self._last_book_id,
            )
            return tmp if ok else None
        except Exception as e:
            print(f"❌ 七猫播放准备失败: {e}")
            return None


_manager: Optional[QimaoManager] = None
_manager_lock = threading.Lock()


def get_qimao_manager() -> QimaoManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = QimaoManager()
    return _manager
