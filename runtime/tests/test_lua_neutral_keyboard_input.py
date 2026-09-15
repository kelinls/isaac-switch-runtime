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


class LuaNeutralKeyboardInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-keyboard-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_keyboard_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"

#include <cstring>

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::ThunkUnavailable;
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

// Test seam for the controller-input bindings: the Hook installation is what publishes the
// real `Manager::` trampolines, so the harness binds fakes and the Lua script asserts that the
// handlers call them with the arguments the PC API passes.
bool FakeIsActionPressed(std::uint32_t action, std::uint32_t controller) {
    return action == 16 && controller == 0;
}
bool FakeIsActionTriggered(std::uint32_t action, std::uint32_t controller) {
    return action == 15 && controller == 0;
}
float FakeGetActionValue(std::uint32_t action, std::uint32_t controller) {
    return (action == 16 && controller == 0) ? 0.5f : 0.0f;
}
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::OpenFailed;
}
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* script = nullptr;
    if (std::strcmp(argv[1], "neutral") == 0) {
        script = R"lua(
local mod = RegisterMod('Keyboard compatibility', 1)
local inputCalls = 0
mod:AddCallback(ModCallbacks.MC_INPUT_ACTION, function()
  inputCalls = inputCalls + 1
end)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  if Keyboard.KEY_R ~= 82 or Keyboard.KEY_ENTER ~= 257 then error('keyboard constants') end
  if ButtonAction.ACTION_RESTART ~= 16 then error('action constant') end
  if InputHook.IS_ACTION_PRESSED ~= 0 then error('hook constant') end
  if Input.IsButtonTriggered(Keyboard.KEY_R, 0) ~= false then error('button trigger') end
  if Input.IsButtonPressed(Keyboard.KEY_R, 0) ~= false then error('button pressed') end
  if Input.GetButtonValue(Keyboard.KEY_R, 0) ~= 0 then error('button value') end
  -- The action queries are bound to real native trampolines on hardware; here they are bound
  -- to fakes, so these assertions prove the handler passes (action, controllerId) through.
  if Input.IsActionTriggered(15, 0) ~= true then error('action trigger') end
  if Input.IsActionTriggered(16, 0) ~= false then error('action trigger argument') end
  if Input.IsActionPressed(16, 0) ~= true then error('action pressed') end
  if Input.IsActionPressed(15, 0) ~= false then error('action pressed argument') end
  if Input.GetActionValue(16, 0) ~= 0.5 then error('action value') end
  if Input.GetActionValue(15, 0) ~= 0 then error('action value argument') end
  if inputCalls ~= 0 then error('input callback dispatched') end
end)
)lua";
    } else if (std::strcmp(argv[1], "input_only") == 0) {
        script = "local mod=RegisterMod('Input only',1); "
                 "mod:AddCallback(ModCallbacks.MC_INPUT_ACTION,function() error('must stay dormant') end)";
    } else if (std::strcmp(argv[1], "invalid") == 0) {
        script = "Input.IsButtonPressed(Keyboard.KEY_R)";
    } else {
        return 91;
    }
    LuaRuntime::SetInputBindings(reinterpret_cast<uintptr_t>(&FakeIsActionPressed),
                                 reinterpret_cast<uintptr_t>(&FakeIsActionTriggered),
                                 reinterpret_cast<uintptr_t>(&FakeGetActionValue));
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@keyboard.lua");
    if (std::strcmp(argv[1], "invalid") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPostUpdate();
    return LuaRuntime::TakeCallbackError() ? 3 : 0;
}
'''.lstrip()
        )
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "lua_keyboard_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
             "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *map(str, lua_objects), "-o", str(cls.harness)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_neutral_keyboard_queries_allow_mod_loading_without_dispatching_input_callback(self):
        self.run_scenario("neutral")

    def test_neutral_keyboard_queries_reject_missing_controller_id(self):
        self.run_scenario("invalid")

    def test_input_only_mod_is_loaded_even_though_its_callback_stays_dormant(self):
        self.run_scenario("input_only")

    def test_runtime_has_no_native_input_dispatch_or_hook(self):
        source = (SOURCE / "lua_runtime.cpp").read_text()
        self.assertNotIn("DispatchInputAction", source)
        self.assertNotIn("ProcessInput", source)


if __name__ == "__main__":
    unittest.main()
