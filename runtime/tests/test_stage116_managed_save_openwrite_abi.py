import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage116ManagedSaveOpenWriteAbiTests(unittest.TestCase):
    def test_original_save_code_proves_openwrite_return_and_virtual_write_receiver(self):
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage116_managed_save_openwrite_abi import export

        document = export(NRO)

        self.assertEqual(document["schema_version"], "stage116-managed-save-openwrite-abi-v1")
        self.assertEqual(document["open_write"]["return_register"], "x0")
        self.assertEqual(document["open_write"]["saved_to"], "GameState.local_stream_slot_[x20+0x0]")
        self.assertEqual(document["open_write"]["null_check"], "cbnz_x0_to_write_path")
        self.assertEqual(document["write_receiver"], "load_[x20+0x0]_into_x0")
        self.assertEqual(document["write"]["vtable_offset"], "00000040")
        self.assertEqual(document["finalize_receiver"], "x19_stream_object")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
