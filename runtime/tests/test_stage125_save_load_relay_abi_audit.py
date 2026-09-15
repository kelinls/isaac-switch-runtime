import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage125SaveLoadRelayAbiAuditTests(unittest.TestCase):
    def test_conservative_aapcs_contract_is_version_locked_and_not_authorized(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage125_save_load_relay_abi_audit import export

        document = export(NRO)
        self.assertEqual(document["postcall_contract"]["save"]["resume"], "0x3fa7b8")
        self.assertEqual(document["postcall_contract"]["load"]["resume"], "0x3fab64")
        self.assertEqual(document["postcall_contract"]["load"]["resume_instruction"], "F703002A")
        self.assertEqual(document["relay_layout"]["callback_slot"], "0x68cf90")
        self.assertTrue(document["relay_layout"]["regions_do_not_overlap"])
        self.assertTrue(document["aapcs_contract"]["stack_alignment"].startswith("sp remains 16"))
        self.assertIn("w0", document["aapcs_contract"]["load_restores"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
