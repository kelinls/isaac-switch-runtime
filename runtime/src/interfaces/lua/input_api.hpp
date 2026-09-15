#pragma once

#include <cstddef>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// Registers the `Input` owner methods (IsButtonTriggered / IsButtonPressed /
// GetButtonValue / IsActionTriggered / IsActionPressed / GetActionValue) from
// the API catalog.
//
// Slice 8 of the per-family split: the binding table and the handler bodies
// live here. The handlers are neutral stubs: they validate the argument shape
// and answer "not pressed" / `0.0` until the native input tables have evidence.
//
// Returns how many methods were attached.
[[nodiscard]] std::size_t AttachInputMethods(lua_State* state) noexcept;

} // namespace isaac::runtime
