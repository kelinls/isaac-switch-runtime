import unittest
from pathlib import Path

try:
    from .test_support import makefile_accepted_diagnostic_stages
except ImportError:
    from test_support import makefile_accepted_diagnostic_stages


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage88InstantRestartObservationDiagnosticTests(unittest.TestCase):
    def test_stage88_is_a_single_read_only_manager_update_observer(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")

        # 阶段号"被构建系统接受"这件事按集合判成员，而不是按旧的分组字面串
        # （旧写法见 `makefile_accepted_diagnostic_stages` 的注释）。
        self.assertIn(88, makefile_accepted_diagnostic_stages(makefile))
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 88", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 88", entry)
        self.assertIn("ObserveStage88InstantRestart", hook)
        self.assertIn("ReportStage88Success", hook)
        self.assertIn("kManagerIsActionTriggeredOffset", constants)
        self.assertIn("VerifyManagerIsActionTriggered", hook)
        self.assertIn("ManagerIsActionTriggeredMismatch", header)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 88", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 88", selector)
        stage88 = hook[hook.index("void ObserveStage88InstantRestart"):hook.index("void ConfigureStage88Bindings")]
        self.assertIn("kStage88ActionRestart", stage88)
        self.assertIn("kStage88FrameLimit", stage88)
        self.assertIn("ReadCurrentGameLevelStage", stage88)
        self.assertIn("ObserveGameIsPaused", stage88)
        self.assertNotIn("RestartGame", stage88)
        self.assertNotIn("RunCommand", stage88)
        self.assertNotIn("LuaRuntime::", stage88)
        self.assertNotIn("Dispatch", stage88)

    def test_stage88_reports_every_startup_and_install_failure_before_exit(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        startup = entry[entry.index("if (startup != StartupStatus::TitleOk)"):entry.index("for (u32 attempt")]
        install_failure = entry[entry.index("} else {\n                SetRuntimeState({.moduleFound = true, .hookAttempted = true});"):entry.index("#endif\n            }\n#endif\n#endif", entry.index("const HookInstallResult install"))]
        memory_layout = entry[entry.index("extern \"C\" void exl_main"):entry.index("exl::hook::Initialize()")]

        self.assertIn("EXL_DIAGNOSTIC_STAGE == 88", startup)
        self.assertIn("ReportStage88Failure(1);", startup)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 88", install_failure)
        self.assertIn("HookInstallResult::ManagerIsActionTriggeredMismatch", install_failure)
        self.assertIn("ReportStage88Failure(2);", install_failure)
        self.assertIn("HookInstallResult::Stage88GameBindingsMismatch", install_failure)
        self.assertIn("ReportStage88Failure(3);", install_failure)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 88", memory_layout)


if __name__ == "__main__":
    unittest.main()
