import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLT_DATAFLOW = ROOT / "analysis/stage16-game-plt-dataflow/plt-dataflow-91C73FDD575061318D68886316AFEAC72388B2AB.json"


class Stage17Tests(unittest.TestCase):
    def test_stage25_exporter_reconciles_game_publication_and_shutdown(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage25GamePublicationChain.java"
        self.assertTrue(script.exists(), "Stage 25 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("003f9194", "003f9228", "003b3978", "publication_to_shutdown_observed", "ready_for_runtime_binding"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage24_exporter_tracks_materialized_game_owner_slot_accesses(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage24GameOwnerSlotPublication.java"
        self.assertTrue(script.exists(), "Stage 24 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("0xAAC698", "adrp", "mov", "add", "materialized_offset", "clear", "HashMap", "mnemonic.equals(\"b\")", "ready_for_runtime_binding"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage23_exporter_scans_game_owner_slot_lifecycle(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage23GameOwnerSlotLifecycle.java"
        self.assertTrue(script.exists(), "Stage 23 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("0xAAC698", "str", "stur", "ldr", "ldur", "lifecycle_status", "direct_slot_accesses", "BasicBlockModel", "pageRegister=\"\"; continue;", "String status=\"unproven\";"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage22_exporter_reconciles_update_and_shutdown_owner_expressions(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage22GameOwnerExpression.java"
        self.assertTrue(script.exists(), "Stage 22 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("same_owner_expression_observed", "003f9058", "003b3974", "0xaac000", "0x698"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage21_exporter_checks_shutdown_global_slot_dominance(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage21ShutdownGlobalSlotDominance.java"
        self.assertTrue(script.exists(), "Stage 21 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("getCodeBlocksContaining", "dominates_destructor_call", "003b3978", "getDestinations"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage20_exporter_supports_page_aligned_adrp_global_sources(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage20GameAdrpGlobalSlot.java"
        self.assertTrue(script.exists(), "Stage 20 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("adrp", "global_page(", "addressOperand", "ADRP address is not page aligned"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage19_exporter_requires_all_predecessor_agreement(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage19GameCfgAgreement.java"
        self.assertTrue(script.exists(), "Stage 19 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("BasicBlockModel", "getSources", "all_predecessors_agree", "new HashSet<>(active)"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_stage18_exporter_supports_callee_saved_register_slices(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage18GameCalleeSavedProvenance.java"
        self.assertTrue(script.exists(), "Stage 18 exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("BasicBlockModel", "getSources", "predecessorBudget", "load(", "getExecutableSHA256()"):
            self.assertIn(required, source)
        self.assertIn("memoryOperand", source)
        self.assertIn("block.getMinAddress() + \":\" + before + \":\" + reg + \":\" + budget", source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_exporter_uses_read_only_basic_block_slices(self):
        script = ROOT / "analysis/ghidra/scripts/ExportStage17GameCallsiteSlice.java"
        self.assertTrue(script.exists(), "Stage 17 slice exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in ("BasicBlockModel", "getSources", "slices", "getExecutableSHA256()", "Files.newBufferedWriter"):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

    def test_callsite_targets_require_stage16_plts(self):
        from tools.stage17_game_callsite_slice import write_callsite_targets

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "targets.json"
            document = write_callsite_targets(PLT_DATAFLOW, output)
            self.assertEqual(document["callsites"]["manager_update_game_update"]["call_address"], "003f905c")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)
