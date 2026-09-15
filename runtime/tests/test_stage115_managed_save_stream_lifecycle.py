import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage115_managed_save_stream_lifecycle.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage115ManagedSaveStreamLifecycleTests(unittest.TestCase):
    def test_tool_runs_directly_from_its_documented_file_path(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "managed-save-stream-lifecycle.json"
            result = subprocess.run(
                ["python3", str(TOOL), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                             "stage115-managed-save-stream-lifecycle-v1")

    def test_raw_nro_audit_keeps_write_and_commit_fragments_unlinked(self):
        self.assertTrue(TOOL.is_file(), "Stage115 stream lifecycle audit must exist")
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage115_managed_save_stream_lifecycle import export

        document = export(NRO)

        self.assertEqual(document["schema_version"], "stage115-managed-save-stream-lifecycle-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["initialization_gate"]["call_address"], "004cb654")
        self.assertEqual(document["initialization_gate"]["symbol"],
                         "_ZN4KAGE7Filesys19SaveDataManagerBase13IsInitializedEv")
        self.assertEqual(document["write"]["vtable_offset"], "00000040")
        self.assertEqual(document["write"]["arguments"],
                         ["stream", "payload", "item_size=1", "length"])
        self.assertEqual(document["separate_finalize_commit_fragment"]["finalize"]["vtable_offset"], "00000078")
        self.assertEqual(document["separate_finalize_commit_fragment"]["commit"]["call_address"], "004ccc8c")
        self.assertEqual(document["separate_finalize_commit_fragment"]["commit"]["symbol"],
                         "_ZN4KAGE7Filesys15SaveDataManager16commit_save_dataEv")
        self.assertEqual(document["open_write_lifecycle"],
                         ["is_initialized", "open_write", "write"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("openwrite_stream_finalize_and_commit_link_not_statically_proven",
                      document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
