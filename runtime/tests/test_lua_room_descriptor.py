"""`Level:GetCurrentRoomDesc` / `GetRoomByIdx` / `GetRooms` 的宿主机行为。

## 这一批的字段偏移从哪来

**不是从 PC 版抄的**：来自 `tools/layout_tables/room_descriptor.json`，每条都带可复核证据
（反汇编里的偏移/常量，或**真机行为观察**）。真机上验证过的几条：

| 字段 | 偏移 | 真机怎么验证的 |
| --- | --- | --- |
| `GridIndex` | `+0x00` | 起始房间（网格正中 84）读到 84；走进宝箱房变成 97 |
| `SafeGridIndex` | `+0x04` | 与上同房间同行（普通房里两者相等） |
| `ListIndex` | `+0x08` | 11 个已分配槽位的值恰好是 0..10 |
| `Data` | `+0x10` | 指向房间配置；`Data+0x8` 读出的类型构成一层合理布局（宝藏 4/商店 7/恶魔 10/BOSS 5） |
| `VisitedCount` | `+0x4c` | 首次进房 0→1；回到起始房间 1→2（计数而非布尔） |
| `Clear` | `+0x50` | 清掉敌人瞬间 0→1，其它字段不动 |

描述符数组**内联在 Level 对象里**：基址 `Level+0x18`、步长 `0x100`、个数在 `Level+0x21510`。

## 这份宿主机测试钉住什么

伪造同一套内存布局（模块槽 → owner → Game），于是**读取链写错就会红**。重点覆盖：

1. 解析链：`GameOwnerSlot()` 是**槽的地址**，要解两次才到 `Game*`（第一版少解两层，
   真机探针上踩过同一个坑）；
2. 每个字段读的是它自己的偏移（逐个钉住，避免"整批偏移错位"这种看不出异常的错）；
3. `GetRoomByIdx(-1)` = 当前房间、越界与负数（非 -1）给 nil；
4. `GetRooms().Size` 与 `:Get(i)`（含越界给 nil）；
5. 非受管回调作用域里调用必须报错。
"""

import json
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

HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"
#include "room_descriptor_layout.hpp"

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace {

// ---- 伪造引擎内存 -----------------------------------------------------------
//
// 解析链（与实现、与真机探针完全一致，**两解**）：
//   `GameOwnerSlot()`（= 模块映像 + kGameOwnerGlobalSlotOffset，是**槽的地址**）
//     → `*(槽)` = owner → `*(owner)` = Game*（= Level*）
// 真机实测同形：`g_Game` 槽内容 ≠ 对象本身，还要再解一层。
// 第一版假内存少铺了一层，于是实现返回 0（"读不到 Level"）—— 补上这一层正是本测试的价值。
// 第一版实现把"槽的地址"当成 Level 用，少解了两层；这里把链铺成真机的样子，
// 于是那种错误会直接让断言红，而不是在设备上才暴露。
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_OwnerBytes;        // 槽 → owner
std::vector<unsigned char> g_GamePointerBytes;  // owner 指向的那个字（里面才是 Game*）
std::vector<unsigned char> g_LevelBytes;
std::vector<unsigned char> g_ConfigBytes;

constexpr std::uint32_t kCurrentIndex = 84;
constexpr std::uint32_t kCurrentDimension = 0;
constexpr std::uint32_t kRooms[3] = {84, 97, 71};      // 起始房间 / 宝箱房 / 普通房
constexpr std::uint32_t kTypes[3] = {1, 4, 1};         // ROOM_DEFAULT / ROOM_TREASURE / ROOM_DEFAULT
constexpr std::uint32_t kClearFlag[3] = {1, 0, 0};     // 起始房间本来就没敌人（真机实测 = 1）
constexpr std::uint32_t kVisited[3] = {2, 1, 1};
constexpr std::uint32_t kArrayBase = 0x18;
constexpr std::uint32_t kStride = 0x100;

template <typename T>
void WriteAt(std::vector<unsigned char>& block, std::size_t offset, T value) {
    std::memcpy(block.data() + offset, &value, sizeof(T));
}

void WriteWord(std::vector<unsigned char>& block, std::size_t offset, std::uint64_t value) {
    WriteAt(block, offset, value);
}

std::uintptr_t AddressOf(const std::vector<unsigned char>& block) {
    return reinterpret_cast<std::uintptr_t>(block.data());
}

// 描述符数组**内联在 Level 对象里**（真机布局就是这样：基址 Level+0x18、步长 0x100），
// 所以字段要写进 Level 块本身 —— 第一版写到一个独立块里，于是实现找不到描述符。

void PublishFakeEngine() {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_OwnerBytes.assign(sizeof(std::uint64_t), 0);
    g_GamePointerBytes.assign(sizeof(std::uint64_t), 0);
    // Level 对象要覆盖到描述符数组、当前索引/维度、以及元素个数所在的 +0x21510。
    g_LevelBytes.assign(0x21520, 0);
    g_ConfigBytes.assign(0x20, 0);

    // 两条取 Game 的路径都要自洽（真机上它们是同一个槽地址）：
    //   ① `*(模块 + 偏移)` → owner → `*(owner)` → Game（`ReadGameCurses` 那条链）
    //   ② `GameOwnerSlot()` → `*(槽)` → owner → Game（`ObserveLevelIsAscent` 那条链）
    WriteWord(g_ModuleBytes, kGameOwnerGlobalSlotOffset, AddressOf(g_GamePointerBytes));
    WriteWord(g_OwnerBytes, 0, AddressOf(g_GamePointerBytes));
    WriteWord(g_GamePointerBytes, 0, AddressOf(g_LevelBytes));
    WriteAt<std::uint32_t>(g_LevelBytes, 0x21510, 3);          // 元素个数
    WriteAt<std::uint32_t>(g_LevelBytes, kLevelCurrentRoomIndexOffset, kCurrentIndex);
    WriteAt<std::uint32_t>(g_LevelBytes, kLevelCurrentRoomDimensionOffset, kCurrentDimension);
    for (std::size_t slot = 0; slot < 3; ++slot) {
        const std::uintptr_t base = kArrayBase + slot * kStride;   // 相对 Level 块
        WriteAt<std::uint32_t>(g_LevelBytes, base + 0x00, kRooms[slot]);
        WriteAt<std::uint32_t>(g_LevelBytes, base + 0x04, kRooms[slot]);
        WriteAt<std::uint32_t>(g_LevelBytes, base + 0x08, static_cast<std::uint32_t>(slot));
        WriteWord(g_LevelBytes, base + 0x10, AddressOf(g_ConfigBytes) + slot * 0x08);
        WriteAt<std::uint32_t>(g_LevelBytes, base + 0x4C, kVisited[slot]);
        WriteAt<std::uint32_t>(g_LevelBytes, base + 0x50, kClearFlag[slot]);
        WriteAt<std::uint32_t>(g_ConfigBytes, slot * 0x08 + 0x08, kTypes[slot]);
    }
    LuaRuntime::SetEngineModuleBase(AddressOf(g_ModuleBytes));
}

}  // namespace

// 覆盖共享默认桩：`Level:GetStage` 等既有 API 在本次测试里不需要真值，保持"读不到"。

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    PublishFakeEngine();
    LuaRuntime::SetGameBindings(AddressOf(g_OwnerBytes), 0x2222);

    const char* script = nullptr;
    if (std::strcmp(scenario, "top_level") == 0) {
        script = "Game():GetLevel():GetCurrentRoomDesc()";
    } else if (std::strcmp(scenario, "fields") == 0) {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local d=Game():GetLevel():GetCurrentRoomDesc()"
            " print('GRID='..tostring(d.GridIndex))"
            " print('SAFE='..tostring(d.SafeGridIndex))"
            " print('LIST='..tostring(d.ListIndex))"
            " print('VISITED='..tostring(d.VisitedCount))"
            " print('CLEAR='..tostring(d.Clear))"
            " print('TYPE='..tostring(d.Data.Type))"
            " end)";
    } else if (std::strcmp(scenario, "by_index") == 0) {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local level=Game():GetLevel()"
            " print('TREASURE='..tostring(level:GetRoomByIdx(97).Data.Type))"
            " print('CURRENT_CLEAR='..tostring(level:GetRoomByIdx(-1).Clear))"
            " print('MISSING='..tostring(level:GetRoomByIdx(999)))"
            " print('NEGATIVE='..tostring(level:GetRoomByIdx(-2)))"
            " end)";
    } else {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local rooms=Game():GetLevel():GetRooms()"
            " print('SIZE='..tostring(rooms.Size))"
            " for i=0,rooms.Size-1 do"
            "   local ok, room = pcall(function() return rooms:Get(i) end)"
            "   if not ok then print('GETERR'..i..'='..tostring(room))"
            "   else"
            "     print('ROOM'..i..'='..tostring(room and room.GridIndex))"
            "   end"
            " end"
            " print('OUT='..tostring(rooms:Get(99)))"
            " end)";
    }

    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@roomdesc.lua");
    if (std::strcmp(scenario, "top_level") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    if (LuaRuntime::TakeCallbackError()) {
        // 失败时把 Lua 错误文本整段打出来 —— 否则宿主机只能看到"回调里出错了"，
        // 又要靠猜（这条通道与真机探针读的是同一份文本）。
        char buffer[512] = {};
        const std::size_t length = LuaRuntime::CopyLastLuaErrorText(buffer, sizeof(buffer));
        std::printf("LUA_ERROR(%.*s)\n", static_cast<int>(length), buffer);
        return 4;
    }
    return 0;
}
'''


class LuaRoomDescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-roomdesc-")
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

    def test_each_field_reads_its_own_offset(self):
        """逐个字段钉住偏移 —— 防"整批偏移错位"这种界面上看不出异常的错。"""
        result = self.run_scenario("fields")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("GRID=84", "SAFE=84", "LIST=0", "VISITED=2", "CLEAR=true", "TYPE=1"):
            with self.subTest(expected=expected):
                self.assertIn(expected, result.stdout)

    def test_get_room_by_idx_finds_rooms_and_refuses_what_it_cannot_prove(self):
        """`GetRoomByIdx(97)` 找到宝箱房；`(-1)` 是当前房间；越界与 -2 给 nil。

        `-2`（PC 的"上一个房间"）**没有证据**（负数槽位公式未验证），所以必须给 nil
        而不是编一个房间 —— 给错房间比给 nil 更糟。
        """
        result = self.run_scenario("by_index")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TREASURE=4", result.stdout)
        self.assertIn("CURRENT_CLEAR=true", result.stdout)
        self.assertIn("MISSING=nil", result.stdout)
        self.assertIn("NEGATIVE=nil", result.stdout)

    def test_get_rooms_exposes_size_and_indexed_access(self):
        """`GetRooms()` 要有 `.Size` 与 `:Get(i)`，越界给 nil（EID 就是这么遍历的）。"""
        result = self.run_scenario("rooms")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SIZE=3", result.stdout)
        self.assertIn("ROOM0=84", result.stdout)
        self.assertIn("ROOM1=97", result.stdout)
        self.assertIn("ROOM2=71", result.stdout)
        self.assertIn("OUT=nil", result.stdout)

    def test_outside_a_managed_callback_the_apis_refuse_to_run(self):
        result = self.run_scenario("top_level")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
