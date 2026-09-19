"""Pytest 全局配置与共享 fixture。

本文件承担两个职责，缺一不可：

1. **运行时目录隔离**（必须在项目模块被 import 之前完成）
   `src/server/web_server.py` 在模块级就调用 `core.platform_config.ensure_runtime_dirs()`
   （见该文件 :89），它会 mkdir `~/.audioflow`。在 CI / 只读 HOME 环境下这会抛
   `PermissionError`，导致 8 个测试文件在 **collection 阶段**就失败，根本无法运行。
   pytest 会先 import conftest.py 再 import 测试模块，因此在这里改写环境变量是
   唯一可靠的时机。

2. **网络熔断**（`assert_no_network` / `no_network` fixture）
   参考实现的离线测试策略：单元测试不应发起真实网络请求。默认只提供断言工具，
   由测试显式启用，避免影响既有用例的可控行为。

项目模块一律通过 `core.*` / `src.*` / `server` 绝对导入，故需把项目根加入 sys.path。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- 运行时目录隔离 ---------------------------------------------------------
# 必须在导入 core.platform_config（以及任何间接导入 web_server 的模块）之前生效。
_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="audioflow-tests-"))
(_RUNTIME_ROOT / "config").mkdir(parents=True, exist_ok=True)
(_RUNTIME_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_RUNTIME_ROOT / "logs").mkdir(parents=True, exist_ok=True)
(_RUNTIME_ROOT / "downloads").mkdir(parents=True, exist_ok=True)

os.environ.setdefault("CONFIG_DIR", str(_RUNTIME_ROOT / "config"))
os.environ.setdefault("DATA_DIR", str(_RUNTIME_ROOT / "data"))
os.environ.setdefault("LOG_DIR", str(_RUNTIME_ROOT / "logs"))
# download_dir() 的默认值是 Docker 路径 /app/downloads，必须显式覆盖。
os.environ.setdefault("DOWNLOAD_DIR", str(_RUNTIME_ROOT / "downloads"))
# 测试期间不要因为后台线程/调度器产生真实副作用。
os.environ.setdefault("AUDIOFLOW_DISABLE_SCHEDULER", "1")


@pytest.fixture
def runtime_root() -> Path:
    """本次测试会话使用的隔离运行时目录。"""
    return _RUNTIME_ROOT


@pytest.fixture
def assert_no_network(monkeypatch):
    """让测试内的真实网络请求直接失败。

    用法::

        def test_x(assert_no_network):
            ...
            assert_no_network()   # 启用熔断（此后任何 requests 调用都会 AssertionError）

    之所以做成"显式启用"而非 autouse：既有用例中包了一层可控的 mock session，
    全局熔断会改变它们的失败模式，不利于本次改造的对比验证。
    """

    def _enable() -> None:
        def _blocked(*args, **kwargs):
            raise AssertionError(
                "测试发起了真实网络请求，已被 assert_no_network 拦截。"
                "请 mock 掉 requests.Session 的相应方法。"
            )

        import requests

        monkeypatch.setattr(requests.Session, "request", _blocked, raising=False)
        monkeypatch.setattr(requests.Session, "get", _blocked, raising=False)
        monkeypatch.setattr(requests.Session, "post", _blocked, raising=False)
        monkeypatch.setattr(requests, "get", _blocked, raising=False)
        monkeypatch.setattr(requests, "post", _blocked, raising=False)

    return _enable
