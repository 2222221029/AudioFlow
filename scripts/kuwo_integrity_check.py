#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""酷我听书专辑完整性检查与修复。

背景（本脚本要修的坑）
----------------------
酷我 albumInfo 接口在**并发分页**请求下会偶发把相邻页的响应返给当前页请求。
实测并发 10 线程抓 1952 集（82 页）时约 23% 的全量抓取会命中错位，单线程顺序抓取
486 次请求 0 错位。错位响应的 success 仍为 True，所以「重试失败页」的老逻辑发现不了；
而一次错位就是一整页（酷我每页固定 rn=24）：

  * 错位页的 24 个位置拿到了别的页的 rid  -> 同样内容被重复下载 24 集
  * 那 24 集的真实章节从未进入下载列表    -> 永久缺失

典型症状是出现「序号前缀与标题自相矛盾」的文件，例如：

    1111-第1039集 新任站长（2）.mp3     （第 1111 个位置装了第 1039 集的内容）

core/kuwo_manager.py 已加入页级校验 + 错位自动重抓 + rid 去重（防止再发生）；
本脚本用于排查并修复**历史遗留**的坏文件：以远端目录为基准逐章核对，
把位置装错的文件挑出来，可选地用正确的章节重新下载。

用法
----
  # 只检查（不改动任何文件）
  python3 scripts/kuwo_integrity_check.py --root "/vol1/1000/downloads/有声书/酷我听书"

  # 只检查某个专辑，并显式指定专辑 ID（source.json 读不到时用）
  python3 scripts/kuwo_integrity_check.py --album-dir "…/谍影风云_…" --album-id 95521606

  # 执行修复（坏文件移入隔离目录，再用正确章节重下）
  python3 scripts/kuwo_integrity_check.py --album-dir "…" --fix

坏文件不会被真删除，而是移动到专辑目录下的 .kuwo_bad_files/ 便于回滚。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".aac", ".wav", ".wma"}
SOURCE_FILE = "source.json"
QUARANTINE_DIR = ".kuwo_bad_files"
NAME_RE = re.compile(r"^(\d+)\s*[-_]\s*(.+)\.([A-Za-z0-9]{2,4})$")


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------

def parse_name(filename):
    """把 {序号}-{标题}.{扩展名} 拆开；不符合命名约定的返回 None。"""
    match = NAME_RE.match(filename)
    if not match:
        return None
    prefix, title, ext = match.group(1), match.group(2), "." + match.group(3).lower()
    if ext not in AUDIO_EXT:
        return None
    return prefix, title, ext


def title_core(text):
    """标题核心词：去掉书名号、集号、数字与标点，只留中英文，用于宽松比对。

    本地文件名可能被重命名流程加工过（如「0433-《北齐怪谈》第433集 将才（一）」），
    而远端标题是原始形态（「北齐怪谈-0433-将才（一）」），必须归一化后才能可靠比较。
    """
    value = str(text or "")
    value = re.sub(r"《[^》]*》", "", value)
    value = re.sub(r"第\s*[0-9０-９]+\s*[集章回节]", "", value)
    value = re.sub(r"[0-9０-９]+", "", value)
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value)
    return value.lower()


def extract_order_number(text):
    """从标题里尽力提取集号（兼容「第433集」「0241-标题」「专辑名-0061-标题」）。"""
    value = str(text or "")
    for pattern in (r"第\s*([0-9]+)\s*[集章回节]", r"^\s*([0-9]{2,5})\s*[-_]", r"-\s*([0-9]{2,5})\s*-"):
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            number = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def titles_match(local_title, remote_title):
    """判断本地标题与远端标题是否指同一章节。

    远端标题常带「专辑名-集号-」前缀（如「北齐怪谈-0060-三天（一）」），必须先剥掉，
    否则本地「第060集 三天（二）」与它比对时核心词会带上一整段专辑名而永不匹配。
    返回 True/False；无法判断（核心词为空）时返回 None，调用方应视为「跳过校验」。
    """
    local = title_core(local_title)
    remote = title_core(extract_chapter_name(remote_title))
    if not local or not remote:
        return None
    if local == remote:
        return True
    shorter, longer = (local, remote) if len(local) <= len(remote) else (remote, local)
    # 门槛取 2：中文有声书章节名常只有两三个字（「贼（一）」「苦（一）」）
    if len(shorter) >= 2 and shorter in longer:
        return True
    return False


def extract_chapter_name(remote_title):
    """从远端标题里剥出纯章节名（用于按本地风格重新拼文件名）。"""
    value = str(remote_title or "").strip()
    value = re.sub(r"^.*?-\s*[0-9]+\s*-\s*", "", value)   # 「专辑名-0041-标题」
    value = re.sub(r"^\s*[0-9]{2,5}\s*[-_]\s*", "", value)  # 「0145-标题」
    value = re.sub(r"^第\s*[0-9]+\s*[集章回节]\s*", "", value)
    return value.strip() or str(remote_title or "").strip()


def file_md5(path, chunk_size=1 << 20):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}GB"


# ----------------------------------------------------------------------
# 专辑发现与元数据
# ----------------------------------------------------------------------

def find_album_dirs(root):
    """找出所有直接包含音频文件的目录（即一张专辑）。"""
    found = []
    for base, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if not name.startswith(".")]
        if any(Path(name).suffix.lower() in AUDIO_EXT for name in files):
            found.append(Path(base))
    return sorted(set(found))


def load_source_info(directory):
    """读取 AudioFlow 写在专辑目录里的 source.json（含 platform / album_id / quality）。"""
    path = Path(directory) / SOURCE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except PermissionError:
        print(f"   ⚠️ 无法读取 {path}（权限不足），请用 --album-id 显式指定专辑 ID")
        return {}
    except (ValueError, OSError) as exc:
        print(f"   ⚠️ 解析 {path} 失败：{exc}")
        return {}


def resolve_quality(source_info, override=""):
    if override:
        return override
    quality = str((source_info or {}).get("quality") or "")
    lowered = quality.lower()
    if "128" in quality:
        return "standard"
    if "192" in quality or "320" in quality:
        return "high"
    if "无损" in quality or "lossless" in lowered or "flac" in lowered:
        return "lossless"
    return "lossless"


# ----------------------------------------------------------------------
# 离线检测
# ----------------------------------------------------------------------

def scan_entries(directory):
    """列出专辑目录里符合 {序号}-{标题}.{ext} 的文件。"""
    entries = []
    for name in sorted(os.listdir(directory)):
        path = Path(directory) / name
        if not path.is_file():
            continue
        parsed = parse_name(name)
        if not parsed:
            continue
        prefix, title, ext = parsed
        entries.append({"order_text": prefix, "title": title, "ext": ext, "path": path})
    return entries


def find_duplicate_groups(entries):
    """按 size 预筛再算 md5，找出字节级完全相同的文件组。"""
    by_size = defaultdict(list)
    for entry in entries:
        try:
            by_size[entry["path"].stat().st_size].append(entry)
        except OSError:
            continue
    groups = []
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        by_hash = defaultdict(list)
        for entry in items:
            try:
                by_hash[file_md5(entry["path"])].append(entry)
            except OSError:
                continue
        for digest, same in by_hash.items():
            if len(same) > 1:
                groups.append({"md5": digest, "size": size, "entries": same})
    return groups


def infer_prefix_style(entries):
    """推断文件名序号是否补零：返回补零宽度，纯数字不补零时返回 None。

    不能用「补零文件占多数」判断：1000 集以上的专辑里，>=1000 的序号本身就有 4 位，
    天然没有前导零，占比会低于一半，从而把补零专辑误判成不补零（新文件会写成
    「167-...」而同目录既有文件是「0167-...」）。只要存在可观的补零文件即可认定。
    """
    if not entries:
        return None
    padded = [
        entry for entry in entries
        if len(entry["order_text"]) > 1
        and entry["order_text"].startswith("0")
        and entry["order_text"].lstrip("0")
    ]
    if len(padded) < max(3, len(entries) * 0.05):
        return None
    return max(len(entry["order_text"]) for entry in entries)


def infer_title_style(entries):
    """从本地文件学习标题风格（是否带《专辑名》、是否带「第N集」）。

    修复时新下载的文件必须与同目录既有命名保持一致，否则同一集会出现两种文件名。
    """
    style = {"label": "", "episode_tag": False}
    if not entries:
        return style
    labels = Counter()
    episode_tagged = 0
    for entry in entries:
        match = re.search(r"《([^》]+)》", entry["title"])
        if match:
            labels[match.group(1)] += 1
        if re.search(r"第\s*[0-9]+\s*集", entry["title"]):
            episode_tagged += 1
    total = len(entries)
    if labels:
        label, count = labels.most_common(1)[0]
        if count >= max(3, total * 0.3):
            style["label"] = label
    style["episode_tag"] = episode_tagged >= total * 0.5
    return style


def build_filename(order, remote_title, ext, prefix_width, style):
    """按检测到的本地命名风格生成目标文件名。"""
    name = extract_chapter_name(remote_title)
    body = f"第{order}集 {name}" if (style or {}).get("episode_tag") else name
    label = (style or {}).get("label")
    if label:
        body = f"《{label}》{body}"
    prefix = str(order).zfill(prefix_width) if prefix_width else str(order)
    return f"{prefix}-{body}{ext}"


# ----------------------------------------------------------------------
# 远端目录校验
# ----------------------------------------------------------------------

def load_remote_directory(album_id):
    """拉取远端完整目录（已带响应错配防护），返回 (manager, chapters, order -> chapter)。"""
    from core.kuwo_manager import KuwoManager

    manager = KuwoManager()
    chapters = manager.get_chapters(str(album_id), page=1, page_size=1000000) or []
    by_order = {}
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        try:
            order = int(chapter.get("order_num"))
        except (TypeError, ValueError):
            continue
        by_order[order] = chapter
    return manager, chapters, by_order


def find_neighbor_match(by_order, order, local_title, window=3):
    """在远端 order 附近 ±window 内找标题匹配的章节。

    远端目录自身会有重复/跳号条目（实测《北齐怪谈》位 59 与 60 都是「三天（一）」），
    这会把本地完全正确的连续文件判成错位。找到相邻匹配即说明只是远端条目漂移。
    """
    for offset in range(1, window + 1):
        for candidate in (order - offset, order + offset):
            remote = by_order.get(candidate)
            if remote and titles_match(local_title, remote.get("title")) is True:
                return candidate
    return None


def inspect_album(directory, album_id="", verbose=False):
    """核对一张专辑，返回检查结果。"""
    entries = scan_entries(directory)
    result = {
        "directory": Path(directory),
        "entries": entries,
        "source": load_source_info(directory),
        "duplicates": find_duplicate_groups(entries),
        "album_id": str(album_id or ""),
        "remote_total": 0,
        "bad": [],
        "missing": [],
        "unmatched_files": [],
        "notes": [],
    }
    if not result["album_id"]:
        result["album_id"] = str(result["source"].get("album_id") or "")

    platform = str((result["source"] or {}).get("platform") or "")
    if platform and platform != "酷我听书":
        result["notes"].append(f"source.json 平台为 {platform}，非酷我听书，已跳过远端校验")
        return result

    if not result["album_id"]:
        result["notes"].append("缺少专辑 ID（source.json 不可用且未传 --album-id），只能做离线重复检测")
        return result

    manager, chapters, by_order = load_remote_directory(result["album_id"])
    result["remote_total"] = len(chapters)
    result["manager"] = manager
    result["remote_by_order"] = by_order
    if not by_order:
        result["notes"].append("远端目录为空，已跳过逐章核对")
        return result

    duplicate_orders = set()
    for group in result["duplicates"]:
        for entry in group["entries"]:
            duplicate_orders.add(int(entry["order_text"]))

    for entry in entries:
        order = int(entry["order_text"])
        remote = by_order.get(order)
        if remote is None:
            result["unmatched_files"].append({**entry, "reason": "本地序号超出远端目录范围"})
            continue
        verdict = titles_match(entry["title"], remote.get("title"))
        if verdict is False:
            neighbor = find_neighbor_match(by_order, order, entry["title"])
            if neighbor is not None:
                if verbose:
                    result["notes"].append(
                        f"第 {order} 位本地「{entry['title']}」对应远端第 {neighbor} 位"
                        "（远端目录条目漂移，本地文件正常）"
                    )
                continue
            # 集号一致、只有子标题不同：远端目录把一集拆成多条或存在重复条目所致
            # （实测《北齐怪谈》远端位 59/60 都是「三天（一）」），本地文件本身没问题。
            # 真错位会表现为集号与位置差好几页（如本地第 145 位装着集号 241 的内容）。
            local_number = extract_order_number(entry["title"])
            remote_number = extract_order_number(remote.get("title"))
            if local_number and remote_number and abs(local_number - remote_number) <= 5:
                if verbose:
                    result["notes"].append(
                        f"第 {order} 位本地「{entry['title']}」与远端「{remote.get('title')}」"
                        "集号一致，仅子标题不同（远端目录条目问题）"
                    )
                continue
            result["bad"].append({
                **entry,
                "reason": "内容与序号不符",
                "expected_title": str(remote.get("title") or ""),
                "rid": str(remote.get("id") or ""),
                "in_duplicate_group": order in duplicate_orders,
            })
        elif verdict is None and verbose:
            result["notes"].append(
                f"第 {order} 个位置标题无法自动判定：本地「{entry['title']}」/ 远端「{remote.get('title')}」"
            )

    # 真实缺失：远端有该序号，本地既没有同序号文件、也没有标题一致的其它文件。
    # 注意要排除本轮判定为坏的文件——它们随后会被隔离，其承载的内容等于丢失。
    bad_paths = {item["path"] for item in result["bad"]}
    local_orders = {int(entry["order_text"]) for entry in entries}
    local_cores = {
        title_core(entry["title"]) for entry in entries if entry["path"] not in bad_paths
    }
    for order in sorted(by_order):
        if order in local_orders:
            continue
        remote = by_order[order]
        core = title_core(extract_chapter_name(remote.get("title")))
        if core and core in local_cores:
            continue
        result["missing"].append({
            "order": order,
            "rid": str(remote.get("id") or ""),
            "title": str(remote.get("title") or ""),
        })
    if len(result["missing"]) > max(20, result["remote_total"] * 0.2):
        result["notes"].append(
            f"缺失 {len(result['missing'])} 集，超过远端总数的两成，疑似命名规则与序号不一致，"
            "已跳过自动补下（请人工确认后再处理）"
        )
        result["missing_suspect"] = True

    by_order_text = defaultdict(list)
    for entry in entries:
        by_order_text[int(entry["order_text"])].append(entry)
    for order, items in sorted(by_order_text.items()):
        if len(items) > 1:
            result["notes"].append(
                "序号 {} 存在 {} 个文件：{}".format(
                    order, len(items), "、".join(item["path"].name for item in items)
                )
            )
    return result


# ----------------------------------------------------------------------
# 修复
# ----------------------------------------------------------------------

def build_repair_plan(result):
    """计算修复计划：命名风格、以及哪些序号已经有正确文件（只需隔离、无需重下）。"""
    prefix_width = infer_prefix_style(result["entries"])
    style = infer_title_style(result["entries"])
    remote_by_order = result.get("remote_by_order") or {}
    healthy_orders = set()
    for entry in result["entries"]:
        order = int(entry["order_text"])
        remote = remote_by_order.get(order)
        if remote and titles_match(entry["title"], remote.get("title")) is True:
            healthy_orders.add(order)
    return prefix_width, style, healthy_orders


def collect_download_targets(result):
    """汇总需要重新下载的章节：装错的坏文件位置 + 远端有而本地缺失的位置。"""
    _prefix_width, _style, healthy_orders = build_repair_plan(result)
    targets = []
    seen = set()
    for item in result["bad"]:
        order = int(item["order_text"])
        if order in healthy_orders or order in seen:
            continue
        seen.add(order)
        targets.append({
            "order": order,
            "rid": item.get("rid") or "",
            "title": item["expected_title"],
            "ext": item["ext"],
        })
    if not result.get("missing_suspect"):
        for item in result.get("missing") or []:
            order = int(item["order"])
            if order in seen:
                continue
            seen.add(order)
            targets.append({
                "order": order,
                "rid": item.get("rid") or "",
                "title": item["title"],
                "ext": ".mp3",
            })
    return sorted(targets, key=lambda entry: entry["order"])


def describe_plan(result):
    """生成人类可读的修复计划行。"""
    prefix_width, style, healthy_orders = build_repair_plan(result)
    lines = []
    for item in result["bad"]:
        order = int(item["order_text"])
        if order in healthy_orders:
            lines.append(f"隔离 {item['path'].name}（第 {order} 位已有正确文件，无需重下）")
        else:
            target = build_filename(order, item["expected_title"], item["ext"], prefix_width, style)
            lines.append(f"隔离 {item['path'].name} → 重下 {target}")
    for item in result.get("missing") or []:
        target = build_filename(int(item["order"]), item["title"], ".mp3", prefix_width, style)
        lines.append(f"本地缺第 {item['order']} 位 → 补下 {target}")
    if result.get("missing_suspect"):
        lines.append("（缺失数量异常偏多，已暂缓自动补下，请人工确认）")
    return lines


def repair_album(result, quality="", assume_yes=False):
    """把坏文件移入隔离目录，再补齐装错与缺失的章节。"""
    directory = result["directory"]
    bad = result["bad"]
    targets = collect_download_targets(result)
    if not bad and not targets:
        return 0

    manager = result.get("manager")
    if manager is None or not result.get("album_id"):
        print("   ❌ 缺少专辑 ID，无法重新下载；请用 --album-id 指定后重试")
        return 0

    prefix_width, style, _healthy = build_repair_plan(result)

    print(f"\n   📋 修复计划：隔离 {len(bad)} 个坏文件，重新下载 {len(targets)} 集")
    for line in describe_plan(result):
        print(f"      ✗ {line}")
    if not assume_yes:
        answer = input("\n   确认执行修复？坏文件会先移入隔离目录 [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("   已取消")
            return 0

    quarantine = directory / QUARANTINE_DIR
    quarantine.mkdir(exist_ok=True)
    for item in bad:
        path = item["path"]
        try:
            shutil.move(str(path), str(quarantine / path.name))
        except OSError as exc:
            print(f"   ❌ 隔离失败 {path.name}：{exc}")

    fixed = 0
    for target in targets:
        order = target["order"]
        path = directory / build_filename(order, target["title"], target["ext"], prefix_width, style)
        if path.exists() and path.stat().st_size > 1024:
            print(f"   ✅ 已存在，跳过：{path.name}")
            fixed += 1
            continue

        rid = target["rid"]
        if not rid:
            print(f"   ❌ 第 {order} 位没有 rid，无法下载")
            continue
        try:
            info = manager.get_download_info(rid, quality) if quality else manager.get_download_info(rid)
        except Exception as exc:  # noqa: BLE001 - 单章失败不应中断整轮修复
            print(f"   ❌ 获取下载地址失败（第 {order} 位 / rid={rid}）：{exc}")
            continue
        if not info or not info.get("url"):
            print(f"   ❌ 未取到音频地址（第 {order} 位 / rid={rid}）：{manager.last_error or '未知原因'}")
            continue

        path = path.with_suffix(str(info.get("extension") or target["ext"]))
        try:
            ok = manager.download_audio(info["url"], str(path), chapter_id=str(rid))
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 下载异常（第 {order} 位）：{exc}")
            continue
        if ok:
            fixed += 1
            print(f"   ✅ 已下载：{path.name}")
        else:
            print(f"   ❌ 下载失败（第 {order} 位）：{manager.last_error or '未知原因'}")

    print(f"\n   修复完成：成功 {fixed}/{len(targets)}；坏文件已隔离到 {quarantine}")
    return fixed


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------

def report(result, verbose=False):
    directory = result["directory"]
    print(f"\n=== {directory.name} ===")
    print(
        f"   本地文件 {len(result['entries'])} 个；远端目录 {result['remote_total'] or '未获取'} 集；"
        f"专辑 ID {result['album_id'] or '未知'}"
    )

    for note in result["notes"]:
        print(f"   ⚠️ {note}")

    if result["duplicates"]:
        print(f"   ❌ 字节级完全重复 {len(result['duplicates'])} 组（同一内容被下载了多次）：")
        for group in result["duplicates"]:
            print(f"      md5={group['md5'][:10]} size={human_size(group['size'])}")
            for entry in group["entries"]:
                print(f"         {entry['path'].name}")
    else:
        print("   ✅ 未发现字节级重复文件")

    if result["bad"]:
        print(f"   ❌ 序号位置与内容不符 {len(result['bad'])} 个（真实章节从未下载）：")
        for item in result["bad"]:
            flag = "（重复组）" if item.get("in_duplicate_group") else ""
            print(f"      第{item['order_text']}位  {item['path'].name}{flag}")
            print(f"          远端该位置应为：{item['expected_title']}")
    elif result["remote_total"]:
        print("   ✅ 所有文件的序号位置与远端目录一致")

    if result.get("missing"):
        print(f"   ❌ 本地缺失 {len(result['missing'])} 集（远端有、本地无对应文件）：")
        for item in result["missing"][:30]:
            print(f"      第 {item['order']} 位  {item['title']}")
        if len(result["missing"]) > 30:
            print(f"      … 其余 {len(result['missing']) - 30} 集省略")

    if result["unmatched_files"]:
        print(f"   ⚠️ 序号超出远端范围 {len(result['unmatched_files'])} 个：")
        for item in result["unmatched_files"][:10]:
            print(f"      {item['path'].name}")

    if result["bad"] or result.get("missing"):
        print(f"   🛠 修复预览（加 --fix 执行）：")
        for line in describe_plan(result):
            print(f"      {line}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="酷我听书专辑完整性检查与修复")
    parser.add_argument("--root", help="专辑根目录（递归查找所有专辑目录）")
    parser.add_argument("--album-dir", action="append", default=[], help="指定单个专辑目录，可重复")
    parser.add_argument("--album-id", default="", help="酷我专辑 ID（source.json 不可用时使用）")
    parser.add_argument("--quality", default="", help="重新下载的音质：lossless/high/standard")
    parser.add_argument("--fix", action="store_true", help="执行修复（隔离坏文件并重下）")
    parser.add_argument("--yes", action="store_true", help="修复时不再交互确认")
    parser.add_argument("--verbose", action="store_true", help="输出更多细节")
    args = parser.parse_args(argv)

    if args.root:
        directories = find_album_dirs(args.root)
    else:
        directories = [Path(p) for p in args.album_dir]
    if not directories:
        parser.error("请用 --root 或 --album-dir 指定要检查的目录")

    print(f"🔍 待检查专辑目录 {len(directories)} 个")
    total_bad = 0
    for directory in directories:
        try:
            result = inspect_album(directory, album_id=args.album_id, verbose=args.verbose)
        except Exception as exc:  # noqa: BLE001 - 单张专辑失败不应中断整轮检查
            print(f"\n=== {Path(directory).name} ===\n   ❌ 检查失败：{exc}")
            continue
        report(result, verbose=args.verbose)
        total_bad += len(result["bad"]) + len(result.get("missing") or [])
        if args.fix and (result["bad"] or result.get("missing")):
            quality = resolve_quality(result.get("source"), args.quality)
            repair_album(result, quality=quality, assume_yes=args.yes)

    print(f"\n总计：{total_bad} 个章节需要处理（内容与序号不符 + 本地缺失）")
    if total_bad and not args.fix:
        print("加 --fix 可隔离坏文件并用正确章节重新下载")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
