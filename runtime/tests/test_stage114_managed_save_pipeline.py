import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage114_managed_save_pipeline.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage114ManagedSavePipelineTests(unittest.TestCase):
    def test_tool_runs_directly_from_its_documented_file_path(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "managed-save-pipeline.json"
            result = subprocess.run(
                ["python3", str(TOOL), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                             "stage114-managed-save-pipeline-v1")

    def test_raw_nro_audit_recovers_the_game_owned_save_manager_pipeline(self):
        self.assertTrue(TOOL.is_file(), "Stage114 managed save-pipeline audit must exist")
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage114_managed_save_pipeline import export

        document = export(NRO)

        self.assertEqual(document["schema_version"], "stage114-managed-save-pipeline-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["save"]["call_address"], "0036f474")
        self.assertEqual(document["save"]["symbol"],
                         "_ZN4KAGE7Filesys19SaveDataManagerBase22OpenSaveFileForWritingEPKc")
        self.assertEqual(document["load"]["call_address"], "00379294")
        self.assertEqual(document["load"]["symbol"],
                         "_ZN4KAGE7Filesys15SaveDataManager22OpenSaveFileForReadingEPKc")
        self.assertEqual(document["manager_global"]["slot"], "00aac940")
        self.assertEqual(document["write_support"]["open_write_buffer"],
                         "_ZN4KAGE7Filesys18BufferedFileStream9OpenWriteEPKc")
        self.assertEqual(document["write_support"]["write_buffer"],
                         "_ZN4KAGE7Filesys18BufferedStreamBase13WriteToBufferEPKcii")
        self.assertEqual(document["write_support"]["commit"],
                         "_ZN4KAGE7Filesys15SaveDataManager16commit_save_dataEv")
        self.assertEqual(document["feasibility"], "candidate_requires_isolated_abi_lifecycle_probe")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
