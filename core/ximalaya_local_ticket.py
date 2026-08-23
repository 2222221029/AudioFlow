"""Pure-Python Ximalaya mobile Ticket generation for an existing App session.

This module does not create or bypass a login session.  It only reproduces the
official client's XUID/Ticket transformation from a previously authenticated
mobile Cookie.  A previously captured ticket is optional and is used only to
preserve the exact SDK/App version suffix when available.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
import time
import uuid
from typing import Mapping

from .ximalaya_credentials import (
    normalize_ximalaya_mobile_credentials,
    ximalaya_mobile_cookie_identity,
    ximalaya_mobile_ticket_metadata,
)

_XUID_KEY = bytes.fromhex(
    "43948091bb379303193d958bd26fe98c9df3a2470e8cc9b10fc671c478404b1a"
)
_TICKET_KEY = bytes.fromhex(
    "b0b1a8ac34e66efa95c7f4157cf8b6ba33dfc2075e41fb964f2e12dcaa"
)
_XUID_SIGN_INDEXES = (3, 6, 9, 12, 19, 22, 25, 28)
_UA_VERSION_RE = re.compile(r"ting[_/](\d+(?:\.\d+){2,3})", re.I)
_TICKET_SUFFIX_RE = re.compile(
    r"com\.ximalaya\.ting\.android!([^!]+)!([^!]+)!b=[^&!]+&s=[^&!]+&u=\d+"
)
_TICKET_CACHE = {}
_TICKET_CACHE_LOCK = threading.Lock()
_TICKET_CACHE_MAX_ENTRIES = 32


class LocalTicketError(ValueError):
    pass


def _ticket_cache_ttl() -> float:
    try:
        value = float(os.getenv("XIMALAYA_LOCAL_TICKET_TTL_SECONDS", "900") or "900")
    except (TypeError, ValueError):
        value = 900.0
    return max(60.0, min(value, 3600.0))


def clear_mobile_ticket_cache() -> None:
    """Clear locally generated session tickets, primarily for account changes/tests."""
    with _TICKET_CACHE_LOCK:
        _TICKET_CACHE.clear()


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
    if ua_match:
        return "1.3.27", ua_match.group(1)
    cookie_version = ximalaya_mobile_cookie_identity(credentials).get("app_version", "")
    return "1.3.27", cookie_version or "9.4.52.3"


def generate_mobile_ticket(value, business: str = "playTrack", scene: str = "play") -> str:
    credentials = normalize_ximalaya_mobile_credentials(value)
    identity = ximalaya_mobile_cookie_identity(credentials)
    metadata = ximalaya_mobile_ticket_metadata(credentials.get("x_tk", ""))
    cookie_uid = str(identity.get("uid") or "").strip()
    ticket_uid = str(metadata.get("uid") or "").strip()
    if ticket_uid and ticket_uid != "0" and cookie_uid and ticket_uid != cookie_uid:
        raise LocalTicketError("现有移动端 Ticket 与 App Cookie 属于不同账号")
    uid = cookie_uid or ticket_uid
    if not uid.isdigit() or uid == "0":
        raise LocalTicketError("移动端 Cookie 不包含已登录账号 UID")

    if identity.get("platform") != "android" or not identity.get("device_id"):
        raise LocalTicketError("移动端 Cookie 缺少稳定 Android 设备 ID")
    stable_hex = identity["device_id"]
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
    cookie_digest = hashlib.sha256(
        credentials.get("cookie", "").encode("utf-8")
    ).hexdigest()
    cache_key = (
        uid,
        stable_hex,
        cookie_digest,
        sdk_version,
        app_version,
        str(business),
        str(scene),
    )
    now = time.monotonic()
    ttl = _ticket_cache_ttl()

    # x-tk is a short-lived session credential, not a per-track signature. The
    # official client reuses it; minting a new random ticket for every chapter
    # causes hundreds of credential validations and triggers the V4 risk window.
    with _TICKET_CACHE_LOCK:
        cached = _TICKET_CACHE.get(cache_key)
        if cached and now - cached[0] < ttl:
            return cached[1]

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
        ticket = prefix + _b64u(
            timestamp + stable_id + random_part + ticket_signature + suffix
        )

        if len(_TICKET_CACHE) >= _TICKET_CACHE_MAX_ENTRIES:
            oldest_key = min(_TICKET_CACHE, key=lambda key: _TICKET_CACHE[key][0])
            _TICKET_CACHE.pop(oldest_key, None)
        _TICKET_CACHE[cache_key] = (now, ticket)
        return ticket
