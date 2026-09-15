import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage102ChangeRoomDiagnosticTests(unittest.TestCase):
    def test_stage102_is_an_isolated_read_only_post_change_room_diagnostic(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        patches = (ROOT / "tools" / "build_patches.py").read_text(encoding="utf-8")

        self.assertIn("102 104 110 112,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("change-room-relay", makefile)
        self.assertIn("ifneq ($(filter 102 104 110 112,$(DIAGNOSTIC_STAGE)),)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 102", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 102", entry)
        self.assertIn("TryInstallStage102ChangeRoomDiagnostic", entry)
        self.assertIn("ReportStage102Failure", entry)
        self.assertIn("TryInstallStage102ChangeRoomDiagnostic", hook)
        self.assertIn("ObserveStage102ChangeRoom", hook)
        self.assertIn("ReportStage102Success", hook)
        self.assertIn("Stage102ChangeRoomInstallResult", header)
        self.assertIn("kGameChangeRoomCallFileOffset = 0x354050", constants)
        self.assertIn("kGameChangeRoomRelayCodeOffset = 0x68CE60", constants)
        self.assertIn("kGameChangeRoomRelaySlotOffset = 0x68CE90", constants)
        self.assertIn("build_game_change_room_relay_patch", patches)
        self.assertIn('"change-room-relay"', patches)

    def test_stage102_preserves_the_original_level_transition_and_does_not_publish_lua(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102")
        stage102 = hook[start:hook.index("#endif", start)]
        self.assertIn("kStage102EventLimit = 4", stage102)
        self.assertIn("ReadGameRoomTypeFromGame", stage102)
        self.assertNotIn("LuaRuntime", stage102)
        self.assertNotIn("GetTreasureRoomVisitCount", stage102)
        self.assertNotIn("AddTreasureRoomsVisited", stage102)
        self.assertNotIn("RestartGame", stage102)
        self.assertNotIn("new ", stage102)
        self.assertNotIn("delete ", stage102)


if __name__ == "__main__":
    unittest.main()
