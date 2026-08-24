import tempfile
import unittest
from pathlib import Path

from core.audiobook_renamer import RenamePlanManager, chapter_number


def _manager(tmp_path):
    return RenamePlanManager(tmp_path / "config" / "rename_plans.json")


def _audio(album_dir, name, content=b"audio"):
    path = album_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class AudiobookRenamerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_chapter_number_supports_chinese_and_explicit_order(self):
        self.assertEqual(chapter_number({"title": "第十二集 风雪"}, 1), 12)
        self.assertEqual(chapter_number({"title": "无序号", "ui_display_index": 25}, 1), 25)

    def test_plan_uses_default_format_and_keeps_unmatched_files(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        special = _audio(album_dir, "作者访谈.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-1",
            album={"title": "测试书", "platform": "测试"},
            chapters=[{"title": "第1集 开始", "ui_display_index": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "pending_confirmation")
        self.assertEqual(plan["items"][0]["target_name"], "0001-《测试书》第001集 开始.mp3")
        self.assertEqual(plan["unmatched_files"], [special.name])
        self.assertTrue(source.exists() and special.exists())

    def test_numbered_special_file_is_not_treated_as_chapter(self):
        album_dir = self.tmp_path / "测试书"
        special = _audio(album_dir, "0001-作者访谈.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-special",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 正文", "order_num": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual(plan["unmatched_files"], [special.name])
        self.assertTrue(special.exists())

    def test_unambiguous_parenthesized_ad_is_suggested_for_removal(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第1集 正文（求订阅）.m4a")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-ad",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 正文（求订阅）", "order_num": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "pending_confirmation")
        self.assertEqual(plan["items"][0]["target_name"], "0001-《测试书》第001集 正文.m4a")

    def test_ambiguous_ad_text_requires_review(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第1集 正文加群.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-review",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 正文加群", "order_num": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "needs_review")
        with self.assertRaisesRegex(ValueError, "不能确认"):
            manager.confirm(plan["id"])

    def test_confirm_renames_and_is_idempotent(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-confirm",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 开始", "order_num": 1}],
            album_dir=album_dir,
        )
        completed = manager.confirm(plan["id"])
        target = album_dir / "0001-《测试书》第001集 开始.mp3"
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(target.exists() and not source.exists())
        self.assertEqual(manager.confirm(plan["id"])["status"], "completed")

    def test_source_change_rejects_confirmation(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-change",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 开始", "order_num": 1}],
            album_dir=album_dir,
        )
        source.write_bytes(b"changed after plan")
        with self.assertRaisesRegex(ValueError, "发生变化"):
            manager.confirm(plan["id"])
        self.assertTrue(source.exists())

    def test_existing_target_blocks_execution(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        target = _audio(album_dir, "0001-《测试书》第001集 开始.mp3", b"existing")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-collision",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 开始", "order_num": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "needs_review")
        self.assertTrue(any(issue["type"] == "target_exists" for issue in plan["issues"]))
        self.assertTrue(source.exists() and target.exists())

    def test_cancel_prevents_confirmation(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-cancel",
            album={"title": "测试书"},
            chapters=[{"title": "第1集 开始", "order_num": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(manager.cancel(plan["id"])["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "不能确认"):
            manager.confirm(plan["id"])
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
