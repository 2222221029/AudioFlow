# -*- coding: utf-8 -*-
"""喜马拉雅「并发分页」等价性测试。

移植分析 P1-6 的接入点。核心要证明的不是「更快」，而是**更快且结果等价**：

  ① 并发路径的结果必须与串行路径**逐条相同**（页号对齐 + 去重后）；
  ② 任何一页失败 → **整体放弃并发结果**，回退串行，绝不产出不完整章节表；
  ③ 前置条件不满足（页数太少 / 拿不到总数）→ 直接返回 None 走原路径，
     即「并发是加速手段，不是新增故障源」。

这些用例全部用假 manager 打桩，不发真实网络请求。
"""

from unittest.mock import patch

import pytest

from core.ximalaya_manager import XimalayaManager


def _chapters(start, count, prefix="c"):
    return [{"id": f"{prefix}{i}", "title": f"第{i}集"} for i in range(start, start + count)]


class _FakeManager(XimalayaManager):
    """只覆写 `get_album_chapters_page`，其余（含 `_chapter_identity`）复用真实实现。

    ⚠ 继承真实类而不是裸对象：`_chapter_identity` 是并发分页的去重依据，
    测试必须验证**生产代码用的那个实现**，不能自己另写一份。
    """

    def __init__(self, pages, fail_pages=()):
        # 刻意不调用父类 __init__：它会建 session / 读 cookie，测试不需要
        self.pages = pages                # {page_number: [chapters]}
        self.fail_pages = set(fail_pages)
        self.calls = []

    def get_album_chapters_page(self, album_id, page=1, page_size=200, log_summary=True):
        self.calls.append(page)
        if page in self.fail_pages:
            raise RuntimeError(f"page {page} blew up")
        return list(self.pages.get(page, [])), 0


def _run(mgr, first_page, first_number, page_size, exact_total):
    return XimalayaManager._fetch_chapters_pages_concurrent(
        mgr,
        album_id="a1",
        first_page_chapters=first_page,
        first_page_number=first_number,
        page_size=page_size,
        exact_total=exact_total,
        log_summary=False,
    )


class TestEquivalenceWithSerial:
    def test_result_matches_serial_layout(self):
        pages = {
            1: _chapters(1, 200),
            2: _chapters(201, 200),
            3: _chapters(401, 200),
            4: _chapters(601, 71),
        }
        mgr = _FakeManager(pages)
        first = pages[1]

        result = _run(mgr, first, 1, 200, 671)

        assert result is not None
        expected = pages[1] + pages[2] + pages[3] + pages[4]
        assert [c["id"] for c in result] == [c["id"] for c in expected]

    def test_pages_are_reordered_not_append_ordered(self):
        """核心回归：即使后面的页先返回，结果也必须按页号排列。

        参考实现记录过酷我「并发响应错配且 success 仍为 true」的事故，
        根因就是按到达顺序 AddRange。
        """
        import time

        pages = {1: _chapters(1, 10), 2: _chapters(11, 10), 3: _chapters(21, 10), 4: _chapters(31, 10)}
        mgr = _FakeManager(pages)
        first = pages[1]

        original = mgr.get_album_chapters_page

        def slow_first_requested_page(album_id, page=1, page_size=200, log_summary=True):
            # 让第 2 页最慢：若实现按完成顺序拼接，第 2 页就会排到最后
            if page == 2:
                time.sleep(0.2)
            return original(album_id, page=page, page_size=page_size, log_summary=log_summary)

        mgr.get_album_chapters_page = slow_first_requested_page
        result = _run(mgr, first, 1, 10, 40)

        assert [c["id"] for c in result] == [f"c{i}" for i in range(1, 41)]

    def test_concurrent_path_is_actually_used(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 7)}
        mgr = _FakeManager(pages)
        result = _run(mgr, pages[1], 1, 10, 60)
        assert result is not None
        assert sorted(mgr.calls) == [2, 3, 4, 5, 6]


class TestHoleFallsBackToSerial:
    """约束②：有缺页就整体放弃，绝不产出不完整的章节表。"""

    def test_any_failed_page_returns_none(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 7)}
        mgr = _FakeManager(pages, fail_pages={4})
        assert _run(mgr, pages[1], 1, 10, 60) is None

    def test_empty_page_is_treated_as_hole(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 7)}
        pages[3] = []
        mgr = _FakeManager(pages)
        assert _run(mgr, pages[1], 1, 10, 60) is None

    def test_transient_failure_is_retried_before_giving_up(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 5)}
        mgr = _FakeManager(pages)
        attempts = {"n": 0}
        original = mgr.get_album_chapters_page

        def flaky(album_id, page=1, page_size=200, log_summary=True):
            if page == 3:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("one-off blip")
            return original(album_id, page=page, page_size=page_size, log_summary=log_summary)

        mgr.get_album_chapters_page = flaky
        result = _run(mgr, pages[1], 1, 10, 40)
        assert result is not None
        assert attempts["n"] == 2
        assert [c["id"] for c in result] == [f"c{i}" for i in range(1, 41)]


class TestPreconditionGuards:
    """约束③：前置条件不满足就直接返回 None（走原路径）。"""

    @pytest.mark.parametrize(
        "first_page,first_number,page_size,exact_total,why",
        [
            (_chapters(1, 10), 1, 10, 10, "单页专辑"),
            (_chapters(1, 10), 1, 10, 15, "首页之后只剩半页，并发无意义"),
            (_chapters(1, 10), 1, 0, 100, "page_size 非法"),
            (_chapters(1, 10), 1, 10, 0, "拿不到总数"),
            (_chapters(1, 10), 1, 10, -5, "总数为负"),
        ],
    )
    def test_guard_returns_none(self, first_page, first_number, page_size, exact_total, why):
        mgr = _FakeManager({})
        assert _run(mgr, first_page, first_number, page_size, exact_total) is None, why
        assert mgr.calls == [], "不应该发出任何请求"

    def test_blank_album_id_returns_none(self):
        mgr = _FakeManager({})
        assert XimalayaManager._fetch_chapters_pages_concurrent(
            mgr,
            album_id="",
            first_page_chapters=_chapters(1, 10),
            first_page_number=1,
            page_size=10,
            exact_total=100,
            log_summary=False,
        ) is None

    def test_two_page_album_stays_serial(self):
        """只剩 1 页要抓时不并发 —— 省不下多少却多担一份风险。"""
        mgr = _FakeManager({1: _chapters(1, 10)})
        assert _run(mgr, _chapters(1, 10), 1, 10, 20) is None
        assert mgr.calls == []


class TestDedupe:
    def test_cross_page_duplicates_are_removed_keeping_first(self):
        pages = {
            1: _chapters(1, 10),
            2: _chapters(11, 10),
            3: _chapters(6, 10),    # 服务端会话导致重发第 6~15 集
            4: _chapters(31, 10),
        }
        mgr = _FakeManager(pages)
        result = _run(mgr, pages[1], 1, 10, 40)
        ids = [c["id"] for c in result]
        assert len(ids) == len(set(ids)), "去重后不应有重复"
        assert ids[:10] == [f"c{i}" for i in range(1, 11)]
        assert ids[-10:] == [f"c{i}" for i in range(31, 41)]

    def test_dedupe_happens_after_page_ordering(self):
        """去重必须在页号重排之后，否则留下的是错误的那一份。"""
        pages = {
            1: [{"id": "x", "title": "首页版本"}],
            2: [{"id": "x", "title": "重复版本"}, {"id": "y", "title": "新集"}],
            3: [{"id": "z", "title": "再一集"}],
        }
        mgr = _FakeManager(pages)
        result = _run(mgr, pages[1], 1, 1, 3)
        assert result[0]["title"] == "首页版本"
        assert [c["id"] for c in result] == ["x", "y", "z"]

    def test_chapters_without_id_are_kept(self):
        pages = {
            1: [{"title": "无 id 的集"}],
            2: [{"title": "另一个无 id 的集"}],
            3: [{"id": "c1", "title": "有 id"}],
        }
        mgr = _FakeManager(pages)
        result = _run(mgr, pages[1], 1, 1, 3)
        assert len(result) == 3


class TestIdentityHelper:
    @pytest.mark.parametrize(
        "chapter,expected",
        [
            ({"id": " 42 "}, "42"),
            ({"track_id": 42}, "42"),
            ({"trackId": "abc"}, "abc"),
            ({"chapter_id": "z"}, "z"),
            ({"id": "1", "track_id": "2"}, "1"),
            ({"title": "无 id"}, ""),
            ({}, ""),
        ],
    )
    def test_identity_extraction(self, chapter, expected):
        assert XimalayaManager._chapter_identity(chapter) == expected

    def test_non_dict_is_safe(self):
        assert XimalayaManager._chapter_identity(None) == ""
        assert XimalayaManager._chapter_identity("str") == ""


class TestConcurrencyEnvOverride:
    def test_env_var_is_clamped(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 7)}
        mgr = _FakeManager(pages)
        with patch.dict("os.environ", {"XMLY_CHAPTER_PAGE_CONCURRENCY": "999"}):
            result = _run(mgr, pages[1], 1, 10, 60)
        assert result is not None
        assert [c["id"] for c in result] == [f"c{i}" for i in range(1, 61)]

    def test_garbage_env_var_falls_back_to_default(self):
        pages = {i: _chapters((i - 1) * 10 + 1, 10) for i in range(1, 7)}
        mgr = _FakeManager(pages)
        with patch.dict("os.environ", {"XMLY_CHAPTER_PAGE_CONCURRENCY": "not-a-number"}):
            result = _run(mgr, pages[1], 1, 10, 60)
        assert result is not None
