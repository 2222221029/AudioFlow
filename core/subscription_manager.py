#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


def utc_now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def sanitize_filename(filename):
    filename = str(filename or "")
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, "_")
    filename = filename.strip()
    if len(filename) > 200:
        filename = filename[:200]
    return filename or "unknown"


AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".flac", ".wav"}
RESTRICTED_CHAPTER_RETRY_SECONDS = 24 * 60 * 60
FAILED_CHAPTER_RETRY_BASE_SECONDS = 60 * 60
FAILED_CHAPTER_RETRY_MAX_SECONDS = 24 * 60 * 60


def failed_chapter_retry_seconds(failure_count):
    """Return a bounded retry delay for a real, non-permission failure."""
    try:
        count = max(1, int(failure_count))
    except (TypeError, ValueError):
        count = 1
    return min(
        FAILED_CHAPTER_RETRY_MAX_SECONDS,
        FAILED_CHAPTER_RETRY_BASE_SECONDS * (2 ** min(count - 1, 5)),
    )


def normalize_match_text(value):
    text = sanitize_filename(value).lower()
    return re.sub(r"[\s_\-—–,，.。:：;；!！?？【】\[\]（）()《》<>|·'\"`~@#$%^&+=]+", "", text)


def album_title_values(album):
    values = []
    for key in ("title", "album_title", "albumTitle", "book_name", "bookName", "name"):
        value = album.get(key) if isinstance(album, dict) else None
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def album_id_values(album):
    values = []
    for key in ("id", "album_id", "albumId", "book_id", "bookId"):
        value = album.get(key) if isinstance(album, dict) else None
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


# 已知平台名（与前端 platforms.js 一致）。开启「按平台分目录」后，下载路径为
# {download_dir}/{平台}/{专辑}/，平台名作为一级目录段。用于区分不同平台的同名专辑。
KNOWN_PLATFORMS = (
    "喜马拉雅", "懒人听书", "起点听书", "蜻蜓FM", "番茄畅听", "番茄听书",
    "七猫听书", "云听FM", "酷我听书", "网易云听书", "荔枝FM", "未知平台",
)
_PLATFORM_NORMS = {normalize_match_text(p) for p in KNOWN_PLATFORMS if normalize_match_text(p)}
_SUBSCRIPTION_PLATFORM_ALIASES = {
    "ximalaya": "\u559c\u9a6c\u62c9\u96c5",
    "xmly": "\u559c\u9a6c\u62c9\u96c5",
    "lrts": "\u61d2\u4eba\u542c\u4e66",
    "qidian": "\u8d77\u70b9\u542c\u4e66",
    "qtfm": "\u8702\u8713FM",
    "fanqie": "\u756a\u8304\u542c\u4e66",
    "fanqietingshu": "\u756a\u8304\u542c\u4e66",
    "qimao": "\u4e03\u732b\u542c\u4e66",
}


def canonical_subscription_platform(value):
    platform = str(value or "").strip()
    return _SUBSCRIPTION_PLATFORM_ALIASES.get(normalize_match_text(platform), platform or "unknown")


def album_platform_norm(album):
    if not isinstance(album, dict):
        return ""
    value = album.get("platform") or album.get("source") or ""
    return normalize_match_text(value)


def path_platform_norm(path, is_file=True):
    """从路径目录段中识别平台名（organize_by_platform 结构）。返回规范化平台名或 ""。

    is_file=True 时排除最后一段（文件名本身），避免文件名里恰好出现平台名造成误判。
    """
    try:
        parts = list(Path(path).parts)
    except Exception:
        return ""
    if is_file and parts:
        parts = parts[:-1]
    for part in parts:
        norm = normalize_match_text(part)
        if norm in _PLATFORM_NORMS:
            return norm
    return ""


def platform_conflicts(path_plat, album_plat):
    """路径平台段存在且与专辑平台不一致 → True（用于排除跨平台同名专辑的本地文件）。"""
    return bool(path_plat and album_plat and path_plat != album_plat)


def album_dir_candidates(album, download_dir, dir_cache=None):
    root = Path(download_dir)
    if not root.exists():
        return []
    title_values = album_title_values(album)
    norm_titles = [normalize_match_text(v) for v in title_values if normalize_match_text(v)]
    id_values = album_id_values(album)
    candidates = []

    for title in title_values:
        path = root / sanitize_filename(title)
        if path.exists() and path.is_dir():
            candidates.append(path)

    if dir_cache is not None and str(root) in dir_cache:
        dirs = dir_cache[str(root)]
    else:
        try:
            dirs = [p for p in root.iterdir() if p.is_dir()]
            for parent in list(dirs):
                try:
                    dirs.extend([p for p in parent.iterdir() if p.is_dir()])
                except Exception:
                    pass
        except Exception:
            dirs = []
        if dir_cache is not None:
            dir_cache[str(root)] = dirs

    album_plat = album_platform_norm(album)
    for path in dirs:
        norm_name = normalize_match_text(path.name)
        if not norm_name:
            continue
        # 路径含其他平台目录段时跳过，避免把别的平台的同名专辑误判为本地已有
        if platform_conflicts(path_platform_norm(path, is_file=False), album_plat):
            continue
        if any(norm_name == t or norm_name in t or t in norm_name for t in norm_titles):
            candidates.append(path)
            continue
        if any(str(value) and str(value) in path.name for value in id_values):
            candidates.append(path)

    seen = set()
    result = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def chapter_title_values(chapter):
    values = []
    for key in ("title", "name", "chapter_title", "chapterTitle", "track_title", "trackTitle", "trackName"):
        value = chapter.get(key) if isinstance(chapter, dict) else None
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def chapter_number_variants(order):
    variants = [str(order), f"{order:02d}", f"{order:03d}", f"{order:04d}"]
    return list(dict.fromkeys(variants))


def collect_album_audio_files(album, download_dir, dir_cache=None, file_cache=None):
    root = Path(download_dir)
    if not root.exists():
        return []
    search_dirs = album_dir_candidates(album, download_dir, dir_cache=dir_cache)
    if not search_dirs:
        search_dirs = [root]
    files = []
    seen = set()
    for base in search_dirs:
        cache_key = str(base)
        if file_cache is not None and cache_key in file_cache:
            for path in file_cache[cache_key]:
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(path)
            continue
        base_files = []
        try:
            iterator = base.rglob("*")
            for path in iterator:
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(path)
                base_files.append(path)
        except Exception:
            logging.warning("scan album directory failed: %s", base, exc_info=True)
        if file_cache is not None:
            file_cache[cache_key] = base_files
    return files


def build_local_file_match_index(local_files, has_album_scope=False):
    index = {
        "names": set(),
        "norm_stems": set(),
        "orders": {},
        "file_count": 0,
        "has_album_scope": bool(has_album_scope),
    }
    prefix_re = re.compile(r"^0*(\d+)(\D|$)")
    for path in local_files or []:
        try:
            if not path.exists() or not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if path.stat().st_size <= 1024:
                continue
            index["file_count"] += 1
            index["names"].add(path.name.lower())
            norm_stem = normalize_match_text(path.stem)
            if norm_stem:
                index["norm_stems"].add(norm_stem)
            match = prefix_re.search(path.stem)
            if match:
                order = int(match.group(1))
                index["orders"].setdefault(order, 0)
                index["orders"][order] += 1
        except Exception:
            continue
    return index


def local_audio_count(album, download_dir, dir_cache=None, file_cache=None):
    files = []
    for base in album_dir_candidates(album, download_dir, dir_cache=dir_cache):
        cache_key = str(base)
        if file_cache is not None and cache_key in file_cache:
            files.extend(file_cache[cache_key])
            continue
        base_files = []
        try:
            base_files = [path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
            files.extend(base_files)
        except Exception:
            pass
        if file_cache is not None:
            file_cache[cache_key] = base_files
    return sum(1 for path in files if path.exists() and path.stat().st_size > 1024)


def indexed_album_audio_files(index_files, album):
    title_values = album_title_values(album)
    norm_titles = [normalize_match_text(v) for v in title_values if normalize_match_text(v)]
    id_values = album_id_values(album)
    album_plat = album_platform_norm(album)
    result = []
    seen = set()
    has_album_scope = False
    skipped_by_platform = []  # 标题/ID 命中但被平台过滤跳过的文件（用于 fail-safe 回退）
    for item in index_files or []:
        path_text = str((item or {}).get("path") or "")
        if not path_text:
            continue
        norm_parent = str((item or {}).get("norm_parent") or "")
        norm_path = normalize_match_text(path_text)
        matched = False
        if any(norm_parent == t or norm_parent in t or t in norm_parent or t in norm_path for t in norm_titles):
            matched = True
        elif any(value and value in path_text for value in id_values):
            matched = True
        if not matched:
            continue
        try:
            path = Path(path_text)
            resolved = path.resolve()
        except Exception:
            path = Path(path_text)
            resolved = path
        if resolved in seen:
            continue
        # 跨平台同名专辑：路径含其他平台目录段且与本专辑平台不符时先跳过，但记录备用
        if platform_conflicts(path_platform_norm(path_text, is_file=True), album_plat):
            skipped_by_platform.append((resolved, path))
            continue
        seen.add(resolved)
        result.append(path)
        has_album_scope = True
    # fail-safe：平台过滤后无任何匹配，但存在被平台过滤跳过的同名文件，
    # 多为 album.platform 与下载目录平台段名不一致（如英文/变体/缺失）导致的误排除。
    # 此时回退采用这些文件，优先保证「本地已下载文件被识别、不重复创建下载任务」。
    if not result and skipped_by_platform:
        for resolved, path in skipped_by_platform:
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append(path)
        has_album_scope = True
    return result, has_album_scope


def chapter_key(chapter):
    for key in ("id", "track_id", "trackId", "chapter_id", "chapterId", "program_id", "programId", "acid"):
        value = chapter.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    title = str(chapter.get("title") or chapter.get("name") or chapter.get("chapter_title") or "").strip()
    order = chapter.get("order") or chapter.get("index") or chapter.get("sort") or chapter.get("episode")
    return f"{order or ''}:{title}"


def is_restricted_chapter(chapter):
    if not isinstance(chapter, dict):
        return False
    if str(chapter.get("_error_type") or "").lower() == "restricted":
        return True
    for key in ("vip", "isVip", "is_vip", "needVip", "need_vip", "vipOnly", "vip_only", "isPlatinum", "is_platinum", "platinumOnly", "platinum_only"):
        if str(chapter.get(key)).lower() in ("1", "true", "yes"):
            return True
    for key in ("isFree", "is_free", "isAuthorized", "is_authorized"):
        value = chapter.get(key)
        if value is not None and str(value).lower() in ("0", "false", "no"):
            return True
    for key in ("permission", "copyright", "trackType", "track_type"):
        try:
            if chapter.get(key) is not None and float(chapter.get(key)) > 0:
                return True
        except Exception:
            pass
    text = " ".join(str(chapter.get(key) or "") for key in ("title", "name", "_error", "msg", "message", "reason"))
    return any(token in text for token in ("白金", "会员", "VIP", "vip", "付费", "权限不足", "无权限"))


def required_access_tier(chapter):
    """Best-effort metadata tier for diagnostics; it is not an account check."""
    if not isinstance(chapter, dict):
        return "unknown"
    for key in ("isSvip", "is_svip", "svipOnly", "svip_only", "superVip", "super_vip"):
        if str(chapter.get(key)).lower() in ("1", "true", "yes"):
            return "svip"
    for key in ("isPlatinum", "is_platinum", "platinumOnly", "platinum_only"):
        if str(chapter.get(key)).lower() in ("1", "true", "yes"):
            return "platinum"
    if is_restricted_chapter(chapter):
        return "vip"
    return "free"


def is_permission_denied_failure(chapter):
    """Only an actual download denial may suppress automatic retries.

    API metadata such as ``isVip`` describes the chapter, not the current
    account.  Treating it as a failed entitlement check made a white-gold user
    permanently skip a chapter after an unrelated network failure.
    """
    if not isinstance(chapter, dict):
        return False
    if str(chapter.get("_error_type") or "").lower() in ("restricted", "permission", "forbidden"):
        return True
    text = " ".join(str(chapter.get(key) or "") for key in ("_error", "error", "message", "reason")).lower()
    return any(token in text for token in (
        "权限不足", "无权限", "无权", "需要vip", "需要会员", "会员专享", "白金专享",
        "vip only", "forbidden", "not authorized", "permission denied",
    ))


def chapter_title(chapter):
    return str(chapter.get("title") or chapter.get("name") or chapter.get("chapter_title") or "unknown")


def chapter_order(chapter, fallback):
    for key in ("ui_display_index", "order_num", "order", "index", "sort", "episode", "chapter_index"):
        value = chapter.get(key)
        try:
            if value is not None and int(value) > 0:
                return int(value)
        except Exception:
            pass
    # 与下载端 download_worker._download_single_chapter 完全一致的提取规则：
    # 先匹配带「章/节/集」锚点的序号（如「001集」→1），最后才退化到任意数字。
    # 否则像「1984：从破产川菜馆开始 001集」这类标题，裸 \d+ 会先抓到书名里的
    # 「1984」当章节号，与下载端实际命名(0001-)的序号(1)对不上，导致已下载文件
    # 永远匹配不到、每轮检测都误报缺失并反复创建「文件已存在被跳过」的无效下载。
    title = chapter_title(chapter)
    for pattern in (r"第?(\d+)[章节集]", r"(\d+)[章节集]", r"第(\d+)", r"(\d+)"):
        match = re.search(pattern, title)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass
    return fallback


def possible_chapter_files(album, chapter, index, download_dir, local_files=None):
    album_title = sanitize_filename((album_title_values(album) or ["unknown"])[0])
    titles = [sanitize_filename(v) for v in (chapter_title_values(chapter) or [chapter_title(chapter)])]
    norm_titles = [normalize_match_text(v) for v in titles if normalize_match_text(v)]
    order = chapter_order(chapter, index)
    numbers = chapter_number_variants(order)
    base_dirs = album_dir_candidates(album, download_dir) or [Path(download_dir) / album_title]
    names = []
    for ext in (".m4a", ".mp3", ".aac", ".flac", ".wav"):
        for number in numbers:
            for title in titles:
                names.append(f"{number}-{title}{ext}")
                names.append(f"{number} - {title}{ext}")
                names.append(f"{number}_{title}{ext}")
        for title in titles:
            names.append(f"{title}{ext}")
    candidates = [base_dir / name for base_dir in base_dirs for name in names]
    scan_files = local_files if local_files is not None else collect_album_audio_files(album, download_dir)
    has_album_scope = bool(base_dirs and any(base.exists() and base.is_dir() for base in base_dirs))
    prefix_re = re.compile(rf"^0*{re.escape(str(order))}(\D|$)")
    for path in scan_files:
        try:
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            stem = path.stem
            norm_stem = normalize_match_text(stem)
            title_match = any(t and (t in norm_stem or (len(t) >= 6 and norm_stem in t)) for t in norm_titles)
            prefix_match = (
                prefix_re.search(stem)
                or any(stem.startswith(f"{number}-") or stem.startswith(f"{number} -") or stem.startswith(f"{number}_") for number in numbers)
            )
            if path.name in names or title_match or (has_album_scope and prefix_match):
                candidates.append(path)
        except Exception:
            pass
    return candidates


def is_chapter_file_complete(album, chapter, index, download_dir, local_files=None, local_index=None):
    try:
        if local_index:
            titles = [sanitize_filename(v) for v in (chapter_title_values(chapter) or [chapter_title(chapter)])]
            norm_titles = [normalize_match_text(v) for v in titles if normalize_match_text(v)]
            order = chapter_order(chapter, index)
            numbers = chapter_number_variants(order)
            names = set()
            for ext in AUDIO_EXTENSIONS:
                for number in numbers:
                    for title in titles:
                        names.add(f"{number}-{title}{ext}".lower())
                        names.add(f"{number} - {title}{ext}".lower())
                        names.add(f"{number}_{title}{ext}".lower())
                for title in titles:
                    names.add(f"{title}{ext}".lower())
            if names & local_index.get("names", set()):
                return True
            norm_stems = local_index.get("norm_stems", set())
            if any(title and title in norm_stems for title in norm_titles):
                return True
            # 序号匹配需要同时满足：目录归属确认 + 至少有一个章节标题能在本地索引中找到对应。
            # 这样可避免同名不同平台专辑（本地文件来自其他平台）误判为已下载。
            index_has_title_match = bool(norm_titles and norm_stems and any(
                any(t and (t in s or (len(t) >= 4 and s and t[:4] in s)) for t in norm_titles)
                for s in norm_stems
            ))
            if local_index.get("has_album_scope") and order in local_index.get("orders", {}) and index_has_title_match:
                return True
            return False
        for path in possible_chapter_files(album, chapter, index, download_dir, local_files=local_files):
            if path.exists() and path.is_file() and path.stat().st_size > 1024:
                return True
    except Exception:
        return False
    return False


class SubscriptionManager:
    def __init__(self, config_dir=None):
        self._lock = threading.RLock()
        self.config_dir = Path(config_dir) if config_dir else Path.home() / ".audioflow"
        self.config_file = self.config_dir / "subscriptions.json"
        self.db_file = self.config_dir / "subscriptions.db"
        self.index_file = self.config_dir / "local_audio_index.json"
        self._audio_index_cache = None
        self.data = {
            "version": 1,
            "settings": {
                "enabled": True,
                "auto_download_missing": True,
                "interval_hours": 6,
                "interval_minutes": 0,
                "quality": "M4A 96K",
                "personal_sync_enabled": False,
                "personal_sync_platform": "ximalaya",
                "personal_sync_interval_hours": 1,
                "personal_sync_interval_minutes": 0,
            },
            "subscriptions": {},
        }
        self.init_db()
        self.load()
        # JSON index data is strictly a cache and no longer used at runtime.
        try:
            self.index_file.unlink(missing_ok=True)
        except OSError:
            logging.warning("could not remove legacy audio index: %s", self.index_file, exc_info=True)

    def init_db(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions ("
                "id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', "
                "updated_at TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS audio_index_meta ("
                "root TEXT PRIMARY KEY, exists_flag INTEGER NOT NULL, updated_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS audio_index_files ("
                "root TEXT NOT NULL, relative_path TEXT NOT NULL, name TEXT NOT NULL, stem TEXT NOT NULL, "
                "parent TEXT NOT NULL, norm_name TEXT NOT NULL, norm_parent TEXT NOT NULL, "
                "size INTEGER NOT NULL, mtime REAL NOT NULL, PRIMARY KEY(root, relative_path))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audio_index_files_root_parent ON audio_index_files(root, norm_parent)")

    def _db_has_data(self):
        try:
            with sqlite3.connect(self.db_file) as conn:
                row = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    def load_from_db(self):
        with sqlite3.connect(self.db_file) as conn:
            settings = {}
            for key, value in conn.execute("SELECT key, value FROM settings"):
                try:
                    settings[key] = json.loads(value)
                except Exception:
                    settings[key] = value
            subscriptions = {}
            for sid, payload in conn.execute("SELECT id, payload FROM subscriptions"):
                try:
                    item = json.loads(payload)
                    subscriptions[sid] = item
                except Exception:
                    pass
        if settings:
            self.data["settings"].update(settings)
        self.data["subscriptions"] = subscriptions

    def save_to_db(self):
        with sqlite3.connect(self.db_file) as conn:
            for key, value in (self.data.get("settings") or {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            for sid, item in (self.data.get("subscriptions") or {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO subscriptions(id, status, updated_at, payload) VALUES(?, ?, ?, ?)",
                    (
                        sid,
                        item.get("status", "active"),
                        item.get("updated_at", ""),
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
            known = set((self.data.get("subscriptions") or {}).keys())
            if known:
                placeholders = ",".join("?" for _ in known)
                conn.execute(f"DELETE FROM subscriptions WHERE id NOT IN ({placeholders})", tuple(known))
            else:
                conn.execute("DELETE FROM subscriptions")

    def load_audio_index(self):
        if self._audio_index_cache is not None:
            return self._audio_index_cache
        self._audio_index_cache = {"version": 2, "roots": {}}
        return self._audio_index_cache

    def save_audio_index(self, data):
        # Index data now lives in SQLite; this only updates the in-process cache.
        self._audio_index_cache = data

    def _load_audio_index_root(self, root):
        root_key = str(root)
        with sqlite3.connect(self.db_file) as conn:
            meta = conn.execute(
                "SELECT exists_flag, updated_at FROM audio_index_meta WHERE root = ?", (root_key,)
            ).fetchone()
            if not meta:
                return None
            rows = conn.execute(
                "SELECT relative_path, name, stem, parent, norm_name, norm_parent, size, mtime "
                "FROM audio_index_files WHERE root = ?", (root_key,)
            ).fetchall()
        files = [
            {
                "path": str(root / row[0]), "name": row[1], "stem": row[2], "parent": row[3],
                "norm_name": row[4], "norm_parent": row[5], "size": row[6], "mtime": row[7],
            }
            for row in rows
        ]
        return {"exists": bool(meta[0]), "files": files, "updated_at": float(meta[1]), "count": len(files)}

    def _save_audio_index_root(self, root, current):
        root_key = str(root)
        rows = []
        for item in current.get("files") or []:
            try:
                relative = str(Path(item["path"]).relative_to(root))
            except Exception:
                continue
            rows.append((root_key, relative, item["name"], item["stem"], item["parent"], item["norm_name"], item["norm_parent"], item["size"], item["mtime"]))
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("DELETE FROM audio_index_files WHERE root = ?", (root_key,))
            conn.execute("DELETE FROM audio_index_meta WHERE root = ?", (root_key,))
            conn.execute("INSERT INTO audio_index_meta(root, exists_flag, updated_at) VALUES(?, ?, ?)", (root_key, int(bool(current.get("exists"))), float(current.get("updated_at") or time.time())))
            if rows:
                conn.executemany(
                    "INSERT INTO audio_index_files(root, relative_path, name, stem, parent, norm_name, norm_parent, size, mtime) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        data = self.load_audio_index()
        data.setdefault("roots", {})[root_key] = current
        self.save_audio_index(data)
        # The former JSON index is regenerable and can be safely reclaimed.
        try:
            self.index_file.unlink(missing_ok=True)
        except OSError:
            logging.warning("could not remove legacy audio index: %s", self.index_file, exc_info=True)

    def build_audio_index(self, download_dir, max_age=300, force=False):
        root = Path(download_dir)
        root_key = str(root)
        now = time.time()
        data = self.load_audio_index()
        current = data.setdefault("roots", {}).get(root_key) or self._load_audio_index_root(root) or {}
        if (
            not force
            and current.get("exists") is True
            and now - float(current.get("updated_at") or 0) < max_age
        ):
            return current
        files = []
        exists = root.exists()
        if exists:
            try:
                # Index only folders belonging to active subscriptions.  The
                # old JSON cache indexed every audio file under the download
                # root, including unrelated media, and grew without bound.
                bases = []
                for subscription in self.all_subscriptions():
                    album = subscription.get("album") or subscription
                    bases.extend(album_dir_candidates(album, root))
                seen_bases = set()
                for base in bases:
                    resolved = base.resolve()
                    if resolved in seen_bases:
                        continue
                    seen_bases.add(resolved)
                    for path in base.rglob("*"):
                        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and path.stat().st_size > 1024:
                            files.append({
                                "path": str(path), "name": path.name, "stem": path.stem, "parent": path.parent.name,
                                "norm_name": normalize_match_text(path.name), "norm_parent": normalize_match_text(path.parent.name),
                                "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                            })
            except Exception as exc:
                logging.exception("build local audio index failed: %s", exc)
                current = {"exists": exists, "error": str(exc), "files": [], "updated_at": now, "count": 0}
                self._save_audio_index_root(root, current)
                return current
        current = {"exists": exists, "files": files, "updated_at": now, "count": len(files)}
        self._save_audio_index_root(root, current)
        return current

    def invalidate_audio_index(self, download_dir):
        """Discard one download-root cache entry after a task changes files.

        A completed download used to refresh this index asynchronously.  A new
        subscription check could run before that refresh finished and trust an
        empty, but still valid, cached index.  Dropping the entry makes the
        next check rebuild it from disk instead of reporting every chapter as
        missing.
        """
        root_key = str(Path(download_dir))
        data = self.load_audio_index()
        data.setdefault("roots", {}).pop(root_key, None)
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("DELETE FROM audio_index_files WHERE root = ?", (root_key,))
            conn.execute("DELETE FROM audio_index_meta WHERE root = ?", (root_key,))
        self.save_audio_index(data)

    def index_album_file_count(self, album, download_dir, force=False):
        index = self.build_audio_index(download_dir, force=force)
        files = index.get("files") or []
        if not files:
            return 0
        title_values = album_title_values(album)
        norm_titles = [normalize_match_text(v) for v in title_values if normalize_match_text(v)]
        id_values = album_id_values(album)
        album_plat = album_platform_norm(album)
        count = 0
        skipped = 0  # 标题/ID 命中但被平台过滤跳过的数量（用于 fail-safe）
        for item in files:
            norm_parent = item.get("norm_parent") or ""
            path_text = str(item.get("path") or "")
            norm_path = normalize_match_text(path_text)
            title_id_match = any(
                norm_parent == t or norm_parent in t or t in norm_parent or t in norm_path
                for t in norm_titles
            ) or any(value and value in path_text for value in id_values)
            if not title_id_match:
                continue
            # 跨平台同名专辑：路径含其他平台目录段且与本专辑平台不符时先跳过
            if platform_conflicts(path_platform_norm(path_text, is_file=True), album_plat):
                skipped += 1
                continue
            count += 1
        # fail-safe：平台过滤后为 0 但有同名文件被过滤 → 多为平台名不一致误排除，回退计入
        if count == 0 and skipped:
            count = skipped
        return count

    def indexed_album_files(self, album, download_dir, max_age=3600):
        index = self.build_audio_index(download_dir, max_age=max_age, force=False)
        files, has_album_scope = indexed_album_audio_files(index.get("files") or [], album)
        return files, has_album_scope, index

    def load(self):
        if self._db_has_data():
            try:
                self.load_from_db()
                self._migrate_subscription_ids()
                compacted = self._compact_stored_chapters()
                self.save()
                if compacted:
                    self._vacuum_db()
                return
            except Exception as exc:
                logging.exception("load subscriptions database failed: %s", exc)
        if not self.config_file.exists():
            self.save()
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["settings"].update(loaded.get("settings") or {})
                self.data["settings"].setdefault("enabled", True)
                self.data["settings"].setdefault("auto_download_missing", True)
                self.data["settings"].setdefault("interval_hours", 6)
                self.data["settings"].setdefault("interval_minutes", 0)
                self.data["settings"].setdefault("personal_sync_enabled", False)
                self.data["settings"].setdefault("personal_sync_platform", "ximalaya")
                self.data["settings"].setdefault("personal_sync_interval_hours", 1)
                self.data["settings"].setdefault("personal_sync_interval_minutes", 0)
                self.data["subscriptions"] = loaded.get("subscriptions") or {}
                self.save_to_db()
        except Exception as exc:
            logging.exception("load subscriptions failed: %s", exc)
        self._migrate_subscription_ids()
        compacted = self._compact_stored_chapters()
        self.save()
        if compacted:
            self._vacuum_db()

    def save(self):
        with self._lock:
            try:
                self.config_dir.mkdir(parents=True, exist_ok=True)
                self.save_to_db()
                # SQLite is the transactional source of truth.  The former
                # full JSON mirror duplicated every chapter snapshot and can
                # be recreated through the export endpoint when needed.
                self.config_file.unlink(missing_ok=True)
            except Exception as exc:
                logging.exception("save subscriptions failed: %s", exc)

    def locked(self):
        return self._lock

    def _migrate_subscription_ids(self):
        """Merge legacy alias keys (for example Ximalaya -> 喜马拉雅)."""
        subscriptions = self.data.setdefault("subscriptions", {})
        migrated = {}
        changed = False
        for old_id, item in subscriptions.items():
            record = dict(item or {})
            target_id = self.subscription_id(record.get("album") or record)
            record["id"] = target_id
            if target_id != old_id:
                changed = True
            existing = migrated.get(target_id)
            if existing:
                changed = True
                existing_downloaded = existing.setdefault("downloaded", {})
                existing_downloaded.update(record.get("downloaded") or {})
                existing_chapters = existing.setdefault("chapters", [])
                known = {chapter_key(chapter) for chapter in existing_chapters if isinstance(chapter, dict)}
                for chapter in record.get("chapters") or []:
                    if isinstance(chapter, dict) and chapter_key(chapter) not in known:
                        existing_chapters.append(chapter)
                        known.add(chapter_key(chapter))
                if record.get("updated_at", "") > existing.get("updated_at", ""):
                    existing.update({key: value for key, value in record.items() if value not in (None, "", [], {})})
                continue
            migrated[target_id] = record
        if changed:
            self.data["subscriptions"] = migrated
            self.save()

    def _compact_stored_chapters(self):
        """Migrate existing full API chapter snapshots to the compact format."""
        changed = False
        for item in (self.data.get("subscriptions") or {}).values():
            chapters = item.get("chapters") or []
            compact = self.snapshot_chapters(chapters)
            if compact != chapters:
                item["chapters"] = compact
                changed = True
        return changed

    def _vacuum_db(self):
        """Reclaim SQLite pages after one-time JSON/snapshot compaction."""
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.execute("VACUUM")
        except Exception:
            logging.warning("subscriptions database vacuum failed", exc_info=True)

    def settings(self):
        return dict(self.data.get("settings") or {})

    def update_settings(self, **kwargs):
        settings = self.data.setdefault("settings", {})
        for key, value in kwargs.items():
            if value is not None:
                settings[key] = value
        self.save()

    def subscription_id(self, album):
        platform = canonical_subscription_platform(album.get("platform") or album.get("source"))
        album_id = str(album.get("id") or album.get("album_id") or album.get("book_id") or "").strip()
        if not album_id:
            album_id = sanitize_filename(album.get("title") or "unknown")
        return f"{platform}:{album_id}"

    def is_subscribed(self, album):
        sid = self.subscription_id(album)
        item = self.data.get("subscriptions", {}).get(sid)
        return bool(item and item.get("status", "active") == "active")

    def add_or_update(self, album, chapters=None, download_dir=None):
        chapters = list(chapters or [])
        sid = self.subscription_id(album)
        now = utc_now_iso()
        old = self.data.setdefault("subscriptions", {}).get(sid, {})
        record = dict(old)
        record.update({
            "id": sid,
            "album_id": str(album.get("id") or album.get("album_id") or album.get("book_id") or ""),
            "title": album.get("title") or old.get("title") or "未知专辑",
            "author": album.get("author") or album.get("anchor") or old.get("author") or "",
            "platform": album.get("platform") or old.get("platform") or "",
            "cover": (
                album.get("cover") or album.get("cover_url") or album.get("coverUrl")
                or album.get("cover_path") or album.get("coverPath")
                or album.get("coverLarge") or album.get("coverMiddle") or album.get("coverSmall")
                or album.get("largeCover") or album.get("smallCover")
                or album.get("albumCover") or album.get("albumCoverUrl") or album.get("album_cover") or album.get("album_cover_url")
                or album.get("pic") or album.get("picUrl") or album.get("image") or album.get("imageUrl")
                or album.get("img") or album.get("imgPath")
                or album.get("thumb_url") or album.get("thumb") or album.get("thumbUrl")
                or album.get("thumbnail") or album.get("thumbnailUrl")
                or album.get("itemCoverUrl") or album.get("itemSquareCoverUrl") or album.get("trackCoverPath")
                or album.get("bookCover") or album.get("book_cover")
                or album.get("poster") or album.get("posterUrl")
                or album.get("hts_img")
                or old.get("cover") or ""
            ),
            "source_url": album.get("url") or album.get("link") or album.get("source_url") or old.get("source_url") or "",
            "album": dict(album or old.get("album") or {}),
            "chapters": self.snapshot_chapters(chapters or old.get("chapters") or []),
            "downloaded": {} if old.get("status") == "cancelled" else old.get("downloaded") or {},
            "created_at": old.get("created_at") or now,
            "updated_at": now,
            "last_check_at": old.get("last_check_at") or "",
            "last_message": old.get("last_message") or "已订阅",
            "status": "active",
            "download_dir": download_dir or old.get("download_dir") or "",
        })
        self.data["subscriptions"][sid] = record
        self.save()
        return record

    def cancel(self, subscription_id):
        item = self.data.setdefault("subscriptions", {}).get(subscription_id)
        if not item:
            return False
        item["status"] = "cancelled"
        item["updated_at"] = utc_now_iso()
        self.save()
        return True

    def set_status(self, subscription_id, status):
        item = self.data.setdefault("subscriptions", {}).get(subscription_id)
        if not item:
            return False
        item["status"] = status or "active"
        item["updated_at"] = utc_now_iso()
        self.save()
        return True

    def active_subscriptions(self):
        return [
            item for item in self.data.get("subscriptions", {}).values()
            if item.get("status", "active") == "active"
        ]

    def all_subscriptions(self, include_cancelled=False):
        items = list(self.data.get("subscriptions", {}).values())
        if include_cancelled:
            return items
        return [
            item for item in items
            if item.get("status", "active") == "active"
        ]

    def get(self, subscription_id):
        return self.data.get("subscriptions", {}).get(subscription_id)

    def export_subscriptions(self):
        """导出订阅用于备份/迁移：去掉体积最大的 chapters 章节快照（导入后首次检测会重新拉取），
        保留 album 标识、下载状态(downloaded)、统计等，恢复后不必从头重下。"""
        items = []
        for item in (self.data.get("subscriptions") or {}).values():
            items.append({k: v for k, v in item.items() if k != "chapters"})
        return items

    def import_subscriptions(self, items):
        """从导出数据恢复订阅。按订阅 id 合并覆盖；章节快照留空，待首次检测重新拉取。"""
        if not isinstance(items, list):
            return 0
        subs = self.data.setdefault("subscriptions", {})
        now = utc_now_iso()
        count = 0
        for rec in items:
            if not isinstance(rec, dict):
                continue
            sid = rec.get("id") or self.subscription_id(rec.get("album") or rec)
            if not sid:
                continue
            rec = dict(rec)
            rec["id"] = sid
            rec["chapters"] = []  # 章节快照不随导入恢复，首次检测时重新拉取
            rec.setdefault("downloaded", {})
            rec.setdefault("status", "active")
            rec.setdefault("created_at", now)
            rec["updated_at"] = now
            subs[sid] = rec
            count += 1
        if count:
            self.save()
        return count

    def interval_seconds(self):
        settings = self.settings()
        try:
            hours = max(0, int(settings.get("interval_hours", 6) or 0))
        except Exception:
            hours = 6
        try:
            minutes = max(0, int(settings.get("interval_minutes", 0) or 0))
        except Exception:
            minutes = 0
        total = hours * 3600 + minutes * 60
        # 至少 1 分钟，避免 0 间隔
        return max(60, total)

    def due_subscriptions(self):
        settings = self.settings()
        if not settings.get("enabled", True):
            return []
        cutoff = datetime.utcnow() - timedelta(seconds=self.interval_seconds())
        due = []
        for item in self.active_subscriptions():
            last = parse_iso(item.get("last_check_at"))
            if last is None or last <= cutoff:
                due.append(item)
        return due

    def next_check_at(self, subscription):
        last = parse_iso((subscription or {}).get("last_check_at"))
        if last is None:
            return ""
        return (last + timedelta(seconds=self.interval_seconds())).replace(microsecond=0).isoformat() + "Z"

    def mark_check_error(self, subscription_id, message):
        item = self.get(subscription_id)
        if not item:
            return
        item["last_check_at"] = utc_now_iso()
        item["updated_at"] = utc_now_iso()
        item["last_message"] = message or "检测失败"
        self.save()

    def snapshot_chapters(self, chapters):
        # Persist only fields required for identity, file matching, permission
        # rechecks and a degraded API fallback.  Raw platform responses often
        # carry nested artwork/metadata and were the main cause of an
        # ever-growing subscriptions database.
        fields = (
            "id", "track_id", "trackId", "chapter_id", "chapterId", "program_id", "programId", "acid",
            "title", "name", "chapter_title", "order_num", "order", "index", "sort", "episode",
            "isVip", "is_vip", "vip", "vipOnly", "isPlatinum", "is_platinum", "platinumOnly",
            "isSvip", "is_svip", "svipOnly", "isFree", "is_free", "isAuthorized", "is_authorized",
            "url", "mediaUrl", "playUrlHigh", "playUrlLow", "downloadUrl", "qimao_book_id",
            "_source_missing",
        )
        result = []
        for idx, chapter in enumerate(chapters or [], start=1):
            if not isinstance(chapter, dict):
                continue
            data = {key: chapter[key] for key in fields if chapter.get(key) not in (None, "", [], {})}
            data["_subscription_key"] = chapter_key(data)
            data["_snapshot_order"] = chapter_order(data, idx)
            result.append(data)
        return result

    def diff_chapters(self, subscription, remote_chapters, download_dir, scan_cache=None, skip_local=False, prefer_index=True, retry_restricted=False):
        scan_cache = scan_cache if isinstance(scan_cache, dict) else {}
        saved = subscription.get("chapters") or []
        saved_keys = {chapter_key(ch): ch for ch in saved if isinstance(ch, dict)}
        downloaded = subscription.setdefault("downloaded", {})
        album = subscription.get("album") or subscription
        album_dirs = []
        index = {}
        if skip_local:
            local_files = []
        elif prefer_index:
            local_files, has_album_scope, index = self.indexed_album_files(album, download_dir)
            album_dirs = [Path("__indexed_album_scope__")] if has_album_scope else []
            # An existing index can still be empty or stale (for example, it
            # was created immediately before a download finished).  Fall back
            # to the album-directory scan whenever it found no files, not only
            # when the root directory itself is absent.
            if not local_files:
                album_dirs = album_dir_candidates(album, download_dir, dir_cache=scan_cache.setdefault("dirs", {}))
                local_files = collect_album_audio_files(
                    album,
                    download_dir,
                    dir_cache=scan_cache.setdefault("dirs", {}),
                    file_cache=scan_cache.setdefault("files", {}),
                )
        else:
            album_dirs = album_dir_candidates(album, download_dir, dir_cache=scan_cache.setdefault("dirs", {}))
            local_files = collect_album_audio_files(
                album,
                download_dir,
                dir_cache=scan_cache.setdefault("dirs", {}),
                file_cache=scan_cache.setdefault("files", {}),
            )
        local_index = {} if skip_local else build_local_file_match_index(local_files, has_album_scope=bool(album_dirs))
        missing = []
        matched_keys = set()
        restricted_count = 0
        deferred_failed_count = 0
        new_count = 0
        file_missing_count = 0
        partial_count = 0
        current_source_total = 0
        now = utc_now_iso()
        for idx, chapter in enumerate(remote_chapters or [], start=1):
            if not isinstance(chapter, dict):
                continue
            # A previous API response may have contained this chapter while
            # the current source no longer does.  Keep it as history, but do
            # not turn an unconfirmed stale entry into a download task.
            if chapter.get("_source_missing"):
                continue
            current_source_total += 1
            key = chapter_key(chapter)
            is_new = key not in saved_keys
            state = downloaded.get(key, {})
            restricted_now = is_restricted_chapter(chapter)
            state_restricted = state.get("status") == "restricted"
            # 只有「实际下载失败并确认受限」(confirmed=True，由 mark_download_results 写入)
            # 才算真正受限。仅凭元数据字段(isFree/isAuthorized/isVip 等)判定的受限并不可靠：
            # 能否下载取决于用户 cookie 的会员/已购权限，元数据无法反映。
            # 因此元数据疑似受限的章节仍会进入待下载列表去实际尝试（与手动全选下载一致）。
            confirmed_restricted = bool(state_restricted and state.get("confirmed"))
            state_ok = state.get("status") in ("downloaded", "skipped")
            local_ok = False if skip_local else is_chapter_file_complete(
                album,
                chapter,
                idx,
                download_dir,
                local_files=local_files,
                local_index=local_index,
            )
            if skip_local and state_ok:
                local_ok = True
            if local_ok:
                matched_keys.add(key)
                # 本地已存在 → 清除可能残留的受限标记
                if state_restricted:
                    downloaded.pop(key, None)
                    state = {}
            elif not skip_local and state.get("status") in ("downloaded", "skipped"):
                # 标记为「已下载」但磁盘上实际没有该文件：多为旧版「按数量兜底」(local-count/
                # local-count-full) 把整本书全部章节误标成已下载留下的脏状态。清除它，否则
                # stats(fast 模式按 state 计数) 会持续假报「已下载 N/N、缺失 0」，与本地实际
                # 文件数不符，且该章节会被误认为已完成而漏下。清除后本章按真实缺失走补全。
                downloaded.pop(key, None)
                state = {}
            # 受限章节只在「已实际下载失败并确认受限」(confirmed_restricted) 时才跳过。
            # 仅凭元数据(isFree/isVip 等)判定的受限并不可靠——能否下载取决于用户 cookie 的
            # 会员/已购权限。早期版本额外用 had_state_record(此前有任意下载记录) 一并跳过，
            # 本意是防 VIP 章节反复建任务，但副作用是：有会员、实际可下的最新几集(如付费精品
            # 书的 613/614/615)，只要历史上被误标过任意状态(local-count/failed 等)，就被永久
            # 跳过、检测永远「无需补全」。改为仅认 confirmed：未确认受限的章节正常进入待下载，
            # 实际下载失败会被 mark_download_results 标为 confirmed，下次自然跳过——既不会对
            # 真 VIP 无限重试(失败一次即 confirmed)，也不会漏掉用户实际可下的章节。
            # retry_restricted=True（用户手动点「补全缺失」）时，连已确认受限的章节也强制
            # 重试一次：手动补全是用户明确意图(且其权限/会员可能已变化或之前是误判失败)，
            # 应尝试所有缺失；只有后台自动检测才保持克制、跳过 confirmed 受限避免反复建任务。
            if not local_ok and confirmed_restricted and not retry_restricted:
                try:
                    next_retry_at = float(state.get("next_retry_at") or 0)
                except (TypeError, ValueError):
                    next_retry_at = 0
                # Existing installations have no retry timestamp.  Start the
                # new daily probe window without suddenly queuing every old
                # restricted chapter on upgrade.
                if not next_retry_at:
                    state["next_retry_at"] = time.time() + RESTRICTED_CHAPTER_RETRY_SECONDS
                    next_retry_at = state["next_retry_at"]
                if time.time() < next_retry_at:
                    restricted_count += 1
                    continue
            # A transient failure must not create a new task on every
            # scheduler tick. This also covers unfamiliar platform permission
            # errors until they can be explicitly classified.
            if not local_ok and state.get("status") == "failed" and not retry_restricted:
                try:
                    next_retry_at = float(state.get("next_retry_at") or 0)
                except (TypeError, ValueError):
                    next_retry_at = 0
                if not next_retry_at:
                    failure_count = state.get("failure_count", 1)
                    state["next_retry_at"] = time.time() + failed_chapter_retry_seconds(failure_count)
                    next_retry_at = state["next_retry_at"]
                if time.time() < next_retry_at:
                    deferred_failed_count += 1
                    continue
            if is_new:
                new_count += 1
            if not local_ok:
                file_missing_count += 1
            if state and state.get("status") not in ("downloaded", "skipped") and not local_ok:
                partial_count += 1
            # local_ok=True 是最高优先级：文件确实存在时绝不重复下载，
            # 无论 is_new 还是历史 state 如何（避免跨检测周期的重复下载）。
            if not local_ok:
                item = dict(chapter)
                item["_subscription_key"] = key
                item["_missing_reason"] = "restricted_released" if state_restricted and not restricted_now else "new" if is_new else "missing_or_incomplete"
                missing.append(item)
        file_count = 0 if skip_local else local_index.get("file_count", 0)
        # Invalidate stale index when file_count dropped significantly from remote total
        # (after manual file deletion). Without this, the cached index still shows
        # deleted files and is_chapter_file_complete with the stale index may miss them.
        if not skip_local and file_count > 0 and current_source_total > file_count * 1.1:
            logging.info(
                "diff_chapters: file_count={} < remote_total={}, index stale, invalidating".format(
                    file_count, current_source_total)
            )
            self.invalidate_audio_index(download_dir)
        # 「按数量推断」只在有标题级别的实际匹配（matched_keys 非空）时才生效，
        # 防止跨平台同名专辑（本地文件来自另一平台）让系统误以为已全部下载。
        if not saved_keys and file_count > len(matched_keys) and matched_keys:
            known_local_count = min(current_source_total, file_count)
            assumed_keys = set()
            now = utc_now_iso()
            for idx, chapter in enumerate(remote_chapters or [], start=1):
                if isinstance(chapter, dict) and chapter.get("_source_missing"):
                    continue
                if idx > known_local_count or not isinstance(chapter, dict):
                    break
                key = chapter_key(chapter)
                assumed_keys.add(key)
                downloaded[key] = {"status": "downloaded", "updated_at": now, "source": "local-count"}
            if assumed_keys:
                missing = [chapter for chapter in missing if chapter_key(chapter) not in assumed_keys]
                file_missing_count = max(0, current_source_total - known_local_count)
        # 文件数兜底：本地实际音频文件数已 >= 远端章节总数，说明文件其实都在
        # （常见于重命名/刮削后文件名与远端章节标题对不上，导致逐章匹配漏判大量章节）。
        # 此时不再报缺失，避免每次「补全缺失」都创建一个文件已存在、被全部跳过的无效下载任务。
        current_source_total = current_source_total
        if not skip_local and current_source_total > 0 and file_count >= current_source_total and missing:
            now = utc_now_iso()
            for chapter in remote_chapters or []:
                if not isinstance(chapter, dict):
                    continue
                if chapter.get("_source_missing"):
                    continue
                k = chapter_key(chapter)
                # 不覆盖「已确认受限」状态；其余按本地已存在标记为已下载
                if (downloaded.get(k) or {}).get("status") != "restricted":
                    downloaded[k] = {"status": "downloaded", "updated_at": now, "source": "local-count-full"}
            missing = []
            file_missing_count = 0
        return {
            "missing": self.dedupe_chapters(missing),
            "new_count": new_count,
            "file_missing_count": file_missing_count,
            "partial_count": partial_count,
            "restricted_count": restricted_count,
            "deferred_failed_count": deferred_failed_count,
            "remote_total": current_source_total,
            "saved_total": len(saved),
        }

    def dedupe_chapters(self, chapters):
        seen = set()
        result = []
        for chapter in chapters or []:
            key = chapter_key(chapter)
            if key in seen:
                continue
            seen.add(key)
            result.append(chapter)
        return result

    def update_check_result(self, subscription_id, remote_chapters, diff, message="已检查", scan_cache=None, refresh_local=True):
        item = self.get(subscription_id)
        if not item:
            return
        chapters = remote_chapters or item.get("chapters") or []
        item["chapters"] = self.snapshot_chapters(chapters)
        item["last_check_at"] = utc_now_iso()
        item["updated_at"] = utc_now_iso()
        item["last_message"] = message
        stats = self.refresh_local_stats(item, item.get("download_dir") or "", save=False, scan_cache=scan_cache) if refresh_local and item.get("download_dir") else None
        item["last_diff"] = {
            "missing_count": (stats or {}).get("missing", len(diff.get("missing") or [])),
            "new_count": diff.get("new_count", 0),
            "file_missing_count": diff.get("file_missing_count", 0),
            "partial_count": diff.get("partial_count", 0),
            "restricted_count": diff.get("restricted_count", 0),
            "deferred_failed_count": diff.get("deferred_failed_count", 0),
            "remote_total": diff.get("remote_total", 0) or len(chapters),
        }
        self.save()

    def mark_download_results(self, album, success_chapters=None, failed_chapters=None):
        sid = self.subscription_id(album)
        sid = self.subscription_id(album)
        item = self.get(sid)
        if not item:
            return
        downloaded = item.setdefault("downloaded", {})
        now = utc_now_iso()
        for chapter in success_chapters or []:
            downloaded[chapter_key(chapter)] = {"status": "downloaded", "updated_at": now}
        for chapter in failed_chapters or []:
            restricted = is_permission_denied_failure(chapter)
            key = chapter_key(chapter)
            previous = downloaded.get(key) or {}
            try:
                failure_count = max(0, int(previous.get("failure_count") or 0)) + 1
            except (TypeError, ValueError):
                failure_count = 1
            downloaded[key] = {
                "status": "restricted" if restricted else "failed",
                "updated_at": now,
                "error": chapter.get("_error", "") if isinstance(chapter, dict) else "",
                "reason": "账号权限不足，等待后续章节降级或账号升级" if restricted else "",
                # confirmed=True 表示这是实际下载失败确认的受限（区别于订阅检测时仅凭元数据的预判）。
                # diff_chapters 只跳过 confirmed 的受限章节，避免对真 VIP 无限重试。
                "confirmed": bool(restricted),
                "required_tier": required_access_tier(chapter) if restricted else "",
                "failure_count": failure_count,
                "next_retry_at": time.time() + (
                    RESTRICTED_CHAPTER_RETRY_SECONDS if restricted
                    else failed_chapter_retry_seconds(failure_count)
                ),
            }
        item["updated_at"] = now
        self.save()

    def refresh_local_stats(self, subscription, download_dir, save=True, scan_cache=None):
        scan_cache = scan_cache if isinstance(scan_cache, dict) else {}
        chapters = subscription.get("chapters") or []
        current_chapters = [chapter for chapter in chapters if isinstance(chapter, dict) and not chapter.get("_source_missing")]
        total = len(current_chapters) or int(subscription.get("episodes") or (subscription.get("album") or {}).get("episodes") or 0)
        album = subscription.get("album") or subscription
        states = subscription.get("downloaded") or {}
        restricted_count = sum(1 for state in states.values() if (state or {}).get("status") == "restricted")
        local_files, has_album_scope, index = self.indexed_album_files(album, download_dir)
        if not local_files:
            album_dirs = album_dir_candidates(album, download_dir, dir_cache=scan_cache.setdefault("dirs", {}))
            local_files = collect_album_audio_files(
                album,
                download_dir,
                dir_cache=scan_cache.setdefault("dirs", {}),
                file_cache=scan_cache.setdefault("files", {}),
            )
            has_album_scope = bool(album_dirs)
        local_index = build_local_file_match_index(local_files, has_album_scope=has_album_scope)
        matched = 0
        downloaded = subscription.setdefault("downloaded", {})
        now = utc_now_iso()
        for idx, chapter in enumerate(current_chapters, start=1):
            if is_chapter_file_complete(album, chapter, idx, download_dir, local_files=local_files, local_index=local_index):
                matched += 1
                downloaded[chapter_key(chapter)] = {"status": "downloaded", "updated_at": now, "source": "local"}
        file_count = local_index.get("file_count", 0)
        downloaded_count = min(total, max(matched, file_count))
        restricted_count = sum(1 for state in downloaded.values() if (state or {}).get("status") == "restricted")
        local_stats = {
            "total": total,
            "downloaded": downloaded_count,
            "missing": max(0, total - downloaded_count - restricted_count),
            "restricted": restricted_count,
            "matched": matched,
            "file_count": file_count,
            "updated_at": now,
        }
        subscription["local_stats"] = local_stats
        if save:
            subscription["updated_at"] = now
            self.save()
        return local_stats

    def stats_for(self, subscription, download_dir, fast=False, scan_cache=None):
        chapters = subscription.get("chapters") or []
        current_chapters = [chapter for chapter in chapters if isinstance(chapter, dict) and not chapter.get("_source_missing")]
        total = len(current_chapters) or int(subscription.get("episodes") or (subscription.get("album") or {}).get("episodes") or 0)
        downloaded = 0
        restricted = 0
        if fast:
            states = subscription.get("downloaded") or {}
            state_count = sum(1 for state in states.values() if (state or {}).get("status") in ("downloaded", "skipped"))
            restricted = sum(1 for state in states.values() if (state or {}).get("status") == "restricted")
            local_stats = subscription.get("local_stats") or {}
            try:
                local_count = int(local_stats.get("downloaded") or 0)
            except Exception:
                local_count = 0
            # fast 模式严格不触碰磁盘（不扫描下载目录）：直接用持久化的 local_stats 与内存中的
            # 下载状态计数，保证 /api/subscriptions?fast=1 秒回。精确的本地文件数由后台异步刷新
            # （refresh_subscription_stats_async）写回 local_stats，下次打开即生效。
            downloaded = min(total, max(state_count, local_count))
        else:
            refreshed = self.refresh_local_stats(subscription, download_dir, save=True, scan_cache=scan_cache)
            downloaded = refreshed["downloaded"]
            restricted = int(refreshed.get("restricted") or 0)
        missing = max(0, total - downloaded - restricted)
        # 已补齐：上次检查发现缺失后，本次实际已下载到本地的数量
        last_diff = subscription.get("last_diff") or {}
        try:
            last_missing = int(last_diff.get("missing_count") or 0)
        except Exception:
            last_missing = 0
        completed = max(0, last_missing - missing)
        return {
            "total": total,
            "downloaded": downloaded,
            "missing": missing,
            "restricted": restricted,
            "completed": completed,
            "last_missing": last_missing,
        }
