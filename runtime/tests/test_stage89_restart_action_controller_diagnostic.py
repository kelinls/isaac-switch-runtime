import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage89RestartActionControllerDiagnosticTests(unittest.TestCase):
    def test_stage89_is_a_two_minute_read_only_controller_probe(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        self.assertIn("89,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 89", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 89", entry)
        self.assertIn("ReportStage89Failure", entry)
        self.assertGreaterEqual(entry.count("ReportStage89Failure(1);"), 7)
        self.assertIn("HookInstallResult::Stage89GameBindingsMismatch", entry)
        entrypoint_setup = entry[entry.index('extern "C" void exl_main'):
                             entry.index("exl::hook::Initialize()", entry.index('extern "C" void exl_main'))]
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 89", entrypoint_setup)
        self.assertIn("ObserveStage89RestartActionControllers", hook)
        self.assertIn("ReportStage89Success", hook)

        stage89 = hook[hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89"):
                       hook.index("#endif", hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89"))]
        self.assertIn("kStage89FrameLimit = 7200", stage89)
        self.assertIn("kStage89Controllers", stage89)
        self.assertIn("static_cast<u32>(-1)", stage89)
        self.assertIn("{0, 1, 2, 3, static_cast<u32>(-1)}", stage89)
        for controller in ("0", "1", "2", "3"):
            self.assertIn(controller, stage89)
        self.assertIn("kStage89ActionRestart", stage89)
        self.assertIn("ReadCurrentGameLevelStage", stage89)
        self.assertIn("ObserveGameIsPaused", stage89)
        self.assertNotIn("RestartGame", stage89)
        self.assertNotIn("RunCommand", stage89)
        self.assertNotIn("LuaRuntime::", stage89)
        self.assertNotIn("Dispatch", stage89)
        self.assertNotIn("IsActionPressed", stage89)

    def test_stage89_reports_controller_bitmap_and_does_not_inherit_600_frame_exit(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        stage89 = hook[hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89"):
                       hook.index("#endif", hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89"))]

        self.assertIn("(89ULL << 32)", stage89)
        self.assertIn("kStage89FirstControllerNoHit", stage89)
        self.assertIn("kStage89AllControllersSeen", stage89)
        self.assertIn("kStage89ControllerZeroSeen", stage89)
        self.assertIn("kStage89ControllerThreeSeen", stage89)
        self.assertNotIn("kStage88FrameLimit", stage89)

    def test_stage89_only_queries_after_the_original_update_returns(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        callback = hook[hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"):
                        hook.index("#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 7",
                                   hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"))]

        self.assertLess(callback.index("Orig(self);"),
                        callback.index("ObserveStage89RestartActionControllers(self);"))


if __name__ == "__main__":
    unittest.main()
