import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class GameObserverStage26Tests(unittest.TestCase):
    def test_observer_only_records_a_non_null_current_frame_object(self):
        source = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")
        historical_observers = source[source.index("void ObserveGameFrame"):]

        self.assertIn("void ObserveGameFrame(IsaacRepentance::Game* game)", source)
        self.assertIn("void ObserveGameUpdateFrame(IsaacRepentance::Game* game)", source)
        self.assertIn("void ObserveGameState2Frame(IsaacRepentance::Game* game)", source)
        self.assertIn("void ObserveGameRenderFrame(IsaacRepentance::Game* game)", source)
        self.assertIn("game != nullptr", source)
        self.assertIn("g_GameObserved.store(true, std::memory_order_release)", source)
        self.assertIn("g_GameUpdateObserverEntered.store(true, std::memory_order_release)", source)
        self.assertIn("g_GameUpdateObserved.store(true, std::memory_order_release)", source)
        self.assertIn("bool HasEnteredGameUpdateObserverRelay()", source)
        self.assertIn("bool HasObservedGameUpdateFrame()", source)
        self.assertIn("bool HasEnteredGameState2ObserverRelay()", source)
        self.assertIn("bool HasObservedGameState2Frame()", source)
        self.assertIn("bool HasEnteredGameRenderObserverRelay()", source)
        self.assertIn("bool HasObservedGameRenderFrame()", source)
        self.assertLess(
            source.index("g_GameUpdateObserverEntered.store"),
            source.index("if (game != nullptr)", source.index("void ObserveGameUpdateFrame")),
        )
        for forbidden in ("IsPaused", "LuaRuntime", "fs", "Game* g_", "new ", "delete "):
            self.assertNotIn(forbidden, historical_observers)

    def test_program_module_compiles_the_observer_translation_unit(self):
        wrapper = (SOURCE / "program/game_observer.cpp").read_text(encoding="utf-8")
        self.assertEqual(wrapper, '#include "../game_observer.cpp"\n')

    def test_observer_relay_is_fully_verified_before_it_is_published(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        installer = source[
            source.index("GameObserverInstallResult TryInstallGameObserverRelay"):
            source.index("HookInstallResult TryInstallManagerUpdateHook")
        ]

        self.assertIn("VerifyGameObserverRelay", installer)
        self.assertIn("RwPages slotPages", installer)
        self.assertIn("__ATOMIC_RELEASE", installer)
        self.assertIn("slotPages.Flush()", installer)
        self.assertIn("ObserveGameFrame", installer)
        self.assertLess(installer.index("VerifyGameObserverRelay"), installer.index("__atomic_store_n"))
        self.assertLess(installer.index("__atomic_load_n"), installer.index("__atomic_store_n"))

    def test_game_update_observer_relay_is_independently_verified_before_publish(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        installer = source[
            source.index("GameUpdateObserverInstallResult TryInstallGameUpdateObserverRelay"):
            source.index("HookInstallResult TryInstallManagerUpdateHook")
        ]

        self.assertIn("VerifyGameUpdateObserverRelay", installer)
        self.assertIn("RwPages slotPages", installer)
        self.assertIn("ObserveGameUpdateFrame", installer)
        self.assertIn("__ATOMIC_RELEASE", installer)
        self.assertIn("slotPages.Flush()", installer)
        self.assertLess(installer.index("VerifyGameUpdateObserverRelay"), installer.index("__atomic_store_n"))

    def test_game_state2_observer_relay_is_independently_verified_before_publish(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        installer = source[
            source.index("GameState2ObserverInstallResult TryInstallGameState2ObserverRelay"):
            source.index("HookInstallResult TryInstallManagerUpdateHook")
        ]

        self.assertIn("VerifyGameState2ObserverRelay", installer)
        self.assertIn("RwPages slotPages", installer)
        self.assertIn("ObserveGameState2Frame", installer)
        self.assertIn("__ATOMIC_RELEASE", installer)
        self.assertIn("slotPages.Flush()", installer)
        self.assertLess(installer.index("VerifyGameState2ObserverRelay"), installer.index("__atomic_store_n"))
        self.assertLess(installer.index("__atomic_load_n"), installer.index("__atomic_store_n"))

    def test_ispaused_render_observer_relay_is_verified_before_publish(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        installer = source[source.index("GameRenderObserverInstallResult TryInstallGameRenderObserverRelay"):source.index("HookInstallResult TryInstallManagerUpdateHook")]
        self.assertIn("VerifyGameRenderObserverRelay", installer)
        self.assertIn("ObserveGameRenderFrame", installer)
        self.assertIn("RwPages slotPages", installer)
        self.assertIn("__ATOMIC_RELEASE", installer)

    def test_historical_observer_installers_are_not_current_install_dependencies(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        installer = source[
            source.index("HookInstallResult TryInstallManagerUpdateHook"):
            source.index("bool InstallManagerUpdateHook")
        ]
        for installer_name in (
            "TryInstallGameObserverRelay(module)",
            "TryInstallGameUpdateObserverRelay(module)",
            "TryInstallGameState2ObserverRelay(module)",
            "TryInstallGameRenderObserverRelay(module)",
        ):
            self.assertNotIn(installer_name, installer)

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        all_line = next(line for line in makefile.splitlines() if line.startswith("all:"))
        for target in (
            "$(DEPLOY_GAME_OBSERVER_RELAY_IPS)",
            "$(DEPLOY_GAME_UPDATE_OBSERVER_RELAY_IPS)",
            "$(DEPLOY_GAME_STATE2_OBSERVER_RELAY_IPS)",
            "$(DEPLOY_GAME_ISPAUSED_RENDER_OBSERVER_RELAY_IPS)",
        ):
            self.assertNotIn(target, all_line)

    def test_runtime_constants_lock_the_full_internal_relay(self):
        source = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")

        for required in (
            "kGameInitCallFileOffset = 0x3F5A44",
            "kGameObserverRelayCodeOffset = 0x68CC40",
            "kGameObserverRelaySlotOffset = 0x68CC70",
            "kGameObserverRelayExpectedEntry",
            "kGameObserverRelayExpectedBytes",
            "kGameUpdateCallFileOffset = 0x3F905C",
            "kGameUpdateObserverRelayCodeOffset = 0x68CC80",
            "kGameUpdateObserverRelaySlotOffset = 0x68CCB0",
            "kGameUpdateObserverRelayExpectedEntry",
            "kGameUpdateObserverRelayExpectedBytes",
            "kGameState2CallFileOffset = 0x3F903C",
            "kGameState2ObserverRelayCodeOffset = 0x68CCC0",
            "kGameState2ObserverRelaySlotOffset = 0x68CCF0",
            "kGameState2ObserverRelayExpectedEntry",
            "kGameState2ObserverRelayExpectedBytes",
        ):
            self.assertIn(required, source)
        self.assertIn("0x7F, 0x5C, 0x0A, 0x14", source)
        self.assertIn("0x09, 0x4F, 0x0A, 0x14", source)


if __name__ == "__main__":
    unittest.main()
