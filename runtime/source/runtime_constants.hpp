#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

using u8 = std::uint8_t;
using u64 = std::uint64_t;

inline constexpr u64 kTargetTitleId = 0x010021C000B6A000ULL;
inline constexpr uintptr_t kGameOwnerGlobalSlotOffset = 0xAAC698;

// --- 玩家 / ItemConfig 的引擎偏移（2026-09-12 反汇编定位，真机探针待验证）------------
//
// 背景：这个 NRO 里**没有 Lua VM、也没有 `Isaac.*` 的绑定函数**（全镜像搜 `luaL_newstate`/
// `lua_State`/`main.lua`/`isaac_mods` 等全部 0 命中），所以 Runtime 必须自己封装引擎原语。
// 下面每一项都是"已交叉验证的静态结论"，但**尚未上真机**：按项目探针纪律，实现前要用一次
// `svcBreak` 把指针与字段打出来确认（配方见 `docs/问题与解决记录.md` 的批次 2 条目）。
//
// 玩家链路：`Game* = *(u64*)(base + kGameOwnerGlobalSlotOffset)`（是**指针变量**，不是对象），
// 玩家数组是 `Game + kGamePlayerArrayOffset` 处的 `std::vector<Entity_Player*>`（步长 8），
// `players[0]` 即主玩家；引擎里**没有** `PlayerManager::GetPlayer(int)`，必须自己索引 + 越界检查。
inline constexpr uintptr_t kGameManagerGlobalSlotOffset = 0xAAC648;       // g_Manager 的 GOT 槽
inline constexpr uintptr_t kGamePlayerManagerOffset = 0x25C40;            // Game::PlayerManager 内嵌对象
inline constexpr uintptr_t kGamePlayerArrayBeginOffset = 0x25C50;         // vector<Entity_Player*> begin
inline constexpr uintptr_t kGamePlayerArrayEndOffset = 0x25C58;           //   … end
inline constexpr uintptr_t kGamePlayerArrayCapacityOffset = 0x25C60;      //   … capacity
// 玩家数量上限：只用于"偏移猜错时不要把一整段垃圾当成向量"这层保险；真正的判据是读者的
// vptr 校验（`Isaac.GetPlayer`）或这条向量自身的形状（`Game:GetNumPlayers`）。
inline constexpr std::size_t kEnginePlayerMaximumCount = 32;
inline constexpr uintptr_t kGameRoomPointerOffset = 0x21550;              // Game → Room*
// `Level` 就是 `Game`（同一地址），"当前房间"是同一段里的两个 u32：
// 证据是固定 NRO 里 `Level::GetCurrentRoomDesc @ 0x3DC684` 的反汇编 —— 它把
// `Level + 0x21558` 读进 `w1`、`Level + 0x21560` 读进 `w2`，随后尾跳到唯一的
// `Level::GetRoomByIdx(int, int)` PLT thunk，即这两个字段就是"当前 descriptor 的解析参数对"。
// 见 `docs/PC-Mod-兼容矩阵.md`（Stage104 条目）与 `docs/会话交接-2026-08-26.md`。
inline constexpr uintptr_t kLevelCurrentRoomIndexOffset = 0x21558;        // 当前房间在描述符表里的索引
// `Level + 0x00` = 关卡（`eLevelStage`）、`Level + 0x04` = **关卡类型**（`eStageType`）。
// 证据（2026-09-16，`Level::SetStage(eLevelStage, eStageType) @ 0x3E420C` 的收尾两条）：
//   `stp w21, w20, [x19]` —— 把第 1 个参数（关卡）与第 2 个参数（类型）**连着**写进 `this+0x00`/`+0x04`；
// 另有两条交叉验证：
//   * `Level::GetAbsoluteStage @ 0x3E7F3C` 只读 `*(u32*)this`（就是关卡，贪婪模式再做一次映射）；
//   * 同函数里对 `+0x04` 做 `and w8, w8, #0xfffffffe; cmp w8, #0x4` —— 清掉 bit0 后与 4 比较，
//     正好把 `STAGETYPE_REPENTANCE(4)` 与 `STAGETYPE_REPENTANCE_B(5)` 归成一类
//     ⇒ 该字里存的就是**枚举原值**（4/5 互为"忏悔时代关卡"）。
inline constexpr uintptr_t kLevelStageOffset = 0x00;
inline constexpr uintptr_t kLevelStageTypeOffset = 0x04;
//: `StageType.STAGETYPE_ORIGINAL`（PC 枚举 0）：`Level:IsAltStage()` 的判据是"类型不是它"。
inline constexpr std::uint32_t kStageTypeOriginal = 0;
//: `TruppeStageType`：`STAGETYPE_REPENTANCE(4)` / `STAGETYPE_REPENTANCE_B(5)` 清掉 bit0 都是 4。
inline constexpr std::uint32_t kStageTypeRepentanceEraMasked = 0x4;
//: 幕府/革赫那第二层（`STAGE4_3 == 9`）—— PC 文档说 `IsPreAscent()` 指的是"通往升华的那一层"。
inline constexpr std::uint32_t kLevelStagePreAscent = 9;
inline constexpr uintptr_t kLevelCurrentRoomDimensionOffset = 0x21560;    //   … 对应的维度参数
inline constexpr uintptr_t kGameHudOffset = 0xE8468;                      // Game → HUD

// `Manager::GetLanguageCode() const`：读取 `Manager + 0x36CC0` 的 StringTable 当前语言索引，
// 再映射为两字符语言代码。反汇编见 `tools/nro_disasm.py 0x3F8AFC`。
inline constexpr uintptr_t kManagerGetLanguageCodeOffset = 0x3F8AFC;
inline constexpr std::array<u8, 16> kManagerGetLanguageCodeExpectedBytes = {
    0xFD, 0x7B, 0xBF, 0xA9, 0xFD, 0x03, 0x00, 0x91,
    0x08, 0x98, 0x8D, 0x52, 0x68, 0x00, 0xA0, 0x72,
};

// `Game + 0x24F99C`：**本局的游戏帧计数（u32）**，即 PC 的 `Game:GetFrameCount()`。
// 证据（同一个 build `91C73FDD…`，`tools/nro_disasm.py`）：
//   * **每帧 +1**：`Game::Update`（`0x351884`）在 `0x3518B0` 先把 `0x24F998` 装进 `x24`
//     （`mov w8,#0xf998; movk w8,#0x24,lsl#16; add x24,x0,x8`），随后 `0x352114`–`0x352120`
//     `ldp w8,w9,[x24,#0x4]; add w8,w8,#1; add w9,w9,#1; stp w8,w9,[x24,#0x4]`
//     —— 一次更新把 `+0x24F99C` 与 `+0x24F9A0` 两个计数器各加一（后者是同一步里的第二个计数）。
//   * **构造时清零**：`Game::init_vars`（`0x34DA80`）`0x34DAC4`–`0x34DAC8`
//     `add x9,x0,x9`（x9 = `0x24F998`）后 `stur xzr,[x9,#0x4]` → `+0x24F99C` 归零。
//   * **引擎自己就是这么用的**：`Entity::GetFrameCount()`（`0x5A210`）与 `Room::GetFrameCount()`
//     （`0x470B0C`）都是 `ldr w8,[Game+0x24F99C]; sub w0,w8,[this+各自的起始帧]`；
//     全镜像共 155 处引用该偏移，`Game::SaveState`（`0x3505A4`）也把它抄进 `GameState`。
// 语义提示：计数器在 `Game::Update` 里前进，所以**暂停期间不增长**，与 PC 文档
// （"Returns the number of frames the gameplay is actively running. Pauses are therefore not
// included!"）一致；但**它是否随"新开一局"归零未验证**（唯一找到的写入点只有 `init_vars`），
// 所以 EID 的 `game:GetFrameCount() < 10*30`（开局警告）不保证能命中。
inline constexpr uintptr_t kGameFrameCountOffset = 0x24F99C;

// --- `Game` 的四个 Lua 字段（2026-09-16 静态取证；NRO 文件偏移 = 运行时模块偏移）------------
//
// 四个都落在已知簇里：`Game+0x24F998` 起是一组计数器/计时器（`FrameCount` 在 0x24F99C），
// `Game+0x26FA88` 起是挑战状态，`Game+0x2F0028` 是难度。每条都有**两处不同函数**的指令证据。

// `game.Challenge`：`eChallenge` 枚举原值（int32，4 字节）。
//   * `Game::Start(ePlayerType, eChallenge, Seeds, eDifficulty) @ 0x34E570`：`str w2,[x21]`，
//     其中 x21 = this+0x26FA88（`0x34E548 mov w8,#0xfa88` / `movk w8,#0x26,lsl#16` / `add x21,x0,x8`），
//     w2 就是第 2 个参数 eChallenge；
//   * `Game::GetChallengeParams() const @ 0x34EA9C`：`ldr w1,[x8]`（x8 = this+0x26FA88）后尾跳到
//     `Manager::GetChallengeParams(eChallenge)`；
//   * 旁证：`Game::Init @ 0x34DD7C` 归零、`Game::SaveState @ 0x350678` / `RestoreState @ 0x34F10C`
//     在 GameState+0x00 来回抄。
inline constexpr uintptr_t kGameChallengeOffset = 0x26FA88;

// `game.Difficulty`：`eDifficulty`（0=NORMAL/1=HARD/2=GREED/3=GREEDIER，int32）。
//   * `Game::Start @ 0x34E56C`：`str w4,[x10]`（x10 = this+0x2F0028，w4 = 第 4 个参数 eDifficulty）；
//   * `Game::IsHardMode() const @ 0x3501EC`：`ldr w8,[x0,#0x2F0028]` + `and w8,w8,#0xfffffffd`
//     + `cmp w8,#0x1` + `cset w0,eq`；同址的 `Game::IsGreedMode() @ 0x350208`（**正是本项目已在打补丁
//     的那条函数**）用 `and #0xfffffffe; cmp #2` —— 两条 `and`/`cmp` 与 PC 枚举逐值吻合，互为印证。
inline constexpr uintptr_t kGameDifficultyOffset = 0x2F0028;

// `game.TimeCounter`：**有符号** int32，30 fps 帧钟（PC 文档："same as FrameCounter but can be
// modified, mostly used for timed events"）。
//   * `Game::Update @ 0x352114-0x352120`：`ldp w8,w9,[x24,#0x4]` / `add …,#1` / `add …,#1` /
//     `stp w8,w9,[x24,#0x4]`（x24 = Game+0x24F998）⇒ 每帧与 `FrameCount`(0x24F99C) **一起** +1；
//   * 控制台 `time` 命令 @0x3EF48：`ldrsw x8,[x8,x9]`（x9 = 0x24F9A0）后除以 30 输出秒 ⇒ 有符号；
//   * 旁证：`ScoreSheet::Calculate @ 0x49C504`（`scvtf` + `/30`）、`Minimap::Render @ 0x41E00C`
//     （SPEED 挑战倒计时 `28800 - 它`）、`Room::TrySpawnBossRushDoor @ 0x466B1C`（日志 "Mom clear time"）。
inline constexpr uintptr_t kGameTimeCounterOffset = 0x24F9A0;

// `game.ScreenShakeOffset`：`Vector`（两个 float，8 字节）——**与既有 `kGameToScreenAdjustOffset`
// 是同一个字段**（那条是结构名、这是 PC 名），这里只加别名，**不要**新增第二个偏移。
// 语义证据（2026-09-16 补）：
//   * `Game::Update @ 0x3518EC-0x35191C`：震动帧数 `Game+0x24F9AC` 递减到**恰好 0** 的那一帧，
//     用 `Vector2::operator=` 把它设成 `KAGE::Math::Vector2::Zero`；
//   * `Camera::update_drag2 @ 0x34164` / `update_ultrasmooth @ 0x33C78`：震动分支（读 0x24F9AC 与
//     0x24F99C 生成 `RandomUnitVector`）之后把结果写进它；
//   * `Room::Render @ 0x47FD60`、`FXLayers::render @ 0x348DAC`、`HUD::Render @ 0x3AC814` 把它
//     加进/读进渲染坐标；
//   * 模组侧佐证：EID 的 `eid_api.lua:2207`
//     `room:WorldToScreenPosition(Vector(0,0)) - room:GetRenderScrollOffset() - game.ScreenShakeOffset`
//     —— 减掉的两项正好是本仓库已取证的那两项（Room 的 0x1938 与 Game 的 0x24F9B0）。
inline constexpr uintptr_t kGameScreenShakeOffsetOffset = 0x24F9B0;
// 顺带取证（同一簇，供后续用）：屏幕震动的剩余帧数 —— `Game::ShakeScreen(int) @ 0x355BD0`
// `str w1,[x0,#0x24F9AC]`，`Game::Update @ 0x3518EC` 每帧 -1。
inline constexpr uintptr_t kGameShakeTimerOffset = 0x24F9AC;

// Entity 基类（顺序与 `Entity::Init` 的 `stp w1,w2,[x0,#0x38]; str w3,[x0,#0x40]` 一致）。
// 陷阱：`Entity + 0x3E8` 是**生成种子**而不是 `Index`（236 处引用多为 `ldr w1,[x,#0x3e8]; bl RNG::RNG`）。
inline constexpr uintptr_t kEntityTypeOffset = 0x38;                      // u32，ENTITY_PLAYER == 1
inline constexpr uintptr_t kEntityVariantOffset = 0x3C;                   // u32
inline constexpr uintptr_t kEntitySubTypeOffset = 0x40;                   // u32（玩家时即 PlayerType）
inline constexpr uintptr_t kEntityPositionOffset = 0x310;                 // Vector2（两个 float）
inline constexpr uintptr_t kEntityVelocityOffset = 0x334;                 // Vector2
inline constexpr uintptr_t kEntitySizeOffset = 0x344;                     // float 半径
// `Entity.Parent`（`Entity*`，8 字节，可为 0）—— 多段实体的主段、跟班挂主人、分段子段回指：
//   * 写：`Entity::SetParent @ 0x59164`：`0x5917C add x21,x0,#0x390` + `0x59234 str x19,[x21]`
//     （x19 = 形参 x1；同函数还维护"父+0x3B8 的 std::set<Entity*>"子集合）；
//   * 读：`Entity::GetLastParent @ 0x58BE8`：`ldr x0,[x0,#0x390]` + `cbnz`（沿本字段上溯到顶）；
//   * 读：`Entity::HasCommonParentWithEntity @ 0x58F40/0x58F54`（两条父链各自上溯后比较）；
//   * 空判：`Entity::ClearReferences @ 0x5D410/0x5D418`（`ldr` 后直接 `cbz`）⇒ 合法取值为 0。
inline constexpr uintptr_t kEntityParentOffset = 0x390;
// `Entity.PositionOffset`（`Vector2`，两个 float）—— 引擎把它叠加在 `Position(0x310)` 上算绘制位置：
//   * `Entity::Render @ 0x624FC`：`0x62534 add x0,x19,#0x310` / `0x62538 add x1,x19,#0x320` /
//     `bl Vector2::operator+`；
//   * `Entity_Player::render_player_sprite @ 0x2D9398`：`0x2D9520 ldr x8,[x19,#0x320]`（存栈）→
//     临时置零 → 渲染时用 `Position + 它` → 还原；
//   * 出生清零：`Entity::Init @ 0x598A8`（`add x0,x19,#0x320` + `Vector2::operator=`）。
//   ⚠️ 命名辨析：PC 另有 `SpriteOffset`（也是 Vector）。0x320 与 Position(0x310)、插值旧位置(0x318)
//   连续构造、并被引擎当"位置偏移"用 ⇒ 判为 `PositionOffset`；**不要**拿它当 `SpriteOffset`。
inline constexpr uintptr_t kEntityPositionOffsetOffset = 0x320;          // Vector2（两个 float）
// `Entity.DropSeed`（4 字节整数；**命名为推断**，偏移是硬的）：
//   * 实体基类**只有这一个 RNG 成员**，就在 `this+0x3D8`（构造 `Entity::Entity @ 0x58978 add x0,x19,#0x3d8`
//     + `bl RNG::RNG()`；析构 `0x589FC` 同址 `bl RNG::~RNG()`）；
//   * `RNG::SetSeed @ 0x44E3C0`：`str w1,[x0]` ⇒ 种子就是该 RNG 对象头 4 字节；
//   * `Room::save_entity @ 0x4550FC`：`ldr w8,[x20,#0x3d8]` → 按 **32 位整数**存进存档
//     （同函数 0x4550F4 存的是 `0x3E8`=InitSeed，与既有常量互证）；
//   * `Room::restore_entity @ 0x4556AC`：`add x0,x19,#0x3d8` + `bl RNG::SetSeed` 回灌。
//   ⚠️ 这是"种子/状态字"：`RNG::Next @ 0x44E4B0` 会把新状态写回同一个字 ⇒ 取随机数会变。
inline constexpr uintptr_t kEntityDropSeedOffset = 0x3D8;                // uint32（RNG 的种子/状态字）
// `EntityFamiliar.Player`（`Entity_Player*`，8 字节）：
//   * 写：`Entity_Familiar::SetPlayer @ 0xD8C10`：`0xD8C98/0xD8CE8 str x20,[x19,#0x400]`；
//   * 读：`Entity_Familiar::player_can_fire @ 0xC0104`：`ldr x0,[x0,#0x400]` 直接喂
//     `Entity_Player::IsExtraAnimationFinished()`；
//   * 读：`Entity_Familiar::Render @ 0xD1174`：`ldr x0,[x0,#0x400]` → `ldr w8,[x0,#0x1738]`
//     （0x1738 正是已知的 `Entity_Player.PlayerType` ⇒ 证明这一格存的是玩家实体）。
//   ⚠️ 引擎这几处读点都不判空，我们自己读之前必须先判 0。
inline constexpr uintptr_t kEntityFamiliarPlayerOffset = 0x400;          // Entity_Player*
inline constexpr uintptr_t kEntityPlayerIndexOffset = 0x19F0;             // Entity_Player 在 players 里的下标
inline constexpr uintptr_t kEntityPlayerTypeOffset = 0x1738;              // PlayerType（没有 GetPlayerType 方法）
inline constexpr uintptr_t kEntityPlayerCollectibleBeginOffset = 0x1AB8;  // 每收藏品计数数组（步长 4）
inline constexpr uintptr_t kEntityPlayerCollectibleEndOffset = 0x1AC0;
inline constexpr uintptr_t kEntityPlayerSize = 0x2FA0;                    // sizeof(Entity_Player)
// `Entity_Player` 的 vtable 指针（`_ZTVN…Entity_PlayerE + 0x10`）：判断"这个指针真是一张
// Entity_Player"的最强判据，用 `*(u64*)candidate == base + 该偏移`。
inline constexpr uintptr_t kEntityPlayerVtableOffset = 0xA37110;
inline constexpr uintptr_t kEntityPlayerHasCollectibleOffset = 0x27D3F4;  // HasCollectible(id, bool)
// `Entity_Player::AddCollectible(eCollectibleType, int, bool, int, int)`（`0x292A74`，
// 符号 `_ZN15IsaacRepentance13Entity_Player14AddCollectibleENS_16eCollectibleTypeEibii`）。
// 清单里属 `missing_easy`（有底座、由我们自己接）。接法与 `HasCollectible` 同一种：
// **模块基址 + 偏移**，外加 4 字节入口指纹（`sub sp, sp, #0x110`）——偏移写错会落到别的函数上，
// 调用它的后果不可预期，所以这一层指认比"只查可读"更严。
inline constexpr uintptr_t kEntityPlayerAddCollectibleOffset = 0x292A74;
// 首 4 字节按**小端整字**存（`FF 43 04 D1` → `0xD10443FF`）：调用点用一次 `ReadEngine<u32>`
// 就能核对，不必逐字节读。
inline constexpr std::uint32_t kEntityPlayerAddCollectibleEntryWord = 0xD10443FFU;

// --- Entity_Player 的字段（2026-09-14 反汇编定位）--------------------------------------
//
// `ControllerIndex`（`+0x19EC`，u32）：`Entity_Player::SetControllerIndex(unsigned, bool)`
// （`0x29B6FC`）的**第一条指令**就是 `str w1, [x0, #0x19ec]`，随后把同一个值传播给子玩家
// （`[x0+0x2460]`/`[x0+0x2588]`/`[x0+0x2580]` 三个"分身/附属"指针各自的 `+0x19EC`）——
// 这就是 PC 的 `EntityPlayer.ControllerIndex`。它与 `kEntityPlayerIndexOffset`（`+0x19F0`）
// 相邻，但**不是**同一个量：`+0x19F0` 是"玩家在 `players` 向量里的下标"。
//
// 血量（PC 的 `GetEffectiveMaxHearts`/`GetSoulHearts`/`GetBrokenHearts`）：
//   * `+0x16B8`（u32）：**红心容器数**。`Entity_Player::GetEffectiveMaxHearts()`（`0x27E8AC`）
//     在"非骨心角色"分支上返回 `[x0+0x16b8] + [x0+0x2450]*2`（`0x27E8DC`–`0x27E8E4`），
//     骨心角色分支只返回 `[x0+0x16b8]`（`0x27E8D4`）。
//   * `+0x2450`（u32）：**骨心数**（上面那个 `lsl #1` 就是它）。
//   * `+0x16C4`（u32）：**魂心数**（半心为单位，含黑心）。证据：
//     `Entity_Player::IsBlackHeart(int)`（`0x27FE70`）先 `ldr w8,[x0,#0x16c4]`，
//     `ccmp w8, w1, #8, ge` —— 即"下标 i 必须 ≤ 魂心总数"；`GetNumBlackHearts()`（`0x27FF84`）
//     同样先取 `[x0+0x16c4]` 再 `[x0+0x16c8]`（黑心位图）。
//   * `+0x2470`（u32）：**碎心数**。`Entity_Player::AddBrokenHearts(int)`（`0x29A550`）
//     `ldr w9,[x19,#0x2470]` → 加参数 → 夹到 `GetHeartLimit(true)/2` → `str w8,[x19,#0x2470]`。
// 骨心角色的判据（`GetEffectiveMaxHearts` 里的掩码）：`PlayerType <= 0x28` 且
// `(1 << PlayerType) & 0x11883021410` 命中的角色**不计**骨心，即 PlayerType ∈
// {4, 10, 12, 17, 24, 25, 31, 35, 36, 40}（???、The Lost、Black Judas、The Soul、
// T.Judas、T.???、T.Lost、T.Forgotten、T.Bethany、T.Soul）。
inline constexpr uintptr_t kEntityPlayerControllerIndexOffset = 0x19EC;    // u32
inline constexpr uintptr_t kEntityPlayerRedHeartContainersOffset = 0x16B8;  // u32（半心单位）
inline constexpr uintptr_t kEntityPlayerSoulHeartsOffset = 0x16C4;          // u32（半心单位）
inline constexpr uintptr_t kEntityPlayerBoneHeartsOffset = 0x2450;          // u32
inline constexpr uintptr_t kEntityPlayerBrokenHeartsOffset = 0x2470;        // u32
inline constexpr std::uint64_t kEntityPlayerBoneHeartlessTypeMask = 0x11883021410ULL;
inline constexpr std::uint32_t kEntityPlayerMaximumTypeForBoneHearts = 0x28;

// --- 房间实体容器（批次 4，2026-09-12 反汇编静态定位）----------------------------------
//
// `Room + 0x1950` 是**内嵌**的 `IsaacRepentance::EntityList` 对象（既不是指针也不是
// `std::vector`）。铁证 `Room::AddEntity` @ `0x468a7c`：
//   `ldr w8,[x0,#0x1948]`（索引计数器）→ `add x0,x0,#0x1950`（取 `&EntityList`）
//   → `str w8,[x1,#0x30]`（**顺带证明 `Entity+0x30` == `Entity.Index`**）→ `b EntityList::Add`。
// `Room::RemoveEntity` / `Room::reset` 同形态。子偏移表（`EL` = `Room + 0x1950`）：
//
//   | 偏移 | 含义 |
//   |---|---|
//   | `EL+0x48` / cap `+0x50`=0x4000 / count `+0x54` | 一般实体表 |
//   | `EL+0x60` / cap `+0x68`=0x100 / count `+0x6c` | 持久实体表（PLAYER/FAMILIAR/KNIFE） |
//   | **`EL+0x78` / cap `+0x80`=0x800 / count `+0x84`** | **房间内活动实体全集（枚举用它）** |
//   | `EL+0x90` / `+0x98` / `+0x9c` | 渲染排序表 |
//   | `EL+0xA8` / cap `+0xB0`=0x4000 / count `+0xB4` | EFFECT 实体表 |
//   | `EL+0xC0` / `+0xD8`(cap 0x8000) / `+0xE4` | 查询结果暂存 + **单调 arena** |
//
// **容量是固定常量、从不重分配**（`Add` 溢出只打日志），所以三个容量值可以当**容器指纹**用。
inline constexpr uintptr_t kRoomEntityListOffset = 0x1950;      // Room → 内嵌 EntityList
// `Room + 0x30` = **网格实体表**（按网格下标索引，每项 8 字节的 `GridEntity*`）。
// 证据（2026-09-16）：`Room::SpawnGridEntity(int index, eGridEntityType type, u32, u32, int) @ 0x452528`
// 里 `add x8, x0, w1, uxtw #3; ldr x8, [x8, #0x30]` —— 就是用网格下标取这一族的指针；
// 同处还看到 `ldr w0, [x8, #0x40]`（`GridEntity + 0x40` 参与碰撞判定）。
inline constexpr uintptr_t kRoomGridEntityTableOffset = 0x30;
// `GridEntity + 0x18` = 类型（`eGridEntityType`）：`GridEntity::Init(eGridEntityType) @ 0x37E438`
// 的第一条存值就是 `str w1, [x0, #0x18]`（w1 即类型参数）。
inline constexpr uintptr_t kGridEntityTypeOffset = 0x18;
// `GridEntity + 0x10` = **variant**。证据（2026-09-16，行为锚点）：
// `GridEntity_Poop::InitSubclass` / `GridEntity_Poop::Update` 反复读 `+0x10` 并与 **1000 (0x3E8)**
// 比较 —— Isaac 里便便的 variant 正是 1000 起（1000=普通便便、1001=玉米…），共 4 处独立比较。
// 另有结构证据：`GridEntity::Init(const Desc&) @ 0x37E4BC` 把 `Desc+0x00..0x1C` 整段拷到
// 对象 `+0x08..0x24`，其中对象 `+0x18` ← `Desc+0x10`（与上面那条"类型在 +0x18"一致 ✓）。
inline constexpr uintptr_t kGridEntityVariantOffset = 0x10;
// `GridEntity + 0x30` = **该网格实体自己的 RNG**（PC Lua 的 `GridEntity:GetRNG()`）。
// 两条互相独立的证据（2026-09-16 反汇编，`add x0, xThis, #0x30` 形态 —— 不是 `ldr`，是把字段
// **地址**当 `this` 交给 `RNG` 的方法，所以看起来像"取地址传参"）：
//   * `GridEntity::hurt_func(Entity*, float, int, unsigned, bool) @ 0x37E57C`：
//     序言 `mov x19, x0`（0x37E5B8）说明 x19 就是 `this`；函数体 0x37E928 处
//     `add x0, x19, #0x30; bl RNG::Next`，紧接着把这个返回值当种子构造临时 RNG
//     （`RNG::RNG(seed, 2)`）—— 这正是引擎里"从本实体的 RNG 派生一个新 RNG"的标准写法；
//   * `GridEntity_Spikes::InitSubclass() @ 0x393B34`：0x393CB8 处
//     `add x0, x19, #0x30; RNG::SetSeed([x19+0x24], 35)` —— 尖刺在初始化时把自己的 RNG
//     按 shift=35（Isaac 内部最常用的档位）重新播种，与 IsaacDocs `GridEntity.md:38` 那句
//     "这个 RNG 在全关卡的所有网格实体上用同一个种子初始化"完全对上。
// `RNG` 自身布局来自 `RNG::SetSeed(uint, uint) @ 0x44E3C0`：`[this+0x0] = seed`、
// `[this+0x4..0xB] = s_Shifts[idx]`、`[this+0xC] = s_Shifts[idx].second` ⇒ 共 16 字节、
// **种子就是头 4 字节**（PC 的 `RNG:GetSeed()` 读它、`Next()` 原地更新它）。
inline constexpr uintptr_t kGridEntityRngOffset = 0x30;
inline constexpr std::size_t kRngObjectSize = 16;

// `Room:WorldToScreenPosition(Vector)`（批次 10，2026-09-15）：引擎里它就是
// `GetRenderPosition(世界坐标, true) + Room.RenderScrollOffset + Game.ToScreenAdjust`。
// 三处证据都来自 `Room::WorldToScreenPosition @ 0x489354` 这一条函数的反汇编：
//   * `bl 0x670fa0`（PLT 桩，指向 `_ZN15IsaacRepentance17GetRenderPositionERKN4KAGE4Math7Vector2Eb`）；
//   * `mov w8,#0x1938` + `add x1,x19,x8` → `Vector2::operator+` ⇒ 房间的滚动偏移；
//   * `g_Game` → `*(g_Game)` + `0x24F9B0` → `Vector2::operator+` ⇒ Game 那一份调整量。
inline constexpr uintptr_t kGetRenderPositionStubOffset = 0x670FA0;
inline constexpr std::array<u8, 16> kGetRenderPositionStubExpectedBytes = {
    0x70, 0x21, 0x00, 0xD0, 0x11, 0x9E, 0x43, 0xF9,
    0x10, 0xE2, 0x1C, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// Game 那一份"世界→屏幕"调整量的偏移。**名字只是结构性的**（该字段的语义没有单独定位），
// 证据 = 同一条函数把它加进结果里；所以它只用于这条 API 的换算，不对外暴露字段。
inline constexpr uintptr_t kGameToScreenAdjustOffset = 0x24F9B0;
inline constexpr uintptr_t kEntityListLiveBeginOffset = 0x78;   // Entity**（步长 8）
inline constexpr uintptr_t kEntityListLiveCapacityOffset = 0x80;  // u32，恒 0x800
inline constexpr uintptr_t kEntityListLiveCountOffset = 0x84;     // u32
inline constexpr std::uint32_t kEntityListLiveCapacity = 0x800;
// 一般实体表的 begin/count（`+0x48`/`+0x54`）：批次 4 的注释表里早就写了这一对与 `+0x50`
// 的容量，但只给容量具了名。2026-09-13 的 `ISAACEL1` 探针要把三张表的 begin/count 都报出来，
// 所以在这里补齐（纯新增，不改动既有常量）。
inline constexpr uintptr_t kEntityListGeneralBeginOffset = 0x48;      // Entity**（步长 8）
inline constexpr uintptr_t kEntityListGeneralCapacityOffset = 0x50;  // u32，恒 0x4000
inline constexpr uintptr_t kEntityListGeneralCountOffset = 0x54;     // u32
inline constexpr std::uint32_t kEntityListGeneralCapacity = 0x4000;
inline constexpr uintptr_t kEntityListEffectBeginOffset = 0xA8;      // Entity**（步长 8）
inline constexpr uintptr_t kEntityListEffectCapacityOffset = 0xB0;   // u32，恒 0x4000
inline constexpr uintptr_t kEntityListEffectCountOffset = 0xB4;      // u32
inline constexpr uintptr_t kEntityListArenaCapacityOffset = 0xE0;    // u32，恒 0x8000
inline constexpr std::uint32_t kEntityListArenaCapacity = 0x8000;

// Entity 基类里本批次新用到的字段（出处：`EntityList::Add`/`Room::AddEntity` 的存值形态与
// `Entity::GetFrameCount` 的减法形态，见 `docs/问题与解决记录.md` 批次 4）。
inline constexpr uintptr_t kEntityIndexOffset = 0x30;         // u32，`Entity.Index`（房间实体表序号）
inline constexpr uintptr_t kEntitySpawnFrameOffset = 0x2F4;   // u32，生成帧（FrameCount 的减数）
// `Entity.InitSeed`（u32）= `+0x3E8`。**这一条在本仓库里早就被记为"陷阱"**：
// "`Entity + 0x3E8` 是**生成种子**而不是 `Index`（236 处引用多为 `ldr w1,[x,#0x3e8]; bl RNG::RNG`）"
// —— 32 位装载后直接当 `RNG::RNG(seed)` 的入参，就是 PC 的 `Entity.InitSeed`。
// EID 把它当**表键**用（`main.lua:206`/`210`/`264`、`features/eid_api.lua:3095`），
// 所以它必须是非 nil 的数字：`table[nil] = x` 在 Lua 里是硬错误 `table index is nil`
// （与批次 3 的 `EID.controllerIndexes[p.ControllerIndex]` 完全同类）。
inline constexpr uintptr_t kEntityInitSeedOffset = 0x3E8;
// 64 位实体标志。bit46 == `EntityFlag.FLAG_NO_QUERY`（`enums.lua:1034`：`1<<46` "Hide from
// query results"）→ `QueryRadius`/`FindByType` 都跳过；bit3 置位的实体**不在活表**里。
inline constexpr uintptr_t kEntityFlagsOffset = 0x1B8;
inline constexpr unsigned kEntityFlagNoQueryBit = 46;
// `Entity` 家族 vtable 区间（`_ZTV` 段），用于"这个指针像不像一张 Entity"的通用判据。
// `Entity_Pickup` 的 vtable 落在其中，是 `ToPickup()` 的唯一判据。
inline constexpr uintptr_t kEntityVtableRangeBeginOffset = 0xA34F58;
inline constexpr uintptr_t kEntityVtableRangeEndOffset = 0xA38230;
inline constexpr uintptr_t kEntityPickupVtableOffset = 0xA36E28;
// `Entity_Familiar` 的 vtable 指针（`_ZTVN15IsaacRepentance15Entity_FamiliarE` @ `0xA35718`，
// 加 `0x10` 跳过 "offset-to-top + typeinfo" 两个表头字 ⇒ `0xA35728`）。
// 这条 `+0x10` 规则由既有两个常量交叉验证：`Entity_Player` 符号 `0xA37100` ↔ 本文件
// `0xA37110`；`Entity_Pickup` 符号 `0xA36E18` ↔ `0xA36E28`（都差 `0x10`，一致）。
// 用途：`Entity:ToFamiliar()` 的判据（`Type == ENTITY_FAMILIAR` 且 vptr 精确相等）。
inline constexpr uintptr_t kEntityFamiliarVtableOffset = 0xA35728;
inline constexpr std::uint32_t kEntityTypePlayer = 1;
inline constexpr std::uint32_t kEntityTypePickup = 5;
// `EntityType.ENTITY_FAMILIAR == 3`（PC 枚举；本项目既有的实体分区代码里也用 `type == 3`
// 判"跟班"，且 `variant == 0xEF` 的特例归到敌人那一类）。
inline constexpr std::uint32_t kEntityTypeFamiliar = 3;
inline constexpr std::uint32_t kEntityTypeEffect = 0x3E8;
// `IsEnemy = (u32)(Type - 10) < 0x3DE`（引擎 `EntityList::collide()` 里的同一条比较）。
inline constexpr std::uint32_t kEntityEnemyTypeBase = 10;
inline constexpr std::uint32_t kEntityEnemyTypeSpan = 0x3DE;

// 扫描一个实体需要的字段区间：最远的字段是 `Size`（`+0x344`，4 字节），所以一次可读性检查
// 覆盖 `[entity, entity + 0x348)` 就够，之后的逐字段读取只是 `memcpy`（真机上 `svcQueryMemory`
// 是系统调用，逐字段检查会让一次全房间扫描变成上千次调用）。
inline constexpr std::size_t kEntitySnapshotSpan = 0x348;

// `Entity_Pickup` 字段（`Touched` **未定案**，`+0x560` 只是候选，见注释）。
inline constexpr uintptr_t kEntityPickupOptionsIndexOffset = 0x55C;
inline constexpr uintptr_t kEntityPickupTouchedOffset = 0x560;   // 猜测：u8/bool
inline constexpr uintptr_t kEntityPickupForceBlindOffset = 0x562;  // u8
inline constexpr uintptr_t kEntityPickupPriceOffset = 0x564;       // u32
inline constexpr uintptr_t kEntityPickupShopItemIdOffset = 0x56C;  // u32

// `EntityPlayer.Luck` 的字段偏移（`0x1920`，32 位整数）。
//
// 偏移来源（2026-09-14 真机定位）：反汇编 `Entity_Player::DonateLuck`（模块偏移 `0x2F9978`）：
//     ldr w8, [x0, #0x1920]   ; 读幸运值
//     add w8, w8, w1          ; 加参数（整数加法 ⇒ 字段是 32 位整数）
//     str w8, [x0, #0x1920]   ; 写回
//     b   Entity_Player::EvaluateItems
// Isaac 的幸运值本来就是整数加成，所以按 `int32 → Lua number` 暴露。
//
// 为什么必须实现（不实现会出什么）：EID 在**逐帧渲染路径**上对"幸运值相关道具"调用
// `EID.LuckFormulas[...](player.Luck)`（`features/eid_data.lua` 里 48 条公式）。字段缺失时
// Lua 侧拿到 `nil` ⇒ 算术抛错 ⇒ 那一条渲染回调**当场中断** ⇒ 该帧那片道具的描述全部不画
// （连带同帧后面要画的一起丢）。真机症状：死亡证明房间里"部分底座道具既没文字也没问号"，
// 错误文本为 `eid_data.lua:1215: attempt to perform arithmetic on a nil value (local 'luck')`。
inline constexpr uintptr_t kEntityPlayerLuckOffset = 0x1920;  // int32

// --- `Entity_Player` 的战斗属性与两个状态字段（2026-09-16 静态取证）------------------------
//
// 这四个属性都只以 `ldr s0/str s0`（float）出现；命名链是引擎自己的
// `PlayerUtils::Get{Speed,TearDelay,Damage,Range}Rating(float)`：`PauseScreen::Show @ 0x43274C`
// 连续读这五个偏移做暂停菜单的属性显示（0x194C→速度、0x1834→射速、0x1844→伤害、0x1854→射程、
// 0x1838→弹速），`Entity_Player::AddCollectible @ 0x292A74` 里同一组。默认值也自证：
// `Entity_Player::Init @ 0x278D94` 写 `+0x194C = 1.0f`、`+0x1854 = 260.0f`（= 6.5×40），
// `EvaluateItems @ 0x280804` 里 `+0x1834` 用 10.0f、`+0x1844` 用 3.5f —— 正好是 Isaac 的
// 基础速度/射程/射速/伤害。
//
// `Damage`（float）：
//   * `Entity_Player::GetTearHitParams @ 0x2AD63C`：`0x2AD6D4 ldr s0,[x20,#0x1844]` → 乘倍率后
//     写进 `TearParams::Damage`（`0x2AD6DC str s0,[x26,#0x48]!`）；
//   * `EvaluateItems` 的 CACHE_DAMAGE 分支：先 `str wzr,[x19,#0x1844]` 清零再累加，
//     收尾 `fmov s10,#3.5`（`0x2830FC str s0,[x19,#0x1844]`）。
inline constexpr uintptr_t kEntityPlayerDamageOffset = 0x1844;          // float
// `MoveSpeed`（float）：
//   * `Entity_Player::Init`：`0x279698 mov w8,#0x194c` + `0x27969C mov w9,#0x3f800000`(1.0f)
//     + `0x2796A0 str x9,[x19,x8]`（默认 1.0）；
//   * `Entity_Player::control_movement @ 0x2B14B4`：`0x2B1748 ldr s0,[x19,#0x194c]` → `fmul`/`fadd`。
inline constexpr uintptr_t kEntityPlayerMoveSpeedOffset = 0x194C;        // float
// `MaxFireDelay`（float；**Switch 侧是 float，PC AB+ 文档写 int —— 按 float 读**）：
//   * `EvaluateItems`：`0x28BB0C bl PlayerUtils::GetTearDelayFromRating` → `0x28BB10 str s0,[x19,#0x1834]`；
//   * `Entity_Player::control_shooting @ 0x2B260C`：`0x2B4150 ldr w9,[x19,#0x1834]` → 抄进每个武器
//     （`0x2B4154 str w9,[x8,#0x1c]`，同一段重复 5 次，对应 +0x1758/1760/1768/1770/1778 五个武器槽）。
inline constexpr uintptr_t kEntityPlayerMaxFireDelayOffset = 0x1834;     // float
// `TearRange`（float，原始射程单位，默认 260 = 6.5×40）：
//   * `Entity_Player::Init`：`0x2796B8 mov w8,#0x43820000`(260.0f) → `0x2796BC str w8,[x19,#0x1854]`；
//   * `Entity_Player::FireDelayedBrimstone @ 0x2FB24C`：`0x2FB3C8 ldr s0,[x19,#0x1854]` → 算激光长度；
//   * `Entity_Player::FireTear @ 0x2A991C`：`0x2A9BA8 ldr w8,[x19,#0x1854]` → 写进新子弹的射程字段。
inline constexpr uintptr_t kEntityPlayerTearRangeOffset = 0x1854;        // float
// `CanFly`（**1 字节** bool，0/1）：
//   * `Entity_Player::GetFlyingOffset @ 0x2BB9F0`：`0x2BBA20 mov w8,#0x1954` + `0x2BBA24 ldrb w8,[x19,x8]`
//     + `cbz`（置位时返回 `Vector2(0,-4)` 的飞行视觉偏移）；
//   * `Entity_Player::AddMinisaac @ 0x2AC1F4`：读同一个字节，按"能否飞"给小以撒定运动层 5/3；
//   * `PlayerManager::ReplacePlayer @ 0x4428DC` 把它从旧玩家拷到新玩家。
//   ⚠️ **只改这一格不会立刻改变碰撞层**：决定"能否飞过石头/坑"的是 `+0x1E0`
//   （`Entity::IsFlying @ 0x5C744` = `[+0x1E0] < 4`），它由引擎在缓存重算时按本字段同步
//   —— PC 侧同样要求"只在 `MC_EVALUATE_CACHE` 里改"，行为一致。
inline constexpr uintptr_t kEntityPlayerCanFlyOffset = 0x1954;           // bool(1 byte)
inline constexpr uintptr_t kEntityFlyingLayerOffset = 0x1E0;             // int32（派生量，非 Lua 字段）
// `ControlsCooldown`（**有符号 int32**；EID 会**写**它来抑制按键连发）：
//   * `Entity_Player::UsePill @ 0x2DFC04` 的麻痹药丸分支：`0x2E1C2C mov w8,#0xf0`(240) /
//     `0x2E1C30 mov w9,#0x78`(120) / `csel` / `0x2E1C3C str w8,[x20,#0x404]`；
//   * `Entity_Player::Update @ 0x2BBCDC` 每帧递减到 0（`ldr w8,[x19,#0x404]` / `subs #1` / `b.lt` / `str`）；
//   * `control_shooting @ 0x2B265C`：`ldr w10,[x19,#0x404]` + `cmp #0` + `b.le`（>0 就不让射击）。
//   ⚠️ `+0x408` 是**另一个**字段（`SetShootingCooldown @ 0x2FB994`，取 max），不是本字段。
inline constexpr uintptr_t kEntityPlayerControlsCooldownOffset = 0x404;  // int32

// `Entity_Player` 的主动道具/饰品/婴儿皮肤（"批次 4 顺带确认的小偏移"）。
inline constexpr uintptr_t kEntityPlayerActiveItemOffset = 0x1964;      // 每槽步长 0x1C
inline constexpr uintptr_t kEntityPlayerActiveItemStride = 0x1C;
inline constexpr uintptr_t kEntityPlayerActiveItemChargeOffset = 0x1968;        // slot 基址 +4
inline constexpr uintptr_t kEntityPlayerActiveItemSecondChargeOffset = 0x196C;  // slot 基址 +8
inline constexpr uintptr_t kEntityPlayerActiveItemMaxChargeOffset = 0x197C;     // slot 基址 +0x18
inline constexpr uintptr_t kEntityPlayerTrinketOffset = 0x1AB0;         // 每槽步长 4，共 2 槽
inline constexpr std::size_t kEntityPlayerTrinketSlotCount = 2;
inline constexpr uintptr_t kEntityPlayerBabySkinOffset = 0x20E8;       // signed，非婴儿 = -1

// `Level` **内嵌在 `Game` 起始处**（见 `docs/问题与解决记录.md` 批次 4 的"更正旧记录"：
// `Level+0x21550` 与 `Game+0x21550` 是同一个地址），所以 `Level* == Game*`，
// `Level:GetCurses()` 读的就是 `Game + 0x0C`（u32 位掩码）。
inline constexpr uintptr_t kLevelCursesOffset = 0x0C;

// `ItemPool`：PC Lua 的 `GetLastPool()` 在 Switch NRO 里没有同名 C++ 访问器，属于字段直读。
// `ItemPool::GetCollectible` 在 `0x3C64C0`、`0x3C66E8` 都把最终选用的池类型写入
// `[ItemPool + 0x8C0]`；`get_chaos_pool()` 返回该池后立即写回，证明这里保存的就是最近一次
// `GetCollectible` 实际使用的池。
inline constexpr uintptr_t kItemPoolLastPoolOffset = 0x8C0;

// 药丸的两张并行小表（批次 13，2026-09-16）：PC Lua 的 `ItemPool:IsPillIdentified(PillColor)`
// 就是读第二张表的第 color 个字节。三条互相独立的指令作证：
//   * 读表一：`ItemPool::GetPillEffect(ePillColor, Entity_Player*) @ 0x3C857C` 里
//     `and w8, w1, #0x7ff` → `add x8, x0, w8, uxtw #2` → `ldr w20, [x8, #0xa2c]`
//     —— 按颜色取药丸效果，颜色先被 **0x7ff** 掩码（IsaacDocs 的 `PILL_COLOR_MASK`）；
//   * 表二的语义锚点：`HUD::PlayerHUD::RenderPocketItems @ 0x3A6094` 在 0x3A6608 处
//     `ldrb w9, [Game + 0x24D28 + color]`，非 0 就显示药丸真名，为 0 就显示字符串表里的
//     `#QUESTION_MARKS_NAME`（"???"）—— `Game + 0x242C0` 正是 `ItemPool`
//     （见 `game_observer.cpp` 的 `kGameItemPoolOffset`），所以那个字节就是"这个颜色认不认得"；
//   * 表二的写入：`ItemPool::RestoreGameState @ 0x3CA1A0` 把存档里的 15 组
//     `[state+0x3b0+4i] → [this+0xa2c+4i]`（药丸效果）与 `[state+0x3ec+i] → [this+0xa68+i]`
//     （识别位）逐条展开写回；`ItemPool::RerollPillEffect(ePillColor, uint, bool) @ 0x3CAA7C`
//     也在重掷时写 `[this+0xa68+color] = 第三个参数 & 1`。
// 数量 15 = IsaacDocs `enums/PillColor.md` 的 `NUM_PILLS`（普通药丸 0..13 + 金色药丸 14），
// 与"0xa2c 起 15×4 字节后正好是 0xa68"这条算术完全吻合。
inline constexpr uintptr_t kItemPoolPillEffectOffset = 0xA2C;
inline constexpr uintptr_t kItemPoolPillIdentifiedOffset = 0xA68;
inline constexpr std::uint32_t kPillColorCount = 15;
inline constexpr std::uint32_t kPillColorMask = 0x7FF;

// --- ItemConfig（2026-09-12 反汇编 + 真机现象逐位互证）--------------------------------
//
// 陷阱（上一轮探针失败的根因）：`ItemConfig` **内嵌**在 `Manager` 里，**不存在指向它的指针**。
// 正确链路是"取地址"，不是"取值"：
//
//   mslot = *(u64*)(base + kGameManagerGlobalSlotOffset)   // 槽内容 = base + 0xABCCE0
//   M     = *(u64*)mslot                                   // Manager*（两级解引用）
//   IC    = M + kManagerItemConfigOffset                   // ★加法：内嵌对象
//
// 反汇编硬证据（`tools/nro_disasm.py`，build `91C73FDD…`）：
//   * `ItemConfig::IsValidCollectible(eCollectibleType)`（`0x3C0D64`）自己就是
//     `ldr x8,[x8,#0x648]`（`g_Manager`）→ `ldr x8,[x8]` → `add x8, x8, #0x36538`，
//     即同一枚"加法"形态；`mov w9,#0x6538; movk w9,#0x3,lsl #16` 就是 0x36538。
//   * `Manager::Manager`（`0x3F3B00`）在 `0x3F3C58` 处
//     `mov w8,#0x6538; movk w8,#0x3,lsl #16; add x0,x19,x8; bl ItemConfig::ItemConfig()`
//     —— `x19` 就是 `Manager` 自身，**加法**组成地址；紧随其后 `0x3F3C68` 的
//     `mov w8,#0x6740 …`（下一个成员）证明 `ItemConfig` 占 `[0x36538, 0x36740)`，
//     即 `sizeof(ItemConfig) == 0x208`（`EntityConfig` 的构造在 `+0x36758`）。
//   * 把 `*(M + 0x36538)` 当指针用会读到 `collectibles.begin()`（真机观测 `0x3954852000`），
//     随后 `items[0]==0` 时的 `*(u64*)8` 又被可读性检查拦掉 —— 与旧报告"第 6 位不亮"逐位吻合。
inline constexpr uintptr_t kManagerItemConfigOffset = 0x36538;

// 并列向量（同一个 `IC` 上，全部是 `std::vector<ItemConfig::Item*>`：begin/end 相邻两个 u64，
// 步长 8）。偏移逐个来自各自 getter 的第一条 `ldp`（符号名即 getter 名）：
//   * `0x3C0C50 ItemConfig::GetCollectible(int) const`   → `ldp x8,x9,[x0]`      → +0x00/+0x08
//   * `0x3C0C9C ItemConfig::GetTrinket(int) const`       → `ldp x9,x10,[x0,#0x18]`→ +0x18/+0x20
//   * `0x3C0CC4 ItemConfig::GetNullItem(int) const`      → `ldp x8,x9,[x0,#0x30]` → +0x30/+0x38
//   * `0x3C0CEC ItemConfig::GetPillEffect(ePillEffect)`  → `ldp x8,x9,[x0,#0x60]` → +0x60/+0x68
//   * `0x3C0D14 ItemConfig::GetCard(int) const`          → `ldp x8,x9,[x0,#0x48]` → +0x48/+0x50
//   * `0x3C0D3C ItemConfig::GetPlayerForm(ePlayerForm)`  → `ldp x8,x9,[x0,#0x90]` → +0x90/+0x98
// 每个 getter 的其余三条指令都是同一形态：`sub end,begin` / `lsr #3` / `cmp` + `b.le` →
// 越界返回空指针，然后 `ldr x0,[begin, w1, uxtw #3]`。所以"条目就是向量元素"是被反汇编证实的。
//
// 数量硬判据：`ItemConfig::Init`（`0x3B4138`）先 `cmp x10, #0x2dc; b.hi`，否则
// `mov w8, #0x2dd`（733）后 `vector::__append(733 - n)`；另一支 `mov w10, #0x16e8`（5864）
// 直接比较字节长度。**733 × 8 == 0x16E8** 就是收藏品向量的长度硬判据（真机探针用它）。
// 顺带：`Init` 里 Trinket 向量（`IC+0x18`）预分配 0xBE == 190 项（0x5F0 字节）。
inline constexpr uintptr_t kItemConfigGetCollectibleOffset = 0x3C0C50;
inline constexpr uintptr_t kItemConfigCollectibleCount = 733;
inline constexpr uintptr_t kItemConfigCollectibleVectorBytes = 0x16E8;  // 733 × 8
inline constexpr uintptr_t kItemConfigTrinketCount = 190;               // 0xBE，`Init` 预分配
inline constexpr uintptr_t kItemConfigCollectibleBeginOffset = 0x00;
inline constexpr uintptr_t kItemConfigCollectibleEndOffset = 0x08;
inline constexpr uintptr_t kItemConfigTrinketBeginOffset = 0x18;
inline constexpr uintptr_t kItemConfigTrinketEndOffset = 0x20;
inline constexpr uintptr_t kItemConfigNullItemBeginOffset = 0x30;
inline constexpr uintptr_t kItemConfigNullItemEndOffset = 0x38;
inline constexpr uintptr_t kItemConfigCardBeginOffset = 0x48;
inline constexpr uintptr_t kItemConfigCardEndOffset = 0x50;
inline constexpr uintptr_t kItemConfigPillEffectBeginOffset = 0x60;
inline constexpr uintptr_t kItemConfigPillEffectEndOffset = 0x68;
inline constexpr uintptr_t kItemConfigPlayerFormBeginOffset = 0x90;
inline constexpr uintptr_t kItemConfigPlayerFormEndOffset = 0x98;

// `ItemConfig::Item`：**`Type`（`eItemType`）**、id、以及 libc++ `std::string` 形态的名字与
// 描述——**不是 `const char*`**，读取要按 SSO 规则（byte0 的 bit0 是 is_long）。
//
// ★ 2026-09-14 重新定案：`+0x00` 是 PC 的 `ItemConfig_Item.Type`（`eItemType`），
//   **不是**"向量类别"。三条独立证据（都在本文件提到的 build `91C73FDD…` 上）：
//   1. `items.xml` 解析器（`ItemConfig::Load`，`0x3B50E0`）把 `type` 属性**原样**写进 `+0x00`：
//      `"passive"`→`str w8(=1),[x27]`（`0x3B57B8`）、`"trinket"`→2（`0x3B57DC`）、
//      `"active"`→3（`0x3B57F8`）、`"familiar"`→4（`0x3B581C`）、`"null"`→0（`0x3B5840`）。
//      这 5 个值恰好是冻结文档 `enums/ItemType.md` 的 `ITEM_NULL/PASSIVE/TRINKET/ACTIVE/FAMILIAR`。
//   2. `ItemConfig::Item::GetSoulChargeType()`（`0x3C19C0`）先 `ldr w9,[x0]` 再 `cmp w9,#0x3`
//      —— 只有"主动道具"（`ITEM_ACTIVE == 3`）才继续算充能类型。
//   3. `ItemConfig::Item::TemporaryEffectsAreInstances()`（`0x3C1804`）用 `1 << [x0]` 与
//      `0x1A`（bit 1/3/4）取交集，即该字段只取 {1,3,4} 这类小枚举值。
//   `id` 仍是 `+0x04`（同一解析器写 `"id"`，且 `0x3C19D0` 用它比 `0x146`/`0x28F` 两个收藏品 id）。
//   历史遗留：本常量曾叫 `kItemConfigItemKindOffset` 并被当成"1=COLLECTIBLE"，
//   于是 `ItemConfig:IsCollectible(id)` 曾要求 `Type == ITEM_PASSIVE`（见 `isaac_api.cpp`）。
//   偏移来源：`ItemConfig::Item::GetDisplayName(eLanguage)`（`0x3B9334`）的
//   `ldrb w8, [x21, #0x8]!`（前索引寻址直接落在 `+0x08`）与 `lsr x9, x8, #1`（短串 size = byte0>>1）；
//   `ItemConfig::Item::GetDisplayDescription(eLanguage)`（`0x3C0F08`）的
//   `ldrb w8, [x21, #0x20]!` → 描述在 `+0x20`。
inline constexpr uintptr_t kItemConfigItemTypeOffset = 0x00;
inline constexpr uintptr_t kItemConfigItemIdOffset = 0x04;
inline constexpr uintptr_t kItemConfigItemNameOffset = 0x08;
inline constexpr uintptr_t kItemConfigItemDescriptionOffset = 0x20;
// `GfxFileName`（PC 文档 `ItemConfig::Item::GfxFileName`，LuaDocs 里是 `string` 字段）在 `+0x38`。
// 偏移来源是 `items.xml` 解析器（`ItemConfig::Load`，`0x3B50E0`）自己的 `gfx` 属性分支：
//   * `0x3B58B4` 用 `strcmp(name, "gfx")` 命中后 `cbz` 跳到 `0x3B5B20`；
//   * 该分支先按 `Type` 选前缀 —— `Type == 2` 走 `"trinkets/"`（`0x3B5C50`），否则走
//     `"collectibles/"`（`0x3B5C9C`）—— 再把 XML 里的值 `append` 上去拼成完整路径；
//   * 两条路在 `0x3B5CE0..0x3B5D10` 汇合，把结果**整体写进 `[x27, #0x38]`**：
//     byte0 → `+0x38`、byte1 → `+0x39`、byte2..9 → `[sp,#0x60]`（= `x27+0x3A`，见 `0x3B5854`）、
//     长串数据指针 → `+0x48`，旧的堆块按 byte0 的 bit0 释放（`0x3B5CE4`/`0x3B5CEC`）。
// 同一段里 `description` → `+0x20`（`0x3B585C` 的 `add x8, x27, #0x20`）、`name` → `+0x08`
// （`0x3B5860` 的 `add x21, x27, #0x8`），与上面两条常量互为交叉验证。
// 形态与 `Name`/`Description` 相同：24 字节 libc++ 串占 `+0x38`..`+0x4F`，数据指针在 `+0x48`。
inline constexpr uintptr_t kItemConfigItemGfxFileNameOffset = 0x38;
// `Quality`（PC 文档 `ItemConfig_Item.Quality`，LuaDocs 里是 `int` 字段）在 `+0xC8`；
// `CraftingQuality`（同族，`ItemConfig_Item.CraftingQuality`）在 `+0xCC`。
//
// 问什么要这两个：EID 拿 `EID.itemConfig:GetCollectible(id).Quality` 算道具品质
// （`features/eid_api.lua:2702`），`main.lua:663` 用它拼 `{{QualityN}}` 图标；读不到 nil 时
// 被 `and desc.Quality` 短路 ⇒ **品质图标静默不显示**（不报错、无日志）。这正是
// "缺口只统计过方法、字段从没被枚举过"的典型案例，见 `docs/错误复盘.md` 2026-09-16。
//
// 偏移来源与 `gfx` 同一条链 —— `items.xml` 解析器 `ItemConfig::Load`（`0x3B50E0`）的属性分派：
//   * `0x3B5A64` `adrp x1, 0x891000` + `0x3B5A68` `add x1, x1, #0x642`（=> `0x891642` = `"quality"`）
//     `strcmp` 命中后 `0x3B5A70` 的 `cbz w0` 跳到 `0x3B623C`；该分支 `bl atoi`（`0x3B6248`）之后
//     就是 **`str w0, [x27, #0xc8]`（`0x3B624C`）** ⇒ `Quality` = 32 位整数，落在 `+0xC8`；
//   * `0x3B5A78` `adrp x1, 0x8AA000` + `0x3B5A7C` `add x1, x1, #0x1a9`（=> `0x8AA1A9` = `"craftquality"`）
//     命中后跳到 `0x3B62A0`，同样是 `atoi` 之后 **`str w0, [x27, #0xcc]`（`0x3B62B0`）**
//     ⇒ `CraftingQuality` = `+0xCC`。
//   * **互证**：同一函数的收尾段 `0x3B6308..0x3B631C` 先 `ldr w8,[x27,#0xcc]` 与哨兵
//     `0x8FFFFFFF`（`mov w9, #-0x70000001`）比较，相等就把 `ldr w8,[x27,#0xc8]` 写回 `+0xcc`
//     ⇒ **`items.xml` 没写 `craftquality` 时默认等于 `quality`**，与 PC 文档语义一致，
//     两个偏移互相印证（这一条也说明实现里不该自己编"缺省值"）。
//   * 与既有常量同源：同一分派链里 `id → +0x04`（`0x3B5AE8` 的 `str w0, [x27, #0x4]`），
//     布尔类属性走 `string2bool` 后写**单字节**（`0x3B6234` 的 `strb w8, [x27, #0xb7]`）
//     —— 两个 32 位整数落在 `+0xC8`/`+0xCC` 这一段，与它们都不冲突。
inline constexpr uintptr_t kItemConfigItemQualityOffset = 0xC8;
inline constexpr uintptr_t kItemConfigItemCraftingQualityOffset = 0xCC;
// `CraftingQuality` 在 `items.xml` 未指定时的哨兵值（见上面 `0x3B630C` 的 `mov w9, #-0x70000001`）。
inline constexpr std::uint32_t kItemConfigItemCraftingQualityUnset = 0x8FFFFFFFu;

// 同一张 `items.xml` 属性分派表里的另外四个字段（2026-09-16 批次：字段缺口台账的 planned 项）。
//
// 取证方法与 `quality` 完全一样 —— 从 `ItemConfig::Load`（`0x3B50E0`）的 `strcmp` 链往下看
// 每个属性分支落点的那条 `str`：
//   * `achievement`：`0x3B58C4` 的 `strcmp` 命中 → `0x3B5B78`；`bl atoi`（`0x3B5B84`）之后
//     **`str w0, [x27, #0x50]`（`0x3B5B88`）** ⇒ `AchievementID` = `+0x50`；
//   * `tags`：`0x3B5A90` 的 `strcmp` 命中 → `0x3B5D48`；`bl atoi`（`0x3B5D54`）之后
//     **`str w0, [x27, #0x58]`（`0x3B5D58`）** ⇒ `Tags` = `+0x58`；
//   * `maxcharges`：`0x3B5978` 的 `strcmp` 命中 → `0x3B5E50`；`bl atoi`（`0x3B5E5C`）之后
//     **`str w0, [x27, #0x74]`（`0x3B5E60`）** ⇒ `MaxCharges` = `+0x74`；
//   * `chargetype`：`0x3B59F0` 的 `strcmp` 命中 → `0x3B5F60`，该分支**先按字符串取枚举**：
//     `"normal"` → `0x3B6038` 的 **`str wzr, [x27, #0xb0]`**（=0）、`"timed"` → `0x3B60C8`
//     （=1）、`"special"` → `0x3B615C` 的 `mov w8,#2; str w8,[x27, #0xb0]`（=2）
//     ⇒ `ChargeType` = `+0xB0`，且三个取值**正好等于** PC 的
//     `ItemConfig.CHARGE_NORMAL(0) / CHARGE_TIMED(1) / CHARGE_SPECIAL(2)`
//     （`analysis/isaacdocs-snapshot/docs/enums/ItemConfig.md:22-24`）—— 三值互证，不是按顺序猜的。
//
// 类型按 PC 文档定（`analysis/isaacdocs-snapshot/docs/ItemConfig_Item.md`）：
//   * `AchievementID` 是 **int** 且"默认可解锁时返回 -1" ⇒ 必须**有符号**读，否则 -1 会变成 4294967295，
//     EID 的 `item.AchievementID == -1`（`features/eid_api.lua:2003`）就永远不成立；
//   * `MaxCharges` / `ChargeType` 也是 int ⇒ 有符号读；
//   * `Tags` 文档写 int，但语义是**位掩码**（EID 用 `item.Tags & ItemConfig.TAG_QUEST`）⇒ 按
//     无符号读（零扩展），这样第 31 位不会被符号扩展污染成高 32 位全 1。
inline constexpr uintptr_t kItemConfigItemAchievementIdOffset = 0x50;
inline constexpr uintptr_t kItemConfigItemTagsOffset = 0x58;
inline constexpr uintptr_t kItemConfigItemMaxChargesOffset = 0x74;
inline constexpr uintptr_t kItemConfigItemChargeTypeOffset = 0xB0;
// `Hidden`（2026-09-16，字段缺口台账 planned 里证据最足的一条）：
//   * `bool`，**1 字节**（`ldrb`，不是 4 字节整数）；
//   * 证据：`ItemConfig::Item::IsAvailable`（`0x3C101C`，见下面那组常量）的**第一条指令**就是
//     `ldrb w8, [x0, #0xb7]`，随后非零直接返回 false —— 与 PC 文档
//     （`ItemConfig_Item.md`：`boolean Hidden`；"HIDDEN 的道具不会出现在死亡证明房间"）吻合；
//   * EID 用法：`features/eid_api.lua:2008` 的 `if item.Hidden then`（隐藏道具不出描述）与
//     `eid_api.lua:1927` Spindown Dice 预测里的 `not item.Hidden`（缺它这一半恒真）。
inline constexpr uintptr_t kItemConfigItemHiddenOffset = 0xB7;
// `MimicCharge`（卡牌 / 胶囊各一个，2026-09-16 取证）。EID 用它给"模仿胶囊/卡牌"补一句说明
// （`features/eid_modifiers.lua:731`）。两个结构都由 `ItemConfig::LoadPocketItems @ 0x3BE42C`
// 从 cards.xml / pills.xml 建出来，"XML 属性名 → 结构体偏移"在指令层面直接可读：
//   * `"mimiccharge"` 这一支在 `0x3BE96C-0x3BE978`（`add x1,x1,#0xf16` + `strcmp` + `cbz`），
//     处理体 `0x3BED08 bl atoi` → `0x3BED0C str w0,[sp,#0x68]`（该栈槽整函数只有这一处属性写入）；
//   * 卡：`0x3BEE88 str w8,[x23,#0x64]`（x23 = `operator new(0x78)` 出来的 Card）；
//   * 胶囊：`0x3BEF5C stp w9,w8,[x23,#0x44]`（x23 = `operator new(0x58)`；STP 先写低地址 ⇒
//     w9→+0x44 announcerdelay、**w8→+0x48 mimiccharge**）。
// 消费侧（第二处独立证据）：`Entity_Player::UseActiveItem @ 0x30653C` 在
//   * `0x3106E8 ldr w9,[x0,#0x64]` → `0x3106EC str w9,[x8,#0x197C]`（卡；前置 `cmp #0x107` = CLEAR_RUNE、
//     且卡类型 `[card+0x58]==2` = rune）；
//   * `0x312A4C ldr w9,[x0,#0x48]` → `0x312A50 str w9,[x8,#0x197C]`（胶囊；前置 `cmp #0x15c` = PLACEBO）。
// 类型：写入来自 `atoi`、读出用 `ldr w` ⇒ 4 字节 int。
inline constexpr uintptr_t kItemConfigCardMimicChargeOffset = 0x64;        // ItemConfig::Card
inline constexpr uintptr_t kItemConfigPillEffectMimicChargeOffset = 0x48;  // ItemConfig::PillEffect
// `eChargeType`（同一份 enums 文档）：`0x3B6038`/`0x3B60C8`/`0x3B615C` 三个落点分别写 0/1/2。
inline constexpr std::int32_t kItemChargeTypeNormal = 0;
inline constexpr std::int32_t kItemChargeTypeTimed = 1;
inline constexpr std::int32_t kItemChargeTypeSpecial = 2;

// --- `IsAvailable` 一族（2026-09-16，取证见 `docs/问题与解决记录.md` 续十二）------------------
//
// **地址以动态符号表为准**：交接文档早先记的 `0x3C0F9C` / `0x3C1B5C` / `0x3C1C48` 是错的
// （各偏低 0x80 / 0x140 / 0x100），实测如下三条：
//   * `ItemConfig::Item::IsAvailable(long, uint)` const  @ `0x3C101C`
//   * `ItemConfig::Card::IsAvailable()` const            @ `0x3C1C9C`
//   * `ItemConfig::PillEffect::IsAvailable()` const      @ `0x3C1D48`
inline constexpr uintptr_t kItemConfigItemIsAvailableOffset = 0x3C101C;
inline constexpr uintptr_t kItemConfigCardIsAvailableOffset = 0x3C1C9C;
inline constexpr uintptr_t kItemConfigPillEffectIsAvailableOffset = 0x3C1D48;
// 入口 4 字节按小端整字存（与 `kEntityPlayerAddCollectibleEntryWord` 同口径），调用前核对一次。
// ⚠️ Card 与 PillEffect 两条**序言同构**（都是 `stp x29,x30,[sp,#-0x20]!`）⇒ 指纹只能证明
// "这里是一条符合预期的序言"；**区分谁是谁靠句柄里的 `beginOffset`**，不靠指纹。
inline constexpr std::uint32_t kItemConfigItemIsAvailableEntryWord = 0xD10143FFU;
inline constexpr std::uint32_t kItemConfigCardIsAvailableEntryWord = 0xA9BE7BFDU;
inline constexpr std::uint32_t kItemConfigPillEffectIsAvailableEntryWord = 0xA9BE7BFDU;
// `Item::IsAvailable` 的 flags 取值 —— **这是我们的兼容层定义**（三个函数在镜像里没有任何调用点，
// 参数无法从游戏自己的用法学到）。依据两条：
//   ① PC 文档 `analysis/isaacdocs-snapshot/docs/ItemConfig_Item.md:35-38`：
//      "true = 已解锁；false = 没解锁 **或被 tags 挡掉**"；
//   ② `0x3C101C` 反汇编读出的位语义：bit1 = 成就解锁（读 `+0x50` 的 `AchievementID`，-1 直接 true）、
//      bit2 = tags/当前局阻挡、bit3 = 模组提供的物品、bit0 = 按 `Type` 分流。
// 已按此在 `api_deviation.cpp` 登记偏离（含未覆盖的 PC 语义）。
inline constexpr std::int64_t kItemIsAvailableFlags = 0x2 | 0x4 | 0x8;

// `eItemType`（冻结文档 `analysis/isaacdocs-snapshot/docs/enums/ItemType.md`）。
inline constexpr std::uint32_t kItemTypeNull = 0;
inline constexpr std::uint32_t kItemTypePassive = 1;
inline constexpr std::uint32_t kItemTypeTrinket = 2;
inline constexpr std::uint32_t kItemTypeActive = 3;
inline constexpr std::uint32_t kItemTypeFamiliar = 4;
inline constexpr uintptr_t kGameIsPausedThunkOffset = 0x671100;
inline constexpr std::array<u8, 16> kGameIsPausedThunkExpectedBytes = {
    0x70, 0x21, 0x00, 0xB0, 0x11, 0xF6, 0x43, 0xF9,
    0x10, 0xA2, 0x1F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// Manager input-action trampolines. Each loads the Manager singleton itself and zeroes the
// trailing `Entity*` argument, so the Runtime can call them with the two arguments the PC
// Lua API takes. Offsets and guards come from the game's own dynamic symbol table (the NRO
// offset equals the loaded module offset, verified against previously recorded guards), and
// the guard bytes are what the Hook installation checks before publishing the binding.
inline constexpr uintptr_t kManagerIsActionPressedOffset = 0x3F9B6C;
inline constexpr std::array<u8, 16> kManagerIsActionPressedExpectedBytes = {
    0x80, 0x35, 0x00, 0xF0, 0x00, 0xB4, 0x43, 0xF9,
    0xE3, 0x03, 0x1F, 0xAA, 0x86, 0xFB, 0x09, 0x14,
};
inline constexpr uintptr_t kManagerIsActionTriggeredOffset = 0x3F9B7C;
inline constexpr std::array<u8, 16> kManagerIsActionTriggeredExpectedBytes = {
    0x80, 0x35, 0x00, 0xF0, 0x00, 0xB4, 0x43, 0xF9,
    0xE3, 0x03, 0x1F, 0xAA, 0x62, 0xDE, 0x09, 0x14,
};
inline constexpr uintptr_t kManagerGetActionValueOffset = 0x3F9B8C;
inline constexpr std::array<u8, 16> kManagerGetActionValueExpectedBytes = {
    0x80, 0x35, 0x00, 0xF0, 0x00, 0xB4, 0x43, 0xF9,
    0xE3, 0x03, 0x1F, 0xAA, 0xFA, 0x0B, 0x0A, 0x14,
};

// `KAGE::Graphics::Font` -- PC Lua's `Font` type (Stage148 audit:
// `analysis/stage148-font-abi/91C73FDD575061318D68886316AFEAC72388B2AB.json`). Every entry
// point below is a module-local definition resolved from the game's own dynamic symbol table,
// and the guard bytes are what the Hook installation checks before publishing the binding.
//
// Unlike Game/Level/Room, the Runtime allocates Font instances itself: `~Font()` only releases
// the Font's internal buffers (it never deletes `this` and never unregisters a global), so the
// destructor is a tail-jump to `Unload()` and the object's storage belongs to us. `Font` has no
// vptr (`+0x00` is the `IsLoaded` flag) and must never be copied by value.
inline constexpr std::size_t kFontObjectSize = 0x20050;
inline constexpr uintptr_t kFontCtorOffset = 0x4CDE20;
inline constexpr std::array<u8, 16> kFontCtorExpectedBytes = {
    0xC8, 0x09, 0x80, 0x52, 0x48, 0x00, 0xA0, 0x72,
    0x1F, 0x00, 0x00, 0x39, 0x1F, 0x40, 0x00, 0xB9,
};
// The destructor's guard window deliberately spans into `Unload()`, because `~Font()` is a
// single `b Unload` tail-jump followed by Unload's prologue.
inline constexpr uintptr_t kFontDestructorOffset = 0x4CE764;
inline constexpr std::array<u8, 16> kFontDestructorExpectedBytes = {
    0xF7, 0xB1, 0x06, 0x14, 0xFD, 0x7B, 0xBC, 0xA9,
    0xF8, 0x5F, 0x01, 0xA9, 0xFD, 0x03, 0x00, 0x91,
};
inline constexpr uintptr_t kFontLoadOffset = 0x4CDEB8;
inline constexpr std::array<u8, 16> kFontLoadExpectedBytes = {
    0xFD, 0x7B, 0xBA, 0xA9, 0xFC, 0x6F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xFA, 0x67, 0x02, 0xA9,
};
inline constexpr uintptr_t kFontUnloadOffset = 0x4CE768;
inline constexpr std::array<u8, 16> kFontUnloadExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF8, 0x5F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
inline constexpr uintptr_t kFontIsLoadedOffset = 0x4CE898;
inline constexpr std::array<u8, 16> kFontIsLoadedExpectedBytes = {
    0x00, 0x00, 0x40, 0x39, 0xC0, 0x03, 0x5F, 0xD6,
    0xFD, 0x7B, 0xBC, 0xA9, 0xF7, 0x0B, 0x00, 0xF9,
};
inline constexpr uintptr_t kFontGetStringWidthOffset = 0x4CE9BC;
inline constexpr std::array<u8, 16> kFontGetStringWidthExpectedBytes = {
    0x2C, 0x00, 0x40, 0x39, 0xCC, 0x08, 0x00, 0x34,
    0xC9, 0x09, 0x80, 0x52, 0x49, 0x00, 0xA0, 0x72,
};
inline constexpr uintptr_t kFontGetStringWidthUTF8Offset = 0x4CEE8C;
inline constexpr std::array<u8, 16> kFontGetStringWidthUTF8ExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF5, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};
inline constexpr uintptr_t kFontGetLineHeightOffset = 0x4CEEE0;
inline constexpr std::array<u8, 16> kFontGetLineHeightExpectedBytes = {
    0x00, 0x28, 0x40, 0x79, 0xC0, 0x03, 0x5F, 0xD6,
    0x00, 0x2C, 0x40, 0x79, 0xC0, 0x03, 0x5F, 0xD6,
};
inline constexpr uintptr_t kFontGetBaselineHeightOffset = 0x4CEEE8;
inline constexpr std::array<u8, 16> kFontGetBaselineHeightExpectedBytes = {
    0x00, 0x2C, 0x40, 0x79, 0xC0, 0x03, 0x5F, 0xD6,
    0xFF, 0x43, 0x01, 0xD1, 0xE9, 0x23, 0x01, 0x6D,
};
inline constexpr uintptr_t kFontGetCharacterWidthOffset = 0x4CE914;
inline constexpr std::array<u8, 16> kFontGetCharacterWidthExpectedBytes = {
    0x08, 0x84, 0x21, 0x8B, 0x08, 0xA1, 0x40, 0x79,
    0xE9, 0xFF, 0x9F, 0x52, 0x1F, 0x01, 0x09, 0x6B,
};
inline constexpr uintptr_t kFontSetMissingCharacterOffset = 0x4CFB74;
inline constexpr std::array<u8, 16> kFontSetMissingCharacterExpectedBytes = {
    0xC8, 0x09, 0x80, 0x52, 0x48, 0x00, 0xA0, 0x72,
    0x01, 0x68, 0x28, 0x78, 0xC0, 0x03, 0x5F, 0xD6,
};

// `Font::DrawString(text, x, y, Color const&, boxWidth, center)`: a thin wrapper that copies
// the colour and calls `DrawStringScaled(text, x, y, 1.0f, 1.0f, &copy, boxWidth, center)`.
// The `Color` argument travels as a pointer (the class has a user-defined copy constructor,
// so it is passed by invisible reference), which is what lets the Lua layer hand it a 16-byte
// RGBA block of its own. No explicit batch handling exists here: KAGE's graphics manager owns
// the batching, so the open question for hardware is whether drawing from the Runtime's render
// callback lands inside a live batch.
inline constexpr uintptr_t kFontDrawStringOffset = 0x4CEEF0;
inline constexpr std::array<u8, 16> kFontDrawStringExpectedBytes = {
    0xFF, 0x43, 0x01, 0xD1, 0xE9, 0x23, 0x01, 0x6D,
    0xFD, 0x7B, 0x02, 0xA9, 0xFD, 0x83, 0x00, 0x91,
};
// 三个绘制变体（同一类，紧邻 `DrawString`）：坐标走 s0/s1，缩放的
// `ScaleX`/`ScaleY` 走 s2/s3，颜色仍走整数寄存器 x2（不可见引用），w3 = boxWidth、w4 = center。
// 已逐条反汇编核对：`DrawStringScaledUTF8` 先 `mov v8..v11` 收下 v0..v3、`mov w20/w19` 收下 w3/w4、
// `mov x21, x2` 收下颜色指针，与 `DrawString` 的形态完全一致（EID 用的就是 ScaledUTF8）。
inline constexpr uintptr_t kFontDrawStringScaledOffset = 0x4CEF6C;
inline constexpr std::array<u8, 16> kFontDrawStringScaledExpectedBytes = {
    0xFF, 0x43, 0x03, 0xD1, 0xEC, 0x2B, 0x00, 0xFD,
    0xEB, 0x2B, 0x06, 0x6D, 0xE9, 0x23, 0x07, 0x6D,
};
inline constexpr uintptr_t kFontDrawStringUTF8Offset = 0x4CF2A4;
inline constexpr std::array<u8, 16> kFontDrawStringUTF8ExpectedBytes = {
    0xFF, 0x83, 0x01, 0xD1, 0xE9, 0x23, 0x01, 0x6D,
    0xFD, 0x7B, 0x02, 0xA9, 0xFD, 0x83, 0x00, 0x91,
};
inline constexpr uintptr_t kFontDrawStringScaledUTF8Offset = 0x4CFAB8;
inline constexpr std::array<u8, 16> kFontDrawStringScaledUTF8ExpectedBytes = {
    0xFF, 0xC3, 0x01, 0xD1, 0xEB, 0x2B, 0x01, 0x6D,
    0xE9, 0x23, 0x02, 0x6D, 0xFD, 0x7B, 0x03, 0xA9,
};

// `IsaacRepentance::ANM2` 的对象尺寸 = **0x158（344 字节）**，两种独立方法互证
// （工具 `tools/stage155_anm2_size_audit.py`，报告 `analysis/stage155-anm2-size/stride.json`）：
//   1. 游戏自己在 `AnmCache::CreateGlobalAnim()` 里就是 `mov w0,#0x158` → `bl operator new`
//      → `bl ANM2::ANM2()`（`0x1793C/0x17940/0x17948`）；
//   2. 同一父对象里连续构造的三个 ANM2 成员位移是 `0x5028`/`0x5180`/`0x52D8`，步长正好 0x158
//      （`0x1B570`–`0x1B590`）。
// 我们按 16 字节对齐自己分配这块内存（尺寸由上面的证据定案），构造用游戏的 ctor、析构用
// `kSpriteDestructorOffset`（D1 不释放内存，游戏自己在 `0xEC38 → 0xEC40` 里紧跟 `operator delete`）。
inline constexpr std::size_t kSpriteObjectSize = 0x158;
inline constexpr std::size_t kSpriteObjectAlignment = 16;
// `ANM2+0x149` 是"图形是否加载完成"的标志；为 0 时 `Render`/`Update` 是安全空操作（stage150 审计）。
inline constexpr std::size_t kSpriteLoadedFlagOffset = 0x149;

// `Entity+0x50` = 内嵌的 `ANM2`（PC Lua 的 `Entity:GetSprite()`）。两条独立指令序列互证
// （都在 `Repentance.nro` 的 `.dynsym` 里，用 `tools/nro_disasm.py` 复现）：
//   1. **构造函数** `Entity::Entity()`（`0x588ac`）：
//        `900052a8 adrp x8, 0xaac000 ; _ZTVN15IsaacRepentance6EntityE`
//        `a8857c08 stp  x8, xzr, [x0], #0x50`  ← **后索引写**：vptr → `this+0`、0 → `this+8`，
//                                                 然后把 x0 前移 0x50
//        `94185c8b bl   0x66fb00 ; import _ZN15IsaacRepentance4ANM2C1Ev`  ← 用 `this+0x50` 当 this
//      内嵌成员没有"指针字段"，构造出来的对象就在 `this+0x50` 上。
//   2. **渲染** `Entity::Render(Vector2 const&)`（`0x624fc`）：
//        `91014260 add x0, x19, #0x50` → `bl 0x6721d0 ; import ANM2::BeginBatches()`
//        `91014260 add x0, x19, #0x50` → `bl 0x66fcf0 ; import ANM2::Render(Vector2 const&, …)`
//   3. **派生类的贴图装载** `Entity_Pickup::ReloadGraphics(bool)`（`0x26a97c`）：给收藏品底座换
//      spritesheet 的那几处全是 `add x0/x20, x19, #0x50` → `ANM2::ReplaceSpritesheet(int,
//      std::string const&)`（例如 `gfx/Items/Pick Ups/...`）。也就是说 `Entity_Pickup` 的
//      **主** sprite 就是基类那个内嵌对象，而不是它自己的另一个 ANM2 成员。
// 单继承下基类子对象在偏移 0，所以**所有** `Entity_*`（含 `Entity_Player`/`Entity_Pickup`）的
// sprite 都在同一个偏移。
//
// ★ 已知偏差（必须写明）：派生类可以**额外**持有别的 ANM2 成员（`Entity_Tear::Render`
// （`0x341794`）渲染的是 `this+0x4d8`/`this+0x630`，`Entity_Pickup::Render`（`0x270c34`）还会
// `RenderLayer` 渲染 `this+0x620` 的从属 sprite）。本轮只把"基类主 sprite"接出来 ——
// 与 PC 的 `Entity:GetSprite()` 对应；对"渲染时主要画从属 sprite"的少数实体，返回值可能不是
// 屏幕上最显眼的那个对象。要么接受这条偏差，要么逐个派生化。
inline constexpr uintptr_t kEntitySpriteOffset = 0x50;

// `ANM2+0x38`：指向**当前动画名**的 libc++ `std::string`（24 字节 SSO 对象；没有动画时为 0）。
// 证据 = 引擎自己的 `ANM2::IsPlaying(char const*)`（`0xa454`）就是这么读的：
//   `f9401c08 ldr x8, [x0, #0x38]`（为 0 → 返回 false）
//   `39415009 ldrb w9, [x0, #0x54]`（在播标志，为 0 → false）
//   `39400109 ldrb w9, [x8]` → `37000129 tbnz w9, #0x1, …`
//     长串（bit0=1）：`f9400900 ldr x0, [x8, #0x10]`（数据指针）
//     短串：`91000500 add x0, x8, #0x1`（内联数据从 +1 开始）
//   → `bl strcmp(x0, 传入的动画名)`
// 这与 `lua_object_handles.hpp`/`sprite_api.cpp` 里 `kLibcxxStringDataOffset = 16` 的长串布局一致，
// 也就是说 `Sprite:GetAnimation()` 可以只用"引擎自己证明过的读法"实现，不需要猜偏移。
inline constexpr uintptr_t kSpriteAnimationNameOffset = 0x38;
// `ANM2+0x60`：`AnimationState*`。证据来自引擎自己的 `ANM2::GetTexel`（`0xC074`）——
// 它第一件事就是 `add x0, x0, #0x60`（`0xC098`）再 tail-call
// `AnimationState::GetTexel`（`0xC0B8`）；`ANM2::Render` 也把同一个 `[this+0x60]` 当参数传下去。
// 用途：调引擎 `GetTexel` 前判"有没有当前动画状态"，指针为空就退回落值，避免空指针解引用。
inline constexpr uintptr_t kSpriteAnimationStateOffset = 0x60;
// `ANM2+0x54`：在播标志（同一个 `IsPlaying` 读到的第二个条件）。
inline constexpr uintptr_t kSpritePlayingFlagOffset = 0x54;

// ---------------------------------------------------------------------------
// `Sprite` 的三个**可写**属性在 `ANM2` 里的落点（2026-09-16）
// ---------------------------------------------------------------------------
// 这三条是"引擎写通道"的第一批落点：PC 的 `Sprite.Scale` / `Sprite.Color` / `Sprite.FlipX`
// 在 PC 上就是写 `ANM2` 的成员，之前我们只把值缓存进句柄（渲染时读不到差别），现在按下面
// 两条互不相同的证据写进原生对象。
//
// **证据一：`ANM2::Reset`（`0x65B8`）—— 构造/复位时写的默认值，本身就把语义钉住了**
//   * `0x670C add x0,x19,#0xdc` → `bl Vector2::operator=(Vector2 const&)`，右操作数是
//     `KAGE::Math::Vector2::One`（`0x6708 ldr x1,[…] ; _ZN4KAGE4Math7Vector23OneE`）
//     ⇒ `ANM2+0xDC` 是**缩放向量**，默认 `(1,1)`；
//   * `0x6704/0x6708` 前一条同类指令是 `0x66E4 add x0,x19,#0xd4` → 同一个 `operator=`
//     但右操作数是 `Vector2::Zero`（`_ZN4KAGE4Math7Vector24ZeroE`）⇒ `+0xD4` 是另一个
//     向量（渲染里加在位置上的那个，本轮不用，仅登记）；
//   * `0x6714 add x0,x19,#0xe8` → `bl ColorMod::Reset()` ⇒ `ANM2+0xE8` 是一个 `ColorMod`
//     （精灵自身的颜色），`0x6720 add x20,x19,#0x114` → 又一次 `ColorMod::Reset()` ⇒ 第二个
//     `ColorMod` 落在 `0x114` ⇒ **第一个 `ColorMod` 占 `0xE8..0x114`，共 0x2C 字节**；
//   * `0x6754 strh wzr,[x19,#0x140]` ⇒ `0x140`/`0x141` 是**两个相邻的字节标志**，默认 0。
//
// **证据二：`ANM2::AnimationLayer::GetDestQuad`（`0x9790`）—— 渲染时怎么用这三格**
//   （该函数第 6 个参数 `x6` 就是 `ANM2 const*`，函数符号自带 `…, ANM2 const*)`）
//   * `0x9824 ldr x8,[x26,#0xdc]!`（`x26` = `ANM2`）一次性读 **8 字节** = 缩放向量的
//     `X@0xDC`/`Y@0xE0`，随后与图层缩放一起进 `Vector2::operator*`（`0x9834`）；
//   * `0x97DC ldrb w23,[x6,#0x140]` 与 `0x97C4 ldrb w8,[x6,#0x141]` 读那两个字节，
//     用于决定四边形是否镜像 ⇒ 就是 PC 的 `FlipX`/`FlipY`；
//   * `0x97D0 ldr w11,[x3,#0x90]`＋`0x97F8 orr w24,w11,w10`（`w10=[ANM2+0x150]`）是把
//     图层标志与 `ANM2+0x150` 合起来判混合模式，与颜色无关（旁证：`0x150` 不是颜色）。
//
// **`ColorMod` 自己的布局（写颜色要知道哪 4 个 float 是 tint）**
//   * `ColorMod::Reset`（`0x37830`）：`0x37834 stp x8,x8,[x0]`（`x8` = `0x3f8000003f800000`
//     = 两个 `1.0f`）+ `0x37838 str xzr,[x0,#0x20]` + `0x3783C str wzr,[x0,#0x28]` +
//     `0x37840 stp xzr,xzr,[x0,#0x10]` ⇒ 头 8 字节 = 两个 1.0f（tint），`0x10..0x1C` 清零（偏移量）；
//   * `ColorMod::SetTint`（`0x37848`）：`str s0,[x0]` / `str s1,[x0,#0x4]` / `str s2,[x0,#0x8]` /
//     `str s3,[x0,#0xc]` ⇒ **tint 的 R/G/B/A 就在 `+0x00/+0x04/+0x08/+0x0C`**；紧随其后的
//     `0x3785C` 起同样四条 `str` 打在 `+0x10/+0x14/+0x18/+0x1C`（偏移量四元组）；
//   * `ColorMod::operator*=`（`0x37BEC`）：`+0x00..+0x0C` 走 `fmul`（相乘），`+0x10..+0x1C`
//     走 `fadd`/平均（叠加）⇒ 与 PC `Color` 的"RGB 乘、Offset 加"一致。
inline constexpr uintptr_t kSpriteScaleOffset = 0xDC;    // Vector2（缩放 X@+0xDC、Y@+0xE0）
inline constexpr uintptr_t kSpriteColorOffset = 0xE8;    // ColorMod：tint 在 +0x00..+0x0C
inline constexpr uintptr_t kSpriteFlipXOffset = 0x140;   // u8（+0x141 是 FlipY，本轮不用）
// `ColorMod` 内部偏移：tint 的 R/G/B/A 与"偏移量"四元组的起点。
inline constexpr uintptr_t kColorModTintOffset = 0x00;
inline constexpr uintptr_t kColorModShiftOffset = 0x10;
inline constexpr std::size_t kColorModSize = 0x2C;
// PC `Color` 的第 5/6/7 个分量（`RO/GO/BO`）在我们的 `KColor` 句柄里也有对应字段
// （`lua_object_handles.hpp` 的 `ColorHandle::offsetRed/Green/Blue`），所以一并写进去。

// `Sprite`（引擎侧 `IsaacRepentance::ANM2`）第一步用到的入口：全部是 `char const*`/数值参数，
// 不涉及 `std::string`，因此没有 ABI 风险。对象尺寸见 `kSpriteObjectSize` 的注释。
// 构造函数 `ANM2::ANM2()`
inline constexpr uintptr_t kSpriteCtorOffset = 0x6540;
inline constexpr std::array<u8, 16> kSpriteCtorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
// 完整对象析构 `ANM2::~ANM2()`（D1：**不释放内存**，内存由我们自己 free）
inline constexpr uintptr_t kSpriteDestructorOffset = 0x71A4;
inline constexpr std::array<u8, 16> kSpriteDestructorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
// `void Play(char const*, bool)`
inline constexpr uintptr_t kSpritePlayOffset = 0xA1F4;
inline constexpr std::array<u8, 16> kSpritePlayExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF6, 0x57, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};
// `bool SetAnimation(char const*, bool)`
inline constexpr uintptr_t kSpriteSetAnimationOffset = 0xA2E8;
inline constexpr std::array<u8, 16> kSpriteSetAnimationExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF8, 0x5F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `void SetFrame(char const*, int)`
inline constexpr uintptr_t kSpriteSetFrameNamedOffset = 0xA65C;
inline constexpr std::array<u8, 16> kSpriteSetFrameNamedExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF7, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `void SetFrame(int)`
inline constexpr uintptr_t kSpriteSetFrameOffset = 0xA8CC;
inline constexpr std::array<u8, 16> kSpriteSetFrameExpectedBytes = {
    0x08, 0x1C, 0x40, 0xF9, 0x88, 0x00, 0x00, 0xB4,
    0x20, 0x00, 0x22, 0x1E, 0x00, 0xC0, 0x00, 0x91,
};
// `int GetFrame() const`
inline constexpr uintptr_t kSpriteGetFrameOffset = 0xA924;
inline constexpr std::array<u8, 16> kSpriteGetFrameExpectedBytes = {
    0x08, 0x1C, 0x40, 0xF9, 0x88, 0x00, 0x00, 0xB4,
    0x00, 0x50, 0x40, 0xBD, 0x00, 0x00, 0x30, 0x1E,
};
// `void SetLayerFrame(int, int)`
inline constexpr uintptr_t kSpriteSetLayerFrameOffset = 0xA958;
inline constexpr std::array<u8, 16> kSpriteSetLayerFrameExpectedBytes = {
    0x08, 0x1C, 0x40, 0xF9, 0x68, 0x00, 0x00, 0xB4,
    0x00, 0xC0, 0x00, 0x91, 0xA3, 0x96, 0x19, 0x14,
};
// `int GetLayerFrame(int)`
inline constexpr uintptr_t kSpriteGetLayerFrameOffset = 0xA96C;
inline constexpr std::array<u8, 16> kSpriteGetLayerFrameExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0x1C, 0x40, 0xF9,
};
// `KColor GetTexel(Vector2, Vector2, float, int) const` —— **EID 用来判"这个底座是不是
// 赎罪线红问号底座"的唯一依据**（`main.lua:230-232` 逐点比色）。旧实现是"恒返回不透明白色"
// 的占位，导致两个 sprite 处处相等 → 每个底座都被判成问号底座 → EID 只画问号、不写描述。
//
// 偏移与 ABI 出处：`analysis/stage150-sprite-abi/<build>.json` 的
// `records.sprite_get_texel`（`confidence = verified`）——
//   * `.dynsym` 符号 `_ZNK15IsaacRepentance4ANM28GetTexelEN4KAGE4Math7Vector2ES3_fi`
//     → file_offset `0xC074`（下面这 16 字节是**从 NRO 抄录**并与镜像逐字节核对过的）；
//   * 前两个 `KAGE::Math::Vector2` **按值传**（HFA → s0..s3），`s4` = alphaThreshold，
//     `w1` = layerId；返回值是 16 字节 `KColor`，走 **x8 间接结果寄存器**
//     （函数体里 `mov x20,x8` 把它存下来，`0xC0B4`）。
inline constexpr uintptr_t kSpriteGetTexelOffset = 0xC074;
inline constexpr std::array<u8, 16> kSpriteGetTexelExpectedBytes = {
    0xFF, 0x83, 0x01, 0xD1, 0xEC, 0x0B, 0x00, 0xFD,
    0xF5, 0x27, 0x00, 0xF9, 0xEB, 0xAB, 0x01, 0x6D,
};

// --- `Level::GetCurses()` 的完整语义所需的两个 `Game` 方法（2026-09-12）-------------
//
// 反汇编 `Level::GetCurses`（`0x3D57CC`，`docs/问题与解决记录.md` 批次 4 记过它读
// `this+0xC`）得到 PC 的完整三步：
//
//     ldr w19,[x0,#0xc]            ; ① Level 自己的诅咒位（我们**已经**在做的一步）
//     ldr x20,[x20,#0x698]         ; 全局 Game 槽
//     ldr x0,[x20]                 ; → Game*
//     bl  GetSpecialSeedPermanentCurses()   ; ② 特殊种子"永久诅咒" —— 调用点 `0x3D57E8`
//     orr w0, w19, w0              ;    合成：curses | permanent（`0x3D57EC`）
//     ldr x0,[x20]
//     bl  GetSpecialSeedBannedCurses()      ; ③ 特殊种子"禁用诅咒" —— `0x3D57F4`
//     and w0, w0, w19              ;    合成：... & banned（`0x3D57F8`）
//
// GOT 槽 `0xAA3E20`/`0xAA3E28` 的重定位表把这两个 PLT 桩解析成
// `_ZNK15IsaacRepentance4Game29GetSpecialSeedPermanentCursesEv` 与
// `_ZNK15IsaacRepentance4Game26GetSpecialSeedBannedCursesEv`（`.dynsym` 里查得到，
// 地址即下面的偏移）。旧实现只做了 ① —— 于是特殊种子（Victory Lap / 挑战 / 种子局）的
// 永久诅咒读不到、"禁用诅咒"也屏蔽不掉，`EID:hasCurseBlind()` 可能因此误判。
inline constexpr uintptr_t kGameSpecialSeedPermanentCursesOffset = 0x35097C;
inline constexpr std::array<u8, 16> kGameSpecialSeedPermanentCursesExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0xB4, 0x8B, 0x52,
};
inline constexpr uintptr_t kGameSpecialSeedBannedCursesOffset = 0x350B98;
inline constexpr std::array<u8, 16> kGameSpecialSeedBannedCursesExpectedBytes = {
    0x08, 0xB4, 0x8B, 0x52, 0x48, 0x00, 0xA0, 0x72,
    0x08, 0x00, 0x08, 0x8B, 0x09, 0x01, 0x40, 0xF9,
};
// `Pickup.Touched` 的字段偏移仍是 `kEntityPickupTouchedOffset`；那个偏移本身是"待真机确认"
// 的猜测，本轮探针就是去确认它。
// `void Update()`
inline constexpr uintptr_t kSpriteUpdateOffset = 0x86BC;
inline constexpr std::array<u8, 16> kSpriteUpdateExpectedBytes = {
    0xE8, 0x0F, 0x1E, 0xFC, 0xFD, 0xFB, 0x00, 0xA9,
    0xFD, 0x23, 0x00, 0x91, 0xF3, 0x0F, 0x00, 0xF9,
};
// `bool IsPlaying(char const*) const`
inline constexpr uintptr_t kSpriteIsPlayingOffset = 0xA454;
inline constexpr std::array<u8, 16> kSpriteIsPlayingExpectedBytes = {
    0xFD, 0x7B, 0xBF, 0xA9, 0xFD, 0x03, 0x00, 0x91,
    0x08, 0x1C, 0x40, 0xF9, 0x28, 0x01, 0x00, 0xB4,
};
// `bool IsFinished(char const*) const`
inline constexpr uintptr_t kSpriteIsFinishedOffset = 0xA528;
inline constexpr std::array<u8, 16> kSpriteIsFinishedExpectedBytes = {
    0xFD, 0x7B, 0xBF, 0xA9, 0xFD, 0x03, 0x00, 0x91,
    0x08, 0x1C, 0x40, 0xF9, 0x68, 0x01, 0x00, 0xB4,
};
// `void Render(Vector2 const&, Vector2 const&, Vector2 const&)`
inline constexpr uintptr_t kSpriteRenderOffset = 0x9BD4;
inline constexpr std::array<u8, 16> kSpriteRenderExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF8, 0x5F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `void RenderLayer(int, Vector2 const&, Vector2 const&, Vector2 const&)`
inline constexpr uintptr_t kSpriteRenderLayerOffset = 0x9D70;
inline constexpr std::array<u8, 16> kSpriteRenderLayerExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF7, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `void PlayRandom(unsigned int)`
inline constexpr uintptr_t kSpritePlayRandomOffset = 0xA198;
inline constexpr std::array<u8, 16> kSpritePlayRandomExpectedBytes = {
    0x08, 0xA0, 0x40, 0xB9, 0x88, 0x01, 0x00, 0x34,
    0x29, 0x08, 0xC8, 0x1A, 0x0A, 0x2A, 0x80, 0x52,
};

// 第二步：`Load`/`ReplaceSpritesheet` 需要 libc++ 的 `std::string`（24 字节 SSO），本模块编的是
// libstdc++（32 字节），所以不能直接传自己的 string。做法是借游戏自己导入的
// `basic_string::assign(char const*)` 往一块 24 字节缓冲里填值 —— 全零的 24 字节在 libc++ 里
// 本来就是合法的空短串（long 标志位为 0、size 为 0），这是最弱的一个假设。
// `void Load(std::string const&, bool)`
inline constexpr uintptr_t kSpriteLoadOffset = 0xC970;
inline constexpr std::array<u8, 16> kSpriteLoadExpectedBytes = {
    0xFD, 0x7B, 0xBA, 0xA9, 0xFB, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xFA, 0x67, 0x02, 0xA9,
};
// `LoadGraphics()`（thunk：`mov w1,wzr; b load_graphics(false)`）
inline constexpr uintptr_t kSpriteLoadGraphicsOffset = 0xCB6C;
inline constexpr std::array<u8, 16> kSpriteLoadGraphicsExpectedBytes = {
    0xE1, 0x03, 0x1F, 0x2A, 0x60, 0x8E, 0x19, 0x14,
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
};
// `void ReplaceSpritesheet(int, std::string const&)`
inline constexpr uintptr_t kSpriteReplaceSpritesheetOffset = 0xD270;
inline constexpr std::array<u8, 16> kSpriteReplaceSpritesheetExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF5, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};
// 游戏导入的 libc++ `basic_string::assign(char const*)` PLT 桩（字符串桥用）
inline constexpr uintptr_t kLibcxxStringAssignOffset = 0x66FB80;
inline constexpr std::array<u8, 16> kLibcxxStringAssignExpectedBytes = {
    0x70, 0x21, 0x00, 0xD0, 0x11, 0x96, 0x46, 0xF9,
    0x10, 0xA2, 0x34, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// `IContentManager::GetMountedFilePath(char const*)`：内容路径 → 真实文件路径（找不到返回 null）。
// 引擎自己的 `ImageManager::LoadImage` 就是这么解析贴图文件的，所以我们用同一条路径判断"在不在"。
// 注意：返回的是**引擎分配**的字符串（调用方按需释放；我们只做存在性判断，沿用引擎自身"不释放"的做法）。
inline constexpr uintptr_t kContentGetMountedFilePathOffset = 0x4C2340;
inline constexpr std::array<u8, 16> kContentGetMountedFilePathExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF5, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};

// 游戏导入的 `operator delete(void*)`（`_ZdlPv`）PLT 桩。
// **为什么需要它**：字符串桥借游戏的 `basic_string::assign(char const*)` 建出来的是 libc++ 的
// 24 字节 `std::string`；路径超过 SSO 容量时，缓冲是**游戏模块的分配器**分配的。本模块（`subsdk9`）
// 有自己的 newlib 堆，用本模块的 `free` 去释放会踩坏堆 —— 必须走游戏自己的 `operator delete`。
inline constexpr uintptr_t kGameOperatorDeleteOffset = 0x66FB10;
inline constexpr std::array<u8, 16> kGameOperatorDeleteExpectedBytes = {
    0x70, 0x21, 0x00, 0xD0, 0x11, 0x7A, 0x46, 0xF9,
    0x10, 0xC2, 0x33, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// libc++ `std::string` 的 `__long` 布局实测（2026-09-13 真机探针，掩码位 17/18）：
// 数据指针在对象**偏移 16**；首字的**最低位为 1 表示 long**（短串则内联，位 14 实测）。
inline constexpr std::size_t kLibcxxStringDataOffset = 16;
// 短串（SSO）的内联数据从对象**偏移 1** 开始 —— 首字节存 `size << 1`（最低位是 long 标志）。
// 证据同样是引擎自己的代码：`ANM2::IsPlaying`（`0xa454`）的 `91000500 add x0, x8, #0x1` 之后
// 直接 `strcmp`，也就是把 `对象+1` 当 C 串首地址用（与 libc++ 的 `__short::__data_` 一致）。
inline constexpr std::size_t kLibcxxStringShortDataOffset = 1;
// 长串标志位（首字最低位）。
inline constexpr std::uint8_t kLibcxxStringLongFlag = 0x01;

inline constexpr uintptr_t kGameIsGreedModeOffset = 0x350200;
// Guard transcribed from `Repentance.nro` @0x350200 and cross-checked against the project's own
// `analysis/starterr-native-evidence/universal-getter-inventory-*.json`
// (`anchors.game_is_greed_mode.entry_guard`). Byte 10 used to read 0xB8 instead of 0x68; because
// `VerifyGameIsGreedMode` compares all 16 bytes with `memcmp`, that single wrong byte disabled the
// whole `Game:IsGreedMode` binding on hardware (fixed 2026-09-13, see docs/问题与解决记录.md).
inline constexpr std::array<u8, 16> kGameIsGreedModeExpectedBytes = {
    0x08, 0x05, 0x80, 0x52, 0xE8, 0x05, 0xA0, 0x72,
    0x08, 0x68, 0x68, 0xB8, 0x08, 0x79, 0x1F, 0x12,
};
inline constexpr uintptr_t kLevelIsAscentOffset = 0x3E1D98;
inline constexpr std::array<u8, 16> kLevelIsAscentExpectedBytes = {
    0x08, 0x00, 0x40, 0xB9, 0x08, 0x05, 0x00, 0x51,
    0x1F, 0x15, 0x00, 0x71, 0x28, 0x01, 0x00, 0x54,
};
// 批次 8（2026-09-15）：EID 在**描述构建**路径上无条件调用、而我们此前没有的两个 `Level` 成员。
// 两个偏移与守卫都取自 `docs/PC-Lua-API-对照清单.md` 的 `missing_easy` 表（该表的
// `file_offset` 已由两个上机验证过的守卫证明"等于运行时模块偏移"，见文档 §3.3）；
// 守卫就是该偏移处的 16 字节原文，安装期逐字节比对。
//
//   * `Level:GetAbsoluteStage()` —— `features/eid_modifiers.lua:197`（潘多拉魔盒条目）与
//     `eid_conditionals_funcs.lua:363/369`、`eid_grid_descriptions.lua:108`、`eid_modifiers.lua:1164`；
//   * `Level:IsNextStageAvailable()` —— `features/eid_holdmapdesc.lua:174`。
//
// 两者都是 `const` 成员函数，返回 `int` / `bool`，参数只有 `this`（`Level*`）一个 —— 与
// `Level::IsAscent` 同一形状，所以 handler 走同一条"解析指针链 → 直接调用"的路。
inline constexpr uintptr_t kLevelGetAbsoluteStageOffset = 0x3E7F3C;
inline constexpr std::array<u8, 16> kLevelGetAbsoluteStageExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0x28, 0x36, 0x00, 0xB0,
};
inline constexpr uintptr_t kLevelIsNextStageAvailableOffset = 0x3DBDBC;
inline constexpr std::array<u8, 16> kLevelIsNextStageAvailableExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF8, 0x5F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `Room::GetFrameCount()`（2026-09-16 补，PC：`Room:GetFrameCount()` ——
// "Returns the amount of frames the room has been active for. Resets to 0 when the player leaves
// the room or the run is exited."）。
//
//   * 符号：`_ZN15IsaacRepentance4Room13GetFrameCountEv`，文件偏移 **`0x470B0C`**，arity 0（只有 `this`）；
//     来源 `analysis/lua-api-inventory/inventory.json`（`match_kind: exact_class`、
//     `match_confidence: high`、`thunk_readiness: ready`），16 字节守卫也取自同一条目。
//   * 语义证据（我们自己的指令级记录，见本文件 `kGameFrameCountOffset` 的注释）：
//     引擎里 `Room::GetFrameCount` 与 `Entity::GetFrameCount` 同形 ——
//     `ldr w8,[Game+0x24F99C]; sub w0,w8,[this+进入房间时的帧号]`
//     ⇒ "本局帧计数 − 进房间那一刻的帧号"，所以离开房间会归零。
//   * EID 用法：`features/eid_bagofcrafting.lua:967` 的 `game:GetRoom():GetFrameCount() < 2`
//     （刚进房间前两帧不显示合成结果）、`eid_bagofcrafting_search.lua:166`、`eid_debugging.lua:202`。
//     **缺它时**：背包合成那条渲染路径**每帧抛错**（`attempt to call a nil value (method
//     'GetFrameCount')`），EID 的描述渲染整段被打断 —— 这是 2026-09-16 真机上暴露出来的。
inline constexpr uintptr_t kRoomGetFrameCountOffset = 0x470B0C;
inline constexpr std::array<u8, 16> kRoomGetFrameCountExpectedBytes = {
    0x08, 0x00, 0x40, 0x39, 0x48, 0x01, 0x00, 0x34,
    0xE8, 0x31, 0x00, 0x90, 0x08, 0x4D, 0x43, 0xF9,
};
inline constexpr uintptr_t kManagerMusicOffset = 0x36068;
inline constexpr uintptr_t kMusicGetCurrentMusicIdOffset = 0x426C38;
inline constexpr std::array<u8, 16> kMusicGetCurrentMusicIdExpectedBytes = {
    0x00, 0xC8, 0x43, 0xB9, 0xC0, 0x03, 0x5F, 0xD6,
    0xE8, 0x03, 0x00, 0xAA, 0x00, 0xCC, 0x43, 0xB9,
};
inline constexpr uintptr_t kMusicPauseOffset = 0x42764C;
inline constexpr std::array<u8, 16> kMusicPauseExpectedBytes = {
    0x08, 0x00, 0x80, 0xB9, 0x09, 0x3C, 0x80, 0x52,
    0x08, 0x01, 0x09, 0x9B, 0x00, 0x21, 0x00, 0x91,
};
inline constexpr uintptr_t kMusicResumeOffset = 0x427660;
inline constexpr std::array<u8, 16> kMusicResumeExpectedBytes = {
    0x08, 0x00, 0x80, 0xB9, 0x09, 0x3C, 0x80, 0x52,
    0x08, 0x01, 0x09, 0x9B, 0x00, 0x21, 0x00, 0x91,
};
inline constexpr uintptr_t kRngSetSeedOffset = 0x44E3C0;
inline constexpr std::array<u8, 16> kRngSetSeedExpectedBytes = {
    0x01, 0x00, 0x00, 0xB9, 0xE8, 0x32, 0x00, 0xF0,
    0x08, 0x65, 0x41, 0xF9, 0x89, 0x01, 0x80, 0x52,
};
inline constexpr uintptr_t kRngNextOffset = 0x44E464;
inline constexpr std::array<u8, 16> kRngNextExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0x00, 0x40, 0xB9,
};
inline constexpr uintptr_t kStage48MusicPlayOffset = 0x427238;
inline constexpr std::array<u8, 16> kStage48MusicPlayExpectedBytes = {
    0xE8, 0x0F, 0x1C, 0xFC, 0xFD, 0x7B, 0x01, 0xA9,
    0xFD, 0x43, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
inline constexpr uintptr_t kStage48SoundActorPlayOffset = 0x517CF4;
inline constexpr std::array<u8, 16> kStage48SoundActorPlayExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr uintptr_t kStage48SoundActorPauseOffset = 0x517D2C;
inline constexpr std::array<u8, 16> kStage48SoundActorPauseExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr uintptr_t kStage48MusicPlayRelayCodeOffset = 0x68CD40;
inline constexpr uintptr_t kStage48MusicPlayRelaySlotOffset = 0x68CD78;
inline constexpr uintptr_t kStage48SoundActorPlayRelayCodeOffset = 0x68CD80;
inline constexpr uintptr_t kStage48SoundActorPlayRelaySlotOffset = 0x68CDB0;
inline constexpr uintptr_t kStage48SoundActorPauseRelayCodeOffset = 0x68CDC0;
inline constexpr uintptr_t kStage48SoundActorPauseRelaySlotOffset = 0x68CDF0;
inline constexpr std::array<u8, 4> kStage48MusicPlayRelayExpectedEntry = {
    0xC2, 0x96, 0x09, 0x14,
};
inline constexpr std::array<u8, 4> kStage48SoundActorPlayRelayExpectedEntry = {
    0x23, 0xD4, 0x05, 0x14,
};
inline constexpr std::array<u8, 4> kStage48SoundActorPauseRelayExpectedEntry = {
    0x25, 0xD4, 0x05, 0x14,
};
inline constexpr std::array<u8, 64> kStage48MusicPlayRelayExpectedBytes = {
    0xE8,0x0F,0x1C,0xFC,0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,
    0xFE,0x0B,0x00,0xF9,0xE0,0x1B,0x00,0xBD,0x31,0x01,0x00,0x10,
    0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,
    0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,0xE0,0x1B,0x40,0xBD,
    0xFF,0x83,0x00,0x91,0x32,0x69,0xF6,0x17,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 48> kStage48SoundActorPlayRelayExpectedBytes = {
    0xFD,0x7B,0xBE,0xA9,0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,
    0x31,0x01,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,
    0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFF,0x83,0x00,0x91,
    0xD5,0x2B,0xFA,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 48> kStage48SoundActorPauseRelayExpectedBytes = {
    0xFD,0x7B,0xBE,0xA9,0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,
    0x31,0x01,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,
    0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFF,0x83,0x00,0x91,
    0xD3,0x2B,0xFA,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kPreGetCollectibleRelayOffset = 0x3C6350;
inline constexpr std::array<u8, 16> kPreGetCollectibleRelayExpectedOriginal = {
    0xFF, 0x03, 0x04, 0xD1, 0xE8, 0x4B, 0x00, 0xFD,
    0xFD, 0x7B, 0x0A, 0xA9, 0xFD, 0x83, 0x02, 0x91,
};
inline constexpr uintptr_t kPreGetCollectibleRelayCodeOffset = 0x68CE00;
inline constexpr uintptr_t kPreGetCollectibleRelaySlotOffset = 0x68CE58;
inline constexpr std::array<u8, 4> kPreGetCollectibleRelayExpectedEntry = {
    0xAC, 0x1A, 0x0B, 0x14,
};
inline constexpr std::array<u8, 96> kPreGetCollectibleRelayExpectedBytes = {
    0xFF,0x03,0x04,0xD1,0xFF,0x03,0x01,0xD1,0xE0,0x07,0x00,0xA9,
    0xE2,0x0F,0x01,0xA9,0xE4,0x13,0x00,0xF9,0xFE,0x17,0x00,0xF9,
    0x11,0x02,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x10,0x01,0x00,0xB4,
    0x00,0x02,0x3F,0xD6,0x11,0xFC,0x60,0xD3,0xB1,0x00,0x00,0xB4,
    0xFE,0x17,0x40,0xF9,0xFF,0x03,0x01,0x91,0xFF,0x03,0x04,0x91,
    0xC0,0x03,0x5F,0xD6,0xE0,0x07,0x40,0xA9,0xE2,0x0F,0x41,0xA9,
    0xE4,0x13,0x40,0xF9,0xFE,0x17,0x40,0xF9,0xFF,0x03,0x01,0x91,
    0x40,0xE5,0xF4,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kGameChangeRoomCallFileOffset = 0x354050;
inline constexpr std::array<u8, 4> kGameChangeRoomCallExpectedOriginal = {
    0xFC, 0x99, 0x0C, 0x94,
};
inline constexpr uintptr_t kGameChangeRoomRelayCodeOffset = 0x68CE60;
inline constexpr uintptr_t kGameChangeRoomRelaySlotOffset = 0x68CE90;
inline constexpr std::array<u8, 4> kGameChangeRoomRelayExpectedEntry = {
    0x84, 0xE3, 0x0C, 0x14,
};
inline constexpr std::array<u8, 64> kGameChangeRoomRelayExpectedBytes = {
    0xFF,0x43,0x00,0xD1,0xFE,0x07,0x00,0xF9,0x76,0xB6,0xFF,0x97,0xE0,0x03,0x13,0xAA,
    0x11,0x01,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,
    0xFE,0x07,0x40,0xF9,0xFF,0x43,0x00,0x91,0x73,0x1C,0xF3,0x17,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kGameStartSavedCallFileOffset = 0x3F92A4;
inline constexpr uintptr_t kGameStartNewCallFileOffset = 0x3F93F8;
inline constexpr uintptr_t kGameStartSavedRelayCodeOffset = 0x68CC20;
inline constexpr uintptr_t kGameStartNewRelayCodeOffset = 0x68CC70;
inline constexpr uintptr_t kGameStartRelaySlotOffset = 0x68CCC0;
// ★ 这两条是"打完补丁后调用点上应有的分支指令"，编码里**含中继代码的地址** ——
// 换代码洞时必须与下面两段 80 字节的护栏**一起重算**，否则校验不过、挂点装不上
// （2026-09-14 就漏改过一次：只重算了 80 字节那段，结果设备上槽一直是 0）。
inline constexpr std::array<u8, 4> kGameStartSavedRelayExpectedEntry = {
    0x5F, 0x4E, 0x0A, 0x14,
};
inline constexpr std::array<u8, 4> kGameStartNewRelayExpectedEntry = {
    0x1E, 0x4E, 0x0A, 0x14,
};
inline constexpr std::array<u8, 80> kGameStartSavedRelayExpectedBytes = {
    0xFF, 0x83, 0x00, 0xD1, 0xE0, 0x07, 0x00, 0xA9,
    0xFE, 0x0B, 0x00, 0xF9, 0x91, 0xBF, 0xFF, 0x97,
    0xE0, 0x03, 0x40, 0xF9, 0x21, 0x00, 0x80, 0x52,
    0x51, 0x04, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x3F, 0xD6,
    0xE0, 0x07, 0x40, 0xA9, 0xFE, 0x0B, 0x40, 0xF9,
    0xFF, 0x83, 0x00, 0x91, 0x95, 0xB1, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr std::array<u8, 80> kGameStartNewRelayExpectedBytes = {
    0xFF, 0x83, 0x00, 0xD1, 0xE0, 0x07, 0x00, 0xA9,
    0xFE, 0x0B, 0x00, 0xF9, 0x8D, 0xBF, 0xFF, 0x97,
    0xE0, 0x03, 0x40, 0xF9, 0x41, 0x00, 0x80, 0x52,
    0xD1, 0x01, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x3F, 0xD6,
    0xE0, 0x07, 0x40, 0xA9, 0xFE, 0x0B, 0x40, 0xF9,
    0xFF, 0x83, 0x00, 0x91, 0xD6, 0xB1, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr std::array<uintptr_t, 3> kGameRestartCallFileOffsets = {
    0x351C50, 0x351D20, 0x351ECC,
};
inline constexpr std::array<uintptr_t, 3> kGameRestartRelayCodeOffsets = {
    0x68CEA0, 0x68CEF0, 0x68CF40,
};
inline constexpr uintptr_t kGameRestartRelaySlotOffset = 0x68CF90;
inline constexpr std::array<u8, 4> kGameRestartRelay0ExpectedEntry = {
    0x94, 0xEC, 0x0C, 0x14,
};
inline constexpr std::array<u8, 4> kGameRestartRelay1ExpectedEntry = {
    0x74, 0xEC, 0x0C, 0x14,
};
inline constexpr std::array<u8, 4> kGameRestartRelay2ExpectedEntry = {
    0x1D, 0xEC, 0x0C, 0x14,
};
inline constexpr std::array<u8, 80> kGameRestartRelay0ExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x45,0x92,0xFF,0x97,
    0xE0,0x03,0x40,0xF9,0x21,0x00,0x80,0x52,0xD1,0x06,0x00,0x10,0x30,0xFE,0xDF,0xC8,
    0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,
    0xFF,0x83,0x00,0x91,0x60,0x13,0xF3,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 80> kGameRestartRelay1ExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x31,0x92,0xFF,0x97,
    0xE0,0x03,0x40,0xF9,0x41,0x00,0x80,0x52,0x51,0x04,0x00,0x10,0x30,0xFE,0xDF,0xC8,
    0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,
    0xFF,0x83,0x00,0x91,0x80,0x13,0xF3,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 80> kGameRestartRelay2ExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x1D,0x92,0xFF,0x97,
    0xE0,0x03,0x40,0xF9,0x61,0x00,0x80,0x52,0xD1,0x01,0x00,0x10,0x30,0xFE,0xDF,0xC8,
    0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,
    0xFF,0x83,0x00,0x91,0xD7,0x13,0xF3,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kSaveLoadRelaySaveCallFileOffset = 0x3FA7B4;
inline constexpr uintptr_t kSaveLoadRelayLoadCallFileOffset = 0x3FAB60;
inline constexpr uintptr_t kSaveLoadRelaySaveCodeOffset = 0x68CEA0;
inline constexpr uintptr_t kSaveLoadRelayLoadCodeOffset = 0x68CEF0;
inline constexpr uintptr_t kSaveLoadRelaySlotOffset = 0x68CF90;
inline constexpr std::array<u8, 4> kSaveLoadRelaySaveExpectedEntry = {0xBB,0x49,0x0A,0x14};
inline constexpr std::array<u8, 4> kSaveLoadRelayLoadExpectedEntry = {0xE4,0x48,0x0A,0x14};
inline constexpr std::array<u8, 80> kSaveLoadRelaySaveExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x79,0xBF,0xFF,0x97,
    0xE0,0x03,0x13,0xAA,0x21,0x00,0x80,0x52,0xD1,0x06,0x00,0x10,0x30,0xFE,0xDF,0xC8,
    0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,
    0xFF,0x83,0x00,0x91,0x39,0xB6,0xF5,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 80> kSaveLoadRelayLoadExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x81,0xBF,0xFF,0x97,
    0xE0,0x1F,0x00,0xB9,0xE0,0x03,0x14,0xAA,0x41,0x00,0x80,0x52,0x31,0x04,0x00,0x10,
    0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x07,0x40,0xA9,
    0xFE,0x0B,0x40,0xF9,0xE0,0x1F,0x40,0xB9,0xFF,0x83,0x00,0x91,0x0E,0xB7,0xF5,0x17,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kStage128SaveDataManagerRelaySaveCallFileOffset = 0x36F474;
inline constexpr uintptr_t kStage128SaveDataManagerRelayLoadCallFileOffset = 0x379294;
inline constexpr uintptr_t kStage128SaveDataManagerRelaySaveCodeOffset = 0x68CEA0;
inline constexpr uintptr_t kStage128SaveDataManagerRelayLoadCodeOffset = 0x68CEF0;
inline constexpr uintptr_t kStage128SaveDataManagerRelaySlotOffset = 0x68CF90;
inline constexpr std::array<u8, 4> kStage128SaveDataManagerRelaySaveExpectedEntry = {0x8B,0x76,0x0C,0x14};
inline constexpr std::array<u8, 4> kStage128SaveDataManagerRelayLoadExpectedEntry = {0x17,0x4F,0x0C,0x14};
inline constexpr std::array<u8, 80> kStage128SaveDataManagerRelaySaveExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x21,0x00,0x80,0x52,
    0x11,0x07,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,
    0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,0xFF,0x83,0x00,0x91,0x0D,0x93,0xFF,0x97,
    0x6A,0x89,0xF3,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::array<u8, 80> kStage128SaveDataManagerRelayLoadExpectedBytes = {
    0xFF,0x83,0x00,0xD1,0xE0,0x07,0x00,0xA9,0xFE,0x0B,0x00,0xF9,0x41,0x00,0x80,0x52,
    0x91,0x04,0x00,0x10,0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,
    0xE0,0x07,0x40,0xA9,0xFE,0x0B,0x40,0xF9,0xFF,0x83,0x00,0x91,0xE9,0x92,0xFF,0x97,
    0xDE,0xB0,0xF3,0x17,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr std::uint32_t kTargetModuleScanAttemptLimit = 300;
inline constexpr std::int64_t kTargetModuleScanIntervalNanoseconds = 100'000'000;
// `ModuleWorker` 一进来就等多久（秒），由构建开关 `WORKER_STARTUP_DELAY_SECONDS` 决定。
//
// **默认 0 = 生产形状**：worker 建好就开始干活（2026-08-23 起就是这样，门禁
// `test_worker_does_not_depend_on_fixed_sdk_initialization_delay` 也钉着这条）。
// 真机实验包用 `WORKER_STARTUP_DELAY_SECONDS=5` 打开：把"我们自己的启动期动作"
// （扫内存找游戏模块、装挂点、自校验、读模组文件）挪到"系统装载游戏本体模块"那个窗口之后。
//
// 为什么不在 `exl_main` 里等：入口是被游戏**同步等待**的（记录续十六 §二：不做延迟不到 1 秒就崩、
// 延迟 8 秒版就"约 8 秒"崩、栈一模一样），在入口等等于直接给开机加秒数。
// 背景、实测数字与取舍见 `docs/问题与解决记录.md` 2026-09-16 续十六 与 `docs/改动日志-20260916.md`。
#if !defined(EXL_WORKER_STARTUP_DELAY_SECONDS)
#define EXL_WORKER_STARTUP_DELAY_SECONDS 0
#endif
inline constexpr std::int64_t kWorkerStartupDelayNanoseconds =
    static_cast<std::int64_t>(EXL_WORKER_STARTUP_DELAY_SECONDS) * 1'000'000'000LL;
inline constexpr std::array<u8, 0x20> kTargetBuildId = {
    0x91, 0xC7, 0x3F, 0xDD, 0x57, 0x50, 0x61, 0x31,
    0x8D, 0x68, 0x88, 0x63, 0x16, 0xAF, 0xEA, 0xC7,
    0x23, 0x88, 0xB2, 0xAB, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr uintptr_t kManagerUpdateFileOffset = 0x3F8DB8;
// Build ID 91C73FDD575061318D68886316AFEAC72388B2AB (Repentance.nro).
// File offset 0x3F8DB8, Ghidra symbol _ZN15IsaacRepentance7Manager6UpdateEv:
// sub sp, #0x50; stp x29, x30, [sp, #0x10]; add x29, sp, #0x10; str x23, [sp, #0x20].
inline constexpr std::array<u8, 16> kManagerUpdateExpectedBytes = {
    0xFF, 0x43, 0x01, 0xD1, 0xFD, 0x7B, 0x01, 0xA9,
    0xFD, 0x43, 0x00, 0x91, 0xF7, 0x13, 0x00, 0xF9,
};
inline constexpr uintptr_t kManagerRelayCodeOffset = 0x68CBE0;
inline constexpr uintptr_t kManagerRelayFallbackOffset = 0x68CBF0;
inline constexpr uintptr_t kManagerRelaySlotOffset = 0x68CBF8;
inline constexpr std::array<u8, 4> kManagerRelayExpectedEntry = {
    0x8A, 0x4F, 0x0A, 0x14,
};
inline constexpr std::array<u8, 32> kManagerRelayExpectedBytes = {
    0xD1, 0x00, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x1F, 0xD6,
    0xFF, 0x43, 0x01, 0xD1, 0x72, 0xB0, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
// 零占洞入口改写的前两条指令（`ldr x16, #8` + `br x16`，literal 就在紧随其后的 +8）。
// 用途：识别"这个函数入口已经被**入口中继**接管"——旧 IPS 形态与这种形态都要认的地方
// （例如 `ValidateItemPoolGetCollectibleMethod`）靠它区分。
// 数值必须与 `source/relay/entry_relay.hpp` 的编码器一致，由
// `src/infrastructure/relay/entry_relay_hook_adapter.cpp` 里的 static_assert 钉住。
inline constexpr std::uint32_t kEntryRelayEntryStubWord0 = 0x58000050;
inline constexpr std::uint32_t kEntryRelayEntryStubWord1 = 0xD61F0200;

inline constexpr uintptr_t kManagerRenderFileOffset = 0x3F9684;
inline constexpr std::array<u8, 16> kManagerRenderExpectedBytes = {
    0xFF, 0xC3, 0x02, 0xD1, 0xE8, 0x3B, 0x00, 0xFD,
    0xFD, 0x7B, 0x08, 0xA9, 0xFD, 0x03, 0x02, 0x91,
};
// `Manager::Render` 体内最后一次 `Present` 调用的位置（不是函数入口！）。
// 这 16 字节以 `bl`（PC 相对）开头，入口中继**无法回放**，因此它不能走 entry_relay；
// 迁移时改为改写该调用所用 PLT 桩对应的 GOT 槽（见设计文档 §4.5）。
// 登记它的唯一目的是把"不可入口挂"这个事实钉进测试。
//
// 偏移**复用**既有的 `kManagerPresentCallFileOffset`（见本文件 Present 调用点那一段），
// 不要再为它新造偏移常量 —— 同一地址只能有一个真值源。同一处的两个数组都配到这一个偏移常量：
// `kManagerPresentCallExpectedEntry` 描述**打补丁后**的 4 字节（入口已被改成跳向代码洞的 `b`），
// 本数组登记的是**原始镜像**的 16 字节。
//
// 命名与配对规则（**不要靠后缀推断**，本文件这里已经踩过坑）：
// `tools/verify_runtime_constants_against_nro.py` 的 `pair_guards()` 两侧剥的尾巴不一样 ——
// 偏移侧按 `OFFSET_SUFFIXES = ("FileOffset", "CallFileOffset", "Offset")` **整段剥掉**后缀
// （`FileOffset` 先命中），所以 `kManagerPresentCallFileOffset` 的基名是 `kManagerPresentCall`；
// 数组侧只剥 `ExpectedBytes`。因此本数组必须叫 `kManagerPresentCallExpectedBytes` 才配得上，
// 叫 `...CallFileExpectedBytes` 会因两侧基名不等而落进 `unpaired_arrays`。
// **不要**为了消掉 `unpaired_arrays` 再给这个地址补一个偏移常量 —— 那会把刚刚消除掉的
// "同一地址两个名字"重新引进来。
//
// 名字还必须以 `ExpectedBytes` 结尾，否则 `ARRAY_PATTERN` 根本不解析它。
// 另外要清楚校验器对它的作用很有限：`0x3F9B40` 是 `tools/build_patches.py` 的写入目标，
// 会被归为 `patched-image` 而跳过比对。换言之：**对着原始 NRO 逐字节核对这 16 字节的只有
// `runtime/tests/test_entry_relay_ledger.py`**，不要指望校验器兜底。
inline constexpr std::array<u8, 16> kManagerPresentCallExpectedBytes = {
    0xC0, 0xDB, 0x09, 0x94, 0xE8, 0x3B, 0x40, 0xFD,
    0xF4, 0x4F, 0x4A, 0xA9, 0xF6, 0x57, 0x49, 0xA9,
};
inline constexpr uintptr_t kManagerRenderRelayCodeOffset = 0x68CC00;
inline constexpr uintptr_t kManagerRenderRelayFallbackOffset = 0x68CC10;
inline constexpr uintptr_t kManagerRenderRelaySlotOffset = 0x68CC18;
inline constexpr std::array<u8, 4> kManagerRenderRelayExpectedEntry = {
    0x5F, 0x4D, 0x0A, 0x14,
};
inline constexpr std::array<u8, 32> kManagerRenderRelayExpectedBytes = {
    0xD1, 0x00, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x1F, 0xD6,
    0xFF, 0xC3, 0x02, 0xD1, 0x9D, 0xB2, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
// `MC_POST_RENDER` 的真实派发点：`Manager::Render` 体内最后一次 `Present` 调用。
//
// 入口中继（上面那组 `kManagerRenderRelay*`）把原函数整体当子调用跑完才回来派发，所以它派发时
// 本帧已经上屏、帧图像队列已经清空。2026-09-13 的真机照片（`analysis/stage149-photo/`）证明
// 后果有两条：回调里画的东西进的是**下一帧**队列，而且在那一帧里排在游戏自己的图像之前，于是
// 画在实体（以撒）与 HUD **之下**、只压在房间地面上。把派发插到 `Present` **之前**，图像就在
// 本帧入队、本帧 apply，并排在最后一个被画 —— 这才是 PC 的 `MC_POST_RENDER` 覆盖层语义。
//
// 调用点上下文：`x0` 已经是 `Present` 的实参（`KAGE::Graphics::g_Manager`），其后到 `ret`
// 之间只用 `d8` 与调用者保存之外寄存器，所以中继桩只需额外保存/恢复 `x0`。
inline constexpr uintptr_t kManagerPresentCallFileOffset = 0x3F9B40;
inline constexpr std::array<u8, 4> kManagerPresentCallExpectedEntry = {
    0xF0, 0x4C, 0x0A, 0x14,
};
inline constexpr uintptr_t kManagerPresentRelayCodeOffset = 0x68CF00;
inline constexpr uintptr_t kManagerPresentRelaySlotOffset = 0x68CF30;
// 覆盖代码洞 0x00..0x37：桩（含保存/恢复 x0 与 `blr`）＋补回的 `bl Present` ＋跳回 `调用点+4`；
// 末尾 8 字节正是 callback 槽（发布前为零），校验时按既有写法排除掉。
inline constexpr std::array<u8, 56> kManagerPresentRelayExpectedBytes = {
    0xFF, 0x43, 0x00, 0xD1, 0xF3, 0x03, 0x00, 0xF9,
    0xF3, 0x03, 0x00, 0xAA, 0x31, 0x01, 0x00, 0x10,
    0x30, 0xFE, 0xDF, 0xC8, 0x50, 0x00, 0x00, 0xB4,
    0x00, 0x02, 0x3F, 0xD6, 0xE0, 0x03, 0x13, 0xAA,
    0xF3, 0x03, 0x40, 0xF9, 0xFF, 0x43, 0x00, 0x91,
    0xC6, 0x8E, 0xFF, 0x97, 0x06, 0xB3, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
// ---- M2b：`render-present` 的"零代码字节"方案（改 GOT 槽，不改游戏指令）----
//
// `Manager::Render` 体内那次 `bl` 的目标是一个 PLT 桩（偏移 `0x670A40`）：
//     adrp x16, 0xA9E000 ; ldr x17, [x16, #0x488] ; add x16, x16, #0x488 ; br x17
// 桩读的那个槽在**模块内偏移 `0xA9E488`**（由 `_ZN4KAGE8Graphics7Manager7PresentEv` 的重定位表
// 独立印证）。槽里存的是加载器解析出来的 `Present` 地址，运行时**必须先读后存**，绝不能拿文件初值
// （`0x66FAA0`，那是懒绑定的解析桩）当准。
// 解码提醒：`ldr` 的 imm12 要按访问宽度缩放 —— 0x91 × 8 = 0x488，别当成 0x091。
//
// `Present` 本体（`_ZN4KAGE8Graphics7Manager7PresentEv`）就定义在本游戏模块内，偏移 `0x4F3014`。
//
// **关键事实（本方案成立的依据）**：指向这个桩的调用点**有 43 处**（`Game::Render` 6、
// `Room::Render` 5、`Manager::Render` 3、`EntityList::Update` 2、`HUD`/菜单/背景/过场…），
// 而 IPS 方案只拦截其中**一处**（`kManagerPresentCallFileOffset` = 0x3F9B40）。因此改槽之后，
// 拦截函数**必须按调用方过滤**：只有返回地址等于 `kManagerPresentCallFileOffset + 4` 的那一次调用
// 才派发 `MC_POST_RENDER`，其余 42 处原样转发给真正的 `Present` —— 行为与 IPS 方案逐字等价。
inline constexpr uintptr_t kManagerPresentStubOffset = 0x670A40;
inline constexpr std::array<u8, 16> kManagerPresentStubExpectedBytes = {
    0x70, 0x21, 0x00, 0xD0, 0x11, 0x46, 0x42, 0xF9,
    0x10, 0x22, 0x12, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kManagerPresentGotSlotOffset = 0xA9E488;
inline constexpr uintptr_t kManagerPresentTargetOffset = 0x4F3014;
inline constexpr std::array<u8, 16> kManagerPresentTargetExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0x00, 0x40, 0xF9,
};
inline constexpr uintptr_t kGameInitCallFileOffset = 0x3F5A44;
inline constexpr std::array<u8, 8> kGameInitContextExpectedBytes = {
    0xE0, 0x03, 0x14, 0xAA, 0x14, 0x01, 0x00, 0xF9,
};
inline constexpr uintptr_t kGameObserverRelayCodeOffset = 0x68CC40;
inline constexpr uintptr_t kGameObserverRelaySlotOffset = 0x68CC70;
inline constexpr std::array<u8, 4> kGameObserverRelayExpectedEntry = {
    0x7F, 0x5C, 0x0A, 0x14,
};
inline constexpr std::array<u8, 64> kGameObserverRelayExpectedBytes = {
    0xFF, 0x43, 0x00, 0xD1, 0xF3, 0x03, 0x00, 0xF9,
    0xF3, 0x03, 0x00, 0xAA, 0x31, 0x01, 0x00, 0x10,
    0x30, 0xFE, 0xDF, 0xC8, 0x50, 0x00, 0x00, 0xB4,
    0x00, 0x02, 0x3F, 0xD6, 0xE0, 0x03, 0x13, 0xAA,
    0xF3, 0x03, 0x40, 0xF9, 0xFF, 0x43, 0x00, 0x91,
    0x92, 0xBE, 0xFF, 0x97, 0x77, 0xA3, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr uintptr_t kGameUpdateCallFileOffset = 0x3F905C;
inline constexpr std::array<u8, 4> kGameUpdateContextExpectedBytes = {
    0xC0, 0x02, 0x40, 0xF9,
};
inline constexpr uintptr_t kGameUpdateObserverRelayCodeOffset = 0x68CC80;
inline constexpr uintptr_t kGameUpdateObserverRelaySlotOffset = 0x68CCB0;
inline constexpr std::array<u8, 4> kGameUpdateObserverRelayExpectedEntry = {
    0x09, 0x4F, 0x0A, 0x14,
};
inline constexpr std::array<u8, 64> kGameUpdateObserverRelayExpectedBytes = {
    0xFF, 0x43, 0x00, 0xD1, 0xF3, 0x03, 0x00, 0xF9,
    0xF3, 0x03, 0x00, 0xAA, 0x31, 0x01, 0x00, 0x10,
    0x30, 0xFE, 0xDF, 0xC8, 0x50, 0x00, 0x00, 0xB4,
    0x00, 0x02, 0x3F, 0xD6, 0xE0, 0x03, 0x13, 0xAA,
    0xF3, 0x03, 0x40, 0xF9, 0xFF, 0x43, 0x00, 0x91,
    0x4E, 0xBF, 0xFF, 0x97, 0xED, 0xB0, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr uintptr_t kGameState2CallFileOffset = 0x3F903C;
inline constexpr std::array<u8, 4> kGameState2ContextExpectedBytes = {
    0xC0, 0x02, 0x40, 0xF9,
};
inline constexpr uintptr_t kGameState2ObserverRelayCodeOffset = 0x68CCC0;
inline constexpr uintptr_t kGameState2ObserverRelaySlotOffset = 0x68CCF0;
inline constexpr std::array<u8, 4> kGameState2ObserverRelayExpectedEntry = {
    0x21, 0x4F, 0x0A, 0x14,
};
inline constexpr std::array<u8, 64> kGameState2ObserverRelayExpectedBytes = {
    0xFF, 0x43, 0x00, 0xD1, 0xF3, 0x03, 0x00, 0xF9,
    0xF3, 0x03, 0x00, 0xAA, 0x31, 0x01, 0x00, 0x10,
    0x30, 0xFE, 0xDF, 0xC8, 0x50, 0x00, 0x00, 0xB4,
    0x00, 0x02, 0x3F, 0xD6, 0xE0, 0x03, 0x13, 0xAA,
    0xF3, 0x03, 0x40, 0xF9, 0xFF, 0x43, 0x00, 0x91,
    0x36, 0xBF, 0xFF, 0x97, 0xD5, 0xB0, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr uintptr_t kManagerLoadConfigsFileOffset = 0x3F5C38;
inline constexpr uintptr_t kGameIsPausedRenderCallFileOffset = 0x3429A4;
inline constexpr std::array<u8, 12> kGameIsPausedRenderContextExpectedBytes = {
    0x48, 0x3B, 0x00, 0xD0, 0x08, 0x4D, 0x43, 0xF9, 0x00, 0x01, 0x40, 0xF9,
};
inline constexpr uintptr_t kGameIsPausedRenderObserverRelayCodeOffset = 0x68CD00;
inline constexpr uintptr_t kGameIsPausedRenderObserverRelaySlotOffset = 0x68CD30;
inline constexpr std::array<u8, 4> kGameIsPausedRenderObserverRelayExpectedEntry = {0xD7, 0x28, 0x0D, 0x14};
inline constexpr std::array<u8, 64> kGameIsPausedRenderObserverRelayExpectedBytes = {
    0xFF,0x43,0x00,0xD1,0xF3,0x03,0x00,0xF9,0xF3,0x03,0x00,0xAA,0x31,0x01,0x00,0x10,
    0x30,0xFE,0xDF,0xC8,0x50,0x00,0x00,0xB4,0x00,0x02,0x3F,0xD6,0xE0,0x03,0x13,0xAA,
    0xF3,0x03,0x40,0xF9,0xFF,0x43,0x00,0x91,0xF6,0x90,0xFF,0x97,0x1F,0xD7,0xF2,0x17,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
};
inline constexpr uintptr_t kManagerToModManagerOffset = 0x36800;
inline constexpr uintptr_t kModManagerResetFileOffset = 0x422440;
inline constexpr std::array<u8, 16> kModManagerResetExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr std::array<u8, 16> kManagerLoadConfigsExpectedBytes = {
    0xFF, 0x43, 0x02, 0xD1, 0xFD, 0x7B, 0x03, 0xA9,
    0xFD, 0xC3, 0x00, 0x91, 0xFC, 0x6F, 0x04, 0xA9,
};
inline constexpr uintptr_t kManagerLoadConfigsRelayCodeOffset = 0x68CC20;
inline constexpr uintptr_t kManagerLoadConfigsRelayFallbackOffset = 0x68CC30;
inline constexpr uintptr_t kManagerLoadConfigsRelaySlotOffset = 0x68CC38;
inline constexpr std::array<u8, 4> kManagerLoadConfigsRelayExpectedEntry = {
    0xFA, 0x5B, 0x0A, 0x14,
};
inline constexpr std::array<u8, 32> kManagerLoadConfigsRelayExpectedBytes = {
    0xD1, 0x00, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x1F, 0xD6,
    0xFF, 0x43, 0x02, 0xD1, 0x02, 0xA4, 0xF5, 0x17,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr std::size_t kGameFileObjectSize = 0x60;
inline constexpr uintptr_t kGameFileConstructorFileOffset = 0x4CCBC0;
inline constexpr uintptr_t kGameFileOpenReadFileOffset = 0x4CCD58;
inline constexpr uintptr_t kGameFileOpenWriteFileOffset = 0x4CCE38;
inline constexpr uintptr_t kGameFileGetLengthFileOffset = 0x4CD104;
inline constexpr uintptr_t kGameFileReadFileOffset = 0x4CD184;
inline constexpr uintptr_t kGameFileWriteFileOffset = 0x4CD190;
inline constexpr uintptr_t kGameFileCloseFileOffset = 0x4CD00C;
inline constexpr uintptr_t kGameFileDestructorFileOffset = 0x4CCBFC;
inline constexpr std::array<u8, 16> kGameFileConstructorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr std::array<u8, 16> kGameFileOpenReadExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x02, 0x1F, 0x00, 0xD0,
};
inline constexpr std::array<u8, 16> kGameFileOpenWriteExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x03, 0x00, 0xAA,
};
inline constexpr std::array<u8, 16> kGameFileGetLengthExpectedBytes = {
    0xFF, 0x83, 0x00, 0xD1, 0xFD, 0x7B, 0x01, 0xA9,
    0xFD, 0x43, 0x00, 0x91, 0xFF, 0x07, 0x00, 0xF9,
};
inline constexpr std::array<u8, 16> kGameFileReadExpectedBytes = {
    0x5B, 0xCB, 0x06, 0x14, 0x00, 0xE0, 0x00, 0xD1,
    0x59, 0xCB, 0x06, 0x14, 0x5C, 0xCB, 0x06, 0x14,
};
inline constexpr std::array<u8, 12> kGameFileWriteExpectedBytes = {
    0x5C, 0xCB, 0x06, 0x14, 0x00, 0xE0, 0x00, 0xD1,
    0x5A, 0xCB, 0x06, 0x14,
};
inline constexpr std::array<u8, 16> kGameFileCloseExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr std::array<u8, 16> kGameFileDestructorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0x2F, 0x00, 0xB0,
};
inline constexpr char kRomfsSentinelPath[] = "rom:/isaac_mod_probe.lua";
inline constexpr char kRomfsSentinelContents[] = "ISAAC_ROMFS_PROBE\n";
inline constexpr std::size_t kRomfsSentinelLength = 18;
inline constexpr char kRomfsLuaProbePath[] = "rom:/runtime_probe.lua";
inline constexpr std::size_t kRomfsLuaProbeMaximumLength = 4096;
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
inline constexpr char kRomfsModManifestPath[] = "rom:/isaac_mods/manifest.json";
// 必须与 `runtime/src/application/mod/manifest_service.hpp` 的同名常量一致：`hook_manager.cpp`
// 同时包含两者，这里未限定的名字解析到全局作用域这一个（`g_DefaultManifest` 的缓冲区），
// 而服务用自己命名空间里的那一个做读取上限判定。
// 2026-09-14：131072 -> 524288。真实贴图 Mod（qualityonsprites）的清单是 **309 KB**（712 个
// 源文件 + 711 个生成的 `.pcx`，每条记录带 path+sha256），而 `GameFileReader::ReadTextFile`
// 对超过缓冲区的文件返回 `LengthOutOfRange` —— "清单过大"等于直接 `ManifestRead` 失败。
// 512 KiB 按实测每条约 200 字节可容纳约 2600 个文件，代价只是 .bss（磁盘映像不变）。
inline constexpr std::size_t kRomfsModManifestMaximumLength = 524288;
// 2026-09-12：16384 -> 1048576。真机报告 `01789200504` 的根因就是它太小：EID 的 `main.lua`
// 是 **87,328 字节**、`require` 的语言文件最大 **243,740 字节**，而 `ReadTextFile` 对超长文件
// 返回 `LengthOutOfRange` → `EntryRead` 失败（诊断字 `[11] = 4`）→ **EID 的 Lua 一行都没跑**，
// 表现为"加载不报错、回调注册表为空、屏幕上什么都没有"。1 MiB 是最大 Lua 文件的 4 倍，
// 只花 .bss。同名常量在 `manifest_service.hpp` 里也有一份，两者必须相等（见 `hook_manager.cpp`
// 的 `static_assert`）。
inline constexpr std::size_t kRomfsModScriptMaximumLength = 1048576;
#endif

// KAGE content mount points: how a Mod's own `resources/` directory reaches the
// engine's resource lookup. `ContentMountPointPath(char const*)` joins the string
// with `ContentManager::ApplicationMountPoint()`, `AddMountPoint(path const&)`
// builds a `ContentMountPoint` whose `FileMap::build` **enumerates the directory
// recursively** (through `nn::fs`, so the Atmosphere SD overlay is included) and
// files stay addressable by their path relative to the mount point. A Mod mount
// point therefore needs no `kage_mount_points.dat` entry.
//
// Hardware evidence for the whole chain (7 guards matching on the device plus the
// four-step resolution result) is in
// `analysis/stage146-content-mount-point/device-evidence-20260913.json`, crash
// report `01789131431`.
inline constexpr uintptr_t kContentManagerSlotOffset = 0xAAC748;
inline constexpr uintptr_t kContentMountPointPathCtorOffset = 0x4C0D84;
inline constexpr std::array<u8, 16> kContentMountPointPathCtorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x08, 0x00, 0x80, 0x12,
};
inline constexpr uintptr_t kContentMountPointPathDtorOffset = 0x4C0F60;
inline constexpr std::array<u8, 16> kContentMountPointPathDtorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr uintptr_t kContentAddMountPointOffset = 0x4C1700;
inline constexpr std::array<u8, 16> kContentAddMountPointExpectedBytes = {
    0xFD, 0x7B, 0xBB, 0xA9, 0xFA, 0x67, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF8, 0x5F, 0x02, 0xA9,
};

// ---------------------------------------------------------------------------
// 目录枚举与文件写入：**引擎自己**的文件层（2026-09-16 取证，当前无调用方）
// ---------------------------------------------------------------------------
//
// ★ 下面这组常量**现在没有调用方** —— 它们是"方案 A（运行时自己发现模组、清单降级成可选）"
//   的取证结果。2026-09-16 用户裁定"先不做"，所以探针代码已删除，**证据留在这里**：
//   下一轮要做的时候不必再从头 argv 一遍（而且门禁会继续用 NRO 逐字节核对这些守卫字节）。
//   完整经过与真机读数见 `docs/问题与解决记录.md` 续三十/续三十一。
//
// 背景：我们模块自己起 `sm`/`fs` 会话是**死的**（`svcConnectToNamedPort("sm:")` 在内核层返回
// `0x10801` OutOfResource，`fsInitialize` 因此 `0xE401`，12 秒后重试不变；见
// `docs/SaltyNX文件服务与自建方案研究.md`）。但引擎自己那套 `KAGE::Filesys` 用的是**游戏进程
// 已经建好的 fs 会话**（游戏靠它写存档、读 romfs，我们的模组挂载点也是靠它生效的）。
//
// 真机结论（`dist/gdb-read-20260916-171017.txt`）：
//   * `isaac_mods/mods` 列得出来，`count = 2`，两个条目就是卡上的两个模组目录，
//     名字读回来分别是 "external item de…" 与 "multimod-probe" ⇒ **覆盖层里的目录看得见**；
//   * 返回的指针是**第一个条目**的地址（条目数走出参给），条目 0x10 字节：
//     `+0x00` 标志字节、`+0x08` 名字指针；标志 `0x06` = 目录 + 自己拥有名字串；
//   * ⚠️ **只对 `isaac_mods/mods` 这种路径安全**：对子目录（`…/multimod-probe`）再调一次会
//     触发**引擎内部断言**（崩溃报告 `01789549162`）。
//
// 下面这些偏移的守卫字节仍由门禁逐字节核对（`*ExpectedBytes` 的既有机制）。

// `KAGE::Filesys::IContentManager::GetDirectoryEntries(char const* path, unsigned& count)`
//   * 符号：`.dynsym` 里函数体在 `0x4C23C4`（名字见 `tools/nro_symbols.py` 的查询结果）；
//   * 行为（反汇编逐条读出）：`x1` = 路径（相对**应用根**，与 `AddMountPoint` 同一口径，
//     内部会先过 `KAGE::Util::Path::CreateCleanPath`），`x2` = 出参 `unsigned& count`；
//     返回一个 `new[]` 出来的块：`[0]` 是条目数（8 字节），条目紧跟其后、**每项 0x10 字节**；
//   * 释放：那块内存是引擎的 `operator new[]`（`_Znam`）分配的 ⇒ 必须用引擎的
//     `operator delete[]`（`_ZdaPv`，GOT 槽见下）释放，用我们的 `free` 会踩坏游戏堆。
inline constexpr uintptr_t kContentGetDirectoryEntriesOffset = 0x4C23C4;
inline constexpr std::array<u8, 16> kContentGetDirectoryEntriesExpectedBytes = {
    0xFF, 0x83, 0x01, 0xD1, 0xFD, 0x7B, 0x02, 0xA9,
    0xFD, 0x83, 0x00, 0x91, 0xF7, 0x1B, 0x00, 0xF9,
};
// `KAGE::Filesys::DirectoryEntry`（0x10 字节一项）：
//   * `+0x00` 标志字节：bit0 = 是文件、bit1 = 是目录。证据 = 引擎自己的两个内联访问器
//     `DirectoryEntry::IsFile()`（`0x4C2730`：`ldrb w8,[x0]` + `and w0,w8,#0x1`）与
//     `DirectoryEntry::IsDirectory()`（`0x4C273C`：`ldrb w8,[x0]` + `ubfx w0,w8,#1,#1`）；
//   * `+0x08` `char const* Name`。证据 = `DirectoryEntry::GetName()`（`0x4C2748`：`ldr x0,[x0,#0x8]`）；
//   * 每项 0x10：`GetDirectoryEntries` 里那条 `asr x8, x8, #4`（按 16 字节算条目数）与拷贝循环
//     里的 `add x21, x21, #0x10`。
inline constexpr std::size_t kDirectoryEntrySize = 0x10;
inline constexpr std::size_t kDirectoryEntryNameOffset = 0x08;
inline constexpr unsigned kDirectoryEntryFileBit = 0;
inline constexpr unsigned kDirectoryEntryDirectoryBit = 1;

// 引擎的 `operator delete[]`（`_ZdaPv`）**GOT 槽**：NRO 的重定位表里 `_ZdaPv` 只有一条
// `R_AARCH64_JUMP_SLOT`（`0xA9DD30`），运行期槽内就是真正要调用的地址。取槽里的值再调用，
// 既不必自己解析 PLT，也不会用到我们模块的 `free`。证据 = `tools/nro_symbols.py` 的
// `parse_dynamic_relocations()` 输出（`_ZdaPv: slots=['0xa9dd30'] type=[1026]`）。
inline constexpr uintptr_t kEngineArrayDeleteGotOffset = 0xA9DD30;

// `KAGE::Filesys::File`（引擎自己的文件类）—— 写文件用：
//   * 构造 `File::File()`（`0x4CCBC0`）：内部调 `BufferedFileStream` 的构造、写 vtable 到
//     `+0x00`/`+0x38`、清 `+0x50` 起两个 8 字节 ⇒ 对象至少 0x58 字节（调用方留 0x80 更稳）；
//   * `bool File::OpenWrite(char const* name)`（`0x4CCE38`）：路径会拼在
//     `g_SaveDataManager.GetMountPoint()` 后面（`0x4CCE48` 取全局 `g_SaveDataManager` 槽、
//     `0x4CCE54` 调 `SaveDataManager::GetMountPoint`），也就是**写进游戏的存档目录**，
//     不是任意 `sdmc:` 路径 —— 想要"模组的开关状态"这类持久化，这里正好是合适的位置；
//   * `File::Write(char const* data, int count, int elementSize)`（`0x4CD190`，尾调到
//     `BufferedStreamBase::WriteToBuffer`），底层是 `nn::fs::WriteFile`；
//   * `bool File::Close()`（`0x4CD00C`）。
inline constexpr uintptr_t kFilesysFileCtorOffset = 0x4CCBC0;
inline constexpr std::array<u8, 16> kFilesysFileCtorExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
inline constexpr uintptr_t kFilesysFileOpenWriteOffset = 0x4CCE38;
inline constexpr std::array<u8, 16> kFilesysFileOpenWriteExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x03, 0x00, 0xAA,
};
// `File::Write`（`0x4CD190`）是**虚函数覆盖**，尾调到 `BufferedStreamBase::WriteToBuffer`
// —— 写进的是缓冲，真机实测"打开成功但写入失败"（缓冲没 `Create` 过）。真正直连
// `nn::fs::WriteFile` 的是 `File::write_stream_data`（`0x4CD220`，2026-09-16 第二轮改用）：
// `0x4CD230` 先查 `[x0+0x40] == 1`（打开模式必须是"写"）、`0x4CD24C` 取 `[x0+0x50]` 的句柄，
// `0x4CD26C` 调 `nn::fs::WriteFile(handle, offset, data, size, option)`。
// `bool File::open_write(char const* name, char const* mount)`（`0x4CCEB0`）：**带自定义挂载点**的
// 打开写入口。⚠️ 2026-09-16 第二轮更正：**自定义挂载点并不等于"能写到任意位置"** —— 挂载点只是
// 拼在名字前面的前缀，最终仍由 `nn::fs::OpenFile` 在**游戏进程已挂载的那张表**里解析。游戏只挂了
// `rom`（`0x4CC6C8` 传字面量 `"rom"`）与存档（`"sdmc"`，见下文 `GetMountPoint`），**没有挂真 SD 卡**
// （它连 `nn::fs::MountSdCard` 都没导入）⇒ 传 `"sdmc:/SaltySD/…"` 只会落进存档分区。第一轮三个探针
// 文件因此全部"打开失败"（真机读数 + 上面的静态证据两条对得上）。
//
// 反汇编（逐条读出）：`w3 = 6`（`nn::fs` 的 `Write|AllowAppend`）；`x0 = this`、`x1 = name`、
// `x2 = mount` 原样传给 `File::open_file_handle`；成功后再尾调
// `BufferedFileStream::OpenWrite(name)`（建立缓冲层，也会把 `[this+0x40]` 置成"写"状态 ——
// `write_stream_data` 的前置检查就是它）。**注意**：这个返回值是"两步都对"才为真，
// 所以第一轮读数里的"打开失败"也可能来自第二步（缓冲层）。
inline constexpr uintptr_t kFilesysFileOpenWriteWithMountOffset = 0x4CCEB0;
inline constexpr std::array<u8, 16> kFilesysFileOpenWriteWithMountExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xC3, 0x00, 0x80, 0x52,
};
// `bool File::open_file_handle(char const* name, char const* mount, int mode)`（`0x4CD2D4`）：
//   * `x1` = 文件名、`x2` = 挂载点前缀、`w3` = `nn::fs::OpenMode`；
//   * 长度上限 `w23 = (mode & 2) ? 0x104 : 0x40`（写路径 260 字节）；
//   * `0x4CD358 bl KAGE::Util::Path::CreateCleanPath(name, mount, false, false)` ⇒ **拼路径**；
//   * `0x4CD374 bl nn::fs::OpenFile(&handle@this+0x50, 拼好的路径, mode)` ⇒ 用的是**游戏自己的
//     fs 会话**（这就是"我们模块自己起不了 sm/fs、但引擎能写"的原因）。
inline constexpr uintptr_t kFilesysFileOpenFileHandleOffset = 0x4CD2D4;
inline constexpr std::array<u8, 16> kFilesysFileOpenFileHandleExpectedBytes = {
    0xFD, 0x7B, 0xBC, 0xA9, 0xF8, 0x5F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF6, 0x57, 0x02, 0xA9,
};
// `File` 对象尺寸 = **0x60 字节**：证据 = `SaveDataManagerBase::OpenSaveFileForWriting`（`0x4CC278`）
// 里 `mov w0, #0x60; bl _Znwm; bl File::File()`（`0x4CC2C8`/`0x4CC2D0`/`0x4CC2D4`），
// 之后 `ldr x8,[x20]; ldr x8,[x8,#0x60]; blr x8` 就是引擎自己的"创建/打开写"虚调用。
inline constexpr std::size_t kFilesysFileObjectSize = 0x60;
// `File::Write`（`0x4CD190`）是**虚函数覆盖**，尾调到 `BufferedStreamBase::WriteToBuffer`
// —— 写进的是缓冲（真机第一轮"打开成功但写入失败"就是它）。本探针把两条路都试一遍，
// 所以这个入口也要有守卫。
inline constexpr uintptr_t kFilesysFileWriteOffset = 0x4CD190;
inline constexpr std::array<u8, 16> kFilesysFileWriteExpectedBytes = {
    0x5C, 0xCB, 0x06, 0x14, 0x00, 0xE0, 0x00, 0xD1,
    0x5A, 0xCB, 0x06, 0x14, 0x20, 0x00, 0xA0, 0x52,
};
inline constexpr uintptr_t kFilesysFileWriteStreamDataOffset = 0x4CD220;
inline constexpr std::array<u8, 16> kFilesysFileWriteStreamDataExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF5, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};
inline constexpr uintptr_t kFilesysFileCloseOffset = 0x4CD00C;
inline constexpr std::array<u8, 16> kFilesysFileCloseExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0xF3, 0x03, 0x00, 0xAA,
};
// 探针写完就删：`g_SaveDataManager` 的全局槽（`File::OpenWrite` 用的就是它）+
// `SaveDataManager::DeleteSaveFile(char const*)`（`0x4CDD18`）。
inline constexpr uintptr_t kSaveDataManagerGlobalSlotOffset = 0xAAC940;
inline constexpr uintptr_t kSaveDataDeleteFileOffset = 0x4CDD18;
inline constexpr std::array<u8, 16> kSaveDataDeleteFileExpectedBytes = {
    0xFD, 0x7B, 0xBD, 0xA9, 0xF5, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0xF4, 0x4F, 0x02, 0xA9,
};

// ---- 读回来的那半条通道 + "存档分区叫什么名字"（2026-09-16 第二轮探针新增）----
//
// `bool File::OpenRead(char const* path)`（`0x4CCD58`）：与 `OpenWrite` **不对称** ——
// `0x4CCD64` 把挂载点写成空串字面量（`0x8AED62`，编目里就是某个字符串的结尾那个 NUL，
// 链接器合并出来的空串），于是 `CreateCleanPath` 只返回原样路径 ⇒ **这里要传完整路径**
// （`"sdmc:/<名字>"`），不能像 `OpenWrite` 那样只传相对名字。之后同样尾调
// `BufferedFileStream::OpenRead`。
inline constexpr uintptr_t kFilesysFileOpenReadOffset = 0x4CCD58;
inline constexpr std::array<u8, 16> kFilesysFileOpenReadExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF4, 0x4F, 0x01, 0xA9,
    0xFD, 0x03, 0x00, 0x91, 0x02, 0x1F, 0x00, 0xD0,
};
// `int File::read_stream_data(char* dest, int count, int elementSize)`（`0x4CD1B0`）：
//   * `0x4CD1BC ldr w9,[x0,#0x40]; cbz` ⇒ 打开模式必须是"读"（`0x4CD1C4` 返回 0 = 失败）；
//   * `0x4CD1D4 mul w9, w3, w2` ⇒ 字节数 = `count * elementSize`（与写侧同形）；
//   * `0x4CD1EC ldp x1,x2,[x0,#0x50]` 取句柄与偏移、`0x4CD1FC bl nn::fs::ReadFile`；
//   * `0x4CD204 ldr x0,[x29,#0x18]` ⇒ **返回值就是读到的字节数**（0 = 失败）。
inline constexpr uintptr_t kFilesysFileReadStreamDataOffset = 0x4CD1B0;
inline constexpr std::array<u8, 16> kFilesysFileReadStreamDataExpectedBytes = {
    0xFD, 0x7B, 0xBE, 0xA9, 0xF3, 0x0B, 0x00, 0xF9,
    0xFD, 0x03, 0x00, 0x91, 0x09, 0x40, 0x40, 0xB9,
};
// `int File::GetLength() const`（`0x4CD104`）：取 `[x0+0x50]` 的句柄问长度。
inline constexpr uintptr_t kFilesysFileGetLengthOffset = 0x4CD104;
inline constexpr std::array<u8, 16> kFilesysFileGetLengthExpectedBytes = {
    0xFF, 0x83, 0x00, 0xD1, 0xFD, 0x7B, 0x01, 0xA9,
    0xFD, 0x43, 0x00, 0x91, 0xFF, 0x07, 0x00, 0xF9,
};
// `char const* SaveDataManager::GetMountPoint() const`（`0x4CDD9C`）—— **游戏存档分区的真实挂载点**。
// 这个名字来自它的函数体（两条独立证人）：
//   ① `0x4CDD9C adrp x0, 0x892000; 0x4CDDA0 add x0, x0, #0x7E3` ⇒ 返回字面量 `0x8927E3`，
//      `Repentance.nro` 该处逐字节为 `"sdmc:/"`；
//   ② `SaveDataManager::Initialize`（`0x4CD95C`）把同一个虚函数槽（`ldr x8,[x19]; ldr x8,[x8,#0x30]`
//      ⇒ `R_AARCH64_JUMP_SLOT` 指向本函数）取回来的字符串用 `strstr(…, ":/")` 截断成 `"sdmc"`，
//      再 `0x4CDA54/0x4CDA74 bl nn::fs::MountSaveData(截断后的名字, uid)`。
// **由此可知**：游戏嘴里的 `sdmc:` **不是真 SD 卡**，而是它自己的存档分区 —— 我们第一轮探针把
// `"sdmc:/SaltySD/plugins/<title>/"` 当挂载点传进去，路径实际落在了存档里，所以三个用例全"打开失败"。
// 顺带解释了更早那次 `nn::fs::OpenFile` 上的 `nn::diag::Abort`（结果码 `0x2EE802`）：
// 那是 `fs::ResultInvalidCharacter`（6004，非法路径字符）——把一个带 `:` 的完整路径当相对名字传了。
inline constexpr uintptr_t kSaveDataManagerGetMountPointOffset = 0x4CDD9C;
inline constexpr std::array<u8, 16> kSaveDataManagerGetMountPointExpectedBytes = {
    0x20, 0x1E, 0x00, 0xB0, 0x00, 0x8C, 0x1F, 0x91,
    0xC0, 0x03, 0x5F, 0xD6, 0xFD, 0x7B, 0xBF, 0xA9,
};
// 两个 `nn::fs` 的 **导入跳板**（我们没有 `nn::fs` 的符号表，只有游戏自己的 `.got` 槽与跳板）：
//   * `0x680200` = `bool nn::fs::IsMounted(char const* mountName)`（槽 `0xAA6068`）；
//   * `0x680220` = `Result nn::fs::CommitSaveData(char const* mountName)`（槽 `0xAA6078`）。
// 证据：`tools/nro_symbols.py` 的 `parse_dynamic_relocations()` 给出这两个槽的
// `R_AARCH64_JUMP_SLOT` 记录（符号名即上面两条），跳板本身是
// `adrp x16, …; ldr x17, [x16, #imm]; add x16, x16, #imm; br x17`（16 字节，见下面的守卫字节）。
// `CommitSaveData` 这一步不是可选的：Switch 的存档是"提交式"的，写完不提交，重启后可能什么都看不到
// （游戏自己的 `SaveDataManager::commit_save_data()`（`0x4CDD94`）就是 `ldr x0,[x0,#0x10]`（挂载名）
// 之后 `b 0x680220`）。
inline constexpr uintptr_t kFilesysIsMountedThunkOffset = 0x680200;
inline constexpr std::array<u8, 16> kFilesysIsMountedThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xD0, 0x11, 0x36, 0x40, 0xF9,
    0x10, 0xA2, 0x01, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysCommitSaveDataThunkOffset = 0x680220;
inline constexpr std::array<u8, 16> kFilesysCommitSaveDataThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xD0, 0x11, 0x3E, 0x40, 0xF9,
    0x10, 0xE2, 0x01, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};

// ---- 直连 `nn::fs` 的整组导入跳板（2026-09-16 第三轮探针新增）----
//
// 为什么直连：走引擎 `File` 类要跟它的缓冲/流模式语义打交道（第二轮 `Close()` 的返回类型其实是
// `void`，我们把它的垃圾返回值当 bool 读，才得出假的"关闭失败"）；直连 `nn::fs` 每一步都有
// `Result` 码（0 = 成功），没有缓冲层，也没有对象状态。
//
// 每个函数的名字与签名都由**引擎自己的调用点**反推（不是猜的）：
//   * `OpenFile(FileHandle* out, char const* path, int mode)`（`0x4CD374`，`0x4CCD78`：读传 1、写传 6）；
//   * `CreateFile(char const* path, s64 size)`（`0x4CD384`：`x0 = 拼好的路径`、`x1 = 1`）；
//   * `WriteFile(FileHandle, s64 offset, void const* data, size_t size, WriteOption const& option)`
//     （`0x4CD26C`：`x4 = 栈上清零的 4 字节 option`）；
//   * `SetFileSize(FileHandle, s64)`（`0x4CD2AC`：写完把位置当新长度设回去，所以第二轮
//     `GetLength()` 才等于 27）；
//   * `ReadFile(u64* outSize, FileHandle, s64 offset, void* buffer, size_t size)`（`0x4CD1FC`）；
//   * `GetFileSize(s64* outSize, FileHandle)`、`FlushFile(FileHandle)`、`CloseFile(FileHandle)`
//     （`File::Exists` 用过 `CloseFile`：`0x4CCD30`）。
// 槽地址与跳板地址的对应关系来自 `tools/nro_symbols.py` 的 `parse_dynamic_relocations()`。
inline constexpr uintptr_t kFilesysOpenFileThunkOffset = 0x680060;
inline constexpr std::array<u8, 16> kFilesysOpenFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xCE, 0x47, 0xF9,
    0x10, 0x62, 0x3E, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysCreateFileThunkOffset = 0x680110;
inline constexpr std::array<u8, 16> kFilesysCreateFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xFA, 0x47, 0xF9,
    0x10, 0xC2, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysWriteFileThunkOffset = 0x6800F0;
inline constexpr std::array<u8, 16> kFilesysWriteFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xF2, 0x47, 0xF9,
    0x10, 0x82, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysReadFileThunkOffset = 0x6800E0;
inline constexpr std::array<u8, 16> kFilesysReadFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xEE, 0x47, 0xF9,
    0x10, 0x62, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysSetFileSizeThunkOffset = 0x680100;
inline constexpr std::array<u8, 16> kFilesysSetFileSizeThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xF6, 0x47, 0xF9,
    0x10, 0xA2, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysFlushFileThunkOffset = 0x6800C0;
inline constexpr std::array<u8, 16> kFilesysFlushFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xE6, 0x47, 0xF9,
    0x10, 0x22, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysGetFileSizeThunkOffset = 0x6800D0;
inline constexpr std::array<u8, 16> kFilesysGetFileSizeThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xEA, 0x47, 0xF9,
    0x10, 0x42, 0x3F, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
inline constexpr uintptr_t kFilesysCloseFileThunkOffset = 0x680070;
inline constexpr std::array<u8, 16> kFilesysCloseFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xB0, 0x11, 0xD2, 0x47, 0xF9,
    0x10, 0x82, 0x3E, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// `nn::fs::DeleteFile(char const* path)`（跳板 `0x680210`，槽 `0xAA6070`）：
// 删掉存档里的一个文件。游戏自己不用它（它用 `SaveDataManager::DeleteSaveFile`），
// 但我们清理探针/过期状态文件时需要"按完整路径删"这一个入口。
inline constexpr uintptr_t kFilesysDeleteFileThunkOffset = 0x680210;
inline constexpr std::array<u8, 16> kFilesysDeleteFileThunkExpectedBytes = {
    0x30, 0x21, 0x00, 0xD0, 0x11, 0x3A, 0x40, 0xF9,
    0x10, 0xC2, 0x01, 0x91, 0x20, 0x02, 0x1F, 0xD6,
};
// `nn::fs::OpenMode`：读 = 1、写 = 6（`Write|AllowAppend`，引擎两个打开点用的就是这两个值）。
inline constexpr int kVfsOpenModeRead = 1;
inline constexpr int kVfsOpenModeWrite = 6;
// `File` 里那个 `FileStream` 子对象的**流模式**字段：`FileStream` 子对象从 `File+0x38` 开始，
// 它的 `+0x08` 就是模式 ⇒ 在 `File` 里的偏移 = `0x38 + 0x08` = `0x40`。
// 取值：`OpenRead` 写 0、`OpenWrite` 写 1、`Close` 写 2（证据：`FileStream::OpenRead 0x4CD47C`
// 的 `str wzr,[x0,#0x8]`、`FileStream::OpenWrite 0x4CD4B4` 的 `mov w8,#0x1; str w8,[x0,#0x8]`、
// `FileStream::Close 0x4CD500` 的 `mov w8,#0x2; str w8,[x0,#0x8]`）。
// `write_stream_data`/`read_stream_data` 的前置检查（`0x4CD230 cmp w9,#0x1`、`0x4CD1C0 cbz w9`）
// 正好按这个字段判方向 —— 所以"写完再读"必须换一个**新构造**的 `File`。
inline constexpr std::size_t kFilesysFileStreamModeOffset = 0x40;

// The engine clears and rebuilds the whole table on content reload, so a Mod mount
// point has to be re-registered every time this function returns.
inline constexpr uintptr_t kRebuildContentMountPointsOffset = 0x3B3510;
inline constexpr std::array<u8, 16> kRebuildContentMountPointsExpectedBytes = {
    0xFF, 0x43, 0x02, 0xD1, 0xFD, 0x7B, 0x06, 0xA9,
    0xFD, 0x83, 0x01, 0x91, 0xF5, 0x3B, 0x00, 0xF9,
};
// The Mod root prefix the Runtime addresses (`rom:/isaac_mods/mods/`); the content
// mount adapter strips the application prefix before handing a path to the engine,
// whose own path constructor adds it back.
inline constexpr char kModAddressRootPrefix[] = "rom:/isaac_mods/mods/";

// Rebuild relay: `RebuildContentMountPoints()` starts with `ClearMountPoints(false)`
// and rebuilds the whole mount point table (base content, add-on content, Rep+ patch),
// so a Mod mount point disappears with every content reload. The relay patches the
// function's **only** `ret` (`0x3B36F4`, verified: one `ret` in the body and every early
// exit funnels into it) with a branch to a stub in the last .text zero page; the stub
// calls a callback published through its own slot and then executes the original `ret`.
// Patching the single return covers all four call sites, one of which is a tail call.
inline constexpr uintptr_t kRebuildMountPointsRelayOffset = 0x3B36F4;
inline constexpr std::array<u8, 16> kRebuildMountPointsRelayExpectedOriginal = {
    0xC0, 0x03, 0x5F, 0xD6, 0xE0, 0x23, 0x40, 0xF9,
    0x05, 0xF1, 0x0A, 0x94, 0xE8, 0x03, 0x40, 0x39,
};
inline constexpr uintptr_t kRebuildMountPointsRelayCodeOffset = 0x68CFA0;
inline constexpr uintptr_t kRebuildMountPointsRelaySlotOffset = 0x68CFC8;
inline constexpr std::array<u8, 4> kRebuildMountPointsRelayExpectedEntry = {
    0x2B, 0x66, 0x0B, 0x14,
};
inline constexpr std::array<u8, 0x30> kRebuildMountPointsRelayExpectedBytes = {
    0xFF, 0x83, 0x00, 0xD1, 0xE0, 0x7B, 0x00, 0xA9,
    0x11, 0x01, 0x00, 0x10, 0x30, 0xFE, 0xDF, 0xC8,
    0x50, 0x00, 0x00, 0xB4, 0x00, 0x02, 0x3F, 0xD6,
    0xE0, 0x7B, 0x40, 0xA9, 0xFF, 0x83, 0x00, 0x91,
    0xC0, 0x03, 0x5F, 0xD6, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
inline constexpr char kLogDirectory[] = "/atmosphere/logs";
inline constexpr char kLogPath[] = "/atmosphere/logs/isaac-runtime-probe.log";
