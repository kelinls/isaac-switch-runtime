import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "analysis/stage31-game-state2-safe-point"
    / "safe-point-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)


class Stage31GameState2SafePointTests(unittest.TestCase):
    def test_fixed_evidence_locks_state2_call_and_unused_relay_cave(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

        self.assertEqual(evidence["schema_version"], "stage31-game-state2-safe-point-v1")
        self.assertEqual(evidence["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            evidence["source_sha256"],
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        self.assertEqual(evidence["safe_point"]["context_offset"], "003f9038")
        self.assertEqual(evidence["safe_point"]["context_instruction"], "LDR x0,[x22]")
        self.assertEqual(evidence["safe_point"]["call_offset"], "003f903c")
        self.assertEqual(evidence["safe_point"]["call_target"], "0067c9c0")
        self.assertEqual(evidence["safe_point"]["post_call_instruction"], "LDRB w8,[x21]")
        self.assertEqual(evidence["relay_cave"]["offset"], "0068ccc0")
        self.assertEqual(evidence["relay_cave"]["length"], 64)
        self.assertEqual(evidence["relay_cave"]["initial_bytes"], "00" * 64)
        self.assertIsNone(evidence["relay_cave"]["function_containing"])
        self.assertEqual(evidence["relay_cave"]["incoming_flow_reference_count"], 0)
        self.assertTrue(evidence["ready_for_relay"])

    def test_exporter_is_read_only_and_verifies_state2_call_context_and_cave(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage31GameState2SafePoint.java"
        self.assertTrue(script.exists(), "Stage 31 safe-point exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "003f9038",
            "003f903c",
            "0067c9c0",
            "0068ccc0",
            "getFunctionContaining",
            "getReferencesTo",
            "ready_for_relay",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
