# -*- coding: utf-8 -*-
"""`core/chunked_download.py` 的行为锁定测试。

移植分析 P0-1 / P0-2 的验证。重点不是「分段能跑」，而是：

  ① **结果与单流逐字节相同**（分段是加速手段，不是新格式）；
  ② **任何异常路径都安全回退**，且回退时不留半截文件；
  ③ **空闲超时真的能触发**（这是参考实现里「最后几集 0 速度、专辑永远下不完」
     那条事故的直接对策）；
  ④ 探大小失败**绝不升级**成下载失败。

用一个真实的本地 HTTP 服务器（支持 Range）跑端到端，其余用假 response 打桩。
"""

import http.server
import os
import socket
import threading
import time
from unittest.mock import patch

import pytest

from core import chunked_download as cd
from core.errors import ContentInvalidError, PermissionDenied, TransientError


# ======================================================================
# 本地 Range 服务器（端到端用）
# ======================================================================

class _RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    delay = 0.0
    no_range = False
    fail_status = None
    request_log = []

    def log_message(self, *args):   # 静音
        pass

    def do_GET(self):
        _RangeHandler.request_log.append(
            (self.path, self.headers.get("Range"), self.headers.get("User-Agent"))
        )
        if _RangeHandler.fail_status:
            self.send_response(_RangeHandler.fail_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        data = _RangeHandler.payload
        if _RangeHandler.delay:
            time.sleep(_RangeHandler.delay)

        rng = self.headers.get("Range")
        if rng and not _RangeHandler.no_range:
            start_s, _, end_s = rng.replace("bytes=", "").partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            chunk = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Type", "audio/mp4")
            self.end_headers()
            self.wfile.write(chunk)
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "audio/mp4")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    _RangeHandler.delay = 0.0
    _RangeHandler.no_range = False
    _RangeHandler.fail_status = None
    _RangeHandler.request_log = []

    yield f"http://127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()


def _requests_getter(session, timeout=(5, 5)):
    """把 requests 包装成本模块要的 get_response 契约。"""
    import requests

    def _get(url, headers, rng, stream):
        hdrs = dict(headers or {})
        if rng is not None:
            hdrs["Range"] = f"bytes={rng[0]}-{rng[1]}"
        return session.get(url, headers=hdrs, stream=stream, timeout=timeout)

    return _get


# ======================================================================
# 端到端：分段 vs 单流
# ======================================================================

class TestEndToEnd:
    def test_segmented_matches_single_stream_byte_for_byte(self, server, tmp_path):
        import requests

        payload = os.urandom(6 * 1024 * 1024)      # 6MB，>= 2MB 门槛
        _RangeHandler.payload = payload
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            getter = _requests_getter(s)
            seg_path = str(tmp_path / "seg.m4a")
            outcome = cd.stream_to_file(
                dst=seg_path, get_response=getter, guess_total=len(payload), url=url
            )
            assert outcome.segmented is True
            seg_bytes = open(seg_path, "rb").read()

            single_path = str(tmp_path / "single.m4a")
            outcome2 = cd.stream_to_file(
                dst=single_path, get_response=getter, guess_total=len(payload),
                url=url, allow_segmented=False,
            )
            single_bytes = open(single_path, "rb").read()

        assert seg_bytes == payload
        assert single_bytes == payload
        assert seg_bytes == single_bytes

    def test_segments_actually_run_in_parallel(self, server, tmp_path):
        """证明「只能变快」这条硬约束的正确形式。

        ## 为什么这样测（而不是直接比总耗时）

        localhost 上没有真实网络延迟，服务端那个 `delay` 是**纯 sleep** ——
        单流 1 次请求付 0.2s，分段 3 次请求**并发**也只付 0.2s。
        两者总耗时几乎相同（差的是线程创建 + 合并的固定开销 ~15ms），
        所以「分段总耗时 < 单流总耗时」在 localhost 上**本来就不成立**。

        真实收益来自**带宽受限**：参考实现实测起点 3.5MB 单连接 3.3MB/s、
        分段 22.3MB/s（6.8×）。localhost 模拟不出带宽瓶颈。

        所以这里验证**并发确实发生了** —— 这是收益的充要条件：
        3 个段各 sleep 0.2s，串行需要 ≥0.6s，并发只需 ≈0.2s。
        """
        import requests

        payload = os.urandom(6 * 1024 * 1024)      # 3 段
        _RangeHandler.payload = payload
        _RangeHandler.delay = 0.2
        _RangeHandler.request_log = []
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            t0 = time.time()
            outcome = cd.stream_to_file(
                dst=str(tmp_path / "seg.m4a"), get_response=_requests_getter(s),
                guess_total=len(payload), url=url,
            )
            elapsed = time.time() - t0

        seg_count = cd.segment_count_for(len(payload))
        assert outcome.segmented is True
        assert seg_count == 3

        # 串行下界 = 段数 × 每段延迟。并发必须显著快于它。
        serial_lower_bound = seg_count * 0.2
        assert elapsed < serial_lower_bound * 0.75, (
            f"3 段耗时 {elapsed:.3f}s，接近串行下界 {serial_lower_bound:.2f}s —— 并发没生效"
        )

        # 而且确实发了 3 个 Range 请求（0 个探测请求，因为 guess_total 已给出）
        ranges = [entry[1] for entry in _RangeHandler.request_log]
        assert len(ranges) == 3
        assert all(r and r.startswith("bytes=") for r in ranges)

    def test_segmented_is_not_slower_than_single_beyond_fixed_overhead(self, server, tmp_path):
        """分段不能比单流**慢出一个固定开销以上** —— 防止引入性能回归。

        localhost 固定开销（线程创建 + 合并）实测 ~15ms；给到 120ms 的宽容上限，
        既抓得住真正的回归（误加探测往返、段被串行化），又不会被 CI 抖动误伤。
        """
        import requests

        payload = os.urandom(6 * 1024 * 1024)
        _RangeHandler.payload = payload
        _RangeHandler.delay = 0.0
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            getter = _requests_getter(s)

            single_times = []
            for i in range(3):
                t0 = time.time()
                cd.stream_to_file(
                    dst=str(tmp_path / f"single{i}.m4a"), get_response=getter,
                    guess_total=len(payload), url=url, allow_segmented=False,
                )
                single_times.append(time.time() - t0)
            single = min(single_times)

            seg_times = []
            for i in range(3):
                t0 = time.time()
                cd.stream_to_file(
                    dst=str(tmp_path / f"seg{i}.m4a"), get_response=getter,
                    guess_total=len(payload), url=url,
                )
                seg_times.append(time.time() - t0)
            segmented = min(seg_times)

        assert segmented - single < 0.120, (
            f"分段 {segmented:.3f}s 比单流 {single:.3f}s 慢了 {segmented - single:.3f}s，超过固定开销"
        )

    def test_probe_is_skipped_when_size_is_known(self, server, tmp_path):
        """接口已报体积时**不能**再发探测请求 —— 那是白白多一次往返。

        这是实测抓到的回归：原来无论 guess_total 有没有值都探一次，
        在 200ms RTT 链路上让 3 段并发的收益全部吐回去。
        """
        import requests

        payload = os.urandom(6 * 1024 * 1024)
        _RangeHandler.payload = payload
        _RangeHandler.request_log = []
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"), get_response=_requests_getter(s),
                guess_total=len(payload), url=url,
            )

        ranges = [entry[1] for entry in _RangeHandler.request_log]
        assert "bytes=0-0" not in ranges, "已知体积时不该再发 1 字节探测"
        assert len(ranges) == cd.segment_count_for(len(payload))

    def test_server_without_range_falls_back_transparently(self, server, tmp_path):
        """CDN 不支持 Range → 自动回退单流，结果照样完整。"""
        import requests

        payload = os.urandom(4 * 1024 * 1024)
        _RangeHandler.payload = payload
        _RangeHandler.no_range = True          # 忽略 Range，永远回 200
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            outcome = cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"), get_response=_requests_getter(s),
                guess_total=len(payload), url=url,
            )
        assert outcome.segmented is False
        assert open(outcome.path, "rb").read() == payload

    def test_no_leftover_part_files_on_success(self, server, tmp_path):
        import requests

        payload = os.urandom(4 * 1024 * 1024)
        _RangeHandler.payload = payload
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            outcome = cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"), get_response=_requests_getter(s),
                guess_total=len(payload), url=url,
            )

        leftovers = [n for n in os.listdir(tmp_path) if ".part" in n]
        assert leftovers == []

    def test_probe_discovers_total_when_api_reports_none(self, server, tmp_path):
        """接口不报 file_size 的平台：靠 1 字节 Range 探测拿到总长并分段。

        参考实现实测番茄/酷我/起点/蜻蜓/懒人**全部不报** file_size。
        """
        import requests

        payload = os.urandom(6 * 1024 * 1024)
        _RangeHandler.payload = payload
        url = f"{server}/audio.m4a"

        with requests.Session() as s:
            outcome = cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"), get_response=_requests_getter(s),
                guess_total=None, url=url,          # 接口没报体积
            )
        assert outcome.segmented is True
        assert open(outcome.path, "rb").read() == payload


# ======================================================================
# 参数与工具
# ======================================================================

class TestSegmentCount:
    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, 1),
            (1 << 20, 1),                      # 1MB < 2MB 门槛
            ((2 << 20) - 1, 1),                # 差 1 字节不到门槛
            (2 << 20, 2),
            (3 << 20, 2),
            (5 << 20, 3),
            (8 << 20, 4),
            (16 << 20, 8),
            (1000 << 20, 8),                   # 封顶 8 段
        ],
    )
    def test_segment_count(self, size, expected):
        assert cd.segment_count_for(size) == expected

    def test_none_is_single(self):
        assert cd.segment_count_for(None) == 1


class TestPartFileHelpers:
    def test_part_paths(self):
        part, segs = cd.part_paths("/d/a.m4a")
        assert part == "/d/a.m4a.part"
        assert segs[0] == "/d/a.m4a.part.s0"
        assert len(segs) == cd.SEG_MAX

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("a.m4a.part", True),
            ("a.m4a.part.s0", True),
            ("a.m4a.part.s7", True),
            ("_report.json", True),
            ("a.m4a", False),
            ("0001 第一集.m4a", False),
        ],
    )
    def test_is_partial_file(self, name, expected):
        assert cd.is_partial_file(name) is expected

    def test_clean_part_files_removes_all(self, tmp_path):
        dst = str(tmp_path / "a.m4a")
        part, segs = cd.part_paths(dst)
        for path in [part] + segs:
            open(path, "wb").write(b"x")
        cd.clean_part_files(dst)
        assert not os.path.exists(part)
        assert not any(os.path.exists(s) for s in segs)

    def test_clean_part_files_is_idempotent(self, tmp_path):
        cd.clean_part_files(str(tmp_path / "never-existed.m4a"))   # 不抛

    def test_outcome_repr_does_not_explode(self, tmp_path):
        outcome = cd.DownloadOutcome(str(tmp_path / "a.m4a"), 123, True)
        assert "a.m4a" in repr(outcome)


# ======================================================================
# 用假 response 打桩的边界测试
# ======================================================================

class _FakeResponse:
    def __init__(self, chunks, status=200, headers=None, error=None):
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = headers or {}
        self._error = error
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
        if self._error:
            raise self._error

    def close(self):
        self.closed = True


def _getter_for(chunks, status=200, headers=None, error=None):
    def _get(url, hdrs, rng, stream):
        return _FakeResponse(chunks, status=status, headers=headers or {}, error=error)

    return _get


class TestErrorClassification:
    def test_403_is_permission_denied(self, tmp_path):
        with pytest.raises(PermissionDenied):
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=_getter_for([], status=403),
                url="http://x/a", allow_segmented=False,
            )

    def test_502_is_transient(self, tmp_path):
        with pytest.raises(TransientError):
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=_getter_for([], status=502),
                url="http://x/a", allow_segmented=False,
            )

    def test_paywall_json_is_permission_not_size(self, tmp_path):
        """47 字节的 {"ret":726} 拒绝体必须报成权限问题，不是「文件过小」。"""
        body = b'{"msg":"\xe7\xab\x8b\xe5\x8d\xb3\xe8\xb4\xad\xe4\xb9\xb0\xe5\x94\xb1\xe5\x90\xac","ret":726}'
        with pytest.raises(PermissionDenied) as exc:
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=_getter_for([body], headers={"Content-Type": "application/json"}),
                url="http://x/a", allow_segmented=False,
            )
        assert "726" in str(exc.value) or "购买" in str(exc.value)

    def test_error_page_is_content_invalid(self, tmp_path):
        with pytest.raises((ContentInvalidError, PermissionDenied)):
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=_getter_for([b"<html>404</html>"], headers={"Content-Type": "text/html"}),
                url="http://x/a", allow_segmented=False,
            )


class TestFailureLeavesNoPartial:
    @pytest.mark.parametrize("status", [403, 404, 502, 503])
    def test_http_error_leaves_no_part_file(self, tmp_path, status):
        dst = str(tmp_path / "a.m4a")
        with pytest.raises(Exception):
            cd.stream_to_file(
                dst=dst, get_response=_getter_for([], status=status),
                url="http://x/a", allow_segmented=False,
            )
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_short_download_is_rejected_and_cleaned(self, tmp_path):
        """声明 1MB 只给 100 字节 → 判不完整，且清掉半截文件。

        参考实现记录过：逐字节核对发现 2 集被 CDN 静默截断（本地 4.95MB /
        服务端 16.80MB），「连接提前 EOF 却不抛异常」。
        """
        dst = str(tmp_path / "a.m4a")
        with pytest.raises(TransientError):
            cd.stream_to_file(
                dst=dst,
                get_response=_getter_for([b"x" * 50000], headers={"Content-Length": str(1 << 20)}),
                url="http://x/a", guess_total=1 << 20, allow_segmented=False,
            )
        assert not os.path.exists(dst)
        assert not any(".part" in n for n in os.listdir(tmp_path))

    def test_tiny_response_is_content_invalid(self, tmp_path):
        dst = str(tmp_path / "a.m4a")
        with pytest.raises(ContentInvalidError):
            cd.stream_to_file(
                dst=dst, get_response=_getter_for([b"x" * 10]),
                url="http://x/a", allow_segmented=False,
            )


class TestIdleTimeout:
    def test_stall_after_data_raises_transient(self, tmp_path):
        """先给数据、再长时间不给 → 必须抛「下载停滞」而不是永远挂着。"""
        sent = {"n": 0}

        class _Stalling:
            status_code = 200
            headers = {"Content-Type": "audio/mp4"}

            def iter_content(self, chunk_size=65536):
                yield b"x" * 4096
                sent["n"] += 1
                time.sleep(0.6)
                yield b"y" * 10

            def close(self):
                pass

        with pytest.raises(TransientError) as exc:
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=lambda *a: _Stalling(),
                url="http://x/a", allow_segmented=False, idle_timeout=0.25,
            )
        assert "停滞" in str(exc.value)

    def test_heartbeat_empty_chunks_do_not_reset_timer(self, tmp_path):
        """空块心跳不能把「停滞」判据喂活 —— 这是防慢性拖死的关键。

        参考实现的事故正是「服务端不掐断也不发数据，worker 永久挂着」。
        若空块重置计时，"每秒发一个空块"就能把下载永远吊住。
        """
        class _Heartbeat:
            status_code = 200
            headers = {"Content-Type": "audio/mp4"}

            def iter_content(self, chunk_size=65536):
                yield b"x" * 4096
                for _ in range(10):
                    time.sleep(0.12)
                    yield b""          # 心跳：不发数据

            def close(self):
                pass

        with pytest.raises(TransientError) as exc:
            cd.stream_to_file(
                dst=str(tmp_path / "a.m4a"),
                get_response=lambda *a: _Heartbeat(),
                url="http://x/a", allow_segmented=False, idle_timeout=0.3,
            )
        assert "停滞" in str(exc.value)

    def test_healthy_slow_stream_is_not_killed(self, tmp_path):
        """数据一直在来（间隔 < 阈值）就不能误杀。"""
        class _SlowButAlive:
            status_code = 200
            headers = {"Content-Type": "audio/mp4"}

            def iter_content(self, chunk_size=65536):
                for _ in range(8):
                    time.sleep(0.08)
                    yield b"z" * 4096

            def close(self):
                pass

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"),
            get_response=lambda *a: _SlowButAlive(),
            url="http://x/a", allow_segmented=False, idle_timeout=0.4,
        )
        assert outcome.bytes == 8 * 4096


class TestProbe:
    def test_probe_failure_is_downgraded_not_raised(self, tmp_path):
        """探测失败绝不能升级成下载失败（参考实现约束②）。"""

        def _get(url, headers, rng, stream):
            if rng is not None:
                raise RuntimeError("probe blew up")
            return _FakeResponse([b"a" * 4096], headers={"Content-Type": "audio/mp4"})

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a", guess_total=None
        )
        assert outcome.segmented is False
        assert outcome.bytes == 4096

    def test_probe_returning_200_is_not_206(self, tmp_path):
        def _get(url, headers, rng, stream):
            if rng is not None:
                return _FakeResponse([b""], status=200)
            return _FakeResponse([b"a" * 4096], headers={"Content-Type": "audio/mp4"})

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a", guess_total=None
        )
        assert outcome.segmented is False

    def test_malformed_content_range_ignored(self, tmp_path):
        def _get(url, headers, rng, stream):
            if rng is not None:
                return _FakeResponse([b""], status=206, headers={"Content-Range": "bytes 0-0"})
            return _FakeResponse([b"a" * 4096], headers={"Content-Type": "audio/mp4"})

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a", guess_total=None
        )
        assert outcome.segmented is False


class TestProgressThrottle:
    def test_progress_called_and_monotonic(self, tmp_path):
        seen = []

        def _get(url, headers, rng, stream):
            return _FakeResponse([b"x" * 4096] * 5, headers={"Content-Type": "audio/mp4"})

        cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a",
            allow_segmented=False, progress=lambda got, total: seen.append(got),
        )
        assert seen == sorted(seen)
        assert seen and seen[-1] == 5 * 4096

    def test_progress_callback_exception_does_not_break_download(self, tmp_path):
        def _get(url, headers, rng, stream):
            return _FakeResponse([b"x" * 4096], headers={"Content-Type": "audio/mp4"})

        def _bad_progress(got, total):
            raise RuntimeError("UI 挂了")

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a",
            allow_segmented=False, progress=_bad_progress,
        )
        assert outcome.bytes == 4096


class TestExtensionCorrection:
    def test_ftyp_lands_as_m4a(self, tmp_path):
        payload = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4096

        def _get(url, headers, rng, stream):
            return _FakeResponse([payload], headers={"Content-Type": "audio/mp4"})

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.mp3"), get_response=_get, url="http://x/a", allow_segmented=False
        )
        assert outcome.path.endswith(".m4a")
        assert os.path.exists(outcome.path)

    def test_flac_lands_as_flac(self, tmp_path):
        payload = b"fLaC" + b"\x00" * 4096

        def _get(url, headers, rng, stream):
            return _FakeResponse([payload], headers={"Content-Type": "audio/flac"})

        outcome = cd.stream_to_file(
            dst=str(tmp_path / "a.m4a"), get_response=_get, url="http://x/a", allow_segmented=False
        )
        assert outcome.path.endswith(".flac")
