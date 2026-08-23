import builtins
import io
import logging
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from core import safe_logging


class SafeLoggingTests(unittest.TestCase):
    def test_context_and_emoji_level_are_formatted(self):
        with safe_logging.log_context(
            platform="喜马拉雅",
            operation="章节目录",
            album_id="104815790",
            page=2,
        ):
            line = safe_logging.format_log_line(
                "⚠️ 网页接口返回空结果",
                timestamp="2026-08-23 12:00:00",
            )

        self.assertEqual(
            line,
            "2026-08-23 12:00:00 WARN [喜马拉雅][章节目录] "
            "网页接口返回空结果 | album_id=104815790 page=2",
        )

    def test_nested_context_merges_and_resets(self):
        self.assertEqual(safe_logging.current_log_context(), {})
        with safe_logging.log_context(platform="酷我听书", operation="搜索", query="三体"):
            with safe_logging.log_context(operation="详情补全", album_id="42"):
                self.assertEqual(
                    safe_logging.current_log_context(),
                    {
                        "platform": "酷我听书",
                        "operation": "详情补全",
                        "query": "三体",
                        "album_id": "42",
                    },
                )
            self.assertEqual(safe_logging.current_log_context()["operation"], "搜索")
        self.assertEqual(safe_logging.current_log_context(), {})

    def test_legacy_message_infers_platform_and_operation(self):
        line = safe_logging.format_log_line(
            "🎧🎧 蜻蜓FM管理器已初始化",
            timestamp="2026-08-23 12:00:00",
        )
        self.assertEqual(
            line,
            "2026-08-23 12:00:00 INFO [蜻蜓FM][初始化] 蜻蜓FM管理器已初始化",
        )

    def test_redaction_and_log_level_filtering(self):
        line = safe_logging.format_log_line(
            "❌ token=1234567890123456 请求失败",
            context={"platform": "系统"},
            timestamp="2026-08-23 12:00:00",
        )
        self.assertIn("token=1234***3456", line)
        self.assertNotIn("1234567890123456", line)

        field_line = safe_logging.format_log_line(
            "请求完成",
            context={"platform": "系统", "token": "abcdefghijklmnop"},
            timestamp="2026-08-23 12:00:00",
        )
        self.assertIn("token=abcd***mnop", field_line)
        self.assertNotIn("abcdefghijklmnop", field_line)

        with patch.dict(os.environ, {"AUDIOFLOW_LOG_LEVEL": "ERROR"}):
            self.assertIsNone(safe_logging.format_log_line("✅ 普通信息"))
            self.assertIn("ERROR", safe_logging.format_log_line("❌ 请求失败"))

    def test_standard_logging_filter_uses_context(self):
        record = logging.LogRecord(
            name="audioflow",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="cookie=abcdefghijklmnop",
            args=(),
            exc_info=None,
        )
        with safe_logging.log_context(platform="荔枝FM", operation="音频地址", track_id="7"):
            self.assertTrue(safe_logging.ContextRedactingFilter().filter(record))
        self.assertEqual(record.levelname, "WARN")
        self.assertEqual(record.log_scope, "[荔枝FM][音频地址]")
        self.assertEqual(record.log_fields, " | track_id=7")
        self.assertIn("abcd***mnop", record.msg)

    def test_custom_file_print_keeps_plain_format_and_redacts(self):
        original_print = builtins.print
        original_installed = safe_logging._PRINT_INSTALLED
        try:
            safe_logging._PRINT_INSTALLED = False
            safe_logging.install_safe_print()
            output = io.StringIO()
            builtins.print("cookie=abcdefghijklmnop", "ok", sep=";", file=output)
            self.assertEqual(output.getvalue(), "cookie=abcd***mnop;ok\n")
        finally:
            builtins.print = original_print
            safe_logging._PRINT_INSTALLED = original_installed


class XimalayaCatalogLoggingTests(unittest.TestCase):
    def test_api_selection_emits_one_summary(self):
        from core.ximalaya_manager import XimalayaManager

        manager = XimalayaManager.__new__(XimalayaManager)
        api_results = {
            "old_api": [{"id": "1"}, {"id": "2"}],
            "new_api": [{"id": "1"}, {"id": "2"}],
            "web_api": [],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            chapters = manager._pick_best_chapter_list(api_results)

        text = output.getvalue()
        self.assertEqual(len(chapters), 2)
        self.assertEqual(text.count("章节接口选择完成"), 1)
        self.assertEqual(text.count("api_counts=old_api:2,new_api:2,web_api:0"), 1)
        self.assertNotIn("获取到 2 个章节", text)

    def test_wfp_detail_failure_is_a_recoverable_warning(self):
        from core.ximalaya_manager import XimalayaManager

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"ret": -1, "msg": "WFP存在但校验失败"}

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        manager = XimalayaManager.__new__(XimalayaManager)
        manager.api_url = "https://www.ximalaya.com"
        manager.session = Session()
        output = io.StringIO()
        with redirect_stdout(output):
            result = manager.get_album_detail("104815790")

        text = output.getvalue()
        self.assertIsNone(result)
        self.assertIn("WARN", text)
        self.assertIn("章节接口仍会继续尝试", text)
        self.assertNotIn("ERROR", text)

    def test_full_catalog_fallback_reports_progress_without_per_page_noise(self):
        from core.enhanced_search_manager import EnhancedSearchManager

        class XimalayaStub:
            def __init__(self):
                self.calls = []

            def get_album_chapters(self, _album_id, page, page_size, log_summary=True):
                self.calls.append((page, page_size, log_summary))
                if page_size > 1000:
                    return []
                if page < 10:
                    return [{"id": f"{page}-{index}"} for index in range(200)]
                if page == 10:
                    return [{"id": f"10-{index}"} for index in range(186)]
                return []

        manager = EnhancedSearchManager.__new__(EnhancedSearchManager)
        manager.ximalaya_manager = XimalayaStub()
        output = io.StringIO()
        with redirect_stdout(output):
            chapters = manager.get_album_chapters("104815790", "喜马拉雅")

        text = output.getvalue()
        self.assertEqual(len(chapters), 1986)
        self.assertEqual(text.count("大页加载不可用，改用稳定分页扫描"), 1)
        self.assertEqual(text.count("目录分页扫描进度"), 2)
        self.assertEqual(text.count("专辑目录加载完成"), 1)
        self.assertTrue(all(not call[2] for call in manager.ximalaya_manager.calls[1:]))


if __name__ == "__main__":
    unittest.main()
