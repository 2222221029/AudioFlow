import threading
import unittest
from types import MethodType

from core.enhanced_search_manager import EnhancedSearchManager


class EnhancedSearchPerformanceTest(unittest.TestCase):
    def manager(self):
        manager = EnhancedSearchManager.__new__(EnhancedSearchManager)
        manager._keyword_search_cache = {}
        manager._keyword_search_cache_lock = threading.Lock()
        return manager

    def test_all_keyword_search_excludes_yuntu_and_preserves_platform_order(self):
        manager = self.manager()
        called = []

        def fake_search(_manager, _keyword, platform):
            called.append(platform)
            return [{"id": platform, "title": platform, "platform": platform}]

        manager._search_platform_cached = MethodType(fake_search, manager)
        results = manager.search_books("测试", "all")

        self.assertNotIn("云听FM", called)
        self.assertEqual(called and set(called), set(manager.KEYWORD_SEARCH_PLATFORMS))
        self.assertEqual([item["platform"] for item in results], list(manager.KEYWORD_SEARCH_PLATFORMS))

    def test_single_yuntu_search_remains_available(self):
        manager = self.manager()
        manager._search_platform_cached = MethodType(
            lambda _manager, _keyword, platform: [{"platform": platform}],
            manager,
        )

        self.assertEqual(manager.search_books("分享链接", "云听FM"), [{"platform": "云听FM"}])


if __name__ == "__main__":
    unittest.main()
