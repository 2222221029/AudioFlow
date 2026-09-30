# -*- coding: utf-8 -*-
"""网易云听书 / 荔枝FM 下载路径的**契约保留**测试。

W2-3 第二批接入分段下载。两个平台的 `download_audio` 都是「布尔返回 + 成功判定
> 1024 字节」的简单契约，但各自的**错误文案与前置校验**必须一字不变：

* 网易云：`_require_cookie()` 在最前（未登录时抛）、`❌ 网易云听书下载失败:`
* 荔枝FM：空 URL 直接 False、`[荔枝FM] 下载失败:`

这些文案会在 `download_worker` 的异常分支里被打印，是排障的主要线索。
"""

import http.server
import os
import socket
import threading

import pytest
import requests

from core.lizhi_manager import LizhiManager
from core.netease_cloud_audiobook_manager import NeteaseCloudAudiobookManager


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = b""
    status = 200
    support_range = True
    content_type = "audio/mpeg"
    request_log = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.request_log.append(self.headers.get("Range"))
        data = _Handler.payload
        if _Handler.status != 200:
            self.send_response(_Handler.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

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
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture
def server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    _Handler.status = 200
    _Handler.support_range = True
    _Handler.content_type = "audio/mpeg"
    _Handler.payload = os.urandom(3 * 1024 * 1024)   # >2MB 走分段
    _Handler.request_log = []

    yield f"http://127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()


# ======================================================================
# 网易云听书
# ======================================================================

def _netease():
    mgr = NeteaseCloudAudiobookManager.__new__(NeteaseCloudAudiobookManager)
    mgr.session = requests.Session()
    mgr.session.headers.update({"User-Agent": "test-ua"})
    # `_require_cookie()` 读的是 `cookie_string`，桩里必须给上（否则前置校验先抛）
    mgr.cookie_string = "MUSIC_U=fake; __csrf=fake"
    return mgr


class TestNetease:
    def test_success_writes_full_file(self, server, tmp_path):
        mgr = _netease()
        dst = str(tmp_path / "ne.mp3")
        assert mgr.download_audio(f"{server}/a.mp3", dst) is True
        assert os.path.getsize(dst) == len(_Handler.payload)

    def test_segmented_and_single_identical(self, server, tmp_path):
        mgr = _netease()
        a = str(tmp_path / "a.mp3")
        mgr.download_audio(f"{server}/a.mp3", a)
        _Handler.support_range = False
        try:
            b = str(tmp_path / "b.mp3")
            mgr.download_audio(f"{server}/a.mp3", b)
        finally:
            _Handler.support_range = True
        assert open(a, "rb").read() == open(b, "rb").read()

    def test_http_error_returns_false_with_original_message(self, server, tmp_path, capsys):
        mgr = _netease()
        _Handler.status = 403
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "ne.mp3")) is False
        finally:
            _Handler.status = 200
        assert "网易云听书下载失败" in capsys.readouterr().out

    def test_tiny_file_is_rejected(self, server, tmp_path):
        mgr = _netease()
        _Handler.payload = b"x" * 100
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "ne.mp3")) is False
        finally:
            _Handler.payload = os.urandom(3 * 1024 * 1024)

    def test_require_cookie_still_runs_first(self, server, tmp_path):
        """未登录必须仍抛（`_require_cookie` 在 try 之前）。"""
        mgr = _netease()
        mgr._require_cookie = lambda: (_ for _ in ()).throw(RuntimeError("未登录"))
        with pytest.raises(RuntimeError):
            mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "ne.mp3"))

    def test_failure_leaves_no_partial_files(self, server, tmp_path):
        mgr = _netease()
        _Handler.status = 500
        dst = str(tmp_path / "ne.mp3")
        try:
            mgr.download_audio(f"{server}/a.mp3", dst)
        finally:
            _Handler.status = 200
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_progress_callback_invoked(self, server, tmp_path):
        mgr = _netease()
        seen = []
        mgr.download_audio(
            f"{server}/a.mp3", str(tmp_path / "ne.mp3"),
            progress_callback=lambda done, total: seen.append(done),
        )
        assert seen and seen == sorted(seen)


# ======================================================================
# 荔枝FM
# ======================================================================

def _lizhi():
    mgr = LizhiManager.__new__(LizhiManager)
    mgr.session = requests.Session()
    mgr.session.headers.update({"User-Agent": "test-ua"})
    return mgr


class TestLizhi:
    def test_success_writes_full_file(self, server, tmp_path):
        mgr = _lizhi()
        dst = str(tmp_path / "lz.mp3")
        assert mgr.download_audio(f"{server}/a.mp3", dst) is True
        assert os.path.getsize(dst) == len(_Handler.payload)

    def test_blank_url_returns_false_without_request(self, tmp_path):
        mgr = _lizhi()
        assert mgr.download_audio("", str(tmp_path / "lz.mp3")) is False
        assert not os.path.exists(str(tmp_path / "lz.mp3"))

    def test_quality_argument_is_ignored_but_accepted(self, server, tmp_path):
        """签名里有 `quality`，既有调用方会传 —— 不能因为新增参数而报错。"""
        mgr = _lizhi()
        assert mgr.download_audio(
            f"{server}/a.mp3", str(tmp_path / "lz.mp3"), quality="high"
        ) is True

    def test_segmented_and_single_identical(self, server, tmp_path):
        mgr = _lizhi()
        a = str(tmp_path / "a.mp3")
        mgr.download_audio(f"{server}/a.mp3", a)
        _Handler.support_range = False
        try:
            b = str(tmp_path / "b.mp3")
            mgr.download_audio(f"{server}/a.mp3", b)
        finally:
            _Handler.support_range = True
        assert open(a, "rb").read() == open(b, "rb").read()

    def test_http_error_returns_false_with_original_message(self, server, tmp_path, capsys):
        mgr = _lizhi()
        _Handler.status = 404
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "lz.mp3")) is False
        finally:
            _Handler.status = 200
        assert "[荔枝FM] 下载失败" in capsys.readouterr().out

    def test_tiny_file_is_rejected(self, server, tmp_path):
        mgr = _lizhi()
        _Handler.payload = b"x" * 100
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "lz.mp3")) is False
        finally:
            _Handler.payload = os.urandom(3 * 1024 * 1024)

    def test_failure_leaves_no_partial_files(self, server, tmp_path):
        mgr = _lizhi()
        _Handler.status = 503
        dst = str(tmp_path / "lz.mp3")
        try:
            mgr.download_audio(f"{server}/a.mp3", dst)
        finally:
            _Handler.status = 200
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_progress_callback_invoked(self, server, tmp_path):
        mgr = _lizhi()
        seen = []
        mgr.download_audio(
            f"{server}/a.mp3", str(tmp_path / "lz.mp3"),
            progress_callback=lambda done, total: seen.append(done),
        )
        assert seen and seen == sorted(seen)


class TestSegmentationActuallyHappens:
    @pytest.mark.parametrize("factory", [_netease, _lizhi])
    def test_range_requests_are_issued_for_large_files(self, server, tmp_path, factory):
        """大文件必须真的走分段（否则接入等于没接）。"""
        mgr = factory()
        _Handler.request_log = []
        mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "x.mp3"))
        ranges = [r for r in _Handler.request_log if r]
        assert len(ranges) >= 2, f"只发了 {len(ranges)} 个 Range 请求，分段没生效"

    @pytest.mark.parametrize("factory", [_netease, _lizhi])
    def test_small_files_do_not_segment(self, server, tmp_path, factory):
        """小文件不该分段（<2MB 门槛）—— 避免为省几十毫秒多开连接。

        ⚠ 不断言「零 Range 请求」：这两个平台的接口**不报 file_size**，
        要判断「要不要分段」必须先用 1 字节 Range 探一次
        （参考实现 `Core/Net.cs:131-136` 的实测：fq/kw/qd/qf/lrts 全部不报）。
        那次探测是**必要的代价**，不是浪费。

        断言的是「探测之后选了单流」——即除了 `bytes=0-0` 之外没有别的 Range 请求。
        """
        mgr = factory()
        _Handler.payload = os.urandom(300 * 1024)
        _Handler.request_log = []
        try:
            mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "x.mp3"))
        finally:
            _Handler.payload = os.urandom(3 * 1024 * 1024)

        ranges = [r for r in _Handler.request_log if r]
        assert ranges in ([], ["bytes=0-0"]), f"小文件不该发分段请求，实际发了 {ranges}"
