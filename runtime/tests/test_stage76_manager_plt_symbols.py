import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/stage76_manager_plt_symbols.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage76ManagerPltSymbolsTests(unittest.TestCase):
    def test_raw_nro_mapping_resolves_all_manager_state_thunks(self):
        self.assertTrue(TOOL.is_file(), "Stage76 raw-NRO PLT resolver must exist")
        if NRO is None:
            self.skipTest("the managed UPD Repentance.nro input is unavailable")
        from tools.stage76_manager_plt_symbols import export
        document = export(NRO)
        self.assertEqual(document["schema_version"], "stage76-manager-plt-symbols-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(len(document["manager_call_targets"]), 18)
        self.assertTrue(all(item["symbol"] for item in document["manager_call_targets"]))
        self.assertEqual(document["status"], "game_edges_found")
        self.assertEqual(document["game_edges"], [{"caller": "SaveGameState", "call_address": "003fa78c", "thunk": "0067aa20", "got_slot": "00aa3478", "symbol": "_ZN15IsaacRepentance4Game9SaveStateERNS_9GameStateE"}])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
