import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import build_lua_harness
except ImportError:
    from test_support import build_lua_harness


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"

#: 本文件特有的那部分 harness：只覆盖 `Level:IsAscent` 的观测函数（行为由场景决定），
#: 其余观测函数与文件读取走 `test_support` 里的共享弱默认桩。
HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstring>

namespace {
GameIsAscentObservation g_Observation = GameIsAscentObservation::AscentFalse;
}

// 覆盖共享默认桩（强符号优先）：`Level:IsAscent` 的行为由场景决定。
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t ownerSlot, uintptr_t method) {
    return ownerSlot == 0x1111 && method == 0x4444 ? g_Observation : GameIsAscentObservation::MethodUnavailable;
}

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
'''


class LuaLevelIsAscentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-isascent-")
        cls.binary = build_lua_harness(
            source_root=SOURCE,
            workdir=Path(cls.temporary.name) / "harness",
            harness_source=HARNESS,
        )

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
