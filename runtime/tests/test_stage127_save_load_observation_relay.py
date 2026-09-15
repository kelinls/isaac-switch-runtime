import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage127SaveLoadObservationRelayTests(unittest.TestCase):
    def test_relays_are_bounded_and_preserve_load_w0(self):
        from tools.stage127_save_load_observation_relay import (
            LOAD_RELAY_CODE, SAVE_RELAY_CODE, RELAY_LENGTH, build_ips,
        )
        self.assertEqual(len(SAVE_RELAY_CODE), RELAY_LENGTH)
        self.assertEqual(len(LOAD_RELAY_CODE), RELAY_LENGTH)
        self.assertIn(bytes.fromhex("E01F00B9"), LOAD_RELAY_CODE)
        self.assertIn(bytes.fromhex("E01F40B9"), LOAD_RELAY_CODE)
        self.assertIn(bytes.fromhex("E00314AA"), LOAD_RELAY_CODE)
        self.assertNotIn(bytes.fromhex("E01F00B9"), SAVE_RELAY_CODE)
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        self.assertGreater(len(build_ips(NRO.read_bytes())), 16)


if __name__ == "__main__":
    unittest.main()
