from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Any

from .schemas import AppStatus, LogEntry, QueueItem


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.stopping = False
        self.progress = 0.0
        self.message = "等待就绪"
        self.started_at = ""
        self.finished_at = ""
        self.error = ""
        self.current_task_id = ""
        self.logs: list[LogEntry] = []
        self.queue: list[QueueItem] = []
        self.failed_items: list[dict[str, str]] = []
        self.stop_event = threading.Event()
        self._seq = 0
        self._subscribers: set[queue.Queue] = set()

    def reset_run(self, clear_logs: bool = True) -> None:
        with self.lock:
            if clear_logs:
                self.logs = []
                self._seq = 0
            self.running = True
            self.stopping = False
            self.progress = 0
            self.message = "准备处理"
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.finished_at = ""
            self.error = ""
            self.failed_items = []
            self.stop_event = threading.Event()
        self.publish()

    def add_log(self, message: str, level: str = "info") -> None:
        with self.lock:
            self._seq += 1
            self.logs.append(LogEntry(seq=self._seq, level=level, message=str(message)))
            self.logs = self.logs[-5000:]
        self.publish({"type": "log", "message": message, "level": level})

    def set_progress(self, progress: float, message: str = "") -> None:
        with self.lock:
            self.progress = max(0, min(100, float(progress)))
            if message:
                self.message = message
        if message:
            self.add_log(f"⏳ {round(self.progress)}% · {message}", "info")
        else:
            self.publish()

    def snapshot(self) -> AppStatus:
        with self.lock:
            return AppStatus(
                running=self.running,
                stopping=self.stopping,
                progress=self.progress,
                message=self.message,
                started_at=self.started_at,
                finished_at=self.finished_at,
                error=self.error,
                current_task_id=self.current_task_id,
                logs=list(self.logs),
                queue=list(self.queue),
                failed_items=list(self.failed_items),
            )

    def mark_finished(self, message: str) -> None:
        with self.lock:
            self.running = False
            self.stopping = False
            self.message = message
            self.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.current_task_id = ""
        self.publish()

    def request_stop(self) -> None:
        with self.lock:
            if self.running:
                self.stopping = True
                self.message = "正在停止"
                self.stop_event.set()
                for item in self.queue:
                    if item.status == "pending":
                        item.status = "stopped"
                        item.updated_at = datetime.now()
        self.add_log("⏹️ 已发送停止请求，等待当前步骤收尾...", "warning")

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self._subscribers.discard(q)

    def publish(self, event: dict[str, Any] | None = None) -> None:
        payload = event or {"type": "status"}
        with self.lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                self.unsubscribe(q)


META_STATE = AppState()
