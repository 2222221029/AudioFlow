import tempfile
import unittest
from pathlib import Path

from core.audiobook_renamer import RenamePlanManager, preview_rule_samples
from core.rename_rules import RenameRuleStore


def _audio(album_dir, name, content=b"audio"):
    path = album_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class RenameRuleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rules = RenameRuleStore(self.root / "rename_rules.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_scope_inheritance_and_versions(self):
        global_rule = self.rules.save_draft({
            "name": "全局格式",
            "scope": "global",
            "rules": {"format": {"chapter_width": 4}},
        })
        self.rules.activate(global_rule["id"])
        platform_rule = self.rules.save_draft({
            "name": "喜马拉雅规则",
            "scope": "platform",
            "selector": "喜马拉雅",
            "rules": {"format": {"chapter_unit": "章"}},
        })
        self.rules.activate(platform_rule["id"])

        effective = self.rules.effective({"platform": "喜马拉雅", "id": "album-1"})

        self.assertEqual(effective["rules"]["format"]["chapter_width"], 4)
        self.assertEqual(effective["rules"]["format"]["chapter_unit"], "章")
        self.assertEqual(len(effective["applied"]), 3)

    def test_template_is_used_and_plan_keeps_rule_snapshot(self):
        rule = self.rules.save_draft({
            "name": "短文件名",
            "scope": "global",
            "rules": {"format": {
                "chapter_template": "{prefix}_{chapter}{unit}_{title}{ext}",
                "chapter_width": 2,
            }},
        })
        self.rules.activate(rule["id"])
        album_dir = self.root / "测试书"
        _audio(album_dir, "0001-第1集 开始.mp3")
        manager = RenamePlanManager(self.root / "plans.json", self.rules)

        plan = manager.create_plan(
            task_id="task-rule", album={"id": "a1", "title": "测试书"},
            chapters=[], album_dir=album_dir,
        )

        self.assertEqual(plan["items"][0]["target_name"], "0001_01集_开始.mp3")
        self.assertIn(rule["id"], plan["rule_version"])
        snapshot = plan["rule_snapshot"]
        newer = self.rules.save_draft({
            "name": "另一格式", "scope": "global",
            "rules": {"format": {"chapter_width": 5}},
        })
        self.rules.activate(newer["id"])
        self.assertEqual(manager.get(plan["id"])["rule_snapshot"], snapshot)

    def test_ai_suggestion_only_updates_plan_after_explicit_apply(self):
        album_dir = self.root / "测试书"
        source = _audio(album_dir, "0001-第1集 正文加群.mp3")
        manager = RenamePlanManager(self.root / "plans.json", self.rules)
        plan = manager.create_plan(
            task_id="task-ai", album={"title": "测试书"}, chapters=[], album_dir=album_dir,
        )
        saved = manager.save_ai_analysis(plan["id"], {
            "summary": "建议去掉运营文案",
            "suggestions": [{
                "id": "suggest-1", "relative_source": source.name,
                "action": "rename", "clean_title": "正文", "reason": "加群是运营文案",
                "confidence": 0.98,
            }],
        })
        self.assertTrue(source.exists())
        self.assertEqual(saved["status"], "needs_review")

        reviewed = manager.apply_ai_suggestions(plan["id"], ["suggest-1"])

        self.assertTrue(source.exists())
        self.assertEqual(reviewed["status"], "pending_confirmation")
        self.assertEqual(reviewed["items"][0]["target_name"], "0001-《测试书》第001集 正文.mp3")

    def test_rule_preview_cleans_ads_without_writing_files(self):
        results = preview_rule_samples({}, ["0001-第1集 正文（求订阅）.m4a"], "测试书")
        self.assertEqual(results[0]["target_name"], "0001-《测试书》第001集 正文.m4a")

    def test_unsafe_regex_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "高风险"):
            self.rules.save_draft({
                "scope": "global",
                "rules": {"cleanup": {"ad_patterns": ["(.+)+$"]}},
            })


if __name__ == "__main__":
    unittest.main()
