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


class LuaRoomGetTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-room-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "room_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstring>

namespace {
GameRoomObservation g_Observation = GameRoomObservation::Success;
std::uint32_t g_RoomType = 5;
std::uint64_t g_RoomStorage = 0;
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::OwnerUnreadable;
}

GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}

GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) { return GameIsAscentObservation::MethodUnavailable; }

GameRoomObservation ReadCurrentGameRoom(uintptr_t ownerSlot, void** room) {
    if (ownerSlot != 0x1111 || room == nullptr) return GameRoomObservation::OwnerUnreadable;
    if (g_Observation == GameRoomObservation::Success) *room = &g_RoomStorage;
    return g_Observation;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t ownerSlot, std::uint32_t* roomType) {
    if (ownerSlot != 0x1111 || roomType == nullptr) return GameRoomObservation::OwnerUnreadable;
    if (g_Observation == GameRoomObservation::Success) *roomType = g_RoomType;
    return g_Observation;
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    const char* script = nullptr;
    if (std::strcmp(scenario, "top_level") == 0) {
        script = "Game():GetRoom()";
    } else {
        if (std::strcmp(scenario, "unreadable") == 0) {
            g_Observation = GameRoomObservation::RoomUnreadable;
        }
        script = "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function() if Game():GetRoom():GetType()~=5 then error('wrong room type') end end)";
    }
    LuaRuntime::SetGameBindings(0x1111, 0x2222);
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@room.lua");
    if (std::strcmp(scenario, "top_level") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    if (std::strcmp(scenario, "unreadable") == 0) return LuaRuntime::TakeCallbackError() ? 0 : 3;
    return LuaRuntime::TakeCallbackError() ? 4 : 0;
}
'''.lstrip(), encoding="utf-8")
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        objects = []
        for source in sorted(lua_root.glob("*.c")):
            if source.name in excluded:
                continue
            output = temporary / f"{source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root),
                 "-c", str(source), "-o", str(output)], text=True, capture_output=True)
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            objects.append(output)
        cls.binary = temporary / "room_harness"
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

    def test_game_getroom_and_room_gettype_resolve_the_current_room_in_a_callback(self):
        self.run_harness("success")

    def test_room_api_rejects_top_level_and_unreadable_native_state(self):
        self.run_harness("top_level")
        self.run_harness("unreadable")

    def test_room_handle_does_not_store_a_native_pointer(self):
        handles = (SOURCE / "lua_object_handles.hpp").read_text(encoding="utf-8")
        handle = handles[handles.index("struct RoomHandle"):handles.index("struct MusicHandle")]
        self.assertNotIn("uintptr_t", handle)
        self.assertNotIn("Room*", handle)
        self.assertNotIn("void*", handle)

    def test_room_api_is_general_and_treasure_visit_count_stays_unregistered(self):
        game_api = (SOURCE.parent / "src" / "interfaces" / "lua" / "game_api.cpp").read_text(
            encoding="utf-8"
        )
        remaining_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "remaining_api.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("0x02010005", game_api)
        self.assertIn("0x04010001", remaining_api)
        self.assertNotIn("GetTreasureRoomVisitCount", game_api + remaining_api)
        self.assertNotIn("EXL_ENABLE_STARTERR_RNG", game_api + remaining_api)


if __name__ == "__main__":
    unittest.main()
