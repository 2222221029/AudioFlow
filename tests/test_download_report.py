# -*- coding: utf-8 -*-
"""`core/download_report.py` 的行为锁定测试（移植分析 P1-9）。

参考实现里这条设计的**核心价值**写在 `DownloadEngine.cs:534-536`：

> 判据必须带上 `FailedTracks`：异常终止（引擎抛错）时只有计数、没有失败集，
> 光看 `_failed` 会渲染出一个**点了没反应**的按钮。

所以本文件重点是「有计数没清单 → 不给重试入口」这条。
"""

import json
import os

import pytest

from core import download_report as dr


class TestBuildReport:
    def test_core_fields(self):
        report = dr.build_report(
            album_id="123", album_title="测试专辑", platform="喜马拉雅",
            quality="无损优先", task_id="t-1", total=100, done=95, failed=5,
            total_bytes=1024 * 1024 * 500,
        )
        assert report["album_id"] == "123"
        assert report["album_title"] == "测试专辑"
        assert report["total"] == 100
        assert report["done"] == 95
        assert report["failed"] == 5
        assert report["bytes"] == 1024 * 1024 * 500
        assert report["schema"] == dr.REPORT_SCHEMA
        assert report["finishedAt"]

    def test_failed_items_normalized(self):
        report = dr.build_report(failed_items=[
            {"id": "x1", "order": 3, "title": "第三集", "error": "HTTP 502", "error_type": "transient"},
        ])
        item = report["failed_items"][0]
        assert item == {
            "id": "x1", "order": 3, "title": "第三集",
            "error": "HTTP 502", "error_type": "transient",
        }

    def test_failed_items_accepts_underscore_keys(self):
        """章节字典里既有 `_error` / `_error_type`（worker 内部字段）。"""
        report = dr.build_report(failed_items=[
            {"id": "x", "order": 1, "title": "t", "_error": "boom", "_error_type": "restricted"},
        ])
        item = report["failed_items"][0]
        assert item["error"] == "boom"
        assert item["error_type"] == "restricted"

    def test_non_dict_items_are_skipped(self):
        report = dr.build_report(failed_items=[{"id": "a"}, "junk", None])
        assert len(report["failed_items"]) == 1

    def test_long_fields_are_truncated(self):
        report = dr.build_report(failed_items=[
            {"id": "x", "order": 1, "title": "t" * 500, "error": "e" * 500},
        ])
        item = report["failed_items"][0]
        assert len(item["title"]) <= 200
        assert len(item["error"]) <= dr.MAX_ERROR_LEN

    def test_failed_items_capped(self):
        items = [{"id": str(i), "order": i, "title": "t"} for i in range(dr.MAX_FAILED_ENTRIES + 500)]
        report = dr.build_report(failed_items=items)
        assert len(report["failed_items"]) == dr.MAX_FAILED_ENTRIES

    def test_garbage_numbers_become_zero(self):
        report = dr.build_report(total="abc", done=None, failed=object())
        assert report["total"] == 0
        assert report["done"] == 0
        assert report["failed"] == 0


class TestWriteAndRead:
    def test_roundtrip(self, tmp_path):
        report = dr.build_report(album_id="a", done=3, failed=1, failed_items=[{"id": "x", "order": 9}])
        path = dr.write_report(str(tmp_path), report)
        assert path and os.path.exists(path)
        assert path.endswith(dr.REPORT_FILENAME)

        loaded = dr.read_report(str(tmp_path))
        assert loaded["album_id"] == "a"
        assert loaded["failed_items"][0]["order"] == 9

    def test_written_as_utf8_json(self, tmp_path):
        dr.write_report(str(tmp_path), dr.build_report(album_title="中文专辑名"))
        raw = open(os.path.join(str(tmp_path), dr.REPORT_FILENAME), encoding="utf-8").read()
        assert "中文专辑名" in raw          # 未被转义成 \uXXXX
        json.loads(raw)                      # 是合法 JSON

    def test_no_temp_file_left_behind(self, tmp_path):
        dr.write_report(str(tmp_path), dr.build_report())
        assert not any(n.endswith(".tmp") for n in os.listdir(str(tmp_path)))

    def test_atomic_overwrite(self, tmp_path):
        dr.write_report(str(tmp_path), dr.build_report(album_id="first"))
        dr.write_report(str(tmp_path), dr.build_report(album_id="second"))
        assert dr.read_report(str(tmp_path))["album_id"] == "second"

    def test_creates_directory(self, tmp_path):
        nested = os.path.join(str(tmp_path), "a", "b")
        path = dr.write_report(nested, dr.build_report())
        assert path and os.path.exists(path)

    def test_blank_dir_returns_none(self):
        assert dr.write_report("", dr.build_report()) is None

    def test_write_failure_is_swallowed(self, tmp_path):
        """侧车写失败**绝不能**让下载失败。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        assert dr.write_report(str(blocker / "sub"), dr.build_report()) is None


class TestReadRobustness:
    def test_missing_file(self, tmp_path):
        assert dr.read_report(str(tmp_path)) is None

    def test_corrupt_json(self, tmp_path):
        (tmp_path / dr.REPORT_FILENAME).write_text("{not json", encoding="utf-8")
        assert dr.read_report(str(tmp_path)) is None

    def test_non_object_json(self, tmp_path):
        (tmp_path / dr.REPORT_FILENAME).write_text("[1,2,3]", encoding="utf-8")
        assert dr.read_report(str(tmp_path)) is None

    def test_future_schema_is_refused(self, tmp_path):
        (tmp_path / dr.REPORT_FILENAME).write_text(
            json.dumps({"schema": 999, "failed": 5, "failed_items": [{"id": "x"}]}),
            encoding="utf-8",
        )
        assert dr.read_report(str(tmp_path)) is None

    def test_directory_instead_of_file(self, tmp_path):
        (tmp_path / dr.REPORT_FILENAME).mkdir()
        assert dr.read_report(str(tmp_path)) is None

    def test_failed_items_on_missing_report(self, tmp_path):
        assert dr.failed_items(str(tmp_path)) == []

    def test_failed_items_tolerates_wrong_type(self, tmp_path):
        dr.write_report(str(tmp_path), {"schema": dr.REPORT_SCHEMA, "failed": 1, "failed_items": "oops"})
        assert dr.failed_items(str(tmp_path)) == []


class TestHasRetryable:
    """核心判据：有计数没清单 = 死按钮，不给入口。"""

    def test_count_and_items_present(self):
        report = dr.build_report(failed=2, failed_items=[{"id": "a"}, {"id": "b"}])
        assert dr.has_retryable(report) is True

    def test_count_without_items_is_not_retryable(self):
        """这是参考实现明确记录过的死按钮形态。"""
        report = dr.build_report(failed=5, failed_items=[])
        assert dr.has_retryable(report) is False

    def test_items_without_count_is_not_retryable(self):
        report = dr.build_report(failed=0, failed_items=[{"id": "a"}])
        assert dr.has_retryable(report) is False

    def test_zero_failures(self):
        assert dr.has_retryable(dr.build_report(failed=0)) is False

    def test_none_report(self):
        assert dr.has_retryable(None) is False
        assert dr.has_retryable({}) is False


class TestRetrySelection:
    def test_selects_ids_and_orders(self):
        report = dr.build_report(failed=2, failed_items=[
            {"id": "c1", "order": 3, "error_type": "transient"},
            {"id": "c2", "order": 7, "error_type": "rate_limited"},
        ])
        selection = dr.retry_selection(report)
        assert selection["chapter_ids"] == ["c1", "c2"]
        assert selection["orders"] == [3, 7]
        assert selection["count"] == 2
        assert selection["error_types"] == ["rate_limited", "transient"]

    def test_returns_none_when_not_retryable(self):
        assert dr.retry_selection(dr.build_report(failed=3)) is None
        assert dr.retry_selection(None) is None

    def test_returns_none_when_no_usable_identifiers(self):
        report = dr.build_report(failed=1, failed_items=[{"title": "只有标题"}])
        assert dr.retry_selection(report) is None

    def test_order_only_items_still_selectable(self):
        report = dr.build_report(failed=1, failed_items=[{"order": 5, "title": "第五集"}])
        selection = dr.retry_selection(report)
        assert selection["orders"] == [5]
        assert selection["chapter_ids"] == []


class TestWorkerIntegration:
    """真实接入点：worker 结束时必须写出侧车。"""

    def test_worker_writes_report_with_failed_items(self, tmp_path):
        from core.download_worker import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)   # 绕开 QThread.__init__
        worker.download_dir = str(tmp_path)
        worker.album_title = "测试专辑"
        worker.album_id = "aid-1"
        worker.platform = "喜马拉雅"
        worker.quality = "无损优先"
        worker.task_id = "task-1"
        worker.chapters = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        worker.success_count = 2
        worker.failed_count = 1
        worker.failed_chapters = [
            {"id": "2", "order_num": 2, "title": "第二集", "_error": "HTTP 502", "_error_type": "transient"}
        ]
        worker._setting_enabled = lambda key, default=False: False
        worker._sanitize_filename = lambda name: str(name or "未知")
        worker._dbg = lambda msg: None
        worker._album_base_dir = lambda title: os.path.join(str(tmp_path), title)

        worker._write_download_report(state="partial")

        album_dir = os.path.join(str(tmp_path), "测试专辑")
        report = dr.read_report(album_dir)
        assert report is not None
        assert report["failed"] == 1
        assert report["done"] == 2
        assert report["state"] == "partial"
        assert report["failed_items"][0]["id"] == "2"
        assert dr.has_retryable(report) is True

    def test_worker_report_failure_does_not_raise(self, tmp_path):
        """写侧车失败时 worker 不能炸。"""
        from core.download_worker import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.download_dir = str(tmp_path)
        worker.album_title = "x"
        worker.album_id = "a"
        worker.platform = "p"
        worker.quality = "q"
        worker.task_id = "t"
        worker.chapters = []
        worker.success_count = 0
        worker.failed_count = 0
        worker.failed_chapters = []
        worker._setting_enabled = lambda key, default=False: False
        worker._sanitize_filename = lambda name: str(name or "未知")
        worker._dbg = lambda msg: None
        worker._album_base_dir = lambda title: "\0invalid\0"      # 必定写不进去

        worker._write_download_report(state="completed")          # 不该抛
