# -*- coding: utf-8 -*-
"""`core/download_adapter.py` 的行为锁定测试。

移植分析 W2-3。核心要证明的是**替换下载实现没有改变对外契约**：

  ① 成功 → 返回最终路径；失败 → 抛 `requests` 异常族（既有 `except` 照常工作）；
  ② `lrts_manager.download_audio` 的布尔契约、阈值、日志前缀一字不变；
  ③ 失败后不留任何半截文件；
  ④ 分段/单流的选择对调用方透明。
"""

import http.server
import os
import socket
import threading
import time

import pytest
import requests

from core import chunked_download as cd
from core import download_adapter
from core.download_adapter import download_with_session, session_to_file


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = b""
    status = 200
    support_range = True
    content_type = "audio/mp4"
    body_override = None
    request_log = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.request_log.append(self.headers.get("Range"))
        if _Handler.status != 200:
            body = _Handler.body_override if _Handler.body_override is not None else b""
            self.send_response(_Handler.status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", _Handler.content_type)
            self.end_headers()
            self.wfile.write(body)
            return

        data = _Handler.body_override if _Handler.body_override is not None else _Handler.payload
        rng = self.headers.get("Range")
        if rng and _Handler.support_range:
            s, _, e = rng.replace("bytes=", "").partition("-")
            start = int(s)
            end = int(e) if e else len(data) - 1
            end = min(end, len(data) - 1)
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
        else:
            body = data
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", _Handler.content_type)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    _Handler.payload = os.urandom(4 * 1024 * 1024)
    _Handler.status = 200
    _Handler.support_range = True
    _Handler.content_type = "audio/mp4"
    _Handler.body_override = None
    _Handler.request_log = []

    yield f"http://127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()


class TestSuccessContract:
    def test_returns_final_path(self, server, tmp_path):
        dst = str(tmp_path / "a.m4a")
        with requests.Session() as s:
            path = session_to_file(session=s, url=f"{server}/a.m4a", save_path=dst)
        assert path == dst
        assert os.path.getsize(path) == len(_Handler.payload)

    def test_segmented_content_is_byte_identical(self, server, tmp_path):
        with requests.Session() as s:
            segmented = session_to_file(
                session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "seg.m4a")
            )
            single = session_to_file(
                session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "sin.m4a"),
                allow_segmented=False,
            )
        assert open(segmented, "rb").read() == open(single, "rb").read()

    def test_extension_correction_returned_to_caller(self, server, tmp_path):
        """落盘时按文件头纠正后缀，返回值必须是**纠正后**的路径。"""
        _Handler.body_override = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20000
        try:
            with requests.Session() as s:
                path = session_to_file(
                    session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "a.mp3")
                )
            assert path.endswith(".m4a")
            assert os.path.exists(path)
        finally:
            _Handler.body_override = None


class TestFailureContract:
    def test_403_raises_requests_http_error(self, server, tmp_path):
        """4xx 必须抛 `requests.HTTPError`，与改造前 `raise_for_status()` 一致。"""
        _Handler.status = 403
        try:
            with requests.Session() as s:
                with pytest.raises(requests.HTTPError):
                    session_to_file(
                        session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "a.m4a")
                    )
        finally:
            _Handler.status = 200

    def test_503_raises_requests_exception(self, server, tmp_path):
        """5xx 抛 `requests.RequestException` 子类（既有 `except Exception` 能捕获）。"""
        _Handler.status = 503
        try:
            with requests.Session() as s:
                with pytest.raises(requests.RequestException):
                    session_to_file(
                        session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "a.m4a")
                    )
        finally:
            _Handler.status = 200

    @pytest.mark.parametrize("status", [403, 404, 500, 502, 503])
    def test_failure_leaves_no_partial_files(self, server, tmp_path, status):
        _Handler.status = status
        dst = str(tmp_path / "a.m4a")
        try:
            with requests.Session() as s:
                with pytest.raises(Exception):
                    session_to_file(session=s, url=f"{server}/a.m4a", save_path=dst)
        finally:
            _Handler.status = 200
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_too_small_body_is_rejected_and_cleaned(self, server, tmp_path):
        _Handler.body_override = b"tiny"
        dst = str(tmp_path / "a.m4a")
        try:
            with requests.Session() as s:
                with pytest.raises(requests.RequestException):
                    session_to_file(session=s, url=f"{server}/a.m4a", save_path=dst)
        finally:
            _Handler.body_override = None
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))


class TestBooleanWrapper:
    def test_success_returns_true(self, server, tmp_path):
        with requests.Session() as s:
            ok = download_with_session(
                s, f"{server}/a.m4a", str(tmp_path / "a.m4a")
            )
        assert ok is True

    def test_failure_returns_false_without_raising(self, server, tmp_path):
        _Handler.status = 403
        try:
            with requests.Session() as s:
                ok = download_with_session(s, f"{server}/a.m4a", str(tmp_path / "a.m4a"))
        finally:
            _Handler.status = 200
        assert ok is False

    def test_min_valid_bytes_enforced(self, server, tmp_path):
        _Handler.body_override = b"x" * 100
        try:
            with requests.Session() as s:
                ok = download_with_session(
                    s, f"{server}/a.m4a", str(tmp_path / "a.m4a"), min_valid_bytes=10240
                )
        finally:
            _Handler.body_override = None
        assert ok is False


class TestErrorBodyRule:
    def test_paywall_json_is_permission_error(self, server, tmp_path):
        """47 字节的 ret=726 拒绝体 → 权限错误，不是「文件过小」。"""
        _Handler.body_override = b'{"msg":"buy now","ret":726}'
        _Handler.content_type = "application/json"
        try:
            with requests.Session() as s:
                with pytest.raises(requests.HTTPError) as exc:
                    session_to_file(session=s, url=f"{server}/a.m4a", save_path=str(tmp_path / "a.m4a"))
            assert "726" in str(exc.value) or "购买" in str(exc.value)
        finally:
            _Handler.body_override = None
            _Handler.content_type = "audio/mp4"


class TestLRTSIntegration:
    """真实验收点：lrts_manager.download_audio 的对外契约必须一字不变。"""

    def test_returns_true_and_writes_file(self, server, tmp_path):
        from core.lrts_manager import LRTSManager

        mgr = LRTSManager.__new__(LRTSManager)     # 绕开会读 cookie 的 __init__
        mgr.session = requests.Session()
        dst = str(tmp_path / "lrts.m4a")

        assert mgr.download_audio(f"{server}/a.m4a", dst) is True
        assert os.path.getsize(dst) == len(_Handler.payload)

    def test_returns_false_on_http_error(self, server, tmp_path, capsys):
        from core.lrts_manager import LRTSManager

        _Handler.status = 403
        try:
            mgr = LRTSManager.__new__(LRTSManager)
            mgr.session = requests.Session()
            ok = mgr.download_audio(f"{server}/a.m4a", str(tmp_path / "lrts.m4a"))
        finally:
            _Handler.status = 200
        assert ok is False
        assert "[lrts] download failed" in capsys.readouterr().out

    def test_small_file_is_rejected(self, server, tmp_path):
        """阈值 > 10240 与改造前一致。"""
        from core.lrts_manager import LRTSManager

        _Handler.body_override = b"x" * 512
        try:
            mgr = LRTSManager.__new__(LRTSManager)
            mgr.session = requests.Session()
            ok = mgr.download_audio(f"{server}/a.m4a", str(tmp_path / "lrts.m4a"))
        finally:
            _Handler.body_override = None
        assert ok is False

    def test_progress_callback_is_invoked(self, server, tmp_path):
        from core.lrts_manager import LRTSManager

        mgr = LRTSManager.__new__(LRTSManager)
        mgr.session = requests.Session()
        seen = []
        mgr.download_audio(
            f"{server}/a.m4a", str(tmp_path / "lrts.m4a"),
            progress_callback=lambda done, total: seen.append(done),
        )
        assert seen, "进度回调必须被调用"
        assert seen == sorted(seen)

    def test_no_range_support_still_succeeds(self, server, tmp_path):
        from core.lrts_manager import LRTSManager

        _Handler.support_range = False
        try:
            mgr = LRTSManager.__new__(LRTSManager)
            mgr.session = requests.Session()
            dst = str(tmp_path / "lrts.m4a")
            assert mgr.download_audio(f"{server}/a.m4a", dst) is True
            assert os.path.getsize(dst) == len(_Handler.payload)
        finally:
            _Handler.support_range = True
