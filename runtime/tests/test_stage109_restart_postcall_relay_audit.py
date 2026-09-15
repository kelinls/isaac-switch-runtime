import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage109_restart_postcall_relay_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage109RestartPostcallRelayAuditTests(unittest.TestCase):
    def test_audit_locks_all_real_game_update_restart_postcall_paths(self):
        self.assertTrue(SCRIPT.is_file(), "Stage109 raw-NRO restart auditor must exist")
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        # 仓库既有惯例见 `test_stage84_instant_restart_switch_gate` / `test_stage95_*`。
        if NRO is None:
            self.skipTest("fixed Repentance.nro input is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "restart-postcall-relay.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage109-restart-postcall-relay-audit-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["restart_target"]["entry"], "0x3fa458")
        self.assertEqual(document["restart_target"]["plt_thunk"], "0x6717c0")
        self.assertEqual([item["callsite"] for item in document["post_call_paths"]],
                         ["0x351c50", "0x351d20", "0x351ecc"])
        self.assertTrue(all(item["calls_restart_plt"] for item in document["post_call_paths"]))
        self.assertTrue(all(item["cleanup_call_follows"] for item in document["post_call_paths"]))
        self.assertTrue(document["relay_layout"]["zero_filled"])
        self.assertTrue(document["relay_layout"]["does_not_overlap_active_relays"])
        self.assertFalse(document["runtime_hook_authorized"])
        self.assertEqual(document["next_step"], "design_one_shot_read_only_restart_return_diagnostic")


if __name__ == "__main__":
    unittest.main()
