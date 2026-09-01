import unittest
from unittest import mock

from src.server import web_server


def _response(books, page, page_count=None, total_count=None, code=0, msg=""):
    page_info = {"pageIndex": page}
    if page_count is not None:
        page_info["pageCount"] = page_count
    if total_count is not None:
        page_info["totalCount"] = total_count
    response = mock.Mock()
    response.json.return_value = {
        "code": code,
        "msg": msg,
        "data": {"booksInfo": books, "pageInfo": page_info},
    }
    return response


class QidianPersonalAPITest(unittest.TestCase):
    def _api(self, *responses):
        api = mock.Mock()
        api.qidian_cookies = {"ywguid": "test-guid"}
        api.qidian_session.get.side_effect = responses
        return api

    def test_audio_book_type_accepts_integer_and_string_only(self):
        self.assertTrue(web_server._is_qidian_audio_book({"bookType": 2}))
        self.assertTrue(web_server._is_qidian_audio_book({"bookType": "2"}))
        self.assertFalse(web_server._is_qidian_audio_book({"bookType": 1}))
        self.assertFalse(web_server._is_qidian_audio_book({"bookType": "1"}))
        self.assertFalse(web_server._is_qidian_audio_book({}))

    def test_bookshelf_loads_all_pages_and_skips_text_books(self):
        api = self._api(
            _response(
                [{"bookId": 1, "bookName": "文字小说", "bookType": 1}],
                page=1,
                page_count=2,
            ),
            _response(
                [
                    {"bookId": 2, "bookName": "有声书 A", "bookType": 2},
                    {"bookId": 3, "bookName": "有声书 B", "bookType": "2"},
                ],
                page=2,
                page_count=2,
            ),
        )

        books = web_server._load_qidian_audio_bookshelf(api)

        self.assertEqual([book["bookId"] for book in books], [2, 3])
        self.assertEqual(
            [call.kwargs["params"]["page"] for call in api.qidian_session.get.call_args_list],
            [1, 2],
        )

    def test_bookshelf_deduplicates_audio_books_across_pages(self):
        api = self._api(
            _response([{"bookId": 7, "bookName": "有声书", "bookType": 2}], 1, page_count=2),
            _response(
                [
                    {"bookId": "7", "bookName": "有声书重复", "bookType": "2"},
                    {"bookId": 8, "bookName": "另一部有声书", "bookType": 2},
                ],
                2,
                page_count=2,
            ),
        )

        books = web_server._load_qidian_audio_bookshelf(api)

        self.assertEqual([str(book["bookId"]) for book in books], ["7", "8"])

    def test_bookshelf_uses_total_count_when_page_count_is_missing(self):
        api = self._api(
            _response([{"bookId": 11, "bookType": 1}], 1, total_count=2),
            _response([{"bookId": 12, "bookType": 2}], 2, total_count=2),
        )

        books = web_server._load_qidian_audio_bookshelf(api)

        self.assertEqual([book["bookId"] for book in books], [12])
        self.assertEqual(api.qidian_session.get.call_count, 2)

    def test_upstream_error_is_not_silently_treated_as_empty_bookshelf(self):
        api = self._api(_response([], 1, code=401, msg="登录已失效"))

        with self.assertRaisesRegex(RuntimeError, "登录已失效"):
            web_server._load_qidian_audio_bookshelf(api)

    def test_personal_favorites_returns_normalized_audio_albums_only(self):
        api = self._api(
            _response(
                [
                    {"bookId": 21, "bookName": "文字小说", "bookType": 1},
                    {
                        "bookId": 22,
                        "bookName": "有声专辑",
                        "authorName": "演播者",
                        "coverUrl": "https://example.com/cover.jpg",
                        "bookType": 2,
                    },
                ],
                1,
                page_count=1,
            )
        )
        api.get_qidian_user_account.return_value = {"user": {"userId": 9}}

        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="ywguid=test-guid"),
            mock.patch("core.search_manager.SearchManager", return_value=api),
        ):
            items = web_server._load_qidian_personal("favorites")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "22")
        self.assertEqual(items[0]["title"], "有声专辑")
        self.assertEqual(items[0]["author"], "演播者")
        self.assertEqual(items[0]["platform"], "起点听书")
        self.assertEqual(items[0]["personal_center_platform"], "qidian")
        self.assertEqual(items[0]["qidian_book_id"], "22")

    def test_personal_favorites_prefers_explicit_audio_catalog_id(self):
        api = self._api(
            _response(
                [{
                    "bookId": 22,
                    "bookName": "有声专辑",
                    "bookType": 2,
                    "audioInfo": {"audioBookId": 9022},
                }],
                1,
                page_count=1,
            )
        )
        api.get_qidian_user_account.return_value = {"user": {"userId": 9}}

        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="ywguid=test-guid"),
            mock.patch("core.search_manager.SearchManager", return_value=api),
        ):
            items = web_server._load_qidian_personal("favorites")

        self.assertEqual(items[0]["id"], "9022")
        self.assertEqual(items[0]["qidian_audio_id"], "9022")
        self.assertEqual(items[0]["qidian_book_id"], "22")

    def test_personal_favorites_derives_official_cover_when_shelf_omits_it(self):
        api = self._api(
            _response(
                [{"bookId": 22, "bookName": "有声专辑", "bookType": 2}],
                1,
                page_count=1,
            )
        )
        api.get_qidian_user_account.return_value = {"user": {"userId": 9}}

        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="ywguid=test-guid"),
            mock.patch("core.search_manager.SearchManager", return_value=api),
        ):
            items = web_server._load_qidian_personal("favorites")

        self.assertEqual(
            items[0]["cover"],
            "https://bookcover.yuewen.com/qdbimg/349573/22/180",
        )

    def test_personal_favorites_uses_nested_upstream_cover_before_fallback(self):
        api = self._api(
            _response(
                [{
                    "bookId": 22,
                    "bookName": "有声专辑",
                    "bookType": 2,
                    "audioInfo": {"bookCover": "//example.com/audio-cover.jpg"},
                }],
                1,
                page_count=1,
            )
        )
        api.get_qidian_user_account.return_value = {"user": {"userId": 9}}

        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="ywguid=test-guid"),
            mock.patch("core.search_manager.SearchManager", return_value=api),
        ):
            items = web_server._load_qidian_personal("favorites")

        self.assertEqual(items[0]["cover"], "https://example.com/audio-cover.jpg")

    def test_bookshelf_novel_id_is_resolved_before_loading_chapters(self):
        personal_api = mock.Mock()
        personal_api.search_qidian.return_value = [
            {"id": "9022", "title": "有声专辑", "author": "演播者"},
        ]
        personal_api.get_qidian_chapters.side_effect = [[], [
            {"id": "chapter-1", "title": "第一集", "platform": "起点听书"},
        ]]
        album = {
            "id": "22",
            "title": "有声专辑",
            "author": "演播者",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
            "qidian_book_id": "22",
        }
        with (
            web_server.app.test_request_context(
                "/api/album/chapters",
                method="POST",
                json={"album": album, "page": 1, "page_size": 100},
            ),
            mock.patch.object(web_server, "_qidian_api_for_album", return_value=personal_api),
        ):
            response = web_server.api_chapters()

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["album"]["id"], "9022")
        self.assertEqual(payload["album"]["qidian_book_id"], "22")
        personal_api.search_qidian.assert_called_once_with("有声专辑", page_size=50)
        self.assertEqual(
            [call.args[0] for call in personal_api.get_qidian_chapters.call_args_list],
            ["22", "9022"],
        )

    def test_bookshelf_original_id_is_tried_before_search(self):
        api = mock.Mock()
        api.get_qidian_chapters.return_value = [
            {"id": "chapter-1", "title": "第一集", "platform": "起点听书"},
        ]
        album = {
            "id": "22",
            "title": "有声专辑",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
            "qidian_book_id": "22",
        }

        resolved, chapters, attempted = web_server._load_personal_qidian_album_chapters(album, api)

        self.assertEqual(resolved["id"], "22")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(attempted, ["22"])
        api.search_qidian.assert_not_called()

    def test_bookshelf_detail_tries_original_id_before_search(self):
        api = mock.Mock()
        api.get_qidian_detail.return_value = {
            "id": "22",
            "title": "有声专辑",
            "platform": "起点听书",
        }
        album = {
            "id": "22",
            "title": "有声专辑",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
            "qidian_book_id": "22",
        }

        resolved, detail, attempted = web_server._load_personal_qidian_album_detail(album, api)

        self.assertEqual(resolved["id"], "22")
        self.assertEqual(detail["title"], "有声专辑")
        self.assertEqual(attempted, ["22"])
        api.get_qidian_detail.assert_called_once_with("22")
        api.search_qidian.assert_not_called()

    def test_personal_album_detail_endpoint_uses_original_bookshelf_id(self):
        api = mock.Mock()
        api.get_qidian_detail.return_value = {
            "id": "22",
            "title": "有声专辑",
            "platform": "起点听书",
        }
        album = {
            "id": "22",
            "title": "有声专辑",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
            "qidian_book_id": "22",
        }
        with (
            web_server.app.test_request_context(
                "/api/album/detail",
                method="POST",
                json={"album": album},
            ),
            mock.patch.object(web_server, "_qidian_api_for_album", return_value=api),
        ):
            response = web_server.api_album_detail()

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["album"]["id"], "22")
        api.get_qidian_detail.assert_called_once_with("22")
        api.search_qidian.assert_not_called()

    def test_bookshelf_title_matches_unique_audio_edition_suffix(self):
        api = mock.Mock()
        api.search_qidian.return_value = [
            {"id": "9022", "title": "有声专辑（多人有声剧）", "author": "演播者"},
            {"id": "9999", "title": "完全不同的专辑", "author": "其他人"},
        ]
        album = {
            "id": "22",
            "title": "有声专辑",
            "author": "演播者",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
            "qidian_book_id": "22",
        }

        resolved = web_server._resolve_personal_qidian_album(album, api)

        self.assertEqual(resolved["id"], "9022")

    def test_audio_catalog_id_keys_are_case_insensitive(self):
        self.assertEqual(
            web_server._qidian_audio_id_from_book({"audioInfo": {"AudioBookID": 9022}}),
            "9022",
        )

    def test_personal_album_uses_isolated_qidian_credentials(self):
        personal_api = mock.Mock()
        album = {
            "id": "22",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
        }
        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="ywguid=personal-guid"),
            mock.patch("core.search_manager.SearchManager", return_value=personal_api),
        ):
            selected = web_server._qidian_api_for_album(album)

        self.assertIs(selected, personal_api)
        personal_api.set_qidian_cookie.assert_called_once_with("ywguid=personal-guid")
        self.assertIsNot(selected, web_server.search_manager.search_manager)

    def test_personal_album_chapters_use_personal_qidian_client(self):
        personal_api = mock.Mock()
        personal_api.get_qidian_chapters.return_value = [
            {"id": "chapter-1", "title": "第一集", "platform": "起点听书"},
        ]
        album = {
            "id": "22",
            "title": "有声专辑",
            "cover": "https://example.com/qidian-cover.jpg",
            "platform": "起点听书",
            "personal_center_platform": "qidian",
        }
        with (
            web_server.app.test_request_context(
                "/api/album/chapters",
                method="POST",
                json={"album": album, "page": 1, "page_size": 100},
            ),
            mock.patch.object(web_server, "_qidian_api_for_album", return_value=personal_api),
        ):
            response = web_server.api_chapters()

        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["chapters"][0]["id"], "chapter-1")
        personal_api.get_qidian_chapters.assert_called_once_with("22")
        self.assertEqual(payload["album"]["personal_center_platform"], "qidian")
        self.assertEqual(payload["album"]["cover"], "https://example.com/qidian-cover.jpg")

    def test_invalid_feature_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不支持"):
            web_server._load_qidian_personal("history")


if __name__ == "__main__":
    unittest.main()
