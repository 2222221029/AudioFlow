#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""酷我听书（TME 系）整专辑下载器（纯 Python，无需登录）。

背景
----
喜马拉雅上部分大 IP 有声剧（如《大奉打更人》边江工作室/TME有声剧场版）被
TME 内容保护加密，无法以普通音频导出。**同一套内容在酷我听书（TME 自家平台）
以明文 MP3/FLAC 分发**（项目内 KuwoManager 直接返回直链，固定 Cookie/Secret，
无需登录）。本工具把该链路做成整专辑批量下载器，用于这类「平台间同内容」
的合规下载。不涉及任何解密/DRM 绕过。

用法
----
    python scripts/download_kuwo_album.py \
        --keyword 大奉打更人 --out /path/to/save \
        [--quality standard|high|lossless] \
        [--album 60135077] [--start 1] [--limit 0] [--dry-run]

说明
----
* ``--keyword`` 搜索并列出候选专辑（含 id），交互选定；也可 ``--album`` 直连；
* 章节列表走 KuwoManager.get_chapters（自带并发防错位+串行重抓），保证全集；
* 已存在且校验通过的文件自动跳过（断点续传）；
* 章节间默认间隔 2s，适配酷我限流（实测高频连续请求会返回「获取失败」）；
* 下载后按文件头校验（ID3/fLaC），残缺文件当场删除；
* 结果清单写入保存目录 result.json（不含任何凭证）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_INTERVAL_MIN = 1.2
_OK_HEADS = (
    b"ID3",                                  # MP3（ID3v2 头）
    b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",   # MP3 帧头（无 ID3 时）
    b"fLaC",                                 # FLAC
    b"\x00\x00\x00\x18ftyp",                 # M4A/MP4
)


def _sanitize(name: str, limit: int = 80) -> str:
    cleaned = "".join(ch for ch in str(name or "")
                      if ch not in '\\/:*?"<>|\r\n\t').strip() or "章节"
    return cleaned[:limit].strip()


def _looks_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64 * 1024:
        return False
    head = path.read_bytes()[:16]
    return any(head.startswith(pre) for pre in _OK_HEADS)


def resolve_album(manager, keyword: str = "", album_id: str = "") -> str:
    """选定专辑：直接 id 或关键词搜索后交互选择。"""
    if album_id:
        return str(album_id)
    if not keyword:
        raise SystemExit("需要 --album 或 --keyword")
    found = manager.search_books(keyword, limit=10) or []
    if not found:
        raise SystemExit(f"酷我搜索「{keyword}」无结果")
    print(f"搜索「{keyword}」候选专辑：")
    for index, book in enumerate(found, start=1):
        print(f"  [{index}] id={book.get('id')} 集数={book.get('episodes')}  "
              f"{str(book.get('title'))[:58]} ｜ {str(book.get('author'))[:36]}")
    choice = input("输入序号选择专辑（回车默认 1）：").strip()
    try:
        return str(found[int(choice) - 1]["id"] if choice else found[0]["id"])
    except (ValueError, IndexError):
        raise SystemExit("序号无效")


def main() -> int:
    parser = argparse.ArgumentParser(description="酷我听书整专辑下载器")
    parser.add_argument("--keyword", default="", help="搜索关键词（交互选专辑）")
    parser.add_argument("--album", default="", help="专辑 id（跳过搜索）")
    parser.add_argument("--out", required=True, help="保存目录")
    parser.add_argument("--quality", default="standard",
                        choices=("standard", "high", "lossless"), help="音质档位")
    parser.add_argument("--start", type=int, default=1, help="起始章节序号（1 起）")
    parser.add_argument("--limit", type=int, default=0, help="集数上限（0=全部）")
    parser.add_argument("--interval", type=float, default=2.0, help="章节间间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只列出章节不下载")
    args = parser.parse_args()

    from core.kuwo_manager import KuwoManager
    manager = KuwoManager()
    album_id = resolve_album(manager, args.keyword, args.album)
    print(f"== 专辑 {album_id} 音质={args.quality} ==")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = max(_INTERVAL_MIN, float(args.interval))

    # 全集章节：get_chapters 内部自带并发防错位分页，一次 page_size 取全
    detail = manager.get_book_detail(album_id) or {}
    total = int(detail.get("total_chapters") or detail.get("episodes") or 0)
    print(f"专辑总集数: {total}")
    page_size = max(3000, total or 3000)
    try:
        chapters = manager.get_chapters(album_id, page=1, page_size=page_size) or []
    except Exception as exc:
        print(f"❌ 获取章节失败: {exc}")
        return 1

    seen = set()
    unique = []
    for ch in chapters:
        rid = str(ch.get("id") or ch.get("kuwo_rid") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        unique.append(ch)
    unique.sort(key=lambda c: (int(c.get("order_num") or 0)
                               if str(c.get("order_num") or "").isdigit() else 0))
    print(f"实际取到章节: {len(unique)}")

    selected = [c for c in unique if int(c.get("order_num") or 0) >= args.start]
    if args.limit:
        selected = selected[: args.limit]
    print(f"待处理: {len(selected)} 集")

    if args.dry_run:
        for ch in selected[:30]:
            print(f"  #{ch.get('order_num'):>4}  {str(ch.get('title'))[:44]}")
        return 0

    results = {"album": album_id, "quality": args.quality, "items": []}
    ok_count = fail_count = skip_count = 0
    for index, ch in enumerate(selected, start=1):
        num = int(ch.get("order_num") or index)
        rid = str(ch.get("id") or ch.get("kuwo_rid") or "")
        title = _sanitize(ch.get("title") or f"第{num}集")
        print(f"[{index}/{len(selected)}] #{num} {title}")

        info = manager.get_download_info(rid, args.quality)
        if not info or not info.get("url"):
            fail_count += 1
            err = getattr(manager, "last_error", "") or "无下载地址"
            print(f"  ❌ {err[:100]}")
            results["items"].append({"order": num, "rid": rid, "status": "failed",
                                     "error": err})
            time.sleep(interval)
            continue

        ext = str(info.get("extension") or ".mp3")
        final = out_dir / f"{num:04d}_{rid}_{title}{ext}"
        if _looks_valid(final):
            skip_count += 1
            print(f"  ⏭ 已存在 {final.name}")
            results["items"].append({"order": num, "rid": rid, "status": "skipped"})
            continue

        if not manager.download_audio(info["url"], str(final), chapter_id=rid):
            fail_count += 1
            err = getattr(manager, "last_error", "") or "下载失败"
            print(f"  ❌ {err[:100]}")
            results["items"].append({"order": num, "rid": rid, "status": "failed",
                                     "error": err})
            time.sleep(interval)
            continue

        if not _looks_valid(final):
            final.unlink(missing_ok=True)
            fail_count += 1
            print("  ❌ 文件校验失败，已删除")
            results["items"].append({"order": num, "rid": rid, "status": "failed",
                                     "error": "文件校验失败"})
        else:
            ok_count += 1
            print(f"  ✅ {final.name} {final.stat().st_size / 1048576:.2f}MB "
                  f"(bitrate={info.get('bitrate')})")
            results["items"].append({"order": num, "rid": rid, "status": "ok",
                                     "size": final.stat().st_size})
        time.sleep(interval)

    result_path = out_dir / "result.json"
    try:
        result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except OSError:
        pass
    print(f"\n== 汇总：成功 {ok_count} / 失败 {fail_count} / 跳过 {skip_count}"
          f"（清单 {result_path}）==")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())