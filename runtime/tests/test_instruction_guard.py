import unittest
import re
from pathlib import Path

try:
    from .test_support import orig_after_commit, target_address, verify_bytes
except ImportError:
    from test_support import orig_after_commit, target_address, verify_bytes


EXPECTED = bytes.fromhex("ff4301d1fd7b01a9fd430091f71300f9")
ROOT = Path(__file__).resolve().parents[1]


class InstructionGuardTests(unittest.TestCase):
    def test_target_address_is_base_plus_file_offset(self):
        self.assertEqual(target_address(0x7100000000, 0x3F8DB8), 0x71003F8DB8)

    def test_instruction_guard_rejects_any_mismatch(self):
        self.assertTrue(verify_bytes(EXPECTED, EXPECTED))
        bad = EXPECTED[:-1] + bytes([EXPECTED[-1] ^ 1])
        self.assertFalse(verify_bytes(EXPECTED, bad))

    def test_expected_bytes_match_analyzed_nro(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        section = re.search(r"kManagerUpdateExpectedBytes = \{(.*?)\};", constants, re.S).group(1)
        actual = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", section))
        self.assertEqual(actual, EXPECTED)

    def test_trampoline_preserves_manager_update_contract(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback = source[source.index("static void Callback"):source.index("};", source.index("static void Callback"))]
        self.assertEqual(callback.count("Orig(self)"), 1)
        self.assertNotIn("Heartbeat", callback)
        self.assertNotIn("g_FrameCounter", callback)
        self.assertNotIn("ManagerUpdateHook::TryInstallAtPtr", source)
        self.assertNotIn("InstallAtOffset", source)

    def test_default_manager_update_callback_only_forwards_to_original(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback = source[source.index("static void Callback"):source.index("};", source.index("static void Callback"))]
        default_path = callback.split("#if defined(EXL_DIAGNOSTIC_STAGE)")[0]
        self.assertEqual(default_path.count("Orig(self)"), 1)
        self.assertNotIn("svcBreak", default_path)
        self.assertNotIn("g_FrameCounter", default_path)
        self.assertNotIn("Heartbeat", default_path)

    def test_lua_callback_errors_are_contained_without_disabling_the_callback(self):
        source = (ROOT / "source/lua_runtime.cpp").read_text()
        invoker = source[
            source.index("struct LuaCallbackInvoker"):
            source.index("void DispatchPostUpdate()")
        ]
        self.assertIn("lua_pcallk", invoker)
        self.assertIn("g_CallbackError.store(true, std::memory_order_release)", invoker)
        self.assertNotIn("luaL_unref", invoker)
        self.assertNotIn("registry->Remove(descriptor.id, descriptor.owner)", invoker)

    def test_stage6_failure_magic_is_distinct_and_isolated_to_runtime_entry(self):
        hook_source = (ROOT / "source/hook_manager.cpp").read_text()
        entry_source = (ROOT / "source/runtime_entry.cpp").read_text()
        header_source = (ROOT / "source/hook_manager.hpp").read_text()
        self.assertIn("0x49534141435F4950ULL", hook_source)
        self.assertIn("0x49534141435F4946ULL", entry_source)
        self.assertNotIn("0x49534141435F4946ULL", hook_source)
        helper_start = entry_source.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6", entry_source.index("constexpr u64 kStage6FailureMagic"))
        failure_helper = entry_source[helper_start:entry_source.index("#endif", helper_start)]
        self.assertIn("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6", failure_helper)
        self.assertIn("[[noreturn]] void ReportStage6Failure(std::uint32_t status);", header_source)

    def test_stage6_success_requires_observed_game_owner_chain(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback_start = source.index("static void Callback")
        callback = source[callback_start:source.index("};", callback_start)]
        stage6_start = callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6")
        stage6 = callback[stage6_start:callback.index("#endif", stage6_start)]

        self.assertLess(callback.index("Orig(self)"), stage6_start)
        self.assertIn("ObserveGameIsPaused", stage6)
        self.assertIn("GameIsPausedObservation::PausedFalse", stage6)
        self.assertIn("GameIsPausedObservation::PausedTrue", stage6)
        self.assertNotIn("HasObservedGame", stage6)
        self.assertIn("ReportStage6IsPausedSuccess", stage6)
        self.assertLess(stage6.index("ObserveGameIsPaused"), stage6.index("ReportStage6IsPausedSuccess"))
        self.assertNotIn("0x49534141435F4946ULL", source)

    def test_stage6_waits_before_reporting_the_deepest_owner_failure(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback_start = source.index("static void Callback")
        callback = source[callback_start:source.index("};", callback_start)]
        stage6_start = callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6")
        stage6 = callback[stage6_start:callback.index("#endif", stage6_start)]

        self.assertIn("g_Stage6ObserverFrames", source)
        self.assertIn("fetch_add", stage6)
        self.assertIn("return;", stage6)
        self.assertLess(stage6.index("return;"), stage6.index("ReportStage6Failure(39)"))
        self.assertIn("kStage6ObserverFrameLimit", stage6)
        self.assertIn("DeepestGameOwnerObservation()", stage6)
        for status in (39, 40, 41, 42):
            self.assertIn(f"ReportStage6Failure({status})", stage6)

    def test_stage6_does_not_depend_on_historical_observer_state(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback_start = source.index("static void Callback")
        callback = source[callback_start:source.index("};", callback_start)]
        stage6_start = callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6")
        stage6 = callback[stage6_start:callback.index("#endif", stage6_start)]

        for observer_state in (
            "HasEnteredGameUpdateObserverRelay()",
            "HasObservedGameUpdateFrame()",
            "HasEnteredGameState2ObserverRelay()",
            "HasObservedGameState2Frame()",
            "HasEnteredGameRenderObserverRelay()",
            "HasObservedGameRenderFrame()",
        ):
            self.assertNotIn(observer_state, stage6)

    def test_historical_observer_installers_remain_available_but_not_current_dependencies(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        installer = source[source.index("HookInstallResult TryInstallManagerUpdateHook"):
                            source.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8",
                                         source.index("HookInstallResult TryInstallManagerUpdateHook"))]
        for call in (
            "TryInstallGameObserverRelay(module)",
            "TryInstallGameUpdateObserverRelay(module)",
            "TryInstallGameState2ObserverRelay(module)",
            "TryInstallGameRenderObserverRelay(module)",
        ):
            self.assertNotIn(call, installer)

        header = (ROOT / "source/hook_manager.hpp").read_text()
        for name in (
            "GameObserverInstallResult",
            "GameUpdateObserverInstallResult",
            "GameState2ObserverInstallResult",
            "GameRenderObserverInstallResult",
        ):
            self.assertIn(name, header)

        self.assertIn("GameOwnerSlotMismatch", header)
        self.assertIn("GameOwnerSlotPublishFailed", header)

    def test_hook_install_failure_does_not_enable_hook(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        install = source[source.index("HookInstallResult TryInstallManagerUpdateHook"):source.index("bool InstallManagerUpdateHook")]
        self.assertIn("ManagerUpdateHook::PublishOriginal", install)
        self.assertIn("RwPages slotPages", install)
        self.assertIn("RelayPublishFailed", install)
        self.assertIn("g_HookEnabled.store(true", install)
        self.assertLess(install.index("ManagerUpdateHook::PublishOriginal"), install.index("g_HookEnabled.store(true"))

    def test_manager_update_publishes_verified_relay_before_callback(self):
        manager = (ROOT / "source/hook_manager.cpp").read_text()
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()
        installer = manager[
            manager.index("HookInstallResult TryInstallManagerUpdateHook"):
            manager.index("bool InstallManagerUpdateHook")
        ]

        self.assertNotIn("ManagerUpdateHook::TryInstallAtPtr", manager)
        self.assertIn("VerifyManagerRelay", manager)
        self.assertIn("__atomic_store_n", installer)
        self.assertIn("__ATOMIC_RELEASE", installer)
        self.assertIn("slotPages.Flush()", installer)
        self.assertLess(installer.index("PublishOriginal"), installer.index("__atomic_store_n"))
        self.assertIn("bool PublishOriginal(uintptr_t original)", trampoline)

    def test_try_hook_interfaces_preserve_prepare_and_commit_failure_causes(self):
        impl_header = (ROOT / "source/lib/hook/nx64/impl.hpp").read_text()
        impl = (ROOT / "source/lib/hook/nx64/hook_impl.cpp").read_text()
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()

        for token in (
            "enum class HookPrepareResult", "InvalidArgument", "BranchOutOfRange",
            "TrampolineAllocationFailed", "enum class HookAttemptResult",
            "PrepareInvalidArgument", "PrepareBranchOutOfRange",
            "PrepareTrampolineAllocationFailed", "CommitInvalidPreparation",
            "CommitBranchOutOfRange", "CommitOriginalInstructionChanged",
            "CommitCompareExchangeFailed",
        ):
            self.assertIn(token, impl_header)
        self.assertIn("HookPrepareResult TryPrepareHook", impl_header)
        self.assertIn("return HookPrepareResult::InvalidArgument;", impl)
        self.assertIn("return HookPrepareResult::BranchOutOfRange;", impl)
        self.assertIn("return HookPrepareResult::TrampolineAllocationFailed;", impl)
        self.assertIn("return HookCommitResult::InvalidPreparation;", impl)
        self.assertIn("return HookCommitResult::OriginalInstructionChanged;", impl)
        self.assertIn("return HookCommitResult::CompareExchangeFailed;", impl)
        self.assertNotIn("HookCommitResult::NotCommitted", impl)
        self.assertIn("HookAttemptResult TryInstallAtPtr", trampoline)

    def test_exlaunch_exposes_non_aborting_try_hook(self):
        impl = (ROOT / "source/lib/hook/nx64/hook_impl.cpp").read_text()
        base = (ROOT / "source/lib/hook/base.hpp").read_text()
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()
        self.assertIn("bool TryHook(", impl)
        self.assertIn("arch::TryHook", base)
        self.assertIn("HookAttemptResult TryInstallAtPtr", trampoline)

    def test_trampoline_is_published_before_target_activation(self):
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()
        install = trampoline[trampoline.index("HookAttemptResult TryInstallAtPtr"):trampoline.index("    };", trampoline.index("HookAttemptResult TryInstallAtPtr"))]
        self.assertIn("TryPrepareHook", install)
        self.assertIn("OrigRef().store(trampoline, std::memory_order_release)", install)
        self.assertIn("TryCommitHook", install)
        self.assertLess(install.index("TryPrepareHook"), install.index("OrigRef().store"))
        self.assertLess(install.index("OrigRef().store"), install.index("TryCommitHook"))

    def test_commit_failure_never_rolls_back_published_orig(self):
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()
        install = trampoline[trampoline.index("HookAttemptResult TryInstallAtPtr"):trampoline.index("    };", trampoline.index("HookAttemptResult TryInstallAtPtr"))]
        self.assertEqual(install.count("OrigRef().store"), 1)
        self.assertNotIn("previous", install)
        self.assertNotIn("OrigRef().store(previous", install)

    def test_concurrent_entry_change_keeps_published_orig_model(self):
        published = 0x7100001000
        self.assertEqual(orig_after_commit(published, commit_succeeded=False, entry_changed=True), published)
        self.assertEqual(orig_after_commit(published, commit_succeeded=False, entry_changed=False), published)

    def test_orig_uses_acquire_without_waiting(self):
        trampoline = (ROOT / "source/lib/hook/trampoline.hpp").read_text()
        orig = trampoline[trampoline.index("static ALWAYS_INLINE decltype(auto) Orig"):trampoline.index("static ALWAYS_INLINE void InstallAtOffset")]
        self.assertIn("OrigRef().load(std::memory_order_acquire)", orig)
        self.assertNotIn("while", orig)
        self.assertNotIn("yield", orig)

    def test_prepare_flushes_trampoline_before_commit_writes_target(self):
        impl = (ROOT / "source/lib/hook/nx64/hook_impl.cpp").read_text()
        prepare = impl[impl.index("HookPrepareResult TryPrepareHook"):impl.index("HookCommitResult TryCommitHook")]
        commit = impl[impl.index("HookCommitResult TryCommitHook"):impl.index("bool TryHook(")]
        self.assertIn("s_HookJit.Flush()", prepare)
        self.assertNotIn("original[0] =", prepare)
        self.assertIn("__sync_cmpswap(original", commit)

    def test_safe_protocol_rejects_multi_instruction_far_jump(self):
        impl = (ROOT / "source/lib/hook/nx64/hook_impl.cpp").read_text()
        prepare = impl[impl.index("HookPrepareResult TryPrepareHook"):impl.index("HookCommitResult TryCommitHook")]
        commit = impl[impl.index("HookCommitResult TryCommitHook"):impl.index("bool TryHook(")]
        self.assertRegex(prepare, r"llabs\(pcOffset\)[\s\S]*?return HookPrepareResult::BranchOutOfRange;")
        self.assertIn("instructionCount = 1", prepare)
        self.assertNotIn("instructionCount = count", prepare)
        self.assertNotIn("instructionCount != 4", commit)
        self.assertNotIn("instructionCount != 5", commit)

    def test_worker_records_guard_and_install_failures_without_logging(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        worker = source[
            source.index("void ModuleWorker"):
            source.index('extern "C" void exl_main', source.index("void ModuleWorker"))
        ]
        self.assertIn("SetRuntimeState({.moduleFound = true, .hookAttempted = true});", worker)
        self.assertNotIn("g_Logger", worker)
        self.assertNotIn("DisableHook(", worker)

    def test_rw_pages_flushes_data_via_rw_alias_and_instructions_via_ro_alias(self):
        for path in (
            ROOT / "source/lib/util/sys/rw_pages.cpp",
            ROOT / "exlaunch/source/lib/util/sys/rw_pages.cpp",
        ):
            source = path.read_text()
            flush = source[source.index("void RwPages::Flush() const"):source.index("RwPages::~RwPages")]
            self.assertIn("armDCacheFlush((void*)claim.GetAlignedRw(), claim.GetAlignedSize())", flush)
            self.assertIn("armICacheInvalidate((void*)claim.GetAlignedRo(), claim.GetAlignedSize())", flush)
            self.assertNotIn("armICacheInvalidate((void*)claim.GetAlignedRw()", flush)

    def test_worker_exits_after_hook_installation(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        worker = source[
            source.index("void ModuleWorker"):
            source.index('extern "C" void exl_main', source.index("void ModuleWorker"))
        ]
        success = worker[worker.index("HookInstallResult::Success"):worker.index("if (attempt + 1")]
        self.assertIn("svcExitThread();", success)
        self.assertNotIn("for (;;)" , success)

    def test_started_worker_handle_is_closed_by_entrypoint(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        entry_start = source.index('extern "C" void exl_main')
        entrypoint = source[entry_start:source.index('extern "C" NORETURN', entry_start)]
        self.assertRegex(entrypoint, r"svcStartThread\(g_WorkerHandle\)[\s\S]*CloseWorkerHandle\(\);")


if __name__ == "__main__":
    unittest.main()
