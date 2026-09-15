import os
import struct
import unittest
from pathlib import Path

from tools.runtime_layout_budget import DEFAULT_CODE_LIMIT, DEFAULT_RO_END_LIMIT


class RuntimeLayoutTests(unittest.TestCase):
    """真机验证过的段映射：结构不变量，而不是某次构建的历史地址。

    2026-09-12 复核：`806adbd` 起段地址按体积推导，硬件要求只有“Rx -> R -> Rw
    三段连续、页对齐、只读段非空”。这里的 0x54000/0x75000 旧断言已删除，
    改用 `tools/runtime_layout_budget.py` 的同一套口径。
    """

    def test_verified_module_load_layout(self):
        artifact = os.environ.get("ISAAC_RUNTIME_LAYOUT_ELF")
        if artifact is None:
            self.skipTest("set ISAAC_RUNTIME_LAYOUT_ELF to validate a Docker-built runtime.elf")

        binary = Path(artifact).read_bytes()
        self.assertGreaterEqual(len(binary), 64)
        self.assertEqual(binary[:4], b"\x7fELF")
        self.assertEqual(binary[4:6], b"\x02\x01")

        program_headers_offset = struct.unpack_from("<Q", binary, 0x20)[0]
        program_header_size = struct.unpack_from("<H", binary, 0x36)[0]
        program_header_count = struct.unpack_from("<H", binary, 0x38)[0]
        self.assertEqual(program_header_size, 56)

        loads = []
        for index in range(program_header_count):
            offset = program_headers_offset + index * program_header_size
            program_type, flags, _, virtual_address, _, file_size, memory_size, alignment = (
                struct.unpack_from("<IIQQQQQQ", binary, offset)
            )
            if program_type == 1:  # PT_LOAD
                loads.append((flags, virtual_address, file_size, memory_size, alignment))

        self.assertEqual(len(loads), 3, "必须是 Rx / R / Rw 三段，exlaunch 靠这个三元组识别模块")
        self.assertEqual([load[0] for load in loads], [5, 4, 6])
        self.assertEqual(loads[0][1], 0)
        for _, virtual_address, _, _, _ in loads:
            self.assertEqual(virtual_address % 0x1000, 0, "段起点必须 4 KiB 对齐")
        # 段之间不得有未映射空洞：映射范围必须首尾相接。
        for index in range(2):
            virtual_address, memory_size = loads[index][1], loads[index][3]
            mapped_end = virtual_address + ((memory_size + 0xFFF) & ~0xFFF)
            self.assertEqual(
                mapped_end,
                loads[index + 1][1],
                "段之间存在未映射空洞，exlaunch 模块扫描会失败",
            )
        # 上限只防失控增长；实际用量由 tools/runtime_layout_budget.py 报告。
        self.assertLessEqual(loads[0][3], DEFAULT_CODE_LIMIT)
        self.assertLessEqual(loads[1][1] + loads[1][3], DEFAULT_RO_END_LIMIT)


if __name__ == "__main__":
    unittest.main()
