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


class LuaItemPoolGetCollectibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-itempool-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "itempool_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"
#include "domain/callback/callback_descriptor.hpp"

#include <array>
#include <cstdint>
#include <cstring>

namespace {
GameItemPoolObservation g_Observation = GameItemPoolObservation::Success;
alignas(8) std::array<std::uint8_t, 0x8C8> g_ItemPool{};
std::uint64_t g_Room = 0;
int g_CallCount = 0;
std::uint64_t g_NestedResult = 1;
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

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t ownerSlot, void** itemPool) {
    if (ownerSlot != 0x1111 || itemPool == nullptr) return GameItemPoolObservation::OwnerUnreadable;
    if (g_Observation == GameItemPoolObservation::Success) *itemPool = g_ItemPool.data();
    return g_Observation;
}

GameRoomObservation ReadCurrentGameRoom(uintptr_t ownerSlot, void** room) {
    if (ownerSlot != 0x1111 || room == nullptr) return GameRoomObservation::OwnerUnreadable;
    *room = &g_Room;
    return GameRoomObservation::Success;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}

std::uint32_t GetCollectible(void* itemPool, std::uint32_t poolType, std::uint32_t seed,
                             std::uint32_t noDecrease, std::uint32_t defaultItem) {
    if (itemPool != g_ItemPool.data()) return 0;
    ++g_CallCount;
    if (g_CallCount == 1 && (poolType != 7 || seed != 11 || noDecrease != 1 || defaultItem != 0)) return 0;
    if (g_CallCount == 2 && (poolType != 8 || seed != 12 || noDecrease != 0 || defaultItem != 13)) return 0;
    g_NestedResult = LuaRuntime::DispatchPreGetCollectible(itemPool, 1, 2, 1, 0);
    return g_CallCount == 1 ? 701 : 802;
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    const char* script = nullptr;
    if (std::strcmp(scenario, "top_level") == 0) {
        script = "Game():GetItemPool()";
    } else if (std::strcmp(scenario, "callback_survives_error") == 0) {
        script =
            "CALLBACK_ATTEMPTS=0;"
            "local mod=RegisterMod('Probe',1);"
            "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function()"
            "  CALLBACK_ATTEMPTS=CALLBACK_ATTEMPTS+1;"
            "  if CALLBACK_ATTEMPTS==1 then error('first frame only') end;"
            "end)";
    } else if (std::strcmp(scenario, "room_grid_path_float") == 0) {
        script =
            "local mod=RegisterMod('Probe',1);"
            "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function()"
            "  local room=Game():GetRoom();"
            "  if room:GetGridPath(5.0)~=1000 then error('grid path') end;"
            "end)";
    } else if (std::strcmp(scenario, "invalid") == 0) {
        script = "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function() Game():GetItemPool():GetCollectible(-1,false,11) end)";
    } else {
        if (std::strcmp(scenario, "unreadable") == 0) {
            g_Observation = GameItemPoolObservation::GameUnreadable;
        }
        script = "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function() local p=Game():GetItemPool(); if p:GetCollectible(7,false,11)~=701 then error('first get') end; if p:GetCollectible(8,true,12,13)~=802 then error('second get') end; if p:GetLastPool()~=12345 then error('last pool') end end)";
    }
    *reinterpret_cast<std::uint32_t*>(g_ItemPool.data() + 0x8C0) = 12345;
    LuaRuntime::SetGameBindings(0x1111, 0x2222);
    LuaRuntime::SetItemPoolGetCollectibleBinding(reinterpret_cast<uintptr_t>(&GetCollectible));
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@itempool.lua");
    if (std::strcmp(scenario, "top_level") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    if (std::strcmp(scenario, "callback_survives_error") == 0) {
        LuaRuntime::DispatchPostUpdate();
        const bool firstError = LuaRuntime::TakeCallbackError();
        const std::uint32_t countAfterFirst =
            LuaRuntime::RegisteredCallbackCount(isaac::runtime::kCallbackPostUpdate);
        LuaRuntime::DispatchPostUpdate();
        const bool secondError = LuaRuntime::TakeCallbackError();
        double attempts = 0.0;
        const bool readAttempts = LuaRuntime::ReadLuaGlobalNumber("CALLBACK_ATTEMPTS", &attempts);
        return firstError && !secondError && countAfterFirst == 1 &&
                       readAttempts && attempts == 2.0
                   ? 0
                   : 4;
    }
    if (std::strcmp(scenario, "room_grid_path_float") == 0) {
        LuaRuntime::DispatchPostUpdate();
        return LuaRuntime::TakeCallbackError() ? 5 : 0;
    }
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    if (std::strcmp(scenario, "unreadable") == 0 || std::strcmp(scenario, "invalid") == 0) {
        return LuaRuntime::TakeCallbackError() ? 0 : 3;
    }
    if (g_CallCount != 2) return 10 + g_CallCount;
    if (g_NestedResult != 0) return 20;
    return LuaRuntime::TakeCallbackError() ? 30 : 0;
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
        cls.binary = temporary / "itempool_harness"
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

    def test_itempool_getcollectible_resolves_the_current_owner_and_preserves_lua_argument_mapping(self):
        self.run_harness("success")

    def test_itempool_getcollectible_rejects_callback_external_invalid_and_unreadable_access(self):
        self.run_harness("top_level")
        self.run_harness("invalid")
        self.run_harness("unreadable")

    def test_callback_lua_error_is_recorded_without_removing_the_callback(self):
        self.run_harness("callback_survives_error")

    def test_room_grid_path_accepts_an_integral_float_from_eid(self):
        self.run_harness("room_grid_path_float")

    def test_itempool_handle_does_not_store_a_native_pointer(self):
        handles = (SOURCE / "lua_object_handles.hpp").read_text(encoding="utf-8")
        handle = handles[handles.index("struct ItemPoolHandle"):handles.index("struct RoomHandle")]
        self.assertNotIn("uintptr_t", handle)
        self.assertNotIn("ItemPool*", handle)
        self.assertNotIn("void*", handle)

    def test_itempool_api_is_general_and_uses_the_existing_reentry_guard(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        game_api = (SOURCE.parent / "src" / "interfaces" / "lua" / "game_api.cpp").read_text(
            encoding="utf-8"
        )
        remaining_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "remaining_api.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("0x02010004", game_api)
        self.assertIn("0x05010001", remaining_api)
        self.assertIn("0x05010002", remaining_api)
        self.assertNotIn("EXL_ENABLE_STARTERR_RNG", game_api + remaining_api)
        self.assertIn("g_InPreGetCollectibleDispatch", runtime)

    def test_hook_manager_publishes_only_the_verified_original_getcollectible_entry(self):
        hook_manager = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        function = hook_manager[hook_manager.index("PreGetCollectibleRelayInstallResult TryInstallPreGetCollectibleRelay"):
                                hook_manager.index("bool VerifyGameOwnerSlot")]
        self.assertIn("VerifyPreGetCollectibleRelay(module, &slot, &original)", function)
        self.assertIn("LuaRuntime::SetItemPoolGetCollectibleBinding(original)", function)
        self.assertIn("PreGetCollectibleRelayInstallResult::Success", function)

    def test_itempool_call_validation_accepts_the_verified_relay_entry_and_original_tail(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        function = runtime[
            runtime.index("bool ValidateItemPoolGetCollectibleMethod(uintptr_t address) {"):
            runtime.index("bool IsReadableMusicObject")
        ]
        self.assertIn("kPreGetCollectibleRelayExpectedEntry", function)
        self.assertIn("kPreGetCollectibleRelayExpectedOriginal", function)
        self.assertNotIn("ValidateMusicMethod", function)


if __name__ == "__main__":
    unittest.main()
