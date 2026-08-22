"""Pure-Python Ximalaya mobile Ticket generation for an existing App session.

This module does not create or bypass a login session.  It only reproduces the
official client's XUID/Ticket transformation from a previously authenticated
mobile Cookie and matching Ticket bundle.  Callers must retain the Bridge as a
fallback for initial login and session renewal.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid
from typing import Mapping

from .ximalaya_credentials import (
    normalize_ximalaya_mobile_credentials,
    ximalaya_mobile_credential_status,
    ximalaya_mobile_ticket_metadata,
)

_XUID_KEY = bytes.fromhex(
    "43948091bb379303193d958bd26fe98c9df3a2470e8cc9b10fc671c478404b1a"
)
_TICKET_KEY = bytes.fromhex(
    "b0b1a8ac34e66efa95c7f4157cf8b6ba33dfc2075e41fb964f2e12dcaa"
)
_XUID_SIGN_INDEXES = (3, 6, 9, 12, 19, 22, 25, 28)
_DEVICE_RE = re.compile(r"(?:^|;\s*)1&_device=android&([0-9a-fA-F-]{32,36})&")
_UA_VERSION_RE = re.compile(r"ting[_/](\d+(?:\.\d+){2,3})", re.I)
_TICKET_SUFFIX_RE = re.compile(
    r"com\.ximalaya\.ting\.android!([^!]+)!([^!]+)!b=[^&!]+&s=[^&!]+&u=\d+"
)


class LocalTicketError(ValueError):
    pass


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decoded_ticket_text(ticket: str) -> str:
    value = str(ticket or "").strip()
    if len(value) <= 3:
        return ""
    try:
        payload = base64.urlsafe_b64decode(value[3:] + "=" * (-len(value[3:]) % 4))
    except (ValueError, TypeError):
        return ""
    return payload.decode("utf-8", "ignore")


def _session_versions(credentials: Mapping[str, str]) -> tuple[str, str]:
    text = _decoded_ticket_text(credentials.get("x_tk", ""))
    match = _TICKET_SUFFIX_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    ua_match = _UA_VERSION_RE.search(credentials.get("user_agent", ""))
    return "1.3.27", ua_match.group(1) if ua_match else "9.4.52.3"


def generate_mobile_ticket(value, business: str = "playTrack", scene: str = "play") -> str:
    credentials = normalize_ximalaya_mobile_credentials(value)
    status = ximalaya_mobile_credential_status(credentials)
    if not status.get("complete"):
        raise LocalTicketError(status.get("message") or "移动端登录会话不可用")

    metadata = ximalaya_mobile_ticket_metadata(credentials.get("x_tk", ""))
    uid = str(metadata.get("uid") or "").strip()
    if not uid.isdigit() or uid == "0":
        raise LocalTicketError("现有移动端 Ticket 不包含已登录 UID")

    device_match = _DEVICE_RE.search(credentials.get("cookie", ""))
    if not device_match:
        raise LocalTicketError("移动端 Cookie 缺少稳定 Android 设备 ID")
    stable_hex = device_match.group(1).replace("-", "")
    try:
        stable_id = bytes.fromhex(stable_hex)
    except ValueError as exc:
        raise LocalTicketError("移动端设备 ID 格式无效") from exc
    if len(stable_id) != 16:
        raise LocalTicketError("移动端设备 ID 必须为 16 字节")

    xuid_prefix = b"XAU"
    digest = hashlib.sha256(xuid_prefix + stable_id + _XUID_KEY).digest()
    signature = bytes(digest[index] for index in _XUID_SIGN_INDEXES)
    xuid = "XAU" + _b64u(stable_id + signature)

    sdk_version, app_version = _session_versions(credentials)
    timestamp = int(time.time()).to_bytes(4, "big")
    random_part = bytes(a ^ b for a, b in zip(uuid.uuid4().bytes, stable_id))
    prefix = f"T{xuid[1]}C"
    attr = f"b={business}&s={scene}&u={uid}"
    suffix = "!".join(
        ("com.ximalaya.ting.android", sdk_version, app_version, attr)
    ).encode("utf-8")
    ticket_signature = hashlib.sha256(
        timestamp + stable_id + random_part + prefix.encode("ascii") + _TICKET_KEY + suffix
    ).digest()
    return prefix + _b64u(timestamp + stable_id + random_part + ticket_signature + suffix)
