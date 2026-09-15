import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class Stage45MusicIdDiagnosticTests(unittest.TestCase):
    def test_stage45_reports_the_current_music_id_without_audio_side_effects(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text(encoding="utf-8")

        self.assertIn("45,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 45", selector)
        self.assertIn("ReportStage45Success", hook)
        self.assertIn("MusicDiagnosticCurrentId", runtime)
        stage45 = script[script.index("EXL_DIAGNOSTIC_STAGE == 45"):script.index("EXL_DIAGNOSTIC_STAGE == 17")]
        self.assertIn("music:GetCurrentMusicID()", stage45)
        self.assertIn("MarkMusicDiagnosticCurrentId", stage45)
        self.assertNotIn("music:Pause()", stage45)
        self.assertNotIn("music:Resume()", stage45)


if __name__ == "__main__":
    unittest.main()
