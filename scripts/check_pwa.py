#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path):
    full = ROOT / path
    if not full.exists():
        raise SystemExit(f"missing: {path}")
    return full


def require_public(src):
    rel = str(src or "").lstrip("/")
    for base in ("frontend/public", "frontend/dist", ""):
        full = ROOT / base / rel if base else ROOT / rel
        if full.exists():
            return full
    raise SystemExit(f"missing: {rel}")


def main():
    manifest_path = require("frontend/public/manifest.webmanifest")
    require("frontend/public/service-worker.js")
    require("frontend/public/runtime-env.js")
    require("frontend/index.html")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("name", "short_name", "icons", "start_url", "display", "background_color", "theme_color"):
        if key not in manifest:
            raise SystemExit(f"manifest missing key: {key}")
    if manifest.get("display") != "standalone":
        raise SystemExit("manifest display must be standalone")
    for icon in manifest.get("icons", []):
        src = icon.get("src", "").lstrip("/")
        if src:
            require_public(src)
    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        for path in ("index.html", "manifest.webmanifest", "service-worker.js", "runtime-env.js"):
            require(f"frontend/dist/{path}")
    print("PWA check passed")


if __name__ == "__main__":
    main()
