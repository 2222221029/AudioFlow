import threading
import unittest
from types import MethodType
from unittest import mock

from core.enhanced_search_manager import EnhancedSearchManager
from core.ximalaya_manager import parse_ximalaya_album_id


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

    def test_ximalaya_album_url_routes_to_exact_album_only(self):
        manager = self.manager()
        manager.search_by_id = mock.Mock(return_value=[{
            "id": "34390396",
            "title": "我的老千江湖",
            "platform": "喜马拉雅",
        }])
        manager._search_platform_cached = mock.Mock()

        results = manager.search_books(
            "https://www.ximalaya.com/album/34390396?from=share",
            "all",
        )

        manager.search_by_id.assert_called_once_with("34390396", "喜马拉雅")
        manager._search_platform_cached.assert_not_called()
        self.assertEqual(results[0]["id"], "34390396")
        self.assertEqual(results[0]["requested_album_id"], "34390396")

    def test_ximalaya_id_search_never_returns_non_matching_first_result(self):
        manager = self.manager()
        manager.ximalaya_manager = mock.Mock()
        manager.ximalaya_manager.search_albums.return_value = [{
            "id": "50069461",
            "title": "同名专辑",
        }]
        manager.ximalaya_manager.get_album_detail.return_value = {
            "id": "34390396",
            "title": "目标专辑",
        }

        result = manager.get_ximalaya_album_by_id("34390396")

        self.assertEqual(result["id"], "34390396")
        self.assertEqual(result["requested_album_id"], "34390396")
        manager.ximalaya_manager.get_album_detail.assert_called_once_with("34390396")

    def test_parse_ximalaya_album_id_rejects_unrelated_urls(self):
        self.assertEqual(parse_ximalaya_album_id("34390396"), "34390396")
        self.assertEqual(
            parse_ximalaya_album_id("https://m.ximalaya.com/album/34390396/"),
            "34390396",
        )
        self.assertIsNone(parse_ximalaya_album_id("https://example.com/album/34390396"))


if __name__ == "__main__":
    unittest.main()
