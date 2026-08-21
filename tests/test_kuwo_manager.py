import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.kuwo_manager import KuwoManager


class FakeResponse:
    def __init__(self, status_code=200, payload=None, body=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def iter_content(self, chunk_size=262144):
        del chunk_size
        yield self._body


class KuwoManagerTest(unittest.TestCase):
    def setUp(self):
        KuwoManager._download_info_cache.clear()

    def test_transient_download_info_failure_is_not_negatively_cached(self):
        manager = KuwoManager()
        failed = FakeResponse(status_code=503)
        success = FakeResponse(payload={
            "code": 200,
            "data": {"url": "http://audio.example/track.mp3", "format": "mp3", "bitrate": 128},
        })

        with mock.patch.object(manager.session, "get", side_effect=[failed, success]) as get:
            self.assertIsNone(manager._get_download_url_internal("rid-1", "mp3", 128))
            info = manager._get_download_url_internal("rid-1", "mp3", 128)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(info["url"], "http://audio.example/track.mp3")

    def test_media_http_error_is_exposed_and_partial_file_is_removed(self):
        manager = KuwoManager()
        response = FakeResponse(status_code=403)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", return_value=response):
                self.assertFalse(manager.download_audio("http://audio.example/expired", str(target)))
            self.assertFalse(target.exists())
            self.assertFalse(Path(str(target) + ".part").exists())

        self.assertIn("HTTP 403", manager.last_error)

    def test_paid_media_403_is_reported_as_restricted(self):
        manager = KuwoManager()
        denied = FakeResponse(status_code=403)
        paid = FakeResponse(payload={"code": -1, "msg": "该歌曲为付费内容，请下载酷我音乐客户端后付费收听"})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", side_effect=[denied, paid]):
                self.assertFalse(
                    manager.download_audio(
                        "http://audio.example/paid",
                        str(target),
                        chapter_id="rid-paid",
                    )
                )

        self.assertEqual(manager.last_error_type, "restricted")
        self.assertIn("付费内容", manager.last_error)

    def test_successful_media_download_is_atomically_promoted(self):
        manager = KuwoManager()
        body = b"ID3" + b"audio" * 3000
        response = FakeResponse(
            body=body,
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(body))},
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "track.mp3"
            with mock.patch.object(manager.session, "get", return_value=response):
                self.assertTrue(manager.download_audio("http://audio.example/track.mp3", str(target)))
            self.assertEqual(target.read_bytes(), body)
            self.assertFalse(Path(str(target) + ".part").exists())
            self.assertEqual(manager.last_error, "")


if __name__ == "__main__":
    unittest.main()
