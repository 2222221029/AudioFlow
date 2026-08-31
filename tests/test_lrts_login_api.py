import unittest
from unittest import mock

from src.server import web_server


class LrtsLoginAPITest(unittest.TestCase):
    phone = "13800138000"

    def test_send_code_returns_structured_official_slider_challenge(self):
        challenge = {
            "status": 0,
            "msg": "请完成滑动验证后继续",
            "_requires_slider": True,
            "_session_id": "login-session",
            "_captcha_app_id": "2082591240",
            "_captcha_script_url": "https://turing.captcha.qcloud.com/TJCaptcha.js",
        }
        with (
            web_server.app.test_request_context(
                "/api/lrts/send-code",
                method="POST",
                json={"phone": self.phone},
            ),
            mock.patch.object(web_server, "lrts_send_sms_code", return_value=challenge) as send,
            mock.patch.object(web_server, "_lrts_login_device_id", return_value="stable-device"),
        ):
            response = web_server.api_lrts_send_code()

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["requires_slider"])
        self.assertEqual(payload["session_id"], "login-session")
        self.assertEqual(payload["captcha_app_id"], "2082591240")
        send.assert_called_once_with(
            self.phone,
            session_id="",
            swipe_ticket="",
            randstr="",
            imei="stable-device",
        )

    def test_slider_retry_forwards_proof_and_same_session(self):
        with (
            web_server.app.test_request_context(
                "/api/lrts/send-code",
                method="POST",
                json={
                    "phone": self.phone,
                    "session_id": "login-session",
                    "swipe_ticket": "official-ticket",
                    "randstr": "official-randstr",
                },
            ),
            mock.patch.object(
                web_server,
                "lrts_send_sms_code",
                return_value={"status": 0, "_session_id": "login-session"},
            ) as send,
            mock.patch.object(web_server, "_lrts_login_device_id", return_value="stable-device"),
        ):
            response = web_server.api_lrts_send_code()

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session_id"], "login-session")
        send.assert_called_once_with(
            self.phone,
            session_id="login-session",
            swipe_ticket="official-ticket",
            randstr="official-randstr",
            imei="stable-device",
        )

    def test_send_code_accepts_string_success_status(self):
        with (
            web_server.app.test_request_context(
                "/api/lrts/send-code",
                method="POST",
                json={"phone": self.phone, "session_id": "login-session"},
            ),
            mock.patch.object(
                web_server,
                "lrts_send_sms_code",
                return_value={"status": "0", "_session_id": "login-session"},
            ),
            mock.patch.object(web_server, "_lrts_login_device_id", return_value="stable-device"),
        ):
            response = web_server.api_lrts_send_code()

        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(response.get_json()["session_id"], "login-session")

    def test_send_code_hides_internal_network_error_details(self):
        with (
            web_server.app.test_request_context(
                "/api/lrts/send-code",
                method="POST",
                json={"phone": self.phone},
            ),
            mock.patch.object(
                web_server,
                "lrts_send_sms_code",
                side_effect=OSError("secret upstream URL and device metadata"),
            ),
            mock.patch.object(web_server, "_lrts_login_device_id", return_value="stable-device"),
        ):
            response, status = web_server.api_lrts_send_code()

        payload = response.get_json()
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "发送验证码失败，请检查服务端网络后重试")
        self.assertNotIn("secret upstream", payload["error"])

    def test_login_uses_opaque_server_session_instead_of_recreating_client(self):
        credential = '{"imei":"stable-device","token":"user-token"}'
        with (
            web_server.app.test_request_context(
                "/api/personal/lrts/login",
                method="POST",
                json={
                    "phone": self.phone,
                    "code": "123456",
                    "session_id": "login-session",
                },
            ),
            mock.patch.object(
                web_server,
                "lrts_sms_login",
                return_value=({"status": 0, "userId": 42}, credential),
            ) as login,
            mock.patch.object(web_server.cookie_manager, "set_cookie") as save,
            mock.patch.object(web_server, "_personal_cookie_status", return_value={"configured": True}),
        ):
            response = web_server.api_personal_lrts_login()

        self.assertTrue(response.get_json()["ok"])
        login.assert_called_once_with(
            self.phone,
            "123456",
            imei="",
            temp_token="",
            session_id="login-session",
        )
        save.assert_called_once_with("personal_lrts", credential)


if __name__ == "__main__":
    unittest.main()
