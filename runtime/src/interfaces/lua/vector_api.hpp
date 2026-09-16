#pragma once

#include <cstddef>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// `Vector` as a Lua value type.
//
// The audit (`analysis/stage150-sprite-abi/`) settles what PC's `Vector` maps onto: the engine's
// `KAGE::Math::Vector2` is **8 bytes** `{ float X; float Y; }` with no vtable (evidence: both
// constructors, `Length`'s `ldp`, the `Zero`/`One` constants eight bytes apart, two `Vector2`
// members of `Sprite` eight bytes apart, and a by-value ABI arriving in `s0..s3`). A Mod only ever
// builds one to pass a coordinate to `Sprite:Render` or to do arithmetic on, so the Runtime can
// own the value: no engine object is involved and nothing has to be published from the hook.
//
// The whole class is arithmetic on those two floats, which is why this batch can implement it
// without a single native call.
[[nodiscard]] std::size_t AttachVectorMethods(lua_State* state) noexcept;

// Lua constructor `Vector(x, y)`; both components default to 0 when omitted (PC allows
// `Vector()`), and a single argument sets X only.
int CreateVectorHandle(lua_State* state);

// `__index` for the metatable: `X`/`Y` fields, the documented constants' accessors, and the
// method table.
int VectorIndex(lua_State* state);

// `__newindex`: PC exposes `X`/`Y` as writable variables, and real Mods do write them
// (`pos.X = pos.X + dx`), so an assignment to any other key is an error rather than a silent
// no-op that would come back as a confusing read-back mismatch.
int VectorNewIndex(lua_State* state);

// Operators: `+`, `-` (both with a Vector), `*` (float or Vector, element-wise), `/` (float),
// unary `-`, `==`, and `__tostring` (`Vector(X,Y)`).
int VectorAdd(lua_State* state);
int VectorSub(lua_State* state);
int VectorMul(lua_State* state);
int VectorDiv(lua_State* state);
int VectorUnaryMinus(lua_State* state);
int VectorEqual(lua_State* state);
int VectorToString(lua_State* state);

// `Vector.Zero` / `Vector.One` / `Vector.FromAngle(degrees)` on the global table.
int VectorFromAngle(lua_State* state);

// `__index` of the global class table: `Vector.Zero` / `Vector.One` are produced fresh on every
// read (a shared userdata would let one Mod corrupt the constant for every other Mod), and
// `Vector.FromAngle` is the static constructor.
int VectorClassIndex(lua_State* state);

// 把一个 `Vector`（两个 float）压成 Lua 的 `Vector` userdata。**给字段读取用**：
// `Game.ScreenShakeOffset` / `Entity.PositionOffset` 这类字段在 PC 侧就是 `Vector` 对象，
// 返回一个真正的 Vector（而不是一张只有 X/Y 的表）才能让 Mod 继续用 `:Length()` 等方法。
//
// 名字带 `Lua` 前缀是为了与 TU 内部那个匿名命名空间的同名函数区分开（同名会让定义处
// 出现 "call is ambiguous"）。
void PushLuaVector(lua_State* state, float x, float y);

} // namespace isaac::runtime
