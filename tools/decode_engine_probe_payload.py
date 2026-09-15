#!/usr/bin/env python3
"""解码帧时间归因探针（`ISAACEV1`）产生的 Atmosphere 崩溃报告。

负载由 `runtime/source/saltynx_runtime_bridge.cpp` 的
`IsaacModRuntime_EngineProbeBreak()` 组装，字段表在本脚本的 `FIELDS` 里逐项列出。
同一个会话内部做了 A/B（引擎内存可读性缓存 **开** / **关**），所以一次真机就能回答：

* 每帧有多少次"引擎内存访问"（attempts/frame）；
* 其中多少次真的落进 `svcQueryMemory`（syscalls/frame），花了多少毫秒；
* 两种口径下的帧间隔平均值、最大值，以及超过 20 ms / 33 ms 的帧数。

用法::

    python3 tools/decode_engine_probe_payload.py <crash-report.log> [...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decode_probe_payload import parse_registers  # noqa: E402

MAGIC = 0x3156454341415349  # "ISAACEV1"
TRIGGERS = {1: "两相跑满", 2: "时间兜底（暂停/提前结束）"}

FIELDS = """\
x2   triggerReason | phasesDone << 32
x3   managedFrames（真的派发过受管 POST_RENDER 的帧数）
x4   ticksPerMs（按 cntfrq_el0 标定）
x5   ON.frames | OFF.frames << 32
x6   ON attempts        x7   OFF attempts
x8   ON syscalls        x9   OFF syscalls
x10  ON syscallTicks    x11  OFF syscallTicks
x12  ON cacheHits       x13  OFF cacheHits
x14  ON frameTicks      x15  OFF frameTicks
x16  ON maxFrameTicks   x17  OFF maxFrameTicks
x18  ON frames>20ms | OFF frames>20ms << 32
x19  ON frames>33ms | OFF frames>33ms << 32
x20  ON dispatchTicks   x21  OFF dispatchTicks
x22  ON drawCalls | OFF drawCalls << 32
x23  lastApiPrimary | filterMask << 32
x24  postRenderCount(该构建恒为 0) | postUpdateCount << 32
x25  EID.GameRenderCount：低 32 读取原因(0 成功/1 不是表/2 无字段/3 不是 number) | 值 << 32"""

EID_REASONS = {0: "成功", 1: "EID 不是表", 2: "没有这个字段", 3: "字段不是 number"}


def low(value: int) -> int:
    return value & 0xFFFFFFFF


def high(value: int) -> int:
    return (value >> 32) & 0xFFFFFFFF


def ms(ticks: int, ticks_per_ms: int) -> float:
    return ticks / ticks_per_ms if ticks_per_ms else 0.0


def report(registers: dict[int, int], label: str) -> None:
    if registers.get(1) != MAGIC:
        print(f"[{label}] 不是 ISAACEV1 报告（x1=0x{registers.get(1, 0):016x}），跳过")
        return
    ticks_per_ms = registers.get(4, 0) or 19200
    trigger = low(registers.get(2, 0))
    on_frames = low(registers.get(5, 0))
    off_frames = high(registers.get(5, 0))
    on = {
        "frames": on_frames,
        "attempts": registers.get(6, 0),
        "syscalls": registers.get(8, 0),
        "syscall_ticks": registers.get(10, 0),
        "cache_hits": registers.get(12, 0),
        "frame_ticks": registers.get(14, 0),
        "max_frame_ticks": registers.get(16, 0),
        "over20": low(registers.get(18, 0)),
        "over33": low(registers.get(19, 0)),
        "dispatch_ticks": registers.get(20, 0),
        "draw_calls": low(registers.get(22, 0)),
    }
    off = {
        "frames": off_frames,
        "attempts": registers.get(7, 0),
        "syscalls": registers.get(9, 0),
        "syscall_ticks": registers.get(11, 0),
        "cache_hits": registers.get(13, 0),
        "frame_ticks": registers.get(15, 0),
        "max_frame_ticks": registers.get(17, 0),
        "over20": high(registers.get(18, 0)),
        "over33": high(registers.get(19, 0)),
        "dispatch_ticks": registers.get(21, 0),
        "draw_calls": high(registers.get(22, 0)),
    }

    print(f"== {label} ==")
    print(f"触发原因          : {trigger} ({TRIGGERS.get(trigger, '未知')})")
    print(f"完成相数          : {high(registers.get(2, 0))}")
    print(f"受管帧总数        : {registers.get(3, 0)}")
    print(f"ticksPerMs        : {ticks_per_ms}")
    eid_word = registers.get(25, 0)
    eid_reason = low(eid_word)
    print(f"EID.GameRenderCount: {high(eid_word)}"
          f"（读取结果：{EID_REASONS.get(eid_reason, '未知')}）")
    print(f"POST_RENDER/POST_UPDATE 派发次数: "
          f"{low(registers.get(24, 0))}/{high(registers.get(24, 0))}")
    print(f"最后调用的 API 位 : {low(registers.get(23, 0))}, 过滤器掩码: "
          f"{high(registers.get(23, 0)) & 0xFFFF}")
    print()

    header = f"{'指标':<28}{'缓存开(ON)':>16}{'缓存关(OFF)':>16}"
    print(header)
    print("-" * len(header))

    def line(name: str, on_value, off_value) -> None:
        print(f"{name:<28}{on_value:>16}{off_value:>16}")

    line("帧数", on["frames"], off["frames"])
    line("绘制调用数", on["draw_calls"], off["draw_calls"])
    line("平均帧间隔 (ms)",
         f"{ms(on['frame_ticks'], ticks_per_ms) / on['frames']:.3f}" if on["frames"] else "-",
         f"{ms(off['frame_ticks'], ticks_per_ms) / off['frames']:.3f}" if off["frames"] else "-")
    line("最大帧间隔 (ms)",
         f"{ms(on['max_frame_ticks'], ticks_per_ms):.3f}",
         f"{ms(off['max_frame_ticks'], ticks_per_ms):.3f}")
    line(">20ms 帧数", on["over20"], off["over20"])
    line(">33ms 帧数", on["over33"], off["over33"])
    line("可读性检查次数/帧",
         f"{on['attempts'] / on['frames']:.1f}" if on["frames"] else "-",
         f"{off['attempts'] / off['frames']:.1f}" if off["frames"] else "-")
    line("系统调用次数/帧",
         f"{on['syscalls'] / on['frames']:.1f}" if on["frames"] else "-",
         f"{off['syscalls'] / off['frames']:.1f}" if off["frames"] else "-")
    line("系统调用总耗时 (ms)",
         f"{ms(on['syscall_ticks'], ticks_per_ms):.1f}",
         f"{ms(off['syscall_ticks'], ticks_per_ms):.1f}")
    line("系统调用耗时/帧 (ms)",
         f"{ms(on['syscall_ticks'], ticks_per_ms) / on['frames']:.3f}" if on["frames"] else "-",
         f"{ms(off['syscall_ticks'], ticks_per_ms) / off['frames']:.3f}" if off["frames"] else "-")
    line("单次系统调用 (us)",
         f"{ms(on['syscall_ticks'], ticks_per_ms) * 1000 / on['syscalls']:.2f}"
         if on["syscalls"] else "-",
         f"{ms(off['syscall_ticks'], ticks_per_ms) * 1000 / off['syscalls']:.2f}"
         if off["syscalls"] else "-")
    line("缓存命中", on["cache_hits"], off["cache_hits"])
    line("派发总耗时 (ms)",
         f"{ms(on['dispatch_ticks'], ticks_per_ms):.1f}",
         f"{ms(off['dispatch_ticks'], ticks_per_ms):.1f}")
    line("派发耗时/帧 (ms)",
         f"{ms(on['dispatch_ticks'], ticks_per_ms) / on['frames']:.3f}" if on["frames"] else "-",
         f"{ms(off['dispatch_ticks'], ticks_per_ms) / off['frames']:.3f}" if off["frames"] else "-")
    print()
    print("字段表：")
    print(FIELDS)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+")
    options = parser.parse_args()
    for name in options.reports:
        text = Path(name).read_text(encoding="utf-8", errors="replace")
        registers = parse_registers(text)
        if not registers:
            print(f"[{name}] 没有读到寄存器段（不是崩溃报告？），跳过")
            continue
        report(registers, Path(name).name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
