# -*- coding: utf-8 -*-
"""渠道契约 —— 「用户选的」与「实际拿到的」必须对得上，且差异要**可见**。

## 移植来源与差异

参考实现 XimalayaApp `Core/DownloadEngine.cs:151-158` 的做法是**硬性**的：

```csharp
// 只走用户选定的那一个渠道 —— 绝不中途换渠道（2026-09-29 用户要求「每个接口独立」）。
private static List<SourceKind> ChannelOrder(SourceKind chosen) => new() { chosen };
```

撞上限制时按类型分流（`DownloadEngine.cs:322-340`）：
* 短时自愈的节流 → 原地等待恢复，**不降档不换渠道**；
* 当天没救的额度 → **抛 `ChannelQuotaException` 暂停整个任务**，交给用户决定。

## 本项目为什么不能照搬「硬性」

AudioFlow 是**无人值守的订阅下载器**（NAS 上跑定时任务）。硬性「不换渠道
就失败」会让订阅在平台限流时整夜空转，而用户第二天只看到「全部失败」。

所以本模块采取**第三条路**，也是移植分析 P0-4 的核心诉求：

> 保留兜底能力，但把「静默降级」变成「显式记录 + 用户可见」。

即：
1. **兜底照旧**（不影响现有功能与成功率）；
2. 但每次兜底都被记成一条 **downgrade 记录**，写进 `_report.json` 与
   章节状态，前端可查；
3. 用户拿到的文件名里带**实测档位标记**（既有机制），报告里带**完整链路**。

## 什么算「降级」

不是所有兜底都是降级。只有**实际交付的档位低于用户所选**才算：

| 用户所选 | 实际交付 | 判定 |
| --- | --- | --- |
| 无损优先 | `web_v3_lossless` | 不降级（拿到了） |
| 无损优先 | `web_v3`（M4A 128K） | **降级** |
| 无损优先 | `legacy_web_redirect` | **降级** |
| 96K | `public_base_info:playUrl64` | **降级** |
| 自动最高音质 | 任意 | 不降级（用户没指定） |

## 与 `quality_check.py` 的分工

* 本模块：**下载时**记录「承诺 vs 实际」，产出可读的降级说明；
* `quality_check.py`：**下载后**读文件实测码率，验证记录是否属实。
两者互补：前者抓「选错档」，后者抓「标错档」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 下载来源 → (展示名, 等效档位序号)。
#: 档位序号越大越好；`None` 表示「未知/不参与比较」。
SOURCE_QUALITY: Dict[str, tuple] = {
    # 移动端 V4（喜马拉雅最高档链路）
    "mobile_v4_level_13": ("Audio Vivid 菁彩声", 5),
    "mobile_v4_level_12": ("杜比全景声", 4),
    "mobile_v4_lossless": ("无损（FLAC/ALAC）", 3),
    "mobile_v4_level_3": ("移动端 无损档", 3),
    "mobile_v4_level_2": ("移动端 高音质", 2),
    "mobile_v4_level_1": ("移动端 标准", 1),
    "mobile_v4_level_0": ("移动端 低码率", 0),
    # 网页端 V3
    "web_v3_lossless": ("网页无损（FHQ/FLAC）", 3),
    "web_v3": ("网页 M4A（128K 档）", 2),
    # 电脑端下载接口
    "pc_download_v2_level_3": ("电脑版 无损档", 3),
    "pc_download_v2_level_2": ("电脑版 高音质", 2),
    "pc_download_v2_level_1": ("电脑版 标准", 1),
    "pc_download_v2_level_0": ("电脑版 低码率", 0),
    # 公开免费兜底（最低档）
    "legacy_web_redirect": ("旧版网页直连", 1),
    "legacy_redirect": ("旧版网页直连", 1),
    "public_base_info": ("公开免费地址", 0),
}

#: 用户所选档位 → 期望达到的最低档位序号。
#: 只列**用户明确表达过偏好**的档位；「自动最高音质」不在此表（不设期望）。
#: 键同时收录中英文写法，因为前端与各 manager 用的串不统一。
EXPECTED_LEVEL: Dict[str, int] = {
    # 明确要无损类
    "无损优先": 3,
    "无损优先（自动降级）": 3,
    "网页无损优先（FHQ WAV）": 3,
    "lossless": 3,
    "flac": 3,
    # 明确要杜比/Vivid
    "杜比全景声优先（自动降级）": 4,
    "Audio Vivid 优先（自动降级）": 5,
    # 明确要某码率档（喜马拉雅 legacy 档位）
    "96K": 2,
    "64K": 1,
    "48K": 1,
    "24K": 0,
    "128K": 2,
    "320K": 3,
}

#: 用户**没有**指定具体档位的偏好 —— 拿到什么算什么，不算降级。
NO_EXPECTATION_QUALITIES = frozenset({
    "喜马拉雅移动端接口（自动最高音质）",
    "喜马拉雅网页版接口",
    "喜马拉雅电脑版接口（自动最高音质）",
    "best",
    "自动最高音质",
    "",
})


def source_label(source: Any) -> str:
    """把内部 source 串翻成用户能读的档位名。"""
    text = str(source or "").strip()
    if not text:
        return "未知来源"
    if text in SOURCE_QUALITY:
        return SOURCE_QUALITY[text][0]
    # 形如 "public_base_info:playUrl64" / "legacy_web_redirect:xxx"
    head = text.split(":", 1)[0]
    if head in SOURCE_QUALITY:
        return SOURCE_QUALITY[head][0]
    return text


def source_level(source: Any) -> Optional[int]:
    """下载来源对应的档位序号；未知来源返回 None（不参与降级判定）。"""
    text = str(source or "").strip()
    if not text:
        return None
    if text in SOURCE_QUALITY:
        return SOURCE_QUALITY[text][1]
    head = text.split(":", 1)[0]
    return SOURCE_QUALITY[head][1] if head in SOURCE_QUALITY else None


def expected_level(quality: Any) -> Optional[int]:
    """用户所选档位期望达到的档位序号；未指定偏好时返回 None。"""
    text = str(quality or "").strip()
    if text in NO_EXPECTATION_QUALITIES:
        return None
    if text in EXPECTED_LEVEL:
        return EXPECTED_LEVEL[text]
    # 模糊匹配：`无损优先（自动降级）` 这类带后缀的写法
    for key, level in EXPECTED_LEVEL.items():
        if key and key in text:
            return level
    return None


def describe_downgrade(quality: Any, source: Any) -> str:
    """判断是否发生降级，返回一句人话；没有降级返回空串。

    ⚠ **只在两边都能判定档位时才下结论**。任一侧未知就返回空串 ——
    宁可漏报也不能误报（误报会让用户以为下载有问题而重下）。
    """
    want = expected_level(quality)
    if want is None:
        return ""
    got = source_level(source)
    if got is None:
        return ""
    if got >= want:
        return ""
    return (
        f"实际交付档位低于所选：所选「{str(quality).strip()}」，"
        f"实际为「{source_label(source)}」。"
        f"平台可能对该章节限制了更高档位（付费/版权/限流），已自动使用可用档位。"
    )


def build_note(
    quality: Any,
    source: Any,
    *,
    extra: Any = "",
) -> Dict[str, Any]:
    """构造一条渠道说明，供章节状态 / `_report.json` 使用。

    返回 `{"source","source_label","quality","downgraded","note"}`。
    """
    note = describe_downgrade(quality, source)
    if not note and extra:
        note = str(extra)[:200]
    return {
        "source": str(source or ""),
        "source_label": source_label(source),
        "quality": str(quality or ""),
        "downgraded": bool(note and describe_downgrade(quality, source)),
        "note": note,
    }


def collect_downgrades(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 `_report.json` 里汇总所有降级记录（供前端/CLI 展示）。"""
    items = (report or {}).get("downgrades")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def summarize_downgrades(items: Any) -> str:
    """把降级记录汇总成一句话（空列表返回空串）。"""
    rows = [i for i in (items or []) if isinstance(i, dict)]
    if not rows:
        return ""
    by_quality: Dict[str, int] = {}
    for row in rows:
        by_quality[str(row.get("quality") or "未指定")] = by_quality.get(
            str(row.get("quality") or "未指定"), 0
        ) + 1
    parts = "、".join(f"{q} × {n}" for q, n in by_quality.items())
    return f"{len(rows)} 集实际档位低于所选（{parts}）"


__all__ = [
    "SOURCE_QUALITY",
    "EXPECTED_LEVEL",
    "NO_EXPECTATION_QUALITIES",
    "source_label",
    "source_level",
    "expected_level",
    "describe_downgrade",
    "build_note",
    "collect_downgrades",
    "summarize_downgrades",
]
