#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"
REQUIREMENTS = ROOT / "requirements.txt"


def read_version():
    value = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"0\.\d{2}", value):
        raise SystemExit(f"Invalid version format in VERSION: {value!r}, expected 0.01")
    return value


def next_version(value):
    number = int(value.split(".", 1)[1])
    return f"0.{number + 1:02d}"


def sync_version(value):
    VERSION_FILE.write_text(value + "\n", encoding="utf-8")

    package = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    package["version"] = value
    FRONTEND_PACKAGE.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    text = REQUIREMENTS.read_text(encoding="utf-8")
    text = re.sub(r"^# AudioFlow v[0-9.]+ 依赖", f"# AudioFlow v{value} 依赖", text, count=1, flags=re.M)
    REQUIREMENTS.write_text(text, encoding="utf-8")


def main():
    current = read_version()
    new_version = next_version(current)
    sync_version(new_version)
    print(new_version)


if __name__ == "__main__":
    main()
