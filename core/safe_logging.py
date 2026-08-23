#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import builtins
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime


SENSITIVE_PATTERNS = [
    re.compile(r"((?:x[-_]?tk|access[_-]?token|csrf[_-]?token|token|cookie|secret[_-]?key|signature|sign)\s*[:=]\s*)([^,;&\s'\"]+)", re.I),
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
_PRINT_MIRROR = None
_LOG_CONTEXT = ContextVar("audioflow_log_context", default={})

_LEVEL_PRIORITY = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40,
}
_LEVEL_ALIASES = {
    "WARNING": "WARN",
    "CRITICAL": "ERROR",
    "FATAL": "ERROR",
}
_TEXT_LEVEL_RE = re.compile(
    r"^\s*(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL)\s*[:：|-]\s*",
    re.I,
)
_STRUCTURED_LOG_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(?:DEBUG|INFO|WARN|ERROR)\s+\["
)
_LEADING_DECORATION_RE = re.compile(
    r"^\s*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?\s*)+"
)
_PLATFORM_HINTS = (
    ("喜马拉雅", ("喜马拉雅", "ximalaya", "xmly", "移动端 v4")),
    ("懒人听书", ("懒人听书", "[lrts", "lrts-")),
    ("番茄畅听", ("番茄畅听", "fanqie")),
    ("番茄听书", ("番茄听书", "fanqie_tingshu")),
    ("七猫听书", ("七猫听书", "qimao")),
    ("蜻蜓FM", ("蜻蜓fm", "蜻蜓FM", "qtfm", "qingting")),
    ("云听FM", ("云听fm", "云听FM", "yuntu", "radio.cn")),
    ("起点听书", ("起点听书", "qidian")),
    ("酷我听书", ("酷我听书", "kuwo")),
    ("网易云听书", ("网易云听书", "netease")),
    ("荔枝FM", ("荔枝fm", "荔枝FM", "lizhi")),
)
_OPERATION_HINTS = (
    ("初始化", ("初始化",)),
    ("搜索", ("搜索", "search")),
    ("专辑详情", ("专辑详情", "书籍详情", "详情获取", "详情加载")),
    ("下载", ("下载", "download", "重试", "限速", "风控冷却")),
    ("章节目录", ("章节", "目录", "chapter")),
    ("音频地址", ("音频url", "音频地址", "audio url", "播放地址", "[lrts-audio]")),
    ("订阅", ("订阅", "subscription")),
    ("凭证", ("凭证", "ticket", "x-tk", "v4")),
    ("登录", ("登录", "cookie", "扫码")),
)


def configured_log_level():
    value = str(os.getenv("AUDIOFLOW_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    value = _LEVEL_ALIASES.get(value, value)
    return value if value in _LEVEL_PRIORITY else "INFO"


def platform_verbose_enabled():
    return str(os.getenv("AUDIOFLOW_PLATFORM_VERBOSE", "0") or "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def current_log_context():
    return dict(_LOG_CONTEXT.get() or {})


@contextmanager
def log_context(platform=None, operation=None, **fields):
    """Attach platform/operation metadata to nested legacy print calls."""
    merged = current_log_context()
    if platform not in (None, ""):
        merged["platform"] = str(platform)
    if operation not in (None, ""):
        merged["operation"] = str(operation)
    for key, value in fields.items():
        if value not in (None, ""):
            merged[str(key)] = value
    token = _LOG_CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _LOG_CONTEXT.reset(token)


def _normalize_level(level):
    value = str(level or "INFO").strip().upper()
    return _LEVEL_ALIASES.get(value, value) if value else "INFO"


def _message_level(message):
    text = str(message or "")
    match = _TEXT_LEVEL_RE.match(text)
    if match:
        return _normalize_level(match.group(1)), text[match.end():].strip()

    stripped = text.lstrip()
    if stripped.startswith("❌"):
        level = "ERROR"
    elif stripped.startswith(("⚠", "⛔", "🧊")):
        level = "WARN"
    else:
        level = "INFO"
    return level, _LEADING_DECORATION_RE.sub("", text, count=1).strip()


def _field_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple, set)):
        if isinstance(value, set):
            value = sorted(value, key=str)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    text = str(value)
    if not text or re.search(r"[\s|=]", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _infer_context(message, context):
    values = dict(context or {})
    text = str(message or "")
    folded = text.casefold()
    if not values.get("platform"):
        for platform, markers in _PLATFORM_HINTS:
            if any(marker.casefold() in folded for marker in markers):
                values["platform"] = platform
                break
    if not values.get("operation"):
        for operation, markers in _OPERATION_HINTS:
            if any(marker.casefold() in folded for marker in markers):
                values["operation"] = operation
                break
    return values


class ContextRedactingFilter(RedactingFilter):
    """Add the same readable scope to standard-library logging records."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", ""))
        values = _infer_context(message, current_log_context())
        platform = str(values.pop("platform", "系统") or "系统")
        operation = str(values.pop("operation", "") or "")
        record.log_scope = f"[{platform}]" + (f"[{operation}]" if operation else "")
        fields = " ".join(
            f"{key}={_field_value(value)}"
            for key, value in values.items()
            if value not in (None, "")
        )
        record.log_fields = f" | {redact(fields)}" if fields else ""
        if record.levelname == "WARNING":
            record.levelname = "WARN"
        elif record.levelname in ("CRITICAL", "FATAL"):
            record.levelname = "ERROR"
        return super().filter(record)


def format_log_line(message, level=None, context=None, timestamp=None):
    """Format one console line. Return None when filtered by log level."""
    text = redact(message).strip()
    if not text:
        return ""
    if _STRUCTURED_LOG_RE.match(text):
        return text

    inferred_level, clean_message = _message_level(text)
    level_name = _normalize_level(level or inferred_level)
    if level_name not in _LEVEL_PRIORITY:
        level_name = "INFO"
    if _LEVEL_PRIORITY[level_name] < _LEVEL_PRIORITY[configured_log_level()]:
        return None

    values = current_log_context() if context is None else dict(context or {})
    values = _infer_context(clean_message or text, values)
    platform = str(values.pop("platform", "系统") or "系统")
    operation = str(values.pop("operation", "") or "")
    scope = f"[{platform}]" + (f"[{operation}]" if operation else "")
    when = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = f"{when} {level_name} {scope} {clean_message or text}"
    fields = redact(" ".join(
        f"{key}={_field_value(value)}"
        for key, value in values.items()
        if value not in (None, "")
    ))
    return f"{result} | {fields}" if fields else result


def log_event(level, message, **fields):
    """Emit a structured event while remaining usable before print is installed."""
    with log_context(**fields):
        value = f"{_normalize_level(level)}: {message}"
        if _PRINT_INSTALLED:
            builtins.print(value)
        else:
            output = format_log_line(value)
            if output is not None:
                _ORIGINAL_PRINT(output)


def install_safe_print(mirror=None):
    global _PRINT_INSTALLED, _PRINT_MIRROR
    if mirror is not None:
        _PRINT_MIRROR = mirror
    if _PRINT_INSTALLED:
        return

    def safe_print(*args, **kwargs):
        masked = [redact(arg) for arg in args]
        target = kwargs.get("file")
        if target is not None and target not in (sys.stdout, sys.stderr):
            _ORIGINAL_PRINT(*masked, **kwargs)
            return
        if not args:
            _ORIGINAL_PRINT(**kwargs)
            return

        separator = kwargs.get("sep", " ")
        if separator is None:
            separator = " "
        message = separator.join(masked)
        formatted = []
        for line in message.splitlines() or [message]:
            output = format_log_line(line)
            if output is not None:
                formatted.append(output)
        if not formatted:
            return

        output_kwargs = dict(kwargs)
        output_kwargs.pop("sep", None)
        rendered = "\n".join(formatted)
        _ORIGINAL_PRINT(rendered, **output_kwargs)
        if callable(_PRINT_MIRROR):
            for line in formatted:
                try:
                    _PRINT_MIRROR(line)
                except Exception:
                    pass

    builtins.print = safe_print
    _PRINT_INSTALLED = True
