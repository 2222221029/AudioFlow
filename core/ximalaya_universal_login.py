#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""扫码一次 → 三端通用凭证派生。

移植来源
--------
逆向成果 ``ximalaya_unified/platforms/ximalaya/auth.py:universal_credentials``
（2026-09-26 重构版），以及其决定性实验
``tools/reverse/test_universal_cookie.py``。

核心结论（源项目已实测验证）
----------------------------
===============  ==============  ==========================================
要素             能否本地派生    说明
===============  ==============  ==========================================
``1&_token``     ❌ 必须登录拿    **账号级主令牌，网页/PC/App 三端完全通用**
``1&_device``    ✅              三端格式不同，但都是本地可造
``x-tk``         ✅              用 device_id + uid 本地签，有真机金标回归
===============  ==============  ==========================================

源项目的实验做法是：拿网页账号的 ``1&_token``，配上**自造的** App
``1&_device`` 和**本地签发的** ``x-tk``，直接打移动版 V4 —— 返回 5 档地址，
且下载付费章节的体积与真机凭证结果**一字节不差**。

换言之：服务端校验的是 token 有效性与请求上下文的**自洽性**（签名、device
分支、UA 配对），而这些上下文都能在本地正确构造，没有哪个平台"绑死"设备。

三端凭证形态
------------
::

    web    Cookie: 1&_token={uid}&{hex}
    pc     Cookie: 1&_token={uid}&{hex}; 1&_device=win32&{UUID}&4.0.15
    mobile Cookie: 1&_device=android&{UUID}&9.4.52; 1&_token={uid}&{hex};
                   channel=and-d12; impl=com.ximalaya.ting.android;
                   osversion=33; device_model=V2059A; url_verify_mode=1
           Header: x-tk（本地签发）, Cookie2: $version=1
           UA:     ting_9.4.52(V2059A,Android33)

设备 UUID 必须**持久化复用**（本模块落在 AudioFlow 的 config 目录）。每次
派生都换新设备等同于"每次换一台新机器"，会无谓地扩大风控暴露面。
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Dict, Optional

from .ximalaya_local_ticket import LocalTicketError, generate_mobile_ticket

#: 设备 UUID 的落盘文件名（位于 AudioFlow 的 config 目录）
DEVICE_UUID_FILENAME = "ximalaya_device_uuid"

#: 派生默认参数 —— 与源项目决定性实验所用的一组完全一致
DEFAULT_APP_VERSION = "9.4.52"
DEFAULT_APP_VERSION_FULL = "9.4.52.3"
DEFAULT_DEVICE_MODEL = "V2059A"
DEFAULT_OS_VERSION = "33"
DEFAULT_PC_PLATFORM = "win32"
DEFAULT_PC_VERSION = "4.0.15"
DEFAULT_MOBILE_UA = f"ting_{DEFAULT_APP_VERSION}({DEFAULT_DEVICE_MODEL},Android{DEFAULT_OS_VERSION})"

_TOKEN_NAMES = ("1&_token", "4&_token", "6&_token", "_token")


class UniversalLoginError(ValueError):
    """凭证派生失败。"""


# ---------------------------------------------------------------------------
# token 与设备号
# ---------------------------------------------------------------------------

def split_token(token: str) -> tuple:
    """把登录令牌拆成 ``(uid, token)``。

    接受三种输入：

    * ``{uid}&{hex}``            —— 标准形态
    * ``1&_token={uid}&{hex}``   —— 带键名
    * 一整段 Cookie              —— 自动抽出 ``1&_token``

    :raises UniversalLoginError: 无法识别的形态
    """
    raw = str(token or "").strip()
    if not raw:
        raise UniversalLoginError("空 token")

    if "1&_token=" in raw or "_token=" in raw:
        for name in _TOKEN_NAMES:
            marker = f"{name}="
            if marker in raw:
                raw = raw.split(marker, 1)[1].split(";")[0].strip()
                break
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        raise UniversalLoginError("空 token")
    if "&" not in raw:
        raise UniversalLoginError(f"无法识别的 token 形态（缺少 '&'）：{raw[:24]}...")
    uid = raw.split("&", 1)[0].strip()
    if not uid.isdigit():
        raise UniversalLoginError(f"token 前缀不是 uid：{uid[:16]}")
    return uid, raw


def device_uuid_path() -> Path:
    """设备 UUID 的落盘路径（AudioFlow config 目录）。"""
    try:
        from .platform_config import config_dir
        return Path(config_dir()) / DEVICE_UUID_FILENAME
    except Exception:  # pragma: no cover - 配置层不可用时退化为家目录
        return Path.home() / f".audioflow_{DEVICE_UUID_FILENAME}"


def load_or_create_device_uuid(path: Optional[str] = None) -> str:
    """读取持久化的设备 UUID；不存在则生成并落盘。

    返回**带连字符**的标准 uuid4 字符串（与真机抓包形态一致）。落盘失败不
    影响本次使用 —— 凭证仍然可用，只是下次会换一个设备号。
    """
    target = Path(path) if path else device_uuid_path()
    try:
        existing = target.read_text(encoding="utf-8").strip()
        if len(existing.replace("-", "")) == 32:
            return existing
    except OSError:
        pass

    value = str(uuid.uuid4())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


# ---------------------------------------------------------------------------
# 三端凭证构造
# ---------------------------------------------------------------------------

def build_web_cookie(token: str) -> str:
    """网页端 Cookie：整条只需要 ``1&_token``。

    源项目实测：单独拎出 ``1&_token`` 即可解锁 VIP 付费取流，其余 20+ 个
    外围 cookie 都可以丢掉。
    """
    _uid, tok = split_token(token)
    return f"1&_token={tok}"


def build_pc_cookie(token: str, device_uuid: str,
                    platform: str = DEFAULT_PC_PLATFORM,
                    version: str = DEFAULT_PC_VERSION) -> str:
    """电脑版 Cookie：``1&_token`` + ``1&_device``（缺后者返回 ret=2004）。"""
    _uid, tok = split_token(token)
    return f"1&_token={tok}; 1&_device={platform}&{device_uuid}&{version}"


def build_mobile_cookie(token: str, device_uuid: str,
                        app_version: str = DEFAULT_APP_VERSION,
                        device_model: str = DEFAULT_DEVICE_MODEL,
                        os_version: str = DEFAULT_OS_VERSION) -> str:
    """App 端 Cookie（不含 x-tk，票据由 :func:`derive_universal_credentials` 另签）。"""
    _uid, tok = split_token(token)
    return (
        f"1&_device=android&{device_uuid}&{app_version}; "
        f"1&_token={tok}; "
        f"channel=and-d12; "
        f"impl=com.ximalaya.ting.android; "
        f"osversion={os_version}; "
        f"device_model={device_model}; "
        f"url_verify_mode=1"
    )


def build_mobile_user_agent(app_version: str = DEFAULT_APP_VERSION,
                            device_model: str = DEFAULT_DEVICE_MODEL,
                            os_version: str = DEFAULT_OS_VERSION) -> str:
    """App 端 UA。必须与 Cookie 里的 ``app_version`` 同源。"""
    return f"ting_{app_version}({device_model},Android{os_version})"


def derive_universal_credentials(token: str,
                                 device_uuid: str = "",
                                 app_version: str = DEFAULT_APP_VERSION,
                                 pc_platform: str = DEFAULT_PC_PLATFORM,
                                 pc_version: str = DEFAULT_PC_VERSION,
                                 device_model: str = DEFAULT_DEVICE_MODEL,
                                 os_version: str = DEFAULT_OS_VERSION,
                                 issue_ticket: bool = True) -> Dict[str, object]:
    """把一个 ``1&_token`` 派生成三端可用的凭证。

    :param token: 登录令牌（``{uid}&{hex}`` 或直接给一段 Cookie）
    :param device_uuid: 设备 UUID；留空则读取/创建持久化的那个
    :param issue_ticket: 是否本地签发移动端 ``x-tk``。关掉则只产出 Cookie，
        适合只想拿 PC/Web 两端的场景。
    :return: 含 ``uid`` / ``token`` / ``device_uuid`` / ``web_cookie`` /
        ``pc_cookie`` / ``mobile_cookie`` / ``mobile_ua`` /
        ``mobile_credentials`` / ``ticket`` 的字典
    """
    uid, tok = split_token(token)
    if not uid or uid == "0":
        raise UniversalLoginError("token 的 uid 为 0，不是有效登录态")
    device = str(device_uuid or "").strip() or load_or_create_device_uuid()

    web_cookie = build_web_cookie(tok)
    pc_cookie = build_pc_cookie(tok, device, pc_platform, pc_version)
    mobile_cookie = build_mobile_cookie(tok, device, app_version, device_model, os_version)
    mobile_ua = build_mobile_user_agent(app_version, device_model, os_version)

    # AudioFlow 的移动凭证结构（与 normalize_ximalaya_mobile_credentials 对齐）
    mobile_credentials: Dict[str, str] = {
        "cookie": mobile_cookie,
        "user_agent": mobile_ua,
        "api_device": "android2",
        "host": "mobile.ximalaya.com",
        "device": "android",
    }

    ticket = ""
    ticket_error = ""
    if issue_ticket:
        try:
            # generate_mobile_ticket 会从 Cookie 的 1&_device 取设备号、
            # 从 1&_token 取 uid，因此这里必须传入已组装好的完整 Cookie。
            ticket = generate_mobile_ticket(mobile_credentials, force_fresh=True)
            mobile_credentials["x_tk"] = ticket
        except LocalTicketError as exc:
            # 出票失败不应让整个派生失败：Web/PC 两端仍然可用。
            ticket_error = str(exc)

    return {
        "uid": uid,
        "token": tok,
        "device_uuid": device,
        "web_cookie": web_cookie,
        "pc_cookie": pc_cookie,
        "mobile_cookie": mobile_cookie,
        "mobile_ua": mobile_ua,
        "mobile_credentials": mobile_credentials,
        "ticket": ticket,
        "ticket_error": ticket_error,
    }


# ---------------------------------------------------------------------------
# 落盘（可选导出）
# ---------------------------------------------------------------------------

def save_bundle(bundle: Dict[str, object], outdir=None,
                backup: bool = True) -> Dict[str, str]:
    """把三端凭证导出成文件，便于人工检查或外部工具引用。

    默认会把已存在的同名文件备份成 ``<name>.bak`` —— 源项目踩过坑：一次
    派生把用户手上的真机 ``x-tk`` 冲掉了。

    :return: ``{键: 落盘路径}``；备份过的文件名放在 ``_backed_up`` 里
    """
    directory = Path(outdir) if outdir else (device_uuid_path().parent / "ximalaya_creds")
    directory.mkdir(parents=True, exist_ok=True)

    paths = {
        "web_cookie": directory / "web_cookie.txt",
        "pc_cookie": directory / "pc_cookie.txt",
        "mobile_headers": directory / "mobile_headers.json",
    }

    backed_up = []
    if backup:
        for path in paths.values():
            try:
                if path.exists() and path.stat().st_size > 0:
                    dst = path.with_suffix(path.suffix + ".bak")
                    shutil.copyfile(path, dst)
                    backed_up.append(dst.name)
            except OSError:
                pass

    paths["web_cookie"].write_text(str(bundle.get("web_cookie", "")), encoding="utf-8")
    paths["pc_cookie"].write_text(str(bundle.get("pc_cookie", "")), encoding="utf-8")
    headers = {
        "x_tk": bundle.get("ticket", ""),
        "cookie": bundle.get("mobile_cookie", ""),
        "user_agent": bundle.get("mobile_ua", ""),
        "api_device": "android2",
        "host": "mobile.ximalaya.com",
        "device": "android",
    }
    paths["mobile_headers"].write_text(
        json.dumps(headers, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    out = {key: str(val) for key, val in paths.items()}
    if backed_up:
        out["_backed_up"] = ", ".join(backed_up)
    return out


def bundle_summary(bundle: Dict[str, object]) -> Dict[str, object]:
    """派生结果的诊断摘要（**不泄露 token / 票据本体**）。"""
    ticket = str(bundle.get("ticket") or "")
    return {
        "uid": bundle.get("uid", ""),
        "device_uuid": bundle.get("device_uuid", ""),
        "web_cookie_ready": bool(bundle.get("web_cookie")),
        "pc_cookie_ready": bool(bundle.get("pc_cookie")),
        "pc_device_present": "1&_device=win32" in str(bundle.get("pc_cookie", "")),
        "mobile_cookie_ready": bool(bundle.get("mobile_cookie")),
        "mobile_device_present": "1&_device=android" in str(bundle.get("mobile_cookie", "")),
        "ticket_ready": bool(ticket),
        "ticket_length": len(ticket),
        "ticket_prefix": ticket[:3],
        "ticket_error": bundle.get("ticket_error", ""),
        "mobile_ua": bundle.get("mobile_ua", ""),
    }


__all__ = [
    "DEFAULT_APP_VERSION",
    "DEFAULT_DEVICE_MODEL",
    "DEFAULT_MOBILE_UA",
    "DEFAULT_OS_VERSION",
    "DEFAULT_PC_PLATFORM",
    "DEFAULT_PC_VERSION",
    "DEVICE_UUID_FILENAME",
    "UniversalLoginError",
    "build_mobile_cookie",
    "build_mobile_user_agent",
    "build_pc_cookie",
    "build_web_cookie",
    "bundle_summary",
    "derive_universal_credentials",
    "device_uuid_path",
    "load_or_create_device_uuid",
    "save_bundle",
    "split_token",
]
