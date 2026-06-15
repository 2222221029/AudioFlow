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
from core.cookie_manager import CookieManager
from core.download_worker import DownloadWorker
from core.enhanced_search_manager import EnhancedSearchManager
from core.notification_manager import NotificationManager
from core.wecom_crypto import WeComCrypto, parse_wecom_message
from core.lrts_manager import (
    lrts_send_sms_code,
    lrts_sms_login,
    normalize_lrts_credentials,
    parse_lrts_credentials,
)
from core.safe_logging import RedactingFilter, install_safe_print
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
from core.subscription_manager import SubscriptionManager, chapter_key
from core.meta_scraper.state import META_STATE
from core.meta_scraper import config_store as meta_config_store
from core.meta_scraper import queue_store as meta_queue_store
from core.meta_scraper import processor as meta_processor
from core.meta_scraper import metadata_provider as meta_provider
from core.meta_scraper import files_service as meta_files
from core.meta_scraper.app_config import META_DATA_DIR


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
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_log_handler.addFilter(RedactingFilter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])
# httpx 内部日志用 %d 格式化状态码字符串会触发 logging 错误，屏蔽其 INFO 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
install_safe_print()

cookie_manager = CookieManager()
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
SUBSCRIPTIONS_FILE = config_dir() / "subscriptions.json"
TASKS_FILE = config_dir() / "tasks.json"
BACKGROUND_EVENTS_FILE = log_dir() / "events.jsonl"
TASK_SAVE_INTERVAL = 1.0
_last_task_save = 0.0
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
        BACKGROUND_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with BACKGROUND_EVENTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        logging.exception("append background event failed")
    return event


def load_background_events(limit=120):
    try:
        limit = max(1, min(500, int(limit or 120)))
    except (TypeError, ValueError):
        limit = 120
    if not BACKGROUND_EVENTS_FILE.exists():
        return []
    try:
        lines = BACKGROUND_EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
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
    return "未知原因"


def load_tasks():
    if not TASKS_FILE.exists():
        return {}
    try:
        raw = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        loaded = raw.get("tasks", {}) if isinstance(raw, dict) else {}
        changed = False
        for task in loaded.values():
            if task.get("status") in ("queued", "running", "paused"):
                task["status"] = "interrupted"
                task["error"] = "服务重启后任务已中断，可重试失败章节或重新添加下载。"
                task["failure_reason"] = "服务重启中断"
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
            item = subscription_manager.add_or_update(album, [], active_download_dir())
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


def _run_subscription_check(sid, queue_missing=False, source="subscription-check", progress=None):
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
    set_progress("正在扫描本地文件", chapter_count=len(chapters))
    scan_cache = {}
    diff = subscription_manager.diff_chapters(item, chapters, active_download_dir(), scan_cache=scan_cache, skip_local=False)
    set_progress("正在更新订阅结果", missing_count=len(diff.get("missing") or []))
    subscription_manager.update_check_result(sid, chapters, diff, "自动检测完成" if queue_missing else "已检查", refresh_local=False)
    item = subscription_manager.get(sid) or item
    item["download_dir"] = active_download_dir()
    stats = subscription_manager.stats_for(item, active_download_dir(), fast=True)
    queued_task_id = ""
    missing = diff.get("missing") or []
    if queue_missing and missing:
        set_progress("正在创建下载任务", missing_count=len(missing))
        if not voice:
            voices = get_album_voices(album)
            voice = voices[0] if voices else None
        queued_task_id = f"sub-{uuid.uuid4().hex[:12]}"
        options = {"download_dir": active_download_dir(), "quality": subscription_manager.settings().get("quality", "M4A 96K"), "voice": voice}
        start_download_task(queued_task_id, album, missing, options, source=source)
        notification_manager.notify(
            "subscription_queued",
            f"订阅发现新章节：{album.get('title') or '未知专辑'}",
            f"平台：{platform}\n新增/缺失：{len(missing)} 章\n任务：{queued_task_id}",
            {"album": album, "missing_count": len(missing), "task_id": queued_task_id, "source": source},
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
        "task_id": queued_task_id,
        "title": album.get("title") or item.get("title") or sid,
    }


def _subscription_job(job_id, sid, queue_missing):
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
        result = _run_subscription_check(sid, queue_missing=queue_missing, source="subscription", progress=update_progress)
        message = "已加入下载队列" if result.get("queued") else "检测完成，无需补全" if queue_missing else "检测完成"
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


def start_subscription_job(sid, queue_missing=False):
    with subscription_job_lock:
        cleanup_subscription_jobs()
        for existing in subscription_jobs.values():
            if (
                existing.get("sid") == sid
                and existing.get("status") in ("queued", "running")
                and bool(existing.get("queue_missing")) == bool(queue_missing)
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
    threading.Thread(target=_subscription_job, args=(job_id, sid, queue_missing), name=job_id, daemon=True).start()
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
        return "https:" + url
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
    if _to_int(merged.get("episodes")) <= 0 and _to_int(normalized.get("episodes")) > 0:
        merged["episodes"] = normalized["episodes"]
    return normalize_album(merged)


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
    all_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    normalized = [normalize_chapter(chapter, index) for index, chapter in enumerate(all_chapters, start=1)]
    wanted = set(ids)
    return [chapter for chapter in normalized if chapter_identifier(chapter) in wanted or chapter.get("id") in wanted]


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


def start_download_task(task_id, album, chapters, options, source="web"):
    album = normalize_album(album)
    chapters = list(chapters or [])
    options = dict(options or {})
    if album.get("platform") == "懒人听书":
        sync_platform_cookie("懒人听书")
    options["download_dir"] = resolve_download_dir(options.get("download_dir"))
    _write_album_source_file(album, options, task_id)
    warning = str(options.get("warning") or "").strip()
    if not warning and album.get("platform") == "懒人听书":
        expected = _to_int(album.get("episodes"))
        if expected > 0 and len(chapters) < expected:
            warning = f"懒人听书目录可能未完整加载：当前任务 {len(chapters)}/{expected} 章。"
    set_task(
        task_id,
        status="queued",
        title=album.get("title"),
        album=album,
        chapters=chapters,
        options=options,
        source=source,
        total=len(chapters),
        completed=0,
        percent=0,
        warning=warning,
        created_at=time.time(),
    )
    thread = threading.Thread(
        target=run_download_task,
        args=(task_id, album, chapters, options),
        name=f"download-{task_id}",
        daemon=True,
    )
    thread.start()
    return task_snapshot(task_id)


def refresh_subscription_audio_index_async():
    def worker():
        try:
            subscription_manager.build_audio_index(active_download_dir(), force=True)
        except Exception:
            logging.debug("refresh subscription audio index failed", exc_info=True)

    threading.Thread(target=worker, name="subscription-index-refresh", daemon=True).start()


def handle_download_completed(task_id, success, failed, success_chapters, failed_chapters):
    current = task_snapshot(task_id)
    status = "stopped" if current.get("status") == "stopping" else ("completed" if failed == 0 else "partial")
    album = current.get("album") or {}
    failure_reason = classify_failure_reason(current.get("error", ""), failed_chapters) if failed else ""
    if album:
        subscription_manager.mark_download_results(album, success_chapters, failed_chapters)
        refresh_subscription_audio_index_async()
    task = set_task(
        task_id,
        status=status,
        success=success,
        failed=failed,
        success_chapters=success_chapters,
        failed_chapters=failed_chapters,
        failure_reason=failure_reason,
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
    return task


def run_download_task(task_id, album, chapters, options):
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
    return json_ok(
        download_dir=str(active_download_dir()),
        download_threads=cookie_manager.get_download_threads(),
        quality=subscription_manager.settings().get("quality", "M4A 96K"),
        organize_by_platform_enabled=cookie_manager.get_cookie("organize_by_platform_enabled") == "true",
        split_chapters_enabled=cookie_manager.get_cookie("split_chapters_enabled") == "true",
        chapters_per_folder=int_cookie_setting("chapters_per_folder", 200),
        filename_prefix_format=cookie_manager.get_cookie("filename_prefix_format") or "0001-",
    )


_NOTIFICATION_SECRET_KEYS = {
    "token",
    "bot_token",
    "send_key",
    "key",
    "url",
    "secret",
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


def _wecom_help_text():
    return (
        "AudioFlow 企业微信指令：\n"
        "帮助：显示指令\n"
        "状态：查看服务版本和任务数\n"
        "搜索 关键词：搜索有声书\n"
        "订阅 序号：订阅最近一次搜索结果\n"
        "下载 序号：下载最近一次搜索结果全部章节\n"
        "示例：搜索 三体"
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


def _wecom_handle_text_command(service_id, user_id, text):
    text = str(text or "").strip()
    if not text or text in {"帮助", "help", "/help", "？", "?"}:
        return _wecom_help_text()
    if text in {"状态", "status", "/status"}:
        tasks_now = task_snapshot()
        running = sum(1 for item in tasks_now if item.get("status") in {"running", "pending", "paused"})
        return f"AudioFlow v{APP_VERSION}\n任务总数：{len(tasks_now)}\n进行中：{running}"
    match = re.match(r"^(搜索|search|/search)\s+(.+)$", text, re.I)
    if match:
        keyword = match.group(2).strip()
        results = [normalize_album(item) for item in search_manager.search_books(keyword, "all")][:8]
        with wecom_session_lock:
            cleanup_wecom_sessions()
            wecom_sessions[_wecom_session_key(service_id, user_id)] = {"keyword": keyword, "results": results, "updated_at": time.time()}
        if not results:
            return f"没有搜索到：{keyword}"
        return f"搜索结果：{keyword}\n{_wecom_album_lines(results)}\n\n发送“订阅 序号”或“下载 序号”继续。"
    match = re.match(r"^(订阅|subscribe|/subscribe)\s+(\d+)$", text, re.I)
    if match:
        album = _wecom_get_cached_album(service_id, user_id, match.group(2))
        album, chapters, voice = _wecom_load_album_chapters(album)
        if voice:
            album["voice"] = voice
        item = subscription_manager.add_or_update(album, chapters, active_download_dir())
        job = None
        if subscription_manager.settings().get("enabled", True):
            ensure_subscription_scheduler()
            job = start_subscription_job(item["id"], queue_missing=subscription_manager.settings().get("auto_download_missing", True))
        suffix = f"\n已启动检测任务：{job.get('id')}" if job else ""
        return f"已订阅：{album.get('title')}\n章节数：{len(chapters)}{suffix}"
    match = re.match(r"^(下载|download|/download)\s+(\d+)$", text, re.I)
    if match:
        album = _wecom_get_cached_album(service_id, user_id, match.group(2))
        album, chapters, voice = _wecom_load_album_chapters(album)
        options = {"download_dir": active_download_dir(), "quality": subscription_manager.settings().get("quality", "M4A 96K")}
        if voice:
            options["voice"] = voice
        task_id = f"wecom-{uuid.uuid4().hex[:12]}"
        start_download_task(task_id, album, chapters, options, source="wecom")
        return f"已加入下载：{album.get('title')}\n章节数：{len(chapters)}\n任务 ID：{task_id}"
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
    results = [normalize_album(item) for item in search_manager.search_books(keyword, platform)]
    return json_ok(results=results, count=len(results))


@app.post("/api/album/chapters")
def api_chapters():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    voice = payload.get("voice")
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
    if platform == "番茄畅听" and active_voice:
        raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), active_voice, page=1, page_size=10000)
    elif platform == "番茄听书" and active_voice:
        raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), active_voice)
    elif platform == "七猫听书" and active_voice:
        raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), active_voice)
    else:
        raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    warning = ""
    if platform == "懒人听书":
        warning = str(getattr(search_manager.lrts_manager, "last_chapter_warning", "") or "")
    if _to_int(album.get("episodes")) <= 0 or not album.get("cover") or not album.get("author"):
        try:
            album = merge_album_detail(album, search_manager.get_album_detail(str(album_id), platform))
        except Exception:
            logging.exception("album detail fallback failed")
    chapters = [
        normalize_chapter(chapter, index)
        for index, chapter in enumerate(raw_chapters, start=1)
    ]
    if _to_int(album.get("episodes")) <= 0 and chapters:
        album["episodes"] = len(chapters)
    expected = _to_int(album.get("episodes"))
    if platform == "懒人听书" and expected > 0 and len(chapters) < expected and not warning:
        warning = f"懒人听书目录可能未完整加载：当前获取 {len(chapters)}/{expected} 章。"
    if warning:
        album["catalog_warning"] = warning
    return json_ok(album=album, chapters=chapters, count=len(chapters), voice=active_voice, warning=warning)


@app.post("/api/album/voices")
def api_album_voices():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    if not album.get("platform"):
        return json_error("缺少平台信息")
    try:
        voices = get_album_voices(album)
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
        if isinstance(ck, dict):
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
    chapters = hydrate_download_chapters(album, payload.get("chapters") or [], payload.get("chapter_ids") or payload.get("chapterIds") or [])
    if not album or not chapters:
        return json_error("缺少专辑或章节")
    if album.get("platform") == "懒人听书":
        sync_platform_cookie("懒人听书")
    task_id = f"web-{uuid.uuid4().hex[:12]}"
    options = payload.get("options") or {}
    options["download_dir"] = resolve_download_dir(options.get("download_dir"))
    if options.get("voice"):
        options["voice"] = resolve_voice_for_album(album, options.get("voice"))
    task = start_download_task(task_id, album, chapters, options, source="web")
    return json_ok(task_id=task_id, task=task)


@app.get("/api/downloads")
def api_downloads():
    return json_ok(tasks=task_snapshot())


@app.post("/api/downloads/retry-unfinished")
def api_retry_unfinished_downloads():
    created = []
    for task in task_snapshot():
        status = task.get("status")
        if status not in ("interrupted", "failed", "partial", "stopped"):
            continue
        chapters = task.get("failed_chapters") or []
        if not chapters and status in ("interrupted", "failed", "stopped"):
            chapters = task.get("chapters") or []
        if not chapters:
            continue
        album = task.get("album") or {"title": task.get("title"), "platform": (task.get("task_info") or {}).get("platform")}
        options = task.get("options") or {}
        new_task_id = f"retry-{uuid.uuid4().hex[:12]}"
        created.append(start_download_task(new_task_id, album, chapters, options, source=f"retry-unfinished:{task.get('id')}"))
    append_background_event("download", "重试未完成任务", f"创建 {len(created)} 个重试任务", {"count": len(created)})
    return json_ok(count=len(created), tasks=created)


@app.get("/api/downloads/<task_id>")
def api_download_detail(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    return json_ok(task=task)


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
    if not worker:
        return json_error("任务未在运行，无法继续", 409)
    resume_worker(worker)
    return json_ok(task=set_task(task_id, status="running"))


@app.post("/api/downloads/<task_id>/stop")
def api_download_stop(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    worker = live_worker(task_id)
    if worker:
        stop_worker(worker)
        return json_ok(task=set_task(task_id, status="stopping"))
    if task.get("status") in ("queued", "running", "paused"):
        return json_ok(task=set_task(task_id, status="stopped", finished_at=time.time()))
    return json_ok(task=task)


@app.post("/api/downloads/<task_id>/retry-failed")
def api_download_retry_failed(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    chapters = task.get("failed_chapters") or []
    if not chapters and task.get("status") in ("failed", "interrupted", "stopped"):
        chapters = task.get("chapters") or []
    if not chapters:
        return json_error("没有可重试的失败章节")
    album = task.get("album") or {"title": task.get("title"), "platform": (task.get("task_info") or {}).get("platform")}
    options = task.get("options") or {}
    if not options and task.get("task_info"):
        info = task.get("task_info") or {}
        options = {"download_dir": info.get("download_dir"), "quality": info.get("quality"), "voice": info.get("voice_config")}
    if options.get("voice"):
        options["voice"] = resolve_voice_for_album(album, options.get("voice"))
    new_task_id = f"retry-{uuid.uuid4().hex[:12]}"
    new_task = start_download_task(new_task_id, album, chapters, options, source=f"retry:{task_id}")
    return json_ok(task_id=new_task_id, task=new_task)


@app.delete("/api/downloads/<task_id>")
def api_download_delete(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("任务不存在", 404)
    if live_worker(task_id) or task.get("status") in ("running", "queued", "paused", "stopping"):
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
    protected = {"queued", "running", "paused", "stopping"}
    deleted = []
    with task_lock:
        for tid, task in list(tasks.items()):
            status = str(task.get("status") or "")
            if status in protected or tid in task_workers:
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
    return json_ok(subscriptions=items, settings=subscription_manager.settings(), scheduler=subscription_scheduler_status(), fast=fast, refresh_local=refresh_local)


@app.post("/api/subscriptions/index/rebuild")
def api_rebuild_subscription_index():
    index = subscription_manager.build_audio_index(active_download_dir(), force=True)
    return json_ok(index={"count": index.get("count", 0), "updated_at": index.get("updated_at"), "exists": index.get("exists")})


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
                jobs.append(start_subscription_job(sid, queue_missing=(action == "complete")))
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
    voice = resolve_voice_for_album(album, payload.get("voice"))
    if not album:
        return json_error("缺少专辑信息")
    if voice:
        album["voice"] = voice
    item = subscription_manager.add_or_update(album, chapters, active_download_dir())
    job = None
    settings = subscription_manager.settings()
    if settings.get("enabled", True):
        ensure_subscription_scheduler()
        job = start_subscription_job(item["id"], queue_missing=settings.get("auto_download_missing", True))
    return json_ok(subscription=item, job=job)


@app.delete("/api/subscriptions/<path:sid>")
def api_unsubscribe(sid):
    ok = subscription_manager.cancel(sid)
    return json_ok(cancelled=ok)


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
    return json_ok(job=start_subscription_job(sid, queue_missing=True))


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
    return json_ok(events=load_background_events(request.args.get("limit", 120)))


@app.delete("/api/events")
def api_clear_events():
    try:
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
        name = _safe_account_label(name) or (_safe_account_label(account_id) if account_id else "")
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
    cookie_manager.set_cookie(platform, cookie)
    search_manager.set_cookie(platform, cookie)
    return json_ok(saved=True, platform=platform, config_file=str(cookie_manager.config_file))


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
    for platform, key in (("ximalaya", "personal_xmly"), ("lrts", "personal_lrts"), ("qidian", "personal_qidian")):
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
    if not phone:
        return json_error("请输入手机号")
    try:
        data = lrts_send_sms_code(phone)
    except Exception as exc:
        logging.exception("lrts send sms failed")
        return json_error(f"发送验证码失败：{exc}", status=500)
    if data.get("status") != 0:
        return json_error(data.get("msg") or f"发送验证码失败：status={data.get('status')}")
    return json_ok(message="验证码已发送", imei=data.get("_imei", ""), temp_token=data.get("_token", ""))


@app.post("/api/lrts/login")
def api_lrts_login():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    imei = str(payload.get("imei") or "").strip()
    temp_token = str(payload.get("temp_token") or "").strip()
    if not phone or not code:
        return json_error("请输入手机号和验证码")
    try:
        data, credential = lrts_sms_login(phone, code, imei=imei, temp_token=temp_token)
    except Exception as exc:
        logging.exception("lrts sms login failed")
        return json_error(f"验证码登录失败：{exc}", status=500)
    if data.get("status") != 0 or not credential:
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
    if not phone or not code:
        return json_error("请输入手机号和验证码")
    try:
        data, credential = lrts_sms_login(phone, code, imei=imei, temp_token=temp_token)
    except Exception as exc:
        logging.exception("personal lrts sms login failed")
        return json_error(f"验证码登录失败：{exc}", status=500)
    if data.get("status") != 0 or not credential:
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
    "ximalaya": "xmly", "qidian": "qidian", "qtfm": "qtfm",
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
    cookie = _get_personal_cookie("lrts")
    if not cookie:
        raise RuntimeError("请先在个人中心为懒人听书登录或粘贴凭证")
    from core.lrts_manager import LRTSManager
    api = LRTSManager()
    api.set_cookie(cookie)
    client = api._client_or_guest()
    lrts_user_id = str(api.credentials.get("userId") or api.credentials.get("uid") or "0")
    items = []
    app_items = _load_lrts_personal_from_app(client, feature)
    if app_items:
        return app_items
    try:
        if feature == "history":
            resp = api.session.get("https://m.lrts.me/ajax/getRecentList?srcType=101", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for item in (data.get("list", []) or []):
                items.append(_normalize_personal_item({
                    "id": str(item.get("bookId") or item.get("entityId") or ""),
                    "title": item.get("name") or item.get("title", ""),
                    "author": item.get("announcer") or item.get("author", ""),
                    "cover": item.get("cover") or item.get("cover_url", ""),
                    "episodes": item.get("sum") or item.get("sections", 0),
                }, "懒人听书"))
        elif feature == "favorites":
            resp = api.session.get(
                "https://m.lrts.me/ajax/getFolderEntities",
                params={"folderId": 7855269751, "opType": "H", "pageSize": 100, "referId": 0, "userId": lrts_user_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in ((data.get("data") or {}).get("list", []) or []):
                 items.append(_normalize_personal_item({
                    "id": str(item.get("bookId") or item.get("entityId") or item.get("id") or ""),
                    "title": item.get("name") or item.get("title", ""),
                    "author": item.get("announcer") or item.get("author", ""),
                    "cover": item.get("cover") or item.get("cover_url", ""),
                    "episodes": item.get("sum") or item.get("sections", 0),
                }, "懒人听书"))
        elif feature == "programs":
            resp = api.session.get(
                "https://m.lrts.me/ajax/getMyBookList",
                params={"pageNum": 1, "pageSize": 100},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in (data.get("list", []) or []):
                items.append(_normalize_personal_item({
                    "id": str(item.get("bookId") or item.get("id") or ""),
                    "title": item.get("name") or item.get("title", ""),
                    "author": item.get("announcer") or item.get("author", ""),
                    "cover": item.get("cover") or item.get("cover_url", ""),
                    "episodes": item.get("sum") or item.get("sections", 0),
                }, "懒人听书"))
    except Exception as e:
        print(f"❌ 懒人听书个人数据加载失败({feature}): {e}")
    return items


def _iter_personal_records(data):
    if isinstance(data, list):
        yield from data
        return
    if not isinstance(data, dict):
        return
    for key in ("list", "booksInfo", "bookList", "albumList", "ablumnList", "resourceList", "records", "items"):
        value = data.get(key)
        if isinstance(value, list):
            yield from value
    inner = data.get("data")
    if isinstance(inner, dict):
        yield from _iter_personal_records(inner)
    elif isinstance(inner, list):
        yield from inner


def _normalize_lrts_personal_record(item):
    if not isinstance(item, dict):
        return None
    entity_type = item.get("baseEntityType") or item.get("entityType") or item.get("type") or item.get("resType") or 2
    entity_id = (
        item.get("baseEntityId") or item.get("entityId") or item.get("bookId") or item.get("ablumnId")
        or item.get("albumId") or item.get("id") or item.get("resId")
    )
    if not entity_id:
        return None
    title = (
        item.get("name") or item.get("bookName") or item.get("ablumnName") or item.get("albumName")
        or item.get("entityName") or item.get("title") or item.get("resName")
    )
    if not title:
        return None
    return _normalize_personal_item({
        "id": f"{entity_type}:{entity_id}",
        "title": title,
        "author": item.get("author") or item.get("authorName") or item.get("anchorName") or item.get("nickname") or item.get("announcer") or "",
        "cover": item.get("cover") or item.get("coverUrl") or item.get("coverPath") or item.get("bestCover") or item.get("pic") or "",
        "episodes": item.get("sections") or item.get("countTrack") or item.get("chapterCount") or item.get("audioCount") or 0,
        "plays": item.get("plays") or item.get("playCount") or item.get("play") or 0,
        "raw_data": item,
    }, "懒人听书")


def _load_lrts_personal_from_app(client, feature):
    from core.lrts_manager import READ_HOST
    endpoint_groups = {
        "history": [
            (READ_HOST, "/yyting/usercenter/getRecentList.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/usercenter/getListenHistory.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/usercenter/getListenRecord.action", {"pageNum": 1, "pageSize": 100}),
        ],
        "favorites": [
            (READ_HOST, "/yyting/usercenter/getCollectList.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/usercenter/getMyCollect.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/usercenter/getFavoriteList.action", {"pageNum": 1, "pageSize": 100}),
        ],
        "programs": [
            (READ_HOST, "/yyting/usercenter/getUserBookList.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/usercenter/getUserAblumnList.action", {"pageNum": 1, "pageSize": 100}),
            (READ_HOST, "/yyting/bookclient/ClientGetBookShelf.action", {"pageNum": 1, "pageSize": 100}),
        ],
    }
    items = []
    seen = set()
    for host, path, params in endpoint_groups.get(feature, []):
        try:
            data = client.get(host, path, params)
            print(f"[personal-lrts] {path}: status={data.get('status')} msg={data.get('msg', '')}")
        except Exception as exc:
            print(f"[personal-lrts] {path} failed: {exc}")
            continue
        if data.get("status") not in (0, None):
            continue
        for record in _iter_personal_records(data):
            item = _normalize_lrts_personal_record(record)
            if not item:
                continue
            key = item.get("id") or item.get("title")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items


def _load_qidian_personal(feature):
    cookie = _get_personal_cookie("qidian")
    if not cookie:
        raise RuntimeError("请先在个人中心为起点听书登录或粘贴 Cookie")
    from core.search_manager import SearchManager
    api = SearchManager()
    api.set_qidian_cookie(cookie)
    items = []
    try:
        if feature == "favorites":
            account = api.get_qidian_user_account()
            if not account:
                raise RuntimeError("起点账号校验失败，请在个人中心重新扫码或粘贴 Cookie")
            resp = api.qidian_session.get(
                "https://wxapp.qidian.com/api/bookShelf/list",
                params={"page": 1, "pageSize": 100},
                headers={
                    **(api.qidian_headers or {}),
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://my.qidian.com/",
                },
                cookies=api.qidian_cookies or None,
                timeout=15,
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(data.get("msg") or f"起点书架获取失败：code={data.get('code')}")
            for book in ((data.get("data") or {}).get("booksInfo") or []):
                items.append(_normalize_personal_item({
                    "id": book.get("bookId"),
                    "title": book.get("bookName"),
                    "author": book.get("authorName"),
                    "cover": book.get("coverUrl"),
                    "last_chapter": book.get("lastChapterName"),
                    "update_time": book.get("updateTime"),
                    "raw_data": book,
                }, "起点听书"))
    except Exception as e:
        print(f"❌ 起点听书个人数据加载失败({feature}): {e}")
        if isinstance(e, RuntimeError):
            raise
    return items


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


# ============ META SCRAPER API ============

@app.route('/api/meta/options', methods=['GET'])
def meta_options():
    if not current_user():
        return json_error("未登录", 401)
    from core.meta_legacy_core import config as lc, docker_web as lw
    return jsonify(ok=True, options={
        'api_sources': list(lw.API_SOURCES),
        'link_platforms': list(lw.LINK_PLATFORMS),
        'platforms': list(lc.get_platform_options()),
        'categories': [{'id': k, 'name': v} for k, v in lc.CATEGORY_MAP.items()],
        'target_formats': list(lw.TARGET_FORMATS),
        'bitrates': list(lw.BITRATE_OPTIONS),
        'finished': list(lw.FINISHED_OPTIONS),
        'data_root': str(META_DATA_DIR),
    })

@app.route('/api/meta/config', methods=['GET'])
def meta_get_config():
    if not current_user():
        return json_error("未登录", 401)
    params = meta_config_store.load_config()
    return jsonify(ok=True, params=params.model_dump())

@app.route('/api/meta/config', methods=['POST'])
def meta_post_config():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    params = meta_config_store.save_config(data.get('params', data))
    return jsonify(ok=True, params=params.model_dump())

@app.route('/api/meta/folder-config', methods=['POST'])
def meta_folder_config():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    found, params, message = meta_config_store.read_folder_config(data.get('path', ''))
    return jsonify(ok=True, found=found, params=params.model_dump(), message=message)

@app.route('/api/meta/fetch-metadata', methods=['POST'])
def meta_fetch_metadata():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    api_source = str(data.get('api_source') or '').strip()
    api_id = str(data.get('api_id') or '').strip()
    if not api_source or not api_id:
        return jsonify(ok=False, error='请填写平台和专辑 ID'), 400
    try:
        result = meta_provider.fetch_by_id(api_source, api_id)
        return jsonify(ok=True, metadata=result)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route('/api/meta/fetch-link', methods=['POST'])
def meta_fetch_link():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    platform = str(data.get('platform') or '').strip()
    url = str(data.get('url') or '').strip()
    if not platform or not url:
        return jsonify(ok=False, error='请填写平台和链接'), 400
    try:
        result = meta_provider.fetch_by_link(platform, url)
        return jsonify(ok=True, metadata=result)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route('/api/meta/status', methods=['GET'])
def meta_status():
    if not current_user():
        return json_error("未登录", 401)
    snap = META_STATE.snapshot()
    return jsonify(ok=True, status=snap.model_dump())

@app.route('/api/meta/run', methods=['POST'])
def meta_run():
    if not current_user():
        return json_error("未登录", 401)
    if META_STATE.running:
        return jsonify(ok=False, error='任务正在运行'), 409
    data = request.get_json() or {}
    try:
        from core.meta_scraper.schemas import ProcessParams
        params = ProcessParams.model_validate(data.get('params', data))
        meta_processor.validate_params(params)
        meta_processor.start_single(params)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400

@app.route('/api/meta/stop', methods=['POST'])
def meta_stop():
    if not current_user():
        return json_error("未登录", 401)
    META_STATE.request_stop()
    return jsonify(ok=True, status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/add', methods=['POST'])
def meta_queue_add():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    from core.meta_scraper.schemas import ProcessParams
    params = ProcessParams.model_validate(data.get('params', data))
    item = meta_queue_store.add_queue_item(params)
    return jsonify(ok=True, item=item.model_dump(), status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/update', methods=['POST'])
def meta_queue_update():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    task_id = str(data.get('id') or '')
    if not task_id:
        return jsonify(ok=False, error='缺少任务 ID'), 400
    from core.meta_scraper.schemas import ProcessParams
    params = ProcessParams.model_validate(data.get('params') or {})
    try:
        item = meta_queue_store.update_queue_item(task_id, params)
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e)), 400
    if not item:
        return jsonify(ok=False, error='未找到任务'), 404
    return jsonify(ok=True, item=item.model_dump(), status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/remove', methods=['POST'])
def meta_queue_remove():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    meta_queue_store.remove_queue_items(set(data.get('ids') or []))
    return jsonify(ok=True, status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/clear', methods=['POST'])
def meta_queue_clear():
    if not current_user():
        return json_error("未登录", 401)
    meta_queue_store.clear_queue()
    return jsonify(ok=True, status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/retry-failed', methods=['POST'])
def meta_queue_retry_failed():
    if not current_user():
        return json_error("未登录", 401)
    meta_queue_store.retry_failed()
    return jsonify(ok=True, status=META_STATE.snapshot().model_dump())

@app.route('/api/meta/queue/start', methods=['POST'])
def meta_queue_start():
    if not current_user():
        return json_error("未登录", 401)
    if META_STATE.running:
        return jsonify(ok=False, error='已有任务正在运行'), 409
    if not META_STATE.queue:
        return jsonify(ok=False, error='队列为空'), 400
    meta_processor.start_queue()
    return jsonify(ok=True)

@app.route('/api/meta/browse', methods=['GET'])
def meta_browse():
    if not current_user():
        return json_error("未登录", 401)
    path = request.args.get('path', '')
    result = meta_files.browse(path)
    return jsonify(ok=True, browser=result)

@app.route('/api/meta/read-source', methods=['GET'])
def meta_read_source():
    if not current_user():
        return json_error("未登录", 401)
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify(ok=False, error='未提供路径')
    folder = meta_files.safe_path(path)
    src_file = folder / SOURCE_INFO_FILE
    if not src_file.exists():
        return jsonify(ok=False, error='未找到 source.json')
    try:
        data = json.loads(src_file.read_text(encoding='utf-8'))
        return jsonify(ok=True, source=data)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route('/api/meta/cover', methods=['GET'])
def meta_cover():
    if not current_user():
        return json_error("未登录", 401)
    path = request.args.get('path', '')
    file_path = meta_files.safe_path(path)
    if not file_path.exists() or not file_path.is_file():
        return jsonify(ok=False, error='封面不存在'), 404
    return send_file(str(file_path))

@app.route('/api/meta/cookies', methods=['GET'])
def meta_get_cookies():
    if not current_user():
        return json_error("未登录", 401)
    from core.meta_legacy_core import config as lc
    return jsonify(ok=True, cookies=lc.get_platform_cookies())

@app.route('/api/meta/cookies', methods=['POST'])
def meta_save_cookies():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    cookies = data.get('cookies', data)
    if not isinstance(cookies, dict):
        return jsonify(ok=False, error='Cookie 数据格式不正确'), 400
    from core.meta_legacy_core import config as lc
    if not lc.set_platform_cookies(cookies):
        return jsonify(ok=False, error='Cookie 保存失败'), 500
    return jsonify(ok=True, cookies=lc.get_platform_cookies())

@app.route('/api/meta/tag-blacklist', methods=['GET'])
def meta_get_tag_blacklist():
    if not current_user():
        return json_error("未登录", 401)
    from core.meta_legacy_core.docker_web import load_tag_blacklist_patterns
    return jsonify(ok=True, patterns=load_tag_blacklist_patterns())

@app.route('/api/meta/tag-blacklist', methods=['POST'])
def meta_save_tag_blacklist():
    if not current_user():
        return json_error("未登录", 401)
    data = request.get_json() or {}
    patterns = data.get('patterns', [])
    if not isinstance(patterns, list):
        return jsonify(ok=False, error='黑名单规则必须是列表'), 400
    from core.meta_legacy_core.docker_web import save_tag_blacklist_patterns
    return jsonify(ok=True, patterns=save_tag_blacklist_patterns(patterns))

@app.route('/api/meta/events', methods=['GET'])
def meta_events():
    if not current_user():
        return json_error("未登录", 401)
    import queue as queue_module
    import json as json_module

    def generate():
        q = META_STATE.subscribe()
        try:
            snap = META_STATE.snapshot().model_dump()
            yield "event: status\ndata: " + json_module.dumps(snap, ensure_ascii=False, default=str) + "\n\n"
            while True:
                try:
                    event = q.get(timeout=15)
                    yield "event: update\ndata: " + json_module.dumps(event, ensure_ascii=False, default=str) + "\n\n"
                except queue_module.Empty:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            META_STATE.unsubscribe(q)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ============ FILE MANAGER API ============

import core.file_manager as _fm

@app.route('/api/file-manager/config', methods=['GET'])
def fm_get_config():
    if not current_user(): return json_error("未登录", 401)
    cfg = _fm.load_fm_config()
    masked = dict(cfg)
    key = masked.get('ai_api_key', '')
    masked['ai_api_key_masked'] = (key[:4] + '****' + key[-4:]) if len(key) > 8 else ('****' if key else '')
    masked.pop('ai_api_key', None)
    return json_ok(config=masked)

@app.route('/api/file-manager/config', methods=['POST'])
def fm_save_config():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    cfg = _fm.load_fm_config()
    for k in ('ai_enabled', 'ai_base_url', 'ai_model', 'custom_ad_rules'):
        if k in data: cfg[k] = data[k]
    if 'ai_api_key' in data and data['ai_api_key'] and '****' not in data['ai_api_key']:
        cfg['ai_api_key'] = data['ai_api_key']
    return json_ok(config=_fm.save_fm_config(cfg))

@app.route('/api/file-manager/scan', methods=['GET'])
def fm_scan():
    if not current_user(): return json_error("未登录", 401)
    root = request.args.get('root') or None
    quick = request.args.get('quick', 'true').lower() != 'false'  # 默认 quick，重命名不需要时长
    try:
        return json_ok(**_fm.scan_directory(root, quick=quick))
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/books', methods=['GET'])
def fm_books():
    if not current_user(): return json_error("未登录", 401)
    root = request.args.get('root') or None
    try:
        result = _fm.scan_directory(root)
        return json_ok(books=result['books'], root=result['root'])
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/local-analyze', methods=['POST'])
def fm_local_analyze():
    """本地智能分析：无需 AI，秒级提取书名和章节标题"""
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    folder_path = data.get('folder_path', '')
    if not folder_path:
        return json_error("缺少 folder_path", 400)
    cfg = _fm.load_fm_config()
    try:
        result = _fm.local_analyze(folder_path, cfg.get('custom_ad_rules', []))
        return json_ok(**result)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/ai-analyze', methods=['POST'])
def fm_ai_analyze():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    file_names = data.get('file_names', [])
    if not file_names:
        return json_error("请提供文件名列表", 400)
    cfg = _fm.load_fm_config()
    try:
        result = _fm.ai_analyze(file_names, cfg)
        return json_ok(result=result)
    except ValueError as e:
        return json_error(str(e), 400)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/ai-analyze-batch', methods=['POST'])
def fm_ai_analyze_batch():
    """单批接口，由前端控制批次循环以显示实时进度"""
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    file_names    = data.get('file_names', [])
    is_first      = data.get('is_first_batch', True)
    if not file_names:
        return json_error("请提供文件名列表", 400)
    cfg = _fm.load_fm_config()
    try:
        result = _fm.ai_analyze_single_batch(file_names, cfg, is_first_batch=is_first)
        return json_ok(result=result)
    except ValueError as e:
        return json_error(str(e), 400)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/rename-preview', methods=['POST'])
def fm_rename_preview():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    folder_path = data.get('folder_path', '')
    template = data.get('template', '')
    book_meta = data.get('book_meta', {})
    if not folder_path or not template:
        return json_error("缺少参数", 400)
    try:
        previews = _fm.preview_rename(folder_path, template, book_meta)
        return json_ok(previews=previews)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/rename-apply', methods=['POST'])
def fm_rename_apply():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    previews = data.get('previews', [])
    note = data.get('note', '')
    if not previews:
        return json_error("无预览数据", 400)
    try:
        result = _fm.apply_rename(previews, note)
        return json_ok(**result)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/rollback', methods=['POST'])
def fm_rollback():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    history_id = data.get('history_id', '')
    if not history_id:
        return json_error("缺少 history_id", 400)
    try:
        result = _fm.rollback(history_id)
        return json_ok(**result)
    except Exception as e:
        return json_error(str(e), 500)

@app.route('/api/file-manager/history', methods=['GET'])
def fm_history():
    if not current_user(): return json_error("未登录", 401)
    return json_ok(history=_fm.load_history())

@app.route('/api/file-manager/templates', methods=['GET'])
def fm_get_templates():
    if not current_user(): return json_error("未登录", 401)
    return json_ok(templates=_fm.load_templates())

@app.route('/api/file-manager/templates', methods=['POST'])
def fm_save_templates():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    templates = data.get('templates', [])
    _fm.save_templates(templates)
    return json_ok(templates=_fm.load_templates())

@app.route('/api/file-manager/clean-preview', methods=['POST'])
def fm_clean_preview():
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    names = data.get('names', [])
    cfg = _fm.load_fm_config()
    results = [{'original': n, 'cleaned': _fm.clean_filename(n, cfg.get('custom_ad_rules', []))} for n in names]
    return json_ok(results=results)

@app.route('/api/file-manager/scrape', methods=['POST'])
def fm_scrape():
    """对接现有刮削系统"""
    if not current_user(): return json_error("未登录", 401)
    data = request.get_json() or {}
    api_source = data.get('api_source', '')
    api_id = data.get('api_id', '')
    link_url = data.get('link_url', '')
    link_platform = data.get('link_platform', '起点听书')
    try:
        if link_url:
            result = meta_provider.fetch_by_link(link_platform, link_url)
        elif api_source and api_id:
            result = meta_provider.fetch_by_id(api_source, api_id)
        else:
            return json_error("请提供 api_source+api_id 或 link_url", 400)
        return json_ok(metadata=result)
    except Exception as e:
        return json_error(str(e), 500)


def main():
    """启动 Web 服务器入口。"""
    import os
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8082))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        ensure_subscription_scheduler()
    print(f"🚀 启动服务器: http://{host}:{port}  debug={debug}")
    try:
        from waitress import serve
        print("📡 使用 waitress 生产服务器")
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        print("📡 waitress 未安装，使用 Flask 内置服务器")
        app.run(host=host, port=port, debug=debug, threaded=True)
