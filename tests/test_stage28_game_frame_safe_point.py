import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "analysis/stage28-game-frame-safe-point"
    / "safe-point-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
EXPECTED_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
RESERVED_INTERVALS = (
    range(0x68CBE0, 0x68CC00),
    range(0x68CC20, 0x68CC40),
    range(0x68CC40, 0x68CC80),
)


class Stage28GameFrameSafePointTests(unittest.TestCase):
    def test_fixed_evidence_locks_game_update_safe_point_and_new_relay_cave(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

        self.assertEqual(evidence["schema_version"], "stage28-game-frame-safe-point-v1")
        self.assertEqual(evidence["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(evidence["source_sha256"], EXPECTED_SHA256)
        self.assertEqual(evidence["safe_point"]["ldr_offset"], "003f9058")
        self.assertEqual(evidence["safe_point"]["ldr_instruction"], "LDR x0,[x22]")
        self.assertEqual(evidence["safe_point"]["bl_offset"], "003f905c")
        self.assertEqual(evidence["safe_point"]["bl_target"], "0067c9e0")
        self.assertEqual(evidence["safe_point"]["post_call_instruction"], "B 0x003f909c")

        cave = int(evidence["relay_cave"]["offset"], 16)
        self.assertEqual(cave, 0x68CC80)
        self.assertEqual(evidence["relay_cave"]["length"], 64)
        self.assertEqual(evidence["relay_cave"]["initial_bytes"], "00" * 64)
        self.assertEqual(evidence["relay_cave"]["function_containing"], None)
        self.assertEqual(evidence["relay_cave"]["incoming_flow_reference_count"], 0)
        self.assertFalse(any(cave in interval for interval in RESERVED_INTERVALS))
        self.assertTrue(evidence["ready_for_relay"])

    def test_exporter_stays_read_only_and_checks_new_cave(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage28GameFrameSafePoint.java"
        self.assertTrue(script.exists(), "Stage 28 safe-point exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "003f9058",
            "003f905c",
            "0067c9e0",
            "0068cc80",
            "incoming_flow_reference_count",
            "ready_for_relay",
            "getFunctionContaining",
            "getReferencesTo",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
