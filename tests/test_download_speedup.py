# -*- coding: utf-8 -*-
"""分段下载的**真实收益**基准（带带宽限制的本地服务器）。

## 为什么需要这个文件

`test_chunked_download.py` 里的 concurrency 断言证明的是「并发发生了」，
不是「更快了」—— localhost 没有带宽瓶颈，两者的总耗时几乎相同。

真实收益来自 **CDN 对单连接限速**。参考实现 XimalayaApp 的实测：

| 场景 | 单流 | 分段并行 |
| --- | --- | --- |
| 起点听书 3.5MB | 3.3 MB/s | 22.3 MB/s（6.8×） |
| 懒人听书 4.8MB | 6.9 MB/s | 23.2 MB/s（3.4×） |

本文件用一个**每连接限速**的本地 HTTP 服务器复现同一现象，把「速度只能变快」
这条约束变成可重复执行的证据。

## 运行

    .venv/bin/python -m pytest tests/test_download_speedup.py -v -s

标了 `slow`，默认不纳入快速回归；需要验证性能时显式跑。
"""

import http.server
import os
import socket
import threading
import time

import pytest

from core import chunked_download as cd

pytestmark = pytest.mark.slow

#: 单连接限速（字节/秒）。取 2MB/s —— 与参考实现实测的起点/酷我单流速度同量级。
PER_CONNECTION_RATE = 2 * 1024 * 1024

#: 测试文件大小。8MB → 4 段（每段 2MB）。
PAYLOAD_SIZE = 8 * 1024 * 1024


class _ThrottledHandler(http.server.BaseHTTPRequestHandler):
    """支持 Range，且**每条连接各自限速**。

    ⚠ 关键在「各自」：限速状态是**每请求**的局部变量，不是类属性。
    若限速共享，并发段之间会互相排队，收益就测不出来了。
    """

    payload = b""
    rate = PER_CONNECTION_RATE
    chunk_size = 64 * 1024

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = _ThrottledHandler.payload
        rng = self.headers.get("Range")

        if rng:
            start_s, _, end_s = rng.replace("bytes=", "").partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
        else:
            body = data
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))

        self.send_header("Content-Type", "audio/mp4")
        self.end_headers()

        # 每条连接独立限速（delay 只影响本线程）
        step = _ThrottledHandler.chunk_size
        per_step_sleep = step / float(_ThrottledHandler.rate)
        for offset in range(0, len(body), step):
            self.wfile.write(body[offset:offset + step])
            time.sleep(per_step_sleep)


@pytest.fixture(scope="module")
def throttled_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    _ThrottledHandler.payload = os.urandom(PAYLOAD_SIZE)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _ThrottledHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _getter(session):
    def _get(url, headers, rng, stream):
        hdrs = dict(headers or {})
        if rng is not None:
            hdrs["Range"] = f"bytes={rng[0]}-{rng[1]}"
        return session.get(url, headers=hdrs, stream=stream, timeout=(5, 120))

    return _get


def test_segmented_download_is_substantially_faster(throttled_server, tmp_path):
    """核心收益断言：每连接限速 2MB/s 时，分段必须显著快于单流。

    下界是「单流耗时 ÷ 段数」—— 完美并发能达到的理论极值。
    允许 1.6× 的余量（线程调度 + 合并开销 + 服务端调度抖动）。
    """
    import requests

    url = f"{throttled_server}/audio.m4a"
    size = len(_ThrottledHandler.payload)
    segments = cd.segment_count_for(size)

    with requests.Session() as session:
        getter = _getter(session)

        t0 = time.time()
        single = cd.stream_to_file(
            dst=str(tmp_path / "single.m4a"), get_response=getter,
            guess_total=size, url=url, allow_segmented=False,
        )
        single_elapsed = time.time() - t0

        t0 = time.time()
        multi = cd.stream_to_file(
            dst=str(tmp_path / "seg.m4a"), get_response=getter,
            guess_total=size, url=url,
        )
        seg_elapsed = time.time() - t0

    # 内容完全一致（加速不能以正确性为代价）
    assert open(single.path, "rb").read() == open(multi.path, "rb").read()

    speedup = single_elapsed / seg_elapsed
    theoretical_max = float(segments)

    print(
        f"\n  {size / 1024 / 1024:.1f}MB @{PER_CONNECTION_RATE / 1024 / 1024:.0f}MB/s/连接"
        f" → 单流 {single_elapsed:.2f}s，{segments} 段 {seg_elapsed:.2f}s，"
        f"加速 {speedup:.2f}x（理论上限 {theoretical_max:.0f}x）"
    )

    assert multi.segmented is True
    # 必须真正更快，且达到理论极值的 1/1.6 以上
    assert speedup > 1.5, f"加速仅 {speedup:.2f}x，分段没起作用"
    assert seg_elapsed < single_elapsed / 1.6
