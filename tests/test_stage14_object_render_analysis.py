import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
PROVENANCE_PATH = (
    ROOT
    / "analysis"
    / "stage14-native-api"
    / f"object-provenance-{BUILD_ID}.json"
)
NATIVE_EVIDENCE_PATH = (
    ROOT / "analysis" / "stage14-native-api" / f"{BUILD_ID}.json"
)
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)
SCRIPT_PATH = (
    ROOT / "analysis" / "ghidra" / "scripts" / "ExportStage14ObjectProvenance.java"
)
GHIDRA_HEADLESS = Path(
    "/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless"
)


def run_provenance_export(script_directory: Path, output: Path):
    result = subprocess.run(
        [
            str(GHIDRA_HEADLESS),
            str(ROOT / "analysis" / "ghidra"),
            "isaac-switch",
            "-process",
            "Repentance.nro",
            "-noanalysis",
            "-scriptPath",
            str(script_directory),
            "-postScript",
            SCRIPT_PATH.name,
            str(output),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 0 and "REPORT SCRIPT ERROR:" in result.stdout:
        return subprocess.CompletedProcess(
            result.args, 1, stdout=result.stdout, stderr=result.stderr
        )
    return result


class ObjectRenderAnalysisTests(unittest.TestCase):
    def test_render_candidate_requires_manual_review(self):
        from tools.inspect_stage14_render_candidate import inspect_render_candidate

        candidate_offset = 0x68CC00
        render_guard = bytes.fromhex("FFC302D1E83B00FDFD7B08A9FD030291")

        review = inspect_render_candidate(
            NRO_PATH, candidate_offset, NATIVE_EVIDENCE_PATH
        )

        self.assertEqual(review["status"], "needs_manual_control_flow_review")
        self.assertNotEqual(review["status"], "ready")
        self.assertEqual(review["candidate_bytes"], bytes(0x20).hex().upper())
        self.assertEqual(review["entry_guard"], render_guard.hex().upper())
        self.assertEqual(review["text_range"], {"start": 0, "end": 0x68D000})
        self.assertFalse(review["reserved_overlap"])
        self.assertTrue(review["branch"]["reachable"])
        self.assertEqual(review["branch"]["encoding_hex"], "5F4D0A14")
        self.assertTrue(review["fallback"]["inside_candidate_window"])
        self.assertTrue(review["slot"]["inside_candidate_window"])
        self.assertGreater(len(review["surrounding_text_bytes"]), 0x40)

        rejected = {
            "reserved candidate": (0x68CBE0, "reserved interval"),
            "outside declared text": (0x68D000, "outside declared NRO text"),
            "unaligned candidate": (0x68CC01, "4-byte aligned"),
            "nonzero window": (0x3F9684, "unique recorded Render offset"),
        }
        for name, (invalid_candidate, message) in rejected.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, message):
                    inspect_render_candidate(
                        NRO_PATH, invalid_candidate, NATIVE_EVIDENCE_PATH
                    )

    def test_render_candidate_rejects_self_consistent_forgery(self):
        from tests.test_content_reader_evidence import make_nro_fixture
        from tools.inspect_stage14_render_candidate import inspect_render_candidate

        render_offset = 0x3F9684
        candidate_offset = 0x68CC00
        text_end = 0x68D000
        render_guard = bytes.fromhex("FFC302D1E83B00FDFD7B08A9FD030291")
        data = bytearray(b"\xA5" * (text_end + 0x100))
        base = make_nro_fixture({})
        data[:len(base)] = base
        data[0x20:0x24] = (0).to_bytes(4, "little")
        data[0x24:0x28] = text_end.to_bytes(4, "little")
        data[render_offset:render_offset + len(render_guard)] = render_guard
        data[candidate_offset:candidate_offset + 0x20] = bytes(0x20)
        candidate = {
            "code_offset": candidate_offset,
            "length": 0x20,
            "entry_branch_hex": "5F4D0A14",
            "fallback_offset": candidate_offset + 0x10,
            "slot_offset": candidate_offset + 0x18,
            "first_32_bytes": bytes(0x20).hex().upper(),
        }
        evidence = {
            "build_id": BUILD_ID,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "functions": {
                "manager_render": {
                    "symbol": "_ZN15IsaacRepentance7Manager6RenderEv",
                    "file_offset": render_offset,
                    "first_16_bytes": render_guard.hex().upper(),
                    "is_defined": True,
                }
            },
            "render_candidates": [candidate],
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nro_path = root / "Repentance.nro"
            evidence_path = root / "evidence.json"
            nro_path.write_bytes(data)
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "source_sha256"):
                inspect_render_candidate(nro_path, candidate_offset, evidence_path)

    def test_render_candidate_rejects_fixed_version_evidence_drift(self):
        from tools.inspect_stage14_render_candidate import inspect_render_candidate

        candidate_offset = 0x68CC00
        evidence = json.loads(NATIVE_EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(evidence["render_candidates"]), 234)
        variants = {}

        missing_candidate = json.loads(json.dumps(evidence))
        missing_candidate["render_candidates"].pop()
        variants["missing candidate"] = missing_candidate

        changed_candidate = json.loads(json.dumps(evidence))
        changed_candidate["render_candidates"][1]["slot_offset"] += 4
        variants["changed unselected candidate"] = changed_candidate

        fallback_outside = json.loads(json.dumps(evidence))
        fallback_outside["render_candidates"][0]["fallback_offset"] += 0xC
        variants["fallback outside selected window"] = fallback_outside

        slot_outside = json.loads(json.dumps(evidence))
        slot_outside["render_candidates"][0]["slot_offset"] += 8
        variants["slot outside selected window"] = slot_outside

        changed_source = json.loads(json.dumps(evidence))
        changed_source["source_sha256"] = "0" * 64
        variants["changed source SHA-256"] = changed_source

        changed_render = json.loads(json.dumps(evidence))
        changed_render["functions"]["manager_render"]["file_offset"] += 4
        variants["changed Manager::Render offset"] = changed_render

        changed_guard = json.loads(json.dumps(evidence))
        changed_guard["functions"]["manager_render"]["first_16_bytes"] = "00" * 16
        variants["changed Manager::Render guard"] = changed_guard

        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "evidence.json"
            for name, variant in variants.items():
                with self.subTest(name=name):
                    evidence_path.write_text(json.dumps(variant), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        inspect_render_candidate(
                            NRO_PATH, candidate_offset, evidence_path
                        )

    def test_render_candidate_cli_runs_directly(self):
        script = ROOT / "tools" / "inspect_stage14_render_candidate.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "review.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(NRO_PATH),
                    "--candidate",
                    "0x68CC00",
                    "--evidence",
                    str(NATIVE_EVIDENCE_PATH),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            review = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                review["status"], "needs_manual_control_flow_review"
            )
            self.assertNotEqual(review["status"], "ready")

    def test_provenance_script_is_read_only(self):
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("public class ExportStage14ObjectProvenance", script)
        self.assertIn(BUILD_ID, script)
        self.assertIn("getReferencesTo", script)
        self.assertIn("getReferenceType().isCall()", script)
        self.assertIn("getFunctionContaining", script)
        self.assertIn("getInstructions(caller.getBody()", script)
        self.assertIn("getReferencesFrom", script)
        self.assertIn("Files.newBufferedWriter", script)
        self.assertIn("StandardCharsets.UTF_8", script)
        for forbidden in (
            "createFunction(",
            "setBytes(",
            "createLabel(",
            "delete(",
            "FileWriter",
            "runtime/",
            "runtime\\\\",
            ".ips",
        ):
            self.assertNotIn(forbidden, script)

    def test_provenance_rejects_fallback_entry_guard_mismatch(self):
        """Fixed-Build-ID offset fallback must authenticate each key's entry bytes."""
        if not GHIDRA_HEADLESS.is_file() or not os.access(GHIDRA_HEADLESS, os.X_OK):
            self.skipTest(f"Ghidra 12.1.3 headless is unavailable: {GHIDRA_HEADLESS}")
        expected_guard = "080080B9093C80520801099B00210091"
        mismatching_guard = "FF0080B9093C80520801099B00210091"
        exact_symbol_line = (
            'TARGETS.put("music_pause", "_ZN15IsaacRepentance5Music5PauseEv");'
        )
        missing_symbol_line = (
            'TARGETS.put("music_pause", "__stage14_missing_music_pause_symbol__");'
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script_directory = root / "scripts"
            script_directory.mkdir()
            script = SCRIPT_PATH.read_text(encoding="utf-8")
            guard_line = (
                'TARGET_GUARDS.put("music_pause", "' + expected_guard + '");'
            )
            self.assertEqual(script.count(guard_line), 1)
            self.assertEqual(script.count(exact_symbol_line), 1)
            modified_script = script.replace(
                guard_line,
                'TARGET_GUARDS.put("music_pause", "' + mismatching_guard + '");',
            ).replace(exact_symbol_line, missing_symbol_line)
            self.assertEqual(modified_script.count(exact_symbol_line), 0)
            self.assertEqual(modified_script.count(missing_symbol_line), 1)
            (script_directory / SCRIPT_PATH.name).write_text(
                modified_script, encoding="utf-8"
            )
            output = root / "must-not-exist.json"

            result = run_provenance_export(script_directory, output)

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Entry guard mismatch for music_pause", result.stdout)
            self.assertFalse(output.exists())

    def test_provenance_evidence_has_stable_callsite_schema(self):
        evidence = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(evidence["build_id"], BUILD_ID)
        self.assertEqual(evidence["program"], "Repentance.nro")
        self.assertEqual(
            list(evidence["targets"]),
            [
                "game_is_paused",
                "music_current_id",
                "music_pause",
                "music_resume",
                "manager_render",
            ],
        )
        for key, target in evidence["targets"].items():
            self.assertEqual(target["key"], key)
            self.assertEqual(
                target["callsites"],
                sorted(
                    target["callsites"],
                    key=lambda callsite: (
                        callsite["reference_address"],
                        callsite["caller"],
                    ),
                ),
            )
            for callsite in target["callsites"]:
                self.assertEqual(
                    set(callsite),
                    {
                        "target",
                        "caller",
                        "reference_address",
                        "context_before",
                        "context_after",
                        "reference_kind",
                        "non_call_references",
                    },
                )
                self.assertLessEqual(len(callsite["context_before"]), 8)
                self.assertLessEqual(len(callsite["context_after"]), 8)
                self.assertEqual(
                    callsite["non_call_references"],
                    sorted(
                        callsite["non_call_references"],
                        key=lambda reference: (
                            reference["reference_address"],
                            reference["target_address"],
                            reference["reference_kind"],
                        ),
                    ),
                )
                for reference in callsite["non_call_references"]:
                    self.assertEqual(
                        set(reference),
                        {
                            "target_address",
                            "reference_address",
                            "reference_kind",
                            "primary_symbol_name",
                            "primary_symbol_source",
                            "memory_block_name",
                            "memory_block_read",
                            "memory_block_execute",
                        },
                    )

        contents = PROVENANCE_PATH.read_bytes()
        self.assertNotIn(b"\r\n", contents)
        self.assertTrue(contents.endswith(b"\n"))

    def test_object_classifier_requires_provenance(self):
        from tools.merge_stage14_object_provenance import merge_object_provenance

        native = json.loads(NATIVE_EVIDENCE_PATH.read_text(encoding="utf-8"))

        def provenance_fixture():
            return {
                "build_id": BUILD_ID,
                "program": "Repentance.nro",
                "targets": {
                    key: {
                        "key": key,
                        "symbol": function["symbol"],
                        "entry_address": f'{function["file_offset"]:08x}',
                        "first_16_bytes": function["first_16_bytes"],
                        "callsites": [],
                    }
                    for key, function in native["functions"].items()
                },
            }

        def callsite(target, instruction, reference):
            return {
                "target": target,
                "caller": "fixture_caller@00100000",
                "reference_address": "00100020",
                "context_before": [instruction],
                "context_after": [],
                "reference_kind": "UNCONDITIONAL_CALL",
                "non_call_references": [reference],
            }

        def reference(address, *, source="USER_DEFINED"):
            return {
                "target_address": address,
                "reference_address": "0010001c",
                "reference_kind": "DATA",
                "primary_symbol_name": f"fixture_{address}",
                "primary_symbol_source": source,
                "memory_block_name": ".data",
                "memory_block_read": True,
                "memory_block_execute": False,
            }

        missing_x0 = provenance_fixture()
        missing_x0["targets"]["game_is_paused"]["callsites"] = [
            callsite(
                "game_is_paused",
                "0010001c: mov x1,x8",
                reference("00700000"),
            )
        ]
        non_authoritative = provenance_fixture()
        non_authoritative["targets"]["game_is_paused"]["callsites"] = [
            callsite(
                "game_is_paused",
                "0010001c: ldr x0,[x8,#0x20]",
                reference("00700000", source="DEFAULT"),
            )
        ]
        missing_lifetime = provenance_fixture()
        missing_lifetime["targets"]["game_is_paused"]["callsites"] = [
            callsite(
                "game_is_paused",
                "0010001c: ldr x0,[x8,#0x20]",
                reference("00700000"),
            )
        ]

        fixtures = {
            "no callsites": provenance_fixture(),
            "preceding instruction does not establish x0": missing_x0,
            "data symbol name is not authoritative": non_authoritative,
            "x0 source has no lifetime evidence": missing_lifetime,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native_path = root / "native.json"
            native_path.write_text(
                json.dumps(native), encoding="utf-8"
            )
            for name, provenance in fixtures.items():
                with self.subTest(name=name):
                    provenance_path = root / "provenance.json"
                    output_path = root / "merged.json"
                    provenance_path.write_text(
                        json.dumps(provenance), encoding="utf-8"
                    )

                    merged = merge_object_provenance(
                        native_path, provenance_path, output_path
                    )

                    self.assertFalse(merged["ready_for_runtime_binding"])
                    self.assertIn(
                        "object_ownership_unproven", merged["blocked_reasons"]
                    )
                    self.assertIn(
                        "render_relay_unproven", merged["blocked_reasons"]
                    )
                    self.assertEqual(set(merged["objects"]), {"game", "music"})
                    for object_evidence in merged["objects"].values():
                        self.assertEqual(object_evidence["status"], "unproven")
                        self.assertEqual(object_evidence["return_register"], "unproven")
                        self.assertEqual(
                            set(object_evidence),
                            {
                                "status",
                                "candidate_refs",
                                "this_register",
                                "return_register",
                                "source_kind",
                                "lifetime_evidence",
                            },
                        )
                    self.assertEqual(merged["objects"]["music"]["candidate_refs"], [])
                    self.assertEqual(
                        json.loads(output_path.read_text(encoding="utf-8")), merged
                    )

            self.assertEqual(
                merge_object_provenance(
                    native_path,
                    root / "provenance.json",
                    root / "merged.json",
                )["objects"]["game"]["candidate_refs"],
                missing_lifetime["targets"]["game_is_paused"]["callsites"][0][
                    "non_call_references"
                ],
            )

            unverified = provenance_fixture()
            unverified["targets"]["game_is_paused"]["callsites"] = [
                callsite(
                    "game_is_paused",
                    "0010001c: ldr x0,[x8,#0x20]",
                    reference("00700000"),
                )
            ]
            music_callsite = callsite(
                "music_pause",
                "0010001c: ldr x0,[0x00700008]",
                reference("00700008"),
            )
            music_callsite["caller"] = "music_fixture@00100000"
            unverified["targets"]["music_pause"]["callsites"] = [music_callsite]
            unverified["lifetime_observations"] = [
                {
                    "object": "game",
                    "target_address": "00700000",
                    "kind": "constructor",
                    "evidence": "fixture constructor stores the live object",
                },
                {
                    "object": "music",
                    "target_address": "00700008",
                    "kind": "singleton",
                    "evidence": "fixture singleton remains live during the caller",
                },
            ]
            unverified_path = root / "unverified.json"
            unverified_path.write_text(json.dumps(unverified), encoding="utf-8")

            merged = merge_object_provenance(
                native_path, unverified_path, root / "unverified-merged.json"
            )

            for object_evidence in merged["objects"].values():
                self.assertEqual(object_evidence["status"], "unproven")
                self.assertEqual(object_evidence["this_register"], "unproven")
                self.assertEqual(object_evidence["return_register"], "unproven")
                self.assertEqual(object_evidence["lifetime_evidence"], [])
            self.assertEqual(
                merged["objects"]["game"]["source_kind"],
                "candidate_data_reference",
            )
            self.assertEqual(
                merged["objects"]["music"]["source_kind"],
                "candidate_data_reference",
            )
            self.assertIn("object_ownership_unproven", merged["blocked_reasons"])
            self.assertIn("render_relay_unproven", merged["blocked_reasons"])
            self.assertFalse(merged["ready_for_runtime_binding"])

    def test_object_classifier_rejects_non_target_version(self):
        from tools.merge_stage14_object_provenance import merge_object_provenance

        native = json.loads(NATIVE_EVIDENCE_PATH.read_text(encoding="utf-8"))

        def provenance_fixture(native_evidence):
            return {
                "build_id": native_evidence["build_id"],
                "program": "Repentance.nro",
                "targets": {
                    key: {
                        "key": key,
                        "symbol": function["symbol"],
                        "entry_address": f'{function["file_offset"]:08x}',
                        "first_16_bytes": function["first_16_bytes"],
                        "callsites": [],
                    }
                    for key, function in native_evidence["functions"].items()
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native_path = root / "native.json"
            provenance_path = root / "provenance.json"
            output_path = root / "merged.json"

            wrong_build_native = dict(native)
            wrong_build_native["build_id"] = "00" * 20
            wrong_build_provenance = provenance_fixture(wrong_build_native)
            native_path.write_text(json.dumps(wrong_build_native), encoding="utf-8")
            provenance_path.write_text(
                json.dumps(wrong_build_provenance), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unsupported native build_id"):
                merge_object_provenance(native_path, provenance_path, output_path)

            wrong_sha_native = dict(native)
            wrong_sha_native["source_sha256"] = "00" * 32
            wrong_sha_provenance = provenance_fixture(wrong_sha_native)
            wrong_sha_provenance["source_sha256"] = "00" * 32
            native_path.write_text(json.dumps(wrong_sha_native), encoding="utf-8")
            provenance_path.write_text(
                json.dumps(wrong_sha_provenance), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unsupported native source_sha256"):
                merge_object_provenance(native_path, provenance_path, output_path)

            native_path.write_text(json.dumps(native), encoding="utf-8")
            wrong_provenance_sha = provenance_fixture(native)
            wrong_provenance_sha["source_sha256"] = "00" * 32
            provenance_path.write_text(
                json.dumps(wrong_provenance_sha), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "provenance source_sha256"):
                merge_object_provenance(native_path, provenance_path, output_path)

            self.assertEqual(native["build_id"], BUILD_ID)
            self.assertEqual(native["source_sha256"], SOURCE_SHA256)


if __name__ == "__main__":
    unittest.main()
