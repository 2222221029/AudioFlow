#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
七猫媒体下载 — 单文件便携版

只需 Python 3.10+，复制本文件到任意电脑即可运行：
  python main.py

首次运行会自动安装依赖：httpx、cryptography
下载文件保存在与本文件同目录的 downloads/ 文件夹。

非交互：
  python main.py --book-only "剑来"
  python main.py --ai-voice "剑来" --voice-id 10 --chapter 1
"""

from __future__ import annotations

import subprocess
import sys


def _ensure_dependencies() -> None:
    required = {"httpx": "httpx", "cryptography": "cryptography"}
    missing: list[str] = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return
    print("正在安装依赖:", ", ".join(missing))
    cmd = [sys.executable, "-m", "pip", "install", *missing, "-q"]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        print("依赖安装失败，请手动执行:")
        print(" ", sys.executable, "-m", "pip", "install", *missing)
        sys.exit(1)


_ensure_dependencies()


# ===== 签名 =====


import base64
import hashlib
import json
import uuid
from typing import Any

SIGN_SECRET = b"d3dGiJc651gSQ8w1"

HEADER_SIGN_KEYS = (
    "AUTHORIZATION",
    "application-id",
    "app-version",
    "channel",
    "is-white",
    "net-env",
    "platform",
    "qm-params",
    "reg",
)

def _build_replace_char_map() -> dict[str, str]:
    outs = [
        0x65, 0x4C, 0x6E, 0x77, 0x44, 0x37, 0x51, 0x6A, 0x76, 0x56, 0x31, 0x5A, 0x53, 0x7A, 0x32, 0x2D,
        0x54, 0x35, 0x78, 0x4B, 0x36, 0x63, 0x75, 0x4F, 0x42, 0x6D, 0x70, 0x34, 0x43, 0x71, 0x52, 0x66,
        0x68, 0x33, 0x6B, 0x46, 0x38, 0x41, 0x4E, 0x67, 0x74, 0x69, 0x47, 0x48, 0x5F, 0x79, 0x6F, 0x30,
        0x49, 0x61, 0x73, 0x39, 0x4A, 0x64, 0x62, 0x57, 0x59, 0x72, 0x45, 0x6C, 0x55, 0x4D, 0x58,
    ]
    o = [chr(c) for c in outs]
    m: dict[str, str] = {"+": "P"}
    for i, ch in enumerate("/0123456789"):
        m[ch] = o[62 - i]
    for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        m[ch] = o[51 - i]
    for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        m[ch] = o[25 - i]
    return m


_REPLACE = _build_replace_char_map()
_REVERSE = {v: k for k, v in _REPLACE.items()}


def md5_sign(plain: str, secret: bytes = SIGN_SECRET) -> str:
    return hashlib.md5(plain.encode("utf-8") + secret).hexdigest()


def sign_concat(items: dict[str, str]) -> str:
    return "".join(f"{k}={items[k]}" for k in sorted(items))


def query_sign(params: dict[str, str]) -> str:
    items = {k: v for k, v in params.items() if k != "sign"}
    return md5_sign(sign_concat(items))


def header_sign(fields: dict[str, str]) -> str:
    items = {k: fields[k] for k in HEADER_SIGN_KEYS if k in fields}
    return md5_sign(sign_concat(items))


def encrypt_qm_params(json_obj: dict[str, Any]) -> str:
    # km.F() 使用 TreeMap，Gson 按 key 字典序序列化
    raw = json.dumps(json_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    b64 = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return "".join(_REPLACE.get(c, c) for c in b64)


def decrypt_qm_params(cipher: str) -> str:
    b64 = "".join(_REVERSE.get(c, c) for c in cipher)
    return base64.b64decode(b64).decode("utf-8")


def build_qm_params_json(
    *,
    device_id: str,
    uuid_str: str | None = None,
    wlb_uid: str = "",
    oaid: str = "",
    imei: str = "",
    mac: str = "",
    brand: str = "Xiaomi",
    model: str = "MI 8",
    sys_ver: str = "10",
    client_id: str = "",
    sourceuid: str = "",
    session_id: str | None = None,
    cf: str = "0",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "uuid": uuid_str or str(uuid.uuid4()),
        "imei": imei,
        "wlb-imei": imei,
        "wlb-uid": wlb_uid,
        "oaid-no-cache": oaid,
        "oaid": oaid,
        "device-id": device_id,
        "mac": mac,
        "brand": brand,
        "phone-level": "1",
        "model": model,
        "sys-ver": sys_ver,
        "client-id": client_id,
        "sourceuid": sourceuid,
        "static_score": "0",
        "device_percent": "0",
        "session-id": session_id or str(uuid.uuid4()),
        "cf": cf,
    }
    if extra:
        data.update(extra)
    return data


def new_track_id() -> str:
    return uuid.uuid4().hex[:30]

# ===== 登录 =====


import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


MAIN_API = "https://xiaoshuo.wtzw.com"
DEFAULT_CHANNEL = "qm-tengxun_lf"
DEFAULT_APP_VERSION = "79600"
DEFAULT_APPLICATION_ID = "com.kmxs.reader"


@dataclass
class LoginResult:
    token: str
    uid: str
    device_id: str
    track_id: str
    reg: str
    is_white: str
    nickname: str
    tourist_mode: str
    raw: dict[str, Any]

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> LoginResult:
        user = parse_jwt(data["token"])
        u = user.get("user", {}) if isinstance(user, dict) else {}
        return cls(
            token=data["token"],
            uid=str(data.get("id") or u.get("uid", "")),
            device_id=u.get("deviceId", u.get("sm_id", "")),
            track_id=u.get("suid", "") or "",
            reg=str(data.get("reg", "")),
            is_white=str(data.get("is_white", "0")),
            nickname=str(data.get("nickname", "")),
            tourist_mode=str(data.get("tourist_mode", "1")),
            raw=data,
        )


def parse_jwt(token: str) -> dict[str, Any]:
    """解析 JWT payload（不校验签名）"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except (IndexError, json.JSONDecodeError, ValueError):
        return {}


def jwt_expired(token: str, *, skew_sec: int = 300) -> bool:
    payload = parse_jwt(token)
    exp = payload.get("exp")
    if not exp:
        return True
    return int(exp) <= int(time.time()) + skew_sec


def body_form_sign(params: dict[str, str]) -> str:
    """
    qi5 POST 体签名：TreeMap 按 key 排序，拼接 key=value（无 &），再 MD5+secret。
    与 query_sign 算法一致，只是参与字段来自 form body。
    """
    items = {k: v for k, v in params.items() if k != "sign"}
    return md5_sign(sign_concat(items))


def build_form_body(params: dict[str, str]) -> str:
    """生成 gender=0&sign=xxx（wire 用 &，sign 明文不含 &）"""
    signed = dict(params)
    signed["sign"] = body_form_sign(signed)
    return urlencode(signed)


def build_api_headers(
    *,
    authorization: str = "",
    qm_params: str | None = None,
    device_id: str | None = None,
    channel: str = DEFAULT_CHANNEL,
    app_version: str = DEFAULT_APP_VERSION,
    application_id: str = DEFAULT_APPLICATION_ID,
    reg: str = "",
    is_white: str = "0",
    net_env: str = "4",
    platform: str = "android",
) -> dict[str, str]:
    if qm_params is None:
        did = device_id or uuid.uuid4().hex + uuid.uuid4().hex[:8]
        qm_params = encrypt_qm_params(build_qm_params_json(device_id=did))
    fields = {
        "AUTHORIZATION": authorization,
        "application-id": application_id,
        "app-version": app_version,
        "channel": channel,
        "is-white": is_white,
        "net-env": net_env,
        "platform": platform,
        "qm-params": qm_params,
        "reg": reg,
    }
    hs = header_sign(fields)
    uid = ""
    if authorization:
        uid = str(parse_jwt(authorization).get("user", {}).get("uid", ""))
    ts = str(int(time.time()))
    return {
        "authorization": authorization,
        "application-id": application_id,
        "app-version": app_version,
        "channel": channel,
        "is-white": is_white,
        "net-env": net_env,
        "platform": platform,
        "qm-params": qm_params,
        "reg": reg,
        "sign": hs,
        "no-permiss": "3",
        "user-agent": "webviewversion/0",
        "accept-encoding": "gzip",
        "Content-Type": "application/x-www-form-urlencoded",
        "qm-uaf": f"{time.strftime('%Y%m%d')}-{uid}" if uid else "",
        "qm-it": ts,
        "qm-ii": str(abs(hash(device_id or "")) % (10**9)),
    }


def login_tourist(
    *,
    gender: str = "0",
    authorization: str = "",
    device_id: str | None = None,
    channel: str = DEFAULT_CHANNEL,
    app_version: str = DEFAULT_APP_VERSION,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> LoginResult:
    """
    游客登录，返回 JWT（即后续请求的 authorization 头）。

    gender: 本地性别偏好，UserModel.loginTourist 使用 pab.s() -> SharedPreferences
    """
    body = build_form_body({"gender": gender})
    headers = build_api_headers(
        authorization=authorization,
        device_id=device_id,
        channel=channel,
        app_version=app_version,
    )
    url = f"{MAIN_API}/api/v1/login/tourist"
    own = client is None
    if own:
        client = httpx.Client(timeout=timeout)
    try:
        r = client.post(url, content=body, headers=headers)
        r.raise_for_status()
        j = r.json()
    finally:
        if own:
            client.close()
    if j.get("errors"):
        raise RuntimeError(f"login/tourist failed: {j['errors']}")
    data = j.get("data") or {}
    if not data.get("token"):
        raise RuntimeError(f"no token in response: {j}")
    return LoginResult.from_response(data)


def login_tourist_token(**kwargs: Any) -> str:
    """仅返回 JWT 字符串"""
    return login_tourist(**kwargs).token

# ===== 解密 =====



import base64
import re
import struct
from html import unescape

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# AKeyGenerator::init → memcpy(this+145, segmentE_key, 16)
CHAPTER_AES_KEY = b"242ccb8230" + struct.pack("<I", 959461220) + struct.pack("<H", 12645)


def decrypt_chapter_content(enc_b64: str, key: bytes = CHAPTER_AES_KEY) -> str:
    """
    解密章节 content 字段（Base64 密文）。
    算法：AES/CBC/NoPadding，IV 为解码后前 16 字节。
    """
    raw = base64.b64decode(enc_b64.encode("utf-8"))
    if len(raw) < 32:
        raise ValueError("密文过短")
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).decryptor()
    pt = dec.update(ct) + dec.finalize()
    return pt.decode("utf-8", errors="replace")


def html_to_plaintext(html: str) -> str:
    """将正文 HTML 转为可读纯文本。"""
    text = html
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ===== API =====


import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from urllib.parse import urlparse

import httpx


API_BC = "https://api-bc.wtzw.com"
API_KS = "https://api-ks.wtzw.com"
API_GW = "https://api-gw.wtzw.com"
API_MAIN = "https://xiaoshuo.wtzw.com"

DEFAULT_CHANNEL = "qm-tengxun_lf"
DEFAULT_APP_VERSION = "79600"
DEFAULT_APPLICATION_ID = "com.kmxs.reader"

# type=2 音色（AI情感女声/标准男声）音频走此 CDN，公网 DNS 常无记录，需 App HTTP-DNS 或 VPN DNS
CDN_TTS_ALI_HOST = "cdn-tts-ali.wtzw.com"

# 搜索 tab：3=书籍综合，2=听书（与 App SearchViewModel 一致）
TAB_BOOK = 3
TAB_LISTEN = 2


class QimaoApiError(Exception):
    def __init__(self, code: str, message: str, raw: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.raw = raw
        super().__init__(f"[{code}] {message}")


@dataclass
class QimaoConfig:
    authorization: str
    qm_params: str | None = None
    channel: str = DEFAULT_CHANNEL
    app_version: str = DEFAULT_APP_VERSION
    application_id: str = DEFAULT_APPLICATION_ID
    platform: str = "android"
    net_env: str = "4"
    is_white: str = "0"
    reg: str = ""
    device_id: str = ""
    track_id: str = ""
    uid: str = ""
    uuid_str: str = ""
    oaid: str = ""
    imei: str = ""
    brand: str = "Xiaomi"
    model: str = "MI 8"
    sys_ver: str = "10"
    extra_qm_json: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_capture_file(cls, path: str | Path) -> QimaoConfig:
        text = Path(path).read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        headers: dict[str, str] = {}
        for ln in lines[1:]:
            if ":" not in ln or ln.startswith("GET "):
                continue
            k, _, v = ln.partition(":")
            headers[k.strip().lower()] = v.strip()
        auth = headers.get("authorization", "")
        user = parse_jwt(auth).get("user", {})
        return cls(
            authorization=auth,
            qm_params=headers.get("qm-params"),
            channel=headers.get("channel", DEFAULT_CHANNEL),
            app_version=headers.get("app-version", DEFAULT_APP_VERSION),
            application_id=headers.get("application-id", DEFAULT_APPLICATION_ID),
            net_env=headers.get("net-env", "4"),
            is_white=headers.get("is-white", "0"),
            reg=headers.get("reg", ""),
            device_id=user.get("deviceId", user.get("sm_id", "")),
            track_id=user.get("suid", ""),
            uid=str(user.get("uid", "")),
            uuid_str=user.get("uuid", ""),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> QimaoConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_login(cls, login: LoginResult, **kwargs: Any) -> QimaoConfig:
        return cls(
            authorization=login.token,
            device_id=login.device_id,
            track_id=login.track_id or new_track_id(),
            uid=login.uid,
            reg=login.reg,
            is_white=login.is_white,
            **kwargs,
        )

    @classmethod
    def tourist_login(cls, gender: str = "0", **kwargs: Any) -> QimaoConfig:
        return cls.from_login(login_tourist(gender=gender), **kwargs)


def _search_base_params(
    wd: str,
    *,
    tab: int,
    page: int = 1,
    track_id: str | None = None,
    gender: int = 0,
    refresh_state: int = 8,
    read_preference: int = 0,
    book_privacy: int = 1,
    is_short_story_user: int = 0,
    extend: str = "",
    book_id: str = "",
) -> dict[str, str]:
    return {
        "extend": extend,
        "tab": str(tab),
        "gender": str(gender),
        "refresh_state": str(refresh_state),
        "track_id": track_id or new_track_id(),
        "page": str(page),
        "book_id": book_id,
        "book_privacy": str(book_privacy),
        "wd": wd,
        "read_preference": str(read_preference),
        "is_short_story_user": str(is_short_story_user),
    }


class QimaoClient:
    def __init__(self, config: QimaoConfig, *, timeout: float = 60.0) -> None:
        self.config = config
        # trust_env=False：避免系统代理干扰 CDN 音频/视频直链下载
        self._client = httpx.Client(timeout=timeout, trust_env=False)
        self._qm_cache: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> QimaoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _qm_params(self) -> str:
        if self._qm_cache:
            return self._qm_cache
        if self.config.qm_params:
            self._qm_cache = self.config.qm_params
            return self._qm_cache
        obj = build_qm_params_json(
            device_id=self.config.device_id,
            uuid_str=self.config.uuid_str or None,
            oaid=self.config.oaid,
            imei=self.config.imei,
            brand=self.config.brand,
            model=self.config.model,
            sys_ver=self.config.sys_ver,
            extra=self.config.extra_qm_json or None,
        )
        self._qm_cache = encrypt_qm_params(obj)
        return self._qm_cache

    def _common_headers(self) -> dict[str, str]:
        qm = self._qm_params()
        c = self.config
        fields = {
            "AUTHORIZATION": c.authorization,
            "application-id": c.application_id,
            "app-version": c.app_version,
            "channel": c.channel,
            "is-white": c.is_white,
            "net-env": c.net_env,
            "platform": c.platform,
            "qm-params": qm,
            "reg": c.reg,
        }
        hs = header_sign(fields)
        uid = c.uid or str(parse_jwt(c.authorization).get("user", {}).get("uid", ""))
        ts = str(int(time.time()))
        return {
            "authorization": c.authorization,
            "application-id": c.application_id,
            "app-version": c.app_version,
            "channel": c.channel,
            "is-white": c.is_white,
            "net-env": c.net_env,
            "platform": c.platform,
            "qm-params": qm,
            "reg": c.reg,
            "sign": hs,
            "no-permiss": "3",
            "user-agent": "webviewversion/0",
            "accept-encoding": "gzip",
            "qm-uaf": f"{time.strftime('%Y%m%d')}-{uid}" if uid else "",
            "qm-it": ts,
            "qm-ii": str(abs(hash(c.device_id or "")) % (10**9)),
        }

    @staticmethod
    def _unwrap(j: dict[str, Any]) -> Any:
        if "errors" in j:
            e = j["errors"]
            raise QimaoApiError(
                str(e.get("code", "?")),
                str(e.get("details") or e.get("title", "unknown")),
                j,
            )
        return j.get("data", j)

    def _signed_get(self, base: str, path: str, params: dict[str, str]) -> Any:
        p = dict(params)
        p["sign"] = query_sign(p)
        url = f"{base}{path}?{urlencode(p)}"
        r = self._client.get(url, headers=self._common_headers())
        r.raise_for_status()
        return self._unwrap(r.json())

    def _download_error_hint(self, url: str, exc: Exception) -> str:
        host = urlparse(url).netloc or url
        msg = str(exc)
        if CDN_TTS_ALI_HOST in host or "getaddrinfo failed" in msg or "11001" in msg:
            return (
                f"CDN 域名无法解析：{host}。"
                f"voice_id=2/3（type=2）依赖该域名；可改用 --voice-id 4~10（如 10=多角色），"
                f"或开启能解析此域名的 VPN 后再试。"
            )
        if "UNEXPECTED_EOF_WHILE_READING" in msg or "SSL" in msg.upper():
            return f"CDN TLS 握手失败（{host}），请关闭 VPN/系统代理后重试。"
        return f"下载失败：{host} — {exc}"

    def download_url(
        self,
        url: str,
        out_path: str | Path,
        *,
        chunk_size: int = 1024 * 256,
        overwrite: bool = False,
        referer: str | None = None,
    ) -> Path:
        """
        真实下载（落盘），用于 mp3/mp4 等 CDN 直链。

        - **不会**自动创建复杂目录；仅确保父目录存在
        - overwrite=False 且文件存在时直接返回
        """
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and not overwrite:
            return out

        headers = {}
        if referer:
            headers["referer"] = referer

        try:
            with self._client.stream("GET", url, headers=headers, follow_redirects=True) as r:
                r.raise_for_status()
                with out.open("wb") as f:
                    for chunk in r.iter_bytes(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
        except httpx.ConnectError as e:
            raise QimaoApiError("CDN_CONNECT", self._download_error_hint(url, e)) from e
        return out

    def download_text(self, url: str, *, referer: str | None = None) -> str:
        headers = {}
        if referer:
            headers["referer"] = referer
        r = self._client.get(url, headers=headers, follow_redirects=True)
        r.raise_for_status()
        return r.text

    def fetch_playlet_aes_key(self, asset_id: str) -> bytes:
        """短剧 HLS AES-128 密钥（token-deal 返回 16 字节原始 key，非 JSON）。"""
        params = {"asset_id": asset_id}
        params["sign"] = query_sign(params)
        url = f"{API_GW}/playlet/api/encrypt/token-deal?{urlencode(params)}"
        r = self._client.get(url, headers=self._common_headers(), follow_redirects=True)
        r.raise_for_status()
        key = r.content
        if key.startswith(b"{"):
            try:
                j = json.loads(key.decode("utf-8"))
                if "errors" in j:
                    e = j["errors"]
                    raise QimaoApiError(str(e.get("code")), str(e.get("details", "")), j)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        if len(key) != 16:
            raise QimaoApiError("BAD_KEY", f"AES-128 key 长度异常: {len(key)}")
        return key

    def download_hls_to_ts(
        self,
        m3u8_url: str,
        out_ts_path: str | Path,
        *,
        referer: str | None = None,
        overwrite: bool = False,
        chunk_size: int = 1024 * 256,
    ) -> Path:
        """
        下载 HLS（m3u8）并合并为单个 .ts（AES-128-CBC 解密后可直接播放）。
        """
        import re
        from urllib.parse import parse_qs, urljoin, urlparse

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        out = Path(out_ts_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and not overwrite:
            return out

        ref = referer or API_GW
        m3u8 = self.download_text(m3u8_url, referer=ref).strip()
        if not m3u8.startswith("#EXTM3U"):
            raise QimaoApiError("NOT_M3U8", "返回内容不是 m3u8")

        key_url: str | None = None
        iv: bytes | None = None
        seg_urls: list[str] = []

        for line in m3u8.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-KEY:"):
                parts = line[len("#EXT-X-KEY:") :].split(",")
                kv: dict[str, str] = {}
                for p in parts:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        kv[k.strip()] = v.strip().strip('"')
                if kv.get("METHOD") == "AES-128":
                    key_url = kv.get("URI")
                    iv_raw = kv.get("IV", "")
                    m = re.search(r"0x([0-9a-fA-F]+)", iv_raw)
                    if m:
                        try:
                            iv = bytes.fromhex(m.group(1))
                        except ValueError:
                            iv = None
                continue
            if line.startswith("#"):
                continue
            seg_urls.append(urljoin(m3u8_url, line))

        if not seg_urls:
            raise QimaoApiError("NO_SEG", "m3u8 没有分片 URL")

        key: bytes | None = None
        if key_url:
            abs_key_url = urljoin(m3u8_url, key_url)
            if "token-deal" in abs_key_url and "asset_id=" in abs_key_url:
                qs = parse_qs(urlparse(abs_key_url).query)
                asset_id = (qs.get("asset_id") or [""])[0]
                if asset_id:
                    key = self.fetch_playlet_aes_key(asset_id)
            else:
                hdrs = self._common_headers() if API_GW in abs_key_url else {"referer": ref}
                key = self._client.get(abs_key_url, headers=hdrs, follow_redirects=True).content
                if key.startswith(b"{"):
                    try:
                        j = json.loads(key.decode("utf-8"))
                        k = j.get("data") or j.get("key") or j.get("result")
                        if isinstance(k, str):
                            key = k.encode("utf-8")
                    except Exception:
                        pass
                if len(key) != 16:
                    raise QimaoApiError("BAD_KEY", f"AES-128 key 长度异常: {len(key)}")

        seg_headers = {"referer": ref}

        with out.open("wb") as f:
            for u in seg_urls:
                with self._client.stream("GET", u, headers=seg_headers, follow_redirects=True) as r:
                    r.raise_for_status()
                    data = b"".join(r.iter_bytes(chunk_size=chunk_size))
                if key:
                    use_iv = iv or b"\x00" * 16
                    dec = Cipher(algorithms.AES(key), modes.CBC(use_iv), backend=default_backend()).decryptor()
                    data = dec.update(data) + dec.finalize()
                f.write(data)
        return out

    # --- 书籍 ---

    def search_words(
        self,
        wd: str,
        *,
        page: int = 1,
        tab: int = TAB_BOOK,
        track_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """书籍综合搜索，返回完整 JSON（含 data）"""
        params = _search_base_params(wd, tab=tab, page=page, track_id=track_id or self.config.track_id, **kwargs)
        params["sign"] = query_sign(params)
        url = f"{API_BC}/search/v1/words?{urlencode(params)}"
        r = self._client.get(url, headers=self._common_headers())
        r.raise_for_status()
        j = r.json()
        if "errors" in j:
            e = j["errors"]
            raise QimaoApiError(str(e.get("code")), str(e.get("details", "")), j)
        return j

    def book_chapter_list(self, book_id: str) -> dict[str, Any]:
        """章节目录。注意参数名为 id 而非 book_id"""
        return self._signed_get(API_KS, "/api/v1/chapter/chapter-list", {"id": book_id})

    def book_chapter_content(self, book_id: str, chapter_id: str) -> dict[str, Any]:
        """
        章节正文（content 为加密 Base64，App 侧由 libcommon-encryption / Security.decrypt 解密）。
        返回字段：content, content_md5, id, type
        """
        return self._signed_get(
            API_KS,
            "/api/v1/chapter/content",
            {"id": book_id, "chapterId": chapter_id},
        )

    def book_chapter_plaintext(self, book_id: str, chapter_id: str, *, as_html: bool = False) -> str:
        """拉取并解密单章正文；默认转为纯文本。"""
        body = self.book_chapter_content(book_id, chapter_id)
        enc = body.get("content") or ""
        if not enc or enc == "<p></p>":
            return ""
        text = decrypt_chapter_content(enc)
        return text if as_html else html_to_plaintext(text)

    def download_book_txt(
        self,
        book_id: str,
        out_path: str | Path,
        *,
        max_chapters: int | None = None,
        chapter_ids: list[str] | None = None,
        overwrite: bool = False,
    ) -> Path:
        """
        下载书籍章节正文并合并保存为 txt。
        chapter_ids 为空时按目录顺序下载；max_chapters 限制章数。
        """
        out = Path(out_path)
        if out.exists() and not overwrite:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)

        if chapter_ids is None:
            cl = self.book_chapter_list(book_id)
            raw = cl.get("chapter_lists") or cl.get("chapter_list") or []
            chapter_ids = []
            if raw and isinstance(raw[0], dict) and "chapter_list" in raw[0]:
                for vol in raw:
                    for ch in vol.get("chapter_list") or []:
                        if ch.get("id"):
                            chapter_ids.append(str(ch["id"]))
            else:
                for ch in raw:
                    if ch.get("id"):
                        chapter_ids.append(str(ch["id"]))

        if max_chapters is not None:
            chapter_ids = chapter_ids[:max_chapters]

        parts: list[str] = []
        for i, cid in enumerate(chapter_ids, 1):
            try:
                text = self.book_chapter_plaintext(book_id, cid)
            except Exception as e:
                text = f"[章节 {cid} 下载失败: {e}]"
            parts.append(f"\n\n{'=' * 40}\n第 {i} 章 (id={cid})\n{'=' * 40}\n\n{text}")

        out.write_text("".join(parts).lstrip(), encoding="utf-8")
        return out

    # --- 书籍 AI 听书（阅读器内 TTS，支持音色切换）---

    @staticmethod
    def iter_chapters(chapter_list_data: dict[str, Any]) -> list[dict[str, Any]]:
        """扁平化章节目录（含 id、title、content_md5 等）。"""
        raw = chapter_list_data.get("chapter_lists") or chapter_list_data.get("chapter_list") or []
        if raw and isinstance(raw[0], dict) and "chapter_list" in raw[0]:
            chapters: list[dict[str, Any]] = []
            for vol in raw:
                chapters.extend(vol.get("chapter_list") or [])
            return chapters
        return list(raw) if isinstance(raw, list) else []

    def book_chapter_meta(self, book_id: str, chapter_id: str) -> dict[str, Any]:
        """按 chapter_id 从目录中取章节元数据（含 content_md5）。"""
        for ch in self.iter_chapters(self.book_chapter_list(book_id)):
            if str(ch.get("id")) == str(chapter_id):
                return ch
        raise QimaoApiError("CHAPTER_NOT_FOUND", f"章节不存在: {chapter_id}")

    def book_player_info(
        self,
        book_id: str,
        chapter_id: str,
        *,
        content_md5: str | None = None,
        new_user: str = "0",
    ) -> dict[str, Any]:
        """
        听书播放器配置（含可选音色列表 voice_list）。
        每项常见字段：voice_id, voice_name, voice_type, voice_url, icon_url, voice_desc
        """
        if not content_md5:
            content_md5 = str(self.book_chapter_meta(book_id, chapter_id).get("content_md5") or "")
        return self._signed_get(
            API_KS,
            "/api/v1/get-player-info",
            {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "new_user": new_user,
                "content_md5": content_md5,
            },
        )

    def book_voice_list(
        self,
        book_id: str,
        chapter_id: str,
        *,
        content_md5: str | None = None,
        new_user: str = "0",
    ) -> list[dict[str, Any]]:
        """当前章节可选 AI 音色列表。"""
        data = self.book_player_info(
            book_id, chapter_id, content_md5=content_md5, new_user=new_user
        )
        vl = data.get("voice_list")
        return vl if isinstance(vl, list) else []

    def book_ai_preload(
        self,
        book_id: str,
        chapter_ids: str | list[str],
        voice_id: str,
        *,
        content_md5: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """
        预加载章节 AI 音频（voice_type=5 等多角色音色常用）。
        返回含 chapter_list: [{chapter_id, voice_url, caption_url, duration, ...}]
        """
        if isinstance(chapter_ids, list):
            cids = chapter_ids
            chapter_ids = ",".join(cids)
            if content_md5 is None:
                content_md5 = [
                    str(self.book_chapter_meta(book_id, cid).get("content_md5") or "") for cid in cids
                ]
        if isinstance(content_md5, list):
            content_md5 = ",".join(content_md5)
        if not content_md5 and "," not in str(chapter_ids):
            content_md5 = str(self.book_chapter_meta(book_id, str(chapter_ids)).get("content_md5") or "")

        data = self._signed_get(
            API_KS,
            "/api/v1/listen/preload-chapter-list",
            {
                "book_id": book_id,
                "chapter_id": chapter_ids,
                "voice_id": str(voice_id),
                "content_md5": content_md5 or "",
            },
        )
        return data if isinstance(data, dict) else {}

    def book_ai_audio_url(
        self,
        book_id: str,
        chapter_id: str,
        voice_id: str,
        *,
        content_md5: str | None = None,
    ) -> str:
        """
        获取指定音色、章节的 AI 听书 mp3 直链。
        - voice_type=2 等：通常由 get-player-info.voice_list[].voice_url 直接给出
        - voice_type=5 等：走 preload-chapter-list 的 chapter_list[].voice_url
        """
        if not content_md5:
            content_md5 = str(self.book_chapter_meta(book_id, chapter_id).get("content_md5") or "")

        for v in self.book_voice_list(book_id, chapter_id, content_md5=content_md5):
            if str(v.get("voice_id")) == str(voice_id):
                url = (v.get("voice_url") or "").strip()
                if url:
                    return url
                break

        pre = self.book_ai_preload(book_id, chapter_id, voice_id, content_md5=content_md5)
        for item in pre.get("chapter_list") or []:
            if str(item.get("chapter_id")) == str(chapter_id):
                url = (item.get("voice_url") or "").strip()
                if url:
                    return url

        raise QimaoApiError(
            "NO_AUDIO_URL",
            f"音色 voice_id={voice_id} 章节 {chapter_id} 未返回 voice_url",
        )

    def download_book_ai_audio(
        self,
        book_id: str,
        chapter_id: str,
        voice_id: str,
        out_path: str | Path,
        *,
        content_md5: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        """下载书籍 AI 听书单章 mp3。"""
        from urllib.parse import urlparse

        url = self.book_ai_audio_url(book_id, chapter_id, voice_id, content_md5=content_md5)
        host = urlparse(url).netloc
        referer = f"https://{host}/" if host else API_KS
        return self.download_url(url, out_path, overwrite=overwrite, referer=referer)

    def book_download_info(self, book_id: str, chapter_ids: str | list[str]) -> dict[str, Any]:
        """
        批量下载信息（可能需 VIP/书币）。
        chapter_ids: 章节 id，多个用逗号拼接。
        """
        if isinstance(chapter_ids, list):
            chapter_ids = ",".join(chapter_ids)
        return self._signed_get(
            API_BC,
            "/api/v1/book/download",
            {"id": book_id, "chapter_ids": chapter_ids},
        )

    # --- 听书 ---

    def search_listen(self, wd: str, *, page: int = 1, **kwargs: Any) -> dict[str, Any]:
        """听书搜索（tab=2）；若无 album 结果可改用 search_words 筛 is_audio"""
        return self.search_words(wd, page=page, tab=TAB_LISTEN, **kwargs)

    @staticmethod
    def first_listen_item(search_result: dict[str, Any]) -> dict[str, Any] | None:
        """从搜索结果中取第一条带 album_id 的听书"""
        books = (search_result.get("data") or {}).get("books") or []
        for b in books:
            if b.get("album_id"):
                return b
        return None

    def album_chapter_list(self, album_id: str) -> dict[str, Any]:
        """听书专辑章节目录"""
        return self._signed_get(API_KS, "/api/v1/album/chapter-list", {"album_id": album_id})

    def album_audio_urls(self, album_id: str, chapter_ids: str | list[str]) -> list[dict[str, Any]]:
        """
        获取听书章节音频直链。
        返回 voice_list: [{voice_url, chapter_id, duration, audio_size, ...}]
        """
        if isinstance(chapter_ids, list):
            chapter_ids = ",".join(chapter_ids)
        data = self._signed_get(
            API_KS,
            "/api/v1/album/preload-audio-list",
            {"album_id": album_id, "chapter_ids": chapter_ids},
        )
        if isinstance(data, dict):
            return data.get("voice_list") or []
        return []

    def download_album_audio(
        self,
        album_id: str,
        chapter_id: str,
        out_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """下载听书单集 mp3（album_id + chapter_id）。"""
        voices = self.album_audio_urls(album_id, chapter_id)
        if not voices:
            raise QimaoApiError("NO_AUDIO_URL", "未拿到 voice_url（可能需登录/版权限制）")
        url = voices[0].get("voice_url") or ""
        if not url:
            raise QimaoApiError("NO_AUDIO_URL", "voice_url 为空")
        return self.download_url(url, out_path, overwrite=overwrite, referer=API_KS)

    # --- 短剧 ---

    def search_playlet(self, wd: str, *, page: int = 1, **kwargs: Any) -> dict[str, Any]:
        """短剧搜索"""
        params = _search_base_params(wd, tab=TAB_BOOK, page=page, **kwargs)
        params["sign"] = query_sign(params)
        url = f"{API_BC}/search/v1/playlet?{urlencode(params)}"
        r = self._client.get(url, headers=self._common_headers())
        r.raise_for_status()
        j = r.json()
        if "errors" in j:
            e = j["errors"]
            raise QimaoApiError(str(e.get("code")), str(e.get("details", "")), j)
        return j

    def playlet_info(self, playlet_id: str) -> dict[str, Any]:
        """短剧详情，含分集列表（video_list 等字段）"""
        return self._signed_get(API_GW, "/playlet/api/info", {"playlet_id": playlet_id})

    def playlet_episodes(self, playlet_id: str) -> list[dict[str, Any]]:
        """短剧分集列表（play_list）"""
        data = self.playlet_info(playlet_id)
        pl = data.get("play_list")
        return pl if isinstance(pl, list) else []

    def playlet_video_urls(self, playlet_id: str) -> list[dict[str, Any]]:
        """分集条目，含 video_url / video_url_h265"""
        return self.playlet_episodes(playlet_id)

    def download_playlet_episode(
        self,
        playlet_id: str,
        episode_index: int,
        out_path: str | Path,
        *,
        prefer_h265: bool = False,
        overwrite: bool = False,
    ) -> Path:
        """
        下载短剧某一集（mp4）。episode_index 从 1 开始。
        """
        eps = self.playlet_episodes(playlet_id)
        if not eps or episode_index < 1 or episode_index > len(eps):
            raise QimaoApiError("NO_EPISODE", f"分集不存在：{episode_index}")
        ep = eps[episode_index - 1]
        url = ""
        if prefer_h265:
            url = ep.get("video_url_h265") or ""
        url = url or ep.get("video_url") or ""
        if not url:
            raise QimaoApiError("NO_VIDEO_URL", "未拿到 video_url（可能需额外播放接口）")
        out = Path(out_path)
        # playlet 的 video_url 常为 m3u8，合并解密后为可播放 .ts
        if out.suffix.lower() in {".mp4", ".m4v"}:
            out = out.with_suffix(".ts")
        head_probe = self._client.get(url, headers={"referer": API_GW}).content[:16]
        if head_probe.startswith(b"#EXTM3U") or ".m3u8" in url:
            return self.download_hls_to_ts(url, out, referer=API_GW, overwrite=overwrite)
        return self.download_url(url, out, overwrite=overwrite, referer=API_GW)

    # --- 便捷方法 ---

    @staticmethod
    def search_books(search_result: dict[str, Any]) -> list[dict[str, Any]]:
        books = (search_result.get("data") or {}).get("books") or []
        return books if isinstance(books, list) else []

    @staticmethod
    def filter_book_items(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """正文书籍：有 book id，且非纯听书条目。"""
        out: list[dict[str, Any]] = []
        for b in books:
            bid = b.get("id") or b.get("book_id")
            if not bid:
                continue
            if str(b.get("is_audio", "0")) == "1":
                continue
            out.append(b)
        return out

    @staticmethod
    def filter_listen_items(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """听书专辑：有 album_id 的条目（含 is_audio=1 或独立专辑）。"""
        out: list[dict[str, Any]] = []
        for b in books:
            aid = b.get("album_id")
            if not aid:
                continue
            if b.get("id") and str(b.get("is_audio", "0")) != "1":
                continue
            out.append(b)
        return out

    @staticmethod
    def voice_download_mode(voice: dict[str, Any]) -> str:
        """
        音色下载路由：ai=书籍 preload；album=真人专辑（voice_id 即 album_id）。
        App 内可在同一 voice_list 切换，但后端 API 不同。
        """
        if str(voice.get("voice_type", "")) == "3":
            return "album"
        return "ai"

    @staticmethod
    def voice_unavailable_hint(voice: dict[str, Any]) -> str | None:
        vid = str(voice.get("voice_id", ""))
        vtype = str(voice.get("voice_type", ""))
        if vtype == "2" and vid in {"2", "3"}:
            return "CDN cdn-tts-ali 公网常不可达"
        return None

    def download_voice_audio(
        self,
        *,
        book_id: str | None,
        chapter_id: str,
        voice: dict[str, Any],
        out_path: str | Path,
        content_md5: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        """按 voice_type 自动路由：AI 走书籍接口，真人(type=3)走专辑接口。"""
        mode = self.voice_download_mode(voice)
        vid = str(voice.get("voice_id", ""))
        if mode == "album":
            return self.download_album_audio(vid, chapter_id, out_path, overwrite=overwrite)
        if not book_id:
            raise QimaoApiError("NO_BOOK", "AI 音色需要 book_id")
        return self.download_book_ai_audio(
            book_id, chapter_id, vid, out_path, content_md5=content_md5, overwrite=overwrite
        )

    @staticmethod
    def first_book(search_result: dict[str, Any]) -> dict[str, Any] | None:
        books = QimaoClient.search_books(search_result)
        return books[0] if books else None

    @staticmethod
    def first_chapter(chapter_list_data: dict[str, Any]) -> dict[str, Any] | None:
        chapters = QimaoClient.iter_chapters(chapter_list_data)
        return chapters[0] if chapters else None

# ===== 交互 CLI =====

#!/usr/bin/env python3
"""
七猫媒体下载（交互式 CLI）

支持：书籍正文 / 书籍听书（AI+真人音色切换）/ 听书专辑 / 短剧视频
流程：选择类型 → 搜索 → 选择条目 → 加载完整目录 → 下载

非交互（兼容旧用法）：
  python download_media.py --book-only "剑来"
  python download_media.py --ai-voice "剑来" --voice-id 10 --chapter 1
"""


import argparse
import re
import sys
from pathlib import Path
from typing import Any



def safe_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s)
    return s[:80] if len(s) > 80 else s


def prompt(msg: str = "> ") -> str:
    try:
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def pick_index(items: list[Any], *, allow_zero: bool = False) -> int | None:
    if not items:
        return None
    lo = 0 if allow_zero else 1
    while True:
        s = prompt(f"请输入序号 ({lo}-{len(items)}，回车取消): ")
        if not s:
            return None
        if not s.isdigit():
            print("请输入有效数字")
            continue
        n = int(s)
        if lo <= n <= len(items):
            return n - 1 if not allow_zero else n
        print(f"超出范围，请选 {lo}-{len(items)}")


def parse_chapter_selection(raw: str, total: int) -> list[int] | None:
    """解析章节选择：1 / 1-5 / 1,3,5 / all / 全部 → 0-based 索引列表。"""
    s = raw.strip().lower()
    if not s:
        return None
    if s in {"all", "全部", "a", "*"}:
        return list(range(total))

    indices: set[int] = set()
    for part in re.split(r"[,，\s]+", s):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                print(f"无效范围：{part}")
                return None
            start, end = int(a), int(b)
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                if i < 1 or i > total:
                    print(f"章节 {i} 超出 1-{total}")
                    return None
                indices.add(i - 1)
        elif part.isdigit():
            i = int(part)
            if i < 1 or i > total:
                print(f"章节 {i} 超出 1-{total}")
                return None
            indices.add(i - 1)
        else:
            print(f"无法解析：{part}")
            return None
    return sorted(indices) if indices else None


def browse_chapters(
    chapters: list[dict[str, Any]],
    *,
    title_key: str = "title",
    id_key: str = "id",
    page_size: int = 30,
) -> None:
    total = len(chapters)
    if total == 0:
        print("（无章节）")
        return
    page = 0
    max_page = (total - 1) // page_size
    while True:
        start = page * page_size
        end = min(start + page_size, total)
        print(f"\n--- 目录 {start + 1}-{end} / 共 {total} ---")
        for i in range(start, end):
            ch = chapters[i]
            title = ch.get(title_key) or ch.get("chapter_title") or f"第{i + 1}项"
            cid = ch.get(id_key) or ch.get("chapter_id") or "?"
            print(f"  {i + 1:4d}. {title}  (id={cid})")
        if max_page == 0:
            break
        cmd = prompt("n下一页 / p上一页 / 回车结束浏览: ").lower()
        if not cmd:
            break
        if cmd in {"n", "next", "下一页"} and page < max_page:
            page += 1
        elif cmd in {"p", "prev", "上一页"} and page > 0:
            page -= 1


def choose_chapters(chapters: list[dict[str, Any]]) -> list[int] | None:
    total = len(chapters)
    if total == 0:
        print("无章节目录")
        return None
    print(f"\n共 {total} 章。输入示例：1  |  1-5  |  1,3,5  |  all（全部）")
    browse_chapters(chapters)
    raw = prompt("选择要下载的章节: ")
    return parse_chapter_selection(raw, total)


def search_and_pick(
    c: QimaoClient,
    kw: str,
    items: list[dict[str, Any]],
    *,
    kind: str,
) -> dict[str, Any] | None:
    if not items:
        print(f"未找到{kind}结果")
        return None
    print(f"\n{kind}搜索结果（{len(items)} 条）：")
    for i, b in enumerate(items, 1):
        title = b.get("title") or "?"
        extra = []
        if b.get("id"):
            extra.append(f"book={b['id']}")
        if b.get("album_id"):
            extra.append(f"album={b['album_id']}")
        if b.get("total_num"):
            extra.append(str(b["total_num"]))
        elif b.get("chapter_count"):
            extra.append(f"{b['chapter_count']}章")
        tag = " | ".join(extra)
        print(f"  {i}. {title}" + (f"  [{tag}]" if tag else ""))
    idx = pick_index(items)
    return items[idx] if idx is not None else None


def search_books(c: QimaoClient, kw: str) -> list[dict[str, Any]]:
    sr = c.search_words(kw, tab=TAB_BOOK)
    return QimaoClient.filter_book_items(QimaoClient.search_books(sr))


def search_listen_items(c: QimaoClient, kw: str) -> list[dict[str, Any]]:
    sr = c.search_listen(kw)
    items = QimaoClient.filter_listen_items(QimaoClient.search_books(sr))
    if not items:
        sr = c.search_words(kw, tab=TAB_BOOK)
        items = QimaoClient.filter_listen_items(QimaoClient.search_books(sr))
    return items


def search_playlets(c: QimaoClient, kw: str) -> list[dict[str, Any]]:
    sr = c.search_playlet(kw)
    return [b for b in QimaoClient.search_books(sr) if b.get("id")]


def pick_voice(c: QimaoClient, book_id: str, chapter_id: str) -> dict[str, Any] | None:
    try:
        voices = c.book_voice_list(book_id, chapter_id)
    except QimaoApiError as e:
        print(f"获取音色失败：{e.message}")
        return None
    if not voices:
        print("无可选音色")
        return None

    print("\n可选音色（App 内可切换；AI 与真人后端 API 不同）：")
    ai_voices: list[dict[str, Any]] = []
    human_voices: list[dict[str, Any]] = []
    for v in voices:
        if QimaoClient.voice_download_mode(v) == "album":
            human_voices.append(v)
        else:
            ai_voices.append(v)

    display: list[dict[str, Any]] = []
    if ai_voices:
        print("  [AI 音色]")
        for v in ai_voices:
            display.append(v)
            hint = QimaoClient.voice_unavailable_hint(v)
            mark = f"  ⚠ {hint}" if hint else ""
            print(
                f"    {len(display)}. {v.get('voice_name')}  "
                f"(id={v.get('voice_id')}, type={v.get('voice_type')}){mark}"
            )
    if human_voices:
        print("  [真人演播 / 专辑]")
        for v in human_voices:
            display.append(v)
            print(
                f"    {len(display)}. {v.get('voice_name')}  "
                f"(id={v.get('voice_id')}=album_id, type={v.get('voice_type')})"
            )
        print("  提示：选真人音色后，章节目录会切换为专辑目录（与正文章节数/标题不同）")

    idx = pick_index(display)
    if idx is None:
        return None
    voice = display[idx]
    hint = QimaoClient.voice_unavailable_hint(voice)
    if hint:
        ok = prompt(f"该音色可能不可用（{hint}），仍要尝试？(y/N): ").lower()
        if ok not in {"y", "yes", "是"}:
            return None
    return voice


def load_chapters_for_voice(
    c: QimaoClient,
    book_id: str,
    voice: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """返回 (章节列表, 模式说明)。"""
    if QimaoClient.voice_download_mode(voice) == "album":
        album_id = str(voice.get("voice_id"))
        cl = c.album_chapter_list(album_id)
        chapters = cl.get("chapter_list") or []
        return chapters, f"真人专辑 album_id={album_id}"
    cl = c.book_chapter_list(book_id)
    return c.iter_chapters(cl), f"书籍 AI 听书 book_id={book_id}"


def download_chapters_txt(
    c: QimaoClient,
    book_id: str,
    title: str,
    chapters: list[dict[str, Any]],
    indices: list[int],
    out_dir: Path,
) -> None:
    cids = [str(chapters[i]["id"]) for i in indices]
    name = safe_name(title)
    if len(indices) == len(chapters):
        out = out_dir / f"book_{name}.txt"
    else:
        out = out_dir / f"book_{name}_ch{indices[0] + 1}-{indices[-1] + 1}.txt"
    print(f"正在下载 {len(cids)} 章正文…")
    p = c.download_book_txt(book_id, out, chapter_ids=cids, overwrite=True)
    print(f"已保存：{p}")


def download_chapters_audio(
    c: QimaoClient,
    *,
    book_id: str | None,
    album_id: str | None,
    title: str,
    voice: dict[str, Any] | None,
    chapters: list[dict[str, Any]],
    indices: list[int],
    out_dir: Path,
    prefix: str,
) -> None:
    vname = ""
    if voice:
        vname = safe_name(str(voice.get("voice_name") or voice.get("voice_id") or ""))
    name = safe_name(title)
    mode = QimaoClient.voice_download_mode(voice) if voice else "album"
    ok, fail = 0, 0
    for i in indices:
        ch = chapters[i]
        cid = str(ch.get("id") or ch.get("chapter_id"))
        ch_title = safe_name(str(ch.get("title") or ch.get("chapter_title") or f"ch{cid}"))
        if voice and mode == "ai":
            out = out_dir / f"{prefix}_{name}_{vname}_{ch_title}.mp3"
            try:
                c.download_voice_audio(
                    book_id=book_id, chapter_id=cid, voice=voice, out_path=out, overwrite=True
                )
                print(f"  ✓ [{i + 1}] {ch.get('title')}")
                ok += 1
            except QimaoApiError as e:
                print(f"  ✗ [{i + 1}] {e.message}")
                fail += 1
        else:
            aid = album_id or (str(voice.get("voice_id")) if voice else "")
            if not aid:
                print("缺少 album_id")
                return
            out = out_dir / f"{prefix}_{name}_{ch_title}.mp3"
            try:
                c.download_album_audio(aid, cid, out, overwrite=True)
                print(f"  ✓ [{i + 1}] {ch.get('title')}")
                ok += 1
            except QimaoApiError as e:
                print(f"  ✗ [{i + 1}] {e.message}")
                fail += 1
    print(f"完成：成功 {ok}，失败 {fail}")


def download_playlet_indices(
    c: QimaoClient,
    playlet_id: str,
    title: str,
    indices: list[int],
    out_dir: Path,
) -> None:
    name = safe_name(title)
    ok, fail = 0, 0
    for i in indices:
        ep_no = i + 1
        out = out_dir / f"playlet_{name}_ep{ep_no}.ts"
        try:
            c.download_playlet_episode(playlet_id, ep_no, out, overwrite=True)
            print(f"  ✓ 第 {ep_no} 集")
            ok += 1
        except QimaoApiError as e:
            print(f"  ✗ 第 {ep_no} 集：{e.message}")
            fail += 1
    print(f"完成：成功 {ok}，失败 {fail}")


def flow_book(c: QimaoClient, out_dir: Path) -> None:
    kw = prompt("搜索书籍关键词: ")
    if not kw:
        return
    item = search_and_pick(c, kw, search_books(c, kw), kind="书籍")
    if not item:
        return
    book_id = str(item.get("id") or item.get("book_id"))
    title = item.get("title") or kw
    print(f"\n加载章节目录…")
    cl = c.book_chapter_list(book_id)
    chapters = c.iter_chapters(cl)
    print(f"《{title}》共 {len(chapters)} 章（正文目录）")

    while True:
        print("\n书籍操作：")
        print("  1. 浏览/下载正文 txt")
        print("  2. 听书下载（可选 AI / 真人音色，支持切换）")
        print("  0. 返回")
        op = prompt("选择: ")
        if op in {"0", "q", ""}:
            return
        if op == "1":
            indices = choose_chapters(chapters)
            if indices:
                download_chapters_txt(c, book_id, title, chapters, indices, out_dir)
        elif op == "2":
            flow_book_voice(c, out_dir, book_id=book_id, title=title)
        else:
            print("无效选项")


def flow_book_voice(
    c: QimaoClient,
    out_dir: Path,
    *,
    book_id: str,
    title: str,
) -> None:
    cl = c.book_chapter_list(book_id)
    book_chapters = c.iter_chapters(cl)
    if not book_chapters:
        print("无章节目录")
        return
    ref_ch = book_chapters[0]
    ref_cid = str(ref_ch["id"])

    voice: dict[str, Any] | None = None
    chapters = book_chapters
    mode_desc = "书籍 AI"

    while True:
        if voice:
            vlabel = voice.get("voice_name") or voice.get("voice_id")
            print(f"\n当前音色：{vlabel}（{mode_desc}，共 {len(chapters)} 章）")
        else:
            print(f"\n尚未选择音色（正文共 {len(book_chapters)} 章）")

        print("  1. 选择/切换音色")
        print("  2. 浏览目录")
        print("  3. 下载音频")
        print("  0. 返回")
        op = prompt("选择: ")
        if op in {"0", "q", ""}:
            return
        if op == "1":
            v = pick_voice(c, book_id, ref_cid)
            if v:
                voice = v
                chapters, mode_desc = load_chapters_for_voice(c, book_id, voice)
                print(f"已切换音色，已加载 {len(chapters)} 章（{mode_desc}）")
        elif op == "2":
            browse_chapters(chapters)
        elif op == "3":
            if not voice:
                print("请先选择音色")
                continue
            indices = choose_chapters(chapters)
            if indices:
                download_chapters_audio(
                    c,
                    book_id=book_id if QimaoClient.voice_download_mode(voice) == "ai" else None,
                    album_id=str(voice.get("voice_id"))
                    if QimaoClient.voice_download_mode(voice) == "album"
                    else None,
                    title=title,
                    voice=voice,
                    chapters=chapters,
                    indices=indices,
                    out_dir=out_dir,
                    prefix="bookai",
                )
        else:
            print("无效选项")


def flow_listen(c: QimaoClient, out_dir: Path) -> None:
    kw = prompt("搜索听书关键词: ")
    if not kw:
        return
    item = search_and_pick(c, kw, search_listen_items(c, kw), kind="听书")
    if not item:
        return
    album_id = str(item["album_id"])
    title = item.get("title") or kw
    print(f"\n加载专辑目录…")
    cl = c.album_chapter_list(album_id)
    chapters = cl.get("chapter_list") or []
    print(f"《{title}》共 {len(chapters)} 集")
    indices = choose_chapters(chapters)
    if indices:
        download_chapters_audio(
            c,
            book_id=None,
            album_id=album_id,
            title=title,
            voice=None,
            chapters=chapters,
            indices=indices,
            out_dir=out_dir,
            prefix="listen",
        )


def flow_playlet(c: QimaoClient, out_dir: Path) -> None:
    kw = prompt("搜索短剧关键词: ")
    if not kw:
        return
    item = search_and_pick(c, kw, search_playlets(c, kw), kind="短剧")
    if not item:
        return
    pid = str(item["id"])
    title = item.get("title") or kw
    print(f"\n加载分集列表…")
    episodes = c.playlet_episodes(pid)
    print(f"《{title}》共 {len(episodes)} 集")
    chapters = [{"id": i + 1, "title": ep.get("title") or f"第{i + 1}集"} for i, ep in enumerate(episodes)]
    indices = choose_chapters(chapters)
    if indices:
        download_playlet_indices(c, pid, title, indices, out_dir)


def interactive_main(c: QimaoClient, out_dir: Path) -> None:
    print("=" * 50)
    print("七猫媒体下载")
    print("=" * 50)
    while True:
        print("\n主菜单：")
        print("  1. 书籍（正文 txt + 听书音色切换）")
        print("  2. 听书（真人专辑）")
        print("  3. 短剧（视频）")
        print("  0. 退出")
        op = prompt("选择: ")
        if op in {"0", "q", "exit"}:
            print("再见")
            return
        if op == "1":
            flow_book(c, out_dir)
        elif op == "2":
            flow_listen(c, out_dir)
        elif op == "3":
            flow_playlet(c, out_dir)
        else:
            print("无效选项")


# --- 非交互（兼容旧脚本）---

def download_book_ai(
    c: QimaoClient,
    kw: str,
    out_dir: Path,
    *,
    voice_id: str = "2",
    chapter_index: int = 1,
    list_voices: bool = False,
) -> None:
    print(f"=== 书籍 AI 听书：{kw}（voice_id={voice_id}）===")
    sr = c.search_words(kw)
    book = c.first_book(sr)
    if not book:
        print("未找到书籍")
        return
    bid = str(book.get("id") or book.get("book_id"))
    title = safe_name(book.get("title") or kw)
    cl = c.book_chapter_list(bid)
    chapters = c.iter_chapters(cl)
    if not chapters:
        print("无章节目录")
        return
    if list_voices:
        ch0 = chapters[0]
        cid = str(ch0["id"])
        voices = c.book_voice_list(bid, cid)
        print(f"首章「{ch0.get('title')}」可选音色（共 {len(voices)} 个）：")
        for v in voices:
            hint = QimaoClient.voice_unavailable_hint(v) or ""
            print(
                f"  - id={v.get('voice_id')}  {v.get('voice_name')}  "
                f"type={v.get('voice_type')}  mode={QimaoClient.voice_download_mode(v)}  {hint}"
            )
        return
    if chapter_index < 1 or chapter_index > len(chapters):
        print(f"章节序号无效：{chapter_index}（共 {len(chapters)} 章）")
        return
    ch = chapters[chapter_index - 1]
    cid = str(ch["id"])
    ch_title = safe_name(str(ch.get("title") or f"ch{cid}"))
    voice: dict[str, Any] | None = None
    for v in c.book_voice_list(bid, cid):
        if str(v.get("voice_id")) == str(voice_id):
            voice = v
            break
    if not voice:
        voice = {"voice_id": voice_id, "voice_type": "5", "voice_name": voice_id}
    vname = safe_name(str(voice.get("voice_name") or voice_id))
    out = out_dir / f"bookai_{title}_{vname}_{ch_title}.mp3"
    try:
        c.download_voice_audio(book_id=bid, chapter_id=cid, voice=voice, out_path=out, overwrite=True)
        print(f"已下载：{out}")
    except QimaoApiError as e:
        print(f"下载失败：{e.message}")


def download_book(c: QimaoClient, kw: str, out_dir: Path, *, max_chapters: int | None = 3) -> None:
    print(f"=== 书籍正文：{kw} ===")
    sr = c.search_words(kw)
    book = c.first_book(sr)
    if not book:
        print("未找到书籍")
        return
    bid = str(book.get("id") or book.get("book_id"))
    title = safe_name(book.get("title") or kw)
    out = out_dir / f"book_{title}.txt"
    p = c.download_book_txt(bid, out, max_chapters=max_chapters, overwrite=True)
    print(f"已保存：{p}（前 {max_chapters} 章）")


def main() -> None:
    parser = argparse.ArgumentParser(description="七猫媒体下载")
    parser.add_argument("book_kw", nargs="?", help="（非交互）书籍搜索词")
    parser.add_argument("listen_kw", nargs="?", default=None, help="（非交互）听书搜索词")
    parser.add_argument("playlet_kw", nargs="?", default=None, help="（非交互）短剧搜索词")
    parser.add_argument("--book-only", action="store_true", help="仅下载书籍 txt")
    parser.add_argument("--ai-voice", metavar="KW", help="下载书籍听书（搜索关键词）")
    parser.add_argument("--voice-id", default="10", help="音色 voice_id，默认 10")
    parser.add_argument("--chapter", type=int, default=1, help="下载第几章（从 1 开始）")
    parser.add_argument("--list-voices", action="store_true", help="仅列出可选音色")
    parser.add_argument("--max-chapters", type=int, default=3, help="书籍 txt 下载章数")
    args = parser.parse_args()

    out_dir = Path(__file__).resolve().parent / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = QimaoConfig.tourist_login()

    legacy = args.book_only or args.ai_voice or args.book_kw is not None
    if not legacy:
        with QimaoClient(cfg) as c:
            interactive_main(c, out_dir)
        return

    with QimaoClient(cfg) as c:
        if args.ai_voice:
            download_book_ai(
                c,
                args.ai_voice,
                out_dir,
                voice_id=args.voice_id,
                chapter_index=args.chapter,
                list_voices=args.list_voices,
            )
        elif args.book_only:
            download_book(c, args.book_kw or "剑来", out_dir, max_chapters=args.max_chapters)
        else:
            download_book(c, args.book_kw or "剑来", out_dir, max_chapters=args.max_chapters)
            if args.listen_kw:
                kw = args.listen_kw
                item = QimaoClient.first_listen_item(c.search_listen(kw)) or QimaoClient.first_listen_item(
                    c.search_words(kw, tab=TAB_LISTEN)
                )
                if item:
                    aid = str(item["album_id"])
                    cl = c.album_chapter_list(aid)
                    chs = cl.get("chapter_list") or []
                    if chs:
                        title = safe_name(item.get("title") or kw)
                        out = out_dir / f"listen_{title}_ch1.mp3"
                        p = c.download_album_audio(aid, str(chs[0]["id"]), out, overwrite=True)
                        print(f"听书已下载：{p}")
            if args.playlet_kw:
                pr = c.search_playlet(args.playlet_kw)
                pb = c.first_book(pr)
                if pb:
                    title = safe_name(pb.get("title") or args.playlet_kw)
                    out = out_dir / f"playlet_{title}_ep1.ts"
                    p = c.download_playlet_episode(str(pb["id"]), 1, out, overwrite=True)
                    print(f"短剧已下载：{p}")


if __name__ == "__main__":
    main()
