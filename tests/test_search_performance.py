import threading
import unittest
from types import MethodType
from unittest import mock

from core.enhanced_search_manager import EnhancedSearchManager
from core.kuwo_manager import KuwoManager
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

    def test_exact_album_names_are_ranked_first_across_platforms(self):
        manager = self.manager()
        rows = {
            "喜马拉雅": [{"id": "similar", "title": "九鼎记有声剧", "platform": "喜马拉雅"}],
            "懒人听书": [{"id": "exact-1", "title": "《九鼎记》", "platform": "懒人听书"}],
            "番茄畅听": [{"id": "far", "title": "九州缥缈录", "platform": "番茄畅听"}],
            "番茄听书": [{"id": "exact-2", "title": "九 鼎 记", "platform": "番茄听书"}],
        }
        manager._search_platform_cached = MethodType(
            lambda _manager, _keyword, platform: rows.get(platform, []),
            manager,
        )

        results = manager.search_books("九鼎记", "all")

        self.assertEqual([item["id"] for item in results[:2]], ["exact-1", "exact-2"])
        self.assertEqual(results[2]["id"], "similar")

    def test_single_platform_ranking_is_stable_for_equal_matches(self):
        manager = self.manager()
        manager._search_platform_cached = MethodType(
            lambda _manager, _keyword, _platform: [
                {"id": "far", "title": "九州缥缈录"},
                {"id": "same-1", "title": "九鼎记广播剧"},
                {"id": "same-2", "title": "九鼎记广播剧"},
                {"id": "exact", "title": "九鼎记"},
            ],
            manager,
        )

        results = manager.search_books("九鼎记", "酷我听书")

        self.assertEqual([item["id"] for item in results], ["exact", "same-1", "same-2", "far"])

    def test_search_coverage_uses_larger_single_requests(self):
        manager = self.manager()
        manager.ximalaya_manager = mock.Mock()
        manager.lrts_manager = mock.Mock()
        manager.kuwo_manager = mock.Mock()
        manager.search_manager = mock.Mock()
        manager.netease_manager = mock.Mock()
        for provider in (
            manager.ximalaya_manager,
            manager.lrts_manager,
            manager.kuwo_manager,
            manager.search_manager,
            manager.netease_manager,
        ):
            provider.search_albums.return_value = []
            provider.search_books.return_value = []
            provider.search_qidian.return_value = []

        for platform in EnhancedSearchManager.SEARCH_RESULT_LIMITS:
            manager._search_platform_impl("目标书名", platform)

        manager.ximalaya_manager.search_albums.assert_called_once_with(
            "目标书名", page=1, page_size=60, max_pages=1
        )
        manager.lrts_manager.search_books.assert_called_once_with("目标书名", limit=50)
        manager.kuwo_manager.search_books.assert_called_once_with("目标书名", limit=60)
        manager.search_manager.search_qidian.assert_called_once_with(
            "目标书名", page_size=50, enrich_details=False
        )
        manager.netease_manager.search_books.assert_called_once_with("目标书名", limit=60)

    def test_kuwo_large_page_deduplicates_without_losing_result_slots(self):
        manager = KuwoManager.__new__(KuwoManager)
        manager.session = mock.Mock()
        albums = [
            {"albumid": "1", "name": "重复"},
            {"albumid": "1", "name": "重复"},
        ] + [
            {"albumid": str(index), "name": f"专辑{index}"}
            for index in range(2, 62)
        ]
        response = mock.Mock(status_code=200)
        response.text = __import__('json').dumps({"albumlist": albums})
        manager.session.get.return_value = response

        results = manager.search_books("目标书名", limit=60)

        self.assertEqual(len(results), 60)
        self.assertEqual(len({item["id"] for item in results}), 60)
        requested_url = manager.session.get.call_args.args[0]
        self.assertIn("pn=0", requested_url)
        self.assertIn("rn=60", requested_url)

    def test_lizhi_full_catalog_has_no_twenty_page_cap(self):
        manager = self.manager()
        manager.lizhi_manager = mock.Mock()
        manager.lizhi_manager.get_chapters.return_value = [{"id": "chapter"}]

        results = manager._get_album_chapters("12345", "荔枝FM")

        self.assertEqual(results, [{"id": "chapter"}])
        manager.lizhi_manager.get_chapters.assert_called_once_with(
            "12345", page=1, page_size=500
        )

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
