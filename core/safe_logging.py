#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import builtins
import re


SENSITIVE_PATTERNS = [
    re.compile(r"((?:access[_-]?token|csrf[_-]?token|token|cookie|secret[_-]?key|signature|sign)\s*[:=]\s*)([^,;&\s'\"]+)", re.I),
    re.compile(r"((?:https?://)[^\s'\"]{24,})", re.I),
]


def redact(value):
    text = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + _mask(m.group(2)), text)
        else:
            text = pattern.sub(lambda m: _mask_url(m.group(1)), text)
    return text


def _mask(value):
    value = str(value or "")
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def _mask_url(value):
    value = str(value or "")
    if len(value) <= 40:
        return value
    return value[:36] + "...[redacted]"


class RedactingFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(arg) for arg in record.args)
        return True


_ORIGINAL_PRINT = builtins.print
_PRINT_INSTALLED = False


def install_safe_print():
    global _PRINT_INSTALLED
    if _PRINT_INSTALLED:
        return

    def safe_print(*args, **kwargs):
        masked = [redact(arg) for arg in args]
        _ORIGINAL_PRINT(*masked, **kwargs)

    builtins.print = safe_print
    _PRINT_INSTALLED = True
