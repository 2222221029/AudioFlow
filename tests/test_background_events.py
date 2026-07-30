import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import web_server


class BackgroundEventsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
