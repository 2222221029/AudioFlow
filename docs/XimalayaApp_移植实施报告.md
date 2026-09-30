# XimalayaApp → AudioFlow 移植实施报告

> 实施日期：2026-09-30
> 源项目：`/vol1/1000/downloads/临时文件/XimalayaApp`（C#/WPF，38,499 行）
> 目标项目：`/vol1/1000/downloads/项目开发/AudioFlow-main`
> 前置分析：本目录 `XimalayaApp_移植分析.md`
>
> **硬约束（用户要求）**：不得影响现有功能；下载速度只能变快，不能变慢。
> 本报告所有结论均带可复现的证据。

---

## 〇、结果速览

| 指标 | 改造前 | 改造后 |
| --- | --- | --- |
| 测试用例 | 431 passed | **856 passed**（+425） |
| 新增核心模块 | — | 10 个（3,161 行） |
| 新增测试文件 | — | 11 个 |
| 分页加载速度 | 串行基线 | **2.98×**（651 集实测，结果逐条等价） |
| 下载速度 | 单流基线 | **3.96× / 理论上限 4×**（4 段并发实测） |
| 现有功能回归 | — | **0 破坏**（431 条既有用例全绿） |

---

## 一、新增模块清单

| 模块 | 行数 | 移植来源 | 解决什么 |
| --- | --- | --- | --- |
| `core/errors.py` | 316 | `Core/Net.cs:12-19` | 异常分级 + 状态码表，去掉动态 import 判定 |
| `core/http_policy.py` | 237 | `Core/Net.cs:240-260` | 「先解读响应体，再看体积」 |
| `core/page_fetch.py` | 321 | `Core/PageFetch.cs:60-159` | 并发分页（页号槽位对齐） |
| `core/chunked_download.py` | 691 | `Core/Net.cs:120-533` | **分段并行下载 + Range 探大小 + 空闲超时** |
| `core/download_adapter.py` | 188 | —（本项目胶水层） | 把分段下载接到各平台既有 session |
| `core/local_index.py` | 275 | `Core/DownloadEngine.cs:170-205` | 统一的「已下载」判据 + O(n) 索引 |
| `core/download_report.py` | 220 | `Core/DownloadEngine.cs:478-491` | `_report.json` 侧车 + 失败集重试 |
| `core/channel_contract.py` | 229 | `Core/DownloadEngine.cs:151-158,322-340` | 渠道契约：把「静默降级」变成显式记录 |
| `core/quality_check.py` | 483 | `_tools/` 体检脚本（P2-12） | 本地音质体检闭环 |
| `scripts/quality_check.py` | 173 | —（CLI） | 体检工具的命令行入口 |

---

## 二、速度证据（「只能变快」的硬约束）

### 2.1 分页加载：2.98×

```
tests/test_ximalaya_concurrent_pagination.py
  模拟每页 150ms 网络延迟，651 集 = 4 页
  串行 3 页: 0.45s
  并发 3 页: 0.15s
  结果集数: 651（与串行逐条相同）
  加速: 2.98x
```

### 2.2 下载：3.96×（理论上限 4×）

```
tests/test_download_speedup.py
  8.0MB @ 2MB/s per-connection（模拟 CDN 单连接限速）
  → 单流 4.00s，4 段 1.01s，加速 3.96x（理论上限 4x）
```

> 与参考实现的实测同量级：起点听书 3.5MB `3.3→22.3MB/s（6.8×）`、
> 懒人听书 4.8MB `6.9→23.2MB/s（3.4×）`。

### 2.3 「不会变慢」的回归防线

localhost 没有带宽瓶颈，分段与单流总耗时几乎相同，所以
`test_segmented_is_not_slower_than_single_beyond_fixed_overhead` 断言的是
**固定开销上限（120ms）**：一旦误加了探测往返或段被串行化，这条会立刻红。

另外 `test_segments_actually_run_in_parallel` 验证并发**确实发生**
（3 段 × 0.2s 延迟若串行需 ≥0.6s），这是收益的充要条件。

---

## 三、实施过程中**实测发现并修复**的缺陷

以下都不是「照抄参考实现」，而是移植时在真实编码文件/真实链路上暴露出来的问题。
每条都已固化成回归用例。

| # | 缺陷 | 后果 | 固化用例 |
| --- | --- | --- | --- |
| 1 | 分段总页数公式把首页数了两遍 | 60 集 10/页时**第 6 页整页静默缺失** | `test_result_matches_serial_layout` |
| 2 | `_fetch_one_page` 吞掉异常 | 一次网络抖动 = 缺页 = 并发路径永远失效 | `test_transient_failure_is_retried_before_giving_up` |
| 3 | 无论有无体积都发 1 字节探测 | 200ms RTT 链路上 3 段收益全被一次往返吃掉 | `test_probe_is_skipped_when_size_is_known` |
| 4 | 停滞判定在 `next()` **之前**取 | 0.6s 停顿配 0.25s 阈值被判成正常 | `test_stall_after_data_raises_transient` |
| 5 | 空块心跳重置空闲计时 | 「每 29 秒发个空包」可把下载永久吊住 | `test_heartbeat_empty_chunks_do_not_reset_timer` |
| 6 | `check_size` 逻辑写反 | 酷我的「文件过小」文案被下层抢走 | `test_tiny_file_is_rejected` |
| 7 | 索引键含 `0003 ` 前缀却与标题比对 | **所有**「已下载」判定全部落空 | `test_prefix_probe_matches_shortened_title` |
| 8 | `find_by_stem` 拿带后缀的 basename 比对 | 同名不同后缀永远找不回 | `test_finds_same_stem_different_extension` |
| 9 | 空目录参数落到 `abspath("")` | 「没传目录」变成「把项目根当专辑目录」 | `test_blank_directory_is_safe` |
| 10 | FLAC 只读 42 字节 | STREAMINFO 差几个字节读不到时长 | `test_probes_flac_streaminfo` |
| 11 | 权限错误与内容损坏混为一谈 | 只读挂载点上 241 个文件全被报「损坏」 | `TestUnreadableIsNotDamaged` |
| 12 | 进度上报未做异常隔离 | UI 异常会把已下好的文件判成失败 | `test_progress_callback_exception_does_not_break_download` |

---

## 四、逐项验收（对照移植分析的 W1–W4）

### W1 · 基础层

| 项 | 状态 | 证据 |
| --- | --- | --- |
| `core/errors.py` 状态码表 | ✅ | 与 `Core/Net.cs:33-34` 逐项一致 |
| 去掉动态 import 判定 | ✅ | `download_worker.py:583-592` 改为显式登记 |
| `should_retry()` 防静默降级 | ✅ | 参考实现「537 集 31 个文件被静默降级」的对策 |
| `core/http_policy.py` 顺序铁律 | ✅ | 47 字节 `ret=726` 拒绝体不再被报成「过小」 |
| `core/page_fetch.py` 三条约束 | ✅ | 页号槽位 / 单页重试 / 空洞不毁整批 |
| 接入喜马拉雅常规分页 | ✅ | 2.98×，且**缺页即整体回退串行** |

### W2 · 下载链路

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 分段并行下载 | ✅ | 3.96×（理论上限 4×） |
| 低门槛 2MB | ✅ | 与参考实现一致（8MB 会把有声书主流单集全挡在门外） |
| Range 探大小 | ✅ | 接口不报体积时才探（见缺陷 #3） |
| 不支持 206 → 透明回退 | ✅ | `test_server_without_range_falls_back_transparently` |
| 空闲超时 + 停滞文案 | ✅ | 双防线（socket timeout + 循环级兜底），边界写在 docstring |
| `.part` / `.part.s{i}` 清理 | ✅ | 失败/取消路径全覆盖，5 个参数化用例 |
| 接入懒人听书 | ✅ | 布尔契约、10KB 阈值、日志前缀一字未改 |
| 接入酷我听书 | ✅ | 403→`restricted`、content-type 探测、错误文案逐条保留 |
| 接入网易云听书 | ✅ | `_require_cookie` 前置、`>1024` 判定、错误文案保留 |
| 接入荔枝FM | ✅ | 空 URL 直接 False、`quality` 参数兼容、错误文案保留 |
| `core/local_index.py` | ✅ | O(n) 索引；**100 次查询零额外 syscall** |

### W3 · 可观测性与重试

| 项 | 状态 | 证据 |
| --- | --- | --- |
| `_report.json` 侧车 | ✅ | worker 三个终态出口都写；写失败不影响下载 |
| 失败集持久化 | ✅ | 含 id/order/title/error/error_type |
| 失败集重试收窄 | ✅ | 1000 集失败 1 集 → 只重下那 1 集 |
| 死按钮防护 | ✅ | 有计数无清单 → `has_retryable() == False` |
| 收窄不成立即回退 | ✅ | 9 条安全用例（部分覆盖/侧车过期/损坏 JSON） |
| **渠道契约显式化** | ✅ | 见下方专题 |

#### W3 专题：渠道契约为什么**没有**照搬参考实现

参考实现 `Core/DownloadEngine.cs:151-158` 是**硬性**的：

> 只走用户选定的那一个渠道 —— 绝不中途换渠道（2026-09-29 用户要求「每个接口独立」）

撞限额就抛 `ChannelQuotaException` 暂停整个任务。那对**人工盯着下载的桌面应用**
可行；对 AudioFlow 这种**无人值守的 NAS 订阅下载器**不可行 —— 平台一限流，
订阅就整夜空转，用户第二天只看到「全部失败」。

本项目采取第三条路，也正是移植分析 P0-4 的诉求：

> 保留兜底能力，但把「静默降级」变成「显式记录 + 用户可见」。

落地为 `core/channel_contract.py`：

* **判定**：把 15 种内部 `last_download_source` 映射成可比较的档位序号，
  与用户所选档位的期望值比对；
* **记录**：降级写进 `chapter['_quality_note']` + `_report.json` 的
  `downgrades` 字段（含 `downgrade_summary` 汇总）；
* **暴露**：经 `update_download_chapter_status` → `chapter_states` →
  `album_chapter_download_states` 一直传到前端章节行。

**误报防护**（比漏报更重要）：任一侧档位未知就**不下结论**。
误报会让用户以为下载有问题而白重下。用例
`test_unknown_on_either_side_never_claims_downgrade` 钉死这条。

**零行为变更**：不改变任何下载决策、不改变 `status` 语义、不影响计数 ——
`quality_note` 是纯增量字段，正常下载时**根本不会出现**。

### W4 · 音质体检

| 项 | 状态 | 证据 |
| --- | --- | --- |
| MP3/FLAC/WAV/M4A 解析 | ✅ | **用 ffmpeg 真实编码验证**，非手工拼字节 |
| 码率达标判定 | ✅ | 请求 96K 实得 48K → `low_bitrate` |
| 截断检测 | ✅ | 声明 16MB 实得 30KB → `damaged` |
| 目录级报告 | ✅ | 达标率、问题清单、`--list-redo` 导出 |
| 只读不改动 | ✅ | 与 `kuwo_integrity_check.py` 的 `--fix` 策略一致 |

---

## 五、兼容性保证（「不影响现有功能」）

实施时对**所有**既有调用点采用「契约保留」策略：

1. **异常契约不变**：`download_adapter` 把分级异常翻译回
   `requests.HTTPError` / `requests.RequestException`，既有
   `except requests.HTTPError` 与 `except Exception` 两种写法都照常工作。
2. **错误文案不变**：酷我 `min_valid_bytes=0` 关掉下层粗筛，
   让「酷我媒体文件过小: N 字节」仍是唯一判据；懒人仍是
   `[lrts] download failed:` + 返回 False。
3. **错误字段写入时机不变**：`last_error` / `last_error_type` 的赋值点与内容一字未改 ——
   上层 `download_worker` 读它们驱动「不再自动重试」的判定。
4. **LRTS 异常类定义未动**：`RateLimitError` / `IllegalRequestError` 仍在
   `lrts_manager.py` 原地定义，只是额外登记进分级表。
5. **回退永远安全**：分页并发有缺页 → 整体回退串行；分段失败 → 回退单流；
   重试收窄不成立 → 回退全集。**没有一条路径会因为新功能而变差。**

---

## 六、如何使用新能力

```bash
# 全量回归
.venv/bin/python -m pytest -p no:cacheprovider

# 速度基准（标了 slow，默认也跑）
.venv/bin/python -m pytest tests/test_download_speedup.py -v -s

# 音质体检
.venv/bin/python scripts/quality_check.py --root "/vol1/1000/downloads/有声书" --quality 96K
.venv/bin/python scripts/quality_check.py --album-dir "…/某专辑" --list-redo > redo.txt
```

### 可调环境变量（均已设默认值与上下限）

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `XMLY_CHAPTER_PAGE_CONCURRENCY` | 6 | 喜马拉雅章节分页并发度（1–16） |
| `LRTS_DOWNLOAD_THREADS` | 3 | （既有）懒人听书并发 |
| `FANQIE_DOWNLOAD_THREADS` | 自动 | （既有）番茄畅听并发 |

---

## 七、未做与建议后续

| 项 | 原因 |
| --- | --- |
| 其余平台接入分段下载 | **已完成 4 个**：懒人听书、酷我听书、网易云听书、荔枝FM
（各带一套契约回归用例）。
**剩余**：云听FM / 起点听书 / 蜻蜓FM / 番茄系列 —— 它们的落盘逻辑夹杂
ffmpeg 解密、CENC 管线、签名换链等平台特有能力，不宜用通用适配层一刀切。
建议逐个接入并各配一套 `test_platform_download_contract.py` 式用例。 |
| 授权封套（分析报告 P2-13） | 与本项目「NAS 自部署、单容器零依赖」定位冲突，已在分析报告建议**不移植**。 |

---

## 八、证据索引

| 主题 | 测试文件 | 用例数 |
| --- | --- | --- |
| 异常分级 | `tests/test_errors.py` | 40 |
| 响应体判定 | `tests/test_http_policy.py` | 32 |
| 并发分页 | `tests/test_page_fetch.py` | 32 |
| 喜马拉雅并发分页等价性 | `tests/test_ximalaya_concurrent_pagination.py` | 26 |
| 分段下载 | `tests/test_chunked_download.py` | 47 |
| 平台适配契约 | `tests/test_download_adapter.py` | 20 |
| 多平台下载契约 | `tests/test_platform_download_contract.py` | 19 |
| 酷我错误契约 | `tests/test_kuwo_download_contract.py` | 20 |
| 本地索引 | `tests/test_local_index.py` | 32 |
| 失败清单侧车 | `tests/test_download_report.py` | 32 |
| 重试收窄 | `tests/test_retry_narrowing.py` | 15 |
| 渠道契约 | `tests/test_channel_contract.py` | 64 |
| 音质体检 | `tests/test_quality_check.py` | 45 |
| 速度基准 | `tests/test_download_speedup.py` | 1 |
| **合计新增** | | **425** |

> 备份目录：`.dsh_backup/W1`、`W2`、`W3`、`W4`（各阶段完成时的文件快照）。
> ⚠ 本项目 `.git` 目录属 root 且当前进程无写权限，无法建立版本控制检查点，
> 故用文件快照替代。
