import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "analysis/ghidra/scripts/ExportStage77GameStateCopyPairs.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage77GameStateCopyPairsTests(unittest.TestCase):
    def test_exporter_reports_only_fixed_game_save_restore_copy_pairs(self):
        self.assertTrue(TOOL.is_file(), "Stage77 Game save/restore copy exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-state-copy-pairs.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                 "Repentance.nro", "-noanalysis", "-scriptPath", str(TOOL.parent),
                 "-postScript", TOOL.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage77-game-state-copy-pairs-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["functions"], [
            {"entry": "0x3505a4", "name": "IsaacRepentance::Game::SaveState"},
            {"entry": "0x34f060", "name": "IsaacRepentance::Game::RestoreState"},
        ])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(document["status"], "candidates_found")
        self.assertEqual(len(document["cross_object_word_copies"]), 35)
        self.assertEqual([(item["game_offset"], item["game_state_offset"])
                          for item in document["bidirectional_candidates"]], [
            ("0xfa5c", "0x40"), ("0xfa60", "0x44"), ("0xfa64", "0x48"),
            ("0xfa68", "0x4c"), ("0xfa6c", "0x50"), ("0xfa70", "0x54"),
            ("0xfa7c", "0x58"), ("0xfa74", "0x5c"), ("0xfa78", "0x60"),
            ("0xf9a4", "0x64"), ("0xf9a8", "0x68"), ("0xf9a0", "0x70"),
            ("0x24", "0x74"), ("0xfd50", "0x190"), ("0x2c", "0x88b8"),
            ("0x50", "0x88f0"), ("0x54", "0x88f4"),
        ])
        self.assertIn("save_restore_copy_pair_does_not_identify_treasure_counter", document["blocked_reasons"])
        for candidate in document["bidirectional_candidates"]:
            self.assertTrue(candidate["forward_copies"])
            self.assertTrue(candidate["reverse_copies"])
            self.assertNotEqual(candidate["game_offset"], candidate["game_state_offset"])


if __name__ == "__main__":
    unittest.main()
