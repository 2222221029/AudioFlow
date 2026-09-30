# -*- coding: utf-8 -*-
"""「按失败清单收窄重试范围」的行为锁定测试（移植分析 P1-9 / W3-1）。

`retry_existing_download_task` 改造前只能「重跑全集 + 跳过已存在」，代价是：
  * 每次重试都重新枚举整个专辑的章节（几百到几千集的接口往返）；
  * 在已知缺失的集上再失败一遍。

新增的 `_retry_chapters_from_report` 用 `_report.json` 侧车把范围收窄到真正
失败的那几集。**核心安全约束**：任何「收窄不成立」的情况都必须原样回退全集 ——
侧车是加速器，绝不能成为漏下章节的原因。
"""

import os

import pytest

from core import download_report as dr


def _load_narrower():
    """拿到真实的 `src.server.web_server` 模块，调用它的收窄函数。

    ⚠ 两个必须绕开的坑：
    ① 项目根的 `web_server.py` 只是个 shim（`from src.server.web_server import
       app, main`），它的骨架里**没有**我们要的函数；实现在 `src/server/`。
    ② `src.server.web_server` import 时会调 `ensure_runtime_dirs()` 去 mkdir
       `~/.audioflow`，只读 HOME 下抛 PermissionError —— conftest 已通过改环境
       变量做隔离（见 tests/conftest.py 顶部的说明），这里依赖它。

    ⚠ 早期版本用 AST 抽源码单独 `exec` 跑，漏掉模块里的辅助函数后误报失败 ——
    那种做法验证的**不是**生产代码，已废弃。
    """
    pytest.importorskip("flask")
    from src.server import web_server

    return web_server


@pytest.fixture(scope="module")
def narrower():
    return _load_narrower()


def _chapter(cid, order, title="章节"):
    return {"id": cid, "order_num": order, "title": title}


def _write_report(directory, failed_items, failed=None):
    report = dr.build_report(
        album_id="aid", failed=failed if failed is not None else len(failed_items),
        failed_items=failed_items,
    )
    dr.write_report(str(directory), report)


@pytest.fixture
def album_setup(tmp_path):
    """建一个专辑目录 + 章节表；返回 (module_fn, album, options, chapters)。"""
    module = _load_narrower()
    download_root = tmp_path / "downloads"
    album_dir = download_root / "测试专辑"
    album_dir.mkdir(parents=True)
    album = {"id": "aid", "title": "测试专辑", "platform": "喜马拉雅"}
    options = {"download_dir": str(download_root)}
    chapters = [_chapter(f"c{i}", i) for i in range(1, 11)]
    return module, album_dir, album, options, chapters


class TestNarrowingApplied:
    def test_narrows_to_failed_items(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [
            {"id": "c3", "order": 3, "title": "三"},
            {"id": "c7", "order": 7, "title": "七"},
        ])
        result = module._retry_chapters_from_report(album, options, chapters)
        assert result is not None
        assert [c["id"] for c in result] == ["c3", "c7"]

    def test_matches_by_order_when_id_missing(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [{"order": 5, "title": "五"}])
        result = module._retry_chapters_from_report(album, options, chapters)
        assert result is not None
        assert [c["id"] for c in result] == ["c5"]

    def test_chapter_prefix_is_normalised(self, narrower, album_setup):
        """worker 内部章节 id 有时带 `chapter-` 前缀。"""
        module, album_dir, album, options, chapters = album_setup
        chapters[2]["id"] = "chapter-c3"
        _write_report(album_dir, [{"id": "c3", "order": 3}])
        result = module._retry_chapters_from_report(album, options, chapters)
        assert result is not None
        assert result[0]["id"] == "chapter-c3"


class TestFallbackSafety:
    """⚠ 这些是**安全关键**用例：收窄不成立时必须回退 None（= 走全集）。"""

    def test_no_report_returns_none(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_report_without_failed_items_returns_none(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [], failed=3)     # 有计数没清单 = 死按钮
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_partial_cover_returns_none(self, narrower, album_setup):
        """侧车里的失败集在章节表里**找不齐** → 必须回退全集，不能漏集。"""
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [
            {"id": "c3", "order": 3},
            {"id": "NOT-IN-LIST", "order": 99},       # 对不上
        ])
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_stale_report_all_present_but_no_matches(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [{"id": "ghost", "order": 999}])
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_narrowing_equal_to_all_is_pointless(self, narrower, album_setup):
        """收窄结果等于全集时不做无谓改动（保持旧行为）。"""
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [_chapter(c["id"], c["order_num"]) for c in chapters])
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_missing_album_folder_returns_none(self, narrower, tmp_path):
        module = _load_narrower()
        album = {"id": "x", "title": "不存在的专辑", "platform": "p"}
        options = {"download_dir": str(tmp_path / "nowhere")}
        assert module._retry_chapters_from_report(album, options, [_chapter("c1", 1)]) is None

    def test_empty_chapter_list_returns_none(self, narrower, album_setup):
        module, album_dir, album, options, _ = album_setup
        _write_report(album_dir, [{"id": "c1", "order": 1}])
        assert module._retry_chapters_from_report(album, options, []) is None

    def test_non_dict_chapters_are_tolerated(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [{"id": "c3", "order": 3}])
        result = module._retry_chapters_from_report(album, options, [None, "junk"] + chapters)
        assert result is not None
        assert [c["id"] for c in result] == ["c3"]

    def test_corrupt_report_returns_none(self, narrower, album_setup):
        module, album_dir, album, options, chapters = album_setup
        (album_dir / dr.REPORT_FILENAME).write_text("{broken", encoding="utf-8")
        assert module._retry_chapters_from_report(album, options, chapters) is None

    def test_exception_is_swallowed(self, narrower, album_setup):
        """任何异常都必须回退，不能让读侧车成为重试的新故障点。"""
        module, album_dir, album, options, chapters = album_setup
        _write_report(album_dir, [{"id": "c3", "order": 3}])
        # 传一个会在迭代时炸掉的 chapters
        class _Boom:
            def __iter__(self):
                raise RuntimeError("boom")

        assert module._retry_chapters_from_report(album, options, _Boom()) is None


class TestRealWorldShapes:
    def test_single_failed_out_of_thousand(self, narrower, tmp_path):
        """真实收益场景：1000 集只失败 1 集 → 只重下那 1 集。"""
        module = _load_narrower()
        root = tmp_path / "dl"
        album_dir = root / "大专辑"
        album_dir.mkdir(parents=True)
        chapters = [_chapter(f"c{i}", i) for i in range(1, 1001)]
        _write_report(album_dir, [{"id": "c847", "order": 847, "title": "第847集"}])

        album = {"id": "big", "title": "大专辑", "platform": "喜马拉雅"}
        result = module._retry_chapters_from_report(album, {"download_dir": str(root)}, chapters)
        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "c847"

    def test_track_id_key_is_supported(self, narrower, tmp_path):
        """不同平台的章节字典用不同键名（track_id / chapter_id）。"""
        module = _load_narrower()
        root = tmp_path / "dl"
        album_dir = root / "专辑"
        album_dir.mkdir(parents=True)
        _write_report(album_dir, [{"id": "t9", "order": 9}])
        chapters = [{"track_id": "t9", "order_num": 9, "title": "九"}]
        album = {"id": "a", "title": "专辑", "platform": "喜马拉雅"}
        # 只有一集且它就是全集 → 收窄无意义，返回 None（符合预期）
        assert module._retry_chapters_from_report(album, {"download_dir": str(root)}, chapters) is None

        chapters.append({"track_id": "t10", "order_num": 10, "title": "十"})
        result = module._retry_chapters_from_report(album, {"download_dir": str(root)}, chapters)
        assert result is not None and result[0]["track_id"] == "t9"
