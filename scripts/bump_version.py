#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本号递增脚本，支持 1.0.XX 与 0.XX 两种格式。

所有版本号文件的唯一来源是 VERSION。前端 package.json / package-lock.json /
requirements.txt 若不匹配 VERSION，会被一并同步为 VERSION 同格式值。
"""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"
FRONTEND_PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
REQUIREMENTS = ROOT / "requirements.txt"

# 兼容主要格式：
#   1.0.25      → 主版本 1，小版本 0，补丁 25
#   0.22        → 视为 0.22.0
_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?$")


def parse_version(value):
    m = _VERSION_RE.match(str(value or "").strip())
    if not m:
        return None
    return {
        "major": int(m.group("major")),
        "minor": int(m.group("minor")),
        "patch": int(m.group("patch") or 0),
    }


def format_version(parts):
    if parts["major"] == 0:
        # 兼容旧的 0.XX 格式：0.22 → 0.23
        return f"{parts['major']}.{parts['minor'] + 1:02d}"
    return f"{parts['major']}.{parts['minor']}.{parts['patch'] + 1}"


def next_version(value):
    parts = parse_version(value)
    if not parts:
        raise SystemExit(f"Invalid version format in VERSION: {value!r}")
    if parts["major"] == 0:
        # 0.XX 格式：递增 minor
        return f"0.{parts['minor'] + 1:02d}"
    return f"{parts['major']}.{parts['minor']}.{parts['patch'] + 1}"


def sync_version(new_value):
    VERSION_FILE.write_text(new_value + "\n", encoding="utf-8")

    # frontend/package.json 全量同步（含 name 字段，保持 JSON 结构不变）
    package = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    package["version"] = new_value
    FRONTEND_PACKAGE.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if FRONTEND_PACKAGE_LOCK.exists():
        package_lock = json.loads(FRONTEND_PACKAGE_LOCK.read_text(encoding="utf-8"))
        package_lock["version"] = new_value
        root_package = (package_lock.get("packages") or {}).get("")
        if isinstance(root_package, dict):
            root_package["version"] = new_value
        FRONTEND_PACKAGE_LOCK.write_text(json.dumps(package_lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # requirements.txt 顶部注释
    text = REQUIREMENTS.read_text(encoding="utf-8")
    text = re.sub(r"^# AudioFlow v[0-9.]+ 依赖", f"# AudioFlow v{new_value} 依赖", text, count=1, flags=re.M)
    REQUIREMENTS.write_text(text, encoding="utf-8")


def main():
    current = read_version()
    new_version = next_version(current)
    sync_version(new_version)
    print(new_version)


def read_version():
    return VERSION_FILE.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    main()
