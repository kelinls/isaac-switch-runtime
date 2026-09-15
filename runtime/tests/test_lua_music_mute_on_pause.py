import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class LuaMusicMuteOnPauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-music-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_music_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"
#include "game_file_reader.hpp"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>

namespace {
bool g_Paused = false;
unsigned g_PauseCalls = 0;
unsigned g_ResumeCalls = 0;
struct alignas(uintptr_t) Manager { unsigned char padding[0x36068]; int musicId; } g_Manager{};
std::string g_ModRoot;

bool ReadHostFile(const std::string& path, std::string* output) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) return false;
    output->assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    return !output->empty();
}
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t ownerSlot, uintptr_t thunk) {
    if (ownerSlot != 0x1111 || thunk != 0x2222) return GameIsPausedObservation::ThunkUnavailable;
    return g_Paused ? GameIsPausedObservation::PausedTrue : GameIsPausedObservation::PausedFalse;
}

GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}

GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) { return GameIsAscentObservation::MethodUnavailable; }

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}

GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}

int CurrentMusicId(const void* music) { return *reinterpret_cast<const int*>(music); }
void PauseMusic(void*) { ++g_PauseCalls; }
void ResumeMusic(void*) { ++g_ResumeCalls; }

namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char* path, u8* buffer,
                            std::size_t capacity, std::size_t* length) {
    constexpr char prefix[] = "rom:/isaac_mods/mods/MuteOnPause/";
    if (path == nullptr || buffer == nullptr || length == nullptr ||
        std::strncmp(path, prefix, sizeof(prefix) - 1) != 0) {
        return TextReadResult::InvalidArgument;
    }
    std::fprintf(stderr, "read=%s\n", path);
    std::string source;
    if (!ReadHostFile(g_ModRoot + "/" + (path + sizeof(prefix) - 1), &source)) {
        return TextReadResult::OpenFailed;
    }
    if (source.size() > capacity) return TextReadResult::LengthOutOfRange;
    std::memcpy(buffer, source.data(), source.size());
    *length = source.size();
    return TextReadResult::Success;
}
}

int main(int argc, char** argv) {
    const bool realHold = argc >= 2 && std::strcmp(argv[1], "real_hold") == 0;
    const bool realResume = argc >= 2 && std::strcmp(argv[1], "real_resume") == 0;
    const bool realMod = realHold || realResume;
    if ((!realMod && argc != 2) || (realMod && argc != 3)) return 90;
    LuaRuntime::SetGameBindings(0x1111, 0x2222);
    LuaRuntime::SetMusicBindings(reinterpret_cast<uintptr_t>(&CurrentMusicId),
                                 reinterpret_cast<uintptr_t>(&PauseMusic),
                                 reinterpret_cast<uintptr_t>(&ResumeMusic));
    const char* script = nullptr;
    std::string realEntry;
    if (std::strcmp(argv[1], "outside") == 0) {
        script = "local music=MusicManager(); music:GetCurrentMusicID()";
    } else if (std::strcmp(argv[1], "mod_fields") == 0) {
        script = "local mod=RegisterMod('Fields',1); function mod:run() return 7 end; "
                 "if type(mod.run)~='function' or mod:run()~=7 then error('mod field mismatch') end; "
                 "mod:AddCallback(ModCallbacks.MC_POST_RENDER,mod.run)";
    } else if (std::strcmp(argv[1], "separate") == 0) {
        script = "local mod=RegisterMod('Probe',1); local update=0; local render=0; "
                 "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function() update=update+1 end); "
                 "mod:AddCallback(ModCallbacks.MC_POST_RENDER,function() render=render+1; "
                 "if update~=1 or render~=1 then error('phase mismatch') end end)";
    } else if (!realMod) {
        script = "local mod=RegisterMod('Mute on Pause',1); local game=Game(); local music=MusicManager(); "
                 "if type(music)~='userdata' then error('not userdata') end; local wasPaused=false; local storedTrack=nil; "
                 "mod:AddCallback(ModCallbacks.MC_POST_RENDER,function() local isPaused=game:IsPaused(); "
                 "if isPaused and not wasPaused then storedTrack=music:GetCurrentMusicID(); music:Pause(); "
                 "elseif not isPaused and wasPaused then if storedTrack and storedTrack~=Music.MUSIC_NULL then music:Resume() end end; "
                 "wasPaused=isPaused; RuntimeTest.MarkPostUpdate() end)";
    }
    LuaRuntime::LuaInitResult init = LuaRuntime::LuaInitResult::ScriptLoadFailed;
    if (realMod) {
        g_ModRoot = argv[2];
        if (!ReadHostFile(g_ModRoot + "/main.lua", &realEntry)) return 91;
        init = LuaRuntime::InitializeManifestMod(
            realEntry.data(), realEntry.size(), "@rom:/isaac_mods/main.lua",
            "rom:/isaac_mods/mods/MuteOnPause", GameFileReader::Bindings{});
    } else {
        init = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@mute.lua");
    }
    if (std::strcmp(argv[1], "outside") == 0) return init == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    if (init != LuaRuntime::LuaInitResult::Success) {
        std::fprintf(stderr, "init=%u\n", static_cast<unsigned>(init));
        return 2;
    }
    if (std::strcmp(argv[1], "mod_fields") == 0) return 0;
    if (std::strcmp(argv[1], "separate") == 0) {
        LuaRuntime::DispatchPostUpdate();
        LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(&g_Manager));
        return LuaRuntime::TakeCallbackError() ? 3 : 0;
    }
    g_Manager.musicId = 7;
    LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(&g_Manager));
    g_Paused = true;
    LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(&g_Manager));
    LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(&g_Manager));
    if (realHold) {
        return g_PauseCalls == 1 && g_ResumeCalls == 0 && !LuaRuntime::TakeCallbackError() ? 0 : 5;
    }
    g_Paused = false;
    LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(&g_Manager));
    return g_PauseCalls == 1 && g_ResumeCalls == 1 && !LuaRuntime::TakeCallbackError() ? 0 : 4;
}
'''.lstrip()
        )

        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / (lua_source.stem + ".o")
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c", str(lua_source), "-o", str(output)],
                text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)

        cls.harness = temporary / "lua_music_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
             "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *(str(path) for path in lua_objects), "-lm", "-o", str(cls.harness)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_harness(self, scenario, *arguments):
        result = subprocess.run([str(self.harness), scenario, *arguments], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_music_manager_is_no_pointer_userdata_and_runs_mute_on_pause_edges(self):
        self.run_harness("mute")

    def test_post_update_and_post_render_callbacks_are_independent(self):
        self.run_harness("separate")

    def test_music_api_is_rejected_outside_a_managed_render_callback(self):
        self.run_harness("outside")

    def test_register_mod_supports_custom_method_fields(self):
        self.run_harness("mod_fields")

    def test_unmodified_mute_on_pause_holds_music_while_pause_state_stays_true(self):
        self.run_harness("real_hold", str(ROOT / "runtime" / "pc-mods" / "MuteOnPause"))

    def test_unmodified_mute_on_pause_resumes_after_a_false_render_state(self):
        self.run_harness("real_resume", str(ROOT / "runtime" / "pc-mods" / "MuteOnPause"))

    def test_music_bindings_are_guarded_and_userdata_has_no_native_address(self):
        """The handle stays pointer-free and the method guards stay in the legacy unit.

        `MusicHandle` and the Music handlers moved to the shared transitional header
        and the `music_api.cpp` family unit; the byte guards and the Manager-derived
        object resolution still live in `lua_runtime.cpp` behind the accessors the
        family unit calls.
        """
        legacy = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        handles = (SOURCE / "lua_object_handles.hpp").read_text(encoding="utf-8")
        music_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "music_api.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("struct MusicHandle {\n    std::uint8_t reserved;\n};", handles)
        self.assertNotIn("uintptr_t", handles[handles.index("struct MusicHandle"):handles.index("struct RngHandle")])
        self.assertIn("Music:GetCurrentMusicID native bindings are unavailable", music_api)
        self.assertIn("kManagerMusicOffset", legacy)
        self.assertIn("ValidateMusicMethod", legacy)
        for accessor in ("ResolveMusicGetCurrentMusicId", "ResolveMusicPause", "ResolveMusicResume"):
            with self.subTest(accessor=accessor):
                self.assertIn(accessor, music_api)
                self.assertIn(accessor, legacy)


if __name__ == "__main__":
    unittest.main()
