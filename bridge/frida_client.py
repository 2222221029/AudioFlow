from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict

from .config import BridgeConfig


class BridgeUnavailable(RuntimeError):
    pass


class XimalayaFridaClient:
    """Attach lazily and serialize calls into Ximalaya's own Android process."""

    def __init__(self, config: BridgeConfig, agent_path: str | Path | None = None):
        self.config = config
        self.agent_path = Path(agent_path or Path(__file__).parent / "agents" / "ximalaya.js")
        self._lock = threading.RLock()
        self._device = None
        self._session = None
        self._script = None
        self._last_error = "尚未连接"
        self._supervisor_thread = None

    def _import_frida(self):
        try:
            import frida
        except ImportError as exc:
            raise BridgeUnavailable("缺少 frida，请重新运行 start_bridge.ps1") from exc
        return frida

    def _clear(self, reason: str = "连接已断开") -> None:
        self._script = None
        self._session = None
        self._device = None
        self._last_error = reason

    def _on_detached(self, session, reason, crash=None) -> None:
        # Frida invokes this callback on its own event thread. Acquiring the
        # request lock here can deadlock when an in-flight RPC is waiting for
        # that same event thread to finish processing the detach notification.
        if self._session is session:
            self._clear(f"喜马拉雅进程已断开：{reason}")

    def start_supervisor(self, interval: float = 1.0) -> None:
        """Keep one instrumented Ximalaya process alive across app restarts."""
        with self._lock:
            if self._supervisor_thread is not None and self._supervisor_thread.is_alive():
                return

            def supervise() -> None:
                while True:
                    try:
                        with self._lock:
                            self._ensure_connected()
                    except Exception as exc:
                        self._last_error = str(exc)
                    time.sleep(interval)

            self._supervisor_thread = threading.Thread(
                target=supervise,
                name="ximalaya-frida-supervisor",
                daemon=True,
            )
            self._supervisor_thread.start()

    def _on_message(self, message, data) -> None:
        if message.get("type") == "error":
            self._last_error = str(message.get("description") or "Frida Agent 错误")

    def _connect(self) -> None:
        frida = self._import_frida()
        try:
            manager = frida.get_device_manager()
            self._device = manager.add_remote_device(self.config.frida_address)
            try:
                process = self._device.get_process(self.config.package_name)
                pid = process.pid
                spawned = False
            except frida.ProcessNotFoundError:
                pid = self._device.spawn([self.config.package_name])
                spawned = True

            self._session = self._device.attach(pid)
            session = self._session
            self._session.on(
                "detached",
                lambda reason, crash=None: self._on_detached(session, reason, crash),
            )
            source = self.agent_path.read_text(encoding="utf-8")
            self._script = self._session.create_script(source)
            self._script.on("message", self._on_message)
            self._script.load()
            if spawned:
                self._device.resume(pid)
            deadline = time.monotonic() + 15
            status = {}
            while time.monotonic() < deadline:
                status = self._script.exports_sync.status()
                if status.get("ready"):
                    break
                time.sleep(0.2)
            if not status.get("ready"):
                raise BridgeUnavailable(str(status.get("error") or "取票类尚未加载"))
            self._last_error = ""
        except Exception as exc:
            self._clear(str(exc))
            if isinstance(exc, BridgeUnavailable):
                raise
            raise BridgeUnavailable(f"连接安卓模拟器失败：{exc}") from exc

    def _ensure_connected(self) -> None:
        if self._script is None:
            self._connect()

    def ticket(self, request: Dict[str, Any]) -> Dict[str, str]:
        del request  # Reserved for version-specific ticket scenes.
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_connected()
                    result = self._script.exports_sync.ticket()
                    if not isinstance(result, dict) or not str(result.get("x_tk") or "").strip():
                        raise BridgeUnavailable("喜马拉雅 App 返回了空 x-tk，请确认账号已登录")
                    return {str(key): str(value) for key, value in result.items() if value}
                except Exception as exc:
                    self._clear(str(exc))
                    if attempt == 1:
                        if isinstance(exc, BridgeUnavailable):
                            raise
                        raise BridgeUnavailable(f"动态取票失败：{exc}") from exc
        raise BridgeUnavailable("动态取票失败")

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if self._script is None:
                return {"connected": False, "error": self._last_error}
            try:
                agent = self._script.exports_sync.status()
                return {
                    "connected": bool(agent.get("ready")),
                    "captured_cookie": bool(agent.get("captured_cookie")),
                    "app_version": str(agent.get("app_version") or ""),
                    "error": str(agent.get("error") or ""),
                }
            except Exception as exc:
                self._clear(str(exc))
                return {"connected": False, "error": str(exc)}
