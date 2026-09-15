import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage107_game_start_postcall_relay_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage107GameStartPostcallRelayAuditTests(unittest.TestCase):
    def test_audit_proves_two_postcall_paths_can_share_an_isolated_diagnostic_cave(self):
        self.assertTrue(SCRIPT.is_file(), "Stage107 post-call relay auditor must exist")
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        # 仓库既有惯例见 `test_stage84_instant_restart_switch_gate` / `test_stage95_*`。
        if NRO is None:
            self.skipTest("fixed Repentance.nro input is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-start-postcall-relay.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage107-game-start-postcall-relay-audit-v1")
        self.assertEqual(document["relay_cave"]["offset"], "0x68cea0")
        self.assertEqual(document["relay_cave"]["length"], "0x100")
        self.assertTrue(document["relay_cave"]["zero_filled"])
        self.assertTrue(document["relay_cave"]["does_not_overlap_existing_relays"])
        self.assertEqual({item["event"] for item in document["post_call_paths"]}, {"new_game", "saved_game"})
        self.assertTrue(all(item["resume_target"] for item in document["post_call_paths"]))
        self.assertTrue(all(item["branch_reaches_cave"] for item in document["post_call_paths"]))
        self.assertFalse(document["runtime_hook_authorized"])
        self.assertEqual(document["next_step"], "design_one_shot_read_only_lifecycle_diagnostic")


if __name__ == "__main__":
    unittest.main()
