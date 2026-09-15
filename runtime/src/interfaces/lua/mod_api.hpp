#pragma once

#include <cstddef>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// Registers the `Mod` owner methods (AddCallback / SaveData / LoadData /
// HasData / RemoveData) from the API catalog.
//
// Slice 1 of the per-family split: the owner's binding table and registration
// loop live in this translation unit, while the handler bodies still live in
// the legacy `lua_runtime.cpp` and are reached through the bindings declared in
// `mod_handler_bindings.hpp`. Moving the bodies is a later slice.
//
// Returns how many methods were attached; unknown ids are skipped so a catalog
// entry can never attach a null function.
[[nodiscard]] std::size_t AttachModMethods(lua_State* state) noexcept;

// --- 命名回调（PC 的"自定义回调名"）--------------------------------------------
//
// 忏悔版的 `Mod:AddCallback` 除数字 `ModCallbacks` 之外还接受**字符串名**：
// 那是 Mod 自己的回调名（`Isaac.RunCallback("SOME_NAME", ...)` 触发）。EID 就用了它
// （`features/eid_bagofcrafting_search.lua:58` 的
// `EID:AddCallback("EIDCallbacks.SEARCH_NAME_CONVERSION", fn)`，而 `EID` 就是
// `RegisterMod` 返回的 Mod 对象，所以那一句直接走 `Mod:AddCallback`）。
//
// 名字形式的登记**不进** `CallbackRegistry`（那是数字 id 的注册表），而是进 Lua registry
// 里的一张按名字索引的表：`name -> { { Function = fn, Mod = <Mod 对象> }, ... }`，
// 条目形状与 PC 的 `Isaac.GetCallbacks(name)` 返回的"回调表"一致（EID 读
// `callbackData.Function` 与 `callbackData.Mod`）。表按 Lua registry 的字符串键存放，
// 所以每个 Lua 状态各有一份、`lua_close` 时自然消失，不存在跨状态悬垂引用。
//
// `RegisterNamedCallback` 从栈顶取两个值：先 `Function`（回调函数），再 `Mod`（Mod 对象），
// 两者都会被消费（`lua_pop`）。成功返回 true。
[[nodiscard]] bool RegisterNamedCallback(lua_State* state, const char* name);

// 把 `name` 名下的回调表压到栈顶（**永远是表**，没有登记时是空表）——`Isaac.GetCallbacks`。
void PushNamedCallbacks(lua_State* state, const char* name);

// 按登记顺序调用 `name` 名下的每个回调，第一个参数固定为它自己的 `Mod` 对象，
// 之后是栈上从 `firstArgument` 开始的实参（与 PC 的 `Mod:AddCallback` 回调签名一致）。
//
// PC 的 `Isaac.RunCallback` 是"跑到第一个返回值就停，并把那个值返回"（见
// `analysis/isaacdocs-snapshot/docs/Isaac.md:579`），所以这里同样在第一个**非 nil**
// 返回值处停下并把该值留在栈顶（返回 1）；一个回调都没有时返回 0（栈上不留值）。
// 回调里抛出的 Lua 错误原样向外抛（与数字路径一致）。
int RunNamedCallbacks(lua_State* state, const char* name, int firstArgument);

} // namespace isaac::runtime
