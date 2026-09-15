import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage129SaveManagerLifecycleFilenameAudit.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage129SaveManagerLifecycleFilenameAuditTests(unittest.TestCase):
    def test_exporter_recovers_lifecycle_boundaries_and_keeps_filename_reuse_blocked(self):
        self.assertTrue(SCRIPT.is_file(), "Stage129 lifecycle/filename exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "save-manager-lifecycle-filename.json"
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

        self.assertEqual(document["schema_version"], "stage129-save-manager-lifecycle-filename-audit-v1")
        self.assertEqual(document["lifecycle"]["initialize"]["call_sites"], ["003b2fc8"])
        self.assertEqual(document["lifecycle"]["shutdown"]["call_sites"], ["004c0d24"])
        self.assertTrue(document["lifecycle"]["initialize"]["caller_context"])
        self.assertTrue(document["lifecycle"]["shutdown"]["caller_context"])
        self.assertEqual(document["filename_arguments"]["save"]["call_site"], "0036f474")
        self.assertEqual(document["filename_arguments"]["load"]["call_site"], "00379294")
        self.assertTrue(document["filename_arguments"]["save"]["pre_call_context"])
        self.assertTrue(document["filename_arguments"]["load"]["pre_call_context"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("original_filename_branch_and_storage_lifetime_not_runtime_verified",
                      document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
