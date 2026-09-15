#include "interfaces/lua/remaining_api.hpp"

#include "room_layout.hpp"

#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"
#include "interfaces/lua/owner_binding.hpp"

#include "game_observer.hpp"
#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstdint>
#include <cstring>
#include <limits>

#if defined(__SWITCH__)
#include "lib/nx/nx.h"
#endif

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
// 供 `lua_runtime.cpp` 注册元表时调用（必须在这个 TU 之外可见，所以不能放在匿名命名空间里）。
int RoomDescriptorIndex(lua_State* state);
int RoomDescriptorListIndex(lua_State* state);
[[nodiscard]] std::size_t AttachRoomDescriptorListMethods(lua_State* state) noexcept;

namespace {

using LuaRuntime::GameOwnerSlot;
using LuaRuntime::InManagedCallbackScope;
using LuaRuntime::ItemPoolGetCollectibleThunk;
using LuaRuntime::ItemPoolHandle;
using LuaRuntime::kItemPoolMetatable;
using LuaRuntime::kLevelMetatable;
using LuaRuntime::kRoomMetatable;
using LuaRuntime::EngineModuleBase;
using LuaRuntime::LevelIsAscentThunk;
using LuaRuntime::ValidateItemPoolGetCollectibleBinding;

int LevelGetStage(lua_State* state);
int LevelIsAscent(lua_State* state);
int LevelGetCurses(lua_State* state);
// 批次 8（2026-09-15）：`Level` 家族四个新成员（依据见各自实现处的长注释）。
int LevelGetCurrentRoomIndex(lua_State* state);
int LevelGetCurrentRoom(lua_State* state);
int LevelGetAbsoluteStage(lua_State* state);
int LevelIsNextStageAvailable(lua_State* state);
// 地基二期（2026-09-15）：房间描述符三件套。字段偏移全部来自布局表 + 真机行为验证，
// 见 `lua_object_handles.hpp` 里 `RoomDescriptorHandle` 上方那段说明。
int LevelGetCurrentRoomDesc(lua_State* state);
int LevelGetRoomByIdx(lua_State* state);
int LevelGetRooms(lua_State* state);
// `RoomDescriptorList.__index` / 方法（`Size` 与 `:Get(i)`）。
int RoomDescriptorListGet(lua_State* state);
int RoomGetType(lua_State* state);
// 批次 7（2026-09-12）：EID 在**网格/寻路**路径上无条件调用的一批 `Room` 成员
// （`features/eid_api.lua:3351` 的 `EID:EvaluateLocation`、`3371` 的 `HasPathToPosition`、
// `2593` 的网格实体查询）。缺任何一个都是 "attempt to call a nil value" ⇒ 整条回调被
// 派发器静默摘除 —— 与之前 `EntityPlayer.GetPill`、`Vector:__sub`、`Sprite:GetTexel` 同一类
// 缺口（真机报告 `01789218953` 就是在贪婪模式商店触发了一次同类错误，但错误文本只有一个
// `.`，无法定位到具体调用点，所以按"补齐已知缺口"处理）。
int RoomGetGridWidth(lua_State* state);
int RoomGetGridHeight(lua_State* state);
int RoomGetGridSize(lua_State* state);
int RoomGetGridIndex(lua_State* state);
int RoomGetGridPath(lua_State* state);
int RoomGetGridEntity(lua_State* state);
int RoomGetRenderScrollOffset(lua_State* state);
int RoomGetWorldToScreenPosition(lua_State* state);
int ItemPoolGetLastPool(lua_State* state);
int ItemPoolGetCollectible(lua_State* state);

constexpr LuaHandlerBinding kLevelHandlers[] = {
    {0x03010001, &LevelGetStage},
    {0x03010002, &LevelIsAscent},
    {0x03010003, &LevelGetCurses},
    {0x03010004, &LevelGetCurrentRoomIndex},
    {0x03010005, &LevelGetCurrentRoom},
    {0x03010006, &LevelGetAbsoluteStage},
    {0x03010007, &LevelIsNextStageAvailable},
    {0x03010008, &LevelGetCurrentRoomDesc},
    {0x03010009, &LevelGetRoomByIdx},
    {0x0301000A, &LevelGetRooms},
};

constexpr LuaHandlerBinding kRoomHandlers[] = {
    {0x04010001, &RoomGetType},
    {0x04010002, &RoomGetGridWidth},
    {0x04010003, &RoomGetGridHeight},
    {0x04010004, &RoomGetGridSize},
    {0x04010005, &RoomGetGridIndex},
    {0x04010006, &RoomGetGridPath},
    {0x04010007, &RoomGetGridEntity},
    {0x04010008, &RoomGetRenderScrollOffset},
    {0x04010009, &RoomGetWorldToScreenPosition},
};

constexpr LuaHandlerBinding kItemPoolHandlers[] = {
    {0x05010001, &ItemPoolGetCollectible},
    {0x05010002, &ItemPoolGetLastPool},
};

template <typename Table>
constexpr std::size_t RowCount(const Table& table) noexcept {
    return sizeof(table) / sizeof(table[0]);
}

int LevelGetStage(lua_State* state) {
    RecordApiSequenceSecondary(17U);
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetStage accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:GetStage is only available during a Runtime callback");
    }
    std::uint32_t stage = 0;
    if (ReadCurrentGameLevelStage(GameOwnerSlot(), &stage) != GameLevelStageObservation::Success) {
        return luaL_error(state, "Level:GetStage could not read the native Level state");
    }
    lua_pushinteger(state, static_cast<lua_Integer>(stage));
    return 1;
}

int LevelIsAscent(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:IsAscent accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:IsAscent is only available during a Runtime callback");
    }
    const GameIsAscentObservation observation =
        ObserveLevelIsAscent(GameOwnerSlot(), LevelIsAscentThunk());
    if (observation == GameIsAscentObservation::AscentFalse) {
        lua_pushboolean(state, 0);
        return 1;
    }
    if (observation == GameIsAscentObservation::AscentTrue) {
        lua_pushboolean(state, 1);
        return 1;
    }
    return luaL_error(state, "Level:IsAscent could not read the native Level state");
}

// `Level:GetCurses()`（批次 4）：读 `Level + 0x0C`（u32 诅咒位掩码）。
//
// **`Level*` 就是 `Game*`**：`Level` 内嵌在 `Game` 起始处（`docs/问题与解决记录.md` 批次 4
// 的"更正旧记录"——`Level+0x21550` 与 `Game+0x21550` 是同一个地址，而 `Game+0x21550` 正是
// `kGameRoomPointerOffset` 那个 Room 指针）。所以这里与 `Level:GetStage` 走同一条链。
//
// ★ **语义偏差（必须写明）**：PC 版 `Level:GetCurses()` 是三步
// `curses | 永久诅咒 & ~禁用诅咒`（"永久诅咒"与"禁用诅咒"来自另外两个引擎来源）。那两次
// 取值的函数地址本轮**没有定位**，所以本实现**只返回字段值**，不做那两步合成 ——
// 结果是：本局临时诅咒（`AddCurse`）能读到，`持久/禁用` 两侧的修正读不到。
// 这条差异写在注释里也写在交付报告里，不做任何猜测性补齐。
//
// 引擎内存读取原语与 `isaac_api.cpp`/`game_api.cpp` 同一形态（设备侧先 `svcQueryMemory` 确认
// 可读，宿主侧只排除空地址）：拿不到就报 Lua 错误——PC 里 `Level:GetCurses()` 返回 int，
// 编一个 0 会让 Mod 以为"这一层没有诅咒"。
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

bool ReadGameCurses(std::uint32_t* curses) noexcept {
    if (curses == nullptr) {
        return false;
    }
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        return false;
    }
    // 两级解引用：槽里是**指针变量**，`*(u64*)槽` 才是 `Game*`（与 `Isaac.GetPlayer` 同链）。
    std::uintptr_t ownerSlot = 0;
    std::uintptr_t game = 0;
    if (!IsEngineMemoryReadable(base + kGameOwnerGlobalSlotOffset, sizeof(std::uintptr_t))) {
        return false;
    }
    std::memcpy(&ownerSlot, reinterpret_cast<const void*>(base + kGameOwnerGlobalSlotOffset),
                sizeof(ownerSlot));
    if (ownerSlot == 0 || !IsEngineMemoryReadable(ownerSlot, sizeof(std::uintptr_t))) {
        return false;
    }
    std::memcpy(&game, reinterpret_cast<const void*>(ownerSlot), sizeof(game));
    if (game == 0 || game > UINTPTR_MAX - kLevelCursesOffset ||
        !IsEngineMemoryReadable(game + kLevelCursesOffset, sizeof(std::uint32_t))) {
        return false;
    }
    std::memcpy(curses, reinterpret_cast<const void*>(game + kLevelCursesOffset),
                sizeof(std::uint32_t));
    return true;
}

// --- `Level:GetCurses()` 的读数探针（2026-09-12，动作三）--------------------------
//
// 为什么要它：EID 的 `hasCurseBlind()`（`eid_api.lua:597`）是"隐瞒分支"四条成因之一，
// 而它的全部输入就是 `Level:GetCurses()` 的返回值。反汇编已经证明我们的**地址链**没错、
// 但**语义**只做了三步里的第一步（见 `runtime_constants.hpp` 的
// `kGameSpecialSeedPermanentCursesOffset` 注释）。真机读数要同时回答两件事：
//   * 这一层的原始诅咒位到底是多少（`raw`）；
//   * 引擎自己的两步合成结果是多少（`permanent`/`banned`），以及调用是否成功（`flags`）。
// 有了这两个数就能判定"误判是位读错，还是缺了合成"。
std::atomic<std::uint32_t> g_ProbeRawCurses{0};
std::atomic<std::uint32_t> g_ProbePermanentCurses{0};
std::atomic<std::uint32_t> g_ProbeBannedCurses{0};
std::atomic<std::uint32_t> g_ProbeCursesFlags{0};
std::atomic<std::uint32_t> g_ProbeCursesReads{0};

// 调引擎的 `Game::GetSpecialSeedPermanentCurses()` / `GetSpecialSeedBannedCurses()`
// （都是 `unsigned int() const`，`this` 在 x0）。与其它引擎调用同一口径：
// **先验 16 字节守卫**，不符就返回 0 且**不调用**，绝不调用未校验的地址。
//
// `Game*` 的取法与 `ReadGameCurses` 完全一致：`GameOwnerSlot()` 是**指针变量**的地址
// （`SetGameBindings` 发布的那个全局槽），所以 `*(u64*)slot` 才是 `Game*`。
std::uint32_t CallGameCurseAccessor(std::uintptr_t offset,
                                    const std::array<u8, 16>& expected,
                                    bool* called) noexcept {
    if (called != nullptr) {
        *called = false;
    }
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || offset > UINTPTR_MAX - base) {
        return 0;
    }
    const std::uintptr_t target = base + offset;
    if (!IsEngineMemoryReadable(target, expected.size())) {
        return 0;
    }
    std::array<u8, 16> actual{};
    std::memcpy(actual.data(), reinterpret_cast<const void*>(target), actual.size());
    if (std::memcmp(actual.data(), expected.data(), expected.size()) != 0) {
        return 0;
    }
    const std::uintptr_t ownerSlot = GameOwnerSlot();
    std::uintptr_t game = 0;
    if (ownerSlot == 0 || !IsEngineMemoryReadable(ownerSlot, sizeof(game))) {
        return 0;
    }
    std::memcpy(&game, reinterpret_cast<const void*>(ownerSlot), sizeof(game));
    if (game == 0) {
        return 0;
    }
    using AccessorFn = std::uint32_t (*)(void*);
    const std::uint32_t value = reinterpret_cast<AccessorFn>(target)(reinterpret_cast<void*>(game));
    if (called != nullptr) {
        *called = true;
    }
    return value;
}

void RecordGameCursesReadForProbe(std::uint32_t raw) noexcept {
    g_ProbeCursesReads.fetch_add(1, std::memory_order_relaxed);
    g_ProbeRawCurses.store(raw, std::memory_order_relaxed);
    std::uint32_t flags = 1U;  // bit0：原始位读到了（能走到这里就说明读到了）
    bool permanentCalled = false;
    bool bannedCalled = false;
    const std::uint32_t permanent = CallGameCurseAccessor(
        kGameSpecialSeedPermanentCursesOffset, kGameSpecialSeedPermanentCursesExpectedBytes,
        &permanentCalled);
    const std::uint32_t banned = CallGameCurseAccessor(kGameSpecialSeedBannedCursesOffset,
                                                       kGameSpecialSeedBannedCursesExpectedBytes,
                                                       &bannedCalled);
    if (permanentCalled) {
        flags |= 2U;  // bit1：permanent 访问器**确实被调到**（守卫通过）
    }
    if (bannedCalled) {
        flags |= 4U;  // bit2：banned 访问器确实被调到
    }
    g_ProbePermanentCurses.store(permanent, std::memory_order_relaxed);
    g_ProbeBannedCurses.store(banned, std::memory_order_relaxed);
    g_ProbeCursesFlags.store(flags, std::memory_order_relaxed);
}

int LevelGetCurses(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetCurses accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:GetCurses is only available during a Runtime callback");
    }
    std::uint32_t curses = 0;
    if (!ReadGameCurses(&curses)) {
        return luaL_error(state, "Level:GetCurses could not read the native Level state");
    }
    RecordGameCursesReadForProbe(curses);
    lua_pushinteger(state, static_cast<lua_Integer>(curses));
    return 1;
}

// --- 批次 8（2026-09-15）：`Level` 家族另外四个成员 ------------------------------
//
// 缺口来源：`tools/eid_api_gap_report.py` 的高置信表（`missing_in_runtime`）。EID 在
// **描述构建**路径上无条件调用它们，缺一个就是 "attempt to call a nil value" ⇒ 整条描述回调
// 在报错处中断（`features/eid_modifiers.lua:197/200` 的潘多拉魔盒条目就是这一处）。
//
// 指针链与 `ReadGameCurses` 逐句相同：`GameOwnerSlot()` 是**指针变量**的地址，
// `*(u64*)slot` 才是 `Game*`；而 `Level` 就内嵌在 `Game` 起始处（同一地址），所以
// `Level*` 与 `Game*` 是同一个值 —— 这不是新假设，`Level:GetStage`/`GetCurses` 走的就是这条链。
//
// **引擎方法（两个）**：入口地址由安装期 16 字节守卫校验后发布（`...Thunk()`，0 = 不可用），
// handler 调用前**再验一次**同一份守卫 —— 与 `CallGameCurseAccessor` 同一口径：
// 宁可报"不可用"，也绝不调用一个没校验过的地址。
bool ResolveCurrentLevel(std::uintptr_t* level) noexcept {
    if (level == nullptr) {
        return false;
    }
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        return false;
    }
    if (!IsEngineMemoryReadable(base + kGameOwnerGlobalSlotOffset, sizeof(std::uintptr_t))) {
        return false;
    }
    std::uintptr_t ownerSlot = 0;
    std::memcpy(&ownerSlot, reinterpret_cast<const void*>(base + kGameOwnerGlobalSlotOffset),
                sizeof(ownerSlot));
    if (ownerSlot == 0 || !IsEngineMemoryReadable(ownerSlot, sizeof(std::uintptr_t))) {
        return false;
    }
    std::uintptr_t game = 0;
    std::memcpy(&game, reinterpret_cast<const void*>(ownerSlot), sizeof(game));
    if (game == 0) {
        return false;
    }
    *level = game;
    return true;
}

// 调用一个"只吃 `this`、返回 int"的 `Level` 成员（当前只有 `GetAbsoluteStage`）。
// 返回 false 表示"这次没有真的调用到引擎"，调用方必须据此报错而不是编一个值。
bool CallLevelIntMethod(std::uintptr_t method, const std::array<u8, 16>& expected,
                        std::int32_t* value) noexcept {
    if (value == nullptr || method == 0 || (method & 3) != 0 ||
        !IsEngineMemoryReadable(method, expected.size())) {
        return false;
    }
    std::array<u8, 16> actual{};
    std::memcpy(actual.data(), reinterpret_cast<const void*>(method), actual.size());
    if (actual != expected) {
        return false;
    }
    std::uintptr_t level = 0;
    if (!ResolveCurrentLevel(&level)) {
        return false;
    }
    using LevelIntMethod = std::int32_t (*)(const void*);
    *value = reinterpret_cast<LevelIntMethod>(method)(reinterpret_cast<const void*>(level));
    return true;
}

// 调"向量进、向量出"的引擎函数（`GetRenderPosition(const Vector2&, bool)`）。
//
// 与 `CallLevelIntMethod` 同一形态：**先核对入口 16 字节**再调用 —— 只认"这个地址上确实是
// 那一串指令"，不认"地址恰好落在模块里"。返回值是 8 字节的结构体（两个 float），
// AAPCS64 下走 `s0/s1`，所以直接按 `Vec2` 值返回即可。
[[maybe_unused]] bool CallVectorEngineMethod(std::uintptr_t method,
                                            const std::array<u8, 16>& expected,
                            const float* input, float* output, bool argument) noexcept {
    if (method == 0 || input == nullptr || output == nullptr || (method & 3) != 0 ||
        !IsEngineMemoryReadable(method, expected.size())) {
        return false;
    }
    std::array<u8, 16> actual{};
    std::memcpy(actual.data(), reinterpret_cast<const void*>(method), actual.size());
    if (std::memcmp(expected.data(), actual.data(), actual.size()) != 0) {
        return false;
    }
    struct Vector2Value {
        float x;
        float y;
    };
    using Method = Vector2Value (*)(const Vector2Value&, bool);
    const Vector2Value value{input[0], input[1]};
    const Vector2Value result = reinterpret_cast<Method>(method)(value, argument);
    output[0] = result.x;
    output[1] = result.y;
    return true;
}

bool CallLevelBoolMethod(std::uintptr_t method, const std::array<u8, 16>& expected,
                         bool* value) noexcept {
    if (value == nullptr || method == 0 || (method & 3) != 0 ||
        !IsEngineMemoryReadable(method, expected.size())) {
        return false;
    }
    std::array<u8, 16> actual{};
    std::memcpy(actual.data(), reinterpret_cast<const void*>(method), actual.size());
    if (actual != expected) {
        return false;
    }
    std::uintptr_t level = 0;
    if (!ResolveCurrentLevel(&level)) {
        return false;
    }
    using LevelBoolMethod = bool (*)(const void*);
    *value = reinterpret_cast<LevelBoolMethod>(method)(reinterpret_cast<const void*>(level));
    return true;
}

int LevelGetCurrentRoomIndex(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetCurrentRoomIndex accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Level:GetCurrentRoomIndex is only available during a Runtime callback");
    }
    std::uintptr_t level = 0;
    if (!ResolveCurrentLevel(&level) ||
        !IsEngineMemoryReadable(level + kLevelCurrentRoomIndexOffset, sizeof(std::uint32_t))) {
        return luaL_error(state, "Level:GetCurrentRoomIndex could not read the native Level state");
    }
    std::uint32_t index = 0;
    std::memcpy(&index, reinterpret_cast<const void*>(level + kLevelCurrentRoomIndexOffset),
                sizeof(index));
    lua_pushinteger(state, static_cast<lua_Integer>(index));
    return 1;
}

// `Level:GetCurrentRoom()` —— PC 语义是"当前房间对象"，与 `Game:GetRoom()` 返回同一个 `Room`。
// 复用 `ReadCurrentGameRoom`（`Game + 0x21550`，已上机验证）与 `Game:GetRoom` 完全相同的句柄编组，
// 所以这里不引入新的偏移假设。
int LevelGetCurrentRoom(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetCurrentRoom accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:GetCurrentRoom is only available during a Runtime callback");
    }
    void* room = nullptr;
    if (ReadCurrentGameRoom(GameOwnerSlot(), &room) != GameRoomObservation::Success) {
        return luaL_error(state, "Level:GetCurrentRoom could not read the native Room state");
    }
    auto* handle = static_cast<LuaRuntime::RoomHandle*>(
        lua_newuserdata(state, sizeof(LuaRuntime::RoomHandle)));
    handle->reserved = 0;
    luaL_getmetatable(state, kRoomMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

int LevelGetAbsoluteStage(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetAbsoluteStage accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Level:GetAbsoluteStage is only available during a Runtime callback");
    }
    std::int32_t stage = 0;
    if (!CallLevelIntMethod(LuaRuntime::LevelGetAbsoluteStageThunk(),
                            kLevelGetAbsoluteStageExpectedBytes, &stage)) {
        return luaL_error(state, "Level:GetAbsoluteStage is unavailable in this build");
    }
    lua_pushinteger(state, static_cast<lua_Integer>(stage));
    return 1;
}

int LevelIsNextStageAvailable(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:IsNextStageAvailable accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Level:IsNextStageAvailable is only available during a Runtime callback");
    }
    bool available = false;
    if (!CallLevelBoolMethod(LuaRuntime::LevelIsNextStageAvailableThunk(),
                             kLevelIsNextStageAvailableExpectedBytes, &available)) {
        return luaL_error(state, "Level:IsNextStageAvailable is unavailable in this build");
    }
    lua_pushboolean(state, available ? 1 : 0);
    return 1;
}

int RoomGetType(lua_State* state) {
    RecordApiSequenceSecondary(16U);
    luaL_checkudata(state, 1, kRoomMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Room:GetType accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Room:GetType is only available during a Runtime callback");
    }
    std::uint32_t roomType = 0;
    if (ReadCurrentGameRoomType(GameOwnerSlot(), &roomType) != GameRoomObservation::Success) {
        return luaL_error(state, "Room:GetType could not read the native Room state");
    }
    lua_pushinteger(state, static_cast<lua_Integer>(roomType));
    return 1;
}

// --- 批次 7：`Room` 的网格与寻路成员 -----------------------------------------
//
// **这些实现是"保守但自洽"的占位，不是引擎真值**，原因与边界都写在这里：
//
//   * 引擎符号表里 **没有** `Room::GetGridWidth`/`GetGridHeight`/`GetGridSize`
//     （只有 `Room::GetGridIndex`/`GetGridEntity`/`GetGridPath` 这三个方法），说明 PC 侧那几条
//     Lua 访问器是**字段直读**，而 Room 结构里网格尺寸的字段偏移**尚未定位**；
//   * EID 的用法（`eid_api.lua:3351-3380`）是：先用宽高做边界判断，再用
//     `gridIndex = x * width + y` 算下标，最后用 `collision <= 900` 判"能不能走"；
//   * 如果编一个**错误**的宽高（例如 0），`startGI % width` 会触发除零；如果编一个大的真值，
//     EID 会真的认为"这里有路"并据此画提示 —— 那是**用假数据影响显示**，不可接受。
//
// 因此这里的口径是"**一律判为不可走**"：
//   * 宽高返回 13（本体标准房间的网格边长，非 0，避免除零；仅用于让下标计算不炸）；
//   * `GetGridPath` 返回 1000（EID 的判据是 `> 900 = 不可走`）⇒ `EvaluateLocation` 恒 false
//     ⇒ `HasPathToPosition` 恒 false ⇒ EID 走"没有可达路径"的保守分支（不画路径相关提示），
//     而不是按假数据画出错误提示。
//   * `GetGridIndex` 按 EID 自己的公式（`x * width + y`）算，纯算术、无引擎依赖。
//   * `GetGridEntity` 返回 nil：没有真实的网格实体数据时，"没有实体"比编一个实体安全。
//
// 成熟度在 Catalog 里统一标 `Experimental`。要变成真实现，需要先做一次静态审计定位
// `Room::GetGridIndex(int,int)` / `GetGridEntity(int,int)` 的**文件偏移**（符号已确认存在）
// 以及网格尺寸字段的偏移，然后把这里的常量替换掉。
constexpr std::uint32_t kRoomGridFallbackWidth = 13;
constexpr std::uint32_t kRoomGridFallbackHeight = 13;
// EID 的"不可走"阈值是 900（`eid_api.lua:3360`），这里给一个明确大于它的值。
constexpr std::uint32_t kRoomGridPathBlocked = 1000;

bool RoomArgumentCountIs(lua_State* state, int expected, const char* apiName) {
    luaL_checkudata(state, 1, kRoomMetatable);
    if (lua_gettop(state) != expected) {
        luaL_error(state, "%s accepts %d argument(s)", apiName, expected - 1);
        return false;
    }
    if (!InManagedCallbackScope()) {
        luaL_error(state, "%s is only available during a Runtime callback", apiName);
        return false;
    }
    return true;
}

// Lua 5.3 distinguishes integer and float subtypes. EID computes grid indexes from Vector
// coordinates, so an integral value can arrive as `5.0`; accept any number that converts
// losslessly to an integer, matching the PC C API's integer parameter contract.
bool RoomGridIndexArgument(lua_State* state, int index, lua_Integer* value) {
    int isNumber = 0;
    const lua_Integer converted = lua_tointegerx(state, index, &isNumber);
    if (!isNumber) {
        return false;
    }
    if (value != nullptr) {
        *value = converted;
    }
    return true;
}

int RoomGetGridWidth(lua_State* state) {
    if (!RoomArgumentCountIs(state, 1, "Room:GetGridWidth")) {
        return 0;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(kRoomGridFallbackWidth));
    return 1;
}

int RoomGetGridHeight(lua_State* state) {
    if (!RoomArgumentCountIs(state, 1, "Room:GetGridHeight")) {
        return 0;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(kRoomGridFallbackHeight));
    return 1;
}

int RoomGetGridSize(lua_State* state) {
    if (!RoomArgumentCountIs(state, 1, "Room:GetGridSize")) {
        return 0;
    }
    // PC 返回网格格子总数（`width * height`）。
    lua_pushinteger(state,
                    static_cast<lua_Integer>(kRoomGridFallbackWidth) * kRoomGridFallbackHeight);
    return 1;
}

int RoomGetGridIndex(lua_State* state) {
    luaL_checkudata(state, 1, kRoomMetatable);
    const int argumentCount = lua_gettop(state);
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Room:GetGridIndex is only available during a Runtime callback");
    }
    // 两个重载：`GetGridIndex(int,int)` 与 `GetGridIndex(Vector)`。EID 用的是 Vector 版
    // （`eid_api.lua:3375` 的 `room:GetGridIndex(startPos)`），但 PC 两种都有，这里都支持。
    if (argumentCount == 2) {
        auto* position =
            static_cast<LuaRuntime::VectorHandle*>(luaL_testudata(state, 2, LuaRuntime::kVectorMetatable));
        if (position == nullptr) {
            return luaL_error(state, "Room:GetGridIndex accepts a Vector");
        }
        // PC：格坐标 = 世界坐标 / 40（一格 40 像素，EID 的注释也是这么写的）。
        const auto toCell = [](float world) {
            const float cell = world / 40.0F;
            return cell < 0.0F ? 0 : static_cast<lua_Integer>(cell);
        };
        const lua_Integer x = toCell(position->x);
        const lua_Integer y = toCell(position->y);
        lua_pushinteger(state, x * static_cast<lua_Integer>(kRoomGridFallbackWidth) + y);
        return 1;
    }
    lua_Integer x = 0;
    lua_Integer y = 0;
    if (argumentCount != 3 || !lua_isinteger(state, 2) || !lua_isinteger(state, 3)) {
        return luaL_error(state, "Room:GetGridIndex accepts a Vector or two integers");
    }
    x = lua_tointegerx(state, 2, nullptr);
    y = lua_tointegerx(state, 3, nullptr);
    if (x < 0 || y < 0) {
        return luaL_error(state, "Room:GetGridIndex does not accept negative coordinates");
    }
    lua_pushinteger(state, x * static_cast<lua_Integer>(kRoomGridFallbackWidth) + y);
    return 1;
}

int RoomGetGridPath(lua_State* state) {
    luaL_checkudata(state, 1, kRoomMetatable);
    if (lua_gettop(state) != 2 || !RoomGridIndexArgument(state, 2, nullptr)) {
        return luaL_error(state, "Room:GetGridPath accepts one grid index");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Room:GetGridPath is only available during a Runtime callback");
    }
    // 见上面的口径说明：恒报"不可走"（1000 > EID 的 900 阈值），不按假数据画出可达路径。
    lua_pushinteger(state, static_cast<lua_Integer>(kRoomGridPathBlocked));
    return 1;
}

int RoomGetGridEntity(lua_State* state) {
    luaL_checkudata(state, 1, kRoomMetatable);
    const int argumentCount = lua_gettop(state);
    if (argumentCount != 2 || !RoomGridIndexArgument(state, 2, nullptr)) {
        return luaL_error(state, "Room:GetGridEntity accepts one grid index");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Room:GetGridEntity is only available during a Runtime callback");
    }
    // 没有真实的网格实体数据：返回 nil（"这里没有网格实体"），不编造对象。
    lua_pushnil(state);
    return 1;
}

// `Room:GetRenderScrollOffset()`（PC 文档 `Room.md:473`，返回 `const Vector`）：
// 房间渲染的滚动偏移。EID 用它把世界坐标换算到屏幕坐标。
//
// 偏移 `+0x1938` 的证据（`tools/layout_tables/room.json`）：引擎自己的
// `Room::WorldToScreenPosition` 里先 `mov w8, #0x1938`、再 `add x1, x19, x8`，
// 把这个字段的**地址**交给 `Vector2::operator+` —— 也就是"世界→屏幕"要加上的那个向量。
// `build_layout_table.py --verify` 会重新反汇编核对这条证据。
int RoomGetRenderScrollOffset(lua_State* state) {
    luaL_checkudata(state, 1, kRoomMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Room:GetRenderScrollOffset accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Room:GetRenderScrollOffset is only available during a Runtime callback");
    }
    void* room = nullptr;
    if (ReadCurrentGameRoom(GameOwnerSlot(), &room) != GameRoomObservation::Success ||
        room == nullptr) {
        return luaL_error(state,
                          "Room:GetRenderScrollOffset could not read the native Room state");
    }
    const std::uintptr_t address = reinterpret_cast<std::uintptr_t>(room);
    float x = 0.0F;
    float y = 0.0F;
    const std::uintptr_t scrollOffset = address + layout::kRoomRenderScrollOffsetOffset;
    if (!IsEngineMemoryReadable(scrollOffset, sizeof(x) + sizeof(y))) {
        return luaL_error(state,
                          "Room:GetRenderScrollOffset could not read the native Room state");
    }
    std::memcpy(&x, reinterpret_cast<const void*>(scrollOffset), sizeof(x));
    std::memcpy(&y, reinterpret_cast<const void*>(scrollOffset + sizeof(float)), sizeof(y));
    // 每次返回**新的** `Vector` userdata：别名会让 `room:GetRenderScrollOffset().X = 1`
    // 这类写法改到下一次读取的结果上（与 `Entity.Position` 同一口径）。
    auto* vector = static_cast<LuaRuntime::VectorHandle*>(
        lua_newuserdata(state, sizeof(LuaRuntime::VectorHandle)));
    vector->x = x;
    vector->y = y;
    luaL_getmetatable(state, LuaRuntime::kVectorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

// `Room:WorldToScreenPosition(Vector)`（批次 10）：把世界坐标换算成屏幕坐标。
// PC 文档 `Room.md:974`，EID 用它把实体位置画到屏幕上。
//
// **换算方式直接来自引擎自己的实现**（`Room::WorldToScreenPosition @ 0x489354` 的反汇编）：
//   `GetRenderPosition(世界坐标, true) + Room.RenderScrollOffset + Game.ToScreenAdjust`
// 三处偏移/入口的证据见 `runtime_constants.hpp` 里那三个常量各自的注释。
//
// 失败一律报 Lua 错误（PC 返回 Vector，编一个 (0,0) 会让 Mod 把东西画到左上角）。
int RoomGetWorldToScreenPosition(lua_State* state) {
    luaL_checkudata(state, 1, kRoomMetatable);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Room:WorldToScreenPosition expects a Vector");
    }
    auto* input = static_cast<LuaRuntime::VectorHandle*>(
        luaL_testudata(state, 2, LuaRuntime::kVectorMetatable));
    if (input == nullptr) {
        return luaL_error(state, "Room:WorldToScreenPosition expects a Vector");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Room:WorldToScreenPosition is only available during a Runtime callback");
    }
    void* room = nullptr;
    if (ReadCurrentGameRoom(GameOwnerSlot(), &room) != GameRoomObservation::Success ||
        room == nullptr) {
        return luaL_error(state, "Room:WorldToScreenPosition could not read the native Room state");
    }
    float screen[2] = {input->x, input->y};
    bool rendered = false;
#if !defined(__SWITCH__)
    // 宿主：没有引擎映像，用注入的实现（与 `HasCollectible` 的宿主钩子同一形态）。
    if (LuaRuntime::GetRenderPositionHostFunction() != nullptr) {
        LuaRuntime::GetRenderPositionHostFunction()(&input->x, screen, true);
        rendered = true;
    }
#else
    rendered = CallVectorEngineMethod(LuaRuntime::GetRenderPositionThunk(),
                                      kGetRenderPositionStubExpectedBytes, &input->x, screen, true);
#endif
    if (!rendered) {
        return luaL_error(state, "Room:WorldToScreenPosition is unavailable in this build");
    }
    // 再加上房间与 Game 各自那一份调整量（同一个函数里的两条 `Vector2::operator+`）。
    const std::uintptr_t roomAddress = reinterpret_cast<std::uintptr_t>(room);
    const std::uintptr_t roomScroll = roomAddress + layout::kRoomRenderScrollOffsetOffset;
    std::uintptr_t game = 0;
    if (ResolveCurrentLevel(&game) == false || game == 0) {
        return luaL_error(state, "Room:WorldToScreenPosition could not read the native Game state");
    }
    const std::uintptr_t gameAdjust = game + kGameToScreenAdjustOffset;
    if (!IsEngineMemoryReadable(roomScroll, sizeof(float) * 2) ||
        !IsEngineMemoryReadable(gameAdjust, sizeof(float) * 2)) {
        return luaL_error(state, "Room:WorldToScreenPosition could not read the native state");
    }
    float roomX = 0.0F;
    float roomY = 0.0F;
    float gameX = 0.0F;
    float gameY = 0.0F;
    std::memcpy(&roomX, reinterpret_cast<const void*>(roomScroll), sizeof(float));
    std::memcpy(&roomY, reinterpret_cast<const void*>(roomScroll + sizeof(float)), sizeof(float));
    std::memcpy(&gameX, reinterpret_cast<const void*>(gameAdjust), sizeof(float));
    std::memcpy(&gameY, reinterpret_cast<const void*>(gameAdjust + sizeof(float)), sizeof(float));
    auto* result = static_cast<LuaRuntime::VectorHandle*>(
        lua_newuserdata(state, sizeof(LuaRuntime::VectorHandle)));
    result->x = screen[0] + roomX + gameX;
    result->y = screen[1] + roomY + gameY;
    luaL_getmetatable(state, LuaRuntime::kVectorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

int ItemPoolGetLastPool(lua_State* state) {
    luaL_checkudata(state, 1, kItemPoolMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "ItemPool:GetLastPool accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "ItemPool:GetLastPool is only available during a Runtime callback");
    }
    void* itemPool = nullptr;
    if (ReadCurrentGameItemPool(GameOwnerSlot(), &itemPool) != GameItemPoolObservation::Success) {
        return luaL_error(state, "ItemPool:GetLastPool could not read the native ItemPool state");
    }
    const std::uintptr_t address = reinterpret_cast<std::uintptr_t>(itemPool);
    if ((address & (alignof(std::uint32_t) - 1)) != 0 ||
        address > UINTPTR_MAX - kItemPoolLastPoolOffset ||
        !IsEngineMemoryReadable(address + kItemPoolLastPoolOffset, sizeof(std::uint32_t))) {
        return luaL_error(state, "ItemPool:GetLastPool could not read the last pool");
    }
    std::uint32_t lastPool = 0;
    std::memcpy(&lastPool, reinterpret_cast<const void*>(address + kItemPoolLastPoolOffset),
                sizeof(lastPool));
    lua_pushinteger(state, static_cast<lua_Integer>(lastPool));
    return 1;
}

int ItemPoolGetCollectible(lua_State* state) {
    luaL_checkudata(state, 1, kItemPoolMetatable);
    const int argumentCount = lua_gettop(state);
    if ((argumentCount != 4 && argumentCount != 5) || !lua_isinteger(state, 2) ||
        !lua_isinteger(state, 4) || (argumentCount == 5 && !lua_isinteger(state, 5))) {
        return luaL_error(state,
                          "ItemPool:GetCollectible accepts poolType, decrease, seed and optional defaultItem");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "ItemPool:GetCollectible is only available during a Runtime callback");
    }
    const lua_Integer poolType = lua_tointegerx(state, 2, nullptr);
    const lua_Integer seed = lua_tointegerx(state, 4, nullptr);
    const lua_Integer defaultItem = argumentCount == 5 ? lua_tointegerx(state, 5, nullptr) : 0;
    if (poolType < 0 || static_cast<std::uint64_t>(poolType) > std::numeric_limits<std::uint32_t>::max() ||
        seed < 0 || static_cast<std::uint64_t>(seed) > std::numeric_limits<std::uint32_t>::max() ||
        defaultItem < 0 || static_cast<std::uint64_t>(defaultItem) > std::numeric_limits<std::uint32_t>::max()) {
        return luaL_error(state, "ItemPool:GetCollectible integer arguments are outside uint32 range");
    }
    void* itemPool = nullptr;
    if (ReadCurrentGameItemPool(GameOwnerSlot(), &itemPool) != GameItemPoolObservation::Success) {
        return luaL_error(state, "ItemPool:GetCollectible could not read the native ItemPool state");
    }
    const std::uintptr_t method = ItemPoolGetCollectibleThunk();
    if (!ValidateItemPoolGetCollectibleBinding(method)) {
        return luaL_error(state, "ItemPool:GetCollectible native bindings are unavailable");
    }
    using GetCollectibleFn = std::uint32_t (*)(void*, std::uint32_t, std::uint32_t, std::uint32_t,
                                               std::uint32_t);
    const auto result = reinterpret_cast<GetCollectibleFn>(method)(
        itemPool, static_cast<std::uint32_t>(poolType), static_cast<std::uint32_t>(seed),
        lua_toboolean(state, 3) ? 0u : 1u, static_cast<std::uint32_t>(defaultItem));
    lua_pushinteger(state, static_cast<lua_Integer>(result));
    return 1;
}

// --- 地基二期（2026-09-15）：`RoomDescriptor` 与它的三件套 API -----------------------
//
// 字段偏移**全部来自布局表 + 真机行为验证**（`tools/layout_tables/room_descriptor.json`，
// 每条带可复核证据），不是从 PC 版抄的：
//
//   描述符数组：内联在 Level(=Game) 对象里，基址 `Level + 0x18`、步长 `0x100`、
//              元素个数在 `Level + 0x21510`（真机实测 11 个已分配槽位，ListIndex 恰好 0..10）；
//   `+0x00` GridIndex（网格下标）、`+0x04` SafeGridIndex、`+0x08` ListIndex、
//   `+0x10` Data（指向房间配置，其中 `Data+0x08` 是 Type）、
//   `+0x4c` VisitedCount、`+0x50` Clear。
//
// **只暴露已 confirmed 的字段**：未确认的（例如 `+0x48` 的显示标志、`Data.Variant`）
// 一律返回 nil，并按项目规矩在文档/台账里标为未满足 —— 读错字段比读不到更糟（会给出错误内容）。
//
// `Level:GetRoomByIdx` 的定位方式：**扫描数组找 `+0x00 == 目标索引`**。
// 引擎自己那段"找起始房间"的循环（`0x3dc624`）也是扫数组；而 `Level + 0x2d18` 那张表
// 经真机验证**不是"索引→槽号"**（索引 97 读出槽号 6，可 slot6 是另一个房间），所以不依赖它。
namespace {

//: 描述符数组的几何（与布局表一致；改动必须同时改表并由 `--verify` 复核）。
constexpr std::uintptr_t kDescriptorArrayBase = 0x18;
constexpr std::uintptr_t kDescriptorStride = 0x100;
constexpr std::uintptr_t kDescriptorCountOffset = 0x21510;
//: 未分配槽位的 `+0x00` 是 0xFFFFFFFF（真机实测：11 号槽位起全是它）。
constexpr std::uint32_t kUnallocatedGridIndex = 0xFFFFFFFFu;
//: 描述符内部偏移。
constexpr std::uintptr_t kGridIndexOffset = 0x00;
constexpr std::uintptr_t kSafeGridIndexOffset = 0x04;
constexpr std::uintptr_t kListIndexOffset = 0x08;
constexpr std::uintptr_t kDataOffset = 0x10;
constexpr std::uintptr_t kVisitedCountOffset = 0x4C;
constexpr std::uintptr_t kClearOffset = 0x50;
//: `Data` 指向的房间配置里，Type 在 +0x8（布局表 confirmed）。
constexpr std::uintptr_t kRoomConfigTypeOffset = 0x08;
//: 数组元素个数上限：3 个维度 × 169 个房间 + 少量特殊槽位，实际远小于 512。
constexpr std::uint32_t kDescriptorMaximumCount = 512;

// `Level` 就内嵌在 `Game` 起始处，所以解析链与 `ReadGameCurses` 逐字相同：
//   `GameOwnerSlot()` 是**槽的地址**（指针变量）→ 解一次得 owner → 再解一次才是 `Game*`。
// ★ 第一版直接把 `GameOwnerSlot()` 当 Level 用，少解了两层 —— 那会去读模块里的代码字节
// （真机探针上踩过同一个坑：`g_Game` 槽里存的不是对象本身）。
std::uintptr_t ResolveLevelForDescriptor() noexcept {
    const std::uintptr_t slot = GameOwnerSlot();
    if (slot == 0 || !IsEngineMemoryReadable(slot, sizeof(std::uintptr_t))) {
        return 0;
    }
    std::uintptr_t owner = 0;
    std::memcpy(&owner, reinterpret_cast<const void*>(slot), sizeof(owner));
    if (owner == 0 || !IsEngineMemoryReadable(owner, sizeof(std::uintptr_t))) {
        return 0;
    }
    std::uintptr_t level = 0;
    std::memcpy(&level, reinterpret_cast<const void*>(owner), sizeof(level));
    return level;
}

bool ReadDescriptorU32(std::uintptr_t descriptor, std::uintptr_t offset,
                       std::uint32_t* value) noexcept {
    if (value == nullptr || descriptor == 0 || descriptor > UINTPTR_MAX - offset) {
        return false;
    }
    if (!IsEngineMemoryReadable(descriptor + offset, sizeof(std::uint32_t))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(descriptor + offset), sizeof(*value));
    return true;
}

std::uintptr_t DescriptorAddress(std::uintptr_t level, std::uint32_t slot) noexcept {
    return level + kDescriptorArrayBase + static_cast<std::uintptr_t>(slot) * kDescriptorStride;
}

//: 房间数组里当前有多少个槽位（引擎自己用它做扫描边界）。
std::uint32_t DescriptorCount(std::uintptr_t level) noexcept {
    if (level == 0 || level > UINTPTR_MAX - kDescriptorCountOffset ||
        !IsEngineMemoryReadable(level + kDescriptorCountOffset, sizeof(std::uint32_t))) {
        return 0;
    }
    std::uint32_t count = 0;
    std::memcpy(&count, reinterpret_cast<const void*>(level + kDescriptorCountOffset),
                sizeof(count));
    // 上限只用来兜住"读到垃圾值"的情形：3 个维度 × 169 个房间 + 少量特殊槽位，实际远小于 512。
    return count > kDescriptorMaximumCount ? kDescriptorMaximumCount : count;
}

//: 按 `+0x00 == gridIndex` 扫描定位描述符；找不到返回 0。
std::uintptr_t FindDescriptorByGridIndex(std::uintptr_t level, std::uint32_t gridIndex) noexcept {
    if (level == 0 || gridIndex == kUnallocatedGridIndex) {
        return 0;
    }
    const std::uint32_t count = DescriptorCount(level);
    for (std::uint32_t slot = 0; slot < count; ++slot) {
        const std::uintptr_t candidate = DescriptorAddress(level, slot);
        std::uint32_t value = 0;
        if (!ReadDescriptorU32(candidate, kGridIndexOffset, &value)) {
            return 0;
        }
        if (value == gridIndex) {
            return candidate;
        }
    }
    return 0;
}

std::uintptr_t CurrentRoomIndex(std::uintptr_t level) noexcept {
    std::uint32_t index = 0;
    if (!ReadDescriptorU32(level, kLevelCurrentRoomIndexOffset, &index)) {
        return kUnallocatedGridIndex;
    }
    return index;
}

int PushRoomDescriptor(lua_State* state, std::uintptr_t descriptor) {
    if (descriptor == 0) {
        lua_pushnil(state);
        return 1;
    }
    auto* handle = static_cast<LuaRuntime::RoomDescriptorHandle*>(
        lua_newuserdata(state, sizeof(LuaRuntime::RoomDescriptorHandle)));
    handle->descriptor = reinterpret_cast<void*>(descriptor);
    luaL_getmetatable(state, LuaRuntime::kRoomDescriptorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

}  // namespace


int RoomDescriptorListGet(lua_State* state) {
    auto* handle = static_cast<LuaRuntime::RoomDescriptorListHandle*>(
        luaL_checkudata(state, 1, LuaRuntime::kRoomDescriptorListMetatable));
    if (handle == nullptr || !lua_isinteger(state, 2)) {
        return luaL_error(state, "RoomDescriptorList:Get expects an index");
    }
    const lua_Integer requested = lua_tointegerx(state, 2, nullptr);
    const std::uintptr_t level = reinterpret_cast<std::uintptr_t>(handle->level);
    const std::uint32_t count = DescriptorCount(level);
    if (requested < 0 || static_cast<std::uint64_t>(requested) >= count) {
        lua_pushnil(state);
        return 1;
    }
    const std::uint32_t slot = static_cast<std::uint32_t>(requested);
    std::uintptr_t descriptor = DescriptorAddress(level, slot);
    std::uint32_t listIndex = 0;
    if (ReadDescriptorU32(descriptor, kListIndexOffset, &listIndex) && listIndex != slot) {
        descriptor = 0;
        for (std::uint32_t candidate = 0; candidate < count; ++candidate) {
            const std::uintptr_t address = DescriptorAddress(level, candidate);
            std::uint32_t value = 0;
            if (ReadDescriptorU32(address, kListIndexOffset, &value) && value == slot) {
                descriptor = address;
                break;
            }
        }
    }
    return PushRoomDescriptor(state, descriptor);
}

// `RoomDescriptorList` 的**方法**表（`Size` 是字段、走 `__index`；这里只有 `Get`）。
// 这一族没有 catalog id（它是 `Level:GetRooms()` 的返回值，不是 Mod 直接调用的 API），
// 所以不能用 `AttachOwnerMethods` 那套"id → handler"的绑定方式，直接按名字挂。
struct NamedHandler {
    const char* name;
    lua_CFunction handler;
};

int LevelGetCurrentRoomDesc(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetCurrentRoomDesc accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state,
                          "Level:GetCurrentRoomDesc is only available during a Runtime callback");
    }
    const std::uintptr_t level = ResolveLevelForDescriptor();
    if (level == 0) {
        return luaL_error(state, "Level:GetCurrentRoomDesc could not read the native Level state");
    }
    const std::uintptr_t index = CurrentRoomIndex(level);
    if (index == kUnallocatedGridIndex) {
        return luaL_error(state, "Level:GetCurrentRoomDesc could not read the current room index");
    }
    return PushRoomDescriptor(state, FindDescriptorByGridIndex(level,
                                                              static_cast<std::uint32_t>(index)));
}

int LevelGetRoomByIdx(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 3 || !lua_isinteger(state, 2)) {
        return luaL_error(state, "Level:GetRoomByIdx expects an index and an optional dimension");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:GetRoomByIdx is only available during a Runtime callback");
    }
    const lua_Integer requested = lua_tointegerx(state, 2, nullptr);
    if (argumentCount == 3 && lua_isinteger(state, 3)) {
        const lua_Integer dimension = lua_tointegerx(state, 3, nullptr);
        if (dimension != 0 && dimension != -1) {
            // 非 0 维度还没验证过（真机那几轮都在普通楼层，维度恒为 0）。宁可给 nil，
            // 也不把"另一个维度的房间"当成当前维度的返回给 Mod。
            lua_pushnil(state);
            return 1;
        }
    }
    const std::uintptr_t level = ResolveLevelForDescriptor();
    if (level == 0) {
        return luaL_error(state, "Level:GetRoomByIdx could not read the native Level state");
    }
    if (requested == -1) {
        // PC 语义：`GetRoomByIdx(-1)` = 当前房间。这里按"当前索引"解析，与 `GetCurrentRoomDesc`
        // 同一条链（而不是去信那个没验证过的负数槽位公式）。
        const std::uintptr_t index = CurrentRoomIndex(level);
        if (index == kUnallocatedGridIndex) {
            lua_pushnil(state);
            return 1;
        }
        return PushRoomDescriptor(state, FindDescriptorByGridIndex(level,
                                                                  static_cast<std::uint32_t>(index)));
    }
    if (requested < 0) {
        // PC 侧 -2/-3 是"上一个/下一个房间"，其含义**没有证据**（负数槽位公式未验证）⇒ 给 nil。
        lua_pushnil(state);
        return 1;
    }
    return PushRoomDescriptor(
        state, FindDescriptorByGridIndex(level, static_cast<std::uint32_t>(requested)));
}

int LevelGetRooms(lua_State* state) {
    luaL_checkudata(state, 1, kLevelMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Level:GetRooms accepts no arguments");
    }
    if (!InManagedCallbackScope()) {
        return luaL_error(state, "Level:GetRooms is only available during a Runtime callback");
    }
    const std::uintptr_t level = ResolveLevelForDescriptor();
    if (level == 0) {
        return luaL_error(state, "Level:GetRooms could not read the native Level state");
    }
    auto* handle = static_cast<LuaRuntime::RoomDescriptorListHandle*>(
        lua_newuserdata(state, sizeof(LuaRuntime::RoomDescriptorListHandle)));
    handle->level = reinterpret_cast<void*>(level);
    luaL_getmetatable(state, LuaRuntime::kRoomDescriptorListMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

} // namespace

std::size_t AttachLevelMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, "Level", kLevelHandlers, RowCount(kLevelHandlers));
}

// `RoomDescriptor` 的字段访问。**只服务已 confirmed 的字段**；其它一律 nil。
int RoomDescriptorIndex(lua_State* state) {
    auto* handle = static_cast<LuaRuntime::RoomDescriptorHandle*>(
        luaL_checkudata(state, 1, LuaRuntime::kRoomDescriptorMetatable));
    const char* key = lua_tostring(state, 2);
    if (handle == nullptr || key == nullptr) {
        lua_pushnil(state);
        return 1;
    }
    const std::uintptr_t descriptor = reinterpret_cast<std::uintptr_t>(handle->descriptor);
    std::uint32_t value = 0;
    if (std::strcmp(key, "GridIndex") == 0) {
        if (!ReadDescriptorU32(descriptor, kGridIndexOffset, &value)) {
            return luaL_error(state, "RoomDescriptor.GridIndex could not read the native state");
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return 1;
    }
    if (std::strcmp(key, "SafeGridIndex") == 0) {
        if (!ReadDescriptorU32(descriptor, kSafeGridIndexOffset, &value)) {
            return luaL_error(state, "RoomDescriptor.SafeGridIndex could not read the native state");
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return 1;
    }
    if (std::strcmp(key, "ListIndex") == 0) {
        if (!ReadDescriptorU32(descriptor, kListIndexOffset, &value)) {
            return luaL_error(state, "RoomDescriptor.ListIndex could not read the native state");
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return 1;
    }
    if (std::strcmp(key, "VisitedCount") == 0) {
        if (!ReadDescriptorU32(descriptor, kVisitedCountOffset, &value)) {
            return luaL_error(state, "RoomDescriptor.VisitedCount could not read the native state");
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return 1;
    }
    if (std::strcmp(key, "Clear") == 0) {
        if (!ReadDescriptorU32(descriptor, kClearOffset, &value)) {
            return luaL_error(state, "RoomDescriptor.Clear could not read the native state");
        }
        // 真机验证过：清房瞬间该字段 0→1；起始房间本来就是 1（没有敌人）。
        lua_pushboolean(state, value != 0 ? 1 : 0);
        return 1;
    }
    if (std::strcmp(key, "Data") == 0) {
        // PC 的 `Data` 是房间配置对象；EID 只读它的 `Type`（布局表 confirmed 的 `Data+0x8`）。
        // 未确认的成员（例如 `Variant`）这里**不编值**，返回 nil 由调用方自行判断。
        std::uintptr_t data = 0;
        if (!IsEngineMemoryReadable(descriptor + kDataOffset, sizeof(std::uintptr_t))) {
            return luaL_error(state, "RoomDescriptor.Data could not read the native state");
        }
        std::memcpy(&data, reinterpret_cast<const void*>(descriptor + kDataOffset), sizeof(data));
        if (data == 0) {
            lua_pushnil(state);
            return 1;
        }
        lua_createtable(state, 0, 1);
        std::uint32_t roomType = 0;
        if (IsEngineMemoryReadable(data + kRoomConfigTypeOffset, sizeof(std::uint32_t))) {
            std::memcpy(&roomType, reinterpret_cast<const void*>(data + kRoomConfigTypeOffset),
                        sizeof(roomType));
            lua_pushinteger(state, static_cast<lua_Integer>(roomType));
            lua_setfield(state, -2, "Type");
        }
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

// `RoomDescriptorList.__index`：`Size` 现读（房间会随探索增加），没有别的方法落在这里。
int RoomDescriptorListIndex(lua_State* state) {
    auto* handle = static_cast<LuaRuntime::RoomDescriptorListHandle*>(
        luaL_checkudata(state, 1, LuaRuntime::kRoomDescriptorListMetatable));
    const char* key = lua_tostring(state, 2);
    if (handle == nullptr || key == nullptr) {
        lua_pushnil(state);
        return 1;
    }
    if (std::strcmp(key, "Size") == 0) {
        lua_pushinteger(state, static_cast<lua_Integer>(
            DescriptorCount(reinterpret_cast<std::uintptr_t>(handle->level))));
        return 1;
    }
    // ★ 必须从**元表**取方法表：用 `lua_getfield(state, 1, ...)` 去索引 userdata 会再次
    // 触发 `__index`（就是本函数），直接 C 栈溢出 —— 宿主机上实测到 `C stack overflow`。
    luaL_getmetatable(state, LuaRuntime::kRoomDescriptorListMetatable);
    lua_getfield(state, -1, "__methods");
    if (!lua_istable(state, -1)) {
        lua_pop(state, 2);
        lua_pushnil(state);
        return 1;
    }
    lua_getfield(state, -1, key);
    lua_remove(state, -2);   // 弹掉 __methods
    lua_remove(state, -2);   // 弹掉元表
    return 1;
}

// `rooms:Get(i)`：EID 的用法是 `for i = 0, rooms.Size - 1 do local room = rooms:Get(i)`。
// 快路径直接取第 i 个槽位；它的 `+0x08`（ListIndex）不等于 i 时才退化为整表扫描
// （槽位顺序与 ListIndex 顺序在真机上一致，但这里不把"一致"当永久保证）。
std::size_t AttachRoomDescriptorListMethods(lua_State* state) noexcept {
    static constexpr NamedHandler kMethods[] = {
        {"Get", &RoomDescriptorListGet},
    };
    std::size_t attached = 0;
    for (const NamedHandler& method : kMethods) {
        lua_pushcfunction(state, method.handler);
        lua_setfield(state, -2, method.name);
        ++attached;
    }
    return attached;
}

std::size_t AttachRoomMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, "Room", kRoomHandlers, RowCount(kRoomHandlers));
}

std::size_t AttachItemPoolMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, "ItemPool", kItemPoolHandlers, RowCount(kItemPoolHandlers));
}

// --- `Level:GetCurses()` 的探针（2026-09-12，动作三）-------------------------------
//
// 探针要一次性回答"EID 的 `hasCurseBlind()` 为什么可能误判"：
//   * `rawCurses`      —— `Level+0xC` 的原始位（也就是我们现在交给 Mod 的那个值）；
//   * `permanentCurses`/`bannedCurses` —— 引擎自己的两个 `Game` 方法的返回值
//     （PC 的完整语义是 `raw | permanent` 再 `& banned`，我们此前**一步都没做**）；
//   * `synthesisFlags` —— bit0 原始位读到了、bit1 permanent 调用成功、bit2 banned 调用成功。
//
// 为什么必须在真机上读、而不能靠反汇编推断：`Level:GetCurses()` 的**地址链**没有疑点
// （反汇编已逐条核对），但"这台机器的这一层到底有没有中盲眼诅咒"只有真机读数能说清；
// 而 `EID:hasCurseBlind()` 的真假正是"隐瞒分支"四条成因之一。
GameCursesProbe GameCursesProbeSnapshot() noexcept {
    GameCursesProbe view{};
    view.raw = g_ProbeRawCurses.load(std::memory_order_relaxed);
    view.permanent = g_ProbePermanentCurses.load(std::memory_order_relaxed);
    view.banned = g_ProbeBannedCurses.load(std::memory_order_relaxed);
    view.flags = g_ProbeCursesFlags.load(std::memory_order_relaxed);
    view.reads = g_ProbeCursesReads.load(std::memory_order_relaxed);
    return view;
}


} // namespace isaac::runtime
