import unittest
from unittest import mock

from src.server import web_server


def _http_response(payload):
    response = mock.Mock()
    response.json.return_value = payload
    return response


class AdditionalPersonalPlatformsTest(unittest.TestCase):
    def test_personal_cookie_keys_are_isolated(self):
        self.assertEqual(web_server.PERSONAL_COOKIE_KEYS["netease"], "personal_netease")
        self.assertEqual(web_server.PERSONAL_COOKIE_KEYS["qtfm"], "personal_qtfm")
        self.assertEqual(web_server.PERSONAL_COOKIE_KEYS["kuwo"], "personal_kuwo")
        self.assertEqual(web_server.PERSONAL_QR_COOKIE_KEYS["netease"], "personal_netease")
        self.assertEqual(web_server.PERSONAL_QR_COOKIE_KEYS["qtfm"], "personal_qtfm")
        self.assertNotIn("kuwo", web_server.PERSONAL_QR_COOKIE_KEYS)

    def test_personal_cookie_status_lists_all_supported_platforms(self):
        with (
            mock.patch.object(web_server.cookie_manager, "load"),
            mock.patch.object(web_server.cookie_manager, "get_cookie", return_value=""),
            mock.patch.object(web_server, "current_user", return_value={"username": "test"}),
        ):
            response = web_server.app.test_client().get("/api/personal/cookies")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.get_json()["cookies"]),
            {"ximalaya", "lrts", "qidian", "netease", "qtfm", "kuwo"},
        )

    def test_personal_qr_poll_saves_netease_and_qtfm_only_to_personal_keys(self):
        for platform, expected_key in (("netease", "personal_netease"), ("qtfm", "personal_qtfm")):
            with self.subTest(platform=platform):
                session = mock.Mock()
                session.snapshot.return_value = {
                    "platform": platform,
                    "status": "success",
                    "cookies": {"token": "secret"},
                }
                with (
                    mock.patch("core.qr_login.manager.get", return_value=session),
                    mock.patch.object(web_server.cookie_manager, "set_cookie") as set_cookie,
                    mock.patch.object(web_server.search_manager, "set_cookie") as set_search_cookie,
                    mock.patch.object(web_server, "current_user", return_value={"username": "test"}),
                ):
                    response = web_server.app.test_client().get("/api/personal/qr/poll/session")
                self.assertEqual(response.status_code, 200)
                set_cookie.assert_called_once_with(expected_key, "token=secret")
                set_search_cookie.assert_not_called()

    def test_netease_subscriptions_paginate_and_deduplicate(self):
        api = mock.Mock()
        api._normalize_radio.side_effect = lambda item: {
            "id": str(item["id"]),
            "title": item["name"],
            "platform": "网易云听书",
        }
        api._post_weapi.side_effect = [
            {"code": 200, "djRadios": [{"id": 1, "name": "A"}], "count": 2, "hasMore": True},
            {
                "code": 200,
                "data": {"djRadios": [{"id": 1, "name": "A2"}, {"id": 2, "name": "B"}], "count": 2, "hasMore": False},
            },
        ]

        items = web_server._load_netease_personal_from_manager(api, "subscriptions")

        self.assertEqual([item["id"] for item in items], ["1", "2"])
        self.assertEqual([call.args[1]["offset"] for call in api._post_weapi.call_args_list], [0, 1])

    def test_netease_history_accepts_nested_program_records(self):
        api = mock.Mock()
        api._normalize_radio.side_effect = lambda item: {
            "id": str(item["id"]),
            "title": item["name"],
            "platform": "网易云听书",
        }
        api._post_weapi.return_value = {
            "code": 200,
            "data": {
                "list": [
                    {"data": {"program": {"radio": {"id": 7, "name": "播客 A"}}}},
                    {"program": {"radio": {"id": 7, "name": "播客 A 重复"}}},
                    {"data": {"radio": {"id": 8, "name": "播客 B"}}},
                ]
            },
        }

        items = web_server._load_netease_personal_from_manager(api, "history")

        self.assertEqual([item["id"] for item in items], ["7", "8"])

    def test_netease_auth_error_is_not_treated_as_empty(self):
        api = mock.Mock()
        api._post_weapi.return_value = {"code": 301, "message": "需要登录"}
        with self.assertRaisesRegex(RuntimeError, "重新扫码"):
            web_server._load_netease_personal_from_manager(api, "subscriptions")

    def test_netease_chapters_use_expected_total_and_restore_album_detail(self):
        album = {
            "id": "radio-1",
            "title": "未知专辑",
            "platform": "网易云听书",
            "episodes": 12,
        }
        chapters = [{
            "id": "program-1",
            "title": "第一集",
            "_radio": {
                "id": "radio-1",
                "name": "真实专辑名",
                "dj": {"nickname": "主播"},
                "programCount": 12,
            },
        }]
        with (
            web_server.app.test_request_context(
                "/api/album/chapters",
                method="POST",
                json={"album": album},
            ),
            mock.patch.object(
                web_server.search_manager,
                "get_album_chapters_page",
                return_value=(chapters, 12),
            ) as get_page,
            mock.patch.object(web_server, "album_chapter_download_states", return_value={}),
            mock.patch.object(web_server, "annotate_album_library", side_effect=lambda item: item),
        ):
            response = web_server.api_chapters()

        payload = response.get_json()
        self.assertEqual(payload["album"]["title"], "真实专辑名")
        self.assertEqual(payload["album"]["author"], "主播")
        self.assertEqual(payload["album"]["episodes"], 12)
        self.assertEqual([chapter["id"] for chapter in payload["chapters"]], ["program-1"])
        get_page.assert_called_once_with(
            "radio-1",
            "网易云听书",
            page=1,
            page_size=100,
            voice=None,
            expected_total=12,
        )

    def test_qtfm_favorites_only_uses_on_demand_programs(self):
        api = mock.Mock(qingting_id="user", access_token="token")
        api.session.get.return_value = _http_response({
            "errcode": 0,
            "data": {
                "favProgram": [
                    {
                        "id": 11,
                        "name": "有声专辑",
                        "album_cover": "https://example.com/cover.jpg",
                        "podcaster": {"name": "主播"},
                        "program_count": 30,
                    }
                ],
                "favRadio": [{"id": 99, "name": "直播电台"}],
            },
        })

        items = web_server._load_qtfm_personal_from_manager(api, "favorites")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "11")
        self.assertEqual(items[0]["title"], "有声专辑")
        self.assertEqual(items[0]["author"], "主播")
        self.assertEqual(items[0]["episodes"], 30)

    def test_qtfm_history_filters_live_radio_and_deduplicates(self):
        api = mock.Mock(qingting_id="user", access_token="token")
        api.session.get.return_value = _http_response({
            "errcode": 0,
            "data": [
                {"cid": 21, "ctype": 1, "cname": "专辑 A", "cavatar": "a.jpg", "pname": "第 1 集"},
                {"cid": 21, "ctype": "1", "cname": "专辑 A 重复"},
                {"cid": 22, "ctype": 2, "cname": "直播电台"},
            ],
        })

        items = web_server._load_qtfm_personal_from_manager(api, "history")

        self.assertEqual([item["id"] for item in items], ["21"])
        self.assertEqual(items[0]["description"], "第 1 集")

    def test_qtfm_auth_error_is_not_treated_as_empty(self):
        api = mock.Mock(qingting_id="user", access_token="expired")
        api.session.get.return_value = _http_response({"errcode": 401, "errmsg": "无权限"})
        with self.assertRaisesRegex(RuntimeError, "重新扫码"):
            web_server._load_qtfm_personal_from_manager(api, "history")

    def test_qtfm_empty_history_is_a_valid_empty_collection(self):
        api = mock.Mock(qingting_id="user", access_token="token")
        api.session.get.return_value = _http_response({"errcode": 0, "data": []})
        self.assertEqual(web_server._load_qtfm_personal_from_manager(api, "history"), [])

    def test_kuwo_personal_cookie_never_touches_search_account_state(self):
        search_cookie = mock.Mock()
        original_manager = web_server.search_manager.kuwo_manager
        with (
            mock.patch.object(web_server, "_get_personal_cookie", return_value="userid=1; sid=session"),
            mock.patch.object(web_server.search_manager, "set_cookie", search_cookie),
        ):
            with self.assertRaisesRegex(RuntimeError, "不会影响账号管理和公开搜索"):
                web_server._load_kuwo_personal("favorites")
        search_cookie.assert_not_called()
        self.assertIs(web_server.search_manager.kuwo_manager, original_manager)

    def test_kuwo_requires_complete_personal_cookie(self):
        with mock.patch.object(web_server, "_get_personal_cookie", return_value="userid=1"):
            with self.assertRaisesRegex(RuntimeError, "sid/websid"):
                web_server._load_kuwo_personal("history")


if __name__ == "__main__":
    unittest.main()
