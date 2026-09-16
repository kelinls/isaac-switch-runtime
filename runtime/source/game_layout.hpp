#pragma once

// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/game.json` 生成，请勿手改。**
//
// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。
// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/game.json --verify`
//
// 口径（与表的 `confidence` 一致）：
//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；
//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；
//   structural —— 只说明结构，不声称字段名。

#include <cstdint>

namespace isaac::runtime::layout {

inline constexpr std::uint8_t kGameBuildId[20] = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31, 0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7, 0x23, 0x88, 0xB2, 0xAB
};

// 局内计时（PC 的 `game.TimeCounter`，**有符号** int32，30fps 帧钟：`Game::Update` 每帧与 `FrameCount` 一起 +1）（confirmed）
//   证据：0x003518b0 —— 低半字：`mov w8, #0xf998` + 0x3518B4 `movk w8, #0x24, lsl #16` = 0x24F998，`add x24, x0, x8` ⇒ x24 指向这一组计数器的基址（`FrameCount` 在 0x24F99C）
//   证据：0x003518b4 —— 高半字：`movk w8, #0x24, lsl #16` —— 与上一条合成 0x24F998
//   证据：0x00352114 —— `ldp w8, w9, [x24, #0x4]`：一次读两个相邻计数器 —— +4 是 0x24F99C（`FrameCount`），+8 就是**本行 0x24F9A0**（本字段）；随后 `add …,#1` 两次、0x352120 `stp w8, w9, [x24, #0x4]` 写回 ⇒ 两个计数器每帧一起 +1
//   证据：0x00352120 —— 同一对的写回（`stp w8, w9, [x24, #0x4]`，8 字节）；本行的 offset = 上面合成出来的基址 0x24F998 + 这个立即数 4 + 4（第二个字）
//   证据：0x0003ef3c —— 低半字：控制台 `time` 命令 `mov w9, #0xf9a0` + 0x3EF40 `movk w9, #0x24, lsl #16` = 0x24F9A0；0x3EF48 `ldrsw x8, [x8, x9]`（**有符号**读，`ldrsw`）后除以 30 输出秒
//   证据：0x0003ef40 —— 同上（高半字 `movk w9, #0x24, lsl #16`）
//   证据：0x0041e020 —— ★ 取值锚点：SPEED 挑战倒计时算的是 `28800 − 本字段`（0x41E028 `sub w20, w9, w8`，其中 w8 = 0x41E00C 读出的本字段）。28800 帧 = 16 分钟 × 30 帧/秒 ⇒ 本字段是 **30fps 帧钟**；同一段 `ldrsw`/`mul` 序列也解释了『有符号』
inline constexpr std::uintptr_t kGameTimeCounterOffset = 0x24F9A0;
inline constexpr std::uintptr_t kGameTimeCounterWidth = 4;

// 本局挑战模式（PC 的 `game.Challenge`，`eChallenge` 枚举原值，int32）（confirmed）
//   证据：0x0034e548 —— 低半字：`mov w8, #0xfa88`（下一条 0x34E54C `movk w8, #0x26, lsl #16` 补高半字 ⇒ 0x26FA88）；0x34E550 `add x21, x0, x8` 之后 0x34E570 `str w2, [x21]` 写进去的是**第 2 个形参**，而 `Game::Start` 的签名里第 2 个参数就是 `eChallenge`
//   证据：0x0034e54c —— 高半字：`movk w8, #0x26, lsl #16` —— 与上一条 `mov w8, #0xfa88` 合成 0x26FA88
//   证据：0x0034ea90 —— 同形的第二处：读该字段作为第 1 个实参，尾跳到 `Manager::GetChallengeParams(eChallenge)`（0x34EAA4 → 0x671860）⇒ 这一格就是 `eChallenge`
//   证据：0x0034ea94 —— 同上（高半字 `movk w8, #0x26, lsl #16`）
//   证据：0x0041e010 —— ★ 取值锚点：小地图渲染把该字段与 `0x16`(=22) 比较，命中时算 `28800 − TimeCounter`（0x41E020 `mov w9, #0x7080`；0x41E028 `sub w20, w9, w8`）—— 28800 帧 = 16 分钟 × 30fps 正是 SPEED 挑战的限时，而 IsaacDocs 快照里 `enums/Challenge.md` 写明 22 = `CHALLENGE_SPEED` ⇒ 该字段确实是 `eChallenge`
inline constexpr std::uintptr_t kGameChallengeOffset = 0x26FA88;
inline constexpr std::uintptr_t kGameChallengeWidth = 4;

// 难度（PC 的 `game.Difficulty`，`eDifficulty`：0=NORMAL / 1=HARD / 2=GREED / 3=GREEDIER，int32）（confirmed）
//   证据：0x003501e4 —— 低半字：`mov w8, #0x28`（下一条 0x3501E8 `movk w8, #0x2f, lsl #16` 补高半字 ⇒ 0x2F0028），随后 0x3501EC `ldr w8, [x0, x8]` 读该字段
//   证据：0x003501e8 —— 高半字：`movk w8, #0x2f, lsl #16` —— 与上一条合成 0x2F0028
//   证据：0x003501f0 —— ★ 取值锚点：`and w8, w8, #0xfffffffd`（清掉 bit1）+ 0x3501F4 `cmp w8, #0x1` + `cset w0, eq` ⇒ 『难度是 0 或 1 且等于 1 ⇒ 困难』，与 `eDifficulty` 的 NORMAL=0/HARD=1 逐值吻合；同一片区的 `Game::IsGreedMode` 用 `and #0xfffffffe; cmp #2`，两条互为印证
//   证据：0x0034e538 —— 第二处独立锚点（低半字）：`mov w8, #0x28` + 0x34E53C `movk w8, #0x2f, lsl #16` = 0x2F0028；0x34E544 `add x10, x0, x8` 之后 0x34E56C `str w4, [x10]` 写的是**第 4 个形参**，签名里第 4 个参数正是 `eDifficulty`
//   证据：0x0034e53c —— 同上（高半字 `movk w8, #0x2f, lsl #16`）
inline constexpr std::uintptr_t kGameDifficultyOffset = 0x2F0028;
inline constexpr std::uintptr_t kGameDifficultyWidth = 4;

// 屏幕震动的剩余帧数：`Game::ShakeScreen(int)` 把它设成传入的帧数，`Game::Update` 每帧 −1，减到 0 的那一帧把 `ScreenShakeOffset`（本表下一行）复位成零向量。candidate：命名靠引擎自己的符号名 `Game::ShakeScreen` 与这段行为，拿不出取值常量（candidate）
//   证据：0x00355bc0 —— 低半字：`mov w8, #0xf9ac` + 0x355BC4 `movk w8, #0x24, lsl #16` = 0x24F9AC；0x355BD0 `str w1, [x0, x8]` 把**第 1 个形参**（震动帧数）写进这一格
//   证据：0x00355bc4 —— 高半字：`movk w8, #0x24, lsl #16` —— 与上一条合成 0x24F9AC
//   证据：0x003518ec —— `ldr w8, [x24, #0x14]`，其中 x24 = `Game+0x24F998`（合成见 0x3518B0 那条）⇒ 0x24F998 + 0x14 = **0x24F9AC**；随后 0x3518F4 `subs w8, w8, #0x1`、0x3518FC `str w8, [x24, #0x14]` 每帧 −1
inline constexpr std::uintptr_t kGameShakeTimerOffset = 0x24F9AC;
inline constexpr std::uintptr_t kGameShakeTimerWidth = 4;

// 屏幕震动偏移（PC 的 `game.ScreenShakeOffset`，Vector2 两个 float；与既有结构名 `kGameToScreenAdjustOffset` 是**同一个**字段，不要再登记第二个偏移）。candidate：命名靠引擎自己的用法（世界坐标转屏幕坐标时加它、震动结束时复位成 `Vector2::Zero`），拿不出取值常量（candidate）
//   证据：0x0035190c —— 低半字：`mov w8, #0xf9b0` + 0x351910 `movk w8, #0x24, lsl #16` = 0x24F9B0；0x351914 `add x0, x19, x8` 后 0x351918 `bl 0x66ff80` = `KAGE::Math::Vector2::operator=`，第 2 个实参来自重定位槽 0xAAC660 = `_ZN4KAGE4Math7Vector24ZeroE` ⇒ 震动帧数归零那一帧把它设成零向量（分支条件见 0x351900 `b.ne`）
//   证据：0x00351910 —— 高半字：`movk w8, #0x24, lsl #16` —— 与上一条合成 0x24F9B0
//   证据：0x0048939c —— 第二处：同一合成形态（0x4893A0 `movk w9, #0x24, lsl #16`），0x4893A4 `add x1, x8, x9` 把 **`Game+0x24F9B0` 的地址**交给 `Vector2::operator+`（0x4893AC）—— 世界坐标转屏幕坐标时先加房间滚动偏移、再加它
//   证据：0x00348db8 —— 第三处：渲染层把该字段的地址交出去（0x348DC0 `add x0, x8, x9`）
//   证据：0x00033c7c —— 第四处：相机震动分支生成 `RandomUnitVector` 后写进它（0x33C84 `add x0, x8, x9` + 0x33C90 `bl` `Vector2::operator=`）
//   证据：0x00034168 —— 第五处：`update_drag2` 同样把结果写进它（0x34170 `add x0, x8, x9`）
inline constexpr std::uintptr_t kGameScreenShakeOffsetOffset = 0x24F9B0;
inline constexpr std::uintptr_t kGameScreenShakeOffsetWidth = 8;

// 模块里 `g_Manager` 的 GOT 槽（0xAAC648 = 页基址 0xAAC000 + 0x648）。同样要解两层：`Manager* = *(u64*)(*(u64*)(base + 0xAAC648))`；`ItemConfig` 等子系统是**内嵌**在 `Manager` 里的（取地址相加，不是解指针）。candidate：命名来自 NRO 重定位表（槽 ↦ `_ZN15IsaacRepentance9g_ManagerE`），页基址一半工具核不到（candidate）
//   证据：0x003c0d74 —— 本行的 offset = 0x3C0D70 `adrp x8, 0xaac000` + 这条 `ldr x8, [x8, #0x648]` 的立即数 0x648 ⇒ 0xAAC648；0x3C0D78 再解一次得 `Manager*`，随后 `add x8, x8, #0x36538`（0x3C0D7C/0x3C0D80 合成）就是内嵌的 `ItemConfig`。带注解反汇编里该槽显示为 `; _ZN15IsaacRepentance9g_ManagerE`
//   证据：0x0034e578 —— 第二处：`adrp x8, 0xaac000`（0x34E574）+ 本行这条装载 —— 开局时经 `g_Manager` 取管理器
//   证据：0x00355bd8 —— 第三处：`adrp x8, 0xaac000`（0x355BD4）+ 本行这条装载（随后按 `Manager` 的选项字节取音效设置）
inline constexpr std::uintptr_t kGameManagerGlobalSlotOffset = 0xAAC648;
inline constexpr std::uintptr_t kGameManagerGlobalSlotWidth = 8;

// 模块里 `g_Game` 的 GOT 槽（0xAAC698 = 页基址 0xAAC000 + 0x698）。⚠️ 槽里存的是**指针变量**：`Game* = *(u64*)(base + 0xAAC698)`，要解两层才到 `Game*`（`Game`/`Level` 是这个对象的起始处）。candidate：命名来自 NRO 自己的重定位表，但页基址那一半工具核不到（见表头 C 形态）（candidate）
//   证据：0x00470b18 —— 本行的 offset = 同函数上一条 0x470B14 `adrp x8, 0xaac000`（页基址）+ 这条 `ldr x8, [x8, #0x698]` 的立即数 0x698 ⇒ 0xAAC698；紧随 0x470B1C 再 `ldr x8, [x8]` 一次才拿到 `Game*` ⇒ 槽里是指针变量。带注解反汇编里该槽显示为 `; _ZN15IsaacRepentance6g_GameE`
//   证据：0x0003ef34 —— 第二处独立使用：`adrp x8, 0xaac000`（0x3EF30）+ 本行这条 `ldr x8, [x8, #0x698]` + `ldr x8, [x8]` —— 同一槽、同样的两级解引用
//   证据：0x00348db0 —— 第三处：渲染层同样经这个槽取 `Game*`（0x348DB4 再解一层），随后用它加上 Game 的屏幕震动偏移
inline constexpr std::uintptr_t kGameOwnerGlobalSlotOffset = 0xAAC698;
inline constexpr std::uintptr_t kGameOwnerGlobalSlotWidth = 8;

// 对外 API 只允许读这些字段（`confidence == confirmed`）。
// 当前可用：Challenge、Difficulty、TimeCounter

} // namespace isaac::runtime::layout
