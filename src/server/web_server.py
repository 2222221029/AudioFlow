#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import mimetypes
import os
import ipaddress
import re
import shutil
import socket
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, stream_with_context

from core.auth_manager import AuthManager
from core.agent_manager import AgentManager
from core.audiobook_renamer import AUDIO_EXTENSIONS, RenamePlanManager, preview_rule_samples
from core.cookie_manager import CookieManager
from core.download_worker import DownloadWorker
from core.developer_agent_manager import DeveloperAgentManager
from core.enhanced_search_manager import EnhancedSearchManager
from core.feishu_bridge import FeishuBridge
from core.notification_manager import NotificationManager
from core.rename_rules import RenameRuleStore, merge_rule_values
from core.wecom_crypto import WeComCrypto, parse_wecom_message
from core.lrts_manager import (
    LrtsLoginSessionError,
    lrts_send_sms_code,
    lrts_sms_login,
    normalize_lrts_credentials,
    parse_lrts_credentials,
)
from core.safe_logging import (
    ContextRedactingFilter,
    configured_log_level,
    install_safe_print,
    log_context,
    log_event,
)
from core.platform_config import (
    APP_NAME,
    APP_VERSION,
    audio_proxy_raw_url_enabled,
    config_dir,
    data_dir,
    download_dir,
    ensure_runtime_dirs,
    host,
    log_dir,
    port,
    project_root,
    pwa_enabled,
)
from core.subscription_manager import SubscriptionManager, canonical_subscription_platform, chapter_key
from core.ximalaya_credentials import (
    MOBILE_CREDENTIAL_PLATFORM,
    extract_ximalaya_mobile_ticket,
    has_ximalaya_mobile_ticket,
    has_ximalaya_web_cookie,
    merge_ximalaya_credentials,
    normalize_ximalaya_mobile_credentials,
    remove_ximalaya_mobile_ticket,
    ximalaya_mobile_credential_status,
)


FRONTEND_DIST_DIR = project_root() / "frontend" / "dist"
FRONTEND_PUBLIC_DIR = project_root() / "frontend" / "public"

app = Flask(__name__, static_folder=None)


@app.errorhandler(500)
def handle_500(e):
    """捕获所有未处理异常，返回 JSON 而非 Waitress 错误页。"""
    import traceback
    traceback.print_exc()
    return jsonify(ok=False, error=str(e) or "服务器内部错误"), 500

@app.errorhandler(Exception)
def handle_unhandled(e):
    """兜底异常处理。"""
    import traceback
    traceback.print_exc()
    return jsonify(ok=False, error=str(e) or "未处理的异常"), 500
ensure_runtime_dirs()

LOG_FILE = log_dir() / "server.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
_log_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_log_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(log_scope)s %(message)s%(log_fields)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log_handler.setFormatter(_log_formatter)
_log_handler.addFilter(ContextRedactingFilter())
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)
_console_handler.addFilter(ContextRedactingFilter())
logging.basicConfig(
    level=getattr(logging, configured_log_level(), logging.INFO),
    handlers=[_log_handler, _console_handler],
)
# httpx 内部日志用 %d 格式化状态码字符串会触发 logging 错误，屏蔽其 INFO 日志
logging.getLogger("httpx").setLevel(logging.WARNING)


def mirror_console_line_to_server_log(line):
    """Mirror normalized print output into the same rotating file as logging."""
    text = str(line or "").rstrip("\r\n")
    if not text:
        return
    encoded_size = len((text + "\n").encode("utf-8", errors="replace"))
    _log_handler.acquire()
    try:
        if _log_handler.stream is None:
            _log_handler.stream = _log_handler._open()
        _log_handler.stream.seek(0, os.SEEK_END)
        if _log_handler.maxBytes > 0 and _log_handler.stream.tell() + encoded_size >= _log_handler.maxBytes:
            _log_handler.doRollover()
        _log_handler.stream.write(text + _log_handler.terminator)
        _log_handler.flush()
    finally:
        _log_handler.release()


install_safe_print(mirror=mirror_console_line_to_server_log)

cookie_manager = CookieManager()


def _migrate_legacy_ximalaya_mobile_ticket():
    """Move old x-tk aliases out of the browser Cookie without mixing sessions."""
    legacy = cookie_manager.get_cookie("xmly")
    if not isinstance(legacy, str) or not legacy:
        return
    clean_web_cookie = remove_ximalaya_mobile_ticket(legacy)
    ticket = extract_ximalaya_mobile_ticket(legacy)
    if clean_web_cookie != legacy:
        if clean_web_cookie:
            cookie_manager.set_cookie("xmly", clean_web_cookie)
        else:
            cookie_manager.delete_cookie("xmly")
    if ticket and not cookie_manager.get_cookie(MOBILE_CREDENTIAL_PLATFORM):
        # Preserve a legacy user ticket as incomplete migration data. It will
        # never be considered ready until a full App request is pasted.
        credential = normalize_ximalaya_mobile_credentials(ticket)
        if has_ximalaya_mobile_ticket(credential):
            cookie_manager.set_cookie(MOBILE_CREDENTIAL_PLATFORM, credential)


_migrate_legacy_ximalaya_mobile_ticket()
search_manager = EnhancedSearchManager(cookie_manager)
task_lock = threading.Lock()
task_workers = {}
subscription_job_lock = threading.Lock()
subscription_jobs = {}
SUBSCRIPTION_JOB_TTL_SECONDS = int(os.getenv("SUBSCRIPTION_JOB_TTL_SECONDS", "3600") or "3600")
SUBSCRIPTION_JOB_MAX_ITEMS = int(os.getenv("SUBSCRIPTION_JOB_MAX_ITEMS", "500") or "500")
SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS = int(os.getenv("SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS", "900") or "900")
wecom_session_lock = threading.Lock()
wecom_sessions = {}
WECOM_SESSION_TTL_SECONDS = int(os.getenv("WECOM_SESSION_TTL_SECONDS", "600") or "600")
WECOM_SESSION_MAX_ITEMS = int(os.getenv("WECOM_SESSION_MAX_ITEMS", "500") or "500")
XMLY_WEB_SUBSCRIPTION_QUALITY = "喜马拉雅网页版接口"
XMLY_SUBSCRIPTION_QUALITIES = {
    XMLY_WEB_SUBSCRIPTION_QUALITY,
    "喜马拉雅移动端接口（自动最高音质）",
    "杜比全景声优先（自动降级）",
    "无损优先（自动降级）",
}
SUBSCRIPTIONS_FILE = config_dir() / "subscriptions.json"
TASKS_FILE = config_dir() / "tasks.json"
BACKGROUND_EVENTS_FILE = log_dir() / "events.jsonl"
BACKGROUND_EVENTS_MAX_BYTES = max(
    64 * 1024,
    int(os.getenv("AUDIOFLOW_EVENTS_MAX_BYTES", str(2 * 1024 * 1024)) or 2 * 1024 * 1024),
)
BACKGROUND_EVENTS_MAX_KEEP_DEFAULT = max(
    10,
    int(os.getenv("AUDIOFLOW_EVENTS_MAX_KEEP", "10") or "10"),
)
background_events_lock = threading.Lock()
TASK_SAVE_INTERVAL = 1.0
TASK_HISTORY_MAX_KEEP_DEFAULT = max(10, int(os.getenv("AUDIOFLOW_TASK_HISTORY_MAX_KEEP", "100") or "100"))
TASK_HISTORY_MAX_AGE_DAYS_DEFAULT = max(1, int(os.getenv("AUDIOFLOW_TASK_HISTORY_MAX_AGE_DAYS", "30") or "30"))
TASK_DETAIL_RETENTION_DAYS_DEFAULT = max(0, int(os.getenv("AUDIOFLOW_TASK_DETAIL_RETENTION_DAYS", "7") or "7"))
TASK_FAILURE_CHAPTER_LIMIT_DEFAULT = max(1, int(os.getenv("AUDIOFLOW_TASK_FAILURE_CHAPTER_LIMIT", "20") or "20"))
TASK_HISTORY_MAX_BYTES_DEFAULT = max(1024 * 1024, int(os.getenv("AUDIOFLOW_TASK_HISTORY_MAX_BYTES", str(10 * 1024 * 1024)) or 10 * 1024 * 1024))
_last_task_save = 0.0


def task_history_setting(key, default, minimum, maximum):
    try:
        value = int(cookie_manager.get_cookie(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def task_history_settings():
    return {
        "task_history_max_keep": task_history_setting("task_history_max_keep", TASK_HISTORY_MAX_KEEP_DEFAULT, 10, 10000),
        "task_history_max_age_days": task_history_setting("task_history_max_age_days", TASK_HISTORY_MAX_AGE_DAYS_DEFAULT, 1, 3650),
        "task_detail_retention_days": task_history_setting("task_detail_retention_days", TASK_DETAIL_RETENTION_DAYS_DEFAULT, 0, 3650),
        "task_failure_chapter_limit": task_history_setting("task_failure_chapter_limit", TASK_FAILURE_CHAPTER_LIMIT_DEFAULT, 1, 1000),
        "task_history_max_bytes": task_history_setting("task_history_max_bytes", TASK_HISTORY_MAX_BYTES_DEFAULT, 1024 * 1024, 1024 * 1024 * 1024),
    }


def background_events_max_keep():
    return task_history_setting(
        "background_events_max_keep",
        BACKGROUND_EVENTS_MAX_KEEP_DEFAULT,
        10,
        5000,
    )
auth_manager = AuthManager(config_dir())
MAX_JSON_BODY_BYTES = int(os.getenv("MAX_JSON_BODY_BYTES", str(16 * 1024 * 1024)))
AUTH_COOKIE_NAME = "audioflow_session"


def _is_public_endpoint(path):
    path = str(path or "")
    if path in ("/", "/health", "/desktop.html", "/m.html", "/manifest.webmanifest", "/service-worker.js", "/runtime-env.js"):
        return True
    return (
        path.startswith("/api/local-audio/")
        or path == "/api/proxy/audio"
        or path.startswith("/api/auth/")
        or path.startswith("/api/wecom/callback/")
        or path.startswith("/assets/")
        or path.startswith("/static/")
        or path.startswith("/favicon")
        or (not path.startswith("/api/") and "." in Path(path).name)
    )


def _session_token():
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(AUTH_COOKIE_NAME, "")


def current_user():
    return auth_manager.user_for_session(_session_token())


@app.before_request
def guard_api_requests():
    if request.content_length and request.content_length > MAX_JSON_BODY_BYTES:
        return json_error("请求体过大", 413)
    if _is_public_endpoint(request.path):
        return None
    if current_user():
        return None
    return json_error("未登录或会话已过期", 401)


def active_download_dir():
    value = str(cookie_manager.get_download_dir() or download_dir())
    if value.startswith("/vol1/") and os.getenv("APP_MODE", "").lower() == "server":
        return str(download_dir())
    return value


def resolve_download_dir(value=None):
    value = str(value or "").strip()
    if value.startswith("/vol1/") and os.getenv("APP_MODE", "").lower() == "server":
        return str(download_dir())
    return value or active_download_dir()


def int_cookie_setting(key, default, minimum=1, maximum=10000):
    try:
        return max(minimum, min(maximum, int(cookie_manager.get_cookie(key) or default)))
    except (TypeError, ValueError):
        return default


def migrate_runtime_file(source, target):
    try:
        source = Path(source)
        target = Path(target)
        if target.exists() or not source.exists() or source.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        logging.info("migrated runtime file %s -> %s", source, target)
    except Exception:
        logging.exception("runtime file migration failed: %s -> %s", source, target)


def _read_json_file(path, fallback=None):
    try:
        path = Path(path)
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("failed to read json file: %s", path)
        return fallback


def _write_json_file(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def merge_subscription_file(source, target):
    try:
        source = Path(source)
        target = Path(target)
        if not source.exists() or source.resolve() == target.resolve():
            return

        defaults = {
            "version": 1,
            "settings": {
                "enabled": True,
                "auto_download_missing": True,
                "interval_hours": 6,
                "interval_minutes": 0,
                "quality": "M4A 96K",
            },
            "subscriptions": {},
        }
        legacy = _read_json_file(source, {}) or {}
        current = _read_json_file(target, {}) or {}
        if not isinstance(legacy, dict):
            return
        if not isinstance(current, dict):
            current = {}

        merged = {
            "version": current.get("version") or legacy.get("version") or defaults["version"],
            "settings": dict(defaults["settings"]),
            "subscriptions": {},
        }
        merged["settings"].update(legacy.get("settings") or {})
        merged["settings"].update(current.get("settings") or {})
        merged["settings"].setdefault("auto_download_missing", True)

        legacy_subs = legacy.get("subscriptions") or {}
        current_subs = current.get("subscriptions") or {}
        if isinstance(legacy_subs, list):
            legacy_subs = {str(item.get("id") or item.get("subscription_id") or idx): item for idx, item in enumerate(legacy_subs) if isinstance(item, dict)}
        if isinstance(current_subs, list):
            current_subs = {str(item.get("id") or item.get("subscription_id") or idx): item for idx, item in enumerate(current_subs) if isinstance(item, dict)}
        if isinstance(legacy_subs, dict):
            merged["subscriptions"].update(legacy_subs)
        if isinstance(current_subs, dict):
            merged["subscriptions"].update(current_subs)

        if merged != current or not target.exists():
            _write_json_file(target, merged)
            logging.info(
                "merged subscription file %s -> %s, total=%s",
                source,
                target,
                len(merged["subscriptions"]),
            )
    except Exception:
        logging.exception("subscription file merge failed: %s -> %s", source, target)


merge_subscription_file(data_dir() / "subscriptions.json", SUBSCRIPTIONS_FILE)
migrate_runtime_file(data_dir() / "tasks.json", TASKS_FILE)
subscription_manager = SubscriptionManager(config_dir=config_dir())

notification_manager = NotificationManager(config_dir() / "notifications.json")
rename_rule_store = RenameRuleStore(config_dir() / "rename_rules.json")
rename_plan_manager = RenamePlanManager(config_dir() / "rename_plans.json", rename_rule_store)
agent_manager = AgentManager(
    config_dir() / "agent.json",
    config_dir() / "agent_sessions.json",
)


MANUAL_ORGANIZE_MODES = {"off", "review", "auto_safe"}
MANUAL_DOWNLOAD_SOURCES = {"web", "wecom"}


def manual_organize_mode():
    """Return the UI-managed post-download organization policy.

    AUDIOFLOW_AUTO_RENAME remains a compatibility fallback for existing
    deployments, but new deployments do not need another Compose variable.
    """
    saved = str(cookie_manager.get_cookie("manual_organize_mode") or "").strip()
    if saved in MANUAL_ORGANIZE_MODES:
        return saved
    legacy_value = os.getenv("AUDIOFLOW_AUTO_RENAME")
    if legacy_value is not None:
        legacy_enabled = str(legacy_value).strip().lower()
        return "review" if legacy_enabled in {"1", "true", "yes", "on"} else "off"
    return "review"


def manual_download_origin(source):
    return str(source or "").strip().casefold() in MANUAL_DOWNLOAD_SOURCES


def _rename_safe_name(value):
    text = str(value or "").strip() or "未知"
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text[:200]


def _task_album_dir(task):
    album = task.get("album") or {}
    root = Path(resolve_download_dir((task.get("options") or {}).get("download_dir")))
    if cookie_manager.get_cookie("organize_by_platform_enabled") == "true":
        root /= _rename_safe_name(album.get("platform") or "未知平台")
    return root / _rename_safe_name(album.get("title") or task.get("title") or "未知专辑")


def _notify_rename_plan(plan, task_id=""):
    if not plan or plan.get("status") not in {"pending_confirmation", "needs_review"}:
        return
    summary = plan.get("summary") or {}
    samples = [
        f"{item.get('source_name')} -> {item.get('target_name')}"
        for item in (plan.get("items") or [])[:3]
    ]
    lines = [
        f"专辑：{(plan.get('album') or {}).get('title') or '-'}",
        f"计划 ID：{plan.get('id')}",
        f"格式：{plan.get('suggested_format')}",
        f"待重命名：{summary.get('planned', 0)} 个",
        f"需复核问题：{summary.get('issues', 0)} 个",
        f"特殊文件：{summary.get('special_files', summary.get('unmatched', 0))} 个",
        f"缺失章节（已预留空号）：{summary.get('missing_chapters', 0)} 个",
    ]
    if samples:
        lines.extend(["示例：", *samples])
    if plan.get("status") == "pending_confirmation":
        lines.append(f"企业微信回复：确认重命名 {plan.get('id')} / 取消重命名 {plan.get('id')}")
    else:
        lines.append("计划存在歧义或特殊文件，已阻止执行；可逐项复核，或选择保留风险文件并整理其余文件。")
    base_url = str(os.getenv("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    if base_url:
        lines.append(f"详情：{base_url}/api/rename-plans/{plan.get('id')}")
    notification_manager.notify(
        "rename_confirmation",
        f"待确认重命名：{(plan.get('album') or {}).get('title') or task_id}",
        "\n".join(lines),
        {
            "title": (plan.get("album") or {}).get("title") or "",
            "plan_id": plan.get("id"),
            "plan_status": plan.get("status"),
            "planned": summary.get("planned", 0),
            "issues": summary.get("issues", 0),
            "task_id": task_id or plan.get("task_id") or "",
        },
    )


def create_rename_plan_for_task(task_id, *, notify=True, replace=False):
    task = task_snapshot(task_id)
    if not task:
        raise KeyError("下载任务不存在")
    if task.get("status") != "completed":
        raise ValueError("只有完整下载成功的任务才能生成重命名计划")
    existing = next((
        item for item in rename_plan_manager.list()
        if item.get("task_id") == task_id
        and item.get("status") not in {"cancelled", "expired", "failed"}
    ), None)
    if existing:
        if not replace:
            return existing
        if existing.get("status") in {"executing", "completed"}:
            raise ValueError("该任务已有已执行或正在执行的整理计划")
        rename_plan_manager.cancel(existing.get("id"))
    album = task.get("album") or {}
    chapters = task.get("success_chapters") or task.get("chapters") or []
    plan = rename_plan_manager.create_plan(
        task_id=task_id,
        album=album,
        chapters=chapters,
        album_dir=_task_album_dir(task),
        origin_source=task.get("origin_source") or task.get("source") or "",
    )
    if notify:
        _notify_rename_plan(plan, task_id)
    return plan


def schedule_rename_plan(task_id):
    mode = manual_organize_mode()
    task = task_snapshot(task_id)
    if mode == "off" or not task or not task.get("organize_after_download"):
        return

    def worker():
        try:
            plan = create_rename_plan_for_task(task_id)
            if (mode == "auto_safe" and plan.get("status") == "pending_confirmation"
                    and not plan.get("configuration_confirmation_required")):
                completed = rename_plan_manager.confirm(plan.get("id"))
                summary = completed.get("summary") or {}
                verification = completed.get("verification") or {}
                verification_text = (
                    f"验证：{'通过' if verification.get('passed') else '发现问题'}"
                    if verification else "验证：未启用"
                )
                notification_manager.notify(
                    "rename_confirmation",
                    f"整理完成：{(completed.get('album') or {}).get('title') or task_id}",
                    f"已按该专辑确认过的规则自动整理 {summary.get('planned', 0)} 个文件。\n{verification_text}",
                    {
                        "title": (completed.get("album") or {}).get("title") or "",
                        "plan_id": completed.get("id"),
                        "plan_status": completed.get("status"),
                        "planned": summary.get("planned", 0),
                        "issues": 0,
                        "task_id": task_id,
                    },
                )
        except Exception:
            logging.exception("automatic audiobook rename analysis failed: %s", task_id)

    threading.Thread(target=worker, name=f"rename-plan-{task_id}", daemon=True).start()


def ximalaya_subscription_quality(album, value=None, *, default_web=False):
    """Validate a per-album Ximalaya subscription profile."""
    platform = canonical_subscription_platform(
        (album or {}).get("platform") or (album or {}).get("source")
    )
    if platform != "喜马拉雅":
        if value not in (None, ""):
            raise ValueError("只有喜马拉雅订阅支持单独选择网页版、杜比或无损")
        return None
    quality = str(value or "").strip()
    if not quality and default_web:
        quality = XMLY_WEB_SUBSCRIPTION_QUALITY
    if quality and quality not in XMLY_SUBSCRIPTION_QUALITIES:
        raise ValueError("不支持的喜马拉雅订阅下载方式")
    return quality or None


def _json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def _serialize_background_event(event):
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) <= BACKGROUND_EVENTS_MAX_BYTES:
        return line

    # A single pathological payload must not defeat the file size limit.
    compact_event = dict(event)
    compact_event["kind"] = str(event.get("kind") or "event")[:64]
    compact_event["title"] = str(event.get("title") or "")[:512]
    compact_event["detail"] = str(event.get("detail") or "")[:4096]
    compact_event["payload"] = {"truncated": True, "reason": "event_too_large"}
    return (json.dumps(compact_event, ensure_ascii=False) + "\n").encode("utf-8")


def _background_events_tail_bytes(byte_limit):
    if byte_limit <= 0 or not BACKGROUND_EVENTS_FILE.exists():
        return b""
    with BACKGROUND_EVENTS_FILE.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - byte_limit)
        handle.seek(start)
        data = handle.read(byte_limit)
    if start:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else b""
    return data


def _replace_background_events(content):
    tmp = BACKGROUND_EVENTS_FILE.with_name(
        f".{BACKGROUND_EVENTS_FILE.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with tmp.open("wb") as handle:
            handle.write(content)
        os.replace(tmp, BACKGROUND_EVENTS_FILE)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _prune_background_events_locked(max_keep=None):
    if not BACKGROUND_EVENTS_FILE.exists():
        return 0
    keep = background_events_max_keep() if max_keep is None else max(10, min(5000, int(max_keep)))
    lines = BACKGROUND_EVENTS_FILE.read_bytes().splitlines(keepends=True)
    if len(lines) > keep:
        lines = lines[-keep:]
        _replace_background_events(b"".join(lines))
    return len(lines)


def prune_background_events(max_keep=None):
    with background_events_lock:
        return _prune_background_events_locked(max_keep)


def append_background_event(kind, title, detail="", payload=None):
    event = {
        "id": uuid.uuid4().hex[:12],
        "kind": str(kind or "event"),
        "title": str(title or ""),
        "detail": str(detail or ""),
        "payload": _json_safe(payload or {}),
        "created_at": time.time(),
    }
    try:
        line = _serialize_background_event(event)
        with background_events_lock:
            BACKGROUND_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            current_size = BACKGROUND_EVENTS_FILE.stat().st_size if BACKGROUND_EVENTS_FILE.exists() else 0
            if current_size + len(line) > BACKGROUND_EVENTS_MAX_BYTES:
                tail = _background_events_tail_bytes(BACKGROUND_EVENTS_MAX_BYTES - len(line))
                _replace_background_events(tail + line)
            else:
                with BACKGROUND_EVENTS_FILE.open("ab") as handle:
                    handle.write(line)
            _prune_background_events_locked()
    except Exception:
        logging.exception("append background event failed")
    return event


def load_background_events(limit=None):
    max_keep = background_events_max_keep()
    try:
        limit = max_keep if limit in (None, "") else max(1, min(max_keep, int(limit)))
    except (TypeError, ValueError):
        limit = max_keep
    if not BACKGROUND_EVENTS_FILE.exists():
        return []
    try:
        with BACKGROUND_EVENTS_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            data = b""
            while position > 0 and data.count(b"\n") <= limit:
                read_size = min(64 * 1024, position)
                position -= read_size
                handle.seek(position)
                data = handle.read(read_size) + data
        lines = data.splitlines()[-limit:]
        events = []
        for line in lines:
            try:
                events.append(json.loads(line.decode("utf-8")))
            except Exception:
                pass
        return list(reversed(events))
    except Exception:
        logging.exception("load background events failed")
        return []


def classify_failure_reason(error="", failed_chapters=None):
    text = " ".join(
        [str(error or "")]
        + [str((chapter or {}).get("_error") or "") for chapter in (failed_chapters or []) if isinstance(chapter, dict)]
    ).lower()
    if any(token in text for token in ("cookie", "登录", "登陆", "unauthorized", "401", "403")):
        return "登录/Cookie 失效"
    if any(token in text for token in ("vip", "会员", "付费", "权限", "白金", "restricted")):
        return "会员/付费限制"
    if any(token in text for token in ("limit", "限流", "频繁", "风控", "apistatus=114", "429")):
        return "平台限流/风控"
    if any(token in text for token in ("timeout", "timed out", "超时", "connection", "network", "连接")):
        return "网络超时/连接失败"
    if any(token in text for token in ("url", "404", "410", "音频", "链接", "地址")):
        return "音频地址失效"
    if any(token in text for token in ("permission", "denied", "no space", "磁盘", "写入", "目录")):
        return "本地文件/磁盘问题"
    if any(token in text for token in ("下载失败", "failed", "error", "失败")):
        return "下载失败"
    return "未知原因"


def load_tasks():
    if not TASKS_FILE.exists():
        return {}
    try:
        raw = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        loaded = raw.get("tasks", {}) if isinstance(raw, dict) else {}
        changed = False
        for task in loaded.values():
            if task.get("status") in ("queued", "running", "paused", "stopping"):
                states = task.get("chapter_states") or {}
                task["chapter_states"] = {
                    key: state for key, state in states.items()
                    if (state or {}).get("status") != "downloading"
                }
                task["status"] = "interrupted"
                task["error"] = "服务重启后任务已中断，可重试失败章节或重新添加下载。"
                task["failure_reason"] = "服务重启中断"
                task["finished_at"] = time.time()
                changed = True
        if changed:
            TASKS_FILE.write_text(json.dumps({"tasks": loaded}, ensure_ascii=False, indent=2), encoding="utf-8")
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logging.exception("load tasks failed")
        print(f"[任务] 加载任务文件失败：{exc}")
        return {}


tasks = load_tasks()


def save_tasks(force=False):
    global _last_task_save
    now = time.time()
    if not force and now - _last_task_save < TASK_SAVE_INTERVAL:
        return
    try:
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {tid: _json_safe(task) for tid, task in tasks.items()}
        TASKS_FILE.write_text(json.dumps({"tasks": snapshot}, ensure_ascii=False, indent=2), encoding="utf-8")
        _last_task_save = now
    except Exception as exc:
        logging.exception("save tasks failed")
        print(f"[任务] 保存任务文件失败：{exc}")

# ── 订阅自动检测调度器 ──────────────────────────────────
# 周期性扫描所有「到期」的订阅（last_check_at 超过 interval_hours），
# 调用 SubscriptionManager.diff_chapters 比对远端章节与本地文件，
# 发现缺失则自动加入下载队列到设置的下载路径。
_scheduler_lock = threading.Lock()
_scheduler_started = False
_scheduler_event = threading.Event()
_scheduler_status = {
    "started": False,
    "running": False,
    "last_run_at": 0,
    "last_due_count": 0,
    "last_checked_count": 0,
    "last_queued_count": 0,
    "last_error": "",
    "personal_sync_running": False,
    "personal_sync_last_run_at": 0,
    "personal_sync_last_total": 0,
    "personal_sync_last_added": 0,
    "personal_sync_last_checked": 0,
    "personal_sync_last_queued": 0,
    "personal_sync_last_error": "",
}


def personal_sync_interval_seconds(settings=None):
    settings = settings or subscription_manager.settings()
    try:
        hours = max(0, int(settings.get("personal_sync_interval_hours", 1) or 0))
    except Exception:
        hours = 1
    try:
        minutes = max(0, int(settings.get("personal_sync_interval_minutes", 0) or 0))
    except Exception:
        minutes = 0
    return max(60, hours * 3600 + minutes * 60)


def personal_sync_due(settings=None):
    settings = settings or subscription_manager.settings()
    if not settings.get("personal_sync_enabled", False):
        return False
    last = float(_scheduler_status.get("personal_sync_last_run_at") or 0)
    return not last or time.time() - last >= personal_sync_interval_seconds(settings)


def _sync_personal_ximalaya_subscriptions(force=False):
    settings = subscription_manager.settings()
    if not force and not personal_sync_due(settings):
        return {"skipped": True, "reason": "not_due"}
    _scheduler_status["personal_sync_running"] = True
    total = added = checked = queued = 0
    try:
        albums = _load_ximalaya_personal("subscriptions", all_pages=True)
        total = len(albums)
        auto_download = bool(settings.get("auto_download_missing", True))
        jobs = []
        for album in albums:
            sid = subscription_manager.subscription_id(album)
            existed = bool(subscription_manager.get(sid))
            item = subscription_manager.add_or_update(
                album,
                [],
                active_download_dir(),
                subscription_quality=None if existed else XMLY_WEB_SUBSCRIPTION_QUALITY,
            )
            if not existed:
                added += 1
                try:
                    job = start_subscription_job(item["id"], queue_missing=auto_download)
                    jobs.append(job)
                    checked += 1
                    queued += 1 if job else 0
                except Exception as exc:
                    subscription_manager.mark_check_error(item.get("id"), f"个人中心同步后检测失败：{exc}")
                    logging.exception("personal sync subscription check failed: %s", item.get("id"))
        _scheduler_status.update({
            "personal_sync_running": False,
            "personal_sync_last_run_at": time.time(),
            "personal_sync_last_total": total,
            "personal_sync_last_added": added,
            "personal_sync_last_checked": checked,
            "personal_sync_last_queued": queued,
            "personal_sync_last_error": "",
        })
        return {"skipped": False, "total": total, "added": added, "checked": checked, "queued": queued, "jobs": jobs}
    except Exception as exc:
        _scheduler_status.update({
            "personal_sync_running": False,
            "personal_sync_last_run_at": time.time(),
            "personal_sync_last_total": total,
            "personal_sync_last_added": added,
            "personal_sync_last_checked": checked,
            "personal_sync_last_queued": queued,
            "personal_sync_last_error": str(exc),
        })
        logging.exception("personal subscription sync failed")
        raise


def _personal_sync_tick(force=False):
    settings = subscription_manager.settings()
    platform = settings.get("personal_sync_platform") or "ximalaya"
    if platform != "ximalaya":
        _scheduler_status["personal_sync_last_error"] = f"暂不支持同步平台：{platform}"
        return {"skipped": True, "reason": "unsupported_platform"}
    return _sync_personal_ximalaya_subscriptions(force=force)


def _scheduler_tick(force=False):
    """单次扫描：处理一批到期的订阅。"""
    checked_count = 0
    queued_count = 0
    try:
        settings = subscription_manager.settings()
        if not settings.get("enabled", True):
            _scheduler_status.update({
                "running": False,
                "last_run_at": time.time(),
                "last_due_count": 0,
                "last_checked_count": 0,
                "last_queued_count": 0,
                "last_error": "",
            })
            return
        due = subscription_manager.active_subscriptions() if force else subscription_manager.due_subscriptions()
        _scheduler_status["last_due_count"] = len(due)
        if not due:
            _scheduler_status.update({
                "running": False,
                "last_run_at": time.time(),
                "last_checked_count": 0,
                "last_queued_count": 0,
                "last_error": "",
            })
            return
        auto_download = bool(settings.get("auto_download_missing", True))
        for item in due:
            try:
                sid = item.get("id")
                album = normalize_album(item.get("album") or item)
                album_id = album.get("id") or album.get("album_id") or album.get("book_id") or item.get("album_id")
                platform = album.get("platform") or item.get("platform")
                if not album_id or not platform:
                    continue
                result = _run_subscription_check(sid, queue_missing=auto_download, source="auto-subscription")
                checked_count += 1
                if result.get("queued"):
                    queued_count += 1
            except Exception as exc:
                subscription_manager.mark_check_error(item.get("id"), f"自动检测失败：{exc}")
                logging.exception("subscription scheduler item failed")
                print(f"[订阅调度] 处理 {item.get('id')} 失败：{exc}")
        _scheduler_status.update({
            "running": False,
            "last_run_at": time.time(),
            "last_checked_count": checked_count,
            "last_queued_count": queued_count,
            "last_error": "",
        })
    except Exception as exc:
        _scheduler_status.update({
            "running": False,
            "last_run_at": time.time(),
            "last_checked_count": checked_count,
            "last_queued_count": queued_count,
            "last_error": str(exc),
        })
        logging.exception("subscription scheduler failed")
        print(f"[订阅调度] 异常：{exc}")


def _scheduler_loop():
    """常驻循环。每分钟检查一次是否有到期订阅。"""
    while True:
        try:
            _scheduler_status["running"] = True
            _scheduler_tick()
            if personal_sync_due():
                _personal_sync_tick(force=False)
        except Exception as exc:
            _scheduler_status["running"] = False
            _scheduler_status["last_error"] = str(exc)
            print(f"[订阅调度] loop 异常：{exc}")
        
        # Weekly index rebuild: force a full rebuild every 7 days to catch
        # stale entries from manual file deletions outside the app.
        try:
            now = time.time()
            last_rebuild = float(_scheduler_status.get("last_index_rebuild_at") or 0)
            if now - last_rebuild > 7 * 24 * 3600:
                _scheduler_status["last_index_rebuild_at"] = now
                _scheduler_status["last_index_rebuild_count"] = 0
                dl_dir = active_download_dir()
                if dl_dir:
                    index = subscription_manager.build_audio_index(dl_dir, force=True)
                    count = index.get("count", 0) if isinstance(index, dict) else 0
                    _scheduler_status["last_index_rebuild_count"] = count
                    logging.info("weekly index rebuild: {} files indexed".format(count))
        except Exception:
            pass  # rebuild is best-effort; never break the main loop
        _scheduler_event.wait(60)
        _scheduler_event.clear()


def start_subscription_scheduler():
    """启动后台调度线程（幂等）。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_scheduler_loop, name="subscription-scheduler", daemon=True)
        thread.start()
        _scheduler_started = True
        _scheduler_status["started"] = True


def ensure_subscription_scheduler():
    """Start the scheduler whenever automatic subscription checks are enabled."""
    settings = subscription_manager.settings()
    if settings.get("enabled", True) or settings.get("personal_sync_enabled", False):
        start_subscription_scheduler()


def wake_subscription_scheduler(force=False):
    start_subscription_scheduler()
    if force:
        threading.Thread(target=lambda: _scheduler_tick(force=True), name="subscription-scheduler-force", daemon=True).start()
    else:
        _scheduler_event.set()


def subscription_scheduler_status():
    status = dict(_scheduler_status)
    status["settings"] = subscription_manager.settings()
    status["interval_seconds"] = subscription_manager.interval_seconds()
    status["personal_sync_interval_seconds"] = personal_sync_interval_seconds(status["settings"])
    status["personal_sync_due"] = personal_sync_due(status["settings"])
    if status.get("last_run_at"):
        try:
            status["last_run_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(status["last_run_at"])))
        except Exception:
            status["last_run_at_iso"] = ""
    if status.get("personal_sync_last_run_at"):
        try:
            status["personal_sync_last_run_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(status["personal_sync_last_run_at"])))
        except Exception:
            status["personal_sync_last_run_at_iso"] = ""
    due = subscription_manager.due_subscriptions()
    status["current_due_count"] = len(due)
    return status


def subscription_download_options(item, album, voice=None):
    """Build platform-specific options for automatic subscription downloads.

    The legacy global setting is a Ximalaya-style value (normally ``M4A 96K``)
    and must not be interpreted as lossless by unrelated platforms.  An
    explicit per-subscription value still wins so existing/future callers can
    opt in to another profile without changing the global default.
    """
    item = item or {}
    album = album or {}
    platform = canonical_subscription_platform(album.get("platform") or item.get("platform"))
    explicit_quality = str(
        item.get("subscription_quality")
        or album.get("subscription_quality")
        or ""
    ).strip()

    if platform == "喜马拉雅":
        # Imported or legacy records may contain a global bitrate label that is
        # not a valid Ximalaya route. Keep those records usable and conservative.
        quality = (
            explicit_quality
            if explicit_quality in XMLY_SUBSCRIPTION_QUALITIES
            else XMLY_WEB_SUBSCRIPTION_QUALITY
        )
    elif explicit_quality:
        quality = explicit_quality
    elif platform == "酷我听书":
        # Do not let the generic M4A value normalize to Kuwo lossless.  Standard
        # mode itself falls back 128 -> 192 -> 320 when a bitrate is unavailable.
        quality = "kuwo:standard"
    else:
        quality = subscription_manager.settings().get("quality", "M4A 96K")

    options = {"download_dir": active_download_dir(), "quality": quality}
    if voice:
        options["voice"] = voice
    return options


def _run_subscription_check(sid, queue_missing=False, source="subscription-check", progress=None, retry_restricted=False):
    def set_progress(message, **fields):
        if callable(progress):
            try:
                progress(message, **fields)
            except Exception:
                logging.debug("subscription progress callback failed", exc_info=True)

    set_progress("正在准备订阅检测")
    item = subscription_manager.get(sid)
    if not item:
        raise ValueError("订阅不存在")
    album = normalize_album(item.get("album") or item)
    voice = item.get("voice") or album.get("voice")
    album_id = album.get("id") or album.get("album_id") or album.get("book_id") or item.get("album_id")
    platform = album.get("platform") or item.get("platform")
    if not album_id or not platform:
        raise ValueError("订阅缺少专辑 ID 或平台")
    set_progress("正在获取远端章节", platform=platform, album_id=album_id)
    if platform == "七猫听书":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    if platform == "番茄听书":
        if not voice:
            voice = resolve_voice_for_album(album, (get_album_voices(album) or [None])[0])
        chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), voice) if voice else []
    elif platform == "七猫听书":
        if not voice:
            voice = resolve_voice_for_album(album, (get_album_voices(album) or [None])[0])
        chapters = search_manager.qimao_manager.get_chapters(str(album_id), voice) if voice else search_manager.qimao_manager.get_chapters(str(album_id))
    else:
        chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    chapters = [normalize_chapter(chapter, index) for index, chapter in enumerate(chapters or [], start=1)]
    if not chapters and item.get("chapters"):
        chapters = item.get("chapters") or []
    # 与历史已知章节合并取并集：避免单次章节 API 抖动（如喜马拉雅 new_api 被风控）导致
    # 章节列表回退、漏掉曾经检测到的章节，从而订阅永远补不全那几集。
    saved_chapters = item.get("chapters") or []
    if saved_chapters and chapters:
        existing_keys = {chapter_key(ch) for ch in chapters if isinstance(ch, dict)}
        appended = [
            {**ch, "_source_missing": True}
            for ch in saved_chapters
            if isinstance(ch, dict) and chapter_key(ch) not in existing_keys
        ]
        if appended:
            chapters = chapters + appended
            set_progress("合并历史章节", merged_extra=len(appended))
    set_progress("正在扫描本地文件", chapter_count=len(chapters))
    scan_cache = {}
    # Keep comparison and result persistence atomic with download-completion
    # callbacks and the background statistics refresher.
    with subscription_manager.locked():
        diff = subscription_manager.diff_chapters(item, chapters, active_download_dir(), scan_cache=scan_cache, skip_local=False, retry_restricted=retry_restricted)
        set_progress("正在更新订阅结果", missing_count=len(diff.get("missing") or []))
        subscription_manager.update_check_result(sid, chapters, diff, "自动检测完成" if queue_missing else "已检查", refresh_local=False)
        item = subscription_manager.get(sid) or item
        item["download_dir"] = active_download_dir()
        stats = subscription_manager.stats_for(item, active_download_dir(), fast=True)
    queued_task_id = ""
    queued_chapter_count = 0
    already_queued_count = 0
    missing = diff.get("missing") or []
    if queue_missing and missing:
        pending_keys = active_task_chapter_keys(album)
        chapters_to_queue = [chapter for chapter in missing if chapter_key(chapter) not in pending_keys]
        already_queued_count = len(missing) - len(chapters_to_queue)
        if already_queued_count:
            set_progress("跳过正在下载的章节", skipped_count=already_queued_count)
        set_progress("正在创建下载任务", missing_count=len(chapters_to_queue))
        if not voice:
            voices = get_album_voices(album)
            voice = voices[0] if voices else None
        if chapters_to_queue:
            queued_task_id = f"sub-{uuid.uuid4().hex[:12]}"
            queued_chapter_count = len(chapters_to_queue)
            options = subscription_download_options(item, album, voice=voice)
            start_download_task(queued_task_id, album, chapters_to_queue, options, source=source)
            notification_manager.notify(
                "subscription_queued",
                f"订阅发现新章节：{album.get('title') or '未知专辑'}",
                f"平台：{platform}\n新增/缺失：{queued_chapter_count} 章\n任务：{queued_task_id}",
                {"album": album, "missing_count": queued_chapter_count, "task_id": queued_task_id, "source": source},
            )
    elif diff.get("missing") and not queue_missing:
        notification_manager.notify(
            "subscription_checked",
            f"订阅检测发现缺失：{album.get('title') or '未知专辑'}",
            f"平台：{platform}\n缺失：{len(diff.get('missing') or [])} 章",
            {"album": album, "missing_count": len(diff.get("missing") or []), "source": source},
        )
    return {
        "diff": diff,
        "stats": stats,
        "chapters": chapters,
        "chapter_count": len(chapters),
        "missing_count": len(missing),
        "queued": bool(queued_task_id),
        "queued_chapter_count": queued_chapter_count,
        "already_queued_count": already_queued_count,
        "task_id": queued_task_id,
        "restricted_count": diff.get("restricted_count", 0),
        "deferred_failed_count": diff.get("deferred_failed_count", 0),
        "title": album.get("title") or item.get("title") or sid,
    }


def _subscription_job(job_id, sid, queue_missing, manual=False):
    started_at = time.time()
    def update_progress(message, **fields):
        payload = {"message": message, "updated_at": time.time()}
        payload.update(fields)
        with subscription_job_lock:
            job = subscription_jobs.get(job_id)
            if job:
                job.update(payload)

    with subscription_job_lock:
        subscription_jobs[job_id].update(
            {
                "status": "running",
                "message": "正在检测订阅",
                "started_at": started_at,
                "updated_at": started_at,
            }
        )
    try:
        result = _run_subscription_check(sid, queue_missing=queue_missing, source="subscription", progress=update_progress, retry_restricted=manual)
        if result.get("queued"):
            message = "已加入下载队列"
        elif queue_missing:
            # 没建下载任务：区分「真的全下好了」与「有受限章节被跳过」，后者别误报无需补全
            rc = int((result.get("diff") or {}).get("restricted_count") or 0)
            message = f"检测完成，{rc} 章受限暂跳过（手动补全可强制重试）" if rc else "检测完成，无需补全"
        else:
            message = "检测完成"
        append_background_event(
            "subscription",
            message,
            f"{result.get('title') or sid} 缺失 {result.get('missing_count') or 0} 章",
            {"sid": sid, "queue_missing": queue_missing, "result": result},
        )
        finished_at = time.time()
        with subscription_job_lock:
            subscription_jobs[job_id].update(
                {"status": "done", "message": message, "result": result, "finished_at": finished_at, "updated_at": finished_at}
            )
    except Exception as exc:
        logging.exception("subscription job failed")
        append_background_event("subscription", "订阅检测失败", f"{sid}：{exc}", {"sid": sid, "error": str(exc)})
        finished_at = time.time()
        with subscription_job_lock:
            subscription_jobs[job_id].update(
                {"status": "failed", "message": str(exc), "error": str(exc), "finished_at": finished_at, "updated_at": finished_at}
            )


def cleanup_subscription_jobs(now=None):
    now = time.time() if now is None else float(now)
    terminal = {"done", "failed", "cancelled"}
    for job_id, job in list(subscription_jobs.items()):
        if job.get("status") in {"queued", "running"}:
            active_at = float(job.get("started_at") or job.get("updated_at") or job.get("created_at") or 0)
            if active_at and now - active_at > SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS:
                message = "订阅检测超时，请稍后重试"
                job.update(
                    {
                        "status": "failed",
                        "message": message,
                        "error": message,
                        "finished_at": now,
                        "updated_at": now,
                    }
                )
                append_background_event(
                    "subscription",
                    "订阅检测超时",
                    f"{job.get('sid') or job_id} 检测超时",
                    {"job_id": job_id, "sid": job.get("sid"), "timeout_seconds": SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS},
                )
        if job.get("status") in terminal:
            finished_at = float(job.get("finished_at") or job.get("updated_at") or job.get("created_at") or 0)
            if finished_at and now - finished_at > SUBSCRIPTION_JOB_TTL_SECONDS:
                subscription_jobs.pop(job_id, None)
    if len(subscription_jobs) <= SUBSCRIPTION_JOB_MAX_ITEMS:
        return
    ordered = sorted(
        subscription_jobs.items(),
        key=lambda item: float(item[1].get("finished_at") or item[1].get("created_at") or 0),
    )
    overflow = len(subscription_jobs) - SUBSCRIPTION_JOB_MAX_ITEMS
    for job_id, job in ordered:
        if overflow <= 0:
            break
        if job.get("status") in terminal:
            subscription_jobs.pop(job_id, None)
            overflow -= 1


def start_subscription_job(sid, queue_missing=False, manual=False):
    with subscription_job_lock:
        cleanup_subscription_jobs()
        for existing in subscription_jobs.values():
            if (
                existing.get("sid") == sid
                and existing.get("status") in ("queued", "running")
            ):
                return dict(existing)
        job_id = f"subjob-{uuid.uuid4().hex[:12]}"
        subscription_jobs[job_id] = {
            "id": job_id,
            "sid": sid,
            "status": "queued",
            "queue_missing": bool(queue_missing),
            "message": "已加入后台队列",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    threading.Thread(target=_subscription_job, args=(job_id, sid, queue_missing, manual), name=job_id, daemon=True).start()
    return dict(subscription_jobs[job_id])


def json_ok(**payload):
    return jsonify({"ok": True, **payload})


def json_error(message, status=400):
    return jsonify({"ok": False, "error": str(message)}), status


@app.get("/api/auth/status")
def api_auth_status():
    user = current_user()
    return json_ok(authenticated=bool(user), user=user, login_required=True)


@app.post("/api/auth/login")
def api_auth_login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if auth_manager.is_locked(username):
        return json_error(f"登录失败次数过多，请 {auth_manager.lock_remaining(username)} 秒后再试", 429)
    token = auth_manager.login(username, password)
    if not token:
        return json_error("账号或密码错误", 401)
    user = auth_manager.user_for_session(token)
    response = json_ok(user=user)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=auth_manager.session_ttl,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def api_auth_logout():
    auth_manager.logout(_session_token())
    response = json_ok(logged_out=True)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@app.post("/api/auth/password")
def api_auth_change_password():
    user = current_user()
    if not user:
        return json_error("未登录或会话已过期", 401)
    payload = request.get_json(silent=True) or {}
    try:
        auth_manager.change_password(
            user["username"],
            payload.get("old_password") or "",
            payload.get("new_password") or "",
        )
    except ValueError as exc:
        return json_error(str(exc), 400)
    response = json_ok(changed=True)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


def _first_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _to_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pick_nested_value(data, keys, nested_keys=("album", "book", "item", "data", "detail", "raw", "raw_data")):
    value = _first_value(data, *keys)
    if value not in (None, ""):
        return value
    for key in nested_keys:
        nested = data.get(key)
        if isinstance(nested, dict):
            value = _pick_nested_value(nested, keys, ())
            if value not in (None, ""):
                return value
    return ""


def normalize_cover_url(url, platform=""):
    url = str(url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    # 酷我图片：搜索结果返回 http://imgN.sycdn.kuwo.cn（无有效 https 证书 + 混合内容被浏览器拦截）。
    # 改写到有有效 https 证书的 imgN.kuwo.cn 主机（同路径可访问），并强制升级为 https。
    if "kuwo.cn" in url:
        url = url.replace("sycdn.kuwo.cn", "kuwo.cn")
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if platform == "喜马拉雅":
        return "https://imagev2.xmcdn.com" + (url if url.startswith("/") else f"/{url}")
    if platform == "懒人听书":
        return "https://m.lrts.me" + (url if url.startswith("/") else f"/{url}")
    if platform == "云听FM":
        return "https://www.radio.cn" + (url if url.startswith("/") else f"/{url}")
    return url


def normalize_album(album):
    data = dict(album or {})
    platform = _pick_nested_value(data, ("platform", "source")) or "未知平台"
    title = _pick_nested_value(data, ("title", "album_title", "albumTitle", "book_name", "bookName", "name", "AudioName")) or "未知专辑"
    author = _pick_nested_value(
        data,
        (
            "anchorNickName", "anchorNickname", "anchorName", "AnchorName",
            "nickname", "nickName", "userName", "userNickname", "userNickName",
            "author", "authorName", "anchor", "announcer", "reader", "narrator",
            "artist", "speaker",
        ),
        ("anchor", "anchorInfo", "announcerInfo", "user", "userInfo", "creator", "album", "book", "item", "data", "detail", "raw", "raw_data"),
    )
    cover = _pick_nested_value(
        data,
        (
            "cover", "cover_url", "coverUrl", "coverPath", "CoverUrl", "albumCover",
            "albumCoverUrl", "pic", "picUrl", "image", "imageUrl", "thumb_url",
            "thumbUrl", "thumb", "thumbnail", "image_link", "bookCover", "posterUrl",
            "img", "imgPath", "hts_img", "albumpic", "albumPic", "web_albumpic_short",
        ),
    )
    episodes = _pick_nested_value(
        data,
        (
            "episodes", "chapter_count", "chapterCount", "chapters", "track_count",
            "trackCount", "tracks", "tracks_count", "tracksCount", "total_chapters",
            "AllAudioChapters", "total_num", "totalNum", "total", "sections",
            "section_count", "entityCount", "programCount", "songCount",
        ),
    )
    data["title"] = title
    data["author"] = str(author or "").strip()
    data["platform"] = platform
    data["cover"] = normalize_cover_url(cover, platform)
    data["episodes"] = _to_int(episodes, 0)
    return data


def merge_album_detail(album, detail):
    if not isinstance(detail, dict):
        return album
    merged = dict(album or {})
    normalized = normalize_album({**detail, "platform": album.get("platform") or detail.get("platform")})
    for key in ("title", "author", "cover", "status", "description", "category"):
        value = normalized.get(key) or detail.get(key)
        if value and (not merged.get(key) or str(merged.get(key)).strip() in ("未知", "未知作者", "未知专辑")):
            merged[key] = value
    # 简介：各平台详情字段名不一（多数用 description，起点用 intro，另有 desc/summary），
    # 统一并入 description，前端右栏即可跨平台读到简介。
    if not merged.get("description"):
        detail_intro = detail.get("description") or detail.get("intro") or detail.get("desc") or detail.get("summary")
        if detail_intro:
            merged["description"] = str(detail_intro).strip()
    if _to_int(merged.get("episodes")) <= 0 and _to_int(normalized.get("episodes")) > 0:
        merged["episodes"] = normalized["episodes"]
    return normalize_album(merged)


def ximalaya_album_identity_error(album):
    album = album or {}
    if album.get("platform") != "喜马拉雅":
        return ""
    requested_id = str(album.get("requested_album_id") or "").strip()
    actual_id = str(album.get("id") or album.get("album_id") or album.get("book_id") or "").strip()
    if requested_id and requested_id != actual_id:
        return f"喜马拉雅专辑ID不一致：请求 {requested_id}，实际任务 {actual_id}，已拒绝创建任务"
    return ""


def parse_duration_seconds(value):
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        seconds = float(value)
        return int(seconds / 1000) if seconds > 10000 else int(seconds)
    text = str(value).strip()
    if not text:
        return 0
    if ":" in text:
        try:
            total = 0
            for part in text.split(":"):
                total = total * 60 + int(float(part))
            return total
        except (TypeError, ValueError):
            return 0
    try:
        seconds = float(text)
        return int(seconds / 1000) if seconds > 10000 else int(seconds)
    except (TypeError, ValueError):
        return 0


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def normalize_chapter(chapter, index=None):
    if not isinstance(chapter, dict):
        return {}
    data = dict(chapter)
    chapter_id = chapter_identifier(data)
    if chapter_id:
        data.setdefault("id", chapter_id)
        data.setdefault("chapter_id", chapter_id.replace("chapter-", "", 1))
    title = (
        data.get("title")
        or data.get("name")
        or data.get("chapter_title")
        or data.get("chapterTitle")
        or data.get("audio_title")
        or data.get("audioTitle")
        or (f"第 {index} 章" if index else "未知章节")
    )
    data.setdefault("title", title)
    seconds = 0
    for key in ("duration", "duration_str", "time", "play_time", "length", "duration_ms", "track_duration", "trackDuration", "audio_duration"):
        seconds = parse_duration_seconds(data.get(key))
        if seconds:
            break
    if seconds:
        data["duration"] = seconds
        data.setdefault("duration_str", format_duration(seconds))
    elif not data.get("duration_str"):
        data["duration_str"] = ""
    if index is not None:
        data.setdefault("ui_display_index", index)
        data.setdefault("order_num", index)
    return data


def hydrate_download_chapters(album, chapters, chapter_ids=None):
    if chapters:
        return [normalize_chapter(chapter, index) for index, chapter in enumerate(chapters, start=1)]
    ids = [str(item) for item in (chapter_ids or []) if item not in (None, "")]
    if not ids:
        return []
    album = normalize_album(album)
    platform = album.get("platform")
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    if not album_id or not platform:
        return []
    if _is_personal_qidian_album(album):
        all_chapters = _qidian_api_for_album(album).get_qidian_chapters(str(album_id)) or []
    else:
        all_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    normalized = [normalize_chapter(chapter, index) for index, chapter in enumerate(all_chapters, start=1)]
    wanted = set(ids)
    return [chapter for chapter in normalized if chapter_identifier(chapter) in wanted or chapter.get("id") in wanted]


def load_all_album_chapters(album, voice=None):
    """Load a complete directory for whole-album downloads and subscriptions."""
    album = normalize_album(album)
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    platform = album.get("platform")
    if not album_id or not platform:
        return []
    if platform == "七猫听书":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    active_voice = resolve_voice_for_album(album, voice)
    if _is_personal_qidian_album(album):
        raw_chapters = _qidian_api_for_album(album).get_qidian_chapters(str(album_id)) or []
    elif platform == "番茄畅听" and active_voice:
        raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), active_voice, page=1, page_size=10000)
    elif platform == "番茄听书" and active_voice:
        raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), active_voice)
    elif platform == "七猫听书" and active_voice:
        raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), active_voice)
    else:
        raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    return [normalize_chapter(chapter, index) for index, chapter in enumerate(raw_chapters or [], start=1)]


def normalize_voice(voice, index=None):
    if not isinstance(voice, dict):
        return {}
    data = dict(voice)
    vid = str(
        data.get("voice_id")
        or data.get("tone_id")
        or data.get("id")
        or data.get("name")
        or index
        or ""
    )
    if vid:
        data.setdefault("id", vid)
        data.setdefault("voice_id", data.get("voice_id") or data.get("tone_id") or vid)
    data.setdefault("name", data.get("title") or data.get("label") or f"音色{index or ''}")
    kind = data.get("kind")
    if not kind:
        kind = "real" if str(data.get("is_real_person") or "") == "1" else "ai"
    data["kind"] = kind
    data.setdefault("category", "真人录制" if kind == "real" else "AI 音色")
    return data


def get_album_voice_context(album):
    album = normalize_album(album)
    album_id = str(album.get("id") or album.get("album_id") or album.get("book_id") or "")
    book_id = str(album.get("book_id") or album_id)
    platform = album.get("platform")
    return album, album_id, book_id, platform


def get_album_voices(album):
    album, album_id, book_id, platform = get_album_voice_context(album)
    if platform == "番茄畅听":
        voices = search_manager.fanqie_manager.fetch_voices(book_id or album_id)
        for voice in voices:
            voice.setdefault("platform", platform)
        return [normalize_voice(v, i) for i, v in enumerate(voices, 1)]
    if platform == "番茄听书":
        voices = search_manager.fanqie_tingshu_manager.fetch_voices(book_id)
        return [normalize_voice(v, i) for i, v in enumerate(voices, 1)]
    if platform == "七猫听书":
        if album_id:
            search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
        voices = search_manager.qimao_manager.fetch_voices(book_id)
        return [normalize_voice(v, i) for i, v in enumerate(voices, 1)]
    return []


def resolve_voice_for_album(album, voice):
    album, album_id, book_id, platform = get_album_voice_context(album)
    if not isinstance(voice, dict):
        return None
    if platform == "番茄畅听":
        return search_manager.fanqie_manager.resolve_voice_config(book_id or album_id, voice) or voice
    if platform == "番茄听书":
        return search_manager.fanqie_tingshu_manager.resolve_voice_config(book_id, voice) or voice
    if platform == "七猫听书":
        voices = search_manager.qimao_manager.fetch_voices(book_id)
        return search_manager.qimao_manager._match_voice(voices, voice) or voice
    return voice


def _is_personal_qidian_album(album):
    return (
        isinstance(album, dict)
        and album.get("platform") in ("起点听书", "qidian")
        and str(album.get("personal_center_platform") or "").strip() == "qidian"
    )


def _qidian_api_for_album(album):
    if not _is_personal_qidian_album(album):
        return search_manager.search_manager
    cookie = _get_personal_cookie("qidian")
    if not cookie:
        raise RuntimeError("起点听书个人中心登录已失效，请重新扫码")
    from core.search_manager import SearchManager
    api = SearchManager()
    api.set_qidian_cookie(cookie)
    return api


_QIDIAN_AUDIO_ID_KEYS = (
    "adid", "audioDataId", "audioBookId", "audioId",
)
_QIDIAN_AUDIO_ID_KEY_NAMES = {key.casefold() for key in _QIDIAN_AUDIO_ID_KEYS}


def _qidian_audio_id_from_book(book):
    """Extract the qdcg audio id without confusing it with the novel bookId."""
    if not isinstance(book, dict):
        return ""
    for key, value in book.items():
        if str(key).casefold() in _QIDIAN_AUDIO_ID_KEY_NAMES and value not in (None, "", 0, "0"):
            return str(value).strip()
    for key in ("audioBook", "audioInfo", "audioData", "audio", "bookInfo", "raw_data", "raw"):
        value = _qidian_audio_id_from_book(book.get(key))
        if value:
            return value
    return ""


def _normalize_qidian_match_text(value):
    return re.sub(r"[\s·•:：,，.。!！?？_\-]+", "", str(value or "")).casefold()


def _resolve_personal_qidian_album(album, api):
    """Resolve a bookshelf novel id to the audio catalog id used by qdcg."""
    resolved = dict(album or {})
    audio_id = str(resolved.get("qidian_audio_id") or _qidian_audio_id_from_book(resolved)).strip()
    book_id = str(
        resolved.get("qidian_book_id")
        or _pick_nested_value(resolved, ("bookId", "book_id"), ("raw_data", "raw", "book"))
        or resolved.get("id")
        or ""
    ).strip()
    if audio_id:
        resolved["id"] = audio_id
        resolved["qidian_audio_id"] = audio_id
        if book_id:
            resolved["qidian_book_id"] = book_id
        return resolved

    # Older bookshelf responses only expose the source novel bookId. Resolve
    # the corresponding audio entry by exact title, then disambiguate by author.
    if not resolved.get("qidian_book_id"):
        return resolved
    title = str(resolved.get("title") or "").strip()
    if not title:
        raise RuntimeError("起点书架条目缺少书名，无法匹配有声专辑")
    candidates = api.search_qidian(title, page_size=50) or []
    wanted_title = _normalize_qidian_match_text(title)
    matches = [
        item for item in candidates
        if _normalize_qidian_match_text(item.get("title") or item.get("bookName")) == wanted_title
    ]
    if not matches:
        matches = [
            item for item in candidates
            if wanted_title
            and (
                wanted_title in _normalize_qidian_match_text(item.get("title") or item.get("bookName"))
                or _normalize_qidian_match_text(item.get("title") or item.get("bookName")) in wanted_title
            )
        ]
    wanted_author = _normalize_qidian_match_text(resolved.get("author"))
    if wanted_author and len(matches) > 1:
        author_matches = [
            item for item in matches
            if _normalize_qidian_match_text(item.get("author") or item.get("authorName")) == wanted_author
        ]
        if author_matches:
            matches = author_matches
    if not matches:
        raise RuntimeError(f"起点书架作品《{title}》未匹配到有声专辑，请确认该作品仍可收听")
    if len(matches) > 1:
        raise RuntimeError(f"起点书架作品《{title}》匹配到多个有声专辑，请通过搜索页选择正确专辑")
    audio_id = str(
        matches[0].get("id")
        or matches[0].get("album_id")
        or _qidian_audio_id_from_book(matches[0])
        or ""
    ).strip()
    if not audio_id:
        raise RuntimeError(f"起点有声专辑《{title}》缺少可用的专辑 ID")
    resolved["id"] = audio_id
    resolved["qidian_audio_id"] = audio_id
    resolved["qidian_book_id"] = book_id
    return resolved


def _load_personal_qidian_album_chapters(album, api):
    """Try every known Qidian album identity before declaring an empty catalog."""
    source = dict(album or {})
    candidate_ids = []

    def add(value):
        value = str(value or "").strip()
        if value and value not in candidate_ids:
            candidate_ids.append(value)

    # A type=2 bookshelf bookId is often already the qdcg adid. Try it first;
    # explicit audio ids and the displayed item id remain compatible fallbacks.
    add(source.get("qidian_book_id"))
    add(source.get("qidian_audio_id") or _qidian_audio_id_from_book(source))
    add(source.get("id") or source.get("album_id") or source.get("book_id"))

    attempted = []
    for candidate_id in candidate_ids:
        chapters = api.get_qidian_chapters(candidate_id) or []
        attempted.append(candidate_id)
        logging.info(
            "Qidian personal chapter candidate: album_id=%s chapters=%s",
            candidate_id,
            len(chapters),
        )
        if chapters:
            resolved = dict(source)
            resolved["id"] = candidate_id
            resolved["qidian_audio_id"] = candidate_id
            return resolved, chapters, attempted

    if source.get("qidian_book_id"):
        search_source = dict(source)
        for key in ("qidian_audio_id", "raw_data", "raw"):
            search_source.pop(key, None)
        search_source["id"] = source.get("qidian_book_id")
        searched = _resolve_personal_qidian_album(search_source, api)
        searched_id = str(searched.get("id") or "").strip()
        if searched_id and searched_id not in attempted:
            chapters = api.get_qidian_chapters(searched_id) or []
            attempted.append(searched_id)
            logging.info(
                "Qidian personal searched candidate: album_id=%s chapters=%s",
                searched_id,
                len(chapters),
            )
            if chapters:
                return searched, chapters, attempted
    return source, [], attempted


def _load_personal_qidian_album_detail(album, api):
    """Resolve a personal bookshelf item without forcing a title search first."""
    source = dict(album or {})
    candidate_ids = []

    def add(value):
        value = str(value or "").strip()
        if value and value not in candidate_ids:
            candidate_ids.append(value)

    add(source.get("qidian_book_id"))
    add(source.get("qidian_audio_id") or _qidian_audio_id_from_book(source))
    add(source.get("id") or source.get("album_id") or source.get("book_id"))

    attempted = []
    for candidate_id in candidate_ids:
        detail = api.get_qidian_detail(candidate_id)
        attempted.append(candidate_id)
        logging.info(
            "Qidian personal detail candidate: album_id=%s found=%s",
            candidate_id,
            bool(detail),
        )
        if detail:
            resolved = dict(source)
            resolved["id"] = candidate_id
            resolved["qidian_audio_id"] = candidate_id
            return resolved, detail, attempted

    if source.get("qidian_book_id"):
        search_source = dict(source)
        for key in ("qidian_audio_id", "raw_data", "raw"):
            search_source.pop(key, None)
        search_source["id"] = source.get("qidian_book_id")
        searched = _resolve_personal_qidian_album(search_source, api)
        searched_id = str(searched.get("id") or "").strip()
        if searched_id and searched_id not in attempted:
            detail = api.get_qidian_detail(searched_id)
            attempted.append(searched_id)
            if detail:
                return searched, detail, attempted
    return source, None, attempted


def chapter_identifier(chapter):
    if not isinstance(chapter, dict):
        return ""
    for key in ("id", "track_id", "trackId", "chapter_id", "chapterId", "cid", "acid", "audio_id", "audioId", "itemId", "item_id"):
        value = chapter.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def sync_platform_cookie(platform):
    """Keep long-lived platform managers aligned with the persisted cookie."""
    key_map = {
        "喜马拉雅": "xmly",
        "懒人听书": "lrts",
        "起点听书": "qidian",
        "蜻蜓FM": "qtfm",
        "网易云听书": "netease",
    }
    key = key_map.get(platform)
    if not key:
        return ""
    cookie = cookie_manager.get_cookie(key)
    if isinstance(cookie, dict):
        cookie = "; ".join(f"{k}={v}" for k, v in cookie.items() if v)
    cookie = str(cookie or "").strip()
    if cookie:
        try:
            search_manager.set_cookie(platform, cookie)
        except Exception:
            logging.exception("sync platform cookie failed: %s", platform)
    return cookie


def chapter_direct_audio_url(chapter):
    if not isinstance(chapter, dict):
        return ""
    for key in ("audio_url", "audioUrl", "play_url", "playUrl", "url", "download_url", "downloadUrl", "mediaUrl", "media_url"):
        value = chapter.get(key)
        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            return value.strip()
    return ""


def pick_audio_url(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        for item in value.values():
            picked = pick_audio_url(item)
            if picked:
                return picked
    if isinstance(value, (list, tuple)):
        for item in value:
            picked = pick_audio_url(item)
            if picked:
                return picked
    return ""


_LOCAL_AUDIO_TOKENS = {}
_LOCAL_AUDIO_TTL = 15 * 60


def register_local_audio(path):
    """Register a temp audio file and return a browser-accessible URL."""
    if not path:
        return ""
    p = Path(str(path))
    if not p.is_file():
        return ""
    token = uuid.uuid4().hex
    _LOCAL_AUDIO_TOKENS[token] = {"path": str(p), "created_at": time.time()}
    return f"/api/local-audio/{token}"


def cleanup_local_audio_tokens():
    now = time.time()
    expired = [
        token for token, item in list(_LOCAL_AUDIO_TOKENS.items())
        if now - float(item.get("created_at") or 0) > _LOCAL_AUDIO_TTL
    ]
    for token in expired:
        item = _LOCAL_AUDIO_TOKENS.pop(token, None) or {}
        try:
            path = item.get("path")
            if path and Path(path).is_file():
                os.remove(path)
        except OSError:
            pass


def task_snapshot(task_id=None):
    with task_lock:
        if task_id:
            return dict(tasks.get(task_id) or {})
        return [dict(item) for item in tasks.values()]


def set_task(task_id, **updates):
    with task_lock:
        task = tasks.setdefault(task_id, {"id": task_id})
        task.update(updates)
        task["updated_at"] = time.time()
        save_tasks(force=bool({"status", "created_at", "finished_at", "failed_chapters", "success_chapters"} & set(updates)))
        return dict(task)


_ACTIVE_DOWNLOAD_STATUSES = {"queued", "running", "paused", "stopping"}


def active_task_chapter_tasks(album):
    """Map chapter IDs to live tasks already covering this album."""
    target = subscription_manager.subscription_id(normalize_album(album))
    chapters = {}
    with task_lock:
        for task in tasks.values():
            if task.get("status") not in _ACTIVE_DOWNLOAD_STATUSES:
                continue
            task_album = task.get("album") or {}
            if subscription_manager.subscription_id(normalize_album(task_album)) != target:
                continue
            for chapter in task.get("chapters") or []:
                if isinstance(chapter, dict):
                    chapters.setdefault(chapter_key(chapter), dict(task))
    return chapters


def active_task_chapter_keys(album):
    """Return chapter IDs already covered by a live task for this album."""
    return set(active_task_chapter_tasks(album))


def download_task_counts(task):
    """Return stable chapter counts for both current and legacy tasks."""
    total = max(0, _to_int(task.get("total") or task.get("chapter_count") or len(task.get("chapters") or [])))
    states = task.get("chapter_states") or {}
    state_success = sum(1 for state in states.values() if (state or {}).get("status") == "success")
    state_failed = sum(1 for state in states.values() if (state or {}).get("status") == "failed")
    success = max(0, _to_int(task.get("success")), state_success)
    failed = max(0, _to_int(task.get("failed")), state_failed)
    downloading = sum(1 for state in states.values() if (state or {}).get("status") == "downloading")
    if task.get("status") not in _ACTIVE_DOWNLOAD_STATUSES:
        downloading = 0
    pending = max(0, total - success - failed - downloading)
    return {
        "total": total,
        "success": min(total, success) if total else success,
        "failed": min(total, failed) if total else failed,
        "downloading": min(total, downloading) if total else downloading,
        "pending": pending,
    }


def update_download_chapter_status(task_id, chapter, status):
    """Persist one lightweight chapter state and refresh live counters."""
    if status not in {"downloading", "success", "failed"} or not isinstance(chapter, dict):
        return {}
    key = chapter_key(chapter)
    if not key:
        return {}
    with task_lock:
        task = tasks.get(task_id)
        if not task:
            return {}
        states = dict(task.get("chapter_states") or {})
        state = {"status": status, "updated_at": time.time()}
        if status == "failed" and chapter.get("_error"):
            state["error"] = str(chapter.get("_error"))[:500]
        states[key] = state
        task["chapter_states"] = states
        counts = download_task_counts(task)
        task["success"] = counts["success"]
        task["failed"] = counts["failed"]
        task["updated_at"] = time.time()
        save_tasks(force=False)
        return dict(task)


def _download_task_detail(task):
    """Build a chapter-level task view without duplicate heavyweight arrays."""
    detail = dict(task)
    raw_chapters = task.get("chapters") or []
    detail_available = bool(raw_chapters) and not task.get("history_compacted")
    states = task.get("chapter_states") or {}
    success_keys = {chapter_key(chapter) for chapter in task.get("success_chapters") or [] if isinstance(chapter, dict)}
    failed_by_key = {
        chapter_key(chapter): chapter
        for chapter in task.get("failed_chapters") or []
        if isinstance(chapter, dict)
    }
    chapters = []
    if detail_available:
        for index, chapter in enumerate(raw_chapters, start=1):
            if not isinstance(chapter, dict):
                continue
            item = dict(chapter)
            key = chapter_key(item)
            state = dict(states.get(key) or {})
            if not state and key in success_keys:
                state = {"status": "success"}
            if not state and key in failed_by_key:
                failed_chapter = failed_by_key[key]
                state = {"status": "failed", "error": failed_chapter.get("_error") or "下载失败"}
            item["download_status"] = state.get("status") or "pending"
            item["download_error"] = state.get("error") or item.get("_error") or ""
            item.setdefault("order_num", index)
            chapters.append(item)
    else:
        for index, chapter in enumerate(failed_by_key.values(), start=1):
            item = dict(chapter)
            item["download_status"] = "failed"
            item["download_error"] = item.get("_error") or "下载失败"
            item.setdefault("order_num", index)
            chapters.append(item)
    detail["chapters"] = chapters
    detail["detail_available"] = detail_available
    detail["counts"] = download_task_counts(task)
    detail.update(detail["counts"])
    detail.pop("success_chapters", None)
    detail.pop("chapter_states", None)
    detail.pop("failed_chapters", None)
    return detail


def album_library_summary(album):
    """Return subscription and cached local-download stats for an album."""
    normalized = normalize_album(album)
    sid = subscription_manager.subscription_id(normalized)
    total = max(0, _to_int(normalized.get("episodes") or normalized.get("chapter_count") or normalized.get("track_count")))
    with subscription_manager.locked():
        subscription = subscription_manager.get(sid)
        subscribed = bool(subscription and subscription.get("status", "active") == "active")
        stats = subscription_manager.stats_for(subscription, active_download_dir(), fast=True) if subscribed else {}
    total = max(total, _to_int(stats.get("total")))
    downloaded = min(total, max(0, _to_int(stats.get("downloaded")))) if total else max(0, _to_int(stats.get("downloaded")))
    restricted = min(total, max(0, _to_int(stats.get("restricted")))) if total else max(0, _to_int(stats.get("restricted")))
    return {
        "subscribed": subscribed,
        "subscription_id": sid,
        "subscription_quality": (
            subscription.get("subscription_quality") if subscribed else ""
        ) or (
            XMLY_WEB_SUBSCRIPTION_QUALITY
            if subscribed and canonical_subscription_platform(normalized.get("platform")) == "喜马拉雅"
            else ""
        ),
        "total": total,
        "downloaded": downloaded,
        "missing": max(0, total - downloaded - restricted),
        "restricted": restricted,
    }


def annotate_album_library(album):
    item = dict(album or {})
    item["library"] = album_library_summary(item)
    return item


def album_chapter_download_states(album):
    """Merge persisted subscription states with the newest matching task state."""
    normalized = normalize_album(album)
    sid = subscription_manager.subscription_id(normalized)
    merged = {}
    with subscription_manager.locked():
        subscription = subscription_manager.get(sid)
        if subscription and subscription.get("status", "active") == "active":
            for key, state in (subscription.get("downloaded") or {}).items():
                if not isinstance(state, dict):
                    continue
                merged[str(key)] = {
                    "status": state.get("status") or "pending",
                    "error": state.get("error") or state.get("reason") or "",
                }

    matching_tasks = []
    with task_lock:
        for task in tasks.values():
            task_album = task.get("album") or {}
            if subscription_manager.subscription_id(normalize_album(task_album)) == sid:
                matching_tasks.append(dict(task))
    matching_tasks.sort(key=lambda task: task.get("updated_at") or task.get("created_at") or 0)
    for task in matching_tasks:
        states = task.get("chapter_states") or {}
        success_keys = {
            chapter_key(chapter)
            for chapter in task.get("success_chapters") or []
            if isinstance(chapter, dict)
        }
        failed_by_key = {
            chapter_key(chapter): chapter
            for chapter in task.get("failed_chapters") or []
            if isinstance(chapter, dict)
        }
        is_active = task.get("status") in _ACTIVE_DOWNLOAD_STATUSES
        for chapter in task.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            key = chapter_key(chapter)
            state = states.get(key) or {}
            status = state.get("status")
            error = state.get("error") or ""
            if not status and key in success_keys:
                status = "success"
            elif not status and key in failed_by_key:
                status = "failed"
                error = failed_by_key[key].get("_error") or "下载失败"
            elif not status and is_active:
                status = "pending"
            if status and (is_active or key not in merged):
                merged[key] = {"status": status, "error": error}
    return merged


# 下载记录列表轻量化：列表页只需展示/操作用的小字段，绝不返回 album/chapters/
# success_chapters/failed_chapters 等重字段（单任务可达几百 KB，任务一多前端会白屏卡顿）。
def _download_list_item(task):
    album = task.get("album") or {}
    platform = album.get("platform") or (task.get("task_info") or {}).get("platform") or ""
    counts = download_task_counts(task)
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "platform": platform,
        "status": task.get("status"),
        "total": counts["total"],
        "completed": task.get("completed", 0),
        "percent": task.get("percent", 0),
        "success": counts["success"],
        "failed": counts["failed"],
        "downloading": counts["downloading"],
        "pending": counts["pending"],
        "source": task.get("source"),
        "warning": task.get("warning"),
        "error": task.get("error"),
        "failure_reason": task.get("failure_reason"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
    }


# status 过滤分组（对齐前端下载页的标签：活跃 / 已完成 / 失败-中断）
_DOWNLOAD_FILTER_GROUPS = {
    "active": {"running", "queued", "paused", "stopping"},
    "completed": {"completed"},
    "failed": {"failed", "partial", "interrupted", "stopped"},
}
# 终态任务（用于自动清理上限与分页排序）
_DOWNLOAD_TERMINAL_STATUSES = {"completed", "partial", "failed", "interrupted", "stopped"}


def _download_summary(snapshot):
    # 全局统计，喂给前端顶部 metrics 瓷砖（分页后前端只有当前页，统计必须由后端给）
    summary = {"total": len(snapshot), "active_count": 0, "completed_count": 0, "failed_count": 0, "interrupted_count": 0}
    for task in snapshot:
        status = task.get("status")
        if status in ("running", "queued", "paused", "stopping"):
            summary["active_count"] += 1
        elif status == "completed":
            summary["completed_count"] += 1
        elif status in ("failed", "partial"):
            summary["failed_count"] += 1
        elif status in ("interrupted", "stopped"):
            summary["interrupted_count"] += 1
    return summary


def paginate_downloads(snapshot, page=1, limit=20, status="all"):
    summary = _download_summary(snapshot)
    filter_set = _DOWNLOAD_FILTER_GROUPS.get(status)
    items = [t for t in snapshot if t.get("status") in filter_set] if filter_set else list(snapshot)
    items.sort(key=lambda t: t.get("created_at") or 0, reverse=True)  # 最新在前
    total = len(items)
    try:
        limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        limit = 20
    total_pages = max(1, (total + limit - 1) // limit)
    try:
        page = max(1, min(int(page), total_pages))
    except (TypeError, ValueError):
        page = 1
    start = (page - 1) * limit
    page_items = [_download_list_item(t) for t in items[start:start + limit]]
    return {
        "tasks": page_items,
        "pagination": {"page": page, "limit": limit, "total": total, "total_pages": total_pages},
        "summary": summary,
    }


def _compact_terminal_task(task, failure_limit=None):
    """Keep an actionable task summary without retaining whole album payloads."""
    if task.get("history_compacted"):
        return False
    failed = []
    for chapter in task.get("failed_chapters") or []:
        if not isinstance(chapter, dict):
            continue
        failed.append({key: chapter.get(key) for key in ("id", "track_id", "chapter_id", "title", "_error", "_error_type") if chapter.get(key) not in (None, "")})
        if len(failed) >= (failure_limit or task_history_settings()["task_failure_chapter_limit"]):
            break
    task["chapter_count"] = task.get("total") or len(task.get("chapters") or [])
    task["failed_chapters"] = failed
    task.pop("chapters", None)
    task.pop("success_chapters", None)
    task.pop("chapter_states", None)
    task.pop("task_info", None)
    task["history_compacted"] = True
    return True


def prune_tasks(max_keep=None):
    """Bound terminal task history by age, count, and retained detail size."""
    settings = task_history_settings()
    max_keep = settings["task_history_max_keep"] if max_keep is None else max_keep
    removed = 0
    compacted = 0
    now = time.time()
    delete_before = now - settings["task_history_max_age_days"] * 86400
    compact_before = now - settings["task_detail_retention_days"] * 86400
    with task_lock:
        terminal = [
            (tid, task) for tid, task in tasks.items()
            if task.get("status") in _DOWNLOAD_TERMINAL_STATUSES
        ]
        for tid, task in terminal:
            finished_at = float(task.get("finished_at") or task.get("created_at") or now)
            if finished_at < delete_before:
                tasks.pop(tid, None)
                removed += 1
            elif finished_at < compact_before and _compact_terminal_task(task, settings["task_failure_chapter_limit"]):
                compacted += 1
        terminal = [(tid, task) for tid, task in tasks.items() if task.get("status") in _DOWNLOAD_TERMINAL_STATUSES]
        if len(terminal) > max_keep:
            terminal.sort(key=lambda kv: kv[1].get("finished_at") or kv[1].get("created_at") or 0, reverse=True)
            for tid, _ in terminal[max_keep:]:
                tasks.pop(tid, None)
                removed += 1
        # A small number of very large albums can exceed the storage budget
        # before the count limit is reached. Compact oldest terminal records
        # first, then remove them only if summaries still exceed the cap.
        def snapshot_size():
            return len(json.dumps({"tasks": _json_safe(tasks)}, ensure_ascii=False).encode("utf-8"))

        if snapshot_size() > settings["task_history_max_bytes"]:
            terminal = sorted(
                ((tid, task) for tid, task in tasks.items() if task.get("status") in _DOWNLOAD_TERMINAL_STATUSES),
                key=lambda kv: kv[1].get("finished_at") or kv[1].get("created_at") or 0,
            )
            for _, task in terminal:
                if _compact_terminal_task(task, settings["task_failure_chapter_limit"]):
                    compacted += 1
                if snapshot_size() <= settings["task_history_max_bytes"]:
                    break
            if snapshot_size() > settings["task_history_max_bytes"]:
                for tid, _ in terminal:
                    if tid in tasks:
                        tasks.pop(tid, None)
                        removed += 1
                    if snapshot_size() <= settings["task_history_max_bytes"]:
                        break
    if removed or compacted:
        save_tasks(force=True)
    return removed, compacted


# 启动时清理一次历史积累（此处 tasks 已在模块加载时 load 完毕）
try:
    prune_tasks()
except Exception:
    logging.debug("startup prune_tasks failed", exc_info=True)


def start_download_task(task_id, album, chapters, options, source="web", origin_source=None):
    album = normalize_album(album)
    chapters = list(chapters or [])
    options = dict(options or {})
    album_id = str(album.get("id") or album.get("album_id") or album.get("book_id") or "")
    requested_album_id = str(album.get("requested_album_id") or "").strip()
    identity_error = ximalaya_album_identity_error(album)
    if identity_error:
        raise ValueError(identity_error)
    previous_task = task_snapshot(task_id)
    origin_source = str(
        origin_source or previous_task.get("origin_source") or previous_task.get("source") or source or ""
    ).strip()
    active_chapters = active_task_chapter_tasks(album)
    pending_chapters = [chapter for chapter in chapters if chapter_key(chapter) not in active_chapters]
    if not pending_chapters and active_chapters:
        existing = next(iter(active_chapters.values()))
        existing["deduplicated"] = True
        existing["skipped_active_chapter_count"] = len(chapters)
        return existing
    skipped_active_chapter_count = len(chapters) - len(pending_chapters)
    chapters = pending_chapters
    if album.get("platform") in ("喜马拉雅", "懒人听书"):
        sync_platform_cookie(album.get("platform"))
    options["download_dir"] = resolve_download_dir(options.get("download_dir"))
    _write_album_source_file(album, options, task_id)
    warning = str(options.get("warning") or "").strip()
    if not warning and album.get("platform") == "懒人听书":
        expected = _to_int(album.get("episodes"))
        if expected > 0 and len(chapters) < expected:
            warning = f"懒人听书目录可能未完整加载：当前任务 {len(chapters)}/{expected} 章。"
    quality = str(options.get("quality") or "M4A 96K")
    download_route = quality
    if album.get("platform") == "喜马拉雅" and quality == XMLY_WEB_SUBSCRIPTION_QUALITY:
        download_route = "旧版 V3 Level 2（仅在接口确认受限时切换网页授权接口）"
    with log_context(
        platform=album.get("platform") or "未知平台",
        operation="创建下载任务",
        task_id=task_id,
        album_id=album_id,
    ):
        log_event(
            "INFO",
            "下载任务身份与路由已确认",
            requested_album_id=requested_album_id or album_id,
            quality=quality,
            route=download_route,
            chapters=len(chapters),
            source=source,
        )
    set_task(
        task_id,
        status="queued",
        title=album.get("title"),
        album=album,
        chapters=chapters,
        options=options,
        source=source,
        origin_source=origin_source,
        organize_after_download=manual_download_origin(origin_source),
        total=len(chapters),
        completed=0,
        percent=0,
        warning=warning,
        skipped_active_chapter_count=skipped_active_chapter_count,
        success=0,
        failed=0,
        success_chapters=[],
        failed_chapters=[],
        chapter_states={},
        error="",
        failure_reason="",
        task_info={},
        started_at=None,
        finished_at=None,
        history_compacted=False,
        preparing=False,
        chapter_count=len(chapters),
        created_at=previous_task.get("created_at") or time.time(),
    )
    thread = threading.Thread(
        target=run_download_task,
        args=(task_id, album, chapters, options),
        name=f"download-{task_id}",
        daemon=True,
    )
    thread.start()
    return task_snapshot(task_id)


def refresh_subscription_audio_index_async(download_dir=None):
    target_dir = resolve_download_dir(download_dir or active_download_dir())

    def worker():
        try:
            subscription_manager.build_audio_index(target_dir, force=True)
        except Exception:
            logging.debug("refresh subscription audio index failed", exc_info=True)

    threading.Thread(target=worker, name="subscription-index-refresh", daemon=True).start()


_last_subscription_stats_refresh = 0.0
_subscription_stats_refresh_lock = threading.Lock()


def refresh_subscription_stats_async(min_interval=600):
    """后台异步刷新所有订阅的本地统计(local_stats)，带节流。

    /api/subscriptions?fast=1 只读 local_stats 缓存秒回，真正的下载目录扫描放这里异步做，
    扫完写回 local_stats，下次打开即生效——既保证订阅页秒开，又让「已下载/缺失」最终准确。
    """
    global _last_subscription_stats_refresh
    now = time.time()
    with _subscription_stats_refresh_lock:
        if now - _last_subscription_stats_refresh < min_interval:
            return
        _last_subscription_stats_refresh = now

    def worker():
        try:
            dl = active_download_dir()
            subscription_manager.build_audio_index(dl, force=True)  # 全盘扫描一次，建索引
            scan_cache = {}
            for item in subscription_manager.all_subscriptions():
                try:
                    with subscription_manager.locked():
                        subscription_manager.refresh_local_stats(item, dl, save=False, scan_cache=scan_cache)
                except Exception:
                    logging.debug("refresh local stats failed for one subscription", exc_info=True)
            with subscription_manager.locked():
                subscription_manager.save()  # 批量持久化一次
        except Exception:
            logging.debug("refresh subscription stats failed", exc_info=True)

    threading.Thread(target=worker, name="subscription-stats-refresh", daemon=True).start()


def handle_download_completed(task_id, success, failed, success_chapters, failed_chapters):
    current = task_snapshot(task_id)
    status = "stopped" if current.get("status") == "stopping" else ("completed" if failed == 0 else "partial")
    album = current.get("album") or {}
    failure_reason = classify_failure_reason(current.get("error", ""), failed_chapters) if failed else ""
    if album:
        with subscription_manager.locked():
            subscription_manager.mark_download_results(album, success_chapters, failed_chapters)
            completed_dir = resolve_download_dir((current.get("options") or {}).get("download_dir"))
            # Invalidate synchronously so a check that starts immediately after
            # completion cannot reuse the pre-download empty index.
            subscription_manager.invalidate_audio_index(completed_dir)
        refresh_subscription_audio_index_async(completed_dir)
    final_states = dict(current.get("chapter_states") or {})
    for chapter in success_chapters or []:
        final_states[chapter_key(chapter)] = {"status": "success", "updated_at": time.time()}
    for chapter in failed_chapters or []:
        final_states[chapter_key(chapter)] = {
            "status": "failed",
            "error": str((chapter or {}).get("_error") or "下载失败")[:500],
            "updated_at": time.time(),
        }
    final_states = {
        key: state for key, state in final_states.items()
        if (state or {}).get("status") != "downloading"
    }
    task = set_task(
        task_id,
        status=status,
        success=success,
        failed=failed,
        success_chapters=success_chapters,
        failed_chapters=failed_chapters,
        chapter_states=final_states,
        failure_reason=failure_reason,
        # 完成(非中途停止)时把进度拉满，避免过程中某次进度上报丢失导致进度条停在中途
        completed=current.get("total") if status != "stopped" else current.get("completed", success + failed),
        percent=100 if status != "stopped" else current.get("percent", 0),
        finished_at=time.time(),
    )
    append_background_event(
        "download",
        ("下载完成" if status == "completed" else "下载部分完成" if status == "partial" else "下载停止"),
        f"{task.get('title') or task_id} 成功 {success} 章，失败 {failed} 章" + (f"，原因：{failure_reason}" if failure_reason else ""),
        {"task_id": task_id, "status": status, "success": success, "failed": failed, "failure_reason": failure_reason},
    )
    if status in ("completed", "partial"):
        scene = "download_completed" if status == "completed" else "download_failed"
        title = "下载完成" if status == "completed" else "下载部分完成"
        notification_manager.notify(
            scene,
            f"{title}：{task.get('title') or task_id}",
            f"平台：{album.get('platform') or '-'}\n成功：{success} 章\n失败：{failed} 章\n任务：{task_id}",
            {"task": task, "album": album, "success": success, "failed": failed},
        )
    if status == "completed":
        schedule_rename_plan(task_id)
    prune_tasks()  # 任务进入终态后清理超量历史记录，防止无限堆积
    return task


def run_download_task(task_id, album, chapters, options):
    normalized_album = normalize_album(album)
    platform = normalized_album.get("platform") or "未知平台"
    album_id = (
        normalized_album.get("id")
        or normalized_album.get("album_id")
        or normalized_album.get("book_id")
        or ""
    )
    with log_context(
        platform=platform,
        operation="下载任务",
        task_id=task_id,
        album_id=album_id,
        chapters=len(chapters or []),
    ):
        log_event("INFO", "下载任务开始")
        try:
            return _run_download_task(task_id, normalized_album, chapters, options)
        finally:
            current = task_snapshot(task_id) or {}
            log_event(
                "INFO" if current.get("status") == "completed" else "WARN",
                "下载任务结束",
                status=current.get("status") or "unknown",
                success=current.get("success", 0),
                failed=current.get("failed", 0),
            )


def _run_download_task(task_id, album, chapters, options):
    album = normalize_album(album)
    chapters = list(chapters or [])
    options = dict(options or {})
    warning = str(options.get("warning") or "").strip()
    if not warning and album.get("platform") == "懒人听书":
        expected = _to_int(album.get("episodes"))
        if expected > 0 and len(chapters) < expected:
            warning = f"懒人听书目录可能未完整加载：当前任务 {len(chapters)}/{expected} 章。"
    set_task(
        task_id,
        status="running",
        title=album.get("title"),
        album=album,
        chapters=chapters,
        options=options,
        total=len(chapters),
        completed=0,
        percent=0,
        warning=warning,
        started_at=time.time(),
    )
    worker = None
    try:
        worker = DownloadWorker(
            chapters=chapters,
            download_dir=resolve_download_dir(options.get("download_dir")),
            quality=options.get("quality") or "M4A 96K",
            album_title=album.get("title") or "未知专辑",
            album_id=str(album.get("id") or album.get("album_id") or album.get("book_id") or ""),
            platform=album.get("platform") or "",
            task_id=task_id,
            search_manager=search_manager,
            voice_config=options.get("voice"),
        )
        with task_lock:
            task_workers[task_id] = worker
        worker.progress_updated.connect(lambda _tid, current, total: set_task(task_id, completed=current, total=total))
        worker.realtime_progress_updated.connect(lambda _tid, completed, total, percent: set_task(task_id, completed=completed, total=total, percent=percent))
        worker.task_info_updated.connect(lambda _tid, info: set_task(task_id, task_info=info))
        worker.chapter_status_updated.connect(
            lambda _tid, chapter, status: update_download_chapter_status(task_id, chapter, status)
        )
        worker.download_completed.connect(
            lambda _tid, success, failed, success_chapters, failed_chapters: handle_download_completed(
                task_id, success, failed, success_chapters, failed_chapters
            )
        )
        worker.run()
    except Exception as exc:
        failure_reason = classify_failure_reason(str(exc), [])
        task = set_task(task_id, status="failed", error=str(exc), failure_reason=failure_reason, finished_at=time.time())
        append_background_event(
            "download",
            "下载失败",
            f"{task.get('title') or task_id}：{failure_reason}",
            {"task_id": task_id, "error": str(exc), "failure_reason": failure_reason},
        )
        notification_manager.notify(
            "download_failed",
            f"下载失败：{task.get('title') or task_id}",
            f"错误：{exc}\n任务：{task_id}",
            {"task": task, "error": str(exc)},
        )
        logging.exception("download task failed: %s", task_id)
    finally:
        with task_lock:
            task_workers.pop(task_id, None)
        current = task_snapshot(task_id)
        if current and current.get("status") in ("running", "queued", "stopping"):
            set_task(task_id, status="stopped", finished_at=time.time())


@app.get("/health")
def health():
    return json_ok(app=APP_NAME, version=APP_VERSION, mode="server")


@app.get("/api/config")
def api_config():
    return json_ok(
        app=APP_NAME,
        version=APP_VERSION,
        config_dir=str(config_dir()),
        data_dir=str(data_dir()),
        download_dir=str(active_download_dir()),
        log_dir=str(log_dir()),
        pwa_enabled=pwa_enabled(),
        auth_required=True,
        auth_user=(current_user() or {}).get("username", ""),
        cookie_encryption_enabled=bool(getattr(cookie_manager, "encryption_enabled", False)),
        download_threads=cookie_manager.get_download_threads(),
        quality=subscription_manager.settings().get("quality", "M4A 96K"),
        organize_by_platform_enabled=cookie_manager.get_cookie("organize_by_platform_enabled") == "true",
        split_chapters_enabled=cookie_manager.get_cookie("split_chapters_enabled") == "true",
        chapters_per_folder=int_cookie_setting("chapters_per_folder", 200),
        filename_prefix_format=cookie_manager.get_cookie("filename_prefix_format") or "0001-",
        manual_organize_mode=manual_organize_mode(),
        background_events_max_keep=background_events_max_keep(),
        **task_history_settings(),
    )


@app.post("/api/config")
def api_set_config():
    """保存系统设置：下载目录、音质、并发线程数"""
    payload = request.get_json(silent=True) or {}
    if "download_dir" in payload:
        cookie_manager.set_download_dir(payload["download_dir"])
    if "quality" in payload and str(payload.get("quality") or "").strip():
        subscription_manager.update_settings(quality=str(payload["quality"]).strip())
    if "download_threads" in payload:
        try:
            threads = max(1, min(64, int(payload["download_threads"])))
            cookie_manager.set_cookie("download_threads", str(threads))
        except (ValueError, TypeError):
            pass
    if "organize_by_platform_enabled" in payload:
        cookie_manager.set_cookie("organize_by_platform_enabled", "true" if payload.get("organize_by_platform_enabled") else "false")
    if "split_chapters_enabled" in payload:
        cookie_manager.set_cookie("split_chapters_enabled", "true" if payload.get("split_chapters_enabled") else "false")
    if "chapters_per_folder" in payload:
        try:
            count = max(1, min(10000, int(payload["chapters_per_folder"])))
            cookie_manager.set_cookie("chapters_per_folder", str(count))
        except (ValueError, TypeError):
            pass
    if "filename_prefix_format" in payload:
        fmt = str(payload.get("filename_prefix_format") or "0001-").strip()
        allowed = {"0001-", "001-", "01-", "1-", "0001.", "001.", "01.", "1.", "none"}
        cookie_manager.set_cookie("filename_prefix_format", fmt if fmt in allowed else "0001-")
    if "manual_organize_mode" in payload:
        mode = str(payload.get("manual_organize_mode") or "off").strip()
        cookie_manager.set_cookie(
            "manual_organize_mode", mode if mode in MANUAL_ORGANIZE_MODES else "off"
        )
    task_setting_limits = {
        "task_history_max_keep": (10, 10000),
        "task_history_max_age_days": (1, 3650),
        "task_detail_retention_days": (0, 3650),
        "task_failure_chapter_limit": (1, 1000),
        "task_history_max_bytes": (1024 * 1024, 1024 * 1024 * 1024),
    }
    if any(key in payload for key in task_setting_limits):
        for key, (minimum, maximum) in task_setting_limits.items():
            if key not in payload:
                continue
            try:
                value = max(minimum, min(maximum, int(payload[key])))
                cookie_manager.set_cookie(key, str(value))
            except (TypeError, ValueError):
                pass
        prune_tasks()
    if "background_events_max_keep" in payload:
        try:
            value = max(10, min(5000, int(payload["background_events_max_keep"])))
            cookie_manager.set_cookie("background_events_max_keep", str(value))
            prune_background_events(value)
        except (TypeError, ValueError):
            pass
    return json_ok(
        download_dir=str(active_download_dir()),
        download_threads=cookie_manager.get_download_threads(),
        quality=subscription_manager.settings().get("quality", "M4A 96K"),
        organize_by_platform_enabled=cookie_manager.get_cookie("organize_by_platform_enabled") == "true",
        split_chapters_enabled=cookie_manager.get_cookie("split_chapters_enabled") == "true",
        chapters_per_folder=int_cookie_setting("chapters_per_folder", 200),
        filename_prefix_format=cookie_manager.get_cookie("filename_prefix_format") or "0001-",
        manual_organize_mode=manual_organize_mode(),
        background_events_max_keep=background_events_max_keep(),
        **task_history_settings(),
    )


_NOTIFICATION_SECRET_KEYS = {
    "token",
    "bot_token",
    "send_key",
    "key",
    "url",
    "secret",
    "app_secret",
    "encoding_aes_key",
}


def _merge_notification_secrets(payload):
    current = notification_manager.load()
    by_id = {item.get("id"): item for item in current.get("services") or []}
    services = []
    for item in payload.get("services") or []:
        if not isinstance(item, dict):
            continue
        service = dict(item)
        config = dict(service.get("config") or {})
        old_config = dict((by_id.get(service.get("id")) or {}).get("config") or {})
        for key in _NOTIFICATION_SECRET_KEYS:
            if (key not in config or not str(config.get(key) or "").strip()) and old_config.get(key):
                config[key] = old_config[key]
        service["config"] = config
        services.append(service)
    merged = {
        "enabled": bool(payload.get("enabled", False)),
        "scenes": payload.get("scenes") or {},
        "services": services,
    }
    return merged


def _merge_notification_service_secrets(service):
    service = dict(service or {})
    current = notification_manager.load()
    by_id = {item.get("id"): item for item in current.get("services") or []}
    config = dict(service.get("config") or {})
    old_config = dict((by_id.get(service.get("id")) or {}).get("config") or {})
    for key in _NOTIFICATION_SECRET_KEYS:
        if (key not in config or not str(config.get(key) or "").strip()) and old_config.get(key):
            config[key] = old_config[key]
    service["config"] = config
    return service


@app.get("/api/notifications")
def api_notifications():
    return json_ok(config=notification_manager.public_config())


@app.post("/api/notifications")
def api_save_notifications():
    payload = request.get_json(silent=True) or {}
    config = notification_manager.save(_merge_notification_secrets(payload))
    feishu_bridge.start()
    return json_ok(config=notification_manager.public_config(), saved_at=config.get("updated_at"))


@app.post("/api/notifications/test")
def api_test_notifications():
    payload = request.get_json(silent=True) or {}
    try:
        service = payload.get("service")
        if isinstance(service, dict):
            result = notification_manager.test(service=_merge_notification_service_secrets(service))
        else:
            result = notification_manager.test(payload.get("service_id") or payload.get("serviceId"))
        return json_ok(result=result)
    except Exception as exc:
        return json_error(str(exc), 400)


@app.get("/api/rename-plans")
def api_rename_plans():
    return json_ok(plans=rename_plan_manager.list(request.args.get("status") or None))


def _rename_folder_entries():
    root = Path(resolve_download_dir()).resolve()
    folders = []
    if not root.exists() or not root.is_dir():
        return folders
    for path in root.rglob("*"):
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        if not relative.parts or len(relative.parts) > 3:
            continue
        if any(part == ".audioflow-trash" or part.startswith(".") for part in relative.parts):
            continue
        count = sum(
            1 for child in path.iterdir()
            if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS
        )
        if count:
            folders.append({
                "relative_path": relative.as_posix(),
                "name": path.name,
                "audio_count": count,
            })
        if len(folders) >= 500:
            break
    return sorted(folders, key=lambda item: item["relative_path"].casefold())[:500]


def create_rename_plan_for_folder(relative_path, album_title=None, *, notify=True, replace=False):
    raw_path = str(relative_path or "").strip().replace("\\", "/")
    if not raw_path or Path(raw_path).is_absolute():
        raise ValueError("请提供下载目录内的相对文件夹路径")
    root = Path(resolve_download_dir()).resolve()
    target = (root / Path(raw_path)).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("整理路径必须位于下载目录内") from exc
    if not relative.parts or any(part == ".audioflow-trash" or part.startswith(".") for part in relative.parts):
        raise ValueError("不能整理隐藏目录或隔离目录")
    if not target.exists() or not target.is_dir():
        raise ValueError("目标文件夹不存在")
    task_id = f"folder:{relative.as_posix()}"
    existing = next((
        item for item in rename_plan_manager.list()
        if (item.get("task_id") == task_id
            or Path(item.get("album_dir") or "").resolve() == target)
        and item.get("status") not in {"cancelled", "expired", "failed"}
    ), None)
    if existing:
        if not replace:
            return existing
        if existing.get("status") in {"executing", "completed"}:
            raise ValueError("该文件夹已有已执行或正在执行的整理计划")
        rename_plan_manager.cancel(existing.get("id"))
    title = str(album_title or target.name).strip() or target.name
    plan = rename_plan_manager.create_plan(
        task_id=task_id,
        album={"title": title, "platform": "", "id": ""},
        chapters=[],
        album_dir=target,
        origin_source="manual",
    )
    if notify:
        _notify_rename_plan(plan, task_id)
    return plan


@app.get("/api/rename-plans/folders")
def api_rename_plan_folders():
    return json_ok(folders=_rename_folder_entries())


@app.post("/api/rename-plans/analyze-folder")
def api_analyze_rename_folder():
    payload = request.get_json(silent=True) or {}
    try:
        plan = create_rename_plan_for_folder(
            payload.get("relative_path") or payload.get("relativePath"),
            payload.get("album_title") or payload.get("albumTitle"),
            notify=True,
            replace=bool(payload.get("replace")),
        )
        return json_ok(plan=plan)
    except (ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.get("/api/rename-rules")
def api_rename_rules():
    return json_ok(
        packs=rename_rule_store.list(),
        effective=rename_rule_store.effective({}),
    )


@app.post("/api/rename-rules/drafts")
def api_save_rename_rule_draft():
    try:
        return json_ok(rule=rename_rule_store.save_draft(request.get_json(silent=True) or {}))
    except (TypeError, ValueError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-rules/<rule_id>/activate")
def api_activate_rename_rule(rule_id):
    try:
        return json_ok(rule=rename_rule_store.activate(rule_id), packs=rename_rule_store.list())
    except KeyError as exc:
        return json_error(str(exc), 404)
    except ValueError as exc:
        return json_error(str(exc), 400)


@app.delete("/api/rename-rules/<rule_id>")
def api_delete_rename_rule(rule_id):
    try:
        if not rename_rule_store.delete_draft(rule_id):
            return json_error("重命名规则不存在", 404)
        return json_ok(deleted=True, packs=rename_rule_store.list())
    except ValueError as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-rules/test")
def api_test_rename_rules():
    payload = request.get_json(silent=True) or {}
    try:
        return json_ok(results=preview_rule_samples(
            payload.get("rules") or {},
            payload.get("samples") or [],
            str(payload.get("album_title") or "示例书名"),
        ))
    except (TypeError, ValueError) as exc:
        return json_error(str(exc), 400)


@app.get("/api/rename-plans/<plan_id>")
def api_rename_plan(plan_id):
    plan = rename_plan_manager.get(plan_id)
    if not plan:
        return json_error("重命名计划不存在", 404)
    return json_ok(plan=plan)


@app.post("/api/rename-plans/analyze")
def api_analyze_rename_plan():
    payload = request.get_json(silent=True) or {}
    try:
        plan = create_rename_plan_for_task(
            str(payload.get("task_id") or payload.get("taskId") or ""),
            notify=False,
            replace=bool(payload.get("replace")),
        )
        return json_ok(plan=plan)
    except KeyError as exc:
        return json_error(str(exc), 404)
    except (ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/review")
def api_review_rename_plan(plan_id):
    try:
        return json_ok(plan=rename_plan_manager.configure(
            plan_id, request.get_json(silent=True) or {}
        ))
    except KeyError as exc:
        return json_error(str(exc), 404)
    except (TypeError, ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/resolve-safe")
def api_resolve_safe_rename_plan(plan_id):
    try:
        return json_ok(plan=rename_plan_manager.resolve_safe(plan_id))
    except KeyError as exc:
        return json_error(str(exc), 404)
    except (TypeError, ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/ai-analyze")
def api_ai_analyze_rename_plan(plan_id):
    plan = rename_plan_manager.get(plan_id)
    if not plan:
        return json_error("重命名计划不存在", 404)
    try:
        analysis = agent_manager.analyze_rename_plan(plan)
        return json_ok(plan=rename_plan_manager.save_ai_analysis(plan_id, analysis))
    except (TypeError, ValueError, requests.RequestException) as exc:
        return json_error(str(exc), 400)


def _full_clean_entries(plan):
    chapters = sorted(
        [item for item in (plan.get("items") or []) if item.get("kind") == "chapter"],
        key=lambda item: (item.get("sequence", 0), item.get("chapter", 0)),
    )
    entries = []
    for index, item in enumerate(chapters):
        neighbors = []
        if index:
            neighbors.append(chapters[index - 1].get("source_name") or "")
        if index + 1 < len(chapters):
            neighbors.append(chapters[index + 1].get("source_name") or "")
        entries.append({
            "relative_source": item.get("relative_source") or item.get("source_name") or "",
            "source_name": item.get("source_name") or "",
            "current_title": item.get("clean_title") or "",
            "chapter": item.get("chapter"),
            "unit": item.get("original_unit") or (plan.get("configuration") or {}).get("chapter_unit") or "集",
            "neighbors": neighbors,
        })
    return entries


def _run_full_clean(plan_id):
    try:
        plan = rename_plan_manager.get(plan_id)
        if not plan:
            return
        entries = _full_clean_entries(plan)
        total = len(entries)
        _provider_id, _spec, model_config = agent_manager._validate_ready()
        existing_state = plan.get("ai_clean") or {}
        existing = {
            str(item.get("relative_source") or ""): dict(item)
            for item in existing_state.get("suggestions") or []
            if isinstance(item, dict) and item.get("relative_source")
        }
        keys = {entry["relative_source"] for entry in entries}
        existing = {key: value for key, value in existing.items() if key in keys}
        started_at = int(existing_state.get("started_at") or time.time())
        rename_plan_manager.update_ai_clean(plan_id, {
            "status": "running", "done": len(existing), "total": total,
            "model": model_config["model"], "started_at": started_at,
        })
        remaining = [entry for entry in entries if entry["relative_source"] not in existing]
        for offset in range(0, len(remaining), 40):
            batch = remaining[offset:offset + 40]
            result = agent_manager.clean_titles_batch(
                plan.get("album") or {},
                (plan.get("rule_snapshot") or {}).get("rules") or {},
                batch,
                max_tokens=4096,
            )
            returned = {
                str(item.get("relative_source") or ""): dict(item)
                for item in result.get("suggestions") or []
                if isinstance(item, dict) and item.get("relative_source")
            }
            for entry in batch:
                key = entry["relative_source"]
                suggestion = returned.get(key) or {
                    "relative_source": key,
                    "clean_title": entry.get("current_title") or "",
                    "changed": False,
                    "action": "keep",
                    "reason": "AI 未返回该条建议，保留规则引擎结果",
                    "confidence": 0,
                }
                suggestion["relative_source"] = key
                suggestion.setdefault("action", "rename" if suggestion.get("changed") else "keep")
                existing[key] = suggestion
            rename_plan_manager.update_ai_clean(plan_id, {
                "status": "running", "done": len(existing), "total": total,
                "model": model_config["model"], "suggestions": list(existing.values()),
            })
        ordered_suggestions = [existing[entry["relative_source"]] for entry in entries]
        saved = rename_plan_manager.save_ai_analysis(plan_id, {
            "mode": "full_clean",
            "suggestions": ordered_suggestions,
            "summary": "已完成全部章节的 AI 清洗建议，建议仍需逐项勾选并最终确认。",
            "model": model_config["model"],
        })
        completed = rename_plan_manager.update_ai_clean(plan_id, {
            "status": "completed", "done": total, "total": total,
            "model": model_config["model"], "suggestions": ordered_suggestions,
            "completed_at": int(time.time()),
        })
        notification_manager.notify(
            "rename_confirmation",
            f"全量 AI 清洗完成：{(saved.get('album') or {}).get('title') or plan_id}",
            f"计划 {plan_id} 已生成 {total} 条全量清洗建议，请在 AudioFlow 中勾选并应用后再确认执行。",
            {"title": (saved.get("album") or {}).get("title") or "", "plan_id": plan_id,
             "plan_status": saved.get("status"), "planned": total, "issues": 0,
             "task_id": saved.get("task_id") or ""},
        )
        return completed
    except Exception as exc:
        logging.exception("full audiobook AI cleaning failed: %s", plan_id)
        try:
            current = rename_plan_manager.get(plan_id) or {}
            state = current.get("ai_clean") or {}
            rename_plan_manager.update_ai_clean(plan_id, {
                "status": "failed", "done": int(state.get("done") or 0),
                "total": int(state.get("total") or 0), "error": str(exc),
                "failed_at": int(time.time()),
            })
        except Exception:
            logging.exception("failed to persist full audiobook AI cleaning error: %s", plan_id)


@app.post("/api/rename-plans/<plan_id>/ai-clean")
def api_ai_clean_rename_plan(plan_id):
    plan = rename_plan_manager.get(plan_id)
    if not plan:
        return json_error("重命名计划不存在", 404)
    if plan.get("status") not in {"needs_review", "pending_confirmation"}:
        return json_error("只有待复核或待确认计划可以进行全量 AI 清洗", 400)
    state = plan.get("ai_clean") or {}
    if state.get("status") == "running":
        return json_ok(plan=plan)
    if state.get("status") == "completed":
        return json_ok(plan=plan)
    try:
        _provider_id, _spec, model_config = agent_manager._validate_ready()
    except (TypeError, ValueError, requests.RequestException) as exc:
        return json_error(f"请先去 Agent 设置配置模型密钥：{exc}", 400)
    entries = _full_clean_entries(plan)
    started_at = int(state.get("started_at") or time.time())
    plan = rename_plan_manager.update_ai_clean(plan_id, {
        "status": "running", "done": int(state.get("done") or 0),
        "total": len(entries), "model": model_config["model"], "started_at": started_at,
    })
    threading.Thread(
        target=_run_full_clean, args=(plan_id,), name=f"rename-ai-clean-{plan_id}", daemon=True
    ).start()
    return json_ok(plan=plan)


@app.post("/api/rename-plans/<plan_id>/ai-apply")
def api_apply_ai_rename_suggestions(plan_id):
    payload = request.get_json(silent=True) or {}
    try:
        return json_ok(plan=rename_plan_manager.apply_ai_suggestions(
            plan_id, payload.get("suggestion_ids") or []
        ))
    except KeyError as exc:
        return json_error(str(exc), 404)
    except (TypeError, ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/ai-rule-draft")
def api_create_ai_rename_rule_draft(plan_id):
    plan = rename_plan_manager.get(plan_id)
    if not plan:
        return json_error("重命名计划不存在", 404)
    try:
        proposed = agent_manager.propose_rename_rule_draft(plan)
        effective = rename_rule_store.effective(plan.get("album") or {})["rules"]
        proposed["rules"] = merge_rule_values(effective, proposed.get("rules") or {})
        proposed["source"] = f"agent:{plan_id}"
        return json_ok(rule=rename_rule_store.save_draft(proposed), packs=rename_rule_store.list())
    except (TypeError, ValueError, requests.RequestException) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/confirm")
def api_confirm_rename_plan(plan_id):
    try:
        return json_ok(plan=rename_plan_manager.confirm(plan_id))
    except KeyError as exc:
        return json_error(str(exc), 404)
    except (ValueError, OSError) as exc:
        return json_error(str(exc), 400)


@app.post("/api/rename-plans/<plan_id>/cancel")
def api_cancel_rename_plan(plan_id):
    try:
        return json_ok(plan=rename_plan_manager.cancel(plan_id))
    except KeyError as exc:
        return json_error(str(exc), 404)
    except ValueError as exc:
        return json_error(str(exc), 400)


def _agent_list_downloads(status="all", limit=20):
    status = str(status or "all")
    limit = max(1, min(50, int(limit or 20)))
    with task_lock:
        snapshot = [dict(item) for item in tasks.values()]
    snapshot.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    if status == "active":
        snapshot = [item for item in snapshot if item.get("status") in {"queued", "preparing", "downloading", "paused"}]
    elif status != "all":
        snapshot = [item for item in snapshot if item.get("status") == status]
    return {
        "tasks": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "platform": (item.get("album") or {}).get("platform"),
                "status": item.get("status"),
                "success": item.get("success", 0),
                "failed": item.get("failed", 0),
                "total": item.get("total", 0),
                "finished_at": item.get("finished_at"),
            }
            for item in snapshot[:limit]
        ]
    }


def _agent_list_rename_plans(status=""):
    plans = rename_plan_manager.list(str(status or "") or None)
    return {
        "plans": [
            {
                "id": item.get("id"),
                "task_id": item.get("task_id"),
                "title": (item.get("album") or {}).get("title"),
                "status": item.get("status"),
                "summary": item.get("summary") or {},
            }
            for item in plans[:50]
        ]
    }


def _agent_get_rename_plan(plan_id):
    plan = rename_plan_manager.get(str(plan_id or ""))
    if not plan:
        raise KeyError("重命名计划不存在")
    return plan


def _agent_create_rename_plan(task_id="", folder="", album_title=""):
    if folder:
        plan = create_rename_plan_for_folder(folder, album_title or None, notify=True)
    else:
        plan = create_rename_plan_for_task(str(task_id or ""), notify=True)
    return {
        "id": plan.get("id"),
        "task_id": plan.get("task_id"),
        "status": plan.get("status"),
        "summary": plan.get("summary") or {},
        "confirmation_required": True,
        "confirmation_command": f"确认重命名 {plan.get('id')}",
        "message": "计划已保存并发送通知；必须由用户明确确认后才会执行。",
    }


def _agent_analyze_rename_plan_with_ai(plan_id):
    plan = rename_plan_manager.get(str(plan_id or ""))
    if not plan:
        raise KeyError("重命名计划不存在")
    analysis = agent_manager.analyze_rename_plan(plan)
    saved = rename_plan_manager.save_ai_analysis(str(plan_id), analysis)
    return {
        "id": saved.get("id"),
        "status": saved.get("status"),
        "suggestions": len((saved.get("ai_analysis") or {}).get("suggestions") or []),
        "message": "AI 风险建议已保存到计划，尚未应用或执行。",
    }


def _agent_apply_ai_rename_suggestions(plan_id, suggestion_ids):
    plan = rename_plan_manager.apply_ai_suggestions(str(plan_id or ""), suggestion_ids or [])
    return {
        "id": plan.get("id"), "status": plan.get("status"),
        "summary": plan.get("summary") or {},
        "message": "选中的 AI 建议已写入计划；仍需用户最终确认后才会执行。",
    }


def _agent_create_rename_rule_draft(plan_id):
    plan = rename_plan_manager.get(str(plan_id or ""))
    if not plan:
        raise KeyError("重命名计划不存在")
    proposed = agent_manager.propose_rename_rule_draft(plan)
    effective = rename_rule_store.effective(plan.get("album") or {})["rules"]
    proposed["rules"] = merge_rule_values(effective, proposed.get("rules") or {})
    proposed["source"] = f"agent:{plan_id}"
    rule = rename_rule_store.save_draft(proposed)
    return {
        "id": rule.get("id"), "name": rule.get("name"), "status": rule.get("status"),
        "message": "规则草稿已生成；必须在规则中心测试并手动启用。",
    }


def _agent_resolve_rename_plan_safe(plan_id):
    plan = rename_plan_manager.resolve_safe(str(plan_id or ""))
    return {
        "id": plan.get("id"),
        "status": plan.get("status"),
        "summary": plan.get("summary") or {},
        "message": "风险和特殊文件将保持不动；安全章节已进入最终确认。",
    }


def _agent_confirm_rename_plan(plan_id):
    plan = rename_plan_manager.confirm(str(plan_id or ""))
    return {
        "id": plan.get("id"),
        "status": plan.get("status"),
        "summary": plan.get("summary") or {},
        "mapping_file": plan.get("mapping_file"),
        "message": "AudioFlow 服务端已完成两阶段整理和结果校验。",
    }


def _agent_cancel_rename_plan(plan_id):
    plan = rename_plan_manager.cancel(str(plan_id or ""))
    return {"id": plan.get("id"), "status": plan.get("status"), "message": "整理计划已取消。"}


agent_manager.set_tools({
    "list_downloads": _agent_list_downloads,
    "list_rename_plans": _agent_list_rename_plans,
    "get_rename_plan": _agent_get_rename_plan,
    "create_rename_plan": _agent_create_rename_plan,
    "analyze_rename_plan_with_ai": _agent_analyze_rename_plan_with_ai,
    "apply_ai_rename_suggestions": _agent_apply_ai_rename_suggestions,
    "create_rename_rule_draft": _agent_create_rename_rule_draft,
    "resolve_rename_plan_safe": _agent_resolve_rename_plan_safe,
    "confirm_rename_plan": _agent_confirm_rename_plan,
    "cancel_rename_plan": _agent_cancel_rename_plan,
})

feishu_bridge = FeishuBridge(
    notification_manager,
    agent_manager,
    rename_plan_manager,
    config_dir() / "feishu_actions.json",
)
developer_agent_manager = DeveloperAgentManager(
    agent_manager,
    notification_manager,
    config_dir(),
    project_root() / "developer-agent",
)


@app.get("/api/agent/status")
def api_agent_status():
    return json_ok(**agent_manager.status(), developer=developer_agent_manager.status())


@app.post("/api/agent/config")
def api_agent_config():
    try:
        config = agent_manager.store.save_config(request.get_json(silent=True) or {})
        developer = developer_agent_manager.reconcile()
        return json_ok(config=config, developer=developer)
    except ValueError as exc:
        return json_error(str(exc), 400)


@app.get("/api/agent/developer/status")
def api_developer_agent_status():
    return json_ok(status=developer_agent_manager.status())


@app.post("/api/agent/developer/start")
def api_developer_agent_start():
    try:
        return json_ok(status=developer_agent_manager.start())
    except ValueError as exc:
        return json_error(str(exc), 400)


@app.post("/api/agent/developer/stop")
def api_developer_agent_stop():
    return json_ok(status=developer_agent_manager.stop())


@app.post("/api/agent/test")
def api_agent_test():
    payload = request.get_json(silent=True) or {}
    try:
        return json_ok(result=agent_manager.test_provider(payload.get("provider")))
    except (ValueError, requests.RequestException) as exc:
        return json_error(str(exc), 400)


@app.get("/api/agent/sessions")
def api_agent_sessions():
    return json_ok(sessions=agent_manager.store.list_sessions())


@app.get("/api/agent/sessions/<session_id>")
def api_agent_session(session_id):
    session = agent_manager.store.get_session(session_id)
    if not session:
        return json_error("Agent 会话不存在", 404)
    return json_ok(session=session)


@app.delete("/api/agent/sessions/<session_id>")
def api_delete_agent_session(session_id):
    if not agent_manager.store.delete_session(session_id):
        return json_error("Agent 会话不存在", 404)
    return json_ok(deleted=True)


@app.post("/api/agent/chat")
def api_agent_chat():
    payload = request.get_json(silent=True) or {}
    try:
        return json_ok(**agent_manager.chat(
            payload.get("message") or payload.get("content"),
            payload.get("session_id") or payload.get("sessionId"),
        ))
    except (ValueError, requests.RequestException) as exc:
        return json_error(str(exc), 400)


def _notification_service(service_id, service_type=None):
    for service in notification_manager.load().get("services") or []:
        if service.get("id") == service_id and (service_type is None or service.get("type") == service_type):
            return service
    return None


def _wecom_config_for_callback(service_id):
    service = _notification_service(service_id, "wecom_app")
    if not service:
        raise ValueError("企业微信应用通知渠道不存在")
    config = dict(service.get("config") or {})
    missing = [key for key in ("corp_id", "agent_id", "secret", "token", "encoding_aes_key") if not str(config.get(key) or "").strip()]
    if missing:
        raise ValueError("企业微信应用回调配置不完整：" + "、".join(missing))
    return service, config


WECOM_PLATFORM_NAMES = {
    "喜马拉雅", "懒人听书", "番茄畅听", "番茄听书", "网易云听书", "荔枝FM",
    "七猫听书", "蜻蜓FM", "云听FM", "起点听书", "酷我听书",
}


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return ""


def _wecom_render(template, **kwargs):
    """安全渲染模板：用 {name} 占位，缺失变量按空字符串处理，渲染异常回退原文。"""
    try:
        return str(template or "").format_map(_SafeFormatDict(kwargs))
    except Exception:
        return str(template or "")


# 模板字段元数据（供前端配置界面展示标签与可用变量）
WECOM_TEMPLATE_FIELDS = [
    {"key": "search_item_title", "label": "搜索结果·卡片标题", "vars": ["index", "title", "platform", "author", "episodes", "keyword"]},
    {"key": "search_item_desc", "label": "搜索结果·卡片描述", "vars": ["index", "title", "platform", "author", "episodes", "keyword"]},
    {"key": "subscribe_title", "label": "订阅结果·标题", "vars": ["title", "episodes", "job_suffix"]},
    {"key": "subscribe_desc", "label": "订阅结果·描述", "vars": ["title", "episodes", "job_suffix"]},
    {"key": "download_title", "label": "下载结果·标题", "vars": ["title", "episodes", "task_id"]},
    {"key": "download_desc", "label": "下载结果·描述", "vars": ["title", "episodes", "task_id"]},
    {"key": "search_empty", "label": "搜索无结果提示", "vars": ["keyword", "platform"]},
    {"key": "processing_search", "label": "搜索·处理中回执", "vars": []},
    {"key": "processing_subscribe", "label": "订阅·处理中回执", "vars": []},
    {"key": "processing_download", "label": "下载·处理中回执", "vars": []},
    {"key": "help_title", "label": "帮助·卡片标题", "vars": []},
    {"key": "help_desc", "label": "帮助·卡片内容", "vars": []},
    {"key": "notify_subscription_queued_title", "label": "通知·订阅新章节·标题", "vars": ["title", "platform", "missing_count", "task_id"]},
    {"key": "notify_subscription_queued_desc", "label": "通知·订阅新章节·描述", "vars": ["title", "platform", "missing_count", "task_id"]},
    {"key": "notify_subscription_checked_title", "label": "通知·订阅检测缺失·标题", "vars": ["title", "platform", "missing_count"]},
    {"key": "notify_subscription_checked_desc", "label": "通知·订阅检测缺失·描述", "vars": ["title", "platform", "missing_count"]},
    {"key": "notify_download_completed_title", "label": "通知·下载完成·标题", "vars": ["title", "platform", "success", "failed", "task_id"]},
    {"key": "notify_download_completed_desc", "label": "通知·下载完成·描述", "vars": ["title", "platform", "success", "failed", "task_id"]},
    {"key": "notify_download_failed_title", "label": "通知·下载失败·标题", "vars": ["title", "task_id", "error"]},
    {"key": "notify_download_failed_desc", "label": "通知·下载失败·描述", "vars": ["title", "task_id", "error"]},
    {"key": "notify_rename_confirmation_title", "label": "通知·重命名确认·标题", "vars": ["title", "plan_id", "planned", "issues", "task_id"]},
    {"key": "notify_rename_confirmation_desc", "label": "通知·重命名确认·描述", "vars": ["title", "plan_id", "planned", "issues", "task_id"]},
]


def _wecom_help_text():
    return (
        "AudioFlow 企业微信指令：\n"
        "帮助：显示指令\n"
        "状态：查看服务版本和任务数\n"
        "搜索 关键词：全平台搜索（结果以卡片推送）\n"
        "搜索 平台 关键词：指定平台搜索，如「搜索 喜马拉雅 三体」\n"
        "下一页 / 上一页：翻看搜索结果\n"
        "订阅 序号：按网页版接口订阅最近一次搜索结果\n"
        "订阅 序号 杜比 / 无损：为该喜马拉雅专辑单独使用移动端 V4\n"
        "下载 序号：下载最近一次搜索结果全部章节\n"
        "确认整理 计划ID / 安全整理 计划ID / 取消整理 计划ID：处理下载后的整理计划\n"
        "示例：搜索 三体 / 搜索 喜马拉雅 三体"
    )


def _wecom_album_lines(results):
    lines = []
    for index, item in enumerate(results[:8], start=1):
        title = item.get("title") or "未知专辑"
        platform = item.get("platform") or "未知平台"
        author = item.get("author") or "未知作者"
        episodes = item.get("episodes") or "?"
        lines.append(f"{index}. {title}\n   {platform} / {author} / {episodes} 章")
    return "\n".join(lines)


def _wecom_session_key(service_id, user_id):
    return f"{service_id}:{user_id or 'unknown'}"


def cleanup_wecom_sessions(now=None):
    now = time.time() if now is None else float(now)
    for key, session in list(wecom_sessions.items()):
        updated_at = float(session.get("updated_at") or 0)
        if updated_at and now - updated_at > WECOM_SESSION_TTL_SECONDS:
            wecom_sessions.pop(key, None)
    if len(wecom_sessions) <= WECOM_SESSION_MAX_ITEMS:
        return
    ordered = sorted(
        wecom_sessions.items(),
        key=lambda item: float(item[1].get("updated_at") or 0),
    )
    overflow = len(wecom_sessions) - WECOM_SESSION_MAX_ITEMS
    for key, _session in ordered[:overflow]:
        wecom_sessions.pop(key, None)


def _wecom_get_cached_album(service_id, user_id, index_text):
    try:
        index = int(str(index_text).strip())
    except (TypeError, ValueError):
        raise ValueError("请输入正确序号，例如：订阅 1")
    with wecom_session_lock:
        cleanup_wecom_sessions()
        session = wecom_sessions.get(_wecom_session_key(service_id, user_id)) or {}
    results = session.get("results") or []
    if not results:
        raise ValueError("还没有搜索结果，请先发送：搜索 关键词")
    if index < 1 or index > len(results):
        raise ValueError(f"序号超出范围，请输入 1-{len(results)}")
    return normalize_album(results[index - 1])


def _wecom_load_album_chapters(album):
    album = normalize_album(album)
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    platform = album.get("platform")
    if not album_id or not platform:
        raise ValueError("缺少专辑 ID 或平台")
    voice = resolve_voice_for_album(album, None)
    if platform == "七猫听书":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    if platform == "番茄畅听" and voice:
        raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), voice, page=1, page_size=10000)
    elif platform == "番茄听书" and voice:
        raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), voice)
    elif platform == "七猫听书" and voice:
        raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), voice)
    else:
        raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    chapters = [normalize_chapter(chapter, index) for index, chapter in enumerate(raw_chapters or [], start=1)]
    if not chapters:
        raise ValueError("没有获取到章节列表")
    return album, chapters, voice


def _wecom_push(service_id, to_user, articles=None, text=None):
    """主动给企业微信用户推送结果（图文卡片优先，回退文本）。"""
    try:
        _service, config = _wecom_config_for_callback(service_id)
        if articles:
            notification_manager.send_wecom_app_news(config, articles, to_user=to_user)
        elif text:
            notification_manager.send_wecom_app_text(config, text, to_user=to_user)
    except Exception:
        logging.exception("wecom push failed: %s", service_id)


WECOM_SEARCH_PAGE_SIZE = 8


def _wecom_search_articles(keyword, platform, results, page=0):
    """渲染搜索结果图文卡片（封面+标题+描述+官方专辑页链接），按页取 8 条，序号全局连续。"""
    from core.notification_manager import wecom_album_url
    tpl = notification_manager.get_wecom_templates()
    base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    start = max(0, int(page)) * WECOM_SEARCH_PAGE_SIZE
    articles = []
    for offset, item in enumerate(results[start:start + WECOM_SEARCH_PAGE_SIZE]):
        idx = start + offset + 1
        plat = item.get("platform") or "未知平台"
        fields = {
            "index": idx,
            "title": item.get("title") or "未知专辑",
            "platform": plat,
            "author": item.get("author") or "未知作者",
            "episodes": item.get("episodes") or "?",
            "keyword": keyword,
        }
        article = {
            "title": _wecom_render(tpl["search_item_title"], **fields),
            "description": _wecom_render(tpl["search_item_desc"], **fields),
        }
        cover = normalize_cover_url(item.get("cover") or "", plat)
        if cover:
            article["picurl"] = cover
        url = wecom_album_url(plat, item.get("id") or item.get("album_id") or item.get("book_id"), base)
        if url:
            article["url"] = url
        articles.append(article)
    return articles


def _wecom_push_help(service_id, user_id):
    tpl = notification_manager.get_wecom_templates()
    _wecom_push(service_id, user_id, articles=[{
        "title": tpl.get("help_title") or "AudioFlow 指令",
        "description": tpl.get("help_desc") or _wecom_help_text(),
    }])


def _wecom_push_page_hint(service_id, user_id, results, page):
    total = len(results)
    total_pages = max(1, (total + WECOM_SEARCH_PAGE_SIZE - 1) // WECOM_SEARCH_PAGE_SIZE)
    if total_pages <= 1:
        return
    _wecom_push(service_id, user_id, text=(
        f"📄 第 {page + 1}/{total_pages} 页 · 共 {total} 条。"
        f"回复「下一页」/「上一页」翻页，「订阅 序号」/「下载 序号」操作。"
    ))


def _wecom_result_card(title, desc, cover="", platform=""):
    """默认结果卡片模板：订阅/下载等单条结果。"""
    article = {"title": title, "description": desc}
    cover = normalize_cover_url(cover or "", platform)
    if cover:
        article["picurl"] = cover
    base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        article["url"] = base
    return [article]


def _wecom_async_command(service_id, user_id, text):
    """后台异步执行慢指令（搜索/订阅/下载）并把结果以卡片推送，避免被动回复 5 秒超时。"""
    try:
        m_search = re.match(r"^(搜索|search|/search)\s+(.+)$", text, re.I)
        if m_search:
            rest = m_search.group(2).strip()
            platform = "all"
            keyword = rest
            parts = rest.split(None, 1)
            if len(parts) == 2 and parts[0] in WECOM_PLATFORM_NAMES:
                platform = parts[0]
                keyword = parts[1].strip()
            results = [normalize_album(item) for item in search_manager.search_books(keyword, platform)]
            with wecom_session_lock:
                cleanup_wecom_sessions()
                wecom_sessions[_wecom_session_key(service_id, user_id)] = {"keyword": keyword, "platform": platform, "results": results, "page": 0, "updated_at": time.time()}
            if not results:
                tpl = notification_manager.get_wecom_templates()
                _wecom_push(service_id, user_id, text=_wecom_render(tpl["search_empty"], keyword=keyword, platform=platform))
                return
            _wecom_push(service_id, user_id, articles=_wecom_search_articles(keyword, platform, results, 0))
            _wecom_push_page_hint(service_id, user_id, results, 0)
            return
        if re.match(r"^(下一页|next|/next|上一页|prev|/prev)$", text, re.I):
            forward = bool(re.match(r"^(下一页|next|/next)$", text, re.I))
            key = _wecom_session_key(service_id, user_id)
            with wecom_session_lock:
                cleanup_wecom_sessions()
                session = wecom_sessions.get(key) or {}
            results = session.get("results") or []
            if not results:
                _wecom_push(service_id, user_id, text="还没有搜索结果，请先发送：搜索 关键词")
                return
            total_pages = max(1, (len(results) + WECOM_SEARCH_PAGE_SIZE - 1) // WECOM_SEARCH_PAGE_SIZE)
            page = max(0, min(total_pages - 1, int(session.get("page", 0)) + (1 if forward else -1)))
            with wecom_session_lock:
                if key in wecom_sessions:
                    wecom_sessions[key]["page"] = page
                    wecom_sessions[key]["updated_at"] = time.time()
            _wecom_push(service_id, user_id, articles=_wecom_search_articles(session.get("keyword", ""), session.get("platform", "all"), results, page))
            _wecom_push_page_hint(service_id, user_id, results, page)
            return
        m_sub = re.match(
            r"^(订阅|subscribe|/subscribe)\s+(\d+)(?:\s+(网页版?|网页|杜比(?:全景声)?|全景声|无损))?$",
            text,
            re.I,
        )
        if m_sub:
            album = _wecom_get_cached_album(service_id, user_id, m_sub.group(2))
            album, chapters, voice = _wecom_load_album_chapters(album)
            if voice:
                album["voice"] = voice
            quality_alias = str(m_sub.group(3) or "").strip()
            requested_quality = {
                "杜比": "杜比全景声优先（自动降级）",
                "杜比全景声": "杜比全景声优先（自动降级）",
                "全景声": "杜比全景声优先（自动降级）",
                "无损": "无损优先（自动降级）",
            }.get(quality_alias, XMLY_WEB_SUBSCRIPTION_QUALITY)
            subscription_quality = ximalaya_subscription_quality(
                album,
                requested_quality if album.get("platform") == "喜马拉雅" else (requested_quality if quality_alias else None),
                default_web=True,
            )
            item = subscription_manager.add_or_update(
                album,
                chapters,
                active_download_dir(),
                subscription_quality=subscription_quality,
            )
            job = None
            if subscription_manager.settings().get("enabled", True):
                ensure_subscription_scheduler()
                job = start_subscription_job(item["id"], queue_missing=subscription_manager.settings().get("auto_download_missing", True))
            tpl = notification_manager.get_wecom_templates()
            fields = {"title": album.get("title") or "", "episodes": len(chapters), "job_suffix": (f"\n检测任务：{job.get('id')}" if job else "")}
            description = _wecom_render(tpl["subscribe_desc"], **fields)
            if subscription_quality:
                description += f"\n下载方式：{subscription_quality}"
            _wecom_push(service_id, user_id, articles=_wecom_result_card(
                _wecom_render(tpl["subscribe_title"], **fields),
                description,
                album.get("cover"), album.get("platform")))
            return
        m_dl = re.match(r"^(下载|download|/download)\s+(\d+)$", text, re.I)
        if m_dl:
            album = _wecom_get_cached_album(service_id, user_id, m_dl.group(2))
            album, chapters, voice = _wecom_load_album_chapters(album)
            options = {"download_dir": active_download_dir(), "quality": subscription_manager.settings().get("quality", "M4A 96K")}
            if voice:
                options["voice"] = voice
            task_id = f"wecom-{uuid.uuid4().hex[:12]}"
            start_download_task(task_id, album, chapters, options, source="wecom")
            tpl = notification_manager.get_wecom_templates()
            fields = {"title": album.get("title") or "", "episodes": len(chapters), "task_id": task_id}
            _wecom_push(service_id, user_id, articles=_wecom_result_card(
                _wecom_render(tpl["download_title"], **fields),
                _wecom_render(tpl["download_desc"], **fields),
                album.get("cover"), album.get("platform")))
            return
    except Exception as exc:
        logging.exception("wecom async command failed: %s", service_id)
        _wecom_push(service_id, user_id, text=f"❌ 执行失败：{exc}")


def _wecom_handle_text_command(service_id, user_id, text):
    text = str(text or "").strip()
    if not text or text in {"帮助", "help", "/help", "？", "?"}:
        threading.Thread(target=_wecom_push_help, args=(service_id, user_id), daemon=True).start()
        return "📖 指令说明已推送给你"
    if text in {"状态", "status", "/status"}:
        tasks_now = task_snapshot()
        running = sum(1 for item in tasks_now if item.get("status") in {"running", "pending", "paused"})
        return f"AudioFlow v{APP_VERSION}\n任务总数：{len(tasks_now)}\n进行中：{running}"
    rename_match = re.match(r"^(确认重命名|确认整理|安全整理|取消重命名|取消整理)\s+([a-f0-9]{10})$", text, re.I)
    if rename_match:
        plan_id = rename_match.group(2).lower()
        try:
            command = rename_match.group(1)
            if command in {"确认重命名", "确认整理"}:
                plan = rename_plan_manager.confirm(plan_id)
                renamed = sum(1 for item in (plan.get("items") or []) if item.get("status") == "renamed")
                return f"整理已完成：{(plan.get('album') or {}).get('title') or plan_id}\n成功：{renamed} 个文件"
            if command == "安全整理":
                rename_plan_manager.resolve_safe(plan_id)
                plan = rename_plan_manager.confirm(plan_id)
                renamed = sum(1 for item in (plan.get("items") or []) if item.get("status") == "renamed")
                return f"安全整理已完成：风险文件保持不动，整理 {renamed} 个文件"
            rename_plan_manager.cancel(plan_id)
            return f"整理计划已取消：{plan_id}"
        except (KeyError, ValueError, OSError) as exc:
            return f"整理操作失败：{exc}"
    # 慢指令（搜索/翻页/订阅/下载）改为后台异步执行 + 主动推送卡片，立即回执避免企业微信 5 秒超时
    tpl = notification_manager.get_wecom_templates()
    if re.match(r"^(搜索|search|/search)\s+.+$", text, re.I):
        threading.Thread(target=_wecom_async_command, args=(service_id, user_id, text), daemon=True).start()
        return tpl["processing_search"]
    if re.match(r"^(下一页|上一页|next|prev|/next|/prev)$", text, re.I):
        threading.Thread(target=_wecom_async_command, args=(service_id, user_id, text), daemon=True).start()
        return "⏳ 正在翻页…"
    if re.match(r"^(订阅|subscribe|/subscribe)\s+\d+(?:\s+(?:网页版?|网页|杜比(?:全景声)?|全景声|无损))?$", text, re.I):
        threading.Thread(target=_wecom_async_command, args=(service_id, user_id, text), daemon=True).start()
        return tpl["processing_subscribe"]
    if re.match(r"^(下载|download|/download)\s+\d+$", text, re.I):
        threading.Thread(target=_wecom_async_command, args=(service_id, user_id, text), daemon=True).start()
        return tpl["processing_download"]
    return "无法识别指令。\n\n" + _wecom_help_text()


def _wecom_text_response_xml(message, content):
    to_user = message.get("FromUserName") or ""
    from_user = message.get("ToUserName") or ""
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{_clean_xml_cdata(content)}]]></Content>"
        "</xml>"
    )


def _clean_xml_cdata(value):
    return str(value or "").replace("]]>", "]]]]><![CDATA[>")


@app.route("/api/wecom/callback/<service_id>", methods=["GET", "POST"])
def api_wecom_callback(service_id):
    try:
        service, config = _wecom_config_for_callback(service_id)
        crypto = WeComCrypto(config["token"], config["encoding_aes_key"], config["corp_id"])
        msg_signature = request.args.get("msg_signature", "")
        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        if request.method == "GET":
            plain = crypto.verify_url(msg_signature, timestamp, nonce, request.args.get("echostr", ""))
            return Response(plain, mimetype="text/plain")

        xml_text = crypto.decrypt_message(msg_signature, timestamp, nonce, request.get_data(as_text=True))
        message = parse_wecom_message(xml_text)
        user_id = message.get("FromUserName") or ""
        msg_type = message.get("MsgType") or ""
        try:
            if msg_type == "text":
                reply = _wecom_handle_text_command(service_id, user_id, message.get("Content") or "")
            elif msg_type == "event":
                reply = _wecom_help_text()
            else:
                reply = "目前仅支持文字指令。\n\n" + _wecom_help_text()
        except Exception as exc:
            logging.exception("wecom command failed: %s", service_id)
            reply = f"指令执行失败：{exc}\n\n{_wecom_help_text()}"
        response_xml = _wecom_text_response_xml(message, reply)
        return Response(crypto.encrypt(response_xml, nonce=nonce), mimetype="application/xml")
    except Exception as exc:
        logging.exception("wecom callback failed: %s", service_id)
        return Response(str(exc), status=200, mimetype="text/plain")


@app.get("/api/wecom/templates")
def api_get_wecom_templates():
    if not current_user():
        return json_error("未登录", 401)
    from core.notification_manager import DEFAULT_WECOM_TEMPLATES
    return json_ok(
        templates=notification_manager.get_wecom_templates(),
        defaults=DEFAULT_WECOM_TEMPLATES,
        fields=WECOM_TEMPLATE_FIELDS,
    )


@app.post("/api/wecom/templates")
def api_save_wecom_templates():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json(silent=True) or {}
    saved = notification_manager.save_wecom_templates(data.get("templates") or {})
    return json_ok(templates=saved)


def _path_status(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    writable = False
    try:
        probe = path / ".audioflow_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except Exception:
        writable = False
    return {"path": str(path), "exists": path.exists(), "writable": writable}


@app.get("/api/diagnostics")
def api_diagnostics():
    cookie_file = cookie_manager.config_file
    sub_file = SUBSCRIPTIONS_FILE
    tasks_file = TASKS_FILE
    ffmpeg = shutil.which("ffmpeg")
    return json_ok(
        app=APP_NAME,
        version=APP_VERSION,
        server_time=int(time.time()),
        paths={
            "config": _path_status(config_dir()),
            "data": _path_status(data_dir()),
            "download": _path_status(active_download_dir()),
            "log": _path_status(log_dir()),
        },
        binaries={"ffmpeg": {"available": bool(ffmpeg), "path": ffmpeg or ""}},
        frontend={
            "dist_exists": FRONTEND_DIST_DIR.exists(),
            "index_exists": (FRONTEND_DIST_DIR / "index.html").exists(),
        },
        runtime={
            "cookie_file": str(cookie_file),
            "cookie_file_exists": cookie_file.exists(),
            "cookie_encrypted": bool(getattr(cookie_manager, "encryption_enabled", False)),
            "subscriptions_file": str(sub_file),
            "subscriptions_file_exists": sub_file.exists(),
            "tasks_file": str(tasks_file),
            "tasks_file_exists": tasks_file.exists(),
            "tasks_count": len(tasks),
            "scheduler": subscription_scheduler_status(),
        },
    )


@app.get("/api/search")
def api_search():
    keyword = request.args.get("q", "").strip()
    platform = request.args.get("platform", "all").strip() or "all"
    if not keyword:
        return json_error("请输入搜索关键词")
    results = [
        annotate_album_library(normalize_album(item))
        for item in search_manager.search_books(keyword, platform)
    ]
    return json_ok(results=results, count=len(results))


@app.post("/api/album/chapters")
def api_chapters():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    voice = payload.get("voice")
    page = max(1, _to_int(payload.get("page"), 1))
    page_size = max(20, min(_to_int(payload.get("page_size") or payload.get("pageSize"), 100), 200))
    load_all = bool(payload.get("load_all") or payload.get("loadAll"))
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    platform = album.get("platform")
    if not album_id or not platform:
        return json_error("缺少专辑 ID 或平台")
    if platform == "七猫听书":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    active_voice = resolve_voice_for_album(album, voice)
    exact_total = 0
    with log_context(
        platform=platform,
        operation="章节目录",
        album_id=album_id,
        page=page,
        page_size=page_size,
        load_all=load_all,
    ):
        if _is_personal_qidian_album(album):
            qidian_api = _qidian_api_for_album(album)
            album, all_chapters, attempted_ids = _load_personal_qidian_album_chapters(album, qidian_api)
            album_id = album.get("id")
            if not all_chapters:
                attempted_text = "、".join(attempted_ids) or str(album_id or "未知")
                raise RuntimeError(
                    f"起点有声专辑《{album.get('title') or album_id}》没有返回章节"
                    f"（已尝试 ID：{attempted_text}），请查看服务日志中的上游错误"
                )
            exact_total = len(all_chapters)
            if load_all:
                raw_chapters = all_chapters
            else:
                offset = (page - 1) * page_size
                raw_chapters = all_chapters[offset:offset + page_size]
        elif load_all:
            if platform == "番茄畅听" and active_voice:
                raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), active_voice, page=1, page_size=10000)
            elif platform == "番茄听书" and active_voice:
                raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), active_voice)
            elif platform == "七猫听书" and active_voice:
                raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), active_voice)
            else:
                raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
            exact_total = len(raw_chapters)
        else:
            chapter_page_options = {
                "page": page,
                "page_size": page_size,
                "voice": active_voice,
            }
            if platform in ("网易云听书", "netease"):
                chapter_page_options["expected_total"] = _to_int(album.get("episodes"))
            raw_chapters, exact_total = search_manager.get_album_chapters_page(
                str(album_id),
                platform,
                **chapter_page_options,
            )
    if platform in ("网易云听书", "netease") and raw_chapters:
        first_chapter = next((item for item in raw_chapters if isinstance(item, dict)), {})
        raw_radio = first_chapter.get("_radio") if isinstance(first_chapter, dict) else None
        if isinstance(raw_radio, dict) and raw_radio:
            radio_detail = search_manager.netease_manager._normalize_radio(
                raw_radio,
                fallback_id=str(album_id),
                episode_count=exact_total,
            )
            album = merge_album_detail(album, radio_detail)
    warning = ""
    if platform == "懒人听书":
        warning = str(getattr(search_manager.lrts_manager, "last_chapter_warning", "") or "")
    start_index = 1 if load_all else (page - 1) * page_size + 1
    chapters = [
        normalize_chapter(chapter, index)
        for index, chapter in enumerate(raw_chapters, start=start_index)
    ]
    expected = max(_to_int(album.get("episodes")), exact_total)
    if expected <= 0 and len(chapters) < page_size:
        expected = (page - 1) * page_size + len(chapters)
    if expected > 0:
        album["episodes"] = expected
    if load_all and platform == "懒人听书" and expected > 0 and len(chapters) < expected and not warning:
        warning = f"懒人听书目录可能未完整加载：当前获取 {len(chapters)}/{expected} 章。"
    if warning:
        album["catalog_warning"] = warning
    chapter_states = album_chapter_download_states(album)
    for chapter in chapters:
        state = chapter_states.get(chapter_key(chapter)) or {}
        chapter["download_status"] = state.get("status") or "pending"
        chapter["download_error"] = state.get("error") or ""
    album = annotate_album_library(album)
    has_more = False if load_all else (page * page_size < expected if expected else len(chapters) >= page_size)
    total_pages = max(1, (expected + page_size - 1) // page_size) if expected else page + (1 if has_more else 0)
    pagination = {
        "page": page,
        "page_size": page_size,
        "total": expected,
        "total_pages": total_pages,
        "total_known": expected > 0,
        "has_more": has_more,
    }
    return json_ok(
        album=album,
        chapters=chapters,
        count=len(chapters),
        voice=active_voice,
        warning=warning,
        pagination=pagination,
    )


@app.post("/api/album/detail")
def api_album_detail():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    platform = album.get("platform")
    if not album_id or not platform:
        return json_error("缺少专辑 ID 或平台")
    try:
        if _is_personal_qidian_album(album):
            qidian_api = _qidian_api_for_album(album)
            album, detail, attempted_ids = _load_personal_qidian_album_detail(album, qidian_api)
            if not detail:
                attempted_text = "、".join(attempted_ids) or str(album_id or "未知")
                raise RuntimeError(
                    f"起点有声专辑《{album.get('title') or album_id}》没有返回详情"
                    f"（已尝试 ID：{attempted_text}）"
                )
        else:
            detail = search_manager.get_album_detail(str(album_id), platform)
        return json_ok(album=annotate_album_library(merge_album_detail(album, detail)))
    except Exception as exc:
        logging.exception("load album detail failed")
        return json_error(str(exc), status=500)


@app.post("/api/album/voices")
def api_album_voices():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    if not album.get("platform"):
        return json_error("缺少平台信息")
    try:
        album_id = album.get("id") or album.get("album_id") or album.get("book_id") or ""
        with log_context(
            platform=album.get("platform"),
            operation="音色列表",
            album_id=album_id,
        ):
            voices = get_album_voices(album)
            log_event(
                "INFO" if voices else "WARN",
                "音色列表加载完成" if voices else "专辑没有可选音色",
                voices=len(voices),
            )
        return json_ok(album=album, voices=voices, count=len(voices))
    except Exception as exc:
        logging.exception("load voices failed")
        return json_error(str(exc), status=500)


@app.post("/api/album/audio")
def api_album_audio():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or {})
    chapter = payload.get("chapter") or {}
    voice = resolve_voice_for_album(album, payload.get("voice"))
    platform = album.get("platform")
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    track_id = chapter_identifier(chapter)
    if not platform or not album_id or not track_id:
        return json_error("缺少专辑、章节或平台信息，无法播放")
    scope = log_context(
        platform=platform,
        operation="试听",
        album_id=album_id,
        track_id=track_id,
    )
    scope.__enter__()
    try:
        if platform == "番茄畅听":
            info = search_manager.fanqie_manager.get_audio_download_info(
                str(track_id),
                voice or "无损真人录制",
                str(album_id),
            )
            if info and info.get("url"):
                suffix = info.get("extension") or ".m4a"
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="fqct_")
                os.close(fd)
                if info.get("play") or info.get("encrypted"):
                    ok = search_manager.fanqie_manager.download_changting_chapter(
                        str(track_id),
                        voice or "无损真人录制",
                        tmp, "M4A 64K",
                    )
                else:
                    ok = search_manager.fanqie_manager.download_audio(info["url"], tmp)
                if ok:
                    local_url = register_local_audio(tmp)
                    if local_url:
                        return json_ok(url=local_url, source_url=info["url"])
        url = chapter_direct_audio_url(chapter)
        if not url:
            voice_name = (voice or {}).get("name") if isinstance(voice, dict) else None
            if platform == "番茄听书" and voice:
                path_or_url = search_manager.fanqie_tingshu_manager.prepare_playback(str(track_id), voice)
                url = path_or_url or ""
            elif platform == "七猫听书" and voice:
                path_or_url = search_manager.qimao_manager.prepare_playback(str(track_id), voice_config=voice)
                url = path_or_url or ""
            elif platform == "懒人听书":
                sync_platform_cookie(platform)
                url = search_manager.lrts_manager.get_audio_url(str(album_id), str(track_id), chapter)
            elif _is_personal_qidian_album(album):
                url = pick_audio_url(
                    _qidian_api_for_album(album).get_qidian_audio_url(str(album_id), str(track_id))
                )
            else:
                url = pick_audio_url(search_manager.get_audio_urls(str(track_id), platform, str(album_id), voice_name))
        if not url:
            return json_error("未获取到可播放的音频地址")
        local_url = register_local_audio(url)
        if local_url:
            return json_ok(url=local_url, source_url=local_url)
        # 浏览器直接拉第三方 CDN 通常会因 Referer/Origin 校验或缺少 cookie 而 403/静音，
        # 改走服务端代理。原始 URL 也一并返回，方便前端/调试。
        proxy_url = register_audio_proxy_url(url, platform)
        if not proxy_url:
            return json_error("音频地址无法生成安全代理链接")
        return json_ok(url=proxy_url, source_url=url)
    except Exception as exc:
        return json_error(str(exc), status=500)
    finally:
        scope.__exit__(None, None, None)


# ── 音频代理 ──────────────────────────────────
# 浏览器播放第三方 CDN 时常因为 Referer / Origin / cookie 校验失败而无声。
# 服务端代理一层，按平台补正确的 Referer/UA，再以流式 chunk 回传给浏览器。
_PLATFORM_REFERER = {
    "喜马拉雅": "https://www.ximalaya.com/",
    "懒人听书": "https://www.lrts.me/",
    "番茄畅听": "https://fanqienovel.com/",
    "蜻蜓FM": "https://www.qtfm.cn/",
    "云听FM": "https://www.radio.cn/",
    "起点听书": "https://www.qidian.com/",
    "酷我听书": "https://www.kuwo.cn/",
    "网易云听书": "https://music.163.com/",
    "荔枝FM": "https://m.lizhi.fm/",
}

_PROXY_ALLOWED_SCHEMES = ("http", "https")
_AUDIO_PROXY_TOKENS = {}
_AUDIO_PROXY_TOKEN_TTL = 15 * 60

_PLATFORM_AUDIO_HOST_HINTS = {
    "喜马拉雅": ("ximalaya.com", "xmcdn.com", "ximalayaos.com"),
    "懒人听书": ("lrts.me", "lrts1.com", "ting55.com"),
    "番茄畅听": ("fanqienovel.com", "snssdk.com", "byteimg.com", "toutiao.com", "bytedance.com"),
    "番茄听书": ("fanqienovel.com", "snssdk.com", "byteimg.com", "toutiao.com", "bytedance.com"),
    "七猫听书": ("qimao.com", "qimao.tv", "qimaoapi.com"),
    "蜻蜓FM": ("qtfm.cn", "qingting.fm", "qtfm.com"),
    "云听FM": ("radio.cn", "cnr.cn", "yunting.cn"),
    "起点听书": ("qidian.com", "qdmobi.com"),
    "酷我听书": ("kuwo.cn", "kuwo.com"),
    "网易云听书": ("music.163.com", "music.126.net", "netease.com"),
    "荔枝FM": ("lizhi.fm", "lizhi.io"),
}


def _cleanup_audio_proxy_tokens():
    now = time.time()
    for token, item in list(_AUDIO_PROXY_TOKENS.items()):
        if now - float(item.get("created_at") or 0) > _AUDIO_PROXY_TOKEN_TTL:
            _AUDIO_PROXY_TOKENS.pop(token, None)


def register_audio_proxy_url(url, platform):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in _PROXY_ALLOWED_SCHEMES or not parsed.netloc:
        return ""
    _cleanup_audio_proxy_tokens()
    token = uuid.uuid4().hex
    _AUDIO_PROXY_TOKENS[token] = {
        "url": str(url),
        "platform": str(platform or ""),
        "created_at": time.time(),
    }
    return "/api/proxy/audio?token=" + quote(token, safe="")


def _hostname_is_private(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return True
    return False


def _is_allowed_audio_host(platform, hostname):
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    hints = _PLATFORM_AUDIO_HOST_HINTS.get(platform) or ()
    if not hints:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in hints)


def _resolve_audio_proxy_request():
    _cleanup_audio_proxy_tokens()
    token = (request.args.get("token") or "").strip()
    if token:
        item = _AUDIO_PROXY_TOKENS.get(token)
        if not item:
            raise ValueError("播放链接已过期，请重新打开试听")
        return str(item.get("url") or ""), str(item.get("platform") or ""), True
    if not audio_proxy_raw_url_enabled():
        raise ValueError("不允许直接代理外部音频地址")
    return (request.args.get("url") or "").strip(), (request.args.get("platform") or "").strip(), False


def _validate_audio_proxy_target(src, platform, trusted_token=False):
    parsed = urlparse(src)
    if parsed.scheme not in _PROXY_ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("非法的音频地址")
    if parsed.username or parsed.password:
        raise ValueError("音频地址不能包含认证信息")
    hostname = parsed.hostname or ""
    if _hostname_is_private(hostname):
        raise ValueError("不允许访问内网或本机地址")
    if not trusted_token and not _is_allowed_audio_host(platform, hostname):
        raise ValueError("音频域名不在平台白名单内")
    return parsed


def _request_audio_upstream(method, src, platform, headers, trusted_token):
    current = src
    for _ in range(4):
        _validate_audio_proxy_target(current, platform, trusted_token=trusted_token)
        upstream = requests.request(method, current, headers=headers, stream=True, timeout=(10, 60), allow_redirects=False)
        if upstream.status_code not in (301, 302, 303, 307, 308):
            return upstream, current
        location = upstream.headers.get("Location", "")
        upstream.close()
        if not location:
            raise ValueError("上游跳转缺少 Location")
        current = requests.compat.urljoin(current, location)
        if upstream.status_code == 303:
            method = "GET"
    raise ValueError("上游跳转次数过多")


@app.route("/api/local-audio/<token>", methods=["GET", "HEAD"])
def api_local_audio(token):
    cleanup_local_audio_tokens()
    item = _LOCAL_AUDIO_TOKENS.get(token)
    if not item:
        return json_error("音频临时文件已失效", status=404)
    path = Path(str(item.get("path") or ""))
    if not path.is_file():
        _LOCAL_AUDIO_TOKENS.pop(token, None)
        return json_error("音频临时文件不存在", status=404)
    mime = mimetypes.guess_type(path.name)[0] or infer_audio_content_type(path.name)
    return send_file(path, mimetype=mime, conditional=True, max_age=0)


def infer_audio_content_type(url, upstream_type=""):
    content_type = (upstream_type or "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in ("application/octet-stream", "binary/octet-stream"):
        return upstream_type
    lower_url = unquote(str(url or "").lower())
    if "audio_mp4" in lower_url or "audio/mp4" in lower_url or ".m4a" in lower_url or ".mp4" in lower_url:
        return "audio/mp4"
    if "audio/mpeg" in lower_url or "mp3" in lower_url or ".mp3" in lower_url:
        return "audio/mpeg"
    if "aac" in lower_url or ".aac" in lower_url:
        return "audio/aac"
    if "flac" in lower_url or ".flac" in lower_url:
        return "audio/flac"
    return upstream_type or "audio/mpeg"


@app.route("/api/proxy/audio", methods=["GET", "HEAD"])
def api_proxy_audio():
    """流式代理第三方音频。

    Query:
        url: 原始音频 URL（必填，应为 http/https）
        platform: 平台名，用于补正确的 Referer
    """
    try:
        src, platform, trusted_token = _resolve_audio_proxy_request()
        if not src:
            return json_error("缺少音频地址")
        _validate_audio_proxy_target(src, platform, trusted_token=trusted_token)
    except ValueError as exc:
        return json_error(str(exc), status=403)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",  # 避免上游 gzip 后流式不易处理
        "Connection": "keep-alive",
    }
    referer = _PLATFORM_REFERER.get(platform)
    if platform == "番茄畅听":
        referer = ""
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.rstrip("/")
    # 透传 Range，支持浏览器拖动进度条
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    # 部分平台音频 CDN 需要带平台 cookie。只有服务端签发的短期 token
    # 才能触发 Cookie 透传，避免外部构造 URL 窃取 Cookie。
    cookie_key_map = {
        "喜马拉雅": "xmly", "懒人听书": "lrts", "起点听书": "qidian",
        "蜻蜓FM": "qtfm", "番茄畅听": "fanqie", "番茄听书": "fanqie_tingshu",
        "七猫听书": "qimao", "云听FM": "yuntu", "酷我听书": "kuwo", "网易云听书": "netease",
        "荔枝FM": "lizhi",
    }
    cookie_required_platforms = {"喜马拉雅", "懒人听书", "起点听书", "蜻蜓FM", "网易云听书"}
    ck_key = cookie_key_map.get(platform) if trusted_token and platform in cookie_required_platforms else None
    if ck_key == "lrts":
        ck_key = None
    if ck_key:
        ck = cookie_manager.get_cookie(ck_key)
        if ck_key == "qidian":
            from src.features.qidian.audio_system import qidian_cookie_header
            ck = qidian_cookie_header(ck)
        elif isinstance(ck, dict):
            ck = "; ".join(f"{k}={v}" for k, v in ck.items() if v)
        if ck:
            headers["Cookie"] = ck

    try:
        method = "HEAD" if request.method == "HEAD" else "GET"
        upstream, final_src = _request_audio_upstream(method, src, platform, headers, trusted_token)
    except Exception as exc:
        return json_error(f"上游请求失败：{exc}", status=502)

    if upstream.status_code >= 400:
        upstream.close()
        return json_error(f"上游返回 {upstream.status_code}", status=upstream.status_code)

    # 透传关键响应头
    passthrough = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
        v = upstream.headers.get(h)
        if v:
            passthrough[h] = v
    passthrough["Content-Type"] = infer_audio_content_type(final_src, passthrough.get("Content-Type", ""))
    passthrough.setdefault("Accept-Ranges", "bytes")
    passthrough["Cache-Control"] = "no-store"
    passthrough["Access-Control-Allow-Origin"] = "*"

    if request.method == "HEAD":
        upstream.close()
        return Response(status=upstream.status_code, headers=passthrough)

    def _generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(_generate()),
        status=upstream.status_code,
        headers=passthrough,
    )


@app.post("/api/downloads")
def api_download():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or {})
    identity_error = ximalaya_album_identity_error(album)
    if identity_error:
        return json_error(identity_error)
    download_all = bool(payload.get("all_chapters") or payload.get("allChapters"))
    options = payload.get("options") or {}
    options["download_dir"] = resolve_download_dir(options.get("download_dir"))
    if options.get("voice"):
        options["voice"] = resolve_voice_for_album(album, options.get("voice"))

    if download_all:
        if not album:
            return json_error("缺少专辑信息")
        task_id = f"web-{uuid.uuid4().hex[:12]}"
        expected = max(0, _to_int(album.get("episodes") or album.get("chapter_count")))
        task = set_task(
            task_id,
            status="queued",
            title=album.get("title"),
            album=album,
            chapters=[],
            options=options,
            source="web",
            total=expected,
            completed=0,
            percent=0,
            success=0,
            failed=0,
            error="",
            failure_reason="",
            task_info={"message": "下载任务已创建，正在后台加载完整目录"},
            chapter_states={},
            created_at=time.time(),
            preparing=True,
        )

        def prepare_whole_album():
            try:
                chapters = load_all_album_chapters(album, options.get("voice"))
                if not chapters:
                    set_task(
                        task_id,
                        status="failed",
                        error="未获取到完整章节目录",
                        failure_reason="未获取到完整章节目录",
                        finished_at=time.time(),
                        preparing=False,
                    )
                    return
                started = start_download_task(task_id, album, chapters, options, source="web")
                if started.get("id") != task_id:
                    with task_lock:
                        tasks.pop(task_id, None)
                        save_tasks(force=True)
            except Exception as exc:
                logging.exception("prepare whole-album download failed")
                set_task(
                    task_id,
                    status="failed",
                    error=str(exc),
                    failure_reason=str(exc),
                    finished_at=time.time(),
                    preparing=False,
                )

        threading.Thread(
            target=prepare_whole_album,
            name=f"prepare-download-{task_id}",
            daemon=True,
        ).start()
        return json_ok(
            task_id=task_id,
            task=task,
            preparing=True,
            message="下载任务已创建，正在后台加载完整目录",
        )

    chapters = hydrate_download_chapters(album, payload.get("chapters") or [], payload.get("chapter_ids") or payload.get("chapterIds") or [])
    if not album or not chapters:
        return json_error("缺少专辑或章节")
    if album.get("platform") == "懒人听书":
        sync_platform_cookie("懒人听书")
    task_id = f"web-{uuid.uuid4().hex[:12]}"
    task = start_download_task(task_id, album, chapters, options, source="web")
    deduplicated = bool(task.get("deduplicated"))
    return json_ok(
        task_id=task.get("id") or task_id,
        task=task,
        deduplicated=deduplicated,
        message=("所选章节已在下载任务中" if deduplicated else f"已加入下载 {len(chapters)} 章"),
    )


@app.get("/api/downloads")
def api_downloads():
    page = request.args.get("page", 1)
    limit = request.args.get("limit", 20)
    status = request.args.get("status") or "all"
    return json_ok(**paginate_downloads(task_snapshot(), page=page, limit=limit, status=status))


@app.post("/api/downloads/retry-unfinished")
def api_retry_unfinished_downloads():
    retried = []
    skipped = []
    for task in task_snapshot():
        status = task.get("status")
        if status not in ("interrupted", "failed", "partial", "stopped"):
            continue
        task_id = task.get("id")
        retried_task, error = retry_existing_download_task(task_id, task, source=f"retry-unfinished:{task_id}")
        if error:
            skipped.append({"task_id": task_id, "error": error})
        else:
            retried.append(retried_task)
    append_background_event(
        "download",
        "重试未完成任务",
        f"重新尝试 {len(retried)} 个任务，跳过 {len(skipped)} 个",
        {"count": len(retried), "skipped_count": len(skipped)},
    )
    return json_ok(count=len(retried), tasks=retried, skipped=skipped)


@app.get("/api/downloads/<task_id>")
def api_download_detail(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    return json_ok(task=_download_task_detail(task))


def live_worker(task_id):
    with task_lock:
        return task_workers.get(task_id)


def pause_worker(worker):
    if hasattr(worker, "pause"):
        worker.pause()
        return
    setattr(worker, "_is_paused", True)


def resume_worker(worker):
    if hasattr(worker, "resume"):
        worker.resume()
        return
    setattr(worker, "_is_paused", False)


def stop_worker(worker):
    if hasattr(worker, "stop"):
        worker.stop()
        return
    setattr(worker, "_is_stopped", True)
    setattr(worker, "_is_paused", False)


def retry_existing_download_task(task_id, task, source):
    if not task_id:
        return None, "任务 ID 缺失"
    if live_worker(task_id):
        return None, "任务仍在运行，请稍后重试"
    chapters = task.get("chapters") or task.get("failed_chapters") or []
    if not chapters:
        return None, "没有可重试的失败章节"
    album = task.get("album") or {"title": task.get("title"), "platform": (task.get("task_info") or {}).get("platform")}
    options = dict(task.get("options") or {})
    if not options and task.get("task_info"):
        info = task.get("task_info") or {}
        options = {"download_dir": info.get("download_dir"), "quality": info.get("quality"), "voice": info.get("voice_config")}
    if options.get("voice"):
        options["voice"] = resolve_voice_for_album(album, options.get("voice"))
    retried_task = start_download_task(
        task_id,
        album,
        chapters,
        options,
        source=source,
        origin_source=task.get("origin_source") or task.get("source"),
    )
    if retried_task.get("id") != task_id:
        return None, "失败章节已在其他下载任务中"
    return retried_task, None


@app.post("/api/downloads/<task_id>/pause")
def api_download_pause(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    worker = live_worker(task_id)
    if not worker:
        return json_error("任务未在运行，无法暂停", 409)
    pause_worker(worker)
    return json_ok(task=set_task(task_id, status="paused"))


@app.post("/api/downloads/<task_id>/resume")
def api_download_resume(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    worker = live_worker(task_id)
    if worker:
        resume_worker(worker)
        return json_ok(task=set_task(task_id, status="running"))
    # 无 worker（服务重启等导致的僵尸任务）：重建下载任务续下章节
    # （download_worker 会跳过本地已存在的文件，不会重复下载已完成章节）
    chapters = task.get("failed_chapters") or task.get("chapters") or []
    if not chapters:
        return json_error("任务无可下载章节，无法恢复", 409)
    album = task.get("album") or {"title": task.get("title"), "platform": (task.get("task_info") or {}).get("platform")}
    options = task.get("options") or {}
    if not options and task.get("task_info"):
        info = task.get("task_info") or {}
        options = {"download_dir": info.get("download_dir"), "quality": info.get("quality"), "voice": info.get("voice_config")}
    if options.get("voice"):
        options["voice"] = resolve_voice_for_album(album, options.get("voice"))
    set_task(task_id, status="stopped", finished_at=time.time())
    new_task_id = f"resume-{uuid.uuid4().hex[:12]}"
    new_task = start_download_task(
        new_task_id,
        album,
        chapters,
        options,
        source=f"resume:{task_id}",
        origin_source=task.get("origin_source") or task.get("source"),
    )
    return json_ok(task_id=new_task.get("id") or new_task_id, task=new_task, resumed=True, deduplicated=bool(new_task.get("deduplicated")))


@app.post("/api/downloads/<task_id>/stop")
def api_download_stop(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    worker = live_worker(task_id)
    if worker:
        stop_worker(worker)
        return json_ok(task=set_task(task_id, status="stopping"))
    # 无 worker：含 stopping 在内的僵尸任务（如服务重启后 worker 丢失）直接落到 stopped
    if task.get("status") in ("queued", "running", "paused", "stopping"):
        return json_ok(task=set_task(task_id, status="stopped", finished_at=time.time()))
    return json_ok(task=task)


@app.post("/api/downloads/<task_id>/retry-failed")
def api_download_retry_failed(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    if task.get("status") not in ("failed", "partial", "interrupted", "stopped"):
        return json_error("当前任务状态不可重试", 409)
    # Re-run the original chapter set so this task keeps coherent totals. The
    # worker skips valid files that already exist and only downloads the gaps.
    retried_task, error = retry_existing_download_task(task_id, task, source=f"retry:{task_id}")
    if error:
        return json_error(error, 409)
    return json_ok(task_id=task_id, task=retried_task, retried=True, deduplicated=bool(retried_task.get("deduplicated")))


@app.delete("/api/downloads/<task_id>")
def api_download_delete(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    # 仅当确有 worker 在运行时才拒绝删除；无 worker 的 running/stopping 等为僵尸任务，允许删除
    if live_worker(task_id):
        return json_error("运行中的任务不能删除，请先停止", 409)
    with task_lock:
        tasks.pop(task_id, None)
        save_tasks(force=True)
    return json_ok(deleted=True)


@app.post("/api/downloads/cleanup")
def api_download_cleanup():
    payload = request.get_json(silent=True) or {}
    statuses = payload.get("statuses") or ["completed", "failed", "partial", "interrupted", "stopped"]
    statuses = {str(item).strip() for item in statuses if str(item).strip()}
    deleted = []
    with task_lock:
        for tid, task in list(tasks.items()):
            status = str(task.get("status") or "")
            # 仅保护确有 worker 在运行的任务；无 worker 的任务即使状态为 running/stopping 也可清理
            if tid in task_workers:
                continue
            if status in statuses:
                tasks.pop(tid, None)
                deleted.append(tid)
        if deleted:
            save_tasks(force=True)
    return json_ok(deleted=deleted, count=len(deleted), statuses=sorted(statuses))


def _album_cover_value(album):
    if not isinstance(album, dict):
        return ""
    return _pick_nested_value(
        album,
        (
            "cover", "cover_url", "coverUrl", "coverPath", "CoverUrl", "albumCover",
            "albumCoverUrl", "pic", "picUrl", "image", "imageUrl", "thumb_url",
            "thumbUrl", "thumb", "thumbnail", "image_link", "bookCover", "posterUrl",
            "img", "imgPath", "hts_img", "albumpic", "albumPic", "web_albumpic_short",
        ),
    )


def _ensure_subscription_cover(item):
    album = item.get("album") or {}
    platform = item.get("platform") or album.get("platform")
    if platform != "酷我听书" or item.get("cover") or _album_cover_value(album):
        return item
    album_id = item.get("album_id") or album.get("id") or album.get("album_id") or album.get("book_id")
    if not album_id:
        return item
    try:
        detail = search_manager.get_album_detail(str(album_id), platform) or {}
        merged = merge_album_detail(album or item, detail)
        cover = _album_cover_value(merged)
        if cover:
            item["cover"] = cover
            item["album"] = {**album, **merged, "cover": cover}
            item["_cover_updated"] = True
    except Exception:
        logging.exception("kuwo subscription cover fallback failed: %s", album_id)
    return item


@app.get("/api/subscriptions")
def api_subscriptions():
    ensure_subscription_scheduler()
    fast = request.args.get("fast", "1").lower() not in ("0", "false", "no")
    refresh_local = request.args.get("refresh_local", "0").lower() in ("1", "true", "yes")
    items = []
    scan_cache = {}
    cover_changed = False
    for item in subscription_manager.all_subscriptions():
        item = _ensure_subscription_cover(item)
        cover_changed = cover_changed or bool(item.pop("_cover_updated", False))
        item["download_dir"] = active_download_dir()
        stats = subscription_manager.stats_for(item, active_download_dir(), fast=fast and not refresh_local, scan_cache=scan_cache)
        data = dict(item)
        data["stats"] = stats
        data["next_check_at"] = subscription_manager.next_check_at(item)
        items.append(data)
    if cover_changed:
        subscription_manager.save()
    # fast 模式秒回缓存后，后台异步刷新一次本地统计（带节流），让数字最终保持准确
    if fast and not refresh_local:
        refresh_subscription_stats_async()
    return json_ok(subscriptions=items, settings=subscription_manager.settings(), scheduler=subscription_scheduler_status(), fast=fast, refresh_local=refresh_local)


@app.post("/api/subscriptions/index/rebuild")
def api_rebuild_subscription_index():
    index = subscription_manager.build_audio_index(active_download_dir(), force=True)
    return json_ok(index={"count": index.get("count", 0), "updated_at": index.get("updated_at"), "exists": index.get("exists")})


@app.get("/api/subscriptions/export")
def api_export_subscriptions():
    items = subscription_manager.export_subscriptions()
    return json_ok(subscriptions=items, count=len(items), settings=subscription_manager.settings())


@app.post("/api/subscriptions/import")
def api_import_subscriptions():
    payload = request.get_json(silent=True) or {}
    items = payload.get("subscriptions")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return json_error("导入文本不是合法的 JSON")
    # 兼容直接整份导出对象 {subscriptions:[...]} 或纯列表
    if isinstance(items, dict) and "subscriptions" in items:
        items = items.get("subscriptions")
    if not isinstance(items, list) or not items:
        return json_error("导入数据应为订阅列表（导出的 JSON）")
    # 旧设备的 download_dir 无意义，统一指向当前实际下载目录
    dl = active_download_dir()
    for rec in items:
        if isinstance(rec, dict):
            rec["download_dir"] = dl
    count = subscription_manager.import_subscriptions(items)
    try:
        refresh_subscription_stats_async(min_interval=0)  # 导入后立即在后台刷新统计
    except Exception:
        pass
    return json_ok(imported=count)


@app.post("/api/downloads/organize-by-platform")
def api_organize_downloads_by_platform():
    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dry_run", False))
    root = Path(active_download_dir())
    moved = []
    skipped = []
    if not root.exists():
        return json_error("下载目录不存在", 404)
    for item in subscription_manager.all_subscriptions():
        title = item.get("title") or (item.get("album") or {}).get("title")
        platform = item.get("platform") or (item.get("album") or {}).get("platform")
        if not title or not platform:
            continue
        source = root / _sanitize_download_folder_name(title)
        target = root / _sanitize_download_folder_name(platform) / _sanitize_download_folder_name(title)
        if not source.exists() or not source.is_dir():
            continue
        if target.exists():
            skipped.append({"title": title, "platform": platform, "reason": "目标目录已存在", "source": str(source), "target": str(target)})
            continue
        moved.append({"title": title, "platform": platform, "source": str(source), "target": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
    if not dry_run:
        subscription_manager.build_audio_index(active_download_dir(), force=True)
    append_background_event(
        "maintenance",
        "下载目录整理",
        f"{'预览' if dry_run else '完成'}：移动 {len(moved)} 个，跳过 {len(skipped)} 个",
        {"dry_run": dry_run, "moved": moved, "skipped": skipped},
    )
    return json_ok(dry_run=dry_run, moved=moved, skipped=skipped, moved_count=len(moved), skipped_count=len(skipped))


@app.get("/api/subscriptions/settings")
def api_get_subscription_settings():
    """读取订阅自动检测设置。"""
    ensure_subscription_scheduler()
    return json_ok(settings=subscription_manager.settings())


@app.post("/api/subscriptions/settings")
def api_update_subscription_settings():
    """更新订阅自动检测设置。

    Body: {"enabled": bool, "auto_download_missing": bool, "interval_hours": int, "interval_minutes": int, "quality": str(可选)}
    """
    payload = request.get_json(silent=True) or {}
    updates = {}
    if "enabled" in payload:
        updates["enabled"] = bool(payload.get("enabled"))
    if "auto_download_missing" in payload:
        updates["auto_download_missing"] = bool(payload.get("auto_download_missing"))
    if "interval_hours" in payload:
        try:
            hours = int(payload.get("interval_hours") or 0)
        except Exception:
            return json_error("interval_hours 必须是整数")
        if hours < 1:
            return json_error("检测间隔至少 1 小时")
        if hours > 24 * 30:
            return json_error("检测间隔过大")
        updates["interval_hours"] = hours
        # 重置分钟字段，按小时为单位
        updates["interval_minutes"] = 0
    if "interval_minutes" in payload:
        try:
            minutes = int(payload.get("interval_minutes") or 0)
        except Exception:
            return json_error("interval_minutes 必须是整数")
        if minutes < 1:
            return json_error("检测间隔至少 1 分钟")
        if minutes > 24 * 30 * 60:
            return json_error("检测间隔过大")
        updates["interval_hours"] = 0
        updates["interval_minutes"] = minutes
    if "quality" in payload and str(payload.get("quality") or "").strip():
        updates["quality"] = str(payload.get("quality")).strip()
    if "personal_sync_enabled" in payload:
        updates["personal_sync_enabled"] = bool(payload.get("personal_sync_enabled"))
    if "personal_sync_platform" in payload and str(payload.get("personal_sync_platform") or "").strip():
        platform = str(payload.get("personal_sync_platform")).strip()
        if platform != "ximalaya":
            return json_error("目前仅支持同步喜马拉雅个人中心订阅")
        updates["personal_sync_platform"] = platform
    if "personal_sync_interval_hours" in payload:
        try:
            hours = int(payload.get("personal_sync_interval_hours") or 0)
        except Exception:
            return json_error("personal_sync_interval_hours 必须是整数")
        if hours < 1:
            return json_error("个人中心同步间隔至少 1 小时")
        if hours > 24 * 30:
            return json_error("个人中心同步间隔过大")
        updates["personal_sync_interval_hours"] = hours
        updates["personal_sync_interval_minutes"] = 0
    if "personal_sync_interval_minutes" in payload:
        try:
            minutes = int(payload.get("personal_sync_interval_minutes") or 0)
        except Exception:
            return json_error("personal_sync_interval_minutes 必须是整数")
        if minutes < 1:
            return json_error("个人中心同步间隔至少 1 分钟")
        if minutes > 24 * 30 * 60:
            return json_error("个人中心同步间隔过大")
        updates["personal_sync_interval_hours"] = 0
        updates["personal_sync_interval_minutes"] = minutes
    if not updates:
        return json_error("未提供任何可更新的字段")
    subscription_manager.update_settings(**updates)
    # 开启时确保调度线程已启动
    if subscription_manager.settings().get("enabled", True):
        wake_subscription_scheduler(force=bool(payload.get("run_now", True)))
    elif subscription_manager.settings().get("personal_sync_enabled", False):
        wake_subscription_scheduler(force=False)
    return json_ok(settings=subscription_manager.settings(), scheduler=subscription_scheduler_status())


@app.post("/api/subscriptions/run")
def api_run_subscriptions_now():
    if not subscription_manager.settings().get("enabled", True):
        return json_error("订阅自动检测未启用")
    ensure_subscription_scheduler()
    auto_download = subscription_manager.settings().get("auto_download_missing", True)
    jobs = [
        start_subscription_job(item.get("id"), queue_missing=auto_download)
        for item in subscription_manager.active_subscriptions()
        if item.get("id")
    ]
    return json_ok(jobs=jobs, count=len(jobs), scheduler=subscription_scheduler_status())


@app.post("/api/subscriptions/batch")
def api_subscriptions_batch():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip()
    ids = payload.get("ids")
    if not ids:
        ids = [item.get("id") for item in subscription_manager.active_subscriptions()]
    ids = [str(item) for item in ids or [] if item]
    if action not in {"check", "complete", "cancel", "enable"}:
        return json_error("不支持的批量操作")
    jobs = []
    changed = 0
    if action in {"check", "complete"}:
        for sid in ids:
            if subscription_manager.get(sid):
                jobs.append(start_subscription_job(sid, queue_missing=(action == "complete"), manual=(action == "complete")))
        append_background_event("subscription", "批量订阅操作", f"{action} {len(jobs)} 个订阅", {"action": action, "count": len(jobs)})
        return json_ok(action=action, jobs=jobs, count=len(jobs), scheduler=subscription_scheduler_status())
    for sid in ids:
        if action == "cancel":
            changed += 1 if subscription_manager.cancel(sid) else 0
        elif action == "enable":
            changed += 1 if subscription_manager.set_status(sid, "active") else 0
    append_background_event("subscription", "批量订阅操作", f"{action} {changed} 个订阅", {"action": action, "count": changed})
    return json_ok(action=action, count=changed, scheduler=subscription_scheduler_status())


@app.post("/api/subscriptions/personal-sync/run")
def api_run_personal_sync_now():
    try:
        ensure_subscription_scheduler()
        result = _personal_sync_tick(force=True)
        return json_ok(result=result, scheduler=subscription_scheduler_status())
    except RuntimeError as exc:
        return json_error(str(exc), status=400)
    except Exception as exc:
        return json_error(str(exc), status=500)


@app.get("/api/subscriptions/scheduler")
def api_subscription_scheduler():
    ensure_subscription_scheduler()
    return json_ok(scheduler=subscription_scheduler_status())


@app.post("/api/subscriptions")
def api_subscribe():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or {})
    chapters = payload.get("chapters") or []
    load_all = bool(payload.get("load_all") or payload.get("loadAll"))
    voice = resolve_voice_for_album(album, payload.get("voice"))
    if not album:
        return json_error("缺少专辑信息")
    if voice:
        album["voice"] = voice
    try:
        with subscription_manager.locked():
            existing = subscription_manager.get(subscription_manager.subscription_id(album))
            subscription_quality = ximalaya_subscription_quality(
                album,
                payload.get("subscription_quality") if "subscription_quality" in payload else None,
                default_web=not existing,
            )
            item = subscription_manager.add_or_update(
                album,
                chapters,
                active_download_dir(),
                subscription_quality=subscription_quality,
            )
    except ValueError as exc:
        return json_error(str(exc))
    job = None
    settings = subscription_manager.settings()
    if settings.get("enabled", True) or load_all:
        ensure_subscription_scheduler()
        # Defer full check to avoid double-fetch: the search results already provided
        # chapters. Only queue missing chapters for download if auto-download is on.
        auto_dl = settings.get("auto_download_missing", True) and settings.get("enabled", True)
        if auto_dl and chapters:
            # Use provided chapters directly - no need to refetch from remote.
            job = start_subscription_job(item["id"], queue_missing=True, manual=False)
        else:
            job = start_subscription_job(item["id"], queue_missing=False, manual=False)
    return json_ok(subscription=item, library=album_library_summary(album), job=job)


@app.delete("/api/subscriptions/<path:sid>")
def api_unsubscribe(sid):
    ok = subscription_manager.cancel(sid)
    return json_ok(cancelled=ok)


@app.patch("/api/subscriptions/<path:sid>")
def api_update_subscription(sid):
    payload = request.get_json(silent=True) or {}
    quality_value = str(payload.get("subscription_quality") or "").strip()
    if not quality_value:
        return json_error("请选择订阅下载方式")
    try:
        with subscription_manager.locked():
            item = subscription_manager.get(sid)
            if not item:
                return json_error("订阅不存在", 404)
            album = normalize_album(item.get("album") or item)
            quality = ximalaya_subscription_quality(album, quality_value)
            updated = subscription_manager.set_subscription_quality(sid, quality)
    except ValueError as exc:
        return json_error(str(exc))
    append_background_event(
        "subscription",
        "订阅下载方式已更新",
        f"{updated.get('title') or sid}：{quality}",
        {"sid": sid, "subscription_quality": quality},
    )
    return json_ok(subscription=updated)


@app.post("/api/subscriptions/<path:sid>/check")
def api_subscription_check(sid):
    if not subscription_manager.get(sid):
        return json_error("订阅不存在", 404)
    return json_ok(job=start_subscription_job(sid, queue_missing=False))


@app.get("/api/subscriptions/jobs/<job_id>")
def api_subscription_job(job_id):
    with subscription_job_lock:
        cleanup_subscription_jobs()
        job = dict(subscription_jobs.get(job_id) or {})
    if not job:
        return json_error("订阅任务不存在", 404)
    return json_ok(job=job)


@app.post("/api/subscriptions/<path:sid>/complete")
def api_subscription_complete(sid):
    if not subscription_manager.get(sid):
        return json_error("订阅不存在", 404)
    return json_ok(job=start_subscription_job(sid, queue_missing=True, manual=True))


@app.get("/api/player/url")
def api_player_url():
    """获取章节的播放 URL"""
    platform = request.args.get("platform", "").strip()
    album_id = request.args.get("album_id", "").strip()
    chapter_id = request.args.get("chapter_id", "").strip()
    if not chapter_id:
        return json_error("缺少 chapter_id 参数")
    try:
        url = None
        if platform == "喜马拉雅":
            urls = search_manager.ximalaya_manager.get_audio_urls(chapter_id)
            if isinstance(urls, dict):
                for q, info in sorted(urls.items(), key=lambda x: x[1].get('quality_level', 0) if isinstance(x[1], dict) else 0, reverse=True):
                    if isinstance(info, dict):
                        u = info.get('url', '')
                        if u and str(u).startswith('http'):
                            url = u
                            break
            else:
                url = urls
        elif platform == "懒人听书":
            sync_platform_cookie(platform)
            url = search_manager.lrts_manager.get_audio_url(album_id, chapter_id)
        elif platform == "番茄畅听":
            voice_name = request.args.get("voice_name", "").strip() or "无损真人录制"
            info = search_manager.fanqie_manager.get_audio_download_info(chapter_id, voice_name, album_id)
            url = info.get("url") if info else None
        elif platform == "云听FM":
            url = request.args.get("direct_url", "")
        elif platform == "起点听书":
            audio_dict = search_manager.search_manager.get_qidian_audio_url(album_id, chapter_id)
            if audio_dict and "default" in audio_dict:
                url = audio_dict["default"].get("url", "")
        elif platform == "蜻蜓FM":
            url = search_manager.qtfm_manager.get_audio_url(album_id, chapter_id)
        elif platform == "酷我听书":
            info = search_manager.kuwo_manager.get_download_info(chapter_id, "lossless")
            url = info.get("url") if info else None
        elif platform == "网易云听书":
            info = search_manager.netease_manager.get_download_info(chapter_id, "exhigh")
            url = info.get("url") if info else None
        if url and str(url).startswith("http"):
            proxy_url = register_audio_proxy_url(str(url), platform)
            if not proxy_url:
                return json_error("音频地址无法生成安全代理链接")
            return json_ok(url=proxy_url, source_url=str(url))
        else:
            return json_error(f"无法获取 {platform} 的播放地址")
    except Exception as e:
        return json_error(str(e), status=500)


@app.post("/api/player/session")
def api_player_session():
    session_file = data_dir() / "player_session.json"
    payload = request.get_json(silent=True) or {}
    session_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_ok(saved=True)


@app.get("/api/player/session")
def api_get_player_session():
    session_file = data_dir() / "player_session.json"
    if not session_file.exists():
        return json_ok(session={})
    return json_ok(session=json.loads(session_file.read_text(encoding="utf-8")))


SOURCE_INFO_FILE = "source.json"
SOURCE_PLATFORM_ALIASES = {
    "云听FM": "云听fm",
    "蜻蜓FM": "蜻蜓fm",
}


def _safe_child_path(root, relative):
    base = Path(root).resolve()
    target = (base / str(relative or "")).resolve()
    if target != base and base not in target.parents:
        raise ValueError("路径越界")
    return target


def _format_bytes(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "0 B"


def _sanitize_download_folder_name(name):
    text = str(name or "").strip() or "未知专辑"
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        text = text.replace(char, "_")
    return text[:200] or "未知专辑"


def _album_download_folder(album, options=None):
    album = normalize_album(album)
    options = dict(options or {})
    root = Path(resolve_download_dir(options.get("download_dir")))
    title = album.get("title") or ""
    if not title:
        return None
    parts = [root]
    if cookie_manager.get_cookie("organize_by_platform_enabled") == "true":
        parts.append(_sanitize_download_folder_name(album.get("platform") or "未知平台"))
    parts.append(_sanitize_download_folder_name(title))
    return Path(*parts)


def _album_source_id(album):
    for key in ("id", "album_id", "book_id", "contentId", "content_id"):
        value = (album or {}).get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _album_source_payload(album, options=None, task_id=""):
    album = normalize_album(album)
    options = dict(options or {})
    platform = album.get("platform") or ""
    return {
        "schema": 1,
        "platform": platform,
        "api_source": SOURCE_PLATFORM_ALIASES.get(platform, platform),
        "album_id": _album_source_id(album),
        "title": album.get("title") or "",
        "author": album.get("author") or "",
        "anchor": album.get("anchor") or album.get("nickname") or album.get("announcer") or "",
        "cover": album.get("cover") or "",
        "intro": album.get("intro") or album.get("description") or album.get("desc") or "",
        "episodes": album.get("episodes") or 0,
        "task_id": task_id,
        "quality": options.get("quality") or "",
        "voice": options.get("voice") or {},
        "saved_at": time.time(),
    }


def _write_album_source_file(album, options=None, task_id=""):
    try:
        folder = _album_download_folder(album, options)
        if not folder:
            return
        folder.mkdir(parents=True, exist_ok=True)
        payload = _album_source_payload(album, options, task_id)
        (folder / SOURCE_INFO_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.exception("write album source failed")


def tail_text_file(path, limit=300):
    try:
        limit = max(1, min(int(limit or 300), 2000))
    except (TypeError, ValueError):
        limit = 300
    if not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]


@app.get("/api/logs")
def api_logs():
    name = request.args.get("file", "server.log")
    try:
        path = _safe_child_path(log_dir(), name)
    except ValueError as exc:
        return json_error(str(exc), 400)
    return json_ok(file=name, lines=tail_text_file(path, request.args.get("limit", 300)))


@app.get("/api/events")
def api_events():
    prune_background_events()
    events = load_background_events(request.args.get("limit"))
    return json_ok(events=events, count=len(events), max_keep=background_events_max_keep())


@app.delete("/api/events")
def api_clear_events():
    try:
        with background_events_lock:
            if BACKGROUND_EVENTS_FILE.exists():
                BACKGROUND_EVENTS_FILE.unlink()
        return json_ok(cleared=True)
    except Exception as exc:
        return json_error(str(exc), 500)


@app.delete("/api/logs")
def api_clear_logs():
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass
    root = log_dir()
    root.mkdir(parents=True, exist_ok=True)
    cleared = []
    for path in root.glob("*.log*"):
        try:
            path.write_text("", encoding="utf-8")
            cleared.append(path.name)
        except Exception as exc:
            logging.exception("clear log failed: %s", path)
            return json_error(f"清空日志失败：{path.name}: {exc}", 500)
    logging.info("logs cleared by web ui")
    return json_ok(cleared=cleared, max_bytes=LOG_MAX_BYTES, backups=LOG_BACKUP_COUNT)


@app.get("/api/logs/files")
def api_log_files():
    root = log_dir()
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for path in root.glob("*.log"):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return json_ok(files=files)


@app.get("/api/cookies")
def api_get_cookies():
    """获取各平台已保存的 Cookie 状态"""
    cookie_manager.load()
    platforms = ["xmly", "lrts", "qidian", "qtfm", "fanqie", "fanqie_tingshu", "qimao", "yuntu", "kuwo", "netease", "lizhi"]
    result = {}
    for p in platforms:
        cookie = cookie_manager.get_cookie(p)
        server = cookie_manager.get_server_cookie_cache(p)
        result[p] = {
            "has_cookie": bool(cookie),
            "has_server": bool(server),
            "length": len(str(cookie)) if cookie else 0,
            **_cookie_account_display(p, cookie),
        }
        if p == "xmly":
            mobile_status = ximalaya_mobile_credential_status(
                cookie_manager.get_cookie(MOBILE_CREDENTIAL_PLATFORM)
            )
            dynamic_provider = bool(str(os.environ.get("XIMALAYA_TICKET_PROVIDER_URL") or "").strip())
            if dynamic_provider:
                local_ready = bool(mobile_status.get("local_ticket_ready"))
                mobile_status = {
                    **mobile_status,
                    "state": "local_with_bridge" if local_ready else "dynamic_provider",
                    "message": (
                        "已启用本地 x-tk，Bridge 将在本地会话失效时自动兜底"
                        if local_ready else
                        "已配置 AudioFlow Bridge，下载时会为每次请求动态获取 x-tk"
                    ),
                    "complete": True,
                    "dynamic_provider": True,
                }
            result[p].update(
                has_web_cookie=has_ximalaya_web_cookie(cookie),
                has_mobile_ticket=mobile_status["complete"],
                mobile_credential=mobile_status,
            )
    return json_ok(cookies=result, config_file=str(cookie_manager.config_file))


def _parse_cookie_pairs(value):
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v not in (None, "")}
    pairs = {}
    for part in str(value or "").replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip()
        val = unquote(val.strip())
        if key and val:
            pairs[key] = val
    return pairs


def _first_nonempty(mapping, *keys):
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value).strip()
    lower = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        value = lower.get(str(key).lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _mask_phone(value):
    text = str(value or "").strip()
    if re.fullmatch(r"1\d{10}", text):
        return f"{text[:3]}****{text[-4:]}"
    return text


def _safe_account_label(value, *, allow_long=False):
    text = _mask_phone(str(value or "").strip().strip('"').strip("'"))
    if not text:
        return ""
    cookieish = (";" in text or "=" in text or "\n" in text or "\r" in text)
    tokenish = len(text) > 40 or bool(re.fullmatch(r"[A-Za-z0-9_\\-]{32,}", text))
    if cookieish or (tokenish and not allow_long):
        return ""
    return text


_xmly_account_cache = {}  # hash(cookie) -> (timestamp, info)
_XMLY_ACCOUNT_TTL = 3600


def _xmly_account_info(cookie):
    """用 Cookie 调喜马拉雅官方 getCurrentUser 接口获取昵称/VIP（带 1 小时缓存，避免每次刷新都请求）。"""
    web_cookie = remove_ximalaya_mobile_ticket(cookie)
    if not web_cookie:
        return {}
    key = hash(str(web_cookie))
    now = time.time()
    cached = _xmly_account_cache.get(key)
    if cached and now - cached[0] < _XMLY_ACCOUNT_TTL:
        return cached[1]
    info = {}
    try:
        info = search_manager.ximalaya_manager.get_account_info(web_cookie) or {}
    except Exception:
        logging.debug("xmly account info fetch failed", exc_info=True)
    _xmly_account_cache[key] = (now, info)
    return info


def _cookie_account_display(platform, cookie):
    if not cookie:
        return {"account_name": "", "account_id": ""}
    platform = str(platform or "").strip()
    if platform == "lrts":
        data = parse_lrts_credentials(cookie)
        account_id = _first_nonempty(data, "userId", "uid", "account")
        name = _first_nonempty(data, "nickname", "nickName", "userName", "account", "phone")
        return {"account_name": _safe_account_label(name) or _safe_account_label(account_id), "account_id": account_id}

    pairs = _parse_cookie_pairs(cookie)
    name = _first_nonempty(
        pairs,
        "nickname", "nickName", "userName", "userNickname", "userNickName",
        "name", "profile_nickname", "displayName",
    )
    account_id = _first_nonempty(
        pairs,
        "uid", "userId", "userid", "user_id", "qingting_id", "ywguid", "YwGuid",
        "_token", "MUSIC_U",
    )
    if platform == "qidian":
        name = _safe_account_label(name) or _safe_account_label(account_id)
    elif platform == "qtfm":
        qingting_id = _first_nonempty(pairs, "qingting_id", "QINGTING_ID", "QingtingId")
        name = _safe_account_label(name) or _safe_account_label(qingting_id)
        account_id = qingting_id or account_id
    elif platform == "xmly":
        token = pairs.get("_token") or ""
        if token and "&" in token:
            account_id = account_id or token.split("&", 1)[0]
        # 用官方接口补充真实昵称与 VIP 状态（cookie 里没有这些信息）
        acc = _xmly_account_info(cookie)
        if acc.get("nickname"):
            name = acc["nickname"]
        else:
            name = _safe_account_label(name) or (_safe_account_label(account_id) if account_id else "")
        return {
            "account_name": name,
            "account_id": _safe_account_label(account_id, allow_long=True),
            "is_vip": bool(acc.get("is_vip")),
            "vip_label": acc.get("vip_label", ""),
            "has_web_cookie": has_ximalaya_web_cookie(cookie),
        }
    elif platform == "netease":
        name = _safe_account_label(name)
    else:
        name = _safe_account_label(name) or _safe_account_label(account_id)
    return {"account_name": name, "account_id": _safe_account_label(account_id, allow_long=True)}


@app.post("/api/cookies")
def api_set_cookie():
    """保存平台 Cookie"""
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "").strip()
    cookie = payload.get("cookie", "").strip()
    if not platform or not cookie:
        return json_error("缺少 platform 或 cookie")
    if platform in ("lrts", "懒人听书"):
        cookie = normalize_lrts_credentials(cookie)
        if not cookie:
            return json_error("懒人听书已改用手机号验证码登录，请使用验证码方式获取凭证")
    elif platform in ("xmly", "ximalaya", "喜马拉雅"):
        # The browser field must never overwrite or carry App credentials.
        cookie = remove_ximalaya_mobile_ticket(cookie)
        if not cookie:
            return json_error("请输入喜马拉雅网页登录 Cookie；移动端请求头请保存到下方独立凭证")
    elif platform in ("qidian", "起点", "起点听书"):
        from src.features.qidian.audio_system import qidian_cookie_header
        cookie = qidian_cookie_header(cookie)
        if not cookie:
            return json_error("未能从输入中提取起点 Cookie，请粘贴 Cookie 字符串或包含 Cookie: 的完整请求头")
    cookie_manager.set_cookie(platform, cookie)
    search_manager.set_cookie(platform, cookie)
    return json_ok(saved=True, platform=platform, config_file=str(cookie_manager.config_file))


@app.post("/api/cookies/xmly/mobile-ticket")
def api_set_ximalaya_mobile_ticket():
    """Save an App Cookie or complete captured request independently."""
    payload = request.get_json(silent=True) or {}
    incoming = payload.get("credentials", payload.get("ticket", ""))
    credential = normalize_ximalaya_mobile_credentials(incoming)
    status = ximalaya_mobile_credential_status(credential)
    if not status["complete"]:
        return json_error(status["message"])
    cookie_manager.set_cookie(MOBILE_CREDENTIAL_PLATFORM, credential)
    search_manager.set_ximalaya_mobile_credentials(credential)
    return json_ok(
        saved=True,
        platform="xmly",
        has_web_cookie=has_ximalaya_web_cookie(cookie_manager.get_cookie("xmly")),
        has_mobile_ticket=True,
        mobile_credential=status,
        config_file=str(cookie_manager.config_file),
    )


def _ximalaya_bridge_request(path, payload):
    ticket_url = str(os.environ.get("XIMALAYA_TICKET_PROVIDER_URL") or "").strip()
    token = str(os.environ.get("XIMALAYA_TICKET_PROVIDER_TOKEN") or "").strip()
    if not ticket_url or not token:
        raise ValueError("未配置喜马拉雅 Bridge，手机号登录暂不可用")
    base_url = ticket_url.rsplit("/ximalaya/ticket", 1)[0].rstrip("/")
    response = requests.post(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=25,
    )
    try:
        body = response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        body = {}
    if response.status_code >= 400 or not body.get("ok", response.status_code < 400):
        raise ValueError(str(body.get("error") or f"Bridge HTTP {response.status_code}"))
    return body


@app.post("/api/cookies/xmly/mobile-login/send-code")
def api_ximalaya_mobile_send_code():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    if not (phone.isdigit() and len(phone) == 11):
        return json_error("请输入 11 位手机号")
    try:
        result = _ximalaya_bridge_request("/ximalaya/login/sms/send", {"phone": phone})
    except (requests.RequestException, ValueError) as exc:
        return json_error(f"发送验证码失败：{exc}")
    return json_ok(message=result.get("message") or "验证码已发送")


@app.post("/api/cookies/xmly/mobile-login/verify")
def api_ximalaya_mobile_login_verify():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    if not (phone.isdigit() and len(phone) == 11 and code.isdigit()):
        return json_error("请输入手机号和短信验证码")
    try:
        result = _ximalaya_bridge_request(
            "/ximalaya/login/sms/verify", {"phone": phone, "code": code}
        )
        # The official App normally starts account requests immediately after
        # login. Give the Bridge a short window to capture the new Cookie and
        # combine it with a freshly generated x-tk.
        credential = {}
        for _ in range(8):
            time.sleep(0.5)
            try:
                candidate = _ximalaya_bridge_request(
                    "/ximalaya/ticket",
                    {"track_id": "1", "quality_level": 3},
                )
                credential = normalize_ximalaya_mobile_credentials(candidate)
                if ximalaya_mobile_credential_status(credential)["complete"]:
                    break
            except (requests.RequestException, ValueError):
                continue
        status = ximalaya_mobile_credential_status(credential)
        if status["complete"]:
            cookie_manager.set_cookie(MOBILE_CREDENTIAL_PLATFORM, credential)
            search_manager.set_ximalaya_mobile_credentials(credential)
            return json_ok(message="移动端登录成功，V4 凭证已保存", mobile_credential=status)
        return json_ok(
            message=(result.get("message") or "移动端登录成功") + "；请在 App 播放任意一集后再下载一次",
            mobile_credential=status,
            needs_playback=True,
        )
    except (requests.RequestException, ValueError) as exc:
        return json_error(f"验证码登录失败：{exc}")


@app.delete("/api/cookies/xmly/mobile-ticket")
def api_delete_ximalaya_mobile_ticket():
    """Delete only the App credential bundle and retain browser login."""
    cookie_manager.delete_cookie(MOBILE_CREDENTIAL_PLATFORM)
    search_manager.set_ximalaya_mobile_credentials({})
    cookie = cookie_manager.get_cookie("xmly")
    return json_ok(
        deleted=True,
        platform="xmly",
        has_web_cookie=has_ximalaya_web_cookie(cookie),
        has_mobile_ticket=False,
        config_file=str(cookie_manager.config_file),
    )


@app.delete("/api/cookies/<platform>")
def api_delete_cookie(platform):
    platform = (platform or "").strip()
    if not platform:
        return json_error("缺少 platform")
    cookie_manager.delete_cookie(platform)
    try:
        search_manager.set_cookie(platform, "")
    except Exception:
        pass
    return json_ok(deleted=True, platform=platform, config_file=str(cookie_manager.config_file))


_COOKIE_EXPORT_PLATFORMS = [
    "xmly",
    MOBILE_CREDENTIAL_PLATFORM,
    "lrts",
    "qidian",
    "qtfm",
    "fanqie",
    "fanqie_tingshu",
    "qimao",
    "yuntu",
    "kuwo",
    "netease",
    "lizhi",
]


@app.get("/api/cookies/export")
def api_export_cookies():
    """导出所有平台的明文凭证，包括喜马拉雅移动版 V4 凭证。"""
    cookie_manager.load()
    data = {}
    for p in _COOKIE_EXPORT_PLATFORMS:
        cookie = cookie_manager.get_cookie(p)
        if cookie:
            data[p] = cookie if isinstance(cookie, str) else json.dumps(cookie, ensure_ascii=False)
    return json_ok(cookies=data, count=len(data))


def _apply_cookie_import(incoming):
    """把 {平台: cookie} 写入 cookie_manager + search_manager，返回 (imported, skipped)。"""
    imported, skipped = [], []
    for platform, cookie in (incoming or {}).items():
        platform = str(platform or "").strip()
        if not platform or not cookie:
            continue
        value = (cookie if isinstance(cookie, str) else json.dumps(cookie, ensure_ascii=False)).strip()
        if platform in ("lrts", "懒人听书"):
            value = normalize_lrts_credentials(value)
            if not value:
                skipped.append(platform)
                continue
        elif platform == MOBILE_CREDENTIAL_PLATFORM:
            # Mobile request headers are deliberately excluded from normal
            # Cookie backup/import. They must pass the dedicated validator.
            skipped.append(platform)
            continue
        elif platform in ("xmly", "ximalaya", "喜马拉雅"):
            value = remove_ximalaya_mobile_ticket(value)
            if not value:
                skipped.append(platform)
                continue
        cookie_manager.set_cookie(platform, value)
        try:
            search_manager.set_cookie(platform, value)
        except Exception:
            pass
        imported.append(platform)
    return imported, skipped


@app.post("/api/cookies/import")
def api_import_cookies():
    """批量导入 Cookie：接收 {平台: cookie} 的 JSON（cookies 可为对象或 JSON 字符串）。"""
    payload = request.get_json(silent=True) or {}
    incoming = payload.get("cookies")
    if isinstance(incoming, str):
        try:
            incoming = json.loads(incoming)
        except Exception:
            return json_error("导入文本不是合法的 JSON")
    if not isinstance(incoming, dict) or not incoming:
        return json_error("导入数据应为 {平台: cookie} 的 JSON 对象")
    imported, skipped = _apply_cookie_import(incoming)
    return json_ok(imported=imported, skipped=skipped, count=len(imported))


def _collect_export_cookies():
    cookie_manager.load()
    out = {}
    for p in _COOKIE_EXPORT_PLATFORMS:
        c = cookie_manager.get_cookie(p)
        if c:
            out[p] = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    return out


@app.get("/api/backup/export")
def api_export_backup():
    """一键全量备份：Cookie + 订阅 + 订阅设置 打包成一个 JSON。"""
    backup = {
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cookies": _collect_export_cookies(),
        "subscriptions": subscription_manager.export_subscriptions(),
        "subscription_settings": subscription_manager.settings(),
    }
    return json_ok(backup=backup)


@app.post("/api/backup/import")
def api_import_backup():
    """从全量备份恢复 Cookie + 订阅 + 订阅设置（各部分按存在与否分别恢复）。"""
    payload = request.get_json(silent=True) or {}
    backup = payload.get("backup")
    if isinstance(backup, str):
        try:
            backup = json.loads(backup)
        except Exception:
            return json_error("导入文本不是合法的 JSON")
    if isinstance(backup, dict) and isinstance(backup.get("backup"), dict):
        backup = backup["backup"]
    if not isinstance(backup, dict):
        return json_error("导入数据应为备份 JSON")
    result = {"cookies": 0, "subscriptions": 0, "subscription_settings": False}
    if isinstance(backup.get("cookies"), dict):
        imported, _ = _apply_cookie_import(backup["cookies"])
        result["cookies"] = len(imported)
    if isinstance(backup.get("subscriptions"), list):
        dl = active_download_dir()
        for rec in backup["subscriptions"]:
            if isinstance(rec, dict):
                rec["download_dir"] = dl
        result["subscriptions"] = subscription_manager.import_subscriptions(backup["subscriptions"])
    if isinstance(backup.get("subscription_settings"), dict):
        subscription_manager.update_settings(**backup["subscription_settings"])
        result["subscription_settings"] = True
    try:
        refresh_subscription_stats_async(min_interval=0)
    except Exception:
        pass
    return json_ok(**result)


PERSONAL_COOKIE_KEYS = {
    "ximalaya": "personal_xmly",
    "xmly": "personal_xmly",
    "lrts": "personal_lrts",
    "qidian": "personal_qidian",
}

PERSONAL_QR_COOKIE_KEYS = {
    "ximalaya": "personal_xmly",
    "qidian": "personal_qidian",
}


def _personal_cookie_key(platform):
    return PERSONAL_COOKIE_KEYS.get(str(platform or "").strip())


def _get_personal_cookie(platform):
    key = _personal_cookie_key(platform)
    return cookie_manager.get_cookie(key) if key else ""


def _personal_cookie_status(platform):
    key = _personal_cookie_key(platform)
    cookie = cookie_manager.get_cookie(key) if key else ""
    display_key = "xmly" if platform == "ximalaya" else platform
    return {
        "has_cookie": bool(cookie),
        "length": len(str(cookie)) if cookie else 0,
        **_cookie_account_display(display_key, cookie),
    }


@app.get("/api/personal/cookies")
def api_personal_cookies():
    cookie_manager.load()
    result = {}
    for platform in ("ximalaya", "lrts", "qidian"):
        result[platform] = _personal_cookie_status(platform)
    return json_ok(cookies=result, config_file=str(cookie_manager.config_file))


@app.post("/api/personal/cookies")
def api_set_personal_cookie():
    payload = request.get_json(silent=True) or {}
    platform = str(payload.get("platform") or "").strip()
    cookie = str(payload.get("cookie") or "").strip()
    key = _personal_cookie_key(platform)
    if not key:
        return json_error("不支持的平台")
    if not cookie:
        return json_error("缺少 Cookie 或凭证")
    if platform == "lrts":
        cookie = normalize_lrts_credentials(cookie)
        if not cookie:
            return json_error("懒人听书个人中心需要 App 凭证，请使用验证码登录或粘贴 token/imei")
    elif platform == "ximalaya":
        cookie = merge_ximalaya_credentials(cookie_manager.get_cookie(key), cookie)
    cookie_manager.set_cookie(key, cookie)
    return json_ok(saved=True, platform=platform, key=key, info=_personal_cookie_status(platform), config_file=str(cookie_manager.config_file))


@app.delete("/api/personal/cookies/<platform>")
def api_delete_personal_cookie(platform):
    key = _personal_cookie_key(platform)
    if not key:
        return json_error("不支持的平台")
    cookie_manager.delete_cookie(key)
    return json_ok(deleted=True, platform=platform, key=key, config_file=str(cookie_manager.config_file))


@app.post("/api/cookies/clear")
def api_clear_cookies():
    cookie_manager.clear_all_cookies()
    return json_ok(cleared=True, config_file=str(cookie_manager.config_file))


# LRTS SMS credential login -------------------------------------------------
_LRTS_LOGIN_DEVICE_KEY = "_lrts_login_device_id"
_LRTS_LOGIN_DEVICE_LOCK = threading.Lock()


def _lrts_login_device_id():
    with _LRTS_LOGIN_DEVICE_LOCK:
        for key in ("personal_lrts", "lrts"):
            credential = parse_lrts_credentials(cookie_manager.get_cookie(key))
            imei = str(credential.get("imei") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", imei):
                return imei
        saved = str(cookie_manager.get_cookie(_LRTS_LOGIN_DEVICE_KEY) or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", saved):
            return saved
        saved = uuid.uuid4().hex[:16]
        cookie_manager.set_cookie(_LRTS_LOGIN_DEVICE_KEY, saved)
        return saved


@app.get("/api/lrts/check")
def api_lrts_check():
    credential = parse_lrts_credentials(cookie_manager.get_cookie("lrts"))
    if not credential.get("token") or not credential.get("imei"):
        return json_ok(ok=False, logged_in=False, is_vip=False, message="未检测到懒人听书 App 凭证，请先用手机号验证码登录")
    try:
        search_manager.set_cookie("lrts", credential)
        probe = search_manager.lrts_manager._client_or_guest().book_search("测试", page_size=1)
        valid = probe.get("status") == 0
    except Exception as exc:
        return json_ok(ok=False, logged_in=False, is_vip=False, message=f"懒人听书凭证校验失败：{exc}")
    if not valid:
        return json_ok(ok=False, logged_in=False, is_vip=False, message=f"懒人听书凭证无效：{probe.get('msg') or probe.get('status')}")
    vip_expire = str(credential.get("vipExpireTime") or "")
    return json_ok(
        ok=True,
        logged_in=True,
        is_vip=bool(vip_expire),
        uid=str(credential.get("userId") or ""),
        user_info={
            "uid": str(credential.get("userId") or ""),
            "phone": str(credential.get("phone") or ""),
            "nickname": str(credential.get("nickname") or ""),
            "vip_expire": vip_expire,
        },
        message="懒人听书 App 凭证有效" + (f"，VIP 到期：{vip_expire}" if vip_expire else ""),
    )


@app.post("/api/lrts/send-code")
def api_lrts_send_code():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    swipe_ticket = str(payload.get("swipe_ticket") or "").strip()
    randstr = str(payload.get("randstr") or "").strip()
    if not re.fullmatch(r"1\d{10}", phone):
        return json_error("请输入正确的 11 位手机号")
    try:
        data = lrts_send_sms_code(
            phone,
            session_id=session_id,
            swipe_ticket=swipe_ticket,
            randstr=randstr,
            imei=_lrts_login_device_id(),
        )
    except LrtsLoginSessionError as exc:
        return json_error(str(exc))
    except Exception:
        logging.exception("lrts send sms failed")
        return json_error("发送验证码失败，请检查服务端网络后重试", status=500)
    if data.get("_requires_slider"):
        return json_ok(
            requires_slider=True,
            message=data.get("msg") or "请完成滑动验证后继续",
            session_id=data.get("_session_id", ""),
            captcha_app_id=data.get("_captcha_app_id", ""),
            captcha_script_url=data.get("_captcha_script_url", ""),
        )
    if str(data.get("status")) != "0":
        return json_error(data.get("msg") or f"发送验证码失败：status={data.get('status')}")
    return json_ok(message="验证码已发送", session_id=data.get("_session_id", ""))


@app.post("/api/lrts/login")
def api_lrts_login():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    imei = str(payload.get("imei") or "").strip()
    temp_token = str(payload.get("temp_token") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not phone or not code:
        return json_error("请输入手机号和验证码")
    try:
        data, credential = lrts_sms_login(
            phone,
            code,
            imei=imei,
            temp_token=temp_token,
            session_id=session_id,
        )
    except LrtsLoginSessionError as exc:
        return json_error(str(exc))
    except Exception:
        logging.exception("lrts sms login failed")
        return json_error("验证码登录失败，请检查服务端网络后重试", status=500)
    if str(data.get("status")) != "0" or not credential:
        return json_error(data.get("msg") or f"验证码登录失败：status={data.get('status')}")
    cookie_manager.set_cookie("lrts", credential)
    search_manager.set_cookie("lrts", credential)
    return json_ok(message="懒人听书登录成功", credential_saved=True, userId=data.get("userId"), nickname=data.get("nickname") or data.get("nickName", ""))


@app.post("/api/personal/lrts/login")
def api_personal_lrts_login():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    imei = str(payload.get("imei") or "").strip()
    temp_token = str(payload.get("temp_token") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not phone or not code:
        return json_error("请输入手机号和验证码")
    try:
        data, credential = lrts_sms_login(
            phone,
            code,
            imei=imei,
            temp_token=temp_token,
            session_id=session_id,
        )
    except LrtsLoginSessionError as exc:
        return json_error(str(exc))
    except Exception:
        logging.exception("personal lrts sms login failed")
        return json_error("验证码登录失败，请检查服务端网络后重试", status=500)
    if str(data.get("status")) != "0" or not credential:
        return json_error(data.get("msg") or f"验证码登录失败：status={data.get('status')}")
    cookie_manager.set_cookie("personal_lrts", credential)
    return json_ok(
        message="懒人听书个人中心登录成功",
        credential_saved=True,
        userId=data.get("userId"),
        nickname=data.get("nickname") or data.get("nickName", ""),
        info=_personal_cookie_status("lrts"),
    )


# Generic QR login for other platforms --------------------------------------
_PLATFORM_COOKIE_KEY = {
    "ximalaya": "xmly", "qidian": "qidian", "qtfm": "qtfm", "netease": "netease",
}


def _cookies_to_string(cookies):
    if isinstance(cookies, str):
        return cookies
    if isinstance(cookies, dict):
        if "_cookie_str" in cookies:
            return cookies["_cookie_str"]
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v and not k.startswith("_"))
    return ""


@app.post("/api/qr/start")
def api_qr_start():
    from core.qr_login import manager as qr_manager
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "").strip()
    if platform == "lrts":
        return json_error("懒人听书已改用手机号验证码登录")
    try:
        session = qr_manager.start(platform)
    except ValueError as exc:
        return json_error(str(exc))
    return json_ok(session_id=session.id, platform=platform)


@app.get("/api/qr/poll/<sid>")
def api_qr_poll(sid):
    from core.qr_login import manager as qr_manager
    session = qr_manager.get(sid)
    if not session:
        return json_error("会话不存在或已过期", 404)
    snap = session.snapshot()
    if snap["status"] == "success" and snap.get("cookies"):
        cookie_str = _cookies_to_string(snap["cookies"])
        key = _PLATFORM_COOKIE_KEY.get(snap["platform"])
        if cookie_str and key:
            try:
                if key == "xmly":
                    cookie_str = remove_ximalaya_mobile_ticket(cookie_str)
                cookie_manager.set_cookie(key, cookie_str)
                search_manager.set_cookie(key, cookie_str)
                snap["saved_to"] = str(cookie_manager.config_file)
            except Exception as exc:
                snap["save_error"] = str(exc)
    return json_ok(session=snap)


@app.get("/api/personal/qr/poll/<sid>")
def api_personal_qr_poll(sid):
    from core.qr_login import manager as qr_manager
    session = qr_manager.get(sid)
    if not session:
        return json_error("会话不存在或已过期", 404)
    snap = session.snapshot()
    if snap["status"] == "success" and snap.get("cookies"):
        cookie_str = _cookies_to_string(snap["cookies"])
        key = PERSONAL_QR_COOKIE_KEYS.get(snap["platform"])
        if cookie_str and key:
            try:
                if key == "personal_xmly":
                    cookie_str = merge_ximalaya_credentials(cookie_manager.get_cookie(key), cookie_str)
                cookie_manager.set_cookie(key, cookie_str)
                snap["saved_to"] = str(cookie_manager.config_file)
            except Exception as exc:
                snap["save_error"] = str(exc)
    return json_ok(session=snap)


@app.post("/api/qr/cancel/<sid>")
def api_qr_cancel(sid):
    from core.qr_login import manager as qr_manager
    ok = qr_manager.cancel(sid)
    return json_ok(cancelled=ok)


# ── 懒人听书反向代理登录 ──────────────────────────────────────────────────────
# 原理：用户通过 /lrts-proxy/ 访问 m.lrts.me，后端代理所有请求并捕获 Cookie。
# 登录成功后（检测到 session Cookie），自动保存并通知前端。

@app.get("/api/cookies/script/<platform>")
def api_cookie_script(platform):
    """返回该平台的浏览器抓取脚本与说明。"""
    scripts = {
        "xmly": {
            "name": "喜马拉雅",
            "login_url": "https://www.ximalaya.com/",
            "script": (
                "/* 喜马拉雅 Cookie 抓取脚本 */\n"
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "qidian": {
            "name": "起点听书",
            "login_url": "https://www.qidian.com/",
            "script": (
                "/* 起点 Cookie 抓取脚本 */\n"
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "qtfm": {
            "name": "蜻蜓FM",
            "login_url": "https://www.qtfm.cn/",
            "script": (
                "/* 蜻蜓FM Cookie 抓取脚本 */\n"
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "fanqie": {
            "name": "番茄畅听",
            "login_url": "https://fanqienovel.com/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "yuntu": {
            "name": "云听FM",
            "login_url": "https://www.radio.cn/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "kuwo": {
            "name": "酷我听书",
            "login_url": "https://www.kuwo.cn/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
        "netease": {
            "name": "网易云听书",
            "login_url": "https://music.163.com/",
            "script": (
                "/* 网易云音乐 Cookie 抓取脚本 */\n"
                "(function(){var c=document.cookie;"
                "prompt('请复制下面这段 Cookie 后回到AudioFlow粘贴：', c);}())"
            ),
        },
    }
    info = scripts.get(platform)
    if not info:
        return json_error("不支持的平台")
    return json_ok(**info)


# ── 个人中心 ──────────────────────────────────
@app.get("/api/personal/<platform>/<feature>")
def api_personal(platform, feature):
    """获取个人中心数据（复用桌面版 UserDataWorker 逻辑）"""
    try:
        if platform == "ximalaya":
            items = _load_ximalaya_personal(feature, all_pages=(feature == "subscriptions"))
        elif platform == "lrts":
            items = _load_lrts_personal(feature)
        elif platform == "qidian":
            items = _load_qidian_personal(feature)
        else:
            return json_error(f"不支持的平台: {platform}")
        return json_ok(items=items, platform=platform, feature=feature)
    except RuntimeError as e:
        return json_error(str(e), status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return json_error(str(e), status=500)


def _load_ximalaya_personal(feature, all_pages=False):
    cookie = _get_personal_cookie("ximalaya")
    if not cookie:
        raise RuntimeError("请先在个人中心为喜马拉雅登录或粘贴 Cookie")
    from core.ximalaya_manager import XimalayaManager
    api = XimalayaManager()
    api.set_cookie(cookie)
    endpoints = {
        "history": "https://www.ximalaya.com/revision/track/history/listen?includeChannel=false&includeRadio=false",
        "liked": "https://www.ximalaya.com/revision/my/getLikeTracks",
        "subscriptions": "https://www.ximalaya.com/revision/album/v1/sub/comprehensive?subType=2&category=all",
        "purchased": "https://www.ximalaya.com/revision/my/getHasBroughtAlbums?pageNum=1&pageSize=30",
    }
    url = endpoints.get(feature)
    if not url:
        return []
    items = []
    if feature == "subscriptions":
        # Ximalaya currently caps this endpoint around 30 records per page.
        # Requesting a larger size can still return 30, so use the cap as the
        # page size and keep paging until an empty/repeated/short page.
        page_size = 30
        max_pages = 50 if all_pages else 1
        seen_ids = set()
        for page in range(1, max_pages + 1):
            resp = api.session.get(url, params={"num": page, "size": page_size}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("data", data) if isinstance(data, dict) else {}
            if not isinstance(content, dict):
                break
            page_items = _extract_ximalaya_personal_items(content, feature)
            new_items = []
            for item in page_items:
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                new_items.append(item)
            items.extend(new_items)
            total = _to_int(content.get("total") or content.get("totalCount") or content.get("count"), 0)
            if not page_items or not new_items or len(page_items) < page_size or (total and len(items) >= total):
                break
        return [it for it in items if it.get("id") and it.get("title")]

    resp = api.session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("data", data) if isinstance(data, dict) else {}
    if not isinstance(content, dict):
        return []
    items = _extract_ximalaya_personal_items(content, feature)
    return [it for it in items if it.get("id") and it.get("title")]


def _extract_ximalaya_personal_items(content, feature=""):
    items = []
    # albumsInfo
    for album in content.get("albumsInfo", []) or []:
        items.append(_normalize_personal_item({
            "id": str(album.get("id", "")),
            "title": album.get("title") or album.get("albumTitle", ""),
            "author": _pick_ximalaya_author(album),
            "cover": album.get("coverPath", ""),
            "episodes": album.get("trackCount", 0),
            "plays": album.get("playCount", 0),
        }, "喜马拉雅"))
    # albumList
    for album in content.get("albumList", []) or []:
        anchor = album.get("anchor") if isinstance(album.get("anchor"), dict) else {}
        items.append(_normalize_personal_item({
            "id": str(album.get("albumId", "")),
            "title": album.get("albumTitle") or album.get("title", ""),
            "author": _pick_ximalaya_author(album) or _pick_ximalaya_author(anchor),
            "cover": album.get("coverPath", ""),
            "episodes": album.get("trackCount", 0),
            "plays": album.get("playCount", 0),
        }, "喜马拉雅"))
    # tracksList
    for track in content.get("tracksList", []) or []:
        items.append(_normalize_personal_item({
            "id": str(track.get("albumId", "")),
            "title": track.get("albumName") or track.get("trackTitle", ""),
            "author": _pick_ximalaya_author(track),
            "cover": track.get("trackCoverPath", ""),
        }, "喜马拉雅"))
    # history groups
    for group in ("today", "yesterday", "earlier"):
        for record in content.get(group, []) or []:
            items.append(_normalize_personal_item({
                "id": str(record.get("itemId", "")),
                "title": record.get("itemTitle") or record.get("albumTitle") or record.get("childTitle", ""),
                "author": _pick_ximalaya_author(record),
            "cover": record.get("itemCoverUrl") or record.get("itemSquareCoverUrl", ""),
        }, "喜马拉雅"))
    return items


def _pick_ximalaya_author(item):
    if not isinstance(item, dict):
        return ""
    keys = (
        "anchorNickName", "anchorNickname", "anchorName", "AnchorName",
        "nickname", "nickName", "userName", "userNickname", "userNickName",
        "author", "authorName", "announcer", "speaker", "artist",
    )
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    for key in ("anchor", "anchorInfo", "announcerInfo", "user", "userInfo", "creator", "album", "item", "data", "raw"):
        nested = item.get(key)
        if isinstance(nested, dict):
            value = _pick_ximalaya_author(nested)
            if value:
                return value
    return ""


def _load_lrts_personal(feature):
    credential_text = _get_personal_cookie("lrts")
    if not credential_text:
        raise RuntimeError("请先在个人中心为懒人听书登录或粘贴凭证")
    credential = parse_lrts_credentials(credential_text)
    if not credential.get("token") or not credential.get("imei"):
        raise RuntimeError("懒人听书个人中心需要有效的 App token/imei 凭证，请重新登录")
    from core.lrts_manager import LRTSManager
    api = LRTSManager()
    api.set_cookie(credential)
    client = api._client_or_guest()
    user_id = credential.get("userId") or credential.get("uid") or 0
    return _load_lrts_personal_from_app(client, feature, user_id=user_id)


def _iter_personal_records(data):
    if isinstance(data, list):
        yield from data
        return
    if not isinstance(data, dict):
        return
    for key in (
        "list", "booksInfo", "bookList", "albumList", "ablumnList",
        "resourceList", "records", "items", "favorites", "resultList",
    ):
        value = data.get(key)
        if isinstance(value, list):
            yield from value
    for key in ("data", "result", "payload"):
        inner = data.get(key)
        if isinstance(inner, (dict, list)):
            yield from _iter_personal_records(inner)


def _lrts_response_value(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    for container_key in ("data", "result", "payload"):
        value = _lrts_response_value(data.get(container_key), *keys)
        if value not in (None, ""):
            return value
    return None


def _lrts_personal_response(data, label):
    if not isinstance(data, dict):
        raise RuntimeError(f"懒人听书{label}接口返回了无法识别的数据")
    status = _lrts_response_value(data, "status")
    if status in (None, "", 0, "0"):
        return data
    message = str(_lrts_response_value(data, "msg", "message", "errorMsg") or "").strip()
    error_text = f"{status} {message}".lower()
    if any(word in error_text for word in ("token", "login", "登录", "登陆", "认证", "未授权", "过期", "失效")):
        raise RuntimeError("懒人听书登录凭证已失效，请在个人中心重新登录")
    detail = message or f"status={status}"
    raise RuntimeError(f"懒人听书{label}获取失败：{detail}")


def _call_lrts_personal(label, callback):
    try:
        return _lrts_personal_response(callback(), label)
    except requests.RequestException as exc:
        raise RuntimeError(f"懒人听书{label}网络请求失败：{exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"懒人听书{label}接口返回了无效 JSON") from exc


def _lrts_entity_type(item, fallback=1):
    if item.get("ablumnId") or item.get("albumId"):
        return 2
    raw_type = item.get("baseEntityType") or item.get("entityType")
    entity_type = _to_int(raw_type, fallback)
    if entity_type == 4:
        return 1
    return entity_type if entity_type in (1, 2) else fallback


def _normalize_lrts_personal_record(item, entity_type=None):
    if not isinstance(item, dict):
        return None
    normalized_type = _lrts_entity_type(item, _to_int(entity_type, 1)) if entity_type is None else _to_int(entity_type, 1)
    if normalized_type == 2:
        entity_id = (
            item.get("ablumnId") or item.get("albumId") or item.get("bookId")
            or item.get("baseEntityId") or item.get("entityId") or item.get("id")
        )
    else:
        entity_id = (
            item.get("bookId") or item.get("baseEntityId") or item.get("entityId")
            or item.get("id")
        )
    if not entity_id:
        return None
    title = (
        item.get("name") or item.get("bookName") or item.get("ablumnName") or item.get("albumName")
        or item.get("entityName") or item.get("title") or item.get("resName")
    )
    if not title:
        return None
    normalized = _normalize_personal_item({
        "id": f"{normalized_type}:{entity_id}",
        "title": title,
        "author": (
            item.get("author") or item.get("authorName") or item.get("anchorName")
            or item.get("announcerName") or item.get("nickname") or item.get("nickName")
            or item.get("announcer") or item.get("userNick") or ""
        ),
        "cover": item.get("cover") or item.get("coverUrl") or item.get("coverPath") or item.get("bestCover") or item.get("pic") or "",
        "episodes": item.get("sections") or item.get("sum") or item.get("countTrack") or item.get("chapterCount") or item.get("audioCount") or 0,
        "plays": item.get("plays") or item.get("playCount") or item.get("play") or 0,
        "description": item.get("desc") or item.get("description") or "",
    }, "懒人听书")
    normalized["plays"] = _to_int(item.get("plays") or item.get("playCount") or item.get("hot"), 0)
    normalized["_lrts_entity_type"] = normalized_type
    normalized["_lrts_entity_id"] = _to_int(entity_id, 0)
    return normalized


def _append_lrts_records(items, seen, records, entity_type=None):
    added = 0
    for record in records:
        item = _normalize_lrts_personal_record(record, entity_type=entity_type)
        if not item:
            continue
        key = item["id"]
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        added += 1
    return added


def _load_lrts_history(client, items, seen, max_pages=200):
    cursor = ""
    requested = set()
    for _ in range(max_pages):
        if cursor in requested:
            return
        requested.add(cursor)
        data = _call_lrts_personal("收听记录", lambda cursor=cursor: client.recent_listens(cursor, 101))
        records = list(_iter_personal_records(data))
        _append_lrts_records(items, seen, records)
        next_cursor = str(_lrts_response_value(data, "referId") or "").strip()
        if not records or next_cursor in ("", "0", cursor):
            return
        cursor = next_cursor
    raise RuntimeError("懒人听书收听记录分页超过安全上限，未能完整加载")


def _load_lrts_published(client, method_name, label, entity_type, user_id, items, seen, max_pages=200):
    page_size = 20
    cursor = 0
    op_type = "H"
    requested = set()
    endpoint_seen = set()
    for _ in range(max_pages):
        marker = (op_type, str(cursor))
        if marker in requested:
            return
        requested.add(marker)
        method = getattr(client, method_name)
        data = _call_lrts_personal(
            label,
            lambda method=method, cursor=cursor, op_type=op_type: method(
                user_id=user_id,
                refer_id=cursor,
                op_type=op_type,
                size=page_size,
            ),
        )
        records = list(_iter_personal_records(data))
        _append_lrts_records(items, seen, records, entity_type=entity_type)
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = record.get("bookId") if entity_type == 1 else (record.get("ablumnId") or record.get("albumId") or record.get("id"))
            if record_id:
                endpoint_seen.add(str(record_id))
        total = _to_int(_lrts_response_value(data, "size", "total", "totalCount"), 0)
        if not records or (total and len(endpoint_seen) >= total):
            return
        last = records[-1] if isinstance(records[-1], dict) else {}
        next_cursor = (
            last.get("bookId") if entity_type == 1
            else (last.get("ablumnId") or last.get("albumId") or last.get("id"))
        )
        if not next_cursor or str(next_cursor) == str(cursor):
            return
        cursor = next_cursor
        op_type = "T"
    raise RuntimeError(f"懒人听书{label}分页超过安全上限，未能完整加载")


def _load_lrts_personal_from_app(client, feature, user_id=0):
    items = []
    seen = set()
    if feature == "history":
        _load_lrts_history(client, items, seen)
    elif feature == "favorites":
        data = _call_lrts_personal("我的收藏", lambda: client.collection_books(11))
        _append_lrts_records(items, seen, _iter_personal_records(data), entity_type=1)
    elif feature == "programs":
        _load_lrts_published(client, "published_books", "我的书籍节目", 1, user_id, items, seen)
        _load_lrts_published(client, "published_albums", "我的专辑节目", 2, user_id, items, seen)
    else:
        raise RuntimeError(f"不支持的懒人听书个人中心功能: {feature}")
    return items


QIDIAN_BOOKSHELF_URL = "https://wxapp.qidian.com/api/bookShelf/list"
QIDIAN_BOOKSHELF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _is_qidian_audio_book(book):
    """Only explicitly typed audiobook records are safe to show as albums."""
    return isinstance(book, dict) and _to_int(book.get("bookType"), 0) == 2


def _qidian_bookshelf_cover(book):
    cover = _pick_nested_value(
        book or {},
        (
            "cover", "coverUrl", "cover_url", "CoverUrl", "bookCover",
            "book_cover", "image", "imageUrl", "pic", "picUrl", "bookImg",
        ),
        ("audioInfo", "audioBook", "bookInfo", "data"),
    )
    cover = str(cover or "").strip()
    if cover:
        if cover.startswith("//"):
            cover = "https:" + cover
        elif cover.startswith("/"):
            cover = "https://bookcover.yuewen.com" + cover
        elif not urlparse(cover).scheme:
            cover = "https://bookcover.yuewen.com/" + cover.lstrip("/")
        return normalize_cover_url(cover, "起点听书")

    book_id = str((book or {}).get("bookId") or "").strip()
    if re.fullmatch(r"\d+", book_id):
        return f"https://bookcover.yuewen.com/qdbimg/349573/{book_id}/180"
    return ""


def _load_qidian_audio_bookshelf(api, page_size=50, max_pages=100):
    items = []
    seen_book_ids = set()
    loaded_count = 0

    for page in range(1, max_pages + 1):
        try:
            response = api.qidian_session.get(
                QIDIAN_BOOKSHELF_URL,
                params={"page": page, "pageSize": page_size},
                headers=QIDIAN_BOOKSHELF_HEADERS,
                cookies=api.qidian_cookies or None,
                timeout=15,
                verify=False,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError("起点书架连接失败，请稍后重试") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("起点书架返回了无法解析的数据，请稍后重试") from exc

        if not isinstance(data, dict):
            raise RuntimeError("起点书架返回的数据格式异常，请稍后重试")
        if _to_int(data.get("code"), -1) != 0:
            message = str(data.get("msg") or "").strip()
            raise RuntimeError(message or f"起点书架获取失败：code={data.get('code')}")

        payload = data.get("data") or {}
        if not isinstance(payload, dict):
            raise RuntimeError("起点书架返回的数据格式异常，请稍后重试")
        books = payload.get("booksInfo") or []
        if not isinstance(books, list):
            raise RuntimeError("起点书架返回的数据格式异常，请稍后重试")

        loaded_count += len(books)
        for book in books:
            if not _is_qidian_audio_book(book):
                continue
            book_id = str(book.get("bookId") or "").strip()
            if not book_id or book_id in seen_book_ids:
                continue
            seen_book_ids.add(book_id)
            items.append(book)

        page_info = payload.get("pageInfo") or {}
        if not isinstance(page_info, dict):
            page_info = {}
        page_count = _to_int(page_info.get("pageCount"), 0)
        total_count = _to_int(page_info.get("totalCount"), 0)

        if not books:
            return items
        if page_count:
            if page >= page_count:
                return items
        elif total_count:
            if loaded_count >= total_count:
                return items
        elif len(books) < page_size:
            return items

    raise RuntimeError("起点书架分页超过安全上限，未能完整加载")


def _load_qidian_personal(feature):
    if feature != "favorites":
        raise RuntimeError(f"不支持的起点听书个人中心功能: {feature}")
    cookie = _get_personal_cookie("qidian")
    if not cookie:
        raise RuntimeError("请先在个人中心为起点听书登录或粘贴 Cookie")
    from core.search_manager import SearchManager
    api = SearchManager()
    api.set_qidian_cookie(cookie)
    items = []
    try:
        account = api.get_qidian_user_account()
        if not account:
            raise RuntimeError("起点账号校验失败，请在个人中心重新扫码或粘贴 Cookie")
        for book in _load_qidian_audio_bookshelf(api):
            book_id = str(book.get("bookId") or "").strip()
            audio_id = _qidian_audio_id_from_book(book)
            item = _normalize_personal_item({
                "id": audio_id or book_id,
                "title": book.get("bookName"),
                "author": book.get("authorName"),
                "cover": _qidian_bookshelf_cover(book),
                "last_chapter": book.get("lastChapterName"),
                "update_time": book.get("updateTime"),
                "raw_data": book,
            }, "起点听书")
            item["personal_center_platform"] = "qidian"
            item["qidian_book_id"] = book_id
            if audio_id:
                item["qidian_audio_id"] = audio_id
            item["raw_data"] = book
            items.append(item)
    except Exception as e:
        print(f"❌ 起点听书个人数据加载失败({feature}): {e}")
        if not isinstance(e, RuntimeError):
            raise RuntimeError("起点书架加载失败，请稍后重试") from e
        raise
    return items


def _netease_response_container(response, list_key, label):
    if not isinstance(response, dict):
        raise RuntimeError(f"网易云听书{label}接口返回格式异常")
    code = response.get("code")
    if code is not None and str(code) != "200":
        message = response.get("message") or response.get("msg") or f"code={code}"
        if str(code) in ("301", "401", "403"):
            raise RuntimeError(f"网易云听书登录已失效，请在个人中心重新扫码：{message}")
        raise RuntimeError(f"网易云听书{label}加载失败：{message}")
    containers = [response]
    if isinstance(response.get("data"), dict):
        containers.append(response["data"])
    for container in containers:
        if list_key in container:
            records = container.get(list_key)
            if not isinstance(records, list):
                raise RuntimeError(f"网易云听书{label}接口返回格式异常")
            return container, records
    raise RuntimeError(f"网易云听书{label}接口未返回 {list_key}")


def _load_netease_personal(feature):
    if feature not in ("subscriptions", "history"):
        raise RuntimeError(f"不支持的网易云听书个人中心功能: {feature}")
    cookie = _get_personal_cookie("netease")
    if not cookie:
        raise RuntimeError("请先在个人中心为网易云听书扫码登录或粘贴 Cookie")
    from core.netease_cloud_audiobook_manager import NeteaseCloudAudiobookManager
    api = NeteaseCloudAudiobookManager()
    api.set_cookie(cookie)
    validation = api.validate_cookie()
    if not isinstance(validation, dict) or not validation.get("ok"):
        raise RuntimeError("网易云听书登录已失效，请在个人中心重新扫码")
    return _load_netease_personal_from_manager(api, feature)


def _load_netease_personal_from_manager(api, feature, max_pages=200):
    items = []
    seen = set()
    if feature == "subscriptions":
        limit = 100
        offset = 0
        for _ in range(max_pages):
            response = api._post_weapi("/weapi/djradio/get/subed", {
                "limit": limit,
                "offset": offset,
                "total": "true",
            })
            container, radios = _netease_response_container(response, "djRadios", "订阅")
            for radio in radios:
                if not isinstance(radio, dict):
                    continue
                normalized = normalize_album(api._normalize_radio(radio))
                radio_id = str(normalized.get("id") or "")
                if not radio_id or radio_id in seen:
                    continue
                seen.add(radio_id)
                items.append(normalized)
            offset += len(radios)
            count = container.get("count") or response.get("count") or 0
            has_more = container.get("hasMore")
            if has_more is None:
                has_more = response.get("hasMore")
            if not radios or has_more is False or (count and offset >= int(count)):
                return items
            if has_more is not True and len(radios) < limit:
                return items
        raise RuntimeError("网易云听书订阅分页超过安全上限，未能完整加载")

    if feature != "history":
        raise RuntimeError(f"不支持的网易云听书个人中心功能: {feature}")
    response = api._post_weapi("/weapi/play-record/djradio/list", {"limit": 1000})
    _, records = _netease_response_container(response, "list", "最近播放")
    for record in records:
        if not isinstance(record, dict):
            continue
        program = record.get("data") or record.get("program") or record
        if isinstance(program, dict) and isinstance(program.get("program"), dict):
            program = program["program"]
        if not isinstance(program, dict):
            continue
        radio = program.get("radio") or record.get("radio")
        if not isinstance(radio, dict):
            continue
        normalized = normalize_album(api._normalize_radio(radio))
        radio_id = str(normalized.get("id") or "")
        if not radio_id or radio_id in seen:
            continue
        seen.add(radio_id)
        items.append(normalized)
    return items


def _qtfm_response_payload(response, label):
    if isinstance(response, dict):
        code = response.get("errcode")
        if code is not None and str(code) not in ("0", "200"):
            message = response.get("errmsg") or response.get("message") or f"errcode={code}"
            if str(code) in ("401", "403"):
                raise RuntimeError(f"蜻蜓 FM 登录已失效，请在个人中心重新扫码：{message}")
            raise RuntimeError(f"蜻蜓 FM {label}加载失败：{message}")
        if "data" in response:
            return response.get("data")
    return response


def _qtfm_podcaster_name(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("nick_name") or value.get("nickname") or "")
    if isinstance(value, list):
        names = [_qtfm_podcaster_name(item) for item in value]
        return " / ".join(name for name in names if name)
    return str(value or "")


def _load_qtfm_personal(feature):
    if feature not in ("favorites", "history"):
        raise RuntimeError(f"不支持的蜻蜓 FM 个人中心功能: {feature}")
    cookie = _get_personal_cookie("qtfm")
    if not cookie:
        raise RuntimeError("请先在个人中心为蜻蜓 FM 扫码登录或粘贴 Cookie")
    from core.qtfm_manager import QtfmManager
    api = QtfmManager()
    api.set_cookie(cookie)
    if not api.is_authenticated() or not api.get_user_profile():
        raise RuntimeError("蜻蜓 FM 登录已失效，请在个人中心重新扫码")
    return _load_qtfm_personal_from_manager(api, feature)


def _load_qtfm_personal_from_manager(api, feature):
    endpoints = {"favorites": "favchannel", "history": "listenhistory"}
    endpoint = endpoints.get(feature)
    if not endpoint:
        raise RuntimeError(f"不支持的蜻蜓 FM 个人中心功能: {feature}")
    try:
        response = api.session.get(
            f"https://webbff.qtfm.cn/www/{endpoint}",
            params={"qingting_id": api.qingting_id, "access_token": api.access_token},
            timeout=20,
        )
        response.raise_for_status()
        payload = _qtfm_response_payload(response.json(), "收藏" if feature == "favorites" else "收听记录")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"蜻蜓 FM {'收藏' if feature == 'favorites' else '收听记录'}连接失败，请稍后重试") from exc

    if feature == "favorites":
        if not isinstance(payload, dict) or "favProgram" not in payload:
            raise RuntimeError("蜻蜓 FM 收藏接口返回格式异常")
        records = payload.get("favProgram")
    else:
        records = payload
        if isinstance(payload, dict):
            if "list" in payload:
                records = payload.get("list")
            elif "records" in payload:
                records = payload.get("records")
            else:
                records = None
    if not isinstance(records, list):
        raise RuntimeError(f"蜻蜓 FM {'收藏' if feature == 'favorites' else '收听记录'}接口返回格式异常")

    items = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if feature == "history" and str(record.get("ctype")) != "1":
            continue
        item_id = record.get("id") if feature == "favorites" else record.get("cid")
        item_id = str(item_id or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        if feature == "favorites":
            normalized = _normalize_personal_item({
                "id": item_id,
                "title": record.get("name") or record.get("title"),
                "author": _qtfm_podcaster_name(record.get("podcaster")),
                "cover": record.get("album_cover") or record.get("cover"),
                "episodes": record.get("program_count") or 0,
                "description": record.get("description") or record.get("desc"),
            }, "蜻蜓FM")
        else:
            normalized = _normalize_personal_item({
                "id": item_id,
                "title": record.get("cname"),
                "author": _qtfm_podcaster_name(record.get("podcaster") or record.get("anchor")),
                "cover": record.get("cavatar"),
                "description": record.get("pname"),
            }, "蜻蜓FM")
        items.append(normalized)
    return items


def _load_kuwo_personal(feature):
    if feature not in ("favorites", "history"):
        raise RuntimeError(f"不支持的酷我听书个人中心功能: {feature}")
    cookie = _get_personal_cookie("kuwo")
    if not cookie:
        raise RuntimeError("请先在个人中心为酷我听书登录或粘贴 Cookie")
    pairs = _parse_cookie_pairs(cookie)
    if not (pairs.get("userid") or pairs.get("uid")) or not (pairs.get("sid") or pairs.get("websid")):
        raise RuntimeError("酷我听书个人中心 Cookie 缺少 userid 和 sid/websid，请重新登录后获取")
    label = "收藏" if feature == "favorites" else "播放记录"
    raise RuntimeError(
        f"酷我听书官网当前未提供可验证的远端{label}读取接口；"
        "个人中心凭证已独立保存，不会影响账号管理和公开搜索"
    )


def _normalize_personal_item(item, platform):
    """将各平台个人中心条目统一为前端可用格式。"""
    d = dict(item or {})
    return normalize_album({
        "id": d.get("id") or d.get("album_id") or d.get("book_id") or "",
        "title": d.get("title") or d.get("name") or d.get("album_title") or "未知专辑",
        "author": d.get("author") or d.get("anchor") or d.get("announcer") or "",
        "cover": d.get("cover") or d.get("cover_url") or d.get("coverUrl") or "",
        "episodes": d.get("episodes") or d.get("track_count") or d.get("sections") or 0,
        "platform": platform,
        "status": d.get("status") or "",
        "description": d.get("description") or d.get("intro") or "",
    })


# ── 前端静态文件服务 ──────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    """服务前端 SPA 及静态资源。"""
    # 1. 优先从 dist 目录提供已构建文件
    if path and FRONTEND_DIST_DIR.exists():
        target = FRONTEND_DIST_DIR / path
        if target.is_file():
            return send_from_directory(str(FRONTEND_DIST_DIR), path)
    # 2. public 目录（service-worker、manifest 等）
    if path:
        pub = FRONTEND_PUBLIC_DIR / path
        if pub.is_file():
            return send_from_directory(str(FRONTEND_PUBLIC_DIR), path)
    # 3. SPA fallback：返回 index.html
    index = FRONTEND_DIST_DIR / "index.html"
    if index.exists():
        return send_from_directory(str(FRONTEND_DIST_DIR), "index.html")
    # 4. dist 未构建时给出提示
    return (
        "<h2>前端未构建</h2><p>请在容器内执行 <code>cd frontend && npm run build</code>，"
        "或使用 Docker 镜像（Dockerfile 会自动构建）。</p>",
        503,
    )



def _initialize_background_services():
    """Initialize optional services without delaying the Web listener."""
    services = (
        ("subscription scheduler", ensure_subscription_scheduler),
        ("Feishu bridge", feishu_bridge.start),
        ("developer Agent", developer_agent_manager.reconcile),
    )
    for name, initialize in services:
        started = time.monotonic()
        try:
            initialize()
            logging.info(
                "Background service initialized: %s (%.2fs)",
                name,
                time.monotonic() - started,
            )
        except Exception:
            logging.exception("Background service initialization failed: %s", name)


def start_background_services():
    """Start optional integrations in a daemon thread."""
    thread = threading.Thread(
        target=_initialize_background_services,
        name="background-service-initializer",
        daemon=True,
    )
    thread.start()
    return thread


def main():
    """启动 Web 服务器入口。"""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8082))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_services()
    print(f"🚀 启动服务器: http://{host}:{port}  debug={debug}")
    try:
        from waitress import serve
        print("📡 使用 waitress 生产服务器")
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        print("📡 waitress 未安装，使用 Flask 内置服务器")
        app.run(host=host, port=port, debug=debug, threaded=True)
