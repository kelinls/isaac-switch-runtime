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


class LuaLevelIsAscentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-isascent-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "isascent_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstring>

namespace {
GameIsAscentObservation g_Observation = GameIsAscentObservation::AscentFalse;
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) { return GameIsPausedObservation::OwnerUnreadable; }
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) { return GameLevelStageObservation::GameUnreadable; }
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) { return GameIsGreedModeObservation::MethodUnavailable; }
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t ownerSlot, uintptr_t method) {
    return ownerSlot == 0x1111 && method == 0x4444 ? g_Observation : GameIsAscentObservation::MethodUnavailable;
}
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) { return GameItemPoolObservation::GameUnreadable; }
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) { return GameRoomObservation::RoomUnreadable; }
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) { return GameRoomObservation::RoomUnreadable; }

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    if (std::strcmp(scenario, "unreadable") == 0) g_Observation = GameIsAscentObservation::GameUnreadable;
    if (std::strcmp(scenario, "true") == 0) g_Observation = GameIsAscentObservation::AscentTrue;
    const char* script = std::strcmp(scenario, "top_level") == 0 ? "Game():GetLevel():IsAscent()" :
        (std::strcmp(scenario, "true") == 0 ?
         "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function() if Game():GetLevel():IsAscent()~=true then error('wrong ascent state') end end)" :
         "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function() if Game():GetLevel():IsAscent()~=false then error('wrong ascent state') end end)");
    LuaRuntime::SetGameBindings(0x1111, 0x2222);
    LuaRuntime::SetLevelIsAscentBinding(0x4444);
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@isascent.lua");
    if (std::strcmp(scenario, "top_level") == 0) return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    return std::strcmp(scenario, "unreadable") == 0 ? (LuaRuntime::TakeCallbackError() ? 0 : 3) :
           (LuaRuntime::TakeCallbackError() ? 4 : 0);
}
'''.lstrip(), encoding="utf-8")
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        objects = []
        for source in sorted(lua_root.glob("*.c")):
            if source.name in excluded:
                continue
            output = temporary / f"{source.stem}.o"
            build = subprocess.run(["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root),
                                    "-c", str(source), "-o", str(output)], text=True, capture_output=True)
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            objects.append(output)
        cls.binary = temporary / "isascent_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_DIAGNOSTIC_STAGE=14",
             "-DEXL_LOAD_KIND=Module", "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *(str(path) for path in objects), "-lm", "-o", str(cls.binary)],
            text=True, capture_output=True)
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_harness(self, scenario):
        result = subprocess.run([str(self.binary), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_level_isascent_is_a_general_runtime_api(self):
        self.run_harness("false")
        self.run_harness("true")

    def test_level_isascent_rejects_top_level_and_unreadable_native_state(self):
        self.run_harness("top_level")
        self.run_harness("unreadable")

    def test_isascent_registration_has_no_mod_specific_build_gate(self):
        api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "remaining_api.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("0x03010002", api)
        self.assertNotIn("INSTANT_RESTART", api)


if __name__ == "__main__":
    unittest.main()
