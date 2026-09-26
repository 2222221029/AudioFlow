#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""喜马拉雅 PC 端 ``xm-sign`` 纯 Python 生成。

移植来源
--------
逆向成果 ``ximalaya_unified/platforms/ximalaya/sign.py`` +
``platforms/ximalaya/crypto.py``（2026-09-26 重构版）。

背景
----
PC 端取流接口 ``mobile/download/v2/track/...`` 强制要求 ``xm-sign`` 头，
形态为 ``{browserId}&&{sessionId}``，由数字联盟 dws SDK 生成。

源项目用可控变量对照实验（A/B/C/D 四组打 PC 下载接口）证明了：

* ``browserId`` **不参与校验** —— 随机 36 字节即可（C 组随机 browserId 通过）；
* ``sessionId`` 必须是真值 —— 但 SDK 在上报失败时会走**本地降级路径**，
  用纯算法直接算出一个 ``_2`` 后缀的 sessionId，该产物**同样通过校验**。

因此本模块完全复刻 dws SDK 的本地降级算法，使 PC 端签名**不需要浏览器、
Node 或 CDP**。

算法（明文恰好 32 字节）
------------------------
::

    h8     = 8 位随机 hex
    r4     = 4 位 base36 随机小数位（模拟 Math.random().toString(36)）
    t8     = 8 位 base62 毫秒时间戳
    f1     = 标志位经 base32（降级路径拿不到 payload，恒为 "0"）
    suffix = 201 / 202（上报失败分支传入，实测两者都出现过）
    body   = h8 + r4 + t8 + f1 + suffix          # 24 字符
    crc    = CRC32(body) 的 8 位小写 hex
    plain  = body + crc                          # 32 字节

    cipher = AES-128-CBC(plain, key=iv=SESSION_AES_KEY, PKCS7)   # 48 字节
    结果   = urlsafe_base64(cipher).rstrip("=") + "_2"            # 66 字符

已知边界（务必如实理解）
------------------------
真机从服务端拿到的是 **45 字符、``_1`` 后缀**的 sessionId，其 base64 部分
解出的是 32 字节**二进制**，用本模块的密钥解不出可读文本 —— 说明服务端那
一套用了不同的算法/密钥，**本模块无法生成 ``_1``**。

本模块生成的是 ``_2`` 降级形态。它的可用性依据是源项目的实测结论（降级
产物通过校验、同一 sessionId 复用 5 次成功 4 次、40 分钟后仍有效），而不是
本项目的端到端验证 —— 本项目没有可用于联调的账号凭证。若线上行为与预期
不符，用 :func:`decode_session_id` 先确认自洽性，再检查 ``_1``/``_2`` 的
服务端策略是否已变更。
"""

from __future__ import annotations

import base64
import os
import random
import re
import threading
import time
import zlib
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: SDK 里硬编码的 AES key（原文 32 字符，实际只取前 16 字节作 key 与 iv）
SESSION_AES_KEY = b"y3hbnr8d4s2ztjbca1wgxk6mqktf9pxr"[:16]

#: 降级模式后缀。服务端返回的正常 sid 是 "_1"，本地降级是 "_2"。
SESSION_SUFFIX_2 = "_2"

#: uploadUPDATASession 各失败分支传入的 suffix（实测 201 / 202 都出现过）
SESSION_SUFFIX_DEFAULT = 202

#: 实测真机 browserId 解码为 36 字节（→ 48 字符标准 base64）
BROWSER_ID_BYTES = 36

#: sessionId 缓存寿命。实测 40 分钟后仍有效，这里保守取 6 小时。
DEFAULT_TTL_SECONDS = 6 * 3600

#: PC 端 v4/baseInfo 的 query 签名常量（PC 4.0.15 app.asar ``le()``）。
#: 注意 "default" 一套与移动端签名常量完全相同，属同一份密钥。
PC_SIGN_KEYS: Dict[str, str] = {
    "mac": "e51c699a2F8E27A765aba368afa70404",
    "win": "Cf4F82633BbFfED0c68AD98e4703E2f1",
    "default": "9e3B103bA2d2cb56e805B3cCeB2512E3",
}

#: 签名 IV 前缀（12 字符，补上时间戳后 4 位正好 16 字节）
PC_IV_PREFIXES: Dict[str, str] = {
    "mac": "W1sHv!09ug@1",
    "win": "asWE@87%gSiL",
    "default": "M%6)W5F6@Jj~",
}

#: PC 端 v4/baseInfo 签名后缀
PC_APP_KEY = "0zpnlXAG"

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: 24 字符明文的字段布局：h8 + r4 + t8 + flags + suffix(3 位数字)
_BODY_RE = re.compile(
    r"^(?P<h8>[0-9a-f]{8})(?P<r4>[0-9a-z]{4})(?P<t8>[0-9a-zA-Z]{8})"
    r"(?P<flags>[0-9a-v]*)(?P<suffix>\d{3})$"
)

_lock = threading.Lock()


class XmSignError(RuntimeError):
    """签名生成失败。"""


# ---------------------------------------------------------------------------
# 基础编码
# ---------------------------------------------------------------------------

def b64url_encode(raw: bytes) -> str:
    """URL 安全 base64，**去掉尾部 '='**。

    Android 的 ``Base64.encodeToString(..., URL_SAFE)`` 会追加换行，照搬会让
    线上 query 变成 ``sign=...%0A``，服务端在权限校验之前就返回 ``ret=1001``。
    这里统一去掉 padding 且不带任何空白。
    """
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _aes128_cbc_encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC + PKCS7。复用项目已有的 pycryptodome 依赖。"""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
    except ImportError as exc:  # pragma: no cover - 依赖缺失时才触发
        raise XmSignError("缺少 pycryptodome，无法生成喜马拉雅 PC 端签名") from exc
    if len(key) != 16 or len(iv) != 16:
        raise XmSignError("AES-128 要求 key 与 iv 均为 16 字节")
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain, AES.block_size))


def _aes128_cbc_decrypt(cipher: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密 + PKCS7 去填充（仅用于自检与诊断）。"""
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
    except ImportError as exc:  # pragma: no cover
        raise XmSignError("缺少 pycryptodome，无法解码喜马拉雅 sessionId") from exc
    raw = AES.new(key, AES.MODE_CBC, iv).decrypt(cipher)
    try:
        return unpad(raw, AES.block_size)
    except ValueError:
        return raw


def _frac_base36(x: float, digits: int = 4) -> str:
    """模拟 JS ``Math.random().toString(36).substring(2, 6)``。

    JS 对 [0,1) 的浮点数输出 ``0.`` + base36 尾数，``substring(2, 6)`` 取随后
    4 位。服务端并不校验这 4 位的具体内容，只要求形态与取值域一致。
    """
    out = []
    for _ in range(digits):
        x *= 36
        d = int(x)
        out.append(_B36[min(d, 35)])
        x -= d
    return "".join(out)


def _to_base62(n: int, width: int = 8) -> str:
    """数字转 base62，左侧补 '0' 到指定宽度（对齐 SDK 的 padStart 行为）。"""
    if n <= 0:
        return "0" * width
    out = ""
    while n > 0:
        out = _B62[n % 62] + out
        n //= 62
    return out.rjust(width, "0")


def _to_base32(n: int) -> str:
    """数字转 base32（SDK 用的是 base36 字符表的前 32 个字符）。"""
    if n <= 0:
        return "0"
    out = ""
    while n > 0:
        out = _B36[n % 32] + out
        n //= 32
    return out


# ---------------------------------------------------------------------------
# sessionId 生成与校验
# ---------------------------------------------------------------------------

def generate_session_id(suffix: int = SESSION_SUFFIX_DEFAULT,
                        flags: str = "00000",
                        ts_ms: Optional[int] = None,
                        rng: Optional[random.Random] = None) -> str:
    """纯 Python 复现 dws SDK 的本地降级 sessionId。

    :param suffix: 上报失败分支传入的长度标记（201 / 202）
    :param flags: 5 位标志串，由 payload 的 csl/ets/rid.dev 决定；降级路径
                  拿不到这些字段，默认全 0（与真机降级行为一致）
    :param ts_ms: 毫秒时间戳，默认取当前时间
    :param rng: 注入的随机源，便于测试复现
    :return: 形如 ``xxxx..._2`` 的 66 字符 sessionId
    """
    r = rng or random
    h8 = "".join(r.choice("0123456789abcdef") for _ in range(8))
    r4 = _frac_base36(r.random(), 4)
    t8 = _to_base62(int(ts_ms if ts_ms is not None else time.time() * 1000), 8)
    f1 = _to_base32(int(flags, 2)) if flags else "0"

    body = f"{h8}{r4}{t8}{f1}{suffix}"
    crc = format(zlib.crc32(body.encode("ascii")) & 0xFFFFFFFF, "08x")
    plain = (body + crc).encode("ascii")
    if len(plain) != 32:
        raise XmSignError(f"sessionId 明文长度异常: {len(plain)} 字节（应为 32）")

    cipher = _aes128_cbc_encrypt(plain, SESSION_AES_KEY, SESSION_AES_KEY)
    return b64url_encode(cipher) + SESSION_SUFFIX_2


def decode_session_id(session_id: str) -> Dict[str, object]:
    """把本模块生成的 ``_2`` sessionId 解回结构（自检 / 排障用）。

    :raises XmSignError: 形态不合法或 CRC 不匹配
    """
    value = str(session_id or "").strip()
    if not value.endswith(SESSION_SUFFIX_2):
        raise XmSignError(
            f"只支持解析本模块生成的 {SESSION_SUFFIX_2} 形态；"
            f"服务端下发的 _1 使用另一套算法"
        )
    core = value[: -len(SESSION_SUFFIX_2)]
    padded = core + "=" * (-len(core) % 4)
    try:
        cipher = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise XmSignError(f"sessionId base64 解码失败: {exc}") from exc
    plain = _aes128_cbc_decrypt(cipher, SESSION_AES_KEY, SESSION_AES_KEY)
    if len(plain) != 32:
        raise XmSignError(f"解码后长度异常: {len(plain)} 字节（应为 32）")
    body, crc = plain[:-8].decode("ascii", "replace"), plain[-8:].decode("ascii", "replace")
    expected = format(zlib.crc32(body.encode("ascii")) & 0xFFFFFFFF, "08x")
    if crc != expected:
        raise XmSignError(f"CRC 不匹配: 内嵌 {crc} / 实算 {expected}")
    match = _BODY_RE.match(body)
    if not match:
        raise XmSignError(f"明文布局不符合预期: {body!r}")
    return {
        "body": body,
        "crc": crc,
        "h8": match.group("h8"),
        "r4": match.group("r4"),
        "t8": match.group("t8"),
        "flags": match.group("flags"),
        "suffix": match.group("suffix"),
        "length": len(value),
    }


def random_browser_id() -> str:
    """生成一个形态合规的 browserId。

    服务端不校验这一半（源项目 C 组实验），所以随机即可；保持与真机一致的
    「36 字节 → 48 字符标准 base64」形态，避免被形态规则挡掉。
    """
    return base64.b64encode(os.urandom(BROWSER_ID_BYTES)).decode("ascii")


def build_xm_sign(session_id: str, browser_id: str = "") -> str:
    """组装 ``xm-sign`` 头。browserId 留空则随机生成。"""
    sid = (session_id or "").strip()
    if not sid:
        raise XmSignError("sessionId 为空，无法组装 xm-sign")
    return f"{browser_id or random_browser_id()}&&{sid}"


# ---------------------------------------------------------------------------
# PC 端 v4/baseInfo 的 query 签名
# ---------------------------------------------------------------------------

def make_pc_base_info_sign(track_id, ts_ms: int, device: str = "win",
                           app_key: str = PC_APP_KEY) -> str:
    """电脑版 v4/baseInfo 签名（PC 4.0.15 ``le()``）。

    明文 = ``f"{trackId}{device}{时间戳后4位}{suffix}"``，
    IV  = ``(iv_prefix + 时间戳后4位)`` 的 UTF-8 字节。

    :param device: 只有 ``mac`` / ``win`` 有意义；其余值落到 ``default`` 一套，
                   而 ``device=default`` 会被服务端以 ``ret=301`` 拒绝。
    """
    tail = str(int(ts_ms))[-4:]
    key_name = device if device in ("mac", "win") else "default"
    key = bytes.fromhex(PC_SIGN_KEYS[key_name])
    iv = (PC_IV_PREFIXES[key_name] + tail).encode("utf-8")
    plain = f"{track_id}{device}{tail}{app_key}".encode("utf-8")
    return b64url_encode(_aes128_cbc_encrypt(plain, key, iv))


# ---------------------------------------------------------------------------
# 签名缓存
# ---------------------------------------------------------------------------

class XmSignCache:
    """``xm-sign`` 的进程内 + 磁盘缓存。

    sessionId 一次生成后可长期复用（源项目实测复用 5 次成功 4 次，40 分钟后
    仍有效），因此缓存能显著减少风控暴露面。缓存文件默认落在 AudioFlow 的
    config 目录，避免污染项目工作区。

    任何签名失败的上游都应调用 :meth:`invalidate`，下次取用时会重新生成。
    """

    def __init__(self, path: Optional[str] = None, ttl: int = DEFAULT_TTL_SECONDS):
        self.ttl = int(ttl)
        self.path = str(path or self._default_path())
        self._sign = ""
        self._session_id = ""
        self._browser_id = ""
        self._created_at = 0.0

    @staticmethod
    def _default_path() -> str:
        try:
            from .platform_config import config_dir
            return str(config_dir() / "ximalaya_xm_session.json")
        except Exception:  # pragma: no cover - 配置层不可用时退化为纯内存缓存
            return ""

    # -- 内部 --------------------------------------------------------------
    def _fresh_locked(self) -> bool:
        return bool(self._session_id) and (time.time() - self._created_at) < self.ttl

    def _load_from_disk_locked(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            import json
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            return
        try:
            created = float(payload.get("ts") or 0)
        except (TypeError, ValueError):
            created = 0.0
        self._session_id = session_id
        self._browser_id = str(payload.get("browserId") or "").strip()
        self._created_at = created
        self._sign = build_xm_sign(self._session_id, self._browser_id)

    def _save_locked(self) -> None:
        if not self.path:
            return
        try:
            import json
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "sessionId": self._session_id,
                        "browserId": self._browser_id,
                        "ts": self._created_at,
                        "source": "pure-python",
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass

    # -- 对外 --------------------------------------------------------------
    def get(self, force_refresh: bool = False) -> str:
        """取一个可用的 ``xm-sign``，必要时生成并落盘。"""
        with _lock:
            if not force_refresh and not self._fresh_locked():
                self._load_from_disk_locked()
            if force_refresh or not self._fresh_locked():
                self._session_id = generate_session_id()
                self._browser_id = random_browser_id()
                self._created_at = time.time()
                self._sign = build_xm_sign(self._session_id, self._browser_id)
                self._save_locked()
            return self._sign

    def invalidate(self) -> None:
        """丢弃当前签名，下次取用时重新生成。"""
        with _lock:
            self._sign = ""
            self._session_id = ""
            self._browser_id = ""
            self._created_at = 0.0
            if self.path and os.path.exists(self.path):
                try:
                    os.remove(self.path)
                except OSError:
                    pass

    def status(self) -> Dict[str, object]:
        """诊断信息（不泄露签名本体）。"""
        with _lock:
            fresh = self._fresh_locked()
            age = (time.time() - self._created_at) if self._created_at else 0.0
            return {
                "ready": fresh,
                "session_id_length": len(self._session_id),
                "session_suffix": self._session_id[-2:] if self._session_id else "",
                "browser_id_length": len(self._browser_id),
                "age_seconds": round(age, 1),
                "ttl_seconds": self.ttl,
                "path": self.path,
            }
