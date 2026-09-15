import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage47MusicReplayPath.java"
EVIDENCE = ROOT / "analysis/stage47-music-replay-path" / f"{BUILD_ID}.json"
GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")


class Stage47MusicReplayPathTests(unittest.TestCase):
    def test_exporter_is_read_only_and_locks_replay_candidates_to_target_build(self):
        self.assertTrue(SCRIPT.is_file(), "Stage47 Ghidra exporter must exist")
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            BUILD_ID,
            SOURCE_SHA256,
            "_ZN15IsaacRepentance5Music4PlayENS_6eMusicE",
            "_ZN15IsaacRepentance5Music4PlayENS_6eMusicEf",
            "_ZN15IsaacRepentance5Music6UpdateEv",
            "_ZN15IsaacRepentance5Music9StreamSet4PlayEv",
            "_ZN15IsaacRepentance5Music9StreamSet4StopEv",
            "_ZN15IsaacRepentance5Music9StreamSet6UpdateEb",
            "_ZN15IsaacRepentance5Music6Stream4PlayEv",
            "_ZN15IsaacRepentance5Music6Stream6UpdateEb",
            "_ZN15IsaacRepentance4Room9PlayMusicEv",
            "_ZN15IsaacRepentance4Room17TryPlayMusicLayerEv",
            "_ZN15IsaacRepentance4Room13PlayBossMusicEv",
            "getReferencesFrom",
            "strb ",
            "strh ",
            "Files.newBufferedWriter",
            "replay_hypothesis_authorized",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "createFunction(", "setBytes(", "createLabel(", "delete(",
            "runtime/", ".ips", "getReferencesTo(toAddr(BACKEND_ENTRY))",
        ):
            self.assertNotIn(forbidden, source)

        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK):
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "music-replay-path.json"
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

        self.assertEqual(document["schema_version"], "stage47-music-replay-path-v1")
        self.assertEqual(document["build_id"], BUILD_ID)
        self.assertEqual(document["source_sha256"], SOURCE_SHA256)
        self.assertEqual(
            [item["name"] for item in document["tracked_functions"]],
            [
                "Music::Play(eMusic)", "Music::Play(eMusic,float)", "Music::Update()",
                "Music::StreamSet::Play()", "Music::StreamSet::Stop()",
                "Music::StreamSet::Pause()", "Music::StreamSet::Resume()",
                "Music::StreamSet::Update(bool)", "Music::Stream::Play()",
                "Music::Stream::Pause()", "Music::Stream::Resume()",
                "Music::Stream::Update(bool)", "Room::PlayMusic()",
                "Room::TryPlayMusicLayer()", "Room::PlayBossMusic()",
            ],
        )
        for item in document["tracked_functions"]:
            self.assertEqual(len(item["entry_guard"]), 32)
            self.assertIn("calls", item)
            self.assertIn("non_stack_writes", item)
            self.assertIn("state_writes", item)
            self.assertNotIn("0066faa0", json.dumps(item).lower())
            self.assertFalse(any("Stack[" in write for write in item["non_stack_writes"]))
            self.assertFalse(any("[sp," in write.lower() for write in item["state_writes"]))
        self.assertFalse(document["replay_hypothesis_authorized"])
        self.assertEqual(json.loads(EVIDENCE.read_text(encoding="utf-8")), document)

    def test_evidence_matches_the_locked_nro_and_records_each_direct_play_target(self):
        self.assertEqual(hashlib.sha256(NRO_PATH.read_bytes()).hexdigest(), SOURCE_SHA256)
        document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        by_name = {item["name"]: item for item in document["tracked_functions"]}
        self.assertEqual(by_name["Music::Play(eMusic)"]["entry"], "0042721c")
        self.assertEqual(by_name["Music::Play(eMusic,float)"]["entry"], "00427238")
        self.assertEqual(by_name["Music::Update()"]["entry"], "00426c58")
        self.assertEqual(by_name["Room::PlayMusic()"]["entry"], "00451834")
        self.assertEqual(by_name["Room::TryPlayMusicLayer()"]["entry"], "00451f50")
        self.assertEqual(by_name["Room::PlayBossMusic()"]["entry"], "00452468")
        self.assertTrue(by_name["Music::Play(eMusic)"]["calls"])
        self.assertTrue(by_name["Music::Play(eMusic,float)"]["calls"])
        self.assertTrue(by_name["Room::PlayMusic()"]["calls"])


if __name__ == "__main__":
    unittest.main()
