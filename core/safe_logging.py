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
        # 先用原始 msg/args 合并出最终消息（此时 %d 等格式符与原始参数类型匹配，不会出错），
        # 再对最终字符串整体脱敏，并清空 args 避免下游 handler 二次格式化。
        # 这样既不破坏整数格式化（waitress/httpx 的 "%d %s"），也不会因脱敏改动 msg 模板占位符。
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", ""))
        record.msg = redact(message)
        record.args = None
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
