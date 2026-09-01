import unittest
from unittest import mock

from core.enhanced_search_manager import EnhancedSearchManager
from core.netease_cloud_audiobook_manager import NeteaseCloudAudiobookManager


def _program(index, total=1200):
    return {
        "id": index,
        "name": f"第{index}集",
        "radio": {"id": "radio-1", "name": "长篇有声书", "programCount": total},
    }


class NeteaseCloudAudiobookManagerTests(unittest.TestCase):
    def test_complete_directory_follows_server_500_item_cap(self):
        manager = NeteaseCloudAudiobookManager()
        manager.set_cookie("MUSIC_U=test; __csrf=test")
        pages = [
            {"programs": [_program(index) for index in range(1, 501)], "count": 1200, "more": True},
            {"programs": [_program(index) for index in range(501, 1001)], "count": 1200, "more": True},
            {"programs": [_program(index) for index in range(1001, 1201)], "count": 1200, "more": False},
        ]
        with mock.patch.object(manager, "_fetch_program_page", side_effect=pages) as fetch:
            chapters = manager.get_all_chapters("radio-1", page_size=1000)

        self.assertEqual(len(chapters), 1200)
        self.assertEqual([chapter["order_num"] for chapter in chapters[499:501]], [500, 501])
        self.assertEqual(chapters[-1]["order_num"], 1200)
        self.assertEqual(
            [call.kwargs for call in fetch.call_args_list],
            [
                {"offset": 0, "limit": 500},
                {"offset": 500, "limit": 500},
                {"offset": 1000, "limit": 500},
            ],
        )

    def test_page_loader_reports_provider_total(self):
        manager = NeteaseCloudAudiobookManager()
        manager.set_cookie("MUSIC_U=test")
        payload = {"programs": [_program(index, total=2345) for index in range(201, 401)], "count": 2345}
        with mock.patch.object(manager, "_fetch_program_page", return_value=payload) as fetch:
            chapters, total = manager.get_chapters_page("radio-1", page=2, page_size=200)

        self.assertEqual(len(chapters), 200)
        self.assertEqual(total, 2345)
        self.assertEqual(chapters[0]["order_num"], 201)
        fetch.assert_called_once_with("radio-1", offset=200, limit=200, expected_total=0)

    def test_known_album_retries_a_transient_empty_program_page(self):
        manager = NeteaseCloudAudiobookManager()
        manager.set_cookie("MUSIC_U=test")
        responses = [
            {"code": 200, "programs": [], "count": 0, "more": False},
            {"code": 200, "programs": [_program(1, total=1)], "count": 1, "more": False},
        ]
        with (
            mock.patch.object(manager, "_post_weapi", side_effect=responses) as post,
            mock.patch("core.netease_cloud_audiobook_manager.time.sleep") as sleep,
        ):
            chapters, total = manager.get_chapters_page(
                "radio-1",
                page=1,
                page_size=100,
                expected_total=1,
            )

        self.assertEqual([chapter["id"] for chapter in chapters], ["1"])
        self.assertEqual(total, 1)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(manager.program_page_retry_delays[0])

    def test_confirmed_empty_album_does_not_retry(self):
        manager = NeteaseCloudAudiobookManager()
        manager.set_cookie("MUSIC_U=test")
        response = {"code": 200, "programs": [], "count": 0, "more": False}
        with (
            mock.patch.object(manager, "_post_weapi", return_value=response) as post,
            mock.patch("core.netease_cloud_audiobook_manager.time.sleep") as sleep,
        ):
            chapters, total = manager.get_chapters_page("radio-1")

        self.assertEqual(chapters, [])
        self.assertEqual(total, 0)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_known_album_never_reports_a_repeated_empty_page_as_success(self):
        manager = NeteaseCloudAudiobookManager()
        manager.set_cookie("MUSIC_U=test")
        response = {"code": 200, "programs": [], "count": 0, "more": False}
        with (
            mock.patch.object(manager, "_post_weapi", return_value=response) as post,
            mock.patch("core.netease_cloud_audiobook_manager.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "连续 3 次未返回章节"):
                manager.get_chapters_page("radio-1", expected_total=12)

        self.assertEqual(post.call_count, manager.program_page_attempts)
        self.assertEqual(sleep.call_count, manager.program_page_attempts - 1)

    def test_enhanced_manager_uses_complete_netease_loader(self):
        manager = object.__new__(EnhancedSearchManager)
        manager.netease_manager = mock.Mock()
        manager.netease_manager.get_all_chapters.return_value = [{"id": str(index)} for index in range(1200)]

        chapters = manager._get_album_chapters("radio-1", "网易云听书")

        self.assertEqual(len(chapters), 1200)
        manager.netease_manager.get_all_chapters.assert_called_once_with("radio-1")


if __name__ == "__main__":
    unittest.main()
