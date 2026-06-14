#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless Ximalaya QR login worker for the Docker/Web server."""

from __future__ import annotations

import base64
import hashlib
import os
import random
import secrets
import string
import time
import uuid

import requests

from core.qt_compat import QThread, pyqtSignal
from core.time_api import get_timestamp, get_timestamp_ms_str


class XimalayaQRLoginWorker(QThread):
    qr_generated = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)
    login_success = pyqtSignal(dict)
    login_failed = pyqtSignal(str)

    API_CONFIGS = {
        "qrcode_gen": [
            "https://passport.ximalaya.com/web/qrCode/gen",
            "https://passport.ximalaya.com/passport/qrcode/gen",
        ],
        "qrcode_check": [
            "https://passport.ximalaya.com/web/qrCode/check",
            "https://passport.ximalaya.com/passport/qrcode/check",
        ],
    }

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.qr_id = None
        self.last_request_time = 0
        self.is_running = False
        self.device_id = self.generate_device_id()
        self.session_id = str(uuid.uuid4())
        self.browser_id = self.generate_browser_id()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.ximalaya.com/",
            "Origin": "https://www.ximalaya.com",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        })

    def generate_device_id(self):
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=32))

    def generate_browser_id(self):
        return hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()

    def generate_wfp(self):
        fingerprint = f"{self.browser_id}_{get_timestamp()}_web_www"
        encoded = base64.b64encode(fingerprint.encode()).decode()
        return f"ACN{encoded[:40]}"

    def generate_xmlog(self):
        return f"h5&{self.session_id}&process.env.sdkVersion"

    def generate_crystal(self):
        return base64.b64encode(b"Salted__" + secrets.token_bytes(120)).decode()[:200]

    def generate_assva(self, version="5"):
        return base64.b64encode(b"Salted__" + secrets.token_bytes(50)).decode()[:88]

    def generate_vmce(self):
        return base64.b64encode(b"Salted__" + secrets.token_bytes(100)).decode()[:180]

    def set_initial_cookies(self):
        current_time = get_timestamp_ms_str()
        site_id = "4a7d8ec50cfd6af753c4f8aee3425070"
        for name, value in {
            "wfp": self.generate_wfp(),
            "_xmLog": self.generate_xmlog(),
            "xm-page-viewid": "ximalaya-web",
            "DATE": current_time,
            "impl": "www.ximalaya.com.login",
            f"Hm_lvt_{site_id}": current_time,
            f"Hm_lpvt_{site_id}": current_time,
            "HMACCOUNT": "F6378486E01D6DF7",
        }.items():
            self.session.cookies.set(name, value, domain=".ximalaya.com", path="/")

    def rate_limit_wait(self, min_interval=3):
        elapsed = time.time() - self.last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self.last_request_time = time.time()

    def generate_qrcode(self):
        self.status_changed.emit("正在生成二维码...")
        self.set_initial_cookies()
        self.rate_limit_wait(min_interval=3)
        try:
            response = self.session.get(
                self.API_CONFIGS["qrcode_gen"][0],
                params={"level": "L", "source": "喜马拉雅网页端"},
                timeout=10,
            )
            data = response.json()
            if data.get("ret") == 0 and data.get("img"):
                qr_id = data.get("qrId")
                qr_filename = "ximalaya_qrcode.png"
                with open(qr_filename, "wb") as f:
                    f.write(base64.b64decode(data["img"]))
                self.qr_id = qr_id
                self.qr_generated.emit(qr_id, os.path.abspath(qr_filename))
                return True
            self.login_failed.emit(f"二维码生成失败: {data.get('msg')}")
        except Exception as exc:
            self.login_failed.emit(f"二维码生成失败: {exc}")
        return False

    def check_qrcode_status(self, qr_id):
        timestamp = get_timestamp_ms_str()
        self.session.cookies.set("web_login", str(timestamp), domain=".ximalaya.com", path="/")
        url = f"{self.API_CONFIGS['qrcode_check'][0]}/{qr_id}/{timestamp}"
        try:
            response = self.session.get(url, timeout=10)
            data = response.json()
            return data.get("ret"), data.get("msg", ""), response
        except Exception as exc:
            return None, str(exc), None

    def wait_for_scan(self):
        if not self.qr_id:
            return False
        self.status_changed.emit("等待扫码中...")
        max_retry = 120
        retry_count = 0
        while retry_count < max_retry and self.is_running:
            self.msleep(1000)
            retry_count += 1
            if not self.is_running:
                return False
            ret, msg, _response = self.check_qrcode_status(self.qr_id)
            if ret == 0:
                self.status_changed.emit("扫码成功，正在获取Cookie...")
                self.complete_cookies()
                cookies = self.get_cookies()
                if cookies:
                    self.login_success.emit(cookies)
                    return True
                self.login_failed.emit("获取Cookie失败")
                return False
            if ret == 32000:
                progress = retry_count / max_retry * 100
                self.status_changed.emit(f"等待扫码中... ({int(progress)}%)")
            elif ret == 32001:
                self.login_failed.emit("二维码已过期")
                return False
            elif ret is not None:
                self.login_failed.emit(f"扫码失败: {msg}")
                return False
        self.login_failed.emit("等待超时")
        return False

    def complete_cookies(self):
        for url in ("https://www.ximalaya.com/", "https://www.ximalaya.com/revision/time", "https://www.ximalaya.com/user/"):
            try:
                self.session.get(url, timeout=10)
            except Exception:
                pass
        for name, value in {
            "crystal": self.generate_crystal(),
            "assva5": self.generate_assva("5"),
            "assva6": self.generate_assva("6"),
            "vmce9xdq": self.generate_vmce(),
            "cmci9xde": self.generate_assva("c"),
            "pmck9xge": self.generate_assva("p")[:44],
        }.items():
            if not self.session.cookies.get(name):
                self.session.cookies.set(name, value, domain=".ximalaya.com", path="/")

    def get_cookies(self):
        return {cookie.name: cookie.value for cookie in self.session.cookies}

    def run(self):
        self.is_running = True
        if self.generate_qrcode():
            self.wait_for_scan()

    def stop(self):
        self.is_running = False

