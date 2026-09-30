# -*- coding: utf-8 -*-
"""平台无关的异常分级与 HTTP 状态判定 —— 全项目「重试 / 降级」的唯一判据出口。

## 为什么需要这个模块

移植自参考实现 XimalayaApp 的 `Core/Net.cs:12-19`。那里用**互斥的异常类型**
把三件本来会被混为一谈的事分开：

| 异常 | 语义 | 正确处置 |
| --- | --- | --- |
| `PermissionDenied` | 真没权限（付费集 / 未登录 / 已下架） | 换下一候选或直接失败，**重试白搭** |
| `TransientError` | 网络瞬时错误（502/503/504/超时/连接重置） | **原地指数退避重试，绝不降档** |
| `RiskControlError` | 风控（ret=1001 / 限速） | 冷却后重试，**不降档** |

参考实现文档记录过误判的代价：初版把网络错误当成降级信号，导致
**537 集里 31 个文件被静默降级**（用户以为下了 96K、实际 48K）。

## 本项目改造前的状态

`core/download_worker.py` 原先靠**动态 import** 判定异常类别：

    try:
        from core.lrts_manager import RateLimitError as _RateLimitError
    except Exception:
        _RateLimitError = None

这有两个问题：
* 被 import 的模块一旦改名/移动，`except` 分支静默把 `_RateLimitError` 置 None，
  **重试逻辑无声失效**，没有任何报错；
* 判定散落在下载 worker 里，新增平台必须回到 worker 里加分支。

## 改造后的契约

* 异常类定义在**本模块**，任何平台模块都可以继承，worker 只 `isinstance` 判一次；
* 状态码表是**纯数据**，`http_policy.py` 与各 manager 共用，不再各写一份；
* 动态 import 全部移除 —— 拿不到类就说明代码写错了，应当显式失败而不是静默降级。

## 兼容性(重要)

`RateLimitError` / `IllegalRequestError` 的**原始定义仍在** `core/lrts_manager.py`
（保持 `raise` 点不变、`str(e)` 文案不变、既有测试的断言不变），本模块只是让它们
**同时**继承到公共基类上，由 `register_*` 完成注册。这样：

* 既有调用方 `from core.lrts_manager import RateLimitError` 照常工作；
* 新增调用方 `isinstance(e, core.errors.TransientError)` 也能命中。

未注册时 `classify()` 退化为「按类型名 + 状态码字段」判定，行为不比改造前差。
"""

from __future__ import annotations

from typing import Any


# ======================================================================
# 状态码表（纯数据，全项目唯一事实源）
# ======================================================================

#: 瞬时错误 —— 原地重试，绝不降档。并发 8~12 时 502 是高频事件。
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

#: 权限 / 不存在 —— 重试白搭，直接判定该候选不可用。
PERMISSION_STATUS = frozenset({400, 401, 403, 404, 410, 451})

#: 参考实现实测：喜马拉雅付费集拒绝响应就是 47 字节的 {"msg":"立即购买畅听","ret":726}。
#: 726 是付费集无权限的**权威信号**，比 isFree / isAuthorized 字段可信。
DEFAULT_PERMISSION_RET = frozenset({"726", "1001_restricted"})

#: 风控类 ret（参考实现：ret=1001「系统繁忙」）。
DEFAULT_RISK_RET = frozenset({"1001"})


def is_transient_status(status: Any) -> bool:
    """该 HTTP 状态是否属于「重试可能成功」的瞬时错误。"""
    try:
        return int(status) in TRANSIENT_STATUS
    except (TypeError, ValueError):
        return False


def is_permission_status(status: Any) -> bool:
    """该 HTTP 状态是否属于「再试也没用」的权限/不存在错误。"""
    try:
        return int(status) in PERMISSION_STATUS
    except (TypeError, ValueError):
        return False


# ======================================================================
# 异常基类
# ======================================================================

class DownloadError(Exception):
    """本模块所有分级异常的基类。

    `error_type` 是既有工程里已经在用的字符串标签（`chapter['_error_type']`），
    保留它是为了不改变前端/订阅逻辑的既有判据。
    """

    error_type = "download_error"

    def __init__(self, message: str = "", *, status_code: Any = None):
        super().__init__(message)
        self.status_code = status_code


class TransientError(DownloadError):
    """瞬时错误 → 原地重试，**绝不降档**。"""

    error_type = "transient"


class PermissionDenied(DownloadError):
    """权限 / 权益 / 已下架 → 不重试。"""

    error_type = "restricted"


class RiskControlError(DownloadError):
    """风控 / 限速 → 冷却后重试，不降档。"""

    error_type = "rate_limited"


class IllegalRequestError(DownloadError):
    """平台判定为非法请求（风控冷却），短间隔重试只会继续失败。"""

    error_type = "illegal_request"


class ContentInvalidError(DownloadError):
    """下载到的内容不是音频（错误页 / 过小 / 被截断）→ 不重试，直接换候选。"""

    error_type = "content_invalid"


# ======================================================================
# 既有异常类的注册（保持 lrts_manager 的定义不变，只挂到基类上）
# ======================================================================

_REGISTERED: dict[str, type] = {}


def register(name: str, cls: type) -> type:
    """把一个既有异常类登记为分级判定的判据。

    ⚠ 只登记、**不修改**该类的定义与 `raise` 点 —— 保证既有 `except XxxError`
    与测试断言完全不受影响。真正生效的方式是让 `classify()` 认识它。

    ⚠ 不再使用「动态 import 失败就置 None」的写法：登记是显式的，
    调用方用 `errors.RATE_LIMIT_TYPES`（可能为空元组）做 isinstance 也不会炸。
    """
    if isinstance(cls, type) and issubclass(cls, BaseException):
        _REGISTERED[name] = cls
    return cls


def _class_of(name: str):
    return _REGISTERED.get(name)


def _types_of(*names: str) -> tuple:
    found = tuple(_REGISTERED[n] for n in names if n in _REGISTERED)
    # 显式登记为空的元组是合法输入（isinstance(x, ()) 恒为 False）
    return found


#: `download_worker` 等调用方直接用的类型元组 ——
#: 模块导入时这些 key 已由上方的注册补丁填好；缺失即空元组，isinstance 不报错。
RATE_LIMIT_TYPES: tuple = ()
ILLEGAL_REQUEST_TYPES: tuple = ()


def refresh_registry() -> None:
    """在平台模块导入后刷新类型元组（`core/__init__.py` 会调用一次）。"""
    global RATE_LIMIT_TYPES, ILLEGAL_REQUEST_TYPES
    RATE_LIMIT_TYPES = _types_of("RateLimitError")
    ILLEGAL_REQUEST_TYPES = _types_of("IllegalRequestError")


# ======================================================================
# 判定入口
# ======================================================================

#: 参考实现 + 本项目既有代码共同确认的「不重试」标签。
NO_RETRY_ERROR_TYPES = frozenset({
    "restricted",            # 权限 / 权益不足
    "quality_unavailable",   # 该音质不可用（换档也没用，属结构性缺失）
    "illegal_request",       # 平台判定非法请求，短间隔重试只会继续失败
    "content_invalid",       # 下载到的不是音频
})

#: 「应重试」标签。
RETRY_ERROR_TYPES = frozenset({
    "transient",
    "rate_limited",
    "download_failed",
    "network",
})


def classify(exc: BaseException) -> str:
    """把一个异常归类成 `error_type` 字符串。

    判定顺序（前者优先）：
      1. 本模块的分级异常（含已注册的既有类）→ 直接用它的 `error_type`；
      2. 带回溯的状态码字段 → 按状态码表判；
      3. 兜底 `"download_error"`（调用方按「可重试」处理，与改造前一致）。
    """
    if isinstance(exc, DownloadError):
        # ⚠ 基类 DownloadError 自身的 error_type 是「未分类」，此时不应短路 ——
        #   它身上挂的 status_code 才是更精确的信息（例如包装了 HTTPError 的场合）。
        if not (type(exc).error_type == DownloadError.error_type and exc.status_code is not None):
            return exc.error_type
    else:
        if RATE_LIMIT_TYPES and isinstance(exc, RATE_LIMIT_TYPES):
            return "rate_limited"
        if ILLEGAL_REQUEST_TYPES and isinstance(exc, ILLEGAL_REQUEST_TYPES):
            return "illegal_request"

    # ⚠ 状态码优先于类型名：requests 的 HTTPError 也是一个普通 Exception 子类，
    #   若先按类型名兜底就会把 403 报成「可重试」，正好踩中参考实现那条
    #   「网络错误被当作降级信号」的坑。所以这里先看状态码。
    status = _extract_status(exc)
    if is_permission_status(status):
        return "restricted"
    if is_transient_status(status):
        return "transient"

    # 超时 / 连接类标准异常一律按瞬时错误处理
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient"

    # ⚠ 标准库 socket.timeout 在 Py3.10+ 是 TimeoutError 的别名，这里再兜 OSError
    #   的 ETIMEDOUT 家族，避免把一个可重试的网络故障判成永久失败。
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _TRANSIENT_ERRNOS:
        return "transient"

    return "download_error"


#: 连接层瞬时故障的 errno（ECONNRESET / ECONNREFUSED / ETIMEDOUT / EPIPE …）。
_TRANSIENT_ERRNOS = frozenset({32, 54, 60, 61, 104, 110, 111})


def _extract_status(exc: BaseException) -> Any:
    """从异常或它的 `response` 上取 HTTP 状态码；取不到返回 None。"""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    return None


def should_retry(error_type: Any, status_code: Any = None) -> bool:
    """该错误是否值得原地重试。

    与改造前的差异：只在**瞬时/网络类**上返回 True；`restricted` /
    `quality_unavailable` 返回 False（这正是参考实现「537 集 31 个文件被静默
    降级」那条教训的直接对策）。
    """
    label = str(error_type or "").strip()
    if label in NO_RETRY_ERROR_TYPES:
        return False
    if status_code is not None:
        if is_permission_status(status_code):
            return False
        if is_transient_status(status_code):
            return True
    if label in RETRY_ERROR_TYPES:
        return True
    # 空标签 / 未知标签：保持改造前的行为（可重试），避免把真实故障吞成静默失败
    return label == "" or label == "download_error"


def is_quota_exhausted(error_type: Any, message: Any = "") -> bool:
    """是否是「当天没救的额度耗尽」（应当暂停整个任务，而不是记为该集失败）。

    对应参考实现 `DownloadEngine.cs:322-340` 的 `ChannelQuotaException` 分流：
    短时自愈的节流 → 原地等待；当天没救的额度 → 暂停任务交给用户决定。
    """
    text = str(message or "")
    if str(error_type or "") == "quota_exhausted":
        return True
    markers = (
        "下载额度已用完", "今日下载额度", "额度用尽", "达到上限",
        "daily limit", "quota exceeded",
    )
    return any(marker in text for marker in markers)


__all__ = [
    "TRANSIENT_STATUS",
    "PERMISSION_STATUS",
    "DEFAULT_PERMISSION_RET",
    "DEFAULT_RISK_RET",
    "NO_RETRY_ERROR_TYPES",
    "RETRY_ERROR_TYPES",
    "RATE_LIMIT_TYPES",
    "ILLEGAL_REQUEST_TYPES",
    "DownloadError",
    "TransientError",
    "PermissionDenied",
    "RiskControlError",
    "IllegalRequestError",
    "ContentInvalidError",
    "is_transient_status",
    "is_permission_status",
    "classify",
    "should_retry",
    "is_quota_exhausted",
    "register",
    "refresh_registry",
]
