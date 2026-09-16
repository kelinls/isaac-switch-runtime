#pragma once

// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/room.json` 生成，请勿手改。**
//
// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。
// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/room.json --verify`
//
// 口径（与表的 `confidence` 一致）：
//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；
//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；
//   structural —— 只说明结构，不声称字段名。

#include <cstdint>

namespace isaac::runtime::layout {

inline constexpr std::uint8_t kRoomBuildId[20] = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31, 0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7, 0x23, 0x88, 0xB2, 0xAB
};

// 进入房间那一刻的本局帧号：引擎自己的 `Room::GetFrameCount()` 返回的就是『`Game` 的本局帧计数 − 本字段』，所以它是『本房间是在第几帧开始的』；离开/重进房间时被写成当时的帧计数（于是 `GetFrameCount()` 归零）（confirmed）
//   证据：0x00470b2c —— 函数体内唯一一处 `this` 字段访问：`ldr w9, [x0, #0x191c]`，紧接着 0x470B30 `sub w0, w8, w9` 返回『本局帧计数 − 本字段』
//   证据：0x00470b20 —— 被减数的来源：`mov w9, #0xf99c` + 0x470B24 `movk w9, #0x24, lsl #16` = **0x24F99C**（`Game` 的本局帧计数，本仓库已有独立记录），再经 0x470B14/0x470B18 取 `g_Game` 槽、0x470B1C 解一层得到 `Game*` ⇒ 本字段是『进房间时的帧号』（不是帧数）。另：入口 0x470B0C 先 `ldrb w8, [x0]` + `cbz`，房间不活动时该函数直接返回 −1
inline constexpr std::uintptr_t kRoomEnterFrameCountOffset = 0x191C;
inline constexpr std::uintptr_t kRoomEnterFrameCountWidth = 4;

// 房间渲染的滚动偏移向量（PC 侧 Room:GetRenderScrollOffset()）。世界坐标转屏幕坐标时先加它、再加 Game 的渲染位置（confirmed）
//   证据：0x0048937c —— 该函数把 Room + 0x1938 的地址传给 Vector2::operator+（前一条指令 mov w8, #0x1938 给出偏移）⇒ 这个字段就是房间的渲染滚动偏移；紧随其后又加上 Game + 0x24F9B0 的向量才得到屏幕坐标
inline constexpr std::uintptr_t kRoomRenderScrollOffsetOffset = 0x1938;
inline constexpr std::uintptr_t kRoomRenderScrollOffsetWidth = 16;

// ★ 本行的 offset 是**函数入口偏移**（0x470B0C，符号 `_ZN15IsaacRepentance4Room13GetFrameCountEv`），不是结构体字段偏移 —— 它对应 `runtime_constants.hpp` 里的 `kRoomGetFrameCountOffset`（EID 的 `game:GetRoom():GetFrameCount()` 用它；缺它时背包合成那条渲染路径每帧抛错）。⚠️ confidence 取 candidate 的理由与其他行不同：**命名没有疑问**（符号名就是它），但『入口地址 = 0x470B0C』这一项本工具核不到（表机制核的是指令里的偏移/立即数），入口由符号表给出（`tools/nro_symbols.py` 读到同一个地址，宿主侧另有 16 字节入口指纹）⇒ 只登记、不进对外 API。取到的那个字段见本表 `EnterFrameCount` 行（candidate）
//   证据：0x00470b2c —— 函数体内的真实访存：`ldr w9, [x0, #0x191c]`（该字段的证据另见 `EnterFrameCount` 行）；入口 0x470B0C 起是 `ldrb w8, [x0]` / `cbz` / 取 `g_Game` 槽 / 相减 / `ret`，活跃路径一共 11 条指令，房间不活动时跳到 0x470B38 直接返回 −1
//   证据：0x00470b20 —— 该函数体内的取值常量 `0xf99c`（与 0x470B24 的 `movk #0x24` 合成 0x24F99C = `Game` 的帧计数）—— 它是『这条函数确实是 `Room::GetFrameCount`』的独立语义锚点（函数名 + 减数来源都指向同一件事）
inline constexpr std::uintptr_t kRoomGetFrameCountOffset = 0x470B0C;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：RenderScrollOffset、EnterFrameCount

} // namespace isaac::runtime::layout
