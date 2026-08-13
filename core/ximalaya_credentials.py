"""Helpers for storing Ximalaya web cookies alongside the Android play ticket."""


MOBILE_TICKET_KEY = "xmly_x_tk"


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
    return str(key or "").strip().lower() == MOBILE_TICKET_KEY


def has_ximalaya_mobile_ticket(value):
    """Return whether a non-empty Android x-tk is present without exposing it."""
    return any(_is_mobile_ticket(key) and bool(val) for key, val in _segments(value))


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
