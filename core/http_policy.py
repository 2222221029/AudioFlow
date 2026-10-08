# -*- coding: utf-8 -*-
"""HTTP 响应的统一判据 —— 「先解读响应体，再看体积」。

## 为什么需要这个模块

移植自参考实现 XimalayaApp 的 `Core/Net.cs:240-260`。那里的注释记录了一个
非常具体的坑：

> 付费集的拒绝响应**恰好是 47 字节的 JSON** `{"msg":"立即购买畅听","ret":726}`。
> 旧实现先判 `got <= 1024`，于是把它报成「响应过小（47 字节）」——
> 用户完全看不出真正原因是「这一集没权限」。

**顺序不能反。** 这个模块把「先读体、再判量」固化成唯一实现。

## 本项目改造前的状态

同一个仓库里存在**三套不同强度**的校验：

| 位置 | 做法 | 问题 |
| --- | --- | --- |
| `ximalaya_download_manager.py:1677` `_validate_mobile_media` | 先查 content-type 与 `{`/`<` 开头 | **做对了** |
| `ximalaya_download_manager.py:820` 网页版路径 | `total_size <= 1024` 先判 | 会把 47 字节的错误 JSON 报成「文件过小」 |
| `ximalaya_download_manager.py:2186` | 精确比对 content-length | 强度尚可 |

本模块把第一种（正确的那种）抽成公共件，供所有落盘路径复用。

## 与本项目既有代码的关系

本模块**不替换**任何既有函数，只提供「可被调用」的判据。既有调用点按需接入，
未接入的调用点行为**完全不变** —— 这是「不影响现有功能」的前提。
"""

from __future__ import annotations

import re
from typing import Any

from core.errors import (
    ContentInvalidError,
    PermissionDenied,
    TransientError,
)

#: 小于这个字节数一律进入「读体判定」流程（不是直接判失败）。
SNIFF_MAX_BYTES = 2048

#: 音频文件的最小合理体积。低于它不可能是有效音频。
MIN_VALID_AUDIO_BYTES = 1024

_TEXT_CONTENT_TYPES = ("json", "html", "xml", "text/plain")

#: 响应体首字节是不是错误页（JSON 对象 / HTML 标签）。
_ERROR_BODY_PREFIXES = (b"{", b"[", b"<")

_RET_RE = re.compile(rb'"ret"\s*:\s*(-?\d+)')
_MSG_RE = re.compile(rb'"(?:msg|message)"\s*:\s*"([^"]{0,120})"')


def _peek(path: str, size: int = SNIFF_MAX_BYTES) -> bytes:
    """读文件开头若干字节；任何异常都返回空字节串（判定失败不升级为下载失败）。"""
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError:
        return b""


def _decode(head: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return head.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _strip_bytes_unit(size: Any) -> int:
    """兼容 0.1.x 期间出现过的 '1234 字节' 之类的字符串体积。"""
    if size is None:
        return 0
    if isinstance(size, (int, float)):
        return int(size)
    match = re.search(r"\d+", str(size))
    return int(match.group()) if match else 0


def looks_like_error_body(path: str, content_type: str = "", check_size: bool = True) -> tuple[bool, str]:
    """判断已落盘的文件是不是「错误响应体」。

    返回 `(is_error, human_message)`。**先解读内容、再看体积**，顺序不可交换。

    ⚠ 只在体积小或 content-type 是文本时才读内容 —— 正常音频体积大且
    content-type 是 `audio/*`，不会进入这里，因此**热路径零额外开销**。

    :param check_size: 是否把「体积过小」也判成错误。默认 True。
        调用方若已经设了自己的体积阈值（各平台文案不同），应传 False ——
        让「体积过小」这句**由调用方来说**，避免两套文案打架。
        **内容级**判定（JSON 错误码 / HTML 错误页）不受此参数影响，永远生效。
    """
    try:
        size = _strip_bytes_unit(__import__("os").path.getsize(path))
    except OSError:
        return False, ""

    ctype = str(content_type or "").lower()
    is_text = any(token in ctype for token in _TEXT_CONTENT_TYPES)
    if size > SNIFF_MAX_BYTES and not is_text:
        return False, ""

    head = _peek(path)
    if not head:
        # ⚠ 空文件、以及「path 其实是目录」这两种情况要分开：
        #   目录读不出 head，但它不是「下载了一半的空壳」，不能报成不完整；
        #   真·0 字节文件才是必须拦下的无效产物。
        try:
            size_now = __import__("os").path.getsize(path)
        except OSError:
            return False, ""
        if size_now == 0 and check_size:
            return True, "下载的内容为空（0 字节），可能没有下载权限"
        return False, ""

    # ① 先解读响应体：JSON 错误码 / 明文提示
    ret_match = _RET_RE.search(head)
    ret_value = ret_match.group(1).decode("ascii", errors="ignore") if ret_match else ""
    msg_match = _MSG_RE.search(head)
    server_msg = msg_match.group(1).decode("utf-8", errors="ignore") if msg_match else ""

    if ret_value == "726":
        return True, "需要购买或会员权益（ret=726 立即购买畅听）"
    if ret_value in ("50", "1001"):
        return True, (
            "登录已失效，请重新登录后再试" if ret_value == "50"
            else "平台返回系统繁忙（ret=1001），请稍后重试"
        )

    stripped = head.lstrip().lower()
    if is_text or stripped.startswith(_ERROR_BODY_PREFIXES):
        # ② 再判体积/内容：确实是错误页而不是音频
        detail = server_msg or _decode(head[:120]).strip() or "空响应"
        return True, f"下载到的内容不是音频（{ctype or '未知类型'}）：{detail}"

    if check_size and size <= MIN_VALID_AUDIO_BYTES:
        return True, f"下载的内容不完整（仅 {size} 字节），可能没有下载权限"

    return False, ""


def raise_for_error_body(path: str, content_type: str = "", check_size: bool = True) -> None:
    """落盘后立刻校验；是错误响应体就抛**分级异常**（供调用方按类处置）。

    * `ret=726` / `ret=50` → `PermissionDenied`（重试白搭，换候选或直接失败）
    * `ret=1001`           → `TransientError`（系统繁忙，可重试）
    * 其它错误页 / 过小    → `ContentInvalidError`（内容无效，换候选）

    :param check_size: 见 `looks_like_error_body`。
    """
    is_error, message = looks_like_error_body(path, content_type, check_size=check_size)
    if not is_error:
        return
    if "ret=726" in message or "需要购买" in message or "重新登录" in message:
        raise PermissionDenied(message)
    if "ret=1001" in message or "系统繁忙" in message:
        raise TransientError(message)
    raise ContentInvalidError(message)


def is_probably_audio(head: bytes) -> bool:
    """从文件头判断是不是已知音频容器/编码。

    覆盖参考实现 `Net.cs:500-533` 的判据集合，本项目 `_detect_mobile_media_format`
    是它的超集（多了 caf / aac），这里只做「是不是音频」的布尔判定，
    **不做后缀推导** —— 后缀推导继续由各 manager 既有的方法负责。
    """
    if len(head) < 4:
        return False
    if head[:4] == b"fLaC":
        return True
    if head[:4] == b"RIFF":
        return True
    if head[:4] == b"OggS":
        return True
    if head[:4] == b"caff":
        return True
    if head[:3] == b"ID3":
        return True
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return True
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:   # MP3 / AAC 裸帧头
        return True
    return False


def sniff_extension(path: str, declared: str = "") -> str:
    """读文件头校正扩展名；识别不出就返回 `declared`（不改名）。

    ⚠ 与参考实现的一个**刻意差异**：参考实现 `Net.cs:513-518` 对 `ftyp` 容器会
    进一步解析 moov/hdlr 来判断有没有视频轨（纯音频 `.m4a`、带视频 `.mp4`）。
    本项目是有声书下载器，`core/ximalaya_download_manager.py:1662` 现有的
    `_detect_mobile_media_format` 对 `ftyp` **一律返回 `.m4a`** —— 这对有声书
    场景更安全，且已被线上 2000+ 集验证过。

    因此本函数**只补 brand 白名单**（M4A/M4B/M4P 明确是纯音频），
    其余 `ftyp` 仍沿用 `.m4a`，不引入行为变化。
    """
    head = _peek(path, 32)
    if len(head) < 4:
        return declared
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"RIFF":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"caff":
        return ".caf"
    if head[:3] == b"ID3":
        return ".mp3"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = bytes(head[8:12]).decode("ascii", errors="ignore")
        if brand[:3] in ("M4A", "M4B", "M4P"):
            return ".m4a"
        return ".m4a"
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return ".mp3"
    return declared


__all__ = [
    "SNIFF_MAX_BYTES",
    "MIN_VALID_AUDIO_BYTES",
    "looks_like_error_body",
    "raise_for_error_body",
    "is_probably_audio",
    "sniff_extension",
]
