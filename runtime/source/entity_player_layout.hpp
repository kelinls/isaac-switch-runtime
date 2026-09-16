#pragma once

// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/entity_player.json` 生成，请勿手改。**
//
// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。
// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/entity_player.json --verify`
//
// 口径（与表的 `confidence` 一致）：
//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；
//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；
//   structural —— 只说明结构，不声称字段名。

#include <cstdint>

namespace isaac::runtime::layout {

inline constexpr std::uint8_t kEntity_PlayerBuildId[20] = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31, 0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7, 0x23, 0x88, 0xB2, 0xAB
};

// 按键冷却（PC 的 `EntityPlayer.ControlsCooldown`，**有符号 int32**；EID 会**写**它来抑制按键连发）。取值锚点：麻痹药丸分支把它设成 240 或 120 帧。⚠️ `+0x408` 是**另一个**字段（`SetShootingCooldown`，取 max），不是本行（confirmed）
//   证据：0x002b265c —— `ldr w10, [x19, #0x404]` + `cmp #0` + `b.le` ⇒ 值 > 0 时不让射击（这就是『控制冷却』的用法）
//   证据：0x002e1c3c —— 麻痹药丸分支 `str w8, [x20, #0x404]`：写入的是上面 `csel` 出来的帧数
//   证据：0x002e1c2c —— `mov w8, #0xf0`(240) 与紧随的 0x2E1C30 `mov w9, #0x78`(120) 经 0x2E1C34 `csel` 二选一写进本字段 ⇒ 冷却按 240/120 **帧**计，是个帧计数
inline constexpr std::uintptr_t kEntity_PlayerControlsCooldownOffset = 0x404;
inline constexpr std::uintptr_t kEntity_PlayerControlsCooldownWidth = 4;

// 射击延迟（PC 的 `EntityPlayer.MaxFireDelay`）。⚠️ **Switch 侧是 float**，PC AB+ 文档写 int —— 按 float 读。取值锚点：`Entity_Player::Init` 写默认 10.0f（confirmed）
//   证据：0x002b4150 —— `ldr w9, [x19, #0x1834]` → `str w9, [x8, #0x1c]` 抄进每个武器（同一段重复 5 次，对应 +0x1758/1760/1768/1770/1778 五个武器槽）⇒ 它是每帧被武器读取的射击延迟
//   证据：0x0027962c —— `mov x9, #0x41200000`(10.0f)（`movk x9, #0x3f80, lsl #48` 补的高 32 位 1.0f 是相邻的 `+0x1838`）→ 0x279660 `str x9, [x19, x8]`，x8 = 0x1834（见 0x27964c）⇒ 默认射击延迟 10.0
inline constexpr std::uintptr_t kEntity_PlayerMaxFireDelayOffset = 0x1834;
inline constexpr std::uintptr_t kEntity_PlayerMaxFireDelayWidth = 4;

// 眼泪伤害（PC 的 `EntityPlayer.Damage`，float）。取值锚点：`Entity_Player::Init` 把 `0x40600000`(3.5f，Isaac 的基础伤害) 写进这一格（confirmed）
//   证据：0x002ad6d4 —— `ldr s0, [x20, #0x1844]` → 乘倍率后写进 `TearParams::Damage`（0x2AD6DC `str s0, [x26, #0x48]!`）⇒ 这一格是『参与伤害计算的基数』
//   证据：0x00279664 —— `mov x9, #0x40600000`（低 32 位就是 3.5f；`movk x9, #0xc1be, lsl #48` 补的高 32 位是同批写的相邻字段）→ 0x279670 `str x9, [x19, x8]`，其中 x8 = 0x1844（见 0x279668 `mov w8, #0x1844`）⇒ 默认伤害 3.5
inline constexpr std::uintptr_t kEntity_PlayerDamageOffset = 0x1844;
inline constexpr std::uintptr_t kEntity_PlayerDamageWidth = 4;

// 眼泪射程（PC 的 `EntityPlayer.TearRange`，float，原始射程单位）。取值锚点：`Entity_Player::Init` 写默认 260.0f（= 6.5 × 40）（confirmed）
//   证据：0x002a9ba8 —— `ldr w8, [x19, #0x1854]` → 写进新生成子弹的射程字段 ⇒ 它是『子弹能飞多远』的输入
//   证据：0x002796bc —— `str w8, [x19, #0x1854]` —— 访问指令里就带字面偏移
//   证据：0x002796b8 —— 上一条 `mov w8, #0x43820000` 就是 260.0f（Isaac 的基础射程）⇒ 默认射程 260
inline constexpr std::uintptr_t kEntity_PlayerTearRangeOffset = 0x1854;
inline constexpr std::uintptr_t kEntity_PlayerTearRangeWidth = 4;

// 移动速度（PC 的 `EntityPlayer.MoveSpeed`，float）。取值锚点：`Entity_Player::Init` 写默认 1.0f（confirmed）
//   证据：0x002b1748 —— `ldr s0, [x19, #0x194c]` → `fmul`/`fadd` 参与每帧位移（同函数内另有两处同址读取）
//   证据：0x0027969c —— `mov w9, #0x3f800000`(1.0f) → 0x2796a0 `str x9, [x19, x8]`，x8 = 0x194c（见 0x279698）⇒ 基础速度 1.0
inline constexpr std::uintptr_t kEntity_PlayerMoveSpeedOffset = 0x194C;
inline constexpr std::uintptr_t kEntity_PlayerMoveSpeedWidth = 4;

// 能否飞（PC 的 `EntityPlayer.CanFly`，**1 字节** bool）。⚠️ 只改这一格不会立刻改变碰撞层：决定能否飞过石头/坑的是 `Entity+0x1E0`（见 `entity.json` 的 `FlyingLayer`），它由引擎在缓存重算时按本字段同步 —— PC 侧同样要求只在 `MC_EVALUATE_CACHE` 里改（confirmed）
//   证据：0x002bba20 —— ★ 证据形态是『寄存器合成』：`mov w8, #0x1954` 把偏移装进寄存器，**真正的访存是紧随其后的 0x2BBA24 `ldrb w8, [x19, x8]`**（该指令里没有字面立即数，工具核不到它，所以这里引的是准备寄存器那一条）。判据随后是 `cbz`，置位时函数返回 `Vector2(0, -4)` 的飞行视觉偏移（0x2BBA30 `fmov s1, #-4.00000000`）
//   证据：0x002ac3e4 —— 同一形态的第二处：`mov w8, #0x1954` → 0x2AC3E8 `ldrb w8, [x19, x8]`
//   证据：0x002ac3f0 —— ★ 语义锚点：读到的字节 `cmp w8, #0x0` 之后 `csel` 出 `5`（或另一层号），**写进 `Entity+0x1E0`**（0x2AC3F8 `str w8, [x20, #0x1e0]`，即 `entity.json` 已证明的飞行层）⇒ 这一格就是『能否飞』
inline constexpr std::uintptr_t kEntity_PlayerCanFlyOffset = 0x1954;
inline constexpr std::uintptr_t kEntity_PlayerCanFlyWidth = 1;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：Damage、MaxFireDelay、TearRange、MoveSpeed、CanFly、ControlsCooldown

} // namespace isaac::runtime::layout
