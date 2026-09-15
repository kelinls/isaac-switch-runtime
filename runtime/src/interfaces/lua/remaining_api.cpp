#include "interfaces/lua/remaining_api.hpp"

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
int ItemPoolGetLastPool(lua_State* state);
int ItemPoolGetCollectible(lua_State* state);

constexpr LuaHandlerBinding kLevelHandlers[] = {
    {0x03010001, &LevelGetStage},
    {0x03010002, &LevelIsAscent},
    {0x03010003, &LevelGetCurses},
};

constexpr LuaHandlerBinding kRoomHandlers[] = {
    {0x04010001, &RoomGetType},
    {0x04010002, &RoomGetGridWidth},
    {0x04010003, &RoomGetGridHeight},
    {0x04010004, &RoomGetGridSize},
    {0x04010005, &RoomGetGridIndex},
    {0x04010006, &RoomGetGridPath},
    {0x04010007, &RoomGetGridEntity},
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

} // namespace

std::size_t AttachLevelMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, "Level", kLevelHandlers, RowCount(kLevelHandlers));
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
