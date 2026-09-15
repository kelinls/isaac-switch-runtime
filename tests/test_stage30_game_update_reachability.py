import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "analysis/stage30-game-update-reachability"
    / "reachability-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)


class Stage30GameUpdateReachabilityTests(unittest.TestCase):
    def test_fixed_evidence_records_all_game_update_calls_and_callsite_predecessors(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

        self.assertEqual(evidence["schema_version"], "stage30-game-update-reachability-v1")
        self.assertEqual(evidence["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            evidence["source_sha256"],
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        self.assertEqual(evidence["game_update_plt_offset"], "0067c9e0")
        self.assertEqual(evidence["manager_update_callsite"], "003f905c")
        self.assertEqual(len(evidence["game_update_calls"]), 1)
        self.assertEqual(evidence["game_update_calls"][0]["call_address"], "003f905c")
        self.assertEqual(
            evidence["dispatch_states"],
            {
                "1": "003f8f5c",
                "2": "003f9030",
                "3": "003f9064",
                "4": "003f90d8",
                "5": "003f9078",
            },
        )
        self.assertGreaterEqual(len(evidence["predecessor_blocks"]), 4)
        self.assertIn("003f9058: ldr x0,[x22]", evidence["callsite_instructions"])
        self.assertIn("003f905c: bl 0x0067c9e0", evidence["callsite_instructions"])
        self.assertFalse(evidence["runtime_binding_authorized"])

    def test_exporter_is_read_only_and_uses_cfg_and_call_references(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage30GameUpdateReachability.java"
        self.assertTrue(script.exists(), "Stage 30 reachability exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "BasicBlockModel",
            "getSources",
            "getDestinations",
            "getReferencesTo",
            "0067c9e0",
            "003f905c",
            "DISPATCH_TABLE",
            "dispatch_states",
            "predecessor_blocks",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
