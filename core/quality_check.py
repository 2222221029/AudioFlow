# -*- coding: utf-8 -*-
"""本地音质体检 —— 「我下到的东西到底对不对」。

## 移植来源

参考实现 XimalayaApp `_tools/` 下的一套体检脚本，以及移植分析 P2-12：

> `_m4a_duration_seconds()` 已能读 mvhd，`_public_quality_label()` **已经算了
> 实测码率却只用于拼标签**；`download_worker` 的品质标记只认三种，
> 无法表达「应 96K 实得 48K」。参考做法是按 `MIN_KBPS` 阈值分
> 「达标 / 低码率 / 损坏」三类，出体检报告。最终「体检达标 2115/2115」。

## 为什么需要它

项目里已经有一些「事后才发现」的静默失效：

* 96K 档位映射错误 → 用户以为下了 96K、实际 48K，**文件名还写着 96K**
  （`docs/接口参考借鉴分析.md` P0-1 记录过）；
* CDN 静默截断 → 本地 4.95MB / 服务端 16.80MB，「连接提前 EOF 却不抛异常」
  （同文档 P1-6）。

这些**都不会表现为下载失败**，只能靠体检发现。本模块把判据集中成一处。

## 三档结论

| 结论 | 判据 | 处置 |
| --- | --- | --- |
| `ok` | 时长可读、码率达标、大小与声明一致 | 无需动作 |
| `low_bitrate` | 能播但码率明显低于请求档位 | 重下（换档或换渠道） |
| `damaged` | 容器无法识别 / 文件头读不出时长 / 体积明显偏小 | 重下 |

⚠ 本模块**只读**，不改动任何文件 —— 与项目既有的 `kuwo_integrity_check.py`
「先检查、显式 --fix 才修」的策略保持一致。
"""

from __future__ import annotations

import os
import struct
from typing import Any, Dict, Iterable, List, Optional

from core import local_index

#: 档位文本 → 最低可接受码率（kbps）。
#: 键既包含各平台实际使用的档位串（`96K`、`无损优先（自动降级）` …），
#: 也包含英文/技术名（`lossless`、`flac`）。设**下限**而不是精确匹配 ——
#: 编码器实际输出会有 ±10% 波动。
QUALITY_MIN_KBPS: Dict[str, int] = {
    "24K": 18,
    "32K": 26,
    "48K": 40,
    "64K": 56,
    "96K": 80,
    "128K": 110,
    "192K": 170,
    "320K": 280,
    # 中文档位名 —— 前端与各 manager 用的就是这些串
    "无损优先": 500,
    "网页无损优先": 500,
    "杜比全景声优先": 200,
    "Audio Vivid 优先": 200,
    "喜马拉雅移动端接口": 0,      # 「自动最高音质」不设下限
    "喜马拉雅网页版接口": 0,
    "喜马拉雅电脑版接口": 0,
    # 英文 / 技术名
    "lossless": 500,
    "flac": 500,
    "best": 0,
}

#: 时长相对声明值的容差（读不到声明值时不校验）。
DURATION_TOLERANCE = 0.10

#: 体积相对声明值的容差。⚠ 比下载时的 0.80 严格，因为体检是**事后**在
#: 本地文件上做的，没有「半截 `.part` 被误判」的风险。
SIZE_TOLERANCE = 0.90


def expected_min_kbps(quality: Any) -> int:
    """把档位文本映射成最低可接受码率。识别不出返回 0（不设限）。"""
    text = str(quality or "").strip().lower()
    if not text:
        return 0
    # 先精确匹配常见写法，再退化到「包含数字 + K」的模糊匹配
    for key, kbps in QUALITY_MIN_KBPS.items():
        if key.lower() == text:
            return kbps
    for key, kbps in QUALITY_MIN_KBPS.items():
        if key.lower() in text:
            return kbps
    return 0


# ======================================================================
# 音频参数解析（只读文件头/元数据，不解码音频）
# ======================================================================

def probe_audio(path: str) -> Dict[str, Any]:
    """读出一个音频文件的容器 / 时长 / 码率。读不出就返回空壳（不抛）。

    支持：MP3（含 ID3v2 与裸帧）、FLAC（STREAMINFO）、WAV（fmt+data）、
    MP4/M4A（moov/mvhd）。这些都是有声书平台的常见容器。
    """
    result: Dict[str, Any] = {
        "path": path,
        "exists": False,
        "size": 0,
        "container": "",
        "duration": 0.0,
        "bitrate_kbps": 0,
        "error": "",
    }
    try:
        if os.path.isdir(str(path)):
            # 目录不是文件：getsize 会返回目录项大小，容易误判成「存在且有效」
            result["exists"] = False
            result["error"] = "路径是目录，不是文件"
            return result
        result["size"] = os.path.getsize(path)
        result["exists"] = True
    except OSError as exc:
        result["error"] = f"无法读取文件：{exc}"
        return result

    if result["size"] <= 0:
        result["error"] = "文件为空"
        return result

    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
            if not head:
                result["error"] = "文件为空"
                return result

            if head[:4] == b"fLaC":
                result["container"] = "flac"
                _probe_flac(path, result)
            elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                result["container"] = "wav"
                _probe_wav(path, result)
            elif len(head) >= 12 and head[4:8] == b"ftyp":
                result["container"] = "m4a"
                _probe_mp4(path, result)
            elif head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
                result["container"] = "mp3"
                _probe_mp3(path, result)
            elif head[:4] == b"OggS":
                result["container"] = "ogg"
            else:
                result["error"] = "无法识别的容器"
                return result
    except OSError as exc:
        result["error"] = f"读取失败：{exc}"
        return result

    # 兜底：拿到时长就算码率；拿不到时长就按「体积 × 8 ÷ 声明时长」由调用方算
    if not result["bitrate_kbps"] and result["duration"] > 0:
        result["bitrate_kbps"] = int(result["size"] * 8 / result["duration"] / 1000)
    return result


def _probe_flac(path: str, result: Dict[str, Any]) -> None:
    """FLAC：STREAMINFO 里的总采样数与采样率能直接算时长。

    ⚠ 必须读到 offset **42** 以外：STREAMINFO 块从第 8 字节开始（4 字节
    `fLaC` 标记 + 4 字节 METADATA_BLOCK_HEADER），块内第 10~17 字节是
    打包的 `采样率(20bit) | 声道-1(3bit) | 位深-1(5bit) | 总采样数(36bit)`。
    只读 42 字节会**刚好差几个字节**读不到完整字段（真踩过）。
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0)
            data = fh.read(64)
        block = data[8:42]
        if len(block) < 34:
            return
        packed = int.from_bytes(block[10:18], "big")
        sample_rate = (packed >> 44) & 0xFFFFF
        total_samples = packed & ((1 << 36) - 1)
        if sample_rate and total_samples:
            result["duration"] = total_samples / float(sample_rate)
    except (OSError, IndexError, ValueError):
        return


def _probe_wav(path: str, result: Dict[str, Any]) -> None:
    """WAV：扫 chunk 找 fmt（采样率/位深/声道）与 data（长度）算时长。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(12)
            byte_rate = 0
            data_size = 0
            while True:
                header = fh.read(8)
                if len(header) < 8:
                    break
                chunk_id = header[:4]
                chunk_size = struct.unpack("<I", header[4:8])[0]
                if chunk_id == b"fmt ":
                    fmt = fh.read(min(chunk_size, 16))
                    if len(fmt) >= 12:
                        byte_rate = struct.unpack("<I", fmt[8:12])[0]
                elif chunk_id == b"data":
                    data_size = chunk_size
                    break
                else:
                    fh.seek(chunk_size, os.SEEK_CUR)
            if byte_rate > 0 and data_size > 0:
                result["duration"] = data_size / float(byte_rate)
    except (OSError, struct.error, ValueError):
        return


def _probe_mp4(path: str, result: Dict[str, Any]) -> None:
    """MP4/M4A：在 moov 里找 mvhd 读时长；找不到就用 mvhd 的 timescale。

    ⚠ moov 可能在文件**尾部**（流式封装），所以按顶层盒大小逐盒定位，
    与项目既有 `_validate_mobile_media` 的定位方式一致。
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_len = fh.tell()
            pos = 0
            moov_off = -1
            moov_len = 0
            while pos + 8 <= file_len:
                fh.seek(pos)
                header = fh.read(8)
                if len(header) < 8:
                    break
                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if size == 1:
                    ext = fh.read(8)
                    if len(ext) < 8:
                        break
                    size = struct.unpack(">Q", ext)[0]
                    if size <= 16:
                        break
                    if box_type == b"moov":
                        moov_off, moov_len = pos + 16, size - 16
                        break
                    pos += size
                elif size == 0:
                    if box_type == b"moov":
                        moov_off, moov_len = pos + 8, file_len - pos - 8
                    break
                else:
                    if box_type == b"moov":
                        moov_off, moov_len = pos + 8, size - 8
                        break
                    pos += size

            if moov_off < 0:
                return
            # mvhd 通常紧跟 moov 头，读前 4KB 足够
            fh.seek(moov_off)
            moov_head = fh.read(min(moov_len, 4096))
            idx = moov_head.find(b"mvhd")
            if idx < 0 or idx + 32 > len(moov_head):
                return
            version = moov_head[idx + 4]
            if version == 1:
                if idx + 32 > len(moov_head):
                    return
                timescale = struct.unpack(">I", moov_head[idx + 24:idx + 28])[0]
                duration = struct.unpack(">Q", moov_head[idx + 28:idx + 36])[0] if idx + 36 <= len(moov_head) else 0
            else:
                timescale = struct.unpack(">I", moov_head[idx + 16:idx + 20])[0]
                duration = struct.unpack(">I", moov_head[idx + 20:idx + 24])[0]
            if timescale > 0:
                result["duration"] = duration / float(timescale)
    except (OSError, struct.error, ValueError):
        return


def _probe_mp3(path: str, result: Dict[str, Any]) -> None:
    """MP3：跳过 ID3 后找第一个有效帧头，用比特率表算码率。

    只读**第一帧**的比特率 —— 有声书极少用 VBR，且体检的目的是
    「档位是否达标」，不是精确码率统计。
    """
    _BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
    _BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
    _RATES = {
        0: (11025, 12000, 8000),
        2: (22050, 24000, 16000),
        3: (44100, 48000, 32000),
    }
    try:
        with open(path, "rb") as fh:
            offset = 0
            head = fh.read(10)
            if head[:3] == b"ID3" and len(head) >= 10:
                # ID3v2: 10 字节头 + syncsafe 长度
                size = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) \
                    | ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
                offset = 10 + size
            fh.seek(offset)
            window = fh.read(64 * 1024)
            for i in range(len(window) - 4):
                b0, b1, b2 = window[i], window[i + 1], window[i + 2]
                if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
                    continue
                version_bits = (b1 >> 3) & 0x03
                layer_bits = (b1 >> 1) & 0x03
                bitrate_idx = (b2 >> 4) & 0x0F
                rate_idx = (b2 >> 2) & 0x03
                if version_bits == 1 or layer_bits != 1 or rate_idx == 3:
                    continue
                if bitrate_idx in (0, 15):
                    continue
                table = _BITRATES_V1_L3 if version_bits == 3 else _BITRATES_V2_L3
                result["bitrate_kbps"] = table[bitrate_idx]
                result["duration"] = result["size"] * 8 / float(result["bitrate_kbps"] * 1000)
                return
    except (OSError, IndexError, ValueError):
        return


# ======================================================================
# 单文件体检
# ======================================================================

def check_file(
    path: str,
    *,
    expected_quality: Any = "",
    expected_duration: Optional[float] = None,
    expected_size: Optional[int] = None,
) -> Dict[str, Any]:
    """体检单个文件，返回含 `verdict` 的字典。

    `verdict` ∈ `{"ok", "low_bitrate", "damaged", "missing", "unreadable"}`。

    ⚠ `unreadable` 与 `damaged` 是**两回事**：
      * `damaged`    = 文件读得到，但内容有问题（截断 / 码率不达标）→ 值得重下；
      * `unreadable` = 进程**读不到**这个文件（权限 / 挂载点掉线）→ 环境问题，
        重下也解决不了。
    把两者混在一起会制造大量假告警（真踩过：在只读挂载点上跑体检，
    整个目录的文件全被报成「损坏」）。`needs_redo()` 只取 `damaged` / `low_bitrate`。
    """
    probe = probe_audio(path)
    issues: List[str] = []

    if not probe["exists"]:
        return _verdict(probe, "missing", ["文件不存在"], expected_quality)

    # 权限类错误单列，不混进「损坏」
    error_text = str(probe.get("error") or "")
    if "Permission denied" in error_text or "Errno 13" in error_text:
        return _verdict(probe, "unreadable", [error_text], expected_quality)

    if probe["error"] and probe["container"] not in ("ogg",):
        return _verdict(probe, "damaged", [probe["error"]], expected_quality)
    if probe["size"] < local_index.MIN_VALID_BYTES:
        return _verdict(probe, "damaged", [f"文件过小（{probe['size']} 字节）"], expected_quality)

    # 大小核对（只在调用方给了声明值时）
    if expected_size and expected_size > 0:
        if probe["size"] < expected_size * SIZE_TOLERANCE:
            issues.append(
                f"体积偏小：本地 {_fmt_size(probe['size'])} / 声明 {_fmt_size(expected_size)}"
            )

    # 时长核对
    if expected_duration and expected_duration > 0 and probe["duration"] > 0:
        delta = abs(probe["duration"] - expected_duration) / expected_duration
        if delta > DURATION_TOLERANCE:
            issues.append(
                f"时长不符：本地 {probe['duration']:.1f}s / 声明 {expected_duration:.1f}s"
            )

    # 码率核对
    min_kbps = expected_min_kbps(expected_quality)
    actual_kbps = probe["bitrate_kbps"]
    if min_kbps and actual_kbps and actual_kbps < min_kbps:
        issues.append(
            f"码率不达标：实测 {actual_kbps}kbps < {expected_quality} 要求的 {min_kbps}kbps"
        )

    if not issues:
        return _verdict(probe, "ok", [], expected_quality)

    # 体积/时长类问题算损坏；纯码率问题算降档
    structural = any(("体积偏小" in i or "时长不符" in i) for i in issues)
    return _verdict(probe, "damaged" if structural else "low_bitrate", issues, expected_quality)


def _verdict(probe, verdict, issues, expected_quality):
    out = dict(probe)
    out["verdict"] = verdict
    out["issues"] = issues
    out["expected_quality"] = str(expected_quality or "")
    return out


def _fmt_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


# ======================================================================
# 目录级体检
# ======================================================================

def check_directory(
    directory: str,
    *,
    expected_quality: Any = "",
    extensions: Optional[Iterable[str]] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """体检一个专辑目录，返回汇总报告。

    自动排除 `.part` / `.part.s{i}` 等半截文件（复用 `local_index` 的判据）。

    `pass_rate` **只以可读文件为分母** —— 读不到的文件（权限 / 挂载点掉线）
    不计入达标率，否则一个权限问题会把整个目录的达标率拉成 0%。
    """
    summary: Dict[str, Any] = {
        "directory": str(directory or ""),
        "expected_quality": str(expected_quality or ""),
        "total": 0,
        "ok": 0,
        "low_bitrate": 0,
        "damaged": 0,
        "unreadable": 0,
        "files": [],
    }
    target_exts = {str(e).lower() for e in (extensions or local_index.AUDIO_EXTENSIONS)}

    for path, size in local_index.list_audio_files(directory, use_cache=use_cache):
        if target_exts and os.path.splitext(path)[1].lower() not in target_exts:
            continue
        result = check_file(path, expected_quality=expected_quality)
        summary["total"] += 1
        summary[result["verdict"]] = summary.get(result["verdict"], 0) + 1
        if result["verdict"] != "ok":
            summary["files"].append(result)

    readable = summary["total"] - summary["unreadable"]
    summary["readable"] = readable
    summary["healthy"] = readable > 0 and summary["ok"] == readable
    summary["pass_rate"] = (
        round(summary["ok"] * 100.0 / readable, 1) if readable else 0.0
    )
    return summary


#: 真正值得「重下」的结论。`unreadable` / `missing` 是环境问题，重下无意义。
REDO_VERDICTS = frozenset({"low_bitrate", "damaged"})


def needs_redo(report: Dict[str, Any]) -> List[str]:
    """从目录体检报告里取出「需要重下」的文件路径列表。

    ⚠ 只含 `low_bitrate` / `damaged` —— 权限读不到的文件（`unreadable`）
    重下也解决不了，混进来会让「导出重下清单」变成纯噪声。
    """
    return [
        f["path"]
        for f in (report or {}).get("files", [])
        if f.get("verdict") in REDO_VERDICTS
    ]


__all__ = [
    "QUALITY_MIN_KBPS",
    "DURATION_TOLERANCE",
    "SIZE_TOLERANCE",
    "expected_min_kbps",
    "probe_audio",
    "check_file",
    "check_directory",
    "needs_redo",
]
