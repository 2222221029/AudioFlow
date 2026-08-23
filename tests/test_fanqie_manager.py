import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.fanqie_manager import FanqieManager


class FanqieDownloadTest(unittest.TestCase):
    def test_download_reuses_resolved_playinfo(self):
        manager = FanqieManager()
        play = {
            "main_url": "https://audio.example/chapter.m4a",
            "backup_url": "",
            "is_encrypt": True,
        }
        portable = mock.Mock()

        def write_audio(_play, output_path, _headers):
            self.assertIs(_play, play)
            Path(output_path).write_bytes(b"audio" * 300)
            return Path(output_path)

        portable.download_chapter_audio.side_effect = write_audio
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "core.fanqie_tingshu_manager._load_wanzheng_module",
            return_value=portable,
        ), mock.patch.object(manager, "_get_play_dict") as resolve:
            output = Path(tmp) / "chapter.m4a"
            ok = manager.download_changting_chapter(
                "123", "无损真人录制", str(output), play=play
            )

        self.assertTrue(ok)
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
