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
本项目的端到端验证 —— 本项目没有可用于联调的账号凭证；且 2026-10-03 实测
该形态已被服务端拒绝（``download/v2/track`` 返回 ``ret=-1``）。因此本模块
的**主路径**改为线上签名 :class:`HdaaSignProvider`：向数字联盟设备指纹上报
服务（hdaa）发一次真实上报，取服务端下发的 ``cadd&&sid`` 拼出 ``xm-sign``
（2026-10-03 实测返回真实 ``_1`` 形态 sid）。本地 ``_2`` 算法仅作为上报
不可用（断网/端点变更）时的离线兜底；若线上行为与预期仍不符，用
:func:`decode_session_id` 先确认自洽性，再检查服务端策略是否已变更。
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
import uuid
import zlib
from typing import Dict, Optional, Tuple

#: 模块加载时快照的实时时钟。签名/上报链路一律用它而不是 ``time.time``：
#: 测试套件会用 ``mock.patch("xxx.time.time")`` 替换**全局** time 模块的属性，
#: 直接引用会让本模块在那些测试里拿到有限 side_effect 的 mock 时钟而崩溃。
import_clock = time.time  # noqa: E402  （导入期快照，patch 不影响）

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

# ---------------------------------------------------------------------------
# 线上签名（hdaa 设备指纹上报）常量
# ---------------------------------------------------------------------------

#: hdaa 上报载荷的 AES-ECB 密钥（du_web_sdk ``_getDeviceKey(0)`` 的返回）
HDAA_KEY = "m9ZtRrz:qujT8@da"

#: 数字联盟设备指纹上报端点。``r`` 参数为随机 uuid，服务端据此关联上报与下发。
HDAA_HOST = "hdaa.shuzilm.cn"
HDAA_REPORT_URL = "https://hdaa.shuzilm.cn/report?v=1.2.0&e=1&c=1&r={uuid}"

#: du_web_sdk 当前版本号（写入 device_info.GF9）
HDAA_SDK_VERSION = "2.0.0"

#: h5 站点的 appKey（device_info.KFp；签名随之绑定域名体系）
HDAA_APP_KEY = "h5_goyxvzyohd"

#: 上报请求超时
HDAA_TIMEOUT_SECONDS = 15

#: 上报请求 UA（与默认指纹 ew1.yV2 一致，避免模板内部不一致）
HDAA_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

#: 线上签名对的缓存寿命。服务端未声明有效期，参考社区实现按需每次上报；
#: 这里保守缓存 30 分钟，失效后取新对。
LIVE_SESSION_TTL_SECONDS = 30 * 60

#: 是否启用线上签名的环境变量开关（测试/离线环境置 1 可关闭）
LIVE_SIGN_DISABLE_ENV = "AUDIOFLOW_DISABLE_PC_LIVE_SIGN"

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
    t8 = _to_base62(int(ts_ms if ts_ms is not None else import_clock() * 1000), 8)
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
            "服务端下发的 _1 使用另一套算法"
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
# 线上签名：hdaa 设备指纹上报
# ---------------------------------------------------------------------------

#: 内置的公共设备指纹模板（与 Ximalaya-Downloader-Next 的
#: ``device_info_default.json`` 同源，已去掉 SDK 内部 collector / storage 字段）。
#: 用户可在 ``config_dir()/ximalaya_device_info.json`` 放自己的指纹覆盖它。
DEFAULT_DEVICE_INFO: Dict[str, object] = {
    "Zf5": 0,
    "GF9": HDAA_SDK_VERSION,
    "HW5": "t6pfoml9679z52kqw93uqu75eflqdg1bykhl",
    "uS7": "",
    "KFp": HDAA_APP_KEY,
    "ew1": {
        "Wg7": "Mozilla",
        "lV1": "Google Inc.",
        "Xt4": "Netscape",
        "yV2": HDAA_DEFAULT_UA,
        "KY1": "Win32",
        "Le3": "Tmxhcm9vei81LjAgKERybXdsZGggTUcgMTAuMDsgRHJtNjQ7IGM2NCkgWmtrb3ZEdnlQcmcvNTM3LjM2IChQU0dOTywgb3JwdiBUdnhwbCkgWHNpbG52LzEyNS4wLjAuMCBIenV6aXIvNTM3LjM2",
        "kH1": 900,
        "ad5": 1440,
        "Ua9": 24,
        "TQ6": 900,
        "kC7": 1440,
        "me8": 24,
        "eY9": True,
        "Kn2": False,
        "OM3": True,
        "sw8": False,
        "uW3": -1,
        "iO8": "https://www.ximalaya.com/",
        "By1": "www.ximalaya.com",
        "Gv4": "/",
        "ef2": "",
        "tZ2": "https:",
        "OG4": True,
        "kx1": True,
        "VD6": True,
        "Ov6": False,
        "lq3": "zh-CN",
        "ef5": ["zh-CN"],
        "OK3": 1,
        "Fg5": True,
        "qS2": [1440, 900, 1440, 900],
        "Fc5": "light",
    },
    "HK3": {
        "iI1": {
            "NF1": -1, "cA1": -1, "NK5": -1, "VP4": "-1.00",
            "RX5": -1, "VP6": -1, "tJ4": -1,
        },
        "ti4": {"xm9": True, "is3": 1},
        "AV9": 8,
        "aK8": "Google Inc. (0x00001D17)",
        "df6": "ANGLE (0x00001D17, ZX C-960 (0x00003A04) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "WB9": "8ba05e37b4b3f57bddb4904eb8f40204",
        "pD7": "d41d8cd98f00b204e9800998ecf8427e",
        "da2": "124.04347527516074",
        "dt2": 16,
        "Sy6": -480,
        "MS3": "Asia/Shanghai",
        "pi9": False,
        "Ao1": [],
        "BH5": 0,
        "UG4": [
            "Arial", "Arial Black", "Arial Narrow", "Calibri", "Cambria",
            "Cambria Math", "Comic Sans MS", "Consolas", "Courier",
            "Courier New", "Georgia", "Helvetica", "Impact", "Lucida Console",
            "Lucida Sans Unicode", "Microsoft Sans Serif", "MS Gothic",
            "MS PGothic", "MS Sans Serif", "MS Serif", "Palatino Linotype",
            "Segoe Print", "Segoe Script", "Segoe UI", "Segoe UI Light",
            "Segoe UI Semibold", "Segoe UI Symbol", "Tahoma", "Times",
            "Times New Roman", "Trebuchet MS", "Verdana", "Wingdings",
        ],
    },
    "fc9": {"cx4": "4g", "zY8": -1, "yj6": 1.5, "dV4": 200, "yX4": False},
    "adi": "070BF8:016D89:6BDE99:1600",
    "acd": "D2t6yNNzRqtSbN4GtZw4eGLn8tfc0Y1EYzqzMOCd9GiGMX17",
    "bdi": None,
    "bcd": None,
    "fd2": {
        "Pf5": 1783179255284,
        "Ja5": "070BF8:016D89:6BDE99:1600",
        "xz7": "2ccd7426-e999-efba-78c5-bbea1fc69729",
        "av1": "D2t6yNNzRqtSbN4GtZw4eGLn8tfc0Y1EYzqzMOCd9GiGMX17",
        "cp9": 0,
    },
    "exts": "",
    "startime": 1783179258568,
    "Zn6": {
        "oe2": "0",
        "EV9": "true",
        "xu2": "1441A790908125E9682A828824A003E99783D166409BBC4DD782F461C2955BA7",
        "CY8": "",
        "nE4": 928,
        "Tw1": [5799.099999904633, 6727.399999856949],
        "Sb1": False,
    },
    "jm9": 2,
    "dla": "",
    "swp": "",
    "ecm": "",
    "emm": "",
    "asu": "",
    "asu1": 0,
    "GJ2": "31b33ada-785b-efc7-cbad-c985880ba125-fcs011",
    "slw": "",
    "bds": "",
    "MT7": "33-00000-0000-1111111-000000-0011-000000-0000-00000-0",
    "bnd": "-0",
    "kec": "000000",
    "BG5": False,
    "Fd8": "1",
    "iq7": False,
    "DP5": "1441A7909C087DBBE7CE59881B9DF8B9",
    "lL1": "63649ded5da25cce1ae7d5f2a950c18a",
    "uT8": "-1",
    "sV5": 2,
    "Vo6": "",
    "infoF": {"lof": False, "baf": False, "auf": False, "iif": False},
}


class HdaaSignError(XmSignError):
    """hdaa 设备指纹上报 / 签名解析失败。"""


def _json_dumps_compact(data) -> str:
    """与 du_web_sdk 内部 ``JSON.stringify`` 一致的紧凑序列化。"""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _decode_uri_special(encoded: str) -> str:
    """模拟 JS ``decodeURIComponent``：兼容 %uXXXX 与 %XX。"""
    out = []
    i, n = 0, len(encoded)
    while i < n:
        ch = encoded[i]
        if ch == "%":
            if i + 5 <= n and encoded[i + 1] == "u":
                h = encoded[i + 2:i + 6]
                if re.match(r"^[0-9A-Fa-f]{4}$", h):
                    out.append(chr(int(h, 16)))
                    i += 6
                    continue
            if i + 2 <= n:
                h = encoded[i + 1:i + 3]
                if re.match(r"^[0-9A-Fa-f]{2}$", h):
                    out.append(chr(int(h, 16)))
                    i += 3
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _string_to_uint8(text: str) -> bytes:
    """按 du_web_sdk 的 URL 编码规则把字符串转成 bytes。

    JS ``encodeURIComponent`` 保留 ``A-Z a-z 0-9 - _ . ! ~ * ' ( )``；requests
    的默认 safe 是 ``/``，补上其余保留字符即与 JS 一致。percent 解码回字符后
    取 ``ord`` —— 对任意文本等价于 ``text.encode("utf-8")``。
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - 依赖缺失时才触发
        raise XmSignError("缺少 requests，无法进行 JS 式 URL 编码") from exc
    encoded = requests.utils.quote(text, safe=")!~*'(")
    decoded = _decode_uri_special(encoded)
    return bytes(ord(c) for c in decoded)


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7（hdaa 上报载荷与响应）。"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    return AES.new(key, AES.MODE_ECB).encrypt(pad(plaintext, AES.block_size))


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密 + PKCS7 去填充（hdaa 响应）。"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    return unpad(AES.new(key, AES.MODE_ECB).decrypt(ciphertext), AES.block_size)


def build_report_body(device_info: Dict, key: str = HDAA_KEY) -> bytes:
    """把 device_info 加工成上报用的二进制 body。

    流程：JSON 紧凑序列化 → JS 式 URL 编码 → zlib 压缩 →
    AES-128-ECB(PKCS7) 加密，返回原始密文 bytes。
    """
    json_str = _json_dumps_compact(device_info)
    uint8 = _string_to_uint8(json_str)
    return _aes_ecb_encrypt(zlib.compress(uint8, 6), key.encode("utf-8"))


def parse_report_response(text: str, key: str = HDAA_KEY) -> Dict:
    """解析 hdaa 上报响应：base64 → AES-ECB 解密 → JSON。

    :raises HdaaSignError: base64/解密/JSON 解析失败
    """
    value = str(text or "").strip()
    if not value:
        raise HdaaSignError("上报响应为空")
    try:
        cipher = base64.b64decode(value)
    except Exception as exc:
        raise HdaaSignError(f"上报响应 base64 解码失败: {exc}") from exc
    if not cipher or len(cipher) % 16:
        raise HdaaSignError("上报响应长度异常（非 AES 块大小整数倍）")
    try:
        plain = _aes_ecb_decrypt(cipher, key.encode("utf-8"))
        obj = json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise HdaaSignError(f"上报响应解密/解析失败: {exc}") from exc
    if not isinstance(obj, dict):
        raise HdaaSignError(f"上报响应不是 JSON 对象: {obj!r}")
    cadd = str(obj.get("cadd") or "")
    sid = str(obj.get("sid") or "")
    if not cadd or not sid:
        raise HdaaSignError(f"上报响应缺少 cadd/sid: {obj!r}")
    return obj


def _refresh_zf5(device_info: Dict) -> Dict:
    """返回一份将 Zf5 刷新为当前毫秒时间戳的副本。"""
    import copy
    info = copy.deepcopy(dict(device_info))
    info["Zf5"] = int(import_clock() * 1000)
    return info


def live_sign_env_enabled() -> bool:
    """是否允许走线上签名（环境变量可关闭，测试/离线环境用）。"""
    value = os.getenv(LIVE_SIGN_DISABLE_ENV, "").strip().lower()
    return value not in ("1", "true", "yes", "on")


class HdaaSignProvider:
    """线上 ``xm-sign`` 提供者：device_info → 上报 → ``cadd&&sid``。

    每次调用 :meth:`fetch_pair` 都打一次 hdaa 上报，直接使用该次响应里的
    ``cadd`` 与 ``sid``（不缓存：缓存由 :class:`XmSignCache` 负责）。

    :param session: 专用的 ``requests.Session``；留空则自建。**不要**复用携带
        喜马拉雅业务 Cookie 的会话，避免把 ``1&_token`` 泄露给第三方指纹服务
    :param device_info_path: 设备指纹 JSON 文件；空则自动探测
        ``config/ximalaya_device_info.json``，再回退内置模板
    """

    def __init__(self, *, key: str = HDAA_KEY, report_url: str = HDAA_REPORT_URL,
                 session=None, timeout: int = HDAA_TIMEOUT_SECONDS,
                 device_info: Optional[Dict] = None,
                 device_info_path: str = "", user_agent: str = ""):
        self._key = key
        self._report_url = report_url
        self._timeout = int(timeout)
        self._user_agent = user_agent or HDAA_DEFAULT_UA
        self._session = session
        self._device_info = device_info
        self._device_info_path = device_info_path or self._default_device_info_path()
        self._last_body_len = 0

    @staticmethod
    def _default_device_info_path() -> str:
        try:
            from .platform_config import config_dir
            candidate = config_dir() / "ximalaya_device_info.json"
            return str(candidate) if candidate.exists() else ""
        except Exception:  # pragma: no cover - 配置层不可用
            return ""

    # -- 内部 --------------------------------------------------------------
    def _load_device_info(self) -> Dict:
        if self._device_info:
            return self._device_info
        path = self._device_info_path
        if path and os.path.exists(path):
            try:
                import json as _json
                with open(path, encoding="utf-8") as handle:
                    loaded = _json.load(handle)
                if isinstance(loaded, dict) and loaded:
                    return loaded
            except (OSError, ValueError):
                pass
        return DEFAULT_DEVICE_INFO

    def _http_session(self):
        """惰性创建专用上报会话（不携带任何业务 Cookie）。"""
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    # -- 对外 --------------------------------------------------------------
    def fetch_pair(self) -> Tuple[str, str]:
        """一次上报，返回 ``(cadd, sid)``。失败抛 :class:`HdaaSignError`。"""
        device_info = _refresh_zf5(self._load_device_info())
        try:
            body = build_report_body(device_info, self._key)
        except Exception as exc:
            raise HdaaSignError(f"组装上报载荷失败: {exc}") from exc
        self._last_body_len = len(body)

        url = self._report_url.format(uuid=str(uuid.uuid4()))
        headers = {
            "Content-Type": "application/octet-stream",
            "User-Agent": self._user_agent,
            "Host": HDAA_HOST,
        }
        try:
            response = self._http_session().post(url, data=body, headers=headers,
                                                 timeout=self._timeout)
            response.raise_for_status()
        except Exception as exc:
            raise HdaaSignError(f"设备指纹上报失败: {exc}") from exc

        try:
            obj = parse_report_response(response.text, self._key)
        except HdaaSignError:
            raise
        return str(obj.get("cadd")), str(obj.get("sid"))

    def fetch_sign(self) -> str:
        """返回形如 ``{cadd}&&{sid}`` 的 xm-sign。"""
        cadd, sid = self.fetch_pair()
        return build_xm_sign(sid, cadd)


# ---------------------------------------------------------------------------
# 签名缓存
# ---------------------------------------------------------------------------

class XmSignCache:
    """``xm-sign`` 的进程内 + 磁盘缓存。

    ``live=True``（默认生产形态）时优先走 :class:`HdaaSignProvider` 线上上报，
    取用服务端下发的 ``cadd&&sid``；上报不可用（断网 / 端点变更 / 响应异常）
    时自动退化为本地 ``_2`` 算法，保证离线环境仍有签名可用。缓存落盘后跨
    进程复用（减少风控暴露面）。缓存文件默认落在 AudioFlow 的 config 目录。

    任何签名失败的上游都应调用 :meth:`invalidate`，下次取用时会重新取对。
    """

    def __init__(self, path: Optional[str] = None, ttl: Optional[int] = None,
                 live: bool = False, session=None,
                 timeout: int = HDAA_TIMEOUT_SECONDS):
        if ttl is None:
            ttl = LIVE_SESSION_TTL_SECONDS if live else DEFAULT_TTL_SECONDS
        self.ttl = int(ttl)
        self.path = str(path or self._default_path())
        self.live = bool(live)
        self._sign = ""
        self._session_id = ""
        self._browser_id = ""
        self._created_at = 0.0
        self._source = ""
        self._device_uuid = ""
        self.last_live_error = ""
        self._provider = HdaaSignProvider(session=session, timeout=timeout) if live else None

    @staticmethod
    def _default_path() -> str:
        try:
            from .platform_config import config_dir
            return str(config_dir() / "ximalaya_xm_session.json")
        except Exception:  # pragma: no cover - 配置层不可用时退化为纯内存缓存
            return ""

    # -- 内部 --------------------------------------------------------------
    def _fresh_locked(self) -> bool:
        return bool(self._session_id) and (import_clock() - self._created_at) < self.ttl

    def _load_from_disk_locked(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            import json
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return
        # 设备 UUID 独立于签名恢复（签名可能尚未生成，只有 UUID 先落盘）
        self._device_uuid = str(payload.get("deviceUuid") or "").strip()
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
        self._source = str(payload.get("source") or "")
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
                        "source": self._source or self._default_source(),
                        "deviceUuid": self._device_uuid,
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass

    def _default_source(self) -> str:
        return "hdaa-live" if (self.live and not self.last_live_error) else "pure-python"

    def _gen_local_locked(self) -> None:
        """降级：用本地 ``_2`` 算法生成一对签名（离线兜底）。"""
        self._session_id = generate_session_id()
        self._browser_id = random_browser_id()
        self._source = "pure-python"
        self._created_at = import_clock()

    def _fetch_live_locked(self) -> None:
        """线上取一对 ``cadd&&sid``；失败抛 :class:`HdaaSignError`。"""
        assert self._provider is not None
        cadd, sid = self._provider.fetch_pair()
        self._session_id = sid
        self._browser_id = cadd
        self._source = "hdaa-live"
        self._created_at = import_clock()

    # -- 对外 --------------------------------------------------------------
    def get(self, force_refresh: bool = False) -> str:
        """取一个可用的 ``xm-sign``，必要时重新取对并落盘。"""
        with _lock:
            if not force_refresh and not self._fresh_locked():
                self._load_from_disk_locked()
            if force_refresh or not self._fresh_locked():
                if self.live:
                    try:
                        self._fetch_live_locked()
                        self.last_live_error = ""
                    except HdaaSignError as exc:
                        self.last_live_error = str(exc)
                        self._gen_local_locked()
                else:
                    self._gen_local_locked()
                self._sign = build_xm_sign(self._session_id, self._browser_id)
                self._save_locked()
            return self._sign

    def invalidate(self) -> None:
        """丢弃当前签名，下次取用时重新取对（线上会强制新一轮上报）。"""
        with _lock:
            self._sign = ""
            self._session_id = ""
            self._browser_id = ""
            self._created_at = 0.0
            self._source = ""
            if self.path and os.path.exists(self.path):
                try:
                    os.remove(self.path)
                except OSError:
                    pass

    def device_uuid(self) -> str:
        """取（并持久化）仿冒设备 UUID，跨进程复用。

        与签名同一文件落盘：同一台"虚拟设备"应保持同一 UUID，避免每次重启
        都换新机器而扩大风控暴露面。
        """
        with _lock:
            if not self._device_uuid:
                self._load_from_disk_locked()
            if not self._device_uuid:
                self._device_uuid = str(uuid.uuid4())
                self._save_locked()
            return self._device_uuid

    def status(self) -> Dict[str, object]:
        """诊断信息（不泄露签名本体）。"""
        with _lock:
            fresh = self._fresh_locked()
            age = (import_clock() - self._created_at) if self._created_at else 0.0
            return {
                "ready": fresh,
                "source": self._source or self._default_source(),
                "live": self.live,
                "session_id_length": len(self._session_id),
                "session_suffix": self._session_id[-2:] if self._session_id else "",
                "browser_id_length": len(self._browser_id),
                "device_uuid": self._device_uuid,
                "age_seconds": round(age, 1),
                "ttl_seconds": self.ttl,
                "path": self.path,
                "live_error": self.last_live_error,
            }
