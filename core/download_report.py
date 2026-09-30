# -*- coding: utf-8 -*-
"""专辑下载报告侧车（`_report.json`）—— 失败集持久化 + 失败集重试。

## 移植来源

参考实现 XimalayaApp `Core/DownloadEngine.cs:478-491`：每次下载结束在专辑目录
写一份 `_report.json`：

    {"album","source","quality","done","skipped","failed","bytes",
     "finishedAt","errors":[...]}

配合 `DownloadHistory`（记录 `FailedTracks`）与
`AlbumTaskRow.RetryVisible`（`:536`）实现「只重试失败的那几集」。

## 为什么值钱

本项目改造前**没有失败清单侧车**：`src/server/web_server.py:3943` 的
`api_download_retry_failed` 注释自承：

> Re-run the original chapter set so this task keeps coherent totals. The worker
> skips valid files that already exist and only downloads the gaps.

也就是**靠「重跑全集 + 跳过已存在」来近似**。代价是每次重试都要：

* 重新枚举整个专辑的章节列表（几百到几千集的接口往返）；
* 对每一集做一次「已存在」探测；
* 遇到已经缺失的集还会再失败一遍。

有了失败清单，重试可以直接定位到那几集。

## 参考实现的一条硬教训（照抄）

`DownloadEngine.cs:534-536` 的注释：

> 判据必须带上 `FailedTracks`：异常终止（引擎抛错）时只有计数、没有失败集，
> 光看 `_failed` 会渲染出一个**点了没反应**的按钮。

所以本模块的 `has_retryable()` 同时检查计数与清单，两者不一致时以清单为准。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

#: 侧车文件名。与参考实现一致。
REPORT_FILENAME = "_report.json"

#: schema 版本。将来结构变化时用它可以做兼容读取。
REPORT_SCHEMA = 1

#: 侧车文件保留的失败条目上限 —— 防止 5000 集的专辑把 JSON 撑到几 MB。
MAX_FAILED_ENTRIES = 2000

#: 错误消息截断长度（与项目其它地方的 200 保持一致）。
MAX_ERROR_LEN = 200


def report_path(album_dir: str) -> str:
    return os.path.join(str(album_dir), REPORT_FILENAME)


def build_report(
    *,
    album_id: str = "",
    album_title: str = "",
    platform: str = "",
    quality: str = "",
    task_id: str = "",
    total: int = 0,
    done: int = 0,
    skipped: int = 0,
    failed: int = 0,
    total_bytes: int = 0,
    failed_items: Optional[Iterable[Dict[str, Any]]] = None,
    errors: Optional[Iterable[str]] = None,
    state: str = "",
) -> Dict[str, Any]:
    """构造报告字典。字段名与参考实现对齐（便于将来交叉比对）。"""
    failed_list: List[Dict[str, Any]] = []
    for item in list(failed_items or [])[:MAX_FAILED_ENTRIES]:
        if not isinstance(item, dict):
            continue
        failed_list.append({
            "id": str(item.get("id") or item.get("chapter_id") or ""),
            "order": _as_int(item.get("order") or item.get("order_num")),
            "title": str(item.get("title") or "")[:200],
            "error": str(item.get("error") or item.get("_error") or "")[:MAX_ERROR_LEN],
            "error_type": str(item.get("error_type") or item.get("_error_type") or ""),
        })

    return {
        "schema": REPORT_SCHEMA,
        "album_id": str(album_id or ""),
        "album_title": str(album_title or ""),
        "platform": str(platform or ""),
        "quality": str(quality or ""),
        "task_id": str(task_id or ""),
        "state": str(state or ""),
        "total": _as_int(total),
        "done": _as_int(done),
        "skipped": _as_int(skipped),
        "failed": _as_int(failed),
        "bytes": _as_int(total_bytes),
        "failedAt": "",
        "finishedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "failed_items": failed_list,
        "errors": [str(e)[:MAX_ERROR_LEN] for e in list(errors or [])[:MAX_FAILED_ENTRIES]],
    }


def write_report(album_dir: str, report: Dict[str, Any]) -> Optional[str]:
    """原子写入侧车（先写 `.tmp` 再 `os.replace`）。

    ⚠ 写侧车**绝不能**让下载失败：任何异常都吞掉并返回 None。
    侧车是「方便重试」的加速器，不是下载正确性的一部分。
    """
    if not album_dir:
        return None
    path = report_path(album_dir)
    temp = path + ".tmp"
    try:
        os.makedirs(str(album_dir), exist_ok=True)
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        os.replace(temp, path)
        return path
    except Exception:  # noqa: BLE001
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
        return None


def read_report(album_dir: str) -> Optional[Dict[str, Any]]:
    """读侧车；不存在 / 损坏 / schema 不认识都返回 None（绝不抛）。"""
    path = report_path(album_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if _as_int(data.get("schema")) not in (0, REPORT_SCHEMA):
        # 未来版本写的侧车：结构可能不兼容，宁可不读
        return None
    return data


def failed_items(album_dir: str) -> List[Dict[str, Any]]:
    """取出侧车里的失败清单（读不到就返回空列表）。"""
    report = read_report(album_dir)
    if not report:
        return []
    items = report.get("failed_items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def has_retryable(report: Optional[Dict[str, Any]]) -> bool:
    """是否值得显示「重试失败」入口。

    ⚠ 与参考实现 `DownloadEngine.cs:536` 同一条判据：**必须同时**有计数与清单。
    只有计数没有清单（引擎异常终止的形态）时，任何「重试」按钮都点了没反应，
    所以这里返回 False —— 宁可少显示一个按钮，也不给一个死按钮。
    """
    if not report:
        return False
    if _as_int(report.get("failed")) <= 0:
        return False
    items = report.get("failed_items")
    return isinstance(items, list) and len(items) > 0


def retry_selection(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把侧车翻译成「只重试这些集」的选择条件。

    返回 `{"chapter_ids": [...], "orders": [...], "count": N}`，
    没有可重试项时返回 None。

    上层据此**跳过章节枚举**，直接按 id/序号定位要重下的集。
    """
    if not has_retryable(report):
        return None
    items = report.get("failed_items") or []
    ids = [str(i.get("id")) for i in items if i.get("id")]
    orders = [int(i["order"]) for i in items if _as_int(i.get("order")) > 0]
    if not ids and not orders:
        return None
    return {
        "chapter_ids": ids,
        "orders": orders,
        "count": len(items),
        "error_types": sorted({str(i.get("error_type") or "") for i in items if i.get("error_type")}),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "REPORT_FILENAME",
    "REPORT_SCHEMA",
    "MAX_FAILED_ENTRIES",
    "report_path",
    "build_report",
    "write_report",
    "read_report",
    "failed_items",
    "has_retryable",
    "retry_selection",
]
