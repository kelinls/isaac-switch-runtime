import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage106_game_start_dispatch_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage106GameStartDispatchAuditTests(unittest.TestCase):
    def test_audit_identifies_the_shared_game_start_dispatch_calls_but_does_not_authorize_a_hook(self):
        self.assertTrue(SCRIPT.is_file(), "Stage106 Game start dispatch auditor must exist")
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        # 仓库既有惯例见 `test_stage84_instant_restart_switch_gate` / `test_stage95_*`。
        if NRO is None:
            self.skipTest("fixed Repentance.nro input is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-start-dispatch.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage106-game-start-dispatch-audit-v1")
        self.assertEqual(document["dispatch"]["symbol"], "Manager::execute_start_game")
        self.assertEqual(document["dispatch"]["entry"], "0x3f9138")
        self.assertEqual(
            {call["event"] for call in document["game_start_calls"]}, {"new_game", "saved_game"},
        )
        self.assertEqual(
            {call["callsite"] for call in document["game_start_calls"]}, {"0x3f92a4", "0x3f93f8"},
        )
        self.assertTrue(all(call["receiver_provenance"] for call in document["game_start_calls"]))
        self.assertFalse(document["runtime_hook_authorized"])
        self.assertEqual(document["next_step"], "audit_post_call_control_flow_and_relay_cave_options")


if __name__ == "__main__":
    unittest.main()
