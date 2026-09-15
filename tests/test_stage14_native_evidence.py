from pathlib import Path
import json
import tempfile
import unittest

from tests.test_content_reader_evidence import make_nro_fixture
from tools.export_stage14_native_evidence import (
    TARGET_BUILD_ID,
    TARGETS,
    export_stage14_native_evidence,
)
from tools.find_stage14_render_caves import find_render_caves, is_branch_reachable


ROOT = Path(__file__).resolve().parents[1]


class Stage14NativeEvidenceTests(unittest.TestCase):
    def test_render_cave_requires_zero_bytes_branch_range_and_no_reserved_overlap(self):
        data = bytearray(b"\xA5" * 0x400)
        data[0x10:0x14] = b"NRO0"
        data[0x20:0x24] = (0).to_bytes(4, "little")
        data[0x24:0x28] = (len(data)).to_bytes(4, "little")
        data[0x180:0x1A0] = bytes(0x20)
        data[0x220:0x240] = bytes(0x20)

        candidates = find_render_caves(
            bytes(data), 0x100, reserved=(range(0x180, 0x1A0),)
        )

        self.assertEqual(
            [candidate["code_offset"] for candidate in candidates], [0x220]
        )
        self.assertEqual(candidates[0]["fallback_offset"], 0x230)
        self.assertEqual(candidates[0]["slot_offset"], 0x238)
        self.assertEqual(candidates[0]["length"], 0x20)

    def test_render_cave_rejects_out_of_range_branch_and_nonzero_bytes(self):
        data = bytearray(b"\xA5" * 0x400)
        data[0x10:0x14] = b"NRO0"
        data[0x20:0x24] = (0).to_bytes(4, "little")
        data[0x24:0x28] = (len(data)).to_bytes(4, "little")
        data[0x200:0x220] = bytes(0x20)
        data[0x204] = 1

        self.assertEqual(find_render_caves(bytes(data), 0x100, reserved=()), [])
        self.assertFalse(is_branch_reachable(0, 0x08000000))

    def test_render_cave_scans_only_the_declared_text_segment(self):
        data = bytearray(b"\xA5" * 0x400)
        data[0x10:0x14] = b"NRO0"
        data[0x20:0x24] = (0x100).to_bytes(4, "little")
        data[0x24:0x28] = (0x100).to_bytes(4, "little")
        data[0x80:0xA0] = bytes(0x20)
        data[0x180:0x1A0] = bytes(0x20)

        candidates = find_render_caves(bytes(data), 0x100, reserved=())

        self.assertEqual(
            [candidate["code_offset"] for candidate in candidates], [0x180]
        )

    def test_ghidra_scripts_are_public_and_export_only_stage14_evidence(self):
        scripts = ROOT / "analysis" / "ghidra" / "scripts"
        finder = (scripts / "FindAsciiReferences.java").read_text(encoding="utf-8")
        exporter = (scripts / "ExportStage14NativeEvidence.java").read_text(encoding="utf-8")

        self.assertIn("public class FindAsciiReferences", finder)
        self.assertIn("public class ExportStage14NativeEvidence", exporter)
        self.assertIn("_ZNK15IsaacRepentance4Game8IsPausedEv", exporter)
        self.assertIn("_ZN15IsaacRepentance5Music5PauseEv", exporter)
        self.assertIn("getReferencesTo", exporter)
        self.assertIn("getReferencesFrom", exporter)
        self.assertIn("SourceType.USER_DEFINED", exporter)
        self.assertIn("getReferenceType().isData()", exporter)
        self.assertIn("getBlock(reference.getToAddress())", exporter)
        self.assertIn("!block.isExecute()", exporter)
        self.assertIn("StandardCharsets.UTF_8", exporter)
        self.assertNotIn("SourceType.ANALYSIS", exporter)
        self.assertNotIn("FileWriter", exporter)
        self.assertNotIn("createFunction(", exporter)
        self.assertNotIn("setBytes(", exporter)

    def test_exporter_writes_defined_function_offsets_and_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro = root / "Repentance.nro"
            output = root / "evidence.json"
            data = bytearray(make_nro_fixture({
                name: 0x400 + index * 0x20
                for index, name in enumerate(TARGETS.values())
            }))
            data[0x400:0x410] = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
            nro.write_bytes(data)

            evidence = export_stage14_native_evidence(nro, output)

            self.assertEqual(evidence["build_id"], TARGET_BUILD_ID)
            self.assertEqual(evidence["functions"]["game_is_paused"]["file_offset"], 0x400)
            self.assertEqual(
                evidence["functions"]["game_is_paused"]["first_16_bytes"],
                "00112233445566778899AABBCCDDEEFF",
            )
            self.assertEqual(
                set(evidence["functions"]["game_is_paused"]),
                {"symbol", "file_offset", "first_16_bytes", "is_defined"},
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                output.read_text(encoding="utf-8").replace("\r\n", "\n"),
            )

    def test_exporter_rejects_missing_or_undefined_required_function(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro = root / "Repentance.nro"
            nro.write_bytes(make_nro_fixture({
                name: 0x400 + index * 0x20
                for index, name in enumerate(TARGETS.values())
            }, imports={TARGETS["music_pause"]}))

            with self.assertRaisesRegex(ValueError, "music_pause.*defined"):
                export_stage14_native_evidence(nro, root / "evidence.json")

    def test_exporter_merges_matching_ghidra_evidence_but_retains_task2_blockers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro = root / "Repentance.nro"
            output = root / "evidence.json"
            ghidra_path = root / "ghidra.json"
            nro.write_bytes(make_nro_fixture({
                name: 0x400 + index * 0x20
                for index, name in enumerate(TARGETS.values())
            }))
            baseline = export_stage14_native_evidence(nro, output)
            ghidra = {
                "build_id": baseline["build_id"],
                "program": "Repentance.nro",
                "functions": {
                    key: {
                        "symbol": value["symbol"],
                        "file_offset": value["file_offset"],
                        "first_16_bytes": value["first_16_bytes"],
                        "callers": [],
                        "instructions": [],
                    }
                    for key, value in baseline["functions"].items()
                },
                "object_candidates": [],
            }
            ghidra_path.write_text(json.dumps(ghidra), encoding="utf-8")

            evidence = export_stage14_native_evidence(nro, output, ghidra_path)

            self.assertEqual(evidence["ghidra"], ghidra)
            self.assertFalse(evidence["ready_for_runtime_binding"])
            self.assertEqual(
                evidence["blocked_reasons"],
                ["object_ownership_unproven", "render_relay_unproven"],
            )

    def test_exporter_rejects_mismatching_ghidra_function_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro = root / "Repentance.nro"
            output = root / "evidence.json"
            ghidra_path = root / "ghidra.json"
            nro.write_bytes(make_nro_fixture({
                name: 0x400 + index * 0x20
                for index, name in enumerate(TARGETS.values())
            }))
            baseline = export_stage14_native_evidence(nro, output)
            ghidra = {
                "build_id": baseline["build_id"],
                "functions": {
                    key: {
                        "symbol": value["symbol"],
                        "file_offset": value["file_offset"],
                        "first_16_bytes": value["first_16_bytes"],
                        "callers": [],
                        "instructions": [],
                    }
                    for key, value in baseline["functions"].items()
                },
                "object_candidates": [],
            }
            ghidra["functions"]["music_pause"]["first_16_bytes"] = "00" * 16
            ghidra_path.write_text(json.dumps(ghidra), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "music_pause.*first_16_bytes"):
                export_stage14_native_evidence(nro, output, ghidra_path)

    def test_non_authoritative_or_semantically_mismatched_candidates_keep_object_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro = root / "Repentance.nro"
            output = root / "evidence.json"
            ghidra_path = root / "ghidra.json"
            nro.write_bytes(make_nro_fixture({
                name: 0x400 + index * 0x20
                for index, name in enumerate(TARGETS.values())
            }))
            baseline = export_stage14_native_evidence(nro, output)
            ghidra = {
                "build_id": baseline["build_id"],
                "program": "Repentance.nro",
                "functions": {
                    key: {
                        "symbol": value["symbol"],
                        "file_offset": value["file_offset"],
                        "first_16_bytes": value["first_16_bytes"],
                        "callers": [],
                        "instructions": [],
                    }
                    for key, value in baseline["functions"].items()
                },
                "object_candidates": [
                    {
                        "address": "0000000000800000",
                        "access_function": "game_is_paused",
                        "reference_address": "00000000003539E4",
                        "reference_kind": "non_call_reference",
                        "name": "game",
                    },
                    {
                        "address": "0000000000800010",
                        "access_function": "music_pause",
                        "reference_address": "0000000000427650",
                        "reference_kind": "global_object",
                        "name": "game",
                    },
                    {
                        "address": "0000000000800020",
                        "access_function": "game_is_paused",
                        "reference_address": "00000000003539E8",
                        "reference_kind": "global_object",
                        "name": "music",
                    },
                ],
            }
            ghidra_path.write_text(json.dumps(ghidra), encoding="utf-8")

            evidence = export_stage14_native_evidence(nro, output, ghidra_path)

            self.assertFalse(evidence["ready_for_runtime_binding"])
            self.assertEqual(
                evidence["blocked_reasons"],
                ["object_ownership_unproven", "render_relay_unproven"],
            )


if __name__ == "__main__":
    unittest.main()
