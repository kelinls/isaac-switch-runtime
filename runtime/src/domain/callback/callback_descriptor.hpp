#pragma once

#include "domain/mod/mod_handle.hpp"
#include "domain/runtime/thread_affinity.hpp"

#include <cstdint>

namespace isaac::runtime {

// ModCallbacks identifier (MC_POST_UPDATE, MC_POST_RENDER, ...). Kept as a
// plain integer so the domain layer does not depend on the Lua API catalogue.
using CallbackId = std::uint32_t;

// Values match the PC ModCallbacks table the Runtime exposes to Lua, so a Mod
// that stores or compares the number behaves like it does on PC. The table
// itself comes from `pc_lua_enum_data.cpp` (generated from the PC `enums.lua`).
//
// `MC_POST_UPDATE` used to be 0 here, which is the PC value of `MC_NPC_UPDATE`;
// 2026-09-12 修正为 1（同时把 Lua 侧的表改成生成表）。
inline constexpr CallbackId kCallbackPostUpdate = 1;
inline constexpr CallbackId kCallbackPostRender = 2;
inline constexpr CallbackId kCallbackInputAction = 13;
// 游戏开局：值必须等于 PC `ModCallbacks` 表里的 `MC_POST_GAME_STARTED`（15）。
inline constexpr CallbackId kCallbackPostGameStarted = 15;
inline constexpr CallbackId kCallbackPreGetCollectible = 62;
// 目前已挂钩派发点的回调种类上限（含未挂钩但允许登记的）：PC 表到 73。
inline constexpr CallbackId kCallbackMaximumId = 73;

// One registered callback. The Lua function is referenced by registry index,
// never by a raw lua_State pointer or C++ function pointer.
struct CallbackDescriptor {
    CallbackId id{0};
    ModHandle owner{};
    ThreadAffinity affinity{ThreadAffinity::ManagedCallback};
    // Lua registry reference of the callback function.
    int luaReference{0};
    // Lua registry reference of the Mod userdata passed as the first argument.
    int modReference{0};

    // MC_POST_UPDATE is 0, so the callback id itself is always a valid value;
    // validity is about having an owner and a Lua registry reference.
    [[nodiscard]] constexpr bool valid() const noexcept {
        return owner.valid() && luaReference != 0;
    }
};

} // namespace isaac::runtime
