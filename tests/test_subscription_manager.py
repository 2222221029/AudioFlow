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

    def test_diff_does_not_loop_when_title_starts_with_number(self):
        # 回归：标题以书名数字开头（如「1984：…001集」）且 order_num=0 时，
        # 检测端不能用裸 \d+ 抓到书名里的「1984」当章节号——否则与下载端按
        # 「001集」→1 命名的本地文件对不上，导致每轮检测都误报缺失、反复下载跳过。
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as config_tmp, tempfile.TemporaryDirectory() as download_tmp:
            manager = SubscriptionManager(config_tmp)
            album = {"id": "album-1984", "title": "1984：从破产川菜馆开始", "platform": "喜马拉雅"}
            chapters = [
                {"id": "101", "title": "1984：从破产川菜馆开始 001集 重生1984", "order_num": 0},
                {"id": "102", "title": "1984：从破产川菜馆开始 002集 重生1984", "order_num": 0},
                {"id": "103", "title": "1984：从破产川菜馆开始 003集 双椒牛肉拌面", "order_num": 0},
            ]
            subscription = manager.add_or_update(album, chapters, download_tmp)
            album_dir = Path(download_tmp) / "喜马拉雅" / "1984：从破产川菜馆开始"
            album_dir.mkdir(parents=True)
            for ch, idx in zip(chapters, (1, 2, 3)):
                (album_dir / f"{idx:04d}-{ch['title']}.m4a").write_bytes(b"x" * 2048)
            manager.build_audio_index(download_tmp, force=True)

            diff = manager.diff_chapters(subscription, chapters, download_tmp)
            self.assertEqual(diff["missing"], [])
            self.assertEqual(diff["file_missing_count"], 0)

    def test_diff_retries_restricted_chapter_unless_confirmed(self):
        # 回归：付费精品书的最新几集元数据是受限(isFree=0)，且历史上被误标过状态
        # (had_state_record)。这些章节用户有会员实际可下，不能仅凭「元数据受限+有历史记录」
        # 就永久跳过、检测永远「无需补全」。只有「实际下载失败并确认受限」(confirmed) 才跳过。
        from core.subscription_manager import chapter_key

        def build(state_for_missing):
            config_tmp = tempfile.mkdtemp()
            download_tmp = tempfile.mkdtemp()
            manager = SubscriptionManager(config_tmp)
            album = {"id": "vipbook", "title": "付费书", "platform": "喜马拉雅"}
            chapters = [
                {"id": "1", "title": "付费书 001集", "order_num": 1},
                {"id": "2", "title": "付费书 002集", "order_num": 2, "isFree": 0},
            ]
            sub = manager.add_or_update(album, chapters, download_tmp)
            sub.setdefault("downloaded", {})[chapter_key(chapters[1])] = dict(state_for_missing)
            album_dir = Path(download_tmp) / "喜马拉雅" / "付费书"
            album_dir.mkdir(parents=True)
            (album_dir / "0001-付费书 001集.m4a").write_bytes(b"a" * 4096)  # 仅第1集在本地
            manager.build_audio_index(download_tmp, force=True)
            return manager.diff_chapters(sub, chapters, download_tmp)

        # 受限但仅 failed（非 confirmed）→ 应继续尝试下载（进 missing）
        diff = build({"status": "failed"})
        self.assertEqual([c["id"] for c in diff["missing"]], ["2"])

        # 受限且已确认(confirmed) → 防回归：仍跳过，不报缺失
        diff = build({"status": "restricted", "confirmed": True})
        self.assertEqual(diff["missing"], [])
        self.assertEqual(diff["restricted_count"], 1)

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
