import unittest

from tools.nro_ips import apply_records, decode_ips, encode_ips, read_module_id


class NroIpsTests(unittest.TestCase):
    def test_read_module_id_uses_nro_header_offset_0x40(self):
        image = bytearray(0x80)
        module_id = bytes.fromhex(
            "91C73FDD575061318D68886316AFEAC72388B2AB" + "00" * 12
        )
        image[0x40:0x60] = module_id

        self.assertEqual(read_module_id(bytes(image)), module_id)

    def test_ips_round_trip_and_application(self):
        records = [(0x2FD1BC, bytes.fromhex("E003271EC0035FD6"))]

        encoded = encode_ips(records)

        self.assertTrue(encoded.startswith(b"PATCH"))
        self.assertTrue(encoded.endswith(b"EOF"))
        self.assertEqual(decode_ips(encoded), records)
        self.assertEqual(
            apply_records(bytes(0x300000), records)[0x2FD1BC:0x2FD1C4],
            records[0][1],
        )

    def test_decode_ips_rejects_rle_records(self):
        with self.assertRaisesRegex(ValueError, "RLE"):
            decode_ips(b"PATCH\x00\x00\x00\x00\x00\x00\x01\xFFEOF")


if __name__ == "__main__":
    unittest.main()
