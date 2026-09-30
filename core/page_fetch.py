# -*- coding: utf-8 -*-
"""分页抓取的并发调度公共件 —— 「槽位即页号」。

## 为什么需要这个模块

移植自参考实现 XimalayaApp 的 `Core/PageFetch.cs:60-159`。它把「并发分页」
从各平台抄来抄去的模板，抽成一个公共件，并把三条踩过的坑写在契约里。

### 关键洞察

**乱序不是问题，问题是没重排。** 并发天然打乱到达顺序，只要每页结果带着
自己的**页号**回来、最后按页号拼装，结果就与串行**完全等价**。

参考实现实测收益：哈利波特 671 集，`串行 8.6s → 并发 6 路 2.1s`，且
「排序后内容一致 = True」。

### 三条必读约束（参考实现原话）

1. **页号必须随结果一起返回，不能靠「完成顺序」推断。**
   酷我历史上的「并发会响应错配且 success 仍为 true」—— 根因**不是并发本身
   不安全**，而是当时**按到达顺序 AddRange，没有重排**，于是最后一个到达的页
   被当成第一页。带上页号重排即可根治。
2. **别把易变状态放在共享闭包里。** 每页结果写进 `results[页号-1]`，
   不写外层 list（并发写 list 会直接抛 `InvalidOperationException`/数据竞争）。
3. **重试粒度 = 单页，不是整批。** 一页抖动不该让整本重来。

### 本项目改造前的状态

`core/ximalaya_manager.py:1511` 的 `_fetch_chapters_concurrent` **已经做对了
重排**（`:1573-1575` 的注释明确记录了章节目录乱序导致「已下载章节被判缺失
反复下载」的事故），但：

* 只在 `page_size > 1000` 时才走并发，常规 200 一页的路径是**串行 while 循环**
  （`:1194-1215`）；
* 单页失败只 `print` **不重试**（`:1560-1563`），缺页只能靠最后的计数 WARN 发现；
* 其它平台（kuwo / lrts / fanqie / yuntu）**没有并发分页**。

本模块把这些统一到一套契约上，并且**默认不改变任何调用点的行为** ——
只有显式接入的地方才享受并发。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Iterable, List, Optional, Sequence, TypeVar

from core.errors import PermissionDenied

T = TypeVar("T")
TItem = TypeVar("TItem")
TPage = TypeVar("TPage")


#: 默认并发度。参考实现实测：单页 RTT 抖动 172~1219ms，并发收益在 6 路左右
#: 已接近饱和（4 路 3.2s / 6 路 2.1s / 8 路 1.6s），再往上收益递减而瞬时 QPS
#: 变大、被风控/限流的概率上升。
DEFAULT_CONCURRENCY = 6

#: **有节流/风控平台**的并发度（喜马拉雅、网易云、云听这类）。
GENTLE_CONCURRENCY = 6

#: **瘦接口**（无节流）平台的并发度。参考实现实测（起点 3050 集 = 102 页）：
#: 单页 RTT 稳定 ~180ms、服务端不限速，6 路 2.87s / 10 路 1.72s / 16 路 1.11s。
#: ⚠ 判据是「**实测过没有限速拐点**」，不要凭感觉给平台升档。
FAST_CONCURRENCY = 16

#: 单页重试次数（含首次）。
DEFAULT_PAGE_RETRIES = 2

#: 单页退避基数（秒）：0.35 / 0.7 / 1.4 …
_BACKOFF_BASE = 0.35


def page_count(total: int, page_size: int) -> int:
    """按「每页 N 条」把总数换成页数（向上取整）。"""
    if total is None or page_size is None:
        return 0
    try:
        total = int(total)
        page_size = int(page_size)
    except (TypeError, ValueError):
        return 0
    if total <= 0 or page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size


def fetch_all(
    total_pages: int,
    fetch_page: Callable[[int], Any],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    retries: int = DEFAULT_PAGE_RETRIES,
    first_page: Any = None,
    on_progress: Optional[Callable[[int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    on_page_error: Optional[Callable[[int, BaseException], None]] = None,
) -> List[Any]:
    """并发抓取第 1..total_pages 页，返回**按页号对齐**的列表。

    保证（与参考实现逐条对应）：

    * 返回列表长度恒为 `total_pages`，下标 i 对应第 i+1 页；
    * 某页彻底失败（重试用尽）→ 该位置为 `None`，**不抛异常**，
      由调用方决定「缺页怎么补」（下一轮补抓 / 少几页可接受）。
      这样单页抖动不会让整本加载失败 —— 这正是「不出错」的关键。
    * `PermissionDenied` **不重试**（下架/未登录，重试也白搭），
      但同样落成 `None` 而不是整批失败。

    :param fetch_page: 抓第 p 页（p 从 1 起）。**必须返回该页自己的结果。**
    :param first_page: 已有结果的首帧；非 None 时第 1 页直接用它，不重复请求。
    :param is_cancelled: 用户停止任务时返回 True；会尽快收工并把未完成页留 None。
    :param on_page_error: 单页最终失败的观察钩子（用于日志/缺页补抓）。
    """
    if total_pages is None or total_pages <= 0:
        return []

    # ⚠ 只容许**首帧复用**（first_page）跳过重试：其余任何情况「重试次数必须 >= 1」。
    #   把一个 0 当成「不试」，等于让一次网络抖动直接变成缺页。
    retries = max(1, int(retries or 1))

    results: List[Any] = [None] * total_pages
    done_lock = threading.Lock()
    done_count = 0

    start_page = 1
    if first_page is not None:
        results[0] = first_page
        done_count = 1
        if on_progress:
            _safe_call(on_progress, 1)
        start_page = 2

    if start_page > total_pages:
        return results

    def _worker(page: int) -> None:
        nonlocal done_count

        def _note_error(exc: BaseException) -> None:
            if on_page_error:
                _safe_call(on_page_error, page, exc)

        try:
            value = _fetch_one(fetch_page, page, retries, is_cancelled, _note_error)
        except PermissionDenied as exc:
            # 权限类不重试（下架/未登录），但不炸整批
            _note_error(exc)
            value = None
        except BaseException as exc:  # noqa: BLE001 - 任何单页异常都不该毁掉整本
            _note_error(exc)
            value = None

        # ⚠ 写入**本页自己的槽位**（下标 = 页号 - 1），与完成顺序无关。
        #   这就是「重排」的全部秘密：槽位即页号，天然对齐。
        results[page - 1] = value
        if value is not None:
            with done_lock:
                done_count += 1
                current = done_count
            if on_progress:
                _safe_call(on_progress, current)

    # 用滚动窗口提交，而不是一次性 submit 全部 —— 3000 集的专辑会产生上百个
    # Future 对象，而 ThreadPoolExecutor 的队列是无限的，提前提交等于把
    # 「并发限制」变成「排队限制」，对风控平台没有意义。
    pending = {}
    next_page = start_page
    window = max(1, concurrency)

    with ThreadPoolExecutor(max_workers=window, thread_name_prefix="pagefetch") as pool:
        while next_page <= total_pages and len(pending) < window:
            pending[pool.submit(_worker, next_page)] = next_page
            next_page += 1

        while pending:
            if is_cancelled and is_cancelled():
                for fut in pending:
                    fut.cancel()
                break
            done, _ = wait(tuple(pending), timeout=0.2, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for fut in done:
                pending.pop(fut, None)
                # _worker 内部已吞掉所有异常，这里只防御性读一次结果
                try:
                    fut.result()
                except BaseException:  # noqa: BLE001
                    pass
            # 补位：保持窗口满载
            while next_page <= total_pages and len(pending) < window:
                if is_cancelled and is_cancelled():
                    break
                pending[pool.submit(_worker, next_page)] = next_page
                next_page += 1

    return results


def _fetch_one(
    fetch_page: Callable[[int], Any],
    page: int,
    retries: int,
    is_cancelled: Optional[Callable[[], bool]],
    on_error: Optional[Callable[[BaseException], None]] = None,
) -> Any:
    """单页抓取 + 指数退避重试。全失败返回 None（不抛，交给调用方决定缺页策略）。

    ⚠ `on_error` 是**本函数内部**的失败通知点：异常在这里就被吞掉了，
    外层 `_worker` 的 `except` 永远看不到它 —— 把观察钩子放在外层等于永远不触发
    （这是实现时踩过一次的坑，`test_page_error_hook_receives_page_number` 钉着它）。
    """
    attempts = max(1, int(retries or 1))
    last_error: Optional[BaseException] = None

    for attempt in range(attempts):
        if is_cancelled and is_cancelled():
            return None
        try:
            return fetch_page(page)
        except PermissionDenied:
            raise                      # 权限类不重试，交给上层落成 None
        except BaseException as exc:   # noqa: BLE001
            last_error = exc
        if attempt < attempts - 1:
            delay = _BACKOFF_BASE * (2 ** attempt)
            deadline = time.time() + delay
            while time.time() < deadline:
                if is_cancelled and is_cancelled():
                    return None
                time.sleep(min(0.1, max(0.0, deadline - time.time())))
    if on_error and last_error is not None:
        on_error(last_error)
    return None


def flatten(pages: Sequence[Any], select: Optional[Callable[[Any], Optional[Iterable]]] = None) -> list:
    """把「按页号对齐、可能含 None 空洞」的页数组摊平成一条列表。

    None 页（彻底失败的那页）直接跳过 —— 缺页是「少几条」而不是「错位」，
    因为槽位就是页号。

    ⚠ 参考实现的 `Flatten` 有两个泛型参数（TPage / TItem），因为各平台的页结果
    类型不统一（有的是元组）。Python 不需要显式泛型，但 `select` 必须能处理
    **该平台实际的页类型**：页是 `(chapters, total)` 元组时传
    `lambda p: p[0]`，页本身就是 list 时传 None。

    ⚠ 但 **`bool` 与 `dict` 的坑要防**：页类型是 `dict` 时 `p is None` 之外的
    空字典要保留（不能当空洞跳过），这里只跳 `None`。
    """
    out: list = []
    if not pages:
        return out
    for page in pages:
        if page is None:
            continue
        if select is None:
            if isinstance(page, (list, tuple)):
                out.extend(page)
            else:
                out.append(page)
            continue
        seq = select(page)
        if seq is None:
            continue
        out.extend(seq)
    return out


def missing_pages(pages: Sequence[Any]) -> List[int]:
    """返回空洞页号（1 起），供调用方决定是否需要补抓一轮。"""
    return [idx + 1 for idx, page in enumerate(pages or []) if page is None]


def dedupe_by_key(items: Iterable[TItem], key: Callable[[TItem], Any]) -> List[TItem]:
    """按 key 去重并**保持首次出现的顺序**。

    并发分页最常见的副作用是跨页重复（服务端会话/缓存导致），参考实现
    `PageFetch.cs:28-33` 记录过酷我的「跨页 rid 重复、页内集号倒序」现象。
    去重必须在**页码重排之后**做，否则会保留到错误的那一份。
    """
    seen = set()
    out: List[TItem] = []
    for item in items:
        try:
            marker = key(item)
        except Exception:  # noqa: BLE001 - 取不到 key 的条目原样保留
            out.append(item)
            continue
        if marker in (None, ""):
            out.append(item)
            continue
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def _safe_call(fn: Callable, *args) -> None:
    """回调绝不能把下载/抓取拖垮。"""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "DEFAULT_CONCURRENCY",
    "GENTLE_CONCURRENCY",
    "FAST_CONCURRENCY",
    "DEFAULT_PAGE_RETRIES",
    "page_count",
    "fetch_all",
    "flatten",
    "missing_pages",
    "dedupe_by_key",
]
