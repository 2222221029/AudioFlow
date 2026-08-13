"""Helpers for storing Ximalaya web cookies alongside the Android play ticket."""


MOBILE_TICKET_KEY = "xmly_x_tk"
MOBILE_TICKET_ALIASES = {
    MOBILE_TICKET_KEY,
    "x-tk",
    "x_tk",
    "xmly-mobile-ticket",
    "xmly_mobile_ticket",
}


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

    # Stream and similar capture tools commonly copy headers as ``x-tk: ...``.
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

    # A standalone value is accepted so the user never has to add a field name.
    if ";" not in text and "\n" not in text and "\r" not in text:
        return text
    return ""


def has_ximalaya_mobile_ticket(value):
    """Return whether a non-empty Android x-tk is present without exposing it."""
    return any(_is_mobile_ticket(key) and bool(val) for key, val in _segments(value))


def save_ximalaya_mobile_ticket(existing, incoming):
    """Save only the mobile ticket while preserving all browser cookies."""
    ticket = extract_ximalaya_mobile_ticket(incoming)
    if not ticket:
        return ""
    web = [(key, val) for key, val in _segments(existing) if not _is_mobile_ticket(key)]
    web.append((MOBILE_TICKET_KEY, ticket))
    return "; ".join(f"{key}={val}" for key, val in web)


def remove_ximalaya_mobile_ticket(existing):
    """Remove only the mobile ticket while preserving browser cookies."""
    web = [(key, val) for key, val in _segments(existing) if not _is_mobile_ticket(key)]
    return "; ".join(f"{key}={val}" for key, val in web)


def has_ximalaya_web_cookie(value):
    """Return whether browser-cookie fields exist alongside any mobile ticket."""
    return any(not _is_mobile_ticket(key) and bool(val) for key, val in _segments(value))


def merge_ximalaya_credentials(existing, incoming):
    """Merge a one-time x-tk with refreshable web cookies.

    Ximalaya's web QR flow returns browser cookies only. If a mobile ticket was
    previously saved, refreshing the browser session must not silently erase it.
    Supplying only ``xmly_x_tk`` also updates that ticket without discarding the
    current browser cookies.
    """
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
