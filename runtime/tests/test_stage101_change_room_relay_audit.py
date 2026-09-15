import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage101_change_room_relay_audit.py"
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)


class Stage101ChangeRoomRelayAuditTests(unittest.TestCase):
    def test_audit_proves_a_post_level_change_room_read_only_relay_contract(self):
        self.assertTrue(SCRIPT.is_file(), "Stage101 relay auditor must exist")
        # 用户提供的固定 NRO 不在仓库里 ⇒ 缺它就**跳过**，而不是硬失败。
        if not NRO.is_file():
            self.skipTest("fixed Repentance.nro input is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "change-room-relay-audit.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)

        self.assertEqual(document["schema_version"], "stage101-change-room-post-call-relay-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            document["source_sha256"],
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        self.assertEqual(document["callsite"]["offset"], "0x354050")
        self.assertEqual(document["callsite"]["original_instruction"], "FC990C94")
        self.assertEqual(document["callsite"]["callee"], "Level::ChangeRoom")
        self.assertEqual(document["post_call"]["next_instruction"], "mov x0, x19")
        self.assertTrue(document["post_call"]["game_receiver_restored"])
        self.assertTrue(document["post_call"]["tail_calls_level_update"])
        self.assertEqual(document["relay"]["cave_offset"], "0x68ce60")
        self.assertEqual(document["relay"]["slot_offset"], "0x68ce90")
        self.assertTrue(document["relay"]["cave_is_zero_filled"])
        self.assertTrue(document["relay"]["does_not_overlap_existing_relays"])
        self.assertEqual(document["relay"]["callback_abi"], "void(Game*)")
        self.assertTrue(document["relay"]["preserves_original_level_call"])
        self.assertTrue(document["relay"]["returns_to_original_post_call"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(document["next_step"], "build_read_only_diagnostic_only")


if __name__ == "__main__":
    unittest.main()
