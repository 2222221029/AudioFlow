#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Versioned, declarative audiobook rename rules.

The rule store deliberately contains only data. Filesystem safety, collision
checks and rename execution remain owned by :mod:`audiobook_renamer`.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


RULE_SCHEMA_VERSION = 1
ALLOWED_TEMPLATE_FIELDS = {
    "prefix", "book", "chapter", "unit", "title", "title_sep",
    "quality", "quality_sep", "label", "ext",
}

DEFAULT_RULE_VALUES: dict[str, Any] = {
    "format": {
        "chapter_template": "{prefix}-《{book}》第{chapter}{unit}{title_sep}{title}{quality_sep}{quality}{ext}",
        "special_template": "{prefix}-《{book}》{label}{ext}",
        "prefix_width": 4,
        "chapter_width": 3,
        "chapter_unit": "auto",
        "prefix_strategy": "chapter",
        "prefix_start": 1,
        "smart_title_separator": True,
    },
    "cleanup": {
        "ad_keywords": [
            "求赞", "点赞", "求评论", "求订阅", "订阅专辑", "加群", "qq群", "qq 群",
            "微信", "公众号", "联系方式", "新书", "上架", "打赏", "冠名", "中奖",
            "直播回听", "播放量", "评论区", "每天更新", "停更", "恢复更新", "更新时间",
            "更新通知", "加更提示", "下面是加更", "以下为加更", "平台出品", "求月票",
            "求推荐", "带货", "购买链接", "福利群", "兄弟们", "宝子们",
        ],
        "ad_patterns": [],
        "preserve_keywords": ["全书完", "大结局", "全书终", "完结", "全书完结"],
        "title_exceptions": [],
        "split_ad_after_first_space": False,
    },
    "special_files": {
        "content_labels": [
            "片花", "预告", "主题曲", "剧情歌", "歌曲", "番外", "花絮", "楔子", "序章",
            "引子", "后记", "彩蛋", "调整说明", "制作特辑", "试听",
        ],
        "operational_labels": [
            "直播回听", "更新通知", "停更", "恢复更新", "中奖名单", "加更提示",
            "以下为加更", "下面是加更", "求赞", "求订阅", "平台出品", "作者有话说",
            "小川有话说",
        ],
        "content_default": "organize",
        "operational_default": "quarantine",
        "unknown_default": "keep",
    },
    "validation": {
        "reserve_missing_prefixes": True,
        "special_files_keep_position": True,
        "require_confirmation": True,
        "scan_residual_ads": True,
    },
}

BUILTIN_RULE_PACK = {
    "id": "builtin-audioflow",
    "name": "AudioFlow 内置规则",
    "description": "安全执行基础与 audiobook-renamer 默认业务规则",
    "scope": "builtin",
    "selector": "",
    "version": 1,
    "status": "active",
    "schema_version": RULE_SCHEMA_VERSION,
    "rules": DEFAULT_RULE_VALUES,
    "readonly": True,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _text_list(value: Any, *, maximum: int = 200) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\r", "\n").splitlines()
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:maximum]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:200])
    return result


def _validate_pattern(pattern: str) -> str:
    pattern = str(pattern or "").strip()
    if not pattern or len(pattern) > 240:
        raise ValueError("广告正则不能为空且不能超过 240 个字符")
    if re.search(r"\\[1-9]|\(\?<[=!]|\(\?P", pattern):
        raise ValueError("广告正则不允许反向引用、后行断言或命名捕获组")
    if re.search(r"(?:\.\*|\.\+|\[[^]]+\][*+])[^)]{0,30}\)[*+]", pattern):
        raise ValueError("广告正则包含高风险嵌套重复，请改为更明确的边界")
    try:
        re.compile(pattern, re.I)
    except re.error as exc:
        raise ValueError(f"广告正则无效：{exc}") from exc
    return pattern


def _validate_template(value: Any, default: str) -> str:
    template = str(value or default).strip()
    if not template or len(template) > 300:
        raise ValueError("命名模板不能为空且不能超过 300 个字符")
    fields = re.findall(r"\{([a-z_]+)\}", template)
    unknown = sorted(set(fields) - ALLOWED_TEMPLATE_FIELDS)
    if unknown:
        raise ValueError("命名模板包含不支持的字段：" + "、".join(unknown))
    residual = re.sub(r"\{[a-z_]+\}", "", template)
    if "{" in residual or "}" in residual:
        raise ValueError("命名模板中的大括号不完整")
    if "{ext}" not in template:
        template += "{ext}"
    elif not template.endswith("{ext}"):
        raise ValueError("命名模板中的 {ext} 必须位于末尾")
    return template


def sanitize_rules(value: dict[str, Any] | None) -> dict[str, Any]:
    merged = _deep_merge(DEFAULT_RULE_VALUES, value or {})
    fmt = merged["format"]
    fmt["chapter_template"] = _validate_template(
        fmt.get("chapter_template"), DEFAULT_RULE_VALUES["format"]["chapter_template"]
    )
    fmt["special_template"] = _validate_template(
        fmt.get("special_template"), DEFAULT_RULE_VALUES["format"]["special_template"]
    )
    fmt["prefix_width"] = max(1, min(8, int(fmt.get("prefix_width") or 4)))
    fmt["chapter_width"] = max(1, min(8, int(fmt.get("chapter_width") or 3)))
    fmt["prefix_start"] = max(1, min(99999999, int(fmt.get("prefix_start") or 1)))
    if fmt.get("chapter_unit") not in {"auto", "章", "集", "回"}:
        fmt["chapter_unit"] = "auto"
    if fmt.get("prefix_strategy") not in {"chapter", "continuous", "original"}:
        fmt["prefix_strategy"] = "chapter"
    fmt["smart_title_separator"] = bool(fmt.get("smart_title_separator", True))

    cleanup = merged["cleanup"]
    for key in ("ad_keywords", "preserve_keywords", "title_exceptions"):
        cleanup[key] = _text_list(cleanup.get(key))
    cleanup["ad_patterns"] = [_validate_pattern(item) for item in _text_list(cleanup.get("ad_patterns"), maximum=80)]
    cleanup["split_ad_after_first_space"] = bool(cleanup.get("split_ad_after_first_space", False))

    special = merged["special_files"]
    special["content_labels"] = _text_list(special.get("content_labels"))
    special["operational_labels"] = _text_list(special.get("operational_labels"))
    for key, default in (
        ("content_default", "organize"),
        ("operational_default", "quarantine"),
        ("unknown_default", "keep"),
    ):
        if special.get(key) not in {"organize", "keep", "quarantine"}:
            special[key] = default

    validation = merged["validation"]
    for key in ("reserve_missing_prefixes", "special_files_keep_position", "require_confirmation", "scan_residual_ads"):
        validation[key] = bool(validation.get(key, True))
    return merged


def sanitize_rule_override(value: dict[str, Any] | None) -> dict[str, Any]:
    """Validate an override while preserving only explicitly supplied fields."""
    raw = value if isinstance(value, dict) else {}
    validated = sanitize_rules(raw)
    result = {}
    for section, section_value in raw.items():
        if section not in DEFAULT_RULE_VALUES or not isinstance(section_value, dict):
            continue
        result[section] = {
            key: copy.deepcopy(validated[section][key])
            for key in section_value if key in validated[section]
        }
    return result


def merge_rule_values(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return sanitize_rules(_deep_merge(base or {}, override or {}))


class RenameRuleStore:
    """Persist draft/active rule packs and resolve the effective inheritance."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load_unlocked(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        return {
            "schema_version": RULE_SCHEMA_VERSION,
            "packs": value.get("packs") if isinstance(value.get("packs"), dict) else {},
        }

    def _save_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            packs = [copy.deepcopy(item) for item in self._load_unlocked()["packs"].values()]
        packs.sort(key=lambda item: (item.get("status") != "active", -int(item.get("updated_at") or 0)))
        return [copy.deepcopy(BUILTIN_RULE_PACK), *packs]

    def get(self, rule_id: str) -> dict[str, Any] | None:
        if rule_id == BUILTIN_RULE_PACK["id"]:
            return copy.deepcopy(BUILTIN_RULE_PACK)
        with self._lock:
            item = self._load_unlocked()["packs"].get(str(rule_id or ""))
        return copy.deepcopy(item) if item else None

    @staticmethod
    def _matches(pack: dict[str, Any], album: dict[str, Any]) -> bool:
        scope = pack.get("scope")
        selector = str(pack.get("selector") or "").casefold()
        if scope == "global":
            return True
        if scope == "platform":
            return selector == str(album.get("platform") or "").casefold()
        if scope == "album":
            album_id = str(album.get("id") or album.get("album_id") or "").casefold()
            title = str(album.get("title") or "").casefold()
            platform_key = f"{str(album.get('platform') or '').casefold()}:{album_id}"
            return selector in {album_id, title, platform_key}
        return False

    def effective(self, album: dict[str, Any] | None = None) -> dict[str, Any]:
        album = album or {}
        rules = copy.deepcopy(DEFAULT_RULE_VALUES)
        applied = [{"id": BUILTIN_RULE_PACK["id"], "version": 1, "scope": "builtin"}]
        with self._lock:
            packs = list(self._load_unlocked()["packs"].values())
        priority = {"global": 1, "platform": 2, "album": 3}
        active = [item for item in packs if item.get("status") == "active" and self._matches(item, album)]
        active.sort(key=lambda item: (priority.get(item.get("scope"), 99), int(item.get("activated_at") or 0)))
        for pack in active:
            rules = _deep_merge(rules, pack.get("rules") or {})
            applied.append({
                "id": pack.get("id"), "version": pack.get("version"),
                "scope": pack.get("scope"), "selector": pack.get("selector") or "",
            })
        return {"rules": sanitize_rules(rules), "applied": applied}

    def save_draft(self, value: dict[str, Any]) -> dict[str, Any]:
        value = dict(value or {})
        scope = str(value.get("scope") or "global")
        if scope not in {"global", "platform", "album"}:
            raise ValueError("规则范围必须是全局、平台或专辑")
        selector = str(value.get("selector") or "").strip()
        if scope != "global" and not selector:
            raise ValueError("平台或专辑规则必须填写匹配值")
        now = int(time.time())
        with self._lock:
            payload = self._load_unlocked()
            rule_id = str(value.get("id") or "")
            current = payload["packs"].get(rule_id)
            if current and current.get("status") != "draft":
                rule_id = ""
            if not rule_id:
                rule_id = "rule-" + uuid.uuid4().hex[:10]
            versions = [
                int(item.get("version") or 0) for item in payload["packs"].values()
                if item.get("scope") == scope and str(item.get("selector") or "").casefold() == selector.casefold()
            ]
            pack = {
                "id": rule_id,
                "name": str(value.get("name") or "自定义重命名规则").strip()[:80],
                "description": str(value.get("description") or "").strip()[:300],
                "scope": scope,
                "selector": selector,
                "version": int((current or {}).get("version") or (max(versions, default=0) + 1)),
                "status": "draft",
                "schema_version": RULE_SCHEMA_VERSION,
                "rules": sanitize_rule_override(value.get("rules") or {}),
                "created_at": int((current or {}).get("created_at") or now),
                "updated_at": now,
                "source": str(value.get("source") or (current or {}).get("source") or "ui"),
            }
            payload["packs"][rule_id] = pack
            self._save_unlocked(payload)
        return copy.deepcopy(pack)

    def activate(self, rule_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load_unlocked()
            pack = payload["packs"].get(str(rule_id or ""))
            if not pack:
                raise KeyError("重命名规则不存在")
            now = int(time.time())
            for item in payload["packs"].values():
                if (item.get("status") == "active" and item.get("scope") == pack.get("scope")
                        and str(item.get("selector") or "").casefold() == str(pack.get("selector") or "").casefold()):
                    item["status"] = "archived"
                    item["updated_at"] = now
            pack["status"] = "active"
            pack["activated_at"] = now
            pack["updated_at"] = now
            self._save_unlocked(payload)
        return copy.deepcopy(pack)

    def delete_draft(self, rule_id: str) -> bool:
        with self._lock:
            payload = self._load_unlocked()
            pack = payload["packs"].get(str(rule_id or ""))
            if not pack:
                return False
            if pack.get("status") != "draft":
                raise ValueError("只能删除尚未启用的规则草稿")
            del payload["packs"][str(rule_id)]
            self._save_unlocked(payload)
            return True


__all__ = [
    "BUILTIN_RULE_PACK", "DEFAULT_RULE_VALUES", "RenameRuleStore",
    "RULE_SCHEMA_VERSION", "merge_rule_values", "sanitize_rule_override", "sanitize_rules",
]
