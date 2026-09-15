#!/usr/bin/env python3
"""Runtime 模块的段布局与只读预算报告。

背景（2026-09-12 复核）：Runtime 的段地址自 `806adbd` 起按体积推导，硬件真正要求的
只有“Rx -> R -> Rw 三段连续、页对齐、只读段非空”（exlaunch 的 FindModules() 靠这个
三元组识别模块，中间出现未映射页会让它找不到自己）。旧的 0x54000/0x75000 只是某次
真机验证过的历史地址，后来被当成上限断言，会在功能写满之前先拦住构建。

因此 `runtime/misc/link.ld` 里只留“失控增长”闸门，默认值与 `link.ld` 的 ASSERT
保持一致（`runtime/tests/test_architecture_layout.py` 会核对两边数值）：

* 代码（Rx）结束 <= 512 KiB
* 只读段结束 <= 1 MiB

本工具在每个构建后打印实际用量、余量与结构不变量，让增长可见；超限或结构不变量被
破坏时以退出码 1 失败。只用 Python 标准库解析 ELF，不依赖交叉工具链，因此本地就能跑。

退出码：

* 0 通过；1 超限或结构不变量被破坏；2 参数错误；3 输入不是可解析的 ELF（例如测试用的
  桩工具链只会 `touch` 一个空文件）。3 与 1 分开是为了让构建入口能"跳过非真实产物"而不是
  把桩工具链的构建判失败，同时真实产物的超限绝不被放过。

用法：

    python3 tools/runtime_layout_budget.py --elf runtime/runtime.elf
    python3 tools/runtime_layout_budget.py --elf runtime/runtime.elf --json
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 与 runtime/misc/link.ld 的 ASSERT 对应；改动必须同时改两处（有测试核对）。
DEFAULT_CODE_LIMIT = 0x80000
DEFAULT_RO_END_LIMIT = 0x100000

PT_LOAD = 1
SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHT_NOBITS = 8


class ElfError(Exception):
    """输入不是可解析的 ELF64 小端文件。"""


@dataclass
class Segment:
    flags: int
    offset: int
    virtual_address: int
    file_size: int
    memory_size: int
    alignment: int

    @property
    def mapped_end(self) -> int:
        return self.virtual_address + round_up(self.memory_size, 0x1000)

    def describe(self) -> str:
        return f"[{self.virtual_address:#x},{self.mapped_end:#x}) flags={self.flags}"


def round_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass
class ElfLayout:
    segments: list[Segment] = field(default_factory=list)
    code_end: int = 0
    read_only_end: int = 0
    writable_end: int = 0


def parse_layout(binary: bytes) -> ElfLayout:
    if len(binary) < 64:
        raise ElfError("文件小于 ELF 头长度")
    if binary[:4] != b"\x7fELF":
        raise ElfError("不是 ELF 文件")
    if binary[4] != 2 or binary[5] != 1:
        raise ElfError("只支持 ELF64 小端")

    program_headers_offset = struct.unpack_from("<Q", binary, 0x20)[0]
    section_headers_offset = struct.unpack_from("<Q", binary, 0x28)[0]
    program_header_size = struct.unpack_from("<H", binary, 0x36)[0]
    program_header_count = struct.unpack_from("<H", binary, 0x38)[0]
    section_header_size = struct.unpack_from("<H", binary, 0x3A)[0]
    section_header_count = struct.unpack_from("<H", binary, 0x3C)[0]

    layout = ElfLayout()
    for index in range(program_header_count):
        offset = program_headers_offset + index * program_header_size
        if offset + 56 > len(binary):
            raise ElfError("程序头越界")
        program_type, flags, file_offset, virtual_address, _, file_size, memory_size, alignment = (
            struct.unpack_from("<IIQQQQQQ", binary, offset)
        )
        if program_type == PT_LOAD:
            layout.segments.append(
                Segment(flags, file_offset, virtual_address, file_size, memory_size, alignment)
            )

    # 段内容边界用节头精确计算：只读段不含 `.bss` 这类 NOBITS 节。
    for index in range(section_header_count):
        offset = section_headers_offset + index * section_header_size
        if offset + 64 > len(binary):
            raise ElfError("节头越界")
        name, section_type, flags, address, _, size = struct.unpack_from("<IIQQQQ", binary, offset)
        del name
        if not flags & SHF_ALLOC:
            continue
        if section_type == SHT_NOBITS:
            continue
        end = address + size
        if flags & SHF_EXECINSTR:
            layout.code_end = max(layout.code_end, end)
        elif flags & SHF_WRITE:
            layout.writable_end = max(layout.writable_end, end)
        else:
            layout.read_only_end = max(layout.read_only_end, end)

    if not layout.segments:
        raise ElfError("没有任何 PT_LOAD 段")
    return layout


def check_invariants(layout: ElfLayout) -> list[str]:
    """返回结构不变量被破坏的说明；空列表表示全部成立。"""
    problems: list[str] = []
    if len(layout.segments) != 3:
        problems.append(
            f"PT_LOAD 段数为 {len(layout.segments)}，期望 3（Rx / R / Rw 三元组）"
        )
        return problems

    rx, ro, rw = layout.segments
    if [rx.flags, ro.flags, rw.flags] != [5, 4, 6]:
        problems.append(
            f"段权限为 {[rx.flags, ro.flags, rw.flags]}，期望 [5, 4, 6]（Rx / R / Rw）"
        )
    if rx.virtual_address != 0:
        problems.append(f"代码段起点为 {rx.virtual_address:#x}，期望 0")
    for segment in layout.segments:
        if segment.virtual_address % 0x1000:
            problems.append(f"段起点未 4 KiB 对齐：{segment.describe()}")
    if rx.mapped_end != ro.virtual_address:
        problems.append("代码段与只读段之间存在未映射空洞")
    if ro.mapped_end != rw.virtual_address:
        problems.append("只读段与读写段之间存在未映射空洞")
    if layout.read_only_end <= 0:
        problems.append("只读段为空：exlaunch 的三元组识别会失败")
    return problems


def build_report(
    layout: ElfLayout,
    code_limit: int = DEFAULT_CODE_LIMIT,
    ro_end_limit: int = DEFAULT_RO_END_LIMIT,
) -> dict:
    problems = check_invariants(layout)
    if layout.code_end > code_limit:
        problems.append(
            f"代码结束 {layout.code_end:#x} 超过上限 {code_limit:#x}（{code_limit / 1024:.0f} KiB）"
        )
    if layout.read_only_end > ro_end_limit:
        problems.append(
            f"只读段结束 {layout.read_only_end:#x} 超过上限 {ro_end_limit:#x}"
            f"（{ro_end_limit / 1024:.0f} KiB）"
        )

    return {
        "segments": [
            {
                "flags": segment.flags,
                "start": segment.virtual_address,
                "mapped_end": segment.mapped_end,
                "file_size": segment.file_size,
            }
            for segment in layout.segments
        ],
        "code_end": layout.code_end,
        "code_limit": code_limit,
        "code_reserve": code_limit - layout.code_end,
        "read_only_end": layout.read_only_end,
        "read_only_limit": ro_end_limit,
        "read_only_reserve": ro_end_limit - layout.read_only_end,
        "writable_end": layout.writable_end,
        "ok": not problems,
        "problems": problems,
    }


def format_report(report: dict) -> str:
    lines = []
    for index, segment in enumerate(report["segments"]):
        role = {5: "Rx", 4: "R", 6: "Rw"}.get(segment["flags"], "?")
        span = (segment["mapped_end"] - segment["start"]) / 1024
        lines.append(
            f"LOAD[{index}] {role:2s} [{segment['start']:#08x},{segment['mapped_end']:#08x}) "
            f"映射 {span:7.1f} KiB  文件 {segment['file_size'] / 1024:7.1f} KiB"
        )
    lines.append(
        f"只读预算 code_end={report['code_end']:#x} limit={report['code_limit']:#x} "
        f"reserve={report['code_reserve'] / 1024:.1f} KiB "
        f"used={report['code_end'] / report['code_limit'] * 100:.0f}%"
    )
    lines.append(
        f"只读预算 ro_end={report['read_only_end']:#x} limit={report['read_only_limit']:#x} "
        f"reserve={report['read_only_reserve'] / 1024:.1f} KiB "
        f"used={report['read_only_end'] / report['read_only_limit'] * 100:.0f}%"
    )
    lines.append(
        "BUDGET "
        f"code_end={report['code_end']:#x} code_limit={report['code_limit']:#x} "
        f"ro_end={report['read_only_end']:#x} ro_limit={report['read_only_limit']:#x} "
        f"loads={len(report['segments'])} ok={'yes' if report['ok'] else 'no'}"
    )
    for problem in report["problems"]:
        lines.append(f"布局问题：{problem}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--elf", required=True, type=Path, help="已构建的 runtime.elf")
    parser.add_argument("--code-limit", type=lambda v: int(v, 0), default=DEFAULT_CODE_LIMIT)
    parser.add_argument("--ro-end-limit", type=lambda v: int(v, 0), default=DEFAULT_RO_END_LIMIT)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    arguments = parser.parse_args(argv)

    try:
        layout = parse_layout(arguments.elf.read_bytes())
    except (OSError, ElfError) as error:
        print(f"无法解析 {arguments.elf}：{error}", file=sys.stderr)
        return 3

    report = build_report(layout, arguments.code_limit, arguments.ro_end_limit)
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
