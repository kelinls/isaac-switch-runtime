import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage100TreasureCounterLifecycleEvents.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage100TreasureCounterLifecycleEventsTests(unittest.TestCase):
    def test_exporter_separates_lifecycle_event_evidence_from_hook_authorization(self):
        self.assertTrue(SCRIPT.is_file(), "Stage100 lifecycle-event exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "treasure-counter-lifecycle-events.json"
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

        self.assertEqual(document["schema_version"], "stage100-treasure-counter-lifecycle-events-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(set(document["events"]), {"new_game", "saved_game", "restart", "room_change"})
        for event in document["events"].values():
            self.assertTrue(event["target"]["entry"])
            self.assertEqual(len(event["target"]["guard"]), 32)
            self.assertIn(event["runtime_hook_status"], {
                "existing_safe_hook", "needs_new_safe_hook", "blocked",
            })
            self.assertIn(event["event_evidence_status"], {"proven", "partial", "blocked"})
            self.assertIsInstance(event["call_edges"], list)
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn(document["state_machine_status"], {"ready_for_design", "blocked"})


if __name__ == "__main__":
    unittest.main()
