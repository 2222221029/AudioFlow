from __future__ import annotations

from pathlib import Path
from core.platform_config import config_dir, data_dir

META_CONFIG_DIR = config_dir() / "meta"
META_DATA_DIR = data_dir()
PROCESS_CONFIG_PATH = META_CONFIG_DIR / "process_params.json"
QUEUE_PATH = META_CONFIG_DIR / "queue.json"
JOURNAL_DIR = META_CONFIG_DIR / "journals"

DEFAULT_PARAMS = {
    "input_folder": "",
    "api_source": "喜马拉雅",
    "api_id": "",
    "link_platform": "起点听书",
    "link_url": "",
    "title": "",
    "subtitle": "",
    "author": "",
    "anchor": "",
    "category": "401",
    "platform": "喜马拉雅",
    "year": "2024",
    "target_format": "原格式保留",
    "bitrate": "自动检测",
    "finished": "完结",
    "check_codec": True,
    "rename_ext": True,
    "debug": True,
    "manual_cover_path": "",
    "manual_desc": "",
    "series_name": "",
    "series_number": "",
    "album_tags": [],
    "team": "RL",
    "fetched_metadata": {},
}


def ensure_runtime_dirs() -> None:
    META_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
