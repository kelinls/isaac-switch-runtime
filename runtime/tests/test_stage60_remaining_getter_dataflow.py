import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage60RemainingGetterDataflow.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage60RemainingGetterDataflowTests(unittest.TestCase):
    def test_exporter_makes_one_explicit_decision_for_each_remaining_getter(self):
        self.assertTrue(SCRIPT.is_file(), "Stage60 remaining-getter exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed analysis project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "remaining-getters.json"
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

        self.assertEqual(document["schema_version"], "stage60-remaining-getter-dataflow-v1")
        getters = document["getters"]
        self.assertEqual(
            set(getters),
            {"game_get_room", "room_get_type", "game_get_treasure_room_visit_count"},
        )
        self.assertEqual(getters["game_get_room"]["status"], "authorized")
        self.assertEqual(getters["room_get_type"]["status"], "authorized")
        self.assertEqual(getters["game_get_treasure_room_visit_count"]["status"], "blocked")
        for key, getter in getters.items():
            self.assertIn(getter["status"], {"authorized", "blocked"}, key)
            self.assertTrue(getter["pc_semantics"], key)
            self.assertTrue(getter["candidate_dataflow"], key)
            if getter["status"] == "authorized":
                self.assertTrue(getter["writer"], key)
                self.assertTrue(getter["independent_reader"], key)
                self.assertTrue(getter["callback_lifecycle"], key)
            else:
                self.assertTrue(getter["blocked_reason"], key)


if __name__ == "__main__":
    unittest.main()
