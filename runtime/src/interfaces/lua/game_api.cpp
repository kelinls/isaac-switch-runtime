#include "interfaces/lua/game_api.hpp"

#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"
#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/owner_binding.hpp"

#include "application/callback/callback_dispatcher.hpp"
#include "game_observer.hpp"
#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstddef>
#include <cstdint>
#include <atomic>
#include <cstring>

#if defined(__SWITCH__)
#include "lib/nx/nx.h"
#endif

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {

// 探针：`Game:IsPaused()` 最后一次返回 true。EID 的 `OnRender` 用它配合 `HideInBattle`
// 决定是否隐藏描述，所以"描述没画"必须能看出这个读数。放在匿名命名空间之外，探针出口要用。
std::atomic<std::uint32_t> g_LastIsPaused{0};

// 探针：`Game:GetNumPlayers()` 最后一次返回值（EID 的 `EID.player` 依赖它）。
std::atomic<std::uint32_t> g_LastNumPlayers{0};

namespace {

using LuaRuntime::EngineModuleBase;
using LuaRuntime::GameHandle;
using LuaRuntime::GameIsGreedModeThunk;
using LuaRuntime::GameIsPausedThunk;
using LuaRuntime::GameOwnerSlot;
using LuaRuntime::InManagedCallbackScope;
using LuaRuntime::ItemPoolHandle;
using LuaRuntime::kGameMetatable;
using LuaRuntime::kItemPoolMetatable;
using LuaRuntime::kLevelMetatable;
using LuaRuntime::kRoomMetatable;
using LuaRuntime::LevelHandle;
using LuaRuntime::RoomHandle;
using LuaRuntime::SeedHandle;
using LuaRuntime::kSeedsMetatable;
// 玩家数量的**唯一**来源：与 `Isaac.GetPlayer` 共用同一次判定（真机 `01789207110` 的根因）。
using isaac::runtime::EnginePlayerArray;
using isaac::runtime::ResolveEnginePlayerArray;

int GameIsPaused(lua_State* state);
int GameIsGreedMode(lua_State* state);
int GameGetLevel(lua_State* state);
int GameGetItemPool(lua_State* state);
int GameGetRoom(lua_State* state);
int GameGetNumPlayers(lua_State* state);
int GameGetFrameCount(lua_State* state);
int GameGetSeeds(lua_State* state);
int SeedsIsCustomRun(lua_State* state);
int SeedsGetStartSeed(lua_State* state);
int GameGetVictoryLap(lua_State* state);

constexpr LuaHandlerBinding kGameHandlers[] = {
    {0x02010001, &GameIsPaused},
    {0x02010002, &GameIsGreedMode},
    {0x02010003, &GameGetLevel},
    {0x02010004, &GameGetItemPool},
    {0x02010005, &GameGetRoom},
    {0x02010006, &GameGetNumPlayers},
    {0x02010007, &GameGetFrameCount},
    {0x02010008, &GameGetSeeds},
    {0x02010009, &GameGetVictoryLap},
};



// `Seeds` 家族（`Game:GetSeeds()` 的返回值）的方法表。EID 只用这两个；语义与依据见
// `api_catalog.cpp` 里对应条目的注释与 `SeedHandle` 的定义。
constexpr LuaHandlerBinding kSeedsHandlers[] = {
    {0x0F010001, &SeedsIsCustomRun},
    {0x0F010002, &SeedsGetStartSeed},
};

constexpr char kSeedsOwner[] = "Seeds";

// `Game` 家族在 Catalog 里的 owner 名（绑定表按它挑条目）。
constexpr char kGameOwner[] = "Game";

// --- 批次 3：`Game` 的两个只读标量 ---------------------------------------------
//
// 这两个方法都要读引擎内存，而 `game_observer` 的观察函数只回答"读到了什么"、不暴露
// `Game*` 本身，所以这里按 `interfaces/lua/isaac_api.cpp` 的同一套写法自带最小读取原语：
// 设备侧先用 `svcQueryMemory` 确认这段内存可读，宿主侧只排除空地址（宿主测试的"引擎内存"
// 是本进程 malloc 出来的伪造块，本来就一定可读）。
//
// **只读**：这两个方法都不写引擎内存。
bool IsEngineMemoryReadable(std::uintptr_t address, std::size_t length) noexcept {
    // 实现见 `interfaces/lua/engine_memory_guard.hpp`：四个 API 族共用同一份缓存与计数。
    //
    // 语义逐字不变：空地址、长度 0、地址回绕一律 false；设备侧仍要求返回码成功、区间完整落在
    // 映射内、且带 `Perm_R`。变的是**命中缓存时不再进内核** —— `svcQueryMemory` 返回的描述
    // 覆盖一整段连续且权限一致的映射，整段都能复用那一次结论。缓存只在受管回调派发期间有效
    // （`EngineGuardEnterDispatch`/`EngineGuardLeaveDispatch`），所以过期窗口为零。
    if (!EngineGuardRangeUsable(address, length)) {
        return false;
    }
#if defined(__SWITCH__)
    // 派发作用域内命中已登记的映射区间：这一帧的同一段内存不再重复问内核。
    if (EngineGuardLookupCached(address, length)) {
        return true;
    }
    const std::uint64_t syscallStart = EngineGuardSyscallBegin();
    MemoryInfo info{};
    u32 pageInfo = 0;
    // 显式写 `std::uint32_t`：本命名空间里有一个同名模板 `Result<T>` 会遮蔽 libnx 的
    // `Result`（u32）typedef，直接写 `Result` 会撞上模板推导。
    const std::uint32_t result = svcQueryMemory(&info, &pageInfo, address);
    EngineGuardSyscallEnd(syscallStart);
    if (R_FAILED(result) || info.size == 0 || info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    if (address < info.addr || address + length > info.addr + info.size ||
        (info.perm & Perm_R) == 0) {
        return false;
    }
    // 这次系统调用描述的是一整段连续且权限一致的映射，整段都可以复用这个结论。
    EngineGuardRememberReadable(info.addr, info.addr + info.size);
    return true;
#else
    // 宿主构建没有 `svcQueryMemory`；宿主测试的"引擎内存"是本进程 malloc 出来的伪造块，
    // 本来就一定可读（见 `runtime/tests/test_lua_entity_player.py`）。
    return true;
#endif
}

template <typename T>
bool ReadEngine(std::uintptr_t address, T* value) noexcept {
    if (value == nullptr || !IsEngineMemoryReadable(address, sizeof(T))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

// `Game*`：与 `Isaac.GetPlayer` **完全同一条链** —— 模块基址 + `kGameOwnerGlobalSlotOffset`
// 是 `g_Game` 的槽（槽里是**指针变量**，解一次才是 `Game*`，见 `isaac_api.cpp` 的
// `ResolveEntityPlayer`）。返回 0 = 拿不到。
//
// 这里刻意不用 `LuaRuntime::GameOwnerSlot()`（那是 `Game:IsPaused` 那一路的入口）：两个方法都
// 只读引擎内存，走"基址 + 槽偏移"就和 `Isaac.GetPlayer` 依赖同一份已发布状态，
// 少一个"要等 `SetGameBindings` 先跑过"的隐含前提；拿不到时降级行为见各自的注释。
std::uintptr_t ResolveGame() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        return 0;
    }
    std::uintptr_t ownerSlot = 0;
    std::uintptr_t game = 0;
    if (!ReadEngine(base + kGameOwnerGlobalSlotOffset, &ownerSlot) || ownerSlot == 0 ||
        !ReadEngine(ownerSlot, &game) || game == 0) {
        return 0;
    }
    return game;
}

// `Game:GetNumPlayers()`：`players` 是 `Game + 0x25C50/+0x25C58` 处的
// `std::vector<Entity_Player*>`（步长 8），元素个数 = `(end - begin) / 8`。
//
// **口径必须与 `Isaac.GetPlayer` 完全一致（2026-09-12 真机报告 `01789207110` 修正）**：
// 两处都走 `ResolveEnginePlayerArray()`，它同时给出"元素个数"和"这次读成功了吗"。
//   * 读成功（`readable`）：**如实返回元素个数**，包括 0 —— 玩家向量为空就是没有玩家，
//     此时 `Isaac.GetPlayer(0)` 也返回 nil，两者一致；
//   * 读失败（不可读）：返回 **1**（单人降级）。这一档里 `GetPlayer(0)` 同样返回 nil，
//     但 EID 只会走"单人快路径"、不会进入 `for i = 0, n-1` 的循环（`main.lua:1065` 判的就是
//     `numPlayers == 1`），因此不会索引 nil。
//
// 修正前的写法是"任何读不到都答 1"（连**空向量**也答 1），于是 EID 的
// `features/eid_api.lua:2620` 循环体里 `Isaac.GetPlayer(0)` 拿到 nil、
// `player.QueuedItem` 直接抛错、派发器静默摘除该回调 —— 屏幕全空但游戏不崩，
// 属于最难查的一类现象。原注释担心"返回 0 会让 EID 走多人分支"，那个担心对
// **读失败**成立（故保留 1），对**读成功且为空**不成立（EID 的多人分支在 0 个玩家时同样不会执行循环体）。
int GameGetNumPlayers(lua_State* state) {
    RecordApiSequence(5U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetNumPlayers accepts no arguments");
    }
    const EnginePlayerArray players = ResolveEnginePlayerArray();
    // **拿不到玩家数组就答 0**（2026-09-12 第五轮修正：原来答 1）。
    //
    // 真机证据链（报告 `01789209134` → `01789209282` → `01789209426` → `01789209547`）：
    // 玩家向量在主界面是 `begin == end == 0`（空向量，`Isaac.GetPlayer(0)` 因此是 nil），
    // 而"读失败降级答 1"这一档让 EID 的
    //   `for i = 0, game:GetNumPlayers() - 1 do local player = Isaac.GetPlayer(i) ... player.QueuedItem`
    // （`features/eid_api.lua:2620-2625`）**真的进了循环体**，于是索引 nil、抛错、
    // 那两条回调被派发器静默摘除 —— 屏幕全空、游戏不崩，是最难查的形态。
    //
    // 口径统一后的规则很简单：**`GetNumPlayers()` 的答案必须让 `Isaac.GetPlayer(0)` 有意义**。
    //   * 数得出个数（含 0）→ 如实报；
    //   * 数不出个数         → 报 0（"不知道"按"没有"处理），而不是报 1 然后给 nil。
    // 旧的"答 1"理由（避免 EID 走多人分支）在**数不出个数**时并不成立：多人分支同样需要
    // `Isaac.GetPlayer`，而它那时也只能给 nil。
    if (!players.readable || players.count == 0) {
        g_LastNumPlayers.store(0, std::memory_order_relaxed);
        lua_pushinteger(state, 0);
        return 1;
    }
    g_LastNumPlayers.store(static_cast<std::uint32_t>(players.count), std::memory_order_relaxed);
    lua_pushinteger(state, static_cast<lua_Integer>(players.count));
    return 1;
}

// `Game:GetFrameCount()`：**引擎自己的帧计数** `Game + 0x24F99C`（u32，`Game::Update`
// 每帧 +1；`Entity::GetFrameCount`/`Room::GetFrameCount`/`Game::SaveState` 读的都是它，
// 指令级证据见 `runtime_constants.hpp`）。
//
// 退化路径：拿不到 `Game*` 或字段不可读时返回本 Runtime 自己的 `ManagedFrameClock`
// （`Isaac.GetFrameCount` 用的同一个时钟）。**这是近似值**：它按 Runtime 的 update 派发
// 前进，与引擎的"游戏内帧数"不保证逐值一致；数值单调递增，所以 EID 的
// `frame - lastTouch > 45`、`frame % maxAnimTime` 这类用法仍然成立。
// `Game:GetSeeds()`：返回一个**自洽的** `Seeds` 句柄（不持有引擎指针，见 `SeedHandle` 的注释）。
//
// 为什么先这样做：EID 的 `features/eid_api.lua:2046` 直接 `game:GetSeeds():IsCustomRun()`，
// nil 会让整条 update 回调报错并被派发器摘除（真机报告 `01789210180`）。Switch 的
// `IsaacRepentance::Seeds` 字段偏移尚未定位，所以先保证"拿得到对象、方法都在"。
int GameGetSeeds(lua_State* state) {
    RecordApiSequence(7U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetSeeds accepts no arguments");
    }
    auto* seeds = static_cast<SeedHandle*>(lua_newuserdata(state, sizeof(SeedHandle)));
    seeds->startSeed = 0;
    seeds->customRun = 0;
    luaL_getmetatable(state, kSeedsMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

// `Game:GetVictoryLap()`：胜利圈计数。非胜利圈时 PC 返回 0，EID 的用法都是 `> 0` 判断
// （`eid_api.lua:2360/2368`、`eid_repentogon.lua:25`），所以返回 0 在正常流程里与 PC 一致。
int GameGetVictoryLap(lua_State* state) {
    RecordApiSequence(8U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetVictoryLap accepts no arguments");
    }
    lua_pushinteger(state, 0);
    return 1;
}

// `Seeds:IsCustomRun()`：PC 语义是"挑战局或带种子的局"（IsaacDocs `Seeds.md`）。
//
// **当前是自洽占位实现**：Switch 的 `IsaacRepentance::Seeds` 里没有 `IsCustomRun` 符号
// （符号表只有 `SetStartSeed`/`Reset`/`Seed2String`/`CanAddSeedEffect` 这类），PC 那条 Lua
// 访问器应是字段直读，字段偏移尚未定位。返回 false 的含义是"成就解锁没被特殊种子挡住"，
// 与 EID 的用法（`AreAchievementsAllowed` 判是否继续查成就）方向一致；偏移定位后再换成真实读数。
// 该 API 在 Catalog 里标记为 `Experimental` 正是这个原因。
int SeedsIsCustomRun(lua_State* state) {
    auto* seeds = static_cast<SeedHandle*>(luaL_checkudata(state, 1, kSeedsMetatable));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Seeds:IsCustomRun accepts no arguments");
    }
    lua_pushboolean(state, seeds->customRun != 0 ? 1 : 0);
    return 1;
}

// `Seeds:GetStartSeed()`：本局起始种子。EID 只用它当缓存键
// （`eid_bagofcrafting.lua:822`、`eid_api.lua:2887`），不参与显示内容，所以先返回句柄里
// 自洽的值；拿到引擎偏移后再换成真实种子。
int SeedsGetStartSeed(lua_State* state) {
    auto* seeds = static_cast<SeedHandle*>(luaL_checkudata(state, 1, kSeedsMetatable));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Seeds:GetStartSeed accepts no arguments");
    }
    lua_pushinteger(state, static_cast<lua_Integer>(seeds->startSeed));
    return 1;
}

int GameGetFrameCount(lua_State* state) {
    RecordApiSequence(6U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetFrameCount accepts no arguments");
    }
    const std::uintptr_t game = ResolveGame();
    std::uint32_t frames = 0;
    if (game != 0 && game <= UINTPTR_MAX - kGameFrameCountOffset &&
        ReadEngine(game + kGameFrameCountOffset, &frames)) {
        lua_pushinteger(state, static_cast<lua_Integer>(frames));
        return 1;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(ManagedFrameClock::Count()));
    return 1;
}

int GameIsPaused(lua_State* state) {
    RecordApiSequence(0U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:IsPaused accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Game:IsPaused is only available during a Runtime callback");
    }

    const std::uintptr_t ownerSlot = GameOwnerSlot();
    const std::uintptr_t isPausedThunk = GameIsPausedThunk();
    if (ownerSlot == 0 || isPausedThunk == 0) {
        return luaL_error(state, "Game:IsPaused native bindings are unavailable");
    }

    const GameIsPausedObservation observation = ObserveGameIsPaused(ownerSlot, isPausedThunk);
    if (observation == GameIsPausedObservation::PausedFalse) {
        g_LastIsPaused.store(0, std::memory_order_relaxed);  // 探针：最后一次返回 false
        lua_pushboolean(state, 0);
        return 1;
    }
    if (observation == GameIsPausedObservation::PausedTrue) {
        g_LastIsPaused.store(1, std::memory_order_relaxed);  // 探针：最后一次返回 true
        lua_pushboolean(state, 1);
        return 1;
    }
    return luaL_error(state, "Game:IsPaused could not read the native Game state");
}

int GameIsGreedMode(lua_State* state) {
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:IsGreedMode accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Game:IsGreedMode is only available during a Runtime callback");
    }
    const GameIsGreedModeObservation observation =
        ObserveGameIsGreedMode(GameOwnerSlot(), GameIsGreedModeThunk());
    if (observation == GameIsGreedModeObservation::GreedFalse) {
        lua_pushboolean(state, 0);
        return 1;
    }
    if (observation == GameIsGreedModeObservation::GreedTrue) {
        lua_pushboolean(state, 1);
        return 1;
    }
    return luaL_error(state, "Game:IsGreedMode could not read the native Game state");
}

int GameGetLevel(lua_State* state) {
    RecordApiSequence(2U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetLevel accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Game:GetLevel is only available during a Runtime callback");
    }
    auto* level = static_cast<LevelHandle*>(lua_newuserdata(state, sizeof(LevelHandle)));
    level->reserved = 0;
    luaL_getmetatable(state, kLevelMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

int GameGetItemPool(lua_State* state) {
    RecordApiSequence(3U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetItemPool accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Game:GetItemPool is only available during a Runtime callback");
    }
    void* itemPool = nullptr;
    if (ReadCurrentGameItemPool(GameOwnerSlot(), &itemPool) != GameItemPoolObservation::Success) {
        return luaL_error(state, "Game:GetItemPool could not read the native ItemPool state");
    }
    auto* handle = static_cast<ItemPoolHandle*>(lua_newuserdata(state, sizeof(ItemPoolHandle)));
    handle->reserved = 0;
    luaL_getmetatable(state, kItemPoolMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

int GameGetRoom(lua_State* state) {
    RecordApiSequence(4U);
    luaL_checkudata(state, 1, kGameMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Game:GetRoom accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Game:GetRoom is only available during a Runtime callback");
    }
    void* room = nullptr;
    if (ReadCurrentGameRoom(GameOwnerSlot(), &room) != GameRoomObservation::Success) {
        return luaL_error(state, "Game:GetRoom could not read the native Room state");
    }
    auto* handle = static_cast<RoomHandle*>(lua_newuserdata(state, sizeof(RoomHandle)));
    handle->reserved = 0;
    luaL_getmetatable(state, kRoomMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

} // namespace

std::size_t AttachGameMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kGameOwner, kGameHandlers,
                              sizeof(kGameHandlers) / sizeof(kGameHandlers[0]));
}

std::size_t AttachSeedsMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kSeedsOwner, kSeedsHandlers,
                              sizeof(kSeedsHandlers) / sizeof(kSeedsHandlers[0]));
}

std::uint32_t LastNumPlayersForProbe() noexcept {
    return g_LastNumPlayers.load(std::memory_order_relaxed);
}

std::uint32_t LastIsPausedForProbe() noexcept {
    return g_LastIsPaused.load(std::memory_order_relaxed);
}

} // namespace isaac::runtime
