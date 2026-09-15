import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage73SwitchStateCopyPairs.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage73SwitchStateCopyPairsTests(unittest.TestCase):
    def test_exporter_reports_only_version_locked_bidirectional_state_copy_candidates(self):
        self.assertTrue(SCRIPT.is_file(), "Stage73 state-copy exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "switch-state-copy-pairs.json"
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

        self.assertEqual(document["schema_version"], "stage73-switch-state-copy-pairs-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["game_state_function_count"], 8)
        self.assertEqual(document["game_state_functions"], [
            "00368f54 IsaacRepentance::GameState::write",
            "0036f3cc IsaacRepentance::GameState::Save",
            "00370f84 IsaacRepentance::GameState::read",
            "00379224 IsaacRepentance::GameState::Load",
            "003e4da8 IsaacRepentance::Level::RestoreGameState",
            "003e5818 IsaacRepentance::Level::StoreGameState",
            "003fa658 IsaacRepentance::Manager::SaveGameState",
            "003faaf4 IsaacRepentance::Manager::LoadGameState",
        ])
        self.assertTrue(document["cross_object_word_copies"])
        self.assertEqual(document["status"], "candidates_found")
        self.assertEqual(len(document["bidirectional_candidates"]), 1)
        candidate = document["bidirectional_candidates"][0]
        self.assertEqual(candidate["left_offset"], "0x1a8")
        self.assertEqual(candidate["right_offset"], "0xc")
        self.assertEqual([item["function"] for item in candidate["forward_copies"]],
                         ["IsaacRepentance::Level::RestoreGameState"])
        self.assertEqual([item["function"] for item in candidate["reverse_copies"]],
                         ["IsaacRepentance::Level::StoreGameState"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("switch_state_copy_pair_does_not_identify_treasure_counter", document["blocked_reasons"])
        self.assertNotEqual(candidate["left_offset"], candidate["right_offset"])
        self.assertTrue(candidate["forward_copies"])
        self.assertTrue(candidate["reverse_copies"])


if __name__ == "__main__":
    unittest.main()
