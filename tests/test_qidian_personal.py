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
        self.assertEqual(items[0]["id"], 22)
        self.assertEqual(items[0]["title"], "有声专辑")
        self.assertEqual(items[0]["author"], "演播者")
        self.assertEqual(items[0]["platform"], "起点听书")

    def test_invalid_feature_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不支持"):
            web_server._load_qidian_personal("history")


if __name__ == "__main__":
    unittest.main()
