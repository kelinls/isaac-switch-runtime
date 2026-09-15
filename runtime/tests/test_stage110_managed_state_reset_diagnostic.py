import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage110ManagedStateResetDiagnosticTests(unittest.TestCase):
    def test_stage110_installs_the_existing_non_overlapping_room_and_lifecycle_relays(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")

        # 2026-09-14：lifecycle 中继已**提升为常驻挂点**（`HookId::GameStart`），
        # 不再挂在 `filter 108 110,$(DIAGNOSTIC_STAGE)` 门下，所以这里改钉"它无条件进产物"。
        # change-room 那条仍然只在 102/104/110/112 下构建（下面单独断言）。
        self.assertIn("all: $(DEPLOY_LIFECYCLE_RELAY_IPS)", makefile)
        self.assertIn("102 104 110 112,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 110", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 110", entry)
        self.assertIn("TryInstallStage110ChangeRoomDiagnostic", entry)
        self.assertIn("TryInstallStage110LifecycleDiagnostic", entry)
        self.assertIn("ReportStage110Failure", entry)
        self.assertIn("TryInstallStage110ChangeRoomDiagnostic", hook)
        self.assertIn("TryInstallStage110LifecycleDiagnostic", hook)
        self.assertIn("ObserveStage110ChangeRoom", hook)
        self.assertIn("ObserveStage110Lifecycle", hook)
        self.assertIn("Stage110ChangeRoomInstallResult", header)
        self.assertIn("Stage110LifecycleInstallResult", header)
        self.assertIn("all: $(DEPLOY_CHANGE_ROOM_RELAY_IPS)", makefile)
        self.assertIn("all: $(DEPLOY_LIFECYCLE_RELAY_IPS)", makefile)
        self.assertIn("InitMemLayout", entry)
        self.assertIn("ReportStage110Failure(1)", entry)

    def test_stage110_reports_only_the_saved_room_restart_new_room_reset_sequence(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110")
        stage110 = hook[start:hook.index("#endif", start)]

        self.assertIn("kStage110SavedLifecycle", stage110)
        self.assertIn("kStage110PreResetRoom", stage110)
        self.assertIn("kStage110NewLifecycle", stage110)
        self.assertIn("kStage110PostResetRoom", stage110)
        self.assertIn("kStage110CounterReset", stage110)
        self.assertIn("kStage110PostResetCountOne", stage110)
        self.assertIn("ReportStage110Success", stage110)
        self.assertIn("g_Stage110RoomEvents.exchange(0", stage110)
        self.assertNotIn("LuaRuntime", stage110)
        self.assertNotIn("GetTreasureRoomVisitCount", stage110)
        self.assertNotIn("AddTreasureRoomsVisited", stage110)
        self.assertNotIn("RestartGame", stage110)
        self.assertNotIn("new ", stage110)
        self.assertNotIn("delete ", stage110)

    def test_stage110_keeps_both_relay_guards_and_slots_separate(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        self.assertIn("kGameChangeRoomRelaySlotOffset = 0x68CE90", constants)
        self.assertIn("kGameStartRelaySlotOffset = 0x68CF90", constants)
        self.assertIn("kGameChangeRoomRelayCodeOffset = 0x68CE60", constants)
        self.assertIn("kGameStartSavedRelayCodeOffset = 0x68CEA0", constants)


if __name__ == "__main__":
    unittest.main()
