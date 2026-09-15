from pathlib import Path
import unittest

from tools.build_patches import (
    LOAD_CONFIGS_RELAY_RECORDS,
    build_manager_loadconfigs_relay_patch,
)
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class ManagerLoadConfigsRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_relay_patch_only_rewrites_loadconfigs_entry_and_independent_code_cave(self):
        records = decode_ips(build_manager_loadconfigs_relay_patch(self.nro))

        self.assertEqual(records, LOAD_CONFIGS_RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [0x3F5C38, 0x68CC20])
        self.assertEqual(self.nro[0x3F5C38:0x3F5C3C], bytes.fromhex("FF4302D1"))
        self.assertEqual(self.nro[0x68CC20:0x68CC40], bytes(0x20))

        patched = apply_records(self.nro, records)
        self.assertEqual(patched[0x68CC30:0x68CC34], bytes.fromhex("FF4302D1"))
        self.assertEqual(patched[0x68CC38:0x68CC40], bytes(8))
        self.assertEqual(patched[0x68CBE0:0x68CC00], self.nro[0x68CBE0:0x68CC00])

    def test_relay_patch_rejects_wrong_module_id(self):
        image = bytearray(self.nro)
        image[0x40] ^= 0xFF

        with self.assertRaisesRegex(ValueError, "模块 ID"):
            build_manager_loadconfigs_relay_patch(bytes(image))

    def test_relay_patch_rejects_changed_loadconfigs_entry(self):
        image = bytearray(self.nro)
        image[0x3F5C38] ^= 0xFF

        with self.assertRaisesRegex(ValueError, "Manager::LoadConfigs"):
            build_manager_loadconfigs_relay_patch(bytes(image))

    def test_relay_patch_rejects_nonempty_independent_code_cave(self):
        image = bytearray(self.nro)
        image[0x68CC20] = 1

        with self.assertRaisesRegex(ValueError, "LoadConfigs 中继代码洞"):
            build_manager_loadconfigs_relay_patch(bytes(image))


if __name__ == "__main__":
    unittest.main()
