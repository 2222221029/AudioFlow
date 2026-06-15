import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import web_server


class SourceMetadataTest(unittest.TestCase):
    def test_source_file_uses_platform_album_folder_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            album = {"id": "1", "title": "Ghost", "platform": "Ximalaya"}
            with mock.patch.object(web_server.cookie_manager, "get_cookie", return_value="true"):
                web_server._write_album_source_file(album, {"download_dir": tmp}, task_id="task-1")

            expected = Path(tmp) / "Ximalaya" / "Ghost" / web_server.SOURCE_INFO_FILE
            legacy = Path(tmp) / "Ghost" / web_server.SOURCE_INFO_FILE
            self.assertTrue(expected.exists())
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
