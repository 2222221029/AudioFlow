# -*- coding: utf-8 -*-
"""渠道契约测试（移植分析 P0-4 / W3 最后一项）。

## 背景：为什么不照搬参考实现

XimalayaApp `Core/DownloadEngine.cs:151-158` 是**硬性**的：

> 只走用户选定的那一个渠道 —— 绝不中途换渠道（2026-09-29 用户要求「每个接口独立」）

撞限额就抛 `ChannelQuotaException` 暂停整个任务。那对**人工盯着下载的桌面应用**
可行；但对 AudioFlow 这种**无人值守的 NAS 订阅下载器**不可行 ——
平台一限流，订阅就整夜空转，用户第二天只看到「全部失败」。

所以本项目采取第三条路：**保留兜底，但让降级可见**。
本文件锁定的是「判定必须准」—— 漏报会让用户不知道拿到了低档，
误报会让用户以为下载出了问题而白重下。
"""

import pytest

from core import channel_contract as cc


class TestSourceLabel:
    @pytest.mark.parametrize(
        "source,expected_fragment",
        [
            ("mobile_v4_level_13", "Audio Vivid"),
            ("mobile_v4_level_12", "杜比"),
            ("mobile_v4_lossless", "无损"),
            ("web_v3_lossless", "无损"),
            ("web_v3", "网页"),
            ("pc_download_v2_level_2", "电脑版"),
            ("legacy_web_redirect", "旧版"),
            ("public_base_info", "公开免费"),
        ],
    )
    def test_known_sources_have_readable_labels(self, source, expected_fragment):
        assert expected_fragment in cc.source_label(source)

    def test_source_with_suffix_is_matched_by_head(self):
        assert "公开免费" in cc.source_label("public_base_info:playUrl64")

    def test_unknown_source_falls_back_to_raw(self):
        assert cc.source_label("totally_new_thing") == "totally_new_thing"

    @pytest.mark.parametrize("source", ["", None])
    def test_blank_source(self, source):
        assert cc.source_label(source) == "未知来源"


class TestExpectedLevel:
    @pytest.mark.parametrize(
        "quality,expected",
        [
            ("无损优先", 3),
            ("无损优先（自动降级）", 3),
            ("网页无损优先（FHQ WAV）", 3),
            ("lossless", 3),
            ("杜比全景声优先（自动降级）", 4),
            ("Audio Vivid 优先（自动降级）", 5),
            ("96K", 2),
            ("64K", 1),
        ],
    )
    def test_explicit_preferences_have_expectations(self, quality, expected):
        assert cc.expected_level(quality) == expected

    @pytest.mark.parametrize(
        "quality",
        [
            "喜马拉雅移动端接口（自动最高音质）",
            "喜马拉雅网页版接口",
            "喜马拉雅电脑版接口（自动最高音质）",
            "best",
            "",
            None,
        ],
    )
    def test_auto_quality_has_no_expectation(self, quality):
        """用户没指定档位 → 拿到什么算什么，不算降级。"""
        assert cc.expected_level(quality) is None


class TestDowngradeJudgement:
    """核心判定：既要抓得住真降级，也不能误报。"""

    @pytest.mark.parametrize(
        "quality,source",
        [
            # 用户要无损，实际拿到无损 —— 正常
            ("无损优先", "mobile_v4_lossless"),
            ("无损优先", "web_v3_lossless"),
            # 用户要无损，实际拿到更高档 —— 不算降级
            ("无损优先", "mobile_v4_level_12"),
            ("无损优先", "mobile_v4_level_13"),
            # 用户没指定 —— 不判
            ("喜马拉雅移动端接口（自动最高音质）", "web_v3"),
            ("best", "public_base_info"),
            # 用户要 96K，实际拿到 96K 档 —— 正常
            ("96K", "pc_download_v2_level_2"),
        ],
    )
    def test_not_downgraded(self, quality, source):
        assert cc.describe_downgrade(quality, source) == ""

    @pytest.mark.parametrize(
        "quality,source",
        [
            # 用户要无损，实际只给网页 M4A（128K 档）—— 真降级
            ("无损优先", "web_v3"),
            # 用户要无损，实际掉到公开免费 —— 真降级
            ("无损优先", "public_base_info"),
            ("无损优先", "legacy_web_redirect"),
            # 用户要 96K，实际给公开免费 —— 真降级
            ("96K", "public_base_info:playUrl64"),
            # 用户要杜比，实际只给无损 —— 降一级
            ("杜比全景声优先（自动降级）", "mobile_v4_lossless"),
            # 用户要 Vivid，实际只给杜比 —— 降一级
            ("Audio Vivid 优先（自动降级）", "mobile_v4_level_12"),
        ],
    )
    def test_downgraded(self, quality, source):
        note = cc.describe_downgrade(quality, source)
        assert note, f"{quality} <- {source} 应判为降级"
        assert "低于所选" in note

    @pytest.mark.parametrize(
        "quality,source",
        [
            ("无损优先", "unknown_new_source"),      # 实际来源未知
            ("完全没见过的档位", "web_v3"),           # 所选档位未知
            ("", "web_v3"),
            (None, None),
        ],
    )
    def test_unknown_on_either_side_never_claims_downgrade(self, quality, source):
        """⚠ 任一侧未知就不下结论 —— 误报会让用户白重下。"""
        assert cc.describe_downgrade(quality, source) == ""

    def test_note_explains_reason(self):
        note = cc.describe_downgrade("无损优先", "web_v3")
        assert "无损优先" in note          # 说清用户选了什么
        assert "网页" in note              # 说清实际拿到了什么
        assert "自动" in note              # 说明是自动兜底，不是用户操作


class TestBuildNote:
    def test_downgraded_flag_is_set(self):
        note = cc.build_note("无损优先", "web_v3")
        assert note["downgraded"] is True
        assert note["source"] == "web_v3"
        assert note["quality"] == "无损优先"
        assert note["note"]

    def test_normal_download_has_no_flag(self):
        note = cc.build_note("无损优先", "mobile_v4_lossless")
        assert note["downgraded"] is False
        assert note["note"] == ""

    def test_extra_message_used_when_no_downgrade(self):
        """非降级场景下可带一条额外说明（例如「已跳过降码率回退」）。"""
        note = cc.build_note("96K", "pc_download_v2_level_2", extra="已跳过公开兜底")
        assert note["downgraded"] is False
        assert note["note"] == "已跳过公开兜底"

    def test_extra_never_overrides_real_downgrade(self):
        note = cc.build_note("无损优先", "web_v3", extra="随便什么")
        assert note["downgraded"] is True
        assert "低于所选" in note["note"]

    @pytest.mark.parametrize("source", ["", None])
    def test_blank_source_is_safe(self, source):
        note = cc.build_note("无损优先", source)
        assert note["source"] == ""
        assert note["downgraded"] is False


class TestSummarize:
    def test_empty_returns_empty_string(self):
        assert cc.summarize_downgrades([]) == ""
        assert cc.summarize_downgrades(None) == ""

    def test_counts_grouped_by_quality(self):
        items = [
            {"quality": "无损优先"}, {"quality": "无损优先"}, {"quality": "96K"},
        ]
        summary = cc.summarize_downgrades(items)
        assert "3 集" in summary
        assert "无损优先 × 2" in summary
        assert "96K × 1" in summary

    def test_ignores_non_dict_entries(self):
        summary = cc.summarize_downgrades([{"quality": "96K"}, "junk", None])
        assert "1 集" in summary

    def test_missing_quality_label(self):
        summary = cc.summarize_downgrades([{}])
        assert "未指定" in summary


class TestCollectFromReport:
    def test_reads_downgrades_from_report(self):
        report = {"downgrades": [{"id": "1"}, {"id": "2"}]}
        assert len(cc.collect_downgrades(report)) == 2

    def test_missing_or_wrong_type(self):
        assert cc.collect_downgrades({}) == []
        assert cc.collect_downgrades(None) == []
        assert cc.collect_downgrades({"downgrades": "oops"}) == []


class TestWorkerIntegration:
    """真实验收点：worker 必须把降级记进章节与侧车。"""

    def _worker(self, tmp_path, quality, chapters):
        from core.download_worker import DownloadWorker

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.download_dir = str(tmp_path)
        worker.album_title = "专辑"
        worker.album_id = "aid"
        worker.platform = "喜马拉雅"
        worker.quality = quality
        worker.task_id = "t1"
        worker.chapters = chapters
        worker.success_count = len(chapters)
        worker.failed_count = 0
        worker.failed_chapters = []
        worker._setting_enabled = lambda key, default=False: False
        worker._sanitize_filename = lambda name: str(name or "未知")
        worker._dbg = lambda msg: None
        worker._album_base_dir = lambda title: str(tmp_path / title)
        return worker

    def test_downgrade_is_recorded_on_chapter(self, tmp_path):
        from core.download_worker import DownloadWorker

        worker = self._worker(tmp_path, "无损优先", [{"id": "1", "order_num": 1, "title": "第一集"}])
        worker._progress_lock = __import__("threading").Lock()
        worker._channel_downgrades = []
        chapter = worker.chapters[0]

        worker._note_channel_downgrade(chapter, {"source": "web_v3"})
        assert chapter["_quality_note"]
        assert chapter["_quality_source"] == "web_v3"
        assert len(worker._channel_downgrades) == 1
        assert worker._channel_downgrades[0]["source_label"]

    def test_no_downgrade_records_nothing(self, tmp_path):
        from core.download_worker import DownloadWorker
        import threading

        worker = self._worker(tmp_path, "无损优先", [{"id": "1", "order_num": 1, "title": "第一集"}])
        worker._progress_lock = threading.Lock()
        worker._channel_downgrades = []
        chapter = worker.chapters[0]

        worker._note_channel_downgrade(chapter, {"source": "mobile_v4_lossless"})
        assert "_quality_note" not in chapter
        assert worker._channel_downgrades == []

    def test_report_contains_downgrades(self, tmp_path):
        import threading

        from core import download_report as dr

        worker = self._worker(
            tmp_path, "无损优先",
            [{"id": "1", "order_num": 1, "title": "第一集"}, {"id": "2", "order_num": 2, "title": "第二集"}],
        )
        worker._progress_lock = threading.Lock()
        worker._channel_downgrades = [
            {"id": "1", "order": 1, "title": "第一集", "quality": "无损优先",
             "source": "web_v3", "source_label": "网页 M4A（128K 档）", "note": "实际交付档位低于所选"}
        ]

        worker._write_download_report(state="completed")

        report = dr.read_report(str(tmp_path / "专辑"))
        assert report is not None
        assert len(report["downgrades"]) == 1
        assert "1 集" in report["downgrade_summary"]

    def test_report_still_written_without_lock_attribute(self, tmp_path):
        """⚠ 缺锁不能让整份侧车写失败（回归：曾因此让报告整份丢失）。"""
        from core import download_report as dr

        worker = self._worker(tmp_path, "无损优先", [{"id": "1", "order_num": 1, "title": "第一集"}])
        # 刻意不设 _progress_lock / _channel_downgrades
        worker.__dict__.pop("_progress_lock", None)
        worker._write_download_report(state="completed")

        assert dr.read_report(str(tmp_path / "专辑")) is not None

    def test_note_failure_does_not_break_download(self, tmp_path):
        worker = self._worker(tmp_path, "无损优先", [{"id": "1", "order_num": 1, "title": "第一集"}])
        # 不设 _progress_lock：_note_channel_downgrade 内部应吞掉异常
        worker._note_channel_downgrade(worker.chapters[0], {"source": "web_v3"})


class TestUiPropagation:
    """验收点：降级说明必须能到达前端。

    链路：worker 写 `chapter['_quality_note']`
        → `chapter_status_updated.emit(..., 'success')`
        → `update_download_chapter_status` 存进 `task['chapter_states']`
        → `album_chapter_download_states` 合并进章节行 → 前端。
    """

    def test_status_update_persists_quality_note(self):
        from src.server import web_server as ws

        task_id = "quality-note-test"
        chapter = {"id": "c1", "order_num": 1, "title": "第一集",
                   "_quality_note": "实际交付档位低于所选：所选「无损优先」，实际为「网页 M4A（128K 档）」",
                   "_quality_source": "web_v3"}
        with ws.task_lock:
            ws.tasks[task_id] = {
                "id": task_id, "title": "t", "chapters": [{"id": "c1", "order_num": 1, "title": "第一集"}],
                "chapter_states": {}, "success_chapters": [], "failed_chapters": [],
                "status": "running", "created_at": 0, "updated_at": 0,
            }
        try:
            ws.update_download_chapter_status(task_id, chapter, "success")
            state = ws.tasks[task_id]["chapter_states"]["c1"]
            assert state["status"] == "success"
            assert "低于所选" in state["quality_note"]
            assert state["quality_source"] == "web_v3"
        finally:
            with ws.task_lock:
                ws.tasks.pop(task_id, None)

    def test_normal_download_adds_no_quality_fields(self):
        from src.server import web_server as ws

        task_id = "quality-note-normal"
        chapter = {"id": "c2", "order_num": 2, "title": "第二集"}
        with ws.task_lock:
            ws.tasks[task_id] = {
                "id": task_id, "title": "t", "chapters": [chapter],
                "chapter_states": {}, "success_chapters": [], "failed_chapters": [],
                "status": "running", "created_at": 0, "updated_at": 0,
            }
        try:
            ws.update_download_chapter_status(task_id, chapter, "success")
            state = ws.tasks[task_id]["chapter_states"]["c2"]
            assert "quality_note" not in state
            assert "quality_source" not in state
        finally:
            with ws.task_lock:
                ws.tasks.pop(task_id, None)

    def test_counters_unaffected_by_quality_note(self):
        """⚠ 纯增量字段：绝不能影响既有计数。"""
        from src.server import web_server as ws

        task_id = "quality-note-counts"
        with ws.task_lock:
            ws.tasks[task_id] = {
                "id": task_id, "title": "t",
                "chapters": [{"id": "c1"}, {"id": "c2"}],
                "chapter_states": {}, "success_chapters": [], "failed_chapters": [],
                "status": "running", "created_at": 0, "updated_at": 0,
            }
        try:
            ws.update_download_chapter_status(
                task_id, {"id": "c1", "_quality_note": "降级了"}, "success"
            )
            task = ws.tasks[task_id]
            assert task["success"] == 1
            assert task["failed"] == 0
        finally:
            with ws.task_lock:
                ws.tasks.pop(task_id, None)
