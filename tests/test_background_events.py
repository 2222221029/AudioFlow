import json
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import web_server


class BackgroundEventsTests(unittest.TestCase):
    def test_append_prunes_events_to_configured_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with (
                mock.patch.object(web_server, "BACKGROUND_EVENTS_FILE", path),
                mock.patch.object(web_server, "background_events_max_keep", return_value=10),
            ):
                for index in range(25):
                    web_server.append_background_event("test", f"event-{index}")

                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 10)
                events = web_server.load_background_events()
                self.assertEqual(len(events), 10)
                self.assertEqual(events[0]["title"], "event-24")
                self.assertEqual(events[-1]["title"], "event-15")

    def test_config_update_prunes_existing_events_immediately(self):
        with (
            web_server.app.test_request_context(
                "/api/config",
                method="POST",
                json={"background_events_max_keep": 5},
            ),
            mock.patch.object(web_server.cookie_manager, "set_cookie") as set_cookie,
            mock.patch.object(web_server, "prune_background_events") as prune,
        ):
            response = web_server.api_set_config()

        self.assertTrue(response.get_json()["ok"])
        set_cookie.assert_any_call("background_events_max_keep", "10")
        prune.assert_called_once_with(10)

    def test_append_keeps_event_file_within_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with (
                mock.patch.object(web_server, "BACKGROUND_EVENTS_FILE", path),
                mock.patch.object(web_server, "BACKGROUND_EVENTS_MAX_BYTES", 64 * 1024),
            ):
                for index in range(300):
                    web_server.append_background_event(
                        "test", f"event-{index}", "x" * 500, {"index": index}
                    )

                self.assertLessEqual(path.stat().st_size, 64 * 1024)
                events = web_server.load_background_events(500)
                self.assertEqual(events[0]["title"], "event-299")
                self.assertNotEqual(events[-1]["title"], "event-0")

    def test_oversized_event_is_compacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with (
                mock.patch.object(web_server, "BACKGROUND_EVENTS_FILE", path),
                mock.patch.object(web_server, "BACKGROUND_EVENTS_MAX_BYTES", 64 * 1024),
            ):
                web_server.append_background_event(
                    "test", "oversized", "detail", {"data": "x" * (128 * 1024)}
                )

                self.assertLessEqual(path.stat().st_size, 64 * 1024)
                event = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(event["payload"]["reason"], "event_too_large")


class ContainerLogMirrorTests(unittest.TestCase):
    def test_console_line_is_written_to_rotating_server_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            handler = RotatingFileHandler(path, maxBytes=1024, backupCount=1, encoding="utf-8")
            try:
                with mock.patch.object(web_server, "_log_handler", handler):
                    web_server.mirror_console_line_to_server_log(
                        "2026-08-23 12:00:00 INFO [喜马拉雅][下载] 测试日志"
                    )
            finally:
                handler.close()

            self.assertIn("[喜马拉雅][下载] 测试日志", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
