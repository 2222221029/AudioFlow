# -*- coding: utf-8 -*-
"""酷我听书下载路径的**契约保留**测试（W2-3 接入分段下载后的回归）。

酷我 `download_audio` 的错误语义比其它平台复杂得多（403 → `restricted`、
content-type 探测 → 「地址已失效」、非 200 → HTTP 码），而这些语义被
`download_worker` 通过 `last_error` / `last_error_type` 读取，
进而驱动「不再自动重试」的判定。所以替换下载实现时**必须逐条锁住**。
"""

import http.server
import os
import socket
import threading

import pytest
import requests

from core.kuwo_manager import KuwoManager


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    content_type = "audio/mpeg"
    payload = b""
    support_range = True

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = _Handler.payload
        if _Handler.status != 200:
            self.send_response(_Handler.status)
            self.send_header("Content-Length", "0")
            self.send_header("Content-Type", _Handler.content_type)
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
            # 客户端在校验失败后会提前关连接（例如「文件过小」直接丢弃），
            # 这是**预期行为**，不是测试故障 —— 吞掉以免污染 pytest 输出。
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
    _Handler.content_type = "audio/mpeg"
    _Handler.payload = os.urandom(2 * 1024 * 1024 + 4096)   # >2MB 走分段
    _Handler.support_range = True

    yield f"http://127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()


def _manager():
    mgr = KuwoManager.__new__(KuwoManager)      # 绕开会发网络请求的 __init__
    mgr.session = requests.Session()
    mgr.last_error = ""
    mgr.last_error_type = ""
    mgr._download_info_cache = {}
    # 真实实现里的两个辅助方法，这里给最小可用版本
    mgr._clear_error = lambda: (setattr(mgr, "last_error", ""), setattr(mgr, "last_error_type", "")) and None
    mgr._record_error = lambda msg, kind=None: (
        setattr(mgr, "last_error", msg),
        setattr(mgr, "last_error_type", kind or ""),
    ) and None
    mgr._get_play_restriction_message = lambda chapter_id: ""
    return mgr


class TestSuccessPath:
    def test_large_file_downloads_and_reports_success(self, server, tmp_path, capsys):
        mgr = _manager()
        dst = str(tmp_path / "kuwo.mp3")
        assert mgr.download_audio(f"{server}/a.mp3", dst) is True
        assert os.path.getsize(dst) == len(_Handler.payload)
        assert "下载成功" in capsys.readouterr().out

    def test_segmented_and_single_agree_byte_for_byte(self, server, tmp_path):
        mgr = _manager()
        a = str(tmp_path / "a.mp3")
        mgr.download_audio(f"{server}/a.mp3", a)
        _Handler.support_range = False
        try:
            b = str(tmp_path / "b.mp3")
            mgr.download_audio(f"{server}/a.mp3", b)
        finally:
            _Handler.support_range = True
        assert open(a, "rb").read() == open(b, "rb").read()

    def test_error_state_cleared_on_success(self, server, tmp_path):
        mgr = _manager()
        mgr.last_error = "陈旧错误"
        mgr.last_error_type = "restricted"
        assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is True
        assert mgr.last_error == ""
        assert mgr.last_error_type == ""


class TestErrorContracts:
    def test_403_records_restricted(self, server, tmp_path):
        mgr = _manager()
        _Handler.status = 403
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is False
        finally:
            _Handler.status = 200
        assert mgr.last_error_type == "restricted"
        assert "403" in mgr.last_error

    def test_403_with_restriction_message_uses_it(self, server, tmp_path):
        mgr = _manager()
        mgr._get_play_restriction_message = lambda cid: "该歌曲为 VIP 专享"
        _Handler.status = 403
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is False
        finally:
            _Handler.status = 200
        assert "VIP 专享" in mgr.last_error
        assert mgr.last_error_type == "restricted"

    @pytest.mark.parametrize("status", [404, 500, 502])
    def test_other_status_records_http_code(self, server, tmp_path, status):
        mgr = _manager()
        _Handler.status = status
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is False
        finally:
            _Handler.status = 200
        assert f"HTTP {status}" in mgr.last_error

    @pytest.mark.parametrize("ctype", ["text/html", "application/json"])
    def test_non_audio_content_type_is_rejected_before_download(self, server, tmp_path, ctype):
        mgr = _manager()
        _Handler.content_type = ctype
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is False
        finally:
            _Handler.content_type = "audio/mpeg"
        assert "非音频内容" in mgr.last_error or "已失效" in mgr.last_error

    def test_tiny_file_is_rejected(self, server, tmp_path):
        mgr = _manager()
        _Handler.payload = b"x" * 512
        try:
            assert mgr.download_audio(f"{server}/a.mp3", str(tmp_path / "a.mp3")) is False
        finally:
            _Handler.payload = os.urandom(2 * 1024 * 1024 + 4096)
        assert "过小" in mgr.last_error

    def test_exception_is_recorded_and_returns_false(self, tmp_path):
        mgr = _manager()

        class _Boom:
            def get(self, *a, **k):
                raise requests.ConnectionError("网络断了")

        mgr.session = _Boom()
        assert mgr.download_audio("http://x/a.mp3", str(tmp_path / "a.mp3")) is False
        assert "异常" in mgr.last_error


class TestNoPartialFilesLeftBehind:
    @pytest.mark.parametrize("status", [403, 404, 500, 502])
    def test_http_error_leaves_nothing(self, server, tmp_path, status):
        mgr = _manager()
        _Handler.status = status
        dst = str(tmp_path / "a.mp3")
        try:
            mgr.download_audio(f"{server}/a.mp3", dst)
        finally:
            _Handler.status = 200
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_small_file_leaves_nothing(self, server, tmp_path):
        mgr = _manager()
        _Handler.payload = b"x" * 512
        dst = str(tmp_path / "a.mp3")
        try:
            mgr.download_audio(f"{server}/a.mp3", dst)
        finally:
            _Handler.payload = os.urandom(2 * 1024 * 1024 + 4096)
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_connection_error_leaves_nothing(self, tmp_path):
        mgr = _manager()

        class _Boom:
            def get(self, *a, **k):
                raise requests.ConnectionError("boom")

        mgr.session = _Boom()
        dst = str(tmp_path / "a.mp3")
        mgr.download_audio("http://x/a.mp3", dst)
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))


class TestProgressReporting:
    def test_progress_callback_receives_monotonic_values(self, server, tmp_path):
        """进度回调必须被调用且单调递增。

        ⚠ 不断言「最后一次 == 文件总大小」：进度上报有 **0.2 秒节流**
        （参考实现 `DownloadEngine.cs:233-242` 的教训 —— 原来每 64KB 报一次，
        8 路并发时每秒数百次 UI 派发把 UI 线程淹没成「未响应」）。
        localhost 上 2MB 文件几十毫秒就下完，因此只有很少几次回调，
        最后一次**未必**等于总大小。断言「单调递增且非空」才是正确的契约。
        """
        mgr = _manager()
        seen = []
        mgr.download_audio(
            f"{server}/a.mp3", str(tmp_path / "a.mp3"),
            progress_callback=lambda done, total: seen.append(done),
        )
        assert seen, "进度回调必须被调用"
        assert seen == sorted(seen), "进度必须单调递增"
        assert all(v > 0 for v in seen)

    def test_progress_is_throttled_not_per_chunk(self, server, tmp_path):
        """节流生效：2MB 文件在 64KB 块下本会有 32 次回调，节流后应远少于它。"""
        mgr = _manager()
        seen = []
        mgr.download_audio(
            f"{server}/a.mp3", str(tmp_path / "a.mp3"),
            progress_callback=lambda done, total: seen.append(done),
        )
        assert len(seen) < 32, f"进度回调 {len(seen)} 次，节流没生效"
