#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Conservative, confirmation-gated audiobook rename planning."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".flac", ".wav", ".ogg", ".caf"}
DEFAULT_PLAN_TTL_SECONDS = 7 * 24 * 60 * 60

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_AD_KEYWORDS = (
    "求赞", "点赞", "求评论", "求订阅", "订阅", "加群", "qq群", "微信",
    "新书", "上架", "打赏", "冠名", "中奖", "直播回听", "播放量",
    "每天更新", "停更", "加更提示", "下面是加更", "以下为加更",
)
_ENDING_MARKERS = ("全书完", "大结局", "全书终", "完结")
_AUDIO_SORT_RE = re.compile(r"^(\d+)[-._\s]")
_LEADING_CHAPTER_RE = re.compile(
    r"^\s*第?\s*(\d+)\s*[章节集]?\s*[-:：._、]?\s*"
)
_LEADING_CN_CHAPTER_RE = re.compile(
    r"^\s*第\s*([零一二三四五六七八九十百千]+)\s*[章节集]\s*[-:：._、]?\s*"
)
_PAREN_BLOCK_RE = re.compile(r"[（(]([^（）()]*)[）)]")


def _now() -> int:
    return int(time.time())


def _safe_filename(value: Any) -> str:
    text = str(value or "").strip() or "未知"
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text[:200]


def _cn_number(value: str) -> int | None:
    """Convert common Chinese numbers from 1 through 9999."""
    if not value:
        return None
    total = 0
    current = 0
    for char in value:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "千":
            total += (current or 1) * 1000
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        else:
            return None
    number = total + current
    return number or None


def chapter_number(chapter: dict[str, Any], fallback: int) -> int:
    for key in ("ui_display_index", "order_num", "order", "index", "sort", "episode"):
        try:
            value = int(chapter.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    title = str(chapter.get("title") or chapter.get("name") or "")
    match = re.search(r"第?\s*(\d+)\s*[章节集]?", title)
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"第\s*([零一二三四五六七八九十百千]+)\s*[章节集]", title)
    if match:
        return _cn_number(match.group(1)) or fallback
    return fallback


def _strip_existing_chapter_prefix(title: str) -> str:
    text = str(title or "").strip()
    match = _LEADING_CN_CHAPTER_RE.match(text)
    if match:
        text = text[match.end():]
    else:
        text = _LEADING_CHAPTER_RE.sub("", text, count=1)
    return text.strip(" \t\u3000_-—:：、")


def _clean_title(title: str, album_title: str) -> tuple[str, list[str]]:
    """Remove only unambiguous parenthesized ads and report risky text."""
    text = _strip_existing_chapter_prefix(title)
    book_variants = {
        album_title.strip(),
        f"《{album_title.strip('《》')}》",
    }
    for variant in sorted((item for item in book_variants if item), key=len, reverse=True):
        if text.startswith(variant):
            text = text[len(variant):].strip(" \t\u3000_-—:：、")

    removed = []

    def replace_block(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        lowered = content.casefold()
        if any(keyword.casefold() in lowered for keyword in _AD_KEYWORDS):
            removed.append(content)
            return ""
        return match.group(0)

    text = _PAREN_BLOCK_RE.sub(replace_block, text)
    text = re.sub(r"[（(]\s*[）)]", "", text).strip(" \t\u3000_-—~")
    issues = []
    lowered = text.casefold()
    if any(keyword.casefold() in lowered for keyword in _AD_KEYWORDS):
        issues.append("标题仍含疑似广告或运营文案，需人工确认")
    if text in {"", "无题"}:
        issues.append("标题为空或无题，默认保留，不自动查找原书标题")
    if removed:
        issues.append("已建议移除明确广告括号：" + "；".join(removed[:3]))
    # End markers are content, never classify them as advertising.
    if any(marker in text for marker in _ENDING_MARKERS):
        issues = [item for item in issues if "广告" not in item or "已建议" in item]
    return text, issues


def _format_target_name(
    *,
    album_title: str,
    number: int,
    title: str,
    extension: str,
    prefix_width: int,
    chapter_width: int,
    chapter_unit: str,
) -> str:
    book = str(album_title or "未知专辑").strip("《》 ")
    prefix = str(number).zfill(prefix_width)
    chapter_label = f"第{str(number).zfill(chapter_width)}{chapter_unit}"
    clean_title = str(title or "").strip()
    separator = "" if clean_title.startswith(("《", "“", "「", "『")) else " "
    suffix = f"{separator}{clean_title}" if clean_title else ""
    return _safe_filename(f"{prefix}-《{book}》{chapter_label}{suffix}") + extension.lower()


def _audio_files(album_dir: Path) -> list[Path]:
    if not album_dir.exists() or not album_dir.is_dir():
        return []
    return sorted(
        (path for path in album_dir.rglob("*")
         if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
         and not path.name.startswith(".__audioflow_rename_")),
        key=lambda path: (str(path.parent), path.name.casefold()),
    )


def _candidate_score(path: Path, number: int, raw_title: str) -> int:
    stem = path.stem
    score = 0
    match = _AUDIO_SORT_RE.match(stem)
    if match and int(match.group(1)) == number:
        score += 10
    safe_title = _safe_filename(raw_title).casefold()
    if safe_title and safe_title in stem.casefold():
        score += 5
    return score


class RenamePlanManager:
    """Persist rename plans and execute confirmed plans idempotently."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        plans = payload.get("plans") if isinstance(payload, dict) else None
        return plans if isinstance(plans, dict) else {}

    def _save_unlocked(self, plans: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "plans": plans}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            plans = list(self._load_unlocked().values())
        if status:
            plans = [plan for plan in plans if plan.get("status") == status]
        return sorted(plans, key=lambda plan: plan.get("created_at", 0), reverse=True)

    def get(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            plan = self._load_unlocked().get(str(plan_id or ""))
            return dict(plan) if plan else None

    def create_plan(
        self,
        *,
        task_id: str,
        album: dict[str, Any],
        chapters: Iterable[dict[str, Any]],
        album_dir: str | Path,
        prefix_width: int = 4,
        chapter_width: int = 3,
        chapter_unit: str = "集",
        ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS,
    ) -> dict[str, Any]:
        album_dir = Path(album_dir).resolve()
        title = str((album or {}).get("title") or album_dir.name or "未知专辑").strip()
        files = _audio_files(album_dir)
        available = set(files)
        items = []
        issues = []

        for fallback, chapter in enumerate(list(chapters or []), start=1):
            number = chapter_number(chapter, fallback)
            raw_title = str(chapter.get("title") or chapter.get("name") or f"第{number}集")
            ranked = sorted(
                ((path, _candidate_score(path, number, raw_title)) for path in available),
                key=lambda item: (-item[1], str(item[0]).casefold()),
            )
            # A numeric prefix alone is not enough: numbered interviews or
            # announcements must remain untouched unless the title also matches.
            if not ranked or ranked[0][1] <= 0 or ranked[0][1] == 10:
                issues.append({
                    "type": "missing_file",
                    "chapter": number,
                    "message": f"未找到第 {number} 集对应音频文件",
                })
                continue
            best_score = ranked[0][1]
            tied = [path for path, score in ranked if score == best_score]
            if len(tied) != 1:
                issues.append({
                    "type": "ambiguous_file",
                    "chapter": number,
                    "message": f"第 {number} 集匹配到多个候选文件",
                    "files": [path.name for path in tied[:10]],
                })
                continue
            source = tied[0]
            available.remove(source)
            clean_title, title_issues = _clean_title(raw_title, title)
            quality_marker = next(
                (marker for marker in ("[Audio Vivid]", "[杜比全景声]", "[无损]")
                 if source.stem.endswith(marker)),
                "",
            )
            if quality_marker and quality_marker not in clean_title:
                clean_title = f"{clean_title} {quality_marker}".strip()
            target_name = _format_target_name(
                album_title=title,
                number=number,
                title=clean_title,
                extension=source.suffix,
                prefix_width=max(1, min(8, int(prefix_width))),
                chapter_width=max(1, min(8, int(chapter_width))),
                chapter_unit=chapter_unit if chapter_unit in {"章", "集"} else "集",
            )
            target = source.with_name(target_name)
            item = {
                "chapter": number,
                "source": str(source),
                "source_name": source.name,
                "target": str(target),
                "target_name": target.name,
                "size": source.stat().st_size,
                "mtime_ns": source.stat().st_mtime_ns,
                "status": "unchanged" if source == target else "planned",
                "title_issues": title_issues,
            }
            items.append(item)
            for message in title_issues:
                if "已建议移除" not in message:
                    issues.append({
                        "type": "title_review",
                        "chapter": number,
                        "message": message,
                        "file": source.name,
                    })

        target_map: dict[str, list[dict[str, Any]]] = {}
        source_paths = {item["source"] for item in items}
        for item in items:
            target_map.setdefault(item["target"].casefold(), []).append(item)
        for duplicates in target_map.values():
            if len(duplicates) > 1:
                issues.append({
                    "type": "duplicate_target",
                    "message": "多个文件会重命名为同一个目标",
                    "files": [item["target_name"] for item in duplicates],
                })
        for item in items:
            target = Path(item["target"])
            if target.exists() and str(target) not in source_paths and target != Path(item["source"]):
                issues.append({
                    "type": "target_exists",
                    "chapter": item["chapter"],
                    "message": "目标文件已存在",
                    "file": target.name,
                })

        planned = [item for item in items if item["status"] == "planned"]
        blocking = any(issue["type"] in {
            "missing_file", "ambiguous_file", "duplicate_target", "target_exists", "title_review"
        } for issue in issues)
        plan_id = uuid.uuid4().hex[:10]
        created_at = _now()
        plan = {
            "id": plan_id,
            "task_id": str(task_id or ""),
            "album": {
                "title": title,
                "platform": str((album or {}).get("platform") or ""),
                "id": str((album or {}).get("id") or (album or {}).get("album_id") or ""),
            },
            "album_dir": str(album_dir),
            "status": (
                "needs_review" if blocking else
                "pending_confirmation" if planned else
                "no_changes"
            ),
            "suggested_format": (
                f"{prefix_width}位序号-《书名》第{chapter_width}位{chapter_unit} 标题.ext"
            ),
            "created_at": created_at,
            "expires_at": created_at + max(60, int(ttl_seconds)),
            "items": items,
            "issues": issues,
            "unmatched_files": [path.name for path in sorted(available, key=lambda p: p.name.casefold())],
            "summary": {
                "audio_files": len(files),
                "matched": len(items),
                "planned": len(planned),
                "unchanged": len(items) - len(planned),
                "issues": len(issues),
                "unmatched": len(available),
            },
        }
        with self._lock:
            plans = self._load_unlocked()
            plans[plan_id] = plan
            self._save_unlocked(plans)
        return plan

    def cancel(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plans = self._load_unlocked()
            plan = plans.get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") in {"completed", "executing"}:
                raise ValueError("该计划已执行或正在执行，不能取消")
            plan["status"] = "cancelled"
            plan["cancelled_at"] = _now()
            self._save_unlocked(plans)
            return dict(plan)

    @staticmethod
    def _assert_inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("重命名路径超出专辑目录") from error

    def confirm(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plans = self._load_unlocked()
            plan = plans.get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") == "completed":
                return dict(plan)
            if plan.get("status") != "pending_confirmation":
                raise ValueError("该计划当前不能确认执行")
            if int(plan.get("expires_at") or 0) < _now():
                plan["status"] = "expired"
                self._save_unlocked(plans)
                raise ValueError("重命名计划已过期，请重新分析")
            root = Path(plan["album_dir"]).resolve()
            planned = [item for item in plan.get("items") or [] if item.get("status") == "planned"]
            if not planned:
                plan["status"] = "no_changes"
                self._save_unlocked(plans)
                return dict(plan)

            for index, item in enumerate(planned):
                source = Path(item["source"])
                target = Path(item["target"])
                self._assert_inside(source, root)
                self._assert_inside(target, root)
                item["temp"] = str(source.with_name(
                    f".__audioflow_rename_{plan_id}_{index:05d}{source.suffix.lower()}"
                ))
                temp = Path(item["temp"])
                self._assert_inside(temp, root)
                if not source.exists():
                    raise ValueError(f"源文件已变化或不存在：{source.name}")
                stat = source.stat()
                if stat.st_size != item.get("size") or stat.st_mtime_ns != item.get("mtime_ns"):
                    raise ValueError(f"源文件在确认前发生变化：{source.name}")
                if target.exists() and target != source:
                    raise ValueError(f"目标文件已存在：{target.name}")
                if temp.exists():
                    raise ValueError(f"临时文件冲突：{temp.name}")

            plan["status"] = "executing"
            plan["started_at"] = _now()
            self._save_unlocked(plans)

            moved_to_temp = []
            moved_to_target = []
            try:
                for item in planned:
                    source = Path(item["source"])
                    temp = Path(item["temp"])
                    os.replace(source, temp)
                    item["status"] = "staged"
                    moved_to_temp.append(item)
                self._save_unlocked(plans)
                for item in planned:
                    temp = Path(item["temp"])
                    target = Path(item["target"])
                    os.replace(temp, target)
                    item["status"] = "renamed"
                    moved_to_target.append(item)
                for item in planned:
                    target = Path(item["target"])
                    if not target.exists() or target.stat().st_size != item.get("size"):
                        raise OSError(f"重命名结果校验失败：{target.name}")
                plan["status"] = "completed"
                plan["completed_at"] = _now()
                self._save_unlocked(plans)
                return dict(plan)
            except Exception as error:
                rollback_errors = []
                for item in reversed(moved_to_target):
                    target = Path(item["target"])
                    source = Path(item["source"])
                    try:
                        if target.exists() and not source.exists():
                            os.replace(target, source)
                            item["status"] = "planned"
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
                for item in reversed(moved_to_temp):
                    temp = Path(item["temp"])
                    source = Path(item["source"])
                    try:
                        if temp.exists() and not source.exists():
                            os.replace(temp, source)
                            item["status"] = "planned"
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
                plan["status"] = "failed"
                plan["error"] = str(error)
                plan["rollback_errors"] = rollback_errors
                plan["failed_at"] = _now()
                self._save_unlocked(plans)
                raise


__all__ = [
    "AUDIO_EXTENSIONS",
    "RenamePlanManager",
    "chapter_number",
]
