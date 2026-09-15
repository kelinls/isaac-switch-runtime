"""入口中继的编码、槽布局与拒绝规则（宿主机纯静态/纯函数测试）。

被测对象：`runtime/source/relay/entry_relay.hpp`（纯头文件，只依赖 `<cstddef>`/`<cstdint>`）、
`runtime/source/relay/entry_relay.cpp`（真机侧写入路径）。

这里刻意**不比对魔数**，而是：
  1. 用本题里独立实现的 AArch64 参考编码器，核对头文件常量对应的**操作数**（寄存器/偏移）；
  2. 把常量**解码回跳转落点**，核对落点正是设计要求的槽内偏移（这是能挡住“偏移抄错”的不变量，
     魔数比对挡不住——`cbz x16, #8` 与 `cbz x16, #0x18` 都是合法编码，但前者会落到保留区）；
  3. 用 `runtime_constants.hpp` 里登记的真实 16 字节函数入口做 PC 相对指令判定的正/反例。
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
HEADER = SOURCE / "relay" / "entry_relay.hpp"
IMPLEMENTATION = SOURCE / "relay" / "entry_relay.cpp"
CONSTANTS = SOURCE / "runtime_constants.hpp"

# 被挂钩函数入口的 16 字节会被原样搬进槽里回放，因此这 16 字节必须能“换个地方执行还正确”。
# 这些指令的地址由 PC 算出，搬走后一律错，必须拒绝安装。
PC_RELATIVE_MNEMONICS = "adr / adrp / ldr(literal) / ldrsw(literal) / b / bl / b.cond / cbz / cbnz / tbz / tbnz"


def header_constants(text):
    """读出 `constexpr std::uint32_t kName = 0x...u;` 形式的常量。"""
    return {
        name: int(value, 16)
        for name, value in re.findall(
            r"constexpr std::uint32_t (k\w+) = (0x[0-9A-Fa-f]+)u;", text
        )
    }


def header_size_constants(text):
    """读出 `constexpr std::size_t kName = ...;` 形式的常量（十六进制或十进制）。"""
    return {
        name: int(value, 16) if value.lower().startswith("0x") else int(value, 10)
        for name, value in re.findall(
            r"constexpr std::size_t (k\w+) = (0[xX][0-9A-Fa-f]+|[0-9]+);", text
        )
    }


# --------------------------------------------------------------------------
# 独立实现的 AArch64 参考编码器 / 解码器（不复用头文件里的实现）
# --------------------------------------------------------------------------


def encode_adr(rd, delta):
    imm = delta & 0xFFFFFFFF
    return 0x10000000 | ((imm & 0x3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | (rd & 0x1F)


def encode_ldar(rt, rn):
    return 0xC8DFFC00 | ((rn & 0x1F) << 5) | (rt & 0x1F)


def encode_cbz(rt, delta):
    return 0xB4000000 | (((delta >> 2) & 0x7FFFF) << 5) | (rt & 0x1F)


def encode_br(rn):
    return 0xD61F0000 | ((rn & 0x1F) << 5)


def encode_ldr_literal(rt, delta):
    return 0x58000000 | (((delta >> 2) & 0x7FFFF) << 5) | (rt & 0x1F)


def sign_extend(value, bits):
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def decode_branch_target(word, pc):
    """解码 `cbz`/`cbnz`/`b.cond`/`b`/`bl` 这类 ADDR_PCREL19/26 的相对落点。"""
    if (word & 0xFC000000) in (0x14000000, 0x94000000):
        return pc + (sign_extend(word & 0x03FFFFFF, 26) << 2)
    return pc + (sign_extend((word >> 5) & 0x7FFFF, 19) << 2)


def decode_literal_target(word, pc):
    return pc + (sign_extend((word >> 5) & 0x7FFFF, 19) << 2)


def decode_adr_target(word, pc):
    imm = ((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 0x3)
    return pc + sign_extend(imm, 21)


def decode_register(word):
    """Rd/Rt 在低 5 位（`adr`/`cbz`/`ldr`(literal)）。"""
    return word & 0x1F


def decode_branch_register(word):
    """`br`/`blr` 的 Rn 在 9:5。"""
    return (word >> 5) & 0x1F


def pc_relative_rules(text):
    """从被测头文件里读出 `kPcRelativeRules` 的 (mask, value) 判定表。"""
    table = re.search(r"kPcRelativeRules\[\] = \{(.*?)\n\};", text, re.DOTALL)
    if table is None:
        raise AssertionError("头文件里找不到 kPcRelativeRules 判定表")
    return [
        (int(mask, 16), int(value, 16))
        for mask, value in re.findall(r"\{(0x[0-9A-Fa-f]+)u, (0x[0-9A-Fa-f]+)u\}", table.group(1))
    ]


def is_pc_relative(word, rules):
    return any((word & mask) == value for mask, value in rules)


def words_of(data):
    return [int.from_bytes(data[index:index + 4], "little") for index in range(0, len(data), 4)]


def registered_entries(text):
    """读出 `runtime_constants.hpp` 里所有 16 字节的 `*ExpectedBytes` / `*ExpectedOriginal`。"""
    entries = {}
    for match in re.finditer(r"std::array<u8, 16> (\w+) = \{(.*?)\};", text, re.DOTALL):
        data = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", match.group(2)))
        if len(data) == 16:
            entries[match.group(1)] = data
    return entries


class EntryRelayEncodingTests(unittest.TestCase):
    def setUp(self):
        self.header = HEADER.read_text(encoding="utf-8")
        self.words = header_constants(self.header)
        self.sizes = header_size_constants(self.header)

    # ------------------------------------------------------------------
    # 机器码
    # ------------------------------------------------------------------

    def test_stub_and_fallback_words_match_the_reference_encoder(self):
        """桩与回退入口的每个字都要等于参考编码器对目标操作数的编码。"""
        expected = {
            "kStubAdrX17Word": encode_adr(17, 0x18),
            "kStubLdarX16Word": encode_ldar(16, 17),
            "kStubBrX16Word": encode_br(16),
            "kFallbackLdrX16Word": encode_ldr_literal(16, 8),
            "kFallbackBrX16Word": encode_br(16),
            "kEntryLdrX16Word": encode_ldr_literal(16, 8),
            "kEntryBrX16Word": encode_br(16),
        }
        for name, word in expected.items():
            self.assertIn(name, self.words, name)
            self.assertEqual(self.words[name], word, f"{name} 编码不符")
        # 规范里点名的几个常量值（与参考编码器结果一致）
        self.assertEqual(self.words["kStubAdrX17Word"], 0x100000D1)
        self.assertEqual(self.words["kStubLdarX16Word"], 0xC8DFFE30)
        self.assertEqual(self.words["kStubBrX16Word"], 0xD61F0200)
        self.assertEqual(self.words["kFallbackLdrX16Word"], 0x58000050)
        self.assertEqual(self.words["kEntryLdrX16Word"], 0x58000050)
        self.assertEqual(self.words["kEntryBrX16Word"], 0xD61F0200)

    def test_dispatch_uses_br_not_blr(self):
        """派发桩必须是 `br x16`（0xD61F0200）而不是 `blr x16`（0xD63F0200）：
        入口处的 x30 是游戏调用方的返回地址，`br` 让回调的 `ret` 直接回到游戏；
        `blr` 会把返回地址写到槽 +0x10 的保留区（8 个零字节），必然崩溃。"""
        self.assertEqual(self.words["kStubBrX16Word"], encode_br(16))
        self.assertNotEqual(self.words["kStubBrX16Word"], 0xD63F0200)
        self.assertEqual(decode_branch_register(self.words["kStubBrX16Word"]), 16)

    def test_cbz_lands_exactly_on_the_fallback_entry(self):
        """`cbz` 的落点必须正好是槽 +0x20 的回退入口。

        这是本次实现相对口头规范做的**唯一**偏移修正：规范给的 `cbz x16, #8`
        （0xB4000050）按“cbz 自身地址 +0x08 + 0x8”算落到槽 +0x10，那里是 8 个零字节
        （未定义指令），槽为 0 时必崩。正确偏移是 0x18（落点槽 +0x20）。
        """
        cbz = self.words["kStubCbzX16Word"]
        self.assertEqual(cbz, encode_cbz(16, 0x18))
        self.assertEqual(cbz, 0xB40000D0)
        self.assertEqual(decode_register(cbz), 16)
        # 用解码而不是魔数：落点必须正好是回退入口，且**不能**是保留区
        landing = decode_branch_target(cbz, self.sizes["kSlotStubOffset"] + 0x8)
        self.assertEqual(landing, self.sizes["kSlotFallbackOffset"])
        self.assertNotEqual(landing, self.sizes["kSlotReservedOffset"])
        self.assertEqual(decode_branch_target(0xB4000050, 0x08), self.sizes["kSlotReservedOffset"])

    def test_stub_reaches_the_callback_slot(self):
        """`adr x17, #0x18` 必须正好指向槽里登记回调地址的位置。"""
        adr = self.words["kStubAdrX17Word"]
        self.assertEqual(decode_register(adr), 17)
        self.assertEqual(decode_adr_target(adr, self.sizes["kSlotStubOffset"]),
                         self.sizes["kSlotCallbackOffset"])
        # 桩里的 `ldar x16, [x17]` 就是读那个位置；`br x16` 用的是同一个寄存器
        self.assertEqual(self.words["kStubLdarX16Word"], encode_ldar(16, 17))
        self.assertEqual(decode_branch_register(self.words["kStubBrX16Word"]), 16)

    def test_fallback_jump_uses_its_own_literal_and_targets_instruction_five(self):
        """回退入口：`ldr x16, #8` 的 literal 必须是槽 +0x38，且槽尾存的必须是 target+16。"""
        ldr = self.words["kFallbackLdrX16Word"]
        self.assertEqual(decode_register(ldr), 16)
        self.assertEqual(decode_literal_target(ldr, self.sizes["kSlotFallbackJumpOffset"]),
                         self.sizes["kSlotFallbackTargetOffset"])
        self.assertEqual(self.words["kFallbackBrX16Word"], 0xD61F0200)
        self.assertEqual(self.sizes["kSlotFallbackTargetOffset"] + 8, self.sizes["kSlotBytes"])
        self.assertEqual(self.sizes["kOriginalEntryBytes"], 16)

    def test_entry_patch_is_one_absolute_jump_in_sixteen_bytes(self):
        """入口改写的 literal 必须紧跟在 `ldr` 后面（+8），16 字节刚好装下一次绝对跳转。"""
        ldr = self.words["kEntryLdrX16Word"]
        self.assertEqual(ldr, 0x58000050)
        self.assertEqual(decode_register(ldr), 16)
        self.assertEqual(decode_literal_target(ldr, 0x00), 0x08)
        self.assertEqual(self.words["kEntryBrX16Word"], 0xD61F0200)
        self.assertEqual(self.sizes["kEntryPatchBytes"], 16)

    # ------------------------------------------------------------------
    # 槽布局
    # ------------------------------------------------------------------

    def test_slot_layout_offsets(self):
        self.assertEqual(self.sizes["kSlotStride"], 0x40)
        self.assertEqual(self.sizes["kSlotStubOffset"], 0x00)
        self.assertEqual(self.sizes["kSlotReservedOffset"], 0x10)
        self.assertEqual(self.sizes["kSlotCallbackOffset"], 0x18)
        self.assertEqual(self.sizes["kSlotFallbackOffset"], 0x20)
        self.assertEqual(self.sizes["kSlotFallbackJumpOffset"], 0x30)
        self.assertEqual(self.sizes["kSlotFallbackTargetOffset"], 0x38)
        self.assertEqual(self.sizes["kSlotBytes"], 0x40)
        # 槽内各段必须首尾相接、不重叠，且正好填满 0x40
        self.assertEqual(self.sizes["kSlotStubOffset"] + 0x10, self.sizes["kSlotReservedOffset"])
        self.assertEqual(self.sizes["kSlotReservedOffset"] + 8, self.sizes["kSlotCallbackOffset"])
        self.assertEqual(self.sizes["kSlotCallbackOffset"] + 8, self.sizes["kSlotFallbackOffset"])
        self.assertEqual(self.sizes["kSlotFallbackOffset"] + 0x10,
                         self.sizes["kSlotFallbackJumpOffset"])
        self.assertEqual(self.sizes["kSlotFallbackJumpOffset"] + 8,
                         self.sizes["kSlotFallbackTargetOffset"])

    def test_arena_capacity_covers_max_relays(self):
        self.assertEqual(self.sizes["kArenaBytes"], 0x1000)
        # 一页满槽：64 × 0x40 = 0x1000。容量是常量而非硬件限制，兼容层的挂点数会超过 16。
        self.assertEqual(self.sizes["kMaxRelays"], 64)
        self.assertLessEqual(self.sizes["kMaxRelays"] * self.sizes["kSlotStride"],
                             self.sizes["kArenaBytes"])
        # 竞技场必须正好一页（JIT_CREATE 的对齐要求），且槽步长是 64 字节对齐
        self.assertEqual(self.sizes["kArenaBytes"] % self.sizes["kPageBytes"], 0)
        self.assertEqual(self.sizes["kSlotStride"] % 0x40, 0)

    def test_slot_offset_is_index_times_stride(self):
        self.assertIn("constexpr std::size_t SlotOffset(std::size_t index)", self.header)
        self.assertIn("return index * kSlotStride;", self.header)

    # ------------------------------------------------------------------
    # 竞技场确实落在本模块的 .text（不碰游戏映像）
    # ------------------------------------------------------------------

    def test_arena_is_a_jit_section_in_our_own_module_text(self):
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("JIT_CREATE(EntryRelayArena, kArenaBytes);", implementation)
        self.assertIn('#include "lib/util/sys/jit.hpp"', implementation)
        # 设备侧路径只改目标入口的 16 字节与自己的槽，绝不写游戏映像的代码洞
        self.assertIn("exl::util::RwPages entryPages(target, kEntryPatchBytes);", implementation)
        self.assertNotIn("build_patches", implementation)

    def test_entry_relay_does_not_depend_on_hook_manager_or_probe_sources(self):
        """本机制必须自包含：不得 include `hook_manager.*`、`probe/**` 或引擎常量表。"""
        for path in (HEADER, IMPLEMENTATION):
            text = path.read_text(encoding="utf-8")
            includes = re.findall(r'^\s*#include\s+["<]([^">]+)[">]', text, re.MULTILINE)
            self.assertTrue(includes, f"{path} 没有任何 include")
            for include in includes:
                for forbidden in ("hook_manager", "probe/", "runtime_constants"):
                    self.assertNotIn(forbidden, include, f"{path}: {include}")

    def test_implementation_never_writes_game_image_code_caves(self):
        """写入路径只能碰（a）目标入口 16 字节、（b）自己的槽：不得出现代码洞/IPS 相关符号。"""
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        for forbidden in ("CodeOffset", "CaveOffset", "build_patches", "IPSOffset"):
            self.assertNotIn(forbidden, implementation, forbidden)

    # ------------------------------------------------------------------
    # PC 相对指令判定
    # ------------------------------------------------------------------

    def test_rules_cover_every_pc_relative_instruction_class(self):
        rules = pc_relative_rules(self.header)
        self.assertEqual(len(rules), 11)
        # 每一类一条：b / bl / adr / adrp / ldr(literal) / ldrsw / b.cond / cbz / cbnz / tbz / tbnz
        synthetic = {
            "b": 0x14000005,
            "bl": 0x94000005,
            "adr": encode_adr(0, 0x20),
            "adrp": 0x90000020,
            "ldr(literal)": 0x58000040,
            "ldr(literal, SIMD)": 0x1C000040,
            "prfm(literal)": 0xD8000040,
            "ldrsw(literal)": 0x98000040,
            "b.cond": 0x54000040,
            "cbz": encode_cbz(0, 0x40),
            "cbnz": 0x35000040,
            "tbz": 0x36000040,
            "tbnz": 0x37000040,
        }
        for mnemonic, word in synthetic.items():
            self.assertTrue(is_pc_relative(word, rules), f"{mnemonic} ({word:#010x}) 未被判定为 PC 相对")

    def test_rules_accept_pure_prologues(self):
        """纯序言（本机制唯一允许的目标形态）不得被误判。"""
        rules = pc_relative_rules(self.header)
        prologue = bytes.fromhex("ffc302d1e83b00fdfd7b08a9fd030291")  # Manager::Render 真实序言
        for word in words_of(prologue):
            self.assertFalse(is_pc_relative(word, rules), f"{word:#010x} 被误判")

    def test_registered_real_entries_are_classified_consistently(self):
        """拿 `runtime_constants.hpp` 里登记的真实 16 字节入口做正/反例。"""
        rules = pc_relative_rules(self.header)
        entries = registered_entries(CONSTANTS.read_text(encoding="utf-8"))
        self.assertGreater(len(entries), 40, "登记的真实入口太少，正反例失去意义")

        accepted = {name for name, data in entries.items()
                    if not any(is_pc_relative(word, rules) for word in words_of(data))}
        rejected = set(entries) - accepted
        # 纯序言的正例（已被各种 Hook 直接使用过、确认是函数序言）
        for name in (
            "kManagerUpdateExpectedBytes",
            "kManagerRenderExpectedBytes",
            "kManagerLoadConfigsExpectedBytes",
            "kPreGetCollectibleRelayExpectedOriginal",
        ):
            self.assertIn(name, accepted, name)
        # 反例：这些登记字节里确实含 PC 相对指令，本机制必须拒绝（否则回放会算错地址）
        self.assertIn("kManagerIsActionPressedExpectedBytes", rejected)  # adrp + b（尾调用 thunk）
        self.assertIn("kFontDestructorExpectedBytes", rejected)  # b
        self.assertIn("kLevelIsAscentExpectedBytes", rejected)  # b.cond
        self.assertIn("kSpriteSetFrameExpectedBytes", rejected)  # cbz
        self.assertIn("kGameFileOpenReadExpectedBytes", rejected)  # adrp
        self.assertIn("kRebuildMountPointsRelayExpectedOriginal", rejected)  # bl（调用点，非序言）


if __name__ == "__main__":
    unittest.main()
