#!/usr/bin/env python3
"""确定 `IsaacRepentance::ANM2`（Lua 的 `Sprite`）对象尺寸。

存在的理由：Lua 的 `Sprite()` 必须在运行时侧自己分配一个引擎对象（和 `Font` 一样由我们
`new`/`delete`），而**尺寸错了就是踩内存**。stage150 的审计只证了 ctor/dtor 偏移，把尺寸列为
未定案，并建议用"分配步长分析"：ANM2 被 Backdrop/Entity/HUD/Room/Entity_Player 等大量类当作
成员构造，因此可以

  1. 找 `operator new` 之后紧跟 `ANM2::ANM2()` 的调用点 —— 分配时传的字节数就是对象尺寸；
  2. 找同一个函数里连续构造的两个 ANM2 成员 —— 两者的 `this` 位移差就是成员步长（= 尺寸向上取整）。

本工具只读、只打印聚合结论；每条结论都带**出处**（调用点地址 + 依据指令），便于复核。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import struct
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import nro_disasm
from tools.nro_symbols import parse_dynamic_symbols


DEFAULT_NRO = (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)
PLT_BASE = 0x670000
PLT_STRIDE = 0x10
ANM2_CTOR_NAMES = (
    "_ZN15IsaacRepentance4ANM2C1Ev",
    "_ZN15IsaacRepentance4ANM2C2Ev",
)
ALLOCATOR_NAMES = ("_Znwm", "_Znwj", "operator new", "malloc")


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _bl_targets(data: bytes, limit: int) -> dict[int, int]:
    """Map every `bl` site in `[0, limit)` to its target."""
    sites = {}
    for offset in range(0, limit, 4):
        word = _u32(data, offset)
        if (word >> 26) & 0x3F != 0x25:
            continue
        immediate = word & 0x03FFFFFF
        if immediate & 0x02000000:
            immediate -= 0x04000000
        sites[offset] = offset + immediate * 4
    return sites


def _amovz_immediate(word: int) -> int | None:
    """`movz w0, #imm`（或 `mov w0, #imm` 的别名）—— 只关心目标寄存器 0。"""
    if (word & 0xFFE00000) == 0x52800000:  # MOVZ 32-bit
        return ((word >> 5) & 0xFFFF) << (((word >> 21) & 0x3) * 16)
    return None


def analyse(nro_path: Path, text_size: int = 0x4F000) -> dict:
    data = nro_path.read_bytes()
    name, symbols = parse_dynamic_symbols(data)
    plt_slots = nro_disasm._relocation_maps(nro_path)[0]

    # 调用经由 PLT：先把每个 `bl` 目标解析成导入符号名（与反汇编器同一套解析）。
    bl = _bl_targets(data, text_size)
    resolved: dict[int, str | None] = {}
    for target in sorted(set(bl.values())):
        resolved[target] = nro_disasm._plt_import(data, target, plt_slots)
    ctor_sites = {site: target for site, target in bl.items()
                  if resolved.get(target) in ANM2_CTOR_NAMES}
    allocator_sites = {site for site, target in bl.items()
                       if resolved.get(target) in ALLOCATOR_NAMES}

    result: dict = {
        "nro": str(nro_path),
        "nro_size": len(data),
        "build_id": name,
        "ctor_symbols": sorted({resolved[target] for target in ctor_sites.values()}),
        "ctor_call_sites": len(ctor_sites),
        "allocator_call_sites": len(allocator_sites),
        "sizes_from_allocation": [],
        "member_strides": [],
    }

    # 1) `bl operator new` 之后（8 条指令内）紧跟 ANM2 ctor，且分配尺寸是常量立即数。
    for site in sorted(allocator_sites):
        window = [offset for offset in ctor_sites if 0 < offset - site <= 0x20]
        if not window:
            continue
        size = None
        for offset in range(site - 0x10, site, 4):
            if offset < 0:
                continue
            immediate = _amovz_immediate(_u32(data, offset))
            if immediate is not None:
                size = immediate
        if size is None:
            continue
        result["sizes_from_allocation"].append({
            "allocator_call": hex(site),
            "ctor_call": hex(window[0]),
            "size": size,
            "size_hex": hex(size),
        })

    # 2) 同一函数内连续两个 ANM2 成员的 `this` 位移差。
    grouped: dict[int, list[int]] = defaultdict(list)
    for site in sorted(ctor_sites):
        # 以 0x400 为窗口把相邻调用点归到"同一函数"（粗糙但足够：步长分析看的是连续构造）。
        bucket = site // 0x400
        grouped[bucket].append(site)
    for bucket, sites in grouped.items():
        if len(sites) < 2:
            continue
        for first, second in zip(sites, sites[1:]):
            result["member_strides"].append({
                "first": hex(first),
                "second": hex(second),
                "delta": second - first,
                "delta_hex": hex(second - first),
            })

    sizes = Counter(entry["size"] for entry in result["sizes_from_allocation"])
    strides = Counter(entry["delta"] for entry in result["member_strides"])
    result["size_histogram"] = {hex(k): v for k, v in sizes.most_common(8)}
    result["stride_histogram"] = {hex(k): v for k, v in strides.most_common(8)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nro", type=Path, default=Path(DEFAULT_NRO))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = analyse(args.nro)
    print(f"build_id={report['build_id']}  ctor 调用点={report['ctor_call_sites']}  "
          f"分配器调用点={report['allocator_call_sites']}")
    print(f"ctor 符号: {report['ctor_symbols']}")
    print(f"来自分配尺寸的候选: {report['size_histogram']}")
    print(f"来自成员步长的候选: {report['stride_histogram']}")
    for entry in report["sizes_from_allocation"][:12]:
        print(f"  alloc @{entry['allocator_call']} -> ctor @{entry['ctor_call']}  "
              f"size={entry['size']} ({entry['size_hex']})")
    if args.report:
        import json
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
