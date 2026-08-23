import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from core.download_worker import DownloadWorker


class FakeCookieManager:
    def __init__(self):
        self.values = {}

    def get_cookie(self, key):
        return self.values.get(key, "")

    def set_cookie(self, key, value):
        self.values[key] = value


class DownloadWorkerTest(unittest.TestCase):
    def make_worker(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return DownloadWorker(
            chapters=[{"id": "1", "title": "第一章"}, {"id": "2", "title": "第二章"}],
            download_dir=tmp.name,
            quality="M4A 96K",
            album_title="测试专辑",
            album_id="album-1",
            platform="喜马拉雅",
            task_id="task-1",
        )

    def test_pause_resume_stop_flags(self):
        worker = self.make_worker()
        self.assertFalse(worker._is_paused)
        self.assertFalse(worker._is_stopped)
        worker.pause()
        self.assertTrue(worker._is_paused)
        worker.resume()
        self.assertFalse(worker._is_paused)
        worker.stop()
        self.assertTrue(worker._is_stopped)
        self.assertFalse(worker._is_paused)

    def test_progress_callback_emits_bounded_percent(self):
        worker = self.make_worker()
        events = []
        worker.realtime_progress_updated.connect(lambda *args: events.append(args))
        callback = worker._make_progress_callback(1)
        callback(50, 100)
        self.assertTrue(events)
        task_id, completed, total, percent = events[-1]
        self.assertEqual(task_id, "task-1")
        self.assertEqual(completed, 0)
        self.assertEqual(total, 2)
        self.assertGreaterEqual(percent, 0)
        self.assertLessEqual(percent, 100)

    def test_platform_album_directory_is_optional(self):
        worker = self.make_worker()
        worker.cookie_manager = FakeCookieManager()
        worker.platform = "喜马拉雅"
        worker.cookie_manager.set_cookie("organize_by_platform_enabled", "false")
        self.assertTrue(worker._album_base_dir("鬼吹灯").endswith("鬼吹灯"))
        self.assertFalse(worker._album_base_dir("鬼吹灯").endswith(os.path.join("喜马拉雅", "鬼吹灯")))

        worker.cookie_manager.set_cookie("organize_by_platform_enabled", "true")
        self.assertTrue(worker._album_base_dir("鬼吹灯").endswith(os.path.join("喜马拉雅", "鬼吹灯")))

    def test_filename_prefix_formats(self):
        worker = self.make_worker()
        worker.cookie_manager = FakeCookieManager()
        worker.cookie_manager.set_cookie("filename_prefix_format", "0001-")
        self.assertEqual(worker._format_filename_prefix(7), "0007-")
        worker.cookie_manager.set_cookie("filename_prefix_format", "001.")
        self.assertEqual(worker._format_filename_prefix(7), "007.")
        worker.cookie_manager.set_cookie("filename_prefix_format", "none")
        self.assertEqual(worker._format_filename_prefix(7), "")

    def test_ximalaya_lossless_extension_is_isolated(self):
        worker = self.make_worker()
        self.assertEqual(worker._ximalaya_extension_for_quality("无损真人录制"), ".flac")
        self.assertEqual(worker._ximalaya_extension_for_quality("LOSSLESS"), ".flac")
        self.assertEqual(worker._ximalaya_extension_for_quality("M4A 96K"), ".m4a")
        self.assertEqual(worker._ximalaya_extension_for_quality("MP3 64K"), ".mp3")
        self.assertEqual(worker._ximalaya_extension_for_quality("杜比全景声"), ".m4a")
        self.assertEqual(worker._ximalaya_extension_for_quality("Audio Vivid 菁彩声"), ".m4a")
        # 网页 V3 默认使用 .m4a 占位，下载后会按实际媒体容器修正后缀。
        self.assertEqual(worker._ximalaya_extension_for_quality("喜马拉雅网页版接口"), ".m4a")
        self.assertEqual(worker._ximalaya_extension_for_quality("喜马拉雅移动端接口（自动最高音质）"), ".m4a")
        self.assertTrue(worker._is_ximalaya_mobile_premium_quality("DOLBY_ATMOS"))
        self.assertTrue(worker._is_ximalaya_mobile_premium_quality("AUDIO_VIVID"))
        for quality in (
            "喜马拉雅移动端接口（自动最高音质）",
            "杜比全景声",
            "Audio Vivid 菁彩声",
            "无损真人录制",
            "M4A 128K",
            "M4A 64K",
            "M4A 24K",
            "杜比全景声优先（自动降级）",
            "Audio Vivid 优先（自动降级）",
            "无损优先（自动降级）",
        ):
            self.assertTrue(worker._is_ximalaya_mobile_v4_quality(quality), quality)
        self.assertFalse(worker._is_ximalaya_mobile_v4_quality("M4A 96K"))

    def test_ximalaya_special_qualities_add_friendly_filename_markers(self):
        worker = self.make_worker()
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落", "杜比全景声"),
            "003 失落 [杜比全景声]",
        )
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落", "Audio Vivid 菁彩声"),
            "003 失落 [Audio Vivid]",
        )
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落", "无损真人录制"),
            "003 失落 [无损]",
        )
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落", "M4A 128K"),
            "003 失落",
        )
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落", "杜比全景声优先（自动降级）"),
            "003 失落",
        )
        self.assertEqual(
            worker._ximalaya_marked_chapter_title("003 失落 [杜比全景声]", "杜比全景声"),
            "003 失落 [杜比全景声]",
        )

    def test_ximalaya_dolby_marker_is_used_in_download_path(self):
        worker = self.make_worker()
        worker.quality = "杜比全景声"
        worker.cookie_manager = FakeCookieManager()
        manager = mock.MagicMock()
        manager.download_audio.return_value = True
        worker.search_manager = mock.MagicMock()
        worker.search_manager.ximalaya_manager = manager

        ok = worker._download_single_chapter(
            {"id": "893621590", "title": "003 失落", "order_num": 3}, 1
        )

        self.assertTrue(ok)
        save_path = manager.download_audio.call_args.args[1]
        self.assertEqual(os.path.basename(save_path), "0003-003 失落 [杜比全景声].m4a")

    def test_ximalaya_failure_log_includes_v4_error(self):
        worker = self.make_worker()
        worker.quality = "喜马拉雅移动端接口（自动最高音质）"
        manager = mock.MagicMock()
        manager.download_audio.return_value = False
        manager.get_thread_download_result.return_value = {
            "error": "喜马拉雅无损音质接口拒绝请求: 凭证验证失败 (ret=1001)",
            "error_type": "rate_limited",
        }
        worker.search_manager = mock.MagicMock()
        worker.search_manager.ximalaya_manager = manager
        chapter = {"id": "893621590", "title": "003 失落", "order_num": 3}

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ok = worker._download_single_chapter(chapter, 1)

        self.assertFalse(ok)
        self.assertIn("凭证验证失败 (ret=1001)", output.getvalue())
        self.assertEqual(chapter["_error_type"], "rate_limited")

    def test_ximalaya_skip_url_fallback_covers_web_endpoint_default(self):
        worker = self.make_worker()
        # 网页版接口内部已有 Web V3/公开免费回退，外层不应再次下载同一章节。
        self.assertTrue(worker._ximalaya_skip_url_fallback("喜马拉雅网页版接口"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("M4A 96K"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("喜马拉雅移动端接口（自动最高音质）"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("无损真人录制"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("M4A 128K"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("M4A 64K"))
        self.assertTrue(worker._ximalaya_skip_url_fallback("M4A 24K"))
        # 非 V4 普通档位保持旧行为：失败后可尝试解析 URL 兜底。
        self.assertFalse(worker._ximalaya_skip_url_fallback("M4A 48K"))
        self.assertFalse(worker._ximalaya_skip_url_fallback("MP3 64K"))

    def test_lrts_branch_calls_download_audio_without_chapter_id(self):
        from core.lrts_manager import LRTSManager

        worker = self.make_worker()
        worker.platform = '懒人听书'
        worker.album_id = "book-1"
        worker.search_manager = mock.MagicMock()
        worker.search_manager.lrts_manager = None
        chapter = {"id": "t1", "title": "第一章"}

        with mock.patch.object(LRTSManager, "get_audio_url", return_value="https://example.com/a.m4a"), mock.patch.object(
            LRTSManager, "download_audio", return_value=True
        ) as dl:
            ok = worker._download_single_chapter(chapter, 1)

        self.assertTrue(ok)
        # 修复前该分支误传 chapter_id=...，LRTSManager 签名不接受，必然 TypeError。
        self.assertNotIn("chapter_id", dl.call_args.kwargs)
        self.assertEqual(dl.call_args.args[:2], ("https://example.com/a.m4a", mock.ANY))

    def test_fanqie_manager_is_reused_within_worker_thread(self):
        from core.fanqie_manager import FanqieManager

        worker = self.make_worker()
        worker.platform = '番茄畅听'
        worker.search_manager = mock.MagicMock()
        manager = mock.MagicMock()

        with mock.patch.object(FanqieManager, "__new__", return_value=manager) as create:
            first = worker._make_thread_manager('番茄畅听')
            second = worker._make_thread_manager('番茄畅听')

        self.assertIs(first, manager)
        self.assertIs(second, manager)
        create.assert_called_once()

    def test_restricted_download_is_not_retried(self):
        worker = self.make_worker()
        chapter = {"id": "1", "title": "第一章"}

        def denied(item, _index):
            item["_error"] = "移动端凭证不可用"
            item["_error_type"] = "restricted"
            return False

        with mock.patch.object(worker, "_download_single_chapter", side_effect=denied) as download:
            result = worker._download_chapter_with_retry(chapter, 1)

        self.assertFalse(result)
        self.assertEqual(download.call_count, 1)

    def test_unavailable_exact_quality_is_not_retried(self):
        worker = self.make_worker()
        worker.platform = "喜马拉雅"
        worker.quality = "杜比全景声"
        chapter = {"id": "1", "title": "第一章"}

        def unavailable(item, _index):
            item["_error"] = "该音频没有杜比全景声"
            item["_error_type"] = "quality_unavailable"
            return False

        with mock.patch.object(worker, "_download_single_chapter", side_effect=unavailable) as download:
            result = worker._download_chapter_with_retry(chapter, 1)

        self.assertFalse(result)
        self.assertEqual(download.call_count, 1)

    def test_dynamic_quality_result_is_renamed_with_actual_marker(self):
        worker = self.make_worker()
        with tempfile.TemporaryDirectory() as tmp:
            original = os.path.join(tmp, "0003-003 失落.wav")
            with open(original, "wb") as output:
                output.write(b"RIFF" + b"audio" * 300)

            final_path = worker._finalize_ximalaya_download_path(original, {
                "source": "mobile_v4_lossless",
                "path": original,
            })

            self.assertEqual(os.path.basename(final_path), "0003-003 失落 [无损].wav")
            self.assertTrue(os.path.exists(final_path))
            self.assertFalse(os.path.exists(original))

    def test_v4_rate_limit_uses_longer_backoff_and_retries(self):
        worker = self.make_worker()
        chapter = {"id": "1", "title": "第一章"}

        def first_limited_then_ok(item, _index):
            if "_attempted" not in item:
                item["_attempted"] = True
                item["_error"] = "ret=1001"
                item["_error_type"] = "rate_limited"
                return False
            return True

        with mock.patch.object(worker, "_download_single_chapter", side_effect=first_limited_then_ok), mock.patch(
            "core.download_worker.time.sleep"
        ) as sleep:
            result = worker._download_chapter_with_retry(chapter, 1)

        self.assertTrue(result)
        sleep.assert_any_call(15)

    def test_chapter_retry_emits_downloading_state(self):
        worker = self.make_worker()
        chapter = {"id": "1", "title": "第一章"}
        events = []
        worker.chapter_status_updated.connect(lambda *args: events.append(args))

        with mock.patch.object(worker, "_download_single_chapter", return_value=True):
            result = worker._download_chapter_with_retry(chapter, 1)

        self.assertTrue(result)
        self.assertEqual(events, [("task-1", chapter, "downloading")])


if __name__ == "__main__":
    unittest.main()
