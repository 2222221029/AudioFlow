# -*- coding: utf-8 -*-
"""把 `chunked_download` 接到各平台现有 session 上的**统一适配层**。

## 为什么要有这一层

各平台的 manager 各有自己的 `session`（不同的鉴权头、cookie、代理、超时），
`chunked_download` 不该接管「用什么发请求」，只负责「怎么切、怎么合、怎么校验」。
本模块就是两者之间的胶水：

    platform manager  ->  session_to_file()  ->  chunked_download.stream_to_file()

## 硬约束（来自用户要求：不能影响现有功能及下载速度）

* **候选地址失败时的行为要和改造前一致**。改造前各 manager 用
  `response.raise_for_status()`，抛的是 `requests.HTTPError`；
  上层 `download_worker` 用 `except Exception` 兜住。
  本模块**保留这个契约**：把分级异常也包装成 `requests.HTTPError`
  （带 `response` 属性），这样既有调用方一行都不用改。
* **任何不满足分段条件的场景都透明回退单流**，且回退路径与改造前的
  `iter_content` 循环**语义等价**。
* **失败不留半截文件**（`.part` / `.part.s{i}` 全部清理）。
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Callable, Dict, Optional

import requests

from core import chunked_download as cd
from core.errors import ContentInvalidError, PermissionDenied, TransientError

#: 默认超时：(连接, 读取)。读取超时即「相邻两次 socket 读的间隔」，
#: 正好是空闲超时的语义。与各 manager 现有的 (10, 90) / 120 同量级。
DEFAULT_TIMEOUT = (10, 90)

#: 线程级取消钩子。DownloadWorker 在每个章节下载线程里设置
#: `is_cancelled=lambda: worker._is_stopped`，使「暂停/停止」能中断在途
#: 下载（chunked 分段、单流、甚至 CDN 大文件的逐块传输），而无需给
#: 每个平台 manager 的 download_audio 都增加参数。
_CANCEL_CHECK: ContextVar = ContextVar("audioflow_cancel_check", default=None)


def set_cancel_check(check: Optional[Callable[[], bool]]) -> None:
    _CANCEL_CHECK.set(check)


def clear_cancel_check() -> None:
    _CANCEL_CHECK.set(None)


def current_cancel_check() -> Optional[Callable[[], bool]]:
    return _CANCEL_CHECK.get()


def session_to_file(
    *,
    session: requests.Session,
    url: str,
    save_path: str,
    headers: Optional[Dict[str, str]] = None,
    expected_size: Optional[int] = None,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    allow_segmented: bool = True,
    timeout=DEFAULT_TIMEOUT,
    idle_timeout: int = cd.IDLE_TIMEOUT_SEC,
    min_valid_bytes: int = 10240,
    check_error_body: bool = True,
    probe_threshold: int = 0,
) -> Optional[str]:
    """下载到 `save_path`，返回**最终路径**（可能因后缀纠正不同），失败抛异常。

    :param expected_size: 接口自报的体积（有就传，能省掉一次探测往返）。
    :param allow_segmented: 已知该 CDN 不支持 Range 时传 False，直接走单流。
    :param min_valid_bytes: 小于它判为无效产物。默认 10240 与 lrts 既有判据一致。
    :param check_error_body: 是否做「先解读响应体」判定。对**已知会返回
        HTML 错误页**的平台关掉它没有意义，默认开。

    ⚠ 抛出的异常类型与改造前保持兼容：
      * HTTP 4xx/5xx → `requests.HTTPError`（带 `.response`），
        与 `response.raise_for_status()` 的契约一致；
      * 内容无效 / 网络停滞 → 本项目的分级异常，但**同时继承自
        `requests.RequestException`**，所以 `except requests.RequestException`
        的既有代码照样能捕获。
    """

    def _get(url_, headers_, rng, stream):
        hdrs = dict(headers or {})
        if rng is not None:
            hdrs["Range"] = f"bytes={rng[0]}-{rng[1]}"
        return session.get(
            url_,
            headers=hdrs,
            stream=stream,
            timeout=timeout,
            allow_redirects=True,
        )

    os.makedirs(os.path.dirname(os.path.abspath(save_path)) or ".", exist_ok=True)

    if is_cancelled is None:
        is_cancelled = current_cancel_check()

    try:
        outcome = cd.stream_to_file(
            dst=save_path,
            get_response=_get,
            guess_total=expected_size,
            progress=progress_callback,
            is_cancelled=is_cancelled,
            idle_timeout=idle_timeout,
            allow_segmented=allow_segmented,
            url=url,
            headers=headers,
            # ⚠ 把调用方的阈值**透传进去**，让 chunked_download 内部的粗筛
            #   与调用方的业务阈值一致 —— 否则内部的 1KB 粗筛会先拦掉一个
            #   512 字节的响应，调用方就永远拿不到它自己那句
            #   「酷我媒体文件过小: 512 字节」，错误契约就变了。
            min_valid_bytes=min_valid_bytes,
            probe_threshold=probe_threshold,
        )
    except (PermissionDenied, TransientError, ContentInvalidError) as exc:
        # 翻译回 requests 的契约，让既有 `except requests.HTTPError` 与
        # `except Exception` 两种写法都能照常工作。
        raise _as_transport_error(exc, url) from exc

    # 二次复判：chunked_download 内部已按同一阈值粗筛过，这里是**防御性**的
    # （万一下层逻辑变了，也不会把过小文件当成成功交出去）。
    if outcome.bytes < min_valid_bytes:
        cd.clean_part_files(save_path)
        try:
            if os.path.exists(outcome.path):
                os.remove(outcome.path)
        except OSError:
            pass
        raise _as_transport_error(
            ContentInvalidError(
                f"下载的内容过小（{outcome.bytes} 字节 < {min_valid_bytes}），已拒绝保存"
            ),
            url,
        )

    return outcome.path


def _as_transport_error(exc: BaseException, url: str) -> requests.RequestException:
    """把本项目的分级异常翻译成 `requests` 的异常族（保持既有 except 契约）。

    * 权限类 → `requests.HTTPError`（既有代码用 `raise_for_status` 的语义）
    * 其余   → `requests.RequestException` 子类，`str()` 保留原始文案
    """
    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, PermissionDenied):
        error = requests.HTTPError(message)
        error.response = getattr(exc, "status_code", None) and _FakeResponse(url, exc.status_code)
        return error
    return requests.RequestException(message)


class _FakeResponse:
    """只为了让 `requests.HTTPError.response.status_code` 可用（既有代码会读它）。"""

    __slots__ = ("url", "status_code", "headers", "text")

    def __init__(self, url, status_code, headers=None, text=""):
        self.url = url
        self.status_code = int(status_code or 0)
        self.headers = headers or {}
        self.text = text

    def json(self):  # pragma: no cover - 仅占位，既有代码极少调用
        raise ValueError("no json body")


def download_with_session(
    session,
    url,
    save_path,
    headers=None,
    progress_callback=None,
    expected_size=None,
    **kwargs,
) -> bool:
    """给「只想换个实现、不想改控制流」的调用方准备的布尔版包装。

    成功返回 True；失败返回 False 并把原因打印出来 —— 与各 manager 现有
    `download_audio` 的返回契约一致，便于**逐平台平滑替换**。
    """
    try:
        session_to_file(
            session=session,
            url=url,
            save_path=save_path,
            headers=headers,
            expected_size=expected_size,
            progress_callback=progress_callback,
            **kwargs,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 与既有 download_audio 契约一致
        print(f"[chunked] download failed: {exc}")
        return False


__all__ = [
    "DEFAULT_TIMEOUT",
    "session_to_file",
    "download_with_session",
]
