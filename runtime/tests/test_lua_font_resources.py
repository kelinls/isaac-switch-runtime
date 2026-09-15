import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
FAMILY = ROOT / "runtime" / "src" / "interfaces" / "lua"


HARNESS = r'''
#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// The observation seams belong to other families; the Lua runtime links them, so the harness
// answers "unavailable" exactly like the other Lua runtime tests.
GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::ThunkUnavailable;
}
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) {
    return GameIsAscentObservation::MethodUnavailable;
}
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::OpenFailed;
}
}

// --- Fake `KAGE::Graphics::Font` ------------------------------------------------------------
//
// The Hook installation publishes the real entry points; the harness replaces them so the Lua
// script can assert what the handlers do *around* the native call: the object the Runtime
// allocated is the object the native constructor wrote, `Load` receives the second-argument NULL,
// every measurement passes its own arguments through, and the release path runs the destructor
// exactly once.
namespace {

constexpr std::uint32_t kFakeFontMagic = 0x4B414745;  // 'KAGE'

int g_ctorCalls = 0;
int g_dtorCalls = 0;
std::uint32_t g_missingCharacter = 0xFFFFu;

// Only the first bytes of the 0x20050-byte block are written: the point of the fake is the
// lifecycle, not the layout.
struct FakeFont {
    std::uint32_t magic;
    bool loaded;
    std::uint32_t missingCharacter;
};

void FakeCtor(void* font) {
    ++g_ctorCalls;
    auto* fake = static_cast<FakeFont*>(font);
    fake->magic = kFakeFontMagic;
    fake->loaded = false;
    fake->missingCharacter = 0xFFFFu;
}

void FakeDestructor(void* font) {
    ++g_dtorCalls;
    auto* fake = static_cast<FakeFont*>(font);
    if (fake->magic != kFakeFontMagic) {
        std::exit(4);
    }
    fake->loaded = false;
    fake->magic = 0;
}

bool FakeLoad(void* font, const char* path, const char* extension) {
    auto* fake = static_cast<FakeFont*>(font);
    if (fake->magic != kFakeFontMagic) {
        return false;
    }
    // The handler must forward the caller's second argument verbatim: NULL when the Mod
    // passed one argument (which is what all 32 in-module call sites pass), and the given
    // string when it passed one -- External Item Descriptions calls `font:Load(path, "")`.
    // Anything else is a value the native Load would interpret, so the fake rejects it.
    if (extension != nullptr && extension[0] != '\0') {
        fake->loaded = false;
        return false;
    }
    if (path == nullptr || path[0] == '\0') {
        fake->loaded = false;
        return false;
    }
    fake->loaded = true;
    return true;
}

void FakeUnload(void* font) {
    auto* fake = static_cast<FakeFont*>(font);
    if (fake->magic != kFakeFontMagic) {
        std::exit(5);
    }
    fake->loaded = false;
}

bool FakeIsLoaded(const void* font) {
    return static_cast<const FakeFont*>(font)->loaded;
}

int FakeGetStringWidth(const void* font, const char* text) {
    if (static_cast<const FakeFont*>(font)->magic != kFakeFontMagic) {
        std::exit(6);
    }
    return static_cast<int>(text == nullptr ? 0 : std::strlen(text)) * 10;
}

// Deliberately different from the single-byte measurement, so a swapped binding is visible.
int FakeGetStringWidthUTF8(const void* font, const char* text) {
    if (static_cast<const FakeFont*>(font)->magic != kFakeFontMagic) {
        std::exit(7);
    }
    return static_cast<int>(text == nullptr ? 0 : std::strlen(text)) * 4 + 1;
}

std::uint16_t FakeGetLineHeight(const void*) { return 42; }
std::uint16_t FakeGetBaselineHeight(const void*) { return 31; }

int FakeGetCharacterWidth(const void* font, char character) {
    if (static_cast<const FakeFont*>(font)->magic != kFakeFontMagic) {
        std::exit(8);
    }
    return 5 + static_cast<int>(character);
}

void FakeSetMissingCharacter(void* font, std::uint16_t character) {
    g_missingCharacter = character;
    static_cast<FakeFont*>(font)->missingCharacter = character;
}

// Records what the native entry received, so the test can assert the whole marshalling path
// (string, two floats, the Runtime-owned colour block, box width and the centre flag).
struct FakeDrawStringCall {
    char text[64];
    float x;
    float y;
    float scaleX;
    float scaleY;
    float red;
    float green;
    float blue;
    float alpha;
    int boxWidth;
    int center;
    int calls;
};
FakeDrawStringCall g_drawString{};

void FakeFontDrawString(void* font, const char* text, float x, float y, const void* color,
                        int boxWidth, bool center) {
    if (font == nullptr || color == nullptr) {
        std::exit(21);
    }
    const auto* rgba = static_cast<const float*>(color);
    std::snprintf(g_drawString.text, sizeof(g_drawString.text), "%s", text == nullptr ? "" : text);
    g_drawString.x = x;
    g_drawString.y = y;
    g_drawString.red = rgba[0];
    g_drawString.green = rgba[1];
    g_drawString.blue = rgba[2];
    g_drawString.alpha = rgba[3];
    g_drawString.boxWidth = boxWidth;
    g_drawString.center = center ? 1 : 0;
    ++g_drawString.calls;
}

// 三个绘制变体各记一份：缩放走 s2/s3、UTF8 变体走各自的入口，参数其余部分与 `DrawString`
// 完全一致。harness 逐项断言，证明"多出来的两个 float 与入口选择"都被如实传递。
FakeDrawStringCall g_drawStringScaled{};
FakeDrawStringCall g_drawStringUTF8{};
FakeDrawStringCall g_drawStringScaledUTF8{};

void FakeFontDrawStringScaled(void* font, const char* text, float x, float y, float scaleX,
                              float scaleY, const void* color, int boxWidth, bool center) {
    if (font == nullptr || color == nullptr) {
        std::exit(22);
    }
    const auto* rgba = static_cast<const float*>(color);
    std::snprintf(g_drawStringScaled.text, sizeof(g_drawStringScaled.text), "%s",
                  text == nullptr ? "" : text);
    g_drawStringScaled.x = x;
    g_drawStringScaled.y = y;
    g_drawStringScaled.scaleX = scaleX;
    g_drawStringScaled.scaleY = scaleY;
    g_drawStringScaled.red = rgba[0];
    g_drawStringScaled.green = rgba[1];
    g_drawStringScaled.blue = rgba[2];
    g_drawStringScaled.alpha = rgba[3];
    g_drawStringScaled.boxWidth = boxWidth;
    g_drawStringScaled.center = center ? 1 : 0;
    ++g_drawStringScaled.calls;
}

// 每个入口各记一份：不复用 `DrawString` 的记录，否则后调的变体会把前者断言用的字段覆盖掉
// （这正是第一版测试写错、被 `text=Bye` 断言当场抓住的地方）。
void RecordCall(FakeDrawStringCall* record, void* font, const char* text, float x, float y,
                float scaleX, float scaleY, const void* color, int boxWidth, bool center,
                int exitCode) {
    if (font == nullptr || color == nullptr) {
        std::exit(exitCode);
    }
    const auto* rgba = static_cast<const float*>(color);
    std::snprintf(record->text, sizeof(record->text), "%s", text == nullptr ? "" : text);
    record->x = x;
    record->y = y;
    record->scaleX = scaleX;
    record->scaleY = scaleY;
    record->red = rgba[0];
    record->green = rgba[1];
    record->blue = rgba[2];
    record->alpha = rgba[3];
    record->boxWidth = boxWidth;
    record->center = center ? 1 : 0;
    ++record->calls;
}

void FakeFontDrawStringUTF8(void* font, const char* text, float x, float y, const void* color,
                            int boxWidth, bool center) {
    RecordCall(&g_drawStringUTF8, font, text, x, y, 0.0f, 0.0f, color, boxWidth, center, 23);
}

void FakeFontDrawStringScaledUTF8(void* font, const char* text, float x, float y, float scaleX,
                                  float scaleY, const void* color, int boxWidth, bool center) {
    RecordCall(&g_drawStringScaledUTF8, font, text, x, y, scaleX, scaleY, color, boxWidth, center,
               24);
}

void PublishFakeBindings() {
    LuaRuntime::LuaFontBindings bindings{};
    bindings.ctor = reinterpret_cast<uintptr_t>(&FakeCtor);
    bindings.destructor_ = reinterpret_cast<uintptr_t>(&FakeDestructor);
    bindings.load = reinterpret_cast<uintptr_t>(&FakeLoad);
    bindings.unload = reinterpret_cast<uintptr_t>(&FakeUnload);
    bindings.isLoaded = reinterpret_cast<uintptr_t>(&FakeIsLoaded);
    bindings.getStringWidth = reinterpret_cast<uintptr_t>(&FakeGetStringWidth);
    bindings.getStringWidthUTF8 = reinterpret_cast<uintptr_t>(&FakeGetStringWidthUTF8);
    bindings.getLineHeight = reinterpret_cast<uintptr_t>(&FakeGetLineHeight);
    bindings.getBaselineHeight = reinterpret_cast<uintptr_t>(&FakeGetBaselineHeight);
    bindings.getCharacterWidth = reinterpret_cast<uintptr_t>(&FakeGetCharacterWidth);
    bindings.setMissingCharacter = reinterpret_cast<uintptr_t>(&FakeSetMissingCharacter);
    bindings.drawString = reinterpret_cast<uintptr_t>(&FakeFontDrawString);
    bindings.drawStringScaled = reinterpret_cast<uintptr_t>(&FakeFontDrawStringScaled);
    bindings.drawStringUTF8 = reinterpret_cast<uintptr_t>(&FakeFontDrawStringUTF8);
    bindings.drawStringScaledUTF8 = reinterpret_cast<uintptr_t>(&FakeFontDrawStringScaledUTF8);
    LuaRuntime::SetFontBindings(bindings);
}

const char* kUnavailableScript = R"lua(
local mod = RegisterMod('Font without bindings', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local ok, err = pcall(Font)
  if ok then error('Font() succeeded without native bindings') end
  if not string.find(tostring(err), 'unavailable', 1, true) then
    error('unexpected error: ' .. tostring(err))
  end
end)
)lua";

const char* kResourcesScript = R"lua(@RESOURCES_SCRIPT@)lua";
const char* kGcScript = R"lua(@GC_SCRIPT@)lua";
const char* kCallbackScript = R"lua(@CALLBACK_SCRIPT@)lua";
const char* kDrawScript = R"lua(@DRAW_SCRIPT@)lua";

} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* script = nullptr;
    bool bound = false;
    if (std::strcmp(argv[1], "unbound") == 0) {
        script = kUnavailableScript;
    } else if (std::strcmp(argv[1], "resources") == 0) {
        PublishFakeBindings();
        bound = true;
        script = kResourcesScript;
    } else if (std::strcmp(argv[1], "gc") == 0) {
        PublishFakeBindings();
        script = kGcScript;
    } else if (std::strcmp(argv[1], "callback-error") == 0) {
        script = kCallbackScript;
    } else if (std::strcmp(argv[1], "draw") == 0) {
        PublishFakeBindings();
        script = kDrawScript;
    } else {
        return 91;
    }
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@font.lua");
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    if (std::strcmp(argv[1], "callback-error") == 0) {
        const auto updatesBefore = LuaRuntime::RegisteredCallbackCount(1 /* MC_POST_UPDATE 的 PC 契约值 */);
        const auto rendersBefore = LuaRuntime::RegisteredCallbackCount(2);
        LuaRuntime::DispatchPostUpdate();
        const bool errored = LuaRuntime::TakeCallbackError();
        const auto updatesAfter = LuaRuntime::RegisteredCallbackCount(1 /* MC_POST_UPDATE 的 PC 契约值 */);
        const auto rendersAfter = LuaRuntime::RegisteredCallbackCount(2);
        std::printf("CB_BEFORE update=%u render=%u\n", updatesBefore, rendersBefore);
        std::printf("CB_AFTER update=%u render=%u errored=%d\n",
                    updatesAfter, rendersAfter, errored ? 1 : 0);
        return 0;
    }
    LuaRuntime::DispatchPostUpdate();
    if (LuaRuntime::TakeCallbackError()) return 3;
    if (std::strcmp(argv[1], "draw") == 0) {
        // 两次绘制：默认参数与显式参数；颜色必须按 16 字节 RGBA 原样送达原生入口。
        if (g_drawString.calls != 2) return 31;
        if (std::strcmp(g_drawString.text, "Bye") != 0) return 32;
        if (g_drawString.x != 1.0f || g_drawString.y != 2.0f) return 33;
        if (g_drawString.red != 1.0f || g_drawString.green != 0.0f ||
            g_drawString.blue != 0.0f) return 34;
        if (g_drawString.boxWidth != 40 || g_drawString.center != 1) return 35;
        // 三个变体：各被调用一次，文本/坐标/颜色一致，多出来的缩放落在 scaleX/scaleY 上。
        if (g_drawStringScaled.calls != 1) return 36;
        if (std::strcmp(g_drawStringScaled.text, "Scaled") != 0) return 37;
        if (g_drawStringScaled.x != 3.0f || g_drawStringScaled.y != 4.0f) return 38;
        if (g_drawStringScaled.scaleX != 0.5f || g_drawStringScaled.scaleY != 2.0f) return 39;
        if (g_drawStringScaled.blue != 1.0f || g_drawStringScaled.boxWidth != 7 ||
            g_drawStringScaled.center != 0) return 40;
        if (g_drawStringUTF8.calls != 1) return 41;
        if (std::strcmp(g_drawStringUTF8.text, "Utf8") != 0) return 42;
        if (g_drawStringUTF8.green != 1.0f) return 43;
        if (g_drawStringScaledUTF8.calls != 1) return 44;
        if (std::strcmp(g_drawStringScaledUTF8.text, "ScaledUtf8") != 0) return 45;
        if (g_drawStringScaledUTF8.scaleX != 1.5f || g_drawStringScaledUTF8.scaleY != 1.5f)
            return 46;
        if (g_drawStringScaledUTF8.center != 1) return 47;
        std::printf("DRAW_OK calls=%d text=%s x=%.2f y=%.2f rgba=%.2f,%.2f,%.2f box=%d center=%d "
                    "scaled=%.1f/%.1f utf8=%d scaledUtf8=%d\n",
                    g_drawString.calls, g_drawString.text, g_drawString.x, g_drawString.y,
                    g_drawString.red, g_drawString.green, g_drawString.blue,
                    g_drawString.boxWidth, g_drawString.center,
                    g_drawStringScaled.scaleX, g_drawStringScaled.scaleY,
                    g_drawStringUTF8.calls, g_drawStringScaledUTF8.calls);
    }
    if (bound) {
        // The native constructor ran on the block this module allocated, and the missing
        // character reached the native setter unchanged.
        if (g_ctorCalls < 1) return 10;
        if (g_missingCharacter != 63u) return 11;
    }
    if (std::strcmp(argv[1], "gc") == 0) {
        // Three dropped Fonts are collected exactly once each, and the manually released one is
        // destructed exactly once: the second `__gc` must not run the destructor again.
        if (g_ctorCalls != 4) return 12;
        if (g_dtorCalls != 4) return 13;
    }
    return 0;
}
'''


RESOURCES_SCRIPT = r'''
local mod = RegisterMod('Font resources', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  if type(Font) ~= 'function' then error('Font is not a global constructor') end

  -- 1) A Font can be constructed and its metatable carries every catalog method.
  local font = Font()
  if type(font) ~= 'userdata' then error('Font() did not return userdata') end
  local metatable = getmetatable(font)
  if type(metatable) ~= 'table' then error('Font has no metatable') end
  if type(metatable.__gc) ~= 'function' then error('Font metatable has no __gc') end
  if type(metatable.__index) ~= 'table' then error('Font metatable has no __index table') end
  for _, name in ipairs({'Load', 'Unload', 'IsLoaded', 'GetStringWidth', 'GetStringWidthUTF8',
                         'GetLineHeight', 'GetBaselineHeight', 'GetCharacterWidth',
                         'SetMissingCharacter'}) do
    if type(metatable.__index[name]) ~= 'function' then error('missing Font method ' .. name) end
    if type(font[name]) ~= 'function' then error('Font method is not reachable: ' .. name) end
  end

  -- 2) A fresh Font is not loaded and refuses every measurement instead of dispatching into
  --    fields that `Load` has not initialised yet.
  if font:IsLoaded() ~= false then error('a fresh Font reports IsLoaded') end
  for _, call in ipairs({
      function() return font:GetStringWidth('a') end,
      function() return font:GetStringWidthUTF8('a') end,
      function() return font:GetLineHeight() end,
      function() return font:GetBaselineHeight() end,
      function() return font:GetCharacterWidth(65) end,
    }) do
    if pcall(call) then error('an unloaded Font answered a measurement') end
  end

  -- 3) Load reports success and failure, and IsLoaded follows.
  if font:Load('font/terminus.fnt') ~= true then error('Load did not succeed') end
  -- Real Mods pass the native second argument as well: External Item Descriptions calls
  -- `font:Load(path, "")`. A handler that insisted on exactly one argument would reject it.
  if font:Load('font/terminus.fnt', '') ~= true then error('Load with an extension argument') end
  if pcall(function() font:Load('font/terminus.fnt', 7) end) then
    error('Load accepted a non-string extension')
  end
  if font:IsLoaded() ~= true then error('IsLoaded after Load') end
  if font:Load('') ~= false then error('Load accepted an empty path') end
  if font:IsLoaded() ~= false then error('IsLoaded after a failed Load') end
  if font:Load('font/terminus.fnt') ~= true then error('reload did not succeed') end

  -- 4) Every measurement passes its own arguments through, with a distinct value per entry point.
  if font:GetStringWidth('abcd') ~= 40 then error('GetStringWidth') end
  if font:GetStringWidth('') ~= 0 then error('GetStringWidth of the empty string') end
  if font:GetStringWidthUTF8('abcd') ~= 17 then error('GetStringWidthUTF8') end
  if font:GetLineHeight() ~= 42 then error('GetLineHeight') end
  if font:GetBaselineHeight() ~= 31 then error('GetBaselineHeight') end
  if font:GetCharacterWidth(65) ~= 70 then error('GetCharacterWidth') end

  -- 5) SetMissingCharacter validates its range and reaches the native setter.
  font:SetMissingCharacter(63)
  if pcall(function() font:SetMissingCharacter(-1) end) then error('negative missing character') end
  if pcall(function() font:SetMissingCharacter(70000) end) then error('missing character overflow') end
  if pcall(function() font:SetMissingCharacter(1.5) end) then error('fractional missing character') end
  if pcall(function() font:SetMissingCharacter() end) then error('missing character without an argument') end

  -- 6) Unload clears the loaded state and measurements are refused again.
  font:Unload()
  if font:IsLoaded() ~= false then error('IsLoaded after Unload') end
  if pcall(function() return font:GetStringWidth('a') end) then error('measured after Unload') end
  if pcall(function() return font:GetLineHeight() end) then error('line height after Unload') end

  -- 7) `__gc` is idempotent, and a released handle stays inert instead of calling through null.
  font:Load('font/terminus.fnt')
  metatable.__gc(font)
  metatable.__gc(font)
  if pcall(function() return font:GetStringWidth('a') end) then error('a released Font measured') end
  if pcall(function() font:Unload() end) then error('a released Font accepted Unload') end

  -- 8) The native `char` form of GetCharacterWidth sign-extends its argument, so only ASCII may
  --    reach it; non-ASCII needs the unicode overload this batch does not publish.
  local ascii = Font()
  if ascii:Load('font/terminus.fnt') ~= true then error('second Font did not load') end
  if pcall(function() return ascii:GetCharacterWidth(200) end) then error('non-ASCII width') end
  if pcall(function() return ascii:GetCharacterWidth(-1) end) then error('negative width') end
  if pcall(function() return ascii:GetCharacterWidth('A') end) then error('string width argument') end

  -- 9) Constructor and argument shape: `Font()` takes no argument, methods need their own object.
  if pcall(Font, 'font/terminus.fnt') then error('Font accepted an argument') end
  if pcall(function() return ascii:GetLineHeight(1) end) then error('GetLineHeight accepted an argument') end
  if pcall(function() return ascii:GetStringWidth({}) end) then error('GetStringWidth accepted a table') end
  if pcall(function() return ascii.Load({}, 'font/terminus.fnt') end) then error('method accepted a table') end
end)
'''


DRAW_SCRIPT = r'''
local mod = RegisterMod('Font draw', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  local font = Font()
  if not font:Load('font/eid_default.fnt') then error('Load failed') end
  local color = KColor(0.25, 0.5, 0.75, 1.0)
  -- PC 文档与 EID 实际使用的都是长名 Red/Green/Blue/Alpha；短名 R/G/B/A 属于另一个类 Color，
  -- 只作为别名接受。这里两种拼法都读一遍，任一读不出来就报错。
  if math.abs(color.Red - 0.25) > 0.001 then error('KColor.Red') end
  if math.abs(color.Green - 0.5) > 0.001 then error('KColor.Green') end
  if math.abs(color.Blue - 0.75) > 0.001 then error('KColor.Blue') end
  if math.abs(color.Alpha - 1.0) > 0.001 then error('KColor.Alpha') end
  if math.abs(color.R - 0.25) > 0.001 or math.abs(color.A - 1.0) > 0.001 then
    error('KColor short-name aliases')
  end
  -- 字段可写（EID 的彩虹/闪烁/淡出就是 `color.Alpha = ...`）
  color.Alpha = 0.5
  if math.abs(color.Alpha - 0.5) > 0.001 then error('KColor.Alpha must be writable') end
  if pcall(function() color.Alpha = 'x' end) then error('KColor.Alpha accepted a string') end
  if pcall(function() color.Nope = 1 end) then error('KColor accepted an unknown field') end
  -- 文档化的类常量，且每次读取都是新值（改常量不能影响别人）
  local white = KColor.White
  if math.abs(white.Red - 1) > 0.001 or math.abs(white.Alpha - 1) > 0.001 then
    error('KColor.White')
  end
  white.Alpha = 0
  if math.abs(KColor.White.Alpha - 1) > 0.001 then error('KColor.White must not be mutated') end
  local transparent = KColor.Transparent
  if math.abs(transparent.Alpha) > 0.001 then error('KColor.Transparent') end
  color.Alpha = 1.0
  -- 默认参数路径：BoxWidth=0、Center=false
  font:DrawString('Hi', 10.5, 20.25, color)
  -- 显式参数路径
  font:DrawString('Bye', 1.0, 2.0, KColor(1, 0, 0), 40, true)
  -- 三个绘制变体：缩放走 s2/s3，UTF8 走各自入口
  font:DrawStringScaled('Scaled', 3.0, 4.0, 0.5, 2.0, KColor(0, 0, 1), 7, false)
  font:DrawStringUTF8('Utf8', 5.0, 6.0, KColor(0, 1, 0))
  font:DrawStringScaledUTF8('ScaledUtf8', 7.0, 8.0, 1.5, 1.5, KColor(1, 1, 1), 0, true)
  -- 未 Load 的字体不得绘制
  local fresh = Font()
  local ok = pcall(function() fresh:DrawString('x', 0, 0, color) end)
  if ok then error('DrawString succeeded on an unloaded Font') end
  font:Unload()
end)
'''

CALLBACK_SCRIPT = r'''
local mod = RegisterMod('Callback probe', 1)
-- POST_UPDATE 回调**故意报错**：模拟"在游戏未就绪时调用 Game 相关 API"的 Mod。
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  error('update callback failed on purpose')
end)
-- POST_RENDER 只计数：它必须存活下来。
mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
  _G['CB_RENDER_RUNS'] = (_G['CB_RENDER_RUNS'] or 0) + 1
end)
'''

GC_SCRIPT = r'''
local mod = RegisterMod('Font collection', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function()
  -- Three objects that simply go out of scope: each must be finalized exactly once.
  for _ = 1, 3 do
    local dropped = Font()
    dropped:Load('font/terminus.fnt')
    if dropped:GetStringWidth('ab') ~= 20 then error('dropped Font measurement') end
  end
  collectgarbage('collect')
  collectgarbage('collect')

  -- One object released by hand, twice: the second release must not run the destructor again, and
  -- neither may the collector's own finalizer run for that object.
  local released = Font()
  released:Load('font/terminus.fnt')
  local metatable = getmetatable(released)
  metatable.__gc(released)
  metatable.__gc(released)
  collectgarbage('collect')
end)
'''


class LuaFontResourceTests(unittest.TestCase):
    # The offsets and 16-byte guards of `KAGE::Graphics::Font`, transcribed from the Stage148 ABI
    # audit (`analysis/stage148-font-abi/91C73FDD575061318D68886316AFEAC72388B2AB.json`). They are
    # hard-coded on purpose: the test must fail when a guard or an offset is edited, and it must not
    # depend on an analysis artifact that a given workspace may not carry.
    FONT_OFFSETS = {
        "kFontCtorOffset": 0x4CDE20,
        "kFontDestructorOffset": 0x4CE764,
        "kFontLoadOffset": 0x4CDEB8,
        "kFontUnloadOffset": 0x4CE768,
        "kFontIsLoadedOffset": 0x4CE898,
        "kFontGetStringWidthOffset": 0x4CE9BC,
        "kFontGetStringWidthUTF8Offset": 0x4CEE8C,
        "kFontGetLineHeightOffset": 0x4CEEE0,
        "kFontGetBaselineHeightOffset": 0x4CEEE8,
        "kFontGetCharacterWidthOffset": 0x4CE914,
        "kFontSetMissingCharacterOffset": 0x4CFB74,
    }
    FONT_GUARDS = {
        "kFontCtorExpectedBytes": "c80980524800a0721f0000391f4000b9",
        "kFontDestructorExpectedBytes": "f7b10614fd7bbca9f85f01a9fd030091",
        "kFontLoadExpectedBytes": "fd7bbaa9fc6f01a9fd030091fa6702a9",
        "kFontUnloadExpectedBytes": "fd7bbca9f85f01a9fd030091f65702a9",
        "kFontIsLoadedExpectedBytes": "00004039c0035fd6fd7bbca9f70b00f9",
        "kFontGetStringWidthExpectedBytes": "2c004039cc080034c90980524900a072",
        "kFontGetStringWidthUTF8ExpectedBytes": "fd7bbda9f50b00f9fd030091f44f02a9",
        "kFontGetLineHeightExpectedBytes": "00284079c0035fd6002c4079c0035fd6",
        "kFontGetBaselineHeightExpectedBytes": "002c4079c0035fd6ff4301d1e923016d",
        "kFontGetCharacterWidthExpectedBytes": "0884218b08a14079e9ff9f521f01096b",
        "kFontSetMissingCharacterExpectedBytes": "c80980524800a07201682878c0035fd6",
    }

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-font-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_font_harness.cpp"
        harness.write_text(
            HARNESS.replace("@RESOURCES_SCRIPT@", RESOURCES_SCRIPT)
            .replace("@GC_SCRIPT@", GC_SCRIPT)
            .replace("@CALLBACK_SCRIPT@", CALLBACK_SCRIPT)
            .replace("@DRAW_SCRIPT@", DRAW_SCRIPT)
            .lstrip()
        )
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "lua_font_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
             "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *map(str, lua_objects), "-o", str(cls.harness)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0,
                         f"scenario {scenario} exited with {result.returncode}\n"
                         + result.stdout + result.stderr)

    def test_font_lifecycle_and_measurements_match_the_pc_api(self):
        self.run_scenario("resources")

    def test_font_release_runs_the_destructor_exactly_once(self):
        self.run_scenario("gc")

    def test_font_draw_string_reaches_the_native_entry_with_its_colour(self):
        """`Font:DrawString` 必须把文本、坐标、16 字节颜色、boxWidth 与 center 原样送到原生入口。"""
        result = subprocess.run([str(self.harness), "draw"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DRAW_OK calls=2 text=Bye", result.stdout)
        self.assertIn("box=40 center=1", result.stdout)

    def test_failing_callback_is_preserved_without_taking_the_other_with_it(self):
        """一个回调报错只记录错误，不摘掉自己。

        POST_UPDATE 回调故意 error；它和另一个 POST_RENDER 回调都必须继续留在注册表里。
        PC 行为是错误可观察、后续派发继续执行；缺一个 API 不应让整个 Mod 永久静默。
        """
        result = subprocess.run([str(self.harness), "callback-error"], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CB_BEFORE update=1 render=1", result.stdout)
        self.assertIn("CB_AFTER update=1 render=1 errored=1", result.stdout)

    def test_font_without_native_bindings_reports_unavailable_and_still_loads_the_mod(self):
        self.run_scenario("unbound")

    def test_every_published_binding_has_a_guard_row(self):
        """`Lua*Bindings` 的每个字段都必须有一条守卫行 —— 对**每个**原生入口表家族都成立。

        漏一行的后果只有真机看得到：那个字段保持 0，对应 handler 抛
        "native binding is unavailable"，而宿主测试直接发布绑定、根本走不到 `Verify*Bindings`。
        2026-09-13 第八轮就是这样：`Font` 的三个绘制变体加了字段与 handler，却忘了加守卫行，
        现象是"屏幕上前三行画出来了，缩放那行起全都没了"（报告 `01789144065`，MARK=6）。
        """
        families = (
            ("LuaFontBindings", "kFontBindingEntries"),
            ("LuaSpriteBindings", "kSpriteBindingEntries"),
        )
        hooks = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        for struct_name, table_name in families:
            with self.subTest(family=struct_name):
                start_index = header.index(f"struct {struct_name} {{")
                body = header[start_index + len(struct_name) + len("struct  {"):]
                depth = 1
                end_index = 0
                for index, character in enumerate(body):
                    if character == "{":
                        depth += 1
                    elif character == "}":
                        depth -= 1
                        if depth == 0:
                            end_index = index
                            break
                fields = re.findall(r"uintptr_t (\w+)\{", body[:end_index])
                self.assertGreaterEqual(len(fields), 12, f"{struct_name} 的字段没解析出来")

                table_start = hooks.index(f"constexpr std::array<BindingGuardEntry, ")
                table_start = hooks.index(table_name)
                table = hooks[table_start:]
                table = table[:table.index("}};")]
                guarded = re.findall(rf"{struct_name}, (\w+)\)", table)
                self.assertEqual(
                    sorted(fields), sorted(guarded),
                    f"{table_name} 与 {struct_name} 字段不一致：缺 "
                    f"{sorted(set(fields) - set(guarded))}，多 {sorted(set(guarded) - set(fields))}",
                )

    def test_font_guards_and_offsets_match_the_stage148_audit(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        self.assertIn("inline constexpr std::size_t kFontObjectSize = 0x20050;", constants)
        for name, value in self.FONT_OFFSETS.items():
            with self.subTest(offset=name):
                self.assertIn(f"inline constexpr uintptr_t {name} = 0x{value:X};", constants)
        for name, guard in self.FONT_GUARDS.items():
            with self.subTest(guard=name):
                section = re.search(rf"{name} = \{{(.*?)\}};", constants, re.S).group(1)
                actual = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", section))
                self.assertEqual(actual.hex(), guard)
        # Every recorded guard is actually verified, and every verified row names a recorded guard:
        # a constant that nobody checks would silently publish an unproven address.
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        for name in self.FONT_OFFSETS:
            with self.subTest(verified_offset=name):
                self.assertIn(f"{name}, offsetof(", hook)
        for name in self.FONT_GUARDS:
            with self.subTest(verified_guard=name):
                self.assertIn(f"&{name}", hook)

    def test_font_family_owns_its_instance_storage_and_publishes_bindings_from_the_hook(self):
        family = (FAMILY / "font_api.cpp").read_text(encoding="utf-8")
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        # The handle owns the block, `__gc` pairs `~Font()` with the release, and the release path
        # is the only place the block is freed.
        self.assertIn("std::malloc(kFontObjectSize + kFontStorageSlack)", family)
        self.assertIn("std::free(raw)", family)
        self.assertIn("constexpr std::size_t kFontAlignment = 16;", family)
        self.assertIn("ReleaseFontStorage(handle->font);", family)
        self.assertIn("handle->font = nullptr;", family)
        self.assertIn('lua_setfield(state, -2, "__gc")', runtime)
        # A Font is never copied by value on this path, and the measurement handlers refuse to run
        # before a successful Load.
        self.assertNotIn("= *font", family)
        self.assertIn("int RequireLoadedFont(lua_State* state, void* font, const char* apiName)", family)
        # The Hook installation verifies each guard and publishes the record; the family is
        # optional, so a mismatch must not abort installation.
        self.assertIn(
            "bool VerifyFontBindings(const TargetModule& module, LuaRuntime::LuaFontBindings* bindings)",
            hook,
        )
        self.assertIn("LuaRuntime::SetFontBindings(fontBindings);", hook)
        self.assertIn("VerifyFontBindings(module, &fontBindings);", hook)


if __name__ == "__main__":
    unittest.main()
