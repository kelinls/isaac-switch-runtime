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


class StarterrRngTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-starterr-rng-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "starterr_rng_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstring>

namespace {
std::uint32_t g_Seed = 0;
std::uint32_t g_Shift = 0;
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::OwnerUnreadable;
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

void SetSeed(void* storage, std::uint32_t seed, std::uint32_t shift) {
    g_Seed = seed;
    g_Shift = shift;
    std::memcpy(storage, &seed, sizeof(seed));
}

void Next(void* storage) {
    std::uint32_t value = 0;
    std::memcpy(&value, storage, sizeof(value));
    value += 1;
    std::memcpy(storage, &value, sizeof(value));
}

int main() {
    LuaRuntime::SetRngBindings(reinterpret_cast<uintptr_t>(&SetSeed), reinterpret_cast<uintptr_t>(&Next));
    const char* script =
        "local rng=RNG(); rng:SetSeed(7,0); local value=rng:Next(); "
        "if value~=8 then error('bad next') end "
        "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function() end)";
    if (LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@starterr-rng.lua") !=
        LuaRuntime::LuaInitResult::Success) return 1;
    return g_Seed == 7 && g_Shift == 0 ? 0 : 2;
}
'''.lstrip()
        )
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            obj = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root),
                 "-c", str(lua_source), "-o", str(obj)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(obj)
        cls.binary = temporary / "starterr_rng_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_DIAGNOSTIC_STAGE=14",
             "-DEXL_LOAD_KIND=Module", "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *(str(path) for path in lua_objects), "-lm", "-o", str(cls.binary)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_rng_setseed_and_next_use_value_owned_userdata(self):
        result = subprocess.run([str(self.binary)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rng_handle_does_not_store_native_pointer(self):
        # The handle bodies moved into the shared transitional header when the
        # per-family Lua API units were split out of `lua_runtime.cpp`.
        handles = (SOURCE / "lua_object_handles.hpp").read_text(encoding="utf-8")
        handle = handles[handles.index("struct RngHandle"):]
        self.assertIn("std::array<std::uint8_t, 16> storage", handle)
        self.assertNotIn("RNG*", handle)

    def test_rng_api_is_not_gated_by_starterr(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        self.assertNotIn("EXL_ENABLE_STARTERR_RNG", runtime)

    def test_rng_runtime_unit_uses_size_optimization_for_verified_module_layout(self):
        """Size optimization is what keeps the verified module LOAD boundary.

        The flag was tightened from `-Os` to `-Oz` when the RX budget was pinned,
        and the per-family Lua API units inherit the same requirement because they
        were split out of `lua_runtime.o`.
        """
        makefile = (SOURCE.parent / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        self.assertIn("lua_runtime.o: CXXFLAGS += -Oz", makefile)
        self.assertIn("$(LUA_FAMILY_OPTIMIZED_CPPFILES): CXXFLAGS += -Oz", makefile)
        for family in ("mod_api.o", "game_api.o", "remaining_api.o", "music_api.o",
                       "rng_api.o", "input_api.o"):
            with self.subTest(family=family):
                self.assertIn(family, makefile)


if __name__ == "__main__":
    unittest.main()
