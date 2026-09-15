from pathlib import Path
import unittest

from tools import build_patches as patches
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class ManagerRenderRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_relay_patch_only_rewrites_render_entry_and_independent_code_cave(self):
        records = decode_ips(patches.build_manager_render_relay_patch(self.nro))

        self.assertEqual(records, patches.RENDER_RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [0x3F9684, 0x68CC00])
        self.assertEqual(patches.RENDER_RELAY_TARGET_OFFSET, 0x3F9684)
        self.assertEqual(patches.RENDER_RELAY_CODE_OFFSET, 0x68CC00)
        self.assertEqual(patches.RENDER_RELAY_SLOT_OFFSET, 0x68CC18)
        self.assertEqual(self.nro[0x3F9684:0x3F9688], bytes.fromhex("FFC302D1"))
        self.assertEqual(self.nro[0x68CC00:0x68CC20], bytes(0x20))

        patched = apply_records(self.nro, records)
        self.assertEqual(patched[0x68CC10:0x68CC14], bytes.fromhex("FFC302D1"))
        self.assertEqual(patched[0x68CC18:0x68CC20], bytes(8))
        self.assertEqual(patched[0x68CBE0:0x68CC00], self.nro[0x68CBE0:0x68CC00])
        self.assertEqual(patched[0x68CC20:0x68CC40], self.nro[0x68CC20:0x68CC40])

    def test_relay_patch_rejects_changed_render_entry(self):
        image = bytearray(self.nro)
        image[0x3F9684] ^= 0xFF

        with self.assertRaisesRegex(ValueError, "Manager::Render"):
            patches.build_manager_render_relay_patch(bytes(image))

    def test_relay_patch_rejects_nonempty_independent_code_cave(self):
        image = bytearray(self.nro)
        image[0x68CC00] = 1

        with self.assertRaisesRegex(ValueError, "Render 中继代码洞"):
            patches.build_manager_render_relay_patch(bytes(image))


if __name__ == "__main__":
    unittest.main()
