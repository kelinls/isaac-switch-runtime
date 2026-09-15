import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage98TransitionGlobalGameAccess.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage98TransitionGlobalGameAccessTests(unittest.TestCase):
    def test_exporter_audits_global_game_fields_in_room_transition_without_authorization(self):
        self.assertTrue(SCRIPT.is_file(), "Stage98 global Game access exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transition-global-game-access.json"
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

        self.assertEqual(document["schema_version"], "stage98-transition-global-game-access-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["game_owner_chain"], "global 0xaac000 + 0x698 -> owner -> Game*")
        self.assertTrue(document["accesses"])
        self.assertIn("0x26fa50", {item["game_offset"] for item in document["accesses"]})
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("counter_writer_reset_reader_still_requires_semantic_closure", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
