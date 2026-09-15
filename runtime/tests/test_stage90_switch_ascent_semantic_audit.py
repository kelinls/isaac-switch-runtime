import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage90SwitchAscentSemanticAudit.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra"


class Stage90SwitchAscentSemanticAuditTests(unittest.TestCase):
    def test_exporter_keeps_ascent_blocked_until_stage_and_game_state_share_one_path(self):
        self.assertTrue(SCRIPT.is_file(), "Stage90 Switch ascent semantic exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "switch-ascent-semantic-audit.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-switch", "-process", "Repentance.nro", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage90-switch-ascent-semantic-audit-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertGreater(len(document["stage_range_sites"]), 0)
        self.assertTrue(all(site["function_name"].startswith(("Game::", "Level::"))
                            and site["stage_load"] and site["range_check"] and site["context"]
                            for site in document["stage_range_sites"]))
        self.assertEqual(len(document["closed_candidates"]), 1)
        candidate = document["closed_candidates"][0]
        self.assertEqual(candidate["function_name"], "Level::IsBackwardsPath")
        self.assertEqual(candidate["function_entry"], "003e1d98")
        self.assertEqual(candidate["entry_guard"], "080040B9080500511F15007128010054")
        self.assertEqual(candidate["game_receiver_evidence"],
                         "load(load(global_page(0xaac000), 0x698))")
        self.assertIn("ldrb w8", candidate["state_load"])
        self.assertIn("and w0,w8,#0x1", candidate["state_test"])
        self.assertIn("ret", candidate["boolean_return_path"])
        self.assertTrue(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
