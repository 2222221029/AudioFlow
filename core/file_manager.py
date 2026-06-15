# -*- coding: utf-8 -*-
"""本地有声书文件管理中心 — 后端核心"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from core.platform_config import config_dir, download_dir

# ============ 常量 ============

AUDIO_EXTS = {'.mp3', '.m4a', '.m4b', '.flac', '.wav', '.aac', '.ogg', '.opus'}

FM_CONFIG_FILE    = config_dir() / "fm_config.json"
FM_TEMPLATES_FILE = config_dir() / "fm_templates.json"
FM_HISTORY_FILE   = config_dir() / "fm_history.json"

ILLEGAL_CHARS = r'[/\\:*?"<>|]'

AD_PATTERNS = [
    r'www\.\S+\.\S+',
    r'https?://\S+',
    r'[QqＱＱ]{1,2}[:：]?\d{5,}',
    r'微信[:：]?\s*\S+',
    r'公众号[:：]?\s*\S+',
    r'\(.*?听书.*?\)',
    r'【.*?听书.*?】',
    r'【.*?下载.*?】',
]

DEFAULT_TEMPLATES = [
    {"id": "t1", "name": "原序号-《书名》第N集 章节名",         "template": "{original_prefix}-《{book_title}》第{chapter_index_3}集 {chapter_title}.{ext}"},
    {"id": "t2", "name": "原序号-《书名》[系列]-第N集 章节名",  "template": "{original_prefix}-《{book_title}》{series_block}第{chapter_index_3}集 {chapter_title}.{ext}"},
    {"id": "t3", "name": "序号-章节名",                        "template": "{chapter_index_3}-{chapter_title}.{ext}"},
    {"id": "t4", "name": "书名-序号-章节名",                   "template": "{book_title}-{chapter_index_3}-{chapter_title}.{ext}"},
    {"id": "t5", "name": "作者-书名-序号",                     "template": "[{author}]{book_title}-{chapter_index_3}.{ext}"},
    {"id": "t6", "name": "纯序号",                             "template": "{chapter_index_4}.{ext}"},
    {"id": "t7", "name": "第N章 章节名",                       "template": "第{chapter_index_3}章 {chapter_title}.{ext}"},
]


# ============ 工具函数 ============

def _get_audio_duration(path: Path) -> float:
    """用 mutagen 读取音频时长（秒），失败返回 0"""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(path))
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return 0.0


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024**3:.1f} GB"
    elif size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024**2:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return ""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ============ 扫描功能 ============

def scan_directory(root: str = None, quick: bool = False) -> dict:
    """扫描目录，返回 books 列表。quick=True 跳过音频时长读取，适合大量文件场景"""
    if root:
        root_path = Path(root)
    else:
        root_path = Path(download_dir())

    if not root_path.exists():
        raise ValueError(f"目录不存在: {root_path}")
    if not root_path.is_dir():
        raise ValueError(f"不是目录: {root_path}")

    books = []
    total_files = 0
    total_size = 0

    folder_map = {}  # folder_path -> [file_path, ...]

    try:
        entries = list(root_path.iterdir())
    except PermissionError:
        raise ValueError(f"无权限访问: {root_path}")

    for entry in entries:
        if entry.is_dir():
            folder_files = []
            try:
                for f in sorted(entry.iterdir()):
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                        folder_files.append(f)
            except PermissionError:
                pass
            if folder_files:
                folder_map[entry] = folder_files
        elif entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
            folder_map.setdefault(root_path, []).append(entry)

    for folder_path, files in folder_map.items():
        book_files = []
        folder_total_size = 0
        folder_total_duration = 0.0

        for f in sorted(files, key=lambda x: x.name):
            try:
                stat = f.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
            except OSError:
                size = 0
                mtime = ""

            duration = 0.0 if quick else _get_audio_duration(f)
            folder_total_size += size
            folder_total_duration += duration

            book_files.append({
                "path": str(f),
                "name": f.name,
                "ext": f.suffix.lstrip('.').lower(),
                "size": size,
                "size_fmt": _format_size(size),
                "duration": duration,
                "duration_fmt": _format_duration(duration) if duration else "",
                "mtime": mtime,
            })

        total_files += len(book_files)
        total_size += folder_total_size

        books.append({
            "folder_path": str(folder_path),
            "folder_name": folder_path.name if folder_path != root_path else "(根目录)",
            "files": book_files,
            "file_count": len(book_files),
            "total_size": folder_total_size,
            "total_size_fmt": _format_size(folder_total_size),
            "total_duration": folder_total_duration,
            "total_duration_fmt": _format_duration(folder_total_duration),
        })

    books.sort(key=lambda b: b["folder_name"])

    return {
        "root": str(root_path),
        "books": books,
        "total_books": len(books),
        "total_files": total_files,
        "total_size": total_size,
        "total_size_fmt": _format_size(total_size),
    }


# ============ 模板引擎 ============

def apply_template(template: str, book_meta: dict, file_info: dict, index: int) -> str:
    """将模板字符串渲染为文件名（不含路径）"""
    idx = index + 1  # 1-based

    name_no_ext = Path(file_info.get("name", "")).stem
    ai_title = book_meta.get("chapter_titles", {}).get(file_info.get("name", ""))
    if ai_title:
        chapter_title = ai_title
    else:
        # 无 AI 结果时基础提取：去掉"序号-书名"前缀 + 章节编号
        stem = name_no_ext
        book_title_val = book_meta.get("book_title", "").strip()
        if book_title_val:
            stem = re.sub(r'^\d+[-\s]+' + re.escape(book_title_val) + r'[-\s]*', '', stem).strip()
        stem = re.sub(r'^\d+[集章回话期]?\s*', '', stem).strip()
        chapter_title = stem if stem else name_no_ext
    # 去掉标题中（前的空格，如"玄门炼真 （一）"→"玄门炼真（一）"
    chapter_title = re.sub(r'\s+（', '（', chapter_title)

    # 从原文件名开头提取数字前缀（如 "0001-xxx" → "0001"）
    prefix_match = re.match(r'^(\d+)', name_no_ext)
    original_prefix = prefix_match.group(1) if prefix_match else str(idx).zfill(4)

    # 系列名块：有 series 时输出 "-【series】-"，没有时为空
    series = book_meta.get("series", "").strip()
    series_block = f"-【{series}】-" if series else ""

    variables = {
        "book_title": book_meta.get("book_title", ""),
        "author": book_meta.get("author", ""),
        "narrator": book_meta.get("narrator", ""),
        "category": book_meta.get("category", ""),
        "series": book_meta.get("series", ""),
        "volume": book_meta.get("volume", ""),
        "original_prefix": original_prefix,
        "series_block": series_block,
        "chapter_index": str(idx),
        "chapter_index_2": str(idx).zfill(2),
        "chapter_index_3": str(idx).zfill(3),
        "chapter_index_4": str(idx).zfill(4),
        "chapter_title": chapter_title,
        "chapter_full": f"{str(idx).zfill(3)}-{chapter_title}",
        "name": name_no_ext,
        "folder": Path(file_info.get("path", "")).parent.name,
        "ext": file_info.get("ext", "mp3"),
        "size": _format_size(file_info.get("size", 0)),
        "duration": _format_duration(file_info.get("duration", 0)),
        "date": datetime.now().strftime("%Y%m%d"),
    }

    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", str(value))

    # 清理非法字符（保留 ext 部分）
    if '.' in result:
        dot_idx = result.rfind('.')
        main_part = result[:dot_idx]
        ext_part = result[dot_idx:]
        main_part = re.sub(ILLEGAL_CHARS, '_', main_part)
        result = main_part + ext_part
    else:
        result = re.sub(ILLEGAL_CHARS, '_', result)

    result = result.strip('. ')
    return result


# ============ 重命名预览 ============

def preview_rename(folder_path: str, template: str, book_meta: dict) -> list:
    """返回预览列表"""
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"文件夹不存在: {folder_path}")

    audio_files = sorted(
        [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS],
        key=lambda x: x.name
    )

    previews = []
    new_names_seen = {}

    for idx, f in enumerate(audio_files):
        try:
            size = f.stat().st_size
        except OSError:
            size = 0

        file_info = {
            "path": str(f),
            "name": f.name,
            "ext": f.suffix.lstrip('.').lower(),
            "size": size,
            "duration": 0.0,  # 重命名不需要时长，跳过读取以支持大量文件
        }

        new_name = apply_template(template, book_meta, file_info, idx)
        new_path = str(folder / new_name)

        conflict = False
        if new_name in new_names_seen:
            conflict = True
        elif (folder / new_name).exists() and (folder / new_name) != f:
            conflict = True
        else:
            new_names_seen[new_name] = str(f)

        previews.append({
            "original_path": str(f),
            "original_name": f.name,
            "new_name": new_name,
            "new_path": new_path,
            "conflict": conflict,
        })

    return previews


# ============ 重命名执行 ============

def apply_rename(previews: list, operation_note: str = "") -> dict:
    """执行重命名，记录历史"""
    history_id = str(uuid.uuid4())
    success = 0
    failed = 0
    ops = []

    for item in previews:
        if item.get("conflict"):
            ops.append({
                "original_path": item["original_path"],
                "new_path": item["new_path"],
                "status": "skipped_conflict",
            })
            continue

        original_path = item["original_path"]
        new_path = item["new_path"]

        if original_path == new_path:
            ops.append({
                "original_path": original_path,
                "new_path": new_path,
                "status": "unchanged",
            })
            continue

        try:
            os.rename(original_path, new_path)
            ops.append({
                "original_path": original_path,
                "new_path": new_path,
                "status": "success",
            })
            success += 1
        except OSError as e:
            logging.error(f"重命名失败: {original_path} -> {new_path}: {e}")
            ops.append({
                "original_path": original_path,
                "new_path": new_path,
                "status": f"failed: {e}",
            })
            failed += 1

    history = load_history()
    history.insert(0, {
        "history_id": history_id,
        "timestamp": datetime.now().isoformat(),
        "note": operation_note,
        "ops": ops,
    })
    save_history(history)

    return {"success": success, "failed": failed, "history_id": history_id}


# ============ 历史与回滚 ============

def load_history() -> list:
    try:
        if FM_HISTORY_FILE.exists():
            with open(FM_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"读取历史失败: {e}")
    return []


def save_history(history: list) -> None:
    """写入，只保留最近 200 条操作（按 history_id 分组）"""
    seen_ids = []
    filtered = []
    for item in history:
        hid = item.get("history_id")
        if hid not in seen_ids:
            seen_ids.append(hid)
        if len(seen_ids) <= 200:
            filtered.append(item)

    try:
        FM_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FM_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存历史失败: {e}")


def rollback(history_id: str) -> dict:
    """回滚指定 history_id 的所有操作"""
    history = load_history()
    target = None
    for item in history:
        if item.get("history_id") == history_id:
            target = item
            break

    if not target:
        raise ValueError(f"未找到操作记录: {history_id}")

    success = 0
    failed = 0
    rollback_ops = []

    for op in reversed(target.get("ops", [])):
        if op.get("status") != "success":
            continue
        original_path = op["original_path"]
        new_path = op["new_path"]

        if not Path(new_path).exists():
            rollback_ops.append({
                "original_path": new_path,
                "new_path": original_path,
                "status": "failed: 文件不存在",
            })
            failed += 1
            continue

        try:
            os.rename(new_path, original_path)
            rollback_ops.append({
                "original_path": new_path,
                "new_path": original_path,
                "status": "success",
            })
            success += 1
        except OSError as e:
            logging.error(f"回滚失败: {new_path} -> {original_path}: {e}")
            rollback_ops.append({
                "original_path": new_path,
                "new_path": original_path,
                "status": f"failed: {e}",
            })
            failed += 1

    rollback_history_id = str(uuid.uuid4())
    history.insert(0, {
        "history_id": rollback_history_id,
        "timestamp": datetime.now().isoformat(),
        "note": f"[回滚] {target.get('note', '')} (原操作 {history_id[:8]})",
        "ops": rollback_ops,
    })
    save_history(history)

    return {"success": success, "failed": failed}


# ============ 模板管理 ============

def load_templates() -> list:
    try:
        if FM_TEMPLATES_FILE.exists():
            with open(FM_TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"读取模板失败: {e}")
    return list(DEFAULT_TEMPLATES)


def save_templates(templates: list) -> None:
    try:
        FM_TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FM_TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存模板失败: {e}")


# ============ 广告清理 ============

def clean_filename(name: str, custom_rules: list = None) -> str:
    """应用广告清理规则，返回清理后的名称"""
    result = name
    all_patterns = list(AD_PATTERNS)
    if custom_rules:
        all_patterns.extend(custom_rules)

    for pattern in all_patterns:
        try:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE)
        except re.error:
            pass

    result = re.sub(r'\s+', ' ', result).strip()
    return result


# ============ DeepSeek 配置 ============

_FM_CONFIG_DEFAULTS = {
    "ai_enabled": False,
    "ai_api_key": "",
    "ai_base_url": "https://api.deepseek.com",
    "ai_model": "deepseek-chat",
    "custom_ad_rules": [],
}


def load_fm_config() -> dict:
    cfg = dict(_FM_CONFIG_DEFAULTS)
    try:
        if FM_CONFIG_FILE.exists():
            with open(FM_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
    except Exception as e:
        logging.error(f"读取FM配置失败: {e}")
    return cfg


def save_fm_config(cfg: dict) -> dict:
    try:
        FM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存FM配置失败: {e}")
    return cfg


# ============ AI 分析 ============

def ai_analyze(file_names: list, config: dict) -> dict:
    """调用 DeepSeek 分析文件名列表"""
    if not config.get("ai_enabled"):
        raise ValueError("AI 分析未启用，请在配置设置中启用")
    api_key = config.get("ai_api_key", "")
    if not api_key:
        raise ValueError("未配置 AI API Key")

    base_url = config.get("ai_base_url", "https://api.deepseek.com").rstrip("/")
    model = config.get("ai_model", "deepseek-chat")

    prompt = f"""你是一个有声书文件名清理与规范化助手。请分析以下音频文件名列表，识别书籍信息，并对每个文件的章节标题进行规范化处理。

文件名列表：
{chr(10).join(f'{i+1}. {name}' for i, name in enumerate(file_names))}

请以 JSON 格式返回结果（不要有任何 markdown 代码块，只返回纯 JSON）：
{{
  "book_title": "书名",
  "author": "作者",
  "narrator": "主播",
  "category": "分类",
  "series": "系列名（如有）",
  "volume": "卷号（如有）",
  "confidence": 0.9,
  "items": [
    {{
      "original": "原始文件名（含扩展名，与输入完全一致）",
      "chapter_title": "规范化后的纯章节标题（不含扩展名）",
      "chapter_index": 1,
      "is_ad": false,
      "is_abnormal": false
    }}
  ]
}}

**章节标题规范化规则（非常重要）：**
1. 删除广告内容：网址、QQ号、微信号、公众号、"听书""下载"等促销文字、录制方/上传方署名（如"【xxx出品】""@xxx"）
2. 删除平台水印：如"喜马拉雅""懒人听书""番茄畅听"等平台名称附加内容
3. 统一标点：将全角标点转为对应中文标点（保留正常的中文句号、逗号等），删除多余的括号、方括号修饰
4. **剥离章节序号**：chapter_title 只保留正文标题，去掉开头的独立数字序号（含"集/章/回/话/期"等单位词）。例："1离家"→"离家"，"003集 酣睡"→"酣睡"，"第5章破晓"→"破晓"。**注意：保留标题中已有括号的分段标记，如"（一）""（二）"，但括号前不允许有空格**。例："玄门炼真 （一）"→"玄门炼真（一）"（序号由模板变量单独控制，不要留在标题里）
5. **同名章节分段括号化**：检测连续下载序号中底名相同的文件组（底名 = 去掉结尾的数字/中文数字后的标题）。满足以下条件时，将整组按下载序号顺序统一重新编号为（1）（2）（3）…：
   - 条件A：去掉结尾数字后底名完全相同，且下载序号连续
   - 条件B：**组内允许有无数字后缀的文件**——即"醋意十足"也属于"醋意十足"这个底名组，视为第一集
   - 编号规则：按下载序号从小到大，统一用阿拉伯数字括号（1）（2）（3）…（忽略原来的后缀数字，全部重新顺序编号）
   - 例1：0021-醋意十足一、0022-醋意十足二 →"醋意十足（1）""醋意十足（2）"
   - 例2：0022-醋意十足、0023-醋意十足1、0024-醋意十足2（序号连续，底名均为"醋意十足"）→"醋意十足（1）""醋意十足（2）""醋意十足（3）"
   - 若序号不连续则不处理，按规则4正常剥离序号
6. 去除重复信息：如文件名已含书名，则章节标题不重复包含书名
7. 保留核心信息：保留章节正文标题、回目名称等有意义内容，保留【篇章名】等分卷标记
8. 删除冗余后缀：如"（完结）""[全集]""_高清"等
9. is_ad 为 true 表示该文件整体是广告/片头片尾，建议跳过重命名
- original 必须与输入文件名完全一致（含扩展名），用于精确匹配"""

    def _do_request(post_fn, url, headers, payload):
        resp = post_fn(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r'^```\w*\n?', '', content)
            content = re.sub(r'\n?```$', '', content)
        return json.loads(content)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    endpoint = f"{base_url}/v1/chat/completions"

    try:
        import httpx
        return _do_request(httpx.post, endpoint, headers, payload)
    except ImportError:
        import requests as req_lib
        return _do_request(req_lib.post, endpoint, headers, payload)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI 返回内容无法解析为 JSON: {e}")
    except Exception as e:
        raise RuntimeError(f"AI 请求失败: {e}")
