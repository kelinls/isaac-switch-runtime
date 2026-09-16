#include "interfaces/lua/input_api.hpp"

#include <atomic>

#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/owner_binding.hpp"

#include "lua_runtime_state.hpp"

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {

// 输入探针（2026-09-16，模组菜单那一轮）：菜单"按了没反应"时必须能分辨三种原因 ——
// 绑定根本没装上、动作号和手柄按键对不上、或者回调根本没跑。这三个量回答前两种：
//   * `g_InputActionTriggeredMask`：`IsActionTriggered` 回答过 `true` 的动作号（按位）；
//   * `g_InputActionQueriedMask`：被问过的动作号（按位）—— 用来确认"菜单确实在问"；
//   * `g_InputLastActionCode` / `g_InputLastActionResult`：最后一次问的号与结果。
// 它们是**我们自己的入口**的计数，与 `g_FontLoadCalls` 同一性质（真机可读、无副作用）。
std::atomic<std::uint32_t> g_InputActionTriggeredMask{0};
//: 非 0 号 controller（手柄）上"回答过是"的动作号。真机上"键位对不上"最常见的原因就是
//: controller 序号：PC 的 `Input.IsActionTriggered(动作, 0)` 里 0 通常指键盘，手柄是 1~4。
std::atomic<std::uint32_t> g_InputActionTriggeredMaskOther{0};
//: `IsActionPressed` 的同一张位图（按住而不是"刚按下"）——两条查询互为对照。
std::atomic<std::uint32_t> g_InputActionPressedMask{0};
//: 非 0 号 controller 上"按住"的动作号（与上面同一张位图的补充，用来确定手柄序号）。
std::atomic<std::uint32_t> g_InputActionPressedMaskOther{0};
std::atomic<std::uint32_t> g_InputActionQueriedMask{0};
std::atomic<std::uint32_t> g_InputLastActionCode{0xFFFFFFFFu};
std::atomic<std::uint32_t> g_InputLastActionResult{0};
std::atomic<std::uint32_t> g_InputLastController{0xFFFFFFFFu};
//: 让"按住/刚按下"返回 true 的那些 **controller 值**（按 `controller & 31` 记位）。
//: 真机结论（2026-09-16）：玩家自己的 `EntityPlayer.ControllerIndex`（那次是 33）才是引擎认的值，
//: 猜 0/1 永远拿不到"按下"。这两张掩码就是用来一次读数钉死"到底该传什么"。
std::atomic<std::uint32_t> g_InputTriggeredControllerMask{0};
std::atomic<std::uint32_t> g_InputPressedControllerMask{0};

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

// ★ 调用约定（2026-09-16 修正，这是"按键查询永远答没按下"的真因）：
//
// 绑定的那三个地址**不是**函数本体，而是游戏自己的**入口跳板**，逐字节读出来是：
//
//     adrp x0, <g_Manager> ; ldr x0, [x0, #0x768]   → x0 = Manager 单例
//     mov  x3, xzr                                  → x3 = 0（尾随的实体参数清零）
//     b    <真函数>                                 → 尾调用真函数
//
// 所以真函数的签名是 `(Manager*, u32 action, u32 controller, void* entity)`，
// 跳板只负责补 x0 与 x3，**动作号在 x1、手柄序号在 x2**。
//
// 照 PC 的形态直接 `query(action, controller)` 会把动作号塞进 x0（被跳板覆盖）、手柄序号塞进 x1
// （被当成动作号）、x2 留成垃圾 ⇒ 引擎每次都答"没按下"。
// 真机实测（`docs/问题与解决日志.md` 续三十六）：0~27 号动作 × controller 0/1 全问一遍、
// 玩家按遍所有键，回答"是"的一个都没有 —— 就是这条错的调用约定。
//
// 因此调用时必须给一个**占位的第一个参数**（跳板会覆盖它），把动作号与手柄序号放进 x1/x2。
using ActionQueryFn = bool (*)(void* /* 被跳板覆盖的 Manager* */, std::uint32_t, std::uint32_t);
using ActionValueFn = float (*)(void*, std::uint32_t, std::uint32_t);

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
    const auto query = reinterpret_cast<ActionQueryFn>(thunk);
    const auto code = static_cast<std::uint32_t>(lua_tointeger(state, 1));
    const auto controller = static_cast<std::uint32_t>(lua_tointeger(state, 2));
    const bool pressed = query(nullptr, code, controller);
    // 探针：位图按 32 位取模（`ButtonAction` 的号都小于 32），越界不影响判定。
    const std::uint32_t bit = 1u << (code & 31u);
    g_InputActionQueriedMask.fetch_or(bit, std::memory_order_relaxed);
    if (pressed) {
        if (controller == 0) {
            g_InputActionTriggeredMask.fetch_or(bit, std::memory_order_relaxed);
        } else {
            g_InputActionTriggeredMaskOther.fetch_or(bit, std::memory_order_relaxed);
        }
        g_InputTriggeredControllerMask.fetch_or(1u << (controller & 31u),
                                                std::memory_order_relaxed);
    }
    g_InputLastActionCode.store(code, std::memory_order_relaxed);
    g_InputLastActionResult.store(pressed ? 1u : 0u, std::memory_order_relaxed);
    g_InputLastController.store(controller, std::memory_order_relaxed);
    lua_pushboolean(state, pressed ? 1 : 0);
    return 1;
}

int InputIsActionPressed(lua_State* state) {
    RecordApiSequenceSecondary(32U);
    CheckInputQueryArguments(state, "Input.IsActionPressed");
    const std::uintptr_t thunk = LuaRuntime::InputIsActionPressedThunk();
    if (thunk == 0) {
        return luaL_error(state, "Input.IsActionPressed native binding is unavailable");
    }
    const auto query = reinterpret_cast<ActionQueryFn>(thunk);
    const auto code = static_cast<std::uint32_t>(lua_tointeger(state, 1));
    const bool held = query(nullptr, code,
                            static_cast<std::uint32_t>(lua_tointeger(state, 2)));
    if (held) {
        const auto controller = static_cast<std::uint32_t>(lua_tointeger(state, 2));
        if (controller == 0) {
            g_InputActionPressedMask.fetch_or(1u << (code & 31u), std::memory_order_relaxed);
        } else {
            g_InputActionPressedMaskOther.fetch_or(1u << (code & 31u),
                                                   std::memory_order_relaxed);
        }
        g_InputPressedControllerMask.fetch_or(1u << (controller & 31u),
                                              std::memory_order_relaxed);
        g_InputLastController.store(controller, std::memory_order_relaxed);
    }
    lua_pushboolean(state, held ? 1 : 0);
    return 1;
}

int InputGetActionValue(lua_State* state) {
    CheckInputQueryArguments(state, "Input.GetActionValue");
    const std::uintptr_t thunk = LuaRuntime::InputGetActionValueThunk();
    if (thunk == 0) {
        return luaL_error(state, "Input.GetActionValue native binding is unavailable");
    }
    // The PC API returns a float; the game's own function is the authority on the scale.
    const auto query = reinterpret_cast<ActionValueFn>(thunk);
    lua_pushnumber(state, static_cast<lua_Number>(
                              query(nullptr, static_cast<std::uint32_t>(lua_tointeger(state, 1)),
                                    static_cast<std::uint32_t>(lua_tointeger(state, 2)))));
    return 1;
}

} // namespace

std::size_t AttachInputMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kInputOwner, kInputHandlers,
                              sizeof(kInputHandlers) / sizeof(kInputHandlers[0]));
}

} // namespace isaac::runtime
