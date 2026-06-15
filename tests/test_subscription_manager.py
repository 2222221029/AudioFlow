import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.subscription_manager as subscription_module
from core.subscription_manager import SubscriptionManager


class SubscriptionManagerTest(unittest.TestCase):
    def test_diff_detects_deleted_local_chapter_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as config_tmp, tempfile.TemporaryDirectory() as download_tmp:
            manager = SubscriptionManager(config_tmp)
            album = {"id": "album-1", "title": "鬼吹灯", "platform": "喜马拉雅"}
            chapters = [
                {"id": "1", "title": "第一章", "order_num": 1},
                {"id": "2", "title": "第二章", "order_num": 2},
            ]
            subscription = manager.add_or_update(album, chapters, download_tmp)
            album_dir = Path(download_tmp) / "鬼吹灯"
            album_dir.mkdir(parents=True)
            first = album_dir / "0001-第一章.m4a"
            second = album_dir / "0002-第二章.m4a"
            first.write_bytes(b"a" * 2048)
            second.write_bytes(b"b" * 2048)

            initial = manager.diff_chapters(subscription, chapters, download_tmp)
            self.assertEqual(initial["missing"], [])

            second.unlink()
            diff = manager.diff_chapters(subscription, chapters, download_tmp)

            self.assertEqual(len(diff["missing"]), 1)
            self.assertEqual(diff["missing"][0]["id"], "2")
            self.assertEqual(diff["file_missing_count"], 1)

    def test_diff_uses_fresh_audio_index_without_directory_scan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as config_tmp, tempfile.TemporaryDirectory() as download_tmp:
            manager = SubscriptionManager(config_tmp)
            album = {"id": "album-1", "title": "Ghost", "platform": "Ximalaya"}
            chapters = [
                {"id": "1", "title": "First", "order_num": 1},
                {"id": "2", "title": "Second", "order_num": 2},
            ]
            subscription = manager.add_or_update(album, chapters, download_tmp)
            album_dir = Path(download_tmp) / "Ximalaya" / "Ghost"
            album_dir.mkdir(parents=True)
            (album_dir / "0001-First.m4a").write_bytes(b"a" * 2048)
            (album_dir / "0002-Second.m4a").write_bytes(b"b" * 2048)
            manager.build_audio_index(download_tmp, force=True)

            with mock.patch.object(subscription_module, "collect_album_audio_files", side_effect=AssertionError("full scan used")):
                diff = manager.diff_chapters(subscription, chapters, download_tmp)

            self.assertEqual(diff["missing"], [])
            self.assertEqual(diff["file_missing_count"], 0)


if __name__ == "__main__":
    unittest.main()
