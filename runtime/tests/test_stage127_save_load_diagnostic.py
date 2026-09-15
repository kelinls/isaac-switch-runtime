import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class Stage127SaveLoadDiagnosticTests(unittest.TestCase):
    def test_stage127_is_a_full_runtime_diagnostic_with_its_own_ips(self):
        makefile = (ROOT / "runtime/Makefile").read_text()
        selector = (ROOT / "runtime/source/program/runtime_entry.cpp").read_text()
        self.assertIn("118, 127", makefile)
        self.assertIn("filter 102 104 108 109 110 112 127", makefile)
        self.assertIn("DEPLOY_SAVE_LOAD_RELAY_IPS", makefile)
        self.assertIn("stage127_save_load_observation_relay.py", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 127", selector)

    def test_stage127_callback_is_read_only_and_needs_both_events(self):
        hook = (ROOT / "runtime/source/hook_manager.cpp").read_text()
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127")
        stage = hook[start:hook.index("#endif", start)]
        self.assertIn("outerManager == nullptr", stage)
        self.assertIn("flags == 3u", stage)
        self.assertNotIn("Open", stage)
        self.assertNotIn("Write", stage)
        self.assertNotIn("LuaRuntime", stage)
        self.assertNotIn("GetTreasureRoomVisitCount", stage)

    def test_stage127_does_not_misidentify_outer_manager_as_save_data_manager(self):
        audit = (ROOT / "tools/stage126_save_load_manager_register_audit.py").read_text()
        self.assertIn('"manager_type": "IsaacRepentance::Manager*"', audit)
        self.assertIn('"not_save_data_manager": True', audit)

    def test_runtime_guard_locks_both_entries_payloads_and_shared_slot(self):
        constants = (ROOT / "runtime/source/runtime_constants.hpp").read_text()
        manager = (ROOT / "runtime/source/hook_manager.cpp").read_text()
        self.assertIn("kSaveLoadRelaySaveExpectedEntry", constants)
        self.assertIn("kSaveLoadRelayLoadExpectedEntry", constants)
        self.assertIn("kSaveLoadRelayLoadExpectedBytes", constants)
        self.assertIn("kSaveLoadRelaySlotOffset", constants)
        self.assertIn("VerifyStage127SaveLoadRelay", manager)
        self.assertIn("TryInstallStage127SaveLoadDiagnostic", manager)


if __name__ == "__main__":
    unittest.main()
