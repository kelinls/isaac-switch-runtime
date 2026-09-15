import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "analysis/stage32-ispaused-callsite-reachability"
    / "callsites-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
CALLS = {
    "000342a8",
    "0005c7ac",
    "0005c82c",
    "000619b0",
    "003429a4",
    "003b389c",
    "0046ada0",
    "0046f4b0",
    "0047e4e4",
    "0047e500",
    "004826ec",
    "00482a40",
    "004a3db8",
}


class Stage32IsPausedCallsiteReachabilityTests(unittest.TestCase):
    def test_fixed_evidence_records_every_ispaused_callsite_conservatively(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

        self.assertEqual(evidence["schema_version"], "stage32-ispaused-callsite-reachability-v1")
        self.assertEqual(evidence["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            evidence["source_sha256"],
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        self.assertEqual(evidence["game_is_paused_plt_offset"], "00671100")

        callsites = evidence["callsites"]
        self.assertEqual({item["call_offset"] for item in callsites}, CALLS)
        self.assertEqual(len(callsites), len(CALLS))
        for item in callsites:
            self.assertEqual(item["call_instruction"], "BL 0x00671100")
            self.assertIn(item["x0_definition_status"], {"observed", "unproven"})
            self.assertIsInstance(item["context_before"], list)
            self.assertIsInstance(item["context_after"], list)
            self.assertIsInstance(item["basic_block"]["predecessors"], list)
            self.assertIsInstance(item["basic_block"]["successors"], list)
            self.assertIsInstance(item["direct_callers"], list)

        candidate = evidence["observer_candidate"]
        self.assertEqual(candidate["status"], "selected")
        self.assertEqual(candidate["call_offset"], "003429a4")
        selected = next(item for item in callsites if item["call_offset"] == candidate["call_offset"])
        self.assertEqual(selected["x0_definition_status"], "observed")
        self.assertIn("ldr x0,[x8]", selected["x0_definition"])
        self.assertFalse(evidence["runtime_binding_authorized"])

    def test_exporter_is_read_only_and_tracks_cfg_x0_and_direct_callers(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage32IsPausedCallsiteReachability.java"
        self.assertTrue(script.exists(), "Stage 32 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "00671100",
            "000342a8",
            "004a3db8",
            "BasicBlockModel",
            "getSources",
            "getDestinations",
            "getReferencesTo",
            "x0_definition_status",
            "observer_candidate",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
