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
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage41MusicProvenance.java"
EFFECT_SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage43MusicPauseEffect.java"
EFFECT_EVIDENCE = (
    ROOT
    / "analysis/stage43-music-pause-effect"
    / "91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
PLT_TARGET = (
    ROOT
    / "analysis/stage41-music-provenance"
    / "plt-target-91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
EVIDENCE = (
    ROOT
    / "analysis/stage41-music-provenance"
    / "91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")


class Stage41MusicProvenanceTests(unittest.TestCase):
    def test_music_pause_effect_exporter_is_version_locked_and_read_only(self):
        self.assertTrue(EFFECT_SCRIPT.exists(), "Stage 43 Ghidra exporter must exist")
        source = EFFECT_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "91C73FDD575061318D68886316AFEAC72388B2AB",
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            "_ZN15IsaacRepentance5Music5PauseEv",
            "_ZN15IsaacRepentance5Music6ResumeEv",
            "getInstructions",
            "getReferencesFrom",
            "Files.newBufferedWriter",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "music-pause-effect.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(EFFECT_SCRIPT.parent),
                    "-postScript", EFFECT_SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage43-music-pause-effect-v1")
        self.assertEqual(document["functions"][0]["entry"], "0042764c")
        self.assertEqual(document["functions"][1]["entry"], "00427660")
        self.assertIn("0067e090", document["functions"][0]["direct_effects"][0])
        self.assertIn("0067e0a0", document["functions"][1]["direct_effects"][0])
        self.assertEqual(json.loads(EFFECT_EVIDENCE.read_text(encoding="utf-8")), document)

    def test_music_update_dynamic_entry_is_version_locked(self):
        from tools.stage41_music_provenance import write_plt_target

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "music-update-plt-target.json"
            document = write_plt_target(NRO_PATH, output)

            self.assertEqual(
                document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB"
            )
            self.assertEqual(
                document["source_sha256"],
                "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            )
            self.assertEqual(
                document["symbol"], "_ZN15IsaacRepentance5Music6UpdateEv"
            )
            self.assertEqual(document["defined_entry"], 0x426C58)
            self.assertEqual(
                document["entry_guard"], "ED33B96DEB2B016DE923026DFD7B03A9"
            )
            self.assertEqual(document["relocation_type"], 1026)
            self.assertEqual(document["jump_slot_count"], 1)
            self.assertEqual(
                document["music_constructor"]["symbol"],
                "_ZN15IsaacRepentance5MusicC1Ev",
            )
            self.assertEqual(document["music_constructor"]["got_slot"], 0xAA41A8)
            self.assertEqual(
                document["music_destructor"]["symbol"],
                "_ZN15IsaacRepentance5MusicD1Ev",
            )
            self.assertEqual(document["music_destructor"]["got_slot"], 0xAA4238)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)

    def test_ghidra_exporter_records_music_update_thunk_and_callsites(self):
        self.assertTrue(SCRIPT.exists(), "Stage 41 Ghidra exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "91C73FDD575061318D68886316AFEAC72388B2AB",
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            "0xAA4490L",
            "getReferencesTo",
            "getFunctionContaining",
            "Files.newBufferedWriter",
            "runtime_binding_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "music-provenance.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(PLT_TARGET), str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage41-music-provenance-v1")
        self.assertEqual(document["target_entry"], "00426c58")
        self.assertEqual(document["thunk_count"], 1)
        self.assertTrue(document["callsites"])
        for callsite in document["callsites"]:
            self.assertEqual(
                set(callsite),
                {"caller", "call_address", "x0_source", "context_before", "context_after"},
            )
            self.assertIsInstance(callsite["x0_source"], str)
            self.assertIsInstance(callsite["context_before"], list)
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)

    def test_music_provenance_proves_one_manager_embedded_lifecycle(self):
        document = json.loads(EVIDENCE.read_text(encoding="utf-8"))

        self.assertEqual(document["manager_update_entry"], "003f8db8")
        self.assertEqual(
            document["manager_update_guard"], "FF4301D1FD7B01A9FD430091F71300F9"
        )
        self.assertEqual(document["manager_this_register"], "x0")
        self.assertEqual(document["manager_this_saved_register"], "x19")
        self.assertEqual(document["music_member_offset"], "00036068")
        self.assertEqual(document["constructor"]["manager_entry"], "003f3b00")
        self.assertEqual(document["constructor"]["call_address"], "003f3c34")
        self.assertEqual(document["constructor"]["thunk_entry"], "0067c480")
        self.assertEqual(document["destructor"]["manager_entry"], "003f3f48")
        self.assertEqual(document["destructor"]["call_address"], "003f4234")
        self.assertEqual(document["destructor"]["thunk_entry"], "0067c5a0")
        self.assertTrue(document["music_object_authorized"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
