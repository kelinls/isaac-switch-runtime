import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class Stage16MusicPauseDiagnosticTests(unittest.TestCase):
    def test_stage16_builds_complete_runtime_with_music_pause_cycle(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        lua = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text(encoding="utf-8")

        self.assertIn("16,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("filter 16,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 16", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 16", entry)
        self.assertIn("TryInstallManagerRenderHook", entry)
        self.assertIn("ReportStage16Success", hook)
        self.assertIn("ReportStage16Failure", hook)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 16", hook)
        self.assertIn("music:Pause()", script)
        self.assertIn("music:Resume()", script)
        self.assertIn("RuntimeTest.MarkMusicDiagnosticCycleCompleted()", script)
        self.assertIn("MarkMusicDiagnosticCycleCompleted", lua)
        self.assertIn("MusicDiagnosticCycleCompleted", header)


if __name__ == "__main__":
    unittest.main()
