import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class Stage17LongMusicPauseDiagnosticTests(unittest.TestCase):
    def test_stage17_keeps_the_verified_music_cycle_audible_long_enough(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text(encoding="utf-8")

        self.assertIn("17,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("filter 17,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 17", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 17", entry)
        self.assertIn("ReportStage17Success", hook)
        self.assertIn("ReportStage17Failure", hook)
        stage17 = script[script.index("EXL_DIAGNOSTIC_STAGE == 17"):script.index("EXL_DIAGNOSTIC_STAGE == 16")]
        self.assertIn("music:Pause()", stage17)
        self.assertIn("music:Resume()", stage17)
        self.assertIn("pausedFrames >= 600", stage17)
        self.assertIn("pausedFrames >= 900", stage17)


if __name__ == "__main__":
    unittest.main()
