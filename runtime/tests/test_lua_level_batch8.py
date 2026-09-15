"""批次 8（2026-09-15）：`Level` 家族四个新成员的宿主机行为。

## 这一批补的是什么

`tools/eid_api_gap_report.py` 的高置信缺口表里，EID 用得到、而我们此前**整条缺失**的四个
`Level` 成员（EID 在描述构建路径上无条件调用，缺一个就是 "attempt to call a nil value"、
整条描述回调在那一行中断）：

| API | 底座 | 依据 |
| --- | --- | --- |
| `Level:GetCurrentRoomIndex()` | 字段读 `Level + 0x21558` | 固定 NRO 里 `Level::GetCurrentRoomDesc @ 0x3DC684` 把该字段当 `Level::GetRoomByIdx(int,int)` 的第一个实参 |
| `Level:GetCurrentRoom()` | `Game + 0x21550`（与 `Game:GetRoom` 同一个对象） | 同一条已上机验证的读取链 |
| `Level:GetAbsoluteStage()` | 引擎方法 @ `0x3E7F3C` | `docs/PC-Lua-API-对照清单.md` 的 `missing_easy` 表（§3.3 已证明 `file_offset` == 运行时模块偏移） |
| `Level:IsNextStageAvailable()` | 引擎方法 @ `0x3DBDBC` | 同上 |

## 宿主机能测什么、不能测什么（口径写清楚，不许含糊）

宿主机**没有引擎**，所以两个引擎方法的**返回值**无法在宿主上验证（真机才有 `Repentance.nro`
的那两条指令）。宿主上能钉住的是它们**可判定的部分**：

1. **字段读的正确性**（`GetCurrentRoomIndex`）：伪造 `Module → ownerSlot → Game` 三级内存，
   往 `Game + 0x21558` 写一个值，断言 Lua 侧读到的就是它；换一个值再读一次，断言跟着变
   （排除"读到常量"的假通过）。
2. **`GetCurrentRoom` 与 `Game:GetRoom` 是同一个对象**：两者都必须能读到同一份伪造 `Room`，
   并且 `Room:GetType()`（读 `Room + 0x10`）给出一致的类型 —— 这条把"PC 语义上这两个是同一个
   房间对象"钉成断言，而不是靠注释声明。
3. **两个引擎方法的失败面（这是安全属性，必须宿主可判定）**：
   * 绑定为 0（安装期守卫没过）⇒ 报 Lua 错误，**不得**返回编造的值；
   * 绑定指向一段**守卫不符**的地址 ⇒ 同样报 Lua 错误（handler 调用前再验一次 16 字节守卫，
     与 `CallGameCurseAccessor` 同一口径：绝不调用没校验过的地址）；
   * 换一个**守卫相符但返回值不同**的假方法在宿主上不可构造（宿主的函数首 16 字节不可能等于
     那串 AArch64 指令），所以"调用确实发生、值来自引擎"这一层留给真机验收 ——
     **本文件不假装测过它**。
4. **作用域约束**：不在受管回调作用域内（Mod 脚本顶层）调用这四个 API 必须报错。
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

HARNESS = r'''
#include "lua_runtime.hpp"
#include "room_layout.hpp"
#include "game_observer.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstdint>
#include <cstring>
#include <vector>

namespace {

// --- 伪造引擎内存 -----------------------------------------------------------
//
// 三级链：`Module + kGameOwnerGlobalSlotOffset` 是**指针变量**（指向 ownerSlot），
// `*(ownerSlot)` 才是 `Game*`。三个块的分工与真机一致，尺寸按要读的字段取。
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_OwnerSlotBytes;
std::vector<unsigned char> g_GameBytes;
std::vector<unsigned char> g_RoomBytes;

constexpr std::uint32_t kRoomType = 4;      // ROOM_TREASURE
constexpr std::uint32_t kRoomTypeOffset = 0x10;

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

// 宿主版"引擎函数"的前置声明（定义在文件末尾，注入点在中间）。
void HostGetRenderPosition(const float* input, float* output, bool useCamera);

void PublishFakeEngine() {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_OwnerSlotBytes.assign(sizeof(std::uint64_t), 0);
    // 覆盖到维度字段（`+0x21560`）。
    // 覆盖到"世界→屏幕"调整量（`Game + 0x24F9B0` 起 8 字节）—— `Room:WorldToScreenPosition` 读它。
    g_GameBytes.assign(kGameToScreenAdjustOffset + sizeof(float) * 2, 0);
    WriteAt<float>(g_GameBytes, kGameToScreenAdjustOffset, 3.0F);
    WriteAt<float>(g_GameBytes, kGameToScreenAdjustOffset + sizeof(float), 4.0F);
    // 覆盖到渲染滚动偏移（`+0x1938` 起 16 字节）—— `Room:GetRenderScrollOffset()` 读它。
    g_RoomBytes.assign(isaac::runtime::layout::kRoomRenderScrollOffsetOffset + isaac::runtime::layout::kRoomRenderScrollOffsetWidth, 0);
    // 值刻意非零、X/Y 不同：证明读的是这两个 float，而不是"返回一个空向量"。
    WriteAt<float>(g_RoomBytes, isaac::runtime::layout::kRoomRenderScrollOffsetOffset, 12.5F);
    WriteAt<float>(g_RoomBytes, isaac::runtime::layout::kRoomRenderScrollOffsetOffset + sizeof(float), -7.25F);
    WriteWord(g_ModuleBytes, kGameOwnerGlobalSlotOffset, AddressOf(g_OwnerSlotBytes));
    WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
    WriteWord(g_GameBytes, kGameRoomPointerOffset, AddressOf(g_RoomBytes));
    WriteAt<std::uint32_t>(g_RoomBytes, kRoomTypeOffset, kRoomType);
    LuaRuntime::SetEngineModuleBase(AddressOf(g_ModuleBytes));
    LuaRuntime::SetGetRenderPositionHostFunction(&HostGetRenderPosition);
}

// 宿主版"引擎函数"：把世界坐标平移 (1000, 2000)，好让断言能分开"引擎算的"与"我们加的"两段。
// 设备侧这条是引擎的 `GetRenderPosition`（PLT 桩 + 16 字节入口守卫），宿主上由注入实现替代。
void HostGetRenderPosition(const float* input, float* output, bool useCamera) {
    output[0] = input[0] + 1000.0F;
    output[1] = input[1] + 2000.0F;
    static_cast<void>(useCamera);
}

}  // namespace

// --- 本场景**覆盖**的默认桩 -------------------------------------------------
//
// 其余观测函数（暂停、阶段、贪婪模式、IsAscent、道具池…）用共享脚手架里的弱默认桩，
// 默认一律"读不到"；这里只覆盖这个场景需要的两个，而且**不写 weak**：强符号优先。
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void** room) {
    if (room == nullptr) return GameRoomObservation::RoomNull;
    *room = reinterpret_cast<void*>(AddressOf(g_RoomBytes));
    return GameRoomObservation::Success;
}
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t* roomType) {
    if (roomType == nullptr) return GameRoomObservation::RoomNull;
    *roomType = kRoomType;
    return GameRoomObservation::Success;
}

// "守卫不符"的假方法地址：宿主的普通函数，首 16 字节不可能等于引擎那串 AArch64 指令。
int FakeMethodThatMustNotBeCalled(const void*) {
    return 4242;
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];

    PublishFakeEngine();
    const std::uint32_t index = std::strcmp(scenario, "index_second") == 0 ? 77u : 42u;
    WriteAt<std::uint32_t>(g_GameBytes, kLevelCurrentRoomIndexOffset, index);

    // 引擎方法的绑定：默认**不发布**（0 = 不可用）；`guard_mismatch` 场景发布一个守卫不符的地址。
    if (std::strcmp(scenario, "guard_mismatch") == 0) {
        LuaRuntime::SetLevelGetAbsoluteStageBinding(
            reinterpret_cast<std::uintptr_t>(&FakeMethodThatMustNotBeCalled));
        LuaRuntime::SetLevelIsNextStageAvailableBinding(
            reinterpret_cast<std::uintptr_t>(&FakeMethodThatMustNotBeCalled));
    }

    const char* script = nullptr;
    if (std::strcmp(scenario, "top_level") == 0) {
        script = "Game():GetLevel():GetCurrentRoomIndex()";
    } else if (std::strcmp(scenario, "absolute_stage") == 0) {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " if Game():GetLevel():GetAbsoluteStage()~=nil then error('expected an error') end end)";
    } else if (std::strcmp(scenario, "next_stage") == 0) {
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " if Game():GetLevel():IsNextStageAvailable()~=nil then error('expected an error') end end)";
    } else {
        // `guard_mismatch` / 默认：四个 API 都要能用（两个引擎方法在 guard_mismatch 下必须报错）。
        script =
            "local mod=RegisterMod('Probe',1);"
            " mod:AddCallback(ModCallbacks.MC_PRE_GET_COLLECTIBLE,function()"
            " local level=Game():GetLevel()"
            " local index=level:GetCurrentRoomIndex()"
            " local room=level:GetCurrentRoom()"
            " local gameRoom=Game():GetRoom()"
            " if type(index)~='number' then error('index must be a number') end"
            " if room:GetType()~=gameRoom:GetType() then error('GetCurrentRoom must be the Game room') end"
            " if room:GetType()~=4 then error('room type must come from Room+0x10') end"
            " local scroll=room:GetRenderScrollOffset()"
            " if math.abs(scroll.X-12.5)>0.001 or math.abs(scroll.Y+7.25)>0.001 then"
            " error('GetRenderScrollOffset must read Room+0x1938, got '..scroll.X..','..scroll.Y) end"
            " local again=room:GetRenderScrollOffset()"
            " again.X=999"
            " if math.abs(room:GetRenderScrollOffset().X-12.5)>0.001 then"
            " error('GetRenderScrollOffset must return a fresh Vector') end"
            " local screen=room:WorldToScreenPosition(Vector(80,280))"
            " if math.abs(screen.X-(80+1000+12.5+3))>0.001 or"
            " math.abs(screen.Y-(280+2000-7.25+4))>0.001 then"
            " error('WorldToScreenPosition must compose engine+room+game, got '"
            " ..screen.X..','..screen.Y) end"
            " print('INDEX='..tostring(index)..' TYPE='..tostring(room:GetType())"
            " ..' SCROLL='..scroll.X..','..scroll.Y..' SCREEN='..screen.X..','..screen.Y)"
            " end)";
    }

    LuaRuntime::SetGameBindings(AddressOf(g_OwnerSlotBytes), 0x2222);
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@level8.lua");
    if (std::strcmp(scenario, "top_level") == 0) {
        // 顶层调用：脚本本身必须失败（"only available during a Runtime callback"）。
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    LuaRuntime::DispatchPreGetCollectible(nullptr, 0, 0, 1, 0);
    if (std::strcmp(scenario, "absolute_stage") == 0 ||
        std::strcmp(scenario, "next_stage") == 0) {
        // 绑定不可用 ⇒ 回调里那次调用必须抛错并被记下来（返回编造的值就会走到 error('expected an error')）。
        return LuaRuntime::TakeCallbackError() ? 0 : 3;
    }
    if (std::strcmp(scenario, "guard_mismatch") == 0) return 0;
    if (LuaRuntime::TakeCallbackError()) return 4;
    // 读到的索引必须是伪造内存里那个值：从 stdout 的 `INDEX=` 行交给 Python 侧断言。
    return 0;
}
'''

#: harness 打印的 `INDEX=` 行：字段读必须真的来自伪造内存（`Game + 0x21558`）。
INDEX_PATTERN = "INDEX="


class LuaLevelBatch8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 地基一期起：桩与 Lua 目标文件走 `test_support.build_lua_harness`（共享脚手架），
        # 本文件只保留"这个场景特有的东西"——伪造引擎内存的铺法与需要覆盖的桩。
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-level8-")
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

    def test_current_room_index_reads_the_level_field(self):
        """`GetCurrentRoomIndex()` 读的是 `Level + 0x21558`，不是常量、也不是别的字段。"""
        result = self.run_scenario("index_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{INDEX_PATTERN}42", result.stdout)

    def test_current_room_index_follows_the_field_when_it_changes(self):
        """换一个值必须跟着变 —— 排除"读到常量就通过"的假通过。"""
        result = self.run_scenario("index_second")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{INDEX_PATTERN}77", result.stdout)

    def test_current_room_is_the_same_object_as_game_get_room(self):
        """`Level:GetCurrentRoom()` 与 `Game:GetRoom()` 必须是同一个 `Room`（PC 语义）。"""
        result = self.run_scenario("index_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unavailable_engine_method_reports_an_error_instead_of_a_value(self):
        """绑定为 0（守卫没过）时，两个引擎方法必须报 Lua 错误，不能编一个值。

        `Level:GetAbsoluteStage()` 编个 0 会让 Mod 以为"在第一层"，`IsNextStageAvailable()`
        编个 false 会让它以为"没有下一层" —— 两者都会静默改变描述内容，所以宁可报错。
        """
        for scenario in ("absolute_stage", "next_stage"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_guard_mismatch_never_calls_the_address(self):
        """入口守护卫不符时**绝不调用**该地址（与 `CallGameCurseAccessor` 同一口径）。

        harness 里那个假方法会返回 4242；这里只要它被调用过一次（或返回值被交回 Lua），
        测试就必须红。字段读与 `GetCurrentRoom` 不受影响，仍然要正常工作。
        """
        result = self.run_scenario("guard_mismatch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("4242", result.stdout)

    def test_outside_a_managed_callback_the_apis_refuse_to_run(self):
        """Mod 脚本顶层调用必须失败（与 `Level:GetStage`/`GetCurses` 同一口径）。"""
        result = self.run_scenario("top_level")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
