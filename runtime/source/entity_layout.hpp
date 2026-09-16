#pragma once

// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/entity.json` 生成，请勿手改。**
//
// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。
// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/entity.json --verify`
//
// 口径（与表的 `confidence` 一致）：
//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；
//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；
//   structural —— 只说明结构，不声称字段名。

#include <cstdint>

namespace isaac::runtime::layout {

inline constexpr std::uint8_t kEntityBuildId[20] = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31, 0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7, 0x23, 0x88, 0xB2, 0xAB
};

// 决定能否飞过沟/石的运动层（派生量，不是 PC 的 Lua 字段）：`Entity::IsFlying()` 的判据字面就是『本字段 < 4』（confirmed）
//   证据：0x0005c744 —— 该函数唯一一条字段访问：`ldr w8, [x0, #0x1e0]`
//   证据：0x0005c748 —— `cmp w8, #0x4` + `cset w0, lo` ⇒ 『层号 < 4 就是能飞』，这个 4 是引擎自己的阈值 ⇒ 本字段是飞行层（`Entity_Player.CanFly` 只是它的来源，见 entity_player.json）
inline constexpr std::uintptr_t kEntityFlyingLayerOffset = 0x1E0;
inline constexpr std::uintptr_t kEntityFlyingLayerWidth = 4;

// 跟班所属的玩家实体指针（PC 的 `EntityFamiliar.Player`，`Entity_Player*`，可为 0）。⚠️ 引擎自己那几处读点都不判空，我们自己读之前必须先判 0（confirmed）
//   证据：0x000d8c98 —— `str x20, [x19, #0x400]` 把形参（`Entity_Player*`）写进这一格（同函数内另两处出口分支同址写入）
//   证据：0x000c0104 —— `ldr x0, [x0, #0x400]` 之后直接喂给『玩家的额外动画是否播完』⇒ 这一格指向的是玩家实体
//   证据：0x000d1184 —— 顺着这一格读出的指针，其 `+0x1738` 被当作 `Entity_Player.PlayerType` 读（0x1738 是本仓库已知的 PlayerType 偏移）⇒ 这一格存的是玩家实体，不是别的对象
inline constexpr std::uintptr_t kEntityFamiliarPlayerOffset = 0x400;
inline constexpr std::uintptr_t kEntityFamiliarPlayerWidth = 8;

// Entity 基类内嵌的主 ANM2（PC 的 `Entity:GetSprite()`）。Entity 单继承、基类子对象在偏移 0，所以所有 `Entity_*`（含 `Entity_Player`/`Entity_Pickup`）的这个 sprite 都在同一偏移。candidate：命名靠『引擎自己把 this+0x50 交给 ANM2 的成员函数』这条形态，没有取值锚点（candidate）
//   证据：0x0006252c —— `add x0, x19, #0x50` 之后紧接 `bl 0x6721d0`；该目标是导入符号 `_ZN15IsaacRepentance4ANM212BeginBatchesEv`（ANM2::BeginBatches）⇒ 引擎自己就把 this+0x50 当 ANM2 用
//   证据：0x0006257c —— 同一函数里真正画 sprite 那一步：`add x0, x19, #0x50` 之后 `bl 0x66fcf0` = 导入符号 `ANM2::Render(Vector2 const&, Vector2 const&, Vector2 const&)`（对照：紧随其后的 0x62538 用的是 `+0x320` 位置偏移，两者不是同一个量）
//   证据：0x000625cc —— 另一个函数（`Entity::PostRender`）里同样 `add x0, x20, #0x50` 后 `bl 0x6721e0` = 导入符号 `ANM2::EndBatches()` —— 两处不同函数互相印证同一个偏移
inline constexpr std::uintptr_t kEntitySpriteOffset = 0x50;
inline constexpr std::uintptr_t kEntitySpriteWidth = 8;

// 叠加在 `Position`(0x310) 上的绘制/逻辑位置偏移（Vector2，两个 float）。candidate：偏移与形态确定，但**命名是推断** —— 项目记录里已辨析过『0x320 与 Position(0x310)、插值旧位置(0x318) 连续构造、并被引擎当位置偏移用，所以判为 PositionOffset，不要拿它当 SpriteOffset』（candidate）
//   证据：0x00062538 —— `add x1, x19, #0x320` 作为第 2 个实参、`add x0, x19, #0x310`（Position）作为第 1 个实参，一起交给 `KAGE::Math::Vector2::operator+`（0x66ff50）⇒ 引擎渲染时算的是 Position + 本字段
//   证据：0x000598a8 —— 出生初始化：`add x0, x19, #0x320` 把该字段交给 0x598B8 `bl 0x66ff80` = `KAGE::Math::Vector2::operator=`（赋初值）
//   证据：0x002d9520 —— 玩家 sprite 渲染：先把整 16 字节 `[x19+0x320]` 存栈 → 临时置零 → 画完再还原 ⇒ 它是『画的时候才叠加』的偏移，不是位置本身
inline constexpr std::uintptr_t kEntityPositionOffsetOffset = 0x320;
inline constexpr std::uintptr_t kEntityPositionOffsetWidth = 16;

// 父实体指针（PC 的 `Entity.Parent`；多段实体的主段、跟班的主人、分段子段的回指都用它，0 表示没有父）。candidate：命名依据是引擎自己的三处符号名（`GetLastParent` 沿本格上溯、`SetParent` 写本格、`ClearReferences` 置 0），但没有被机器复核的取值常量（candidate）
//   证据：0x00058bec —— `ldr x0, [x0, #0x390]` + `cbnz` 循环：沿这一格一路往上找『没有父的那个实体』⇒ 它是父指针，且 0 是合法取值
//   证据：0x0005917c —— `add x21, x0, #0x390` 把该字段地址留在 x21，函数出口 0x59234 `str x19, [x21]` 把形参（新父）写进去；同函数还维护『父 + 0x3B8 的子集合』
//   证据：0x000599a8 —— 出生初始化 `str xzr, [x19, #0x390]`（按 8 字节清零）⇒ 与『0 是合法取值』一致
inline constexpr std::uintptr_t kEntityParentOffset = 0x390;
inline constexpr std::uintptr_t kEntityParentWidth = 8;

// 实体自带 RNG 的种子/状态字（PC 的 `Entity.DropSeed`；`RNG::Next` 会把新状态写回同一格 ⇒ 取过随机数就会变）。candidate：项目记录里写明『命名为推断，偏移是硬的』，故只登记（candidate）
//   证据：0x00058978 —— 构造函数把 `this+0x3d8` 交给紧随的 0x58990 `bl 0x670930` = 导入符号 `_ZN15IsaacRepentance3RNGC1Ev`（`RNG::RNG()`）⇒ 这一格是**内嵌的 RNG 对象头**（实体基类只有这一个 RNG 成员）；出生时 `Entity::Init` 又用同一个 `this+0x3d8`（0x597E8 `add x21, x0, #0x3d8`）调 `RNG::SetSeed(unsigned, unsigned)`（0x5980C）设种子
//   证据：0x004550fc —— 存档按 **32 位**读它（0x455100 `str w8, [x19, #0x38]` 存进实体存档）；同函数 0x4550f4 存的是 `+0x3E8`（InitSeed），两者互证『这一格是 RNG 的种子字』
//   证据：0x004556ac —— 读档回灌：`add x0, x19, #0x3d8` 之后 `bl 0x670970` = `RNG::SetSeed(unsigned, unsigned)` ⇒ 这一格就是种子的落地位置
inline constexpr std::uintptr_t kEntityDropSeedOffset = 0x3D8;
inline constexpr std::uintptr_t kEntityDropSeedWidth = 4;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：FlyingLayer、FamiliarPlayer

} // namespace isaac::runtime::layout
