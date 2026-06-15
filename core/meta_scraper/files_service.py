from __future__ import annotations

from pathlib import Path

from .app_config import META_DATA_DIR


AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
COVER_NAMES = {"cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png"}


def safe_path(path_text: str | None = None) -> Path:
    root = META_DATA_DIR.resolve()
    candidate = Path(path_text or root).resolve()
    if not str(candidate).startswith(str(root)):
        return root
    return candidate


def browse(path_text: str | None = None) -> dict:
    root = META_DATA_DIR.resolve()
    current = safe_path(path_text)
    if not current.exists() or not current.is_dir():
        current = root
    dirs = []
    if current != root:
        dirs.append({"name": "..", "path": str(current.parent), "has_audio": False})
    try:
        for child in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not child.is_dir():
                continue
            try:
                has_audio = any(file.suffix.lower() in AUDIO_EXTS for file in child.iterdir() if file.is_file())
            except PermissionError:
                has_audio = False
            dirs.append({"name": child.name, "path": str(child), "has_audio": has_audio})
    except PermissionError:
        pass
    return {"root": str(root), "current": str(current), "items": dirs}


def find_cover(folder: str, manual_cover_path: str = "") -> str:
    if manual_cover_path and Path(manual_cover_path).exists():
        return manual_cover_path
    folder_path = Path(folder)
    for name in COVER_NAMES:
        candidate = folder_path / name
        if candidate.exists():
            return str(candidate)
    return ""
