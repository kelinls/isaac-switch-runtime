import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage121_save_manager_real_lifecycle.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage121SaveManagerRealLifecycleTests(unittest.TestCase):
    def test_tool_runs_directly_from_its_documented_file_path(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "save-manager-real-lifecycle.json"
            result = subprocess.run(["python3", str(TOOL), str(NRO), str(output)],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                             "stage121-save-manager-real-lifecycle-v1")

    def test_real_gamestate_save_uses_openwrite_result_and_closes_same_buffered_stream_owner(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage121_save_manager_real_lifecycle import export

        document = export(NRO)

        self.assertEqual(document["game_state_save"]["entry"], "0036f3cc")
        self.assertEqual(document["game_state_save"]["open_write_call"], "0036f474")
        self.assertEqual(document["game_state_save"]["open_result_slot"], "owner + 0x38")
        self.assertEqual(document["game_state_save"]["owner_recovery"], "load [owner + 0x38] - 0x38")
        self.assertEqual(document["game_state_save"]["close_virtual_call"], "0036f4d0")
        self.assertEqual(document["game_state_save"]["close_target"],
                         "KAGE::Filesys::BufferedFileStream::Close")
        self.assertEqual(document["save_manager_openwrite"]["parent_directory_call"], "004cc2c0")
        self.assertEqual(document["save_manager_openwrite"]["returned_interface"],
                         "BufferedFileStream + 0x38")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("runtime_manager_receiver_and_safe_save_hook_not_verified", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
