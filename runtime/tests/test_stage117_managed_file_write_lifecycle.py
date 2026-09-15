import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage117_managed_file_write_lifecycle.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage117ManagedFileWriteLifecycleTests(unittest.TestCase):
    def test_tool_runs_directly_from_its_documented_file_path(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "managed-file-write-lifecycle.json"
            result = subprocess.run(
                ["python3", str(TOOL), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                             "stage117-managed-file-write-lifecycle-v1")

    def test_original_file_wrapper_owns_the_complete_write_close_lifecycle(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage117_managed_file_write_lifecycle import export

        document = export(NRO)

        self.assertEqual(document["schema_version"], "stage117-managed-file-write-lifecycle-v1")
        self.assertEqual(document["file_object_size"], "00000060")
        self.assertEqual(document["construct"]["address"], "004ccbc0")
        self.assertEqual(document["open_write"]["address"], "004cce38")
        self.assertEqual(document["open_write"]["mode"], "6")
        self.assertEqual(document["write"]["address"], "004cd190")
        self.assertEqual(document["write"]["arguments"], ["file", "payload", "item_size", "length"])
        self.assertEqual(document["close"]["address"], "004cd00c")
        self.assertTrue(document["close"]["flushes_before_close"])
        self.assertEqual(document["destroy"]["address"], "004ccbfc")
        self.assertEqual(document["lifecycle"], ["construct", "open_write", "write", "close", "destroy"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("true_hardware_write_not_yet_verified", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
