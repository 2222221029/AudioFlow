#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫码登录会话管理（无 Qt 依赖）。"""

import base64
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional



SUPPORTED_PLATFORMS = ("ximalaya", "qidian", "qtfm", "netease")
# NetEase's current web QR flow uses the raw /api endpoints with type=3.
# Older type=1 keys still generate a QR image but are rejected by the app.
_NETEASE_QR_TYPE = 3


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
    http.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://music.163.com/",
    })

    try:
        response = http.post(
            "https://music.163.com/api/login/qrcode/unikey",
            data={"type": _NETEASE_QR_TYPE},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logging.exception("NetEase QR key request failed")
        session.update(status="failed", message=f"网易云听书二维码生成失败：{exc}")
        return

    if not isinstance(payload, dict):
        session.update(status="failed", message="网易云听书二维码接口返回格式异常")
        return
    key = str(payload.get("unikey") or "").strip()
    if str(payload.get("code")) != "200" or not key:
        message = str(payload.get("message") or payload.get("msg") or "未知错误")
        session.update(status="failed", message=f"网易云听书二维码生成失败：{message}")
        return

    qr_url = f"https://music.163.com/login?codekey={key}"
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
            response = http.post(
                "https://music.163.com/api/login/qrcode/client/login",
                data={"key": key, "type": _NETEASE_QR_TYPE},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
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
            session.update(status="failed", message=f"网易云听书登录失败：{message}")
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
