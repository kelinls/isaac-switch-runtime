from pathlib import Path
import struct
import unittest

from tools.build_patches import NORMAL_RECORDS, build_normal_patch
from tools.nro_ips import apply_records, decode_ips


NRO_PATH = Path(
    "The Binding of Isaac_ Afterbirth+ 1.7.9b "
    "[010021C000B6A800][v524288][UPD]/Program #0/1/.nro/Repentance.nro"
)


class NormalDonationPatchTests(unittest.TestCase):
    def test_normal_donation_patch_skips_both_variant_eight_jam_branches(self):
        original = NRO_PATH.read_bytes()

        # Entity_Slot::Update's variant dispatch table maps Slot variant 8 to
        # the donation-machine handler at 0x32AFFC.
        dispatch_offset = 0x008BDD54 + (8 - 1) * 2
        dispatch_delta = struct.unpack_from("<H", original, dispatch_offset)[0]
        self.assertEqual(0x0032A128 + dispatch_delta * 4, 0x0032AFFC)

        records = decode_ips(build_normal_patch(original))

        self.assertEqual(records, NORMAL_RECORDS)
        self.assertEqual(
            NORMAL_RECORDS,
            [
                (0x0032E394, bytes.fromhex("ECFDFF17")),
                (0x0032E488, bytes.fromhex("AFFDFF17")),
            ],
        )
        self.assertEqual(original[0x32E394:0x32E398], bytes.fromhex("85BDFF54"))
        self.assertEqual(original[0x32E488:0x32E48C], bytes.fromhex("E2B5FF54"))
        patched = apply_records(original, records)
        self.assertEqual(patched[0x32E394:0x32E398], bytes.fromhex("ECFDFF17"))
        self.assertEqual(patched[0x32E488:0x32E48C], bytes.fromhex("AFFDFF17"))
        for offset, _ in NORMAL_RECORDS:
            self.assertGreaterEqual(offset, 0x0032905C)
            self.assertLess(offset, 0x0032E5C0)
        self.assertNotEqual(NORMAL_RECORDS[0][0], 0x002FD1BC)


if __name__ == "__main__":
    unittest.main()
