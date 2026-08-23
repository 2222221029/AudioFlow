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
            web_server.task_workers.clear()

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

    def test_retry_failed_reuses_original_task(self):
        task_id = "failed-task"
        album = {"id": "album-1", "title": "Example", "platform": "Ximalaya"}
        failed_chapter = {"id": "track-1", "title": "Chapter 1", "_error": "timeout"}
        successful_chapter = {"id": "track-2", "title": "Chapter 2"}
        with web_server.task_lock:
            web_server.tasks[task_id] = {
                "id": task_id,
                "status": "partial",
                "album": album,
                "chapters": [failed_chapter, successful_chapter],
                "failed_chapters": [failed_chapter],
                "success_chapters": [successful_chapter],
                "options": {"download_dir": "downloads"},
                "success": 1,
                "failed": 1,
                "error": "timeout",
                "failure_reason": "network",
                "created_at": 123,
                "finished_at": 456,
            }

        with (
            web_server.app.test_request_context(),
            mock.patch.object(web_server, "save_tasks"),
            mock.patch.object(web_server, "_write_album_source_file"),
            mock.patch.object(web_server.threading, "Thread") as thread,
        ):
            response = web_server.api_download_retry_failed(task_id)

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(set(web_server.tasks), {task_id})
        retried = web_server.tasks[task_id]
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["created_at"], 123)
        self.assertEqual(retried["chapters"], [failed_chapter, successful_chapter])
        self.assertEqual(retried["total"], 2)
        self.assertEqual(retried["success"], 0)
        self.assertEqual(retried["failed"], 0)
        self.assertEqual(retried["failed_chapters"], [])
        self.assertEqual(retried["error"], "")
        self.assertIsNone(retried["finished_at"])
        thread.return_value.start.assert_called_once_with()

    def test_retry_unfinished_reuses_each_original_task(self):
        with web_server.task_lock:
            web_server.tasks.update({
                "failed-one": {
                    "id": "failed-one",
                    "status": "failed",
                    "album": {"id": "album-1", "title": "One", "platform": "Ximalaya"},
                    "chapters": [{"id": "track-1", "title": "Chapter 1"}],
                    "options": {"download_dir": "downloads"},
                    "created_at": 101,
                },
                "partial-two": {
                    "id": "partial-two",
                    "status": "partial",
                    "album": {"id": "album-2", "title": "Two", "platform": "Ximalaya"},
                    "chapters": [{"id": "track-2", "title": "Chapter 2"}],
                    "failed_chapters": [{"id": "track-2", "title": "Chapter 2"}],
                    "options": {"download_dir": "downloads"},
                    "created_at": 202,
                },
            })

        with (
            web_server.app.test_request_context(),
            mock.patch.object(web_server, "save_tasks"),
            mock.patch.object(web_server, "_write_album_source_file"),
            mock.patch.object(web_server, "append_background_event"),
            mock.patch.object(web_server.threading, "Thread") as thread,
        ):
            response = web_server.api_retry_unfinished_downloads()

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["skipped"], [])
        self.assertEqual(set(web_server.tasks), {"failed-one", "partial-two"})
        self.assertTrue(all(task["status"] == "queued" for task in web_server.tasks.values()))
        self.assertEqual(web_server.tasks["failed-one"]["created_at"], 101)
        self.assertEqual(web_server.tasks["partial-two"]["created_at"], 202)
        self.assertEqual(thread.call_count, 2)

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
