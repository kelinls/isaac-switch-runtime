import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage95_challenge_compatibility_gate.py"
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)
CHALLENGES = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/resources/challenges.xml"
)


class Stage95ChallengeCompatibilityGateTests(unittest.TestCase):
    def test_fixed_nro_and_switch_resources_prove_the_shared_challenge_route(self):
        self.assertTrue(SCRIPT.is_file(), "Stage95 challenge gate exporter must exist")
        if not NRO.is_file() or not CHALLENGES.is_file():
            self.skipTest("fixed Switch NRO or challenges.xml input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "challenge-compatibility-gate.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(CHALLENGES), str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage95-challenge-compatibility-gate-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["challenge_resource"]["format"], "challenges_xml_v1")
        self.assertEqual(document["challenge_resource"]["entry_count"], 35)
        self.assertEqual(document["challenge_resource"]["name_to_id"]["Pitch Black"], 1)
        self.assertEqual(document["challenge_resource"]["name_to_id"]["PONG"], 35)
        self.assertNotIn("Ludovico is everywhere", document["challenge_resource"]["name_to_id"])
        adapter = document["native_state_adapter"]["game_get_challenge_params"]
        self.assertEqual(adapter["entry"], "0034ea68")
        self.assertEqual(adapter["current_challenge_enum"]["offset"], "0026fa88")
        self.assertEqual(
            document["native_state_adapter"]["manager_get_challenge_params"]["abi"],
            "Manager_x0_eChallenge_w1_returns_params_pointer_x0",
        )
        route = document["compatibility_route"]
        self.assertFalse(route["per_getter_address_hunt_required"])
        self.assertEqual(route["shared_native_bridge_count"], 1)
        self.assertFalse(route["runtime_binding_authorized"])
        self.assertEqual(route["blocked_reason"], "candidate_custom_challenge_name_is_not_in_base_challenges_xml")


if __name__ == "__main__":
    unittest.main()
