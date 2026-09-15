"""房间实体容器（`Room+0x1950` 的内嵌 `EntityList`）与查询 API 的宿主行为测试。

批次 4 交付的是 EID 显示链的最后一段：`Isaac.FindInRadius` / `Isaac.FindByType` 必须真的
枚举房间实体，否则 EID 永远没有可显示的内容（`main.lua:1466`）。偏移本身全部来自反汇编
静态定位（记在 `runtime_constants.hpp` 的"房间实体容器"段与 `docs/问题与解决记录.md` 批次 4），
所以这里要证明的是**我们这一侧**读得对不对、过滤得对不对、降级得对不对。

做法与 `test_lua_entity_player.py` 同形：用 `malloc` 出来的块伪造一份完整引擎内存

    模块块[base + 0xAAC698] = &g_Game 变量 → Game*
    Game + 0x21550          = Room*
    Room + 0x1950           = 内嵌 EntityList（带正确容量指纹）
    EL + 0x78 / +0x80 / +0x84 = 活表 begin / cap(0x800) / count
    EL + 0x50 / +0xE0         = 0x4000 / 0x8000（指纹的另两个成员）
    EL + 0xA8 / +0xB0 / +0xB4 = EFFECT 表
    Game + 0x25C50/+0x25C58   = 玩家向量
    Game + 0x24F99C           = 本局帧计数（FrameCount 的被减数）
    Game + 0x0C               = 诅咒位掩码（`Level* == Game*`）
    实体块首字                = 假模块的 vtable 区间内的地址

场景覆盖（每个都在下面有对应断言）：半径含 `Size`、严格 `<` 的边界、七个分区各自的过滤
（PLAYER 走玩家向量、EFFECT 走 `+0xA8`、其余走活表）、`FLAG_NO_QUERY` 跳过、空元素与
坏 vptr 元素跳过、去重（同一实体同时出现在活表与玩家向量里）、`FindByType` 的通配与
`Cache` 参数语义、`CountEnemies`、实体字段与 `FrameCount`、`GetData` 同实体稳定同表与
换房间失效、`ToPickup` 正反例、`GetPtrHash`、`GetActiveItem`/`GetTrinket`/`GetBabySkin`、
`Level:GetCurses`，以及四种降级形态（指纹不匹配 / count > cap / 基址未发布 / 实体 vptr 被改坏）。

★ 脚本里用的是**位掩码字面量**，但同一段脚本会用 `check` 把字面量与**生成的 PC 枚举表**
`EntityPartition.*` 逐项钉在一起（`FAMILIAR=1 … EFFECT=64`，EID 的 `searchPartitions` = 57）。
生成器曾经把 `1<<N` 解析成 `1`（2026-09-12 修复，见 `tools/generate_pc_lua_enum_tables.py` 与
`test_lua_global_pc_enums.py`）；把"字面量 == 枚举"写成断言之后，那条缺陷再回来会在**这里**
立刻失败，而不是让断言继续用字面量安静通过。harness 另外把读到的 `EntityPartition.PLAYER`
打印成证据行，Python 侧断言它等于 32。

批次 5（`Entity:GetSprite()` / `Sprite:GetAnimation()`）在同一份伪造内存里多铺一层：
实体块 `+0x50` 上那个内嵌 `ANM2`（偏移证据见 `runtime_constants.hpp` 的 `kEntitySpriteOffset`）：

    Entity + 0x50 + 0x149 = 1            （`Sprite:IsLoaded` 读的引擎标志）
    Entity + 0x50 + 0x38  = 假 std::string 对象地址（当前动画名）
    Entity + 0x48         = 毒值 0xDEADBEEF…（`ANM2` 前面那个字）

断言三件事：读值对不对（短串 `Idle` / 长串 `ShopIdle` / 无动画时空串）、跨帧句柄仍有效
（`0x38`→实体存活复核→`owner+0x50` 重新解析）、以及实体 vptr 被改坏之后**报 Lua 错误**
而不是继续解引用。`+0x48` 的毒值让"引擎 sprite 被当成我们自己分配的对象去 `free`"这件事
在宿主上立刻 abort，所以**所有权标记**是有可判定断言的（去掉它的变异会让这些用例红）。

两个已做过的变异实验（都证明用例非空转，不是空转的断言）：
  1. `DestroySpriteHandle` 忽略 `source` 标记 → 宿主 `free(0xDEADBEEF…)` → 进程 SIGABRT，4 个用例失败；
  2. `ResolveSpriteObject` 去掉 `IsLiveEntityPointer` 复核 → `corrupt_entity` 场景里
     "a sprite of a corrupted entity must refuse to be used" 失败；
  3. `ReadAnimationName` 忽略 libc++ 长串分支 → "GetAnimation must read a long libc++ string
     through +0x10" 失败。
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

// 伪造内存里的读数（单一真源：Python 侧的期望值由这些常量推导）。
constexpr std::uint32_t kFrameNow = 1000;
constexpr std::uint32_t kEnemySpawnFrame = 950;
constexpr std::uint32_t kCurses = 12;               // CURSE_OF_THE_LOST(4) | CURSE_OF_THE_UNKNOWN(8)
constexpr std::uint32_t kActiveItemSlot0 = 40;
constexpr std::uint32_t kTrinketSlot0 = 30;
constexpr std::int32_t kBabySkinNonBaby = -1;
constexpr std::uint32_t kPickupPrice = 15;
constexpr std::uint32_t kPickupOptionsIndex = 3;
constexpr std::uint32_t kInitSeed = 0x11223344;

constexpr std::size_t kEntityBlockSize = 0x648;    // 覆盖扫描区间(0x348)与 Pickup 字段(0x56C)

// --- 伪造的引擎内存 --------------------------------------------------------

enum EntitySlot {
    kPlayer = 0,
    kEnemyA,
    kPickup,
    kTearNear,
    kSizeOnly,
    kNoQuery,
    kFamiliar,
    kCharmed,
    kBullet,
    kFakePickup,
    kGarbage,
    kEffect,
    kEffectNoQuery,
    kEntitySlotCount,
};

std::vector<unsigned char> g_ModuleBytes;
std::vector<unsigned char> g_OwnerSlotBytes;
std::vector<unsigned char> g_GameBytes;
std::vector<unsigned char> g_RoomBytes;
std::vector<unsigned char> g_RoomBytesSecond;
std::vector<unsigned char> g_LiveArrayBytes;
std::vector<unsigned char> g_EffectArrayBytes;
std::vector<unsigned char> g_PlayerArrayBytes;
std::vector<std::vector<unsigned char>> g_Entities;

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

// `Entity` 家族 vtable 区间里的一个地址（区间是 `[base+0xA34F58, base+0xA38230)`）。
std::uintptr_t GenericEntityVtable() {
    return g_EngineBase + kEntityVtableRangeBeginOffset + 0x100;
}

void CreateBlocks() {
    g_ModuleBytes.assign(kGameOwnerGlobalSlotOffset + sizeof(std::uint64_t), 0);
    g_GameBytes.assign(kGamePlayerArrayCapacityOffset + sizeof(std::uint64_t), 0);
    g_RoomBytes.assign(kRoomEntityListOffset + 0x100, 0);
    g_RoomBytesSecond.assign(kRoomEntityListOffset + 0x100, 0);
    g_OwnerSlotBytes.assign(sizeof(std::uint64_t), 0);
    g_Entities.assign(kEntitySlotCount, {});
    for (std::size_t index = 0; index < kEntitySlotCount; ++index) {
        const std::size_t size = index == kPlayer ? kEntityPlayerSize : kEntityBlockSize;
        g_Entities[index].assign(size, 0);
    }
    g_EngineBase = AddressOf(g_ModuleBytes);
}

void FillEntity(EntitySlot slot, std::uint32_t type, std::uint32_t variant, std::uint32_t subType,
                float x, float y, float size, std::uint32_t index, std::uint32_t spawnFrame,
                std::uintptr_t vtable, std::uint64_t flags) {
    std::vector<unsigned char>& block = g_Entities[slot];
    WriteWord(block, 0, vtable);
    WriteAt<std::uint64_t>(block, kEntityFlagsOffset, flags);
    WriteAt<std::uint32_t>(block, kEntityTypeOffset, type);
    WriteAt<std::uint32_t>(block, kEntityVariantOffset, variant);
    WriteAt<std::uint32_t>(block, kEntitySubTypeOffset, subType);
    WriteAt<std::uint32_t>(block, kEntityIndexOffset, index);
    WriteAt<float>(block, kEntityPositionOffset, x);
    WriteAt<float>(block, kEntityPositionOffset + sizeof(float), y);
    WriteAt<float>(block, kEntitySizeOffset, size);
    WriteAt<std::uint32_t>(block, kEntitySpawnFrameOffset, spawnFrame);
    WriteAt<std::uint32_t>(block, kEntityInitSeedOffset, kInitSeed);
}

constexpr std::uint64_t kNoQueryFlag = 1ULL << kEntityFlagNoQueryBit;

// --- 伪造的 sprite 内容（`Entity + kEntitySpriteOffset` 上的内嵌 ANM2）----------
//
// `Entity:GetSprite()` 返回的就是这个对象，所以这里铺上两个**引擎自己会读**的字段：
//   * `ANM2 + kSpriteLoadedFlagOffset(0x149)`：图形已加载标志（`Sprite:IsLoaded`）；
//   * `ANM2 + kSpriteAnimationNameOffset(0x38)`：当前动画名（libc++ 24 字节 `std::string`，
//     `Sprite:GetAnimation`）。
// 两种 string 形态都要造（引擎自己的 `ANM2::IsPlaying` 就分这两支）：
//   * 短串（SSO）：内容内联在对象 +1，首字节 = `size << 1`；
//   * 长串：首字节最低位为 1，数据指针在对象的 +0x10。
constexpr std::size_t kSpriteNameObjectOffset = 0x580;
constexpr std::size_t kSpriteLongNameOffset = 0x5A0;
constexpr char kEnemyAnimationName[] = "Idle";
constexpr char kPickupAnimationName[] = "ShopIdle";

// `ANM2` 起始处**前面**那个字（`entity+0x48`）在真实引擎里没有已知含义，这里放一个毒值：
// `ReleaseSpriteStorage` 会把它当作"malloc 基址"读出来。于是"引擎 sprite 被当成我们自己
// 分配的对象去 free"这件事在宿主上会**立刻 abort**，而不是安静地踩坏堆 —— 所有权标记的
// 变异测试就靠它变得可判定（见测试文档字符串）。
constexpr std::uint64_t kSpritePoisonWord = 0xDEADBEEFDEADBEEFULL;

// `longForm` 强制用 libc++ 的**长串**编码（首字节最低位 = 1、数据指针在 +0x10）。这不是
// 造假的布局：libc++ 只要容量超过 SSO 就会用长串（`reserve` 过、或名字本身超过 22 字节），
// 所以两条分支都必须读对 —— 而且只有两种形态都造出来，`ReadAnimationName` 的分支才是被
// 真正覆盖的（否则"只看短串"的变异会安静通过）。
void FillEntitySprite(EntitySlot slot, bool loaded, const char* name, bool longForm) {
    std::vector<unsigned char>& block = g_Entities[slot];
    WriteWord(block, kEntitySpriteOffset - sizeof(std::uint64_t), kSpritePoisonWord);
    if (!loaded) {
        return;  // 图形未加载、也没有当前动画：`IsLoaded()==false`、`GetAnimation()==''`
    }
    WriteAt<std::uint8_t>(block, kEntitySpriteOffset + kSpriteLoadedFlagOffset, 1);
    if (name == nullptr) {
        return;
    }
    const std::size_t length = std::strlen(name);
    if (longForm) {
        WriteAt<std::uint8_t>(block, kSpriteNameObjectOffset, kLibcxxStringLongFlag);
        std::memcpy(block.data() + kSpriteLongNameOffset, name, length + 1);
        WriteWord(block, kSpriteNameObjectOffset + kLibcxxStringDataOffset,
                  AddressOf(block) + kSpriteLongNameOffset);
    } else {
        WriteAt<std::uint8_t>(block, kSpriteNameObjectOffset,
                              static_cast<std::uint8_t>(length << 1));
        std::memcpy(block.data() + kSpriteNameObjectOffset + kLibcxxStringShortDataOffset, name,
                    length + 1);
    }
    WriteWord(block, kEntitySpriteOffset + kSpriteAnimationNameOffset,
              AddressOf(block) + kSpriteNameObjectOffset);
}

// 活表里的实体（下标就是房间实体表顺序）。位置/Size 的设计让每条判据都能被单独验证：
//   * `kEnemyA` 距查询点正好 20、Size 10 → 半径 10 时 `dist² < (10+10)²` 取**严格小于**，必须落空；
//     半径 10.5 时必须命中（证明 `Size` 真的加进了半径）。
//   * `kSizeOnly` 距查询点 25、Size 30 → 半径 10 时只有把 `Size` 加进半径才会命中。
//   * `kNoQuery` 置 `FLAG_NO_QUERY`；`kGarbage` 的 vptr 不在 `Entity` 区间；`kFakePickup`
//     是 `Type==5` 但 vptr 不是 `Entity_Pickup`。三者在查询里都必须被跳过。
void FillEntities() {
    FillEntity(kPlayer, kEntityTypePlayer, 0, 0, 80.0F, 280.0F, 12.5F, 0, 10,
               g_EngineBase + kEntityPlayerVtableOffset, 0);
    std::vector<unsigned char>& player = g_Entities[kPlayer];
    WriteAt<std::uint32_t>(player, kEntityPlayerTypeOffset, 0);
    WriteAt<std::uint32_t>(player, kEntityPlayerControllerIndexOffset, 0);
    WriteAt<std::uint32_t>(player, kEntityPlayerRedHeartContainersOffset, 6);
    WriteAt<std::uint32_t>(player, kEntityPlayerActiveItemOffset, kActiveItemSlot0);
    WriteAt<std::uint32_t>(player, kEntityPlayerActiveItemOffset + kEntityPlayerActiveItemStride, 0);
    WriteAt<std::uint32_t>(player, kEntityPlayerTrinketOffset, kTrinketSlot0);
    WriteAt<std::uint32_t>(player, kEntityPlayerTrinketOffset + sizeof(std::uint32_t), 0);
    WriteAt<std::int32_t>(player, kEntityPlayerBabySkinOffset, kBabySkinNonBaby);
    // `+0x19F0`（玩家在 `players` 向量里的下标）与 `+0x30`（房间实体表序号）刻意写成不同的值：
    // 批次 4 起 `Index` 读的是 `+0x30`，`+0x19F0` 不再作为任何 Lua 字段暴露。
    WriteAt<std::uint32_t>(player, kEntityPlayerIndexOffset, 0);

    FillEntity(kEnemyA, 20, 5, 0, 100.0F, 280.0F, 10.0F, 1, kEnemySpawnFrame,
               GenericEntityVtable(), 0);
    FillEntity(kPickup, kEntityTypePickup, 100, 1, 85.0F, 285.0F, 8.0F, 2, 900,
               g_EngineBase + kEntityPickupVtableOffset, 0);
    std::vector<unsigned char>& pickup = g_Entities[kPickup];
    WriteAt<std::uint32_t>(pickup, kEntityPickupPriceOffset, kPickupPrice);
    WriteAt<std::int32_t>(pickup, kEntityPickupShopItemIdOffset, 0);
    WriteAt<std::uint32_t>(pickup, kEntityPickupOptionsIndexOffset, kPickupOptionsIndex);
    WriteAt<std::uint8_t>(pickup, kEntityPickupForceBlindOffset, 0);
    WriteAt<std::uint8_t>(pickup, kEntityPickupTouchedOffset, 1);

    FillEntity(kTearNear, 2, 0, 0, 80.0F, 240.0F, 4.0F, 3, 900, GenericEntityVtable(), 0);
    FillEntity(kSizeOnly, 24, 7, 0, 80.0F, 305.0F, 30.0F, 4, 900, GenericEntityVtable(), 0);
    FillEntity(kNoQuery, 22, 9, 0, 80.0F, 280.0F, 5.0F, 5, 900, GenericEntityVtable(),
               kNoQueryFlag);
    // 真跟班：`Type == 3` 且 vptr **精确**等于 `Entity_Familiar` 的 vtable ——
    // `Entity:ToFamiliar()` 的判据要求两者同时成立（见 `kEntityFamiliarVtableOffset`）。
    FillEntity(kFamiliar, 3, 5, 0, 80.0F, 280.0F, 5.0F, 6, 900,
               g_EngineBase + kEntityFamiliarVtableOffset, 0);
    // `Type 3 && Variant 0xEF` → 引擎 `collide()` 把它算进 ENEMY。
    FillEntity(kCharmed, 3, 0xEF, 0, 80.0F, 280.0F, 5.0F, 7, 900, GenericEntityVtable(), 0);
    FillEntity(kBullet, 9, 0, 0, 80.0F, 280.0F, 3.0F, 8, 900, GenericEntityVtable(), 0);
    // 远处（查询点 100 半径外）的 `Type==5`：vptr 不是 `Entity_Pickup`，`ToPickup()` 必须 nil。
    FillEntity(kFakePickup, kEntityTypePickup, 100, 1, 900.0F, 900.0F, 8.0F, 9, 900,
               GenericEntityVtable(), 0);
    // vptr 落在区间外：任何查询都必须当它不存在。
    FillEntity(kGarbage, 20, 5, 0, 80.0F, 280.0F, 10.0F, 11, 900, 0xDEADBEEFULL, 0);

    FillEntity(kEffect, kEntityTypeEffect, 7, 0, 80.0F, 280.0F, 6.0F, 0, 900, GenericEntityVtable(),
               0);
    FillEntity(kEffectNoQuery, kEntityTypeEffect, 8, 0, 80.0F, 280.0F, 6.0F, 1, 900,
               GenericEntityVtable(), kNoQueryFlag);

    // 内嵌 ANM2（`Entity:GetSprite()`）的内容：
    //   * `kEnemyA`：短串动画名 "Idle"；
    //   * `kPickup`：用**长串编码**写的动画名 "ShopIdle"（数据指针在 string 对象 +0x10）；
    //   * `kFamiliar`：图形未加载、没有当前动画（`false` + 空串）。
    FillEntitySprite(kEnemyA, true, kEnemyAnimationName, false);
    FillEntitySprite(kPickup, true, kPickupAnimationName, true);
    FillEntitySprite(kFamiliar, false, nullptr, false);
}

// `Room + 0x1950` 的内嵌 `EntityList`：活表、EFFECT 表与三个容量指纹。
void PublishEntityList(std::vector<unsigned char>& room, bool validFingerprint) {
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListGeneralCapacityOffset,
                           validFingerprint ? kEntityListGeneralCapacity : 0U);
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListLiveCapacityOffset,
                           validFingerprint ? kEntityListLiveCapacity : 0U);
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListArenaCapacityOffset,
                           validFingerprint ? kEntityListArenaCapacity : 0U);
    WriteWord(room, kRoomEntityListOffset + kEntityListLiveBeginOffset, AddressOf(g_LiveArrayBytes));
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListLiveCountOffset, 12U);
    WriteWord(room, kRoomEntityListOffset + kEntityListEffectBeginOffset,
              AddressOf(g_EffectArrayBytes));
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListEffectCapacityOffset,
                           kEntityListGeneralCapacity);
    WriteAt<std::uint32_t>(room, kRoomEntityListOffset + kEntityListEffectCountOffset, 2U);
}

// 活表：12 个槽，最后两个是"空指针"与"vptr 越界"的坏元素。
void PublishLists() {
    const std::uintptr_t live[] = {
        AddressOf(g_Entities[kPlayer]),   AddressOf(g_Entities[kEnemyA]),
        AddressOf(g_Entities[kPickup]),   AddressOf(g_Entities[kTearNear]),
        AddressOf(g_Entities[kSizeOnly]), AddressOf(g_Entities[kNoQuery]),
        AddressOf(g_Entities[kFamiliar]), AddressOf(g_Entities[kCharmed]),
        AddressOf(g_Entities[kBullet]),   AddressOf(g_Entities[kFakePickup]),
        0,                                AddressOf(g_Entities[kGarbage]),
    };
    g_LiveArrayBytes.assign(sizeof(live), 0);
    for (std::size_t index = 0; index < sizeof(live) / sizeof(live[0]); ++index) {
        WriteWord(g_LiveArrayBytes, index * sizeof(std::uint64_t), live[index]);
    }
    const std::uintptr_t effects[] = {AddressOf(g_Entities[kEffect]),
                                      AddressOf(g_Entities[kEffectNoQuery])};
    g_EffectArrayBytes.assign(sizeof(effects) + sizeof(std::uint64_t), 0);
    for (std::size_t index = 0; index < sizeof(effects) / sizeof(effects[0]); ++index) {
        WriteWord(g_EffectArrayBytes, index * sizeof(std::uint64_t), effects[index]);
    }
    // 玩家向量：`kPlayer` 同时出现在活表与向量里 —— 查询必须去重（否则 `FindByType(1)` 会给两个）。
    g_PlayerArrayBytes.assign(2 * sizeof(std::uint64_t), 0);
    WriteWord(g_PlayerArrayBytes, 0, AddressOf(g_Entities[kPlayer]));
}

void PublishGame() {
    WriteWord(g_ModuleBytes, kGameOwnerGlobalSlotOffset, AddressOf(g_OwnerSlotBytes));
    WriteWord(g_OwnerSlotBytes, 0, AddressOf(g_GameBytes));
    WriteWord(g_GameBytes, kGameRoomPointerOffset, AddressOf(g_RoomBytes));
    WriteWord(g_GameBytes, kGamePlayerArrayBeginOffset, AddressOf(g_PlayerArrayBytes));
    WriteWord(g_GameBytes, kGamePlayerArrayEndOffset,
              AddressOf(g_PlayerArrayBytes) + sizeof(std::uint64_t));
    WriteAt<std::uint32_t>(g_GameBytes, kGameFrameCountOffset, kFrameNow);
    // `Level` 内嵌在 `Game` 起始处 → `Level+0x0C` 就是 `Game+0x0C`。
    WriteAt<std::uint32_t>(g_GameBytes, kLevelCursesOffset, kCurses);
}

// --- 注入的"引擎方法" ------------------------------------------------------

std::uintptr_t g_CallEntity = 0;
std::uint32_t g_CallId = 0;
int g_CallCount = 0;

bool HarnessHasCollectible(void* entity, std::uint32_t collectibleId) {
    g_CallEntity = reinterpret_cast<std::uintptr_t>(entity);
    g_CallId = collectibleId;
    ++g_CallCount;
    return collectibleId == 1;
}

std::string BuildPrelude() {
    std::string text = "EXPECT = {\n";
    const char* names[] = {"player",  "enemyA",       "pickup",     "tearNear", "sizeOnly",
                           "noQuery", "familiar",     "charmed",    "bullet",   "fakePickup",
                           "garbage", "effect",       "effectNoQuery"};
    const EntitySlot slots[] = {kPlayer,  kEnemyA,       kPickup,     kTearNear, kSizeOnly,
                                kNoQuery, kFamiliar,     kCharmed,    kBullet,   kFakePickup,
                                kGarbage, kEffect,       kEffectNoQuery};
    for (std::size_t index = 0; index < sizeof(slots) / sizeof(slots[0]); ++index) {
        text += "  ";
        text += names[index];
        text += " = ";
        text += std::to_string(AddressOf(g_Entities[slots[index]]));
        text += ",\n";
    }
    text += "}\n";
    text += "FRAME_NOW = " + std::to_string(kFrameNow) + "\n";
    text += "ENEMY_SPAWN_FRAME = " + std::to_string(kEnemySpawnFrame) + "\n";
    text += "EXPECTED_CURSES = " + std::to_string(kCurses) + "\n";
    text += "EXPECTED_ACTIVE_ITEM = " + std::to_string(kActiveItemSlot0) + "\n";
    text += "EXPECTED_TRINKET = " + std::to_string(kTrinketSlot0) + "\n";
    text += "EXPECTED_BABY_SKIN = " + std::to_string(kBabySkinNonBaby) + "\n";
    text += "EXPECTED_PICKUP_PRICE = " + std::to_string(kPickupPrice) + "\n";
    text += "EXPECTED_OPTIONS_INDEX = " + std::to_string(kPickupOptionsIndex) + "\n";
    text += "EXPECTED_INIT_SEED = " + std::to_string(kInitSeed) + "\n";
    return text;
}

bool ExpectNumber(const char* name, double expected) {
    double value = 0.0;
    if (!LuaRuntime::ReadLuaGlobalNumber(name, &value)) {
        std::printf("QUERY_FAIL: global %s was never written\n", name);
        return false;
    }
    if (value != expected) {
        std::printf("QUERY_FAIL: %s expected %.0f, got %.0f\n", name, expected, value);
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const std::string scenario = argv[1];
    if (scenario != "valid" && scenario != "room_change" && scenario != "corrupt_entity" &&
        scenario != "bad_fingerprint" && scenario != "count_too_large" && scenario != "no_base") {
        return 91;
    }

    CreateBlocks();
    FillEntities();
    PublishLists();
    const bool publishBase = scenario != "no_base";
    if (publishBase) {
        LuaRuntime::SetEngineModuleBase(g_EngineBase);
        PublishGame();
        PublishEntityList(g_RoomBytes, scenario != "bad_fingerprint");
        PublishEntityList(g_RoomBytesSecond, true);
        if (scenario == "count_too_large") {
            // count 超过容量 → 活表整张作废（EFFECT 表不受影响）。
            WriteAt<std::uint32_t>(g_RoomBytes,
                                   kRoomEntityListOffset + kEntityListLiveCountOffset,
                                   kEntityListLiveCapacity + 1);
        }
    }

    isaac::runtime::SetEntityPlayerHasCollectibleHostImplementation(&HarnessHasCollectible);

    const std::string script =
        "SCENARIO = '" + scenario + "'\n" + BuildPrelude() + kScriptTemplate;
    const auto result =
        LuaRuntime::InitializeFromBuffer(script.c_str(), script.size(), "@entity_query.lua");
    if (result != LuaRuntime::LuaInitResult::Success) {
        std::printf("QUERY_FAIL: the Lua state did not initialize\n");
        return 2;
    }

    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("QUERY_FAIL: the Lua callback raised an error on the first frame\n");
        return 1;
    }

    if (scenario == "room_change") {
        // 换房间：`Room*` 换成另一块（活表内容不变）→ ModData 的 epoch 必须前进。
        WriteWord(g_GameBytes, kGameRoomPointerOffset, AddressOf(g_RoomBytesSecond));
    } else if (scenario == "corrupt_entity") {
        // 句柄必须自证失效：把 `kEnemyA` 的 vptr 改到区间外。
        WriteWord(g_Entities[kEnemyA], 0, 0xDEADBEEFULL);
    }

    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) {
        std::printf("QUERY_FAIL: the Lua callback raised an error on the second frame\n");
        return 1;
    }

    if (!ExpectNumber("SCENARIO_OK", 1.0)) return 1;
    if (!ExpectNumber("PHASE1_OK", 1.0)) return 1;
    if (!ExpectNumber("PHASE2_OK", 1.0)) return 1;

    double partitionPlayer = 0.0;
    if (LuaRuntime::ReadLuaGlobalNumber("PARTITION_PLAYER_SEEN", &partitionPlayer)) {
        // Python 侧断言这一行等于 32（生成的 PC 枚举表里 `EntityPartition.PLAYER`）。
        std::printf("ENTITY_PARTITION_PLAYER_SEEN=%.0f\n", partitionPlayer);
    }
    if (scenario != "valid") {
        std::printf("ENTITY_QUERY_DEGRADED scenario=%s engine_calls=%d\n", scenario.c_str(),
                    g_CallCount);
        return 0;
    }
    if (g_CallCount != 2) {
        std::printf("QUERY_FAIL: the cast player must reach HasCollectible exactly twice, got %d\n",
                    g_CallCount);
        return 1;
    }
    if (g_CallEntity != AddressOf(g_Entities[kPlayer])) {
        std::printf("QUERY_FAIL: x0 must be the Entity_Player address\n");
        return 1;
    }
    if (g_CallId != 2) {
        std::printf("QUERY_FAIL: the last collectible id must be 2, got %u\n", g_CallId);
        return 1;
    }
    std::printf("ENTITY_QUERY_OK scenario=%s engine_calls=%d\n", scenario.c_str(), g_CallCount);
    // 两帧的 Lua 侧结论都已经由 `ExpectNumber` 核对过，这里只是把标记打出来给 Python 侧看。
    std::printf("PHASE1_OK PHASE2_OK\n");
    return 0;
}
'''


SCRIPT = r'''
local mod = RegisterMod('Room entity query', 1)

local function check(condition, message)
  if not condition then error(message, 2) end
end

local function describe(value)
  if value == nil then return 'nil' end
  return type(value) .. ':' .. tostring(value)
end

-- ★ 分区位用**字面量**表示，但下面立刻把它们与生成的 PC 枚举表逐项钉死：两边的数值必须
-- 一致（生成器曾把 `1<<N` 解析成 `1`；那条缺陷再回来就会在这里报错）。
local FAMILIAR, BULLET, TEAR, ENEMY, PICKUP, PLAYER, EFFECT = 1, 2, 4, 8, 16, 32, 64
check(EntityPartition.FAMILIAR == FAMILIAR and EntityPartition.BULLET == BULLET and
      EntityPartition.TEAR == TEAR and EntityPartition.ENEMY == ENEMY and
      EntityPartition.PICKUP == PICKUP and EntityPartition.PLAYER == PLAYER and
      EntityPartition.EFFECT == EFFECT,
      'EntityPartition 与字面量掩码不一致: PLAYER=' .. tostring(EntityPartition.PLAYER))
local ALL_PARTITIONS = FAMILIAR + BULLET + TEAR + ENEMY + PICKUP + PLAYER + EFFECT  -- 127
-- EID 的 `searchPartitions`（`main.lua:1304`）：FAMILIAR + ENEMY + PICKUP + PLAYER = 57。
local EID_SEARCH_PARTITIONS = FAMILIAR + ENEMY + PICKUP + PLAYER
check(EntityPartition.FAMILIAR + EntityPartition.ENEMY + EntityPartition.PICKUP +
      EntityPartition.PLAYER == EID_SEARCH_PARTITIONS,
      'EID searchPartitions 掩码不一致')

-- 同时交给 harness 打印（Python 侧断言 = 32）。
PARTITION_PLAYER_SEEN = EntityPartition.PLAYER

-- 数组契约：整数键 1..n、中间无洞、n+1 为 nil、没有 0 号键。
local function checkArray(entities, expectedCount, message)
  check(type(entities) == 'table', message .. ': expected a table, got ' .. describe(entities))
  check(#entities == expectedCount,
        message .. ': expected ' .. expectedCount .. ' entries, got ' .. tostring(#entities))
  for index = 1, expectedCount do
    check(entities[index] ~= nil, message .. ': entry ' .. index .. ' is nil')
  end
  check(entities[expectedCount + 1] == nil, message .. ': the array has extra entries')
  check(entities[0] == nil, message .. ': a Lua array starts at 1')
end

local function hashOf(entity)
  local key = GetPtrHash(entity)
  check(type(key) == 'number', 'GetPtrHash must return a number, got ' .. describe(key))
  return key
end

-- 结果集合必须**恰好**是这些实体（数量、身份、无重复）。
local function checkSet(entities, names, message)
  local seen = {}
  for _, entity in ipairs(entities) do
    local key = hashOf(entity)
    check(key ~= 0, message .. ': GetPtrHash must not be 0 for a live entity')
    seen[key] = (seen[key] or 0) + 1
  end
  for _, name in ipairs(names) do
    local key = EXPECT[name]
    check(key ~= nil, message .. ': unknown expectation name ' .. name)
    if seen[key] ~= 1 then
      print('DEBUG name=' .. name .. ' seen=' .. tostring(seen[key]) .. ' key=' .. tostring(key)
            .. ' expected=' .. tostring(EXPECT[name]))
      for k, v in pairs(seen) do print('DEBUG hash=' .. tostring(k) .. ' x' .. tostring(v)) end
    end
    check(seen[key] == 1, message .. ': ' .. name .. ' must appear exactly once')
  end
  local size = 0
  for key, count in pairs(seen) do
    check(count == 1, message .. ': duplicate entity ' .. tostring(key))
    check(key ~= 0, message .. ': a zero hash is not a valid result')
    size = size + 1
  end
  check(size == #names, message .. ': expected ' .. #names .. ' entities, got ' .. size)
end

-- ===== 查询：半径语义与分区过滤 =====
local function verifyRadiusAndPartitions()
  local center = Vector(80, 280)

  -- 全部七个分区、半径 100：活表 8 个（空指针/坏 vptr/FLAG_NO_QUERY 三个都被跳过）
  -- + 玩家向量 1 个（与活表重复 → 必须去重）+ EFFECT 表 1 个 = 9。
  checkArray(Isaac.FindInRadius(center, 100, ALL_PARTITIONS), 9, 'all partitions')
  checkSet(Isaac.FindInRadius(center, 100, ALL_PARTITIONS),
           { 'player', 'enemyA', 'pickup', 'tearNear', 'sizeOnly', 'familiar', 'charmed',
             'bullet', 'effect' }, 'all partitions')

  -- EID 的真实掩码（57）：FAMILIAR+ENEMY+PICKUP+PLAYER，TEAR/BULLET/EFFECT 都要排除。
  checkSet(Isaac.FindInRadius(center, 100, EID_SEARCH_PARTITIONS),
           { 'player', 'enemyA', 'pickup', 'sizeOnly', 'familiar', 'charmed' },
           'EID searchPartitions')

  -- 半径含 Size，且是**严格** `dist² < (radius + Size)²`：
  --   enemyA 距 20、Size 10 → 半径 10 时 400 < 400 为假（落空），半径 10.5 时命中。
  --   sizeOnly 距 25、Size 30 → 只有把 Size 加进半径才会命中（25 < 10 为假）。
  checkSet(Isaac.FindInRadius(center, 10, ENEMY), { 'charmed', 'sizeOnly' }, 'radius 10 ENEMY')
  checkSet(Isaac.FindInRadius(center, 10.5, ENEMY), { 'charmed', 'sizeOnly', 'enemyA' },
           'radius 10.5 ENEMY')

  -- 半径 0 也**不是**空集：`Size` 覆盖查询点的实体会命中（PC 语义）。
  checkSet(Isaac.FindInRadius(center, 0, ALL_PARTITIONS),
           { 'player', 'pickup', 'sizeOnly', 'familiar', 'charmed', 'bullet', 'effect' },
           'radius 0')

  -- 逐个分区。
  checkSet(Isaac.FindInRadius(center, 100, FAMILIAR), { 'familiar' }, 'FAMILIAR')
  -- `Entity:ToFamiliar()`（批次 9，与 `ToPickup` 同一形态）：真跟班（`Type==3` 且 vptr 是
  -- `Entity_Familiar`）必须非 nil；被魅惑的实体（`Type==3`、`Variant==0xEF`、vptr 是通用实体）
  -- 必须 nil —— 否则 EID 会把敌人当跟班。
  local familiars = Isaac.FindInRadius(center, 100, FAMILIAR)
  check(#familiars == 1 and familiars[1]:ToFamiliar() ~= nil,
        'a real familiar must answer ToFamiliar with an object')
  for _, entity in ipairs(Isaac.FindInRadius(center, 100, ENEMY)) do
    if entity.Variant == 0xEF then
      check(entity:ToFamiliar() == nil, 'charmed must not be reported as a familiar')
    end
  end
  checkSet(Isaac.FindInRadius(center, 100, BULLET), { 'bullet' }, 'BULLET')
  checkSet(Isaac.FindInRadius(center, 100, TEAR), { 'tearNear' }, 'TEAR')
  checkSet(Isaac.FindInRadius(center, 100, PICKUP), { 'pickup' }, 'PICKUP')
  checkSet(Isaac.FindInRadius(center, 100, PLAYER), { 'player' }, 'PLAYER')
  checkSet(Isaac.FindInRadius(center, 100, EFFECT), { 'effect' }, 'EFFECT')
  checkSet(Isaac.FindInRadius(center, 100, ENEMY), { 'enemyA', 'sizeOnly', 'charmed' }, 'ENEMY')
  -- 普通分区 + EFFECT 同时命中：两段结果必须合并去重（EFFECT 只从 `EL+0xA8` 来）。
  checkSet(Isaac.FindInRadius(center, 100, ENEMY + EFFECT),
           { 'enemyA', 'sizeOnly', 'charmed', 'effect' }, 'ENEMY+EFFECT')

  -- 整值浮点必须和整数同义（PC 的 `luaL_checkinteger` 就收 `57.0`；JSON 配置里的数字都是
  -- float，判成"参数不可用"会安静地返回空表）。
  checkSet(Isaac.FindInRadius(center, 100.0, 8.0), { 'enemyA', 'sizeOnly', 'charmed' },
           'float mask/radius')
  checkSet(Isaac.FindByType(20.0, 5.0, 0.0), { 'enemyA' }, 'float type arguments')
  check(Isaac.GetPlayer(0):GetActiveItem(0.0) == EXPECTED_ACTIVE_ITEM, 'float active slot')
  check(Isaac.GetPlayer(0):GetTrinket(0.0) == EXPECTED_TRINKET, 'float trinket slot')
  check(Isaac.GetPlayer(0):GetActiveItem(0.5) == 0, 'a fractional slot is not usable')

  -- 掩码 0 → 没有任何分区 → 空表（但仍然是"数组"）。
  checkArray(Isaac.FindInRadius(center, 100, 0), 0, 'mask 0')
  -- 缺第三个参数时用 PC 的默认值（0xFFFFFFFF = 全部分区）。
  checkArray(Isaac.FindInRadius(center, 100), 9, 'default partitions')
  -- 参数不可用 → 空表，不报错。
  checkArray(Isaac.FindInRadius(42, 100, ALL_PARTITIONS), 0, 'non-Vector position')
  checkArray(Isaac.FindInRadius(center, -5, ALL_PARTITIONS), 0, 'negative radius')
  checkArray(Isaac.FindInRadius(center, 0 / 0, ALL_PARTITIONS), 0, 'NaN radius')
  checkArray(Isaac.FindInRadius(), 0, 'no arguments')

  -- 以 (900,900) 为中心的查询只应看到那个远处的实体（半径 100 的查询看不到它）。
  checkSet(Isaac.FindInRadius(Vector(900, 900), 10, PICKUP), { 'fakePickup' }, 'far query')
end

-- ===== FindByType：通配、跨表、去重、Cache 参数 =====
local function verifyFindByType()
  checkSet(Isaac.FindByType(20, 5, 0, false, false), { 'enemyA' }, 'FindByType exact')
  checkSet(Isaac.FindByType(20, -1, -1, false, false), { 'enemyA' }, 'FindByType variant wildcard')
  checkSet(Isaac.FindByType(20, 5, 99), {}, 'FindByType subtype mismatch')
  checkSet(Isaac.FindByType(999, -1, -1), {}, 'FindByType unknown type')
  checkSet(Isaac.FindByType(-1, -1, -1), {}, 'FindByType negative type')
  checkSet(Isaac.FindByType(3, -1, -1), { 'familiar', 'charmed' }, 'FindByType familiar family')
  -- 玩家同时在活表与玩家向量里：必须只返回一个。
  checkSet(Isaac.FindByType(1, -1, -1), { 'player' }, 'FindByType player dedup')
  -- EFFECT 只存在于 `EL+0xA8` 表里；`FLAG_NO_QUERY` 的那个必须被排除。
  checkSet(Isaac.FindByType(1000, 7, -1), { 'effect' }, 'FindByType effect')
  checkSet(Isaac.FindByType(1000, -1, -1), { 'effect' }, 'FindByType effect no-query filter')
  -- `FLAG_NO_QUERY` 的 ENEMY（Type 22）必须被排除；坏 vptr 的 Type 20 实体也不许出现。
  checkSet(Isaac.FindByType(22, -1, -1), {}, 'FindByType no-query entity')
  checkSet(Isaac.FindByType(20, -1, -1, false, false), { 'enemyA' }, 'FindByType garbage vptr')

  -- ★ 第 4 个参数是忏悔版的 `Cache`（纯性能提示），**不是**旧文档里的 `Nearest`：
  -- EID 用 `Isaac.FindByType(5, 100, -1, true, false)` 拿房间里**所有**收藏品底座
  -- （`main.lua:366`/`826`/`1133`），所以传 true 时必须仍然返回全部两个。
  checkSet(Isaac.FindByType(5, 100, -1, true, false), { 'pickup', 'fakePickup' }, 'FindByType cache=true')

  checkArray(Isaac.FindByType(), 0, 'FindByType no arguments')
  checkArray(Isaac.FindByType('nope', -1, -1), 0, 'FindByType non-integer type')
  checkArray(Isaac.FindByType(20), 1, 'FindByType default wildcards')
end

-- ===== CountEnemies / CountBosses =====
local function verifyCounts()
  -- `IsEnemy = (u32)(Type - 10) < 0x3DE`：活表里 Type 20/22/24 三个命中。
  --   * `kNoQuery`（Type 22）**照样计数**：`FLAG_NO_QUERY` 只作用于查询结果，没有证据
  --     说明引擎的 `CountType` 会跳过它；
  --   * `kCharmed`（Type 3 && Variant 0xEF）**不**计数：`collide()` 把它算进 ENEMY，但任务
  --     给定的 CountEnemies 判据只有那条类型区间比较（两条已知偏差都写在 `isaac_api.cpp`）。
  --   * 坏 vptr 的 Type 20 实体不计数（vptr 闸门先否决）。
  check(Isaac.CountEnemies() == 3, 'CountEnemies must count 3, got ' .. tostring(Isaac.CountEnemies()))
  -- `CountBosses` 仍是安全 stub：本轮没有任何 boss 判据的证据。
  check(Isaac.CountBosses() == 0, 'CountBosses must stay 0 (no evidence for a boss test)')
end

-- ===== 实体字段 / FrameCount / GetPtrHash / GetData / ToPickup =====
local function verifyEntityView()
  local enemy = Isaac.FindByType(20, 5, 0, false, false)[1]
  check(enemy ~= nil, 'the enemy must be found')
  check(enemy.Type == 20, 'Type must be 20, got ' .. describe(enemy.Type))
  check(enemy.Variant == 5, 'Variant must be 5, got ' .. describe(enemy.Variant))
  check(enemy.SubType == 0, 'SubType must be 0, got ' .. describe(enemy.SubType))
  -- `Entity.Index` = `Entity+0x30`（`Room::AddEntity` 的 `str w8,[x1,#0x30]`），不是 `+0x19F0`。
  check(enemy.Index == 1, 'Index must be the room list index (1), got ' .. describe(enemy.Index))
  check(type(enemy.Size) == 'number' and math.abs(enemy.Size - 10) < 0.001,
        'Size must be 10, got ' .. describe(enemy.Size))
  check(math.abs(enemy.Position.X - 100) < 0.001 and math.abs(enemy.Position.Y - 280) < 0.001,
        'Position must be (100, 280)')
  -- `FrameCount = [Game+0x24F99C] - [entity+0x2F4]`
  check(enemy.FrameCount == FRAME_NOW - ENEMY_SPAWN_FRAME,
        'FrameCount must be ' .. (FRAME_NOW - ENEMY_SPAWN_FRAME) .. ', got '
        .. describe(enemy.FrameCount))
  check(enemy.NotAField == nil, 'an unknown field must be nil')
  -- `Entity.InitSeed`（`+0x3E8`）：EID 把它当**表键**（`main.lua:206`/`264`），
  -- 所以必须是数字而不是 nil（`table[nil] = x` 是硬错误）。
  check(enemy.InitSeed == EXPECTED_INIT_SEED,
        'InitSeed must be the spawn seed, got ' .. describe(enemy.InitSeed))
  check(GetPtrHash(enemy) == EXPECT.enemyA, 'GetPtrHash must return the entity address')

  -- `GetPtrHash` 对非实体对象返回 0（我们只有 Entity 家族持有原生指针），不报错。
  check(GetPtrHash(42) == 0, 'GetPtrHash(non-object) must be 0')
  check(GetPtrHash('nope') == 0, 'GetPtrHash(string) must be 0')
  check(type(GetPtrHash(Vector(1, 1))) == 'number', 'GetPtrHash must always answer a number')

  -- `Entity:ToPlayer()`：非玩家实体必须 nil（`Entity.md:631`），玩家实体必须能转型。
  check(enemy:ToPlayer() == nil, 'a non-player entity must not cast to EntityPlayer')
  local fromQuery = Isaac.FindByType(1, -1, -1)[1]
  local casted = fromQuery:ToPlayer()
  check(casted ~= nil, 'a player entity must cast to EntityPlayer')
  check(casted.Type == 1, 'the cast must keep the player fields')
  check(casted:GetPlayerType() == 0, 'the cast must reach EntityPlayer methods')
  -- `HasCollectible` 是玩家专属引擎调用：句柄必须能走到注入的实现，参数与返回值原样往返。
  check(casted:HasCollectible(1) == true, 'HasCollectible(1) must be true')
  check(casted:HasCollectible(2) == false, 'HasCollectible(2) must be false')
end

local function verifyToPickup()
  local pickupEntity, fakePickupEntity = nil, nil
  for _, entity in ipairs(Isaac.FindByType(5, 100, -1, false, false)) do
    if GetPtrHash(entity) == EXPECT.pickup then pickupEntity = entity
    elseif GetPtrHash(entity) == EXPECT.fakePickup then fakePickupEntity = entity end
  end
  check(pickupEntity ~= nil, 'the real pickup must be found')
  check(fakePickupEntity ~= nil, 'the fake pickup must be found')

  local view = pickupEntity:ToPickup()
  check(view ~= nil, 'Type==5 with the Entity_Pickup vtable must cast')
  check(view.Price == EXPECTED_PICKUP_PRICE,
        'Price must be ' .. EXPECTED_PICKUP_PRICE .. ', got ' .. describe(view.Price))
  check(view.ShopItemId == 0, 'ShopItemId must be 0, got ' .. describe(view.ShopItemId))
  check(view.OptionsPickupIndex == EXPECTED_OPTIONS_INDEX,
        'OptionsPickupIndex must be ' .. EXPECTED_OPTIONS_INDEX)
  check(view.ForceBlind == false, 'ForceBlind must be false, got ' .. describe(view.ForceBlind))
  check(view.Touched == true, 'Touched must be true (candidate offset +0x560), got '
        .. describe(view.Touched))
  -- `EntityPickup` 继承 `Entity` 的字段与方法。
  check(view.Type == 5, 'the pickup view must expose the base fields')
  check(view.Index == 2, 'the pickup view must expose Index')
  check(type(view:GetData()) == 'table', 'the pickup view must reach Entity methods')
  check(view.NotAField == nil, 'an unknown pickup field must be nil')
  -- `IsShopItem` 是安全 stub（恒 false）：EID 在卡片/药丸描述路径上无条件调用它
  -- （`main.lua:1611`/`1636`），nil 方法会打断整段描述渲染。
  check(view:IsShopItem() == false, 'EntityPickup:IsShopItem must answer false')
  -- `EntityPickup` 也会回落到 `Entity` 的字段（PC 里它就是 `Entity` 的子类）。
  check(view.InitSeed == EXPECTED_INIT_SEED, 'InitSeed must be readable through the pickup view')

  -- 反例：`Type==5` 但 vptr 不是 `Entity_Pickup`；以及根本不是 Pickup 的实体。
  check(fakePickupEntity:ToPickup() == nil, 'Type==5 without the pickup vtable must not cast')
  local enemy = Isaac.FindByType(20, 5, 0, false, false)[1]
  check(enemy:ToPickup() == nil, 'a non-pickup entity must not cast')
  -- `Entity:ToFamiliar()`（批次 9）与 `ToPickup` 同一形态：**Type 与 vptr 都要对**。
  -- 玩家（Type==1、vptr 是 Entity_Player）必须 nil —— 否则 EID 会把玩家当跟班。
  local playerEntity = Isaac.GetPlayer(0)
  if playerEntity ~= nil then
    check(playerEntity:ToFamiliar() == nil, 'a player must not be reported as a familiar')
  end

end

local function verifyPlayerGetters()
  local player = Isaac.GetPlayer(0)
  check(player ~= nil, 'the player must be found')
  check(player:GetActiveItem() == EXPECTED_ACTIVE_ITEM, 'GetActiveItem must default to slot 0')
  check(player:GetActiveItem(0) == EXPECTED_ACTIVE_ITEM, 'GetActiveItem(0) must read +0x1964')
  check(player:GetActiveItem(1) == 0, 'the second active slot must be empty')
  check(player:GetActiveItem(4) == 0, 'an out-of-range active slot must answer 0')
  check(player:GetActiveItem(-1) == 0, 'a negative active slot must answer 0')
  check(player:GetActiveItem('nope') == 0, 'a non-integer slot must answer 0')
  check(player:GetTrinket(0) == EXPECTED_TRINKET, 'GetTrinket(0) must read +0x1AB0')
  check(player:GetTrinket(1) == 0, 'the second trinket slot must be empty')
  check(player:GetTrinket(2) == 0, 'there are only two trinket slots')
  check(player:GetTrinket(-1) == 0, 'a negative trinket slot must answer 0')
  check(player:GetBabySkin() == EXPECTED_BABY_SKIN,
        'GetBabySkin must be -1 for a non-baby (got ' .. describe(player:GetBabySkin()) .. ')')

  local level = Game():GetLevel()
  check(level:GetCurses() == EXPECTED_CURSES,
        'GetCurses must read Level+0x0C, got ' .. describe(level:GetCurses()))
end

-- ===== Entity:GetSprite() / Sprite:GetAnimation()（批次 5）=====
--
-- EID 的 `EID:IsAltChoice()`（`main.lua:216`）在宝藏房里走的正是这条链：
-- `pickup:GetSprite():GetAnimation()`。这里用伪造的引擎内存（实体 +0x50 的内嵌 ANM2）验证：
--   * 返回的是 `Sprite` userdata，`IsLoaded`/`GetAnimation` 读的是引擎自己写的那两个字段；
--   * 短串（内联）与长串（+0x10 数据指针）两种 libc++ string 形态都读对；
--   * 没有当前动画时返回**空串**（不是 nil：EID 会拿它做 `~= 'Idle'` 比较）；
--   * 引擎来源的句柄被 Lua 回收（`__gc`）时**不拥有**引擎对象：回收之后引擎的 ANM2 必须
--     原封不动、还能重新读到同样的值。
local savedEnemySprite = nil

local function entityByName(name)
  for _, entity in ipairs(Isaac.FindInRadius(Vector(80, 280), 100, ALL_PARTITIONS)) do
    if GetPtrHash(entity) == EXPECT[name] then return entity end
  end
  return nil
end

local function verifyEntitySprite()
  local enemy = Isaac.FindByType(20, 5, 0, false, false)[1]
  check(enemy ~= nil, 'the enemy must be found for GetSprite')
  local sprite = enemy:GetSprite()
  check(sprite ~= nil, 'Entity:GetSprite() must answer a Sprite object, not nil')
  check(type(sprite) == 'userdata', 'GetSprite must answer a userdata, got ' .. describe(sprite))
  check(sprite:IsLoaded() == true, 'the fake ANM2 must report "graphics loaded"')
  check(sprite:GetAnimation() == 'Idle',
        'GetAnimation must read ANM2+0x38 (short libc++ string), got '
        .. describe(sprite:GetAnimation()))
  -- 属性缓存对引擎 sprite 同样可用（同一个 `SpriteHandle` 家族）。
  sprite.FlipX = true
  check(sprite.FlipX == true, 'FlipX must be cached on an engine sprite view too')

  -- 长串（数据指针在 +0x10）：`ShopIdle` 是 8 字节，超过 SSO 内联容量。
  local pickup = entityByName('pickup')
  check(pickup ~= nil, 'the pickup must be found')
  local pickupSprite = pickup:GetSprite()
  check(pickupSprite ~= nil, 'a pickup must hand out a sprite')
  check(pickupSprite:GetAnimation() == 'ShopIdle',
        'GetAnimation must read a long libc++ string through +0x10, got '
        .. describe(pickupSprite:GetAnimation()))
  check(pickupSprite:IsLoaded() == true, 'the pickup sprite must report loaded')

  -- 没有当前动画 + 图形未加载：`false` 与**空串**（不是 nil）。
  local familiar = entityByName('familiar')
  check(familiar ~= nil, 'the familiar must be found')
  local familiarSprite = familiar:GetSprite()
  check(familiarSprite ~= nil, 'an entity with an empty ANM2 must still hand out a sprite')
  check(familiarSprite:IsLoaded() == false, 'an unloaded ANM2 must answer false')
  check(familiarSprite:GetAnimation() == '',
        'a sprite without a current animation must answer an empty string, got '
        .. describe(familiarSprite:GetAnimation()))

  -- 参数个数不对 → 报错（与其它 `Sprite` 只读访问器同一口径）。
  local extraOk = pcall(function() return sprite:GetAnimation('nope') end)
  check(not extraOk, 'GetAnimation must reject arguments')

  -- ★ `__gc` 不得动引擎对象：丢掉引用 → 强制 GC（跑一次 `__gc`）→ 引擎的 ANM2 必须原样还在。
  --   如果所有权标记被去掉，`__gc` 会走"我们自己的对象"那条分支：它先读 `entity+0x48` 当
  --   malloc 基址（宿主里是个毒值）再 free，进程会 abort —— 这条用例就是那条纪律的守卫。
  do
    local transient = enemy:GetSprite()
    check(transient:IsLoaded() == true, 'a transient engine sprite must still read')
  end
  collectgarbage('collect')
  collectgarbage('collect')
  local again = enemy:GetSprite()
  check(again ~= nil, 'the entity must still hand out a sprite after a GC cycle')
  check(again:IsLoaded() == true, 'the engine ANM2 must survive the Lua view being collected')
  check(again:GetAnimation() == 'Idle', 'the engine ANM2 animation must survive collection')

  savedEnemySprite = sprite
end

-- ===== GetData：同一实体稳定同表 =====
local savedData = nil
local savedPlayerHash = 0
local savedEnemy = nil

local function verifyGetData()
  local player = Isaac.GetPlayer(0)
  local data = player:GetData()
  check(type(data) == 'table', 'GetData must return a table, got ' .. describe(data))
  check(data == player:GetData(), 'the same entity must keep the same table')
  data.MARK = 'v1'
  check(player:GetData().MARK == 'v1', 'a write through GetData must be visible next time')
  savedData = data
  savedPlayerHash = GetPtrHash(player)

  local enemy = Isaac.FindByType(20, 5, 0, false, false)[1]
  savedEnemy = enemy
  local enemyData = enemy:GetData()
  check(type(enemyData) == 'table', 'GetData on a plain Entity must return a table')
  check(enemyData ~= data, 'different entities must get different tables')
  check(next(enemyData) == nil, 'a fresh table must be empty')
  check(enemyData == enemy:GetData(), 'the entity table must be stable inside one frame')
  -- `EntityPlayer:GetData()` 与 `Entity:GetData()` 是同一个存储：同一指针必须同一张表。
  check(Isaac.GetPlayer(0):GetData() == data, 'the store must be shared');

  -- 过期/伪造句柄 → nil（EID 的判据就是 `~= nil`）。
  check(GetPtrHash(nil) == 0, 'GetPtrHash(nil) must be 0')
end

local function verifySecondFrame()
  if SCENARIO == 'room_change' then
    -- 换房间 → 房间 epoch 前进 → 旧的表必须失效（与 PC 的已知差异，见 `PushEntityDataTable`）。
    local player = Isaac.GetPlayer(0)
    check(player ~= nil, 'the player must still be there after the room change')
    local again = player:GetData()
    check(type(again) == 'table', 'GetData must still answer a table after a room change')
    check(again ~= savedData, 'a room change must hand out a new table')
    check(again.MARK == nil, 'mod data must not leak into the next room')
    -- 查询在新的 `Room*` 上照常工作。
    checkSet(Isaac.FindInRadius(Vector(80, 280), 100, ENEMY), { 'enemyA', 'sizeOnly', 'charmed' },
             'queries after the room change')
    return
  end
  if SCENARIO == 'corrupt_entity' then
    -- 第 1 帧拿到的句柄必须**每次访问重做校验**：vptr 被改坏之后它自己就要失效
    -- （字段、方法、`GetPtrHash` 全部降级），而不只是"从查询里消失"。
    check(savedEnemy ~= nil, 'the first frame must have produced an entity handle')
    check(savedEnemy.Type == nil, 'an invalidated handle must not answer fields')
    check(savedEnemy.Position == nil, 'an invalidated handle must not answer Position')
    check(savedEnemy.FrameCount == nil, 'an invalidated handle must not answer FrameCount')
    check(GetPtrHash(savedEnemy) == 0, 'an invalidated handle must hash to 0')
    check(savedEnemy:GetData() == nil, 'an invalidated handle must not answer GetData')
    check(savedEnemy:ToPlayer() == nil, 'an invalidated handle must not cast')
    check(savedEnemy:ToPickup() == nil, 'an invalidated handle must not cast')
    check(savedEnemy:GetSprite() == nil, 'a corrupted entity must not hand out a sprite')
    -- ★ 引擎 sprite 句柄：实体消失后必须**报 Lua 错误**（可被 pcall 接住），而不是拿一个
    --   可能已经被引擎释放的地址继续解引用。这条断言就是"每次访问重新校验实体"的守卫：
    --   去掉那次 `IsLiveEntityPointer` 校验，`IsLoaded()` 会安静地读一块坏掉的内存。
    if savedEnemySprite ~= nil then
      local loadedOk, loadedErr = pcall(function() return savedEnemySprite:IsLoaded() end)
      check(not loadedOk, 'a sprite of a corrupted entity must refuse to be used')
      check(type(loadedErr) == 'string' and string.find(loadedErr, 'no longer alive', 1, true) ~= nil,
            'the failure must name the missing entity, got ' .. describe(loadedErr))
      local nameOk = pcall(function() return savedEnemySprite:GetAnimation() end)
      check(not nameOk, 'GetAnimation must refuse to read a corrupted entity sprite')
    end
    -- 而且不再出现在任何查询结果里。
    local stale = Isaac.FindByType(20, 5, 0, false, false)[1]
    check(stale == nil, 'a corrupted entity must disappear from FindByType')
    checkSet(Isaac.FindInRadius(Vector(80, 280), 100, ENEMY), { 'sizeOnly', 'charmed' },
             'ENEMY after corruption')
    check(Isaac.CountEnemies() == 2, 'CountEnemies must drop the corrupted entity')
    return
  end
  if SCENARIO == 'corrupt_entity' then
    return
  end
  -- 稳定同表（跨帧）。
  local player = Isaac.GetPlayer(0)
  check(player ~= nil, 'the player must still be there')
  check(GetPtrHash(player) == savedPlayerHash, 'the pointer hash must be stable')
  check(player:GetData() == savedData, 'GetData must answer the same table on the next frame')
  check(player:GetData().MARK == 'v1', 'the stored value must survive to the next frame')
  -- 引擎 sprite 句柄跨帧仍然有效：每次访问都用 `owner + 0x50` 重新解析，所以句柄不过期。
  check(savedEnemySprite ~= nil, 'the first frame must have produced an engine sprite handle')
  check(savedEnemySprite:IsLoaded() == true,
        'an engine sprite view must stay valid across frames')
  check(savedEnemySprite:GetAnimation() == 'Idle',
        'the animation name must stay readable on the next frame')
end

local frame = 0
local function run()
  frame = frame + 1
  if SCENARIO == 'no_base' then
    -- 基址未发布：所有查询安静地返回空表/0，`GetPlayer` 返回 nil，`GetData` 返回 nil。
    checkArray(Isaac.FindInRadius(Vector(80, 280), 100, ALL_PARTITIONS), 0, 'no base radius')
    checkArray(Isaac.FindByType(20, -1, -1), 0, 'no base type')
    check(Isaac.CountEnemies() == 0, 'no base enemies')
    check(Isaac.CountBosses() == 0, 'no base bosses')
    check(Isaac.GetPlayer(0) == nil, 'no base player')
    local level = Game():GetLevel()
    local cursesOk = pcall(function() return level:GetCurses() end)
    check(not cursesOk, 'GetCurses must report a Lua error while the state is unreadable')
    if frame == 1 then PHASE1_OK = 1 end
    if frame == 2 then PHASE2_OK = 1 end
    SCENARIO_OK = 1
    return
  end
  if SCENARIO == 'bad_fingerprint' then
    -- 容器指纹不匹配 → 一切查询返回空，**不报错**；`GetData` 退化成"按指针 + 生成帧"作键。
    checkArray(Isaac.FindInRadius(Vector(80, 280), 100, ALL_PARTITIONS), 0, 'fingerprint radius')
    checkArray(Isaac.FindByType(20, -1, -1), 0, 'fingerprint type')
    check(Isaac.CountEnemies() == 0, 'fingerprint enemies')
    check(Isaac.GetPlayer(0) ~= nil, 'GetPlayer does not need the EntityList')
    local player = Isaac.GetPlayer(0)
    check(type(player:GetData()) == 'table', 'GetData must degrade to a table, not nil')
    check(player:GetData() == player:GetData(), 'the degraded table must still be stable')
    check(player:GetActiveItem() == EXPECTED_ACTIVE_ITEM, 'player getters are unaffected')
    if frame == 1 then PHASE1_OK = 1 end
    if frame == 2 then PHASE2_OK = 1 end
    SCENARIO_OK = 1
    return
  end
  if SCENARIO == 'count_too_large' then
    -- 活表 count > cap → 活表整张作废，但 EFFECT 表（`EL+0xA8`）照常工作。
    -- 活表作废，但 PLAYER 分区走的是玩家向量、EFFECT 走 `EL+0xA8`：两者都不受影响。
    checkArray(Isaac.FindInRadius(Vector(80, 280), 100, ALL_PARTITIONS), 2, 'overflow radius')
    checkSet(Isaac.FindInRadius(Vector(80, 280), 100, ALL_PARTITIONS), { 'player', 'effect' },
             'overflow radius')
    checkSet(Isaac.FindInRadius(Vector(80, 280), 100, EFFECT), { 'effect' }, 'overflow effect')
    checkSet(Isaac.FindInRadius(Vector(80, 280), 100, PLAYER), { 'player' }, 'overflow player')
    checkArray(Isaac.FindByType(20, -1, -1, false, false), 0, 'overflow live type')
    checkSet(Isaac.FindByType(1000, 7, -1), { 'effect' }, 'overflow effect type')
    check(Isaac.CountEnemies() == 0, 'overflow enemies')
    if frame == 1 then PHASE1_OK = 1 end
    if frame == 2 then PHASE2_OK = 1 end
    SCENARIO_OK = 1
    return
  end

  if frame == 1 then
    verifyRadiusAndPartitions()
    verifyFindByType()
    verifyCounts()
    verifyEntityView()
    verifyToPickup()
    verifyEntitySprite()
    verifyPlayerGetters()
    verifyGetData()
    PHASE1_OK = 1
  elseif frame == 2 then
    verifySecondFrame()
    PHASE2_OK = 1
  end
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


class LuaEntityQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        cc, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-entity-query-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "entity_query_harness.cpp"
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
        cls.harness = temporary / "entity_query_harness"
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
        self.assertNotIn("QUERY_FAIL", result.stdout + result.stderr)
        return result

    def test_enumerates_the_room_entity_container(self):
        """半径语义（含 `Size`、严格 `<`）、七个分区、去重、`FLAG_NO_QUERY` 与坏元素过滤。"""
        result = self.run_scenario("valid")
        self.assertIn("ENTITY_QUERY_OK scenario=valid engine_calls=2", result.stdout)
        # 生成的枚举表里 `EntityPartition.PLAYER` 必须是 32（Lua 字面量掩码与它逐项一致，
        # 脚本内的 `check` 已核对其余六项；这里再核一次 Python 侧看得到的证据行）。
        self.assertIn("ENTITY_PARTITION_PLAYER_SEEN=32", result.stdout)

    def test_find_by_type_and_entity_views(self):
        """`FindByType` 的通配/跨表/去重/`Cache` 参数、实体字段、`GetData`、`ToPickup`、玩家取值。"""
        result = self.run_scenario("valid")
        self.assertIn("PHASE1_OK", result.stdout)
        self.assertIn("PHASE2_OK", result.stdout)

    def test_get_data_survives_a_room_change_by_invalidating(self):
        """换房间 → 房间 epoch 前进 → 旧的 ModData 表失效（与 PC 的已知差异）。"""
        result = self.run_scenario("room_change")
        self.assertIn("ENTITY_QUERY_DEGRADED scenario=room_change", result.stdout)

    def test_entity_handles_are_revalidated_on_every_access(self):
        """vptr 被改坏之后：`FindByType`/`FindInRadius`/`CountEnemies` 都不再包含那个实体。"""
        result = self.run_scenario("corrupt_entity")
        self.assertIn("ENTITY_QUERY_DEGRADED scenario=corrupt_entity", result.stdout)

    def test_queries_return_empty_when_the_container_fingerprint_does_not_match(self):
        """容器指纹不匹配 → 空结果且不报错；`GetData` 退化成"按指针 + 生成帧"作键。"""
        result = self.run_scenario("bad_fingerprint")
        self.assertIn("ENTITY_QUERY_DEGRADED scenario=bad_fingerprint", result.stdout)

    def test_a_count_above_capacity_invalidates_only_the_live_table(self):
        """`count > cap` → 活表作废，但 EFFECT 表（另一张表）照常可查。"""
        result = self.run_scenario("count_too_large")
        self.assertIn("ENTITY_QUERY_DEGRADED scenario=count_too_large", result.stdout)

    def test_every_query_degrades_quietly_without_an_engine_base(self):
        """基址未发布 → 全部空结果、不抛 Lua 错误（`GetCurses` 除外，它必须报错而不是编 0）。"""
        result = self.run_scenario("no_base")
        self.assertIn("ENTITY_QUERY_DEGRADED scenario=no_base", result.stdout)


if __name__ == "__main__":
    unittest.main()
