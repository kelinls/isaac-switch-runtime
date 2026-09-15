import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class Stage15RenderPauseDiagnosticTests(unittest.TestCase):
    def test_stage15_builds_both_relays_and_reports_distinct_results(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        lua = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text(encoding="utf-8")

        self.assertIn("15,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("filter 15,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 15", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 15", selector)
        self.assertIn("TryInstallManagerRenderHook", entry)
        self.assertIn("ReportStage15Success", hook)
        self.assertIn("ReportStage15Failure", hook)
        self.assertIn("u32 PostRenderCount();", header)
        self.assertIn("u32 PostRenderPausedCount();", header)
        self.assertIn("MarkPostRender", lua)
        self.assertIn("MarkPostRenderPaused", lua)
        self.assertIn("RuntimeTest.MarkPostRender()", script)
        self.assertIn("RuntimeTest.MarkPostRenderPaused()", script)


if __name__ == "__main__":
    unittest.main()
