import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.ximalaya_download_manager import XimalayaDownloadManager


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"", json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._json_data = json_data

    def iter_content(self, chunk_size=1):
        if self._body:
            yield self._body

    def json(self):
        return self._json_data


class XimalayaDownloadManagerTest(unittest.TestCase):
    def test_m4a_permission_response_is_exposed_as_restricted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=example")
            response = FakeResponse(
                headers={"content-type": "application/json", "content-length": "32"},
                body=b'{"ret":130,"msg":"VIP required"}',
            )
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=response):
                    ok = manager.download_audio_by_quality("123", "M4A_96K", str(Path(tmp) / "track.m4a"))

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertTrue(manager.last_error)

    def test_http_forbidden_is_exposed_as_restricted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            response = FakeResponse(status_code=403)
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(manager.session, "get", return_value=response):
                    ok = manager.download_audio_by_quality("123", "M4A_96K", str(Path(tmp) / "track.m4a"))

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")

    def test_successful_member_redirect_keeps_original_download_path(self):
        audio = FakeResponse(
            headers={"content-type": "audio/mp4", "content-length": "4096"},
            body=b"ftyp" + (b"audio" * 1000),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=member")
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(manager.session, "get", return_value=audio) as get:
                    ok = manager.download_audio_by_quality(
                        "member-track", "M4A_96K", str(save_path)
                    )

        self.assertTrue(ok)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            get.call_args.args[0],
            "http://mobile.ximalaya.com/mobile/redirect/free/play/member-track/96",
        )
        self.assertEqual(get.call_args.kwargs["headers"]["Cookie"], "_token=member")

    def test_public_free_track_falls_back_after_redirect_ret_130(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        public_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isPaid": False,
            "isVip": False,
            "isVipFree": False,
            "hqNeedVip": False,
            "paidType": 0,
            "priceTypeId": 0,
            "priceTypeEnum": 0,
            "vipFreeType": 0,
            "vipFirstStatus": 0,
            "sampleDuration": 0,
            "priceTypes": [],
            "playPathHq": "",
            "playPathAacv224": "https://audio.example/free-track.mp3",
            "playPathAacv224Size": 4096,
        })
        audio = FakeResponse(
            headers={"content-type": "audio/mpeg", "content-length": "4096"},
            body=b"ID3" + (b"audio" * 1000),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            with tempfile.TemporaryDirectory() as tmp:
                save_path = Path(tmp) / "track.m4a"
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, public_info, audio]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "28311957", "M4A_96K", str(save_path)
                    )
                self.assertTrue(save_path.exists())
                self.assertGreater(save_path.stat().st_size, 1024)

        self.assertTrue(ok)
        self.assertEqual(manager.last_error_type, "")
        self.assertEqual(manager.last_download_source, "public_base_info:playPathAacv224")
        self.assertIn("device=ios", get.call_args_list[1].args[0])
        self.assertNotIn("Cookie", get.call_args_list[1].kwargs["headers"])
        self.assertEqual(get.call_args_list[2].args[0], "https://audio.example/free-track.mp3")

    def test_paid_track_never_uses_public_cdn_fallback(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        paid_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isPaid": True,
            "playUrl64": "https://audio.example/paid-track.mp3",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager()
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, paid_info]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "paid-track", "M4A_96K", str(Path(tmp) / "track.m4a")
                    )

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertEqual(get.call_count, 2)
        self.assertNotIn("Cookie", get.call_args_list[1].kwargs["headers"])

    def test_vip_track_never_uses_public_cdn_fallback(self):
        redirect_error = FakeResponse(
            headers={"content-type": "application/json", "content-length": "32"},
            body=b'{"ret":130,"msg":"130","seed":0}',
        )
        vip_info = FakeResponse(json_data={
            "ret": 0,
            "isPublic": True,
            "isVipFree": True,
            "playUrl64": "https://audio.example/vip-track.mp3",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            manager = XimalayaDownloadManager(cookie_string="_token=member")
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(
                    manager.session, "get", side_effect=[redirect_error, vip_info]
                ) as get:
                    ok = manager.download_audio_by_quality(
                        "vip-track", "M4A_96K", str(Path(tmp) / "track.m4a")
                    )

        self.assertFalse(ok)
        self.assertEqual(manager.last_error_type, "restricted")
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
