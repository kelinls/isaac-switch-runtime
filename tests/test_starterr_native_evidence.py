import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage49StarterrObjectFamilyEvidence.java"
PRE_GET_COLLECTIBLE_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage50PreGetCollectibleEvidence.java"
)
PC_BINDING_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportPcStarterrLuaBindingEvidence.java"
)
GETTER_SEMANTICS_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage52StarterrGetterSemantics.java"
)
OBJECT_FIELD_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage53StarterrObjectFieldEvidence.java"
)
GETTER_RECOVERY_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage54GetterRecoveryInventory.java"
)
LEVEL_STAGE_FLOW_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage55LevelStageFieldFlow.java"
)
ROOM_INIT_FLOW_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportStage56RoomInitReceiverFlow.java"
)
PC_ITEM_POOL_CONTRACT_SCRIPT = (
    ROOT / "analysis/ghidra/scripts/ExportPcItemPoolGetCollectibleContract.java"
)
PC_GHIDRA_PROJECT = Path("/tmp/isaac-pc-ghidra.DjKcoN")
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
PC_LUA_MAIN = ROOT / "The Binding of Isaac Rebirth pc/resources/scripts/main.lua"


class StarterrNativeEvidenceTests(unittest.TestCase):
    def test_dynamic_targets_are_version_locked(self):
        from tools.starterr_native_evidence import write_plt_targets

        output = ROOT / "analysis/starterr-native-evidence/test-output.json"
        self.addCleanup(output.unlink, missing_ok=True)
        document = write_plt_targets(NRO_PATH, output)

        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            document["targets"]["item_pool_get_collectible"]["entry"], 0x3C6350
        )
        self.assertEqual(document["targets"]["rng_next"]["entry"], 0x44E464)
        self.assertEqual(
            document["missing_pc_getters"]["game_get_room"],
            "_ZN15IsaacRepentance4Game7GetRoomEv",
        )

    def test_ghidra_exporter_records_game_member_lifecycle(self):
        self.assertTrue(SCRIPT.exists(), "Starterr object-family Ghidra exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "object-family.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage49-starterr-object-family-v1")
        self.assertEqual(set(document["members"]), {"room", "level", "item_pool"})
        level_callsite = document["members"]["level"]["constructor_callsites"][0]
        self.assertIsInstance(level_callsite, dict)
        self.assertEqual(level_callsite["lifecycle"], "game_constructor")
        self.assertFalse(document["runtime_binding_authorized"])

    def test_ghidra_exporter_records_pre_get_collectible_call_contracts(self):
        self.assertTrue(
            PRE_GET_COLLECTIBLE_SCRIPT.exists(),
            "MC_PRE_GET_COLLECTIBLE Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pre-get-collectible.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(PRE_GET_COLLECTIBLE_SCRIPT.parent),
                    "-postScript", PRE_GET_COLLECTIBLE_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage50-pre-get-collectible-evidence-v1")
        self.assertEqual(document["target_entry"], "003c6350")
        self.assertEqual(document["aapcs"], {
            "this_register": "x0",
            "item_pool_type_register": "w1",
            "seed_register": "w2",
            "no_decrease_register": "w3",
            "last_collectible_type_register": "w4",
            "return_register": "w0",
        })
        self.assertGreater(document["callsite_count"], 0)
        self.assertEqual(len(document["callsites"]), document["callsite_count"])
        for callsite in document["callsites"]:
            self.assertIsInstance(callsite, dict)
            self.assertIn("argument_context", callsite)
            self.assertIsInstance(callsite["argument_context"], list)
            self.assertIn("hook_candidate", callsite)
        self.assertEqual(document["target_entry_hook"], {
            "entry": "003c6350",
            "requires_original_trampoline": True,
            "requires_thread_local_reentry_guard": True,
        })
        self.assertEqual(document["recursive_callsite_count"], 1)
        self.assertEqual(document["recursive_callsites"][0]["call_address"], "003c6838")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("target_entry_relay_abi_unproven", document["blocked_reasons"])

    def test_pc_exporter_recovers_lua_registration_handlers(self):
        self.assertTrue(
            PC_BINDING_SCRIPT.exists(),
            "PC Lua binding Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        if not PC_GHIDRA_PROJECT.is_dir():
            self.skipTest("temporary PC Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-bindings.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(PC_GHIDRA_PROJECT), "isaac-pc", "-process",
                    "isaac-ng.exe", "-noanalysis", "-scriptPath", str(PC_BINDING_SCRIPT.parent),
                    "-postScript", PC_BINDING_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "pc-starterr-lua-binding-evidence-v2")
        self.assertEqual(
            document["source_sha256"],
            "04469d0c3d3581936fcf85bea5f9f4f3a65b2ccf96b36310456c9626bac36dc6",
        )
        expected_handlers = {
            "GetItemPool": "00427900",
            "GetLevel": "00420330",
            "GetRoom": "004023f0",
            "GetTreasureRoomVisitCount": "00705fd0",
            "GetStage": "004076f0",
            "GetType": "00402360",
        }
        for name, handler in expected_handlers.items():
            registrations = document["apis"][name]["registrations"]
            self.assertGreaterEqual(len(registrations), 1)
            self.assertTrue(
                any(item["handler_entry"] == handler for item in registrations),
                f"{name} must recover the PC Lua registration handler",
            )
            for item in registrations:
                self.assertIsInstance(item["handler_instruction_context"], list)
                self.assertGreater(len(item["handler_instruction_context"]), 0)

    def test_pc_exporter_records_get_collectible_hidden_argument_flow(self):
        self.assertTrue(
            PC_ITEM_POOL_CONTRACT_SCRIPT.exists(),
            "PC ItemPool contract Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        if not PC_GHIDRA_PROJECT.is_dir():
            self.skipTest("temporary PC Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-item-pool-contract.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(PC_GHIDRA_PROJECT), "isaac-pc", "-process",
                    "isaac-ng.exe", "-noanalysis", "-scriptPath", str(PC_ITEM_POOL_CONTRACT_SCRIPT.parent),
                    "-postScript", PC_ITEM_POOL_CONTRACT_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "pc-item-pool-get-collectible-contract-v1")
        self.assertEqual(document["handler_entry"], "00701230")
        self.assertGreater(len(document["handler_instructions"]), 0)
        self.assertGreater(len(document["call_contexts"]), 0)
        self.assertFalse(document["runtime_binding_authorized"])

    def test_pc_item_pool_wrapper_fixes_default_item_and_raw_argument_order(self):
        if not PC_LUA_MAIN.is_file():
            self.skipTest("PC resources/scripts/main.lua is unavailable")
        source = PC_LUA_MAIN.read_text(encoding="utf-8")
        self.assertIn(
            "return ItemPool_GetCollectible(self, poolType, seed or Random(), "
            "(decrease and 0) or 1, defaultItem or 0)",
            source,
        )

    def test_ghidra_exporter_distinguishes_switch_getter_candidates_from_bindings(self):
        self.assertTrue(
            GETTER_SEMANTICS_SCRIPT.exists(),
            "Starterr getter-semantics Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "getter-semantics.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(GETTER_SEMANTICS_SCRIPT.parent),
                    "-postScript", GETTER_SEMANTICS_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage52-starterr-getter-semantics-v1")
        self.assertEqual(document["level_stage_candidate"], {
            "entry": "003d0498",
            "entry_guard": "FD7BBEA9F30B00F9FD030091E9360090",
            "symbol": "_ZNK15IsaacRepentance5Level10GetStageIDEb",
            "got_slot": "00a9de98",
            "return_register": "w0",
        })
        self.assertEqual(
            document["room_type_status"],
            "blocked_no_authenticated_switch_accessor_or_field",
        )
        self.assertEqual(
            document["treasure_room_visit_count_status"],
            "blocked_no_authenticated_switch_accessor_or_field",
        )
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("level_stage_semantic_equivalence_unproven", document["blocked_reasons"])

    def test_ghidra_exporter_records_room_initialization_field_flow(self):
        self.assertTrue(
            OBJECT_FIELD_SCRIPT.exists(),
            "Starterr object-field Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "object-fields.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(OBJECT_FIELD_SCRIPT.parent),
                    "-postScript", OBJECT_FIELD_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage53-starterr-object-field-evidence-v1")
        self.assertEqual(document["room_init"], {
            "entry": "0045c020",
            "entry_guard": "EB2BB86DE923016DFD7B02A9FD830091",
            "symbol": "_ZN15IsaacRepentance4Room4InitEPKNS_10RoomConfig4RoomEPNS_14RoomDescriptorE",
        })
        self.assertEqual(document["level_current_room_descriptor"], {
            "entry": "003dc684",
            "entry_guard": "08AB82524800A0720800088B010140B9",
            "symbol": "_ZN15IsaacRepentance5Level18GetCurrentRoomDescEv",
        })
        self.assertEqual(
            document["room_type_status"],
            "blocked_room_init_does_not_authenticate_a_stable_room_type_accessor",
        )
        self.assertEqual(
            document["treasure_room_visit_count_status"],
            "blocked_no_authenticated_counter_data_flow",
        )
        self.assertFalse(document["runtime_binding_authorized"])

    def test_ghidra_exporter_inventories_getter_recovery_paths_without_authorizing_them(self):
        self.assertTrue(
            GETTER_RECOVERY_SCRIPT.exists(),
            "getter recovery inventory Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "getter-recovery.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(GETTER_RECOVERY_SCRIPT.parent),
                    "-postScript", GETTER_RECOVERY_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage54-getter-recovery-inventory-v1")
        self.assertEqual(document["exact_symbol_status"]["level_get_stage"], "absent")
        self.assertEqual(document["exact_symbol_status"]["room_get_type"], "absent")
        self.assertEqual(document["anchors"]["level_set_stage"]["symbol"], "SetStage")
        self.assertGreater(len(document["anchors"]["room_init"]["receiver_accesses"]), 0)
        self.assertFalse(document["runtime_binding_authorized"])

    def test_ghidra_exporter_records_level_stage_writer_and_reader_traces(self):
        self.assertTrue(
            LEVEL_STAGE_FLOW_SCRIPT.exists(),
            "Level stage field-flow Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "level-stage-flow.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(LEVEL_STAGE_FLOW_SCRIPT.parent),
                    "-postScript", LEVEL_STAGE_FLOW_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage55-level-stage-field-flow-v1")
        self.assertEqual(document["set_stage"]["entry"], "003e420c")
        self.assertEqual(document["get_stage_id"]["entry"], "003d0498")
        self.assertEqual(document["stage_field"], {
            "offset": 0,
            "width": "u32",
            "writer": "003e4278: stp w21,w20,[x19]",
            "reader": "003d0510: ldp w8,w1,[x0]",
        })
        self.assertTrue(document["runtime_binding_authorized"])

    def test_ghidra_exporter_preserves_room_init_machine_code_for_receiver_alias_tracing(self):
        self.assertTrue(
            ROOM_INIT_FLOW_SCRIPT.exists(),
            "Room Init receiver-flow Ghidra exporter must exist",
        )
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "room-init-flow.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(ROOM_INIT_FLOW_SCRIPT.parent),
                    "-postScript", ROOM_INIT_FLOW_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage56-room-init-receiver-flow-v1")
        self.assertEqual(document["room_init"]["entry"], "0045c020")
        self.assertEqual(document["room_init"]["entry_guard"], "EB2BB86DE923016DFD7B02A9FD830091")
        self.assertIn("0045c020: stp", document["room_init"]["instructions"][0])
        self.assertIn("0045c060: mov x26,x1", document["room_init"]["instructions"])
        self.assertIn("0045c064: mov x19,x0", document["room_init"]["instructions"])
        self.assertIn("0045c070: mov x23,x2", document["room_init"]["instructions"])
        self.assertFalse(document["runtime_binding_authorized"])

    def test_docs_distinguish_static_evidence_from_runtime_support(self):
        matrix = (ROOT / "docs/PC-Mod-兼容矩阵.md").read_text(encoding="utf-8")
        problem_log = (ROOT / "docs/问题与解决记录.md").read_text(encoding="utf-8")
        self.assertIn("Starterr 原生对象族静态证据", matrix)
        self.assertIn("MC_PRE_GET_COLLECTIBLE", matrix)
        self.assertIn("尚未授权 Runtime", matrix)
        self.assertIn("36 个调用点", matrix)
        self.assertIn("0x3c6838", matrix)
        self.assertIn("Stage 49", problem_log)
        self.assertIn("Stage 50", problem_log)
        self.assertIn("0x242c0", problem_log)


if __name__ == "__main__":
    unittest.main()
