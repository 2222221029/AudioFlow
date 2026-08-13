import os
import tempfile
import unittest

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
        self.assertTrue(worker._is_ximalaya_mobile_premium_quality("DOLBY_ATMOS"))
        self.assertTrue(worker._is_ximalaya_mobile_premium_quality("AUDIO_VIVID"))


if __name__ == "__main__":
    unittest.main()
