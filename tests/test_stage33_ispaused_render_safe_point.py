import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "analysis/stage33-ispaused-render-safe-point"
    / "safe-point-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)


class Stage33IsPausedRenderSafePointTests(unittest.TestCase):
    def test_fixed_evidence_locks_render_ispaused_context_and_empty_cave(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(evidence["schema_version"], "stage33-ispaused-render-safe-point-v1")
        self.assertEqual(evidence["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(evidence["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(evidence["safe_point"]["context_offset"], "00342998")
        self.assertEqual(evidence["safe_point"]["call_offset"], "003429a4")
        self.assertEqual(evidence["safe_point"]["call_target"], "00671100")
        self.assertEqual(evidence["safe_point"]["post_call_instruction"], "TBZ w0,#0x0,0x003429FC")
        self.assertEqual(evidence["relay_cave"]["offset"], "0068cd00")
        self.assertEqual(evidence["relay_cave"]["initial_bytes"], "00" * 64)
        self.assertIsNone(evidence["relay_cave"]["function_containing"])
        self.assertEqual(evidence["relay_cave"]["incoming_flow_reference_count"], 0)
        self.assertTrue(evidence["ready_for_relay"])

    def test_exporter_is_read_only_and_locks_original_call(self):
        source = (ROOT / "analysis/ghidra/scripts/ExportStage33IsPausedRenderSafePoint.java").read_text(encoding="utf-8")
        for value in ("00342998", "003429a4", "00671100", "0068cd00", "getFunctionContaining", "getReferencesTo"):
            self.assertIn(value, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
