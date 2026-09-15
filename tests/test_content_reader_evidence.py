import struct
import tempfile
import unittest
from pathlib import Path

from tools.export_content_reader_evidence import TARGET_BUILD_ID, export_content_reader_evidence
from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


def make_nro_fixture(symbols: dict[str, int], imports: set[str] | None = None) -> bytes:
    imports = imports or set()
    mod0_offset = 0x80
    dynamic_offset = 0xA0
    hash_offset = 0x140
    symbol_offset = 0x180
    string_offset = 0x240
    data = bytearray(0x600)
    data[0x4:0x8] = struct.pack("<I", mod0_offset)
    data[0x10:0x14] = b"NRO0"
    data[0x40:0x54] = bytes.fromhex(TARGET_BUILD_ID)
    data[mod0_offset:mod0_offset + 4] = b"MOD0"
    data[mod0_offset + 4:mod0_offset + 8] = struct.pack("<I", dynamic_offset - mod0_offset)

    dynamic = (
        (4, hash_offset),
        (5, string_offset),
        (6, symbol_offset),
        (11, 24),
        (0, 0),
    )
    for index, (tag, value) in enumerate(dynamic):
        struct.pack_into("<QQ", data, dynamic_offset + index * 16, tag, value)

    struct.pack_into("<II", data, hash_offset, 1, len(symbols) + 1)
    cursor = string_offset + 1
    for index, (name, value) in enumerate(symbols.items(), start=1):
        encoded = name.encode("ascii") + b"\0"
        data[cursor:cursor + len(encoded)] = encoded
        is_import = name in imports
        struct.pack_into("<IBBHQQ", data, symbol_offset + index * 24,
                         cursor - string_offset, 2, 0, 0 if is_import else 1, 0 if is_import else value, 0)
        if not is_import:
            data[value:value + 16] = bytes(range(16))
        cursor += len(encoded)
    return bytes(data)


def make_relocation_fixture() -> bytes:
    data = bytearray(make_nro_fixture({
        "_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc": 0x400,
        "_ZN4KAGE7Filesys15IContentManager17GetFileMountPointEPKc": 0x420,
        "_ZN4KAGE7Filesys15IContentManager19GetDirectoryEntriesEPKcRj": 0x440,
        "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci": 0,
        "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm": 0,
        "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE": 0,
    }, imports={
        "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci",
        "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm",
        "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE",
    }))
    mod0_offset = 0x80
    dynamic_offset = 0xA0
    rela_offset = 0x500
    jmprel_offset = 0x540
    dynamic = (
        (4, 0x180), (5, 0x600), (6, 0x1C0), (11, 24),
        (7, rela_offset), (8, 24), (9, 24),
        (23, jmprel_offset), (2, 72), (20, 7), (0, 0),
    )
    for index, (tag, value) in enumerate(dynamic):
        struct.pack_into("<QQ", data, dynamic_offset + index * 16, tag, value)

    symbol_count = 7
    original_entries = bytes(data[0x180:0x180 + symbol_count * 24])
    original_names = bytes(data[0x240:0x400])
    struct.pack_into("<II", data, 0x180, 1, symbol_count)
    data[0x1C0:0x1C0 + len(original_entries)] = original_entries
    data[0x600:0x600 + len(original_names)] = original_names

    # AArch64 R_AARCH64_JUMP_SLOT is 1026. The symbol index occupies r_info's high word.
    struct.pack_into("<QQq", data, rela_offset, 0x580, (4 << 32) | 1026, 0)
    struct.pack_into("<QQq", data, jmprel_offset, 0x5A0, (4 << 32) | 1026, 0)
    struct.pack_into("<QQq", data, jmprel_offset + 24, 0x5A8, (5 << 32) | 1026, 0)
    struct.pack_into("<QQq", data, jmprel_offset + 48, 0x5B0, (6 << 32) | 1026, 0)
    return bytes(data)


class ContentReaderEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.fixture_path = Path(self.temp_dir.name) / "Repentance.nro"
        self.output_path = Path(self.temp_dir.name) / "evidence.json"
        self.fixture_path.write_bytes(make_nro_fixture({
            "_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc": 0x400,
            "_ZN4KAGE7Filesys15IContentManager17GetFileMountPointEPKc": 0x420,
            "_ZN4KAGE7Filesys15IContentManager19GetDirectoryEntriesEPKcRj": 0x440,
            "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci": 0x460,
            "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE": 0x470,
            "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm": 0x480,
        }, imports={
            "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci",
            "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE",
            "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm",
        }))

    def test_parse_dynamic_symbols_reads_mod0_sysv_entries(self):
        build_id, symbols = parse_dynamic_symbols(self.fixture_path.read_bytes())

        self.assertEqual(build_id, TARGET_BUILD_ID)
        self.assertEqual(
            symbols["_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc"].file_offset,
            0x400,
        )
        self.assertIsNone(symbols["_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci"].file_offset)

    def test_parse_dynamic_symbols_keeps_undefined_imports_without_an_entry_guard(self):
        _, symbols = parse_dynamic_symbols(make_nro_fixture({
            "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci": 0x460,
        }, imports={"_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci"}))

        imported = symbols["_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci"]
        self.assertFalse(imported.is_defined)
        self.assertIsNone(imported.file_offset)

    def test_parse_dynamic_symbols_skips_defined_bss_symbols_outside_file(self):
        fixture = bytearray(make_nro_fixture({
            "_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc": 0x400,
            "_ZN4KAGE7Filesys16g_ContentManagerE": 0x900,
        }))

        _, symbols = parse_dynamic_symbols(bytes(fixture))

        self.assertIn("_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc", symbols)
        self.assertNotIn("_ZN4KAGE7Filesys16g_ContentManagerE", symbols)

    def test_parse_dynamic_relocations_returns_plt_got_slots_for_imports(self):
        build_id, relocations = parse_dynamic_relocations(make_relocation_fixture())

        self.assertEqual(build_id, TARGET_BUILD_ID)
        self.assertEqual(
            [(entry.table, entry.offset, entry.relocation_type) for entry in
             relocations["_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci"]],
            [("rela", 0x580, 1026), ("jmprel", 0x5A0, 1026)],
        )
        self.assertEqual(
            relocations["_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm"][0].offset,
            0x5A8,
        )

    def test_exporter_records_import_relocation_slots_without_marking_them_callable(self):
        self.fixture_path.write_bytes(make_relocation_fixture())

        evidence = export_content_reader_evidence(self.fixture_path, self.output_path)

        open_file = evidence["candidates"]["nnFsOpenFile"]
        self.assertEqual(open_file["relocations"], [
            {"table": "rela", "target_offset": 0x580, "relocation_type": 1026, "addend": 0},
            {"table": "jmprel", "target_offset": 0x5A0, "relocation_type": 1026, "addend": 0},
        ])
        self.assertFalse(evidence["direct_runtime_call_permitted"])
        self.assertNotIn("PLT relocation resolution", evidence["next_required_evidence"])

    def test_exporter_records_get_file_size_as_an_undefined_import_with_a_plt_slot(self):
        self.fixture_path.write_bytes(make_relocation_fixture())

        evidence = export_content_reader_evidence(self.fixture_path, self.output_path)

        get_file_size = evidence["candidates"]["nnFsGetFileSize"]
        self.assertEqual(get_file_size["symbol_kind"], "undefined_import")
        self.assertIsNone(get_file_size["file_offset"])
        self.assertIsNone(get_file_size["first_16_bytes"])
        self.assertEqual(get_file_size["relocations"], [
            {"table": "jmprel", "target_offset": 0x5B0, "relocation_type": 1026, "addend": 0},
        ])
        self.assertFalse(evidence["direct_runtime_call_permitted"])

    def test_exporter_requires_supported_build_and_marks_candidates_non_callable(self):
        evidence = export_content_reader_evidence(self.fixture_path, self.output_path)

        self.assertEqual(evidence["build_id"], TARGET_BUILD_ID)
        self.assertFalse(evidence["direct_runtime_call_permitted"])
        self.assertIn("getMountedFilePath", evidence["candidates"])
        self.assertEqual(evidence["candidates"]["getMountedFilePath"]["file_offset"], 0x400)
        self.assertEqual(evidence["candidates"]["getMountedFilePath"]["first_16_bytes"],
                         "000102030405060708090A0B0C0D0E0F")
        self.assertEqual(evidence["candidates"]["nnFsOpenFile"]["symbol_kind"], "undefined_import")
        self.assertIsNone(evidence["candidates"]["nnFsOpenFile"]["file_offset"])
        self.assertIn("ABI", evidence["next_required_evidence"])
        self.assertIn("file size strategy", evidence["next_required_evidence"])


if __name__ == "__main__":
    unittest.main()
