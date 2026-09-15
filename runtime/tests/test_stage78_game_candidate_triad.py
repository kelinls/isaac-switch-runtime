import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage78GameCandidateTriad.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage78GameCandidateTriadTests(unittest.TestCase):
    def test_exporter_keeps_all_stage77_candidates_blocked_without_full_triad(self):
        self.assertTrue(SCRIPT.is_file(), "Stage78 Game candidate triad exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-candidate-triad.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                 "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                 "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage78-game-candidate-triad-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["game_function_count"], 101)
        self.assertEqual(document["status"], "blocked_no_candidate_triad_closed")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(len(document["candidate_offsets"]), 17)
        self.assertTrue(all(not candidate["triad_closed"] for candidate in document["candidates"]))
        by_offset = {candidate["field_offset"]: candidate for candidate in document["candidates"]}
        self.assertEqual(len(by_offset["0x2c"]["reset_writers"]), 3)
        self.assertEqual(len(by_offset["0xfa7c"]["reset_writers"]), 3)
        self.assertEqual(len(by_offset["0xfd50"]["reset_writers"]), 4)
        self.assertEqual(len(by_offset["0x50"]["independent_readers"]), 1)
        self.assertEqual(len(by_offset["0x54"]["independent_readers"]), 1)
        self.assertTrue(all(not candidate["increment_writers"] for candidate in document["candidates"]))


if __name__ == "__main__":
    unittest.main()
