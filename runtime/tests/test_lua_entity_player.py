"""`Isaac.GetPlayer(i)` 与 `Entity`/`EntityPlayer` 只读视图的宿主行为测试。

批次 2 的这两件事都建立在"已真机交叉验证过的引擎偏移"上（见 `runtime_constants.hpp` 的常量表与
探针魔数 `ISAACGP1`），所以设备侧不需要新的证据；需要被证明的是**我们这一侧的指针链、校验与
Lua 视图**对不对。那些正好可以完全在宿主上验证：

1. 用 `malloc` 出来的块**伪造一份引擎内存**（模块块里的 `g_Game` 槽 → 指针变量 → `Game` →
   `players` 向量 → `Entity_Player`），并通过 `SetEngineModuleBase()` 把它当成引擎模块基址发布；
2. 用真实 Lua 脚本断言 `Isaac.GetPlayer(0)` 的字段值（真机实测值：`Type == 1`、`Variant == 0`、
   `SubType == 0`、`PlayerType == 0`、下标 0、位置 `(80.0, 280.0)`）、`Position` 的"新对象"语义、
   `ToPlayer`、`GetPlayerType`、`GetData`、`HasCollectible`；
3. 逐场景断言"拿不到就返回 nil / false"：越界、空指针、vptr 不匹配、槽为空、基址未发布。

`HasCollectible` 的设备实现是一条 `bl base + 0x27D3F4`，宿主上既没有引擎映像也不可能把 C++
函数放进去，所以宿主通过 `SetEntityPlayerHasCollectibleHostImplementation()` 注入那一条调用
（设备构建里不存在这个入口，见 `isaac_api.hpp`）。注入点记录它收到的参数，因此下面这几件事仍然
是**被验证的**，而不是假设的：

  * 传给引擎的第一个参数就是 `players[0]` 的地址（`x0 = this`）；
  * 收藏品 id 原样到达、返回值原样变成 Lua 布尔；
  * **vptr 校验先于调用** —— 引擎内存被改坏之后，那个句柄的 `HasCollectible` 不再产生调用。

最后一条用"两帧"实现：第一帧拿到句柄并正常调用；harness 在派发之间把伪造的 vptr 改坏；第二帧
用同一个句柄访问字段/方法，必须既返回安全值、又不产生新的引擎调用。
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

#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

// Observation seams owned by other families: the Lua runtime links them, so the harness answers
// "unavailable" exactly like the other Lua runtime tests do.
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

const char* kScriptTemplate = R"lua(@SCRIPT@)lua";

// --- 伪造的引擎内存 --------------------------------------------------------
//
// 与真机完全同形：模块块（`base + kGameOwnerGlobalSlotOffset` 是 `g_Game` 的**槽**，槽里存的是
// 指向 .bss 变量的指针）→ 变量里才是 `Game*` → `Game` 里的 `std::vector<Entity_Player*>`
// → 元素指向 `Entity_Player` 对象，对象首字是 vtable 指针。
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_GameBytes;
std::vector<unsigned char> g_PlayerBytes;
std::vector<unsigned char> g_PlayerArrayBytes;
std::vector<unsigned char> g_OwnerSlotBytes;

std::uintptr_t g_EngineBase = 0;

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

// `Isaac.GetPlayer` 用到的偏移都要落在这块里：全局槽在 `0xAAC698`，vptr 期望值在 `0xA37110`。
void CreateBlocks() {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_GameBytes.assign(kGamePlayerArrayEndOffset + sizeof(std::uint64_t), 0);
    g_PlayerBytes.assign(kEntityPlayerSize, 0);
    g_OwnerSlotBytes.assign(sizeof(std::uint64_t), 0);
    g_EngineBase = AddressOf(g_ModuleBytes);
}

// 发布两级链的**第一级**：模块块里的槽 → .bss 变量。
void PublishOwnerSlot(std::uint64_t variableAddress) {
    WriteWord(g_ModuleBytes, kGameOwnerGlobalSlotOffset, variableAddress);
}

// `players` 数组（元素是 `Entity_Player*`）与 `Game` 里的 begin/end。
void PublishPlayers(const std::vector<std::uint64_t>& players) {
    g_PlayerArrayBytes.assign(players.size() * sizeof(std::uint64_t) + sizeof(std::uint64_t), 0);
    for (std::size_t index = 0; index < players.size(); ++index) {
        WriteWord(g_PlayerArrayBytes, index * sizeof(std::uint64_t), players[index]);
    }
    WriteWord(g_GameBytes, kGamePlayerArrayBeginOffset, AddressOf(g_PlayerArrayBytes));
    WriteWord(g_GameBytes, kGamePlayerArrayEndOffset,
              AddressOf(g_PlayerArrayBytes) + players.size() * sizeof(std::uint64_t));
}

// 真机实测值：Type == 1、Variant == 0、SubType == 0、PlayerType == 0、下标 0、位置 (80, 280)。
void FillPlayer(bool validVtable) {
    WriteWord(g_PlayerBytes, 0,
              validVtable ? g_EngineBase + kEntityPlayerVtableOffset : 0x5A5A5A5AULL);
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntityTypeOffset, 1);
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntityVariantOffset, 0);
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntitySubTypeOffset, 0);
    WriteAt<float>(g_PlayerBytes, kEntityPositionOffset, 80.0F);
    WriteAt<float>(g_PlayerBytes, kEntityPositionOffset + sizeof(float), 280.0F);
    WriteAt<float>(g_PlayerBytes, kEntitySizeOffset, 12.5F);
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntityPlayerTypeOffset, 0);
    // `Entity.Index` 是**房间实体表序号**（`Entity+0x30`，批次 4 定位），与"玩家在 `players`
    // 向量里的下标"（`+0x19F0`）不是同一个量；两个偏移刻意写不同的值，用来钉住读的是哪个。
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntityIndexOffset, 7);
    WriteAt<std::uint32_t>(g_PlayerBytes, kEntityPlayerIndexOffset, 0);
}

void CorruptVtable() {
    WriteWord(g_PlayerBytes, 0, 0xDEADBEEFULL);
}

// --- 注入的"引擎方法" ------------------------------------------------------

std::uintptr_t g_CallEntity = 0;
std::uint32_t g_CallId = 0;
int g_CallCount = 0;

bool HarnessHasCollectible(void* entity, std::uint32_t collectibleId) {
    g_CallEntity = reinterpret_cast<std::uintptr_t>(entity);
    g_CallId = collectibleId;
    ++g_CallCount;
    // 只对 1 号收藏品回答 true：返回值必须原样往返（恒真/恒假都算实现错了）。
    return collectibleId == 1;
}

bool ExpectNumber(const char* name, double expected) {
    double value = 0.0;
    if (!LuaRuntime::ReadLuaGlobalNumber(name, &value)) {
        std::printf("ISAAC_FAIL: global %s was never written\n", name);
        return false;
    }
    if (value != expected) {
        std::printf("ISAAC_FAIL: %s expected %.0f, got %.0f\n", name, expected, value);
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const std::string scenario = argv[1];
    if (scenario != "valid" && scenario != "empty" && scenario != "zero_element" &&
        scenario != "bad_vptr" && scenario != "no_game" && scenario != "no_base") {
        return 91;
    }

    const bool publishBase = scenario != "no_base";
    CreateBlocks();
    if (publishBase) {
        LuaRuntime::SetEngineModuleBase(g_EngineBase);
    }

    if (scenario == "valid") {
        FillPlayer(true);
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
        PublishPlayers({AddressOf(g_PlayerBytes)});
    } else if (scenario == "empty") {
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
        PublishPlayers({});
    } else if (scenario == "zero_element") {
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
        PublishPlayers({0});
    } else if (scenario == "bad_vptr") {
        FillPlayer(false);
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
        PublishPlayers({AddressOf(g_PlayerBytes)});
    } else if (scenario == "no_game") {
        // 槽可读但变量里是 0：第二级解引用拿到空指针。
        PublishOwnerSlot(AddressOf(g_OwnerSlotBytes));
        WriteWord(g_OwnerSlotBytes, 0, 0);
        PublishPlayers({AddressOf(g_PlayerBytes)});
    }

    isaac::runtime::SetEntityPlayerHasCollectibleHostImplementation(&HarnessHasCollectible);

    const std::string script = "SCENARIO = '" + scenario + "'\n" + kScriptTemplate;
    const auto result =
        LuaRuntime::InitializeFromBuffer(script.c_str(), script.size(), "@entity_player.lua");
    if (result != LuaRuntime::LuaInitResult::Success) {
        std::printf("ISAAC_FAIL: the Lua state did not initialize\n");
        return 2;
    }

    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("ISAAC_FAIL: the Lua callback raised an error\n");
        return 1;
    }
    if (scenario == "valid") {
        // 第二帧之前把伪造的 vptr 改坏：句柄必须自证失效，而且不许再调用引擎方法。
        CorruptVtable();
        LuaRuntime::DispatchPostUpdate();
        if (LuaRuntime::TakeCallbackError()) {
            std::printf("ISAAC_FAIL: the second frame raised a Lua error\n");
            return 1;
        }
    }

    if (!ExpectNumber("SCENARIO_OK", 1.0)) return 1;
    if (scenario == "valid") {
        if (!ExpectNumber("PHASE1_OK", 1.0)) return 1;
        if (!ExpectNumber("PHASE2_OK", 1.0)) return 1;
        if (g_CallCount != 2) {
            std::printf("ISAAC_FAIL: HasCollectible must reach the engine exactly twice, got %d\n",
                        g_CallCount);
            return 1;
        }
        if (g_CallEntity != AddressOf(g_PlayerBytes)) {
            std::printf("ISAAC_FAIL: x0 must be the Entity_Player address (got %llx, want %llx)\n",
                        static_cast<unsigned long long>(g_CallEntity),
                        static_cast<unsigned long long>(AddressOf(g_PlayerBytes)));
            return 1;
        }
        if (g_CallId != 2) {
            std::printf("ISAAC_FAIL: the last collectible id must be 2, got %u\n", g_CallId);
            return 1;
        }
    } else if (g_CallCount != 0) {
        std::printf("ISAAC_FAIL: no engine call is allowed without a validated player, got %d\n",
                    g_CallCount);
        return 1;
    }

    std::printf("ENTITY_PLAYER_OK scenario=%s engine_calls=%d\n", scenario.c_str(), g_CallCount);
    if (scenario == "valid") {
        // 两帧的 Lua 侧结论都已经由 `ExpectNumber` 核对过，这里只是把标记打出来给 Python 侧看。
        std::printf("PHASE1_OK PHASE2_OK\n");
    }
    return 0;
}
'''

SCRIPT = r'''
local mod = RegisterMod('EntityPlayer view', 1)

local function check(condition, message)
  if not condition then error(message, 2) end
end

-- 引擎基址发布到 Lua 侧（宿主测试专用入口；设备构建里不存在）。
local savedPlayer = nil
local frame = 0

local function describe(value)
  if value == nil then return 'nil' end
  return type(value) .. ':' .. tostring(value)
end

-- 拿不到玩家的场景：任何下标都必须安静地返回 nil。
-- `Game:GetNumPlayers()` 报出的每一个下标都必须是**能拿到的**玩家；报 0 就必须承认没有玩家。
-- 两种可接受口径：①如实报引擎的元素个数（读成功，含 0）；②读失败时降级报 1，
-- 但那时 EID 走的是"单人快路径"（`main.lua:1065` 判 `numPlayers == 1`），不会进循环。
-- **不可接受**的是"报 1 且 `Isaac.GetPlayer(0)` 为 nil 且向量其实读得到且为空"。
local function verifyPlayerCountCoherence()
  local ok, count = pcall(function() return Game():GetNumPlayers() end)
  check(ok, 'Game:GetNumPlayers must not raise a Lua error')
  check(type(count) == 'number', 'Game:GetNumPlayers must return a number, got ' .. describe(count))
  local reachable = 0
  for index = 0, math.max(count, 1) do
    if Isaac.GetPlayer(index) ~= nil then reachable = reachable + 1 end
  end
  if count == 0 then
    check(reachable == 0, 'a zero player count must mean no reachable player')
  elseif count == 1 then
    check(reachable == 1,
          'a reported player count must never point at an unreachable player, got ' ..
          reachable .. ' of ' .. count)
  else
    check(reachable == count,
          'the reported player count must match the reachable players, got ' ..
          reachable .. ' of ' .. count)
  end
  PLAYER_COUNT = count
  PLAYER_REACHABLE = reachable
end

local function verifyUnavailable()
  for _, index in ipairs({ 0, 1, 99 }) do
    local ok, value = pcall(function() return Isaac.GetPlayer(index) end)
    check(ok, 'Isaac.GetPlayer must not raise a Lua error for index ' .. index)
    check(value == nil, 'Isaac.GetPlayer(' .. index .. ') must be nil, got ' .. describe(value))
  end
  local negativeOk, negative = pcall(function() return Isaac.GetPlayer(-1) end)
  check(negativeOk, 'Isaac.GetPlayer(-1) must not raise a Lua error')
  check(negative == nil, 'Isaac.GetPlayer(-1) must be nil, got ' .. describe(negative))
  local typeOk, typed = pcall(function() return Isaac.GetPlayer('nope') end)
  check(typeOk, 'Isaac.GetPlayer must not raise for a non-integer index')
  check(typed == nil, 'Isaac.GetPlayer must be nil for a non-integer index')
  -- 参数缺省时按 0 处理，拿不到玩家同样是 nil。
  local noneOk, none = pcall(function() return Isaac.GetPlayer() end)
  check(noneOk, 'Isaac.GetPlayer() must not raise a Lua error')
  check(none == nil, 'Isaac.GetPlayer() must be nil without a player')
  -- **口径一致性（真机 01789207110 的根因）**：`Game:GetNumPlayers()` 与
  -- `Isaac.GetPlayer(i)` 对同一个引擎事实必须给出同一个答案。EID 的
  -- `features/eid_api.lua:2620` 就是 `for i = 0, game:GetNumPlayers() - 1 do local player = Isaac.GetPlayer(i)`
  -- 之后立刻索引 `player.QueuedItem`：只要 `GetNumPlayers()` 报了一个"读不到的玩家"，
  -- 那一帧就会抛错、回调被派发器静默摘除，屏幕全空而游戏不崩。
  verifyPlayerCountCoherence()
end

local function verifyFields(player)
  check(player.Type == 1, 'Type must be ENTITY_PLAYER (1), got ' .. describe(player.Type))
  check(player.Variant == 0, 'Variant must be 0, got ' .. describe(player.Variant))
  check(player.SubType == 0, 'SubType must be 0, got ' .. describe(player.SubType))
  -- 批次 4 起 `Entity.Index` 读 `Entity+0x30`（`Room::AddEntity` 的 `str w8,[x1,#0x30]`），
  -- 不再是"玩家在 players 向量里的下标"（`+0x19F0`，已不再作为 Lua 字段暴露）。
  check(player.Index == 7, 'Index must be the room list index (7), got ' .. describe(player.Index))
  check(player.PlayerType == 0, 'PlayerType must be 0 (Isaac), got ' .. describe(player.PlayerType))
  check(player:GetPlayerType() == player.PlayerType,
        'GetPlayerType must read the same field as PlayerType')
  check(type(player.Size) == 'number' and math.abs(player.Size - 12.5) < 0.001,
        'Size must be the entity radius, got ' .. describe(player.Size))
  local position = player.Position
  check(type(position) == 'userdata', 'Position must be a Vector, got ' .. describe(position))
  check(math.abs(position.X - 80.0) < 0.001 and math.abs(position.Y - 280.0) < 0.001,
        'Position must be (80, 280), got (' .. position.X .. ', ' .. position.Y .. ')')
  -- 必须是**新的** Vector：改它不能影响下一次读取（别名会让 Mod 改到别人的值）。
  position.X = -1
  position.Y = -1
  local again = player.Position
  check(math.abs(again.X - 80.0) < 0.001 and math.abs(again.Y - 280.0) < 0.001,
        'Position must return a fresh Vector on every read')
  -- 未知字段仍然是 nil（`__index` 不能凭空造值）。
  check(player.NotAThing == nil, 'an unknown field must be nil')

  -- 批次 6（2026-09-12）：EID 逐帧调用的一批成员。缺任何一个都是 "attempt to call a nil value"，
  -- 而派发器会**静默摘除**整条回调（真机报告 `01789210946` 的 `player:GetPill(0)` 就是这样）。
  -- 这一组断言只钉"名字在、不抛错、返回类型对"，不钉数值语义 —— 数值要等引擎字段偏移定位。
  -- 无参成员：返回数字。
  local zeroNoArg = { 'GetNumKeys', 'GetNumBombs', 'GetNumCoins', 'GetHearts', 'GetMaxHearts',
                      'GetSoulCharge', 'GetBloodCharge', 'GetPoopMana', 'GetPoopSpell',
                      'GetZodiacEffect', 'GetModelingClayEffect', 'GetGlyphOfBalanceDrop' }
  for _, name in ipairs(zeroNoArg) do
    local ok, value = pcall(function() return player[name](player) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(type(value) == 'number', name .. ' must return a number, got ' .. describe(value))
  end
  -- 带槽位参数的成员：同样返回数字（药丸/卡片/饰品/表单计数都是"没有就是 0"）。
  local zeroSlotted = { 'GetPill', 'GetCard', 'GetCollectibleNum', 'GetTrinketMultiplier',
                        'GetPlayerFormCounter' }
  for _, name in ipairs(zeroSlotted) do
    local ok, value = pcall(function() return player[name](player, 0) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(type(value) == 'number', name .. ' must return a number, got ' .. describe(value))
  end
  -- 可能为 nil 的对象/id：PC 上"没有就是这个结果"，比编一个假对象安全。
  local nilNoArg = { 'GetMainTwin', 'GetName' }
  for _, name in ipairs(nilNoArg) do
    local ok, value = pcall(function() return player[name](player) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(value == nil, name .. ' must be nil by default, got ' .. describe(value))
  end
  local nilSlotted = { 'GetCollectibleRNG', 'GetCardRNG', 'GetPillRNG' }
  for _, name in ipairs(nilSlotted) do
    local ok, value = pcall(function() return player[name](player, 0) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(value == nil, name .. ' must be nil by default, got ' .. describe(value))
  end
  local okTrinketRng, trinketRng = pcall(function() return player:GetTrinketRNG(0) end)
  check(okTrinketRng, 'GetTrinketRNG must not raise: ' .. tostring(trinketRng))
  check(trinketRng == nil, 'GetTrinketRNG must be nil by default')
  -- 布尔成员。
  local booleanNoArg = { 'HasGoldenBomb', 'CanPickRedHearts', 'IsSubPlayer' }
  for _, name in ipairs(booleanNoArg) do
    local ok, value = pcall(function() return player[name](player) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(type(value) == 'boolean', name .. ' must return a boolean, got ' .. describe(value))
  end
  for _, name in ipairs({ 'HasTrinket', 'HasPlayerForm' }) do
    local ok, value = pcall(function() return player[name](player, 0) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(type(value) == 'boolean', name .. ' must return a boolean, got ' .. describe(value))
  end
  -- 集合类：返回可遍历的表（空集合比报错安全）。
  for _, name in ipairs({ 'GetSmeltedTrinkets', 'GetEffects' }) do
    local ok, value = pcall(function() return player[name](player) end)
    check(ok, name .. ' must not raise: ' .. tostring(value))
    check(type(value) == 'table', name .. ' must return a table, got ' .. describe(value))
  end
  -- `player.QueuedItem` 是 EID 逐帧读 14 次的字段；PC 上它永远是一张表。
  check(type(player.QueuedItem) == 'table',
        'player.QueuedItem must be a table, got ' .. describe(player.QueuedItem))
  check(player.QueuedItem.Item == nil, 'an empty queued item must have no Item')
end

local function verifyValid()
  local player = Isaac.GetPlayer(0)
  check(player ~= nil, 'Isaac.GetPlayer(0) must return an EntityPlayer')
  check(type(player) == 'userdata', 'GetPlayer must return a userdata, got ' .. describe(player))
  verifyFields(player)

  -- 方法：引擎侧的返回值必须原样往返（注入实现对 id == 1 回答 true）。
  check(player:HasCollectible(1) == true, 'HasCollectible(1) must be true')
  check(player:HasCollectible(2) == false, 'HasCollectible(2) must be false')
  local same = player:ToPlayer()
  check(same == player, 'EntityPlayer:ToPlayer must return the player itself')
  -- 批次 4 起 `GetData` 返回 Runtime 自管的稳定表（EID 真的往里面写，见
  -- `features/eid_api.lua:2229`），同一个实体每次都必须是**同一张**表。
  local data = player:GetData()
  check(type(data) == 'table', 'GetData must return a table, got ' .. describe(data))
  check(data == player:GetData(), 'GetData must keep the same table for the same entity')
  data.MARK = 'entity-player'
  check(player:GetData().MARK == 'entity-player', 'a write must be visible through GetData')

  -- 只有一个玩家：越界必须返回 nil。
  check(Isaac.GetPlayer(1) == nil, 'Isaac.GetPlayer(1) must be nil with a single player')
  -- 同上：数量与可达玩家必须一致（本场景只有一个玩家）。
  verifyPlayerCountCoherence()
  check(PLAYER_COUNT == 1, 'the valid scenario has exactly one player, got ' .. PLAYER_COUNT)
  check(PLAYER_REACHABLE == 1, 'that one player must be reachable')
  check(Isaac.GetPlayer(99) == nil, 'Isaac.GetPlayer(99) must be nil')
  savedPlayer = player
  PHASE1_OK = 1
end

-- 第二帧：harness 已经把伪造的 vptr 改坏，句柄必须自证失效。
local function verifyInvalidatedHandle()
  check(savedPlayer ~= nil, 'the first frame must have produced a handle')
  check(Isaac.GetPlayer(0) == nil, 'GetPlayer must be nil once the vptr no longer matches')
  check(savedPlayer.Type == nil, 'an invalidated handle must not answer fields')
  check(savedPlayer.Position == nil, 'an invalidated handle must not answer Position')
  check(savedPlayer:GetPlayerType() == nil, 'GetPlayerType must be nil on an invalidated handle')
  -- 这一句在 harness 侧被核对：它不许再到达引擎（调用计数必须停在 2）。
  check(savedPlayer:HasCollectible(1) == false,
        'HasCollectible on an invalidated handle must degrade to false')
  PHASE2_OK = 1
end

local function run()
  frame = frame + 1
  if SCENARIO == 'valid' then
    if frame == 1 then
      verifyValid()
    elseif frame == 2 then
      verifyInvalidatedHandle()
    end
    SCENARIO_OK = 1
    return
  end
  verifyUnavailable()
  SCENARIO_OK = 1
end

mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(run)
  if not ok then
    SCENARIO_OK = 0
    print('ISAAC_ERROR: ' .. tostring(err))
  end
end)
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


class LuaEntityPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-entity-player-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "entity_player_harness.cpp"
        harness.write_text(HARNESS.replace("@SCRIPT@", SCRIPT).lstrip())
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
        cls.harness = temporary / "entity_player_harness"
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

    def run_scenario(self, scenario: str) -> subprocess.CompletedProcess:
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(
            result.returncode, 0,
            f"scenario {scenario} exited with {result.returncode}\n{result.stdout}{result.stderr}",
        )
        self.assertNotIn("ISAAC_ERROR", result.stdout + result.stderr)
        self.assertNotIn("ISAAC_FAIL", result.stdout + result.stderr)
        return result

    def test_player_fields_and_methods_match_the_hardware_verified_values(self):
        """真机实测值逐条核对 + `Position` 的"新对象"语义 + 方法返回值往返。

        伪造的引擎内存按真机探针 `ISAACGP1` 的读数摆好（`Type == 1`、`Variant == 0`、
        `SubType == 0`、`PlayerType == 0`、下标 0、位置 `(80, 280)`），Lua 侧逐字段断言；
        harness 另外核对注入的"引擎方法"收到的 `x0`（必须是 `Entity_Player` 地址）与 id。
        """
        result = self.run_scenario("valid")
        self.assertIn("ENTITY_PLAYER_OK scenario=valid engine_calls=2", result.stdout)

    def test_get_player_returns_nil_when_the_player_cannot_be_validated(self):
        """四种"拿不到玩家"的形态都必须返回 nil，而且**一次引擎调用都不许发生**。

        * `empty`：玩家向量为空（`begin == end`）；
        * `zero_element`：向量里有一个空元素；
        * `bad_vptr`：元素非空但首字不是 `base + 0xA37110`；
        * `no_game`：`g_Game` 槽可读、但变量里是空指针；
        * `no_base`：引擎模块基址根本没发布（宿主/真机早期）。
        """
        for scenario in ("empty", "zero_element", "bad_vptr", "no_game", "no_base"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn(f"ENTITY_PLAYER_OK scenario={scenario} engine_calls=0", result.stdout)

    def test_invalidated_handle_degrades_without_reaching_the_engine(self):
        """vptr 校验必须发生在**每一次**访问上，尤其是引擎调用之前。

        `valid` 场景的第二帧里，伪造的 vptr 已经被改坏：字段返回 nil、`HasCollectible` 返回
        false，而 harness 记录的引擎调用次数仍然停在 2（第一帧那两次），说明
        `Entity_Player::HasCollectible` 没有被调用。
        """
        result = self.run_scenario("valid")
        self.assertIn("PHASE1_OK", result.stdout)
        self.assertIn("PHASE2_OK", result.stdout)
        # harness 的核对（调用次数、x0、id）通过才会打印这一行。
        self.assertIn("engine_calls=2", result.stdout)


if __name__ == "__main__":
    unittest.main()
