import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage94ConsoleLifecycleCallers.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra"


class Stage94ConsoleLifecycleCallerTests(unittest.TestCase):
    def test_exporter_records_all_direct_console_lifecycle_callers_without_authorization(self):
        self.assertTrue(SCRIPT.is_file(), "Stage94 Console lifecycle caller exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "console-lifecycle-callers.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-switch", "-process", "Repentance.nro", "-noanalysis",
                 "-readOnly", "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            self.assertTrue(output.is_file(), combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage94-console-lifecycle-callers-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["console_constructor"]["entry"], "0003bc34")
        self.assertEqual(document["console_destructor"]["entry"], "0003bc64")
        self.assertIsInstance(document["constructor_callers"], list)
        self.assertIsInstance(document["destructor_callers"], list)
        self.assertTrue(all(isinstance(item["context"], list) and item["context"]
                            for item in document["constructor_callers"] + document["destructor_callers"]))
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("console_owner_chain_unproven", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
