# core package
"""AudioFlow 核心包。

## 为什么这里要写代码

`core/errors.py` 提供的是**平台无关**的异常分级判据，但真正要分级的异常类
（`core.lrts_manager.RateLimitError` / `IllegalRequestError`）定义在各平台模块里。

改造前，`core/download_worker.py` 用这种写法拿到它们：

    try:
        from core.lrts_manager import RateLimitError as _RateLimitError
    except Exception:
        _RateLimitError = None

一旦 lrts 模块改名/移动，`_RateLimitError` 静默变成 None，**重试逻辑无声失效**，
且没有任何报错。这里改成**显式登记**：

* 登记失败 = 真出错，但**不阻断 import**（`except Exception` 里不打印也不抛），
  因为 `core` 包被大量模块导入，不能因为一个可选平台的异常类拿不到就让整个进程起不来；
* 登记失败时 `errors.RATE_LIMIT_TYPES` 保持空元组，`isinstance(x, ())` 恒为 False，
  **不抛 TypeError** —— 这是相对旧写法的硬性改进。

⚠ 这里刻意**不 import 平台 manager**：那会把 requests/ffmpeg 等重依赖拉进每一个
`import core.xxx` 的路径。`import core` 时**只登记已经被导入过的模块**里的类；
下载链路必然 import lrts_manager，所以运行期一定登记得上。若某次运行没有导入它，
`classify()` 会退回按状态码/类型名判定，行为不比改造前差。
"""

import sys


def _register_platform_errors() -> None:
    """把已加载的平台模块里已有的异常类登记到 `core.errors` 的分级表上。

    ⚠ 只查 `sys.modules`，**不主动 import** —— 避免 `import core` 变成
    「把所有平台依赖全部加载」。
    """
    try:
        from core import errors
    except Exception:  # pragma: no cover - errors 是纯标准库模块，正常不会失败
        return

    changed = False
    for module_name, class_name in (
        ("core.lrts_manager", "RateLimitError"),
        ("core.lrts_manager", "IllegalRequestError"),
    ):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        cls = getattr(module, class_name, None)
        if cls is not None:
            errors.register(class_name, cls)
            changed = True
    if changed:
        errors.refresh_registry()


_register_platform_errors()

#: 平台模块被导入后调用它，即可让分级表认识该平台自己的异常类。
register_platform_errors = _register_platform_errors
