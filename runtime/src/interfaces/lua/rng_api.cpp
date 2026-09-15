#include "interfaces/lua/rng_api.hpp"

#include "interfaces/lua/owner_binding.hpp"

#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"

#include <cstdint>
#include <cstring>
#include <limits>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

using LuaRuntime::kRngMetatable;
using LuaRuntime::RngHandle;
using LuaRuntime::RngNextThunk;
using LuaRuntime::RngSetSeedThunk;
using LuaRuntime::ValidateRngNextBinding;
using LuaRuntime::ValidateRngSetSeedBinding;

int RngSetSeed(lua_State* state);
int RngNext(lua_State* state);

constexpr LuaHandlerBinding kRngHandlers[] = {
    {0x07010001, &RngSetSeed},
    {0x07010002, &RngNext},
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

} // namespace

std::size_t AttachRngMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kRngOwner, kRngHandlers,
                              sizeof(kRngHandlers) / sizeof(kRngHandlers[0]));
}

} // namespace isaac::runtime
