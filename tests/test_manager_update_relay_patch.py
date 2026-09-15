from pathlib import Path
import unittest

from tools.build_patches import RELAY_RECORDS, build_manager_update_relay_patch
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class ManagerUpdateRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_relay_patch_only_rewrites_manager_entry_and_verified_code_cave(self):
        records = decode_ips(build_manager_update_relay_patch(self.nro))

        self.assertEqual(records, RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [0x3F8DB8, 0x68CBE0])
        self.assertEqual(self.nro[0x3F8DB8:0x3F8DBC], bytes.fromhex("FF4301D1"))
        self.assertEqual(self.nro[0x68CBE0:0x68CC00], bytes(0x20))

        patched = apply_records(self.nro, records)
        self.assertEqual(patched[0x68CBF0:0x68CBF4], bytes.fromhex("FF4301D1"))
        self.assertEqual(patched[0x68CBF8:0x68CC00], bytes(8))

    def test_relay_patch_rejects_wrong_module_id(self):
        image = bytearray(self.nro)
        image[0x40] ^= 0xFF

        with self.assertRaisesRegex(ValueError, "模块 ID"):
            build_manager_update_relay_patch(bytes(image))

    def test_relay_patch_rejects_changed_manager_entry(self):
        image = bytearray(self.nro)
        image[0x3F8DB8] ^= 0xFF

        with self.assertRaisesRegex(ValueError, "Manager::Update"):
            build_manager_update_relay_patch(bytes(image))

    def test_relay_patch_rejects_nonempty_code_cave(self):
        image = bytearray(self.nro)
        image[0x68CBE0] = 1

        with self.assertRaisesRegex(ValueError, "中继代码洞"):
            build_manager_update_relay_patch(bytes(image))


if __name__ == "__main__":
    unittest.main()
