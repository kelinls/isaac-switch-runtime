import unittest
from pathlib import Path

try:
    from .test_support import TARGET_BUILD_ID, find_target_status, step, synthetic_module
except ImportError:
    from test_support import TARGET_BUILD_ID, find_target_status, step, synthetic_module


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
TARGET_PATH = r"D:\Projekte\P4\isaac-ngModDLC\Platforms\NX\dlls_us\submission\Repentance.nrs"


class RuntimeStateTests(unittest.TestCase):
    def test_title_mismatch_disables_without_scan(self):
        self.assertEqual(step("Cold", title_ok=False, module=None), "Disabled")

    def test_cold_title_match_waits_for_module(self):
        self.assertEqual(step("Cold", title_ok=True, module=None), "WaitingForModule")

    def test_runtime_context_state_model_covers_all_transitions(self):
        self.assertEqual(step("Cold", title_checked=False), "Cold")
        self.assertEqual(step("Cold", title_checked=True, fatal_failure=True), "Disabled")
        self.assertEqual(step("Cold", title_checked=True, title_ok=False), "Disabled")
        self.assertEqual(step("WaitingForModule", title_checked=True, title_ok=True, module=None), "WaitingForModule")
        self.assertEqual(step("WaitingForModule", title_checked=True, title_ok=True, build_mismatch=True), "Disabled")
        self.assertEqual(step("WaitingForModule", title_checked=True, title_ok=True,
                              module="valid", hook_attempted=False, hook_ok=True), "Disabled")
        self.assertEqual(step("WaitingForModule", title_checked=True, title_ok=True,
                              module="valid", hook_attempted=True, hook_ok=False), "Disabled")
        self.assertEqual(step("WaitingForModule", title_checked=True, title_ok=True,
                              module="valid", hook_attempted=True, hook_ok=True), "Ready")
        self.assertEqual(step("Ready", title_checked=True, fatal_failure=True), "Ready")
        self.assertEqual(step("Disabled", title_checked=True, title_ok=True, module="valid",
                              hook_attempted=True, hook_ok=True), "Disabled")

    def test_cpp_runtime_context_exposes_model_fields_and_step(self):
        header = (SOURCE / "runtime_state.hpp").read_text()
        implementation = (SOURCE / "runtime_state.cpp").read_text()
        for field in ("titleChecked", "titleOk", "fatalFailure", "moduleFound",
                      "buildMismatch", "hookAttempted", "hookSucceeded"):
            self.assertIn(field, header)
        self.assertIn("RuntimeState Step(RuntimeState state, const RuntimeContext& context)", implementation)

    def test_title_query_failure_is_published_before_worker_and_worker_exits_without_scan(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        entry_start = source.index('extern "C" void exl_main')
        entry = source[entry_start:source.index('extern "C" NORETURN', entry_start)]
        self.assertLess(entry.index("StartupStatus::TitleQueryFailed"), entry.index("svcCreateThread"))
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        startup = worker[worker.index("const StartupStatus startup"):worker.index("for (u32 attempt")]
        self.assertIn("startup != StartupStatus::TitleOk", startup)
        self.assertIn("svcExitThread();", startup)
        self.assertNotIn("ScanTargetModule", startup)

    def test_default_runtime_does_not_reference_probe_logger(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        entry_start = source.index('extern "C" void exl_main')
        entry = source[entry_start:source.index('extern "C" NORETURN', entry_start)]
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertNotIn("probe_logger.hpp", source)
        self.assertNotIn("ProbeLogger", source)
        self.assertNotIn("g_Logger", entry)
        self.assertNotIn("g_Logger", worker)

    def test_waiting_for_module_stays_waiting(self):
        self.assertEqual(step("WaitingForModule", title_ok=True, module=None), "WaitingForModule")

    def test_valid_module_reaches_ready_only_after_hook(self):
        self.assertEqual(step("WaitingForModule", title_ok=True, module="valid", hook_ok=True), "Ready")
        self.assertEqual(step("WaitingForModule", title_ok=True, module="valid", hook_ok=False), "Disabled")

    def test_build_mismatch_is_distinguished_from_missing_module(self):
        mismatch = synthetic_module(TARGET_PATH, bytes(32))
        wrong_name = synthetic_module(TARGET_PATH.replace("Repentance.nrs", "Other.nrs"), TARGET_BUILD_ID)
        self.assertEqual(find_target_status(mismatch, TARGET_BUILD_ID)[0], "BuildMismatch")
        self.assertEqual(find_target_status(wrong_name, TARGET_BUILD_ID)[0], "NotFound")

    def test_build_mismatch_is_not_gated_on_manager_update_window(self):
        mismatch = synthetic_module(TARGET_PATH, bytes(32))
        self.assertLess(mismatch.text_size, 0x3F8DB8 + 16)
        self.assertEqual(find_target_status(mismatch, TARGET_BUILD_ID)[0], "BuildMismatch")
        scanner = (SOURCE / "module_finder.cpp").read_text()
        scan = scanner[scanner.index("TargetModuleScanResult ScanTargetModule"):]
        self.assertNotIn("targetOffset", scan)

    def test_title_query_uses_program_id_and_mismatch_exits_worker_without_scanning(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        entry_start = source.index('extern "C" void exl_main')
        entry = source[entry_start:source.index('extern "C" NORETURN', entry_start)]
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertIn("InfoType_ProgramId", entry)
        self.assertIn("svcGetInfo", entry)
        self.assertIn("StartupStatus::TitleMismatch", entry)
        self.assertLess(entry.index("StartupStatus::TitleMismatch"), entry.index("svcCreateThread"))
        mismatch = worker[worker.index("if (startup != StartupStatus::TitleOk)"):worker.index("for (u32 attempt")]
        self.assertIn("svcExitThread();", mismatch)
        self.assertNotIn("ScanTargetModule", mismatch)

    def test_worker_classifies_build_mismatch_before_hooking(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertIn("TargetModuleScanStatus::BuildMismatch", worker)
        self.assertIn("SetRuntimeState({.buildMismatch = true});", worker)
        self.assertLess(worker.index("TargetModuleScanStatus::BuildMismatch"), worker.index("TryInstallManagerUpdateHook"))

    def test_module_found_attempts_hook_without_logging(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text()
        hook = (SOURCE / "hook_manager.cpp").read_text()
        self.assertIn("TryInstallManagerUpdateHook(*scan.module)", entry)
        self.assertNotIn("module_found", entry)
        self.assertNotIn("ProbeLogger", hook)

    def test_manager_update_verifies_ips_relay_before_publishing_callback(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        manager = (ROOT / "source/hook_manager.cpp").read_text()

        for token in (
            "kManagerRelayCodeOffset",
            "kManagerRelayFallbackOffset",
            "kManagerRelaySlotOffset",
            "kManagerRelayExpectedBytes",
            "kManagerRelayExpectedEntry",
        ):
            with self.subTest(token=token):
                self.assertIn(token, constants)
        self.assertIn("RelayPatchMismatch", manager)
        self.assertIn("RelaySlotNotEmpty", manager)
        self.assertIn("RelayPublishFailed", manager)
        self.assertIn("VerifyManagerRelay", manager)
        self.assertIn("__atomic_load_n", manager)

    def test_manager_update_treats_relay_slot_as_runtime_state_not_fixed_bridge_bytes(self):
        manager = (ROOT / "source/hook_manager.cpp").read_text()

        self.assertIn("!module.Contains(relay, kManagerRelayExpectedBytes.size())", manager)
        self.assertIn("IsMappedRxModuleCodeWindow(relay, kManagerRelayExpectedBytes.size())", manager)
        self.assertIn(
            "std::array<u8, kManagerRelayExpectedBytes.size() - sizeof(uintptr_t)> bridge", manager
        )
        self.assertNotIn("std::array<u8, kManagerRelayExpectedBytes.size()> bridge", manager)
        self.assertLess(manager.index("__atomic_load_n(rwSlot"), manager.index("RelaySlotNotEmpty"))

    def test_three_hook_outcomes_are_classified_without_reverification(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        hook = (SOURCE / "hook_manager.cpp").read_text()
        self.assertIn("RelayPatchMismatch", hook)
        self.assertIn("HookInstallResult::Success", source)
        self.assertIn("else {\n                SetRuntimeState({.moduleFound = true, .hookAttempted = true});", source)
        self.assertNotIn("g_Logger", source)
        installer_start = hook.index("HookInstallResult TryInstallManagerUpdateHook")
        default_installer = hook[
            installer_start:
            hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11", installer_start)
        ]
        self.assertEqual(default_installer.count("VerifyManagerUpdate(module, &target)"), 1)

    def test_readme_and_make_contract(self):
        readme = (ROOT / "README.md").read_text()
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("devkitpro/devkita64:latest", readme)
        self.assertIn("atmosphere/contents/010021C000B6A000/exefs/main.npdm", readme)
        self.assertIn("atmosphere/contents/010021C000B6A000/exefs/subsdk9", readme)
        self.assertIn("isaac-stage12-backup-", readme)
        self.assertIn("不得用 `rm -rf` 删除整个 Title 目录", readme)
        self.assertIn("默认 Runtime 不产生", readme)
        self.assertIn("| Atmosphere", readme)
        self.assertIn("$(OUT)/$(SD_OUT)/$(BINARY_NAME)", makefile)

    def test_worker_uses_lower_priority_than_the_game_main_thread(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        self.assertIn("g_WorkerStack + sizeof(g_WorkerStack), 0x3A, 2", source)

    def test_worker_does_not_retry_log_opening_after_hook_installation(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertNotIn("g_Logger.Open", worker)
        self.assertNotIn("FlushPendingHeartbeat", worker)

    def test_worker_does_not_depend_on_fixed_sdk_initialization_delay(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertNotIn("svcSleepThread(5'000'000'000)", worker)

    def test_worker_waits_thirty_seconds_for_the_late_game_module(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text()
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        self.assertIn("kTargetModuleScanAttemptLimit = 300", constants)
        self.assertIn("kTargetModuleScanIntervalNanoseconds = 100'000'000", constants)
        self.assertIn("attempt < kTargetModuleScanAttemptLimit", worker)
        self.assertIn("attempt + 1 < kTargetModuleScanAttemptLimit", worker)
        self.assertIn("svcSleepThread(kTargetModuleScanIntervalNanoseconds)", worker)


if __name__ == "__main__":
    unittest.main()
