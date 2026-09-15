"""Stage148：Mod 内容挂载点重建中继的补丁与常量一致性测试。

覆盖三件事：
1. 补丁记录只改两个位置（唯一 `ret` 与代码洞），且不动 `ret` 之后的冷路径字节；
2. 桩本身保存/恢复 `x0`（返回值）与 `x30`（返回地址）——`blr` 会覆盖 `x30`，
   不保存就会在回调返回后跳到错误地址；
3. `runtime_constants.hpp` 里的守卫与生成器逐字节一致，避免两侧漂移。
"""

import re
import unittest
from pathlib import Path

from tools import build_patches as patches
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class RebuildMountPointsRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_target_is_the_only_return_instruction_in_the_body(self):
        """只有一条 `ret` 才允许只补这一处覆盖全部返回路径。"""
        body = self.nro[0x3B3510:0x3B371C]
        returns = sum(
            1 for offset in range(0, len(body) - 3, 4)
            if int.from_bytes(body[offset:offset + 4], "little") == 0xD65F03C0
        )
        self.assertEqual(returns, 1)
        self.assertEqual(
            self.nro[patches.REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET:
                     patches.REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET + 4],
            bytes.fromhex("C0035FD6"),
        )

    def test_patch_rewrites_only_the_return_and_the_cave(self):
        records = decode_ips(patches.build_rebuild_mount_points_relay_patch(self.nro))
        self.assertEqual(records, patches.REBUILD_MOUNT_POINTS_RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [0x3B36F4, 0x68CFA0])
        patched = apply_records(self.nro, records)
        # `ret` 之后的冷清理路径（早退分支回落的主流程）必须原样保留：它们不属于补丁范围。
        self.assertEqual(
            patched[0x3B36F8:0x3B3704],
            self.nro[0x3B36F8:0x3B3704],
        )
        # 洞区原本全零，补丁后只有桩 + 8 字节空槽。
        self.assertEqual(self.nro[0x68CFA0:0x68CFD0], bytes(0x30))
        self.assertEqual(patched[0x68CFC8:0x68CFD0], bytes(8))

    def test_cave_records_fit_the_last_zero_page_of_text(self):
        """`.text` 里只剩这一块零填充区，记录必须装得下且不越界。"""
        self.assertLessEqual(0x68CFA0 + 0x30, 0x68D000)
        self.assertEqual(self.nro[0x68CFA0:0x68CFD0], bytes(0x30))

    def test_stub_saves_and_restores_the_return_value_and_return_address(self):
        code = patches.REBUILD_MOUNT_POINTS_RELAY_CODE
        self.assertEqual(len(code), 0x30)
        self.assertEqual(
            code,
            bytes.fromhex(
                "FF8300D1"  # sub sp, sp, #0x20
                "E07B00A9"  # stp x0, x30, [sp]   <- 保存返回值与返回地址
                "11010010"  # adr x17, <slot>
                "30FEDFC8"  # ldar x16, [x17]
                "500000B4"  # cbz x16, +8         <- 未发布时直接走恢复
                "00023FD6"  # blr x16            <- 会覆盖 x30，所以必须已保存
                "E07B40A9"  # ldp x0, x30, [sp]
                "FF830091"  # add sp, sp, #0x20
                "C0035FD6"  # ret                 <- 原指令语义
            )
            + bytes(4)
            + bytes(8),
        )
        # `adr` 目标必须正好是槽：写错就会从别处取函数指针。
        self.assertEqual(
            patches.REBUILD_MOUNT_POINTS_RELAY_SLOT_OFFSET,
            patches.REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET + 0x28,
        )

    def test_runtime_verifier_matches_generated_relay(self):
        constants = (ROOT / "runtime" / "source" / "runtime_constants.hpp").read_text(
            encoding="utf-8"
        )

        def scalar(name):
            match = re.search(rf"{name} = (0x[0-9A-Fa-f]+);", constants)
            self.assertIsNotNone(match, name)
            return int(match.group(1), 16)

        def array(name, size):
            match = re.search(rf"{name} = \{{(.*?)\n\}};", constants, re.DOTALL)
            self.assertIsNotNone(match, name)
            values = bytes(int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", match.group(1)))
            self.assertEqual(len(values), size, name)
            return values

        self.assertEqual(scalar("kRebuildMountPointsRelayOffset"),
                         patches.REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET)
        self.assertEqual(scalar("kRebuildMountPointsRelayCodeOffset"),
                         patches.REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET)
        self.assertEqual(scalar("kRebuildMountPointsRelaySlotOffset"),
                         patches.REBUILD_MOUNT_POINTS_RELAY_SLOT_OFFSET)
        self.assertEqual(array("kRebuildMountPointsRelayExpectedOriginal", 16),
                         patches.REBUILD_MOUNT_POINTS_RELAY_TARGET_ORIGINAL)
        self.assertEqual(array("kRebuildMountPointsRelayExpectedEntry", 4),
                         patches.REBUILD_MOUNT_POINTS_RELAY_TARGET_RECORD[1])
        self.assertEqual(array("kRebuildMountPointsRelayExpectedBytes", 0x30),
                         patches.REBUILD_MOUNT_POINTS_RELAY_CODE)

    def test_relay_only_calls_published_callback_through_the_slot(self):
        """桩必须经槽间接调用：模块加载地址按会话变化，中继里不能写死回调地址。"""
        code = patches.REBUILD_MOUNT_POINTS_RELAY_CODE
        self.assertIn(bytes.fromhex("30FEDFC8"), code)   # ldar x16, [x17]
        self.assertIn(bytes.fromhex("00023FD6"), code)   # blr x16
        # 槽在补丁里必须是零：Runtime 发布前的任何取值都意味着有人在别处写过它。
        self.assertEqual(patches.REBUILD_MOUNT_POINTS_RELAY_CODE[-8:], bytes(8))


if __name__ == "__main__":
    unittest.main()
