#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""喜马拉雅「网页版接口」预检工具：验证某个网页登录 Cookie 能否解锁目标专辑
的网页取流（防盗链 isAntiLeech 内容匿名不返回地址；本工具用 Cookie 直接探测
web v3 baseInfo，并判定返回 URL 的形态：明文直链 / 可解密 / 疑似 TME 密文）。

用法
----
    python scripts/ximalaya_web_cookie_probe.py \
        --cookie-file /path/to/cookie.txt \
        [--album 130326634] [--track 1017785310] [--levels 2,1,0]

Cookie 文件内容为浏览器 Cookie 一行串（``;`` 分隔），须含 ``1&_token=``。
只探测不落盘任何文件。结论：
  * playable=<url 形态>          -> 网页通道可直接下载
  * restricted=未配置/无权限      -> 网页接口不放地址（配置/授权问题）
  * restricted=TME/DRM 密文      -> 地址加密且内置密钥解不开，无明文出口
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def load_cookie(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if "1&_token=" not in value:
        raise SystemExit(f"Cookie 文件缺少 1&_token（{path}），喜马拉雅网页登录态无效")
    return value


def uid_of(cookie: str) -> str:
    token = next((s.split("=", 1)[1].strip() for s in cookie.split(";")
                  if s.strip().startswith("1&_token=")), "")
    return token.split("&", 1)[0] if token else "?"


def classify_item(item: dict, dm) -> tuple:
    """返回 (形态, 直链, 说明)。"""
    encrypted = str(item.get("url") or "").strip()
    ql = item.get("qualityLevel")
    label = f"type={item.get('type')} level={ql}"
    if not encrypted:
        return "empty", None, label
    if encrypted.startswith(("http://", "https://")):
        return "cdn-direct", encrypted, label
    decrypted = dm._decrypt_play_url_candidates(encrypted)
    if decrypted:
        return "decryptable", decrypted, label
    return "undecryptable(疑似TME/DRM)", None, label


def main() -> int:
    parser = argparse.ArgumentParser(description="喜马拉雅网页 Cookie 预检（只探测不下载）")
    parser.add_argument("--cookie-file", required=True)
    parser.add_argument("--album", default="130326634",
                        help="专辑 ID（配合 --track 缺省取专辑第一章）")
    parser.add_argument("--track", default="", help="单集 trackId（优先于 --album）")
    parser.add_argument("--levels", default="2,1,0", help="trackQualityLevel 列表")
    args = parser.parse_args()

    cookie = load_cookie(args.cookie_file)
    uid = uid_of(cookie)
    print(f"== Cookie uid={uid} ==")

    from core.ximalaya_download_manager import XimalayaDownloadManager
    from core.ximalaya_manager import XimalayaManager

    dm = XimalayaDownloadManager(cookie_string=cookie)
    mgr = XimalayaManager()
    mgr.set_cookie("xmly_web", cookie)

    track_id = args.track
    if not track_id:
        chapters, total = mgr._fetch_chapters_mobile_v3(
            args.album, page=1, page_size=5)
        if not chapters:
            print("❌ 拿不到章节（App v3 接口异常），请用 --track 直接指定单集")
            return 2
        track_id = str(chapters[0]["id"])
        print(f"专辑 {args.album} 共 {total} 集，取第一章 track={track_id}")

    verdict = None
    for level in [int(x) for x in args.levels.split(",") if x.strip().isdigit()]:
        print(f"\n== web v3 baseInfo level={level} track={track_id} ==")
        track_info, data = dm._request_web_track_info(track_id)
        if track_info is None:
            print(f"  ❌ {dm.last_error}")
            verdict = f"接口失败: {dm.last_error}"
            continue
        current_uid = str((data.get("extendInfo") or {}).get("currentUid") or "0")
        info = {
            "isPaid": track_info.get("isPaid"),
            "isFree": track_info.get("isFree"),
            "isAuthorized": track_info.get("isAuthorized"),
            "isAntiLeech": track_info.get("isAntiLeech"),
            "hqNeedVip": track_info.get("hqNeedVip"),
            "currentUid": current_uid,
        }
        print(f"  字段: {info}")
        items = track_info.get("playUrlList") or []
        print(f"  playUrlList: {len(items)} 条")
        for item in items:
            shape, direct, label = classify_item(item, dm)
            print(f"    [{shape}] {label}"
                  + (f" url={direct[:110]}" if direct else ""))
        if not items:
            if current_uid in ("", "0"):
                verdict = "restricted=网页Cookie未生效（currentUid=0），防盗链内容不放地址"
            else:
                verdict = "restricted=接口未返回地址（账号无该章播放权限或防盗链不放行）"
            print(f"  → 结论: {verdict}")
        else:
            shapes = [classify_item(i, dm)[0] for i in items]
            if any(s in ("cdn-direct", "decryptable") for s in shapes):
                good = next(classify_item(i, dm)[1] for i in items
                            if classify_item(i, dm)[0] in ("cdn-direct", "decryptable"))
                verdict = f"playable（网页通道可下载） 样例直链: {good[:110]}"
            else:
                verdict = "restricted=TME/DRM 密文（内置密钥解不开，无明文出口）"
            print(f"  → 结论: {verdict}")
        # 命中可播放就不再试更低档位
        if verdict.startswith("playable"):
            break
        time.sleep(1.0)

    print(f"\n== 最终结论: {verdict} ==")
    if verdict and verdict.startswith("playable"):
        return 0
    if verdict and "Cookie未生效" in verdict:
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())