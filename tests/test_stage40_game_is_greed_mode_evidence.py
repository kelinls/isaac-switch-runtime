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
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage40GameIsGreedModeEvidence.java"
PLT_TARGET = ROOT / "analysis/stage40-game-isgreedmode-evidence/plt-target-91C73FDD575061318D68886316AFEAC72388B2AB.json"
EVIDENCE = ROOT / "analysis/stage40-game-isgreedmode-evidence/91C73FDD575061318D68886316AFEAC72388B2AB.json"


class Stage40GameIsGreedModeEvidenceTests(unittest.TestCase):
    def test_dynamic_symbol_and_jump_slot_are_version_locked(self):
        from tools.stage40_game_is_greed_mode import write_plt_target

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plt-target.json"
            document = write_plt_target(NRO_PATH, output)

            self.assertEqual(
                document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB"
            )
            self.assertEqual(
                document["source_sha256"],
                "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            )
            self.assertEqual(document["symbol"], "_ZNK15IsaacRepentance4Game11IsGreedModeEv")
            self.assertEqual(document["defined_entry"], 0x350200)
            self.assertEqual(document["entry_guard"], "08058052E805A072086868B808791F12")
            self.assertEqual(document["got_slot"], 0xA9E4E0)
            self.assertEqual(document["relocation_type"], 1026)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)

    def test_ghidra_exporter_locks_the_unique_thunk_and_bool_abi(self):
        self.assertTrue(SCRIPT.exists(), "Stage 40 Ghidra exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "91C73FDD575061318D68886316AFEAC72388B2AB",
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            "0xA9E4E0L",
            "getReferencesTo",
            "getFunctionContaining",
            "Files.newBufferedWriter",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "isgreedmode-evidence.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(ghidra), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(PLT_TARGET), str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage40-game-isgreedmode-evidence-v1")
        self.assertEqual(document["target_entry"], "00350200")
        self.assertEqual(document["target_guard"], "08058052E805A072086868B808791F12")
        self.assertEqual(document["thunk_count"], 1)
        self.assertEqual(document["thunk_entry"], "00670af0")
        self.assertEqual(document["thunk_guard"], "702100D0117242F91082139120021FD6")
        self.assertIn("00350208: ldr w8,[x0, x8, LSL #0x0]", document["target_instructions"])
        self.assertIn("00350214: cset w0,eq", document["target_instructions"])
        self.assertEqual(document["aapcs_this_register"], "x0")
        self.assertEqual(document["aapcs_return_register"], "w0")
        self.assertTrue(document["runtime_binding_authorized"])
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)

    def test_handoff_and_problem_log_distinguish_evidence_from_runtime_implementation(self):
        handoff = (ROOT / "docs/会话交接-2026-08-26.md").read_text(encoding="utf-8")
        problem_log = (ROOT / "docs/问题与解决记录.md").read_text(encoding="utf-8")

        self.assertIn("Stage 40", handoff)
        self.assertIn("Game:IsGreedMode", handoff)
        self.assertIn("尚未加入 Runtime", handoff)
        self.assertIn("Stage 40", problem_log)
        self.assertIn("Game:IsGreedMode", problem_log)


if __name__ == "__main__":
    unittest.main()
