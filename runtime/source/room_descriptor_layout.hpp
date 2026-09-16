#pragma once

// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/room_descriptor.json` 生成，请勿手改。**
//
// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。
// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/room_descriptor.json --verify`
//
// 口径（与表的 `confidence` 一致）：
//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；
//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；
//   structural —— 只说明结构，不声称字段名。

#include <cstdint>

namespace isaac::runtime::layout {

inline constexpr std::uint8_t kRoomDescriptorBuildId[20] = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31, 0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7, 0x23, 0x88, 0xB2, 0xAB
};

// 房间在 13×13 网格里的下标（PC 侧 RoomDescriptor.GridIndex）（confirmed）
//   证据：真机读数 dist/room-probe-A3.json 的 slot7 +0x0 —— 起始房间：用户站在网格正中，Level+0x21558 读到 84，该槽位 +0x00 也是 84（Type=1）
//   证据：真机读数 dist/room-probe-B.json 的 slot3 +0x0 —— 走进宝箱房后索引变 97，该槽位 +0x00 也是 97（Type=4 宝箱房）
inline constexpr std::uintptr_t kRoomDescriptorGridIndexOffset = 0x0;
inline constexpr std::uintptr_t kRoomDescriptorGridIndexWidth = 4;

// 同上，但按 PC 语义是「在网格里就给下标、不在网格里给 -1」；**普通房间里与 GridIndex 相等**（confirmed）
//   证据：真机读数 dist/room-probe-A3.json 的 slot7 +0x4 —— 起始房间：与 GridIndex 相等
//   证据：真机读数 dist/room-probe-B.json 的 slot3 +0x4 —— 宝箱房：与 GridIndex 相等
inline constexpr std::uintptr_t kRoomDescriptorSafeGridIndexOffset = 0x4;
inline constexpr std::uintptr_t kRoomDescriptorSafeGridIndexWidth = 4;

// 房间类型（PC 侧 RoomDescriptor.Data.Type，枚举 RoomType）（confirmed，相对 Data）
//   证据：0x003d0e7c —— 解引用 Data 之后按 4 字节读 Data+0x8
//   证据：0x003d0e80 —— 紧接着与常量 4 比较；本项目自己的 RoomType 表里 ROOM_TREASURE = 4，而函数语义正是『能否转成红宝藏房』⇒ 该字段是房间类型
inline constexpr std::uintptr_t kRoomDescriptorDataTypeOffset = 0x8;
inline constexpr std::uintptr_t kRoomDescriptorDataTypeWidth = 4;

// 指向该房间的配置对象 RoomConfig::Room（PC 侧 RoomDescriptor.Data）（confirmed）
//   证据：0x003d0e78 —— 该函数只有一条字段访问：把 desc+0x10 当指针解引用（紧随其后就按 Data 内部字段做判断）
//   证据：0x003d669c —— 预计算可用门位时两次读取该指针（门的种类来自房间配置）
//   证据：0x003d0e80 —— 顺着 desc+0x10 这个指针读出的内部字段，立刻被拿去与常量 4（本项目 RoomType 表的 ROOM_TREASURE）比较 ⇒ 该指针指向的是房间配置对象（PC 侧 RoomDescriptor.Data），而不是普通计数
inline constexpr std::uintptr_t kRoomDescriptorDataOffset = 0x10;
inline constexpr std::uintptr_t kRoomDescriptorDataWidth = 8;

// 该房间被访问的次数（PC 侧 RoomDescriptor.VisitedCount）（confirmed）
//   证据：真机读数 dist/room-probe-C.json 的 slot8 +0x4c —— 第一次进入房间 71 ⇒ 0→1
//   证据：真机读数 dist/room-probe-D.json 的 slot7 +0x4c —— 回到起始房间 ⇒ 1→2（计数递增，不是布尔）
inline constexpr std::uintptr_t kRoomDescriptorVisitedCountOffset = 0x4C;
inline constexpr std::uintptr_t kRoomDescriptorVisitedCountWidth = 4;

// 房间是否已清（PC 侧 RoomDescriptor.Clear）（confirmed）
//   证据：真机读数 dist/room-probe-C.json 的 slot8 +0x50 —— 有敌人的房间、未清时是 0
//   证据：真机读数 dist/room-probe-D.json 的 slot8 +0x50 —— 清掉敌人后同一槽位同一偏移变成 1（其它字段都没动）
inline constexpr std::uintptr_t kRoomDescriptorClearOffset = 0x50;
inline constexpr std::uintptr_t kRoomDescriptorClearWidth = 4;

// PC 的 `RoomDescriptor.Flags`：**32 位位掩码**（不是布尔）。低六位分别是 `FLAG_CLEAR`=bit0、`PRESSURE_PLATES_TRIGGERED`=bit1、`SACRIFICE_DONE`=bit2、`CHALLENGE_DONE`=bit3、`SURPRISE_MINIBOSS`=bit4、`HAS_WATER`=bit5；更高位另有用途（bit10 显房、bit16 Rotgut 已清）。★ 与本表 `Clear` 行是**同一个偏移**：`Clear` 只是它的第 0 位（旧实现按『非零即真』读，会把 bit3/bit10/bit16 也算成清房，现在按 PC 契约只看 bit0）（confirmed）
//   证据：0x0036ffc0 —— 存档读取：`add x1, x23, #0x50` 把 desc+0x50 当**对象地址**交给读通道，紧随 0x36FFC4 `mov w2, #0x4` ⇒ 按 4 字节读（旧存档格式 <0x3f 时改走『六次 ldr/orr #1|2|4|8|0x10|0x20/str』把六个布尔合成低六位 ⇒ 低六位确实都是独立位）
//   证据：0x0036889c —— 存档写回：`ldr w8, [x19, #0x50]` 同样按 4 字节读同一个字段（同函数 0x368884 也把 desc+0x50 交给写通道）
//   证据：0x0046ac68 —— 第三处独立读取：`ldr w8, [x0, #0x50]`，其中 x0 是上一条 `bl` 返回的**当前房间描述符** —— 同函数 0x46AC54 `ldr x8, [x0, #0x10]`（本表 `Data` 行）+ 0x46AC5C `ldr w8, [x8, #0x8]`（`Data.Type`，本表 `Data.Type` 行）`cmp #0x5`（ROOM_BOSS）⇒ 只有 BOSS 房才置位，反证 x0 就是 RoomDescriptor
//   证据：0x003e6c50 —— ★ 取值锚点：该函数用 `tbnz w9, #0x3` 单独测 **bit3**（`CHALLENGE_DONE`）—— 一个布尔字段容不下『第 3 位有独立含义』这件事 ⇒ 它是位掩码
//   证据：0x0046ac70 —— ★ 第二个取值锚点：参数为真时 `orr w8, w8, #0x10000` 置 **bit16**，为假时 0x46AC78 `and w8, w8, #0xfffeffff` 清它，再 0x46AC7C `str w8, [x0, #0x50]` 写回 —— `1<<16` 更不可能是布尔
inline constexpr std::uintptr_t kRoomDescriptorFlagsOffset = 0x50;
inline constexpr std::uintptr_t kRoomDescriptorFlagsWidth = 4;

// 房间配置里的房间形状（PC 的 `RoomConfig::Room.Shape`，`RoomShape` 枚举；相对本表 `Data` 指针 +0x5C）。⚠️ 与 `RoomDescriptor+0x50` 的 `Flags` 是两回事：那一格在描述符上，这一格在 `Data` 指过去的配置对象里（confirmed，相对 Data）
//   证据：0x0048f6ec —— XML 房间表解析：`str w0, [x20, #0x5c]` 存的是紧邻上一条 0x48F6E8 `bl atoi` 的返回值（该组件自己的属性名就是 `shape`）；相邻的 `strb …, #0x5b`/`#0x5a` 是 height/width（**1 字节**）⇒ 这一格按 4 字节写，不可能是宽高
//   证据：0x00491774 —— 二进制房间表加载器：`str w8, [x20, #0x5c]!`（后索引写，同样 4 字节）
//   证据：0x0045f97c —— 进房间初始化：`ldr w8, [x8, #0x5c]`，其中 x8 由 0x45F974 `ldr x8, [x19, #0x8]` + 0x45F978 `ldr x8, [x8, #0x10]` 得到（描述符的 `Data`，本表 `Data` 行）⇒ 这一格确实在 `Data` 指向的对象里
//   证据：0x0045f980 —— ★ 取值锚点：读到之后 `sub w8, w8, #0x9` + `cmp w8, #0x3` + `b.hi`（不命中就跳过一整段特判）⇒ 只有 **9..12** 四个取值走 L 形房间的特判，与 PC 的 `RoomShape`（9..12 正好是四个 L 形）逐值吻合 ⇒ 这一格是形状枚举
inline constexpr std::uintptr_t kRoomDescriptorDataShapeOffset = 0x5C;
inline constexpr std::uintptr_t kRoomDescriptorDataShapeWidth = 4;

// 同上函数的第二个条件（值 == 0x22），字段名未定（候选 Variant/Difficulty）；**不进对外 API**（candidate，相对 Data）
//   证据：0x003d0e88 —— 解引用 Data 之后按 4 字节读 Data+0x10
//   证据：0x003d0e8c —— 与常量 0x22 比较；字段名未定 ⇒ 只登记、不进 API
inline constexpr std::uintptr_t kRoomDescriptorDataSecondConditionOffset = 0x10;
inline constexpr std::uintptr_t kRoomDescriptorDataSecondConditionWidth = 4;

// 允许的门（PC 侧 RoomDescriptor.AllowedDoors）；候选依据是『预计算可用门』函数唯一一次字段写就落在这里（candidate）
//   证据：0x003d6818 —— 该函数（语义就是预先算允许的门）唯一一次字段写：str x8, [x20, #0x20]
inline constexpr std::uintptr_t kRoomDescriptorAllowedDoorsOffset = 0x20;
inline constexpr std::uintptr_t kRoomDescriptorAllowedDoorsWidth = 8;

// 显示/可见性标志候选（PC 侧 RoomDescriptor.DisplayFlags 或 Flags）；名字未定 ⇒ 不进 API（candidate）
//   证据：真机读数 dist/room-probe-C.json 的 slot9 +0x48 —— 相邻房间在地图上变得可见时 0→5（位 0 与位 2 置起），而清房不改它
inline constexpr std::uintptr_t kRoomDescriptorDisplayFlagsOffset = 0x48;
inline constexpr std::uintptr_t kRoomDescriptorDisplayFlagsWidth = 4;

// 标量前缀到此为止（0x74 起是容器区）；最后一个标量字段在 0x70（4 字节）（structural）
//   证据：0x003e76f4 —— 拷贝构造用 8 字节 ldp/stp 成对搬标量前缀，最后一处是 4 字节的 0x70
inline constexpr std::uintptr_t kRoomDescriptorScalarPrefixEndOffset = 0x74;

// 一组 vector 的 begin/end（0x90 / 0x98）（structural）
//   证据：0x0035e45c —— ldr x23, [x20, #0x90]! 之后用 [x20, #0x8] 比较 begin/end
inline constexpr std::uintptr_t kRoomDescriptorContainerVectorAOffset = 0x90;
inline constexpr std::uintptr_t kRoomDescriptorContainerVectorAWidth = 16;

// 一个 libc++ string（data 0xa8 / size 0xb0 / capacity 0xb8）（structural）
//   证据：0x0035e4b8 —— ldr x1, [x20, #0xb0]! 之后 sub x0, x20, #0x8（对 0xa8 处的对象做析构/清空）
inline constexpr std::uintptr_t kRoomDescriptorContainerStringOffset = 0xA8;
inline constexpr std::uintptr_t kRoomDescriptorContainerStringWidth = 24;

// 另一组 vector 的 begin/end（0xc0 / 0xc8）（structural）
//   证据：0x0035e494 —— ldr x23, [x20, #0xc0]! 之后用 [x20, #0x8] 比较 begin/end
inline constexpr std::uintptr_t kRoomDescriptorContainerVectorBOffset = 0xC0;
inline constexpr std::uintptr_t kRoomDescriptorContainerVectorBWidth = 16;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：Data、Data.Type、GridIndex、SafeGridIndex、VisitedCount、Clear、Flags、Data.Shape

} // namespace isaac::runtime::layout
