import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0" / "1" / ".nro" / "Repentance.nro"
)
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage51RelayEvidence.java"
EVIDENCE = ROOT / "analysis/stage51-relay-evidence" / f"{BUILD_ID}.json"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")


def validate_export(result, output):
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        raise AssertionError(combined)
    if "REPORT SCRIPT ERROR" in combined:
        raise AssertionError(combined)
    if not output.is_file():
        raise AssertionError("Ghidra exporter did not create its output")
    try:
        return json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AssertionError("Ghidra exporter produced invalid JSON") from error


def validate_schema(document):
    if not isinstance(document, dict):
        raise AssertionError("document must be object")

    def field(name, expected_type):
        if name not in document:
            raise AssertionError(f"missing field: {name}")
        value = document[name]
        if expected_type is bool and type(value) is not bool:
            raise AssertionError(f"{name} must be bool")
        if expected_type is int and (type(value) is not int or isinstance(value, bool)):
            raise AssertionError(f"{name} must be int")
        if expected_type not in (bool, int) and not isinstance(value, expected_type):
            raise AssertionError(f"{name} must be {expected_type.__name__}")
        return value

    if field("schema_version", str) != "stage51-relay-evidence-v1":
        raise AssertionError("schema_version mismatch")
    if field("build_id", str) != BUILD_ID:
        raise AssertionError("build_id mismatch")
    if field("source_sha256", str) != SOURCE_SHA256:
        raise AssertionError("source_sha256 mismatch")
    if field("target_entry", str) != "003c6350":
        raise AssertionError("target_entry mismatch")
    if field("target_guard", str) != "FF0304D1E84B00FDFD7B0AA9FD830291":
        raise AssertionError("target_guard mismatch")
    if field("candidate_start", str) != "0068ce00":
        raise AssertionError("candidate_start mismatch")
    if field("candidate_length", int) != 0x80:
        raise AssertionError("candidate_length mismatch")
    if field("candidate_end_exclusive", str) != "0068ce80":
        raise AssertionError("candidate_end_exclusive mismatch")
    candidate_bytes = field("candidate_bytes", str)
    try:
        decoded_candidate = bytes.fromhex(candidate_bytes)
    except ValueError as error:
        raise AssertionError("candidate_bytes must be hexadecimal") from error
    if len(decoded_candidate) != field("candidate_length", int):
        raise AssertionError("candidate_bytes length mismatch")
    if field("candidate_all_zero", bool) is not True:
        raise AssertionError("candidate_all_zero must be true")
    if field("candidate_in_text", bool) is not True:
        raise AssertionError("candidate_in_text must be true")
    if field("candidate_function", type(None)) is not None:
        raise AssertionError("candidate_function must be null")

    text_block = field("text_block", dict)
    for name, expected in (("name", ".text"), ("start", "00000000"),
                           ("end_exclusive", "0068d000")):
        if type(text_block.get(name)) is not str or text_block[name] != expected:
            raise AssertionError(f"text_block.{name} mismatch")
    for name in ("execute", "contains"):
        if type(text_block.get(name)) is not bool or text_block[name] is not True:
            raise AssertionError(f"text_block.{name} must be true bool")

    ownership = field("function_ownership", dict)
    if type(ownership.get("owners")) is not list:
        raise AssertionError("function_ownership.owners must be list")
    if ownership["owners"]:
        raise AssertionError("function_ownership.owners must be empty")
    if type(ownership.get("unowned")) is not bool or ownership["unowned"] is not True:
        raise AssertionError("function_ownership.unowned must be true bool")
    if field("incoming_control_flow_references", list):
        raise AssertionError("incoming_control_flow_references must be empty")

    relay_overlap = field("relay_overlap", dict)
    if type(relay_overlap.get("overlaps")) is not bool or relay_overlap["overlaps"] is not False:
        raise AssertionError("relay_overlap.overlaps must be false bool")
    relays = relay_overlap.get("existing_relays")
    if type(relays) is not list or not relays:
        raise AssertionError("relay_overlap.existing_relays must be non-empty list")
    expected_relays = [
        {"name": "manager_update", "start": "0068cbe0", "end_exclusive": "0068cc00"},
        {"name": "manager_render", "start": "0068cc00", "end_exclusive": "0068cc20"},
        {"name": "manager_loadconfigs", "start": "0068cc20", "end_exclusive": "0068cc40"},
        {"name": "game_observer", "start": "0068cc40", "end_exclusive": "0068cc80"},
        {"name": "game_update_observer", "start": "0068cc80", "end_exclusive": "0068ccc0"},
        {"name": "game_state2_observer", "start": "0068ccc0", "end_exclusive": "0068cd00"},
        {"name": "game_ispaused_render_observer", "start": "0068cd00", "end_exclusive": "0068cd40"},
        {"name": "stage48_music_play", "start": "0068cd40", "end_exclusive": "0068cd80"},
        {"name": "stage48_soundactor_play", "start": "0068cd80", "end_exclusive": "0068cdc0"},
        {"name": "stage48_soundactor_pause", "start": "0068cdc0", "end_exclusive": "0068ce00"},
    ]
    if relays != expected_relays:
        raise AssertionError("relay_overlap.existing_relays mismatch")
    if field("overlapping_relays", list):
        raise AssertionError("overlapping_relays must be empty")
    if relay_overlap.get("overlapping_relays") != []:
        raise AssertionError("relay_overlap.overlapping_relays must be empty")

    entry_branch = field("entry_branch", dict)
    expected_branch = {
        "mnemonic": "B", "source": "003c6350", "target": "0068ce00",
        "delta": 0x68CE00 - 0x3C6350, "opcode_hex": "AC1A0B14", "reachable": True,
    }
    if entry_branch != expected_branch:
        raise AssertionError("entry_branch mismatch")

    conclusion = field("conclusion", dict)
    if conclusion != {
        "authorized": True,
        "reason": "candidate is a verified NRO-local relay cave reachable by one direct AArch64 B",
    }:
        raise AssertionError("conclusion mismatch")
    if field("ready_for_relay", bool) is not True:
        raise AssertionError("ready_for_relay must be true")
    if field("runtime_binding_authorized", bool) is not False:
        raise AssertionError("runtime_binding_authorized must be false")


class Stage51RelayEvidenceTests(unittest.TestCase):
    def test_exporter_is_read_only_and_locks_target_identity_and_cave(self):
        self.assertTrue(SCRIPT.is_file(), "Stage51 Ghidra exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            BUILD_ID, SOURCE_SHA256, "0x68CE00L", "0x3C6350L",
            "getBlock", "getFunctionContaining", "getReferencesTo",
            "Files.newBufferedWriter",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "createFunction(", "setBytes(", "createLabel(", "delete(",
            "runtime/", ".ips", "build_patches",
        ):
            self.assertNotIn(forbidden, source)

    def test_ghidra_exporter_writes_typed_static_evidence(self):
        self.assertTrue(SCRIPT.is_file(), "Stage51 Ghidra exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stage51.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ], cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            document = validate_export(result, output)
            validate_schema(document)

        self.assertEqual(document["schema_version"], "stage51-relay-evidence-v1")
        self.assertEqual(document["build_id"], BUILD_ID)
        self.assertEqual(document["source_sha256"], SOURCE_SHA256)
        self.assertEqual(document["target_entry"], "003c6350")
        self.assertEqual(document["candidate_start"], "0068ce00")
        self.assertEqual(document["candidate_length"], 0x80)
        self.assertIsInstance(document["candidate_all_zero"], bool)
        self.assertIsInstance(document["candidate_in_text"], bool)
        self.assertIsInstance(document["candidate_function"], type(None))
        self.assertIsInstance(document["incoming_control_flow_references"], list)
        self.assertIsInstance(document["overlapping_relays"], list)
        self.assertEqual(document["entry_branch"]["mnemonic"], "B")
        self.assertEqual(document["entry_branch"]["target"], "0068ce00")
        self.assertTrue(document["entry_branch"]["reachable"])
        self.assertTrue(document["conclusion"]["authorized"])
        self.assertTrue(document["ready_for_relay"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)

    def test_evidence_file_matches_direct_nro_identity_and_typed_guards(self):
        nro = NRO_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(nro).hexdigest(), SOURCE_SHA256)
        self.assertEqual(nro[0x40:0x54].hex().upper(), BUILD_ID)
        self.assertEqual(
            nro[0x3C6350:0x3C6360].hex().upper(),
            "FF0304D1E84B00FDFD7B0AA9FD830291",
        )
        self.assertEqual(len(nro[0x68CE00:0x68CE80]), 0x80)
        self.assertEqual(nro[0x68CE00:0x68CE80], bytes(0x80))
        document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        validate_schema(document)
        self.assertEqual(document["build_id"], nro[0x40:0x54].hex().upper())
        self.assertEqual(document["target_guard"], nro[0x3C6350:0x3C6360].hex().upper())
        self.assertEqual(document["candidate_bytes"], nro[0x68CE00:0x68CE80].hex().upper())
        self.assertEqual(document["candidate_length"], len(bytes.fromhex(document["candidate_bytes"])))
        self.assertIsInstance(document["candidate_all_zero"], bool)
        self.assertIsInstance(document["candidate_in_text"], bool)
        self.assertIsNone(document["candidate_function"])
        self.assertIsInstance(document["incoming_control_flow_references"], list)
        self.assertIsInstance(document["overlapping_relays"], list)
        self.assertTrue(document["candidate_all_zero"])
        self.assertTrue(document["candidate_in_text"])
        self.assertTrue(document["ready_for_relay"])
        self.assertFalse(document["runtime_binding_authorized"])

    def test_export_validation_rejects_tool_and_json_failures(self):
        success = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "missing.json"
            with self.assertRaises(AssertionError):
                validate_export(subprocess.CompletedProcess([], 1, "", "failed"), output)
            with self.assertRaises(AssertionError):
                validate_export(
                    subprocess.CompletedProcess([], 0, "REPORT SCRIPT ERROR: bad", ""), output
                )
            with self.assertRaises(AssertionError):
                validate_export(success, output)
            output.write_text("not-json", encoding="utf-8")
            with self.assertRaises(AssertionError):
                validate_export(success, output)

        valid = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        validate_schema(valid)
        for field in ("candidate_all_zero", "candidate_in_text"):
            invalid = json.loads(json.dumps(valid))
            invalid[field] = "true"
            with self.assertRaises(AssertionError):
                validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["incoming_control_flow_references"] = {}
        with self.assertRaises(AssertionError):
            validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["entry_branch"] = []
        with self.assertRaises(AssertionError):
            validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["conclusion"] = []
        with self.assertRaises(AssertionError):
            validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["text_block"] = []
        with self.assertRaises(AssertionError):
            validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["function_ownership"] = "unowned"
        with self.assertRaises(AssertionError):
            validate_schema(invalid)
        invalid = json.loads(json.dumps(valid))
        invalid["relay_overlap"] = []
        with self.assertRaises(AssertionError):
            validate_schema(invalid)


if __name__ == "__main__":
    unittest.main()
