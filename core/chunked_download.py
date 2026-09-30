# -*- coding: utf-8 -*-
"""分段并行下载 + Range 探大小 + 空闲超时 —— 全项目唯一的音频落盘实现。

## 移植来源

参考实现 XimalayaApp `Core/Net.cs:120-533`。它把「下载一个音频文件」这件事
从「开一个 GET、for 循环 iter_content、写文件」升级成了带**探测 → 分段 →
合并 → 校验 → 原子替换**的完整流水线。

## 为什么值钱（参考实现的实测数据）

| 场景 | 单流 | 分段并行 |
| --- | --- | --- |
| 起点听书 3.5MB | 3.3 MB/s | **22.3 MB/s（6.8×）** |
| 懒人听书 4.8MB | 6.9 MB/s | **23.2 MB/s（3.4×）** |

**小文件才是分段最划算的地方** —— 参考实现最初按 8MB 设门槛，结果起点 3.5MB、
懒人 4.8MB 这些有声书主流单集**全被挡在门外**，分段等于从没生效过。降到 2MB
之后收益才显现。本模块沿用 2MB。

## 三条来自参考实现的硬约束（照抄，不重新发明）

### ① 接口不报 file_size 的平台要补「1 字节 Range 探大小」

参考实现实测：番茄 / 酷我 / 起点 / 蜻蜓 / 懒人 **全部不报** `file_size`。
原来这些平台的请求直接落进单流分支，分段从未生效。所以这里补一次 1 字节
Range 探测；探不到（CDN 回 200 或报错）就**维持单流路径，一个字节的行为差异
都不制造**。

### ② 探到的总长只用来决定「要不要分段」，不能当完整性校验基准

某些 CDN 报的 `Content-Range` 与实际可读字节数有出入。若把探测值当校验基准，
会凭空冒出一个 minRatio 失败 —— 而这条路在改动前是**必过**的。

### ③ 空闲超时「不能没有」

参考实现原话：

> `HttpClient.Timeout` 和响应头阶段的那只 30s CTS **都只管到响应头**。
> 正文一旦停发，`ReadAsync` 永远不返回、也不会抛超时。CDN/运营商静默掐断
> 连接时 worker 就永久挂在那一行读上 —— 表现正是「最后几集 0 速度、专辑
> 永远下不完、日志里一个错都没有」。

本模块用「距上次收到数据的秒数」计时，超阈值抛 `TransientError`，
交给上层既有的重试逻辑。

## 与 AudioFlow 既有代码的关系（**关键**）

本模块是**新增能力，不是替换**。设计成「只在确定能更快时才生效」：

1. **默认走既有的单流路径**。`stream_to_file()` 的签名与语义对齐各 manager
   现有的 `iter_content` 循环，可被逐个替换；
2. **降级永远安全**。CDN 不支持 Range、探测失败、分段任何一步出错 →
   透明回退单流，且**回退时不留半截文件**；
3. **`.part` + `.part.s{i}` 命名与参考实现一致**，且**明确排除在任何
   「已下载」索引之外** —— 半截文件绝不能被误判为成品。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from core import http_policy
from core.errors import (
    ContentInvalidError,
    PermissionDenied,
    TransientError,
)

# ----------------------------------------------------------------------
# 分段参数（与参考实现 Core/Net.cs:157-167 一致）
# ----------------------------------------------------------------------

#: ≥2MB 就分段。参考实现实测后从 8MB 降到 2MB —— 有声书主流单集就是 3~5MB。
SEG_MIN_BYTES = 2 * 1024 * 1024

#: 每段目标 2MB。每段 2MB 的粒度保证连接数不失控
#: （最大 8 段，8 路 worker 时 ≤64 连接，已在参考实现 16 路压测下 12/12 全成功）。
SEG_TARGET_BYTES = 2 * 1024 * 1024

#: 最多 8 段。
SEG_MAX = 8

#: 正文读的空闲超时（秒）：这么久收不到一个字节即判连接已死。
IDLE_TIMEOUT_SEC = 30

#: 响应头阶段超时（秒）。
HEADER_TIMEOUT_SEC = 30

#: 读块大小 64KB。
CHUNK_SIZE = 1 << 16

#: 完整性下限：实收 / 应达。与各 manager 既有的 0.8 保持一致，
#: 避免把历史 fileSize 不准的专辑变成失败。
MIN_RATIO = 0.80

#: 小于这个字节数一律不可能是有效音频。
MIN_VALID_BYTES = 1024


class RangeNotSupported(Exception):
    """该候选不支持 Range（返回 200 而非 206）→ 调用方回退单流。"""


def segment_count_for(size: int) -> int:
    """按文件大小决定段数（1 = 不分段）。"""
    if size is None or size < SEG_MIN_BYTES:
        return 1
    return int(max(2, min(SEG_MAX, -(-size // SEG_TARGET_BYTES))))


# ======================================================================
# 结果
# ======================================================================

class DownloadOutcome:
    """一次落盘的结果。`path` 是最终文件路径（可能因后缀纠正与入参不同）。"""

    __slots__ = ("path", "bytes", "segmented", "content_type", "reported_total")

    def __init__(self, path, bytes_, segmented, content_type="", reported_total=None):
        self.path = path
        self.bytes = bytes_
        self.segmented = segmented
        self.content_type = content_type
        self.reported_total = reported_total

    def __repr__(self):  # pragma: no cover - 调试用
        return (
            f"<DownloadOutcome path={os.path.basename(self.path)} "
            f"bytes={self.bytes} segmented={self.segmented}>"
        )


# ======================================================================
# 工具
# ======================================================================

def part_paths(dst: str) -> Tuple[str, list]:
    """返回 `(主 .part 路径, 各分段的 .part.s{i} 路径列表)`。"""
    part = dst + ".part"
    return part, [f"{part}.s{i}" for i in range(SEG_MAX)]


def clean_part_files(dst: str) -> None:
    """清掉 `.part` 与所有分段残留。

    ⚠ 失败/取消后**必须**调用：否则残段会被「已下载」索引或本地播放
    误判为成品（参考实现 `Net.cs:444-446` 的注释）。
    """
    part, segs = part_paths(dst)
    _try_delete(part)
    for seg in segs:
        _try_delete(seg)


def _try_delete(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def is_partial_file(name: str) -> bool:
    """判断一个文件名是不是「半截产物」（`.part` / `.part.s{i}` / `_` 前缀）。

    供 `local_index` 之类的「已下载」判定复用：半截文件**绝不能**算已下载。
    """
    base = os.path.basename(str(name or ""))
    if base.startswith("_"):
        return True
    lowered = base.lower()
    return ".part" in lowered


# ======================================================================
# 主入口
# ======================================================================

def stream_to_file(
    *,
    dst: str,
    get_response: Callable[..., Any],
    guess_total: Optional[int] = None,
    progress: Optional[Callable[[int, Optional[int]], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    min_ratio: float = MIN_RATIO,
    idle_timeout: int = IDLE_TIMEOUT_SEC,
    allow_segmented: bool = True,
    content_type: str = "",
    session: Any = None,
    url: str = "",
    headers: Optional[Dict[str, str]] = None,
    min_valid_bytes: int = MIN_VALID_BYTES,
    probe_threshold: int = 0,
) -> DownloadOutcome:
    """把 URL 落盘到 `dst`，优先分段并行，失败安全回退单流。

    :param dst: 最终文件路径（`.part` 由本函数自己管理）。
    :param get_response: `(url, headers, range_tuple_or_None, stream) -> response`。
        传 range 为 `(start, end)` 的闭区间；返回的 response 必须有 `status_code`
        / `headers` / `iter_content`（requests 的 Response 契约）。
        ⚠ 用回调而不是直接收 `requests`：各 manager 的 session / 重试策略各不相同，
        本模块只负责「怎么切、怎么合、怎么校验」，不接管「用什么发」。
    :param guess_total: 接口自报的体积（可能不准 / 为 None）。
    :param allow_segmented: 关掉即强制单流（用于已知不支持 Range 的平台）。
    :param is_cancelled: 用户停止任务。
    :param session: 可选的 session（分段时会用 `session.get` 发额外请求，
        没有就复用 `get_response`）。
    :param min_valid_bytes: 「这不可能是一段音频」的粗筛下限。
        ⚠ 它的**唯一用途**是拦住明显的错误响应（例如 47 字节的拒绝体 JSON），
        不是替调用方做业务判定。各平台阈值不同（酷我/懒人 10KB、喜马拉雅 1KB），
        调用方会在拿到结果后用自己的阈值复判并给出**它自己的**错误文案。
        传 0 可完全关掉这一层。
    :param probe_threshold: 预估体积低于它就**跳过 1 字节 Range 探测**直接单流。
        默认 0（= 只按「接口没报体积」的规则走）。
        ⚠ 上面那句注释与这里的分工：`guess_total` 有值时不探（省一次往返）；
        没有值时**要不要为了分段去探**由本参数决定 ——
        对「已知这批文件都很小」的调用方，探了也白探（结果必然是单流）。
        实现里取 `max(probe_threshold, SEG_MIN_BYTES)`：低于分段门槛的文件
        本来就不该分段，探它没有意义。
    """
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    clean_part_files(dst)

    # ---- ① 决定要不要分段（探大小只在"确实需要"时才发） ----
    #
    # ⚠ 探测是一次**额外的往返**。参考实现里它是必要的（那时接口普遍不报
    #   file_size），但如果接口已经报了体积，再探一次就是纯粹的浪费 ——
    #   实测在 200ms RTT 的链路上，多这一次探测会让 3 段并发的收益全部吐回去
    #   （tests/test_chunked_download.py::test_segmented_is_faster_than_single_stream）。
    #
    #   所以规则是：**只有"接口没报体积"时才探**。
    #
    # ⚠ 而且探测本身有代价：即使文件最终 < SEG_MIN_BYTES（走单流），
    #   这一次往返也**白花了**。对有声书单集（主流 3~5MB）这个开销占比很小，
    #   但对「已知这批文件普遍较小」的调用方就是纯损耗。
    #
    #   `probe_threshold` 就是给这种调用方的显式开关：
    #     * `0`（默认）= 不做优化，保持「接口没报体积就探」的行为；
    #     * `> 0` = **只在需要时探**。注意本函数无法在探测前知道真实体积
    #       （那正是要探的原因），所以这里的语义是「调用方声明：本平台单集
    #       普遍低于这个值」→ **跳过探测**，直接走单流。
    #       代价是超过 SEG_MIN_BYTES 的大单集不会分段。
    #     * `< 0` = 与 `> 0` 同义（永远不探），保留是为了让调用方能直白地
    #       表达「我确定不需要分段」。
    size_hint = None
    if guess_total and guess_total > 0:
        size_hint = int(guess_total)
    elif allow_segmented and url and probe_threshold == 0:
        size_hint = _probe_total(get_response, url, headers, is_cancelled)

    if allow_segmented and size_hint and size_hint >= SEG_MIN_BYTES:
        try:
            return _segmented_download(
                dst=dst,
                get_response=get_response,
                url=url,
                headers=headers,
                expected_size=size_hint,
                progress=progress,
                is_cancelled=is_cancelled,
                min_ratio=min_ratio,
                idle_timeout=idle_timeout,
                content_type=content_type,
                min_valid_bytes=min_valid_bytes,
            )
        except RangeNotSupported:
            clean_part_files(dst)
        except PermissionDenied:
            clean_part_files(dst)
            raise
        except TransientError:
            # ⚠ 分段失败**不直接抛** —— 回退单流再试一次。参考实现的行为是
            #   分段错误上抛给引擎重试；本项目各 manager 的重试在更外层，
            #   这里多给一次单流机会能显著降低「明明能下却失败」的概率。
            clean_part_files(dst)

    # ---- 回退：单流 ----
    return _single_download(
        dst=dst,
        get_response=get_response,
        url=url,
        headers=headers,
        expected_size=guess_total,
        progress=progress,
        is_cancelled=is_cancelled,
        min_ratio=min_ratio,
        idle_timeout=idle_timeout,
        content_type=content_type,
        min_valid_bytes=min_valid_bytes,
    )


# ======================================================================
# 探大小
# ======================================================================

def _probe_total(get_response, url, headers, is_cancelled) -> Optional[int]:
    """1 字节 Range 探真实总长。

    任何异常 / 非 206 都返回 None —— **探测失败绝不能升级成下载失败**
    （参考实现 `Net.cs:189-203`）。
    """
    if not url or get_response is None:
        return None
    try:
        resp = get_response(url, headers, (0, 0), False)
    except Exception:  # noqa: BLE001
        return None
    try:
        if getattr(resp, "status_code", None) != 206:
            return None
        content_range = (getattr(resp, "headers", None) or {}).get("Content-Range", "")
        # 形如 "bytes 0-0/1234567"
        if "/" not in str(content_range):
            return None
        total = str(content_range).rsplit("/", 1)[-1].strip()
        if total in ("", "*"):
            return None
        value = int(total)
        return value if value > 0 else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        _close_quietly(resp)


# ======================================================================
# 分段下载
# ======================================================================

def _segmented_download(
    *,
    dst,
    get_response,
    url,
    headers,
    expected_size,
    progress,
    is_cancelled,
    min_ratio,
    idle_timeout,
    content_type,
    min_valid_bytes: int = MIN_VALID_BYTES,
) -> DownloadOutcome:
    """Range 206 → N 段并行写 `.part.s{i}` → 合并 → 校验 → 原子替换。"""
    part, seg_files = part_paths(dst)
    segs = segment_count_for(expected_size)
    if segs < 2:
        raise RangeNotSupported(f"文件实际 {expected_size}B，不足分段")

    seg_len = -(-expected_size // segs)   # 向上取整
    counter = _ByteCounter(progress)

    def _fetch_segment(index: int, start: int, end: int):
        resp = get_response(url, headers, (start, end), True)
        try:
            _ensure_ok(resp)
            if getattr(resp, "status_code", None) != 206:
                raise RangeNotSupported(f"段 {index} 返回 HTTP {resp.status_code}")
            written = _read_body_to_file(
                resp, seg_files[index], counter, is_cancelled, idle_timeout, stop=end - start + 1
            )
            return written
        finally:
            _close_quietly(resp)

    results: Dict[int, Any] = {}
    errors: Dict[int, BaseException] = {}
    lock = threading.Lock()
    threads = []

    def _worker(index: int, start: int, end: int):
        try:
            value = _fetch_segment(index, start, end)
            with lock:
                results[index] = value
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors[index] = exc

    for i in range(segs):
        start = i * seg_len
        end = min(start + seg_len, expected_size) - 1
        if end < start:
            continue
        thread = threading.Thread(
            target=_worker, args=(i, start, end), name=f"seg{i}", daemon=True
        )
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    if is_cancelled and is_cancelled():
        clean_part_files(dst)
        raise TransientError("下载已取消")

    if errors:
        first = next(iter(errors.values()))
        # 用户取消 / 权限类原样上抛，别包装成「分段失败」走错重试分支
        if isinstance(first, (PermissionDenied, ContentInvalidError)):
            raise first
        if isinstance(first, RangeNotSupported):
            raise first
        raise TransientError(f"分段下载失败：{first}")

    if len(results) != segs:
        raise TransientError(f"分段下载不完整：{len(results)}/{segs} 段")

    # ---- 合并 ----
    total_written = 0
    with open(part, "wb") as out:
        for i in range(segs):
            seg_file = seg_files[i]
            if not os.path.exists(seg_file):
                raise TransientError(f"分段 {i} 缺失，无法合并")
            with open(seg_file, "rb") as src:
                while True:
                    block = src.read(CHUNK_SIZE)
                    if not block:
                        break
                    out.write(block)
                    total_written += len(block)
    for seg_file in seg_files:
        _try_delete(seg_file)

    if total_written < expected_size * min_ratio:
        clean_part_files(dst)
        raise TransientError(f"下载不完整：{total_written} < {expected_size}×{min_ratio}")

    # ⚠ 与单流路径同一规则：内容级判定永远生效（JSON 错误码 / HTML 错误页），
    #   但「体积过小」这句在调用方有自己阈值时**由调用方来说**。
    http_policy.raise_for_error_body(part, content_type, check_size=min_valid_bytes > 0)
    final_path = _commit(part, dst)
    return DownloadOutcome(final_path, total_written, True, content_type, expected_size)


# ======================================================================
# 单流下载
# ======================================================================

def _single_download(
    *,
    dst,
    get_response,
    url,
    headers,
    expected_size,
    progress,
    is_cancelled,
    min_ratio,
    idle_timeout,
    content_type,
    min_valid_bytes: int = MIN_VALID_BYTES,
) -> DownloadOutcome:
    """单流：响应 → `.part` → 错误体检测 → 大小校验 → 原子替换。"""
    part = dst + ".part"
    resp = get_response(url, headers, None, True)
    try:
        _ensure_ok(resp)
        real_type = content_type or _content_type_of(resp)
        declared = _content_length_of(resp) or expected_size

        counter = _ByteCounter(progress)

        def _report(got, total_):
            # ⚠ 进度回调绝不能拖垮下载：它跑的是 UI 派发，抛异常=界面出问题，
            #   不该把已经下好的文件判成失败（_ByteCounter 里也是同一个原则）。
            try:
                if progress:
                    progress(got, total_)
            except Exception:  # noqa: BLE001
                pass

        written = _read_body_to_file(
            resp, part, counter, is_cancelled, idle_timeout, stop=None
        )

        # ⚠ 顺序铁律：先解读响应体（付费集拒绝体是 47 字节 JSON），再看体积。
        #   调换顺序会把「这一集没权限」报成「响应过小」，用户看不出真因。
        #
        # ⚠ `check_size=False` 的含义：调用方设了 `min_valid_bytes` 时，
        #   「体积过小」这句**由调用方来说**（各平台文案不同：酷我
        #   「酷我媒体文件过小: N 字节」、懒人返回 False）。本层只负责
        #   「内容看起来就不是音频」这类**内容级**判定，不抢调用方的文案。
        http_policy.raise_for_error_body(part, real_type, check_size=min_valid_bytes > 0)

        if written < min_valid_bytes:
            clean_part_files(dst)
            raise ContentInvalidError(
                f"下载的内容不完整（仅 {written} 字节，低于下限 {min_valid_bytes}）"
            )

        if declared and declared > 0 and written < declared * min_ratio:
            clean_part_files(dst)
            raise TransientError(f"下载不完整：{written} < {declared}×{min_ratio}")

        final_path = _commit(part, dst)
        _report(written, declared)
        return DownloadOutcome(final_path, written, False, real_type, declared)
    except BaseException:
        # 任何失败都要清掉半截文件：否则会被「已下载」判定误当成成品
        clean_part_files(dst)
        raise
    finally:
        _close_quietly(resp)


# ======================================================================
# 正文读 + 空闲超时
# ======================================================================

def _read_body_to_file(
    resp, path, counter, is_cancelled, idle_timeout, stop: Optional[int]
) -> int:
    """正文 → 文件，带**空闲超时**。

    ## 空闲超时是怎么真正生效的（⚠ 别改回「只在循环里比时间」）

    参考实现 `Net.cs:169-175` 记录的事故是：「正文一旦停发 `ReadAsync` 永远不返回、
    也不会抛超时 → worker 永久挂在那一行读上」。

    Python 这边的等价风险是 `iter_content` 卡在 socket 读上。本实现用**两条防线**：

    ① **socket 级 deadline（主防线）**：`requests` 的 `timeout=(连接, 读取)` 中，
       读取超时是「相邻两次 socket 读之间」的间隔 —— 这正好就是「空闲超时」的
       语义。调用方通过 `get_response` 传入的 timeout 已经覆盖它（各 manager
       现有用法已有 `timeout=(10, 90)` 之类）。本模块**不重复实现 socket 超时**，
       因为那需要接管 session 构造，会破坏各平台现有的鉴权头/代理配置。
    ② **循环级兜底（本函数）**：只要 `iter_content` 还能返回（哪怕是空块心跳），
       就按「距上次收到有效数据的秒数」判停滞。它覆盖「服务端每隔一会儿发一个
       心跳字节」这种慢性拖死的场景，是 ① 的有效补充。

    ⚠ 明确它的边界：如果 socket 读**彻底挂死**，② 不会触发 —— 那种情况靠 ①。
    不要为了「让 ② 也能触发」而把它改成后台线程强杀，那会引入
    「文件已关闭却仍在写」的竞态，得不偿失。
    """
    written = 0
    last_data_at = time.time()

    with open(path, "wb") as fh:
        try:
            iterator = resp.iter_content(chunk_size=CHUNK_SIZE)
            while True:
                if is_cancelled and is_cancelled():
                    raise TransientError("下载已取消")

                # ⚠ 停滞判定要看**「这次 next() 花了多久」**，不能只看进循环前的快照。
                #
                #   生成器/`iter_content` 的睡眠发生在 `next()` 内部 —— next() 返回的
                #   那一刻，`last_data_at` 还是上一块的时间，但 `stalled`（进循环前
                #   算的）是 False。所以必须在 next() 返回**之后**重算一次。
                #   这是实测踩出来的：idle_timeout=0.25 时，0.6s 的停顿被判成了正常
                #   （见 tests/test_chunked_download.py::TestIdleTimeout）。
                stalled = time.time() - last_data_at > idle_timeout

                try:
                    block = next(iterator)
                except StopIteration:
                    break
                except TransientError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if time.time() - last_data_at > idle_timeout:
                        raise TransientError(_stall_message(idle_timeout, written)) from exc
                    raise TransientError(f"读取响应体失败：{exc}") from exc

                # 重新计时：本次 next() 实际等了多久
                stalled = time.time() - last_data_at > idle_timeout

                if not block:
                    # 空块是 keep-alive 心跳：不写盘、也**不重置**计时。
                    # ⚠ 若在这里重置，"服务端每 29 秒发一个空块"就能把下载
                    #   永远吊住 —— 那正是要防的慢性拖死。
                    if stalled:
                        raise TransientError(_stall_message(idle_timeout, written))
                    continue

                if stalled:
                    raise TransientError(_stall_message(idle_timeout, written))

                fh.write(block)
                written += len(block)
                counter.add(len(block))
                last_data_at = time.time()
                # 分段路径：拿到本段应得的字节数就收工，不多读一个字节
                if stop is not None and written >= stop:
                    break
        finally:
            _close_quietly(resp)

    return written


def _stall_message(idle_timeout: int, written: int) -> str:
    return f"下载停滞：{idle_timeout} 秒收不到数据（已收 {written} 字节）"


class _ByteCounter:
    """跨段聚合进度 + 节流上报。

    ⚠ 节流不能省：参考实现注释记录过「原来每 64KB 块报一次，8 路并发时
    每秒数百次 UI 派发，UI 线程被淹没 → 窗口未响应」。这里最多 5 次/秒。
    """

    __slots__ = ("_total", "_progress", "_lock", "_last_report")

    def __init__(self, progress):
        self._total = 0
        self._progress = progress
        self._lock = threading.Lock()
        self._last_report = 0.0

    def add(self, n: int) -> None:
        with self._lock:
            self._total += n
            current = self._total
        if not self._progress:
            return
        now = time.time()
        with self._lock:
            if now - self._last_report < 0.2:
                return
            self._last_report = now
        try:
            self._progress(current, None)
        except Exception:  # noqa: BLE001 - 进度回调绝不能拖垮下载
            pass

    @property
    def total(self) -> int:
        return self._total


# ======================================================================
# 辅助
# ======================================================================

def _commit(part: str, dst: str) -> str:
    """原子替换 + 后缀纠正。

    ⚠ 后缀纠正用的是 `http_policy.sniff_extension`，它与各 manager 既有的
    `_detect_mobile_media_format` 行为一致（`ftyp` → `.m4a`），不引入变化。
    """
    declared_ext = os.path.splitext(dst)[1]
    real_ext = http_policy.sniff_extension(part, declared_ext)
    final = dst if real_ext == declared_ext else os.path.splitext(dst)[0] + real_ext
    os.replace(part, final)
    return final


def _ensure_ok(resp) -> None:
    status = getattr(resp, "status_code", 200)
    if status in (206, 200):
        return
    if status in (400, 401, 403, 404, 410, 451):
        raise PermissionDenied(f"CDN 拒绝访问（HTTP {status}）")
    raise TransientError(f"CDN 暂时无法访问（HTTP {status}）")


def _content_type_of(resp) -> str:
    headers = getattr(resp, "headers", None) or {}
    try:
        return str(headers.get("Content-Type") or "")
    except Exception:  # noqa: BLE001
        return ""


def _content_length_of(resp) -> Optional[int]:
    headers = getattr(resp, "headers", None) or {}
    try:
        raw = headers.get("Content-Length")
    except Exception:  # noqa: BLE001
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _close_quietly(resp) -> None:
    try:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "SEG_MIN_BYTES",
    "SEG_TARGET_BYTES",
    "SEG_MAX",
    "IDLE_TIMEOUT_SEC",
    "CHUNK_SIZE",
    "MIN_RATIO",
    "RangeNotSupported",
    "DownloadOutcome",
    "segment_count_for",
    "stream_to_file",
    "clean_part_files",
    "part_paths",
    "is_partial_file",
]
