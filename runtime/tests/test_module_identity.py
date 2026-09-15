import unittest
import re
from pathlib import Path

try:
    from .test_support import (encode_path_record, find_target, find_target_compatible,
                               read_path_record, scan_targets, synthetic_module, target_window_valid)
except ImportError:
    from test_support import (encode_path_record, find_target, find_target_compatible,
                              read_path_record, scan_targets, synthetic_module, target_window_valid)

TARGET_BUILD_ID = bytes.fromhex("91C73FDD575061318D68886316AFEAC72388B2AB") + bytes(12)
TARGET_PATH = r"D:\Projekte\P4\isaac-ngModDLC\Platforms\NX\dlls_us\submission\Repentance.nrs"
ROOT = Path(__file__).resolve().parents[1]


class ModuleIdentityTests(unittest.TestCase):
    def test_repentance_path_and_build_id_are_accepted(self):
        module = synthetic_module(TARGET_PATH, TARGET_BUILD_ID)
        self.assertEqual(find_target(module, TARGET_BUILD_ID).text_start, module.text_start)

    def test_wrong_name_or_build_id_is_rejected(self):
        self.assertIsNone(find_target(synthetic_module(TARGET_PATH.replace("Repentance.nrs", "Other.nrs"), TARGET_BUILD_ID), TARGET_BUILD_ID))
        self.assertIsNone(find_target(synthetic_module(TARGET_PATH, bytes(32)), TARGET_BUILD_ID))

    def test_invalid_nro_header_is_rejected(self):
        module = synthetic_module(TARGET_PATH, TARGET_BUILD_ID, valid_header=False)
        self.assertIsNone(find_target(module, TARGET_BUILD_ID))

    def test_mod0_outside_rodata_is_rejected(self):
        module = synthetic_module(TARGET_PATH, TARGET_BUILD_ID, mod0_in_rodata=False)
        self.assertIsNone(find_target(module, TARGET_BUILD_ID))

    def test_scanner_masks_all_memory_types(self):
        source = (ROOT / "source/module_finder.cpp").read_text()
        self.assertIn("(info.type & MemState_Type) == MemType_ModuleCodeStatic", source)
        self.assertIn("(ro.type & MemState_Type) != MemType_ModuleCodeStatic", source)
        self.assertIn("(data.type & MemState_Type) != MemType_ModuleCodeMutable", source)
        self.assertNotIn("== MemType_CodeStatic", source)
        self.assertNotIn("!= MemType_CodeMutable", source)
        self.assertIn("Contains(range, mod0Address, sizeof(u32), false)", source)

    def test_target_module_exposes_text_size_and_validates_16_byte_window(self):
        header = (ROOT / "source/module_finder.hpp").read_text()
        hook_source = (ROOT / "source/hook_manager.cpp").read_text()
        target_module = re.search(r"struct TargetModule \{(.*?)\};", header, re.S).group(1)
        self.assertIn("size_t textSize", target_module)
        self.assertIn("kManagerUpdateFileOffset", hook_source)
        self.assertFalse(target_window_valid(0x100, 0x3F8DB8))
        self.assertTrue(target_window_valid(0x3F8DB8 + 16, 0x3F8DB8))
        self.assertFalse(target_window_valid(0x3F8DB8 + 15, 0x3F8DB8))
        self.assertFalse(target_window_valid(1 << 64, (1 << 64) - 8))

    def test_scan_keeps_build_mismatch_but_compat_wrapper_rejects_short_text(self):
        short_text = synthetic_module(TARGET_PATH, TARGET_BUILD_ID)
        self.assertEqual(scan_targets([short_text], TARGET_BUILD_ID)[0], "Found")
        self.assertIsNone(find_target_compatible(short_text, TARGET_BUILD_ID, 0x3F8DB8))

    def test_scan_continues_after_build_mismatch_to_later_correct_module(self):
        mismatch = synthetic_module(TARGET_PATH, bytes(32))
        correct = synthetic_module(TARGET_PATH, TARGET_BUILD_ID)
        self.assertEqual(scan_targets([mismatch, correct], TARGET_BUILD_ID)[0], "Found")

    def test_scan_continues_after_malformed_candidate_to_later_correct_module(self):
        malformed = synthetic_module(TARGET_PATH, TARGET_BUILD_ID, valid_header=False)
        correct = synthetic_module(TARGET_PATH, TARGET_BUILD_ID)
        self.assertEqual(scan_targets([malformed, correct], TARGET_BUILD_ID)[0], "Found")

    def test_scan_returns_build_mismatch_when_no_correct_candidate_exists(self):
        mismatch = synthetic_module(TARGET_PATH, bytes(32))
        self.assertEqual(scan_targets([mismatch], TARGET_BUILD_ID)[0], "BuildMismatch")

    def test_cpp_wrapper_restores_task3_window_postcondition(self):
        source = (ROOT / "source/module_finder.cpp").read_text()
        wrapper = source[source.index("std::optional<TargetModule> FindTargetModule"):]
        self.assertIn("kManagerUpdateFileOffset % 4", wrapper)
        self.assertIn("kManagerUpdateExpectedBytes.size()", wrapper)
        self.assertIn("module->Contains", wrapper)

    def test_cpp_scanner_advances_past_malformed_data_candidate(self):
        source = (ROOT / "source/module_finder.cpp").read_text()
        scanner = source[source.index("TargetModuleScanResult ScanTargetModule"):source.index("std::optional<TargetModule> FindTargetModule")]
        data_check = scanner[scanner.index("if (data.addr != dataQueryAddress"):scanner.index("ModuleRange range")]
        self.assertIn("cursor = next;", data_check)
        self.assertIn("continue;", data_check)

    def test_module_wait_sleep_is_only_in_worker(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        entry = source[source.index('extern "C" void exl_main'):source.index('extern "C" NORETURN')]
        self.assertIn("svcSleepThread", worker)
        self.assertNotIn("svcSleepThread", entry)

    def test_worker_handle_is_closed_on_exit_paths(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        self.assertIn("svcCloseHandle(g_WorkerHandle)", source)
        self.assertIn("g_WorkerHandle = INVALID_HANDLE", source)
        self.assertEqual(source.count("CloseWorkerHandle();"), 2)

    def test_path_record_accepts_declared_length_without_terminator(self):
        record = encode_path_record(TARGET_PATH)
        self.assertEqual(int.from_bytes(record[4:8], "little"), 0x4C)
        self.assertEqual(record[8 + 0x4C:], b"")
        self.assertEqual(read_path_record(record + b"MOD0"), TARGET_PATH)

    def test_path_record_rejects_embedded_terminator(self):
        record = bytearray(encode_path_record(TARGET_PATH))
        record[12] = 0
        self.assertIsNone(read_path_record(bytes(record)))


if __name__ == "__main__":
    unittest.main()
