import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage130_save_manager_global_access_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage130SaveManagerGlobalAccessAuditTests(unittest.TestCase):
    def test_fixed_nro_has_only_read_accesses_to_the_imported_manager_global(self):
        self.assertTrue(TOOL.is_file(), "Stage130 SaveDataManager global-access audit must exist")
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "save-manager-global-access.json"
            result = subprocess.run(["python3", str(TOOL), str(NRO), str(output)],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage130-save-manager-global-access-audit-v1")
        self.assertEqual(document["manager_global"]["slot"], "00aac940")
        self.assertEqual(document["manager_global"]["relocation_type"], "GLOB_DAT")
        self.assertIn("0036f470", document["read_accesses"])
        self.assertIn("003b2fc0", document["read_accesses"])
        self.assertEqual(document["write_accesses"], [])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("loader_owned_global_publication_not_runtime_owned", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
