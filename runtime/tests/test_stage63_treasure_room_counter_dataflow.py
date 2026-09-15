import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage63TreasureRoomCounterDataflow.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage63TreasureRoomCounterDataflowTests(unittest.TestCase):
    def test_exporter_reports_version_locked_candidate_dataflow_or_specific_blocker(self):
        self.assertTrue(SCRIPT.is_file(), "Stage63 treasure-room counter exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "treasure-room-counter.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage63-treasure-room-counter-dataflow-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["pc_semantic_source"]["increment_operation"], "INC Game counter")
        self.assertTrue(document["game_receiver_candidates"])
        self.assertGreater(document["named_game_method_count"], 0)
        self.assertIn("named_game_mutation_candidates", document)
        exact = document["exact_game_field_candidate"]
        self.assertEqual(exact["total_game_offset_candidate"], "0x345f80")
        self.assertEqual(exact["verdict"], "rejected_per_frame_update_counter")
        self.assertTrue(exact["accesses"])
        self.assertIn(document["status"], {"authorized_candidate", "blocked"})
        if document["status"] == "blocked":
            self.assertTrue(document["blocked_reason"])
        else:
            self.assertTrue(document["counter_field"]["increment_writer"])
            self.assertTrue(document["counter_field"]["reset_writer"])
            self.assertTrue(document["counter_field"]["independent_reader"])


if __name__ == "__main__":
    unittest.main()
