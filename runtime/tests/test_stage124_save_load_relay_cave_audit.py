import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage124SaveLoadRelayCaveAuditTests(unittest.TestCase):
    def test_save_load_postcall_relays_have_one_mutually_exclusive_version_locked_cave(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage124_save_load_relay_cave_audit import export

        document = export(NRO)

        self.assertTrue(document["relay_cave"]["zero_filled"])
        self.assertEqual(document["relay_cave"]["offset"], "0x68cea0")
        self.assertEqual(document["relay_cave"]["length"], "0x100")
        self.assertTrue(document["relay_cave"]["mutually_exclusive_with_stage108_109"])
        self.assertEqual(document["save"]["call_address"], "003fa7b4")
        self.assertEqual(document["save"]["resume_target"], "003fa7b8")
        self.assertEqual(document["load"]["call_address"], "003fab60")
        self.assertEqual(document["load"]["resume_target"], "003fab64")
        self.assertEqual(document["load"]["must_preserve_register"], "w0")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
