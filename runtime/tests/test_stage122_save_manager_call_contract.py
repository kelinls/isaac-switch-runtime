import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage122SaveManagerCallContractTests(unittest.TestCase):
    def test_original_save_and_load_share_manager_global_but_runtime_binding_stays_blocked(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage122_save_manager_call_contract import export

        document = export(NRO)

        self.assertEqual(document["manager_global"]["slot"], "00aac940")
        self.assertEqual(document["save"]["entry"], "0036f3cc")
        self.assertEqual(document["load"]["entry"], "00379224")
        self.assertEqual(document["save"]["open_write"]["call_address"], "0036f474")
        self.assertEqual(document["load"]["open_read"]["call_address"], "00379294")
        self.assertIn("x1", document["save"]["filename_argument"])
        self.assertIn("x1", document["load"]["filename_argument"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("global_manager_lifecycle_not_runtime_verified", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
