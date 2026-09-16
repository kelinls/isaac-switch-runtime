"""多模组加载（2026-09-16）的宿主行为测试。

## 为什么需要它

在此之前本运行时**一次只加载一个模组**：`manifest.json` 的 `mods[]` 里只取第一个
`enabled = true` 的（`ModManifest::SelectFirstEnabled`），Lua 侧 `RegisterMod` 还会在第二次调用
时直接报 "RegisterMod accepts exactly one Mod"。用户侧的观察就是"现在只能使用一个模组"。

多模组加载按 PC 的语义实现，本用例钉住其中**三条**最容易写错的：

1. **多个 Mod 共享一个 Lua 状态**：第二个 Mod 的入口脚本必须**追加**执行，而不是
   `luaL_newstate()` 把第一个 Mod 的状态整个丢掉（丢了之后的症状不是"只加载一个"，
   而是"只剩最后一个"——比原来更糟）。判据：两个 Mod 各自登记的 `MC_POST_UPDATE` 回调
   在同一次派发里**都**被调用。
2. **每个 Mod 有自己的 owner 句柄**：回调登记按 owner 区分归属（`CallbackRegistry` 的
   Find/Remove/RemoveOwner 都靠它），两个 Mod 的回调不能混成一家。
3. **第二个 `RegisterMod` 不报错**（旧行为就是在这里抛错，把第二个 Mod 的加载整个打断）。

## 口径说明

* 夹具的两份脚本都**不用 `require`**：`require` 只在入口脚本里按各自的 Mod 目录解析，
  运行期 `require` 的已知局限见 `docs/问题与解决记录.md`，不在本用例的范围内。
* `InitializeManifestMod` 两次调用的 `modRoot` 是两个不同的目录，用的就是设备上真实的形状
  （`rom:/isaac_mods/mods/<目录名>`）。
"""

import shutil
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

HARNESS = r'''
#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/sprite_api.hpp"

#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"
#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include "application/callback/callback_registry.hpp"
#include "domain/callback/callback_descriptor.hpp"

#include <cstdio>
#include <cstring>
#include <string>

// 其他族的观测缝：Lua 运行时链接它们，宿主这边一律答"拿不到"（与其它 Lua 运行时用例同形）。
GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::ThunkUnavailable;
}
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) {
    return GameIsAscentObservation::MethodUnavailable;
}
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::OpenFailed;
}
}

namespace {

int g_Failures = 0;

void Check(bool condition, const char* what) {
    if (!condition) {
        ++g_Failures;
        std::printf("MULTIMOD_FAIL %s\n", what);
    }
}

// 两个 Mod 的入口脚本。都只做两件事：登记自己（`RegisterMod`）与登记一个 update 回调。
// 回调体给各自的全局计数器 +1 —— 那正是"同一次派发里两个 Mod 都被调用"的判据。
constexpr char kModAScript[] =
    "local mod = RegisterMod('Mod A', 1)\n"
    "A_OWNER_TAG = 'A'\n"
    "mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()\n"
    "  A_TICKS = (A_TICKS or 0) + 1\n"
    "end)\n";

constexpr char kModBScript[] =
    "local mod = RegisterMod('Mod B', 2)\n"
    "B_OWNER_TAG = 'B'\n"
    "mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()\n"
    "  B_TICKS = (B_TICKS or 0) + 1\n"
    "end)\n";

// 第三次登记：证明"多模组"不是"恰好两个"的上限，而是一次性守卫真的没了。
constexpr char kModCScript[] =
    "local mod = RegisterMod('Mod C', 3)\n"
    "mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()\n"
    "  C_TICKS = (C_TICKS or 0) + 1\n"
    "end)\n";

} // namespace

int main() {
    const GameFileReader::Bindings bindings{};
    const auto load = [&](const char* script, std::size_t length, const char* chunkName,
                          const char* modRoot) {
        return LuaRuntime::InitializeManifestMod(script, length, chunkName, modRoot, bindings);
    };

    // 第一个 Mod：走"首建"路径（状态还不存在）。
    const auto first = load(kModAScript, sizeof(kModAScript) - 1,
                            "@rom:/isaac_mods/mods/ModA/main.lua",
                            "rom:/isaac_mods/mods/ModA");
    Check(first == LuaRuntime::LuaInitResult::Success, "first_mod_initializes");
    Check(LuaRuntime::IsReady(), "engine_ready_after_first_mod");

    // 第二个 Mod：走"追加"路径 —— 旧实现会在这里新建状态，把第一个 Mod 丢掉。
    const auto second = load(kModBScript, sizeof(kModBScript) - 1,
                             "@rom:/isaac_mods/mods/ModB/main.lua",
                             "rom:/isaac_mods/mods/ModB");
    Check(second == LuaRuntime::LuaInitResult::Success, "second_mod_appends");
    // 第三个：证明上限不是 2。
    const auto third = load(kModCScript, sizeof(kModCScript) - 1,
                            "@rom:/isaac_mods/mods/ModC/main.lua",
                            "rom:/isaac_mods/mods/ModC");
    Check(third == LuaRuntime::LuaInitResult::Success, "third_mod_appends");

    // 三个 Mod 的登记结果：每个 Mod 的 `RegisterMod` 都必须成功（旧实现在第二次就报错，
    // 那会把第二个 Mod 的整个入口脚本打断），而且三次登记必须属于三个不同的 owner 句柄。
    Check(LuaRuntime::IsReady(), "engine_ready_after_all_mods");

    // 一次派发：三个 Mod 的 update 回调都要被调用（各 +1）。
    LuaRuntime::DispatchPostUpdate();
    Check(!LuaRuntime::TakeCallbackError(), "dispatch_without_error");
    double ticksA = 0.0;
    double ticksB = 0.0;
    double ticksC = 0.0;
    const bool readA = LuaRuntime::ReadLuaGlobalNumber("A_TICKS", &ticksA);
    const bool readB = LuaRuntime::ReadLuaGlobalNumber("B_TICKS", &ticksB);
    const bool readC = LuaRuntime::ReadLuaGlobalNumber("C_TICKS", &ticksC);
    Check(readA && ticksA == 1.0, "mod_a_callback_dispatched");
    Check(readB && ticksB == 1.0, "mod_b_callback_dispatched");
    Check(readC && ticksC == 1.0, "mod_c_callback_dispatched");

    // 回调归属：三条登记必须属于**三个不同的** owner 句柄。
    const auto& registry = LuaRuntime::ManagedCallbackRegistry();
    Check(registry.Count() == 3, "three_registrations_total");
    const auto* first_entry = registry.AtIndex(0);
    const auto* second_entry = registry.AtIndex(1);
    const auto* third_entry = registry.AtIndex(2);
    Check(first_entry != nullptr && second_entry != nullptr && third_entry != nullptr,
          "entries_present");
    if (first_entry != nullptr && second_entry != nullptr && third_entry != nullptr) {
        Check(first_entry->owner != second_entry->owner, "owners_differ_a_b");
        Check(second_entry->owner != third_entry->owner, "owners_differ_b_c");
        Check(first_entry->owner != third_entry->owner, "owners_differ_a_c");
        Check(first_entry->owner.valid() && second_entry->owner.valid() &&
                  third_entry->owner.valid(),
              "owners_valid");
        // 每个 Mod 的 Mod 对象都必须是 distinct 的 Lua 引用（否则 `RunCallback` 会把
        // 回调交给别人的 Mod 对象，PC 上 `self == mod` 的断言就会失败）。
        Check(first_entry->modReference != second_entry->modReference,
              "mod_references_differ");
    }

    // 第二个 Mod 的脚本执行失败**不关掉**状态：第三个 Mod 之前登记的全局仍在。
    // （这条与"追加路径失败不 ResetAfterFailure"是同一条语义。）
    const auto broken = load("this is not lua\n", 16,
                             "@rom:/isaac_mods/mods/Broken/main.lua",
                             "rom:/isaac_mods/mods/Broken");
    Check(broken != LuaRuntime::LuaInitResult::Success, "broken_mod_reports_failure");
    double ticksAfter = 0.0;
    Check(LuaRuntime::ReadLuaGlobalNumber("A_TICKS", &ticksAfter) && ticksAfter == 1.0,
          "state_survives_a_broken_mod");
    LuaRuntime::DispatchPostUpdate();
    Check(LuaRuntime::ReadLuaGlobalNumber("A_TICKS", &ticksAfter) && ticksAfter == 2.0,
          "callbacks_still_dispatch_after_a_broken_mod");

    if (g_Failures == 0) {
        std::printf("MULTIMOD_OK registrations=%zu ticks_a=%.0f ticks_b=%.0f ticks_c=%.0f\n",
                    registry.Count(), ticksA, ticksB, ticksC);
    }
    return g_Failures == 0 ? 0 : 1;
}
'''


def host_compilers() -> tuple[str, str] | None:
    """(C 编译器, C++ 编译器)；缺任何一个就整体 skip，不伪装成通过。"""
    cc = shutil.which("cc")
    if cc is None:
        return None
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return cc, found
    return None


class MultiModLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-multimod-")
        temporary = Path(cls.temporary.name)
        # 与其它 Lua 运行时宿主用例同一套桩：`stdfloat` 兼容头 + 本仓库自带的 Lua 5.3.3。
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "multimod_harness.cpp"
        harness.write_text(HARNESS.lstrip(), encoding="utf-8")
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                [cc, "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "multimod_harness"
        build = subprocess.run(
            [cxx, "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
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
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_three_mods_share_one_state_and_dispatch_together(self):
        result = subprocess.run([str(self.harness)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MULTIMOD_OK registrations=3", result.stdout)
        self.assertNotIn("MULTIMOD_FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
