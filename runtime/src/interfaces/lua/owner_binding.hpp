#pragma once

#include "interfaces/lua/api_catalog.hpp"

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// One row of a family's `descriptor id -> handler` table. Families own their
// table next to the handlers they expose, so adding a method to a family means
// adding one catalog descriptor and one row here.
struct LuaHandlerBinding {
    std::uint32_t id;
    lua_CFunction handler;
};

// Attaches every catalog descriptor whose owner matches `owner` and whose id has
// a binding. Ids without a binding are skipped, so a catalog entry can never
// publish a null function, and the loop itself lives in one place instead of
// being copied into every family translation unit.
//
// Returns the number of attached methods.
[[nodiscard]] std::size_t AttachOwnerMethods(lua_State* state, const char* owner,
                                             const LuaHandlerBinding* bindings,
                                             std::size_t count) noexcept;

} // namespace isaac::runtime
