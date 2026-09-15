import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage113DescriptorWord50Bitfield.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage113DescriptorWord50BitfieldTests(unittest.TestCase):
    def test_exporter_rejects_word50_as_pc_treasure_qualification_state(self):
        self.assertTrue(SCRIPT.is_file(), "Stage113 word-0x50 exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "descriptor-word50-bitfield.json"
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

        self.assertEqual(document["schema_version"], "stage113-descriptor-word50-bitfield-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["restore_reconstruction"]["bit_values"], [1, 2, 4, 8, 16, 32])
        self.assertEqual(document["restore_reconstruction"]["zero_before_reconstruction"], "0x370068")
        self.assertEqual(document["pc_qualification_equivalence"], "rejected")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
