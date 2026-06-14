#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import importlib
import os
import sys
from pathlib import Path
import tempfile


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    root = Path(tempfile.mkdtemp(prefix="audioflow-smoke-"))
    os.environ["CONFIG_DIR"] = str(root / "config")
    os.environ["DATA_DIR"] = str(root / "data")
    os.environ["DOWNLOAD_DIR"] = str(root / "downloads")
    os.environ["LOG_DIR"] = str(root / "logs")
    os.environ["AUDIOFLOW_DEFAULT_USERNAME"] = "admin"
    os.environ["AUDIOFLOW_DEFAULT_PASSWORD"] = "admin"
    server = importlib.import_module("src.server.web_server")
    client = server.app.test_client()

    resp = client.get("/api/config")
    assert resp.status_code == 401, resp.get_data(as_text=True)

    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    for path in ("/api/config", "/api/diagnostics", "/api/downloads", "/api/logs?limit=5"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.get_data(as_text=True)}"
        data = resp.get_json()
        assert data and data.get("ok") is True, f"{path}: {data}"

    resp = client.post("/api/downloads/cleanup", json={"statuses": ["completed", "failed"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("ok") is True

    print(f"smoke api ok ({root})")


if __name__ == "__main__":
    main()
