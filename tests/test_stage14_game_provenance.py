import hashlib
import json
import os
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path

from tests.test_content_reader_evidence import make_nro_fixture


ROOT = Path(__file__).resolve().parents[1]
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)


def _anchor_fixture(offset_delta: int = 0) -> bytes:
    from tools.stage14_game_provenance import ANCHORS

    symbols = {
        symbol: offset + (offset_delta if key == "game_is_paused" else 0)
        for key, (symbol, offset, _) in ANCHORS.items()
    }
    data = bytearray(make_nro_fixture(symbols))
    largest_offset = max(offset for _, offset, _ in ANCHORS.values())
    data.extend(bytes(largest_offset + 16 - len(data)))
    string_offset = 0x400
    struct.pack_into("<Q", data, 0xA0 + 16 + 8, string_offset)
    cursor = string_offset + 1
    for index, (symbol, offset) in enumerate(symbols.items(), start=1):
        encoded = symbol.encode("ascii") + b"\0"
        data[cursor:cursor + len(encoded)] = encoded
        struct.pack_into(
            "<IBBHQQ", data, 0x180 + index * 24,
            cursor - string_offset, 2, 0, 1, offset, 0,
        )
        cursor += len(encoded)
    for key, (_, offset, guard) in ANCHORS.items():
        if key != "game_is_paused" or offset_delta == 0:
            data[offset:offset + 16] = bytes.fromhex(guard)
    return bytes(data)


class Stage14GameProvenanceTests(unittest.TestCase):
    def test_game_anchor_evidence_is_generated_from_formal_nro_lock(self):
        from tools.export_stage14_game_anchor_evidence import write_game_anchor_evidence

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-anchor.json"
            document = write_game_anchor_evidence(NRO_PATH, output)
            self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
            self.assertEqual(document["anchors"]["game_is_paused"]["file_offset"], 0x3539DC)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)

    def test_ghidra_exporter_is_read_only(self):
        script = ROOT / "analysis" / "ghidra" / "scripts" / "ExportStage14GameProvenance.java"
        self.assertTrue(script.exists(), "Stage 14 Game exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "BUILD_ID",
            "getReferencesTo",
            "getReferencesFrom",
            "getFunctionContaining",
            "getInstructions",
            "getMemory().getBlock",
            "verifyAnchorEvidence",
            "getExecutableSHA256()",
            "Program SHA-256 mismatch",
            "Files.newBufferedWriter",
            "StandardCharsets.UTF_8",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "createFunction(",
            "setBytes(",
            "createLabel(",
            "delete(",
            "FileWriter",
            "runtime/",
            ".ips",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("block.isExecute()", source)
        self.assertIn("block.contains(end)", source)

        output = ROOT / "analysis" / "stage14-native-api" / (
            "game-provenance-91C73FDD575061318D68886316AFEAC72388B2AB.json"
        )
        if output.exists():
            output.unlink()
        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        command = [
            str(ghidra), str(ROOT / "analysis" / "ghidra"), "isaac-switch",
            "-process", "Repentance.nro", "-noanalysis",
            "-scriptPath", str(script.parent), "-postScript", script.name,
            str(ROOT / "analysis" / "stage14-native-api" / "game-anchor-91C73FDD575061318D68886316AFEAC72388B2AB.json"),
            str(output),
        ]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        self.assertNotIn("REPORT SCRIPT ERROR:", result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(output.exists())
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        for anchor in document["anchors"].values():
            for callsite in anchor["callers"]:
                self.assertEqual(
                    set(callsite),
                    {"caller", "call_address", "context_before", "context_after"},
                )
                self.assertLessEqual(len(callsite["context_before"]), 12)
                self.assertLessEqual(len(callsite["context_after"]), 12)
        for candidate in document["api_candidates"]:
            self.assertIsInstance(candidate["file_offset"], int)
            self.assertRegex(candidate["first_16_bytes"], r"^[0-9A-F]{32}$")
            self.assertIn("call_address", candidate["xref_context"])

    def test_ghidra_export_rejects_game_paused_guard_mismatch(self):
        script = ROOT / "analysis" / "ghidra" / "scripts" / "ExportStage14GameProvenance.java"
        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            temporary_script = Path(directory) / script.name
            source = script.read_text(encoding="utf-8").replace(
                '"FD7BBEA9F44F01A9FD03009108339F52"',
                '"007BBEA9F44F01A9FD03009108339F52"', 1,
            )
            temporary_script.write_text(source, encoding="utf-8")
            temporary_evidence = Path(directory) / "game-anchor.json"
            temporary_evidence.write_text(
                (ROOT / "analysis" / "stage14-native-api" / "game-anchor-91C73FDD575061318D68886316AFEAC72388B2AB.json")
                .read_text(encoding="utf-8")
                .replace("FD7BBEA9F44F01A9FD03009108339F52", "007BBEA9F44F01A9FD03009108339F52"),
                encoding="utf-8",
            )
            output = Path(directory) / "game-provenance.json"
            command = [
                str(ghidra), str(ROOT / "analysis" / "ghidra"), "isaac-switch",
                "-process", "Repentance.nro", "-noanalysis",
                "-scriptPath", directory, "-postScript", temporary_script.name,
                str(temporary_evidence),
                str(output),
            ]
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, env=environment
            )
            combined = result.stdout + result.stderr
            self.assertTrue(
                result.returncode != 0 or "REPORT SCRIPT ERROR:" in combined,
                combined,
            )
            self.assertIn("Entry guard mismatch for game_is_paused", combined)
            self.assertFalse(output.exists())

    def test_game_anchor_nro_is_fully_version_locked(self):
        from tools.stage14_game_provenance import ANCHORS, verify_game_anchor_nro

        anchors = verify_game_anchor_nro(NRO_PATH)

        self.assertEqual(list(anchors), list(ANCHORS))
        self.assertEqual(anchors["game_is_paused"]["file_offset"], 0x3539DC)
        self.assertEqual(anchors["pause_show"]["file_offset"], 0x43274C)
        self.assertEqual(
            anchors["game_is_paused"]["first_16_bytes"],
            "FD7BBEA9F44F01A9FD03009108339F52",
        )

    def test_formal_entry_rejects_fixture_source_sha256(self):
        from tools.stage14_game_provenance import verify_game_anchor_nro

        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "Repentance.nro"
            fixture_path.write_bytes(_anchor_fixture())
            with self.assertRaisesRegex(ValueError, "source_sha256"):
                verify_game_anchor_nro(fixture_path)

    def test_internal_entry_rejects_incorrect_build_id(self):
        from tools.stage14_game_provenance import _verify_game_anchor_data

        data = bytearray(_anchor_fixture())
        data[0x40] ^= 0xFF
        fixture = bytes(data)
        with self.assertRaisesRegex(ValueError, "build ID"):
            _verify_game_anchor_data(fixture, hashlib.sha256(fixture).hexdigest())

    def test_internal_entry_rejects_incorrect_symbol_offset(self):
        from tools.stage14_game_provenance import _verify_game_anchor_data

        fixture = _anchor_fixture(offset_delta=4)
        with self.assertRaisesRegex(ValueError, "game_is_paused: file_offset"):
            _verify_game_anchor_data(fixture, hashlib.sha256(fixture).hexdigest())

    def test_internal_entry_rejects_incorrect_entry_guard(self):
        from tools.stage14_game_provenance import _verify_game_anchor_data

        data = bytearray(_anchor_fixture())
        data[0x43274C] ^= 0xFF
        fixture = bytes(data)
        with self.assertRaisesRegex(ValueError, "pause_show: entry guard"):
            _verify_game_anchor_data(fixture, hashlib.sha256(fixture).hexdigest())

    def test_merge_requires_complete_game_chain(self):
        from tools.merge_stage14_game_provenance import merge_game_provenance
        from tools.stage14_game_provenance import ANCHORS, TARGET_BUILD_ID, TARGET_SOURCE_SHA256

        anchors = {
            key: {
                "symbol": symbol,
                "entry_address": f"{offset:08x}",
                "first_16_bytes": guard,
            }
            for key, (symbol, offset, guard) in ANCHORS.items()
        }

        def raw_fixture(owners, x0_instructions, calls, lifetime):
            lifetime_events = [
                {"event": "game_ctor", "owner_address": "00800000"},
                {"event": "game_init", "owner_address": "00800000", "call_address": "003539DC", "update_compatible": True},
            ] if lifetime == ["game_ctor", "game_init"] else lifetime
            return {
                "build_id": TARGET_BUILD_ID,
                "schema_version": "stage14-game-provenance-v2",
                "source_sha256": TARGET_SOURCE_SHA256,
                "program": "Repentance.nro",
                "anchors": anchors,
                "object_references": [
                    {
                        "from": "game_init",
                        "reference_address": owner,
                        "target_address": "00800000",
                        "reference_kind": "READ",
                        "memory_block_read": True,
                        "memory_block_execute": False,
                    }
                    for owner in owners
                ],
                "edges": [
                    {
                        "from": "game_init",
                        "call_address": call,
                        "to": "003539DC",
                        "edge_kind": "CALL",
                        "owner_from": "game_init",
                        "object_address": "00800000",
                        "x0_source_address": "00800000",
                        "context_before": x0_instructions,
                    }
                    for call in calls
                ],
                "lifetime_events": lifetime_events,
                "api_candidates": [
                    {
                        "semantic_source": "game_init",
                        "file_offset": 0x3539DC,
                        "first_16_bytes": ANCHORS["game_is_paused"][2],
                        "xref_context": {"call_address": "003539DC"},
                        "status": "observed",
                    }
                ],
            }

        native = {
            "build_id": TARGET_BUILD_ID,
            "source_sha256": TARGET_SOURCE_SHA256,
            "functions": {
                "game_is_paused": {
                    "symbol": ANCHORS["game_is_paused"][0],
                    "file_offset": ANCHORS["game_is_paused"][1],
                    "first_16_bytes": ANCHORS["game_is_paused"][2],
                }
            },
            "ready_for_runtime_binding": False,
            "blocked_reasons": ["object_ownership_unproven", "render_relay_unproven"],
        }
        cases = {
            "no owner": ([], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_init"]),
            "broken x0": (["00800000"], ["003539d8: mov x1,x8"], ["003539DC"], ["game_init"]),
            "no target call": (["00800000"], ["003539d8: ldr x0,[0x00800000]"], [], ["game_init"]),
            "no lifetime": (["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], []),
            "complete": (["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_ctor", "game_init"]),
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_path = root / "native.json"
            raw_path = root / "raw.json"
            output_path = root / "merged.json"
            native_path.write_text(json.dumps(native), encoding="utf-8")
            for name, (owners, x0, calls, lifetime) in cases.items():
                with self.subTest(name=name):
                    raw = raw_fixture(owners, x0, calls, lifetime)
                    raw_path.write_text(json.dumps(raw), encoding="utf-8")
                    merged = merge_game_provenance(native_path, raw_path, output_path)
                    self.assertFalse(merged["ready_for_runtime_binding"])
                    self.assertIn("render_relay_unproven", merged["blocked_reasons"])
                    if name == "complete":
                        self.assertEqual(merged["game"]["status"], "proven")
                        self.assertNotIn("object_ownership_unproven", merged["blocked_reasons"])
                    else:
                        self.assertEqual(merged["game"]["status"], "unproven")
                        self.assertIn("object_ownership_unproven", merged["blocked_reasons"])
                    self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), merged)

            malformed = raw_fixture(["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_ctor", "game_init"])
            malformed["build_id"] = "00" * 20
            raw_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_game_provenance(native_path, raw_path, output_path)
            malformed = raw_fixture(["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_ctor", "game_init"])
            malformed["source_sha256"] = "00" * 32
            raw_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_game_provenance(native_path, raw_path, output_path)
            malformed = raw_fixture(["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_ctor", "game_init"])
            malformed["anchors"]["game_is_paused"]["first_16_bytes"] = "00" * 16
            raw_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_game_provenance(native_path, raw_path, output_path)
            malformed = raw_fixture(["00800000"], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], ["game_ctor", "game_init"])
            del malformed["api_candidates"][0]["xref_context"]
            raw_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_game_provenance(native_path, raw_path, output_path)
            malformed = raw_fixture([], ["003539d8: ldr x0,[0x00800000]"], ["003539DC"], [])
            malformed["api_candidates"][0]["status"] = "proven_for_future_stage"
            raw_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(ValueError):
                merge_game_provenance(native_path, raw_path, output_path)

    def test_merge_rejects_unrelated_chain_fragments(self):
        from tools.merge_stage14_game_provenance import merge_game_provenance
        from tools.stage14_game_provenance import ANCHORS, TARGET_BUILD_ID, TARGET_SOURCE_SHA256
        anchors = {key: {"symbol": symbol, "entry_address": f"{offset:08x}", "first_16_bytes": guard}
                   for key, (symbol, offset, guard) in ANCHORS.items()}
        native = {"build_id": TARGET_BUILD_ID, "source_sha256": TARGET_SOURCE_SHA256,
                  "functions": {"game_is_paused": {"symbol": ANCHORS["game_is_paused"][0],
                  "file_offset": ANCHORS["game_is_paused"][1], "first_16_bytes": ANCHORS["game_is_paused"][2]}},
                  "blocked_reasons": ["object_ownership_unproven", "render_relay_unproven"]}
        def raw(owner_from="game_init", owner_target="00800000", x0_addr="003539d8",
                x0_operand="[0x00800000]", call_addr="003539dc", edge_kind="CALL",
                lifetime_owner="00800000", dtor=False, update=True, init_call=True,
                lifetime_events=None):
            events = [{"event": "game_ctor", "owner_address": lifetime_owner},
                      {"event": "game_init", "owner_address": lifetime_owner,
                       "update_compatible": update}]
            if init_call: events[1]["call_address"] = call_addr
            if dtor: events.insert(1, {"event": "game_dtor", "owner_address": lifetime_owner, "call_address": call_addr})
            if lifetime_events is not None: events = lifetime_events
            return {"schema_version": "stage14-game-provenance-v2", "build_id": TARGET_BUILD_ID, "source_sha256": TARGET_SOURCE_SHA256, "program": "Repentance.nro",
                    "anchors": anchors,
                    "object_references": [{"from": owner_from, "reference_address": owner_from,
                        "target_address": owner_target, "reference_kind": "READ",
                        "memory_block_read": True, "memory_block_execute": False}],
                    "edges": [{"from": "game_init", "call_address": call_addr, "to": "003539dc",
                        "edge_kind": edge_kind, "owner_from": "game_init", "object_address": owner_target,
                        "x0_source_address": owner_target,
                        "context_before": [f"{x0_addr}: ldr x0,{x0_operand}"]}],
                    "lifetime_events": events,
                    "api_candidates": [{"semantic_source": "game_init", "file_offset": 0x3539dc,
                        "first_16_bytes": ANCHORS["game_is_paused"][2], "xref_context": {"call_address": call_addr}, "status": "observed"}]}
        all_links = ["owner_source_missing", "x0_chain_missing", "is_paused_call_missing", "lifetime_evidence_missing"]
        cases = {
            "unrelated owner": (raw(owner_from="other_init"), all_links),
            "wrong call address": (raw(call_addr="003539e0"), all_links),
            "non call edge": (raw(edge_kind="DATA"), all_links),
            "x0 operand unrelated": (raw(x0_operand="[0x00900000]"), all_links),
            "dtor before call": (raw(dtor=True), ["lifetime_evidence_missing"]),
            "dtor event order wins over address": (raw(lifetime_events=[
                {"event": "game_ctor", "owner_address": "00800000"},
                {"event": "game_dtor", "owner_address": "00800000", "call_address": "00354000"},
                {"event": "game_init", "owner_address": "00800000", "call_address": "003539dc", "update_compatible": True},
            ]), ["lifetime_evidence_missing"]),
            "lifetime unrelated": (raw(lifetime_owner="00900000"), ["lifetime_evidence_missing"]),
            "no update compatibility": (raw(update=False), ["lifetime_evidence_missing"]),
            "lifetime call missing": (raw(init_call=False), ["lifetime_evidence_missing"]),
            "ctor owner missing": (raw(lifetime_events=[{"event": "game_ctor"}, {"event": "game_init", "owner_address": "00800000", "call_address": "003539dc", "update_compatible": True}]), ["lifetime_evidence_missing"]),
            "init call differs": (raw(lifetime_events=[{"event": "game_ctor", "owner_address": "00800000"}, {"event": "game_init", "owner_address": "00800000", "call_address": "003539e0", "update_compatible": True}]), ["lifetime_evidence_missing"]),
            "structured init before ctor": (raw(lifetime_events=["game_ctor", {"event": "game_init", "owner_address": "00800000", "call_address": "003539dc", "update_compatible": True}, {"event": "game_ctor", "owner_address": "00800000"}, "game_init"]), ["lifetime_evidence_missing"]),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); native_path = root / "native.json"; raw_path = root / "raw.json"; output = root / "out.json"
            native_path.write_text(json.dumps(native), encoding="utf-8")
            for name, (evidence, expected_links) in cases.items():
                with self.subTest(name=name):
                    raw_path.write_text(json.dumps(evidence), encoding="utf-8")
                    merged = merge_game_provenance(native_path, raw_path, output)
                    self.assertEqual(merged["game"]["status"], "unproven")
                    self.assertEqual(merged["game"]["blocking_links"], expected_links)
                    self.assertEqual(merged["game"]["lifetime_evidence"], [])


if __name__ == "__main__":
    unittest.main()
