import unittest
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"
PATCHES = ROOT / "tools" / "build_patches.py"


class Stage109RestartPostcallDiagnosticTests(unittest.TestCase):
    def test_stage109_installs_only_the_three_real_restart_postcall_relays(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        patches = PATCHES.read_text(encoding="utf-8")

        self.assertIn("109,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("DEPLOY_RESTART_RELAY_IPS", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 109", selector)
        self.assertIn("TryInstallStage109RestartDiagnostic", entry)
        self.assertIn("ReportStage109Failure", entry)
        self.assertIn("TryInstallStage109RestartDiagnostic", hook)
        self.assertIn("ObserveStage109Restart", hook)
        self.assertIn("Stage109RestartInstallResult", header)
        self.assertIn("restart-relay", patches)
        self.assertIn("GAME_RESTART_RELAY_CODE_OFFSETS", patches)

    def test_stage109_callback_is_read_only_and_reports_only_after_restart_returns(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109")
        stage109 = hook[start:hook.index("#endif", start)]

        self.assertIn("ReportStage109Success", stage109)
        self.assertNotIn("RestartGame", stage109)
        self.assertNotIn("LuaRuntime", stage109)
        self.assertNotIn("GetTreasureRoomVisitCount", stage109)
        self.assertNotIn("AddTreasureRoomsVisited", stage109)
        self.assertNotIn("new ", stage109)
        self.assertNotIn("delete ", stage109)

    def test_stage109_relays_restore_callsite_registers_before_the_seed_cleanup_resume(self):
        patches = PATCHES.read_text(encoding="utf-8")
        relay_start = patches.index("def build_game_restart_postcall_relay(")
        relay = patches[relay_start:patches.index("GAME_RESTART_RELAY_CODES =", relay_start)]

        self.assertIn('"E00740A9"', relay)
        self.assertIn('"FE0B40F9"', relay)
        self.assertIn("encode_branch(code_offset + 0x34, resume_offset)", relay)

    def test_runtime_relay_guards_match_the_generator_bytes(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        patches = import_module("tools.build_patches")

        for index in range(3):
            start = constants.index(f"kGameRestartRelay{index}ExpectedBytes")
            end = constants.index("};", start)
            guarded = bytes(
                int(token, 16)
                for token in __import__("re").findall(r"0x([0-9A-F]{2})", constants[start:end])
            )
            self.assertEqual(guarded, patches.GAME_RESTART_RELAY_CODES[index])


if __name__ == "__main__":
    unittest.main()
