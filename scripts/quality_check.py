#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地音质体检 CLI（移植分析 P2-12 / W4）。

## 用途

「我下到的东西到底对不对」—— 项目里有几类**不会表现为下载失败**的静默失效：

* 96K 档位映射错误 → 用户以为下了 96K、实际 48K，**文件名还写着 96K**；
* CDN 静默截断 → 本地 4.95MB / 服务端 16.80MB，「连接提前 EOF 却不抛异常」。

只能靠事后体检发现。

## 用法

    # 体检整个目录（只报问题文件）
    python3 scripts/quality_check.py --root "/vol1/1000/downloads/有声书"

    # 指定档位，检查是否达标
    python3 scripts/quality_check.py --album-dir "…/某专辑" --quality 96K

    # 输出 JSON（便于接监控 / 订阅周期自检）
    python3 scripts/quality_check.py --root "…" --json

    # 把问题文件列成可重下清单
    python3 scripts/quality_check.py --root "…" --list-redo

⚠ 本脚本**只读**，不改动、不删除任何文件。要修复请用各平台的重下流程
（或 `scripts/kuwo_integrity_check.py --fix`）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import quality_check  # noqa: E402

_VERDICT_LABEL = {
    "ok": "达标",
    "low_bitrate": "低码率",
    "damaged": "损坏/不完整",
    "missing": "缺失",
    "unreadable": "读不到（权限/挂载）",
}

_VERDICT_COLOR = {
    "ok": "\033[32m",
    "low_bitrate": "\033[33m",
    "damaged": "\033[31m",
    "missing": "\033[35m",
    "unreadable": "\033[36m",
}
_RESET = "\033[0m"


def _c(text, verdict, enabled):
    if not enabled:
        return text
    return f"{_VERDICT_COLOR.get(verdict, '')}{text}{_RESET}"


def find_album_dirs(root):
    """把 root 下含音频文件的目录都当成专辑目录（含 root 自身）。"""
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if any(os.path.splitext(f)[1].lower() in quality_check.local_index.AUDIO_EXTENSIONS for f in files):
            found.append(current)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地音质体检（只读，不改动文件）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--root", help="递归体检的根目录（自动识别其中的专辑目录）")
    group.add_argument("--album-dir", help="只体检单个专辑目录")
    parser.add_argument("--quality", default="", help="期望档位，例如 96K / 无损优先（自动降级）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--list-redo", action="store_true", help="只打印需要重下的文件路径")
    parser.add_argument("--all", action="store_true", help="连达标的文件也逐条打印")
    parser.add_argument("--no-color", action="store_true", help="关闭颜色输出")
    args = parser.parse_args(argv)

    color = (not args.no_color) and sys.stdout.isatty()

    if args.album_dir:
        targets = [args.album_dir]
        if not os.path.isdir(args.album_dir):
            print(f"❌ 目录不存在：{args.album_dir}", file=sys.stderr)
            return 2
    else:
        if not os.path.isdir(args.root):
            print(f"❌ 目录不存在：{args.root}", file=sys.stderr)
            return 2
        targets = find_album_dirs(args.root)

    if not targets:
        print("⚠️ 没找到任何含音频文件的目录")
        return 0

    reports = []
    for directory in targets:
        reports.append(quality_check.check_directory(directory, expected_quality=args.quality))

    if args.list_redo:
        for report in reports:
            for path in quality_check.needs_redo(report):
                print(path)
        return 0

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0

    total = sum(r["total"] for r in reports)
    ok = sum(r["ok"] for r in reports)
    low = sum(r["low_bitrate"] for r in reports)
    damaged = sum(r["damaged"] for r in reports)
    unreadable = sum(r.get("unreadable", 0) for r in reports)
    problems = sum(len(quality_check.needs_redo(r)) for r in reports)

    print(f"🔍 体检目录：{len(reports)} 个，音频文件 {total} 个"
          + (f"（期望档位：{args.quality}）" if args.quality else ""))
    print()

    for report in reports:
        if not report["total"]:
            continue
        if report["files"] or args.all:
            mark = "✅" if report["healthy"] else "⚠️"
            print(f"{mark} {report['directory']}  "
                  f"达标 {report['ok']}/{report['total']}（{report['pass_rate']}%）")
            for item in report["files"]:
                verdict = item["verdict"]
                name = os.path.basename(item["path"])
                label = _c(_VERDICT_LABEL.get(verdict, verdict), verdict, color)
                detail = "；".join(item["issues"]) or item.get("error", "")
                print(f"    [{label}] {name}")
                if detail:
                    print(f"           {detail}")
            print()

    print("─" * 60)
    parts = [
        _c(f"达标 {ok}", "ok", color),
        _c(f"低码率 {low}", "low_bitrate", color),
        _c(f"损坏 {damaged}", "damaged", color),
    ]
    if unreadable:
        parts.append(_c(f"读不到 {unreadable}", "unreadable", color))
    print(f"总计 {total} 个文件：" + " · ".join(parts))

    readable = total - unreadable
    if readable:
        print(f"达标率：{round(ok * 100.0 / readable, 1)}%（分母只算可读文件 {readable} 个）")
    if unreadable:
        print(f"⚠️ 有 {unreadable} 个文件读不到（权限或挂载问题）—— 重下也解决不了，已排除在达成率与重下清单外。")
    if problems:
        print(f"\n💡 有 {problems} 个文件需要重下，可用 --list-redo 导出清单。")
    elif readable and ok == readable:
        print("\n🎉 全部达标。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
