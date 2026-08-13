import unittest
from unittest import mock

from core.lrts_manager import (
    ALBUM_ENTITY_TYPE,
    BOOK_ENTITY_TYPE,
    LRTSManager,
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


if __name__ == "__main__":
    unittest.main()
