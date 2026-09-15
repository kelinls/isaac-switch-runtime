#include "interfaces/lua/rng_api.hpp"

#include "interfaces/lua/owner_binding.hpp"

#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

// 句柄体就是"一份 RNG 对象快照"（16 字节：种子 + 移位三元组），必须与引擎里 `RNG` 的尺寸
// 一致 —— 否则 `RNG:SetSeed`/`Next` 打到引擎函数上的那块内存会越界（证据见
// `runtime_constants.hpp` 的 `kRngObjectSize` 注释）。
static_assert(sizeof(LuaRuntime::RngHandle::storage) == kRngObjectSize,
              "the RNG handle body must match the engine RNG object size");

using LuaRuntime::kRngMetatable;
using LuaRuntime::RngHandle;
using LuaRuntime::RngNextThunk;
using LuaRuntime::RngSetSeedThunk;
using LuaRuntime::ValidateRngNextBinding;
using LuaRuntime::ValidateRngSetSeedBinding;

int RngSetSeed(lua_State* state);
int RngNext(lua_State* state);
int RngGetSeed(lua_State* state);

constexpr LuaHandlerBinding kRngHandlers[] = {
    {0x07010001, &RngSetSeed},
    {0x07010002, &RngNext},
    {0x07010003, &RngGetSeed},
};

constexpr char kRngOwner[] = "RNG";

int RngSetSeed(lua_State* state) {
    auto* rng = static_cast<RngHandle*>(luaL_checkudata(state, 1, kRngMetatable));
    if (lua_gettop(state) != 3 || !lua_isinteger(state, 2) || !lua_isinteger(state, 3)) {
        return luaL_error(state, "RNG:SetSeed accepts seed and shift integers");
    }
    const lua_Integer seed = lua_tointegerx(state, 2, nullptr);
    const lua_Integer shift = lua_tointegerx(state, 3, nullptr);
    if (seed < 0 || static_cast<std::uint64_t>(seed) > std::numeric_limits<std::uint32_t>::max() ||
        shift < 0 || static_cast<std::uint64_t>(shift) > std::numeric_limits<std::uint32_t>::max()) {
        return luaL_error(state, "RNG:SetSeed arguments are outside uint32 range");
    }
    const std::uintptr_t method = RngSetSeedThunk();
    if (!ValidateRngSetSeedBinding(method)) {
        return luaL_error(state, "RNG:SetSeed native bindings are unavailable");
    }
    using SetSeedFn = void (*)(void*, std::uint32_t, std::uint32_t);
    reinterpret_cast<SetSeedFn>(method)(rng->storage.data(), static_cast<std::uint32_t>(seed),
                                        static_cast<std::uint32_t>(shift));
    return 0;
}

int RngNext(lua_State* state) {
    auto* rng = static_cast<RngHandle*>(luaL_checkudata(state, 1, kRngMetatable));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "RNG:Next accepts no arguments");
    }
    const std::uintptr_t method = RngNextThunk();
    if (!ValidateRngNextBinding(method)) {
        return luaL_error(state, "RNG:Next native bindings are unavailable");
    }
    using NextFn = void (*)(void*);
    reinterpret_cast<NextFn>(method)(rng->storage.data());
    std::uint32_t value = 0;
    std::memcpy(&value, rng->storage.data(), sizeof(value));
    lua_pushinteger(state, static_cast<lua_Integer>(value));
    return 1;
}

// `RNG:GetSeed()`（批次 13，2026-09-16）：PC 文档 `analysis/isaacdocs-snapshot/docs/RNG.md:56`
// —— "Returns the current seed of the RNG object"，返回类型 `int`。
//
// 为什么不需要引擎调用：RNG 的种子就是它自己的**头 4 字节**。证据在
// `RNG::SetSeed(uint seed, uint shift) @ 0x44E3C0`：第一条就是 `str w1, [x0]`（w1 即 seed 参数），
// 随后才写 `[x0+0x4..0xB]`（移位三元组）与 `[x0+0xC]`；`RNG::Next() @ 0x44E4E4` 也是原地更新
// `[x19]` 后把新值返回。所以本句柄里那 16 字节的**前半**就是引擎语义上的"当前种子"。
//
// 与 `SetSeed`/`Next` 不同，这条**不查** `InManagedCallbackScope()`：它只读本句柄自己的字节，
// 不碰任何引擎状态（PC 侧 `RNG():GetSeed()` 在脚本顶层同样是合法的）。
int RngGetSeed(lua_State* state) {
    auto* rng = static_cast<RngHandle*>(luaL_checkudata(state, 1, kRngMetatable));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "RNG:GetSeed accepts no arguments");
    }
    std::uint32_t seed = 0;
    std::memcpy(&seed, rng->storage.data(), sizeof(seed));
    lua_pushinteger(state, static_cast<lua_Integer>(seed));
    return 1;
}

} // namespace

std::size_t AttachRngMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kRngOwner, kRngHandlers,
                              sizeof(kRngHandlers) / sizeof(kRngHandlers[0]));
}

} // namespace isaac::runtime
