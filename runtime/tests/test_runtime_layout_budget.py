"""`tools/runtime_layout_budget.py` 的解析与门禁行为测试。

这些测试完全离线：自己拼一个最小 ELF64（程序头 + 节头）来驱动工具，因此不需要
Docker 或交叉工具链。真实产物的一致性由 `test_architecture_layout.py` 在容器内构建
后核对（工具输出与 `nm` 的代码结束位置、段数与结构不变量）。
"""

import struct
import tempfile
import unittest
from pathlib import Path

from tools.runtime_layout_budget import (
    DEFAULT_CODE_LIMIT,
    DEFAULT_RO_END_LIMIT,
    ElfError,
    build_report,
    format_report,
    main,
    parse_layout,
)

PT_LOAD = 1
SHT_PROGBITS = 1
SHT_NOBITS = 8
SHF_ALLOC = 0x2
SHF_WRITE = 0x1
SHF_EXECINSTR = 0x4


def build_elf(segments, sections) -> bytes:
    """拼一个只有 ELF 头、程序头、节头的最小 ELF64 小端文件。

    segments: [(flags, vaddr, file_size, memory_size)]
    sections: [(type, flags, addr, size)]
    """
    header_size = 64
    phoff = header_size
    phsize = 56 * len(segments)
    shoff = phoff + phsize
    shsize = 64 * len(sections)
    binary = bytearray(shoff + shsize)

    binary[0:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", binary, 0x20, phoff)
    struct.pack_into("<Q", binary, 0x28, shoff)
    struct.pack_into("<H", binary, 0x34, header_size)
    struct.pack_into("<H", binary, 0x36, 56)
    struct.pack_into("<H", binary, 0x38, len(segments))
    struct.pack_into("<H", binary, 0x3A, 64)
    struct.pack_into("<H", binary, 0x3C, len(sections))

    for index, (flags, vaddr, file_size, memory_size) in enumerate(segments):
        struct.pack_into(
            "<IIQQQQQQ",
            binary,
            phoff + index * 56,
            PT_LOAD,
            flags,
            0,
            vaddr,
            0,
            file_size,
            memory_size,
            0x1000,
        )

    for index, (section_type, flags, addr, size) in enumerate(sections):
        struct.pack_into(
            "<IIQQQQIIQQ",
            binary,
            shoff + index * 64,
            0,
            section_type,
            flags,
            addr,
            0,
            size,
            0,
            0,
            8,
            0,
        )
    return bytes(binary)


def healthy_elf(code_end=0x4FBE0, read_only_end=0x6AF14) -> bytes:
    """与当前 Runtime 同形状的段/节布局（三段连续、页对齐、只读段非空）。"""
    ro_start = (code_end + 0xFFF) & ~0xFFF
    rw_start = (read_only_end + 0xFFF) & ~0xFFF
    segments = [
        (5, 0, code_end, code_end),
        (4, ro_start, read_only_end - ro_start, read_only_end - ro_start),
        (6, rw_start, 0x1000, 0x200000),
    ]
    sections = [
        (SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, code_end),
        (SHT_PROGBITS, SHF_ALLOC, ro_start, read_only_end - ro_start),
        (SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, rw_start, 0x100),
        (SHT_NOBITS, SHF_ALLOC | SHF_WRITE, rw_start + 0x1000, 0x100000),
    ]
    return build_elf(segments, sections)


class RuntimeLayoutBudgetToolTests(unittest.TestCase):
    def test_healthy_layout_passes_both_limits(self):
        layout = parse_layout(healthy_elf())
        report = build_report(layout)

        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(len(report["segments"]), 3)
        self.assertEqual(report["code_end"], 0x4FBE0)
        self.assertEqual(report["read_only_end"], 0x6AF14)
        self.assertEqual(report["code_reserve"], DEFAULT_CODE_LIMIT - 0x4FBE0)
        self.assertEqual(report["read_only_reserve"], DEFAULT_RO_END_LIMIT - 0x6AF14)
        self.assertIn("BUDGET", format_report(report))
        self.assertTrue(format_report(report).rstrip().endswith("ok=yes"))

    def test_code_growth_beyond_limit_fails(self):
        beyond = (DEFAULT_CODE_LIMIT + 0x1000) & ~0xFFF
        report = build_report(parse_layout(healthy_elf(code_end=beyond, read_only_end=beyond + 0x2000)))

        self.assertFalse(report["ok"])
        self.assertTrue(
            any("代码结束" in problem for problem in report["problems"]), report["problems"]
        )

    def test_read_only_growth_beyond_limit_fails(self):
        beyond = (DEFAULT_RO_END_LIMIT + 0x1000) & ~0xFFF
        report = build_report(parse_layout(healthy_elf(read_only_end=beyond)))

        self.assertFalse(report["ok"])
        self.assertTrue(
            any("只读段结束" in problem for problem in report["problems"]), report["problems"]
        )

    def test_unmapped_hole_between_segments_is_rejected(self):
        binary = bytearray(healthy_elf())
        # 把只读段起点往后挪一页，制造未映射空洞。
        phoff = struct.unpack_from("<Q", binary, 0x20)[0]
        struct.pack_into("<Q", binary, phoff + 56 + 16, 0x060000)

        report = build_report(parse_layout(bytes(binary)))
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("未映射空洞" in problem for problem in report["problems"]), report["problems"]
        )

    def test_empty_read_only_segment_is_rejected(self):
        segments = [
            (5, 0, 0x4000, 0x4000),
            (4, 0x4000, 0x1000, 0x1000),
            (6, 0x5000, 0x1000, 0x1000),
        ]
        sections = [(SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, 0x4000)]
        report = build_report(parse_layout(build_elf(segments, sections)))

        self.assertFalse(report["ok"])
        self.assertTrue(
            any("只读段为空" in problem for problem in report["problems"]), report["problems"]
        )

    def test_non_elf_input_is_rejected(self):
        with self.assertRaises(ElfError):
            parse_layout(b"not an elf")


class RuntimeLayoutBudgetCommandTests(unittest.TestCase):
    """构建入口只按退出码判断，所以退出码语义本身要被测到：

    0 = 通过，1 = 超限/结构被破坏（构建必须失败），3 = 不是真实 ELF（桩工具链，跳过）。
    """

    def write(self, directory, data) -> str:
        path = Path(directory) / "runtime.elf"
        path.write_bytes(data)
        return str(path)

    def test_over_budget_elf_exits_with_one(self):
        beyond = (DEFAULT_RO_END_LIMIT + 0x1000) & ~0xFFF
        with tempfile.TemporaryDirectory(prefix="layout-budget-") as temporary:
            artifact = self.write(temporary, healthy_elf(read_only_end=beyond))
            self.assertEqual(main(["--elf", artifact]), 1)

    def test_healthy_elf_exits_with_zero(self):
        with tempfile.TemporaryDirectory(prefix="layout-budget-") as temporary:
            artifact = self.write(temporary, healthy_elf())
            self.assertEqual(main(["--elf", artifact]), 0)

    def test_stub_toolchain_artifact_exits_with_three(self):
        """桩工具链的 `%.elf` 只 touch 一个空文件：跳过而不是让构建失败。"""
        with tempfile.TemporaryDirectory(prefix="layout-budget-") as temporary:
            artifact = self.write(temporary, b"")
            self.assertEqual(main(["--elf", artifact]), 3)
        with tempfile.TemporaryDirectory(prefix="layout-budget-") as temporary:
            self.assertEqual(main(["--elf", str(Path(temporary) / "missing.elf")]), 3)


if __name__ == "__main__":
    unittest.main()
