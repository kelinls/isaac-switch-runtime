import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage64TransitionGameReceiverPropagation.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage64TransitionGameReceiverPropagationTests(unittest.TestCase):
    def test_exporter_reports_version_locked_transition_receivers(self):
        self.assertTrue(SCRIPT.is_file(), "Stage64 transition receiver exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transition-receivers.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage64-transition-game-receiver-propagation-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(
            {target["entry"] for target in document["transition_targets"]},
            {"003d9aac", "003d6cb0"},
        )
        self.assertEqual(len(document["transition_target_observations"]), 2)
        self.assertEqual(
            {item["entry"] for item in document["transition_target_observations"]},
            {"003d9aac", "003d6cb0"},
        )
        for target in document["transition_target_observations"]:
            self.assertGreaterEqual(target["incoming_reference_count"], 0)
            self.assertGreaterEqual(target["direct_callsite_count"], 0)
        for callsite in document["transition_callsites"]:
            self.assertTrue(callsite["caller_entry"])
            self.assertTrue(callsite["call_address"])
            self.assertTrue(callsite["receiver_provenance"])
            self.assertIn(callsite["receiver_verdict"], {
                "authenticated_game_embedded_level", "unproven_transition_receiver",
                "indirect_or_non_call_reference",
            })
        authenticated = [
            callsite for callsite in document["transition_callsites"]
            if callsite["receiver_verdict"] == "authenticated_game_embedded_level"
        ]
        self.assertEqual(len(authenticated), 1)
        self.assertEqual(authenticated[0]["caller_entry"], "00353fe0")
        self.assertEqual(authenticated[0]["transition_entry"], "003d9aac")
        self.assertIn(document["status"], {
            "authenticated_transition_game_receiver", "blocked_no_authenticated_transition_game_receiver",
            "blocked_no_direct_transition_callgraph_edge",
        })
        if document["status"] != "authenticated_transition_game_receiver":
            self.assertIn(document["blocked_reason"], {
                "blocked_no_authenticated_transition_game_receiver",
                "blocked_no_direct_transition_callgraph_edge",
            })
        else:
            self.assertEqual(
                document["counter_field_status"],
                "unchanged_blocked_no_game_counter_increment_reset_independent_reader_closure",
            )
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
