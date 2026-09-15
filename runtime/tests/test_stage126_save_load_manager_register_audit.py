import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage126SaveLoadManagerRegisterAuditTests(unittest.TestCase):
    def test_manager_registers_are_distinct_from_gamestate_receiver(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage126_save_load_manager_register_audit import export
        document = export(NRO)
        self.assertEqual(document["save"]["manager_register"], "x19")
        self.assertEqual(document["save"]["manager_type"], "IsaacRepentance::Manager*")
        self.assertTrue(document["save"]["not_save_data_manager"])
        self.assertEqual(document["save"]["call_receiver_register"], "x20")
        self.assertEqual(document["load"]["manager_register"], "x20")
        self.assertEqual(document["load"]["manager_type"], "IsaacRepentance::Manager*")
        self.assertTrue(document["load"]["not_save_data_manager"])
        self.assertEqual(document["load"]["call_receiver_register"], "x19")
        self.assertTrue(document["save"]["postcall_can_reuse_manager_register"])
        self.assertTrue(document["load"]["postcall_can_reuse_manager_register"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
