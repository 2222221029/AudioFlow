#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 exe 与开发环境下的资源根目录。"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def ffmpeg_bin_dir() -> Path:
    return app_root() / "ffmpeg-8.0-essentials_build" / "bin"


def ffmpeg_exe_path() -> Path:
    return ffmpeg_bin_dir() / "ffmpeg.exe"
