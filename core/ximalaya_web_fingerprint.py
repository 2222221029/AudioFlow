#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""喜马拉雅网页设备指纹（wfp＝数美 ATS openId）的生成与落盘。

背景
----
2026 年起喜马拉雅网页登录态接口两级风控：
1. 请求头 ``xm-sign``（数美 hdaa 上报 cadd&&sid，见 core/ximalaya_pc_sign.py，
   纯 Python 已实现，扫码/手动保存时同步预热即可）；
2. Cookie ``wfp``＝数美 ATS SDK openId（``$ats.getOpenId()``），需真实浏览器
   （headless Chromium + Playwright）访问页面让 JS 取号 —— 与本机 IP/UA 绑定。

本模块把 ``scripts/ximalaya_web_fingerprint.py`` 的取号逻辑提炼为可调用函数，
并提供 ``ensure_wfp(player=None)`` 供扫码/手动登录后后台线程调用；生成的
openId 落盘 ``config_dir()/ximalaya_wfp.json``，下载器（_web_wfp_cookie）自动读取。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_DEFAULT_PAGE = "https://www.ximalaya.com/album/130326634"
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_WFP_FILENAME = "ximalaya_wfp.json"

_SINGLE_FLIGHT = threading.Lock()


class WfpUnavailable(Exception):
    """playwright/chromium 不可用，无法生成本环境指纹。"""


def wfp_path() -> Path:
    """wfp 落盘路径（位于 config 目录）。"""
    try:
        from core.platform_config import config_dir
        path = config_dir() / _WFP_FILENAME
    except Exception:
        path = Path("config") / _WFP_FILENAME
    return path


def load_wfp() -> str:
    """读已落盘的 wfp（openId）；没有或损坏返回空串。"""
    try:
        path = wfp_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            value = str(data.get("wfp") or "").strip()
            return value if len(value) >= 8 else ""
    except Exception:
        pass
    return ""


def fetch_wfp(ua: str = _DEFAULT_UA, wait: int = 45,
              page: str = _DEFAULT_PAGE) -> str:
    """无头 Chromium 访问喜马拉雅页面，取 ``wfp`` openId。

    :raises WfpUnavailable: playwright 未安装 / 浏览器缺失
    :return: openId 字符串；超时返回空串
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WfpUnavailable(
            "需要 playwright：pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ])
        except Exception as exc:
            raise WfpUnavailable(
                f"chromium 启动失败（请先 playwright install chromium）: {exc}"
            ) from exc
        try:
            context = browser.new_context(
                user_agent=ua,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page_obj = context.new_page()
            page_obj.goto(page, wait_until="domcontentloaded", timeout=60000)
            deadline = time.time() + wait
            while time.time() < deadline:
                cookies = context.cookies("https://www.ximalaya.com")
                wfp = next((c["value"] for c in cookies
                            if c["name"] == "wfp" and c.get("value")), "")
                if wfp:
                    return wfp
                try:
                    direct = page_obj.evaluate(
                        "new Promise((res) => {"
                        "  if (window.$ats && window.$ats.getOpenId) {"
                        "    window.$ats.getOpenId().then((v) => res(v || ''))"
                        ".catch(() => res(''));"
                        "  } else { res(''); }"
                        "})"
                    )
                    if isinstance(direct, str) and len(direct) > 10:
                        return direct
                except Exception:
                    pass
                page_obj.wait_for_timeout(1000)
            return ""
        finally:
            try:
                browser.close()
            except Exception:
                pass


def save_wfp(wfp: str, source: str = "headless-chromium",
             path: Optional[Path] = None) -> Path:
    """落盘 wfp 指纹配置（下载器自动读取）。"""
    target = path or wfp_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    payload = {
        "wfp": wfp,
        "created_at": int(time.time()),
        "source": source,
        "ua": _DEFAULT_UA,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


def ensure_wfp(player: Optional[Callable[[], str]] = None,
              wait: int = 45) -> dict:
    """确保 wfp 就绪（已落盘则直接返回；否则尝试本地生成）。

    :param player: 自定义取号回调（主要供测试注入）；缺省用 :func:`fetch_wfp`
    :return: 诊断摘要（可安全回显）：
        ``{"wfp_ready": bool, "source": ..., "error": ..., "generated": bool}``
    """
    existing = load_wfp()
    if existing:
        return {"wfp_ready": True, "source": "cached", "generated": False}
    try:
        fetcher = player or fetch_wfp
        if not isinstance(fetcher, Callable):
            raise TypeError("player 必须是可调用对象")
        wfp = fetcher(wait=wait) if player else fetch_wfp(wait=wait)
        if not wfp:
            return {"wfp_ready": False, "generated": False,
                    "error": "取号超时，未拿到 openId"}
        save_wfp(wfp)
        return {"wfp_ready": True, "source": "headless-chromium",
                "generated": True}
    except WfpUnavailable as exc:
        return {"wfp_ready": False, "generated": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"wfp_ready": False, "generated": False, "error": str(exc)}


def ensure_wfp_async(on_done: Optional[Callable[[dict], None]] = None,
                     wait: int = 45) -> str:
    """后台线程生成 wfp（扫码/手动保存后调用，不阻塞响应）。

    :param on_done: 完成回调（线程内执行），收到诊断摘要
    :return: 若已在后台启动返回 ``"started"``；已就绪返回 ``"ready"``
    """
    if load_wfp():
        return "ready"
    if not _SINGLE_FLIGHT.locked():
        _SINGLE_FLIGHT.acquire()

        def _work():
            try:
                result = ensure_wfp(wait=wait)
                if on_done is not None:
                    try:
                        on_done(result)
                    except Exception:
                        pass
            finally:
                try:
                    _SINGLE_FLIGHT.release()
                except RuntimeError:
                    pass

        threading.Thread(target=_work, daemon=True, name="ximalaya-wfp").start()
        return "started"
    return "running"