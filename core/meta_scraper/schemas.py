from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


TaskStatus = Literal["pending", "processing", "done", "failed", "stopped"]


class ProcessParams(BaseModel):
    input_folder: str = ""
    api_source: str = "喜马拉雅"
    api_id: str = ""
    link_platform: str = "起点听书"
    link_url: str = ""
    title: str = ""
    subtitle: str = ""
    author: str = ""
    anchor: str = ""
    category: str = ""
    platform: str = ""
    year: str = ""
    target_format: str = "原格式保留"
    bitrate: str = "自动检测"
    finished: str = ""
    check_codec: bool = True
    rename_ext: bool = True
    debug: bool = True
    manual_cover_path: str = ""
    manual_desc: str = ""
    series_name: str = ""
    series_number: str = ""
    album_tags: list[str] = Field(default_factory=list)
    team: str = "RL"
    fetched_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("album_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [part.strip() for part in str(value).replace("，", ",").split(",") if part.strip()]

    def missing_required_labels(self) -> list[str]:
        required = {
            "input_folder": "音频目录",
            "title": "专辑标题",
            "author": "原著作者",
            "anchor": "演播艺术家",
            "category": "专辑分类",
            "platform": "发布平台",
            "year": "发布年份",
            "finished": "专辑状态",
        }
        data = self.model_dump()
        return [label for key, label in required.items() if not str(data.get(key, "")).strip()]


class QueueItem(BaseModel):
    id: str
    title: str = "未命名"
    author: str = ""
    anchor: str = ""
    source: str = ""
    status: TaskStatus = "pending"
    error: str = ""
    params: ProcessParams
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class LogEntry(BaseModel):
    seq: int
    level: str = "info"
    message: str
    ts: datetime = Field(default_factory=datetime.now)


class AppStatus(BaseModel):
    running: bool = False
    stopping: bool = False
    progress: float = 0
    message: str = "等待就绪"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    current_task_id: str = ""
    logs: list[LogEntry] = Field(default_factory=list)
    queue: list[QueueItem] = Field(default_factory=list)
    failed_items: list[dict[str, str]] = Field(default_factory=list)
