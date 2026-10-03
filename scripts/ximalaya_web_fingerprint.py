#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""喜马拉雅网页设备指纹（wfp）生成器。

背景（2026-10-03 逆向确认）
--------------------------
喜马拉雅网页登录态接口（getTracksList / baseInfo 等）自 2026 年起要求两级设备
风控，缺一即 407：

1. 请求头 ``xm-sign``（数美 hdaa 上报的 ``cadd&&sid``）——AudioFlow 已在
   ``core/ximalaya_pc_sign.py`` 纯 Python 实现（HdaaSignProvider）；
2. Cookie ``wfp``＝数美 ATS SDK 的 ``openId``（``$ats.getOpenId()``）。该值由
   页面 JS（ats.2.5.7.js）采集真实浏览器环境（canvas/webgl/字体…）并经
   ``POST /xuid-web-fireeyes/report/v1`` 取号，**与生成它的环境绑定**——把
   浏览器里导出的 wfp 换一台机器/IP 复用必被 407「WFP存在但校验失败」。

本工具用无头 Chromium（Playwright）在**本机环境**跑一次喜马拉雅页面，让页面
自己的 JS 完成取号并把 ``wfp`` 落盘到 ``config/ximalaya_wfp.json``。下载器会在
网页请求里自动附带该 wfp 与 xm-sign（同环境自洽）。

用法
----
    pip install playwright && playwright install chromium
    python scripts/ximalaya_web_fingerprint.py           # 生成并落盘
    python scripts/ximalaya_web_fingerprint.py --verify   # 生成 + 用接口验证放行

输出
----
    config_dir()/ximalaya_wfp.json  ->  {"wfp": "...", "created_at": ..., ...}

说明
----
* openId 长期有效（页面按 365 天写 cookie）；只需偶尔重新生成（换 IP/UA 后）。
* 数据中心/代理 IP 的设备信誉可能让**内容层**风控（riskLevel=1005 空列表）
  仍然生效——那时换家庭宽带/住宅代理重跑本工具即可。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_PAGE = "https://www.ximalaya.com/album/130326634"
_WAIT_SECONDS = 45


def _save_wfp(wfp: str, source: str) -> Path:
    from core.platform_config import config_dir
    path = config_dir() / "ximalaya_wfp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "wfp": wfp,
        "created_at": int(time.time()),
        "source": source,
        "ua": _UA,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def fetch_wfp(ua: str = _UA, wait: int = _WAIT_SECONDS) -> str:
    """无头 Chromium 打开喜马拉雅页面，等 ``wfp`` cookie 就绪；没有则直接调
    ``$ats.getOpenId()``。返回 openId 字符串。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "需要 playwright：pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(
            user_agent=ua,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.goto(_PAGE, wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + wait
        while time.time() < deadline:
            cookies = context.cookies("https://www.ximalaya.com")
            wfp = next((c["value"] for c in cookies
                        if c["name"] == "wfp" and c.get("value")), "")
            if wfp:
                browser.close()
                return wfp
            try:
                direct = page.evaluate(
                    "new Promise((res) => {"
                    "  if (window.$ats && window.$ats.getOpenId) {"
                    "    window.$ats.getOpenId().then((v) => res(v || '')).catch(() => res(''));"
                    "  } else { res(''); }"
                    "})"
                )
                if isinstance(direct, str) and len(direct) > 10:
                    browser.close()
                    return direct
            except Exception:
                pass
            page.wait_for_timeout(1000)
        browser.close()
    return ""


def verify_wfp(wfp: str) -> dict:
    """用 wfp + xm-sign 请求章节接口，返回服务端判定。"""
    import requests
    from core.ximalaya_download_manager import XimalayaDownloadManager

    result = {}
    manager = XimalayaDownloadManager(cookie_string="")
    try:
        # 让下载器带上 wfp + xm-sign 发一次 getTracksList
        from unittest import mock
        with mock.patch.object(manager, "_web_wfp_cookie", return_value=wfp):
            manager._bootstrap_web_session("1017785891")
            headers = manager._web_headers("1017785891")
            session = requests.Session()
            session.headers.update(headers)
            session.headers.setdefault("Referer", "https://www.ximalaya.com/")
            r = session.get(
                "https://www.ximalaya.com/revision/album/v1/getTracksList",
                params={"albumId": "130326634", "pageNum": 1, "pageSize": 3},
                timeout=15,
            )
            d = r.json()
            result["ret"] = d.get("ret")
            result["msg"] = d.get("msg")
            result["riskLevel"] = ((d.get("data") or {}).get("riskLevel")
                                   if isinstance(d.get("data"), dict) else None)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="喜马拉雅网页 wfp（数美 openId）生成器")
    parser.add_argument("--verify", action="store_true",
                        help="生成后用章节接口验证是否放行")
    parser.add_argument("--wait", type=int, default=_WAIT_SECONDS,
                        help="等待 wfp 秒数（默认 45）")
    parser.add_argument("--out", default="", help="输出路径（默认 config/ximalaya_wfp.json）")
    args = parser.parse_args()

    print("== 启动无头浏览器取号（需要 playwright + chromium） ==")
    wfp = fetch_wfp(wait=args.wait)
    if not wfp:
        print("❌ 未能在限时内取到 wfp（openId）。检查网络/重试，或换 IP 后重跑。")
        return 2
    print(f"✅ wfp = {wfp[:24]}…（{len(wfp)} 字符）")

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"wfp": wfp, "created_at": int(time.time()), "ua": _UA},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入: {target}")
    else:
        target = _save_wfp(wfp, "headless-chromium")
        print(f"已写入: {target}")

    if args.verify:
        print("\n== 用 wfp + xm-sign 验证章节接口 ==")
        res = verify_wfp(wfp)
        print("  ", res)
        ret = res.get("ret")
        if ret == 200:
            print("✅ 校验放行（内容层 riskLevel 受所在网络 IP 信誉影响）")
        elif ret == 407:
            print("⚠️ 仍被指纹层拦截（环境不匹配/取号失败），换 IP 重跑")
        elif ret == 1001:
            print("⚠️ 风控（多为内容层 IP 信誉），换住宅网络后重跑")
        else:
            print(f"ℹ️ 其他返回 ret={ret}，属正常业务判定（如未登录 401）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())