"""批次 13（2026-09-16）：`GridEntity:GetRNG()` / `RNG:GetSeed()` / `ItemPool:IsPillIdentified()`。

## 这一批补的是什么

`tools/eid_api_gap_report.py` 的高置信缺口表里**最后剩下的两条**（补完这一批，表里 ① 类归零）：

| API | 底座 | 依据 |
| --- | --- | --- |
| `GridEntity:GetRNG()` | 字段读 `GridEntity + 0x30`（16 字节的 `RNG`） | `GridEntity::hurt_func @ 0x37E928` 的 `add x0, x19, #0x30; bl RNG::Next`（x19 就是 `this`）；`GridEntity_Spikes::InitSubclass @ 0x393CB8` 的 `add x0, x19, #0x30; RNG::SetSeed(…, 35)` |
| `RNG:GetSeed()` | RNG 对象头 4 字节 | `RNG::SetSeed(uint, uint) @ 0x44E3C0` 第一条存值就是 `str w1, [x0]` |
| `ItemPool:IsPillIdentified(color)` | 字段读 `ItemPool + 0xa68 + color` | HUD 用它决定显示药丸真名还是 `#QUESTION_MARKS_NAME`；`ItemPool::RestoreGameState` 与 `ItemPool::RerollPillEffect` 都写它 |

EID 的调用点是 `features/eid_itemprediction.lua:120`（`spikes:GetRNG():GetSeed()`）、
`main.lua:1649` / `features/eid_itemprediction.lua:339` / `features/eid_holdmapdesc.lua:575`
（`pool:IsPillIdentified(color)`）。

## 宿主机能测什么、不能测什么

宿主机**没有引擎**，所以"`GridEntity + 0x30` 在真机上确实是那个 RNG"这一层只能由真机验收；
这里钉住的是**我们这个实现自己的可判定性质**：

1. **读的确实是那几个字节**：伪造 `GridEntity` 与 `ItemPool`，写死值，断言 Lua 侧读到的就是它；
   换一个值再读一次必须跟着变（排除"读到常量"的假通过）。
2. **`GetSeed()` 读的是 RNG 头 4 字节**，而且 `SetSeed` 写进去的种子能读回来（同一份句柄内的往返）。
3. **颜色掩码与越界口径**：`PILL_GIANT_FLAG`（0x800）置位的颜色按本色回答；掩码后 ≥ 15、
   负数一律 `false`（不抛错 —— 抛错会摘掉 EID 整条描述回调）。
4. **★ 快照语义（已登记的偏离，这里把它钉成断言）**：从 `GetRNG()` 拿到的对象上调用 `Next()`
   之后，**引擎那块内存必须一个字节都没动**。这一条正是 `api_deviation.cpp` 里 `0x0E010054`
   那条 `Partial` 偏离的可执行版本 —— 偏离被明说，也被测到，不是"悄悄换掉"。
5. **作用域约束**：不在受管回调里（Mod 脚本顶层）调用必须报错；读不到 `ItemPool` 时也必须报错，
   **不得**返回编造的值。
"""

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

#: 伪造 `GridEntity + 0x30` 的那个 RNG 的种子（两端场景各一个，用来排除常量）。
SEED_FIRST = 0x12345678
SEED_SECOND = 0x0BADF00D

HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {

// --- 伪造引擎内存 -----------------------------------------------------------
//
// 三级链与其它场景一致：`Module + kGameOwnerGlobalSlotOffset` 是**指针变量**（指向 ownerSlot），
// `*(ownerSlot)` 才是 `Game*`。`ItemPool` 是 `Game` 里的**内嵌对象**（`Game + 0x242c0`，
// 见 `game_observer.cpp` 的 `kGameItemPoolOffset`），所以伪造块要覆盖到
// `0x242c0 + 0xa68 + 15` 那一带。
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_OwnerSlotBytes;
std::vector<unsigned char> g_GameBytes;
std::vector<unsigned char> g_RoomBytes;
std::vector<unsigned char> g_GridEntityBytes;

constexpr std::uint32_t kRoomType = 4;   // ROOM_TREASURE
constexpr std::uint32_t kGridIndex = 5;
constexpr std::uint64_t kItemPoolInGameOffset = 0x242C0;

template <typename T>
void WriteAt(std::vector<unsigned char>& block, std::size_t offset, T value) {
    std::memcpy(block.data() + offset, &value, sizeof(T));
}

std::uintptr_t AddressOf(const std::vector<unsigned char>& block) {
    return reinterpret_cast<std::uintptr_t>(block.data());
}

std::uint32_t ReadSeedAt(const std::vector<unsigned char>& block, std::size_t offset) {
    std::uint32_t value = 0;
    std::memcpy(&value, block.data() + offset, sizeof(value));
    return value;
}

// --- 宿主版"引擎 RNG 方法" ---------------------------------------------------
//
// 只实现 RNG 句柄自己用得到的那部分语义：`SetSeed` 写种子 + 移位三元组（与
// `RNG::SetSeed @ 0x44E3C0` 的存值形态同构），`Next` 原地推进种子。
std::uint32_t g_NextCalls = 0;

void HostRngSetSeed(void* storage, std::uint32_t seed, std::uint32_t shift) {
    auto* bytes = static_cast<unsigned char*>(storage);
    std::memcpy(bytes, &seed, sizeof(seed));
    const std::uint32_t shifts[3] = {shift, shift + 1, shift + 2};
    std::memcpy(bytes + 4, shifts, sizeof(shifts));
}

void HostRngNext(void* storage) {
    auto* bytes = static_cast<unsigned char*>(storage);
    std::uint32_t seed = 0;
    std::memcpy(&seed, bytes, sizeof(seed));
    seed = seed * 7u + 1u;
    std::memcpy(bytes, &seed, sizeof(seed));
    ++g_NextCalls;
}

void PublishFakeEngine(std::uint32_t seed, bool withOwner) {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_OwnerSlotBytes.assign(sizeof(std::uint64_t), 0);
    g_GameBytes.assign(kItemPoolInGameOffset + kItemPoolPillIdentifiedOffset +
                           kPillColorCount + 16,
                       0);
    g_RoomBytes.assign(kRoomGridEntityTableOffset + (kGridIndex + 2) * sizeof(std::uintptr_t), 0);
    g_GridEntityBytes.assign(kGridEntityRngOffset + kRngObjectSize, 0);

    // 网格实体：variant 1000、type 7，自己的 RNG 放在 `+0x30`（种子由场景给定）。
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityVariantOffset, 1000u);
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityTypeOffset, 7u);
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityRngOffset, seed);
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityRngOffset + 4, 0x35u);
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityRngOffset + 8, 0x36u);
    WriteAt<std::uint32_t>(g_GridEntityBytes, kGridEntityRngOffset + 12, 0x37u);

    // 药丸"已识别"表：只有颜色 3 认得了（其余保持 0）。
    WriteAt<std::uint8_t>(g_GameBytes,
                          kItemPoolInGameOffset +
                              kItemPoolPillIdentifiedOffset + 3,
                          static_cast<std::uint8_t>(1));

    WriteAt<std::uint32_t>(g_RoomBytes, 0x10, kRoomType);
    WriteAt<std::uintptr_t>(g_RoomBytes,
                            kRoomGridEntityTableOffset + kGridIndex * sizeof(std::uintptr_t),
                            AddressOf(g_GridEntityBytes));
    WriteAt<std::uintptr_t>(g_GameBytes, kGameRoomPointerOffset, AddressOf(g_RoomBytes));
    WriteAt<std::uintptr_t>(g_ModuleBytes, kGameOwnerGlobalSlotOffset, AddressOf(g_OwnerSlotBytes));
    if (withOwner) {
        WriteAt<std::uintptr_t>(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
    }
    LuaRuntime::SetEngineModuleBase(AddressOf(g_ModuleBytes));
}

}  // namespace

// --- 本场景覆盖的默认桩 -----------------------------------------------------
//
// `no_owner` 场景把 owner 槽的内容留成 0 ⇒ 这个桩按引擎口径返回"读不到 Game"，
// 用来验证 `IsPillIdentified` 是**报错**而不是编一个 `false`。
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t ownerSlot, void** itemPool) {
    if (itemPool == nullptr) return GameItemPoolObservation::ItemPoolUnreadable;
    if (ownerSlot == 0) return GameItemPoolObservation::OwnerUnreadable;
    const auto owner = *reinterpret_cast<const std::uintptr_t*>(ownerSlot);
    if (owner == 0) return GameItemPoolObservation::OwnerUnreadable;
    *itemPool = reinterpret_cast<void*>(AddressOf(g_GameBytes) + kItemPoolInGameOffset);
    return GameItemPoolObservation::Success;
}
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void** room) {
    if (room == nullptr) return GameRoomObservation::RoomNull;
    *room = reinterpret_cast<void*>(AddressOf(g_RoomBytes));
    return GameRoomObservation::Success;
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    const bool secondSeed = std::strcmp(scenario, "seed_second") == 0;
    const bool noOwner = std::strcmp(scenario, "no_owner") == 0;

    PublishFakeEngine(secondSeed ? 0x0BADF00Du : 0x12345678u, !noOwner);
    LuaRuntime::SetRngBindings(reinterpret_cast<uintptr_t>(&HostRngSetSeed),
                              reinterpret_cast<uintptr_t>(&HostRngNext));

    const char* script = nullptr;
    if (std::strcmp(scenario, "top_level") == 0) {
        // 顶层调用：`GetRNG` 与 `IsPillIdentified` 都必须拒绝（"only available during a callback"）。
        script = "Game():GetItemPool():IsPillIdentified(3)";
    } else if (std::strcmp(scenario, "top_level_grid") == 0) {
        script = "Game():GetRoom():GetGridEntity(5):GetRNG()";
    } else if (noOwner) {
        // 读不到 ItemPool ⇒ 必须报错；`GetRNG` 只依赖句柄自己的地址，仍然要能用。
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local ok=pcall(function() return Game():GetItemPool():IsPillIdentified(3) end)"
            " if ok then error('IsPillIdentified must fail without an ItemPool') end"
            " print('NOOWNER_OK')"
            " end)";
    } else {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local grid=Game():GetRoom():GetGridEntity(5)"
            " if grid==nil then error('grid entity must exist') end"
            " local rng=grid:GetRNG()"
            " if rng==nil then error('GetRNG must return an RNG') end"
            " local seed=rng:GetSeed()"
            " print('SEED='..tostring(seed))"
            " if type(seed)~='number' then error('GetSeed must be a number') end"
            " local advanced=rng:Next()"
            " if advanced==seed then error('Next must advance the snapshot') end"
            " if rng:GetSeed()==seed then error('the snapshot seed must change after Next') end"
            " local own=RNG()"
            " own:SetSeed(4242,35)"
            " if own:GetSeed()~=4242 then error('GetSeed must read back SetSeed, got '"
            " ..tostring(own:GetSeed())) end"
            " local pool=Game():GetItemPool()"
            " local identified=pool:IsPillIdentified(3)"
            " local plain=pool:IsPillIdentified(4)"
            " local empty=pool:IsPillIdentified(0)"
            " local giant=pool:IsPillIdentified(3+2048)"
            " local outOfRange=pool:IsPillIdentified(15)"
            " local maskedOutOfRange=pool:IsPillIdentified(2047)"
            " local negative=pool:IsPillIdentified(-1)"
            " print('PILLS='..tostring(identified)..','..tostring(plain)..','..tostring(empty)"
            " ..','..tostring(giant)..','..tostring(outOfRange)..','"
            " ..tostring(maskedOutOfRange)..','..tostring(negative))"
            " if identified~=true then error('colour 3 must be identified') end"
            " if plain~=false then error('colour 4 must be unidentified') end"
            " if empty~=false then error('colour 0 must be unidentified') end"
            " if giant~=true then error('PILL_GIANT_FLAG must mask down to colour 3') end"
            " if outOfRange~=false then error('out of range colour must be false') end"
            " if maskedOutOfRange~=false then error('masked out of range must be false') end"
            " if negative~=false then error('negative colour must be false') end"
            " end)";
    }

    LuaRuntime::SetGameBindings(AddressOf(g_OwnerSlotBytes), 0x2222);
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@grid13.lua");
    if (std::strcmp(scenario, "top_level") == 0 ||
        std::strcmp(scenario, "top_level_grid") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    if (noOwner) {
        // `no_owner` 里那次失败调用被脚本自己的 `pcall` 接住了 ⇒ 不该留下"未捕获错误"记录；
        // 而 `NOOWNER_OK` 由脚本末尾打印，Python 侧据此确认"后面几步真的跑到了"。
        return LuaRuntime::TakeCallbackError() ? 3 : 0;
    }
    if (LuaRuntime::TakeCallbackError()) {
        char text[256] = {0};
        static_cast<void>(LuaRuntime::CopyLastLuaErrorText(text, sizeof(text)));
        std::printf("LUA_ERROR=%s\n", text);
        return 4;
    }
    // ★ 快照语义的可执行判据：引擎那块内存里的种子必须还是原值（Lua 侧已经对它 `Next()` 过一次）。
    const std::uint32_t engineSeed = ReadSeedAt(g_GridEntityBytes, kGridEntityRngOffset);
    const std::uint32_t expected = secondSeed ? 0x0BADF00Du : 0x12345678u;
    std::printf("ENGINE_SEED=%u NEXT_CALLS=%u\n", engineSeed, g_NextCalls);
    return engineSeed == expected ? 0 : 5;
}
'''

SEED_PATTERN = "SEED="
ENGINE_SEED_PATTERN = "ENGINE_SEED="


class LuaGridRngPillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-grid13-")
        cls.binary = build_lua_harness(
            source_root=SOURCE,
            workdir=Path(cls.temporary.name) / "harness",
            harness_source=HARNESS,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.binary), scenario], text=True, capture_output=True)

    def test_get_rng_reads_the_engine_rng_of_that_grid_entity(self):
        """`GetRNG():GetSeed()` 读的就是 `GridEntity + 0x30` 那个种子的值。"""
        result = self.run_scenario("seed_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{SEED_PATTERN}{SEED_FIRST}", result.stdout)

    def test_get_rng_follows_the_field_when_it_changes(self):
        """换一个种子必须跟着变 —— 排除"读到常量就通过"的假通过。"""
        result = self.run_scenario("seed_second")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{SEED_PATTERN}{SEED_SECOND}", result.stdout)

    def test_the_returned_rng_is_a_snapshot_not_the_engine_object(self):
        """★ 已登记偏离的可执行版本：在返回的 RNG 上 `Next()` 不会动引擎内存。

        `api_deviation.cpp` 里 `0x0E010054` 那条记的是"PC 返回引用、我们交回快照"。
        这条测试让那份偏离**可复核**：harness 在派发之后回读引擎那块 16 字节，必须还是原值
        （同时 `NEXT_CALLS` 证明 `Next()` 真的被调用过，不是"没调用所以没变"）。
        """
        result = self.run_scenario("seed_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{ENGINE_SEED_PATTERN}{SEED_FIRST}", result.stdout)
        self.assertIn("NEXT_CALLS=1", result.stdout)

    def test_pill_identified_reads_the_per_colour_byte(self):
        """颜色 3 认得了、颜色 4/0 没有；巨大药丸标志按本色回答；越界与负数一律 `false`。

        这七个值一次性打在 `PILLS=` 行上（顺序 = 3 / 4 / 0 / 3|0x800 / 15 / 2047 / -1），
        边界口径因此和断言写在一起、失败时能直接看见。
        """
        result = self.run_scenario("seed_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PILLS=true,false,false,true,false,false,false", result.stdout)

    def test_missing_item_pool_reports_an_error_instead_of_a_value(self):
        """读不到 `ItemPool` 时必须报错，不能编一个 `false`（编了会让 EID 以为"药丸没识别过"）。"""
        result = self.run_scenario("no_owner")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 脚本自己用 `pcall` 断言"这次调用确实失败"，并在末尾打标记证明后面几步跑到了。
        self.assertIn("NOOWNER_OK", result.stdout)

    def test_outside_a_managed_callback_the_apis_refuse_to_run(self):
        """Mod 脚本顶层调用必须失败（与其它族同一口径）。"""
        for scenario in ("top_level", "top_level_grid"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
