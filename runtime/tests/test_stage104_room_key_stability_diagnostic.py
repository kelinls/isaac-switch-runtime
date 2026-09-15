import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage104RoomKeyStabilityDiagnosticTests(unittest.TestCase):
    def test_stage104_reuses_only_the_verified_post_change_room_relay(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")

        self.assertIn("102 104 110 112,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 104", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 104", entry)
        self.assertIn("TryInstallStage104RoomKeyDiagnostic", entry)
        self.assertIn("ReportStage104Failure", entry)
        self.assertIn("TryInstallStage104RoomKeyDiagnostic", hook)
        self.assertIn("ObserveStage104ChangeRoom", hook)
        self.assertIn("Stage104RoomKeyInstallResult", header)
        self.assertIn("all: $(DEPLOY_CHANGE_ROOM_RELAY_IPS)", makefile)
        self.assertIn("$(DEPLOY_CHANGE_ROOM_RELAY_IPS): $(BUILD)", makefile)

    def test_stage104_only_reads_the_authenticated_key_and_compares_a_b_a_b(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104")
        stage104 = hook[start:hook.index("#endif", start)]

        self.assertIn("kStage104EventLimit = 4", stage104)
        self.assertIn("ReadGameCurrentRoomKeyFromGame", stage104)
        self.assertIn("kStage104ThirdMatchesFirst", stage104)
        self.assertIn("kStage104FourthMatchesSecond", stage104)
        self.assertIn("ReportStage104Success", stage104)
        self.assertNotIn("LuaRuntime", stage104)
        self.assertNotIn("GetTreasureRoomVisitCount", stage104)
        self.assertNotIn("AddTreasureRoomsVisited", stage104)
        self.assertNotIn("RestartGame", stage104)
        self.assertNotIn("new ", stage104)
        self.assertNotIn("delete ", stage104)

    def test_stage104_payload_keeps_both_initial_key_components_and_repeat_verdicts(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("kStage104FirstIndexShift = 8", hook)
        self.assertIn("kStage104SecondIndexShift = 16", hook)
        self.assertIn("kStage104FirstDimensionShift = 24", hook)
        self.assertIn("kStage104SecondDimensionShift = 26", hook)
        self.assertIn("g_Stage104Keys[0] == g_Stage104Keys[2]", hook)
        self.assertIn("g_Stage104Keys[1] == g_Stage104Keys[3]", hook)


if __name__ == "__main__":
    unittest.main()
