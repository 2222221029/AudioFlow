import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    class _RequestException(Exception):
        pass
    sys.modules["requests"] = types.SimpleNamespace(
        RequestException=_RequestException,
        request=lambda *args, **kwargs: None,
        post=lambda *args, **kwargs: None,
    )

if not hasattr(sys.modules["requests"], "RequestException"):
    sys.modules["requests"].RequestException = Exception

from core.audiobook_renamer import RenamePlanManager
try:
    from src.server import web_server
except ModuleNotFoundError as exc:
    web_server = None
    WEB_SERVER_IMPORT_ERROR = str(exc)
else:
    WEB_SERVER_IMPORT_ERROR = ""


@unittest.skipIf(web_server is None, f"Web API dependencies unavailable: {WEB_SERVER_IMPORT_ERROR}")
class RenameFolderApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = self.root / "本地专辑"
        self.book.mkdir()
        (self.book / "0001-第1集 开始.m4a").write_bytes(b"audio")
        (self.root / ".audioflow-trash" / "old").mkdir(parents=True)
        (self.root / ".audioflow-trash" / "old" / "0001-第1集 旧文件.m4a").write_bytes(b"audio")
        (self.root / ".hidden").mkdir()
        (self.root / ".hidden" / "0001-第1集 隐藏.m4a").write_bytes(b"audio")
        self.manager = RenamePlanManager(self.root / "config" / "rename_plans.json")
        self.old_manager = web_server.rename_plan_manager
        web_server.rename_plan_manager = self.manager
        self.download_dir = patch.object(web_server, "resolve_download_dir", return_value=str(self.root))
        self.user = patch.object(web_server, "current_user", return_value={"username": "test"})
        self.notify = patch.object(web_server.notification_manager, "notify")
        self.download_dir.start()
        self.user.start()
        self.notify.start()
        web_server.app.config.update(TESTING=True)
        self.client = web_server.app.test_client()

    def tearDown(self):
        self.notify.stop()
        self.user.stop()
        self.download_dir.stop()
        web_server.rename_plan_manager = self.old_manager
        self.temp.cleanup()

    def test_folder_listing_excludes_hidden_and_trash_directories(self):
        response = self.client.get("/api/rename-plans/folders")

        self.assertEqual(response.status_code, 200)
        folders = response.get_json()["folders"]
        self.assertEqual([item["relative_path"] for item in folders], ["本地专辑"])
        self.assertEqual(folders[0]["audio_count"], 1)

    def test_manual_folder_plan_review_and_confirm(self):
        response = self.client.post("/api/rename-plans/analyze-folder", json={"relative_path": "本地专辑"})
        self.assertEqual(response.status_code, 200)
        plan = response.get_json()["plan"]
        self.assertEqual(plan["origin_source"], "manual")
        self.assertEqual(plan["task_id"], "folder:本地专辑")

        reviewed = self.client.post(
            f"/api/rename-plans/{plan['id']}/review",
            json={"configuration": plan["configuration"]},
        )
        self.assertEqual(reviewed.status_code, 200)
        completed = self.client.post(f"/api/rename-plans/{plan['id']}/confirm", json={})
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.get_json()["plan"]["status"], "completed")
        self.assertTrue((self.book / "0001-《本地专辑》第001集 开始.m4a").exists())

    def test_folder_path_traversal_is_rejected(self):
        response = self.client.post("/api/rename-plans/analyze-folder", json={"relative_path": "../本地专辑"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("下载目录内", response.get_json()["error"])

    def test_duplicate_folder_plan_is_reused_until_replaced(self):
        first = self.client.post("/api/rename-plans/analyze-folder", json={"relative_path": "本地专辑"}).get_json()["plan"]
        second = self.client.post("/api/rename-plans/analyze-folder", json={"relative_path": "本地专辑"}).get_json()["plan"]
        self.assertEqual(first["id"], second["id"])

        replaced = self.client.post("/api/rename-plans/analyze-folder", json={"relative_path": "本地专辑", "replace": True}).get_json()["plan"]
        self.assertNotEqual(first["id"], replaced["id"])
        self.assertEqual(self.manager.get(first["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
