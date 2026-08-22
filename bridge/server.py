from __future__ import annotations

import argparse
import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from .config import BridgeConfig
from .frida_client import BridgeUnavailable, XimalayaFridaClient


class BridgeService:
    def __init__(self, config: BridgeConfig, signer=None):
        self.config = config
        self.signer = signer or XimalayaFridaClient(config)

    def authorized(self, authorization: str) -> bool:
        expected = f"Bearer {self.config.token}"
        return hmac.compare_digest(str(authorization or ""), expected)

    def health(self) -> Dict[str, Any]:
        state = self.signer.status()
        return {
            "ok": bool(state.get("connected")),
            "service": "AudioFlow Bridge",
            "package": self.config.package_name,
            "supported_version": self.config.supported_version,
            **state,
        }

    def issue_ticket(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        track_id = str(payload.get("track_id") or "").strip()
        if not track_id.isdigit():
            raise ValueError("track_id 必须是数字")
        try:
            level = int(payload.get("quality_level"))
        except (TypeError, ValueError) as exc:
            raise ValueError("quality_level 无效") from exc
        if level not in {3, 12, 13}:
            raise ValueError("Bridge 仅处理无损、杜比和 Audio Vivid 请求")

        dynamic = self.signer.ticket(payload)
        headers = {
            "x-tk": dynamic["x_tk"],
            "cookie": dynamic.get("cookie") or self.config.cookie,
            "user-agent": dynamic.get("user_agent") or self.config.user_agent,
            "accept-language": dynamic.get("accept_language") or self.config.accept_language,
        }
        headers = {key: value for key, value in headers.items() if value}
        return {
            "headers": headers,
            "api_device": dynamic.get("api_device") or self.config.api_device,
            "host": dynamic.get("host") or self.config.host,
        }

    @staticmethod
    def _phone(payload: Dict[str, Any]) -> str:
        phone = str(payload.get("phone") or "").strip()
        if not (phone.isdigit() and len(phone) == 11):
            raise ValueError("请输入 11 位手机号")
        return phone

    def send_sms(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.signer.sms_send(self._phone(payload))

    def sms_login(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        phone = self._phone(payload)
        code = str(payload.get("code") or "").strip()
        if not (code.isdigit() and 4 <= len(code) <= 8):
            raise ValueError("请输入短信验证码")
        return self.signer.sms_login(phone, code)


def make_handler(service: BridgeService):
    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "AudioFlowBridge/0.1"

        def log_message(self, format, *args):
            # The default log contains paths only; never log request bodies or headers.
            super().log_message(format, *args)

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                self._json(HTTPStatus.OK, service.health())
            elif path == "/":
                self._json(HTTPStatus.OK, {
                    "service": "AudioFlow Bridge",
                    "health": "/health",
                    "ticket": "/ximalaya/ticket",
                })
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            routes = {
                "/ximalaya/ticket": service.issue_ticket,
                "/ximalaya/login/sms/send": service.send_sms,
                "/ximalaya/login/sms/verify": service.sms_login,
            }
            action = routes.get(path)
            if action is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not service.authorized(self.headers.get("Authorization", "")):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 16384:
                    raise ValueError("请求体大小无效")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                result = action(payload)
                self._json(HTTPStatus.OK, result)
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except BridgeUnavailable as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Bridge 内部错误"})

    return BridgeHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="AudioFlow Android credential bridge")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.json"),
        help="Bridge JSON configuration",
    )
    args = parser.parse_args()
    config = BridgeConfig.load(args.config)
    service = BridgeService(config)
    start_supervisor = getattr(service.signer, "start_supervisor", None)
    if callable(start_supervisor):
        start_supervisor()
    server = ThreadingHTTPServer((config.bind_host, config.port), make_handler(service))
    print(f"AudioFlow Bridge 已启动：http://{config.bind_host}:{config.port}")
    print("请保持安卓模拟器和喜马拉雅 App 运行；Ctrl+C 可停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
