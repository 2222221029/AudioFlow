#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Skill-compatible, confirmation-gated audiobook organization plans."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from core.rename_rules import (
    DEFAULT_RULE_VALUES,
    MANDATORY_AD_KEYWORDS,
    RenameRuleStore,
    sanitize_rules,
)


AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".flac", ".wav", ".ogg", ".caf"}
DEFAULT_PLAN_TTL_SECONDS = 7 * 24 * 60 * 60

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_SEQUENCE = tuple("一二三四五六七八九十")
_ROMAN_SEQUENCE = tuple("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ")
_CN_CAP_SEQUENCE = tuple("壹贰叁肆伍陆柒捌玖拾")
_ENDING_MARKERS = ("全书完", "大结局", "全书终", "完结", "全书完结")
_QUALITY_MARKERS = ("[Audio Vivid]", "[杜比全景声]", "[无损]")
_AD_KEYWORDS = tuple(MANDATORY_AD_KEYWORDS)
_SPECIAL_CONTENT_LABELS = (
    "片花", "预告", "主题曲", "剧情歌", "歌曲", "番外", "花絮", "楔子", "序章",
    "引子", "后记", "彩蛋", "调整说明", "制作特辑", "试听",
)
_SPECIAL_OPERATIONAL_LABELS = (
    "直播回听", "更新通知", "停更", "恢复更新", "中奖名单", "加更提示", "以下为加更",
    "下面是加更", "求赞", "求订阅", "平台出品", "作者有话说", "小川有话说",
)
_AUDIO_SORT_RE = re.compile(r"^(\d+)[-._\s]")
_SORT_PREFIX_RE = re.compile(r"^\s*\d+\s*[-._、]\s*")
_CHAPTER_RE = re.compile(
    r"(?:第\s*)?(?P<number>\d+|[零〇一二两三四五六七八九十百千万]+)\s*"
    r"(?P<unit>章|节|集|回)(?:\s*[-:：._、]\s*|\s*)"
)
_LEADING_NUMBER_RE = re.compile(
    r"^\s*第?\s*(?:\d+|[零〇一二两三四五六七八九十百千万]+)\s*"
    r"(?:章|节|集|回)*\s*[-:：._、]?\s*"
)
_LEADING_BOOK_TITLE_RE = re.compile(r"^\s*《(?P<title>[^《》]{1,100})》")
_PAREN_BLOCK_RE = re.compile(r"[（(]([^（）()]*)[）)]")
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TRAILING_VARIANTS = (
    ("ud", re.compile(r"^(.*?)[（(]([上中下])[）)]$")),
    ("ar", re.compile(r"^(.*?)[（(](\d+)[）)]$")),
    ("rm", re.compile(r"^(.*?)[（(]([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])[）)]$")),
    ("cncap", re.compile(r"^(.*?)[（(]([壹贰叁肆伍陆柒捌玖拾]+)[）)]$")),
    ("cn", re.compile(r"^(.*?)[（(]([一二三四五六七八九十]+)[）)]$")),
    ("ud", re.compile(r"^(.*?)([上中下])$")),
    ("rm", re.compile(r"^(.*?)([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])$")),
    ("cncap", re.compile(r"^(.*?)([壹贰叁肆伍陆柒捌玖拾]+)$")),
    ("ar", re.compile(r"^(.*?)(\d+)$")),
    ("cn", re.compile(r"^(.*?)([一二三四五六七八九十]+)$")),
)


def _now() -> int:
    return int(time.time())


def _safe_filename(value: Any) -> str:
    text = str(value or "").strip() or "未知"
    replacements = {
        "<": "《", ">": "》", ":": "：", '"': "”", "/": "／",
        "\\": "／", "|": "｜", "?": "？", "*": "＊",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text[:220].rstrip(" .")


def _canonical_book_title(value: Any) -> str:
    """Prefer the formal title over platform marketing copy in album names."""
    text = str(value or "").strip()
    match = _LEADING_BOOK_TITLE_RE.match(text)
    if match:
        return match.group("title").strip()
    return text.strip("《》 ")


def _cn_number(value: str) -> int | None:
    """Convert common Chinese numbers from 1 through 99999."""
    if not value:
        return None
    total = 0
    section = 0
    current = 0
    for char in value:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in {"十", "百", "千"}:
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            section += (current or 1) * unit
            current = 0
        elif char == "万":
            total += (section + current or 1) * 10000
            section = 0
            current = 0
        else:
            return None
    number = total + section + current
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
    match = _CHAPTER_RE.search(title)
    if match:
        token = match.group("number")
        return int(token) if token.isdigit() else (_cn_number(token) or fallback)
    return fallback


def _strip_existing_chapter_prefix(title: str) -> str:
    return _LEADING_NUMBER_RE.sub("", str(title or "").strip(), count=1).strip(
        " \t\u3000_-—:：、"
    )


def _normalize_punctuation(value: str) -> tuple[str, list[str]]:
    text = _WHITESPACE_RE.sub(" ", str(value or "").replace("\u3000", " ")).strip()
    issues = []
    if _CJK_RE.search(text):
        text = text.translate(str.maketrans({
            "(": "（", ")": "）", ",": "，", "!": "！", "?": "？",
            ";": "；", ":": "：",
        }))
    pairs = (("（", "）"), ("【", "】"), ("[", "]"), ("「", "」"),
             ("『", "』"), ("“", "”"), ("‘", "’"), ("《", "》"))
    for opening, closing in pairs:
        if text.count(opening) != text.count(closing):
            issues.append(f"符号不成对：{opening}{closing}")
    text = re.sub(r" {2,}", " ", text).strip(" \t\u3000_-—~")
    return text, issues


def _remove_ad_blocks(
    value: str, cleanup_rules: dict[str, Any] | None = None
) -> tuple[str, list[str], bool]:
    cleanup = (cleanup_rules or DEFAULT_RULE_VALUES["cleanup"])
    ad_keywords = tuple(cleanup.get("ad_keywords") or _AD_KEYWORDS)
    preserve_keywords = tuple(cleanup.get("preserve_keywords") or _ENDING_MARKERS)
    removed = []

    def contains_ad(text: str) -> bool:
        folded = str(text or "").casefold()
        return any(keyword.casefold() in folded for keyword in ad_keywords if keyword)

    protected = {}
    text = str(value or "")
    for index, marker in enumerate(sorted(preserve_keywords, key=len, reverse=True)):
        token = f"\ue000{index}\ue001"
        if marker and marker in text:
            protected[token] = marker
            text = text.replace(marker, token)

    def replace_block(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if contains_ad(content):
            removed.append(content)
            return ""
        return match.group(0)

    text = _PAREN_BLOCK_RE.sub(replace_block, text)
    for pattern in cleanup.get("ad_patterns") or []:
        def replace_pattern(match: re.Match[str]) -> str:
            removed.append(match.group(0).strip())
            return ""
        text = re.sub(pattern, replace_pattern, text, flags=re.I)

    # Remove leading operational notices when a clear punctuation boundary
    # separates them from the actual chapter title, for example
    # "求订阅：正文". Repeat to handle stacked notices.
    for _ in range(3):
        leading = re.match(r"^([^，。！!；;、:：~～|丨]{1,80})[，。！!；;、:：~～|丨]+(.+)$", text)
        if not leading or not contains_ad(leading.group(1)):
            break
        removed.append(leading.group(1).strip())
        text = leading.group(2).strip()

    for keyword in sorted(ad_keywords, key=len, reverse=True):
        match = re.search(rf"(?:[，。！!；;、~～\s\-—|丨]+){re.escape(keyword)}.*$", text, re.I)
        if match:
            removed.append(text[match.start():].strip())
            text = text[:match.start()]
            break
    if cleanup.get("split_ad_after_first_space"):
        title = text.strip()
        if title not in set(cleanup.get("title_exceptions") or []):
            for boundary in re.finditer(r"[ \u00a0\u3000，。！!；;、~～\-—|丨]+", title):
                suffix = title[boundary.end():].strip()
                if suffix and contains_ad(suffix):
                    removed.append(suffix)
                    text = title[:boundary.start()]
                    break
    text = re.sub(r"[（(]\s*[）)]", "", text).strip(" \t\u3000_-—~")
    for token, marker in protected.items():
        text = text.replace(token, marker)
    residual = contains_ad(text)
    return text, removed, residual


def _clean_title(
    title: str, album_title: str, rules: dict[str, Any] | None = None
) -> tuple[str, list[str], list[str]]:
    text = _strip_existing_chapter_prefix(title)
    book = str(album_title or "").strip("《》 ")
    for variant in sorted({book, f"《{book}》"}, key=len, reverse=True):
        if variant and text.startswith(variant):
            text = text[len(variant):].strip(" \t\u3000_-—:：、")
    text, removed, residual = _remove_ad_blocks(text, (rules or {}).get("cleanup"))
    text, punctuation_issues = _normalize_punctuation(text)
    blocking = list(punctuation_issues)
    notes = []
    if residual:
        blocking.append("标题仍含疑似广告或运营文案")
    if not text:
        blocking.append("标题为空，无法确定真实章节名")
    elif text == "无题":
        notes.append("标题为“无题”，按技能默认规则保留，不联网查找")
    if removed:
        notes.append("建议移除明确广告文案：" + "；".join(removed[:3]))
    # Ending markers are never removed. Residual advertising around them still
    # remains a review item when no safe text boundary can be identified.
    return text, blocking, notes


def _parse_file_chapter(path: Path) -> dict[str, Any] | None:
    stem = _SORT_PREFIX_RE.sub("", path.stem, count=1)
    quality = next((marker for marker in _QUALITY_MARKERS if stem.endswith(marker)), "")
    parse_stem = stem[:-len(quality)].rstrip() if quality else stem
    match = _CHAPTER_RE.search(parse_stem)
    if not match:
        return None
    token = match.group("number")
    number = int(token) if token.isdigit() else _cn_number(token)
    if not number:
        return None
    raw_unit = match.group("unit")
    return {
        "number": number,
        "unit": raw_unit if raw_unit in {"章", "集", "回"} else "集",
        "title": parse_stem[match.end():].strip(" \t\u3000_-—:：、"),
        "prelude": parse_stem[:match.start()].strip(" \t\u3000_-—:：、"),
        "quality": quality,
    }


def _normalized_token(value: str) -> str:
    return re.sub(r"[\W_]+", "", _strip_existing_chapter_prefix(value),
                  flags=re.UNICODE).casefold()


def _audio_files(album_dir: Path) -> list[Path]:
    if not album_dir.exists() or not album_dir.is_dir():
        return []

    def sort_key(path: Path) -> tuple[Any, ...]:
        relative = path.relative_to(album_dir)
        match = _AUDIO_SORT_RE.match(path.name)
        return (str(relative.parent).casefold(), int(match.group(1)) if match else 10**12,
                path.name.casefold())

    return sorted(
        (
            path for path in album_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
            and not path.name.startswith(".__audioflow_rename_")
            and ".audioflow-trash" not in path.parts
        ),
        key=sort_key,
    )


def _special_kind(value: str, rules: dict[str, Any] | None = None) -> tuple[str, str]:
    text = _SORT_PREFIX_RE.sub("", str(value or ""), count=1)
    special = (rules or {}).get("special_files") or {}
    content_labels = special.get("content_labels") or _SPECIAL_CONTENT_LABELS
    operational_labels = special.get("operational_labels") or _SPECIAL_OPERATIONAL_LABELS
    ad_keywords = ((rules or {}).get("cleanup") or {}).get("ad_keywords") or _AD_KEYWORDS
    for label in content_labels:
        if label in text:
            return "content", label
    for label in operational_labels:
        if label in text:
            return "operational", label
    if any(keyword.casefold() in text.casefold() for keyword in ad_keywords):
        return "operational", text
    return "unknown", text.strip() or "特殊文件"


def _variant(value: str) -> tuple[str, str, str]:
    text = str(value or "").strip()
    for style, pattern in _TRAILING_VARIANTS:
        match = pattern.match(text)
        if match and match.group(1).strip():
            return match.group(1).strip(), style, match.group(2)
    return text, "none", ""


def _group_key(value: str) -> str:
    return re.sub(r"\s+", "", value, flags=re.UNICODE).casefold()


def _cn_sequence_number(number: int) -> str:
    if number <= 10:
        return _CN_SEQUENCE[number - 1]
    tens, ones = divmod(number, 10)
    prefix = "十" if tens == 1 else _CN_SEQUENCE[tens - 1] + "十"
    return prefix + (_CN_SEQUENCE[ones - 1] if ones else "")


def _normalize_repeated_titles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize repeated-title suffixes only within consecutive chapter runs."""
    issues = []
    ordered = sorted((item for item in items if item.get("kind") == "chapter"),
                     key=lambda item: (item.get("sequence", 0), item.get("chapter", 0)))
    index = 0
    grouped = set()
    while index < len(ordered):
        first = ordered[index]
        first_base, _first_style, _first_suffix = _variant(first.get("clean_title") or "")
        run = [first]
        cursor = index + 1
        while cursor < len(ordered):
            previous = run[-1]
            candidate = ordered[cursor]
            base, _style, _suffix = _variant(candidate.get("clean_title") or "")
            if (candidate.get("chapter") != previous.get("chapter", 0) + 1
                    or _group_key(base) != _group_key(first_base)):
                break
            run.append(candidate)
            cursor += 1
        if len(run) >= 2:
            grouped.update(id(item) for item in run)
            parsed = [_variant(item.get("clean_title") or "") for item in run]
            styles = {style for _base, style, _suffix in parsed if style != "none"}
            if "ud" in styles and len(run) <= 3 and styles <= {"ud"}:
                labels = ["上", "下"] if len(run) == 2 else ["上", "中", "下"]
                for position, (item, (base, _style, suffix)) in enumerate(zip(run, parsed)):
                    if suffix and suffix != labels[position]:
                        issues.append({
                            "type": "sequence_review", "file": item["source_name"],
                            "message": "上/中/下顺序与连续集号不一致",
                        })
                    item["clean_title"] = f"{base}（{labels[position]}）"
            else:
                if len(styles) > 1:
                    for item in run:
                        issues.append({
                            "type": "sequence_review", "file": item["source_name"],
                            "message": "连续同名章节混用了多种尾部序号，请确认统一方式",
                        })
                style = next((candidate for candidate in ("ud", "cn", "ar", "rm", "cncap")
                              if candidate in styles), "ar")
                if style == "ud" or (style == "cn" and len(run) >= 4):
                    style = "cn"
                for position, (item, (base, _old_style, _suffix)) in enumerate(zip(run, parsed), start=1):
                    if style == "cn":
                        label = _cn_sequence_number(position)
                    elif style == "rm" and position <= len(_ROMAN_SEQUENCE):
                        label = _ROMAN_SEQUENCE[position - 1]
                    elif style == "cncap" and position <= len(_CN_CAP_SEQUENCE):
                        label = _CN_CAP_SEQUENCE[position - 1]
                    else:
                        label = str(position)
                    item["clean_title"] = f"{base}（{label}）"
        index = cursor if cursor > index + 1 else index + 1
    for item in ordered:
        _base, style, suffix = _variant(item.get("clean_title") or "")
        if style == "ud" and suffix and id(item) not in grouped:
            issues.append({
                "type": "sequence_review", "file": item["source_name"],
                "message": "发现孤立的上/中/下标题，可能缺少相邻分集",
            })
    return issues


def _foreign_book_hints(source_name: str, prelude: str, album_title: str) -> list[str]:
    book = str(album_title or "").strip("《》 ")
    hints = []
    for mentioned in re.findall(r"《([^》]{2,80})》", source_name):
        if mentioned not in book and book not in mentioned and not any(
                keyword in mentioned for keyword in ("新书", "预告")):
            hints.append(mentioned)
    cleaned_prelude = _SORT_PREFIX_RE.sub("", prelude or "").strip("《》丨| -_：:")
    if (cleaned_prelude and len(cleaned_prelude) >= 2 and cleaned_prelude not in book
            and book not in cleaned_prelude and not any(
                marker in cleaned_prelude for marker in _QUALITY_MARKERS)):
        hints.append(cleaned_prelude)
    return list(dict.fromkeys(hints))


def _book_for_item(config: dict[str, Any], item: dict[str, Any]) -> str:
    volumes = config.get("volumes") if isinstance(config.get("volumes"), dict) else {}
    value = volumes.get(str(item.get("volume_index") or 1)) or config.get("album_title")
    return str(value or "未知专辑").strip("《》 ")


def _render_filename(template: str, values: dict[str, Any], extension: str) -> str:
    rendered = str(template or "").format_map({key: str(value or "") for key, value in values.items()})
    suffix = str(extension or "").lower()
    if suffix and rendered.lower().endswith(suffix):
        rendered = rendered[:-len(suffix)]
    return _safe_filename(rendered) + suffix


def _format_chapter_name(config: dict[str, Any], item: dict[str, Any]) -> str:
    book = _book_for_item(config, item)
    prefix = str(item["prefix"]).zfill(int(config.get("prefix_width") or 4))
    number = str(item["chapter"]).zfill(int(config.get("chapter_width") or 3))
    unit = config.get("chapter_unit") if config.get("chapter_unit") in {"章", "集", "回"} else "集"
    title = str(item.get("clean_title") or "").strip()
    smart_separator = config.get("smart_title_separator", True)
    separator = (
        "" if not title or (smart_separator and title.startswith(("《", "“", "「", "『")))
        else " "
    )
    quality = str(item.get("quality") or "")
    if quality and quality in title:
        quality = ""
    values = {
        "prefix": prefix, "book": book, "chapter": number, "unit": unit,
        "title": title, "title_sep": separator, "quality": quality,
        "quality_sep": " " if quality else "", "label": "", "ext": item["extension"],
    }
    template = str(config.get("chapter_template") or DEFAULT_RULE_VALUES["format"]["chapter_template"])
    return _render_filename(template, values, item["extension"])


def _format_special_name(
    config: dict[str, Any], item: dict[str, Any], rules: dict[str, Any] | None = None
) -> str:
    book = _book_for_item(config, item)
    prefix = str(item["prefix"]).zfill(int(config.get("prefix_width") or 4))
    label, _issues = _normalize_punctuation(item.get("special_label") or "特殊文件")
    label, _removed, _residual = _remove_ad_blocks(label, (rules or {}).get("cleanup"))
    label = label or item.get("special_type") or "特殊文件"
    values = {
        "prefix": prefix, "book": book, "chapter": "", "unit": "", "title": "",
        "title_sep": "", "quality": "", "quality_sep": "", "label": label,
        "ext": item["extension"],
    }
    template = str(config.get("special_template") or DEFAULT_RULE_VALUES["format"]["special_template"])
    return _render_filename(template, values, item["extension"])


class RenamePlanManager:
    """Persist complete plans and execute confirmed changes idempotently."""

    def __init__(self, path: str | Path, rule_store: RenameRuleStore | None = None):
        self.path = Path(path)
        self.rule_store = rule_store
        self._lock = threading.RLock()

    def _load_payload_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 2, "plans": {}, "profiles": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 2, "plans": {}, "profiles": {}}
        if not isinstance(payload, dict):
            return {"version": 2, "plans": {}, "profiles": {}}
        return {
            "version": 2,
            "plans": payload.get("plans") if isinstance(payload.get("plans"), dict) else {},
            "profiles": payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {},
        }

    def _save_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _profile_key(album: dict[str, Any]) -> str:
        platform = str((album or {}).get("platform") or "")
        album_id = str((album or {}).get("id") or (album or {}).get("album_id") or "")
        title = str((album or {}).get("title") or "")
        return f"{platform}:{album_id or title}".casefold()

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            plans = list(self._load_payload_unlocked()["plans"].values())
        if status:
            plans = [plan for plan in plans if plan.get("status") == status]
        return sorted(plans, key=lambda plan: plan.get("created_at", 0), reverse=True)

    def get(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            plan = self._load_payload_unlocked()["plans"].get(str(plan_id or ""))
            return dict(plan) if plan else None

    def profile_for(self, album: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            profile = self._load_payload_unlocked()["profiles"].get(self._profile_key(album))
            return dict(profile) if profile else None

    def _write_mapping(self, plan: dict[str, Any]) -> None:
        mapping_dir = self.path.parent / "rename_plans"
        mapping_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = mapping_dir / f"{plan['id']}.tsv"
        lines = ["旧名\t新名\t动作\t状态"]
        for item in plan.get("items") or []:
            lines.append("\t".join((
                str(item.get("relative_source") or item.get("source_name") or ""),
                str(item.get("relative_target") or item.get("target_name") or ""),
                str(item.get("action") or "keep"),
                str(item.get("status") or ""),
            )))
        mapping_path.write_text("\ufeff" + "\n".join(lines) + "\n", encoding="utf-8")
        plan["mapping_file"] = str(mapping_path)

    @staticmethod
    def _issue(issue_type: str, message: str, *, item: dict[str, Any] | None = None,
               blocking: bool = True, **extra: Any) -> dict[str, Any]:
        issue = {
            "id": uuid.uuid4().hex[:12], "type": issue_type, "message": message,
            "blocking": bool(blocking), "resolved": False,
        }
        if item:
            issue["file"] = item.get("source_name")
            issue["relative_source"] = item.get("relative_source")
        issue.update(extra)
        return issue

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
        origin_source: str = "",
    ) -> dict[str, Any]:
        album_dir = Path(album_dir).resolve()
        title = str((album or {}).get("title") or album_dir.name or "未知专辑").strip()
        book_title = _canonical_book_title(title) or "未知专辑"
        files = _audio_files(album_dir)
        chapter_list = list(chapters or [])
        metadata_by_number: dict[int, list[dict[str, Any]]] = {}
        for fallback, chapter in enumerate(chapter_list, start=1):
            metadata_by_number.setdefault(chapter_number(chapter, fallback), []).append(chapter)

        with self._lock:
            payload = self._load_payload_unlocked()
            profile = payload["profiles"].get(self._profile_key(album))
        effective = (
            self.rule_store.effective(album) if self.rule_store
            else {"rules": sanitize_rules({}), "applied": [{"id": "builtin-audioflow", "version": 1}]}
        )
        rules = effective["rules"]
        rule_version = "|".join(
            f"{item.get('id')}@{item.get('version')}" for item in effective.get("applied") or []
        )
        format_rules = rules["format"]
        validation_rules = rules["validation"]
        profile_rule_version = str((profile or {}).get("rule_version") or "")
        profile_outdated = bool(profile_rule_version and profile_rule_version != rule_version)
        parsed_units = {
            parsed["unit"]
            for path in files
            if (parsed := _parse_file_chapter(path))
        }
        inferred_unit = "回" if parsed_units == {"回"} else chapter_unit
        configured_unit = (profile or {}).get("chapter_unit") or format_rules.get("chapter_unit")
        config = {
            "album_title": str((profile or {}).get("album_title") or book_title),
            "chapter_template": str((profile or {}).get("chapter_template") or format_rules["chapter_template"]),
            "special_template": str((profile or {}).get("special_template") or format_rules["special_template"]),
            "prefix_width": max(1, min(8, int((profile or {}).get("prefix_width") or format_rules.get("prefix_width") or prefix_width))),
            "chapter_width": max(1, min(8, int((profile or {}).get("chapter_width") or format_rules.get("chapter_width") or chapter_width))),
            "chapter_unit": (configured_unit if configured_unit in {"章", "集", "回"}
                             else (inferred_unit if inferred_unit in {"章", "集", "回"} else "集")),
            "prefix_strategy": str((profile or {}).get("prefix_strategy") or format_rules.get("prefix_strategy") or "chapter"),
            "prefix_start": max(1, int((profile or {}).get("prefix_start") or format_rules.get("prefix_start") or 1)),
            "smart_title_separator": bool((profile or {}).get("smart_title_separator", format_rules.get("smart_title_separator", True))),
            "reserve_missing_prefixes": bool(validation_rules.get("reserve_missing_prefixes", True)),
            "special_files_keep_position": bool(validation_rules.get("special_files_keep_position", True)),
            "profile_reused": bool(profile),
            "profile_rule_outdated": profile_outdated,
            "rule_version": rule_version,
            "volumes": dict((profile or {}).get("volumes") or {}),
        }
        items = []
        issues = []
        notes = []
        if not files:
            issues.append(self._issue(
                "no_audio_files", "专辑目录中没有找到可整理的音频文件"
            ))
        previous_chapter = 0
        next_prefix = config["prefix_start"]
        volume_index = 1
        seen_chapters: dict[tuple[int, int], dict[str, Any]] = {}

        for sequence, path in enumerate(files, start=1):
            parsed = _parse_file_chapter(path)
            if not parsed:
                token = _normalized_token(path.stem)
                candidates = []
                for number, chapter_items in metadata_by_number.items():
                    for chapter in chapter_items:
                        raw = str(chapter.get("title") or chapter.get("name") or "")
                        raw_token = _normalized_token(raw)
                        if raw_token and raw_token in token:
                            candidates.append((number, raw))
                if len(candidates) == 1:
                    number, raw = candidates[0]
                    parsed = {"number": number, "unit": inferred_unit, "title": raw,
                              "prelude": "", "quality": ""}

            relative = path.relative_to(album_dir)
            stat = path.stat()
            base_item = {
                "sequence": sequence, "source": str(path), "source_name": path.name,
                "relative_source": str(relative), "extension": path.suffix.lower(),
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            }
            prefix_match = _AUDIO_SORT_RE.match(path.name)
            original_prefix = int(prefix_match.group(1)) if prefix_match else None
            if parsed:
                number = int(parsed["number"])
                if previous_chapter and number < previous_chapter:
                    volume_index += 1
                    previous_chapter = 0
                if config["prefix_strategy"] == "chapter" and config["reserve_missing_prefixes"]:
                    if not previous_chapter and number > 1:
                        next_prefix += number - 1
                    elif previous_chapter and number > previous_chapter + 1:
                        next_prefix += number - previous_chapter - 1
                assigned_prefix = (
                    original_prefix if config["prefix_strategy"] == "original" and original_prefix is not None
                    else next_prefix
                )
                item = {
                    **base_item, "kind": "chapter", "chapter": number,
                    "volume_index": volume_index,
                    "original_unit": parsed.get("unit") or "集", "prefix": assigned_prefix,
                    "original_prefix": original_prefix,
                    "quality": parsed.get("quality") or "", "prelude": parsed.get("prelude") or "",
                }
                clean_title, title_issues, title_notes = _clean_title(
                    parsed.get("title") or f"第{number}集", config["album_title"], rules
                )
                item["original_title"] = parsed.get("title") or ""
                item["clean_title"] = clean_title
                items.append(item)
                for message in title_issues:
                    issues.append(self._issue("title_review", message, item=item))
                for message in title_notes:
                    notes.append({"file": path.name, "message": message})
                hints = _foreign_book_hints(path.name, item["prelude"], config["album_title"])
                if hints:
                    issues.append(self._issue(
                        "cross_book_suspected", "文件名疑似属于其他书籍，已阻止自动执行",
                        item=item, hints=hints,
                    ))
                chapter_identity = (volume_index, number)
                if chapter_identity in seen_chapters:
                    issues.append(self._issue(
                        "duplicate_chapter", f"章节号 {number} 出现多个文件，可能是分册重置或重复下载",
                        item=item, files=[seen_chapters[chapter_identity]["source_name"], path.name],
                    ))
                seen_chapters.setdefault(chapter_identity, item)
                previous_chapter = max(previous_chapter, number)
                next_prefix = max(next_prefix + 1, assigned_prefix + 1)
            else:
                special_type, label = _special_kind(path.stem, rules)
                assigned_prefix = (
                    original_prefix if config["prefix_strategy"] == "original" and original_prefix is not None
                    else next_prefix
                )
                item = {
                    **base_item, "kind": "special", "special_type": special_type,
                    "special_label": label, "prefix": assigned_prefix, "original_prefix": original_prefix,
                    "volume_index": volume_index,
                    "action": "undecided",
                }
                items.append(item)
                default_action = rules["special_files"].get(f"{special_type}_default", "keep")
                issues.append(self._issue(
                    "special_file", "非章节文件必须由你选择整理、保持原名或隔离",
                    item=item, special_type=special_type,
                    suggested_action=default_action,
                ))
                next_prefix = max(next_prefix + 1, assigned_prefix + 1)

        for issue_data in _normalize_repeated_titles(items):
            issue_item = next((entry for entry in items
                               if entry["source_name"] == issue_data.get("file")), None)
            issues.append(self._issue(issue_data["type"], issue_data["message"], item=issue_item))

        volume_count = max((item.get("volume_index") or 1 for item in items), default=1)
        if volume_count > 1:
            suggested_volumes = {}
            for index in range(1, volume_count + 1):
                prelude_counts = {}
                for item in items:
                    if item.get("kind") != "chapter" or item.get("volume_index") != index:
                        continue
                    item_prelude = str(item.get("prelude") or "").strip("《》丨| -_：:")
                    if item_prelude:
                        prelude_counts[item_prelude] = prelude_counts.get(item_prelude, 0) + 1
                prelude = max(prelude_counts, key=prelude_counts.get) if prelude_counts else ""
                if prelude and prelude not in title and title not in prelude:
                    suggested = f"{title}·{prelude}"
                elif prelude:
                    suggested = prelude
                else:
                    suggested = f"{title}·第{_cn_sequence_number(index)}册"
                suggested_volumes[str(index)] = suggested
            if not config["volumes"]:
                config["volumes"] = suggested_volumes
            else:
                for key, value in suggested_volumes.items():
                    config["volumes"].setdefault(key, value)
            issues.append(self._issue(
                "volume_configuration",
                "检测到章节号回退，请逐册确认《总书名·分册名》；各册集号独立，前缀保持全局顺序",
                volumes=volume_count,
            ))
            for issue in issues:
                if issue.get("type") != "cross_book_suspected":
                    continue
                source_item = next((item for item in items
                                    if item.get("relative_source") == issue.get("relative_source")), None)
                volume_title = str((config.get("volumes") or {}).get(
                    str((source_item or {}).get("volume_index") or 1)
                ) or "")
                if volume_title and all(str(hint) in volume_title for hint in issue.get("hints") or []):
                    issue["resolved"] = True
                    issue["resolution"] = "volume_title_suggestion"

        missing_by_volume = {}
        for index in range(1, volume_count + 1):
            chapter_numbers = sorted(set(
                item["chapter"] for item in items
                if item["kind"] == "chapter" and item.get("volume_index") == index
            ))
            if chapter_numbers and chapter_numbers[-1] <= 20000:
                existing = set(chapter_numbers)
                missing_by_volume[str(index)] = [
                    number for number in range(1, chapter_numbers[-1] + 1) if number not in existing
                ]
            else:
                missing_by_volume[str(index)] = []
        missing_chapters = (
            missing_by_volume.get("1", []) if volume_count == 1 else
            [{"volume": int(index), "chapter": number}
             for index, numbers in missing_by_volume.items() for number in numbers]
        )
        for item in items:
            if item["kind"] == "chapter":
                item["target_name"] = _format_chapter_name(config, item)
                item.setdefault("action", "rename")
            else:
                item["target_name"] = _format_special_name(config, item, rules)
            target = Path(item["source"]).with_name(item["target_name"])
            item["target"] = str(target)
            item["relative_target"] = str(target.relative_to(album_dir))
            if item["action"] == "rename":
                item["status"] = "unchanged" if target == Path(item["source"]) else "planned"
            else:
                item["status"] = "awaiting_decision"

        plan_id = uuid.uuid4().hex[:10]
        created_at = _now()
        plan = {
            "id": plan_id, "task_id": str(task_id or ""), "origin_source": str(origin_source or ""),
            "album": {
                "title": title, "book_title": book_title,
                "platform": str((album or {}).get("platform") or ""),
                "id": str((album or {}).get("id") or (album or {}).get("album_id") or ""),
            },
            "profile_key": self._profile_key(album), "album_dir": str(album_dir),
            "configuration": config,
            "configuration_confirmation_required": not bool(profile) or profile_outdated,
            "rule_version": rule_version,
            "rule_snapshot": {"rules": rules, "applied": effective.get("applied") or []},
            "suggested_format": (
                f"{config['prefix_width']}位序号 · {config['chapter_width']}位章节号 · "
                f"{config['chapter_unit']} · {config['prefix_strategy']}"
            ),
            "created_at": created_at, "expires_at": created_at + max(60, int(ttl_seconds)),
            "items": items, "issues": issues, "notes": notes,
            "volume_count": volume_count, "missing_chapters": missing_chapters,
            "missing_by_volume": missing_by_volume,
        }
        self._refresh_plan(plan)
        with self._lock:
            payload = self._load_payload_unlocked()
            payload["plans"][plan_id] = plan
            self._write_mapping(plan)
            self._save_payload_unlocked(payload)
        return plan

    def _refresh_plan(self, plan: dict[str, Any]) -> None:
        active = [item for item in plan.get("items") or [] if item.get("action") == "rename"]
        source_paths = {item.get("source") for item in active}
        generated_issues = [issue for issue in plan.get("issues") or []
                            if issue.get("type") not in {"duplicate_target", "target_exists"}]
        target_map: dict[str, list[dict[str, Any]]] = {}
        item_by_source = {str(item.get("source") or ""): item for item in active}
        for item in active:
            target_map.setdefault(str(item.get("target") or "").casefold(), []).append(item)
        for duplicates in target_map.values():
            if len(duplicates) > 1:
                generated_issues.append(self._issue(
                    "duplicate_target", "多个文件会重命名为同一个目标",
                    files=[item.get("target_name") for item in duplicates],
                ))
        for item in active:
            target = Path(item["target"])
            holder = item_by_source.get(str(target))
            holder_moves_away = bool(
                holder and holder is not item and holder.get("status") == "planned"
                and holder.get("target") != holder.get("source")
            )
            if (target.exists() and target != Path(item["source"])
                    and (str(target) not in source_paths or not holder_moves_away)):
                generated_issues.append(self._issue("target_exists", "目标文件已存在", item=item))
        plan["issues"] = generated_issues
        unresolved = [issue for issue in generated_issues
                      if issue.get("blocking", True) and not issue.get("resolved")]
        planned = [item for item in plan.get("items") or []
                   if item.get("status") in {"planned", "quarantine_planned"}]
        plan["status"] = (
            "needs_review" if unresolved else
            "pending_confirmation" if planned else
            "no_changes"
        )
        plan["summary"] = {
            "audio_files": len(plan.get("items") or []),
            "chapters": sum(item.get("kind") == "chapter" for item in plan.get("items") or []),
            "special_files": sum(item.get("kind") == "special" for item in plan.get("items") or []),
            "planned": len(planned),
            "unchanged": sum(item.get("status") in {"unchanged", "skipped"}
                             for item in plan.get("items") or []),
            "issues": len(unresolved),
            "missing_chapters": len(plan.get("missing_chapters") or []),
            "unmatched": sum(item.get("kind") == "special" for item in plan.get("items") or []),
        }
        plan["unmatched_files"] = [item["source_name"] for item in plan.get("items") or []
                                   if item.get("kind") == "special"]

    def configure(self, plan_id: str, choices: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply the one-shot album confirmation and per-file review choices."""
        choices = dict(choices or {})
        with self._lock:
            payload = self._load_payload_unlocked()
            plan = payload["plans"].get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") in {"completed", "executing", "cancelled"}:
                raise ValueError("该计划当前不能修改")
            config = plan.get("configuration") or {}
            incoming_config = choices.get("configuration") or {}
            if "album_title" in incoming_config:
                config["album_title"] = str(incoming_config.get("album_title") or "").strip("《》 ")
                if not config["album_title"]:
                    raise ValueError("书名不能为空")
            format_input = {
                key: incoming_config.get(key, config.get(key))
                for key in (
                    "chapter_template", "special_template", "prefix_width", "chapter_width",
                    "chapter_unit", "prefix_strategy", "prefix_start", "smart_title_separator",
                )
            }
            validated_format = sanitize_rules({"format": format_input})["format"]
            for key in format_input:
                value = validated_format[key]
                if key == "chapter_unit" and value == "auto":
                    continue
                config[key] = value
            if isinstance(incoming_config.get("volumes"), dict):
                volumes = {}
                for key, value in incoming_config["volumes"].items():
                    name = str(value or "").strip("《》 ")
                    if name:
                        volumes[str(key)] = name
                config["volumes"] = volumes
            special_actions = choices.get("special_actions") or {}
            item_actions = choices.get("item_actions") or {}
            item_overrides = choices.get("item_overrides") or {}
            snapshot_rules = (plan.get("rule_snapshot") or {}).get("rules") or sanitize_rules({})
            for item in plan.get("items") or []:
                key = item.get("relative_source") or item.get("source_name")
                override = item_overrides.get(key) if isinstance(item_overrides.get(key), dict) else {}
                action = (
                    special_actions.get(key) if item.get("kind") == "special"
                    else (override.get("action") or item_actions.get(key))
                )
                if item.get("kind") == "chapter" and "clean_title" in override:
                    clean_title, punctuation_issues = _normalize_punctuation(
                        str(override.get("clean_title") or "").strip()
                    )
                    if not clean_title:
                        raise ValueError(f"手工标题不能为空：{item.get('source_name')}")
                    if punctuation_issues:
                        raise ValueError(f"手工标题符号不完整：{item.get('source_name')}")
                    item["clean_title"] = clean_title
                    item["manual_override"] = True
                if action in {"organize", "rename", "accept"}:
                    item["action"] = "rename"
                    item["status"] = "planned"
                elif action in {"keep", "skip"}:
                    item["action"] = "keep"
                    item["status"] = "skipped"
                elif item.get("kind") == "special" and action in {"delete", "quarantine"}:
                    item["action"] = "quarantine"
                    item["status"] = "quarantine_planned"
                if item.get("kind") == "chapter":
                    item["target_name"] = _format_chapter_name(config, item)
                else:
                    item["target_name"] = _format_special_name(config, item, snapshot_rules)
                target = Path(item["source"]).with_name(item["target_name"])
                item["target"] = str(target)
                item["relative_target"] = str(target.relative_to(Path(plan["album_dir"])))
                if item.get("action") == "rename":
                    item["status"] = "unchanged" if target == Path(item["source"]) else "planned"
            for issue in plan.get("issues") or []:
                if issue.get("type") == "volume_configuration":
                    expected = int(issue.get("volumes") or plan.get("volume_count") or 1)
                    names = [str((config.get("volumes") or {}).get(str(index)) or "").strip()
                             for index in range(1, expected + 1)]
                    if all(names) and len(set(names)) == expected:
                        issue["resolved"] = True
                        issue["resolution"] = "configured"
                    continue
                key = issue.get("relative_source") or issue.get("file")
                affected = next((item for item in plan.get("items") or []
                                 if key in {item.get("relative_source"), item.get("source_name")}), None)
                if affected and affected.get("action") in {"rename", "keep", "quarantine"}:
                    issue["resolved"] = True
                    issue["resolution"] = affected["action"]
            plan["configuration_confirmation_required"] = False
            plan["configured_at"] = _now()
            self._refresh_plan(plan)
            self._write_mapping(plan)
            self._save_payload_unlocked(payload)
            return dict(plan)

    def save_ai_analysis(self, plan_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
        """Attach AI suggestions to a plan without resolving or executing it."""
        with self._lock:
            payload = self._load_payload_unlocked()
            plan = payload["plans"].get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") in {"completed", "executing", "cancelled"}:
                raise ValueError("该计划当前不能进行 AI 复核")
            known = {
                item.get("relative_source") or item.get("source_name")
                for item in plan.get("items") or []
            }
            suggestions = []
            for raw in (analysis or {}).get("suggestions") or []:
                key = str(raw.get("relative_source") or raw.get("file") or "")
                if key not in known:
                    continue
                action = str(raw.get("action") or "keep")
                if action not in {"keep", "accept", "rename", "quarantine"}:
                    action = "keep"
                try:
                    confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0)))
                except (TypeError, ValueError):
                    confidence = 0.0
                suggestions.append({
                    "id": str(raw.get("id") or uuid.uuid4().hex[:12]),
                    "relative_source": key,
                    "action": action,
                    "clean_title": str(raw.get("clean_title") or "").strip()[:220],
                    "reason": str(raw.get("reason") or "AI 未提供理由").strip()[:500],
                    "confidence": confidence,
                })
            plan["ai_analysis"] = {
                "status": "completed", "suggestions": suggestions,
                "summary": str((analysis or {}).get("summary") or "").strip()[:1000],
                "model": str((analysis or {}).get("model") or ""), "created_at": _now(),
            }
            self._save_payload_unlocked(payload)
            return dict(plan)

    def apply_ai_suggestions(
        self, plan_id: str, suggestion_ids: Iterable[str] | None = None
    ) -> dict[str, Any]:
        """Apply explicitly selected AI suggestions through normal plan review."""
        plan = self.get(plan_id)
        if not plan:
            raise KeyError("重命名计划不存在")
        available = (plan.get("ai_analysis") or {}).get("suggestions") or []
        selected_ids = {str(item) for item in (suggestion_ids or []) if str(item)}
        selected = [item for item in available if not selected_ids or item.get("id") in selected_ids]
        if not selected:
            raise ValueError("没有可应用的 AI 建议")
        item_overrides = {}
        special_actions = {}
        for item in selected:
            key = item["relative_source"]
            action = item.get("action") or "keep"
            source_item = next((entry for entry in plan.get("items") or []
                                if (entry.get("relative_source") or entry.get("source_name")) == key), None)
            if not source_item:
                continue
            if source_item.get("kind") == "special":
                special_actions[key] = action if action in {"keep", "quarantine"} else "organize"
            else:
                item_overrides[key] = {
                    "action": "accept" if action in {"accept", "rename"} else "keep",
                    **({"clean_title": item["clean_title"]} if item.get("clean_title") else {}),
                }
        reviewed = self.configure(plan_id, {
            "configuration": plan.get("configuration") or {},
            "special_actions": special_actions,
            "item_overrides": item_overrides,
        })
        with self._lock:
            payload = self._load_payload_unlocked()
            stored = payload["plans"].get(str(plan_id))
            if stored:
                stored.setdefault("ai_analysis", {})["applied_ids"] = [item["id"] for item in selected]
                stored["ai_analysis"]["applied_at"] = _now()
                self._save_payload_unlocked(payload)
                reviewed = dict(stored)
        return reviewed

    def resolve_safe(self, plan_id: str) -> dict[str, Any]:
        """Keep every uncertain file untouched and organize only safe chapters."""
        plan = self.get(plan_id)
        if not plan:
            raise KeyError("重命名计划不存在")
        special_actions = {}
        item_actions = {}
        risky_files = {
            issue.get("relative_source") or issue.get("file")
            for issue in plan.get("issues") or [] if issue.get("blocking", True)
        }
        risky_targets = {
            target_name
            for issue in plan.get("issues") or [] if issue.get("blocking", True)
            for target_name in (issue.get("files") or [])
        }
        for item in plan.get("items") or []:
            key = item.get("relative_source") or item.get("source_name")
            if item.get("kind") == "special":
                special_actions[key] = "keep"
            elif (key in risky_files or item.get("source_name") in risky_files
                  or item.get("target_name") in risky_targets):
                item_actions[key] = "keep"
        return self.configure(plan_id, {
            "configuration": plan.get("configuration") or {},
            "special_actions": special_actions,
            "item_actions": item_actions,
        })

    def cancel(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load_payload_unlocked()
            plan = payload["plans"].get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") in {"completed", "executing"}:
                raise ValueError("该计划已执行或正在执行，不能取消")
            plan["status"] = "cancelled"
            plan["cancelled_at"] = _now()
            self._save_payload_unlocked(payload)
            return dict(plan)

    @staticmethod
    def _assert_inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("整理路径超出专辑目录") from error

    def confirm(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load_payload_unlocked()
            plan = payload["plans"].get(str(plan_id or ""))
            if not plan:
                raise KeyError("重命名计划不存在")
            if plan.get("status") == "completed":
                return dict(plan)
            if plan.get("status") != "pending_confirmation":
                raise ValueError("该计划当前不能确认执行；请先处理全部复核项")
            if int(plan.get("expires_at") or 0) < _now():
                plan["status"] = "expired"
                self._save_payload_unlocked(payload)
                raise ValueError("重命名计划已过期，请重新分析")
            root = Path(plan["album_dir"]).resolve()
            planned = [item for item in plan.get("items") or []
                       if item.get("status") in {"planned", "quarantine_planned"}]
            if not planned:
                plan["status"] = "no_changes"
                self._save_payload_unlocked(payload)
                return dict(plan)

            trash_root = root / ".audioflow-trash" / plan["id"]
            for index, item in enumerate(planned):
                source = Path(item["source"])
                target = Path(item["target"])
                temp = source.with_name(f".__audioflow_rename_{plan_id}_{index:05d}{source.suffix.lower()}")
                item["temp"] = str(temp)
                if item.get("action") == "quarantine":
                    item["quarantine"] = str(trash_root / item["relative_source"])
                self._assert_inside(source, root)
                self._assert_inside(target, root)
                self._assert_inside(temp, root)
                if not source.exists():
                    raise ValueError(f"源文件已变化或不存在：{source.name}")
                stat = source.stat()
                if stat.st_size != item.get("size") or stat.st_mtime_ns != item.get("mtime_ns"):
                    raise ValueError(f"源文件在确认前发生变化：{source.name}")
                if item.get("action") == "rename" and target.exists() and target != source:
                    raise ValueError(f"目标文件已存在：{target.name}")
                if temp.exists():
                    raise ValueError(f"临时文件冲突：{temp.name}")

            plan["status"] = "executing"
            plan["started_at"] = _now()
            self._write_mapping(plan)
            self._save_payload_unlocked(payload)
            staged = []
            completed = []
            try:
                for item in planned:
                    source = Path(item["source"])
                    temp = Path(item["temp"])
                    os.replace(source, temp)
                    item["status"] = "staged"
                    staged.append(item)
                self._write_mapping(plan)
                self._save_payload_unlocked(payload)
                for item in planned:
                    temp = Path(item["temp"])
                    if item.get("action") == "quarantine":
                        destination = Path(item["quarantine"])
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        final_status = "quarantined"
                    else:
                        destination = Path(item["target"])
                        final_status = "renamed"
                    os.replace(temp, destination)
                    item["status"] = final_status
                    completed.append((item, destination))
                for item, destination in completed:
                    if not destination.exists() or destination.stat().st_size != item.get("size"):
                        raise OSError(f"整理结果校验失败：{destination.name}")
                plan["status"] = "completed"
                plan["completed_at"] = _now()
                profile = dict(plan.get("configuration") or {})
                profile["confirmed_at"] = _now()
                payload["profiles"][plan["profile_key"]] = profile
                self._write_mapping(plan)
                self._save_payload_unlocked(payload)
                return dict(plan)
            except Exception as error:
                rollback_errors = []
                for item, destination in reversed(completed):
                    source = Path(item["source"])
                    try:
                        if destination.exists() and not source.exists():
                            source.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(destination, source)
                            item["status"] = "planned" if item.get("action") == "rename" else "quarantine_planned"
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
                for item in reversed(staged):
                    temp = Path(item["temp"])
                    source = Path(item["source"])
                    try:
                        if temp.exists() and not source.exists():
                            os.replace(temp, source)
                            item["status"] = "planned" if item.get("action") == "rename" else "quarantine_planned"
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
                plan["status"] = "failed"
                plan["error"] = str(error)
                plan["rollback_errors"] = rollback_errors
                plan["failed_at"] = _now()
                self._write_mapping(plan)
                self._save_payload_unlocked(payload)
                raise


def preview_rule_samples(
    rule_values: dict[str, Any] | None,
    samples: Iterable[str],
    album_title: str = "示例书名",
) -> list[dict[str, Any]]:
    """Preview declarative rules without touching the filesystem."""
    rules = sanitize_rules(rule_values or {})
    fmt = rules["format"]
    config = {
        "album_title": _canonical_book_title(album_title) or "示例书名",
        "chapter_template": fmt["chapter_template"],
        "special_template": fmt["special_template"],
        "prefix_width": fmt["prefix_width"],
        "chapter_width": fmt["chapter_width"],
        "chapter_unit": "集" if fmt["chapter_unit"] == "auto" else fmt["chapter_unit"],
        "prefix_strategy": fmt["prefix_strategy"],
        "prefix_start": fmt["prefix_start"],
        "smart_title_separator": fmt["smart_title_separator"],
        "volumes": {},
    }
    results = []
    next_prefix = config["prefix_start"]
    for raw in list(samples or [])[:100]:
        name = Path(str(raw or "").strip()).name
        if not name:
            continue
        path = Path(name)
        extension = path.suffix.lower() if path.suffix.lower() in AUDIO_EXTENSIONS else ".m4a"
        parsed = _parse_file_chapter(path)
        issues = []
        if parsed:
            clean_title, blocking, notes = _clean_title(
                parsed.get("title") or "", config["album_title"], rules
            )
            item = {
                "prefix": next_prefix, "chapter": parsed["number"], "clean_title": clean_title,
                "quality": parsed.get("quality") or "", "extension": extension, "volume_index": 1,
            }
            sample_config = config
            if fmt["chapter_unit"] == "auto":
                sample_config = {**config, "chapter_unit": parsed.get("unit") or "集"}
            target = _format_chapter_name(sample_config, item)
            issues.extend(blocking)
            issues.extend(notes)
        else:
            special_type, label = _special_kind(path.stem, rules)
            item = {
                "prefix": next_prefix, "special_label": label, "special_type": special_type,
                "extension": extension, "volume_index": 1,
            }
            target = _format_special_name(config, item, rules)
            issues.append("未识别为章节，按特殊文件预览")
        results.append({"source_name": name, "target_name": target, "issues": issues})
        next_prefix += 1
    return results


__all__ = [
    "AUDIO_EXTENSIONS", "RenamePlanManager", "chapter_number", "preview_rule_samples",
]
