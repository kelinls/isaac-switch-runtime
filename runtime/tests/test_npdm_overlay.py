import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime.tools import patch_npdm as npdm


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800]"
    "[v524288][UPD]/Program #0/0/main.npdm"
)
EXPECTED_SHA256 = "d325e6e0fdec64053c0efd7e2bf1cd679215b10772c410a91247e1d41bae4c5e"
EXPECTED_OUTPUT_SHA256 = "4a67e5ac4710f635e494dc6bab3e01de87cbfb584a38bcfcf9ff3d477e9d41f5"
REQUIRED_SYSCALLS = {0x74, 0x75}


def changed_byte_offsets(before: bytes, after: bytes) -> set[int]:
    """Compare only the source-length prefix; appended bytes are structural."""
    if len(after) < len(before):
        raise AssertionError("NPDM output is shorter than its source")
    return {
        offset
        for offset, (left, right) in enumerate(zip(before, after[: len(before)]))
        if left != right
    }


def enabled_syscalls(parsed: npdm.Npdm) -> set[int]:
    result = set()
    for table in (parsed.aci0, parsed.acid):
        for descriptor in table.syscall_descriptors:
            result.update(
                descriptor.base + bit
                for bit in range(24)
                if descriptor.mask & (1 << bit)
            )
    return result


@unittest.skipUnless(FIXTURE.is_file(), "supplied NPDM fixture is not present")
class NpdmOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.output = Path(self.tempdir.name) / "main.npdm"
        self.bad_fixture = Path(self.tempdir.name) / "bad.npdm"
        shutil.copyfile(FIXTURE, self.bad_fixture)
        bad = bytearray(self.bad_fixture.read_bytes())
        bad[0x20] ^= 0x01
        self.bad_fixture.write_bytes(bad)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_parse_reports_both_kernel_capability_ranges_and_syscall_descriptors(self):
        parsed = npdm.parse_npdm(FIXTURE.read_bytes())

        self.assertEqual(parsed.aci0.kernel_capability_range, range(0x600, 0x624))
        self.assertEqual(parsed.acid.kernel_capability_range, range(0x430, 0x454))
        self.assertEqual(
            [descriptor.base for descriptor in parsed.acid.syscall_descriptors],
            [0x00, 0x18, 0x30, 0x48],
        )
        self.assertEqual(
            [descriptor.base for descriptor in parsed.aci0.syscall_descriptors],
            [0x00, 0x18, 0x30, 0x48],
        )

    def test_patch_adds_one_group4_descriptor_to_both_tables(self):
        report = npdm.patch_npdm(FIXTURE, self.output)
        before = FIXTURE.read_bytes()
        after = self.output.read_bytes()
        parsed_before = npdm.parse_npdm(before)
        parsed_after = npdm.parse_npdm(after)

        self.assertEqual(report.source_sha256, EXPECTED_SHA256)
        self.assertEqual(report.output_sha256, EXPECTED_OUTPUT_SHA256)
        self.assertEqual(report.output_sha256, hashlib.sha256(after).hexdigest())
        self.assertEqual(
            report.replaced_offsets,
            frozenset({0x74, 0x7C, 0x284, 0x2B4, 0x454, 0x455, 0x456, 0x457, 0x494}),
        )
        self.assertEqual(after[0x624:], bytes.fromhex("0f000086"))
        self.assertEqual(len(before), 0x624)
        self.assertEqual(len(after), 0x628)
        self.assertEqual(enabled_syscalls(parsed_after), enabled_syscalls(parsed_before) | REQUIRED_SYSCALLS)
        self.assertEqual(
            {
                descriptor.base + bit
                for descriptor in parsed_after.aci0.syscall_descriptors
                for bit in range(24)
                if descriptor.mask & (1 << bit)
            },
            {
                descriptor.base + bit
                for descriptor in parsed_after.acid.syscall_descriptors
                for bit in range(24)
                if descriptor.mask & (1 << bit)
            },
        )
        for table in (parsed_after.acid, parsed_after.aci0):
            group4 = [descriptor for descriptor in table.syscall_descriptors if descriptor.base == 0x60]
            self.assertEqual(len(group4), 1)
            self.assertEqual(group4[0].mask, 0x300000)

        expected_prefix = bytearray(before)
        for offset, value in {
            0x74: 0xC8,
            0x7C: 0xD8,
            0x284: 0xD8,
            0x2B4: 0x28,
            0x494: 0x28,
        }.items():
            expected_prefix[offset] = value
        expected_prefix[0x454:0x458] = bytes.fromhex("0f000086")
        self.assertEqual(after[: len(before)], bytes(expected_prefix))

    def test_wrong_source_hash_is_rejected_without_output(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            npdm.patch_npdm(self.bad_fixture, self.output)
        self.assertFalse(self.output.exists())

    def test_source_and_destination_same_file_is_rejected_without_mutation(self):
        source = Path(self.tempdir.name) / "source.npdm"
        shutil.copyfile(FIXTURE, source)
        original = source.read_bytes()
        original_sha256 = hashlib.sha256(original).hexdigest()

        with self.assertRaisesRegex(ValueError, "same file"):
            npdm.patch_npdm(source, source)

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_sha256)

    def test_hardlink_destination_is_rejected_without_mutation(self):
        source = Path(self.tempdir.name) / "source.npdm"
        alias = Path(self.tempdir.name) / "source-hardlink.npdm"
        shutil.copyfile(FIXTURE, source)
        try:
            alias.hardlink_to(source)
        except OSError as error:
            self.skipTest(f"filesystem does not support hard links: {error}")
        original = source.read_bytes()
        original_sha256 = hashlib.sha256(original).hexdigest()

        with self.assertRaisesRegex(ValueError, "same file"):
            npdm.patch_npdm(source, alias)

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_sha256)

    def test_malformed_npdm_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "NPDM"):
            npdm.parse_npdm(FIXTURE.read_bytes()[:0x100])

    def test_already_permitted_group4_is_rejected(self):
        source = Path(self.tempdir.name) / "already-permitted.npdm"
        npdm.patch_npdm(FIXTURE, source)
        already_permitted = source.read_bytes()

        with mock.patch.object(npdm, "TARGET_NPDM_SHA256", hashlib.sha256(already_permitted).hexdigest()):
            with self.assertRaisesRegex(ValueError, "already permits"):
                npdm.patch_npdm(source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
