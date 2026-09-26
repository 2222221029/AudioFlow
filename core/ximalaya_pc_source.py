#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""喜马拉雅 PC 端取流通道（``mobile/download/v2/track``）。

移植来源
--------
逆向成果 ``ximalaya_unified/platforms/ximalaya/source_pc.py`` +
``credential.py:ensure_device_cookie``（2026-09-26 重构版）。

链路
----
::

    GET https://mobile.ximalaya.com/mobile/download/v2/track/{trackId}/ts-{毫秒}
        ?trackId={trackId}&device={win32|darwin}&trackQualityLevel={0|1|2|3}
    Header: xm-sign        ← 本项目的 core/ximalaya_pc_sign.py 纯 Python 生成
    Cookie: 1&_token + 1&_device=win32&{UUID}&4.0.15
    → data.downloadAacUrl 为 base64url 密文
    → AES-128-ECB + PKCS7 解密（key 与网页版同一个）
    → CDN 直链带时效签名，取到后需立即下载

必需凭证（缺一不可）
--------------------
============  ==================================================
``1&_token``   登录态。缺失 → ``ret=2002``
``1&_device``  ``{platform}&{设备UUID}&{客户端版本}``。缺失 → ``ret=2004``
``xm-sign``    缺失或失效 → ``ret=-1``；被拒 → HTTP 400 错误页
============  ==================================================

音质 ladder（客户端内部索引，非线性码率排序）
---------------------------------------------
``0`` = 24K、``1`` = 64K(标准)、``2`` = 128K(HQ 高清)、``3`` = 256K(客户端标称"无损")

服务端会用 ``data.downloadQualityLevel`` 回传该 track **实际采用**的档位，
因此不要拿请求档位当结果标签。

与移动端 V4 的取舍
------------------
PC 通道的独特价值是**不依赖真机票据 / Frida**：只要有网页登录态与本地生成的
``xm-sign`` 就能取址（移动端 V4 仍需 ``x-tk``）。代价是最高档只有 256K，
拿不到杜比全景声 / Audio Vivid / 无损 FLAC。
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .ximalaya_pc_sign import (
    PC_APP_KEY,
    XmSignCache,
    make_pc_base_info_sign,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: PC 客户端 UA。与 Cookie 里的 ``1&_device`` 版本号必须配套。
PC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Ximalaya-PC/4.0.15"
)

#: 电脑版 ``1&_device`` 里的客户端版本
PC_DEVICE_VERSION = "4.0.15"

PC_TRACK_URL = (
    "https://mobile.ximalaya.com/mobile/download/v2/track/{track_id}/ts-{ts}"
)

#: 客户端档位索引 → 展示名
PC_QUALITY_TIERS: Dict[int, str] = {
    0: "PC 24K",
    1: "PC 64K",
    2: "PC 128K",
    3: "PC 256K",
}

#: PC 端取址地址的解密密钥（与网页版同一个：网页 JS ``Kt()``）
PC_MEDIA_AES_KEY_HEX = "aaad3e4fd540b0f79dca95606e72bf93"

#: 需要重新生成签名的返回码
PC_RET_SIGN_STALE = -1
PC_RET_NOT_LOGGED_IN = 2002
PC_RET_MISSING_DEVICE = 2004
PC_RET_RISK_CONTROL = 1001

PC_RET_MESSAGES: Dict[int, str] = {
    PC_RET_SIGN_STALE: "xm-sign 缺失或已失效（ret=-1），已自动换新签名重试",
    PC_RET_NOT_LOGGED_IN: "未登录（ret=2002）：Cookie 缺少 1&_token",
    PC_RET_MISSING_DEVICE: "缺少设备号（ret=2004）：Cookie 需要 1&_device=win32&<UUID>&4.0.15",
    PC_RET_RISK_CONTROL: "风控（ret=1001）：签名或设备态未被接受",
}

_CDN_MARKERS = ("xmcdn.com", "ximalaya.com")
_CDN_PATH_MARKERS = (".mp3", ".m4a", ".aac", ".flac", ".wav", "/storages/", "aod.cos")


class PcSourceError(RuntimeError):
    """PC 通道取址失败。"""


@dataclass
class PcTrackResult:
    """一次 PC 端取址的结果。"""

    ok: bool = False
    url: str = ""
    #: 服务端回传的实际档位（``data.downloadQualityLevel``）
    level: Optional[int] = None
    quality_label: str = ""
    file_size: int = 0
    ext: str = ".m4a"
    ret: Optional[int] = None
    message: str = ""
    raw: Dict = field(default_factory=dict)

    @property
    def tier(self) -> str:
        """展示用档位名；服务端未回传档位时退化为通用标签。"""
        if self.level is None:
            return "PC"
        return PC_QUALITY_TIERS.get(self.level, f"PC level-{self.level}")


# ---------------------------------------------------------------------------
# Cookie 处理
# ---------------------------------------------------------------------------

def _cookie_segments(value) -> list:
    """把 Cookie 串/dict 拆成 ``[(key, val)]``，保留原始顺序。"""
    if isinstance(value, dict):
        return [(str(k).strip(), str(v).strip()) for k, v in value.items() if k]
    out = []
    for chunk in str(value or "").replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, val = chunk.split("=", 1)
        key, val = key.strip(), val.strip()
        if key:
            out.append((key, val))
    return out


def extract_login_token(value) -> str:
    """从任意凭证形态里抽出 ``1&_token`` 的值（``{uid}&{hex}``）。

    兼容 ``1&_token`` / ``4&_token`` / ``_token`` 三种命名；找不到返回空串。
    """
    for key, val in _cookie_segments(value):
        if key.strip().lower() in {"1&_token", "4&_token", "6&_token", "_token"}:
            return val
    return ""


def cookie_uid(value) -> str:
    """取 ``1&_token`` 前缀里的 uid。"""
    token = extract_login_token(value)
    return token.split("&", 1)[0].strip() if token else ""


def ensure_pc_device_cookie(cookie_header: str, platform: str = "win32",
                            version: str = PC_DEVICE_VERSION,
                            device_uuid: str = "") -> Tuple[str, str]:
    """补齐电脑版必需的 ``1&_device={platform}&{UUID}&{version}``。

    :param platform: ``win32`` 或 ``darwin``（对应签名 device ``win``/``mac``）
    :param device_uuid: 设备 UUID；留空则本次随机生成（调用方应传入持久化的值，
        否则每次进程重启都会换设备，等同于"每次换一台新机器"，没有必要地扩大
        了风控暴露面）
    :return: ``(补好的 Cookie 串, 1&_device 的值)``
    """
    segments = _cookie_segments(cookie_header)
    for key, val in segments:
        if key == "1&_device":
            return cookie_header, val
    device = str(device_uuid or "").strip() or str(uuid.uuid4())
    value = f"{platform}&{device}&{version}"
    segments.append(("1&_device", value))
    return "; ".join(f"{k}={v}" for k, v in segments), value


def pc_cookie_from_token(token: str, platform: str = "win32",
                         version: str = PC_DEVICE_VERSION,
                         device_uuid: str = "") -> str:
    """用登录 token 直接构造一套电脑版 Cookie。"""
    value = (token or "").strip()
    if not value:
        raise PcSourceError("缺少 1&_token，无法构造电脑版 Cookie")
    device = str(device_uuid or "").strip() or str(uuid.uuid4())
    return f"1&_token={value}; 1&_device={platform}&{device}&{version}"


def pc_device_platform(cookie_header: str, default: str = "win32") -> str:
    """从 Cookie 的 ``1&_device`` 反推平台（决定签名用哪套密钥）。"""
    for key, val in _cookie_segments(cookie_header):
        if key == "1&_device":
            parts = val.split("&")
            if parts and parts[0].strip():
                return parts[0].strip()
    return default


# ---------------------------------------------------------------------------
# 地址解密
# ---------------------------------------------------------------------------

def looks_like_cdn_url(url: str) -> bool:
    """判断解密结果是否像真实 CDN 直链，防止把乱码误判成地址。

    要求「http(s) 前缀 + CDN 域名 + 路径后缀」三重命中。
    """
    if not url or not str(url).startswith(("http://", "https://")):
        return False
    low = str(url).lower()
    return any(m in low for m in _CDN_MARKERS) and any(m in low for m in _CDN_PATH_MARKERS)


def decrypt_pc_media_url(raw: str) -> str:
    """解密 ``downloadAacUrl``：AES-128-ECB + PKCS7，密文为 base64url。

    已经是明文直链时原样返回。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
    except ImportError as exc:  # pragma: no cover
        raise PcSourceError("缺少 pycryptodome，无法解密 PC 端音频地址") from exc

    padded = value.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        cipher = base64.b64decode(padded)
    except Exception:
        return ""
    if not cipher or len(cipher) % 16:
        return ""
    key = bytes.fromhex(PC_MEDIA_AES_KEY_HEX)
    plain = AES.new(key, AES.MODE_ECB).decrypt(cipher)
    try:
        plain = unpad(plain, AES.block_size)
    except ValueError:
        # 少量历史响应未做 PKCS7，退化为"末尾字节 <= 16 就裁掉"
        if plain and plain[-1] <= 16:
            plain = plain[: -plain[-1]]
    text = plain.decode("utf-8", "replace").strip()
    return text


# ---------------------------------------------------------------------------
# PC 取流
# ---------------------------------------------------------------------------

class PcTrackSource:
    """电脑版 download/v2 取址器。

    典型用法::

        src = PcTrackSource(cookie=pc_cookie)
        result = src.resolve("516265274", level=2)
        if result.ok:
            download(result.url)

    :param session: 复用的 ``requests.Session``；留空则自建
    :param cookie: 电脑版 Cookie（缺 ``1&_device`` 会自动补）
    :param device: ``win32`` 或 ``darwin``，决定签名密钥与请求参数
    :param sign_cache: 复用的 :class:`XmSignCache`；留空则自建
    """

    def __init__(self, session=None, cookie: str = "", device: str = "win32",
                 sign_cache: Optional[XmSignCache] = None, timeout: int = 30,
                 device_uuid: str = ""):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.device = device if device in ("win32", "darwin") else "win32"
        self.sign_platform = "win" if self.device == "win32" else "mac"
        self.timeout = int(timeout)
        self.sign_cache = sign_cache or XmSignCache()
        self._raw_cookie = str(cookie or "")
        self._ready_cookie = ""
        self._device_uuid = str(device_uuid or "")
        self.last_error = ""
        self.last_ret: Optional[int] = None

    # -- Cookie -----------------------------------------------------------
    @property
    def cookie(self) -> str:
        """补齐 ``1&_device`` 后可直接发送的 Cookie 串。"""
        if not self._ready_cookie:
            self._ready_cookie, _ = ensure_pc_device_cookie(
                self._raw_cookie, self.device, PC_DEVICE_VERSION, self._device_uuid
            )
        return self._ready_cookie

    def set_cookie(self, cookie: str) -> None:
        self._raw_cookie = str(cookie or "")
        self._ready_cookie = ""

    @property
    def uid(self) -> str:
        return cookie_uid(self._raw_cookie)

    def has_login(self) -> bool:
        """是否具备登录态（有 ``1&_token`` 且 uid 非 0）。"""
        uid = self.uid
        return bool(uid) and uid != "0"

    # -- 请求 -------------------------------------------------------------
    def headers(self, with_sign: bool = True) -> Dict[str, str]:
        headers = {
            "User-Agent": PC_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.ximalaya.com/",
            "Cookie": self.cookie,
        }
        if with_sign:
            headers["xm-sign"] = self.sign_cache.get()
        return headers

    def invalidate_sign(self) -> None:
        """丢弃当前签名（HTTP 400 错误页 / ``ret=-1`` 时调用）。"""
        self.sign_cache.invalidate()

    def build_url(self, track_id, level: int) -> str:
        """构造官方客户端同形的请求 URL。"""
        track = str(track_id).strip()
        if not track.isdigit():
            raise PcSourceError(f"非法的 trackId: {track_id!r}")
        quality = int(level)
        if quality not in PC_QUALITY_TIERS:
            raise PcSourceError(f"PC 档位只能是 0~3，收到 {level!r}")
        ts = int(time.time() * 1000)
        return (
            PC_TRACK_URL.format(track_id=track, ts=ts)
            + f"?trackId={track}&device={self.device}&trackQualityLevel={quality}"
        )

    def base_info_sign(self, track_id, level: int = 0) -> str:
        """v4/baseInfo 的 query 签名（备用链路，需要时使用）。"""
        return make_pc_base_info_sign(
            track_id, int(time.time() * 1000), self.sign_platform, PC_APP_KEY
        )

    def _request(self, track_id, level: int) -> Tuple[Optional[dict], Optional[int], str]:
        """发一次请求。返回 ``(json, http_status, error)``。"""
        url = self.build_url(track_id, level)
        headers = self.headers()
        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
        except Exception as exc:
            return None, None, f"请求异常: {exc}"

        status = getattr(response, "status_code", None)
        text = ""
        try:
            text = response.text or ""
        except Exception:
            text = ""
        content_type = ""
        try:
            content_type = str(response.headers.get("content-type", "")).lower()
        except Exception:
            content_type = ""

        # 服务端在签名不被接受时返回 HTTP 400 + HTML 错误页。
        stripped = text.lstrip()[:1]
        if status == 400 or (stripped == "<" and "html" in content_type):
            return None, status, "签名被拒（HTTP 400/HTML 错误页）"
        if status != 200:
            return None, status, f"HTTP {status}"
        try:
            return json.loads(text), status, ""
        except (ValueError, TypeError):
            return None, status, "响应不是合法 JSON"

    def resolve(self, track_id, level: int = 2) -> PcTrackResult:
        """取址。签名失效会被自动重建并重试一次。

        :param level: 0=24K / 1=64K / 2=128K / 3=256K
        """
        self.last_error = ""
        self.last_ret = None

        if not self.has_login():
            self.last_error = PC_RET_MESSAGES[PC_RET_NOT_LOGGED_IN]
            return PcTrackResult(ok=False, message=self.last_error)

        for attempt in (1, 2):
            payload, status, error = self._request(track_id, level)
            if payload is None:
                # 签名类问题才值得换签名重试，其余直接失败
                if attempt == 1 and ("签名" in error or "400" in str(error)):
                    self.invalidate_sign()
                    continue
                self.last_error = error
                return PcTrackResult(ok=False, message=error)

            if not payload:
                self.last_error = "服务端返回空响应（可能是风控或接口行为已变更）"
                return PcTrackResult(ok=False, message=self.last_error, raw=payload)

            ret = payload.get("ret")
            self.last_ret = ret if isinstance(ret, int) else None

            if ret == PC_RET_SIGN_STALE and attempt == 1:
                self.invalidate_sign()
                continue
            if ret not in (0, None):
                self.last_error = PC_RET_MESSAGES.get(ret, f"download/v2 返回 ret={ret}")
                return PcTrackResult(ok=False, ret=ret, message=self.last_error,
                                     raw=payload)

            data = payload.get("data") or {}
            raw_url = data.get("downloadAacUrl") or data.get("downloadUrl") or ""
            if not raw_url:
                self.last_error = "响应里没有 downloadAacUrl（该集可能未授权）"
                return PcTrackResult(ok=False, ret=ret, message=self.last_error,
                                     raw=payload)

            real = decrypt_pc_media_url(raw_url)
            if not looks_like_cdn_url(real):
                self.last_error = "downloadAacUrl 解密失败或不是有效 CDN 直链"
                return PcTrackResult(ok=False, ret=ret, message=self.last_error,
                                     raw=payload)

            server_level = data.get("downloadQualityLevel")
            try:
                server_level = int(server_level) if server_level is not None else None
            except (TypeError, ValueError):
                server_level = None

            size = 0
            for key in ("downloadAacSize", "downloadSize", "fileSize"):
                try:
                    candidate = int(data.get(key) or 0)
                except (TypeError, ValueError):
                    candidate = 0
                if candidate:
                    size = candidate
                    break

            download_type = str(data.get("downloadType", ""))
            ext = ".mp3" if "MP3" in download_type.upper() else ".m4a"

            result = PcTrackResult(
                ok=True,
                url=real,
                level=server_level,
                file_size=size,
                ext=ext,
                ret=ret,
                raw=data,
            )
            result.quality_label = result.tier
            return result

        self.last_error = self.last_error or "签名重试后仍未取到地址"
        return PcTrackResult(ok=False, message=self.last_error)

    # -- 诊断 -------------------------------------------------------------
    def probe(self) -> Dict[str, object]:
        """凭证与签名自检（不发网络请求）。"""
        status = self.sign_cache.status()
        return {
            "device": self.device,
            "sign_platform": self.sign_platform,
            "has_login": self.has_login(),
            "uid_present": bool(self.uid),
            "cookie_has_device": any(
                key == "1&_device" for key, _ in _cookie_segments(self.cookie)
            ),
            "sign_ready": status.get("ready", False),
            "sign_suffix": status.get("session_suffix", ""),
            "last_error": self.last_error,
        }


__all__ = [
    "PC_DEVICE_VERSION",
    "PC_MEDIA_AES_KEY_HEX",
    "PC_QUALITY_TIERS",
    "PC_RET_MESSAGES",
    "PC_USER_AGENT",
    "PcSourceError",
    "PcTrackResult",
    "PcTrackSource",
    "cookie_uid",
    "decrypt_pc_media_url",
    "ensure_pc_device_cookie",
    "extract_login_token",
    "looks_like_cdn_url",
    "pc_cookie_from_token",
    "pc_device_platform",
]
