import json
import tempfile
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

    def test_load_tasks_recovers_stopping_task_and_clears_inflight_states(self):
        payload = {
            "tasks": {
                "task-1": {
                    "id": "task-1",
                    "status": "stopping",
                    "chapter_states": {
                        "1": {"status": "success"},
                        "2": {"status": "downloading"},
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            tasks_file = Path(tmp) / "tasks.json"
            tasks_file.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(web_server, "TASKS_FILE", tasks_file):
                loaded = web_server.load_tasks()

        task = loaded["task-1"]
        self.assertEqual(task["status"], "interrupted")
        self.assertEqual(task["failure_reason"], "服务重启中断")
        self.assertEqual(set(task["chapter_states"]), {"1"})
        self.assertIsNotNone(task["finished_at"])

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

    def test_download_origin_marks_only_manual_tasks_for_organization(self):
        album = {"id": "album-1", "title": "Example", "platform": "测试"}
        chapter = {"id": "track-1", "title": "第1集 One"}
        with (
            mock.patch.object(web_server, "save_tasks"),
            mock.patch.object(web_server, "_write_album_source_file"),
            mock.patch.object(web_server.threading, "Thread"),
        ):
            manual = web_server.start_download_task("manual-task", album, [chapter], {}, source="web")
            subscription = web_server.start_download_task(
                "subscription-task", {**album, "id": "album-2"}, [chapter], {}, source="auto-subscription"
            )

        self.assertEqual(manual["origin_source"], "web")
        self.assertTrue(manual["organize_after_download"])
        self.assertEqual(subscription["origin_source"], "auto-subscription")
        self.assertFalse(subscription["organize_after_download"])

    def test_retry_preserves_original_manual_or_subscription_source(self):
        album = {"id": "album-1", "title": "Example", "platform": "测试"}
        chapter = {"id": "track-1", "title": "第1集 One"}
        for origin, expected in (("web", True), ("auto-subscription", False)):
            task = {
                "id": "retry-task", "status": "partial", "album": album,
                "chapters": [chapter], "options": {}, "origin_source": origin,
            }
            with mock.patch.object(web_server, "start_download_task", return_value={"id": "retry-task"}) as start:
                retried, error = web_server.retry_existing_download_task(
                    "retry-task", task, source="retry:retry-task"
                )
            self.assertIsNone(error)
            self.assertEqual(start.call_args.kwargs["origin_source"], origin)
            self.assertEqual(web_server.manual_download_origin(origin), expected)

    def test_automatic_plan_scheduler_skips_subscription_tasks(self):
        with web_server.task_lock:
            web_server.tasks.update({
                "manual": {"id": "manual", "organize_after_download": True},
                "subscription": {"id": "subscription", "organize_after_download": False},
            })
        with (
            mock.patch.object(web_server, "manual_organize_mode", return_value="review"),
            mock.patch.object(web_server.threading, "Thread") as thread,
        ):
            web_server.schedule_rename_plan("subscription")
            thread.assert_not_called()
            web_server.schedule_rename_plan("manual")
            thread.assert_called_once()

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

    def test_download_counts_and_detail_merge_live_chapter_states(self):
        task = {
            "id": "task-detail",
            "status": "running",
            "total": 4,
            "success": 0,
            "failed": 0,
            "chapters": [
                {"id": "1", "title": "One"},
                {"id": "2", "title": "Two"},
                {"id": "3", "title": "Three"},
                {"id": "4", "title": "Four"},
            ],
            "chapter_states": {
                "1": {"status": "success"},
                "2": {"status": "failed", "error": "timeout"},
                "3": {"status": "downloading"},
            },
        }

        counts = web_server.download_task_counts(task)
        detail = web_server._download_task_detail(task)

        self.assertEqual(counts, {"total": 4, "success": 1, "failed": 1, "downloading": 1, "pending": 1})
        self.assertEqual(
            [chapter["download_status"] for chapter in detail["chapters"]],
            ["success", "failed", "downloading", "pending"],
        )
        self.assertEqual(detail["chapters"][1]["download_error"], "timeout")

    def test_chapter_status_updates_original_task_counts(self):
        task_id = "same-task"
        with web_server.task_lock:
            web_server.tasks[task_id] = {
                "id": task_id,
                "status": "running",
                "total": 2,
                "success": 0,
                "failed": 0,
                "chapter_states": {},
            }

        with mock.patch.object(web_server, "save_tasks"):
            web_server.update_download_chapter_status(task_id, {"id": "1"}, "downloading")
            web_server.update_download_chapter_status(task_id, {"id": "1"}, "success")
            web_server.update_download_chapter_status(task_id, {"id": "2", "_error": "network"}, "failed")

        self.assertEqual(set(web_server.tasks), {task_id})
        self.assertEqual(web_server.tasks[task_id]["success"], 1)
        self.assertEqual(web_server.tasks[task_id]["failed"], 1)
        self.assertEqual(web_server.tasks[task_id]["chapter_states"]["2"]["error"], "network")

    def test_search_results_include_subscription_and_local_summary(self):
        album = {"id": "album-1", "title": "Example", "platform": "喜马拉雅", "episodes": 3}
        subscription = {"id": "喜马拉雅:album-1", "status": "active"}
        with (
            web_server.app.test_request_context("/api/search?q=Example&platform=all"),
            mock.patch.object(web_server.search_manager, "search_books", return_value=[album]),
            mock.patch.object(web_server.subscription_manager, "get", return_value=subscription),
            mock.patch.object(
                web_server.subscription_manager,
                "stats_for",
                return_value={"total": 3, "downloaded": 2, "missing": 1, "restricted": 0},
            ),
        ):
            response = web_server.api_search()

        library = response.get_json()["results"][0]["library"]
        self.assertTrue(library["subscribed"])
        self.assertEqual((library["downloaded"], library["total"], library["missing"]), (2, 3, 1))

    def test_download_task_rejects_mismatched_exact_ximalaya_album_id(self):
        album = {
            "id": "50069461",
            "requested_album_id": "34390396",
            "title": "我的老千江湖",
            "platform": "喜马拉雅",
        }

        with self.assertRaisesRegex(ValueError, "请求 34390396，实际任务 50069461"):
            web_server.start_download_task(
                "web-mismatch",
                album,
                [{"id": "track-1", "title": "One"}],
                {"quality": web_server.XMLY_WEB_SUBSCRIPTION_QUALITY},
            )

        with web_server.app.test_request_context(
            "/api/downloads",
            method="POST",
            json={"album": album, "all_chapters": True},
        ):
            response = web_server.api_download()

        payload = (response[0] if isinstance(response, tuple) else response).get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("请求 34390396，实际任务 50069461", payload["error"])

    def test_album_chapters_include_download_status(self):
        album = {
            "id": "album-1",
            "title": "Example",
            "platform": "喜马拉雅",
            "episodes": 2,
            "cover": "https://example.test/cover.jpg",
            "author": "Author",
            "intro": "Intro",
        }
        chapters = [{"id": "1", "title": "One"}, {"id": "2", "title": "Two"}]
        with (
            web_server.app.test_request_context("/api/album/chapters", method="POST", json={"album": album}),
            mock.patch.object(web_server.search_manager, "get_album_chapters_page", return_value=(chapters, 2)) as get_page,
            mock.patch.object(
                web_server,
                "album_chapter_download_states",
                return_value={"1": {"status": "downloaded"}, "2": {"status": "failed", "error": "timeout"}},
            ),
            mock.patch.object(web_server, "annotate_album_library", side_effect=lambda item: {**item, "library": {"subscribed": True}}),
        ):
            response = web_server.api_chapters()

        payload = response.get_json()
        self.assertEqual([chapter["download_status"] for chapter in payload["chapters"]], ["downloaded", "failed"])
        self.assertEqual(payload["chapters"][1]["download_error"], "timeout")
        self.assertTrue(payload["album"]["library"]["subscribed"])
        self.assertEqual(payload["pagination"]["total"], 2)
        get_page.assert_called_once_with("album-1", "喜马拉雅", page=1, page_size=100, voice=None)

    def test_album_chapters_marks_a_provisional_page_count_as_unknown(self):
        album = {"id": "album-1", "title": "Example", "platform": "喜马拉雅"}
        chapters = [{"id": str(index), "title": f"Chapter {index}"} for index in range(100)]
        with (
            web_server.app.test_request_context("/api/album/chapters", method="POST", json={"album": album}),
            mock.patch.object(web_server.search_manager, "get_album_chapters_page", return_value=(chapters, 0)),
            mock.patch.object(web_server, "album_chapter_download_states", return_value={}),
            mock.patch.object(web_server, "annotate_album_library", side_effect=lambda item: item),
        ):
            response = web_server.api_chapters()

        pagination = response.get_json()["pagination"]
        self.assertFalse(pagination["total_known"])
        self.assertEqual(pagination["total_pages"], 2)
        self.assertTrue(pagination["has_more"])

    def test_whole_album_download_returns_while_directory_prepares(self):
        album = {"id": "album-1", "title": "Example", "platform": "喜马拉雅", "episodes": 500}
        with (
            web_server.app.test_request_context(
                "/api/downloads",
                method="POST",
                json={"album": album, "all_chapters": True, "options": {}},
            ),
            mock.patch.object(web_server, "save_tasks"),
            mock.patch.object(web_server.threading, "Thread") as thread,
        ):
            response = web_server.api_download()

        payload = response.get_json()
        self.assertTrue(payload["preparing"])
        self.assertEqual(payload["message"], "下载任务已创建，正在后台加载完整目录")
        self.assertEqual(payload["task"]["status"], "queued")
        self.assertEqual(
            payload["task"]["task_info"]["message"],
            "下载任务已创建，正在后台加载完整目录",
        )
        self.assertEqual(payload["task"]["total"], 500)
        self.assertEqual(payload["task"]["chapters"], [])
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    def test_netease_whole_album_download_uses_complete_directory(self):
        album = {"id": "radio-1", "title": "Long Book", "platform": "网易云听书", "episodes": 1200}
        chapters = [{"id": str(index), "title": f"Chapter {index}"} for index in range(1, 1201)]
        with (
            web_server.app.test_request_context(
                "/api/downloads",
                method="POST",
                json={"album": album, "all_chapters": True, "options": {}},
            ),
            mock.patch.object(web_server, "save_tasks"),
            mock.patch.object(web_server, "load_all_album_chapters", return_value=chapters) as load_all,
            mock.patch.object(
                web_server,
                "start_download_task",
                side_effect=lambda task_id, *_args, **_kwargs: {"id": task_id},
            ) as start_download,
            mock.patch.object(web_server.threading, "Thread") as thread,
        ):
            response = web_server.api_download()
            thread.call_args.kwargs["target"]()

        payload = response.get_json()
        self.assertTrue(payload["preparing"])
        load_all.assert_called_once()
        self.assertEqual(load_all.call_args.args[0]["id"], "radio-1")
        self.assertEqual(load_all.call_args.args[0]["platform"], "网易云听书")
        self.assertIsNone(load_all.call_args.args[1])
        self.assertEqual(len(start_download.call_args.args[2]), 1200)

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

    def test_subscription_download_rejects_stale_ximalaya_quality_from_import(self):
        album = {"id": "1", "platform": "喜马拉雅"}

        options = web_server.subscription_download_options(
            {"subscription_quality": "M4A 96K"}, album
        )

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

    def test_subscription_check_queues_missing_chapters_with_saved_ximalaya_quality(self):
        sid = "喜马拉雅:album-quality"
        chapter = {"id": "track-1", "title": "One"}
        album = {"id": "album-quality", "title": "Example", "platform": "喜马拉雅"}
        item = {
            "id": sid,
            "album": album,
            "platform": "喜马拉雅",
            "subscription_quality": "杜比全景声优先（自动降级）",
            "chapters": [],
        }
        diff = {
            "missing": [chapter],
            "restricted_count": 0,
            "deferred_failed_count": 0,
        }
        with (
            mock.patch.object(web_server.subscription_manager, "get", return_value=item),
            mock.patch.object(web_server.search_manager, "get_album_chapters", return_value=[chapter]),
            mock.patch.object(web_server.subscription_manager, "diff_chapters", return_value=diff),
            mock.patch.object(web_server.subscription_manager, "update_check_result"),
            mock.patch.object(web_server.subscription_manager, "stats_for", return_value={}),
            mock.patch.object(web_server, "active_task_chapter_keys", return_value=set()),
            mock.patch.object(web_server, "get_album_voices", return_value=[]),
            mock.patch.object(web_server, "start_download_task") as start_download,
            mock.patch.object(web_server.notification_manager, "notify"),
        ):
            result = web_server._run_subscription_check(sid, queue_missing=True)

        self.assertTrue(result["queued"])
        options = start_download.call_args.args[3]
        self.assertEqual(options["quality"], "杜比全景声优先（自动降级）")

    def test_netease_subscription_queues_every_missing_chapter(self):
        sid = "网易云听书:radio-1"
        album = {"id": "radio-1", "title": "Long Book", "platform": "网易云听书"}
        chapters = [{"id": str(index), "title": f"Chapter {index}"} for index in range(1, 1201)]
        item = {"id": sid, "album": album, "platform": "网易云听书", "chapters": []}
        diff = {"missing": chapters, "restricted_count": 0, "deferred_failed_count": 0}
        with (
            mock.patch.object(web_server.subscription_manager, "get", return_value=item),
            mock.patch.object(web_server.search_manager, "get_album_chapters", return_value=chapters),
            mock.patch.object(web_server.subscription_manager, "diff_chapters", return_value=diff),
            mock.patch.object(web_server.subscription_manager, "update_check_result"),
            mock.patch.object(web_server.subscription_manager, "stats_for", return_value={}),
            mock.patch.object(web_server, "active_task_chapter_keys", return_value=set()),
            mock.patch.object(web_server, "get_album_voices", return_value=[]),
            mock.patch.object(web_server, "start_download_task") as start_download,
            mock.patch.object(web_server.notification_manager, "notify"),
        ):
            result = web_server._run_subscription_check(sid, queue_missing=True)

        self.assertEqual(result["chapter_count"], 1200)
        self.assertEqual(result["queued_chapter_count"], 1200)
        self.assertEqual(len(start_download.call_args.args[2]), 1200)
        self.assertEqual(start_download.call_args.kwargs["source"], "subscription-check")

    def test_new_ximalaya_subscription_defaults_to_web_without_resetting_existing_override(self):
        album = {"id": "album-quality", "title": "Example", "platform": "喜马拉雅"}
        record = {"id": "喜马拉雅:album-quality", "album": album, "status": "active"}
        for existing, expected in (
            (None, web_server.XMLY_WEB_SUBSCRIPTION_QUALITY),
            ({**record, "subscription_quality": "杜比全景声优先（自动降级）"}, None),
        ):
            with self.subTest(existing=bool(existing)):
                with (
                    web_server.app.test_request_context(
                        "/api/subscriptions",
                        method="POST",
                        json={"album": album, "chapters": []},
                    ),
                    mock.patch.object(web_server.subscription_manager, "get", return_value=existing),
                    mock.patch.object(web_server.subscription_manager, "add_or_update", return_value=record) as add,
                    mock.patch.object(web_server.subscription_manager, "settings", return_value={"enabled": False}),
                    mock.patch.object(web_server, "album_library_summary", return_value={"subscribed": True}),
                ):
                    response = web_server.api_subscribe()

                self.assertTrue(response.get_json()["ok"])
                self.assertEqual(add.call_args.kwargs["subscription_quality"], expected)

    def test_personal_sync_defaults_new_ximalaya_album_without_resetting_existing_override(self):
        albums = [
            {"id": "new-album", "title": "New", "platform": "喜马拉雅"},
            {"id": "saved-album", "title": "Saved", "platform": "喜马拉雅"},
        ]
        saved = {
            "id": "喜马拉雅:saved-album",
            "album": albums[1],
            "subscription_quality": "杜比全景声优先（自动降级）",
            "status": "active",
        }

        def get_subscription(sid):
            return saved if sid == saved["id"] else None

        def add_subscription(album, _chapters, _download_dir, subscription_quality=None):
            return {
                "id": f"喜马拉雅:{album['id']}",
                "album": album,
                "subscription_quality": subscription_quality,
                "status": "active",
            }

        with (
            mock.patch.object(
                web_server.subscription_manager,
                "settings",
                return_value={"auto_download_missing": False},
            ),
            mock.patch.object(web_server, "_load_ximalaya_personal", return_value=albums),
            mock.patch.object(
                web_server.subscription_manager,
                "get",
                side_effect=get_subscription,
            ),
            mock.patch.object(
                web_server.subscription_manager,
                "add_or_update",
                side_effect=add_subscription,
            ) as add,
            mock.patch.object(web_server, "active_download_dir", return_value="downloads"),
            mock.patch.object(web_server, "start_subscription_job", return_value={"id": "job-new"}),
        ):
            result = web_server._sync_personal_ximalaya_subscriptions(force=True)

        self.assertEqual(result["added"], 1)
        self.assertEqual(
            [call.kwargs["subscription_quality"] for call in add.call_args_list],
            [web_server.XMLY_WEB_SUBSCRIPTION_QUALITY, None],
        )

    def test_subscription_quality_api_updates_one_album(self):
        sid = "喜马拉雅:album-quality"
        album = {"id": "album-quality", "title": "Example", "platform": "喜马拉雅"}
        current = {"id": sid, "album": album, "status": "active"}
        updated = {**current, "subscription_quality": "无损优先（自动降级）"}
        with (
            web_server.app.test_request_context(
                f"/api/subscriptions/{sid}",
                method="PATCH",
                json={"subscription_quality": "无损优先（自动降级）"},
            ),
            mock.patch.object(web_server.subscription_manager, "get", return_value=current),
            mock.patch.object(web_server.subscription_manager, "set_subscription_quality", return_value=updated) as setter,
            mock.patch.object(web_server, "append_background_event") as append_event,
        ):
            response = web_server.api_update_subscription(sid)

        self.assertTrue(response.get_json()["ok"])
        setter.assert_called_once_with(sid, "无损优先（自动降级）")
        append_event.assert_called_once()

    def test_wecom_subscription_defaults_to_web_and_accepts_dolby_override(self):
        album = {"id": "album-quality", "title": "Example", "platform": "喜马拉雅"}
        chapters = [{"id": "track-1", "title": "One"}]
        record = {"id": "喜马拉雅:album-quality", "album": album, "status": "active"}
        for command, expected in (
            ("订阅 1", web_server.XMLY_WEB_SUBSCRIPTION_QUALITY),
            ("订阅 1 杜比", "杜比全景声优先（自动降级）"),
            ("订阅 1 无损", "无损优先（自动降级）"),
        ):
            with self.subTest(command=command):
                with (
                    mock.patch.object(web_server, "_wecom_get_cached_album", return_value=album),
                    mock.patch.object(web_server, "_wecom_load_album_chapters", return_value=(album, chapters, None)),
                    mock.patch.object(web_server.subscription_manager, "add_or_update", return_value=record) as add,
                    mock.patch.object(web_server.subscription_manager, "settings", return_value={"enabled": False}),
                    mock.patch.object(web_server, "_wecom_push"),
                ):
                    web_server._wecom_async_command("service", "user", command)

                self.assertEqual(add.call_args.kwargs["subscription_quality"], expected)


if __name__ == "__main__":
    unittest.main()
