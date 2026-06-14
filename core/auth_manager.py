#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path


class AuthManager:
    def __init__(self, config_dir):
        self.config_dir = Path(config_dir)
        self.auth_file = self.config_dir / "auth.json"
        self.sessions = {}
        self.session_ttl = 7 * 24 * 3600
        self.data = {"version": 1, "users": {}}
        self.failures = {}
        self.max_failures = int(os.getenv("AUDIOFLOW_LOGIN_MAX_FAILURES", "5") or 5)
        self.lock_seconds = int(os.getenv("AUDIOFLOW_LOGIN_LOCK_SECONDS", "300") or 300)
        self.load()
        self.ensure_default_user()

    def load(self):
        try:
            if self.auth_file.exists():
                data = json.loads(self.auth_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.data.update(data)
        except Exception:
            self.data = {"version": 1, "users": {}}

    def save(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.auth_file)

    def ensure_default_user(self):
        users = self.data.setdefault("users", {})
        if users:
            return
        username = os.getenv("AUDIOFLOW_DEFAULT_USERNAME", "admin").strip() or "admin"
        password = os.getenv("AUDIOFLOW_DEFAULT_PASSWORD", "admin")
        users[username] = self._password_record(password, must_change=True)
        self.save()

    def _password_record(self, password, must_change=False):
        salt = secrets.token_hex(16)
        rounds = 260000
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt.encode("utf-8"), rounds).hex()
        now = int(time.time())
        return {
            "salt": salt,
            "hash": digest,
            "rounds": rounds,
            "must_change_password": bool(must_change),
            "updated_at": now,
            "created_at": now,
        }

    def verify_password(self, username, password):
        user = self.data.get("users", {}).get(str(username or ""))
        if not user:
            return False
        salt = str(user.get("salt") or "")
        rounds = int(user.get("rounds") or 260000)
        expected = str(user.get("hash") or "")
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt.encode("utf-8"), rounds).hex()
        return hmac.compare_digest(digest, expected)

    def login(self, username, password):
        username = str(username or "").strip()
        if self.is_locked(username):
            return None
        if not username or not self.verify_password(username, password):
            self.record_failure(username)
            return None
        self.clear_failures(username)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"username": username, "created_at": time.time(), "last_seen": time.time()}
        return token

    def record_failure(self, username):
        key = str(username or "").strip() or "__empty__"
        now = time.time()
        item = self.failures.get(key) or {"count": 0, "last_at": 0, "locked_until": 0}
        if now - float(item.get("last_at") or 0) > self.lock_seconds:
            item = {"count": 0, "last_at": 0, "locked_until": 0}
        item["count"] = int(item.get("count") or 0) + 1
        item["last_at"] = now
        if item["count"] >= self.max_failures:
            item["locked_until"] = now + self.lock_seconds
        self.failures[key] = item

    def clear_failures(self, username):
        self.failures.pop(str(username or "").strip() or "__empty__", None)

    def is_locked(self, username):
        key = str(username or "").strip() or "__empty__"
        item = self.failures.get(key) or {}
        locked_until = float(item.get("locked_until") or 0)
        if locked_until <= time.time():
            if locked_until:
                self.failures.pop(key, None)
            return False
        return True

    def lock_remaining(self, username):
        item = self.failures.get(str(username or "").strip() or "__empty__") or {}
        return max(0, int(float(item.get("locked_until") or 0) - time.time()))

    def logout(self, token):
        if token:
            self.sessions.pop(token, None)

    def user_for_session(self, token):
        if not token:
            return None
        item = self.sessions.get(token)
        if not item:
            return None
        if time.time() - float(item.get("created_at") or 0) > self.session_ttl:
            self.sessions.pop(token, None)
            return None
        item["last_seen"] = time.time()
        username = item.get("username")
        user = self.data.get("users", {}).get(username)
        if not user:
            return None
        return {
            "username": username,
            "must_change_password": bool(user.get("must_change_password")),
        }

    def change_password(self, username, old_password, new_password):
        username = str(username or "").strip()
        new_password = str(new_password or "")
        if len(new_password) < 6:
            raise ValueError("新密码至少需要 6 位")
        if not self.verify_password(username, old_password):
            raise ValueError("当前密码不正确")
        self.data.setdefault("users", {})[username] = self._password_record(new_password, must_change=False)
        self.save()
        for token, item in list(self.sessions.items()):
            if item.get("username") == username:
                self.sessions.pop(token, None)
