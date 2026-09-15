import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class GameIsPausedNativeStage36Tests(unittest.TestCase):
    def test_thunk_offset_and_guard_are_version_locked(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        expected = """inline constexpr uintptr_t kGameIsPausedThunkOffset = 0x671100;
inline constexpr std::array<u8, 16> kGameIsPausedThunkExpectedBytes = {
    0x70, 0x21, 0x00, 0xB0, 0x11, 0xF6, 0x43, 0xF9,
    0x10, 0xA2, 0x1F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};"""
        self.assertIn(expected, constants)

    def test_native_reader_exposes_only_value_results(self):
        header = (SOURCE / "game_observer.hpp").read_text(encoding="utf-8")
        for token in (
            "enum class GameIsPausedObservation : std::uint32_t",
            "PausedFalse",
            "PausedTrue",
            "GameIsPausedObservation ObserveGameIsPaused(uintptr_t ownerSlot, uintptr_t isPausedAddress)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, header)
        self.assertNotIn("Game* Resolve", header)
        self.assertNotIn("Game* Get", header)

    def test_native_reader_calls_fixed_abi_only_after_mapping_checks(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        signature = "GameIsPausedObservation ObserveGameIsPaused"
        self.assertIn(signature, source)
        start = source.index(signature)
        body = source[start:source.index("void ObserveGameFrame", start)]
        for token in (
            "IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))",
            "IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))",
            "IsReadableNonExecutableMapping(gameAddress, sizeof(uintptr_t))",
            "using GameIsPausedFunction = bool (*)(const IsaacRepentance::Game*)",
            "isPaused(game)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertLess(
            body.index("IsReadableNonExecutableMapping(gameAddress"),
            body.index("isPaused(game)"),
        )

    def test_native_reader_revalidates_the_thunk_mapping_before_owner_reads_or_calls(self):
        header = (SOURCE / "game_observer.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        signature = "GameIsPausedObservation ObserveGameIsPaused"
        body_start = source.index(signature)
        body = source[body_start:source.index("void ObserveGameFrame", body_start)]

        self.assertIn("ThunkUnavailable", header)
        self.assertIn("kGameIsPausedThunkWindowSize = 16", source)
        for token in (
            "length == 0",
            "address > UINTPTR_MAX - length",
            "svcQueryMemory",
            "MemType_ModuleCodeStatic",
            "info.perm == Perm_Rx",
            "end <= info.addr + info.size",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

        thunk_check = body.index("if (isPausedAddress == 0 || (isPausedAddress & 3) != 0 ||")
        thunk_failure = body.index("return GameIsPausedObservation::ThunkUnavailable;", thunk_check)
        owner_dereference = body.index("reinterpret_cast<const uintptr_t*>(ownerSlot)")
        native_call = body.index("isPaused(game)")
        self.assertIn(
            "IsMappedRxModuleCodeWindow(isPausedAddress, kGameIsPausedThunkWindowSize)",
            body[thunk_check:thunk_failure],
        )
        self.assertLess(thunk_check, owner_dereference)
        self.assertLess(thunk_failure, owner_dereference)
        self.assertLess(thunk_failure, native_call)

    def test_native_reader_does_not_cache_or_cross_subsystems(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        for forbidden in (
            "Game* g_",
            "uintptr_t g_Owner",
            "uintptr_t g_Game",
            "LuaRuntime",
            "fsOpen",
            "ProbeLogger",
            "std::thread",
            "new ",
            "delete ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_installer_verifies_and_publishes_thunk_before_callback(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[
            source.index("HookInstallResult TryInstallManagerUpdateHook"):
            source.index("bool InstallManagerUpdateHook")
        ]
        self.assertIn("VerifyGameIsPausedThunk", install)
        for token in (
            "VerifyGameIsPausedThunk(module, &isPausedThunk)",
            "g_Stage6GameIsPausedThunk.store(isPausedThunk, std::memory_order_release)",
            "g_Stage6GameIsPausedThunk.load(std::memory_order_acquire)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, install)
        self.assertLess(install.index("VerifyGameIsPausedThunk"), install.index("__atomic_store_n"))

    def test_callback_calls_native_reader_after_orig_and_reports_bool(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        callback = source[
            source.index("static void Callback"):
            source.index("};", source.index("static void Callback"))
        ]
        self.assertIn("ObserveGameIsPaused", callback)
        self.assertLess(callback.index("Orig(self)"), callback.index("ObserveGameIsPaused"))
        self.assertIn("GameIsPausedObservation::PausedFalse", callback)
        self.assertIn("GameIsPausedObservation::PausedTrue", callback)
        self.assertIn("0x49534141435F4950ULL", source)
        self.assertIn("(6ULL << 32) | (paused ? 2ULL : 1ULL)", source)

    def test_stage36_failure_magic_and_new_install_statuses_are_distinct(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        self.assertIn("0x49534141435F4946ULL", entry)
        self.assertIn("GameIsPausedThunkMismatch", header)
        self.assertIn("GameIsPausedThunkPublishFailed", header)
        self.assertRegex(
            entry,
            r"GameIsPausedThunkMismatch:\s*ReportStage6Failure\(43\);",
        )
        self.assertRegex(
            entry,
            r"GameIsPausedThunkPublishFailed:\s*ReportStage6Failure\(44\);",
        )

    def test_stage36_keeps_single_manager_relay_payload(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("all: $(DEPLOY_NPDM)\nifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)\nelse\nall: $(DEPLOY_RELAY_IPS)\nendif", makefile)


if __name__ == "__main__":
    unittest.main()
