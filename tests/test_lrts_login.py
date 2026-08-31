import unittest
from unittest import mock

from core import lrts_manager as lm
from core.lrts_manager import (
    LRTS_CAPTCHA_APP_ID,
    LrtsAppClient,
    LrtsLoginSessionExpired,
    LrtsLoginSessionStore,
    lrts_send_sms_code,
    lrts_sms_login,
    parse_lrts_credentials,
)


class FakeLoginClient:
    def __init__(self, imei=None):
        self.imei = imei or "generated-device"
        self.token = ""
        self.fetch_count = 0
        self.send_calls = []
        self.login_calls = []
        self.send_response = {"status": 0, "msg": "ok"}
        self.login_response = {
            "status": 0,
            "token": "user-token",
            "userId": 42,
            "nickname": "listener",
        }
        self.session = mock.Mock()

    def fetch_temp_token(self):
        self.fetch_count += 1
        self.token = "temp-token"
        return self.token

    def send_sms_code(self, phone, **kwargs):
        self.send_calls.append((phone, kwargs))
        return dict(self.send_response)

    def sms_login(self, phone, code):
        self.login_calls.append((phone, code))
        return dict(self.login_response)


class LrtsLoginSessionTest(unittest.TestCase):
    phone = "13800138000"

    def make_store(self, client, ttl=900, clock=None):
        return LrtsLoginSessionStore(
            ttl=ttl,
            client_factory=lambda imei=None: client,
            clock=clock,
        )

    def test_first_send_requests_official_slider_without_calling_upstream(self):
        client = FakeLoginClient(imei="stable-device")
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            result = lrts_send_sms_code(self.phone, imei="stable-device")

        self.assertTrue(result["_requires_slider"])
        self.assertEqual(result["_captcha_app_id"], LRTS_CAPTCHA_APP_ID)
        self.assertTrue(result["_session_id"])
        self.assertEqual(client.imei, "stable-device")
        self.assertEqual(client.fetch_count, 0)
        self.assertEqual(client.send_calls, [])

    def test_slider_proof_sends_sms_and_login_reuses_same_client(self):
        client = FakeLoginClient(imei="stable-device")
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            challenge = lrts_send_sms_code(self.phone, imei="stable-device")
            session_id = challenge["_session_id"]
            sent = lrts_send_sms_code(
                self.phone,
                session_id=session_id,
                swipe_ticket="official-ticket",
                randstr="official-randstr",
                imei="ignored-new-device",
            )
            login, credential_text = lrts_sms_login(
                self.phone,
                "123456",
                session_id=session_id,
            )

        self.assertEqual(sent["status"], 0)
        self.assertEqual(client.fetch_count, 1)
        self.assertEqual(client.send_calls, [(
            self.phone,
            {
                "login_key": "",
                "swipe_ticket": "official-ticket",
                "randstr": "official-randstr",
            },
        )])
        self.assertEqual(client.login_calls, [(self.phone, "123456")])
        self.assertEqual(login["userId"], 42)
        credential = parse_lrts_credentials(credential_text)
        self.assertEqual(credential["imei"], "stable-device")
        self.assertEqual(credential["token"], "user-token")
        self.assertNotIn(session_id, store.sessions)
        client.session.close.assert_called_once()

    def test_upstream_slider_error_returns_a_new_challenge_on_same_session(self):
        client = FakeLoginClient()
        client.send_response = {"status": 460, "msg": "请做滑动验证校验"}
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            first = lrts_send_sms_code(self.phone)
            result = lrts_send_sms_code(
                self.phone,
                session_id=first["_session_id"],
                swipe_ticket="expired-ticket",
                randstr="expired-randstr",
            )

        self.assertTrue(result["_requires_slider"])
        self.assertEqual(result["_session_id"], first["_session_id"])
        self.assertFalse(store.sessions[first["_session_id"]].code_sent)

    def test_slider_status_without_message_returns_a_new_challenge(self):
        client = FakeLoginClient()
        client.send_response = {"status": 460}
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            first = lrts_send_sms_code(self.phone)
            result = lrts_send_sms_code(
                self.phone,
                session_id=first["_session_id"],
                swipe_ticket="expired-ticket",
                randstr="expired-randstr",
            )

        self.assertTrue(result["_requires_slider"])
        self.assertEqual(result["_session_id"], first["_session_id"])

    def test_nested_login_payload_is_flattened_and_saved(self):
        client = FakeLoginClient(imei="stable-device")
        client.login_response = {
            "status": "0",
            "data": {"token": "nested-token", "userId": 99, "nickname": "nested-user"},
        }
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            challenge = lrts_send_sms_code(self.phone)
            session_id = challenge["_session_id"]
            lrts_send_sms_code(
                self.phone,
                session_id=session_id,
                swipe_ticket="official-ticket",
                randstr="official-randstr",
            )
            response, credential_text = lrts_sms_login(self.phone, "123456", session_id=session_id)

        credential = parse_lrts_credentials(credential_text)
        self.assertEqual(response["userId"], 99)
        self.assertEqual(credential["token"], "nested-token")
        self.assertEqual(credential["nickname"], "nested-user")

    def test_success_without_app_token_keeps_session_for_retry(self):
        client = FakeLoginClient()
        client.login_response = {"status": 0, "userId": 42}
        store = self.make_store(client)

        with mock.patch.object(lm, "_LRTS_LOGIN_SESSIONS", store):
            challenge = lrts_send_sms_code(self.phone)
            session_id = challenge["_session_id"]
            lrts_send_sms_code(
                self.phone,
                session_id=session_id,
                swipe_ticket="official-ticket",
                randstr="official-randstr",
            )
            response, credential_text = lrts_sms_login(self.phone, "123456", session_id=session_id)

        self.assertEqual(response["status"], -1)
        self.assertEqual(credential_text, "")
        self.assertIn(session_id, store.sessions)

    def test_session_expiry_closes_client_and_requires_a_new_slider(self):
        now = [100.0]
        client = FakeLoginClient()
        store = self.make_store(client, ttl=10, clock=lambda: now[0])
        session = store.get_or_create(self.phone)
        now[0] = 110.0

        with self.assertRaisesRegex(LrtsLoginSessionExpired, "已过期"):
            store.get(session.session_id, self.phone)

        client.session.close.assert_called_once()

    def test_client_uses_official_slider_parameter_names(self):
        client = LrtsAppClient(imei="stable-device", token="temp-token")
        client.get = mock.Mock(return_value={"status": 0})

        client.send_sms_code(
            self.phone,
            login_key="login-key",
            swipe_ticket="official-ticket",
            randstr="official-randstr",
        )

        client.get.assert_called_once()
        params = client.get.call_args.args[2]
        self.assertEqual(params["loginKey"], "login-key")
        self.assertEqual(params["swipeTicket"], "official-ticket")
        self.assertEqual(params["randstr"], "official-randstr")


if __name__ == "__main__":
    unittest.main()
