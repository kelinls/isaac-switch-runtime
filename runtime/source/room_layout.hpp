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

// 房间渲染的滚动偏移向量（PC 侧 Room:GetRenderScrollOffset()）。世界坐标转屏幕坐标时先加它、再加 Game 的渲染位置（confirmed）
//   证据：0x0048937c —— 该函数把 Room + 0x1938 的地址传给 Vector2::operator+（前一条指令 mov w8, #0x1938 给出偏移）⇒ 这个字段就是房间的渲染滚动偏移；紧随其后又加上 Game + 0x24F9B0 的向量才得到屏幕坐标
inline constexpr std::uintptr_t kRoomRenderScrollOffsetOffset = 0x1938;
inline constexpr std::uintptr_t kRoomRenderScrollOffsetWidth = 16;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：RenderScrollOffset

} // namespace isaac::runtime::layout
