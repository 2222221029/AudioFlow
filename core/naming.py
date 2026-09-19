"""文件与目录命名规则的单一出口。

## 为什么需要这个模块

改造前项目里存在 **4 份互不一致的"清洗文件名"实现**：

| 位置 | 长度上限 | 空值兜底 |
| --- | --- | --- |
| `core/download_worker.py` | 200 | `未知` |
| `core/subscription_manager.py` | 200 | `unknown` |
| `core/ximalaya_download_manager.py` | 150 | `未知音频` |
| `core/download_manager.py` | 150 | `未命名音频` |

四者字符表相同但上限与兜底串各不相同，属于"同一条规则、四种行为"——
调用方无法预期自己会拿到哪一种。本模块把它们收敛为带默认值的纯函数：

* 行为**完全等价**于各调用点此前的实现（由 `tests/test_naming.py` 的
  兼容性用例逐一锁定），因此改造本身不改变任何既有产物；
* 差异通过参数显式表达（`max_len` / `fallback` / `strip`），而不是靠复制粘贴。

参考实现 `kuwo_dl/naming.py` 的做法与此一致：命名是高频复用的纯逻辑，
应集中在一处、零依赖、可独立测试。

## 关于专辑目录与 album_id

`album_dirname()` 支持可选的 `album_id` 后缀（参考实现写作 `专辑名 [album_id]`）。
它默认**不开启**：给目录名追加 ID 会改变既有专辑的落盘路径，属于会打断
断点续传/已有归档的破坏性变更，应由部署方显式选择，而不是在重构中默默生效。
"""

from __future__ import annotations

import os
import re

# Windows 与 Linux 都不安全的路径字符
ILLEGAL_PATH_CHARS = ('<', '>', ':', '"', '/', '\\', '|', '?', '*')

# 剪辑市场常见的营销拼接串分隔符（酷我等平台的专辑标题普遍带这类后缀）
_MARKETING_SEPARATORS = ('|', '｜')

_WHITESPACE_RUN = re.compile(r"\s+")


def sanitize_segment(
    name,
    max_len: int = 200,
    fallback: str = "未知",
    *,
    strip: bool = True,
) -> str:
    """清洗单个路径片段（文件名或目录名）。

    行为与旧的四处实现逐项等价：

    * `strip=True` 时先去除首尾空白（`download_worker` / `subscription_manager` 的行为）；
    * 随后把 `ILLEGAL_PATH_CHARS` 逐个替换为下划线；
    * 超过 `max_len` 时截断；
    * 结果为空时返回 `fallback`。
    """
    text = str(name or "")
    if strip:
        text = text.strip()
    for char in ILLEGAL_PATH_CHARS:
        text = text.replace(char, "_")
    if len(text) > max_len:
        text = text[:max_len]
    return text or fallback


def sanitize_download_folder_name(name, max_len: int = 200) -> str:
    """目录名清洗（不 strip，空值兜底为「未知专辑」）。

    对应 `src/server/web_server.py` 的 `_sanitize_download_folder_name`。
    """
    return sanitize_segment(name, max_len=max_len, fallback="未知专辑", strip=False)


def collapse_whitespace(text) -> str:
    """把连续空白（含全角空格）压成单个半角空格。"""
    value = str(text or "").replace("\u3000", " ")
    return _WHITESPACE_RUN.sub(" ", value).strip()


def album_dirname(
    title,
    album_id=None,
    *,
    max_len: int = 150,
    short: bool = False,
    include_id: bool = False,
) -> str:
    """专辑目录名。

    :param short: 为 True 时在第一个 `|`/`｜` 处截断营销拼接串，
        例如「斗破苍穹|多播剧|完结放心听|…」→「斗破苍穹」。
    :param include_id: 为 True 时追加 `[album_id]` 以避免同名专辑互相覆盖。
        默认 False —— 见模块 docstring 中关于破坏性变更的说明。
    """
    name = str(title or "")
    if short:
        for separator in _MARKETING_SEPARATORS:
            if separator in name:
                head = name.split(separator, 1)[0].strip()
                if head:
                    name = head
                break
    cleaned = sanitize_segment(name, max_len=max_len, fallback="未知专辑")
    if include_id and album_id not in (None, ""):
        return f"{cleaned} [{album_id}]"
    return cleaned


def unique_path(path) -> str:
    """若目标已存在则追加 (2)(3)…，避免静默覆盖既有文件。"""
    candidate = str(path)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(candidate)
    index = 2
    while True:
        alternative = f"{base} ({index}){ext}"
        if not os.path.exists(alternative):
            return alternative
        index += 1
