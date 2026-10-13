#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""喜马拉雅 V4 限流（ret=1001）· 同账号换设备对照实验。

验证的假设（来自用户在另一程序上的实测）：
    V4 接口返回 ret=1001（系统繁忙）后，**同一个账号**使用全新的
    device_id + Cookie + x-tk 三件套可以立即继续下载。

实验设计（三组对照，缺一不可）：
    阶段 1  压测      用固定三件套 A 连续请求 baseInfo 直到 ret=1001
    阶段 2  换设备    1001 出现后立即换全新 device_uuid B（重签 x-tk）请求
    阶段 3  同设备对照 切回三件套 A 立即请求 —— 排除「限流刚好到期」

结论判定：
    B 成功 + A 同时刻失败  →  限流按【设备】维度，轮换有效
    B 失败 + A 也失败      →  限流按【账号/IP】维度，轮换无效
    B 失败、A 等待后成功   →  限流按【时间】维度，轮换只是碰巧赶上冷却

三件套的构造（与 core/ximalaya_universal_login.py 完全一致）：
    Cookie   1&_device=android&{uuid}&9.4.52; 1&_token={uid}&{hex}; channel=and-d12; ...
    x-tk     generate_mobile_ticket() 用 device_id + uid 本地签（每次全新）
    UA       ting_9.4.52(V2059A,Android33)（与 Cookie 的 app_version 同源）
    sign     与 device_id 无关（只含 device 参数 android），换设备不用重算

安全边界：
    * 只请求 baseInfo 元数据，绝不下载音频文件；
    * 每次请求之间有间隔（--interval），压测轮数有上限（--max-requests）；
    * 1001 是接口级软限流，不代表账号被封；但**频繁伪造新设备可能扩大账号
      风控暴露面**（core/ximalaya_credentials.py 的注释明确持保守立场），
      请用小号或自担风险。

用法：
    python3 scripts/test_v4_device_rotation.py --token "1234567890&abcdef1234567890abcdef1234567890"
    python3 scripts/test_v4_device_rotation.py --token "1&_token=1234567890&abcdef..." --interval 0.4
    python3 scripts/test_v4_device_rotation.py --device-a "<固定设备uuid>"  # 复现你另一程序的基线
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ximalaya_download_manager import XimalayaDownloadManager  # noqa: E402
from core.ximalaya_universal_login import derive_universal_credentials  # noqa: E402

DEFAULT_TRACK_ID = "539592153"  # 公开免费样本（与 check_ximalaya_free_api.py 一致）
PROBE_LEVEL = 0                 # 最低档：不涉及会员权限，ret 语义最干净


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "*/*"})
    return session


def new_bundle(token: str, device_uuid: str = "") -> dict:
    """用给定 token + 设备号派生一套全新三件套（x-tk 每次全新签发）。"""
    bundle = derive_universal_credentials(token, device_uuid=device_uuid or str(uuid.uuid4()))
    if bundle.get("ticket_error"):
        raise RuntimeError(f"本地签发 x-tk 失败：{bundle['ticket_error']}")
    return bundle


def v4_probe(session: requests.Session, bundle: dict, track_id: str, level: int = PROBE_LEVEL) -> dict:
    """发一次 V4 baseInfo，只读 ret / 是否有地址，不下载音频。"""
    timestamp = int(time.time() * 1000)
    url = XimalayaDownloadManager._mobile_v4_request_url(
        track_id, timestamp, device="android", level=level,
    )
    headers = {
        "User-Agent": bundle["mobile_ua"],
        "Accept": "*/*",
        "Cookie2": "$version=1",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "x-tk": bundle["ticket"],
        "Cookie": bundle["mobile_cookie"],
    }
    started = time.monotonic()
    out: dict = {"status": -1, "ret": "", "has_url": False, "elapsed": 0.0, "error": ""}
    try:
        resp = session.get(url, headers=headers, timeout=20)
        out["status"] = resp.status_code
        out["elapsed"] = time.monotonic() - started
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}"
            return out
        data = resp.json()
        # 成功响应没有 ret 字段，直接给 playUrlInfos 地址数组；
        # 被限时才返回 {"ret": "1001", ...}。归一化成 ret='0' 便于统一判定。
        out["ret"] = str(data.get("ret", "")).strip() if "ret" in data else ""
        out["has_url"] = bool(data.get("playUrlInfos"))
        if not out["ret"] and out["has_url"]:
            out["ret"] = "0"
    except Exception as exc:  # noqa: BLE001 - 实验脚本不因单次网络异常中断
        out["error"] = str(exc)[:120]
    return out


def short_device(bundle: dict) -> str:
    value = str(bundle.get("device_uuid") or "")
    return value[:8] + "…" if len(value) > 8 else value


def fmt(result: dict) -> str:
    if result["error"]:
        return f"ERR({result['error']})"
    if result["ret"] == "0":
        return f"ret=0{' 有地址' if result['has_url'] else ' 无地址'}"
    return f"ret={result['ret']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token", required=True, help="1&_token 的值：{uid}&{hex}，或整段 Cookie")
    parser.add_argument("--track-id", default=DEFAULT_TRACK_ID, help=f"探测用 trackId（默认免费样本 {DEFAULT_TRACK_ID}）")
    parser.add_argument("--device-a", default="", help="固定组设备 uuid（留空则随机生成；可填你另一程序的设备号）")
    parser.add_argument("--interval", type=float, default=0.5, help="压测请求间隔秒数（默认 0.5）")
    parser.add_argument("--max-requests", type=int, default=80, help="压测阶段请求上限（默认 80）")
    parser.add_argument("--level", type=int, default=PROBE_LEVEL, help="V4 trackQualityLevel（默认 0；测付费/无损用 3，杜比用 12）")
    parser.add_argument("--warmup", type=int, default=1, help="压测前先确认 ret=0 的成功次数（默认 1）")
    parser.add_argument("--recovery-rounds", type=int, default=0,
                        help="连续触发测试轮数（默认 0=关；设 3 可测冷却是否逐次升级）")
    args = parser.parse_args()

    print("=" * 68)
    print("喜马拉雅 V4 限流 · 同账号换设备对照实验")
    print("  仅请求 baseInfo 元数据，不下载音频；请自担账号风控风险")
    print("=" * 68)

    session = make_session()

    # ── 阶段 0：自检 ──────────────────────────────────────────────
    print(f"\n[阶段0] 自检：trackId={args.track_id} level={args.level}")
    bundle_a = new_bundle(args.token, args.device_a)
    print(f"  固定组设备 A = {short_device(bundle_a)}（uid={bundle_a['uid']}）")
    ok = 0
    for i in range(max(1, args.warmup)):
        result = v4_probe(session, bundle_a, args.track_id, args.level)
        print(f"  预检 #{i + 1}: {fmt(result)}  ({result['elapsed']:.2f}s)")
        if result["ret"] == "0":
            ok += 1
        elif result["ret"] == "1001":
            print("  ⚠️ 预检就 1001：说明当前签名/凭证形态本身被拒（或已被限流），")
            print("     本实验测的是『继续被限』；请换 --track-id 或稍后再试。")
            return 1
        else:
            print(f"  ❌ 预检未通过（{fmt(result)}），中止实验 —— 需要先拿到 ret=0 基线")
            return 1
    if not ok:
        print("  ❌ 未建立 ret=0 基线，中止")
        return 1

    # ── 阶段 1：压测到 1001 ──────────────────────────────────────
    print(f"\n[阶段1] 压测固定设备 A，间隔 {args.interval}s，上限 {args.max_requests} 次")
    hit_1001_at = -1
    started = time.monotonic()
    for i in range(1, args.max_requests + 1):
        result = v4_probe(session, bundle_a, args.track_id, args.level)
        marker = ""
        if result["ret"] == "1001":
            marker = "  ← 触发限流"
            hit_1001_at = i
        if result["ret"] == "0" or marker:
            print(f"  #{i:>3} {fmt(result)}  ({result['elapsed']:.2f}s){marker}")
        elif i <= 3 or i % 10 == 0:
            print(f"  #{i:>3} {fmt(result)}  ({result['elapsed']:.2f}s)")
        if marker:
            break
        time.sleep(args.interval)
    if hit_1001_at < 0:
        print(f"  ⚠️ 打满 {args.max_requests} 次仍未触发 1001 —— 本次间隔可能太保守，")
        print("     可用 --interval 0.3 --max-requests 200 再试；或你的账号配额本来就大。")
        return 1
    t_1001 = time.monotonic()
    print(f"  ✅ 第 {hit_1001_at} 次触发 1001（自压测开始 {(t_1001 - started):.1f}s）")

    # ── 阶段 2：零间隔交替（关键对照）────────────────────────────
    # 1001 后不给时间窗口恢复的机会，立即交替发 B（每轮全新设备）与
    # A（同一原设备）。若 B 持续成功而 A 持续 1001，则是设备维度限流；
    # 若两者都立即恢复，则是极短时间窗口，与设备无关。
    print(f"\n[阶段2] 1001 后零间隔交替测试（B=每轮全新设备，A=原设备 {short_device(bundle_a)}）")
    rounds = 3
    b_ok_all = True
    a_blocked = False
    for r in range(1, rounds + 1):
        bundle_b = new_bundle(args.token)
        result_b = v4_probe(session, bundle_b, args.track_id, args.level)
        result_a = v4_probe(session, new_bundle(args.token, bundle_a["device_uuid"]), args.track_id)
        print(f"  第{r}轮  B({short_device(bundle_b)}): {fmt(result_b)}   A(原设备): {fmt(result_a)}")
        if result_b["ret"] != "0":
            b_ok_all = False
        if result_a["ret"] == "1001":
            a_blocked = True
    print(f"  交替阶段结束（自 1001 起 {(time.monotonic() - t_1001):.1f}s）")

    # ── 阶段 3（可选）：连续触发 · 恢复时间是否升级 ───────────────
    # 对应 AudioFlow 的真实下载场景：连续触发 1001 后，风控会不会逐次
    # 拉长恢复时间（0.5s → 5s → 50s）？若会升级，长冷却有其必要；
    # 若始终亚秒级，AudioFlow 的 60s+ 指数退避就是纯过度保守。
    recovery_rounds = max(0, args.recovery_rounds)
    if recovery_rounds:
        print(f"\n[阶段3] 连续触发测试 ×{recovery_rounds}（每轮压到 1001 后零间隔重试直到恢复）")
        for r in range(1, recovery_rounds + 1):
            hit = -1
            t0 = time.monotonic()
            for i in range(1, 40):
                res = v4_probe(session, bundle_a, args.track_id, args.level)
                if res["ret"] == "1001":
                    hit = i
                    break
                time.sleep(0.08)
            if hit < 0:
                print(f"  第{r}轮  40 次未触发 1001（风控可能已对本账号静默）")
                break
            # 零间隔重试，记录恢复耗时
            recovered = -1.0
            for i in range(1, 200):
                res = v4_probe(session, new_bundle(args.token, bundle_a["device_uuid"]), args.track_id)
                if res["ret"] == "0":
                    recovered = time.monotonic() - t0
                    break
                time.sleep(0.08)
            print(f"  第{r}轮  触发于第 {hit} 次请求，恢复耗时 {recovered:.1f}s" if recovered >= 0
                  else f"  第{r}轮  触发后 200 次重试（约 16s）未恢复")
            time.sleep(1.0)

    # ── 判定 ─────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    if b_ok_all and a_blocked:
        print("✅ 结论：你的观察被复现 —— 限流按【设备】维度。")
        print("   同账号换全新 device_id + 重签 x-tk 零间隔即可绕过 1001；")
        print("   原设备同时刻仍被限，排除了『限流刚好到期』的解释。")
        print("   落地方式：AudioFlow 在 _mark_mobile_v4_rate_limited() 时轮换")
        print("   device_uuid 并重派三件套（改 core/ximalaya_universal_login.py 的持久化策略）。")
    elif b_ok_all and not a_blocked:
        print("⚠️ 结论：两组都立即恢复 —— 限流是极短的时间窗口（亚秒级），与设备无关。")
        print("   换设备『有效』是巧合；AudioFlow 的 60s+ 全局冷却过于保守，")
        print("   把冷却调到 1-2s 或退避改为线性即可，无需实现设备轮换。")
    else:
        print(f"❌ 结论：B 未全部成功（b_ok_all={b_ok_all}, a_blocked={a_blocked}）")
        print("   限流可能按【账号/IP】维度，换设备无效；")
        print("   AudioFlow 现有的全局冷却策略已是正确做法。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
