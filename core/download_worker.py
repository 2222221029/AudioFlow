#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from core.qt_compat import QThread, pyqtSignal
import os
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 每个失败章节最多自动重试次数
_MAX_RETRIES = 2
# 重试间隔基数（秒），第 n 次重试等待 n * _RETRY_BACKOFF 秒
_RETRY_BACKOFF = 2
# 限速错误等待时间（秒）——lrts.me apiStatus=114 时等待后重试
_RATE_LIMIT_WAIT = 30
# 懒人听书 status=4 / “非法请求AA” 通常是风控冷却，短间隔重试只会继续失败。
_LRTS_ILLEGAL_WAIT = int(os.getenv("LRTS_ILLEGAL_REQUEST_WAIT", "180") or "180")


class DownloadWorker(QThread):
    """下载工作线程，避免阻塞UI线程"""
    progress_updated = pyqtSignal(str, int, int)  # task_id, current, total
    realtime_progress_updated = pyqtSignal(str, int, int, int)  # task_id, completed, total, percent
    download_completed = pyqtSignal(str, int, int, list, list)  # task_id, success, fail, success_chapters, failed_chapters
    task_info_updated = pyqtSignal(str, dict)  # task_id, task_info

    def __init__(self, chapters, download_dir, quality, album_title, album_id, platform, task_id,
                 parent=None, search_manager=None, voice_config=None, coin_reference_id=None):
        super().__init__(parent)
        self.chapters = chapters
        self.download_dir = download_dir
        self.quality = quality
        self.album_title = album_title
        self.album_id = album_id
        self.platform = platform
        self.task_id = task_id
        self.success_count = 0
        self.failed_count = 0
        self.success_chapters = []
        self.failed_chapters = []
        self.search_manager = search_manager
        self.voice_config = voice_config
        self._is_paused = False
        self._is_stopped = False
        self.coin_reference_id = None  # 自用版不使用旧额度机制，保留参数兼容旧调用
        self._progress_lock = threading.Lock()
        self._chapter_progress = {}
        self._completed_for_progress = 0
        self._thread_managers = threading.local()
        # CookieManager 只创建一次，所有线程共享读取（CookieManager 只做文件读取，线程安全）
        from core.cookie_manager import CookieManager
        self.cookie_manager = CookieManager()
        # 详细逐章日志开关：默认关闭，避免批量补全（每专辑上千集）刷出成千上万条日志。
        # 需排查时用环境变量 AUDIOFLOW_DOWNLOAD_VERBOSE=1 或 cookie download_verbose_log 开启。
        self._verbose = (
            os.getenv("AUDIOFLOW_DOWNLOAD_VERBOSE", "").lower() in ("1", "true", "yes", "on")
            or str(self.cookie_manager.get_cookie("download_verbose_log") or "").lower() in ("1", "true", "yes", "on")
        )

    def _dbg(self, msg):
        """仅在 verbose 模式打印的逐章调试日志（默认静默以免刷屏）。"""
        if self._verbose:
            print(msg)

    def _setting_enabled(self, key, default=False):
        value = self.cookie_manager.get_cookie(key)
        if value in ("", None):
            return bool(default)
        return str(value).lower() in ("1", "true", "yes", "on")

    def _format_filename_prefix(self, order):
        try:
            order = max(1, int(order))
        except (TypeError, ValueError):
            order = 1
        fmt = str(self.cookie_manager.get_cookie("filename_prefix_format") or "").strip()
        if not fmt:
            if self.cookie_manager.get_cookie("fixed_naming_enabled") == "true":
                legacy = self.cookie_manager.get_cookie("fixed_naming_format") or "0001"
                return f"{legacy}{order}-"
            if self.cookie_manager.get_cookie("increment_naming_enabled") == "true":
                legacy = self.cookie_manager.get_cookie("increment_naming_format") or "000"
                width = {"000": 4, "00": 3, "0": 2}.get(legacy, max(1, len(legacy)))
                return f"{str(order).zfill(width)}-"
            fmt = "0001-"
        if fmt.lower() in ("none", "no", "off"):
            return ""
        digits = sum(1 for char in fmt if char in "01")
        width = digits if digits > 0 else len(str(order))
        suffix = "".join(char for char in fmt if char not in "01")
        return f"{str(order).zfill(width)}{suffix}"

    def _album_base_dir(self, safe_album_title):
        parts = [self.download_dir]
        if self._setting_enabled("organize_by_platform_enabled", False):
            parts.append(self._sanitize_filename(self.platform or "未知平台"))
        parts.append(safe_album_title)
        return os.path.join(*parts)

    def _chapters_per_folder(self):
        value = self.cookie_manager.get_cookie("chapters_per_folder")
        try:
            return max(1, min(10000, int(value)))
        except (TypeError, ValueError):
            return 200

    # ------------------------------------------------------------------
    # 进度工具
    # ------------------------------------------------------------------

    def _make_progress_callback(self, chapter_index):
        """创建线程安全的单章下载进度回调。"""
        def callback(downloaded, total):
            if total and total > 0:
                percent = max(0, min(100, int(downloaded * 100 / total)))
            else:
                with self._progress_lock:
                    percent = min(95, self._chapter_progress.get(chapter_index, 0) + 1)
                    self._chapter_progress[chapter_index] = percent
                    self._emit_realtime_progress_locked()
                return
            with self._progress_lock:
                self._chapter_progress[chapter_index] = percent
                self._emit_realtime_progress_locked()
        return callback

    def _emit_realtime_progress_locked(self):
        total_chapters = max(1, len(self.chapters))
        active_units = sum(self._chapter_progress.values())
        current_units = self._completed_for_progress * 100 + active_units
        percent = max(0, min(100, int(current_units / (total_chapters * 100) * 100)))
        self.realtime_progress_updated.emit(
            self.task_id,
            self._completed_for_progress,
            len(self.chapters),
            percent
        )

    def pause(self):
        """暂停下载任务。"""
        self._is_paused = True

    def resume(self):
        """继续下载任务。"""
        self._is_paused = False

    def stop(self):
        """请求停止下载任务。"""
        self._is_stopped = True
        self._is_paused = False

    # ------------------------------------------------------------------
    # 主下载循环
    # ------------------------------------------------------------------

    def run(self):
        """在后台线程中执行下载任务"""
        try:
            print("✅ 自用版：开始下载（不进行授权或额度校验）")

            max_workers = self.cookie_manager.get_download_threads()
            total_chapters = len(self.chapters)
            if self.platform == '喜马拉雅':
                xmly_threads = os.getenv("XMLY_DOWNLOAD_THREADS")
                if xmly_threads:
                    max_workers = int(xmly_threads)
                max_workers = max(1, min(64, max_workers, total_chapters))
                print(f"🎧 喜马拉雅并发下载: {max_workers}（跟随设置，可用 XMLY_DOWNLOAD_THREADS 覆盖）")
            if self.platform == '懒人听书':
                lrts_workers = int(os.getenv("LRTS_DOWNLOAD_THREADS", "1") or "1")
                max_workers = max(1, min(3, lrts_workers, total_chapters))
                print(f"📚 懒人听书使用保守下载模式，并发数: {max_workers}（可用 LRTS_DOWNLOAD_THREADS=1-3 调整）")
            if self.platform == '番茄畅听':
                # 每章需要 ffmpeg 解密+转码，CPU 密集；限制并发避免打满 NAS CPU
                max_workers = max(1, min(2, max_workers, total_chapters))
                print(f"🍅 番茄畅听 CPU 密集模式，并发数限制为: {max_workers}")
            if self.platform in ('番茄听书', '七猫听书'):
                print(f"📖 {self.platform} 并发下载，线程数: {max_workers}（与设置一致）")
            max_workers = max(1, min(64, max_workers, total_chapters))
            print(f"Using configured parallel downloads: {max_workers}")
            print(f"📊 准备下载 {total_chapters} 个章节，并发数: {max_workers}")

            # 在任务开始时发送一次任务元数据（不在章节循环中重复发送）
            task_info = {
                'album_title': self.album_title,
                'download_dir': self.download_dir,
                'quality': self.quality,
                'platform': self.platform,
                'album_id': self.album_id,
                'voice_config': self.voice_config,
            }
            self.task_info_updated.emit(self.task_id, task_info)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                print(f"🚀 开始下载...")
                futures = {}
                print(f"📝 正在准备下载任务...")
                for i, chapter in enumerate(self.chapters, 1):
                    if total_chapters <= 100 or i <= 5 or i > total_chapters - 5 or i % 500 == 0:
                        print(f"   🔍 准备下载章节 {i}/{total_chapters}: title={chapter.get('title', '未知')[:20]}..., "
                              f"order_num={chapter.get('order_num', 'None')}")
                    chapter_index = i
                    # _download_chapter_with_retry 自行创建线程安全的 Manager，不再传入共享实例
                    future = executor.submit(self._download_chapter_with_retry, chapter, chapter_index)
                    futures[future] = (chapter_index, chapter)

                print(f"✅ 任务准备完成，开始下载...")

                completed_count = 0
                last_update_time = 0

                for future in as_completed(futures):
                    # 检查停止标志
                    if self._is_stopped:
                        print(f"⏹️ 下载任务已停止")
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        # 保留已成功的数据，不清零
                        self.download_completed.emit(
                            self.task_id,
                            self.success_count,
                            self.failed_count,
                            self.success_chapters,
                            self.failed_chapters,
                        )
                        return

                    # 暂停等待
                    while self._is_paused and not self._is_stopped:
                        self.msleep(50)

                    if self._is_stopped:
                        print(f"⏹️ 下载任务已停止")
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        self.download_completed.emit(
                            self.task_id,
                            self.success_count,
                            self.failed_count,
                            self.success_chapters,
                            self.failed_chapters,
                        )
                        return

                    chapter_index, chapter = futures[future]
                    completed_count += 1

                    try:
                        success = future.result(timeout=120)
                        if success:
                            self.success_count += 1
                            self.success_chapters.append(chapter)
                        else:
                            self.failed_count += 1
                            if '_error' not in chapter:
                                chapter['_error'] = '下载失败'
                            self.failed_chapters.append(chapter)
                            print(f"   ❌ 章节下载失败: {chapter.get('title', '未知章节')} - "
                                  f"{chapter.get('_error', '未知错误')}")
                    except TimeoutError:
                        print(f"   ⏱️ 下载章节超时: {chapter.get('title', '未知章节')}")
                        self.failed_count += 1
                        chapter['_error'] = '下载超时（2分钟）'
                        self.failed_chapters.append(chapter)
                    except Exception as e:
                        print(f"   ❌ 下载章节时出错: {e}")
                        self.failed_count += 1
                        chapter['_error'] = f'下载异常: {str(e)[:50]}'
                        self.failed_chapters.append(chapter)

                    current_time = time.time()
                    with self._progress_lock:
                        self._completed_for_progress = completed_count
                        self._chapter_progress.pop(chapter_index, None)
                        self._emit_realtime_progress_locked()
                    if current_time - last_update_time >= 0.2 or completed_count == len(self.chapters):
                        self.progress_updated.emit(self.task_id, completed_count, len(self.chapters))
                        last_update_time = current_time
                        # 进度日志每 20 章（或最后一章）打印一次，避免上千集时刷屏
                        if completed_count % 20 == 0 or completed_count == len(self.chapters):
                            print(f"📊 下载进度: {completed_count}/{len(self.chapters)} "
                                  f"({int(completed_count / len(self.chapters) * 100)}%)")

            print(f"🎉 下载任务完成: 成功 {self.success_count} 个，失败 {self.failed_count} 个")
            self.download_completed.emit(
                self.task_id,
                self.success_count,
                self.failed_count,
                self.success_chapters,
                self.failed_chapters,
            )

        except Exception as e:
            print(f"❌ 下载任务执行失败: {e}")
            import traceback
            traceback.print_exc()
            self.download_completed.emit(
                self.task_id,
                self.success_count,
                self.failed_count,
                self.success_chapters,
                self.failed_chapters,
            )

    # ------------------------------------------------------------------
    # 兼容旧调用
    # ------------------------------------------------------------------

    def _handle_coin_refund(self):
        """兼容旧调用：自用版没有旧额度返还。"""
        pass

    # ------------------------------------------------------------------
    # 重试封装
    # ------------------------------------------------------------------

    def _download_chapter_with_retry(self, chapter, chapter_index):
        """带自动重试的章节下载入口（在线程池中执行）。"""
        # 动态导入异常类型，避免循环依赖
        try:
            from core.lrts_manager import RateLimitError as _RateLimitError
        except Exception:
            _RateLimitError = None
        try:
            from core.lrts_manager import IllegalRequestError as _IllegalRequestError
        except Exception:
            _IllegalRequestError = None

        for attempt in range(_MAX_RETRIES + 1):
            if self._is_stopped:
                return False

            if attempt > 0:
                wait = attempt * _RETRY_BACKOFF
                print(f"   🔄 章节 {chapter_index} 第 {attempt} 次重试，等待 {wait}s…")
                time.sleep(wait)
                chapter.pop('_error', None)

            try:
                result = self._download_single_chapter(chapter, chapter_index)
            except Exception as e:
                # 捕获限速错误：等待 30 秒后重试（不计入普通重试次数）
                if _RateLimitError and isinstance(e, _RateLimitError):
                    wait_sec = _RATE_LIMIT_WAIT
                    print(f"   ⏳ 章节 {chapter_index} 触发限速，等待 {wait_sec}s 后重试…")
                    for _ in range(wait_sec):
                        if self._is_stopped:
                            return False
                        time.sleep(1)
                    chapter.pop('_error', None)
                    chapter['_error'] = '限速重试中'
                    # 重试本次（attempt 不递增）
                    try:
                        result = self._download_single_chapter(chapter, chapter_index)
                    except Exception as e2:
                        if _RateLimitError and isinstance(e2, _RateLimitError):
                            chapter['_error'] = f'持续限速，跳过: {e2}'
                            return False
                        chapter['_error'] = str(e2)[:100]
                        result = False
                elif _IllegalRequestError and isinstance(e, _IllegalRequestError):
                    wait_sec = _LRTS_ILLEGAL_WAIT
                    print(f"   🧊 章节 {chapter_index} 触发懒人听书风控({e})，冷却 {wait_sec}s 后重试…")
                    for _ in range(wait_sec):
                        if self._is_stopped:
                            return False
                        time.sleep(1)
                    chapter.pop('_error', None)
                    chapter['_error'] = '非法请求冷却重试中'
                    try:
                        result = self._download_single_chapter(chapter, chapter_index)
                    except Exception as e2:
                        if _IllegalRequestError and isinstance(e2, _IllegalRequestError):
                            chapter['_error'] = f'持续风控，跳过: {e2}'
                            return False
                        chapter['_error'] = str(e2)[:100]
                        result = False
                else:
                    chapter['_error'] = str(e)[:100]
                    result = False

            if result:
                if attempt > 0:
                    print(f"   ✅ 章节 {chapter_index} 重试成功（第 {attempt} 次）")
                return True

            if self._is_stopped:
                return False

        print(f"   ❌ 章节 {chapter_index} 重试 {_MAX_RETRIES} 次后仍失败")
        return False

    # ------------------------------------------------------------------
    # 线程安全的 Manager 工厂
    # ------------------------------------------------------------------

    def _make_thread_manager(self, platform):
        """
        为当前线程创建独立的 Manager 实例。
        每个下载线程持有自己的 session，彻底消除共享 session 竞态。
        Cookie 从 search_manager 的已有实例中复制（若可用），
        否则回退到各 Manager 默认的 CookieManager 读取。
        """
        sm = self.search_manager

        def _copy_cookies(src_mgr, dst_mgr):
            """把 src_mgr.session 的 headers/cookies 复制到 dst_mgr.session。"""
            if src_mgr and hasattr(src_mgr, 'session') and hasattr(dst_mgr, 'session'):
                dst_mgr.session.headers.update(src_mgr.session.headers)
                dst_mgr.session.cookies.update(src_mgr.session.cookies)

        if platform == '喜马拉雅':
            # 喜马拉雅 Manager 本身已线程安全（每次下载走独立的 downloader 实例），
            # 可以共享；此处返回 None 让调用方继续使用 search_manager 中的实例。
            return None

        if platform == '懒人听书':
            cached = getattr(self._thread_managers, 'lrts', None)
            if cached is not None:
                return cached
            from core.lrts_manager import LRTSManager
            m = LRTSManager()
            src = sm.lrts_manager if sm else None
            _copy_cookies(src, m)
            if src and getattr(src, 'credentials', None):
                m.set_cookie(getattr(src, 'credentials'))
            elif src and hasattr(src, 'cookie_string'):
                # LRTS now stores Android App credentials (imei + token) in the
                # cookie slot. Copying only cookie_string is not enough because
                # a fresh manager would otherwise create a guest client and paid
                # chapters return "收费章节未购买".
                m.set_cookie(src.cookie_string)
            elif self.cookie_manager:
                m.set_cookie(self.cookie_manager.get_cookie('lrts'))
            self._thread_managers.lrts = m
            return m

        if platform == '番茄畅听':
            from core.fanqie_manager import FanqieManager
            m = FanqieManager()
            _copy_cookies(sm.fanqie_manager if sm else None, m)
            return m

        if platform == '番茄听书':
            # 番茄听书 Manager 底层依赖单例 fanqie_portable.py 模块，无法真正复制；
            # 但 download_chapter(chapter_id, voice_cfg, file_path) 以参数传入 voice_cfg，
            # 不依赖实例属性，多线程共享同一实例是安全的。
            return sm.fanqie_tingshu_manager if sm else None

        if platform == '七猫听书':
            # 同上，download_chapter 以参数传入 voice_config，共享实例安全。
            return sm.qimao_manager if sm else None

        if platform == '云听FM':
            from core.yuntu_manager import YunTuManager
            m = YunTuManager()
            _copy_cookies(sm.yuntu_manager if sm else None, m)
            return m

        if platform == '起点听书':
            # 起点听书走 search_manager 内部的 SearchManager，共享即可（内部无 session 竞态）
            return sm.search_manager if sm else None

        if platform == '蜻蜓FM':
            from core.qtfm_manager import QtfmManager
            m = QtfmManager()
            src = sm.qtfm_manager if sm else None
            _copy_cookies(src, m)
            if src:
                if hasattr(src, 'access_token'):
                    m.access_token = src.access_token
                if hasattr(src, 'qingting_id'):
                    m.qingting_id = src.qingting_id
                if hasattr(src, 'is_logged_in'):
                    m.is_logged_in = src.is_logged_in
            return m

        if platform == '酷我听书':
            # 酷我每线程必须独立 Manager（已知 session 会导致实际串行）
            from core.kuwo_manager import KuwoManager
            return KuwoManager()

        if platform == '网易云听书':
            from core.netease_cloud_audiobook_manager import NeteaseCloudAudiobookManager
            m = NeteaseCloudAudiobookManager()
            src = sm.netease_manager if sm else None
            _copy_cookies(src, m)
            if src and hasattr(src, 'cookie_string'):
                m.cookie_string = src.cookie_string
                m.csrf_token = src.csrf_token
            return m

        if platform == '荔枝FM':
            from core.lizhi_manager import LizhiManager
            m = LizhiManager()
            _copy_cookies(sm.lizhi_manager if sm else None, m)
            return m

        return None

    # ------------------------------------------------------------------
    # 单章节下载（线程池内执行）
    # ------------------------------------------------------------------

    def _download_single_chapter(self, chapter, chapter_index):
        """下载单个章节。每次调用均使用线程本地 Manager，无共享 session 竞态。"""
        thread_name = threading.current_thread().name

        try:
            if self._is_stopped:
                print(f"⏹️ [线程 {thread_name}] 任务已停止，跳过章节 {chapter_index}")
                return False

            while self._is_paused and not self._is_stopped:
                time.sleep(0.05)

            if self._is_stopped:
                print(f"⏹️ [线程 {thread_name}] 任务已停止，跳过章节 {chapter_index}")
                return False

            print(f"📥 开始下载第 {chapter_index} 章 [{thread_name}]")

            # 获取该线程专用的 Manager（无竞态）
            download_manager = self._make_thread_manager(self.platform)
            if download_manager is None and self.platform not in ('喜马拉雅',):
                print(f"❌ 不支持的平台: {self.platform}")
                chapter['_error'] = f'不支持的平台: {self.platform}'
                return False
            # 喜马拉雅使用 search_manager 中的共享实例（内部下载路径线程安全）
            if self.platform == '喜马拉雅':
                if self.search_manager:
                    download_manager = self.search_manager.ximalaya_manager
                else:
                    from core.ximalaya_manager import XimalayaManager
                    download_manager = XimalayaManager()

            # ---- 章节基础信息 ----
            chapter_id = str(chapter.get('id', ''))
            if chapter_id.startswith('chapter-'):
                chapter_id = chapter_id.replace('chapter-', '')
                print(f"   🔄 移除chapter-前缀后的章节ID: {chapter_id}")

            chapter_title = chapter.get('title', f'章节{chapter_index}')
            self._dbg(f"   📝 UI显示标题: {chapter_title}")

            safe_chapter_title = self._sanitize_filename(chapter_title)
            safe_album_title = self._sanitize_filename(self.album_title)

            # ---- 命名格式（使用实例级 cookie_manager，避免重复实例化）----
            cookie_manager = self.cookie_manager
            increment_naming_enabled = cookie_manager.get_cookie('increment_naming_enabled') == 'true'
            fixed_naming_enabled = cookie_manager.get_cookie('fixed_naming_enabled') == 'true'

            order_num_value = chapter.get('ui_display_index') or chapter.get('order_num')

            self._dbg(f"   🔍 调试: order_num={order_num_value} chapter_index={chapter_index} "
                      f"递增命名={increment_naming_enabled} 固定命名={fixed_naming_enabled}")

            if order_num_value is not None and order_num_value > 0:
                actual_order = order_num_value
                self._dbg(f"   ✅ 使用order_num（UI序号）: {actual_order}")
            else:
                import re
                patterns = [
                    r'第?(\d+)[章节集]',
                    r'(\d+)[章节集]',
                    r'第(\d+)',
                    r'(\d+)',
                ]
                actual_order = None
                for pattern in patterns:
                    match = re.search(pattern, chapter_title)
                    if match:
                        actual_order = int(match.group(1))
                        self._dbg(f"   ✅ 从标题提取章节号: {actual_order} (使用模式: {pattern})")
                        break
                if actual_order is None:
                    actual_order = chapter_index
                    self._dbg(f"   ⚠️ 无法提取序号，使用下载队列序号: {actual_order}")

            filename_prefix = self._format_filename_prefix(actual_order)
            self._dbg(f"   🎯 最终文件名前缀: {filename_prefix}")

            # ---- 文件扩展名 ----
            fanqie_audio_info = None
            if self.platform == '番茄畅听':
                fanqie_audio_info = download_manager.get_audio_download_info(
                    chapter_id, self.voice_config or '无损真人录制', self.album_id
                )
                if not fanqie_audio_info or not fanqie_audio_info.get('url'):
                    print(f"❌ 无法获取番茄音频链接")
                    chapter['_error'] = '无法获取音频链接'
                    return False
                file_extension = '.mp3' if str(self.quality or '').upper().startswith('MP3') else '.m4a'
                print(f"   🎵 番茄实际格式: {fanqie_audio_info.get('format')}{file_extension}")
            elif self.platform == '喜马拉雅':
                file_extension = '.m4a' if self.quality.startswith('M4A') else '.mp3'
            elif self.platform == '懒人听书':
                file_extension = '.m4a'
            elif self.platform == '云听FM':
                file_extension = '.mp3'
            elif self.platform == '蜻蜓FM':
                file_extension = '.m4a'
            elif self.platform == '酷我听书':
                file_extension = '.mp3'
            elif self.platform == '网易云听书':
                file_extension = '.mp3'
            elif self.platform == '荔枝FM':
                file_extension = '.mp3'
            elif self.platform == '番茄听书':
                file_extension = '.m4a' if self.quality.startswith('M4A') else '.mp3'
            elif self.platform == '七猫听书':
                file_extension = '.mp3'
            else:
                file_extension = '.mp3'

            filename = f"{filename_prefix}{safe_chapter_title}{file_extension}"

            # ---- 文件路径（支持分章节保存）----
            chapter_order = actual_order
            split_enabled = cookie_manager.get_cookie('split_chapters_enabled') == 'true'
            self._dbg(f"   📁 分章节保存启用: {split_enabled}")

            if split_enabled and chapter_order > 0:
                chapters_per_folder = self._chapters_per_folder()
                folder_start = ((chapter_order - 1) // chapters_per_folder) * chapters_per_folder + 1
                folder_end = folder_start + chapters_per_folder - 1
                folder_name = f"{folder_start}-{folder_end}章"
                chapter_range_dir = os.path.join(self._album_base_dir(safe_album_title), folder_name)
                Path(chapter_range_dir).mkdir(parents=True, exist_ok=True)
                file_path = os.path.join(chapter_range_dir, filename)
                self._dbg(f"   📂 分章节保存到: {safe_album_title}/{folder_name}/{filename}")
                self._dbg(f"   📍 完整路径: {file_path}")
            else:
                album_folder = self._album_base_dir(safe_album_title)
                Path(album_folder).mkdir(parents=True, exist_ok=True)
                file_path = os.path.join(album_folder, filename)
                self._dbg(f"   📂 保存到: {safe_album_title}/{filename}")
                self._dbg(f"   📍 完整路径: {file_path}")

            # 每章保留这一条标识日志（序号+标题），其余逐章细节走 _dbg（默认静默）
            print(f"📖 [{actual_order}] {chapter_title}")

            # ---- 文件已存在检查 ----
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                if file_size > 1024:
                    size_mb = file_size / (1024 * 1024)
                    if self.platform == '番茄畅听':
                        print(f"ℹ️ 番茄畅听目标文件已存在 ({size_mb:.1f}MB)，继续走专用管线确认输出")
                    else:
                        print(f"✅ 文件已存在，跳过下载 ({size_mb:.1f}MB)")
                        return True
                else:
                    print(f"🔄 文件损坏，重新下载")
            else:
                self._dbg(f"📥 开始下载文件")

            # ---- 番茄听书 / 七猫听书 直接走各自 download_chapter ----
            if self.platform == '番茄听书':
                voice_cfg = self.voice_config
                if not voice_cfg:
                    print("❌ 番茄听书：未设置音色配置")
                    chapter['_error'] = '未设置音色'
                    return False
                print(f"📖 番茄听书下载: tone={voice_cfg.get('name')}")
                success = download_manager.download_chapter(chapter_id, voice_cfg, file_path)
                if not success:
                    chapter['_error'] = chapter.get('_error') or '下载失败'
                return success

            if self.platform == '七猫听书':
                voice = self.voice_config
                print(f"📖 七猫听书下载: voice={voice.get('name') if voice else '默认'}")
                qimao_book_id = (
                    (voice or {}).get('book_id')
                    or chapter.get('qimao_book_id')
                    or getattr(download_manager, '_last_book_id', '')
                    or self.album_id
                )
                success = download_manager.download_chapter(
                    chapter_id,
                    voice_config=voice,
                    output_path=file_path,
                    book_id=qimao_book_id,
                )
                if not success:
                    chapter['_error'] = chapter.get('_error') or '下载失败'
                return success

            # ---- 暂停/停止二次检查 ----
            if self._is_stopped:
                return False
            while self._is_paused and not self._is_stopped:
                time.sleep(0.05)
            if self._is_stopped:
                return False

            # ---- 获取音频 URL ----
            audio_url = None
            try:
                if self.platform == '喜马拉雅':
                    audio_url = str(chapter_id)
                elif self.platform == '懒人听书':
                    chapter_data = chapter.get('_chapter_data', None)
                    # RateLimitError 会透传到 _download_chapter_with_retry 处理
                    audio_url = download_manager.get_audio_url(self.album_id, chapter_id, chapter_data)
                elif self.platform == '番茄畅听':
                    audio_url = fanqie_audio_info.get('url')
                    print(f"🎵 使用已解析的番茄音频链接 ({fanqie_audio_info.get('format')})")
                elif self.platform == '云听FM':
                    audio_url = (
                        chapter.get('mediaUrl', '')
                        or chapter.get('playUrlHigh', '')
                        or chapter.get('playUrlLow', '')
                        or chapter.get('downloadUrl', '')
                    )
                    if audio_url:
                        print(f"☁️ 云听FM音频URL: {audio_url[:100]}...")
                    else:
                        print(f"❌ 云听FM章节数据中没有音频URL")
                        print(f"   章节数据字段: {list(chapter.keys())}")
                elif self.platform == '起点听书':
                    print(f"📖 获取起点听书音频URL...")
                    audio_url_dict = download_manager.get_qidian_audio_url(self.album_id, chapter_id)
                    if audio_url_dict and 'default' in audio_url_dict:
                        audio_url = audio_url_dict['default'].get('url', '')
                    else:
                        audio_url = None
                elif self.platform == '蜻蜓FM':
                    print(f"🎧 获取蜻蜓FM音频URL...")
                    audio_url = download_manager.get_audio_url(self.album_id, chapter_id)
                    if audio_url:
                        print(f"🎧 蜻蜓FM音频URL: {audio_url[:100]}...")
                    else:
                        print(f"❌ 蜻蜓FM音频URL获取失败")
                elif self.platform == '酷我听书':
                    kuwo_quality = (
                        download_manager.normalize_download_quality(self.quality, self.voice_config)
                        if hasattr(download_manager, 'normalize_download_quality')
                        else 'lossless'
                    )
                    print(f"🎵 获取酷我听书下载信息，音质: {kuwo_quality}...")
                    download_info = download_manager.get_download_info(chapter_id, kuwo_quality)
                    if download_info and download_info.get('url'):
                        audio_url = download_info['url']
                        actual_format = download_info.get('format', 'mp3')
                        actual_extension = download_info.get('extension', f'.{actual_format}')
                        actual_bitrate = download_info.get('bitrate', 0)
                        print(f"🎵 酷我听书音频URL: {audio_url[:100]}...")
                        print(f"🎵 实际格式: {actual_format}, 比特率: {actual_bitrate}kbps")
                        if not file_path.endswith(actual_extension):
                            old_file_path = file_path
                            file_path = os.path.splitext(file_path)[0] + actual_extension
                            print(f"🔄 更新文件后缀: {os.path.basename(old_file_path)} -> {os.path.basename(file_path)}")
                    else:
                        audio_url = None
                        print(f"❌ 酷我听书下载信息获取失败")
                elif self.platform == '网易云听书':
                    print(f"🎧 获取网易云听书音频URL...")
                    download_info = download_manager.get_download_info(chapter_id, 'exhigh')
                    if download_info and download_info.get('url'):
                        audio_url = download_info['url']
                        actual_extension = download_info.get('extension', '.mp3')
                        if not file_path.endswith(actual_extension):
                            old_file_path = file_path
                            file_path = os.path.splitext(file_path)[0] + actual_extension
                            print(f"🔄 更新文件后缀: {os.path.basename(old_file_path)} -> {os.path.basename(file_path)}")
                        print(f"🎧 网易云听书音频URL: {audio_url[:100]}...")
                    else:
                        audio_url = None
                        print(f"❌ 网易云听书下载信息获取失败")
                elif self.platform == '荔枝FM':
                    audio_url = (
                        chapter.get('audio_url')
                        or chapter.get('mediaUrl')
                        or chapter.get('downloadUrl')
                        or chapter.get('url')
                        or download_manager.get_audio_url(self.album_id, chapter_id)
                    )
                    if audio_url:
                        print(f"🍥 荔枝FM音频URL: {audio_url[:100]}...")
                    else:
                        print("❌ 荔枝FM音频URL获取失败")
                else:
                    print(f"❌ 不支持的平台: {self.platform}")
                    chapter['_error'] = f'不支持的平台: {self.platform}'
                    return False
            except TimeoutError:
                print(f"❌ 获取音频链接超时")
                chapter['_error'] = '获取音频链接超时'
                return False
            except Exception as e:
                # 懒人听书限速/风控错误透传给上层重试逻辑
                try:
                    from core.lrts_manager import RateLimitError as _RLE, IllegalRequestError as _ILE
                    if isinstance(e, (_RLE, _ILE)):
                        raise
                except ImportError:
                    pass
                print(f"❌ 获取音频链接失败: {str(e)[:100]}")
                chapter['_error'] = f'获取音频链接失败: {str(e)[:50]}'
                import traceback
                traceback.print_exc()
                return False

            if not audio_url:
                print(f"❌ 无法获取音频链接（URL为空）")
                chapter['_error'] = '无法获取音频链接（URL为空）'
                return False

            # ---- 下载音频文件 ----
            success = False
            try:
                if self.platform == '喜马拉雅':
                    progress_callback = self._make_progress_callback(chapter_index)
                    success = download_manager.download_audio(
                        str(chapter_id), file_path, self.quality, progress_callback=progress_callback
                    )
                    if not success and audio_url:
                        if str(self.quality).startswith('M4A 96'):
                            print("WARN: high-quality Ximalaya path failed; skip low-bitrate URL fallback")
                        else:
                            print("WARN: track_id download failed; falling back to resolved URL")
                            success = download_manager.download_audio(
                                audio_url, file_path, self.quality, progress_callback=progress_callback
                            )
                elif self.platform == '番茄畅听':
                    print(f"🍅 番茄畅听下载（CENC管线优先）...")
                    voice_for_download = self.voice_config or '无损真人录制'
                    changting_chapter_id = str(chapter_id).replace("chapter-", "", 1)
                    if hasattr(download_manager, 'download_changting_chapter'):
                        success = download_manager.download_changting_chapter(
                            changting_chapter_id, voice_for_download, file_path, self.quality
                        )
                    else:
                        success = False
                    if not success:
                        print(f"🔄 CENC管线失败，回退普通下载...")
                        success = download_manager.download_audio(
                            audio_url, file_path,
                            progress_callback=self._make_progress_callback(chapter_index),
                        )
                elif self.platform == '懒人听书':
                    success = download_manager.download_audio(
                        audio_url, file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                elif self.platform == '云听FM':
                    print(f"☁️ 下载云听FM音频中...")
                    import requests as _requests
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    response = _requests.get(audio_url, stream=True, timeout=(10, 90))
                    response.raise_for_status()
                    total_size = int(response.headers.get('Content-Length') or 0)
                    downloaded_size = 0
                    progress_callback = self._make_progress_callback(chapter_index)
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=262144):
                            if chunk:
                                f.write(chunk)
                                downloaded_size += len(chunk)
                                progress_callback(downloaded_size, total_size)
                    success = True
                    print(f"✅ 云听FM音频下载成功")
                elif self.platform == '起点听书':
                    print(f"📖 下载起点听书音频中...")
                    success = download_manager.download_qidian_audio(
                        audio_url, file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                elif self.platform == '蜻蜓FM':
                    print(f"🎧 下载蜻蜓FM音频中...")
                    success = download_manager.download_audio(
                        book_id=self.album_id,
                        program_id=chapter_id,
                        title=chapter_title,
                        save_path=file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                elif self.platform == '酷我听书':
                    print(f"🎵 下载酷我听书音频中...")
                    success = download_manager.download_audio(
                        audio_url, file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                elif self.platform == '网易云听书':
                    print(f"🎧 下载网易云听书音频中...")
                    success = download_manager.download_audio(
                        audio_url, file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                elif self.platform == '荔枝FM':
                    print(f"🍥 下载荔枝FM音频中...")
                    success = download_manager.download_audio(
                        audio_url, file_path,
                        progress_callback=self._make_progress_callback(chapter_index),
                    )
                else:
                    print(f"❌ 不支持的平台: {self.platform}")
                    chapter['_error'] = f'不支持的平台: {self.platform}'
                    return False
            except TimeoutError:
                print(f"❌ 下载超时")
                chapter['_error'] = '下载超时'
                return False
            except ConnectionError as e:
                print(f"❌ 网络连接失败: {str(e)[:100]}")
                chapter['_error'] = f'网络连接失败: {str(e)[:50]}'
                return False
            except Exception as e:
                print(f"❌ 下载失败: {str(e)[:100]}")
                chapter['_error'] = f'下载失败: {str(e)[:50]}'
                import traceback
                traceback.print_exc()
                return False

            if success:
                self._dbg(f"✅ 下载成功: {chapter_title}")
                return True
            else:
                print(f"❌ 下载失败: {chapter_title}")
                chapter['_error'] = chapter.get('_error') or '下载失败（原因未知）'
                return False

        except Exception as e:
            print(f"   ❌ [线程 {thread_name}] 下载章节时出错: {e}")
            return False

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _clean_chapter_title(self, title: str) -> str:
        """保持章节标题与UI显示完全一致，不进行任何清理"""
        return title if title else "未知章节"

    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        filename = str(filename or "").strip() or "未知"
        for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
            filename = filename.replace(char, '_')
        if len(filename) > 200:
            filename = filename[:200]
        return filename
