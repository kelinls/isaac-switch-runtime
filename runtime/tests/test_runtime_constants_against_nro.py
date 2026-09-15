"""守卫常量必须与真实游戏镜像逐字节一致。

`hook_manager.cpp` 的每个 `Verify*` 都用 `memcmp` 精确比较 16 字节守卫：**抄错一个字节就等于
静默关掉一个绑定**，没有日志也没有报错。2026-09-13 就是这样发现 `kGameIsGreedModeExpectedBytes`
第 10 字节写成 `0xB8`（真值 `0x68`）的——`Game:IsGreedMode` 在真机上从未可用。
`tools/verify_runtime_constants_against_nro.py` 是同一套判据，两个入口共用 `check()`。
"""

import unittest
from pathlib import Path

from tools.verify_runtime_constants_against_nro import check


ROOT = Path(__file__).resolve().parents[2]
CONSTANTS = ROOT / "runtime/source/runtime_constants.hpp"
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class GuardCheckerTests(unittest.TestCase):
    """Judge the judge: the checker must fail on a wrong byte and accept a patch target."""

    CONSTANTS_TEXT = """
inline constexpr uintptr_t kFakeEntryOffset = 0x10;
inline constexpr std::array<u8, 4> kFakeEntryExpectedBytes = {
    0x11, 0x22, 0x33, 0x44,
};
inline constexpr uintptr_t kFakePatchedOffset = 0x40;
inline constexpr std::array<u8, 4> kFakePatchedExpectedBytes = {
    0xAA, 0xBB, 0xCC, 0xDD,
};
inline constexpr uintptr_t kFakeOrphanCodeOffset = 0x60;
inline constexpr std::array<u8, 4> kFakeOrphanExpectedBytes = {
    0x55, 0x66, 0x77, 0x88,
};
"""

    def image(self) -> bytes:
        data = bytearray(0x80)
        data[0x10:0x14] = bytes.fromhex("11223344")
        data[0x40:0x44] = bytes.fromhex("AABBCCDD")
        data[0x60:0x64] = bytes.fromhex("55667788")
        return bytes(data)

    def test_matching_guard_passes_and_patch_target_is_classified_not_failed(self):
        report = check(self.CONSTANTS_TEXT, self.image(), patched={0x40})
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["patched_image"], 1)
        # `kFakeOrphanExpectedBytes` locates its bytes at `...CodeOffset`, which the exact-base
        # pairing deliberately does not guess; it is reported instead of force-paired.
        self.assertEqual(report["unpaired_arrays"], ["kFakeOrphanExpectedBytes"])

    def test_single_wrong_byte_is_reported_with_both_values(self):
        text = self.CONSTANTS_TEXT.replace("0x33", "0xB8")
        report = check(text, self.image(), patched=set())
        self.assertEqual(len(report["failures"]), 1)
        failure = report["failures"][0]
        self.assertEqual(failure["offset_name"], "kFakeEntryOffset")
        self.assertEqual(failure["status"], "mismatch")
        self.assertEqual(failure["actual_hex"], "11223344")
        self.assertEqual(failure["expected_hex"], "1122B844")

    def test_declared_size_must_match_the_listed_bytes(self):
        text = self.CONSTANTS_TEXT.replace(
            "std::array<u8, 4> kFakeEntryExpectedBytes", "std::array<u8, 6> kFakeEntryExpectedBytes"
        )
        report = check(text, self.image(), patched=set())
        self.assertEqual([entry["status"] for entry in report["failures"]], ["declared-size-mismatch"])

    def test_out_of_range_offset_is_a_failure(self):
        text = self.CONSTANTS_TEXT.replace("kFakeEntryOffset = 0x10", "kFakeEntryOffset = 0x1000")
        report = check(text, self.image(), patched=set())
        self.assertEqual([entry["status"] for entry in report["failures"]], ["out-of-range"])


@unittest.skipUnless(NRO.exists(), "user-provided Switch dump is not present")
class RuntimeConstantsAgainstNroTests(unittest.TestCase):
    def test_every_pairable_guard_matches_the_pristine_game_image(self):
        report = check(CONSTANTS.read_text(encoding="utf-8"), NRO.read_bytes())
        self.assertEqual(
            report["failures"], [],
            "守卫与镜像不符：" + ", ".join(
                f"{entry['offset_name']} 常量={entry['expected_hex']} 镜像={entry.get('actual_hex')}"
                for entry in report["failures"]
            ),
        )
        # A vacuous pass (nothing paired) must not be possible.
        self.assertGreaterEqual(report["matched"], 30)


if __name__ == "__main__":
    unittest.main()
