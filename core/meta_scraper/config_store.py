from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .app_config import DEFAULT_PARAMS, PROCESS_CONFIG_PATH, ensure_runtime_dirs
from .schemas import ProcessParams


def normalize_params(data: dict[str, Any] | None = None) -> ProcessParams:
    merged = dict(DEFAULT_PARAMS)
    merged.update(data or {})
    if merged.get("bitrate") == "auto":
        merged["bitrate"] = "自动检测"
    merged["check_codec"] = True
    merged["rename_ext"] = True
    merged["debug"] = True
    return ProcessParams.model_validate(merged)


def load_config() -> ProcessParams:
    ensure_runtime_dirs()
    if not PROCESS_CONFIG_PATH.exists():
        return save_config(normalize_params({}))
    with PROCESS_CONFIG_PATH.open("r", encoding="utf-8-sig") as f:
        return normalize_params(json.load(f))


def save_config(params: ProcessParams | dict[str, Any]) -> ProcessParams:
    ensure_runtime_dirs()
    normalized = params if isinstance(params, ProcessParams) else normalize_params(params)
    PROCESS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(normalized.model_dump(), f, ensure_ascii=False, indent=2, default=str)
    return normalized


def read_folder_config(folder: str) -> tuple[bool, ProcessParams, str]:
    folder_path = Path(folder)
    config_path = folder_path / "process_params.json"
    if not config_path.exists():
        return False, normalize_params({"input_folder": str(folder_path)}), "未找到 process_params.json"
    with config_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    data["input_folder"] = str(folder_path)
    desc_path = folder_path / "desc.txt"
    if desc_path.exists():
        data["manual_desc"] = desc_path.read_text(encoding="utf-8", errors="ignore")
    elif data.get("clean_desc") and not data.get("manual_desc"):
        data["manual_desc"] = data.get("clean_desc")
    return True, normalize_params(data), "已加载目录配置"
