# -*- coding: utf-8 -*-
"""本地已下载索引 —— 「这一集是不是已经下好了」的**唯一判据出口**。

## 为什么需要这个模块

移植分析 P1-5。参考实现 XimalayaApp `Core/DownloadEngine.cs:170-205` 记录：

> 原先**每集扫一遍目录**，2000 集 = O(n²)，目录大了以后像卡死。

它改成「一次性列目录建索引」（key = 文件名前 5 字符），并且**明确把
`.part` / `.part.s{i}` 排除在索引外**，防止半截文件被误判为成品。

本项目改造前有**两套互不相干的「已存在」判据**：

| 位置 | 判据 |
| --- | --- |
| `download_worker._find_existing_*` | 按本集文件名探测（不扫目录） |
| `subscription_manager.build_audio_index` | 全目录索引 + 300 秒缓存 |

两者不一致的直接后果，`subscription_manager.py:427` 的注释已经承认了：

> ……永远匹配不到、每轮检测都误报缺失并反复创建「文件已存在被跳过」的无效下载。

本模块把判据收敛到一处：**一次性列目录 → 键前缀索引 → 显式排除半截文件**。

## 关键设计

* **键前缀 = 文件名的前 N 个字符**（默认 5，与参考实现一致）。下载产物统一是
  `{序号:04d} {标题}.ext` 形态，所以前 5 字符恰好是 `"0001 "`，
  天然按集号分桶。
* **目录级缓存带 TTL**（默认 300 秒，与 `build_audio_index` 既有行为一致），
  下载过程中每集都重建索引是 N² 的来源，必须缓存。
* **半截文件永不入索引**：`.part` / `.part.s{i}` / `_` 前缀一律跳过。
  这条依赖 `chunked_download.is_partial_file`，全项目一份实现。
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.chunked_download import is_partial_file

#: 键前缀长度。下载产物是 `0001 标题.ext`，前 5 字符 = `"0001 "`。
KEY_PREFIX_LEN = 5

#: 目录索引缓存有效期（秒）。与 subscription_manager 既有行为一致。
DEFAULT_TTL = 300

#: 小于它就认为不是有效产物（与各 manager 的既有阈值同量级）。
MIN_VALID_BYTES = 1024

#: 音频后缀（含各平台实际会落盘的容器）。
AUDIO_EXTENSIONS = (
    ".m4a", ".mp3", ".flac", ".wav", ".aac", ".ogg", ".caf", ".mp4", ".m4b",
)

_INDEX_CACHE: Dict[str, Tuple[float, List[Tuple[str, int]]]] = {}
_CACHE_LOCK = threading.Lock()


def invalidate(directory: Optional[str] = None) -> None:
    """让某个目录（或全部）的缓存失效。

    下载成功后应当调用 `invalidate(album_dir)`，否则紧随其后的
    「是否已下载」判定会读到过期快照。
    """
    with _CACHE_LOCK:
        if directory is None:
            _INDEX_CACHE.clear()
        else:
            _INDEX_CACHE.pop(os.path.abspath(str(directory)), None)


def list_audio_files(directory: str, *, ttl: int = DEFAULT_TTL, use_cache: bool = True) -> List[Tuple[str, int]]:
    """列出一个目录下的**有效音频产物**，返回 `[(完整路径, 字节数), ...]`。

    一次性列目录（O(n)），结果按 TTL 缓存。半截文件与过小文件都被排除。

    ⚠ `directory` 为空时必须**直接返回空**，不能让它落到 `abspath("")` ——
    那会解析成当前工作目录，于是「没传目录」变成了「把项目根目录当成专辑目录」，
    既慢又可能把无关文件当成已下载产物（真踩过）。
    """
    raw = str(directory or "").strip()
    if not raw:
        return []
    key = os.path.abspath(raw)

    now = time.time()
    if use_cache:
        with _CACHE_LOCK:
            hit = _INDEX_CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return list(hit[1])

    entries: List[Tuple[str, int]] = []
    try:
        with os.scandir(key) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                name = entry.name
                if is_partial_file(name):
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size < MIN_VALID_BYTES:
                    continue
                entries.append((entry.path, size))
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        entries = []

    if use_cache:
        with _CACHE_LOCK:
            _INDEX_CACHE[key] = (now, list(entries))
    return entries


def build_index(directory: str, *, ttl: int = DEFAULT_TTL, use_cache: bool = True) -> Dict[str, List[Tuple[str, int]]]:
    """建键前缀索引：`{"0001 ": [(路径, 大小), ...], ...}`。

    这正是参考实现 `BuildExistingIndex` 的做法 —— 用空间换掉「每集扫一遍目录」
    的 O(n²)。
    """
    index: Dict[str, List[Tuple[str, int]]] = {}
    for path, size in list_audio_files(directory, ttl=ttl, use_cache=use_cache):
        name = os.path.basename(path)
        bucket = name[:KEY_PREFIX_LEN]
        index.setdefault(bucket, []).append((path, size))
    return index


def find_existing(
    index: Dict[str, List[Tuple[str, int]]],
    order: int,
    title: str,
    *,
    extensions: Optional[Iterable[str]] = None,
    probe_len: int = 20,
    min_bytes: int = MIN_VALID_BYTES,
) -> Optional[str]:
    """在索引里按「集号 + 标题前缀」找已下载文件。

    对应参考实现 `FindExisting`：先按 `{序号:04d} ` 定位桶，再比对文件名中
    是否包含标题前 `probe_len` 个字符。

    :param title: 章节标题（会被清洗成与落盘文件名一致的形态由调用方负责；
        这里只做「包含」比对，所以传入原始标题也能命中）。
    """
    if not index:
        return None
    if order is None or int(order) <= 0:
        return None

    bucket_key = f"{int(order):04d} "[:KEY_PREFIX_LEN]
    bucket = index.get(bucket_key)
    if not bucket:
        return None

    probe = _normalize_probe(title, probe_len)
    allowed = tuple(e.lower() for e in (extensions or AUDIO_EXTENSIONS))

    for path, size in bucket:
        if size < min_bytes:
            continue
        name = os.path.basename(path)
        stem, ext = os.path.splitext(name)
        if allowed and ext.lower() not in allowed:
            continue
        if not probe:
            return path
        # ⚠ 磁盘文件名带着「0003 」集号前缀（本索引正是按它分桶的），
        #   直接拿去和**章节标题**比会永远不相等（真踩过：
        #   `test_prefix_probe_matches_shortened_title`）。
        #   必须先剥掉前 KEY_PREFIX_LEN 个字符再比标题。
        title_stem = stem[KEY_PREFIX_LEN:] if len(stem) > KEY_PREFIX_LEN else stem
        disk_stem = _normalize_probe(title_stem, max_len=None)
        query = _normalize_probe(title, max_len=None)
        # ⚠ 三种命中形态，缺一不可。每个分支都要求**非空**，否则
        #   「空串 in 任意串」恒为真，会把完全不同的标题误判成命中 ——
        #   那是比重复下载严重得多的 bug（真实缺失被永久漏掉）。
        #   ① 查询标题的前 probe_len 字符出现在磁盘名里（注释/后缀差异）
        #   ② 磁盘名主干出现在查询标题里（磁盘侧标题更短或被截断过）
        #   ③ 两者共享一段足够长的前缀（两侧被**分别**截断过，见本测试用例）
        if probe and probe in disk_stem:
            return path
        if disk_stem and disk_stem in query:
            return path
        if (
            disk_stem
            and query
            and _shared_prefix_len(disk_stem, query) >= min(probe_len, len(disk_stem), len(query))
        ):
            return path
    return None


def _shared_prefix_len(a: str, b: str) -> int:
    """两个字符串的公共前缀长度。"""
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


def find_by_stem(
    directory: str,
    stem: str,
    *,
    extensions: Optional[Iterable[str]] = None,
    min_bytes: int = MIN_VALID_BYTES,
    use_cache: bool = True,
) -> Optional[str]:
    """按「同名不同后缀」找已下载文件（番茄、喜马拉雅档位纠正的场景）。

    这是 `download_worker._find_existing_fanqie_output` 的推广版：
    那些方法是在**候选路径**上逐个 `os.path.exists`（最多 4 次 syscall）；
    本函数查的是已经建好的目录索引，**零额外 syscall**。

    ⚠ 比对的是**去后缀的主干**：`stem` 通常带着候选后缀（`0001 第一集.m4a`），
    而磁盘上可能是另一个后缀（`0001 第一集.mp3`）。直接拿带后缀的 basename
    去比会永远不相等（真踩过 —— `test_finds_same_stem_different_extension`）。
    """
    if not stem:
        return None
    target = os.path.abspath(str(stem))
    directory = os.path.dirname(target)
    # 去掉候选路径自己的后缀，只留主干用于比对
    base = os.path.splitext(os.path.basename(target))[0]
    allowed = {str(e).lower() for e in (extensions or AUDIO_EXTENSIONS)}

    for path, size in list_audio_files(directory, use_cache=use_cache):
        if size < min_bytes:
            continue
        name_stem, ext = os.path.splitext(os.path.basename(path))
        if ext.lower() not in allowed:
            continue
        if name_stem == base:
            return path
    return None


def _normalize_probe(text: Any, max_len: Optional[int] = 20) -> str:
    """把标题/文件名归一化成可做「包含」比对的形式。

    ⚠ 只做**保守**归一化：去掉首尾空白、把连续空白压成一个空格。
    不做大小写折叠、不删标点 —— 那会让「第1集」和「第 1 集」这类本该区分的
    文件名互相误命中，导致**真实缺失被漏判**（那是比重复下载严重得多的 bug）。
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_len is not None and len(value) > max_len:
        value = value[:max_len]
    return value


__all__ = [
    "KEY_PREFIX_LEN",
    "DEFAULT_TTL",
    "MIN_VALID_BYTES",
    "AUDIO_EXTENSIONS",
    "invalidate",
    "list_audio_files",
    "build_index",
    "find_existing",
    "find_by_stem",
]
