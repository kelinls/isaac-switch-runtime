"""`Isaac.GetItemConfig()` 与 `ItemConfig` / `ItemConfig_Item` 只读视图的宿主行为测试。

批次 2b 的这条链路（`Manager + 0x36538` 的**内嵌** `ItemConfig` → 并列向量 → 条目 → libc++ SSO
字符串）已经由反汇编定位（偏移与指令级证据记在 `runtime_constants.hpp` 的 ItemConfig 段），
需要被证明的是**我们这一侧的指针链、边界检查、字符串形态判定与 Lua 视图**对不对。那些正好
可以完全在宿主上验证：

1. 用 `malloc`/`std::vector` 出来的块**伪造一份引擎内存**：模块块里的 `g_Manager` 槽 → 槽里是
   `g_Manager` 变量的地址 → 变量里才是 `Manager*` → `Manager + kManagerItemConfigOffset` 处是
   **内嵌**的 `ItemConfig`（加法，不是解引用）→ 六条并列向量的前四条 → 条目对象（`+0x00` 类别、
   `+0x04` id、`+0x08` 名字 `std::string`、`+0x20` 描述 `std::string`）；
2. 用真实 Lua 脚本断言字段值（`GetCollectible(1).Name == "Sad Onion"`、描述非空）、
   并列向量（`GetTrinket`/`GetCard`/`GetPillEffect` 各自的下标空间）、越界/负数/非整数/缺参数
   一律 `nil`、`HasTags` 恒假；
3. **两条字符串路径都读对**：短串（≤22 字节，data 内联在 `+0x01`）与长串（>22 字节，data 指针
   在 `+0x10`、size 在 `+0x08`）各摆一条，逐字比对；
4. 逐场景断言"读不出来就降级"：基址未发布、`g_Manager` 槽为 0、槽里是空指针、向量为空、
   字节长度不是 8 的倍数、长度超过上限、条目指针为空、字符串 size 离谱、类别与向量不符。

最后一类用"两帧"实现：第一帧拿到句柄并正常读取；harness 在派发之间把收藏品向量**重新指向
另一块数组**（等价于换局后 `ItemConfig::Init` 重排向量）；第二帧用同一个句柄访问，必须既返回
`nil`、又在同一次派发里让**新句柄**立刻读到新数据 —— 这就是"每一次访问都重新校验"的判据。
"""

import shutil
import subprocess
import tempfile
import re
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"

# 长串条目（> 22 字节 → libc++ 的堆形态）：写进伪造内存与 Lua 期望值用的是同一个字面量。
LONG_NAME = "#THE_SAD_ONION_DESCRIPTION_LONG_STUB_TAIL"


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
const char* kLongName = "@LONG_NAME@";
// `GfxFileName`（`ItemConfig::Item` 的 `+0x38`）夹具值：短的一条走 libc++ 的 SSO，长的一条走堆形态。
const char* kShortGfx = "gfx/items/c1.png";
const char* kLongGfx = "gfx/items/collectibles/Collectibles_LongEntry.png";

// --- 伪造的引擎内存 --------------------------------------------------------
//
// 与真机同形（`ItemConfig` 是**内嵌**对象，所以 `IC` 是"算出来的地址"：
// `IC = Manager + kManagerItemConfigOffset`，正是上一轮探针多解引用一次的那个位置）：
//
//   模块块[base + 0xAAC648] = &g_Manager 变量        （槽里是**变量地址**，不是对象）
//   g_Manager 变量          = Manager*
//   Manager + 0x36538       = ItemConfig（内嵌，加法）
//   IC + 0x00/+0x08         = std::vector<ItemConfig::Item*>（收藏品，733 项）
//   IC + 0x18/+0x20 …       = 并列向量（Trinket / Card / PillEffect …）
//   条目 + 0x00/+0x04/+0x08/+0x20/+0x38 = Type / id / 名字 / 描述 / GfxFileName（都是 std::string）
std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_ManagerBytes;
std::vector<unsigned char> g_ManagerSlotBytes;
std::vector<std::vector<unsigned char>> g_ItemBlocks;
std::vector<std::vector<unsigned char>> g_VectorBlocks;
std::vector<std::vector<char>> g_LongStrings;

std::uintptr_t g_EngineBase = 0;

// 条目块的下标（`NewItemBlock()` 的顺序即身份）。
enum ItemSlot : std::size_t {
    kSadOnion = 0,      // 收藏品 1：短串名字
    kLongCollectible,   // 收藏品 2：长串名字（>22 字节）
    kTrinketItem,       // Trinket[1]
    kCardItem,          // Card[0]
    kPillItem,          // PillEffect[1]
    kBrimstone,         // 第二帧的新收藏品 1（换局后的向量）
    kItemSlotCount,
};

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

std::uintptr_t AddressOf(const std::vector<char>& block) {
    return reinterpret_cast<std::uintptr_t>(block.data());
}

// libc++ `std::string` 的两种形态都写一遍：`forceLong` 用来把"短串"强行按堆形态摆放，
// 从而证明读取走的是 **byte0 的 bit0**，而不是"串短就一定是内联"这类巧合。
void WriteLibcxxString(std::vector<unsigned char>& block, std::size_t offset,
                       const std::string& text, bool forceLong) {
    if (!forceLong && text.size() <= 22) {
        const std::uint8_t header = static_cast<std::uint8_t>(text.size() << 1);
        std::memcpy(block.data() + offset, &header, 1);
        std::memcpy(block.data() + offset + 1, text.data(), text.size());
        return;
    }
    // 长串：byte0 = 1（bit0 是 is_long）、size 在 +0x08、data 指针在 +0x10。
    std::uint8_t header = 1;
    std::memcpy(block.data() + offset, &header, 1);
    g_LongStrings.emplace_back(text.begin(), text.end());
    g_LongStrings.back().push_back('\0');
    WriteWord(block, offset + 0x08, static_cast<std::uint64_t>(text.size()));
    WriteWord(block, offset + 0x10, static_cast<std::uint64_t>(AddressOf(g_LongStrings.back())));
}

void FillItem(std::size_t slot, std::uint32_t type, std::uint32_t id, const std::string& name,
              const std::string& description, const std::string& gfxFileName, bool nameIsLong) {
    auto& block = g_ItemBlocks[slot];
    WriteAt<std::uint32_t>(block, kItemConfigItemTypeOffset, type);
    WriteAt<std::uint32_t>(block, kItemConfigItemIdOffset, id);
    WriteLibcxxString(block, kItemConfigItemNameOffset, name, nameIsLong);
    WriteLibcxxString(block, kItemConfigItemDescriptionOffset, description, false);
    WriteLibcxxString(block, kItemConfigItemGfxFileNameOffset, gfxFileName, false);
}

std::uintptr_t ItemAddress(std::size_t slot) {
    return AddressOf(g_ItemBlocks[slot]);
}

void CreateBlocks() {
    // 0x60 而不是 0x40：`GfxFileName` 在 `+0x38`，24 字节的 libc++ 串要占到 `+0x4F`。
    g_ItemBlocks.assign(kItemSlotCount, std::vector<unsigned char>(0x60, 0));
    g_ManagerBytes.assign(kManagerItemConfigOffset + 0xA0, 0);
    g_ManagerSlotBytes.assign(sizeof(std::uint64_t), 0);
    // 模块块只需要覆盖 `g_Manager` 的槽（`Isaac.GetItemConfig` 用不到 `g_Game`，但玩家链在同一
    // 块里，测试保留同样的口径：越界的槽读不到就是"拿不到"）。
    g_ModuleBytes.assign(kGameManagerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_EngineBase = AddressOf(g_ModuleBytes);
}

// 建一条 `std::vector<ItemConfig::Item*>` 并把 begin/end 写进 `IC` 的对应偏移。
void PublishVector(std::size_t beginOffset, const std::vector<std::uint64_t>& elements) {
    g_VectorBlocks.emplace_back(elements.size() * sizeof(std::uint64_t) + sizeof(std::uint64_t), 0);
    auto& block = g_VectorBlocks.back();
    for (std::size_t index = 0; index < elements.size(); ++index) {
        WriteWord(block, index * sizeof(std::uint64_t), elements[index]);
    }
    WriteWord(g_ManagerBytes, kManagerItemConfigOffset + beginOffset, AddressOf(block));
    WriteWord(g_ManagerBytes, kManagerItemConfigOffset + beginOffset + sizeof(std::uint64_t),
              AddressOf(block) + elements.size() * sizeof(std::uint64_t));
}

std::vector<std::uint64_t> CollectibleElements() {
    // 733 项（真机硬判据 `E - B == 0x16E8`）：0 号是真机上的空槽（指针为 0）。
    std::vector<std::uint64_t> elements(kItemConfigCollectibleCount, 0);
    elements[1] = ItemAddress(kSadOnion);
    elements[2] = ItemAddress(kLongCollectible);
    return elements;
}

// 条目数据。`+0x00` 是 PC 的 `ItemType`（批次 3 定案：`items.xml` 的 `type` 属性被解析器原样
// 写进这个字段，见 `runtime_constants.hpp` 的 `kItemConfigItemTypeOffset`）：1=PASSIVE、
// 2=TRINKET、3=ACTIVE、4=FAMILIAR。卡牌/药丸条目沿用 3/4 只是为了证明"`Type` 原样返回
// `+0x00` 的读数"，不声称卡牌/药丸在引擎里就是这两个类型。
void FillItems() {
    FillItem(kSadOnion, kItemTypePassive, 1, "Sad Onion", "Tears up + damage up", kShortGfx,
             false);
    FillItem(kLongCollectible, kItemTypePassive, 2, kLongName, "long name entry", kLongGfx, true);
    FillItem(kTrinketItem, kItemTypeTrinket, 1, "Swallowed Penny", "trinket stub",
             "gfx/items/t1.png", false);
    FillItem(kCardItem, 3, 0, "Card stub", "card stub", "gfx/items/card.png", false);
    FillItem(kPillItem, 4, 1, "Pill stub", "pill stub", "gfx/items/pill.png", false);
    FillItem(kBrimstone, kItemTypePassive, 1, "Brimstone", "blood laser barrage",
             "gfx/items/c118.png", false);
}

// 换局：`ItemConfig::Init` 重排向量 —— 收藏品向量指到另一块数组、1 号条目换了一个对象。
// 旧的句柄缓存的是旧 begin，所以必须自证过期（而不是"地址还读得到就继续用"）。
void RepointCollectibleVector() {
    std::vector<std::uint64_t> elements(kItemConfigCollectibleCount, 0);
    elements[1] = ItemAddress(kBrimstone);
    elements[2] = ItemAddress(kLongCollectible);
    PublishVector(kItemConfigCollectibleBeginOffset, elements);
}

void PublishVectors() {
    PublishVector(kItemConfigCollectibleBeginOffset, CollectibleElements());
    // Trinket：{ 空槽, 条目, 空槽 } —— 下标空间与收藏品不同，越界判据必须按各自的向量算。
    PublishVector(kItemConfigTrinketBeginOffset,
                  {0, ItemAddress(kTrinketItem), 0});
    // Card：{ 条目, 空槽 }
    PublishVector(kItemConfigCardBeginOffset, {ItemAddress(kCardItem), 0});
    // PillEffect：{ 空槽, 条目 }
    PublishVector(kItemConfigPillEffectBeginOffset, {0, ItemAddress(kPillItem)});
}

void PublishManager() {
    WriteWord(g_ModuleBytes, kGameManagerGlobalSlotOffset, AddressOf(g_ManagerSlotBytes));
    WriteWord(g_ManagerSlotBytes, 0, AddressOf(g_ManagerBytes));
    // `Manager + kManagerItemConfigOffset` 就是 `ItemConfig`；向量已经写在这块里了。
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
    const char* kScenarios[] = {"valid", "no_base", "slot_zero", "no_manager", "empty_vector",
                                "bad_length", "huge_length", "null_entry", "bad_string",
                                "wrong_kind"};
    bool known = false;
    for (const char* candidate : kScenarios) {
        if (scenario == candidate) known = true;
    }
    if (!known) return 91;

    CreateBlocks();
    FillItems();

    if (scenario != "no_base") {
        PublishVectors();
        PublishManager();
        if (scenario == "slot_zero") {
            // `g_Manager` 的**槽**本身是 0：第二级解引用必须变成"拿不到"，而不是读地址 0。
            WriteWord(g_ModuleBytes, kGameManagerGlobalSlotOffset, 0);
        } else if (scenario == "no_manager") {
            // 槽可读、变量里是空指针。
            WriteWord(g_ManagerSlotBytes, 0, 0);
        } else if (scenario == "empty_vector") {
            // begin == end：长度为 0。
            const std::uintptr_t begin = AddressOf(g_VectorBlocks.front());
            WriteWord(g_ManagerBytes, kManagerItemConfigOffset + kItemConfigCollectibleEndOffset,
                      begin);
        } else if (scenario == "bad_length") {
            // `E - B` 不是 8 的倍数（0x16E8 + 4）。
            const std::uintptr_t begin = AddressOf(g_VectorBlocks.front());
            WriteWord(g_ManagerBytes, kManagerItemConfigOffset + kItemConfigCollectibleEndOffset,
                      begin + kItemConfigCollectibleVectorBytes + 4);
        } else if (scenario == "huge_length") {
            // 长度超过合理上限（4097 项）：偏移猜错时"读到一段垃圾"必须变成"读不到"。
            const std::uintptr_t begin = AddressOf(g_VectorBlocks.front());
            WriteWord(g_ManagerBytes, kManagerItemConfigOffset + kItemConfigCollectibleEndOffset,
                      begin + 4097 * sizeof(std::uint64_t));
        } else if (scenario == "null_entry") {
            WriteWord(g_VectorBlocks.front(), 1 * sizeof(std::uint64_t), 0);
        } else if (scenario == "bad_string") {
            // `is_long` 置位（byte0 的 bit0）但 size 离谱（0x100000）：名字必须读成 nil，
            // 而不是拿这个长度去越界读。
            WriteAt<std::uint8_t>(g_ItemBlocks[kSadOnion], kItemConfigItemNameOffset, 1);
            WriteWord(g_ItemBlocks[kSadOnion], kItemConfigItemNameOffset + 0x08, 0x100000);
        } else if (scenario == "wrong_kind") {
            // 收藏品向量里的条目却带着 TRINKET 的类别：`IsCollectible` 必须说 false。
            WriteAt<std::uint32_t>(g_ItemBlocks[kSadOnion], kItemConfigItemTypeOffset,
                                   kItemTypeTrinket);
        }
        LuaRuntime::SetEngineModuleBase(g_EngineBase);
    }

    const std::string script = "SCENARIO = '" + scenario + "'\n" + kScriptTemplate;
    const auto result =
        LuaRuntime::InitializeFromBuffer(script.c_str(), script.size(), "@item_config.lua");
    if (result != LuaRuntime::LuaInitResult::Success) {
        std::printf("ISAAC_FAIL: the Lua state did not initialize\n");
        return 2;
    }

    const bool hasSecondFrame = scenario == "valid";
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("ISAAC_FAIL: the Lua callback raised an error\n");
        return 1;
    }
    if (hasSecondFrame) {
        // 第二帧之前把向量重新指向另一块数组：旧句柄必须自证过期。
        RepointCollectibleVector();
        LuaRuntime::DispatchPostUpdate();
        if (LuaRuntime::TakeCallbackError()) {
            std::printf("ISAAC_FAIL: the second frame raised a Lua error\n");
            return 1;
        }
    }

    if (!ExpectNumber("SCENARIO_OK", 1.0)) return 1;
    if (hasSecondFrame) {
        if (!ExpectNumber("PHASE1_OK", 1.0)) return 1;
        if (!ExpectNumber("PHASE2_OK", 1.0)) return 1;
    }
    std::printf("ITEM_CONFIG_OK scenario=%s\n", scenario.c_str());
    if (hasSecondFrame) {
        // 两帧的 Lua 侧结论都已经由 `ExpectNumber` 核对过，这里只是把标记打出来给 Python 侧看。
        std::printf("PHASE1_OK PHASE2_OK\n");
    }
    return 0;
}
'''


SCRIPT = r'''
local mod = RegisterMod('ItemConfig view', 1)

local function check(condition, message)
  if not condition then error(message, 2) end
end

local function describe(value)
  if value == nil then return 'nil' end
  return type(value) .. ':' .. tostring(value)
end

local LONG_NAME = '@LONG_NAME@'
local savedConfig = nil
local savedItem = nil
local frame = 0

-- 链路拿不到（基址未发布 / 槽为 0 / Manager 为空 / 向量非法 / 长度为 0）：只能是 nil，不报错。
local function verifyUnavailable()
  local ok, value = pcall(function() return Isaac.GetItemConfig() end)
  check(ok, 'Isaac.GetItemConfig must not raise a Lua error: ' .. tostring(value))
  check(value == nil, 'Isaac.GetItemConfig must be nil, got ' .. describe(value))
end

local function verifyValid()
  local config = Isaac.GetItemConfig()
  check(config ~= nil, 'Isaac.GetItemConfig() must return an ItemConfig')
  check(type(config) == 'userdata', 'GetItemConfig must return a userdata, got '
        .. describe(config))
  check(config.NotAThing == nil, 'an unknown ItemConfig field must be nil')
  check(type(config.GetCollectible) == 'function', 'GetCollectible must exist')

  local item = config:GetCollectible(1)
  check(type(item) == 'userdata', 'GetCollectible must return an ItemConfig_Item, got '
        .. describe(item))
  check(item.ID == 1, 'ID must be the item id (1), got ' .. describe(item.ID))
  -- `+0x00` 是 PC 的 `ItemType`（1 = ITEM_PASSIVE）：Sad Onion 是被动道具，所以读数是 1。
  check(item.Type == 1, 'Type must be the raw ItemType field (1 = ITEM_PASSIVE), got '
        .. describe(item.Type))
  check(item.Name == 'Sad Onion', 'Name must be "Sad Onion", got ' .. describe(item.Name))
  check(type(item.Description) == 'string' and #item.Description > 0,
        'Description must be a non-empty string, got ' .. describe(item.Description))
  -- `GfxFileName`（`ItemConfig::Item` 的 `+0x38`）：EID 在 `features/eid_api.lua:1278` 直接把它交给
  -- `Sprite:ReplaceSpritesheet`。缺这个字段正是真机上"一进道具范围就报错、描述全不显示"的根因
  -- （报告 `01789228056` 那条 126 字节的 Lua 错误唯一能落在 `eid_api.lua:1278`）。
  check(item.GfxFileName == 'gfx/items/c1.png',
        'GfxFileName must be the raw items.xml gfx path, got ' .. describe(item.GfxFileName))
  check(item.NotAThing == nil, 'an unknown item field must be nil')
  -- 没有证据的成员：恒假，而不是报错或编一个值。
  check(item:HasTags(1) == false, 'ItemConfig_Item:HasTags has no evidence and must return false')
  -- 批次 3：`ItemConfig_Item:IsCollectible()`（**无参**）是 EID 在描述构建里直接调用的成员
  -- （`main.lua:669`/`738`/`757`），判据是"句柄仍有效且 `Type != ITEM_NULL`"。
  check(item:IsCollectible() == true,
        'ItemConfig_Item:IsCollectible must be true for a validated collectible entry')
  check(config:HasTags(1) == false, 'ItemConfig:HasTags has no evidence and must return false')

  -- 0 号是真机上的空槽（指针为 0）：必须 nil，既不是假对象也不能崩。
  check(config:GetCollectible(0) == nil, 'the null collectible slot must be nil')
  check(config:IsCollectible(1) == true, 'IsCollectible(1) must be true for Sad Onion')
  check(config:IsCollectible(0) == false, 'IsCollectible(0) must be false for the null slot')
  check(config:IsCollectible(999) == false, 'IsCollectible out of range must be false')

  -- 越界 / 负数 / 非整数 / 缺参数：一律 nil，且不抛 Lua 错误。
  for _, id in ipairs({ -1, 733, 999999 }) do
    local ok, value = pcall(function() return config:GetCollectible(id) end)
    check(ok, 'GetCollectible(' .. id .. ') must not raise: ' .. tostring(value))
    check(value == nil, 'GetCollectible(' .. id .. ') must be nil, got ' .. describe(value))
  end
  check(config:GetCollectible('nope') == nil, 'GetCollectible must be nil for a non-integer id')
  check(config:GetCollectible() == nil, 'GetCollectible must be nil without an id')
  check(config:IsCollectible('nope') == false, 'IsCollectible must be false for a non-integer id')
  -- id 的接受范围与 PC 的 `luaL_checkinteger` 一致：整值浮点收，带小数的浮点不收。
  local asFloat = config:GetCollectible(1.0)
  check(type(asFloat) == 'userdata' and asFloat.Name == 'Sad Onion',
        'an integral float id (1.0) must be accepted the way PC accepts it')
  check(config:GetCollectible(1.5) == nil, 'a fractional id must be nil')
  check(config:IsCollectible(1.0) == true, 'IsCollectible(1.0) must behave like IsCollectible(1)')

  -- 长串（> 22 字节）走 libc++ 的堆形态：必须逐字读对。
  local long = config:GetCollectible(2)
  check(long ~= nil, 'the long-name entry must exist')
  check(#LONG_NAME > 22, 'the fixture itself must be longer than the SSO buffer')
  check(long.Name == LONG_NAME,
        'a long std::string must be read from +0x10/+0x08, got ' .. describe(long.Name))
  check(long.ID == 2, 'the long-name entry must keep its own id, got ' .. describe(long.ID))
  check(type(long.Description) == 'string' and #long.Description > 0,
        'the long-name entry must still have a description')
  check(type(long.GfxFileName) == 'string' and #long.GfxFileName > 22,
        'a long gfx name must be read from the heap form, got ' .. describe(long.GfxFileName))
  check(string.sub(long.GfxFileName, 1, 4) == 'gfx/',
        'the long gfx name must be the fixture path, got ' .. describe(long.GfxFileName))

  -- 并列向量：各自的下标空间与条目内容。
  local trinket = config:GetTrinket(1)
  check(trinket ~= nil, 'GetTrinket(1) must exist')
  check(trinket.Name == 'Swallowed Penny',
        'GetTrinket(1).Name must come from the trinket vector, got ' .. describe(trinket.Name))
  check(trinket.Type == 2, 'a trinket entry must report kind 2, got ' .. describe(trinket.Type))
  check(trinket.ID == 1, 'the trinket id must come from its own entry')
  check(config:GetTrinket(0) == nil, 'the null trinket slot must be nil')
  check(config:GetTrinket(2) == nil, 'the trinket vector has 3 slots: index 2 is the null slot')
  check(config:GetTrinket(3) == nil, 'GetTrinket(3) must be nil (out of range)')
  check(config:GetTrinket(733) == nil, 'the trinket vector is not the collectible vector')

  local card = config:GetCard(0)
  check(card ~= nil, 'GetCard(0) must exist')
  check(card.Name == 'Card stub', 'GetCard(0).Name must come from the card vector, got '
        .. describe(card.Name))
  check(card.Type == 3, 'Type must be the raw kind field of the card entry')
  check(config:GetCard(1) == nil, 'the card vector has 2 slots: index 1 is the null slot')
  check(config:GetCard(2) == nil, 'GetCard(2) must be nil (out of range)')

  local pill = config:GetPillEffect(1)
  check(pill ~= nil, 'GetPillEffect(1) must exist')
  check(pill.Name == 'Pill stub', 'GetPillEffect(1).Name must come from the pill vector, got '
        .. describe(pill.Name))
  check(config:GetPillEffect(0) == nil, 'the null pill slot must be nil')
  check(config:GetPillEffect(2) == nil, 'GetPillEffect(2) must be nil (out of range)')

  savedConfig = config
  savedItem = item
  PHASE1_OK = 1
end

-- 第二帧：向量已经被重新指向另一块数组（等价于换局后的 `ItemConfig::Init`）。
-- 旧句柄必须自证过期；同一帧里新取的句柄必须立刻读到新数据。
local function verifyExpired()
  check(savedConfig ~= nil and savedItem ~= nil, 'the first frame must have produced handles')
  check(savedConfig:GetCollectible(1) == nil,
        'a stale ItemConfig handle must not answer entries')
  check(savedConfig:IsCollectible(1) == false,
        'a stale ItemConfig handle must not answer IsCollectible')
  check(savedItem.ID == nil, 'a stale item handle must not answer ID')
  check(savedItem.Name == nil, 'a stale item handle must not answer Name')
  check(savedItem.Description == nil, 'a stale item handle must not answer Description')
  check(savedItem.GfxFileName == nil, 'a stale item handle must not answer GfxFileName')
  check(savedItem:HasTags(1) == false, 'HasTags stays false on a stale handle')

  local fresh = Isaac.GetItemConfig()
  check(fresh ~= nil, 'a new handle must be available right after the vector moved')
  local item = fresh:GetCollectible(1)
  check(item ~= nil, 'the new handle must answer entries')
  check(item.Name == 'Brimstone',
        'the new handle must read the new vector, got ' .. describe(item.Name))
  check(fresh:IsCollectible(1) == true, 'the new handle must answer IsCollectible')
  PHASE2_OK = 1
end

-- 条目指针为 0：单个下标拿不到，其余下标必须照常工作。
local function verifyNullEntry()
  local config = Isaac.GetItemConfig()
  check(config ~= nil, 'the config itself is still readable')
  check(config:GetCollectible(1) == nil, 'a null entry pointer must read as nil')
  check(config:IsCollectible(1) == false, 'a null entry pointer must read as false')
  check(config:GetCollectible(2) ~= nil, 'other indices must still work')
  check(config:GetCollectible(2).Name == LONG_NAME, 'the neighbouring entry must be intact')
end

-- 字符串 size 离谱：`Name` 降级成 nil，但同一对象的其他字段必须照常可读。
local function verifyBadString()
  local item = Isaac.GetItemConfig():GetCollectible(1)
  check(item ~= nil, 'the entry itself is still readable')
  check(item.ID == 1, 'ID must still be readable')
  check(item.Type == 1, 'Type must still be readable')
  check(item.Name == nil, 'an absurd std::string size must read as nil, got '
        .. describe(item.Name))
  check(type(item.Description) == 'string' and #item.Description > 0,
        'the description must be unaffected by the broken name')
  check(item.GfxFileName == 'gfx/items/c1.png',
        'GfxFileName must be unaffected by the broken name, got '
        .. describe(item.GfxFileName))
end

-- 收藏品向量里的条目带着 TRINKET 的类别：`IsCollectible` 必须按 `kind` 说 false。
local function verifyWrongKind()
  local config = Isaac.GetItemConfig()
  local item = config:GetCollectible(1)
  check(item ~= nil, 'the entry is still addressable')
  check(item.Type == 2, 'Type is the raw kind field, got ' .. describe(item.Type))
  check(config:IsCollectible(1) == false, 'IsCollectible requires kind == 1')
  check(config:IsCollectible(2) == true, 'the neighbouring collectible is untouched')
end

local RUNNERS = {
  null_entry = verifyNullEntry,
  bad_string = verifyBadString,
  wrong_kind = verifyWrongKind,
}

local function run()
  frame = frame + 1
  if SCENARIO == 'valid' then
    if frame == 1 then
      verifyValid()
    elseif frame == 2 then
      verifyExpired()
    end
    SCENARIO_OK = 1
    return
  end
  local runner = RUNNERS[SCENARIO] or verifyUnavailable
  runner()
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


class LuaItemConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-item-config-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "item_config_harness.cpp"
        harness.write_text(
            HARNESS.replace("@SCRIPT@", SCRIPT).replace("@LONG_NAME@", LONG_NAME).lstrip()
        )
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
        cls.harness = temporary / "item_config_harness"
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

    def test_collectible_fields_and_parallel_vectors(self):
        """`GetCollectible(1)` 的字段、并列向量、越界/负数/非整数一律 nil、`HasTags` 恒假。

        伪造内存按反汇编结论摆放（`IC = Manager + 0x36538` 的内嵌对象、收藏品向量 733 项、
        条目 `+0x00` 类别 / `+0x04` id / `+0x08` 名字 / `+0x20` 描述），Lua 侧逐条断言；
        短串与长串（> 22 字节）两条 libc++ `std::string` 路径都在这一帧里被读到。
        """
        result = self.run_scenario("valid")
        self.assertIn("ITEM_CONFIG_OK scenario=valid", result.stdout)
        self.assertIn("PHASE1_OK", result.stdout)

    def test_stale_handles_degrade_and_new_handles_re_read_the_engine(self):
        """每次访问都重新校验：向量被重排后，旧句柄一律 nil，新句柄立刻读到新数据。

        harness 在两帧之间把收藏品向量重新指向另一块数组（`ItemConfig::Init` 的等价形态）。
        旧 `ItemConfig` 句柄缓存的 begin 与现场不一致 → `GetCollectible` 返回 nil；
        旧条目句柄的"向量 begin + 下标"不再匹配 → 字段返回 nil；
        同一帧新取的句柄必须读到新向量里的 "Brimstone"。
        """
        result = self.run_scenario("valid")
        self.assertIn("PHASE1_OK", result.stdout)
        self.assertIn("PHASE2_OK", result.stdout)

    def test_unreadable_links_return_nil_without_a_lua_error(self):
        """六种"整条链路拿不到"的形态都必须返回 nil，而且不抛 Lua 错误。

        * `no_base`：引擎模块基址根本没发布；
        * `slot_zero`：`g_Manager` 的槽本身是 0（第二级解引用不许读地址 0）；
        * `no_manager`：槽可读、但变量里是空指针；
        * `empty_vector`：`begin == end`（长度为 0）；
        * `bad_length`：`E - B` 不是 8 的倍数（0x16E8 + 4）；
        * `huge_length`：长度超过合理上限（4097 项）。
        """
        for scenario in ("no_base", "slot_zero", "no_manager", "empty_vector", "bad_length",
                         "huge_length"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn(f"ITEM_CONFIG_OK scenario={scenario}", result.stdout)

    def test_entry_and_string_level_failures_degrade_field_by_field(self):
        """条目级失败只影响那一条读数，不许把整个视图打断。

        * `null_entry`：条目指针为 0 → 该下标是 nil，相邻下标照常；
        * `bad_string`：`is_long` 置位但 size 离谱 → `Name` 是 nil，`ID`/`Type`/`Description`
          照常可读；
        * `wrong_kind`：收藏品向量里的条目带着 TRINKET 的类别 → `Type` 原样返回 2、
          `IsCollectible` 说 false。
        """
        for scenario in ("null_entry", "bad_string", "wrong_kind"):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn(f"ITEM_CONFIG_OK scenario={scenario}", result.stdout)


if __name__ == "__main__":
    unittest.main()

class ItemConfigTagConstantTests(unittest.TestCase):
    """`ItemConfig.TAG_*` 常量必须与 PC 文档逐条一致。

    真机报告 `01789202729`：EID 的 `features/eid_data.lua:1025` 第一件事就是
    `ItemConfig.TAG_GUPPY`，而 Runtime 此前**没有全局 `ItemConfig` 表**（只有实例元表），
    于是整包加载在那一行断掉。这里拿 isaacdocs 快照逐条核对，防止手抄的位号出错。
    """

    def test_tag_constants_match_the_pc_docs(self):
        docs = (
            ROOT / "analysis" / "isaacdocs-snapshot" / "docs" / "enums" / "ItemConfig.md"
        )
        source = (ROOT / "runtime" / "src" / "interfaces" / "lua" / "isaac_api.cpp").read_text(
            encoding="utf-8"
        )
        if not docs.is_file():
            self.skipTest(f"缺少 PC 文档快照：{docs}")
        expected: dict[str, int] = {}
        for line in docs.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\|.*?\|\s*1\s*<<\s*(\d+)\s*\|\s*(TAG_[A-Z0-9_]+)\s*\{:", line)
            if match is not None:
                expected[match.group(2)] = int(match.group(1))
        self.assertGreater(len(expected), 30, "PC 文档里应当有 30 条以上的 TAG_* 常量")
        published = {
            name: int(bit)
            for name, bit in re.findall(
                r'ItemConfigConstant\{"(TAG_[A-Z0-9_]+)",\s*(\d+)\}', source
            )
        }
        self.assertEqual(published, expected, "ItemConfig.TAG_* 与 PC 文档不一致")
        # 全局表必须真的发布出去（EID 用的是全局名，不是实例上的字段）。
        self.assertIn('lua_setglobal(state, "ItemConfig")', source)
