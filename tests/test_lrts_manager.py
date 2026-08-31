import time
import unittest
from unittest import mock

from core import lrts_manager as lm
from core.lrts_manager import (
    ALBUM_ENTITY_TYPE,
    APP_HEADERS,
    BOOK_ENTITY_TYPE,
    COLLECTION_BOOKS_PATH,
    LrtsAppClient,
    LRTSManager,
    PERSONAL_HOST,
    PUBLISHED_ALBUMS_PATH,
    PUBLISHED_BOOKS_PATH,
    RECENT_LISTENS_PATH,
    V3_LISTEN_PATH,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeWebSession:
    def __init__(self, payloads):
        self.headers = {}
        self.payloads = iter(payloads)

    def get(self, *args, **kwargs):
        return FakeResponse(next(self.payloads))


class LRTSManagerTest(unittest.TestCase):
    def test_personal_api_methods_use_current_host_and_http_methods(self):
        client = LrtsAppClient(imei="test-imei", token="test-token")
        session = mock.Mock()
        session.get.return_value = FakeResponse({"status": 0})
        session.post.return_value = FakeResponse({"status": 0, "data": []})
        client.session = session

        client.recent_listens("cursor-1")
        client.collection_books()
        client.published_books(user_id=88, refer_id=10, op_type="T", size=20)
        client.published_albums(user_id=0, refer_id=0, op_type="H", size=20)

        recent_call = session.get.call_args_list[0]
        self.assertEqual(recent_call.args[0], PERSONAL_HOST + RECENT_LISTENS_PATH)
        self.assertEqual(recent_call.kwargs["params"]["referId"], "cursor-1")
        self.assertEqual(recent_call.kwargs["params"]["srcType"], "101")

        favorite_call = session.post.call_args
        self.assertEqual(favorite_call.args[0], PERSONAL_HOST + COLLECTION_BOOKS_PATH)
        self.assertEqual(favorite_call.kwargs["data"]["srcType"], "11")
        self.assertEqual(favorite_call.kwargs["data"]["token"], "test-token")
        self.assertIn("sc", favorite_call.kwargs["data"])

        book_call = session.get.call_args_list[1]
        self.assertEqual(book_call.args[0], PERSONAL_HOST + PUBLISHED_BOOKS_PATH)
        self.assertEqual(
            {key: book_call.kwargs["params"][key] for key in ("userId", "referId", "opType", "size")},
            {"userId": "88", "referId": "10", "opType": "T", "size": "20"},
        )

        album_call = session.get.call_args_list[2]
        self.assertEqual(album_call.args[0], PERSONAL_HOST + PUBLISHED_ALBUMS_PATH)
        self.assertNotIn("userId", album_call.kwargs["params"])

    def test_web_book_url_maps_to_app_book_entity(self):
        manager = LRTSManager()

        self.assertEqual(
            manager._parse_entity_ref("https://www.lrts.me/book/45151"),
            (BOOK_ENTITY_TYPE, 45151),
        )
        self.assertEqual(
            manager._parse_entity_ref("4:45151"),
            (BOOK_ENTITY_TYPE, 45151),
        )
        self.assertEqual(manager._parse_entity_ref("45151"), (BOOK_ENTITY_TYPE, 45151))
        self.assertEqual(manager._parse_entity_ref("2:99"), (ALBUM_ENTITY_TYPE, 99))

    def test_item_entity_infers_book_and_album_without_type(self):
        manager = LRTSManager()

        self.assertEqual(manager._item_entity({"bookId": 45151}), (BOOK_ENTITY_TYPE, 45151))
        self.assertEqual(manager._item_entity({"ablumnId": 99}), (ALBUM_ENTITY_TYPE, 99))

    def test_web_chapters_preserve_real_resource_id(self):
        page = {
            "data": {
                "data": [{
                    "fatherResId": 45151,
                    "payType": 0,
                    "resId": 4515100000001,
                    "resName": "&#x7b2c;1&#x96c6;_&#x6954;&#x5b50;",
                    "section": 1,
                }]
            }
        }
        empty_page = {"data": {"data": []}}
        session = FakeWebSession([page, empty_page])

        with mock.patch("core.lrts_manager.requests.Session", return_value=session):
            manager = LRTSManager()
            chapters = manager._fetch_web_book_chapters(45151)

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["id"], 4515100000001)
        self.assertEqual(chapters[0]["chapterId"], 4515100000001)
        self.assertEqual(chapters[0]["section"], 1)
        self.assertEqual(chapters[0]["chapterName"], "第1集_楔子")

    def test_web_book_chapters_use_app_book_entity_and_real_id(self):
        manager = LRTSManager()
        client = mock.Mock()
        client.fetch_all_chapters.return_value = [{
            "id": 4515100000001,
            "sectionId": "4515100000001",
            "tmeId": "1038301964",
            "section": 1,
            "name": "第1集_楔子",
            "payType": 0,
        }]
        manager._client = client

        chapters = manager.get_chapters("https://www.lrts.me/book/45151")

        client.fetch_all_chapters.assert_called_once_with(BOOK_ENTITY_TYPE, 45151)
        self.assertEqual(chapters[0]["id"], "4515100000001")
        self.assertEqual(chapters[0]["_chapter_data"]["entity_type"], BOOK_ENTITY_TYPE)
        self.assertEqual(chapters[0]["_chapter_data"]["id"], 4515100000001)
        self.assertEqual(chapters[0]["_chapter_data"]["track_id"], 1038301964)

    def test_android_v3_play_path_uses_track_id_and_hq(self):
        client = LrtsAppClient(imei="test-imei", token="test-token")
        client.get = mock.Mock(return_value={
            "status": 0,
            "data": {"path": "//audio.example/hq.m4a", "quality": 3},
        })

        result = client.get_play_path(
            BOOK_ENTITY_TYPE,
            46111471,
            4611147100000004,
            section=4,
            op_type=1,
            track_id=1038301964,
            quality=3,
        )

        self.assertEqual(result["data"]["quality"], 3)
        client.get.assert_called_once_with(
            "https://dapis.mting.info",
            V3_LISTEN_PATH,
            {
                "entityType": BOOK_ENTITY_TYPE,
                "entityId": 46111471,
                "trackId": 1038301964,
                "opType": 1,
                "quality": 3,
                "effect": 0,
                "section": 4,
                "resId": 4611147100000004,
            },
        )

    def test_android_v3_unsupported_quality_retries_next_tier(self):
        client = LrtsAppClient(imei="test-imei", token="test-token")
        client.get = mock.Mock(side_effect=[
            {"status": 33, "msg": "SQ unavailable"},
            {"status": 0, "data": {"path": "//audio.example/hq.m4a", "quality": 3}},
        ])

        result = client.get_play_path(
            BOOK_ENTITY_TYPE,
            46111471,
            4611147100000004,
            section=4,
            track_id=1038301964,
            quality=4,
        )

        self.assertEqual(result["data"]["quality"], 3)
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.get.call_args_list[1].args[2]["quality"], 3)

    def test_android_v3_finds_and_caches_best_available_quality(self):
        client = LrtsAppClient(imei="test-imei", token="test-token")
        client.get = mock.Mock(side_effect=[
            {
                "status": 27,
                "data": {"path": "//audio.example/normal.m4a", "quality": 1},
            },
            {
                "status": 0,
                "data": {"path": "//audio.example/high.m4a", "quality": 2},
            },
        ])

        result = client.get_play_path(
            BOOK_ENTITY_TYPE,
            46111471,
            4611147100000004,
            section=4,
            track_id=1038301964,
            quality=3,
        )

        self.assertEqual(result["data"]["quality"], 2)
        self.assertEqual([call.args[2]["quality"] for call in client.get.call_args_list], [3, 2])

        client.get.reset_mock()
        client.get.side_effect = None
        client.get.return_value = {
            "status": 0,
            "data": {"path": "//audio.example/high-2.m4a", "quality": 2},
        }
        client.get_play_path(
            BOOK_ENTITY_TYPE,
            46111471,
            4611147100000005,
            section=5,
            track_id=1038301968,
            quality=3,
        )

        client.get.assert_called_once()
        self.assertEqual(client.get.call_args.args[2]["quality"], 2)

    def test_current_android_headers_are_used(self):
        self.assertEqual(APP_HEADERS["ClientVersion"], "8.8.12")
        self.assertIn("/ch_yyting/8812/8.8.12", APP_HEADERS["User-Agent"])

    def test_audio_lookup_uses_book_entity_for_web_book(self):
        manager = LRTSManager()
        client = mock.Mock()
        client.session.headers = {"User-Agent": "test"}
        client.get_play_path.return_value = {
            "status": 0,
            "data": {"path": "//audio.example/chapter-1.m4a"},
        }
        manager._client = client
        chapter_data = {
            "entity_type": BOOK_ENTITY_TYPE,
            "entity_id": 45151,
            "section": 1,
            "id": 4515100000001,
        }

        with mock.patch("core.lrts_manager._throttle_audio_request"):
            url = manager.get_audio_url(
                "https://www.lrts.me/book/45151",
                "4515100000001",
                chapter_data,
            )

        self.assertEqual(url, "https://audio.example/chapter-1.m4a")
        client.get_play_path.assert_called_once_with(
            BOOK_ENTITY_TYPE,
            45151,
            4515100000001,
            1,
            op_type=1,
        )

    def test_audio_lookup_passes_tme_track_and_accepts_quality_downgrade(self):
        manager = LRTSManager()
        client = mock.Mock()
        client.session.headers = {"User-Agent": "test"}
        client.get_play_path.return_value = {
            "status": 27,
            "data": {
                "path": "//audio.example/downgraded.m4a",
                "quality": 1,
                "bitrate": "48000",
            },
        }
        manager._client = client
        chapter_data = {
            "entity_type": BOOK_ENTITY_TYPE,
            "entity_id": 46111471,
            "section": 4,
            "id": 4611147100000004,
            "track_id": 1038301964,
        }

        with (
            mock.patch("core.lrts_manager._throttle_audio_request"),
            mock.patch.dict("core.lrts_manager.os.environ", {"LRTS_AUDIO_QUALITY": "3"}),
        ):
            url = manager.get_audio_url("1:46111471", "4611147100000004", chapter_data)

        self.assertEqual(url, "https://audio.example/downgraded.m4a")
        client.get_play_path.assert_called_once_with(
            BOOK_ENTITY_TYPE,
            46111471,
            4611147100000004,
            4,
            op_type=1,
            track_id=1038301964,
            quality=3,
        )

    def test_book_detail_unwraps_app_payload(self):
        manager = LRTSManager()
        client = mock.Mock()
        client.book_detail.return_value = {
            "status": 0,
            "data": {
                "bookDetail": {
                    "id": 45151,
                    "name": "民调局异闻录后传",
                    "author": "耳东水寿",
                    "announcer": "小川说书",
                    "sections": 413,
                }
            },
        }
        manager._client = client

        detail = manager.get_book_detail("https://www.lrts.me/book/45151")

        self.assertEqual(detail["id"], "1:45151")
        self.assertEqual(detail["title"], "民调局异闻录后传")
        self.assertEqual(detail["episodes"], 413)


class LrtsAdaptivePacingTest(unittest.TestCase):
    """自适应节流：连续成功自动提速，风控响应自动降速并封顶。"""

    def setUp(self):
        self.saved = (
            lm._CURRENT_INTERVAL,
            lm._CONSECUTIVE_OK,
            lm._MIN_REQUEST_INTERVAL,
            lm._START_REQUEST_INTERVAL,
            lm._MAX_REQUEST_INTERVAL,
            lm._LAST_REQUEST_TIME,
        )
        lm._MIN_REQUEST_INTERVAL = 0.3
        lm._START_REQUEST_INTERVAL = 0.5
        lm._MAX_REQUEST_INTERVAL = 8.0
        lm._CURRENT_INTERVAL = 0.5
        lm._CONSECUTIVE_OK = 0
        lm._LAST_REQUEST_TIME = 0.0

    def tearDown(self):
        (
            lm._CURRENT_INTERVAL,
            lm._CONSECUTIVE_OK,
            lm._MIN_REQUEST_INTERVAL,
            lm._START_REQUEST_INTERVAL,
            lm._MAX_REQUEST_INTERVAL,
            lm._LAST_REQUEST_TIME,
        ) = self.saved

    def test_consecutive_successes_recover_toward_fast_floor(self):
        for _ in range(4):
            lm._note_request_ok()
        self.assertAlmostEqual(lm._CURRENT_INTERVAL, 0.3, places=6)
        for _ in range(4):
            lm._note_request_ok()
        self.assertAlmostEqual(lm._CURRENT_INTERVAL, 0.3, places=6)
        for _ in range(12):
            lm._note_request_ok()
        self.assertEqual(lm._CURRENT_INTERVAL, 0.3)  # 到达下限后不再下降

    def test_rate_limit_escalates_and_caps(self):
        lm._CURRENT_INTERVAL = 0.3
        lm._note_rate_limited()
        self.assertAlmostEqual(lm._CURRENT_INTERVAL, 0.54, places=6)
        for _ in range(10):
            lm._note_rate_limited()
        self.assertEqual(lm._CURRENT_INTERVAL, 8.0)  # 上限封顶

    def test_escalation_resets_recovery_counter(self):
        for _ in range(3):
            lm._note_request_ok()
        lm._note_rate_limited()
        self.assertEqual(lm._CONSECUTIVE_OK, 0)
        self.assertGreater(lm._CURRENT_INTERVAL, 0.5)
        for _ in range(3):
            lm._note_request_ok()
        self.assertEqual(lm._CONSECUTIVE_OK, 3)  # 未达到恢复阈值
        lm._note_request_ok()
        self.assertLess(lm._CURRENT_INTERVAL, lm._CURRENT_INTERVAL + 1)  # 第 4 次成功开始恢复
        self.assertEqual(lm._CONSECUTIVE_OK, 0)

    def test_throttle_uses_current_dynamic_interval(self):
        lm._CURRENT_INTERVAL = 1.0
        lm._LAST_REQUEST_TIME = time.time() - 10  # 很久没有请求
        with mock.patch("time.sleep") as sleep:
            lm._throttle_audio_request()
            sleep.assert_not_called()
        with mock.patch("time.sleep") as sleep:
            lm._throttle_audio_request()  # 刚请求过，必须按动态间隔等待
            sleep.assert_called_once()
            self.assertGreaterEqual(sleep.call_args[0][0], 0.9)
            self.assertLessEqual(sleep.call_args[0][0], 1.0)

    def test_get_audio_url_feeds_pacing_feedback(self):
        manager = LRTSManager()
        client = mock.Mock()
        client.get_play_path.return_value = {
            "status": 114,
            "msg": "download too frequently",
        }
        manager._client = client
        chapter_data = {
            "entity_type": BOOK_ENTITY_TYPE,
            "entity_id": 46111471,
            "section": 1,
            "id": 4611147100000001,
            "track_id": 1038301964,
        }
        before = lm._CURRENT_INTERVAL
        with (
            mock.patch("core.lrts_manager._throttle_audio_request"),
            mock.patch.object(lm, "_note_request_ok") as ok,
        ):
            with self.assertRaises(lm.RateLimitError):
                manager.get_audio_url("1:46111471", "4611147100000001", chapter_data)
        ok.assert_not_called()
        self.assertGreater(lm._CURRENT_INTERVAL, before)


if __name__ == "__main__":
    unittest.main()
