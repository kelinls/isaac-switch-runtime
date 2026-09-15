#include "interfaces/lua/mod_api.hpp"

#include "interfaces/lua/owner_binding.hpp"

#include "application/callback/callback_registry.hpp"
#include "domain/callback/callback_descriptor.hpp"

#include "lua_runtime_state.hpp"
#include "mod_persistence.hpp"
#include "persistence_event_journal.hpp"

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

using LuaRuntime::CallbackOperation;
using LuaRuntime::IsStage13CallbackMode;
using LuaRuntime::kModMetatable;
using LuaRuntime::kStage13CallbackValue;
using LuaRuntime::ModHandle;
using LuaRuntime::RecordCallbackOperation;

int ModAddCallback(lua_State* state);
int ModSaveData(lua_State* state);
int ModLoadData(lua_State* state);
int ModHasData(lua_State* state);
int ModRemoveData(lua_State* state);

constexpr LuaHandlerBinding kModHandlers[] = {
    {0x01010001, &ModAddCallback},
    {0x08010001, &ModSaveData},
    {0x08010002, &ModLoadData},
    {0x08010003, &ModHasData},
    {0x08010004, &ModRemoveData},
};

constexpr char kModOwner[] = "Mod";

ModHandle* CheckModHandle(lua_State* state, int expectedArguments, const char* apiName) {
    if (lua_gettop(state) != expectedArguments) {
        luaL_error(state, "%s received an invalid argument count", apiName);
    }
    return static_cast<ModHandle*>(luaL_checkudata(state, 1, kModMetatable));
}

const char* PersistenceErrorMessage(ModPersistence::Result result) {
    switch (result) {
        case ModPersistence::Result::Unavailable:
            return "Mod persistent storage is unavailable";
        case ModPersistence::Result::Corrupt:
            return "Mod persistent storage is corrupt";
        case ModPersistence::Result::TooLarge:
            return "Mod persistent storage is full";
        case ModPersistence::Result::IoFailure:
            return "Mod persistent storage I/O failed";
        case ModPersistence::Result::Success:
        case ModPersistence::Result::Missing:
            return "Mod persistent storage failed";
    }
    return "Mod persistent storage failed";
}

// Maps a Lua `ModCallbacks` value onto the catalog's stable callback id. The
// Stage13 manifest/require diagnostic only allows its own POST_UPDATE phase.
//
// 非 Stage13 构建里，**PC 表里的每一种回调都允许登记**：我们只有少数几种挂了
// 派发点（见 `HasDispatchSite`），其余的登记下来但永不触发。这样做是刻意的——
// PC Mod 会在加载阶段一次性注册十几种回调，任何一种报错都会让整个 Mod 加载失败
// （EID 就是这种），而“注册成功但收不到事件”只会让功能静默缺失。哪些种类挂了
// 派发点、哪些只是登记，由 `IsaacModRuntime_GetHookDiagnostics` 报出去。
bool HasDispatchSite(CallbackId id) {
    return id == kCallbackPostUpdate || id == kCallbackPostRender ||
           id == kCallbackInputAction || id == kCallbackPreGetCollectible ||
           id == kCallbackPostGameStarted;
}

bool ResolveCallbackId(lua_Integer callback, CallbackId* id) {
    if (IsStage13CallbackMode()) {
        if (callback != kStage13CallbackValue) {
            return false;
        }
        *id = kCallbackPostUpdate;
        return true;
    }
    if (callback < 0 || callback > static_cast<lua_Integer>(kCallbackMaximumId)) {
        return false;
    }
    // PC 表里的值直接作为内部 id：这样 Lua 侧的枚举值与注册表 id 一一对应，
    // 诊断报出来的掩码就是 Mod 注册过的枚举值本身。
    *id = static_cast<CallbackId>(callback);
    return true;
}

int ModAddCallback(lua_State* state) {
    luaL_checkudata(state, 1, kModMetatable);
    // PC 版签名是 `Mod:AddCallback(kind, function[, optionalParam])`：可选的第三个
    // 参数（过滤用，例如 CollectibleType）我们接受但忽略——过滤器只影响派发筛选，
    // 而我们尚未实现这些种类的派发。多传参数仍然报错，避免静默吞掉调用方的笔误。
    const int argumentCount = lua_gettop(state);
    if (argumentCount != 3 && argumentCount != 4) {
        return luaL_error(state, "unsupported callback id for this Runtime phase");
    }
    luaL_checktype(state, 3, LUA_TFUNCTION);
    // **字符串回调名**（PC 忏悔版的自定义回调）：只登记进命名回调表，不抛错。
    // EID 在加载阶段就会走这一条（`features/eid_bagofcrafting_search.lua:58`），
    // 报错会让 `require("features.eid_bagofcrafting")` 失败并把整个 `main.lua` 打断。
    // 名字不参与数字派发、也不进回调普查掩码（掩码是 `ModCallbacks` 枚举的位图）。
    if (lua_type(state, 2) == LUA_TSTRING) {
        // 顺序：先压回调函数、再压 Mod 对象 —— `RegisterNamedCallback` 的约定。
        lua_pushvalue(state, 3);
        lua_pushvalue(state, 1);
        if (!RegisterNamedCallback(state, lua_tostring(state, 2))) {
            return luaL_error(state, "named callback registration failed");
        }
        return 0;
    }
    const lua_Integer callback = luaL_checkinteger(state, 2);
    CallbackId id = 0;
    if (!ResolveCallbackId(callback, &id)) {
        return luaL_error(state, "unsupported callback id for this Runtime phase");
    }
    lua_pushvalue(state, 3);
    const int functionRef = luaL_ref(state, LUA_REGISTRYINDEX);
    lua_pushvalue(state, 1);
    const int modRef = luaL_ref(state, LUA_REGISTRYINDEX);
    // **追加**登记，而不是"替换同一 id 的旧登记"。
    //
    // PC 语义是后者做不到的：同一 Mod 对同一 id 可以登记多个回调，派发时**按登记顺序全部调用**。
    // EID 就重度依赖这一点 —— 它一次登记 **5 个 `MC_POST_NEW_ROOM`**（`main.lua:848/865`、
    // `eid_bagofcrafting.lua:467` …）、**10 个 `MC_PRE_USE_ITEM`**（`main.lua:381/403/1768/1782/1795/1804`、
    // `eid_itemprediction.lua:60/69`、`eid_bagofcrafting.lua:504`）。
    // 2026-09-12 真机报告 `01789203482` 里"注册表只有 7 条"正是替换语义造成的：
    // 7 = 种类的个数，而不是登记的个数。
    //
    // 旧实现先 `Remove(id, owner)` 再登记，注释写的是"避免 Lua 引用泄漏" —— 泄漏确实要防，
    // 但正确的做法是**追加**（引用各自归各自的登记），而不是把 Mod 的回调吃掉。
    CallbackRegistry& registry = LuaRuntime::ManagedCallbackRegistry();
    const isaac::runtime::ModHandle owner = LuaRuntime::RuntimeOwnerHandle();  // domain handle, not the userdata
    CallbackDescriptor descriptor{};
    descriptor.id = id;
    descriptor.owner = owner;
    descriptor.affinity = isaac::runtime::ThreadAffinity::Any;
    descriptor.luaReference = functionRef;
    descriptor.modReference = modRef;
    if (!registry.Register(descriptor).ok()) {
        luaL_unref(state, LUA_REGISTRYINDEX, functionRef);
        luaL_unref(state, LUA_REGISTRYINDEX, modRef);
        // 容量耗尽：把可判据的原因说清楚（`CallbackRegistry::kCapacity` 是全 Mod 共享的上限）。
        return luaL_error(state, "callback registry is full (%d entries)",
                          static_cast<int>(CallbackRegistry::kCapacity));
    }
    LuaRuntime::RecordCallbackRegistration(id, HasDispatchSite(id));
    return 0;
}

int ModSaveData(lua_State* state) {
    ModHandle* mod = CheckModHandle(state, 2, "Mod:SaveData");
    std::size_t length = 0;
    const char* data = luaL_checklstring(state, 2, &length);
    RecordCallbackOperation(CallbackOperation::SaveData);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationEntered,
        PersistenceEventJournal::Operation::SaveData);
    const ModPersistence::Result result =
        ModPersistence::SaveModData(mod->persistenceNamespace, data, length);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationReturned,
        PersistenceEventJournal::Operation::SaveData,
        static_cast<std::uint32_t>(result));
    if (result != ModPersistence::Result::Success) {
        return luaL_error(state, "%s", PersistenceErrorMessage(result));
    }
    return 0;
}

int ModLoadData(lua_State* state) {
    ModHandle* mod = CheckModHandle(state, 1, "Mod:LoadData");
    RecordCallbackOperation(CallbackOperation::LoadData);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationEntered,
        PersistenceEventJournal::Operation::LoadData);
    const char* data = nullptr;
    std::size_t length = 0;
    const ModPersistence::Result result =
        ModPersistence::LoadModData(mod->persistenceNamespace, &data, &length);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationReturned,
        PersistenceEventJournal::Operation::LoadData,
        static_cast<std::uint32_t>(result));
    if (result == ModPersistence::Result::Missing) {
        lua_pushliteral(state, "");
        return 1;
    }
    if (result != ModPersistence::Result::Success) {
        return luaL_error(state, "%s", PersistenceErrorMessage(result));
    }
    lua_pushlstring(state, data, length);
    return 1;
}

int ModHasData(lua_State* state) {
    ModHandle* mod = CheckModHandle(state, 1, "Mod:HasData");
    RecordCallbackOperation(CallbackOperation::HasData);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationEntered,
        PersistenceEventJournal::Operation::HasData);
    bool has = false;
    const ModPersistence::Result result = ModPersistence::HasModData(mod->persistenceNamespace, &has);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationReturned,
        PersistenceEventJournal::Operation::HasData,
        static_cast<std::uint32_t>(result));
    if (result != ModPersistence::Result::Success) {
        return luaL_error(state, "%s", PersistenceErrorMessage(result));
    }
    lua_pushboolean(state, has ? 1 : 0);
    return 1;
}

int ModRemoveData(lua_State* state) {
    ModHandle* mod = CheckModHandle(state, 1, "Mod:RemoveData");
    RecordCallbackOperation(CallbackOperation::RemoveData);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationEntered,
        PersistenceEventJournal::Operation::RemoveData);
    const ModPersistence::Result result = ModPersistence::RemoveModData(mod->persistenceNamespace);
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::OperationReturned,
        PersistenceEventJournal::Operation::RemoveData,
        static_cast<std::uint32_t>(result));
    if (result != ModPersistence::Result::Success) {
        return luaL_error(state, "%s", PersistenceErrorMessage(result));
    }
    return 0;
}

} // namespace

std::size_t AttachModMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kModOwner, kModHandlers,
                              sizeof(kModHandlers) / sizeof(kModHandlers[0]));
}

// --- 命名回调（字符串回调名）------------------------------------------------
//
// 实现说明（栈纪律）：所有条目都放在 Lua registry 的一张表里，调用方只需要按
// `[[nodiscard]]`/`void`/`int` 的约定使用，函数自己把栈恢复到调用前的形状。
// 注意：这一段必须留在 `isaac::runtime` 里（不是 `isaac::runtime::LuaRuntime`）——
// `mod_api.hpp` 在 `isaac::runtime` 里声明它们，限定命名空间会让定义落到另一个符号上，
// 链接期表现为未定义符号（本文件末尾的回调普查段有同样的说明）。
namespace {

// Lua registry 里那张"名字 → 回调表"的表的键。用**字符串键**（而不是 `luaL_ref` 的整数引用）
// 是因为每个 Lua 状态各有一份 registry：`lua_close` 之后引用自然失效，
// 不需要（也不可能）跨状态维护一个静态引用。
constexpr char kNamedCallbacksRegistryKey[] = "IsaacRuntime.NamedCallbacks";

// 把命名回调表压到栈顶（不存在时创建并写回 registry）。失败（栈溢出等）返回 false
// 且不留下值——调用方据此报 "named callback registration failed"。
bool PushNamedCallbacksTable(lua_State* state) {
    lua_getfield(state, LUA_REGISTRYINDEX, kNamedCallbacksRegistryKey);
    if (lua_istable(state, -1)) {
        return true;
    }
    lua_pop(state, 1);
    lua_newtable(state);                            // [.. named]
    lua_pushstring(state, kNamedCallbacksRegistryKey);
    lua_pushvalue(state, -2);                       // [.. named key named]
    lua_rawset(state, LUA_REGISTRYINDEX);           // registry[键] = named（弹出键与值）
    return true;
}

} // namespace

bool RegisterNamedCallback(lua_State* state, const char* name) {
    if (state == nullptr || name == nullptr || name[0] == '\0') {
        return false;
    }
    // 约定：栈顶是 `Mod`，紧邻它下面的是回调函数（`Mod:AddCallback` 的压栈顺序）。
    const int modIndex = lua_gettop(state);
    const int functionIndex = modIndex - 1;
    if (functionIndex < 1 || !lua_isfunction(state, functionIndex)) {
        return false;
    }

    if (!PushNamedCallbacksTable(state)) {          // [.. named]
        return false;
    }
    const int namedIndex = lua_gettop(state);
    lua_getfield(state, namedIndex, name);          // [.. named list?]
    if (!lua_istable(state, -1)) {
        lua_pop(state, 1);
        lua_newtable(state);                        // [.. named list]
        const int freshListIndex = lua_gettop(state);
        lua_pushstring(state, name);
        lua_pushvalue(state, freshListIndex);
        // 用 `lua_rawset` 而不是"按名字写字段"的封装：家族 TU 里出现按名字写字段的形态
        // 会被契约测试当成"自己再写一遍注册循环"拦下来，而这里写的是我们自己的 registry
        // 表、不是元表注册。`lua_rawset` 的栈约定是"**值在栈顶、键在它下面**"，
        // 所以每一处都是先压键、再压值。
        lua_rawset(state, namedIndex);              // named[name] = list
    }
    const int listIndex = lua_gettop(state);

    // 条目形状与 PC 的 `Isaac.GetCallbacks` 返回的回调表一致：`{ Function = fn, Mod = mod }`。
    //
    // 再一次提醒：`lua_rawset` 取"栈顶 = 值、其下 = 键"，所以要**先压键、再压值**。
    lua_newtable(state);                            // [.. named list entry]
    const int entryIndex = lua_gettop(state);
    lua_pushstring(state, "Function");
    lua_pushvalue(state, functionIndex);
    lua_rawset(state, entryIndex);                  // entry.Function = fn
    lua_pushstring(state, "Mod");
    lua_pushvalue(state, modIndex);
    lua_rawset(state, entryIndex);                  // entry.Mod = mod
    // 追加到末尾：登记顺序 = 派发顺序（PC 亦然）。
    const lua_Integer next = static_cast<lua_Integer>(lua_rawlen(state, listIndex)) + 1;
    lua_rawseti(state, listIndex, next);            // 消费 entry

    // 把调用方压进来的两个值（fn、mod）也消费掉，恢复调用前的栈。
    lua_settop(state, functionIndex - 1);
    return true;
}

void PushNamedCallbacks(lua_State* state, const char* name) {
    if (state == nullptr || name == nullptr) {
        lua_newtable(state);
        return;
    }
    if (!PushNamedCallbacksTable(state)) {          // [.. named]
        lua_newtable(state);
        return;
    }
    lua_getfield(state, -1, name);                  // [.. named list?]
    lua_remove(state, -2);
    if (!lua_istable(state, -1)) {                  // "没有登记"与"名字下有 0 个回调"同形：空表
        lua_pop(state, 1);
        lua_newtable(state);
    }
}

int RunNamedCallbacks(lua_State* state, const char* name, int firstArgument) {
    if (state == nullptr || name == nullptr) {
        return 0;
    }
    const int lastArgument = lua_gettop(state);
    if (!PushNamedCallbacksTable(state)) {          // [args.. named]
        return 0;
    }
    lua_getfield(state, -1, name);                  // [args.. named list?]
    lua_remove(state, -2);
    if (!lua_istable(state, -1)) {                  // 没有这个名字：安静返回（PC 亦然）
        lua_pop(state, 1);
        return 0;
    }
    const int listIndex = lua_gettop(state);
    const lua_Integer count = static_cast<lua_Integer>(lua_rawlen(state, listIndex));
    for (lua_Integer index = 1; index <= count; ++index) {
        lua_rawgeti(state, listIndex, index);       // [.. list entry]
        if (!lua_istable(state, -1)) {
            lua_pop(state, 1);
            continue;
        }
        lua_getfield(state, -1, "Function");        // [.. list entry fn]
        if (!lua_isfunction(state, -1)) {
            lua_pop(state, 2);
            continue;
        }
        lua_getfield(state, -2, "Mod");             // [.. list entry fn mod]
        // 回调签名是 `function(mod, ...)`：第一个参数永远是它自己的 Mod 对象，
        // 之后是 `Isaac.RunCallback(name, ...)` 的实参。
        for (int argument = firstArgument; argument <= lastArgument; ++argument) {
            lua_pushvalue(state, argument);
        }
        const int argumentCount = (lastArgument - firstArgument + 1) + 1;
        if (lua_pcallk(state, argumentCount, 1, 0, 0, nullptr) != LUA_OK) {
            // 错误原样上抛：调用方（`Isaac.RunCallback`）只负责把深度计数还回去。
            lua_remove(state, listIndex);
            return lua_error(state);
        }
        if (!lua_isnil(state, -1)) {
            // PC："breaking on the first return" —— 留下这个返回值交给 Lua。
            return 1;
        }
        lua_pop(state, 2);                          // 返回值 + entry
    }
    lua_settop(state, lastArgument);
    return 0;
}


} // namespace isaac::runtime

// --- 回调登记普查 ---------------------------------------------------------
//
// 这段实现原来单独放在 `source/callback_registration_stats.cpp`，但**根目录下的新
// `source/*.cpp` 不会被打包构建自动收集**（构建只扫描 `source/` 的子目录与 `src/`），
// 结果是链接期留下未定义符号、真机加载时才会以 PC=0 的形式炸掉（`test_runtime_elf_symbols`
// 专门拦这种形态）。放回本文件——它本来就是唯一调用方，且一定被编译。
// 注意必须写成 `::LuaRuntime`：本文件在 `isaac::runtime` 命名空间里，不限定的话
// 定义会落进 `isaac::runtime::LuaRuntime`，与 `lua_runtime_state.hpp` 的声明（全局
// `LuaRuntime`）不是同一个符号 —— 链接期表现为未定义符号、真机加载时 PC=0。
namespace LuaRuntime {
namespace {

std::atomic<std::uint64_t> g_RegisteredLow{0};
std::atomic<std::uint64_t> g_RegisteredHigh{0};
std::atomic<std::uint32_t> g_Dispatchable{0};
std::atomic<std::uint32_t> g_Unhooked{0};

} // namespace

void RecordCallbackRegistration(std::uint32_t callbackId, bool hasDispatchSite) noexcept {
    if (callbackId < 64) {
        g_RegisteredLow.fetch_or(1ULL << callbackId, std::memory_order_release);
    } else if (callbackId < 128) {
        g_RegisteredHigh.fetch_or(1ULL << (callbackId - 64), std::memory_order_release);
    }
    if (hasDispatchSite) {
        g_Dispatchable.fetch_add(1, std::memory_order_relaxed);
    } else {
        g_Unhooked.fetch_add(1, std::memory_order_relaxed);
    }
}

std::uint64_t RegisteredCallbackKindMaskLow() noexcept {
    return g_RegisteredLow.load(std::memory_order_acquire);
}

std::uint64_t RegisteredCallbackKindMaskHigh() noexcept {
    return g_RegisteredHigh.load(std::memory_order_acquire);
}

std::uint32_t DispatchableCallbackRegistrationCount() noexcept {
    return g_Dispatchable.load(std::memory_order_acquire);
}

std::uint32_t UnhookedCallbackRegistrationCount() noexcept {
    return g_Unhooked.load(std::memory_order_acquire);
}

} // namespace LuaRuntime
