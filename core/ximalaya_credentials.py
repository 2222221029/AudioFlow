"""Parse and store Ximalaya browser and mobile-App credentials safely."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict


MOBILE_CREDENTIAL_PLATFORM = "xmly_mobile"
MOBILE_TICKET_KEY = "xmly_x_tk"
MOBILE_TICKET_ALIASES = {
    MOBILE_TICKET_KEY,
    "x-tk",
    "x_tk",
    "xmly-mobile-ticket",
    "xmly_mobile_ticket",
}
MOBILE_V4_ANONYMOUS_TICKET = (
    "TACZSIZWgP4Xg5JsY96rjV_fF2Kb0TPw8YZgQRnpD_FmqF5ctPSIFVI5S6TcR7on"
    "XT6TaMZFf_CgXXD7jILUvHcBWkiwb1jbG9zZSEwITAhYj10aWNrZXQmcz1jbG9zZSZ1aWQ9MA"
)


def _segments(value):
    if isinstance(value, dict):
        items = value.items()
        return [(str(key).strip(), str(val).strip()) for key, val in items if key and val not in (None, "")]

    result = []
    for part in str(value or "").replace("\r", "").replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key and val:
            result.append((key, val))
    return result


def _is_mobile_ticket(key):
    return str(key or "").strip().lower() in MOBILE_TICKET_ALIASES


def extract_ximalaya_mobile_ticket(value):
    """Extract an x-tk from a raw value, header line, cookie pair, or mapping."""
    if isinstance(value, dict):
        tickets = [val for key, val in _segments(value) if _is_mobile_ticket(key)]
        return tickets[-1] if tickets else ""

    text = str(value or "").strip()
    if not text:
        return ""

    matched_alias = False
    for part in text.replace("\r", "").replace("\n", ";").split(";"):
        candidate = part.strip()
        for separator in ("=", ":"):
            if separator not in candidate:
                continue
            key, val = candidate.split(separator, 1)
            if _is_mobile_ticket(key):
                matched_alias = True
                if val.strip():
                    return val.strip()

    if matched_alias:
        return ""

    # A standalone ticket remains supported for legacy import/migration.
    if (
        ";" not in text and "\n" not in text and "\r" not in text
        and " " not in text and "=" not in text and ":" not in text
    ):
        return text
    return ""


def is_anonymous_ximalaya_mobile_ticket(value) -> bool:
    ticket = extract_ximalaya_mobile_ticket(value)
    return bool(
        ticket
        and (
            ticket == MOBILE_V4_ANONYMOUS_TICKET
            or ximalaya_mobile_ticket_uid(ticket) == "0"
        )
    )


def ximalaya_mobile_ticket_uid(value) -> str:
    """Read the non-secret uid marker carried by a baseInfo x-tk, if present.

    Ximalaya prefixes its URL-safe Base64 ticket with three transport bytes.
    Trying each Base64 alignment also keeps this compatible with tickets whose
    prefix changes, without logging or exposing the ticket itself.
    """
    ticket = extract_ximalaya_mobile_ticket(value)
    if not ticket and isinstance(value, str):
        standalone = value.strip()
        if ";" not in standalone and ":" not in standalone and " " not in standalone:
            ticket = standalone
    if not ticket:
        return ""

    compact = ticket.rstrip("=")
    for offset in range(min(8, len(compact))):
        candidate = compact[offset:]
        if not candidate or not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
            continue
        try:
            decoded = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))
        except (TypeError, ValueError):
            continue
        matches = re.findall(rb"(?:^|[&!?])uid=(\d+)(?:$|[&!?])", decoded)
        if matches:
            return matches[-1].decode("ascii")
    return ""


def _looks_like_mobile_v4_sign(value) -> bool:
    """Detect the common mistake of pasting the 32-byte AES sign as x-tk."""
    ticket = extract_ximalaya_mobile_ticket(value)
    if not ticket and isinstance(value, str):
        standalone = value.strip()
        if ";" not in standalone and ":" not in standalone and " " not in standalone:
            ticket = standalone
    if not re.fullmatch(r"[A-Za-z0-9_-]{42,44}={0,2}", ticket or ""):
        return False
    try:
        decoded = base64.urlsafe_b64decode(ticket + "=" * (-len(ticket) % 4))
    except (TypeError, ValueError):
        return False
    return len(decoded) == 32


def has_ximalaya_mobile_ticket(value):
    """Return whether a non-empty, non-anonymous x-tk is present."""
    ticket = extract_ximalaya_mobile_ticket(value)
    return bool(ticket and not is_anonymous_ximalaya_mobile_ticket(ticket))


def save_ximalaya_mobile_ticket(existing, incoming):
    """Legacy helper: save an x-tk alongside browser cookies."""
    ticket = extract_ximalaya_mobile_ticket(incoming)
    if not ticket:
        return ""
    web = [(key, val) for key, val in _segments(existing) if not _is_mobile_ticket(key)]
    web.append((MOBILE_TICKET_KEY, ticket))
    return "; ".join(f"{key}={val}" for key, val in web)


def remove_ximalaya_mobile_ticket(existing):
    """Remove legacy x-tk aliases while preserving browser cookies."""
    web = [(key, val) for key, val in _segments(existing) if not _is_mobile_ticket(key)]
    return "; ".join(f"{key}={val}" for key, val in web)


def has_ximalaya_web_cookie(value):
    """Return whether browser-cookie fields exist alongside any legacy x-tk."""
    return any(not _is_mobile_ticket(key) and bool(val) for key, val in _segments(value))


def merge_ximalaya_credentials(existing, incoming):
    """Legacy compatibility helper for configurations created before v0.22."""
    incoming_segments = _segments(incoming)
    if not incoming_segments:
        ticket = extract_ximalaya_mobile_ticket(incoming)
        if ticket:
            return save_ximalaya_mobile_ticket(existing, ticket)
        return str(incoming or "").strip()

    incoming_tickets = [(key, val) for key, val in incoming_segments if _is_mobile_ticket(key)]
    incoming_web = [(key, val) for key, val in incoming_segments if not _is_mobile_ticket(key)]
    existing_segments = _segments(existing)
    existing_tickets = [(key, val) for key, val in existing_segments if _is_mobile_ticket(key)]

    if incoming_web:
        merged = list(incoming_web)
        if incoming_tickets:
            merged.append((MOBILE_TICKET_KEY, incoming_tickets[-1][1]))
        elif existing_tickets:
            merged.append((MOBILE_TICKET_KEY, existing_tickets[-1][1]))
    else:
        merged = [(key, val) for key, val in existing_segments if not _is_mobile_ticket(key)]
        merged.append((MOBILE_TICKET_KEY, incoming_tickets[-1][1]))

    return "; ".join(f"{key}={val}" for key, val in merged)


def _mobile_cookie_string(value: Any) -> str:
    """Normalize a captured Cookie header without decoding or logging values."""
    parts = []
    for key, val in _segments(value):
        if not _is_mobile_ticket(key):
            parts.append(f"{key}={val}")
    return "; ".join(parts)


def _header_mapping(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        source = value.get("headers") if isinstance(value.get("headers"), dict) else value
        return {
            str(key).strip().lower(): str(val).strip()
            for key, val in source.items()
            if key and val not in (None, "")
        }

    text = str(value or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            return _header_mapping(parsed)

    headers = {}
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        if re.fullmatch(r"[a-z0-9_*&-]+", key) and val.strip():
            headers[key] = val.strip()
    return headers


def _detect_mobile_device(user_agent: str, explicit: str = "") -> str:
    value = str(explicit or "").strip().lower()
    if value in {"ios", "iphone"}:
        return "ios"
    if value == "android":
        return "android"
    agent = str(user_agent or "").lower()
    if "iphone" in agent or "ipad" in agent or "cfnetwork" in agent or agent.startswith("ting_v"):
        return "ios"
    return "android"


def normalize_ximalaya_mobile_credentials(value: Any) -> Dict[str, str]:
    """Parse Stream/Charles copied headers into the small allow-listed bundle we need."""
    original = value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            value = parsed

    headers = _header_mapping(value)
    mapping = value if isinstance(value, dict) else {}
    lowered = {str(key).strip().lower(): val for key, val in mapping.items()} if mapping else {}

    ticket = (
        headers.get("x-tk")
        or headers.get("x_tk")
        or headers.get(MOBILE_TICKET_KEY)
        or str(lowered.get("x_tk") or lowered.get(MOBILE_TICKET_KEY) or "").strip()
        or extract_ximalaya_mobile_ticket(original)
    )
    cookie = (
        headers.get("cookie")
        or str(lowered.get("cookie") or lowered.get("mobile_cookie") or "").strip()
    )
    user_agent = (
        headers.get("user-agent")
        or str(lowered.get("user_agent") or lowered.get("user-agent") or "").strip()
    )
    accept_language = (
        headers.get("accept-language")
        or str(lowered.get("accept_language") or lowered.get("accept-language") or "").strip()
    )
    device = _detect_mobile_device(user_agent, str(lowered.get("device") or ""))

    # If only a raw Cookie string was pasted, recognize it as such. An x-tk
    # alias inside that string is extracted separately and never forwarded.
    if not cookie and isinstance(original, str) and "=" in original and "\n" not in original:
        raw_parts = _segments(original)
        if any("token" in key.lower() or "_device" in key.lower() for key, _ in raw_parts):
            cookie = original

    result = {
        "x_tk": str(ticket or "").strip(),
        "cookie": _mobile_cookie_string(cookie),
        "user_agent": str(user_agent or "").strip(),
        "accept_language": str(accept_language or "").strip(),
    }
    result = {key: val for key, val in result.items() if val}
    if result:
        result["device"] = device
    return result


def _mobile_cookie_has_login(cookie: str) -> bool:
    return bool(_mobile_cookie_uid(cookie))


def _mobile_cookie_uid(cookie: str) -> str:
    for key, val in _segments(cookie):
        name = key.strip().lower()
        if name.endswith("&*token") or name.endswith("&_token") or name in {"*token", "access_token"}:
            uid = str(val).split("&", 1)[0].strip()
            if val and uid not in {"", "0"}:
                return uid
    return ""


def ximalaya_mobile_credential_status(value: Any) -> Dict[str, Any]:
    """Return non-secret structural status for UI and download gating."""
    credential = normalize_ximalaya_mobile_credentials(value)
    ticket = credential.get("x_tk", "")
    cookie = credential.get("cookie", "")
    user_agent = credential.get("user_agent", "")
    anonymous = is_anonymous_ximalaya_mobile_ticket(ticket)
    ticket_uid = ximalaya_mobile_ticket_uid(ticket)
    cookie_uid = _mobile_cookie_uid(cookie)
    has_login_cookie = bool(cookie_uid)
    account_match = bool(ticket_uid and cookie_uid and ticket_uid == cookie_uid)

    if not ticket:
        state, message = "missing_ticket", "缺少已登录 App 实际请求头中的 x-tk"
    elif _looks_like_mobile_v4_sign(ticket):
        state, message = "sign_as_ticket", "当前值看起来是 URL 查询参数 sign，不是请求头 x-tk"
    elif anonymous:
        state, message = "anonymous_ticket", "x-tk 是 uid=0 的匿名票据，请重新抓取登录账号的请求"
    elif not cookie:
        state, message = "missing_cookie", "缺少同一次移动端请求中的 Cookie，x-tk 不能单独使用"
    elif not has_login_cookie:
        state, message = "not_logged_in", "移动端 Cookie 中没有检测到已登录账号 token"
    elif ticket_uid and ticket_uid != cookie_uid:
        state, message = "account_mismatch", "x-tk 与移动端 Cookie 属于不同账号，请从同一次 baseInfo 请求重新复制"
    elif not user_agent:
        state, message = "missing_user_agent", "缺少同一次移动端请求中的 User-Agent"
    else:
        state, message = "complete", "移动端请求头格式完整，将在下载时验证账号与音质权限"

    return {
        "state": state,
        "message": message,
        "complete": state == "complete",
        "has_ticket": bool(ticket),
        "has_mobile_cookie": bool(cookie),
        "has_login_cookie": has_login_cookie,
        "has_user_agent": bool(user_agent),
        "device": credential.get("device", "android"),
        "ticket_has_account": bool(ticket_uid and ticket_uid != "0"),
        "account_match": account_match if ticket_uid and cookie_uid else None,
    }


def has_ximalaya_mobile_credentials(value: Any) -> bool:
    return bool(ximalaya_mobile_credential_status(value)["complete"])
