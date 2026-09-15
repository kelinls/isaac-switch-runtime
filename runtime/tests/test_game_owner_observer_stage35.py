import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class GameOwnerObserverStage35Tests(unittest.TestCase):
    def test_owner_slot_offset_is_version_locked(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        self.assertIn("kGameOwnerGlobalSlotOffset = 0xAAC698", constants)

    def test_observer_exposes_only_ephemeral_observation_results(self):
        header = (SOURCE / "game_observer.hpp").read_text(encoding="utf-8")
        for token in (
            "enum class GameOwnerObservation : std::uint32_t",
            "OwnerNull", "OwnerUnreadable", "GameNull", "GameUnreadable", "Success",
            "GameOwnerObservation ObserveGameOwnerChain(uintptr_t ownerSlot)",
            "GameOwnerObservation DeepestGameOwnerObservation()",
        ):
            self.assertIn(token, header)
        self.assertNotIn("Game* Get", header)
        self.assertNotIn("Game* Resolve", header)

    def test_observer_checks_readable_non_executable_mappings_before_dereference(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        self.assertIn("svcQueryMemory", source)
        self.assertIn("(info.perm & Perm_R) != 0", source)
        self.assertIn("(info.perm & Perm_X) == 0", source)
        self.assertIn("IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))", source)
        self.assertIn("IsReadableNonExecutableMapping(game, sizeof(uintptr_t))", source)
        self.assertIn("if (ownerSlot == 0)", source)
        self.assertLess(
            source.index("if (ownerSlot == 0)"),
            source.index("reinterpret_cast<const uintptr_t*>(ownerSlot)"),
        )
        self.assertLess(
            source.index("IsReadableNonExecutableMapping(owner, sizeof(uintptr_t))"),
            source.index("reinterpret_cast<const uintptr_t*>(owner)"),
        )
        self.assertLess(
            source.index("IsReadableNonExecutableMapping(game, sizeof(uintptr_t))"),
            source.index("GameOwnerObservation::Success"),
        )

    def test_observer_validates_the_public_owner_slot_before_reading_it(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        owner_slot_dereference = source.index("reinterpret_cast<const uintptr_t*>(ownerSlot)")
        self.assertIn("IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))", source)
        self.assertLess(source.index("if (ownerSlot == 0)"), owner_slot_dereference)
        self.assertLess(
            source.index("IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))"),
            owner_slot_dereference,
        )
        self.assertLess(
            source.index("(ownerSlot & (alignof(uintptr_t) - 1)) != 0"),
            owner_slot_dereference,
        )
        specification = (
            ROOT.parent / "docs" / "superpowers" / "specs" /
            "2026-08-26-stage35-game-owner-chain-observer-design.md"
        ).read_text(encoding="utf-8")
        plan = (
            ROOT.parent / "docs" / "superpowers" / "plans" /
            "2026-08-26-stage35-game-owner-chain-observer-implementation.md"
        ).read_text(encoding="utf-8")
        for token in (
            "非零", "8 字节对齐", "svcQueryMemory", "完整位于同一映射", "Perm_R", "Perm_X",
        ):
            with self.subTest(document="specification", token=token):
                self.assertIn(token, specification)

        function_start = plan.index("GameOwnerObservation ObserveGameOwnerChain(uintptr_t ownerSlot) {")
        plan_function = plan[
            function_start:plan.index("GameOwnerObservation DeepestGameOwnerObservation()", function_start)
        ]
        owner_slot_dereference = plan_function.index("reinterpret_cast<const uintptr_t*>(ownerSlot)")
        for token in (
            "if (ownerSlot == 0)",
            "(ownerSlot & (alignof(uintptr_t) - 1)) != 0",
            "IsReadableNonExecutableMapping(ownerSlot, sizeof(uintptr_t))",
        ):
            with self.subTest(document="plan", token=token):
                self.assertIn(token, plan_function)
                self.assertLess(plan_function.index(token), owner_slot_dereference)

    def test_observer_keeps_only_the_deepest_enum(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        owner_observer = source[
            source.index("GameOwnerObservation ObserveGameOwnerChain"):
            source.index("GameOwnerObservation DeepestGameOwnerObservation")
        ]
        self.assertIn("std::atomic<GameOwnerObservation>", source)
        self.assertIn("compare_exchange_weak", source)
        self.assertIn("DeepestGameOwnerObservation", source)
        self.assertNotIn("IsPaused(", owner_observer)
        for forbidden in (
            "LuaRuntime", "Game* g_", "uintptr_t g_Owner", "uintptr_t g_Game",
            "new ", "delete ", "fsOpen", "ProbeLogger",
        ):
            self.assertNotIn(forbidden, source)

    def test_stage6_verifies_and_publishes_owner_slot_before_hook_callback(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[source.index("HookInstallResult TryInstallManagerUpdateHook"):
                         source.index("bool InstallManagerUpdateHook")]
        self.assertIn("VerifyGameOwnerSlot(module, &ownerSlot)", install)
        self.assertIn("g_Stage6GameOwnerSlot.store(ownerSlot, std::memory_order_release)", install)
        self.assertIn("g_Stage6GameOwnerSlot.load(std::memory_order_acquire)", install)
        self.assertLess(install.index("VerifyGameOwnerSlot"), install.index("g_Stage6GameOwnerSlot.store"))
        self.assertLess(install.index("g_Stage6GameOwnerSlot.store"), install.index("__atomic_store_n"))

    def test_stage6_reads_ispaused_after_orig_and_reports_the_deepest_failure(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        callback = source[source.index("static void Callback"):
                          source.index("};", source.index("static void Callback"))]
        stage6 = callback[callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"):
                          callback.index("#endif", callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"))]
        self.assertLess(callback.index("Orig(self)"), callback.index("ObserveGameIsPaused"))
        self.assertIn("GameIsPausedObservation::PausedFalse", stage6)
        self.assertIn("GameIsPausedObservation::PausedTrue", stage6)
        for observation, status in (
            ("OwnerNull", 39),
            ("OwnerUnreadable", 40),
            ("GameNull", 41),
            ("GameUnreadable", 42),
        ):
            with self.subTest(observation=observation):
                self.assertRegex(
                    stage6,
                    rf"case GameOwnerObservation::{observation}:\s*ReportStage6Failure\({status}\);",
                )
        for status in range(19, 37):
            with self.subTest(historical_callback_status=status):
                self.assertNotIn(f"ReportStage6Failure({status});", stage6)
        self.assertIn("g_Stage6ObserverFrames.fetch_add", stage6)
        self.assertIn("kStage6ObserverFrameLimit", stage6)

    def test_current_runtime_does_not_install_historical_game_relays(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[source.index("HookInstallResult TryInstallManagerUpdateHook"):
                         source.index("bool InstallManagerUpdateHook")]
        for forbidden in (
            "TryInstallGameObserverRelay(module)",
            "TryInstallGameUpdateObserverRelay(module)",
            "TryInstallGameState2ObserverRelay(module)",
            "TryInstallGameRenderObserverRelay(module)",
        ):
            self.assertNotIn(forbidden, install)

    def test_current_deploy_only_builds_the_manager_relay(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("all: $(DEPLOY_NPDM)\nifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)\nelse\nall: $(DEPLOY_RELAY_IPS)\nendif", makefile)


if __name__ == "__main__":
    unittest.main()
