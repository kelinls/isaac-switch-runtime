import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stage103_room_key_audit.py"
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)


class Stage103RoomKeyAuditTests(unittest.TestCase):
    def test_fixed_current_room_descriptor_dataflow_exports_only_candidates(self):
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        if not NRO.is_file():
            self.skipTest("fixed Repentance.nro input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "room-key.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            hashlib.sha256(NRO.read_bytes()).hexdigest(),
            document["source_sha256"],
        )
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        current = document["current_room_descriptor"]
        self.assertEqual(current["entry"], "0x3dc684")
        self.assertEqual(current["room_index_offset"], "0x21558")
        self.assertEqual(current["dimension_offset"], "0x21560")
        self.assertEqual(current["resolver"], "Level::GetRoomByIdx")
        self.assertEqual(current["key_candidate"], "(room_index, dimension)")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("hardware_stability_not_verified", document["blocked_reasons"])

    def test_audit_rejects_a_changed_current_descriptor_instruction_window(self):
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        if not NRO.is_file():
            self.skipTest("fixed Repentance.nro input is unavailable")
        data = bytearray(NRO.read_bytes())
        data[0x3DC690] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.nro"
            changed.write_bytes(data)
            output = Path(directory) / "room-key.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(changed), str(output)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported NRO source_sha256", result.stderr)


if __name__ == "__main__":
    unittest.main()
