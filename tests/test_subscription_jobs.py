import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import web_server


class SubscriptionJobsTest(unittest.TestCase):
    def tearDown(self):
        web_server.subscription_jobs.clear()
        with web_server.task_lock:
            web_server.tasks.clear()

    def test_cleanup_marks_stale_running_job_failed(self):
        web_server.subscription_jobs["job-1"] = {
            "id": "job-1",
            "sid": "album-1",
            "status": "running",
            "started_at": 100,
            "updated_at": 100,
            "created_at": 100,
        }

        with mock.patch.object(web_server, "append_background_event") as append_event:
            web_server.cleanup_subscription_jobs(
                now=100 + web_server.SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS + 1
            )

        job = web_server.subscription_jobs["job-1"]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["finished_at"], 100 + web_server.SUBSCRIPTION_JOB_RUNNING_TIMEOUT_SECONDS + 1)
        self.assertEqual(job["error"], job["message"])
        append_event.assert_called_once()

    def test_active_task_chapter_keys_are_scoped_to_album(self):
        album = {"id": "album-1", "title": "Example", "platform": "Ximalaya"}
        other_album = {"id": "album-2", "title": "Other", "platform": "Ximalaya"}
        with web_server.task_lock:
            web_server.tasks.update({
                "active-same": {"status": "running", "album": album, "chapters": [{"id": "track-1"}]},
                "active-other": {"status": "queued", "album": other_album, "chapters": [{"id": "track-2"}]},
                "done": {"status": "completed", "album": album, "chapters": [{"id": "track-3"}]},
            })

        self.assertEqual(web_server.active_task_chapter_keys(album), {"track-1"})

    def test_start_download_task_reuses_active_chapter_task(self):
        album = {"id": "album-1", "title": "Example", "platform": "Ximalaya"}
        with web_server.task_lock:
            web_server.tasks["active-same"] = {
                "id": "active-same", "status": "running", "album": album, "chapters": [{"id": "track-1"}],
            }

        task = web_server.start_download_task("new-task", album, [{"id": "track-1"}], {}, source="web")

        self.assertEqual(task["id"], "active-same")
        self.assertTrue(task["deduplicated"])

    def test_subscription_job_dedupes_same_album_regardless_of_mode(self):
        with mock.patch.object(web_server.threading, "Thread") as thread:
            first = web_server.start_subscription_job("Ximalaya:album-1", queue_missing=False)
            second = web_server.start_subscription_job("Ximalaya:album-1", queue_missing=True)

        self.assertEqual(first["id"], second["id"])
        thread.assert_called_once()

    def test_subscription_download_defaults_ximalaya_to_web_endpoint(self):
        album = {"id": "1", "platform": "喜马拉雅"}

        options = web_server.subscription_download_options({}, album)

        self.assertEqual(options["quality"], "喜马拉雅网页版接口")

    def test_subscription_download_does_not_map_generic_quality_to_kuwo_lossless(self):
        album = {"id": "2", "platform": "酷我听书"}
        with mock.patch.object(web_server.subscription_manager, "settings", return_value={"quality": "M4A 96K"}):
            options = web_server.subscription_download_options({}, album)

        self.assertEqual(options["quality"], "kuwo:standard")

    def test_subscription_download_preserves_explicit_per_subscription_quality(self):
        album = {"id": "3", "platform": "酷我听书"}

        options = web_server.subscription_download_options(
            {"subscription_quality": "kuwo:high"}, album, voice={"name": "voice"}
        )

        self.assertEqual(options["quality"], "kuwo:high")
        self.assertEqual(options["voice"], {"name": "voice"})


if __name__ == "__main__":
    unittest.main()
