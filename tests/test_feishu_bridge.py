import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.feishu_bridge import FeishuActionStore, FeishuBridge, feishu_authorized
from core.notification_manager import NotificationManager


class FeishuAuthorizationTests(unittest.TestCase):
    def test_empty_allowlists_fail_closed(self):
        self.assertFalse(feishu_authorized({}, "ou_user", "oc_chat"))

    def test_configured_allowlists_are_all_enforced(self):
        config = {"allowed_users": "ou_ok\nou_other", "allowed_chats": ["oc_ok"]}
        self.assertTrue(feishu_authorized(config, "ou_ok", "oc_ok"))
        self.assertFalse(feishu_authorized(config, "ou_bad", "oc_ok"))
        self.assertFalse(feishu_authorized(config, "ou_ok", "oc_bad"))


class FeishuActionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeishuActionStore(Path(self.temp.name) / "actions.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_action_is_bound_and_one_time(self):
        nonce = self.store.create("plan123", "svc1", user_id="ou_user", chat_id="oc_chat")
        with self.assertRaises(PermissionError):
            self.store.consume(nonce, "svc1", "plan123", "ou_wrong", "oc_chat")
        result = self.store.consume(nonce, "svc1", "plan123", "ou_user", "oc_chat")
        self.assertEqual(result["plan_id"], "plan123")
        with self.assertRaisesRegex(ValueError, "已经处理"):
            self.store.consume(nonce, "svc1", "plan123", "ou_user", "oc_chat")


class FeishuBridgeCardTests(unittest.TestCase):
    def test_sdk_shaped_card_event_confirms_server_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifications = Mock()
            notifications.load.return_value = {"services": [{
                "id": "svc1", "type": "feishu", "enabled": True,
                "config": {"allowed_users": "ou_user", "allowed_chats": "oc_chat"},
            }]}
            rename_plans = Mock()
            rename_plans.confirm.return_value = {"id": "plan123", "status": "completed"}
            bridge = FeishuBridge(notifications, Mock(), rename_plans, Path(tmp) / "actions.json")
            nonce = bridge.actions.create("plan123", "svc1", user_id="ou_user", chat_id="oc_chat")
            event = {"event": {
                "operator": {"open_id": "ou_user"},
                "context": {"open_chat_id": "oc_chat"},
                "action": {"value": {"action": "confirm_rename", "plan_id": "plan123", "nonce": nonce}},
            }}
            response = bridge._on_card_action("svc1", event)
            self.assertEqual(response["toast"]["type"], "success")
            rename_plans.confirm.assert_called_once_with("plan123")

    def test_review_card_keeps_risky_files_then_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifications = Mock()
            notifications.load.return_value = {"services": [{
                "id": "svc1", "type": "feishu", "enabled": True,
                "config": {"allowed_users": "ou_user", "allowed_chats": "oc_chat"},
            }]}
            rename_plans = Mock()
            rename_plans.resolve_safe.return_value = {"id": "plan123", "status": "pending_confirmation"}
            rename_plans.confirm.return_value = {"id": "plan123", "status": "completed"}
            bridge = FeishuBridge(notifications, Mock(), rename_plans, Path(tmp) / "actions.json")
            nonce = bridge.actions.create("plan123", "svc1", user_id="ou_user", chat_id="oc_chat")
            event = {"event": {
                "operator": {"open_id": "ou_user"},
                "context": {"open_chat_id": "oc_chat"},
                "action": {"value": {"action": "resolve_safe_rename", "plan_id": "plan123", "nonce": nonce}},
            }}
            response = bridge._on_card_action("svc1", event)
            self.assertEqual(response["toast"]["type"], "success")
            rename_plans.resolve_safe.assert_called_once_with("plan123")
            rename_plans.confirm.assert_called_once_with("plan123")


class FeishuNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "notifications.json"
        self.manager = NotificationManager(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def _service(self):
        return {
            "id": "feishu-main",
            "name": "飞书",
            "type": "feishu",
            "enabled": True,
            "switchs": [],
            "config": {
                "app_id": "cli_test",
                "app_secret": "very-secret-value",
                "receive_id": "ou_user",
                "receive_id_type": "open_id",
                "allowed_users": "ou_user",
            },
        }

    def test_app_secret_is_encrypted_and_redacted(self):
        self.manager.save({"enabled": True, "services": [self._service()]})
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("very-secret-value", raw)
        self.assertIn("app_secret_encrypted", raw)
        public = self.manager.public_config()
        public_config = public["services"][0]["config"]
        self.assertNotIn("app_secret", public_config)
        self.assertNotIn("very-secret-value", json.dumps(public))
        self.assertTrue(public["services"][0]["configured"])

    def test_rename_notification_uses_interactive_one_time_card(self):
        self.manager.save({"enabled": True, "scenes": {"rename_confirmation": True}, "services": [self._service()]})
        with patch.object(self.manager, "send_feishu_message", return_value={"provider": "feishu"}) as send:
            result = self.manager.notify(
                "rename_confirmation",
                "待确认重命名：测试书",
                "请确认",
                {"plan_id": "abc1234567", "planned": 3, "issues": 0},
            )
        self.assertEqual(result["sent"], 1)
        card, message_type = send.call_args.args[1:3]
        self.assertEqual(message_type, "interactive")
        actions = card["elements"][1]["actions"]
        self.assertEqual(actions[0]["value"]["action"], "confirm_rename")
        self.assertEqual(actions[1]["value"]["action"], "cancel_rename")
        self.assertEqual(actions[0]["value"]["nonce"], actions[1]["value"]["nonce"])

    def test_review_notification_offers_safe_execution_card(self):
        self.manager.save({"enabled": True, "scenes": {"rename_confirmation": True}, "services": [self._service()]})
        with patch.object(self.manager, "send_feishu_message", return_value={"provider": "feishu"}) as send:
            result = self.manager.notify(
                "rename_confirmation",
                "需要复核：测试书",
                "请复核",
                {"plan_id": "abc1234567", "plan_status": "needs_review", "planned": 3, "issues": 2},
            )
        self.assertEqual(result["sent"], 1)
        card = send.call_args.args[1]
        actions = card["elements"][1]["actions"]
        self.assertEqual(actions[0]["value"]["action"], "resolve_safe_rename")

    def test_text_message_uses_official_feishu_api(self):
        config = self._service()["config"]
        token_response = Mock()
        token_response.json.return_value = {"code": 0, "tenant_access_token": "token", "expire": 7200}
        token_response.raise_for_status.return_value = None
        send_response = Mock()
        send_response.json.return_value = {"code": 0, "data": {"message_id": "om_1"}}
        send_response.raise_for_status.return_value = None
        with patch("core.notification_manager.requests.post", side_effect=[token_response, send_response]) as post:
            self.manager.send_feishu_text(config, "hello")
        self.assertIn("tenant_access_token/internal", post.call_args_list[0].args[0])
        self.assertEqual(post.call_args_list[1].kwargs["params"]["receive_id_type"], "open_id")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["msg_type"], "text")


if __name__ == "__main__":
    unittest.main()
