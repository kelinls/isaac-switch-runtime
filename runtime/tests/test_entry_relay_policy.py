"""入口中继的安装策略测试：在**宿主机**上编译并执行被测头文件的真实实现。

被测行为（全部来自 `runtime/source/relay/entry_relay.hpp` 的纯函数，不涉及真机）：
  * `BuildSlotImage` / `BuildEntryPatch` 生成的字节与本题独立实现的 Python 参考模型逐字节相同；
  * `EvaluateEntryRelayPreconditions` 的失败码：地址非法 1、入口字节不符 2、竞技场耗尽 3、
    含 PC 相对指令 5、通过 0；
  * “入口字节不符时一个字节都不许写”在实现侧的落点：判定发生在任何 `RwPages`/写入之前。

宿主机没有 C++ 编译器时该测试会 skip（并打印原因），不会把真机行为混进断言。
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
HEADER = SOURCE / "relay" / "entry_relay.hpp"
IMPLEMENTATION = SOURCE / "relay" / "entry_relay.cpp"

# 目标 / 回调 / 槽地址都用真实量级（模块基址 0x7100000000），避免“只有在真机上才算错”的假设。
TARGET = 0x710045C020         # Room::Init 的 NRO 文件偏移 0x45C020 + 基址
CALLBACK = 0x7100055120       # 我们模块里的 C++ 回调
SLOT_ADDRESS = 0x7100A00000   # 竞技场基址（槽 0）
# 真实序言：runtime_constants.hpp 的 kManagerRenderExpectedBytes
PROLOGUE = bytes.fromhex("ffc302d1e83b00fdfd7b08a9fd030291")
# 反例：含 b 的入口（kManagerIsActionPressedExpectedBytes 的第 2 条指令形态）
PC_RELATIVE_ENTRY = bytes.fromhex("803500f0" + "86fb0914" + "00000000" + "00000000")

DRIVER = r"""
#include "relay/entry_relay.hpp"
#include <cstdio>
#include <cstring>
using namespace isaac::runtime::relay;

static void dump(const char* label, const std::uint8_t* data, std::size_t size) {
    std::printf("%s=", label);
    for (std::size_t i = 0; i < size; ++i) std::printf("%02x", data[i]);
    std::printf("\n");
}

int main() {
    const std::uintptr_t target = TARGET_VALUE;
    const std::uintptr_t callback = CALLBACK_VALUE;
    const std::uintptr_t slotAddress = SLOT_ADDRESS_VALUE;
    const std::uint8_t prologue[16] = {PROLOGUE_BYTES};
    const std::uint8_t pcRelative[16] = {PC_RELATIVE_BYTES};

    std::uint8_t slot[kSlotBytes];
    std::memset(slot, 0xAA, sizeof(slot));
    BuildSlotImage(slot, target, prologue, callback);
    dump("slot", slot, sizeof(slot));

    std::uint8_t patch[kEntryPatchBytes];
    std::memset(patch, 0xAA, sizeof(patch));
    BuildEntryPatch(patch, slotAddress);
    dump("entry", patch, sizeof(patch));

    const std::uint8_t* current = prologue;
    std::printf("pre_ok=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, prologue, current, 0));
    std::printf("pre_lastslot=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, prologue, current, kMaxRelays - 1));
    std::printf("pre_exhausted=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, prologue, current, kMaxRelays));

    std::uint8_t mismatched[16];
    std::memcpy(mismatched, prologue, 16);
    mismatched[15] ^= 0x01;
    std::printf("pre_mismatch=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, prologue, mismatched, 0));
    std::printf("pre_pcrel=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, pcRelative, pcRelative, 0));
    std::printf("pre_null_target=%u\n", (unsigned)EvaluateEntryRelayPreconditions(0, callback, prologue, current, 0));
    std::printf("pre_null_callback=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, 0, prologue, current, 0));
    std::printf("pre_null_expected=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback, nullptr, current, 0));
    std::printf("pre_misaligned_target=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target + 2, callback, prologue, current, 0));
    std::printf("pre_misaligned_callback=%u\n", (unsigned)EvaluateEntryRelayPreconditions(target, callback + 1, prologue, current, 0));
    // 页尾 16 字节内（跨页）的入口必须拒绝
    std::printf("pre_page_straddle=%u\n", (unsigned)EvaluateEntryRelayPreconditions(0x7100400FF8, callback, prologue, current, 0));
    std::printf("slot_offset_0=%zu\n", SlotOffset(0));
    std::printf("slot_offset_1=%zu\n", SlotOffset(1));
    std::printf("slot_offset_last=%zu\n", SlotOffset(kMaxRelays - 1));
    std::printf("failure_codes=%u,%u,%u,%u,%u,%u\n",
                (unsigned)ToFailureCode(EntryRelayFailure::None),
                (unsigned)ToFailureCode(EntryRelayFailure::InvalidAddress),
                (unsigned)ToFailureCode(EntryRelayFailure::EntryMismatch),
                (unsigned)ToFailureCode(EntryRelayFailure::ArenaExhausted),
                (unsigned)ToFailureCode(EntryRelayFailure::WriteVerifyFailed),
                (unsigned)ToFailureCode(EntryRelayFailure::PcRelativeInstruction));
    return 0;
}
"""


def find_compiler():
    for candidate in (os.environ.get("CXX"), "clang++", "g++"):
        if candidate and shutil.which(candidate):
            return shutil.which(candidate)
    return None


def header_constant(name):
    """从 `entry_relay.hpp` 读出整数常量，供"由常量派生"的断言使用。

    容量（`kMaxRelays`）是常量而不是硬限制，拿它的地方一律派生，测试才不会在
    改容量时变成一条与行为无关的失败。
    """
    match = re.search(rf"constexpr std::size_t {name} = (0x[0-9A-Fa-f]+|\d+);",
                      HEADER.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"entry_relay.hpp 里找不到常量 {name}")
    return int(match.group(1), 0)


def reference_slot_image(target, callback, prologue):
    """独立实现的槽镜像参考模型（与头文件实现无关的另一份写法）。"""
    image = bytearray(0x40)
    image[0x00:0x04] = (0x100000D1).to_bytes(4, "little")  # adr x17, #0x18
    image[0x04:0x08] = (0xC8DFFE30).to_bytes(4, "little")  # ldar x16, [x17]
    image[0x08:0x0C] = (0xB40000D0).to_bytes(4, "little")  # cbz x16, #0x18 → 槽 +0x20
    image[0x0C:0x10] = (0xD61F0200).to_bytes(4, "little")  # br x16
    image[0x10:0x18] = bytes(8)                            # 保留
    image[0x18:0x20] = callback.to_bytes(8, "little")      # 回调槽
    image[0x20:0x30] = prologue                            # 回退入口：原入口 16 字节
    image[0x30:0x34] = (0x58000050).to_bytes(4, "little")  # ldr x16, #8
    image[0x34:0x38] = (0xD61F0200).to_bytes(4, "little")  # br x16
    image[0x38:0x40] = (target + 16).to_bytes(8, "little")
    return bytes(image)


def reference_entry_patch(slot_address):
    patch = bytearray(0x10)
    patch[0x00:0x04] = (0x58000050).to_bytes(4, "little")  # ldr x16, #8
    patch[0x04:0x08] = (0xD61F0200).to_bytes(4, "little")  # br x16
    patch[0x08:0x10] = slot_address.to_bytes(8, "little")
    return bytes(patch)


class EntryRelayPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()
        cls.output = {}
        cls.compile_error = None
        if cls.compiler is None:
            return
        driver = (
            DRIVER.replace("TARGET_VALUE", hex(TARGET))
            .replace("CALLBACK_VALUE", hex(CALLBACK))
            .replace("SLOT_ADDRESS_VALUE", hex(SLOT_ADDRESS))
            .replace("PROLOGUE_BYTES", ", ".join(f"0x{byte:02X}" for byte in PROLOGUE))
            .replace("PC_RELATIVE_BYTES", ", ".join(f"0x{byte:02X}" for byte in PC_RELATIVE_ENTRY))
        )
        cls.tempdir = tempfile.TemporaryDirectory(prefix="entry_relay_policy_")
        source = Path(cls.tempdir.name) / "entry_relay_policy_driver.cpp"
        source.write_text(driver, encoding="utf-8")
        binary = Path(cls.tempdir.name) / "driver"
        build = subprocess.run(
            [cls.compiler, "-std=c++23", "-Wall", "-Wextra", "-Werror",
             "-I", str(SOURCE), "-o", str(binary), str(source)],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            cls.compile_error = build.stderr.strip() or build.stdout.strip()
            return
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        if run.returncode != 0:
            cls.compile_error = f"driver 退出码 {run.returncode}: {run.stderr.strip()}"
            return
        for line in run.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                cls.output[key] = value

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tempdir", None) is not None:
            cls.tempdir.cleanup()

    def require_driver(self):
        if self.compiler is None:
            self.skipTest("宿主机没有可用的 C++ 编译器（clang++/g++），跳过真实头文件执行测试")
        if self.compile_error:
            self.fail(f"头文件在宿主机编译/执行失败：{self.compile_error}")

    # ------------------------------------------------------------------
    # 字节级：真实头文件实现 vs 独立 Python 参考模型
    # ------------------------------------------------------------------

    def test_slot_image_bytes_match_the_reference_model(self):
        self.require_driver()
        self.assertEqual(self.output["slot"],
                         reference_slot_image(TARGET, CALLBACK, PROLOGUE).hex())

    def test_entry_patch_bytes_match_the_reference_model(self):
        self.require_driver()
        self.assertEqual(self.output["entry"], reference_entry_patch(SLOT_ADDRESS).hex())

    def test_slot_words_and_tail_target(self):
        self.require_driver()
        raw = bytes.fromhex(self.output["slot"])
        words = [int.from_bytes(raw[index:index + 4], "little") for index in range(0, 0x10, 4)]
        self.assertEqual(words, [0x100000D1, 0xC8DFFE30, 0xB40000D0, 0xD61F0200])
        self.assertEqual(raw[0x10:0x18], bytes(8))
        self.assertEqual(int.from_bytes(raw[0x18:0x20], "little"), CALLBACK)
        self.assertEqual(raw[0x20:0x30], PROLOGUE)
        self.assertEqual(int.from_bytes(raw[0x38:0x40], "little"), TARGET + 16)

    def test_entry_patch_words_and_literal(self):
        self.require_driver()
        raw = bytes.fromhex(self.output["entry"])
        self.assertEqual(int.from_bytes(raw[0:4], "little"), 0x58000050)
        self.assertEqual(int.from_bytes(raw[4:8], "little"), 0xD61F0200)
        self.assertEqual(int.from_bytes(raw[8:16], "little"), SLOT_ADDRESS)

    # ------------------------------------------------------------------
    # 安装策略
    # ------------------------------------------------------------------

    def test_preconditions_pass_for_a_pure_prologue_target(self):
        self.require_driver()
        self.assertEqual(self.output["pre_ok"], "0")
        self.assertEqual(self.output["pre_lastslot"], "0")

    def test_entry_byte_mismatch_is_rejected(self):
        """入口现场字节与期望不符时必须拒绝（失败码 2），实现侧在写入之前就返回。"""
        self.require_driver()
        self.assertEqual(self.output["pre_mismatch"], "2")
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        install = implementation[implementation.index("bool TryInstallEntryRelay"):]
        precondition = install.index("EvaluateEntryRelayPreconditions")
        first_write = install.index("exl::util::RwPages")
        memcpy_to_target = install.index("std::memcpy(rwEntry")
        self.assertLess(precondition, first_write, "判定必须发生在任何 RwPages 写入之前")
        self.assertLess(precondition, memcpy_to_target, "判定必须发生在写目标入口之前")
        # 现场 16 字节只读地读出、从不先写后比
        self.assertIn("std::memcpy(current, reinterpret_cast<const void*>(target)", install)
        self.assertLess(install.index("std::memcpy(current"), precondition)

    def test_arena_exhaustion_is_reported(self):
        """用满 kMaxRelays 个槽之后返回失败码 3。"""
        self.require_driver()
        self.assertEqual(self.output["pre_exhausted"], "3")
        self.assertEqual(self.output["slot_offset_0"], "0")
        self.assertEqual(self.output["slot_offset_1"], "64")
        # 最后一个槽的偏移由 kMaxRelays 派生，不硬编码容量：容量改成一页满槽（64）
        # 时这里不该跟着改（硬编码 15 会在改容量后变成一条与实现无关的失败）。
        self.assertEqual(self.output["slot_offset_last"], str((header_constant("kMaxRelays") - 1) * 64))
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        install = implementation[implementation.index("bool TryInstallEntryRelay"):]
        self.assertIn("fetch_add", install)
        self.assertIn("if (index >= kMaxRelays)", install)
        self.assertIn("EntryRelayFailure::ArenaExhausted", install)

    def test_pc_relative_entry_is_rejected(self):
        self.require_driver()
        self.assertEqual(self.output["pre_pcrel"], "5")

    def test_invalid_addresses_are_rejected(self):
        self.require_driver()
        for key in ("pre_null_target", "pre_null_callback", "pre_null_expected",
                    "pre_misaligned_target", "pre_misaligned_callback", "pre_page_straddle"):
            self.assertEqual(self.output[key], "1", key)

    def test_failure_codes_are_the_documented_ones(self):
        self.require_driver()
        self.assertEqual(self.output["failure_codes"], "0,1,2,3,4,5")

    def test_write_verify_failure_path_exists(self):
        """写入失败要能被发现：读取一律用只读地址回读校验，失败记 4。"""
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("EntryRelayFailure::WriteVerifyFailed", implementation)
        self.assertIn("reinterpret_cast<const void*>(roSlot)", implementation)
        self.assertIn("reinterpret_cast<const void*>(target)", implementation)

    def test_writes_go_through_rw_pages_and_are_flushed(self):
        """写入必须走 RwPages 的可写别名，并且写完必须 Flush()（DCache + ICache）。"""
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("exl::util::RwPages entryPages(target, kEntryPatchBytes);", implementation)
        self.assertIn("entryPages.Flush();", implementation)
        self.assertIn("EntryRelayArena.Flush();", implementation)
        # 槽的刷新必须早于入口改写（否则可能跳到还没刷新的槽）
        self.assertLess(implementation.index("EntryRelayArena.Flush();"),
                        implementation.index("entryPages.Flush();"))
        # 竞技场通过 Jit 的 Rw/Flush 使用，不是自己另建一份
        self.assertIn("EntryRelayArena.GetRw()", implementation)
        self.assertIn("EntryRelayArena.GetRo()", implementation)

    def test_slot_allocation_is_atomic(self):
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("std::atomic<std::size_t> s_NextSlot{0};", implementation)
        self.assertIn("std::atomic<std::size_t> s_InstalledCount{0};", implementation)
        self.assertIn("std::atomic<std::uint32_t> s_LastFailure", implementation)

    def test_relay_sources_avoid_bool_atomics(self):
        """项目门禁禁止 runtime 源码里出现 `std::atomic<bool>`（见
        `test_manager_update_hook_audit.test_runtime_source_contains_no_bool_atomics`）。"""
        for path in (HEADER, IMPLEMENTATION):
            self.assertNotIn("std::atomic<bool>", path.read_text(encoding="utf-8"), str(path))

    def test_public_api_matches_the_contract(self):
        header = HEADER.read_text(encoding="utf-8")
        for signature in (
            "bool TryInstallEntryRelay(std::uintptr_t target, const void* expectedEntry16,",
            "std::uintptr_t callback, std::uintptr_t* outOriginalEntry);",
            "std::size_t InstalledCount();",
            "std::uint32_t LastFailure();",
        ):
            self.assertIn(signature, header, signature)
        # 回退入口就是槽 +0x20
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("*outOriginalEntry = roSlot + kSlotFallbackOffset;", implementation)
        self.assertIn("EntryRelayFailure::None", implementation)


if __name__ == "__main__":
    unittest.main()
