import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage39GameScalarApiInventory.java"
EVIDENCE = ROOT / "analysis/stage39-game-scalar-api-inventory/91C73FDD575061318D68886316AFEAC72388B2AB.json"


class Stage39GameScalarApiInventoryTests(unittest.TestCase):
    def test_exporter_is_read_only_and_records_unresolved_game_scalar_apis(self):
        self.assertTrue(SCRIPT.exists(), "Stage 39 exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "91C73FDD575061318D68886316AFEAC72388B2AB",
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            "GetFrameCount",
            "GetNumPlayers",
            "getExecutableSHA256()",
            "getFunctions(true)",
            "findBytes",
            "Files.newBufferedWriter",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_ghidra_exporter_records_only_negative_binding_evidence(self):
        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scalar-api-inventory.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(ghidra), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage39-game-scalar-api-inventory-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            document["source_sha256"],
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        self.assertEqual(document["targets"]["game_get_frame_count"]["symbol_matches"], [])
        self.assertEqual(document["targets"]["game_get_frame_count"]["raw_name_occurrences"], 0)
        self.assertEqual(document["targets"]["game_get_frame_count"]["name_references"], [])
        self.assertEqual(document["targets"]["game_get_num_players"]["symbol_matches"], [])
        self.assertEqual(document["targets"]["game_get_num_players"]["raw_name_occurrences"], 0)
        self.assertEqual(document["targets"]["game_get_num_players"]["name_references"], [])
        self.assertTrue(document["known_game_scalar_getters"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(
            document["blocked_reasons"],
            ["no_version_locked_game_entry", "no_unique_field_offset_or_return_semantics"],
        )
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)


if __name__ == "__main__":
    unittest.main()
