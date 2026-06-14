#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫码登录会话管理（无 Qt 依赖）。"""

import base64
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional



SUPPORTED_PLATFORMS = ("ximalaya", "qidian", "qtfm")


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
        session.update(status="failed", message="获取二维码失败")
        return

    qr_path = Path("qrcode.png")
    if not qr_path.exists():
        session.update(status="failed", message="二维码文件未找到")
        return

    session.update(
        qr_image=_file_to_data_url(qr_path),
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


_DRIVERS = {
    "ximalaya": _drive_ximalaya,
    "qidian": _drive_qidian,
    "qtfm": _drive_qtfm,
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
