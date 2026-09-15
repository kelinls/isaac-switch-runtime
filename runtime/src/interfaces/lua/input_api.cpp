#include "interfaces/lua/input_api.hpp"

#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/owner_binding.hpp"

#include "lua_runtime_state.hpp"

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

int InputIsButtonTriggered(lua_State* state);
int InputIsButtonPressed(lua_State* state);
int InputGetButtonValue(lua_State* state);
int InputIsActionTriggered(lua_State* state);
int InputIsActionPressed(lua_State* state);
int InputGetActionValue(lua_State* state);

constexpr LuaHandlerBinding kInputHandlers[] = {
    {0x09010001, &InputIsButtonTriggered},
    {0x09010002, &InputIsButtonPressed},
    {0x09010003, &InputGetButtonValue},
    {0x09010004, &InputIsActionTriggered},
    {0x09010005, &InputIsActionPressed},
    {0x09010006, &InputGetActionValue},
};

constexpr char kInputOwner[] = "Input";

// Every Input method takes `(input code, controllerId)`.
void CheckInputQueryArguments(lua_State* state, const char* apiName) {
    if (lua_gettop(state) != 2 || !lua_isinteger(state, 1) || !lua_isinteger(state, 2)) {
        luaL_error(state, "%s accepts input code and controllerId integers", apiName);
    }
}

// The button query functions have no entry point the Runtime can call yet: the game has no
// `Manager::IsButton*` trampoline, and the `KAGE::Input::ManagerBase::IsButton*` variants need
// the KAGE input manager instance, which the Runtime has not located. Those handlers keep
// validating their arguments and answer "not pressed" so a Mod using them runs instead of
// erroring. The three action queries below are bound to the game's own `Manager::` trampolines,
// which load the Manager singleton themselves and zero the trailing `Entity*` argument, so
// they take exactly the two arguments the PC API takes.

int InputIsButtonTriggered(lua_State* state) {
    CheckInputQueryArguments(state, "Input.IsButtonTriggered");
    lua_pushboolean(state, 0);
    return 1;
}

int InputIsButtonPressed(lua_State* state) {
    CheckInputQueryArguments(state, "Input.IsButtonPressed");
    lua_pushboolean(state, 0);
    return 1;
}

int InputGetButtonValue(lua_State* state) {
    CheckInputQueryArguments(state, "Input.GetButtonValue");
    lua_pushnumber(state, 0.0);
    return 1;
}

int InputIsActionTriggered(lua_State* state) {
    CheckInputQueryArguments(state, "Input.IsActionTriggered");
    const std::uintptr_t thunk = LuaRuntime::InputIsActionTriggeredThunk();
    if (thunk == 0) {
        return luaL_error(state, "Input.IsActionTriggered native binding is unavailable");
    }
    using Query = bool (*)(std::uint32_t, std::uint32_t);
    const auto query = reinterpret_cast<Query>(thunk);
    lua_pushboolean(state, query(static_cast<std::uint32_t>(lua_tointeger(state, 1)),
                                 static_cast<std::uint32_t>(lua_tointeger(state, 2)))
                                ? 1
                                : 0);
    return 1;
}

int InputIsActionPressed(lua_State* state) {
    RecordApiSequenceSecondary(32U);
    CheckInputQueryArguments(state, "Input.IsActionPressed");
    const std::uintptr_t thunk = LuaRuntime::InputIsActionPressedThunk();
    if (thunk == 0) {
        return luaL_error(state, "Input.IsActionPressed native binding is unavailable");
    }
    using Query = bool (*)(std::uint32_t, std::uint32_t);
    const auto query = reinterpret_cast<Query>(thunk);
    lua_pushboolean(state, query(static_cast<std::uint32_t>(lua_tointeger(state, 1)),
                                 static_cast<std::uint32_t>(lua_tointeger(state, 2)))
                                ? 1
                                : 0);
    return 1;
}

int InputGetActionValue(lua_State* state) {
    CheckInputQueryArguments(state, "Input.GetActionValue");
    const std::uintptr_t thunk = LuaRuntime::InputGetActionValueThunk();
    if (thunk == 0) {
        return luaL_error(state, "Input.GetActionValue native binding is unavailable");
    }
    // The PC API returns a float; the game's own function is the authority on the scale.
    using Query = float (*)(std::uint32_t, std::uint32_t);
    const auto query = reinterpret_cast<Query>(thunk);
    lua_pushnumber(state, static_cast<lua_Number>(
                              query(static_cast<std::uint32_t>(lua_tointeger(state, 1)),
                                    static_cast<std::uint32_t>(lua_tointeger(state, 2)))));
    return 1;
}

} // namespace

std::size_t AttachInputMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kInputOwner, kInputHandlers,
                              sizeof(kInputHandlers) / sizeof(kInputHandlers[0]));
}

} // namespace isaac::runtime
