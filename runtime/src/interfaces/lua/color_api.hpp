#pragma once

#include <cstddef>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// `KColor` as a Lua value type.
//
// PC Mods build colours inline, for example
// `font:DrawString("text", 0, 0, KColor(1, 0, 0, 1))`, so the render-facing APIs need a
// colour the Mod can construct. The engine's `KAGE::Graphics::Color` is a 16-byte RGBA
// aggregate and the audit shows `Font::DrawString` receives it through an invisible
// reference, so a Runtime-owned value type is enough: no engine object is involved and
// nothing has to be published from the hook beyond the draw entry itself.
//
// Returns how many methods were attached (none yet: `KColor` is read through its fields).
[[nodiscard]] std::size_t AttachColorMethods(lua_State* state) noexcept;

// Lua constructor `KColor(red, green, blue, alpha)`; alpha defaults to 1 when omitted.
int CreateColorHandle(lua_State* state);

// `__index` for the metatable: exposes R/G/B/A as numbers.
int ColorIndex(lua_State* state);

// `__newindex` for the same four fields. PC Mods do write them -- External Item Descriptions'
// Rainbow/Blink/Fade effects assign `color.Alpha` and replace the colour through a function --
// so a read-only metatable would turn working PC Mods into Lua errors.
int ColorNewIndex(lua_State* state);

// `KColor` 类表的 `__index`：文档化的 `KColor.Black/Red/Green/Blue/Yellow/Cyan/Magenta/White/
// Transparent` 常量（每次读取产出新值）。
int KColorClassIndex(lua_State* state);

} // namespace isaac::runtime
