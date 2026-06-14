#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from pathlib import Path


APP_NAME = "AudioFlow"


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def app_mode():
    return os.getenv("APP_MODE", "desktop").strip().lower() or "desktop"


def project_root():
    return Path(__file__).resolve().parents[1]


def app_version():
    version_file = project_root() / "VERSION"
    try:
        value = version_file.read_text(encoding="utf-8").strip()
        return value or "0.01"
    except Exception:
        return "0.01"


APP_VERSION = app_version()


def default_user_config_dir():
    return Path.home() / ".audioflow"


def config_dir():
    return Path(os.getenv("CONFIG_DIR") or os.getenv("DATA_DIR") or default_user_config_dir())


def data_dir():
    return Path(os.getenv("DATA_DIR") or config_dir())


def download_dir():
    default = Path("/app/downloads")
    return Path(os.getenv("DOWNLOAD_DIR") or default)


def log_dir():
    return Path(os.getenv("LOG_DIR") or data_dir() / "logs")


def host():
    return os.getenv("HOST", "127.0.0.1")


def port():
    try:
        return int(os.getenv("PORT", "8082"))
    except Exception:
        return 8082


def public_base_url():
    return os.getenv("PUBLIC_BASE_URL", "")


def pwa_enabled():
    return env_bool("PWA_ENABLED", True)


def audio_proxy_raw_url_enabled():
    return env_bool("AUDIOFLOW_ALLOW_RAW_AUDIO_PROXY", False)


def ensure_runtime_dirs():
    for path in (config_dir(), data_dir(), download_dir(), log_dir()):
        path.mkdir(parents=True, exist_ok=True)
