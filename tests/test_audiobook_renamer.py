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

    def test_plan_requires_special_file_review_and_keeps_it_untouched_by_default(self):
        album_dir = self.tmp_path / "测试书"
        source = _audio(album_dir, "0001-第1集 开始.mp3")
        special = _audio(album_dir, "作者访谈.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-1",
            album={"title": "测试书", "platform": "测试"},
            chapters=[{"title": "第1集 开始", "ui_display_index": 1}],
            album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual(plan["items"][0]["target_name"], "0001-《测试书》第001集 开始.mp3")
        self.assertEqual(plan["unmatched_files"], [special.name])
        self.assertTrue(any(issue["type"] == "special_file" for issue in plan["issues"]))
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
        self.assertTrue(any(issue["type"] in {"target_exists", "duplicate_chapter"} for issue in plan["issues"]))
        self.assertTrue(source.exists() and target.exists())

    def test_missing_chapter_reserves_prefix_gap(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第1集 开始.mp3")
        _audio(album_dir, "0003-第3集 继续.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-gap",
            album={"title": "测试书"},
            chapters=[],
            album_dir=album_dir,
        )
        self.assertEqual(plan["missing_chapters"], [2])
        self.assertEqual([item["prefix"] for item in plan["items"]], [1, 3])
        self.assertEqual(plan["items"][1]["target_name"], "0003-《测试书》第003集 继续.mp3")

    def test_special_file_keeps_position_and_does_not_consume_chapter_number(self):
        album_dir = self.tmp_path / "测试书"
        special = _audio(album_dir, "0001-片花---每天更新.mp3")
        _audio(album_dir, "0002-第1集 开始.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-special-order", album={"title": "测试书"}, chapters=[], album_dir=album_dir,
        )
        reviewed = manager.configure(plan["id"], {
            "special_actions": {special.name: "organize"},
        })
        self.assertEqual(reviewed["status"], "pending_confirmation")
        self.assertEqual(reviewed["items"][0]["target_name"], "0001-《测试书》片花.mp3")
        self.assertEqual(reviewed["items"][1]["target_name"], "0002-《测试书》第001集 开始.mp3")

    def test_chinese_chapter_numbers_and_ending_marker_are_preserved(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第一百零一集 大结局（求订阅）.m4a")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-cn", album={"title": "测试书"}, chapters=[], album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "pending_confirmation")
        self.assertEqual(plan["items"][0]["target_name"], "0101-《测试书》第101集 大结局.m4a")

    def test_consecutive_same_titles_get_suffixes_but_nonconsecutive_do_not(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第1集 围城.mp3")
        _audio(album_dir, "0002-第2集 围城.mp3")
        _audio(album_dir, "0010-第10集 围城.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-repeat", album={"title": "测试书"}, chapters=[], album_dir=album_dir,
        )
        titles = [item["clean_title"] for item in plan["items"]]
        self.assertEqual(titles, ["围城（1）", "围城（2）", "围城"])

    def test_multi_volume_album_uses_independent_chapters_and_global_prefixes(self):
        album_dir = self.tmp_path / "系列"
        _audio(album_dir, "0001-第一部 第1集 开始.mp3")
        _audio(album_dir, "0002-第一部 第2集 继续.mp3")
        _audio(album_dir, "0003-第二部 第1集 重逢.mp3")
        _audio(album_dir, "0004-第二部 第2集 终章.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-volumes", album={"title": "系列"}, chapters=[], album_dir=album_dir,
        )
        self.assertEqual(plan["volume_count"], 2)
        self.assertEqual(plan["configuration"]["volumes"], {
            "1": "系列·第一部", "2": "系列·第二部",
        })
        self.assertEqual(plan["status"], "needs_review")
        reviewed = manager.configure(plan["id"], {"configuration": plan["configuration"]})
        self.assertEqual(reviewed["status"], "pending_confirmation")
        self.assertEqual(
            [item["target_name"] for item in reviewed["items"]],
            [
                "0001-《系列·第一部》第001集 开始.mp3",
                "0002-《系列·第一部》第002集 继续.mp3",
                "0003-《系列·第二部》第001集 重逢.mp3",
                "0004-《系列·第二部》第002集 终章.mp3",
            ],
        )

    def test_multi_volume_keeps_missing_and_duplicate_checks_scoped_to_each_volume(self):
        album_dir = self.tmp_path / "系列"
        _audio(album_dir, "0001-第一部 第1集 开始.mp3")
        _audio(album_dir, "0003-第一部 第3集 跨越.mp3")
        _audio(album_dir, "0004-第二部 第1集 重逢.mp3")
        _audio(album_dir, "0005-第二部 第1集 重复下载.mp3")
        plan = _manager(self.tmp_path).create_plan(
            task_id="task-volume-risks", album={"title": "系列"}, chapters=[], album_dir=album_dir,
        )
        self.assertEqual(plan["missing_by_volume"], {"1": [2], "2": []})
        self.assertEqual(plan["missing_chapters"], [{"volume": 1, "chapter": 2}])
        self.assertTrue(any(issue["type"] == "duplicate_chapter" for issue in plan["issues"]))
        self.assertTrue(all(
            issue.get("resolved")
            for issue in plan["issues"]
            if issue["type"] == "cross_book_suspected"
        ))
        self.assertEqual(plan["status"], "needs_review")

    def test_cross_book_file_blocks_execution(self):
        album_dir = self.tmp_path / "九鼎记"
        _audio(album_dir, "1345-妙手大仙医 第1345集 订阅专辑，兄弟们.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-cross", album={"title": "九鼎记"}, chapters=[], album_dir=album_dir,
        )
        self.assertEqual(plan["status"], "needs_review")
        self.assertTrue(any(issue["type"] == "cross_book_suspected" for issue in plan["issues"]))
        safe = manager.resolve_safe(plan["id"])
        self.assertEqual(safe["status"], "no_changes")

    def test_special_file_quarantine_is_recoverable(self):
        album_dir = self.tmp_path / "测试书"
        special = _audio(album_dir, "0001-更新通知.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-trash", album={"title": "测试书"}, chapters=[], album_dir=album_dir,
        )
        reviewed = manager.configure(plan["id"], {
            "special_actions": {special.name: "quarantine"},
        })
        completed = manager.confirm(reviewed["id"])
        quarantined = Path(completed["items"][0]["quarantine"])
        self.assertFalse(special.exists())
        self.assertTrue(quarantined.exists())

    def test_plan_writes_bom_mapping_and_reuses_confirmed_album_profile(self):
        album_dir = self.tmp_path / "测试书"
        _audio(album_dir, "0001-第1集 开始.mp3")
        manager = _manager(self.tmp_path)
        plan = manager.create_plan(
            task_id="task-profile-1", album={"id": "album-1", "title": "测试书", "platform": "测试"},
            chapters=[], album_dir=album_dir,
        )
        configured = manager.configure(plan["id"], {
            "configuration": {"chapter_unit": "章", "chapter_width": 4},
        })
        mapping = Path(configured["mapping_file"])
        self.assertEqual(mapping.read_bytes()[:3], b"\xef\xbb\xbf")
        manager.confirm(plan["id"])
        second = manager.create_plan(
            task_id="task-profile-2", album={"id": "album-1", "title": "测试书", "platform": "测试"},
            chapters=[], album_dir=album_dir,
        )
        self.assertTrue(second["configuration"]["profile_reused"])
        self.assertEqual(second["configuration"]["chapter_unit"], "章")
        self.assertEqual(second["configuration"]["chapter_width"], 4)

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
