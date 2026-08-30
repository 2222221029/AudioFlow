import unittest
from unittest import mock

from src.server import web_server


class LrtsPersonalAPITest(unittest.TestCase):
    def test_history_follows_cursor_and_uses_parent_entity_ids(self):
        client = mock.Mock()
        client.recent_listens.side_effect = [
            {
                "status": 0,
                "data": {
                    "list": [
                        {
                            "bookId": 10,
                            "id": 100000001,
                            "sonId": 100000002,
                            "entityType": 4,
                            "name": "书籍 A",
                            "announcer": "主播 A",
                        },
                        {"bookId": 20, "entityType": 2, "name": "专辑 B"},
                    ],
                    "referId": "cursor-2",
                },
            },
            {
                "status": 0,
                "list": [
                    {"bookId": 10, "entityType": 4, "name": "书籍 A（重复）"},
                    {"bookId": 30, "entityType": 4, "name": "书籍 C"},
                ],
                "referId": "cursor-3",
            },
            {"status": 0, "list": [], "referId": "cursor-3"},
        ]

        items = web_server._load_lrts_personal_from_app(client, "history")

        self.assertEqual([item["id"] for item in items], ["1:10", "2:20", "1:30"])
        self.assertEqual(items[0]["author"], "主播 A")
        self.assertEqual(
            [call.args for call in client.recent_listens.call_args_list],
            [("", 101), ("cursor-2", 101), ("cursor-3", 101)],
        )

    def test_favorites_normalize_collection_record_as_book_and_deduplicate(self):
        client = mock.Mock()
        client.collection_books.return_value = {
            "status": 0,
            "data": [
                {"id": 41, "chapterId": 41000001, "entityType": 63, "name": "收藏书籍", "sections": 12},
                {"id": 41, "entityType": 63, "name": "收藏书籍（重复）"},
            ],
        }

        items = web_server._load_lrts_personal_from_app(client, "favorites")

        client.collection_books.assert_called_once_with(11)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "1:41")
        self.assertEqual(items[0]["episodes"], 12)

    def test_programs_merge_book_and_album_cursor_pages(self):
        client = mock.Mock()
        client.published_books.side_effect = [
            {"status": 0, "size": 2, "list": [{"bookId": 51, "bookName": "节目书籍 A"}]},
            {
                "status": 0,
                "size": 2,
                "list": [
                    {"bookId": 51, "bookName": "节目书籍 A（重复）"},
                    {"bookId": 52, "bookName": "节目书籍 B"},
                ],
            },
        ]
        client.published_albums.return_value = {
            "status": 0,
            "size": 1,
            "list": [{"id": 51, "entityType": 2, "name": "节目专辑 A", "nickName": "创作者"}],
        }

        items = web_server._load_lrts_personal_from_app(client, "programs", user_id=88)

        self.assertEqual([item["id"] for item in items], ["1:51", "1:52", "2:51"])
        self.assertEqual(items[-1]["author"], "创作者")
        self.assertEqual(client.published_books.call_args_list[0].kwargs, {
            "user_id": 88,
            "refer_id": 0,
            "op_type": "H",
            "size": 20,
        })
        self.assertEqual(client.published_books.call_args_list[1].kwargs["refer_id"], 51)
        self.assertEqual(client.published_books.call_args_list[1].kwargs["op_type"], "T")
        client.published_albums.assert_called_once_with(
            user_id=88,
            refer_id=0,
            op_type="H",
            size=20,
        )

    def test_upstream_authentication_error_is_not_silently_empty(self):
        client = mock.Mock()
        client.collection_books.return_value = {"status": 401, "msg": "token expired"}

        with self.assertRaisesRegex(RuntimeError, "重新登录"):
            web_server._load_lrts_personal_from_app(client, "favorites")

    def test_invalid_feature_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不支持"):
            web_server._load_lrts_personal_from_app(mock.Mock(), "unknown")


if __name__ == "__main__":
    unittest.main()
