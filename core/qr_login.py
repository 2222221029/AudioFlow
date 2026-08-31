#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫码登录会话管理（无 Qt 依赖）。"""

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote



SUPPORTED_PLATFORMS = ("ximalaya", "qidian", "qtfm", "netease")
_NETEASE_QR_TYPE = 3
_NETEASE_EAPI_BASE_URL = "https://interface.music.163.com"
_NETEASE_EAPI_KEY = b"e82ckenh8dichen8"
_NETEASE_EAPI_MARKER = "-36cd479b6b5-"
_NETEASE_DEVICE_UA = "NeteaseMusic 9.0.90/5038 (iPhone; iOS 16.2; zh_CN)"
_NETEASE_QUOTE_SAFE = "~()*!.'-_"


def _netease_now_ms() -> int:
    return int(time.time() * 1000)


def _netease_device_id() -> str:
    return secrets.token_hex(26).upper()


def _netease_request_header(device_id: str) -> Dict[str, str]:
    now_ms = _netease_now_ms()
    return {
        "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
        "deviceId": device_id,
        "os": "pc",
        "appver": "3.1.17.204416",
        "versioncode": "140",
        "mobilename": "",
        "buildver": str(now_ms // 1000),
        "resolution": "1920x1080",
        "__csrf": "",
        "channel": "netease",
        "requestId": f"{now_ms}_{secrets.randbelow(1000):04d}",
    }


def _netease_eapi_request(path: str, payload: Dict, device_id: str):
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    header = _netease_request_header(device_id)
    body = dict(payload)
    body["e_r"] = False
    body["header"] = header
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.md5(f"nobody{path}use{text}md5forencrypt".encode("utf-8")).hexdigest()
    plaintext = f"{path}{_NETEASE_EAPI_MARKER}{text}{_NETEASE_EAPI_MARKER}{digest}"
    encrypted = AES.new(_NETEASE_EAPI_KEY, AES.MODE_ECB).encrypt(
        pad(plaintext.encode("utf-8"), AES.block_size)
    )
    cookie = "; ".join(
        f"{quote(str(name), safe=_NETEASE_QUOTE_SAFE)}="
        f"{quote(str(value), safe=_NETEASE_QUOTE_SAFE)}"
        for name, value in header.items()
    )
    request_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "User-Agent": _NETEASE_DEVICE_UA,
    }
    endpoint = f"{_NETEASE_EAPI_BASE_URL}/eapi/{path.removeprefix('/api/')}"
    return endpoint, {"params": encrypted.hex().upper()}, request_headers


def _netease_post(http, path: str, payload: Dict, device_id: str, timeout: int = 15):
    endpoint, form_data, headers = _netease_eapi_request(path, payload, device_id)
    response = http.post(endpoint, data=form_data, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _netease_cookie_value(cookie_jar, name: str) -> str:
    if cookie_jar is None:
        return ""
    try:
        for cookie in cookie_jar:
            if getattr(cookie, "name", "") == name and getattr(cookie, "value", ""):
                return str(cookie.value)
    except TypeError:
        pass
    try:
        return str(cookie_jar.get(name) or "")
    except (AttributeError, KeyError):
        return ""


class QRSession:
    __slots__ = (
        "id", "platform", "status", "qr_image", "message",
        "cookies", "extra", "created_at", "updated_at", "_stop_flag",
    )

    def __init__(self, platform: str):
        self.id = uuid.uuid4().hex[:16]
        self.platform = platform
        self.status = "preparing"
        self.qr_image = ""
        self.message = "正在生成二维码…"
        self.cookies: Optional[Dict[str, str]] = None
        self.extra: Dict[str, str] = {}
        self.created_at = time.time()
        self.updated_at = time.time()
        self._stop_flag = threading.Event()

    def update(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)
        self.updated_at = time.time()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "status": self.status,
            "qr_image": self.qr_image,
            "message": self.message,
            "cookies": self.cookies,
            "extra": dict(self.extra),
        }

    def stop(self):
        self._stop_flag.set()

    @property
    def stopped(self) -> bool:
        return self._stop_flag.is_set()


def _file_to_data_url(path) -> str:
    if isinstance(path, str) and path.startswith("data:image/"):
        return path
    p = Path(path)
    if not p.exists():
        return ""
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _bytes_to_data_url(data: bytes, content_type: str = "image/png") -> str:
    if not data:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type or 'image/png'};base64,{b64}"


def _base64_image_to_data_url(value, content_type: str = "image/png") -> str:
    """Normalize a base64 image response without touching the filesystem."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("二维码图片为空")
    if text.startswith("data:image/"):
        return text
    base64.b64decode(text, validate=True)
    return f"data:{content_type or 'image/png'};base64,{text}"


# ── 平台驱动 ────────────────────────────────────────────────────────

def _drive_ximalaya(session: QRSession) -> None:
    from core.ximalaya_qr_login import XimalayaQRLoginWorker
    worker = XimalayaQRLoginWorker()
    worker.is_running = True

    def emit_qr(qr_id, qr_path):
        session.update(
            qr_image=_file_to_data_url(qr_path),
            status="waiting",
            message="请用喜马拉雅 APP 扫描二维码",
            extra={"qr_id": str(qr_id)},
        )

    def emit_status(msg):
        if session.status in ("success", "failed", "expired", "cancelled"):
            return
        session.update(message=str(msg))

    def emit_ok(cookies):
        session.update(status="success", message="登录成功", cookies=dict(cookies or {}))

    def emit_fail(msg):
        if session.stopped:
            return
        text = str(msg or "")
        if "过期" in text or "expired" in text.lower() or "timeout" in text.lower():
            session.update(status="expired", message=text or "二维码已过期")
        else:
            session.update(status="failed", message=text or "登录失败")

    class _Signal:
        def __init__(self, fn): self.fn = fn
        def emit(self, *args): self.fn(*args)
        def connect(self, *_): pass

    worker.qr_generated = _Signal(emit_qr)
    worker.status_changed = _Signal(emit_status)
    worker.login_success = _Signal(emit_ok)
    worker.login_failed = _Signal(emit_fail)

    def _monitor_cancel():
        while not session.stopped and session.status not in ("success", "failed", "expired"):
            time.sleep(0.5)
        worker.is_running = False
    threading.Thread(target=_monitor_cancel, daemon=True).start()

    try:
        worker.run()
    except Exception as exc:
        emit_fail(f"登录异常：{exc}")


def _drive_qidian(session: QRSession) -> None:
    from src.features.qidian.audio_system import QrcodeLogin
    login = QrcodeLogin()
    uuid_val = login.get_qrcode()
    if not uuid_val:
        detail = str(getattr(login, "last_error", "") or "").strip()
        session.update(status="failed", message=f"获取二维码失败：{detail}" if detail else "获取二维码失败")
        return

    qr_image = str(getattr(login, "qr_image", "") or "").strip()
    if not qr_image:
        session.update(status="failed", message="二维码响应中没有图片")
        return

    session.update(
        qr_image=_base64_image_to_data_url(qr_image),
        status="waiting",
        message="请用起点读书 APP 扫描二维码（30 秒有效）",
        extra={"uuid": str(uuid_val)},
    )

    result_box: Dict[str, object] = {}

    def _poll():
        try:
            cookies = login.get_ck(max_wait=120)
            result_box["cookies"] = cookies
        except Exception as exc:
            result_box["error"] = str(exc)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()

    deadline = time.time() + 130
    while t.is_alive() and time.time() < deadline:
        if session.stopped:
            session.update(status="cancelled", message="已取消")
            return
        time.sleep(0.6)

    cookies = result_box.get("cookies")
    if cookies and isinstance(cookies, dict):
        session.update(status="success", message="登录成功", cookies=dict(cookies))
    elif "error" in result_box:
        session.update(status="failed", message=f"登录异常：{result_box['error']}")
    else:
        session.update(status="expired", message="等待超时或二维码已过期")


def _drive_qtfm(session: QRSession) -> None:
    import requests

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.qtfm.cn/",
    })
    try:
        r = sess.get("https://user.qtfm.cn/u2/api/v4/users/qrcode/generate", timeout=10)
        data = r.json()
    except Exception as exc:
        session.update(status="failed", message=f"二维码生成失败：{exc}")
        return

    if data.get("errorno") != 0 or "data" not in data:
        session.update(status="failed", message=f"二维码生成失败：{data.get('errormsg', '未知错误')}")
        return

    code_id = data["data"]["code_id"]
    qr_url = data["data"]["qrcode_url"]

    qr_image = ""
    qr_render_error = ""
    if isinstance(qr_url, str) and qr_url.startswith(("http://", "https://")):
        try:
            img_resp = sess.get(qr_url, timeout=10)
            img_resp.raise_for_status()
            content_type = img_resp.headers.get("Content-Type", "image/png").split(";", 1)[0].strip()
            if content_type.startswith("image/") and img_resp.content:
                qr_image = _bytes_to_data_url(img_resp.content, content_type)
        except Exception as exc:
            qr_render_error = f"二维码图片下载失败：{exc}"
    if not qr_image:
        try:
            import qrcode
            from io import BytesIO
            img = qrcode.make(qr_url)
            buf = BytesIO()
            img.save(buf, format="PNG")
            qr_image = _bytes_to_data_url(buf.getvalue(), "image/png")
        except ImportError:
            qr_render_error = "服务端缺少 qrcode 库（pip install qrcode），无法生成二维码图片"
        except Exception as exc:
            qr_render_error = f"二维码渲染失败：{exc}"

    if not qr_image:
        session.update(
            status="failed",
            message=qr_render_error or "二维码渲染失败，请确认服务端已安装 qrcode 库",
            extra={"code_id": code_id, "qr_url": qr_url},
        )
        return

    session.update(
        qr_image=qr_image,
        status="waiting",
        message="请用蜻蜓 FM APP 直接扫描二维码",
        extra={"code_id": code_id, "qr_url": qr_url},
    )

    deadline = time.time() + 180
    while time.time() < deadline:
        if session.stopped:
            session.update(status="cancelled", message="已取消")
            return
        time.sleep(3)
        try:
            r = sess.get(
                "https://user.qtfm.cn/u2/api/v4/users/qrcode/status_query",
                params={"code_id": code_id},
                timeout=10,
            )
            d = r.json()
        except Exception:
            continue
        if d.get("errorno") != 0:
            continue
        payload = d.get("data") or {}
        status = payload.get("qrcode_status", "")
        if status == "scanned":
            session.update(status="scanned", message="已扫描，请在 APP 上确认")
        elif status in ("success", "confirmed", "authorize"):
            access_token = payload.get("access_token", "")
            qingting_id = payload.get("qingting_id", "")
            if access_token and qingting_id:
                cookies = {"access_token": access_token, "qingting_id": qingting_id}
                session.update(status="success", message="登录成功", cookies=cookies)
                return
            session.update(message=f"等待登录信息返回（{status}）…")
        elif status == "expired":
            session.update(status="expired", message="二维码已过期")
            return

    session.update(status="expired", message="等待超时")


def _drive_netease(session: QRSession) -> None:
    from http.cookies import SimpleCookie
    from io import BytesIO

    import qrcode
    import requests

    http = requests.Session()
    device_id = _netease_device_id()
    http.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://music.163.com/",
    })

    try:
        payload = _netease_post(
            http,
            "/api/login/qrcode/unikey",
            {"type": _NETEASE_QR_TYPE},
            device_id,
        )
    except Exception:
        logging.exception("NetEase QR key request failed")
        session.update(status="failed", message="网易云听书二维码生成连接失败，请检查 NAS 网络后重试")
        return

    if not isinstance(payload, dict):
        session.update(status="failed", message="网易云听书二维码接口返回格式异常")
        return
    key = str(payload.get("unikey") or "").strip()
    if str(payload.get("code")) != "200" or not key:
        message = str(payload.get("message") or payload.get("msg") or "未知错误")
        session.update(status="failed", message=f"网易云听书二维码生成失败：{message}")
        return

    chain_device = _netease_cookie_value(getattr(http, "cookies", None), "sDeviceId")
    if not chain_device:
        chain_device = f"unknown-{secrets.randbelow(1_000_000)}"
    chain_id = f"v1_{chain_device}_web_login_{_netease_now_ms()}"
    qr_url = (
        f"https://music.163.com/login?codekey={quote(key, safe='')}"
        f"&chainId={quote(chain_id, safe='_-.')}"
    )
    try:
        image = qrcode.make(qr_url)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        qr_image = _bytes_to_data_url(buffer.getvalue(), "image/png")
    except Exception as exc:
        session.update(status="failed", message=f"网易云听书二维码渲染失败：{exc}")
        return

    session.update(
        qr_image=qr_image,
        status="waiting",
        message="请使用网易云音乐 APP 扫描二维码",
        extra={"qr_url": qr_url},
    )

    deadline = time.time() + 180
    consecutive_errors = 0
    last_code = ""
    while time.time() < deadline:
        if session.stopped:
            session.update(status="cancelled", message="已取消")
            return
        time.sleep(2)
        try:
            payload = _netease_post(
                http,
                "/api/login/qrcode/client/login",
                {"key": key, "type": _NETEASE_QR_TYPE},
                device_id,
            )
            consecutive_errors = 0
        except Exception:
            logging.exception("NetEase QR status request failed")
            consecutive_errors += 1
            if consecutive_errors >= 5:
                session.update(status="failed", message="网易云听书登录连接失败，请检查网络后重试")
                return
            continue

        if not isinstance(payload, dict):
            session.update(status="failed", message="网易云听书登录接口返回格式异常")
            return
        code = str(payload.get("code") or "")
        if code != last_code:
            logging.info("NetEase QR status changed: %s", code or "unknown")
            last_code = code
        if code == "800":
            session.update(status="expired", message="二维码已过期，请重新获取")
            return
        if code == "802":
            session.update(status="scanned", message="已扫描，请在网易云音乐 APP 中确认登录")
            continue
        if code == "801":
            continue
        if code != "803":
            message = str(payload.get("message") or payload.get("msg") or "登录失败")
            safe_message = " ".join(message.split())[:200]
            logging.warning(
                "NetEase QR authorization rejected: code=%s message=%s",
                code or "unknown",
                safe_message or "unknown",
            )
            if code == "8821":
                session.update(
                    status="failed",
                    message="网易云拒绝了本次扫码授权，请重新获取二维码并使用最新版网易云音乐 APP 扫描",
                )
            else:
                session.update(status="failed", message=f"网易云听书登录失败：{safe_message}")
            return

        cookies = {}
        for jar in (getattr(http, "cookies", None),):
            if jar is None:
                continue
            try:
                cookies.update(jar.get_dict())
            except AttributeError:
                try:
                    cookies.update(dict(jar))
                except (TypeError, ValueError):
                    pass
        cookie_text = str(payload.get("cookie") or "").strip()
        if cookie_text:
            parsed = SimpleCookie()
            parsed.load(cookie_text)
            cookies.update({name: morsel.value for name, morsel in parsed.items() if morsel.value})

        if not cookies.get("MUSIC_U"):
            session.update(status="failed", message="网易云听书已确认登录，但未返回账号 Cookie，请重新扫码")
            return
        session.update(status="success", message="网易云听书登录成功", cookies=cookies)
        return

    session.update(status="expired", message="等待扫码超时，请重新获取二维码")


_DRIVERS = {
    "ximalaya": _drive_ximalaya,
    "qidian": _drive_qidian,
    "qtfm": _drive_qtfm,
    "netease": _drive_netease,
}


class QRLoginManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, QRSession] = {}
        self._lock = threading.Lock()

    def start(self, platform: str) -> QRSession:
        if platform not in _DRIVERS:
            raise ValueError(f"不支持的平台：{platform}")
        session = QRSession(platform)
        with self._lock:
            self._sessions[session.id] = session
        threading.Thread(target=_DRIVERS[platform], args=(session,), daemon=True, name=f"qr-{platform}-{session.id}").start()
        return session

    def get(self, sid: str) -> Optional[QRSession]:
        with self._lock:
            return self._sessions.get(sid)

    def cancel(self, sid: str) -> bool:
        s = self.get(sid)
        if not s:
            return False
        s.stop()
        s.update(status="cancelled", message="已取消")
        return True

    def cleanup(self, max_age: int = 3600) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            for sid in list(self._sessions.keys()):
                if now - self._sessions[sid].updated_at > max_age:
                    self._sessions.pop(sid, None)
                    removed += 1
        return removed


manager = QRLoginManager()
