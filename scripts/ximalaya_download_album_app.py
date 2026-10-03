#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""喜马拉雅专辑「App 通道」下载器（纯 Python，无需真机/Frida）。

适用场景
--------
电脑版下载/v2 通道被版权方关闭的专辑（大 IP 有声剧《大奉打更人》130326634
等，服务端返回「该内容暂不支持下载！」），但**手机 App 内允许下载**。
本工具复用 App 移动端 V4 授权链路：

    web 登录 Cookie（1&_token）
        │  ximalaya_universal_login.derive_universal_credentials()
        ▼
    App 移动端 Cookie（1&_device=android&<UUID>&版本） + 本地生成的 x-tk
        │  mobile/v1/album/track/v3 列章节
        │  mobile-playpage/track/v4/baseInfo + x-tk 取址
        ▼
    真实播放直链 → 落盘

用法
----
    AUDIOFLOW_DISABLE_PC_LIVE_SIGN=0 \
    python scripts/ximalaya_download_album_app.py \
        --cookie-file /path/to/cookie.txt \
        --album 130326634 \
        --out /path/to/save \
        [--start 1] [--limit 20] [--levels 3,2,1,0] [--page-size 50]

约定
----
* Cookie 文件内容为浏览器 Cookie 一行串（以 ``;`` 分隔），其中须含
  ``1&_token=<uid>&<hex>``；
* 虚拟设备号持久化在 ``config/ximalaya_app_device.json``，同一台"手机"身份
  跨次复用（不要在每次运行都换新设备，容易触发风控）；
* 已存在的文件自动跳过（断点续传）；档位链由高到低降级（仅"无该档位"降级）；
* 每次取址之间默认间隔 1.5s，专辑章节并发由内部信号量限流（_MOBILE_V4 语义）；
* 完成/失败清单输出到运行目录 result.json（不落盘真实 Cookie）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 保证线上/移动通道按生产默认行为运行（本工具自建运行时环境）
os.environ.pop("AUDIOFLOW_DISABLE_PC_LIVE_SIGN", None)


def _persisted_device_uuid() -> str:
    """持久化虚拟设备号：config 目录 ximalaya_app_device.json。"""
    try:
        from core.platform_config import config_dir
        path = config_dir() / "ximalaya_app_device.json"
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return value
    except Exception:
        return str(uuid.uuid4())


def _load_cookie(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if "1&_token=" not in value:
        raise SystemExit(f"Cookie 文件缺少 1&_token（{path}），无法派生 App 凭证")
    return value


def _safe_name(title: str, max_len: int = 60) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(title or "")).strip() or "未命名"
    return cleaned[:max_len]


def _check_media(path: Path) -> tuple:
    """返回 (大小, 是否像音频容器, 备注)。加密/试听/损坏文件提前识别。"""
    size = path.stat().st_size if path.exists() else 0
    try:
        head = path.read_bytes()[:16]
    except OSError:
        head = b""
    okay = False
    note = ""
    if size < 64 * 1024:
        note = "文件过小（疑似试听/错误响应）"
    elif head[4:8] == b"ftyp":
        okay = True
    elif head[:3] == b"ID3" or head[8:11] in (b"mp3",):
        okay = True
    elif head[:4] == b"OggS":
        note = "Ogg 容器（可能是加密/分片流）"
    else:
        note = "头部非标准音频容器（疑似 DRM 加密/需 App 内播放）"
    return size, okay, note


def main() -> int:
    parser = argparse.ArgumentParser(description="喜马拉雅专辑 App 通道下载器")
    parser.add_argument("--cookie-file", required=True, help="浏览器 Cookie 一行串文件（须含 1&_token）")
    parser.add_argument("--album", required=True, help="专辑 ID")
    parser.add_argument("--out", required=True, help="保存目录")
    parser.add_argument("--start", type=int, default=1, help="起始章节序号（1 起）")
    parser.add_argument("--limit", type=int, default=0, help="下载集数上限（0=全部）")
    parser.add_argument("--levels", default="3,2,1,0", help="V4 档位降级链（默认 3,2,1,0）")
    parser.add_argument("--page-size", type=int, default=50, help="章节分页大小")
    parser.add_argument("--interval", type=float, default=1.5, help="章节间间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只列章节不下载")
    args = parser.parse_args()

    cookie = _load_cookie(args.cookie_file)
    token = next((s.split("=", 1)[1].strip() for s in cookie.split(";")
                  if s.strip().startswith("1&_token=")), "")
    uid = token.split("&", 1)[0] if token else ""
    print(f"== 账号 uid={uid} 专辑={args.album} ==")

    device_uuid = _persisted_device_uuid()
    print(f"设备号(持久化): {device_uuid}")

    from core.ximalaya_manager import XimalayaManager
    from core.ximalaya_universal_login import derive_universal_credentials
    from core.ximalaya_download_manager import XimalayaDownloadManager

    bundle = derive_universal_credentials(token, device_uuid=device_uuid)
    mobile = bundle["mobile_credentials"]
    print("移动凭证: x_tk=" + (str(mobile.get("x_tk", ""))[:16] + "…" if mobile.get("x_tk") else "缺失"))

    manager = XimalayaDownloadManager(cookie_string=cookie, mobile_credentials=mobile)
    mgr = XimalayaManager()
    mgr.set_cookie("xmly_web", cookie)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = [int(x) for x in args.levels.split(",") if x.strip().isdigit()]

    chapters, total = mgr._fetch_chapters_mobile_v3(
        args.album, page=1, page_size=args.page_size)
    print(f"章节总数: {total}（本页 {len(chapters)}，如需跨页本工具按顺序翻页）")

    # 顺序翻页直至覆盖 start+limit
    all_chapters = list(chapters)
    page = 2
    while all_chapters and len(all_chapters) < (args.start + args.limit - 1 if args.limit else total):
        more, _ = mgr._fetch_chapters_mobile_v3(args.album, page=page, page_size=args.page_size)
        if not more:
            break
        all_chapters.extend(more)
        page += 1
        if len(all_chapters) >= total:
            break

    selected = [ch for ch in all_chapters if ch["order_num"] >= args.start]
    if args.limit:
        selected = selected[: args.limit]
    print(f"待处理章节: {len(selected)}")

    if args.dry_run:
        for ch in selected[:20]:
            paid = bool(ch.get("is_paid")) or bool(ch.get("is_vip"))
            print(f"  #{ch['order_num']:>4} track={ch['id']} paid={paid} {ch['title'][:40]}")
        return 0

    results = {"album": args.album, "uid": uid, "items": []}
    ok_count = fail_count = skip_count = 0
    for index, ch in enumerate(selected, start=1):
        title = _safe_name(ch["title"])
        base = out_dir / f"{ch['order_num']:04d}_{ch['id']}_{title}"
        done_path = base.with_suffix(".m4a")
        if done_path.exists() and done_path.stat().st_size > 64 * 1024:
            skip_count += 1
            print(f"[{index}/{len(selected)}] ⏭ 已存在: {done_path.name}")
            results["items"].append({"order": ch["order_num"], "track": ch["id"],
                                     "status": "skipped"})
            continue

        target = done_path  # ext 会在成功后按容器纠正
        ok = False
        err = ""
        for level in levels:
            attempt_target = base.with_suffix(f".l{level}.m4a")
            if manager._download_mobile_quality(
                ch["id"], str(attempt_target), level, ch["title"]):
                ok = True
                break
            err = manager.last_error or "未知错误"
            if manager.last_error_type != "restricted":
                break  # 网络/风控/完整性失败不降级，交给上层重试
        if not ok:
            fail_count += 1
            print(f"[{index}/{len(selected)}] ❌ #{ch['order_num']} {err[:120]}")
            results["items"].append({"order": ch["order_num"], "track": ch["id"],
                                     "status": "failed", "error": err})
            time.sleep(max(0.5, args.interval))
            continue

        # 定位实际写入的文件并纠正扩展名/校验
        written = None
        for suffix in [".l3.m4a", ".l2.m4a", ".l1.m4a", ".l0.m4a", ".m4a"]:
            cand = base.with_suffix(suffix)
            if cand.exists():
                written = cand
                break
        if written:
            size, okay, note = _check_media(written)
            if not okay:
                fail_count += 1
                written.unlink(missing_ok=True)
                print(f"[{index}/{len(selected)}] ⚠️ #{ch['order_num']} 校验失败: {note}")
                results["items"].append({"order": ch["order_num"], "track": ch["id"],
                                         "status": "failed", "error": note})
            else:
                final = base.with_suffix(".mp3" if written.suffix == ".mp3" else ".m4a")
                if written != final:
                    written.rename(final)
                ok_count += 1
                print(f"[{index}/{len(selected)}] ✅ #{ch['order_num']} "
                      f"{final.name} {size/1048576:.2f}MB "
                      f"(source={manager.last_download_source})")
                results["items"].append({"order": ch["order_num"], "track": ch["id"],
                                         "status": "ok", "size": size,
                                         "source": manager.last_download_source})
        time.sleep(max(0.5, args.interval))

    result_path = out_dir / "result.json"
    try:
        result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except OSError:
        pass
    print(f"\n== 汇总: 成功 {ok_count} / 失败 {fail_count} / 跳过 {skip_count}"
          f"（清单 {result_path}）==")
    if fail_count:
        print("失败原因含「暂不支持下载/未授权」多为内容版权或账号权益限制。")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())