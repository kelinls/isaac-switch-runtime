import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage66TransitionCalleeReceiverAudit.java"
PLT_TOOL = ROOT / "tools/stage66_transition_plt_targets.py"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage66TransitionCalleeReceiverAuditTests(unittest.TestCase):
    def test_exporter_audits_transition_callees_without_authorizing_runtime(self):
        self.assertTrue(SCRIPT.is_file(), "Stage66 callee receiver audit exporter must exist")
        self.assertTrue(PLT_TOOL.is_file(), "Stage66 PLT target exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transition-callee-audit.json"
            plt_targets = Path(directory) / "transition-plt-targets.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            nro_candidates = list(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"))
            if not nro_candidates:
                self.skipTest("the managed UPD Repentance.nro input is unavailable")
            plt_result = subprocess.run(
                ["python3", "-m", "tools.stage66_transition_plt_targets", str(nro_candidates[0]), str(plt_targets)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            self.assertEqual(plt_result.returncode, 0, plt_result.stdout + plt_result.stderr)
            result = subprocess.run([str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name,
                str(plt_targets), str(output)], cwd=ROOT, text=True, capture_output=True, env=environment)
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "stage66-transition-callee-receiver-audit-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual({item["entry"] for item in document["transition_functions"]}, {"00353fe0", "003d9aac"})
        self.assertTrue(document["calls"])
        for call in document["calls"]:
            self.assertTrue(call["call_address"])
            self.assertTrue(call["target_symbol"])
            self.assertTrue(call["x0_provenance"])
            self.assertIn(call["x0_verdict"], {"entry_receiver_preserved", "non_receiver_or_unproven"})
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
