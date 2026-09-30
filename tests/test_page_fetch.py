# -*- coding: utf-8 -*-
"""`core/page_fetch.py` 的行为锁定测试。

重点锁定移植分析 P1-6 的三条约束：
  ① 页号必须随结果返回，不靠完成顺序推断；
  ② 单页失败只留空洞，不毁整本；
  ③ 重试粒度 = 单页。
"""

import threading
import time

import pytest

from core import page_fetch
from core.errors import PermissionDenied, TransientError


def _slow(value, delay):
    def _fn():
        time.sleep(delay)
        return value

    return _fn


class TestPageCount:
    @pytest.mark.parametrize(
        "total,size,expected",
        [(671, 200, 4), (200, 200, 1), (201, 200, 2), (0, 200, 0), (100, 0, 0), (-5, 10, 0)],
    )
    def test_page_count(self, total, size, expected):
        assert page_fetch.page_count(total, size) == expected

    def test_page_count_tolerates_junk(self):
        assert page_fetch.page_count(None, 200) == 0
        assert page_fetch.page_count("abc", 200) == 0


class TestSlotIsPageNumber:
    """约束①：结果按页号对齐，与完成顺序无关。"""

    def test_out_of_order_completion_is_realigned(self):
        # 第 1 页最慢，其它页飞快 —— 若按完成顺序 AddRange，第 1 页会跑到最后。
        delays = {1: 0.15, 2: 0.01, 3: 0.01, 4: 0.01, 5: 0.01}

        def fetch(page):
            time.sleep(delays.get(page, 0))
            return [f"p{page}"]

        pages = page_fetch.fetch_all(5, fetch, concurrency=5)
        assert pages == [["p1"], ["p2"], ["p3"], ["p4"], ["p5"]]
        assert page_fetch.flatten(pages) == ["p1", "p2", "p3", "p4", "p5"]

    def test_tuple_page_types_flatten_with_selector(self):
        def fetch(page):
            return ([f"c{page}"], 100)

        pages = page_fetch.fetch_all(3, fetch, concurrency=3)
        assert page_fetch.flatten(pages, lambda p: p[0]) == ["c1", "c2", "c3"]

    def test_result_length_always_equals_total_pages(self):
        pages = page_fetch.fetch_all(7, lambda p: [p], concurrency=2)
        assert len(pages) == 7


class TestSinglePageFailureIsolated:
    """约束②：一页抖动不该让整本失败。"""

    def test_failed_page_becomes_hole_not_exception(self):
        def fetch(page):
            if page == 3:
                raise ValueError("boom")
            return [f"p{page}"]

        pages = page_fetch.fetch_all(5, fetch, concurrency=3, retries=1)
        assert len(pages) == 5
        assert pages[2] is None
        assert page_fetch.missing_pages(pages) == [3]
        assert page_fetch.flatten(pages) == ["p1", "p2", "p4", "p5"]

    def test_permission_denied_does_not_explode_the_batch(self):
        def fetch(page):
            if page == 2:
                raise PermissionDenied("需要购买")
            return [f"p{page}"]

        pages = page_fetch.fetch_all(4, fetch, concurrency=4, retries=3)
        assert pages[1] is None
        assert page_fetch.flatten(pages) == ["p1", "p3", "p4"]

    def test_permission_denied_is_not_retried(self):
        calls = {"n": 0}

        def fetch(page):
            calls["n"] += 1
            raise PermissionDenied("下架")

        page_fetch.fetch_all(1, fetch, concurrency=1, retries=5)
        assert calls["n"] == 1

    def test_page_error_hook_receives_page_number(self):
        seen = []

        def fetch(page):
            if page == 2:
                raise ValueError("boom")
            return [page]

        page_fetch.fetch_all(3, fetch, concurrency=2, retries=1, on_page_error=lambda p, e: seen.append((p, type(e).__name__)))
        assert (2, "ValueError") in seen


class TestPerPageRetry:
    """约束③：重试粒度 = 单页。"""

    def test_transient_page_is_retried_individually(self):
        attempts = {}

        def fetch(page):
            attempts[page] = attempts.get(page, 0) + 1
            if page == 2 and attempts[page] < 2:
                raise TransientError("HTTP 502")
            return [f"p{page}"]

        pages = page_fetch.fetch_all(3, fetch, concurrency=3, retries=3)
        assert pages == [["p1"], ["p2"], ["p3"]]
        assert attempts[2] == 2
        assert attempts[1] == 1  # 其它页不受影响，不重跑

    def test_exhausted_retries_leave_hole(self):
        calls = {"n": 0}

        def fetch(page):
            calls["n"] += 1
            raise TransientError("HTTP 503")

        pages = page_fetch.fetch_all(2, fetch, concurrency=2, retries=3)
        assert pages == [None, None]
        assert calls["n"] == 6  # 2 页 × 3 次


class TestFirstPageReuse:
    def test_first_page_result_is_not_refetched(self):
        fetched = []

        def fetch(page):
            fetched.append(page)
            return [page]

        pages = page_fetch.fetch_all(4, fetch, concurrency=3, first_page=["probe"])
        assert pages[0] == ["probe"]
        assert 1 not in fetched
        assert sorted(fetched) == [2, 3, 4]

    def test_single_page_with_first_page_skips_network_entirely(self):
        pages = page_fetch.fetch_all(1, lambda p: pytest.fail("不该发请求"), first_page=[1])
        assert pages == [[1]]

    def test_zero_pages_returns_empty(self):
        assert page_fetch.fetch_all(0, lambda p: pytest.fail("不该发请求")) == []


class TestProgress:
    def test_progress_counts_successful_pages_only(self):
        seen = []

        def fetch(page):
            if page == 2:
                raise ValueError("boom")
            return [page]

        page_fetch.fetch_all(3, fetch, concurrency=2, retries=1, on_progress=seen.append)
        assert seen[-1] == 2  # 3 页里成功 2 页

    def test_progress_callback_exception_does_not_break_fetch(self):
        def on_progress(_n):
            raise RuntimeError("UI 挂了")

        pages = page_fetch.fetch_all(3, lambda p: [p], concurrency=2, on_progress=on_progress)
        assert pages == [[1], [2], [3]]


class TestCancellation:
    def test_cancel_stops_submitting_and_leaves_holes(self):
        cancel = threading.Event()
        started = []

        def fetch(page):
            started.append(page)
            if len(started) >= 2:
                cancel.set()
            time.sleep(0.05)
            return [page]

        pages = page_fetch.fetch_all(50, fetch, concurrency=2, is_cancelled=cancel.is_set, retries=1)
        assert len(pages) == 50
        assert all(p is None for p in pages[4:])   # 后面的页没有提交
        assert any(p is not None for p in pages)   # 已开始的页正常落位

    def test_cancel_before_start_does_nothing(self):
        pages = page_fetch.fetch_all(
            10, lambda p: pytest.fail("不该发请求"), is_cancelled=lambda: True
        )
        assert pages == [None] * 10


class TestFlattenAndDedupe:
    def test_flatten_skips_none_pages(self):
        assert page_fetch.flatten([None, [1, 2], None, [3]]) == [1, 2, 3]

    def test_flatten_keeps_empty_dict_pages(self):
        # dict 页是合法页类型（有些平台的页结果就是 dict），不能被当空洞丢掉
        assert page_fetch.flatten([{}, {"a": 1}]) == [{}, {"a": 1}]

    def test_flatten_without_selector_on_non_iterable_pages(self):
        assert page_fetch.flatten(["a", "b"]) == ["a", "b"]

    def test_flatten_handles_none_and_empty(self):
        assert page_fetch.flatten(None) == []
        assert page_fetch.flatten([]) == []

    def test_dedupe_by_key_keeps_first_occurrence_order(self):
        items = [{"id": 1}, {"id": 2}, {"id": 1}, {"id": 3}, {"id": 2}]
        out = page_fetch.dedupe_by_key(items, lambda x: x["id"])
        assert [x["id"] for x in out] == [1, 2, 3]

    def test_dedupe_keeps_items_without_key(self):
        items = [{"id": ""}, {"id": 1}, {"id": 1}]
        out = page_fetch.dedupe_by_key(items, lambda x: x["id"])
        assert out == [{"id": ""}, {"id": 1}]

    def test_dedupe_tolerates_broken_key_function(self):
        items = [{"noid": 1}, {"noid": 2}]
        out = page_fetch.dedupe_by_key(items, lambda x: x["id"])
        assert out == items


class TestConcurrencyWindow:
    def test_never_exceeds_requested_concurrency(self):
        lock = threading.Lock()
        state = {"now": 0, "peak": 0}

        def fetch(page):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.02)
            with lock:
                state["now"] -= 1
            return [page]

        page_fetch.fetch_all(30, fetch, concurrency=4, retries=1)
        assert state["peak"] <= 4

    def test_concurrency_actually_parallelizes(self):
        def fetch(page):
            time.sleep(0.1)
            return [page]

        start = time.time()
        page_fetch.fetch_all(6, fetch, concurrency=6, retries=1)
        assert time.time() - start < 0.4   # 串行要 0.6s
