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
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage46MusicAudioPath.java"
EVIDENCE = (
    ROOT
    / "analysis/stage46-music-audio-path"
    / "91C73FDD575061318D68886316AFEAC72388B2AB.json"
)
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")


class Stage46MusicAudioPathTests(unittest.TestCase):
    def test_exporter_is_read_only_and_locks_the_audio_lineage_to_the_target_build(self):
        self.assertTrue(SCRIPT.is_file(), "Stage 46 Ghidra exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "91C73FDD575061318D68886316AFEAC72388B2AB",
            "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
            "_ZN15IsaacRepentance5Music5PauseEv",
            "_ZN15IsaacRepentance5Music6ResumeEv",
            "getReferencesTo",
            "getReferencesFrom",
            "Files.newBufferedWriter",
            "kage_sound_link_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "music-audio-path.json"
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

        self.assertEqual(document["schema_version"], "stage46-music-audio-path-v1")
        self.assertEqual(document["music_selection"], {
            "current_index_offset": "00000000",
            "stream_set_offset": "00000008",
            "record_stride": "000001e0",
        })
        self.assertEqual(document["pause_dispatch"]["entry"], "0067e090")
        self.assertEqual(document["pause_dispatch"]["got_slot"], "00aa4fb0")
        self.assertEqual(document["resume_dispatch"]["entry"], "0067e0a0")
        self.assertEqual(document["resume_dispatch"]["got_slot"], "00aa4fb8")
        self.assertEqual(document["shared_backend"]["entry"], "0066faa0")
        self.assertEqual(
            document["shared_backend"]["relevant_dispatch_entries"],
            ["0067e090", "0067e0a0"],
        )
        self.assertGreater(document["shared_backend"]["import_stub_reference_count"], 2)
        self.assertEqual(
            [context["name"] for context in document["pause_context"]],
            ["Manager::StartMenu", "PauseScreen::Show", "PauseScreen::Hide"],
        )
        self.assertEqual(len(document["kage_sound_candidates"]), 1)
        self.assertEqual(
            document["kage_sound_candidates"][0]["source"],
            "crash_report_kage_sound_update_thread",
        )
        self.assertTrue(document["kage_sound_candidates"][0]["effects"])
        self.assertFalse(document["kage_sound_candidates"][0]["direct_music_link"])
        self.assertFalse(document["kage_sound_link_authorized"])
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)

    def test_streamset_pause_and_resume_are_locked_to_kage_sound_actor_methods(self):
        from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols

        data = NRO_PATH.read_bytes()
        _, symbols = parse_dynamic_symbols(data)
        _, relocations = parse_dynamic_relocations(data)
        expected = {
            "pause": (
                "_ZN15IsaacRepentance5Music9StreamSet5PauseEv",
                0x424834,
                "_ZN4KAGE5Sound10SoundActor5PauseEv",
                0xAA4F20,
                "0067df70",
            ),
            "resume": (
                "_ZN15IsaacRepentance5Music9StreamSet6ResumeEv",
                0x4249C8,
                "_ZN4KAGE5Sound10SoundActor6ResumeEv",
                0xAA4F28,
                "0067df80",
            ),
        }
        document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        for action, (streamset_symbol, streamset_entry, actor_symbol, actor_slot, actor_thunk) in expected.items():
            self.assertEqual(symbols[streamset_symbol].file_offset, streamset_entry)
            self.assertTrue(symbols[streamset_symbol].is_defined)
            self.assertTrue(symbols[actor_symbol].is_defined)
            self.assertIn(actor_slot, [item.offset for item in relocations[actor_symbol]])
            self.assertEqual(document["streamset_actions"][action], {
                "streamset_symbol": streamset_symbol,
                "entry": f"{streamset_entry:08x}",
                "sound_actor_symbol": actor_symbol,
                "sound_actor_got_slot": f"{actor_slot:08x}",
                "sound_actor_thunk": actor_thunk,
            })


if __name__ == "__main__":
    unittest.main()
