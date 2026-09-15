#include "interfaces/lua/music_api.hpp"

#include "interfaces/lua/owner_binding.hpp"

#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
#include "hook_manager.hpp"
#endif

#include <cstdint>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

using LuaRuntime::kMusicMetatable;
using LuaRuntime::MusicGetCurrentMusicIdThunk;
using LuaRuntime::MusicPauseThunk;
using LuaRuntime::MusicResumeThunk;
using LuaRuntime::ResolveMusicGetCurrentMusicId;
using LuaRuntime::ResolveMusicPause;
using LuaRuntime::ResolveMusicResume;

int MusicGetCurrentMusicId(lua_State* state);
int MusicPause(lua_State* state);
int MusicResume(lua_State* state);

constexpr LuaHandlerBinding kMusicHandlers[] = {
    {0x06010001, &MusicGetCurrentMusicId},
    {0x06010002, &MusicPause},
    {0x06010003, &MusicResume},
};

constexpr char kMusicOwner[] = "MusicManager";

// Resolving a Music call is two steps that must not be reordered: the method
// guard verdict first (the bound address must still hold the expected
// instructions), then the callback's Manager-derived Music object. Both steps
// live in the legacy translation unit; the handler only sees the verdict.
int MusicGetCurrentMusicId(lua_State* state) {
    luaL_checkudata(state, 1, kMusicMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Music:GetCurrentMusicID accepts no arguments");
    }
    const std::uintptr_t method = MusicGetCurrentMusicIdThunk();
    std::uintptr_t music = 0;
    if (!ResolveMusicGetCurrentMusicId(method, &music)) {
        return luaL_error(state, "Music:GetCurrentMusicID native bindings are unavailable");
    }
    using GetCurrentMusicIdFn = int (*)(const void*);
    lua_pushinteger(state, reinterpret_cast<GetCurrentMusicIdFn>(method)(reinterpret_cast<const void*>(music)));
    return 1;
}

int MusicPause(lua_State* state) {
    luaL_checkudata(state, 1, kMusicMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Music:Pause accepts no arguments");
    }
    const std::uintptr_t method = MusicPauseThunk();
    std::uintptr_t music = 0;
    if (!ResolveMusicPause(method, &music)) {
        return luaL_error(state, "Music:Pause native bindings are unavailable");
    }
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
    Stage48ArmMusicReplayProbe();
#endif
    using PauseFn = void (*)(void*);
    reinterpret_cast<PauseFn>(method)(reinterpret_cast<void*>(music));
    return 0;
}

int MusicResume(lua_State* state) {
    luaL_checkudata(state, 1, kMusicMetatable);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Music:Resume accepts no arguments");
    }
    const std::uintptr_t method = MusicResumeThunk();
    std::uintptr_t music = 0;
    if (!ResolveMusicResume(method, &music)) {
        return luaL_error(state, "Music:Resume native bindings are unavailable");
    }
    using ResumeFn = void (*)(void*);
    reinterpret_cast<ResumeFn>(method)(reinterpret_cast<void*>(music));
    return 0;
}

} // namespace

std::size_t AttachMusicMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kMusicOwner, kMusicHandlers,
                              sizeof(kMusicHandlers) / sizeof(kMusicHandlers[0]));
}

} // namespace isaac::runtime
