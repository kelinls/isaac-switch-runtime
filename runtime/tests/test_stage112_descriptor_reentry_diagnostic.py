import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage112DescriptorReentryDiagnosticTests(unittest.TestCase):
    def test_stage112_is_an_isolated_change_room_snapshot_diagnostic(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        observer = (SOURCE / "game_observer.cpp").read_text(encoding="utf-8")

        self.assertIn("112,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("all: $(DEPLOY_CHANGE_ROOM_RELAY_IPS)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 112", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 112", entry)
        self.assertIn("TryInstallStage112DescriptorReentryDiagnostic", entry)
        self.assertIn("ReportStage112Failure", entry)
        self.assertIn("Stage112DescriptorReentryInstallResult", header)
        self.assertIn("TryInstallStage112DescriptorReentryDiagnostic", hook)
        self.assertIn("ObserveStage112ChangeRoom", hook)
        self.assertIn("ReadGameRoomDescriptorSnapshotFromGame", observer)
        self.assertIn("kRoomDescriptorOffset = 0x8", observer)
        self.assertIn("kDescriptorCandidateWord0cOffset = 0xc", observer)
        self.assertIn("kDescriptorCandidateWord50Offset = 0x50", observer)

    def test_stage112_requires_same_treasure_room_reentry_and_never_publishes_lua(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112")
        stage112 = hook[start:hook.index("#endif", start)]

        self.assertIn("kStage112RoomTreasure = 4", stage112)
        self.assertIn("ReadGameRoomTypeFromGame", stage112)
        self.assertIn("ReadGameCurrentRoomKeyFromGame", stage112)
        self.assertIn("ReadGameRoomDescriptorSnapshotFromGame", stage112)
        self.assertIn("g_Stage112FirstKey == key", stage112)
        self.assertIn("kStage112Word0cShift", stage112)
        self.assertIn("kStage112Word50Shift", stage112)
        self.assertIn("snapshot.candidateWord0c & 0xffu", stage112)
        self.assertIn("snapshot.candidateWord50 & 0xffu", stage112)
        self.assertIn("ReportStage112Success", stage112)
        self.assertNotIn("LuaRuntime", stage112)
        self.assertNotIn("GetTreasureRoomVisitCount", stage112)
        self.assertNotIn("AddTreasureRoomsVisited", stage112)
        self.assertNotIn("RestartGame", stage112)
        self.assertNotIn("new ", stage112)
        self.assertNotIn("delete ", stage112)


if __name__ == "__main__":
    unittest.main()
