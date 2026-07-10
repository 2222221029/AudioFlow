import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.ximalaya_download_manager import XimalayaDownloadManager


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def iter_content(self, chunk_size=1):
        if self._body:
            yield self._body


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


if __name__ == "__main__":
    unittest.main()
