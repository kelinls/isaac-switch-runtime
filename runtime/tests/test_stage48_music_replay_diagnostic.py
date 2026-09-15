import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage48MusicReplayDiagnosticTests(unittest.TestCase):
    def test_stage48_is_a_isolated_combined_replay_observer(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        lua = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        music_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "music_api.cpp"
        ).read_text(encoding="utf-8")
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text(encoding="utf-8")
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")

        self.assertIn("48,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_STAGE48_CXXFLAGS := -Os", makefile)
        self.assertIn("$(EXL_STAGE48_CXXFLAGS)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 48", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 48", entry)
        self.assertIn("exl::util::impl::InitMemLayout();", entry)
        self.assertIn("exl::util::impl::InitMemLayout();\n    virtmemSetup();", entry)
        self.assertIn("TryInstallStage48MusicReplayProbe", hook)
        self.assertIn("Stage48AdvanceMusicReplayProbe", hook)
        self.assertIn("Stage48TakeMusicReplayReport", hook)
        self.assertIn("MusicReplayMusicPlayHook", hook)
        self.assertIn("MusicReplaySoundActorPlayHook", hook)
        self.assertIn("MusicReplaySoundActorPauseHook", hook)
        self.assertIn("kStage48MusicPlayOffset", constants)
        self.assertIn("kStage48SoundActorPlayOffset", constants)
        self.assertIn("kStage48SoundActorPauseOffset", constants)
        self.assertIn("kStage48MusicPlayRelayCodeOffset", constants)
        self.assertIn("kStage48SoundActorPlayRelayCodeOffset", constants)
        self.assertIn("kStage48SoundActorPauseRelayCodeOffset", constants)
        self.assertIn("stage48-music-replay-relay", makefile)
        self.assertNotIn("Stage48ArmMusicReplayProbe", lua)
        self.assertIn("Stage48ArmMusicReplayProbe", music_api)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 48", music_api)
        stage48 = script[script.index("EXL_DIAGNOSTIC_STAGE == 48"):script.index("EXL_DIAGNOSTIC_STAGE == 45")]
        self.assertIn("music:Pause()", stage48)
        self.assertIn("game:IsPaused()", stage48)
        self.assertIn("local wasPaused = true", stage48)
        self.assertIn("local seenReadyUnpaused = false", stage48)
        self.assertIn("music:GetCurrentMusicID()", stage48)
        self.assertIn("musicId ~= Music.MUSIC_NULL", stage48)
        self.assertIn("seenReadyUnpaused and not wasPaused", stage48)
        self.assertNotIn("music:Resume()", stage48)
        self.assertIn("Stage48MusicReplayProbeInstallResult", header)
        for status in (
            "TargetMismatchMusicPlay", "TargetMismatchSoundActorPlay", "TargetMismatchSoundActorPause",
            "RelayPublishMusicPlayFailed", "RelayPublishSoundActorPlayFailed", "RelayPublishSoundActorPauseFailed",
        ):
            self.assertIn(status, header)
        self.assertIn("static_cast<u32>(stage48Install)", entry)
        stage48_install = hook[hook.index("Stage48MusicReplayProbeInstallResult TryInstallStage48MusicReplayProbe"):hook.index("void Stage48ArmMusicReplayProbe")]
        self.assertNotIn("TryInstallAtPtr", stage48_install)
        self.assertIn("PublishStage48RelayCallback", stage48_install)
        self.assertIn("original.size() > module.textSize ||", hook)
        self.assertIn("relayLength > module.textSize ||", hook)
        self.assertIn("sizeof(uintptr_t) > module.textSize", hook)


if __name__ == "__main__":
    unittest.main()
