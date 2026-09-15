"""入口中继台账：16 字节必须与固定 NRO 逐字节一致，且不得含 PC 相对指令。

entry_relay 会把这 16 字节原样搬进槽里回放，所以：
  1. 抄错一个字节 = 安装时 EntryMismatch，静默失去一个挂点（本项目已有先例）；
  2. 含 adr/adrp/ldr(literal)/b/bl/b.cond/cbz/cbnz/tbz/tbnz = 换个地方执行就算错地址，
     必须被安装门禁拒绝 —— 台账要在设计阶段就暴露出这种挂点。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)

# 挂点台账：偏移与 16 字节都**复用既有常量**（不新造一套，避免同一个地址有两个真值源）。
# 只有 Present 调用点是本次新增的 —— 登记它是为了把"不可入口挂"钉进测试。
LEDGER = {
    "ManagerUpdate": ("kManagerUpdateFileOffset", "kManagerUpdateExpectedBytes"),
    "ManagerRender": ("kManagerRenderFileOffset", "kManagerRenderExpectedBytes"),
    "PreGetCollectible": ("kPreGetCollectibleRelayOffset",
                          "kPreGetCollectibleRelayExpectedOriginal"),
    "RebuildMountPoints": ("kRebuildContentMountPointsOffset",
                           "kRebuildContentMountPointsExpectedBytes"),
    "ManagerPresentCallsite": ("kManagerPresentCallFileOffset",
                               "kManagerPresentCallExpectedBytes"),
}

# 可以用入口中继挂的只有函数入口那四个；`ManagerPresentCallsite` 登记在台账里是为了
# **明确记录它不可挂**（它是一条 `bl` 调用点，走 GOT 槽改写），因此不参与"必须无 PC 相对指令"的断言。
ENTRY_HOOKABLE = ("ManagerUpdate", "ManagerRender", "PreGetCollectible", "RebuildMountPoints")

PC_RELATIVE = (
    (0xFC000000, 0x14000000), (0xFC000000, 0x94000000), (0x9F000000, 0x10000000),
    (0x9F000000, 0x90000000), (0x3B000000, 0x18000000), (0xFF000000, 0x98000000),
    (0xFF000010, 0x54000000), (0x7F000000, 0x34000000), (0x7F000000, 0x35000000),
    (0x7F000000, 0x36000000), (0x7F000000, 0x37000000),
)


def parse_entry(text, name):
    offset_const, bytes_const = LEDGER[name]
    offset = int(re.search(rf"{offset_const}\s*=\s*(0x[0-9A-Fa-f]+)", text).group(1), 16)
    body = re.search(rf"{bytes_const}\s*=\s*\{{(.*?)\}};", text, re.S).group(1)
    values = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
    return offset, bytes(values)


def is_pc_relative(word):
    return any((word & mask) == value for mask, value in PC_RELATIVE)


def flip_ledger_byte(text, name, index):
    """把台账里某个挂点的第 `index` 个字节取反，返回改坏后的常量文本。

    只服务于判官自检：判官必须能对"抄错的台账"报错，否则"逐字节比对"就是一句空话 ——
    本项目 2026-09-13 正是抄错一个字节（`kGameIsGreedModeExpectedBytes`）让一个绑定静默失效过。
    """
    _, bytes_const = LEDGER[name]
    match = re.search(rf"{bytes_const}\s*=\s*\{{(.*?)\}};", text, re.S)
    values = re.findall(r"0x([0-9A-Fa-f]{2})", match.group(1))
    values[index] = f"{int(values[index], 16) ^ 0xFF:02X}"
    body = "\n    " + ", ".join(f"0x{value}" for value in values) + ",\n"
    return text[:match.start(1)] + body + text[match.end(1):]


@unittest.skipUnless(NRO.exists(), "user-provided Switch dump is not present")
class EntryRelayLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CONSTANTS.read_text(encoding="utf-8")
        cls.nro = NRO.read_bytes()

    def test_every_entry_matches_the_live_image(self):
        for name in LEDGER:
            with self.subTest(entry=name):
                offset, expected = parse_entry(self.text, name)
                self.assertEqual(len(expected), 16, f"{name} 必须是 16 字节")
                self.assertEqual(self.nro[offset:offset + 16], expected,
                                 f"{name} 的台账与 NRO 不一致")

    def test_every_entry_hookable_point_is_free_of_pc_relative_instructions(self):
        for name in ENTRY_HOOKABLE:
            with self.subTest(entry=name):
                _, expected = parse_entry(self.text, name)
                for index in range(0, 16, 4):
                    word = int.from_bytes(expected[index:index + 4], "little")
                    self.assertFalse(is_pc_relative(word),
                                     f"{name}+0x{index:X} 是 PC 相对指令，入口中继无法回放")

    def test_every_ledger_entry_is_classified(self):
        """台账里每一条都必须被明确分类，否则新增条目会静默逃过上面那条 PC 相对指令断言。"""
        self.assertEqual(set(LEDGER), set(ENTRY_HOOKABLE) | {"ManagerPresentCallsite"})

    def test_the_present_callsite_is_recorded_as_not_entry_hookable(self):
        """render-present 的靶点是 `bl` 调用点：台账登记它，但必须能被判为不可挂。

        这条断言把"M2b 走 GOT 槽改写而不是入口中继"这个决定钉死在测试里，
        避免后来有人误以为它也能用 entry_relay 挂。
        """
        _, expected = parse_entry(self.text, "ManagerPresentCallsite")
        words = [int.from_bytes(expected[i:i + 4], "little") for i in range(0, 16, 4)]
        self.assertTrue(any(is_pc_relative(w) for w in words),
                        "Present 调用点含 bl，判定表必须命中")

    def test_the_judge_reports_a_miscopied_byte(self):
        """判官自检（台账方向）：抄错一个字节，逐字节比对必须报错。

        本类其余用例只在"台账恰好正确"时通过，不能证明它们**会**对错误输入报错；
        这里把 `ManagerUpdate` 台账的第 13 个字节取反（真值 `0xF7`，取反后 `0x08`），
        再原样跑一遍上面那条断言，要求它必须抛出 `AssertionError`。
        """
        offset, expected = parse_entry(self.text, "ManagerUpdate")
        self.assertEqual(self.nro[offset:offset + 16], expected, "自检前提：正确的台账必须相符")

        broken_offset, broken_expected = parse_entry(
            flip_ledger_byte(self.text, "ManagerUpdate", 12), "ManagerUpdate")
        self.assertEqual(broken_offset, offset, "自检只改字节，不改偏移")
        self.assertEqual(
            [i for i, (actual, declared) in
             enumerate(zip(self.nro[offset:offset + 16], broken_expected)) if actual != declared],
            [12], "必须正好报出被改坏的那一个字节")
        with self.assertRaises(AssertionError):
            self.assertEqual(self.nro[broken_offset:broken_offset + 16], broken_expected,
                             "ManagerUpdate 的台账与 NRO 不一致")

    def test_the_judge_rejects_a_known_pc_relative_site(self):
        """判官自检：rebuild-mount-points 现在的靶点(函数唯一 ret)含 bl，必须被判为不可挂。"""
        site = self.nro[0x3B36F4:0x3B36F4 + 16]
        words = [int.from_bytes(site[i:i + 4], "little") for i in range(0, 16, 4)]
        self.assertTrue(any(is_pc_relative(w) for w in words),
                        "该 ret 处含 bl，判定表必须命中，否则判定表本身失效")


if __name__ == "__main__":
    unittest.main()
