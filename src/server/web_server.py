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


FRONTEND_DIST_DIR = project_root() / "frontend" / "dist"
FRONTEND_PUBLIC_DIR = project_root() / "frontend" / "public"

app = Flask(__name__, static_folder=None)


@app.errorhandler(500)
def handle_500(e):
    """鎹曡幏鎵€鏈夋湭澶勭悊寮傚父锛岃繑鍥?JSON 鑰岄潪 Waitress 閿欒椤点€?""
    import traceback
    traceback.print_exc()
    return jsonify(ok=False, error=str(e) or "鏈嶅姟鍣ㄥ唴閮ㄩ敊璇?), 500

@app.errorhandler(Exception)
def handle_unhandled(e):
    """鍏滃簳寮傚父澶勭悊銆?""
    import traceback
    traceback.print_exc()
    return jsonify(ok=False, error=str(e) or "鏈鐞嗙殑寮傚父"), 500
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
        return json_error("璇锋眰浣撹繃澶?, 413)
    if _is_public_endpoint(request.path):
        return None
    if current_user():
        return None
    return json_error("鏈櫥褰曟垨浼氳瘽宸茶繃鏈?, 401)


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
    if any(token in text for token in ("cookie", "鐧诲綍", "鐧婚檰", "unauthorized", "401", "403")):
        return "鐧诲綍/Cookie 澶辨晥"
    if any(token in text for token in ("vip", "浼氬憳", "浠樿垂", "鏉冮檺", "鐧介噾", "restricted")):
        return "浼氬憳/浠樿垂闄愬埗"
    if any(token in text for token in ("limit", "闄愭祦", "棰戠箒", "椋庢帶", "apistatus=114", "429")):
        return "骞冲彴闄愭祦/椋庢帶"
    if any(token in text for token in ("timeout", "timed out", "瓒呮椂", "connection", "network", "杩炴帴")):
        return "缃戠粶瓒呮椂/杩炴帴澶辫触"
    if any(token in text for token in ("url", "404", "410", "闊抽", "閾炬帴", "鍦板潃")):
        return "闊抽鍦板潃澶辨晥"
    if any(token in text for token in ("permission", "denied", "no space", "纾佺洏", "鍐欏叆", "鐩綍")):
        return "鏈湴鏂囦欢/纾佺洏闂"
    return "鏈煡鍘熷洜"


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
                task["error"] = "鏈嶅姟閲嶅惎鍚庝换鍔″凡涓柇锛屽彲閲嶈瘯澶辫触绔犺妭鎴栭噸鏂版坊鍔犱笅杞姐€?
                task["failure_reason"] = "鏈嶅姟閲嶅惎涓柇"
                changed = True
        if changed:
            TASKS_FILE.write_text(json.dumps({"tasks": loaded}, ensure_ascii=False, indent=2), encoding="utf-8")
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logging.exception("load tasks failed")
        print(f"[浠诲姟] 鍔犺浇浠诲姟鏂囦欢澶辫触锛歿exc}")
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
        print(f"[浠诲姟] 淇濆瓨浠诲姟鏂囦欢澶辫触锛歿exc}")

# 鈹€鈹€ 璁㈤槄鑷姩妫€娴嬭皟搴﹀櫒 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 鍛ㄦ湡鎬ф壂鎻忔墍鏈夈€屽埌鏈熴€嶇殑璁㈤槄锛坙ast_check_at 瓒呰繃 interval_hours锛夛紝
# 璋冪敤 SubscriptionManager.diff_chapters 姣斿杩滅绔犺妭涓庢湰鍦版枃浠讹紝
# 鍙戠幇缂哄け鍒欒嚜鍔ㄥ姞鍏ヤ笅杞介槦鍒楀埌璁剧疆鐨勪笅杞借矾寰勩€?_scheduler_lock = threading.Lock()
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
                    subscription_manager.mark_check_error(item.get("id"), f"涓汉涓績鍚屾鍚庢娴嬪け璐ワ細{exc}")
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
        _scheduler_status["personal_sync_last_error"] = f"鏆備笉鏀寔鍚屾骞冲彴锛歿platform}"
        return {"skipped": True, "reason": "unsupported_platform"}
    return _sync_personal_ximalaya_subscriptions(force=force)


def _scheduler_tick(force=False):
    """鍗曟鎵弿锛氬鐞嗕竴鎵瑰埌鏈熺殑璁㈤槄銆?""
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
                subscription_manager.mark_check_error(item.get("id"), f"鑷姩妫€娴嬪け璐ワ細{exc}")
                logging.exception("subscription scheduler item failed")
                print(f"[璁㈤槄璋冨害] 澶勭悊 {item.get('id')} 澶辫触锛歿exc}")
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
        print(f"[璁㈤槄璋冨害] 寮傚父锛歿exc}")


def _scheduler_loop():
    """甯搁┗寰幆銆傛瘡鍒嗛挓妫€鏌ヤ竴娆℃槸鍚︽湁鍒版湡璁㈤槄銆?""
    while True:
        try:
            _scheduler_status["running"] = True
            _scheduler_tick()
            if personal_sync_due():
                _personal_sync_tick(force=False)
        except Exception as exc:
            _scheduler_status["running"] = False
            _scheduler_status["last_error"] = str(exc)
            print(f"[璁㈤槄璋冨害] loop 寮傚父锛歿exc}")
        _scheduler_event.wait(60)
        _scheduler_event.clear()


def start_subscription_scheduler():
    """鍚姩鍚庡彴璋冨害绾跨▼锛堝箓绛夛級銆?""
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

    set_progress("姝ｅ湪鍑嗗璁㈤槄妫€娴?)
    item = subscription_manager.get(sid)
    if not item:
        raise ValueError("璁㈤槄涓嶅瓨鍦?)
    album = normalize_album(item.get("album") or item)
    voice = item.get("voice") or album.get("voice")
    album_id = album.get("id") or album.get("album_id") or album.get("book_id") or item.get("album_id")
    platform = album.get("platform") or item.get("platform")
    if not album_id or not platform:
        raise ValueError("璁㈤槄缂哄皯涓撹緫 ID 鎴栧钩鍙?)
    set_progress("姝ｅ湪鑾峰彇杩滅绔犺妭", platform=platform, album_id=album_id)
    if platform == "涓冪尗鍚功":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    if platform == "鐣寗鍚功":
        if not voice:
            voice = resolve_voice_for_album(album, (get_album_voices(album) or [None])[0])
        chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), voice) if voice else []
    elif platform == "涓冪尗鍚功":
        if not voice:
            voice = resolve_voice_for_album(album, (get_album_voices(album) or [None])[0])
        chapters = search_manager.qimao_manager.get_chapters(str(album_id), voice) if voice else search_manager.qimao_manager.get_chapters(str(album_id))
    else:
        chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    chapters = [normalize_chapter(chapter, index) for index, chapter in enumerate(chapters or [], start=1)]
    if not chapters and item.get("chapters"):
        chapters = item.get("chapters") or []
    set_progress("姝ｅ湪鎵弿鏈湴鏂囦欢", chapter_count=len(chapters))
    scan_cache = {}
    diff = subscription_manager.diff_chapters(item, chapters, active_download_dir(), scan_cache=scan_cache, skip_local=False)
    set_progress("姝ｅ湪鏇存柊璁㈤槄缁撴灉", missing_count=len(diff.get("missing") or []))
    subscription_manager.update_check_result(sid, chapters, diff, "鑷姩妫€娴嬪畬鎴? if queue_missing else "宸叉鏌?, refresh_local=False)
    item = subscription_manager.get(sid) or item
    item["download_dir"] = active_download_dir()
    stats = subscription_manager.stats_for(item, active_download_dir(), fast=True)
    queued_task_id = ""
    missing = diff.get("missing") or []
    if queue_missing and missing:
        set_progress("姝ｅ湪鍒涘缓涓嬭浇浠诲姟", missing_count=len(missing))
        if not voice:
            voices = get_album_voices(album)
            voice = voices[0] if voices else None
        queued_task_id = f"sub-{uuid.uuid4().hex[:12]}"
        options = {"download_dir": active_download_dir(), "quality": subscription_manager.settings().get("quality", "M4A 96K"), "voice": voice}
        start_download_task(queued_task_id, album, missing, options, source=source)
        notification_manager.notify(
            "subscription_queued",
            f"璁㈤槄鍙戠幇鏂扮珷鑺傦細{album.get('title') or '鏈煡涓撹緫'}",
            f"骞冲彴锛歿platform}\n鏂板/缂哄け锛歿len(missing)} 绔燶n浠诲姟锛歿queued_task_id}",
            {"album": album, "missing_count": len(missing), "task_id": queued_task_id, "source": source},
        )
    elif diff.get("missing") and not queue_missing:
        notification_manager.notify(
            "subscription_checked",
            f"璁㈤槄妫€娴嬪彂鐜扮己澶憋細{album.get('title') or '鏈煡涓撹緫'}",
            f"骞冲彴锛歿platform}\n缂哄け锛歿len(diff.get('missing') or [])} 绔?,
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
                "message": "姝ｅ湪妫€娴嬭闃?,
                "started_at": started_at,
                "updated_at": started_at,
            }
        )
    try:
        result = _run_subscription_check(sid, queue_missing=queue_missing, source="subscription", progress=update_progress)
        message = "宸插姞鍏ヤ笅杞介槦鍒? if result.get("queued") else "妫€娴嬪畬鎴愶紝鏃犻渶琛ュ叏" if queue_missing else "妫€娴嬪畬鎴?
        append_background_event(
            "subscription",
            message,
            f"{result.get('title') or sid} 缂哄け {result.get('missing_count') or 0} 绔?,
            {"sid": sid, "queue_missing": queue_missing, "result": result},
        )
        finished_at = time.time()
        with subscription_job_lock:
            subscription_jobs[job_id].update(
                {"status": "done", "message": message, "result": result, "finished_at": finished_at, "updated_at": finished_at}
            )
    except Exception as exc:
        logging.exception("subscription job failed")
        append_background_event("subscription", "璁㈤槄妫€娴嬪け璐?, f"{sid}锛歿exc}", {"sid": sid, "error": str(exc)})
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
                message = "璁㈤槄妫€娴嬭秴鏃讹紝璇风◢鍚庨噸璇?
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
                    "璁㈤槄妫€娴嬭秴鏃?,
                    f"{job.get('sid') or job_id} 妫€娴嬭秴鏃?,
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
            "message": "宸插姞鍏ュ悗鍙伴槦鍒?,
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
        return json_error(f"鐧诲綍澶辫触娆℃暟杩囧锛岃 {auth_manager.lock_remaining(username)} 绉掑悗鍐嶈瘯", 429)
    token = auth_manager.login(username, password)
    if not token:
        return json_error("璐﹀彿鎴栧瘑鐮侀敊璇?, 401)
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
        return json_error("鏈櫥褰曟垨浼氳瘽宸茶繃鏈?, 401)
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
    if platform == "鍠滈┈鎷夐泤":
        return "https://imagev2.xmcdn.com" + (url if url.startswith("/") else f"/{url}")
    if platform == "鎳掍汉鍚功":
        return "https://m.lrts.me" + (url if url.startswith("/") else f"/{url}")
    if platform == "浜戝惉FM":
        return "https://www.radio.cn" + (url if url.startswith("/") else f"/{url}")
    return url


def normalize_album(album):
    data = dict(album or {})
    platform = _pick_nested_value(data, ("platform", "source")) or "鏈煡骞冲彴"
    title = _pick_nested_value(data, ("title", "album_title", "albumTitle", "book_name", "bookName", "name", "AudioName")) or "鏈煡涓撹緫"
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
        if value and (not merged.get(key) or str(merged.get(key)).strip() in ("鏈煡", "鏈煡浣滆€?, "鏈煡涓撹緫")):
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
        or (f"绗?{index} 绔? if index else "鏈煡绔犺妭")
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
    data.setdefault("name", data.get("title") or data.get("label") or f"闊宠壊{index or ''}")
    kind = data.get("kind")
    if not kind:
        kind = "real" if str(data.get("is_real_person") or "") == "1" else "ai"
    data["kind"] = kind
    data.setdefault("category", "鐪熶汉褰曞埗" if kind == "real" else "AI 闊宠壊")
    return data


def get_album_voice_context(album):
    album = normalize_album(album)
    album_id = str(album.get("id") or album.get("album_id") or album.get("book_id") or "")
    book_id = str(album.get("book_id") or album_id)
    platform = album.get("platform")
    return album, album_id, book_id, platform


def get_album_voices(album):
    album, album_id, book_id, platform = get_album_voice_context(album)
    if platform == "鐣寗鐣呭惉":
        voices = search_manager.fanqie_manager.fetch_voices(book_id or album_id)
        for voice in voices:
            voice.setdefault("platform", platform)
        return [normalize_voice(v, i) for i, v in enumerate(voices, 1)]
    if platform == "鐣寗鍚功":
        voices = search_manager.fanqie_tingshu_manager.fetch_voices(book_id)
        return [normalize_voice(v, i) for i, v in enumerate(voices, 1)]
    if platform == "涓冪尗鍚功":
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
    if platform == "鐣寗鐣呭惉":
        return search_manager.fanqie_manager.resolve_voice_config(book_id or album_id, voice) or voice
    if platform == "鐣寗鍚功":
        return search_manager.fanqie_tingshu_manager.resolve_voice_config(book_id, voice) or voice
    if platform == "涓冪尗鍚功":
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
        "鍠滈┈鎷夐泤": "xmly",
        "鎳掍汉鍚功": "lrts",
        "璧风偣鍚功": "qidian",
        "铚昏湏FM": "qtfm",
        "缃戞槗浜戝惉涔?: "netease",
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
    if album.get("platform") == "鎳掍汉鍚功":
        sync_platform_cookie("鎳掍汉鍚功")
    options["download_dir"] = resolve_download_dir(options.get("download_dir"))
    _write_album_source_file(album, options, task_id)
    warning = str(options.get("warning") or "").strip()
    if not warning and album.get("platform") == "鎳掍汉鍚功":
        expected = _to_int(album.get("episodes"))
        if expected > 0 and len(chapters) < expected:
            warning = f"鎳掍汉鍚功鐩綍鍙兘鏈畬鏁村姞杞斤細褰撳墠浠诲姟 {len(chapters)}/{expected} 绔犮€?
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
        ("涓嬭浇瀹屾垚" if status == "completed" else "涓嬭浇閮ㄥ垎瀹屾垚" if status == "partial" else "涓嬭浇鍋滄"),
        f"{task.get('title') or task_id} 鎴愬姛 {success} 绔狅紝澶辫触 {failed} 绔? + (f"锛屽師鍥狅細{failure_reason}" if failure_reason else ""),
        {"task_id": task_id, "status": status, "success": success, "failed": failed, "failure_reason": failure_reason},
    )
    if status in ("completed", "partial"):
        scene = "download_completed" if status == "completed" else "download_failed"
        title = "涓嬭浇瀹屾垚" if status == "completed" else "涓嬭浇閮ㄥ垎瀹屾垚"
        notification_manager.notify(
            scene,
            f"{title}锛歿task.get('title') or task_id}",
            f"骞冲彴锛歿album.get('platform') or '-'}\n鎴愬姛锛歿success} 绔燶n澶辫触锛歿failed} 绔燶n浠诲姟锛歿task_id}",
            {"task": task, "album": album, "success": success, "failed": failed},
        )
    return task


def run_download_task(task_id, album, chapters, options):
    album = normalize_album(album)
    chapters = list(chapters or [])
    options = dict(options or {})
    warning = str(options.get("warning") or "").strip()
    if not warning and album.get("platform") == "鎳掍汉鍚功":
        expected = _to_int(album.get("episodes"))
        if expected > 0 and len(chapters) < expected:
            warning = f"鎳掍汉鍚功鐩綍鍙兘鏈畬鏁村姞杞斤細褰撳墠浠诲姟 {len(chapters)}/{expected} 绔犮€?
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
            album_title=album.get("title") or "鏈煡涓撹緫",
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
            "涓嬭浇澶辫触",
            f"{task.get('title') or task_id}锛歿failure_reason}",
            {"task_id": task_id, "error": str(exc), "failure_reason": failure_reason},
        )
        notification_manager.notify(
            "download_failed",
            f"涓嬭浇澶辫触锛歿task.get('title') or task_id}",
            f"閿欒锛歿exc}\n浠诲姟锛歿task_id}",
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
        ai_api_key_masked=_mask_api_key(str(cookie_manager.get_cookie("ai_api_key") or "")),
        ai_enabled=bool(cookie_manager.get_cookie("ai_api_key")),
    )


@app.post("/api/config")
def api_set_config():
    """淇濆瓨绯荤粺璁剧疆锛氫笅杞界洰褰曘€侀煶璐ㄣ€佸苟鍙戠嚎绋嬫暟"""
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
        ai_api_key_masked=_mask_api_key(str(cookie_manager.get_cookie("ai_api_key") or "")),
        ai_enabled=bool(cookie_manager.get_cookie("ai_api_key")),
        download_dir=str(active_download_dir()),
        download_threads=cookie_manager.get_download_threads(),
        quality=subscription_manager.settings().get("quality", "M4A 96K"),
        organize_by_platform_enabled=cookie_manager.get_cookie("organize_by_platform_enabled") == "true",
        split_chapters_enabled=cookie_manager.get_cookie("split_chapters_enabled") == "true",
        chapters_per_folder=int_cookie_setting("chapters_per_folder", 200),
        filename_prefix_format=cookie_manager.get_cookie("filename_prefix_format") or "0001-",
        ai_api_key_masked=_mask_api_key(str(cookie_manager.get_cookie("ai_api_key") or "")),
        ai_enabled=bool(cookie_manager.get_cookie("ai_api_key")),
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
        raise ValueError("浼佷笟寰俊搴旂敤閫氱煡娓犻亾涓嶅瓨鍦?)
    config = dict(service.get("config") or {})
    missing = [key for key in ("corp_id", "agent_id", "secret", "token", "encoding_aes_key") if not str(config.get(key) or "").strip()]
    if missing:
        raise ValueError("浼佷笟寰俊搴旂敤鍥炶皟閰嶇疆涓嶅畬鏁达細" + "銆?.join(missing))
    return service, config


def _wecom_help_text():
    return (
        "AudioFlow 浼佷笟寰俊鎸囦护锛歕n"
        "甯姪锛氭樉绀烘寚浠n"
        "鐘舵€侊細鏌ョ湅鏈嶅姟鐗堟湰鍜屼换鍔℃暟\n"
        "鎼滅储 鍏抽敭璇嶏細鎼滅储鏈夊０涔n"
        "璁㈤槄 搴忓彿锛氳闃呮渶杩戜竴娆℃悳绱㈢粨鏋淺n"
        "涓嬭浇 搴忓彿锛氫笅杞芥渶杩戜竴娆℃悳绱㈢粨鏋滃叏閮ㄧ珷鑺俓n"
        "绀轰緥锛氭悳绱?涓変綋"
    )


def _wecom_album_lines(results):
    lines = []
    for index, item in enumerate(results[:8], start=1):
        title = item.get("title") or "鏈煡涓撹緫"
        platform = item.get("platform") or "鏈煡骞冲彴"
        author = item.get("author") or "鏈煡浣滆€?
        episodes = item.get("episodes") or "?"
        lines.append(f"{index}. {title}\n   {platform} / {author} / {episodes} 绔?)
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
        raise ValueError("璇疯緭鍏ユ纭簭鍙凤紝渚嬪锛氳闃?1")
    with wecom_session_lock:
        cleanup_wecom_sessions()
        session = wecom_sessions.get(_wecom_session_key(service_id, user_id)) or {}
    results = session.get("results") or []
    if not results:
        raise ValueError("杩樻病鏈夋悳绱㈢粨鏋滐紝璇峰厛鍙戦€侊細鎼滅储 鍏抽敭璇?)
    if index < 1 or index > len(results):
        raise ValueError(f"搴忓彿瓒呭嚭鑼冨洿锛岃杈撳叆 1-{len(results)}")
    return normalize_album(results[index - 1])


def _wecom_load_album_chapters(album):
    album = normalize_album(album)
    album_id = album.get("id") or album.get("album_id") or album.get("book_id")
    platform = album.get("platform")
    if not album_id or not platform:
        raise ValueError("缂哄皯涓撹緫 ID 鎴栧钩鍙?)
    voice = resolve_voice_for_album(album, None)
    if platform == "涓冪尗鍚功":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    if platform == "鐣寗鐣呭惉" and voice:
        raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), voice, page=1, page_size=10000)
    elif platform == "鐣寗鍚功" and voice:
        raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), voice)
    elif platform == "涓冪尗鍚功" and voice:
        raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), voice)
    else:
        raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    chapters = [normalize_chapter(chapter, index) for index, chapter in enumerate(raw_chapters or [], start=1)]
    if not chapters:
        raise ValueError("娌℃湁鑾峰彇鍒扮珷鑺傚垪琛?)
    return album, chapters, voice


def _wecom_handle_text_command(service_id, user_id, text):
    text = str(text or "").strip()
    if not text or text in {"甯姪", "help", "/help", "锛?, "?"}:
        return _wecom_help_text()
    if text in {"鐘舵€?, "status", "/status"}:
        tasks_now = task_snapshot()
        running = sum(1 for item in tasks_now if item.get("status") in {"running", "pending", "paused"})
        return f"AudioFlow v{APP_VERSION}\n浠诲姟鎬绘暟锛歿len(tasks_now)}\n杩涜涓細{running}"
    match = re.match(r"^(鎼滅储|search|/search)\s+(.+)$", text, re.I)
    if match:
        keyword = match.group(2).strip()
        results = [normalize_album(item) for item in search_manager.search_books(keyword, "all")][:8]
        with wecom_session_lock:
            cleanup_wecom_sessions()
            wecom_sessions[_wecom_session_key(service_id, user_id)] = {"keyword": keyword, "results": results, "updated_at": time.time()}
        if not results:
            return f"娌℃湁鎼滅储鍒帮細{keyword}"
        return f"鎼滅储缁撴灉锛歿keyword}\n{_wecom_album_lines(results)}\n\n鍙戦€佲€滆闃?搴忓彿鈥濇垨鈥滀笅杞?搴忓彿鈥濈户缁€?
    match = re.match(r"^(璁㈤槄|subscribe|/subscribe)\s+(\d+)$", text, re.I)
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
        suffix = f"\n宸插惎鍔ㄦ娴嬩换鍔★細{job.get('id')}" if job else ""
        return f"宸茶闃咃細{album.get('title')}\n绔犺妭鏁帮細{len(chapters)}{suffix}"
    match = re.match(r"^(涓嬭浇|download|/download)\s+(\d+)$", text, re.I)
    if match:
        album = _wecom_get_cached_album(service_id, user_id, match.group(2))
        album, chapters, voice = _wecom_load_album_chapters(album)
        options = {"download_dir": active_download_dir(), "quality": subscription_manager.settings().get("quality", "M4A 96K")}
        if voice:
            options["voice"] = voice
        task_id = f"wecom-{uuid.uuid4().hex[:12]}"
        start_download_task(task_id, album, chapters, options, source="wecom")
        return f"宸插姞鍏ヤ笅杞斤細{album.get('title')}\n绔犺妭鏁帮細{len(chapters)}\n浠诲姟 ID锛歿task_id}"
    return "鏃犳硶璇嗗埆鎸囦护銆俓n\n" + _wecom_help_text()


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
                reply = "鐩墠浠呮敮鎸佹枃瀛楁寚浠ゃ€俓n\n" + _wecom_help_text()
        except Exception as exc:
            logging.exception("wecom command failed: %s", service_id)
            reply = f"鎸囦护鎵ц澶辫触锛歿exc}\n\n{_wecom_help_text()}"
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


@app.get("/api/files")
def api_list_files():
    """列出下载目录中的文件和文件夹"""
    base_str = request.args.get("path", "").strip()
    try:
        base = Path(active_download_dir())
        if base_str:
            base = _safe_child_path(base, base_str)
        if not base.exists():
            return json_error("目录不存在", 404)
        if not base.is_dir():
            return json_error("路径不是目录", 400)
        items = []
        for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append({
                "name": entry.name,
                "path": str(entry.relative_to(active_download_dir())).replace("\\", "/"),
                "full_path": str(entry),
                "is_dir": entry.is_dir(),
                "size": stat.st_size if entry.is_file() else 0,
                "mtime": stat.st_mtime,
                "ext": entry.suffix.lower() if entry.is_file() else "",
            })
        return json_ok(
            items=items,
            current_path=str(Path(base_str).as_posix()) if base_str else "",
            parent_path=str(Path(base_str).parent.as_posix()) if base_str else "",
            base_dir=str(active_download_dir()),
        )
    except ValueError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:
        logging.exception("list files failed")
        return json_error(str(exc), 500)


@app.post("/api/files/rename")
def api_rename_file():
    """重命名文件或文件夹"""
    payload = request.get_json(silent=True) or {}
    file_path = str(payload.get("path") or "").strip()
    new_name = str(payload.get("new_name") or "").strip()
    if not file_path or not new_name:
        return json_error("缺少 path 或 new_name 参数")
    if "/" in new_name or "\\" in new_name:
        return json_error("新名称不能包含路径分隔符")
    try:
        base = active_download_dir()
        src = _safe_child_path(base, file_path)
        if not src.exists():
            return json_error("文件或目录不存在", 404)
        dst = src.parent / new_name
        if dst.exists():
            return json_error("目标名称已存在", 409)
        src.rename(dst)
        rel_path = str(dst.relative_to(base)).replace("\\", "/")
        return json_ok(
            renamed=True,
            old_name=src.name,
            new_name=dst.name,
            path=rel_path,
            is_dir=dst.is_dir(),
        )
    except ValueError as exc:
        return json_error(str(exc), 400)
    except OSError as exc:
        return json_error(f"重命名失败: {exc}", 500)
    except Exception as exc:
        logging.exception("rename failed")
        return json_error(str(exc), 500)


@app.post("/api/files/scrape")
def api_scrape_file():
    """刮削文件元数据（预留功能）"""
    payload = request.get_json(silent=True) or {}
    file_path = str(payload.get("path") or "").strip()
    if not file_path:
        return json_error("缺少 path 参数")
    try:
        base = active_download_dir()
        src = _safe_child_path(base, file_path)
        if not src.exists():
            return json_error("文件或目录不存在", 404)
        return json_ok(
            name=src.name,
            path=file_path,
            is_dir=src.is_dir(),
            size=src.stat().st_size if src.is_file() else 0,
            message="刮削功能暂未实现，将在后续版本集成 AI",
        )
    except ValueError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:
        return json_error(str(exc), 500)


@app.post("/api/files/ai-rename")
def api_ai_rename():
    """使用 DeepSeek AI 为文件生成新名称"""
    payload = request.get_json(silent=True) or {}
    file_path = str(payload.get("path") or "").strip()
    if not file_path:
        return json_error("缺少 path 参数")
    api_key = str(cookie_manager.get_cookie("ai_api_key") or "").strip()
    if not api_key:
        return json_error("请先在系统设置中配置 AI API Key", 400)
    try:
        base = active_download_dir()
        src = _safe_child_path(base, file_path)
        if not src.exists():
            return json_error("文件或目录不存在", 404)
        name_stem = src.stem
        ext = src.suffix
        model = cookie_manager.get_cookie("ai_model") or "deepseek-chat"
        base_url = cookie_manager.get_cookie("ai_base_url") or "https://api.deepseek.com"
        prompt = (
            "你是一个音频文件重命名助手。请根据以下文件名分析其内容，生成一个更规范、可读性更强的文件名。"
            f"文件名：{name_stem}\n"
            "要求：\n"
            "1. 保留原文件中的序号信息\n"
            "2. 如果是音频章节，提取专辑名、章节号、章节标题\n"
            "3. 使用中文命名，去除乱码或无关字符\n"
            "4. 只返回新文件名（不带路径和扩展名），不要任何解释"
        )
        resp = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 128,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return json_error(f"AI 请求失败: {resp.status_code} {resp.text[:200]}", 502)
        result = resp.json()
        new_stem = (result.get("choices") or [{}])[0].get("message", {}).get("content", "").strip().strip("\"'`").strip()
        if not new_stem:
            return json_error("AI 返回内容为空", 502)
        new_name = new_stem + ext
        if new_name == src.name:
            new_name = new_stem + "_renamed" + ext
        dst = src.parent / new_name
        if dst.exists():
            new_name = new_stem + f"_{uuid.uuid4().hex[:4]}" + ext
            dst = src.parent / new_name
        src.rename(dst)
        rel_path = str(dst.relative_to(base)).replace("\\", "/")
        return json_ok(
            renamed=True,
            old_name=src.name,
            new_name=dst.name,
            path=rel_path,
        )
    except ValueError as exc:
        return json_error(str(exc), 400)
    except requests.RequestException as exc:
        return json_error(f"AI 请求失败: {exc}", 502)
    except OSError as exc:
        return json_error(f"重命名失败: {exc}", 500)
    except Exception as exc:
        logging.exception("ai rename failed")
        return json_error(str(exc), 500)




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
        return json_error("璇疯緭鍏ユ悳绱㈠叧閿瘝")
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
        return json_error("缂哄皯涓撹緫 ID 鎴栧钩鍙?)
    if platform == "涓冪尗鍚功":
        search_manager.qimao_manager._search_cache[str(album_id)] = dict(album)
        if album.get("book_id"):
            search_manager.qimao_manager._search_cache[str(album.get("book_id"))] = dict(album)
        if album.get("album_id"):
            search_manager.qimao_manager._search_cache[str(album.get("album_id"))] = dict(album)
    active_voice = resolve_voice_for_album(album, voice)
    if platform == "鐣寗鐣呭惉" and active_voice:
        raw_chapters = search_manager.fanqie_manager.get_chapters_for_voice(str(album_id), active_voice, page=1, page_size=10000)
    elif platform == "鐣寗鍚功" and active_voice:
        raw_chapters = search_manager.fanqie_tingshu_manager.get_chapters(str(album_id), active_voice)
    elif platform == "涓冪尗鍚功" and active_voice:
        raw_chapters = search_manager.qimao_manager.get_chapters(str(album_id), active_voice)
    else:
        raw_chapters = search_manager.get_album_chapters(str(album_id), platform) or []
    warning = ""
    if platform == "鎳掍汉鍚功":
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
    if platform == "鎳掍汉鍚功" and expected > 0 and len(chapters) < expected and not warning:
        warning = f"鎳掍汉鍚功鐩綍鍙兘鏈畬鏁村姞杞斤細褰撳墠鑾峰彇 {len(chapters)}/{expected} 绔犮€?
    if warning:
        album["catalog_warning"] = warning
    return json_ok(album=album, chapters=chapters, count=len(chapters), voice=active_voice, warning=warning)


@app.post("/api/album/voices")
def api_album_voices():
    payload = request.get_json(silent=True) or {}
    album = normalize_album(payload.get("album") or payload)
    if not album.get("platform"):
        return json_error("缂哄皯骞冲彴淇℃伅")
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
        return json_error("缂哄皯涓撹緫銆佺珷鑺傛垨骞冲彴淇℃伅锛屾棤娉曟挱鏀?)
    try:
        if platform == "鐣寗鐣呭惉":
            info = search_manager.fanqie_manager.get_audio_download_info(
                str(track_id),
                voice or "鏃犳崯鐪熶汉褰曞埗",
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
                        voice or "鏃犳崯鐪熶汉褰曞埗",
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
            if platform == "鐣寗鍚功" and voice:
                path_or_url = search_manager.fanqie_tingshu_manager.prepare_playback(str(track_id), voice)
                url = path_or_url or ""
            elif platform == "涓冪尗鍚功" and voice:
                path_or_url = search_manager.qimao_manager.prepare_playback(str(track_id), voice_config=voice)
                url = path_or_url or ""
            elif platform == "鎳掍汉鍚功":
                sync_platform_cookie(platform)
                url = search_manager.lrts_manager.get_audio_url(str(album_id), str(track_id), chapter)
            else:
                url = pick_audio_url(search_manager.get_audio_urls(str(track_id), platform, str(album_id), voice_name))
        if not url:
            return json_error("鏈幏鍙栧埌鍙挱鏀剧殑闊抽鍦板潃")
        local_url = register_local_audio(url)
        if local_url:
            return json_ok(url=local_url, source_url=local_url)
        # 娴忚鍣ㄧ洿鎺ユ媺绗笁鏂?CDN 閫氬父浼氬洜 Referer/Origin 鏍￠獙鎴栫己灏?cookie 鑰?403/闈欓煶锛?        # 鏀硅蛋鏈嶅姟绔唬鐞嗐€傚師濮?URL 涔熶竴骞惰繑鍥烇紝鏂逛究鍓嶇/璋冭瘯銆?        proxy_url = register_audio_proxy_url(url, platform)
        if not proxy_url:
            return json_error("闊抽鍦板潃鏃犳硶鐢熸垚瀹夊叏浠ｇ悊閾炬帴")
        return json_ok(url=proxy_url, source_url=url)
    except Exception as exc:
        return json_error(str(exc), status=500)


# 鈹€鈹€ 闊抽浠ｇ悊 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 娴忚鍣ㄦ挱鏀剧涓夋柟 CDN 鏃跺父鍥犱负 Referer / Origin / cookie 鏍￠獙澶辫触鑰屾棤澹般€?# 鏈嶅姟绔唬鐞嗕竴灞傦紝鎸夊钩鍙拌ˉ姝ｇ‘鐨?Referer/UA锛屽啀浠ユ祦寮?chunk 鍥炰紶缁欐祻瑙堝櫒銆?_PLATFORM_REFERER = {
    "鍠滈┈鎷夐泤": "https://www.ximalaya.com/",
    "鎳掍汉鍚功": "https://www.lrts.me/",
    "鐣寗鐣呭惉": "https://fanqienovel.com/",
    "铚昏湏FM": "https://www.qtfm.cn/",
    "浜戝惉FM": "https://www.radio.cn/",
    "璧风偣鍚功": "https://www.qidian.com/",
    "閰锋垜鍚功": "https://www.kuwo.cn/",
    "缃戞槗浜戝惉涔?: "https://music.163.com/",
    "鑽旀灊FM": "https://m.lizhi.fm/",
}

_PROXY_ALLOWED_SCHEMES = ("http", "https")
_AUDIO_PROXY_TOKENS = {}
_AUDIO_PROXY_TOKEN_TTL = 15 * 60

_PLATFORM_AUDIO_HOST_HINTS = {
    "鍠滈┈鎷夐泤": ("ximalaya.com", "xmcdn.com", "ximalayaos.com"),
    "鎳掍汉鍚功": ("lrts.me", "lrts1.com", "ting55.com"),
    "鐣寗鐣呭惉": ("fanqienovel.com", "snssdk.com", "byteimg.com", "toutiao.com", "bytedance.com"),
    "鐣寗鍚功": ("fanqienovel.com", "snssdk.com", "byteimg.com", "toutiao.com", "bytedance.com"),
    "涓冪尗鍚功": ("qimao.com", "qimao.tv", "qimaoapi.com"),
    "铚昏湏FM": ("qtfm.cn", "qingting.fm", "qtfm.com"),
    "浜戝惉FM": ("radio.cn", "cnr.cn", "yunting.cn"),
    "璧风偣鍚功": ("qidian.com", "qdmobi.com"),
    "閰锋垜鍚功": ("kuwo.cn", "kuwo.com"),
    "缃戞槗浜戝惉涔?: ("music.163.com", "music.126.net", "netease.com"),
    "鑽旀灊FM": ("lizhi.fm", "lizhi.io"),
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
            raise ValueError("鎾斁閾炬帴宸茶繃鏈燂紝璇烽噸鏂版墦寮€璇曞惉")
        return str(item.get("url") or ""), str(item.get("platform") or ""), True
    if not audio_proxy_raw_url_enabled():
        raise ValueError("涓嶅厑璁哥洿鎺ヤ唬鐞嗗閮ㄩ煶棰戝湴鍧€")
    return (request.args.get("url") or "").strip(), (request.args.get("platform") or "").strip(), False


def _validate_audio_proxy_target(src, platform, trusted_token=False):
    parsed = urlparse(src)
    if parsed.scheme not in _PROXY_ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("闈炴硶鐨勯煶棰戝湴鍧€")
    if parsed.username or parsed.password:
        raise ValueError("闊抽鍦板潃涓嶈兘鍖呭惈璁よ瘉淇℃伅")
    hostname = parsed.hostname or ""
    if _hostname_is_private(hostname):
        raise ValueError("涓嶅厑璁歌闂唴缃戞垨鏈満鍦板潃")
    if not trusted_token and not _is_allowed_audio_host(platform, hostname):
        raise ValueError("闊抽鍩熷悕涓嶅湪骞冲彴鐧藉悕鍗曞唴")
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
            raise ValueError("涓婃父璺宠浆缂哄皯 Location")
        current = requests.compat.urljoin(current, location)
        if upstream.status_code == 303:
            method = "GET"
    raise ValueError("涓婃父璺宠浆娆℃暟杩囧")


@app.route("/api/local-audio/<token>", methods=["GET", "HEAD"])
def api_local_audio(token):
    cleanup_local_audio_tokens()
    item = _LOCAL_AUDIO_TOKENS.get(token)
    if not item:
        return json_error("闊抽涓存椂鏂囦欢宸插け鏁?, status=404)
    path = Path(str(item.get("path") or ""))
    if not path.is_file():
        _LOCAL_AUDIO_TOKENS.pop(token, None)
        return json_error("闊抽涓存椂鏂囦欢涓嶅瓨鍦?, status=404)
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
    """娴佸紡浠ｇ悊绗笁鏂归煶棰戙€?
    Query:
        url: 鍘熷闊抽 URL锛堝繀濉紝搴斾负 http/https锛?        platform: 骞冲彴鍚嶏紝鐢ㄤ簬琛ユ纭殑 Referer
    """
    try:
        src, platform, trusted_token = _resolve_audio_proxy_request()
        if not src:
            return json_error("缂哄皯闊抽鍦板潃")
        _validate_audio_proxy_target(src, platform, trusted_token=trusted_token)
    except ValueError as exc:
        return json_error(str(exc), status=403)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",  # 閬垮厤涓婃父 gzip 鍚庢祦寮忎笉鏄撳鐞?        "Connection": "keep-alive",
    }
    referer = _PLATFORM_REFERER.get(platform)
    if platform == "鐣寗鐣呭惉":
        referer = ""
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.rstrip("/")
    # 閫忎紶 Range锛屾敮鎸佹祻瑙堝櫒鎷栧姩杩涘害鏉?    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    # 閮ㄥ垎骞冲彴闊抽 CDN 闇€瑕佸甫骞冲彴 cookie銆傚彧鏈夋湇鍔＄绛惧彂鐨勭煭鏈?token
    # 鎵嶈兘瑙﹀彂 Cookie 閫忎紶锛岄伩鍏嶅閮ㄦ瀯閫?URL 绐冨彇 Cookie銆?    cookie_key_map = {
        "鍠滈┈鎷夐泤": "xmly", "鎳掍汉鍚功": "lrts", "璧风偣鍚功": "qidian",
        "铚昏湏FM": "qtfm", "鐣寗鐣呭惉": "fanqie", "鐣寗鍚功": "fanqie_tingshu",
        "涓冪尗鍚功": "qimao", "浜戝惉FM": "yuntu", "閰锋垜鍚功": "kuwo", "缃戞槗浜戝惉涔?: "netease",
        "鑽旀灊FM": "lizhi",
    }
    cookie_required_platforms = {"鍠滈┈鎷夐泤", "鎳掍汉鍚功", "璧风偣鍚功", "铚昏湏FM", "缃戞槗浜戝惉涔?}
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
        return json_error(f"涓婃父璇锋眰澶辫触锛歿exc}", status=502)

    if upstream.status_code >= 400:
        upstream.close()
        return json_error(f"涓婃父杩斿洖 {upstream.status_code}", status=upstream.status_code)

    # 閫忎紶鍏抽敭鍝嶅簲澶?    passthrough = {}
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
        return json_error("缂哄皯涓撹緫鎴栫珷鑺?)
    if album.get("platform") == "鎳掍汉鍚功":
        sync_platform_cookie("鎳掍汉鍚功")
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
    append_background_event("download", "閲嶈瘯鏈畬鎴愪换鍔?, f"鍒涘缓 {len(created)} 涓噸璇曚换鍔?, {"count": len(created)})
    return json_ok(count=len(created), tasks=created)


@app.get("/api/downloads/<task_id>")
def api_download_detail(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
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
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
    worker = live_worker(task_id)
    if not worker:
        return json_error("浠诲姟鏈湪杩愯锛屾棤娉曟殏鍋?, 409)
    pause_worker(worker)
    return json_ok(task=set_task(task_id, status="paused"))


@app.post("/api/downloads/<task_id>/resume")
def api_download_resume(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
    worker = live_worker(task_id)
    if not worker:
        return json_error("浠诲姟鏈湪杩愯锛屾棤娉曠户缁?, 409)
    resume_worker(worker)
    return json_ok(task=set_task(task_id, status="running"))


@app.post("/api/downloads/<task_id>/stop")
def api_download_stop(task_id):
    task = task_snapshot(task_id)
    if not task:
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
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
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
    chapters = task.get("failed_chapters") or []
    if not chapters and task.get("status") in ("failed", "interrupted", "stopped"):
        chapters = task.get("chapters") or []
    if not chapters:
        return json_error("娌℃湁鍙噸璇曠殑澶辫触绔犺妭")
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
        return json_error("浠诲姟涓嶅瓨鍦?, 404)
    if live_worker(task_id) or task.get("status") in ("running", "queued", "paused", "stopping"):
        return json_error("杩愯涓殑浠诲姟涓嶈兘鍒犻櫎锛岃鍏堝仠姝?, 409)
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
    if platform != "閰锋垜鍚功" or item.get("cover") or _album_cover_value(album):
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
        return json_error("涓嬭浇鐩綍涓嶅瓨鍦?, 404)
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
            skipped.append({"title": title, "platform": platform, "reason": "鐩爣鐩綍宸插瓨鍦?, "source": str(source), "target": str(target)})
            continue
        moved.append({"title": title, "platform": platform, "source": str(source), "target": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
    if not dry_run:
        subscription_manager.build_audio_index(active_download_dir(), force=True)
    append_background_event(
        "maintenance",
        "涓嬭浇鐩綍鏁寸悊",
        f"{'棰勮' if dry_run else '瀹屾垚'}锛氱Щ鍔?{len(moved)} 涓紝璺宠繃 {len(skipped)} 涓?,
        {"dry_run": dry_run, "moved": moved, "skipped": skipped},
    )
    return json_ok(dry_run=dry_run, moved=moved, skipped=skipped, moved_count=len(moved), skipped_count=len(skipped))


@app.get("/api/subscriptions/settings")
def api_get_subscription_settings():
    """璇诲彇璁㈤槄鑷姩妫€娴嬭缃€?""
    ensure_subscription_scheduler()
    return json_ok(settings=subscription_manager.settings())


@app.post("/api/subscriptions/settings")
def api_update_subscription_settings():
    """鏇存柊璁㈤槄鑷姩妫€娴嬭缃€?
    Body: {"enabled": bool, "auto_download_missing": bool, "interval_hours": int, "interval_minutes": int, "quality": str(鍙€?}
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
            return json_error("interval_hours 蹇呴』鏄暣鏁?)
        if hours < 1:
            return json_error("妫€娴嬮棿闅旇嚦灏?1 灏忔椂")
        if hours > 24 * 30:
            return json_error("妫€娴嬮棿闅旇繃澶?)
        updates["interval_hours"] = hours
        # 閲嶇疆鍒嗛挓瀛楁锛屾寜灏忔椂涓哄崟浣?        updates["interval_minutes"] = 0
    if "interval_minutes" in payload:
        try:
            minutes = int(payload.get("interval_minutes") or 0)
        except Exception:
            return json_error("interval_minutes 蹇呴』鏄暣鏁?)
        if minutes < 1:
            return json_error("妫€娴嬮棿闅旇嚦灏?1 鍒嗛挓")
        if minutes > 24 * 30 * 60:
            return json_error("妫€娴嬮棿闅旇繃澶?)
        updates["interval_hours"] = 0
        updates["interval_minutes"] = minutes
    if "quality" in payload and str(payload.get("quality") or "").strip():
        updates["quality"] = str(payload.get("quality")).strip()
    if "personal_sync_enabled" in payload:
        updates["personal_sync_enabled"] = bool(payload.get("personal_sync_enabled"))
    if "personal_sync_platform" in payload and str(payload.get("personal_sync_platform") or "").strip():
        platform = str(payload.get("personal_sync_platform")).strip()
        if platform != "ximalaya":
            return json_error("鐩墠浠呮敮鎸佸悓姝ュ枩椹媺闆呬釜浜轰腑蹇冭闃?)
        updates["personal_sync_platform"] = platform
    if "personal_sync_interval_hours" in payload:
        try:
            hours = int(payload.get("personal_sync_interval_hours") or 0)
        except Exception:
            return json_error("personal_sync_interval_hours 蹇呴』鏄暣鏁?)
        if hours < 1:
            return json_error("涓汉涓績鍚屾闂撮殧鑷冲皯 1 灏忔椂")
        if hours > 24 * 30:
            return json_error("涓汉涓績鍚屾闂撮殧杩囧ぇ")
        updates["personal_sync_interval_hours"] = hours
        updates["personal_sync_interval_minutes"] = 0
    if "personal_sync_interval_minutes" in payload:
        try:
            minutes = int(payload.get("personal_sync_interval_minutes") or 0)
        except Exception:
            return json_error("personal_sync_interval_minutes 蹇呴』鏄暣鏁?)
        if minutes < 1:
            return json_error("涓汉涓績鍚屾闂撮殧鑷冲皯 1 鍒嗛挓")
        if minutes > 24 * 30 * 60:
            return json_error("涓汉涓績鍚屾闂撮殧杩囧ぇ")
        updates["personal_sync_interval_hours"] = 0
        updates["personal_sync_interval_minutes"] = minutes
    if not updates:
        return json_error("鏈彁渚涗换浣曞彲鏇存柊鐨勫瓧娈?)
    subscription_manager.update_settings(**updates)
    # 寮€鍚椂纭繚璋冨害绾跨▼宸插惎鍔?    if subscription_manager.settings().get("enabled", True):
        wake_subscription_scheduler(force=bool(payload.get("run_now", True)))
    elif subscription_manager.settings().get("personal_sync_enabled", False):
        wake_subscription_scheduler(force=False)
    return json_ok(settings=subscription_manager.settings(), scheduler=subscription_scheduler_status())


@app.post("/api/subscriptions/run")
def api_run_subscriptions_now():
    if not subscription_manager.settings().get("enabled", True):
        return json_error("璁㈤槄鑷姩妫€娴嬫湭鍚敤")
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
        return json_error("涓嶆敮鎸佺殑鎵归噺鎿嶄綔")
    jobs = []
    changed = 0
    if action in {"check", "complete"}:
        for sid in ids:
            if subscription_manager.get(sid):
                jobs.append(start_subscription_job(sid, queue_missing=(action == "complete")))
        append_background_event("subscription", "鎵归噺璁㈤槄鎿嶄綔", f"{action} {len(jobs)} 涓闃?, {"action": action, "count": len(jobs)})
        return json_ok(action=action, jobs=jobs, count=len(jobs), scheduler=subscription_scheduler_status())
    for sid in ids:
        if action == "cancel":
            changed += 1 if subscription_manager.cancel(sid) else 0
        elif action == "enable":
            changed += 1 if subscription_manager.set_status(sid, "active") else 0
    append_background_event("subscription", "鎵归噺璁㈤槄鎿嶄綔", f"{action} {changed} 涓闃?, {"action": action, "count": changed})
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
        return json_error("缂哄皯涓撹緫淇℃伅")
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
        return json_error("璁㈤槄涓嶅瓨鍦?, 404)
    return json_ok(job=start_subscription_job(sid, queue_missing=False))


@app.get("/api/subscriptions/jobs/<job_id>")
def api_subscription_job(job_id):
    with subscription_job_lock:
        cleanup_subscription_jobs()
        job = dict(subscription_jobs.get(job_id) or {})
    if not job:
        return json_error("璁㈤槄浠诲姟涓嶅瓨鍦?, 404)
    return json_ok(job=job)


@app.post("/api/subscriptions/<path:sid>/complete")
def api_subscription_complete(sid):
    if not subscription_manager.get(sid):
        return json_error("璁㈤槄涓嶅瓨鍦?, 404)
    return json_ok(job=start_subscription_job(sid, queue_missing=True))


@app.get("/api/player/url")
def api_player_url():
    """鑾峰彇绔犺妭鐨勬挱鏀?URL"""
    platform = request.args.get("platform", "").strip()
    album_id = request.args.get("album_id", "").strip()
    chapter_id = request.args.get("chapter_id", "").strip()
    if not chapter_id:
        return json_error("缂哄皯 chapter_id 鍙傛暟")
    try:
        url = None
        if platform == "鍠滈┈鎷夐泤":
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
        elif platform == "鎳掍汉鍚功":
            sync_platform_cookie(platform)
            url = search_manager.lrts_manager.get_audio_url(album_id, chapter_id)
        elif platform == "鐣寗鐣呭惉":
            voice_name = request.args.get("voice_name", "").strip() or "鏃犳崯鐪熶汉褰曞埗"
            info = search_manager.fanqie_manager.get_audio_download_info(chapter_id, voice_name, album_id)
            url = info.get("url") if info else None
        elif platform == "浜戝惉FM":
            url = request.args.get("direct_url", "")
        elif platform == "璧风偣鍚功":
            audio_dict = search_manager.search_manager.get_qidian_audio_url(album_id, chapter_id)
            if audio_dict and "default" in audio_dict:
                url = audio_dict["default"].get("url", "")
        elif platform == "铚昏湏FM":
            url = search_manager.qtfm_manager.get_audio_url(album_id, chapter_id)
        elif platform == "閰锋垜鍚功":
            info = search_manager.kuwo_manager.get_download_info(chapter_id, "lossless")
            url = info.get("url") if info else None
        elif platform == "缃戞槗浜戝惉涔?:
            info = search_manager.netease_manager.get_download_info(chapter_id, "exhigh")
            url = info.get("url") if info else None
        if url and str(url).startswith("http"):
            proxy_url = register_audio_proxy_url(str(url), platform)
            if not proxy_url:
                return json_error("闊抽鍦板潃鏃犳硶鐢熸垚瀹夊叏浠ｇ悊閾炬帴")
            return json_ok(url=proxy_url, source_url=str(url))
        else:
            return json_error(f"鏃犳硶鑾峰彇 {platform} 鐨勬挱鏀惧湴鍧€")
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
    "浜戝惉FM": "浜戝惉fm",
    "铚昏湏FM": "铚昏湏fm",
}


def _safe_child_path(root, relative):
    base = Path(root).resolve()
    target = (base / str(relative or "")).resolve()
    if target != base and base not in target.parents:
        raise ValueError("璺緞瓒婄晫")
    return target


def _format_bytes(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "0 B"


def _sanitize_download_folder_name(name):
    text = str(name or "").strip() or "鏈煡涓撹緫"
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        text = text.replace(char, "_")
    return text[:200] or "鏈煡涓撹緫"


def _album_download_folder(album, options=None):
    album = normalize_album(album)
    options = dict(options or {})
    root = Path(resolve_download_dir(options.get("download_dir")))
    title = album.get("title") or ""
    if not title:
        return None
    parts = [root]
    if cookie_manager.get_cookie("organize_by_platform_enabled") == "true":
        parts.append(_sanitize_download_folder_name(album.get("platform") or "鏈煡骞冲彴"))
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
            return json_error(f"娓呯┖鏃ュ織澶辫触锛歿path.name}: {exc}", 500)
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
    """鑾峰彇鍚勫钩鍙板凡淇濆瓨鐨?Cookie 鐘舵€?""
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
    """淇濆瓨骞冲彴 Cookie"""
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "").strip()
    cookie = payload.get("cookie", "").strip()
    if not platform or not cookie:
        return json_error("缂哄皯 platform 鎴?cookie")
    if platform in ("lrts", "鎳掍汉鍚功"):
        cookie = normalize_lrts_credentials(cookie)
        if not cookie:
            return json_error("鎳掍汉鍚功宸叉敼鐢ㄦ墜鏈哄彿楠岃瘉鐮佺櫥褰曪紝璇蜂娇鐢ㄩ獙璇佺爜鏂瑰紡鑾峰彇鍑瘉")
    cookie_manager.set_cookie(platform, cookie)
    search_manager.set_cookie(platform, cookie)
    return json_ok(saved=True, platform=platform, config_file=str(cookie_manager.config_file))


@app.delete("/api/cookies/<platform>")
def api_delete_cookie(platform):
    platform = (platform or "").strip()
    if not platform:
        return json_error("缂哄皯 platform")
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
        return json_error("涓嶆敮鎸佺殑骞冲彴")
    if not cookie:
        return json_error("缂哄皯 Cookie 鎴栧嚟璇?)
    if platform == "lrts":
        cookie = normalize_lrts_credentials(cookie)
        if not cookie:
            return json_error("鎳掍汉鍚功涓汉涓績闇€瑕?App 鍑瘉锛岃浣跨敤楠岃瘉鐮佺櫥褰曟垨绮樿创 token/imei")
    cookie_manager.set_cookie(key, cookie)
    return json_ok(saved=True, platform=platform, key=key, info=_personal_cookie_status(platform), config_file=str(cookie_manager.config_file))


@app.delete("/api/personal/cookies/<platform>")
def api_delete_personal_cookie(platform):
    key = _personal_cookie_key(platform)
    if not key:
        return json_error("涓嶆敮鎸佺殑骞冲彴")
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
        return json_ok(ok=False, logged_in=False, is_vip=False, message="鏈娴嬪埌鎳掍汉鍚功 App 鍑瘉锛岃鍏堢敤鎵嬫満鍙烽獙璇佺爜鐧诲綍")
    try:
        search_manager.set_cookie("lrts", credential)
        probe = search_manager.lrts_manager._client_or_guest().book_search("娴嬭瘯", page_size=1)
        valid = probe.get("status") == 0
    except Exception as exc:
        return json_ok(ok=False, logged_in=False, is_vip=False, message=f"鎳掍汉鍚功鍑瘉鏍￠獙澶辫触锛歿exc}")
    if not valid:
        return json_ok(ok=False, logged_in=False, is_vip=False, message=f"鎳掍汉鍚功鍑瘉鏃犳晥锛歿probe.get('msg') or probe.get('status')}")
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
        message="鎳掍汉鍚功 App 鍑瘉鏈夋晥" + (f"锛孷IP 鍒版湡锛歿vip_expire}" if vip_expire else ""),
    )


@app.post("/api/lrts/send-code")
def api_lrts_send_code():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    if not phone:
        return json_error("璇疯緭鍏ユ墜鏈哄彿")
    try:
        data = lrts_send_sms_code(phone)
    except Exception as exc:
        logging.exception("lrts send sms failed")
        return json_error(f"鍙戦€侀獙璇佺爜澶辫触锛歿exc}", status=500)
    if data.get("status") != 0:
        return json_error(data.get("msg") or f"鍙戦€侀獙璇佺爜澶辫触锛歴tatus={data.get('status')}")
    return json_ok(message="楠岃瘉鐮佸凡鍙戦€?, imei=data.get("_imei", ""), temp_token=data.get("_token", ""))


@app.post("/api/lrts/login")
def api_lrts_login():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    imei = str(payload.get("imei") or "").strip()
    temp_token = str(payload.get("temp_token") or "").strip()
    if not phone or not code:
        return json_error("璇疯緭鍏ユ墜鏈哄彿鍜岄獙璇佺爜")
    try:
        data, credential = lrts_sms_login(phone, code, imei=imei, temp_token=temp_token)
    except Exception as exc:
        logging.exception("lrts sms login failed")
        return json_error(f"楠岃瘉鐮佺櫥褰曞け璐ワ細{exc}", status=500)
    if data.get("status") != 0 or not credential:
        return json_error(data.get("msg") or f"楠岃瘉鐮佺櫥褰曞け璐ワ細status={data.get('status')}")
    cookie_manager.set_cookie("lrts", credential)
    search_manager.set_cookie("lrts", credential)
    return json_ok(message="鎳掍汉鍚功鐧诲綍鎴愬姛", credential_saved=True, userId=data.get("userId"), nickname=data.get("nickname") or data.get("nickName", ""))


@app.post("/api/personal/lrts/login")
def api_personal_lrts_login():
    payload = request.get_json(silent=True) or {}
    phone = str(payload.get("phone") or "").strip()
    code = str(payload.get("code") or "").strip()
    imei = str(payload.get("imei") or "").strip()
    temp_token = str(payload.get("temp_token") or "").strip()
    if not phone or not code:
        return json_error("璇疯緭鍏ユ墜鏈哄彿鍜岄獙璇佺爜")
    try:
        data, credential = lrts_sms_login(phone, code, imei=imei, temp_token=temp_token)
    except Exception as exc:
        logging.exception("personal lrts sms login failed")
        return json_error(f"楠岃瘉鐮佺櫥褰曞け璐ワ細{exc}", status=500)
    if data.get("status") != 0 or not credential:
        return json_error(data.get("msg") or f"楠岃瘉鐮佺櫥褰曞け璐ワ細status={data.get('status')}")
    cookie_manager.set_cookie("personal_lrts", credential)
    return json_ok(
        message="鎳掍汉鍚功涓汉涓績鐧诲綍鎴愬姛",
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
        return json_error("鎳掍汉鍚功宸叉敼鐢ㄦ墜鏈哄彿楠岃瘉鐮佺櫥褰?)
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
        return json_error("浼氳瘽涓嶅瓨鍦ㄦ垨宸茶繃鏈?, 404)
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
        return json_error("浼氳瘽涓嶅瓨鍦ㄦ垨宸茶繃鏈?, 404)
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


# 鈹€鈹€ 鎳掍汉鍚功鍙嶅悜浠ｇ悊鐧诲綍 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 鍘熺悊锛氱敤鎴烽€氳繃 /lrts-proxy/ 璁块棶 m.lrts.me锛屽悗绔唬鐞嗘墍鏈夎姹傚苟鎹曡幏 Cookie銆?# 鐧诲綍鎴愬姛鍚庯紙妫€娴嬪埌 session Cookie锛夛紝鑷姩淇濆瓨骞堕€氱煡鍓嶇銆?
@app.get("/api/cookies/script/<platform>")
def api_cookie_script(platform):
    """杩斿洖璇ュ钩鍙扮殑娴忚鍣ㄦ姄鍙栬剼鏈笌璇存槑銆?""
    scripts = {
        "xmly": {
            "name": "鍠滈┈鎷夐泤",
            "login_url": "https://www.ximalaya.com/",
            "script": (
                "/* 鍠滈┈鎷夐泤 Cookie 鎶撳彇鑴氭湰 */\n"
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "qidian": {
            "name": "璧风偣鍚功",
            "login_url": "https://www.qidian.com/",
            "script": (
                "/* 璧风偣 Cookie 鎶撳彇鑴氭湰 */\n"
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "qtfm": {
            "name": "铚昏湏FM",
            "login_url": "https://www.qtfm.cn/",
            "script": (
                "/* 铚昏湏FM Cookie 鎶撳彇鑴氭湰 */\n"
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "fanqie": {
            "name": "鐣寗鐣呭惉",
            "login_url": "https://fanqienovel.com/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "yuntu": {
            "name": "浜戝惉FM",
            "login_url": "https://www.radio.cn/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "kuwo": {
            "name": "閰锋垜鍚功",
            "login_url": "https://www.kuwo.cn/",
            "script": (
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
        "netease": {
            "name": "缃戞槗浜戝惉涔?,
            "login_url": "https://music.163.com/",
            "script": (
                "/* 缃戞槗浜戦煶涔?Cookie 鎶撳彇鑴氭湰 */\n"
                "(function(){var c=document.cookie;"
                "prompt('璇峰鍒朵笅闈㈣繖娈?Cookie 鍚庡洖鍒癆udioFlow绮樿创锛?, c);}())"
            ),
        },
    }
    info = scripts.get(platform)
    if not info:
        return json_error("涓嶆敮鎸佺殑骞冲彴")
    return json_ok(**info)


# 鈹€鈹€ 涓汉涓績 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
@app.get("/api/personal/<platform>/<feature>")
def api_personal(platform, feature):
    """鑾峰彇涓汉涓績鏁版嵁锛堝鐢ㄦ闈㈢増 UserDataWorker 閫昏緫锛?""
    try:
        if platform == "ximalaya":
            items = _load_ximalaya_personal(feature, all_pages=(feature == "subscriptions"))
        elif platform == "lrts":
            items = _load_lrts_personal(feature)
        elif platform == "qidian":
            items = _load_qidian_personal(feature)
        else:
            return json_error(f"涓嶆敮鎸佺殑骞冲彴: {platform}")
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
        raise RuntimeError("璇峰厛鍦ㄤ釜浜轰腑蹇冧负鍠滈┈鎷夐泤鐧诲綍鎴栫矘璐?Cookie")
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
        }, "鍠滈┈鎷夐泤"))
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
        }, "鍠滈┈鎷夐泤"))
    # tracksList
    for track in content.get("tracksList", []) or []:
        items.append(_normalize_personal_item({
            "id": str(track.get("albumId", "")),
            "title": track.get("albumName") or track.get("trackTitle", ""),
            "author": _pick_ximalaya_author(track),
            "cover": track.get("trackCoverPath", ""),
        }, "鍠滈┈鎷夐泤"))
    # history groups
    for group in ("today", "yesterday", "earlier"):
        for record in content.get(group, []) or []:
            items.append(_normalize_personal_item({
                "id": str(record.get("itemId", "")),
                "title": record.get("itemTitle") or record.get("albumTitle") or record.get("childTitle", ""),
                "author": _pick_ximalaya_author(record),
            "cover": record.get("itemCoverUrl") or record.get("itemSquareCoverUrl", ""),
        }, "鍠滈┈鎷夐泤"))
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
        raise RuntimeError("璇峰厛鍦ㄤ釜浜轰腑蹇冧负鎳掍汉鍚功鐧诲綍鎴栫矘璐村嚟璇?)
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
                }, "鎳掍汉鍚功"))
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
                }, "鎳掍汉鍚功"))
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
                }, "鎳掍汉鍚功"))
    except Exception as e:
        print(f"鉂?鎳掍汉鍚功涓汉鏁版嵁鍔犺浇澶辫触({feature}): {e}")
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
    }, "鎳掍汉鍚功")


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
        raise RuntimeError("璇峰厛鍦ㄤ釜浜轰腑蹇冧负璧风偣鍚功鐧诲綍鎴栫矘璐?Cookie")
    from core.search_manager import SearchManager
    api = SearchManager()
    api.set_qidian_cookie(cookie)
    items = []
    try:
        if feature == "favorites":
            account = api.get_qidian_user_account()
            if not account:
                raise RuntimeError("璧风偣璐﹀彿鏍￠獙澶辫触锛岃鍦ㄤ釜浜轰腑蹇冮噸鏂版壂鐮佹垨绮樿创 Cookie")
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
                raise RuntimeError(data.get("msg") or f"璧风偣涔︽灦鑾峰彇澶辫触锛歝ode={data.get('code')}")
            for book in ((data.get("data") or {}).get("booksInfo") or []):
                items.append(_normalize_personal_item({
                    "id": book.get("bookId"),
                    "title": book.get("bookName"),
                    "author": book.get("authorName"),
                    "cover": book.get("coverUrl"),
                    "last_chapter": book.get("lastChapterName"),
                    "update_time": book.get("updateTime"),
                    "raw_data": book,
                }, "璧风偣鍚功"))
    except Exception as e:
        print(f"鉂?璧风偣鍚功涓汉鏁版嵁鍔犺浇澶辫触({feature}): {e}")
        if isinstance(e, RuntimeError):
            raise
    return items


def _normalize_personal_item(item, platform):
    """灏嗗悇骞冲彴涓汉涓績鏉＄洰缁熶竴涓哄墠绔彲鐢ㄦ牸寮忋€?""
    d = dict(item or {})
    return normalize_album({
        "id": d.get("id") or d.get("album_id") or d.get("book_id") or "",
        "title": d.get("title") or d.get("name") or d.get("album_title") or "鏈煡涓撹緫",
        "author": d.get("author") or d.get("anchor") or d.get("announcer") or "",
        "cover": d.get("cover") or d.get("cover_url") or d.get("coverUrl") or "",
        "episodes": d.get("episodes") or d.get("track_count") or d.get("sections") or 0,
        "platform": platform,
        "status": d.get("status") or "",
        "description": d.get("description") or d.get("intro") or "",
    })


# 鈹€鈹€ 鍓嶇闈欐€佹枃浠舵湇鍔?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    """鏈嶅姟鍓嶇 SPA 鍙婇潤鎬佽祫婧愩€?""
    # 1. 浼樺厛浠?dist 鐩綍鎻愪緵宸叉瀯寤烘枃浠?    if path and FRONTEND_DIST_DIR.exists():
        target = FRONTEND_DIST_DIR / path
        if target.is_file():
            return send_from_directory(str(FRONTEND_DIST_DIR), path)
    # 2. public 鐩綍锛坰ervice-worker銆乵anifest 绛夛級
    if path:
        pub = FRONTEND_PUBLIC_DIR / path
        if pub.is_file():
            return send_from_directory(str(FRONTEND_PUBLIC_DIR), path)
    # 3. SPA fallback锛氳繑鍥?index.html
    index = FRONTEND_DIST_DIR / "index.html"
    if index.exists():
        return send_from_directory(str(FRONTEND_DIST_DIR), "index.html")
    # 4. dist 鏈瀯寤烘椂缁欏嚭鎻愮ず
    return (
        "<h2>鍓嶇鏈瀯寤?/h2><p>璇峰湪瀹瑰櫒鍐呮墽琛?<code>cd frontend && npm run build</code>锛?
        "鎴栦娇鐢?Docker 闀滃儚锛圖ockerfile 浼氳嚜鍔ㄦ瀯寤猴級銆?/p>",
        503,
    )


def main():
    """鍚姩 Web 鏈嶅姟鍣ㄥ叆鍙ｃ€?""
    import os
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8082))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        ensure_subscription_scheduler()
    print(f"馃殌 鍚姩鏈嶅姟鍣? http://{host}:{port}  debug={debug}")
    try:
        from waitress import serve
        print("馃摗 浣跨敤 waitress 鐢熶骇鏈嶅姟鍣?)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print("馃摗 waitress 鏈畨瑁咃紝浣跨敤 Flask 鍐呯疆鏈嶅姟鍣?)
        app.run(host=host, port=port, debug=debug, threaded=True)



