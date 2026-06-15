from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from core.meta_legacy_core import processor as legacy_processor
from .schemas import ProcessParams
from .state import META_STATE
from .config_store import save_config
from .queue_store import persist_queue


class StateLogHandler(logging.Handler):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state

    def emit(self, record: logging.LogRecord) -> None:
        self.state.add_log(self.format(record), record.levelname.lower())


def build_state_logger(state, name: str = "meta-scraper") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = StateLogHandler(state)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def validate_params(params: ProcessParams) -> None:
    missing = params.missing_required_labels()
    if missing:
        raise ValueError("请补全：" + "、".join(missing))
    if not Path(params.input_folder).is_dir():
        raise ValueError(f"音频目录不存在：{params.input_folder}")


class ProgressBridge:
    def __init__(self, state) -> None:
        self.state = state

    def __call__(self, percent, message) -> None:
        self.state.set_progress(float(percent or 0), str(message or ""))


def run_process(params: ProcessParams, task_id: str = "") -> dict:
    logger = build_state_logger(META_STATE)

    def record_failed(file_path: str, error_msg: str) -> None:
        with META_STATE.lock:
            META_STATE.failed_items.append({"file": file_path, "error": error_msg})
        META_STATE.add_log(f"处理失败：{file_path} | {error_msg}", "error")

    validate_params(params)
    save_config(params)
    META_STATE.add_log(params.title or Path(params.input_folder).name, "info")
    result = legacy_processor.process_audio_books(
        params.model_dump(),
        logger,
        progress_callback=ProgressBridge(META_STATE),
        failed_audios_callback=record_failed,
        stop_event=META_STATE.stop_event,
    )
    return result or {}


def run_single(params: ProcessParams) -> None:
    META_STATE.reset_run(clear_logs=True)
    try:
        result = run_process(params)
        with META_STATE.lock:
            if result.get("error"):
                META_STATE.error = result["error"]
                message = "处理失败"
            elif META_STATE.stop_event.is_set():
                message = "已停止"
            else:
                META_STATE.progress = 100
                message = "处理完成"
        META_STATE.mark_finished(message)
    except Exception as exc:
        with META_STATE.lock:
            META_STATE.error = str(exc)
        META_STATE.add_log(f"处理失败：{exc}", "error")
        META_STATE.mark_finished("处理失败")


def run_queue() -> None:
    META_STATE.reset_run(clear_logs=True)
    try:
        while True:
            with META_STATE.lock:
                if META_STATE.stop_event.is_set():
                    break
                item = next((x for x in META_STATE.queue if x.status in ("pending", "failed")), None)
                if not item:
                    break
                item.status = "processing"
                item.error = ""
                item.updated_at = datetime.now()
                META_STATE.current_task_id = item.id
                META_STATE.progress = 0
                META_STATE.message = "正在处理队列任务"
                persist_queue()
            META_STATE.publish()
            result = run_process(item.params, item.id)
            with META_STATE.lock:
                if META_STATE.stop_event.is_set():
                    item.status = "stopped"
                    item.updated_at = datetime.now()
                    persist_queue()
                    break
                if result.get("error"):
                    item.status = "failed"
                    item.error = result.get("error", "处理失败")
                else:
                    item.status = "done"
                item.updated_at = datetime.now()
                persist_queue()
        META_STATE.mark_finished("已停止" if META_STATE.stop_event.is_set() else "队列处理完成")
    except Exception as exc:
        with META_STATE.lock:
            META_STATE.error = str(exc)
        META_STATE.add_log(f"队列处理失败：{exc}", "error")
        META_STATE.mark_finished("处理失败")


def start_single(params: ProcessParams) -> threading.Thread:
    thread = threading.Thread(target=run_single, args=(params,), daemon=True)
    thread.start()
    return thread


def start_queue() -> threading.Thread:
    thread = threading.Thread(target=run_queue, daemon=True)
    thread.start()
    return thread
