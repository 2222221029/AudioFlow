import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.config import BridgeConfig
from bridge.server import BridgeService, ThreadingHTTPServer, make_handler


class FakeSigner:
    def __init__(self):
        self.calls = []

    def status(self):
        return {
            "connected": True,
            "captured_cookie": True,
            "app_version": "9.4.52.3",
            "error": "",
        }

    def ticket(self, payload):
        self.calls.append(payload)
        return {
            "x_tk": f"dynamic-{len(self.calls)}",
            "cookie": "1&*token=123456&session",
            "user_agent": "ting_test",
            "api_device": "android2",
        }


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.config = BridgeConfig(token="a" * 32)
        self.signer = FakeSigner()
        self.service = BridgeService(self.config, signer=self.signer)

    def test_service_returns_fresh_ticket_without_exposing_it_in_health(self):
        first = self.service.issue_ticket({"track_id": "123", "quality_level": 3})
        second = self.service.issue_ticket({"track_id": "123", "quality_level": 3})

        self.assertEqual(first["headers"]["x-tk"], "dynamic-1")
        self.assertEqual(second["headers"]["x-tk"], "dynamic-2")
        self.assertEqual(second["api_device"], "android2")
        self.assertNotIn("x_tk", json.dumps(self.service.health()))

    def test_http_endpoint_requires_bearer_token(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/ximalaya/ticket"
        body = json.dumps({"track_id": "123", "quality_level": 3}).encode()
        try:
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(url, data=body, method="POST"), timeout=2)
            self.assertEqual(denied.exception.code, 401)

            request = Request(url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
            })
            with urlopen(request, timeout=2) as response:
                payload = json.load(response)
            self.assertEqual(payload["headers"]["x-tk"], "dynamic-1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_service_rejects_non_premium_or_invalid_requests(self):
        with self.assertRaisesRegex(ValueError, "track_id"):
            self.service.issue_ticket({"track_id": "abc", "quality_level": 3})
        with self.assertRaisesRegex(ValueError, "仅处理"):
            self.service.issue_ticket({"track_id": "123", "quality_level": 1})


if __name__ == "__main__":
    unittest.main()
