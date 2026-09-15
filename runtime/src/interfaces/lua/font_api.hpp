#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// The `Font` family (`KAGE::Graphics::Font`, PC Lua's `Font` type).
//
// This is the first family whose native object the Runtime owns. `~Font()` only releases the
// Font's internal buffers -- it never deletes `this` and never unregisters a global -- so the
// object's storage is allocated and released on this side (see `font_api.cpp`) and the
// `IsaacRuntime.Font` metatable carries a `__gc` that pairs `~Font()` with that release.
//
// `CreateFontHandle` builds one `Font` userdata; `RegisterFontApi` in the legacy translation
// unit installs the metatable and the global constructor, exactly like the other families.
int CreateFontHandle(lua_State* state);

// Destroy hook installed as the metatable's `__gc`. Idempotent: a second collection of the same
// userdata finds the handle already cleared and does nothing.
int DestroyFontHandle(lua_State* state);

// Registers the `Font` owner methods (Load / Unload / IsLoaded / GetStringWidth /
// GetStringWidthUTF8 / GetLineHeight / GetBaselineHeight / GetCharacterWidth /
// SetMissingCharacter) from the API catalog.
//
// Returns how many methods were attached.
[[nodiscard]] std::size_t AttachFontMethods(lua_State* state) noexcept;

// 把"最近一次 `Font:Load` 交给引擎的名字与结果"记成一个字，供探针读取（见 `.cpp` 注释）。
//
// 真机验证用：本轮的修复点就是这个名字，报告侧只需要看
// `bit1`（是不是内容挂载点相对名）与 `bits16..47`（名字前 4 字节，应为 `font`）。
void RecordFontLoadForProbe(std::string_view handedToEngine, bool accepted) noexcept;
[[nodiscard]] std::uint32_t LastFontLoadWordForProbe() noexcept;

// `Font:DrawString*` 的调用次数与累计文本字节数（探针读）。
//
// 它回答的问题：屏幕上没有描述文字，是"描述构建完成了但没交给引擎画"，还是
// "描述根本没构建出来"。两个计数都为 0 就说明 `EID:printDescription` 那一支没走到。
// 字体链的四个读数（探针用）：`Load`/`IsLoaded` 被调用过几次、最后一次结果如何，
// 以及绘制时用的颜色 alpha 与缩放 X（千分比，便于整数打包）。
//
// 为什么需要：真机报告 `01789214465` 的 API 序列显示 `Font.DrawString*` 四个变体一次都没被
// 调用，而 EID 的 `renderString`（`eid_api.lua:1637`）用的就是 `EID.font:DrawStringScaledUTF8`。
// 这四个读数用来区分"没走到绘制"与"画了但看不见"（alpha=0 / scale=0 / 字体未加载）。
struct FontChainProbe {
    std::uint32_t loadCalls{0};
    std::uint32_t loadResult{0};
    std::uint32_t isLoadedCalls{0};
    std::uint32_t isLoadedResult{0};
    std::uint32_t drawAlphaMilli{0};
    std::uint32_t drawScaleXMilli{0};
};

[[nodiscard]] FontChainProbe FontChainProbeSnapshot() noexcept;
[[nodiscard]] std::uint32_t FontDrawCallCountForProbe() noexcept;
[[nodiscard]] std::uint32_t FontDrawByteCountForProbe() noexcept;

// `Font:Load` 路径归一化的**可观测入口**（宿主验证通道专用）。
//
// 存在的理由：真机上一轮只能问一个问题，而"EID 把哪个字符串交给了引擎、归一化又把它改成了什么"
// 是两层信息。宿主 harness 只能看到引擎侧那一个名字（`FontLoadThunk` 的实现），归一化本身
// 是 `font_api.cpp` 里的内部函数 —— 不给出口就只能靠复刻一份实现去猜，而"复刻的实现"永远
// 不能证明"真实现的行为"。这个入口把**同一个**归一化实现暴露出来，宿主 harness 可以逐字比对
// （2026-09-12 就是靠它定位到折叠循环的 off-by-one）。
[[nodiscard]] std::string_view NormalizeModResourcePathForProbe(const char* path,
                                                              std::string& storage) noexcept;

} // namespace isaac::runtime
