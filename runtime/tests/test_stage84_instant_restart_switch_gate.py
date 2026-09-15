import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage84_instant_restart_switch_gate.py"
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)


class Stage84InstantRestartSwitchGateTests(unittest.TestCase):
    def test_fixed_nro_records_the_unresolved_input_and_ascent_gates(self):
        self.assertTrue(SCRIPT.is_file(), "Stage84 Switch gate exporter must exist")
        if not NRO.is_file():
            self.skipTest("fixed Repentance.nro input is unavailable")
        self.assertEqual(
            hashlib.sha256(NRO.read_bytes()).hexdigest(),
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "instant-restart-switch-gate.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage84-instant-restart-switch-gate-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["native_entries"]["manager_is_action_triggered"]["entry"], "003f9b7c")
        self.assertEqual(document["native_entries"]["console_run_command"]["entry"], "0003d740")
        self.assertEqual(document["native_entries"]["manager_restart_game"]["entry"], "003fa458")
        self.assertEqual(document["lua_input_callback_abi"], "not_present_in_switch_nro")
        self.assertEqual(document["action_restart_reachability"], "not_proven_by_static_nro")
        self.assertEqual(document["level_is_ascent"], "no_exact_dynamic_symbol")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
