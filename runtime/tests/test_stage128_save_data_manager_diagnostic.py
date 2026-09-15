import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage128SaveDataManagerDiagnosticTests(unittest.TestCase):
    def _runtime_array(self, constants: str, name: str) -> bytes:
        match = re.search(
            rf"{name} = \{{(?P<body>.*?)\n\}};",
            constants,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, name)
        return bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", match.group("body")))

    def test_relays_preserve_the_original_file_calls_and_are_bounded(self):
        from tools.stage128_save_data_manager_observation_relay import (
            LOAD_RELAY_CODE,
            RELAY_LENGTH,
            SAVE_RELAY_CODE,
            build_ips,
        )

        self.assertEqual(len(SAVE_RELAY_CODE), RELAY_LENGTH)
        self.assertEqual(len(LOAD_RELAY_CODE), RELAY_LENGTH)
        # The callback happens before the original BL.  x0/x1/x30 are restored
        # before it, so the game's OpenSaveFile receiver and filename survive.
        for relay in (SAVE_RELAY_CODE, LOAD_RELAY_CODE):
            self.assertIn(bytes.fromhex("E00700A9"), relay)
            self.assertIn(bytes.fromhex("E00740A9"), relay)
            self.assertIn(bytes.fromhex("FE0B40F9"), relay)
            self.assertIn(bytes.fromhex("00023FD6"), relay)
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        self.assertGreater(len(build_ips(NRO.read_bytes())), 16)

    def test_runtime_guards_match_generated_payloads_byte_for_byte(self):
        from tools.stage128_save_data_manager_observation_relay import (
            LOAD_RELAY_CODE,
            SAVE_RELAY_CODE,
        )

        constants = (ROOT / "runtime/source/runtime_constants.hpp").read_text()
        self.assertEqual(
            self._runtime_array(constants, "kStage128SaveDataManagerRelaySaveExpectedBytes"),
            SAVE_RELAY_CODE,
        )
        self.assertEqual(
            self._runtime_array(constants, "kStage128SaveDataManagerRelayLoadExpectedBytes"),
            LOAD_RELAY_CODE,
        )

    def test_stage128_is_a_version_locked_read_only_runtime_diagnostic(self):
        makefile = (ROOT / "runtime/Makefile").read_text()
        selector = (ROOT / "runtime/source/program/runtime_entry.cpp").read_text()
        hook = (ROOT / "runtime/source/hook_manager.cpp").read_text()
        constants = (ROOT / "runtime/source/runtime_constants.hpp").read_text()

        self.assertIn("128", makefile)
        self.assertIn("stage128_save_data_manager_observation_relay.py", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 128", selector)
        self.assertIn("TryInstallStage128SaveDataManagerDiagnostic", hook)
        self.assertIn("VerifyStage128SaveDataManagerRelay", hook)
        self.assertIn("saveDataManager == nullptr", hook)
        self.assertIn("kStage128SaveDataManagerRelaySaveExpectedBytes", constants)
        self.assertIn("kStage128SaveDataManagerRelayLoadExpectedBytes", constants)

    def test_stage128_callback_does_not_access_manager_or_file_apis(self):
        hook = (ROOT / "runtime/source/hook_manager.cpp").read_text()
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128")
        stage = hook[start:hook.index("#endif", start)]
        self.assertIn("flags == 3u", stage)
        self.assertNotIn("Open", stage)
        self.assertNotIn("Write", stage)
        self.assertNotIn("Read", stage)
        self.assertNotIn("LuaRuntime", stage)


if __name__ == "__main__":
    unittest.main()
