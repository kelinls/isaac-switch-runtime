import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage91ExecuteCommandAbiAudit.java"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PROJECT = ROOT / "analysis/ghidra"


class Stage91ExecuteCommandAbiAuditTests(unittest.TestCase):
    def test_audit_refuses_runtime_binding_without_the_full_string_and_receiver_abi(self):
        self.assertTrue(SCRIPT.is_file(), "Stage91 ExecuteCommand ABI exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "execute-command-abi-audit.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-switch", "-process", "Repentance.nro", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage91-execute-command-abi-audit-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["console_run_command"]["entry"], "0003d740")
        self.assertEqual(document["console_run_command"]["entry_guard"],
                         "E80F19FCFD7B01A9FD430091FC6F02A9")
        self.assertTrue(document["console_run_command"]["entry_context"])
        self.assertTrue(document["console_run_command"]["string_argument_evidence"])
        self.assertTrue(document["console_run_command"]["return_path_evidence"])
        self.assertTrue(document["receiver_paths"])
        self.assertTrue(all(path["call_context"] and path["receiver_setup"]
                            for path in document["receiver_paths"]))
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(set(document["blocked_reasons"]), {
            "pc_lua_stack_and_return_marshalling_not_proven",
            "switch_console_owner_not_proven_from_runtime_safe_state",
            "switch_return_string_lifetime_not_proven",
        })


if __name__ == "__main__":
    unittest.main()
