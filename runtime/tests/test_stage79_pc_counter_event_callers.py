import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage79PcCounterEventCallers.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = Path("/tmp/isaac-pc-ghidra.DjKcoN")


class Stage79PcCounterEventCallersTests(unittest.TestCase):
    def test_exporter_proves_pc_add_api_has_no_original_event_callers(self):
        self.assertTrue(SCRIPT.is_file(), "Stage79 PC counter caller exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_dir():
            self.skipTest("Ghidra analyzeHeadless or the managed PC project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-counter-event-callers.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-pc", "-process", "isaac-ng.exe", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "stage79-pc-counter-event-callers-v1")
        self.assertEqual(document["source_sha256"], "04469d0c3d3581936fcf85bea5f9f4f3a65b2ccf96b36310456c9626bac36dc6")
        self.assertEqual(document["handler"]["entry"], "0x7a8020")
        self.assertEqual(document["event_timing_status"], "blocked_lua_handler_has_no_original_callers")
        self.assertEqual(document["original_call_references"], [])
        self.assertEqual(len(document["non_call_references"]), 1)
        self.assertEqual(document["non_call_references"][0]["from_address"], "00809f2d")
        self.assertFalse(document["switch_event_anchor_authorized"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
