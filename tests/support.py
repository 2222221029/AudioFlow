"""测试辅助工具。

## 为什么需要 `patched_module`

`mock.patch.dict(sys.modules, {"qrcode": fake})` 看似只替换一个键，实际上
`unittest.mock` 在退出时会执行 `sys.modules.clear()` + `update(原始快照)`，
也就是**把 patch 期间新 import 的所有模块一并从 sys.modules 中移除**。

这会造成难以定位的连锁失败：pycryptodome 的 `AES.new(key, MODE_ECB)` 是
**函数内延迟 import** `Crypto.Cipher._mode_ecb`，而该模块在导入时通过
`load_pycryptodome_raw_lib()` 调用 `ffi.cdef()` 声明 C 符号，且这条路径**没有幂等缓存**。
一旦 `_mode_ecb` 被恢复快照时删掉，下一次 `AES.new(..., MODE_ECB)` 会重新执行模块代码、
再次 cdef 同名符号，抛：

    cffi.FFIError: multiple declarations of function ECB_start_operation

于是"先跑某文件、再跑另一文件"就会出现 4~7 个与密码学无关的测试失败
（表现为网易云扫码失败、喜马拉雅解密失败），而单独运行每个文件都通过。

`patched_module` 只增删目标这一个键，语义精确，不会波及其他模块。
"""

from __future__ import annotations

import contextlib
import sys

_MISSING = object()


@contextlib.contextmanager
def patched_module(name: str, module):
    """临时把 `name` 指向 `module`，退出时**只恢复这一个键**。

    与 `mock.patch.dict(sys.modules, {name: module})` 的区别在于后者会恢复整个
    sys.modules 快照，从而丢弃 patch 期间新导入的模块（见模块 docstring）。
    """
    previous = sys.modules.get(name, _MISSING)
    sys.modules[name] = module
    try:
        yield module
    finally:
        if previous is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
