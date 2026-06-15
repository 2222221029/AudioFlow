from __future__ import annotations

import json
import uuid
from datetime import datetime

from .app_config import QUEUE_PATH, ensure_runtime_dirs
from .schemas import ProcessParams, QueueItem
from .state import META_STATE


def load_queue() -> list[QueueItem]:
    ensure_runtime_dirs()
    if not QUEUE_PATH.exists():
        return []
    try:
        with QUEUE_PATH.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        return [QueueItem.model_validate(item) for item in raw if isinstance(item, dict)]
    except Exception:
        return []


def persist_queue() -> None:
    ensure_runtime_dirs()
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("w", encoding="utf-8") as f:
        json.dump([item.model_dump() for item in META_STATE.queue], f, ensure_ascii=False, indent=2, default=str)


def restore_queue() -> None:
    with META_STATE.lock:
        META_STATE.queue = load_queue()


def add_queue_item(params: ProcessParams) -> QueueItem:
    item = QueueItem(
        id=uuid.uuid4().hex[:12],
        title=params.title or "未命名",
        author=params.author,
        anchor=params.anchor,
        source=params.input_folder,
        params=params,
    )
    with META_STATE.lock:
        META_STATE.queue.append(item)
    persist_queue()
    META_STATE.publish()
    return item


def update_queue_item(task_id: str, params: ProcessParams) -> QueueItem | None:
    with META_STATE.lock:
        for item in META_STATE.queue:
            if item.id != task_id:
                continue
            if item.status == "processing":
                raise RuntimeError("处理中任务不可编辑")
            item.title = params.title or "未命名"
            item.author = params.author
            item.anchor = params.anchor
            item.source = params.input_folder
            item.params = params
            item.status = "pending"
            item.error = ""
            item.updated_at = datetime.now()
            persist_queue()
            META_STATE.publish()
            return item
    return None


def remove_queue_items(ids: set[str]) -> None:
    with META_STATE.lock:
        META_STATE.queue = [item for item in META_STATE.queue if item.id not in ids or item.status == "processing"]
    persist_queue()
    META_STATE.publish()


def clear_queue() -> None:
    with META_STATE.lock:
        META_STATE.queue = [item for item in META_STATE.queue if item.status == "processing"]
    persist_queue()
    META_STATE.publish()


def retry_failed() -> None:
    with META_STATE.lock:
        for item in META_STATE.queue:
            if item.status in ("failed", "stopped"):
                item.status = "pending"
                item.error = ""
                item.updated_at = datetime.now()
    persist_queue()
    META_STATE.publish()
