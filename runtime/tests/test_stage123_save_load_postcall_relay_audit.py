import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage123SaveLoadPostcallRelayAuditTests(unittest.TestCase):
    def test_manager_save_and_load_have_distinct_postcall_observation_contracts(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage123_save_load_postcall_relay_audit import export

        document = export(NRO)

        self.assertEqual(document["save"]["manager_entry"], "003fa658")
        self.assertEqual(document["save"]["call_address"], "003fa7b4")
        self.assertEqual(document["save"]["postcall"], "003fa7b8")
        self.assertEqual(document["load"]["manager_entry"], "003faaf4")
        self.assertEqual(document["load"]["call_address"], "003fab60")
        self.assertEqual(document["load"]["postcall"], "003fab64")
        self.assertEqual(document["load"]["return_value_use"], "w0 copied to w23")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("no_version_locked_unused_relay_cave_selected", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
