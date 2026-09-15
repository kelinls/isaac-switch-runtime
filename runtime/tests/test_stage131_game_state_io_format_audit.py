import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage131_game_state_io_format_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage131GameStateIOFormatAuditTests(unittest.TestCase):
    def test_fixed_nro_format_audit_is_conservative_until_all_extension_gates_close(self):
        self.assertTrue(TOOL.is_file(), "Stage131 GameStateIO format audit must exist")
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage131_game_state_io_format_audit import export

        document = export(NRO)
        self.assertEqual(document["schema_version"], "stage131-game-state-io-format-audit-v1")
        self.assertEqual(document["symbols"]["game_state_write"], "00368f54")
        self.assertEqual(document["symbols"]["game_state_read"], "00370f84")
        self.assertEqual(document["symbols"]["game_state_save"], "0036f3cc")
        self.assertEqual(document["symbols"]["game_state_load"], "00379224")
        self.assertTrue(document["write_path"]["checksum_calls"])
        self.assertTrue(document["read_path"]["checksum_calls"])
        self.assertFalse(document["format_gates"]["append_position_proven"])
        self.assertFalse(document["format_gates"]["length_or_checksum_update_proven"])
        self.assertFalse(document["format_gates"]["old_save_skip_proven"])
        self.assertFalse(document["format_gates"]["failure_rollback_proven"])
        self.assertEqual(document["result"], "blocked")
        self.assertFalse(document["runtime_binding_authorized"])

    def test_direct_cli_writes_valid_json(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-state-io-format.json"
            result = subprocess.run(["python3", str(TOOL), str(NRO), str(output)],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["result"], "blocked")


if __name__ == "__main__":
    unittest.main()
