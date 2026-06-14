import os
import tempfile
import unittest

from core.auth_manager import AuthManager


class AuthManagerTest(unittest.TestCase):
    def test_default_user_login_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_user = os.environ.get("AUDIOFLOW_DEFAULT_USERNAME")
            old_password = os.environ.get("AUDIOFLOW_DEFAULT_PASSWORD")
            os.environ["AUDIOFLOW_DEFAULT_USERNAME"] = "tester"
            os.environ["AUDIOFLOW_DEFAULT_PASSWORD"] = "secret123"
            try:
                manager = AuthManager(tmp)
                token = manager.login("tester", "secret123")
                self.assertTrue(token)
                self.assertEqual(manager.user_for_session(token)["username"], "tester")
            finally:
                if old_user is None:
                    os.environ.pop("AUDIOFLOW_DEFAULT_USERNAME", None)
                else:
                    os.environ["AUDIOFLOW_DEFAULT_USERNAME"] = old_user
                if old_password is None:
                    os.environ.pop("AUDIOFLOW_DEFAULT_PASSWORD", None)
                else:
                    os.environ["AUDIOFLOW_DEFAULT_PASSWORD"] = old_password

    def test_change_password_invalidates_existing_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_password = os.environ.get("AUDIOFLOW_DEFAULT_PASSWORD")
            os.environ["AUDIOFLOW_DEFAULT_PASSWORD"] = "oldpass"
            try:
                manager = AuthManager(tmp)
                token = manager.login("admin", "oldpass")
                self.assertTrue(token)
                manager.change_password("admin", "oldpass", "newpass")
                self.assertIsNone(manager.user_for_session(token))
                self.assertIsNone(manager.login("admin", "oldpass"))
                self.assertTrue(manager.login("admin", "newpass"))
            finally:
                if old_password is None:
                    os.environ.pop("AUDIOFLOW_DEFAULT_PASSWORD", None)
                else:
                    os.environ["AUDIOFLOW_DEFAULT_PASSWORD"] = old_password

    def test_login_lockout_after_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_max = os.environ.get("AUDIOFLOW_LOGIN_MAX_FAILURES")
            old_lock = os.environ.get("AUDIOFLOW_LOGIN_LOCK_SECONDS")
            os.environ["AUDIOFLOW_LOGIN_MAX_FAILURES"] = "2"
            os.environ["AUDIOFLOW_LOGIN_LOCK_SECONDS"] = "60"
            try:
                manager = AuthManager(tmp)
                self.assertIsNone(manager.login("admin", "bad"))
                self.assertIsNone(manager.login("admin", "bad"))
                self.assertTrue(manager.is_locked("admin"))
            finally:
                if old_max is None:
                    os.environ.pop("AUDIOFLOW_LOGIN_MAX_FAILURES", None)
                else:
                    os.environ["AUDIOFLOW_LOGIN_MAX_FAILURES"] = old_max
                if old_lock is None:
                    os.environ.pop("AUDIOFLOW_LOGIN_LOCK_SECONDS", None)
                else:
                    os.environ["AUDIOFLOW_LOGIN_LOCK_SECONDS"] = old_lock


if __name__ == "__main__":
    unittest.main()
