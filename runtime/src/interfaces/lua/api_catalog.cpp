#include "interfaces/lua/api_catalog.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

constexpr std::uint32_t MakeId(ApiDomain domain, std::uint8_t ownerGroup,
                               std::uint16_t sequence) noexcept {
    return (static_cast<std::uint32_t>(domain) << 24) |
           (static_cast<std::uint32_t>(ownerGroup) << 16) |
           static_cast<std::uint32_t>(sequence);
}

constexpr ApiVersion kV1{1, 0};

bool SameText(const char* left, const char* right) noexcept {
    if (left == nullptr || right == nullptr) {
        return false;
    }
    return std::strcmp(left, right) == 0;
}

bool EmptyText(const char* text) noexcept {
    return text == nullptr || text[0] == '\0';
}

// The table is the single list of Lua APIs this Runtime exposes. Maturity is
// deliberately conservative: only APIs with hardware evidence are marked
// HardwareVerified, and persistence stays Experimental while Stage145 is open.
constexpr LuaApiDescriptor kDefaultApis[] = {
    // Global surface.
    {MakeId(ApiDomain::Global, 1, 0x0001), ApiDomain::Global, "Global", "RegisterMod", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Global, 1, 0x0002), ApiDomain::Global, "Global", "require", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Global, 1, 0x0003), ApiDomain::Global, "Global", "load", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Disabled},
    {MakeId(ApiDomain::Global, 1, 0x0004), ApiDomain::Global, "Global", "loadfile", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Disabled},
    {MakeId(ApiDomain::Global, 1, 0x0005), ApiDomain::Global, "Global", "dofile", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Disabled},
    {MakeId(ApiDomain::Global, 1, 0x0010), ApiDomain::Global, "Global", "Game", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Global, 1, 0x0011), ApiDomain::Global, "Global", "MusicManager", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Global, 1, 0x0012), ApiDomain::Global, "Global", "RNG", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Global, 1, 0x0013), ApiDomain::Global, "Global", "ModCallbacks", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::HardwareVerified},
    // 版本判定全局：PC 引擎在忏悔版里定义 `REPENTANCE`，Mod 用它选代码分支（EID 就是靠它
    // 决定走 Repentance 还是 AB+）。Switch 版 1.7.9b 就是忏悔版，所以恒为 `true` 的布尔值，
    // 没有 `lua_CFunction`；`REPENTANCE_PLUS` 刻意不提供（PC 专属更新，Mod 会按非 Plus 处理）。
    {MakeId(ApiDomain::Global, 1, 0x001D), ApiDomain::Global, "Global", "REPENTANCE", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x001A), ApiDomain::Global, "Global", "KColor", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x001B), ApiDomain::Global, "Global", "Vector", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x001C), ApiDomain::Global, "Global", "Sprite", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x0014), ApiDomain::Global, "Global", "Music", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Global, 1, 0x0015), ApiDomain::Global, "Global", "Input", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x0016), ApiDomain::Global, "Global", "Keyboard", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x0017), ApiDomain::Global, "Global", "ButtonAction", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 1, 0x0018), ApiDomain::Global, "Global", "InputHook", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    // The `Font` constructor builds an object this Runtime owns (see
    // `interfaces/lua/font_api.cpp`); it runs wherever a Mod callback runs.
    {MakeId(ApiDomain::Global, 1, 0x0019), ApiDomain::Global, "Global", "Font", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 全局 `Color(...)`：PC 的 `Color` 与 `KColor` 是同一个类（`KColor` 是别名之一），
    // 所以这里把**同一个类表**再挂一个全局名（`lua_runtime.cpp` 的 `RegisterColorApi`），
    // 七个参数的重载 `Color(R,G,B[,A[,RO,GO,BO]])` 由 `color_api.cpp` 的
    // `CreateColorHandle` 实现（EID 的 `renderIcon` 用的就是七参形式）。
    {MakeId(ApiDomain::Global, 1, 0x001E), ApiDomain::Global, "Global", "Color", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    // 全局 `GetPtrHash(object)`：PC 的"把对象指针变成稳定整数"（`GlobalFunctions.md:143`），
    // EID 用它做实体身份比较（`main.lua:492`/`527`/`1473`，`features/eid_api.lua:2387`）。
    // 我们只持有 `Entity`/`EntityPlayer`/`EntityPickup` 三种**原生指针**句柄
    // （`Game`/`Level`/`Room` 的句柄是占位符，见 `lua_object_handles.hpp`），所以只对它们返回
    // 真实指针整数，其余返回 0（见 `isaac_api.cpp` 的 `GlobalGetPtrHash`）。
    {MakeId(ApiDomain::Global, 1, 0x001F), ApiDomain::Global, "Global", "GetPtrHash", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // Mod surface.
    {MakeId(ApiDomain::Mod, 1, 0x0001), ApiDomain::Mod, "Mod", "AddCallback", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Persistence, 1, 0x0001), ApiDomain::Persistence, "Mod", "SaveData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Persistence, 1, 0x0002), ApiDomain::Persistence, "Mod", "LoadData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Persistence, 1, 0x0003), ApiDomain::Persistence, "Mod", "HasData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Persistence, 1, 0x0004), ApiDomain::Persistence, "Mod", "RemoveData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // Game surface.
    {MakeId(ApiDomain::Game, 1, 0x0001), ApiDomain::Game, "Game", "IsPaused", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Game, 1, 0x0002), ApiDomain::Game, "Game", "IsGreedMode", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Game, 1, 0x0003), ApiDomain::Game, "Game", "GetLevel", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Game, 1, 0x0004), ApiDomain::Game, "Game", "GetItemPool", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Game, 1, 0x0005), ApiDomain::Game, "Game", "GetRoom", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    // 批次 3：EID 的 `OnRender` 早期就要用到的两个 Game 方法。
    // `GetNumPlayers` 读 `players` 向量（与 `Isaac.GetPlayer` 同一条指针链）：
    // `EID:setPlayer()`（`main.lua:1062`）用它决定单人快速路径还是多人分支。
    // `GetFrameCount` 读引擎自己的帧计数 `Game + 0x24F99C`（`Game::Update` 每帧 +1，
    // 见 `runtime_constants.hpp`），`main.lua:1210`/`1044`/`955` 都在用；拿不到时退化为
    // 本 Runtime 的 `ManagedFrameClock`（近似值，注释里写明）。
    {MakeId(ApiDomain::Game, 1, 0x0006), ApiDomain::Game, "Game", "GetNumPlayers", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Game, 1, 0x0007), ApiDomain::Game, "Game", "GetFrameCount", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    // `Game:GetSeeds()` / `Game:GetVictoryLap()`（2026-09-12 第五轮，真机报告 `01789210180`）。
    //
    // EID 的 `features/eid_api.lua:2046` 写的是
    //   `if not game:GetSeeds():IsCustomRun() and not EID:PlayersHaveCollectible(...) then`
    // —— `GetSeeds()` 返回 nil 就整条 update 回调报错、被派发器静默摘除（屏幕全空）。
    // 这两个访问器在 Switch 符号表里没有对应方法（`IsaacRepentance::Game` 类没有
    // `GetSeeds`/`GetVictoryLap`），说明 PC 那条 Lua 访问器是**字段直读**，而字段偏移尚未定位；
    // 因此先给"有对象、有方法、值自洽"的最小实现，成熟度 `Experimental`。
    {MakeId(ApiDomain::Game, 1, 0x0008), ApiDomain::Game, "Game", "GetSeeds", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Game, 1, 0x0009), ApiDomain::Game, "Game", "GetVictoryLap", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // `Seeds` 家族（`Game:GetSeeds()` 的返回值）。EID 只用到这两个：
    //   * `IsCustomRun()` —— PC 语义"挑战局或带种子的局"（IsaacDocs 原文）；
    //   * `GetStartSeed()` —— EID 拿它当缓存键（`eid_bagofcrafting.lua:822`、
    //     `eid_api.lua:2887`），值自洽即可，不参与任何显示内容。
    {MakeId(ApiDomain::Seed, 1, 0x0001), ApiDomain::Seed, "Seeds", "IsCustomRun", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Seed, 1, 0x0002), ApiDomain::Seed, "Seeds", "GetStartSeed", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Level, 1, 0x0001), ApiDomain::Level, "Level", "GetStage", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Level, 1, 0x0002), ApiDomain::Level, "Level", "IsAscent", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HardwareVerified},
    // 批次 4：`Level:GetCurses()`。`Level` **内嵌在 `Game` 起始处**（同一地址），所以读的是
    // `Game + 0x0C`（u32 诅咒位掩码）。**语义偏差**见 `remaining_api.cpp` 的注释：PC 是
    // `curses | 永久诅咒 & ~禁用诅咒` 三步，那两次调用的函数地址本轮未定位，这里**只返回字段值**。
    {MakeId(ApiDomain::Level, 1, 0x0003), ApiDomain::Level, "Level", "GetCurses", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 批次 8（2026-09-15）：EID 在**描述构建**路径上无条件调用、此前整条缺失的四个 `Level` 成员。
    // 缺口来自 `tools/eid_api_gap_report.py` 的高置信表（`missing_in_runtime`）；底座依据见
    // `docs/PC-Lua-API-对照清单.md`（该表 §3.3 已证明 `file_offset` 等于运行时模块偏移）：
    //   * `GetAbsoluteStage()` —— Switch 有符号 `_ZNK15IsaacRepentance5Level16GetAbsoluteStageEv`
    //     @ `0x3E7F3C`，安装期按 16 字节守卫校验后直接调用；
    //   * `IsNextStageAvailable()` —— 符号 @ `0x3DBDBC`，同上；
    //   * `GetCurrentRoomIndex()` —— 字段读 `Level + 0x21558`，依据是固定 NRO 里
    //     `Level::GetCurrentRoomDesc` 的反汇编：它把这个字段当 `Level::GetRoomByIdx(int,int)`
    //     的第一个实参（`docs/PC-Mod-兼容矩阵.md` 的 Stage104 条目）；
    //   * `GetCurrentRoom()` —— PC 语义就是"当前 `Room`"，与 `Game:GetRoom()` 是同一个对象
    //     （都读 `Game + 0x21550`），所以直接复用那条**已上机验证**的读取链 ⇒ 成熟度与
    //     `Game:GetRoom` 同级记 `HostVerified`，其余三条记为待真机确认的 `Experimental`。
    {MakeId(ApiDomain::Level, 1, 0x0004), ApiDomain::Level, "Level", "GetCurrentRoomIndex", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Level, 1, 0x0005), ApiDomain::Level, "Level", "GetCurrentRoom", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Level, 1, 0x0006), ApiDomain::Level, "Level", "GetAbsoluteStage", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Level, 1, 0x0007), ApiDomain::Level, "Level", "IsNextStageAvailable", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0001), ApiDomain::Room, "Room", "GetType", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    // 批次 7（2026-09-12）：EID 在网格/寻路路径上无条件调用的一批 `Room` 成员
    // （`eid_api.lua:3351` 的 `EvaluateLocation`、`3371` 的 `HasPathToPosition`、`2593` 的
    // 网格实体查询）。缺任何一个都是"调 nil"、整条回调被摘除。当前是**保守占位**实现：
    // 宽高给标准房间边长 13（避免除零）、`GetGridPath` 恒报不可走、`GetGridEntity` 返回 nil，
    // 因此 EID 的寻路判定恒为"没有可达路径"—— 不会用假数据画出错误提示。要变真实现需先做
    // 静态审计定位 `Room::GetGridIndex`/`GetGridEntity` 的文件偏移与网格尺寸字段偏移。
    {MakeId(ApiDomain::Room, 1, 0x0002), ApiDomain::Room, "Room", "GetGridWidth", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0003), ApiDomain::Room, "Room", "GetGridHeight", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0004), ApiDomain::Room, "Room", "GetGridSize", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0005), ApiDomain::Room, "Room", "GetGridIndex", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0006), ApiDomain::Room, "Room", "GetGridPath", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Room, 1, 0x0007), ApiDomain::Room, "Room", "GetGridEntity", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::ItemPool, 1, 0x0001), ApiDomain::ItemPool, "ItemPool", "GetCollectible", kV1,
     0, ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::ItemPool, 1, 0x0002), ApiDomain::ItemPool, "ItemPool", "GetLastPool", kV1,
     0, ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // Music surface.
    {MakeId(ApiDomain::Music, 1, 0x0001), ApiDomain::Music, "MusicManager", "GetCurrentMusicID",
     kV1, 0, ThreadAffinity::MainRender, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Music, 1, 0x0002), ApiDomain::Music, "MusicManager", "Pause", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::HardwareVerified},
    {MakeId(ApiDomain::Music, 1, 0x0003), ApiDomain::Music, "MusicManager", "Resume", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::HardwareVerified},

    // RNG surface.
    {MakeId(ApiDomain::Rng, 1, 0x0001), ApiDomain::Rng, "RNG", "SetSeed", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},
    {MakeId(ApiDomain::Rng, 1, 0x0002), ApiDomain::Rng, "RNG", "Next", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::HostVerified},

    // Input surface: reachability was probed, the edge timing is still unproven.
    {MakeId(ApiDomain::Input, 1, 0x0001), ApiDomain::Input, "Input", "IsButtonTriggered", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Input, 1, 0x0002), ApiDomain::Input, "Input", "IsButtonPressed", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Input, 1, 0x0003), ApiDomain::Input, "Input", "GetButtonValue", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Input, 1, 0x0004), ApiDomain::Input, "Input", "IsActionTriggered", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Input, 1, 0x0005), ApiDomain::Input, "Input", "IsActionPressed", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Input, 1, 0x0006), ApiDomain::Input, "Input", "GetActionValue", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},

    // Font surface. `Font` is the first family whose native object the Runtime allocates itself
    // (`~Font()` does not delete `this`), so every entry point here is a call into a game function
    // with a pointer this Runtime owns. Loading reads through KAGE's file manager and the metrics
    // read the loaded glyph tables; nothing here renders, and Lua only ever runs on the game
    // thread inside the Runtime's callback scope, so `ManagedCallback` is the honest affinity and
    // no frame phase is implied.
    //
    // All nine rows share owner group 2 of the Global domain: the group field is what separates
    // `Font` owns a domain of its own, matching every other object family (`Rng`, `Input`, ...):
    // the domain byte is part of the wire id, so reusing `Global` would make the ids collide with
    // the global table's own sequence.
    {MakeId(ApiDomain::Font, 1, 0x0001), ApiDomain::Font, "Font", "Load", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0002), ApiDomain::Font, "Font", "Unload", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0003), ApiDomain::Font, "Font", "IsLoaded", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0004), ApiDomain::Font, "Font", "GetStringWidth", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0005), ApiDomain::Font, "Font", "GetStringWidthUTF8", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0006), ApiDomain::Font, "Font", "GetLineHeight", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0007), ApiDomain::Font, "Font", "GetBaselineHeight", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0008), ApiDomain::Font, "Font", "GetCharacterWidth", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x0009), ApiDomain::Font, "Font", "SetMissingCharacter", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x000A), ApiDomain::Font, "Font", "DrawString", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x000B), ApiDomain::Font, "Font", "DrawStringScaled", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x000C), ApiDomain::Font, "Font", "DrawStringUTF8", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Font, 1, 0x000D), ApiDomain::Font, "Font", "DrawStringScaledUTF8", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},


    // `Vector` = `KAGE::Math::Vector2`（8 字节 `{float X, float Y}`，无虚表）。整族都是纯算术，
    // 不需要任何原生入口，所以全部为 Experimental，等真机验收后再提升成熟度。
    // `X`/`Y` 是字段（走 `__index`/`__newindex`），运算符在元表里，两者都不占 catalog 行。
    {MakeId(ApiDomain::Vector, 1, 0x0001), ApiDomain::Vector, "Vector", "Length", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0002), ApiDomain::Vector, "Vector", "LengthSquared", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0003), ApiDomain::Vector, "Vector", "Distance", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0004), ApiDomain::Vector, "Vector", "DistanceSquared", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0005), ApiDomain::Vector, "Vector", "Dot", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0006), ApiDomain::Vector, "Vector", "Cross", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0007), ApiDomain::Vector, "Vector", "GetAngleDegrees", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0008), ApiDomain::Vector, "Vector", "Normalize", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0009), ApiDomain::Vector, "Vector", "Normalized", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000A), ApiDomain::Vector, "Vector", "Resize", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000B), ApiDomain::Vector, "Vector", "Resized", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000C), ApiDomain::Vector, "Vector", "Rotated", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000D), ApiDomain::Vector, "Vector", "Clamp", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000E), ApiDomain::Vector, "Vector", "Clamped", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x000F), ApiDomain::Vector, "Vector", "Lerp", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Vector, 1, 0x0010), ApiDomain::Vector, "Vector", "FromAngle", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},


    // Lua 的 `Sprite` = 引擎的 `IsaacRepentance::ANM2`。第二步补上需要 libc++ `std::string` 的三个
    // 入口（`Load`/`LoadGraphics`/`ReplaceSpritesheet`）；它们的路径串由运行时借游戏自己导入的
    // `basic_string::assign(char const*)` 代建。
    {MakeId(ApiDomain::Sprite, 1, 0x0001), ApiDomain::Sprite, "Sprite", "Play", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0002), ApiDomain::Sprite, "Sprite", "SetAnimation", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0003), ApiDomain::Sprite, "Sprite", "SetFrame", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0004), ApiDomain::Sprite, "Sprite", "GetFrame", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0005), ApiDomain::Sprite, "Sprite", "SetLayerFrame", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0006), ApiDomain::Sprite, "Sprite", "GetLayerFrame", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0007), ApiDomain::Sprite, "Sprite", "Update", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0008), ApiDomain::Sprite, "Sprite", "IsPlaying", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0009), ApiDomain::Sprite, "Sprite", "IsFinished", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000A), ApiDomain::Sprite, "Sprite", "PlayRandom", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000B), ApiDomain::Sprite, "Sprite", "Render", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000C), ApiDomain::Sprite, "Sprite", "RenderLayer", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    // `Sprite:GetTexel`（2026-09-12，真机报告 `01789211565`）：EID 的 `IsAltChoice`
    // （`main.lua:230`）用它逐像素比对底座贴图；方法不存在就是"调 nil"、整条渲染链被摘除。
    // 当前是常量实现（Switch 的 `ANM2` 没有 `GetTexel`，真实像素链路未定位），成熟度
    // 因此标 `Experimental` —— 详见 `sprite_api.cpp` 里该函数的注释。
    {MakeId(ApiDomain::Sprite, 1, 0x0012), ApiDomain::Sprite, "Sprite", "GetTexel", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000D), ApiDomain::Sprite, "Sprite", "IsLoaded", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000E), ApiDomain::Sprite, "Sprite", "Load", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x000F), ApiDomain::Sprite, "Sprite", "LoadGraphics", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Sprite, 1, 0x0010), ApiDomain::Sprite, "Sprite", "ReplaceSpritesheet", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 批次 5：`Sprite:GetAnimation()` —— 纯读 `ANM2+0x38`（引擎自己的 `ANM2::IsPlaying` 读的是
    // 同一处布局），没有引擎调用，宿主测试可完整覆盖。
    {MakeId(ApiDomain::Sprite, 1, 0x0011), ApiDomain::Sprite, "Sprite", "GetAnimation", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // `Isaac` 门面：PC Mod 的第一句常常就是 `Isaac.GetItemConfig()`（EID 正是如此），
    // 所以这一族先保证**存在且被调用时不报 Lua 错误**。
    //
    // 有真实行为的成员：`GetFrameCount`/`GetTime` 读 Runtime 自己的帧计数、
    // `DebugString` 走 Lua `print` 的同一个输出通道、`IsInGame` 读托管回调作用域、
    // `RunCallback` 走 Runtime 自己的回调注册表，以及批次 2 起的 `GetPlayer`（引擎基址 →
    // `Game*` → `players` 向量 → `Entity_Player`，带 vptr 校验）；其余是安全 stub——返回安全
    // 默认值，并在同一个输出通道上每个成员每会话告警一次。引擎数据（ItemConfig、
    // FindByType ...）要等引擎结构偏移定位后再接，因此整族一律 `Experimental`。
    {MakeId(ApiDomain::Isaac, 1, 0x0001), ApiDomain::Isaac, "Isaac", "GetFrameCount", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0002), ApiDomain::Isaac, "Isaac", "GetTime", kV1, 0,
     ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0003), ApiDomain::Isaac, "Isaac", "DebugString", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0004), ApiDomain::Isaac, "Isaac", "IsInGame", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0005), ApiDomain::Isaac, "Isaac", "RunCallback", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0006), ApiDomain::Isaac, "Isaac", "GetPlayer", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0007), ApiDomain::Isaac, "Isaac", "GetItemConfig", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0008), ApiDomain::Isaac, "Isaac", "FindByType", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0009), ApiDomain::Isaac, "Isaac", "FindInRadius", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000A), ApiDomain::Isaac, "Isaac", "CountEnemies", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000B), ApiDomain::Isaac, "Isaac", "CountBosses", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000C), ApiDomain::Isaac, "Isaac", "WorldToScreen", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000D), ApiDomain::Isaac, "Isaac", "WorldToRenderPosition", kV1,
     0, ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000E), ApiDomain::Isaac, "Isaac", "GetPersistentGameData", kV1,
     0, ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x000F), ApiDomain::Isaac, "Isaac", "GetTrinketIdByName", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0010), ApiDomain::Isaac, "Isaac", "GetCallbacks", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0011), ApiDomain::Isaac, "Isaac", "LoadModData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0012), ApiDomain::Isaac, "Isaac", "SaveModData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0013), ApiDomain::Isaac, "Isaac", "RenderScaledText", kV1, 0,
     ThreadAffinity::MainRender, ApiMaturity::Experimental},

    // 批次 2：引擎实体只读视图。`Isaac.GetPlayer`（条目 `0x0E010006`，id 不变）从批次 2 起是
    // 真实现（读引擎内存）；另外四个方法挂在 `Entity` / `EntityPlayer` 的元表上（owner 就是元表名）。
    //
    // 域仍是 `Isaac`（都是引擎门面这一族），sequence 从 0x0014 继续，id 不复用。
    // `EntityPlayer` 的字段（`Position`/`Type`/`Variant`/`SubType`/`Index`/`PlayerType`/`Size`）
    // **不占 catalog 行**：它们是 `__index` 里的名字、没有独立的 `lua_CFunction`，
    // 与 `Vector` 的 `X`/`Y` 同例（Catalog 门禁要求"登记了就必须真的被注册"）。
    // `GetData` 只是"存在且返回 nil"的占位：ModData 结构还没有证据，所以它不是 stub 告警，
    // 而是明确的"暂缺"。
    {MakeId(ApiDomain::Isaac, 1, 0x0014), ApiDomain::Isaac, "Entity", "ToPlayer", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0015), ApiDomain::Isaac, "EntityPlayer", "HasCollectible", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0016), ApiDomain::Isaac, "EntityPlayer", "GetPlayerType", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0017), ApiDomain::Isaac, "EntityPlayer", "GetData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // 批次 2b：`ItemConfig` 只读视图。`Isaac.GetItemConfig`（条目 `0x0E010007`，id 不变）从批次 2b
    // 起是真实现（`IC = Manager + 0x36538` 的内嵌对象），另外七个方法挂在 `ItemConfig` /
    // `ItemConfig_Item` 的元表上（owner 就是元表名）。
    //
    // 域仍是 `Isaac`，sequence 从 0x0018 继续，id 不复用。条目的字段
    // （`ID`/`Type`/`Name`/`Description`）与 `EntityPlayer` 的 `Position` 同例：`__index` 里的名字、
    // 没有独立的 `lua_CFunction`，所以**不占 catalog 行**（Catalog 门禁要求"登记了就必须真的被注册"）。
    // `HasTags` 两条都是"存在但恒假"的降级实现：`Item` 的 tags 字段偏移未定位（见
    // `isaac_api.cpp` 的注释），所以它们不是 stub 告警，而是明确的"没有证据、安全默认值"。
    // PC 里 `HasTags` 挂在条目上（`item:HasTags(tag)`），本批次两处都给：`ItemConfig:HasTags(id, ...)`
    // 是门面级的便捷形式，`ItemConfig_Item:HasTags(tags)` 与 PC 签名一致。
    {MakeId(ApiDomain::Isaac, 1, 0x0018), ApiDomain::Isaac, "ItemConfig", "GetCollectible", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0019), ApiDomain::Isaac, "ItemConfig", "GetTrinket", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x001A), ApiDomain::Isaac, "ItemConfig", "GetCard", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x001B), ApiDomain::Isaac, "ItemConfig", "GetPillEffect", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x001C), ApiDomain::Isaac, "ItemConfig", "IsCollectible", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x001D), ApiDomain::Isaac, "ItemConfig", "HasTags", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x001E), ApiDomain::Isaac, "ItemConfig_Item", "HasTags", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // 批次 3：EID 的**渲染**路径在 `EID:setPlayer()`（`main.lua:1061`）与描述文本构建里
    // 直接调用的成员。前四条挂在 `EntityPlayer` 元表上（owner 就是元表名），
    // `ControllerIndex` **不占 catalog 行**（`__index` 里的名字，与 `Position` 同例）。
    //
    // 偏移与证据（`runtime_constants.hpp` 的 Entity_Player 段）：
    //   * `GetOtherTwin`：真机单人场景返回 nil（PC 文档：非双人角色返回 nil）；
    //   * `GetEffectiveMaxHearts` / `GetSoulHearts` / `GetBrokenHearts`：读 `+0x16B8`
    //     （+ 骨心 `+0x2450`）、`+0x16C4`、`+0x2470`，**恒返回数字**（EID 对它们做算术，
    //     返回 nil 会变成 "attempt to perform arithmetic on a nil value"）；
    //   * `ControllerIndex`：`+0x19EC`，`SetControllerIndex` 的第一条存值指令。
    //
    // `ItemConfig_Item.IsCollectible()`（**无参**，挂在条目上）：EID 在
    // `main.lua:669`/`738`/`757` 直接写 `itemConfig:IsCollectible()`，缺了它那些描述构建
    // 会在调用处报 "attempt to call a nil value"。
    {MakeId(ApiDomain::Isaac, 1, 0x001F), ApiDomain::Isaac, "EntityPlayer", "GetOtherTwin", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0020), ApiDomain::Isaac, "EntityPlayer",
     "GetEffectiveMaxHearts", kV1, 0, ThreadAffinity::ManagedCallback,
     ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0021), ApiDomain::Isaac, "EntityPlayer", "GetSoulHearts", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0022), ApiDomain::Isaac, "EntityPlayer", "GetBrokenHearts", kV1,
     0, ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0023), ApiDomain::Isaac, "ItemConfig_Item", "IsCollectible",
     kV1, 0, ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // 批次 4：房间实体枚举（`Isaac.FindInRadius`/`FindByType`/`CountEnemies` 从 stub 变成
    // 真实现，id 不变）与实体视图补全。
    //
    //   0x0024 `Entity.ToPickup`      —— `Type==5` 且 vptr == `base+0xA36E28` → `EntityPickup` 视图
    //   0x0025 `Entity.GetData`       —— Runtime 自管的稳定 Lua 表（引擎**没有**这个字段）
    //   0x0026 `EntityPlayer.GetActiveItem` / 0x0027 `GetTrinket` / 0x0028 `GetBabySkin`
    //
    // `EntityPlayer.GetData`（0x0017）**语义改变**：批次 2 起它返回 nil（"ModData 结构还没有
    // 证据"），批次 4 起返回 Runtime 自管的那张稳定表（同一实体每次都拿到同一张表），因为
    // EID 真的往它里面写（`features/eid_api.lua:2229`：`entity:GetData()[str] = value`）。
    // 字段（`Price`/`ShopItemId`/…）走 `__index`，不占 catalog 行。
    {MakeId(ApiDomain::Isaac, 1, 0x0024), ApiDomain::Isaac, "Entity", "ToPickup", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0025), ApiDomain::Isaac, "Entity", "GetData", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0026), ApiDomain::Isaac, "EntityPlayer", "GetActiveItem", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0027), ApiDomain::Isaac, "EntityPlayer", "GetTrinket", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0028), ApiDomain::Isaac, "EntityPlayer", "GetBabySkin", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 批次 6（2026-09-12）：EID 在逐帧路径上调用的一批 `EntityPlayer` 成员。
    //
    // 真机报告 `01789210946` 的 `features/eid_api.lua:2606` 是 `player:GetPill(0)`：
    // 缺一个方法就是 "attempt to call a nil value"，**整条 update 回调**被派发器
    // 静默摘除（屏幕全空、游戏不崩）。所以这一批先把名字补齐、返回类型正确的默认值，
    // 具体数值等引擎字段偏移定位后再逐个换成真实现 —— 成熟度因此统一标 `Experimental`，
    // 默认值的选取规则见 `isaac_api.cpp` 里这一批实现开头的长注释。
    {MakeId(ApiDomain::Isaac, 1, 0x0030), ApiDomain::Isaac, "EntityPlayer", "GetPill", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0031), ApiDomain::Isaac, "EntityPlayer", "GetCard", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0032), ApiDomain::Isaac, "EntityPlayer", "GetMainTwin", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0033), ApiDomain::Isaac, "EntityPlayer", "GetCollectibleRNG", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0034), ApiDomain::Isaac, "EntityPlayer", "GetTrinketRNG", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0035), ApiDomain::Isaac, "EntityPlayer", "GetCardRNG", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0036), ApiDomain::Isaac, "EntityPlayer", "GetPillRNG", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0037), ApiDomain::Isaac, "EntityPlayer", "GetNumKeys", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0038), ApiDomain::Isaac, "EntityPlayer", "GetNumBombs", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0039), ApiDomain::Isaac, "EntityPlayer", "GetNumCoins", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003A), ApiDomain::Isaac, "EntityPlayer", "GetHearts", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003B), ApiDomain::Isaac, "EntityPlayer", "GetMaxHearts", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003C), ApiDomain::Isaac, "EntityPlayer", "GetSoulCharge", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003D), ApiDomain::Isaac, "EntityPlayer", "GetBloodCharge", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003E), ApiDomain::Isaac, "EntityPlayer", "GetPoopMana", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x003F), ApiDomain::Isaac, "EntityPlayer", "GetPoopSpell", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0040), ApiDomain::Isaac, "EntityPlayer", "GetZodiacEffect", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0041), ApiDomain::Isaac, "EntityPlayer", "GetModelingClayEffect", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0042), ApiDomain::Isaac, "EntityPlayer", "GetGlyphOfBalanceDrop", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0043), ApiDomain::Isaac, "EntityPlayer", "GetCollectibleNum", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0044), ApiDomain::Isaac, "EntityPlayer", "GetTrinketMultiplier", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0045), ApiDomain::Isaac, "EntityPlayer", "GetPlayerFormCounter", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0046), ApiDomain::Isaac, "EntityPlayer", "GetSmeltedTrinkets", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0047), ApiDomain::Isaac, "EntityPlayer", "GetEffects", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0048), ApiDomain::Isaac, "EntityPlayer", "GetName", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x0049), ApiDomain::Isaac, "EntityPlayer", "HasTrinket", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x004A), ApiDomain::Isaac, "EntityPlayer", "HasGoldenBomb", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x004B), ApiDomain::Isaac, "EntityPlayer", "HasPlayerForm", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x004C), ApiDomain::Isaac, "EntityPlayer", "CanPickRedHearts", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Isaac, 1, 0x004D), ApiDomain::Isaac, "EntityPlayer", "IsSubPlayer", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 0x004E `EntityPlayer.AddCollectible` —— **真实现**（2026-09-14）：底座是
    // `Entity_Player::AddCollectible`（`0x292A74`，清单里的 `missing_easy`），签名
    // `(type, charge, force, slot, varData)`，返回值取"**实际结果**"（调用后用 `HasCollectible` 复核），
    // 因为引擎那一层的返回类型我们没从反汇编确认过 —— 用效果说话更稳。
    {MakeId(ApiDomain::Isaac, 1, 0x004E), ApiDomain::Isaac, "EntityPlayer", "AddCollectible", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 0x0029 `EntityPickup.IsShopItem` —— **安全 stub（恒 false）**：EID 在卡片/药丸描述路径上
    // 无条件调用它（`main.lua:1611`/`1636`），缺了它那一句就是
    // "attempt to call a nil value (method 'IsShopItem')"；而"商店商品"的判据我们**没有证据**
    // （只有 `ShopItemId` 这个候选字段，见 `isaac_api.cpp` 的注释）。
    // `EntityPickup` 的字段（`Price`/`ShopItemId`/`OptionsPickupIndex`/`ForceBlind`/`Touched`）
    // 走 `__index`，不占 catalog 行。
    {MakeId(ApiDomain::Isaac, 1, 0x0029), ApiDomain::Isaac, "EntityPickup", "IsShopItem", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},
    // 批次 5：`Entity.GetSprite()`（id 0x002A）—— 真实现，**只读**：返回 `Entity + 0x50` 那个
    // 内嵌 `ANM2` 的 `Sprite` 视图（偏移证据见 `runtime_constants.hpp` 的 `kEntitySpriteOffset`）。
    // 句柄带"引擎实体来源"标记（`kSpriteSourceEngineEntity`）：`__gc` 不析构、不释放引擎对象，
    // 每次访问重新校验实体还活着（`lua_object_handles.hpp` 的 `SpriteHandle` 注释）。
    // EID 的 `EID:IsAltChoice()`（`main.lua:216`）在宝藏房里就靠它，缺了它整段描述渲染中断。
    {MakeId(ApiDomain::Isaac, 1, 0x002A), ApiDomain::Isaac, "Entity", "GetSprite", kV1, 0,
     ThreadAffinity::ManagedCallback, ApiMaturity::Experimental},

    // `Options` 表：EID 只读 `HUDOffset` 与 `Language` 两个字段，没有它们 EID 一加载就报错。
    // 这两条是**值**而不是方法（绑定表里没有 `lua_CFunction`），值由
    // `interfaces/lua/isaac_api.cpp` 的 `RegisterOptionsTable` 写进表里；PC 版是从引擎选项
    // 结构读的，偏移尚未定位，所以先给与 EID 中文默认配置一致的兼容值。
    {MakeId(ApiDomain::Global, 2, 0x0001), ApiDomain::Global, "Options", "HUDOffset", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0002), ApiDomain::Global, "Options", "Language", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},

    // 最小 `debug` 库（批次 3）：只有一个 `debug.getinfo`，因为 EID 的
    // `GetCurrentModPath`（`main.lua:148`）用 `debug.getinfo(fn).source` 直接取真实 mod 路径
    // （`@<mod 根>/main.lua`），比 `require("")` 的错误文本 hack 可靠得多。它同样是**值 +
    // 函数**形态的表：表本身由 `lua_runtime.cpp` 建（`RegisterDebugLibrary`），这里的条目是
    // 表里的成员名（与 `Options` 的两个字段同一种登记方式）。
    {MakeId(ApiDomain::Global, 2, 0x0003), ApiDomain::Global, "debug", "getinfo", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},

    // 最小 `os` 库（2026-09-12 批次 3 后补）：EID 的 `features/eid_tmtrainer.lua:200` 用
    // `os.date("%m/%d")` 判愚人节彩蛋，而我们提供 `debug`（真值）却不提供 `os` 时
    // `os.date` 索引 nil 会抛错、整包加载死在那一行。
    //
    // **刻意不提供** `remove`/`rename`/`tmpname`/`exit`（前三个是文件系统写操作，本模块的
    // 写通道尚未打通；`exit` 会直接结束玩家的游戏）—— 因此这里只登记实际存在的 7 个成员，
    // 而不是照抄 PC 的 `os` 库。表本身由 `lua_runtime.cpp` 的 `RegisterOsLibrary` 建。
    {MakeId(ApiDomain::Global, 2, 0x0004), ApiDomain::Global, "os", "clock", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0005), ApiDomain::Global, "os", "date", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0006), ApiDomain::Global, "os", "difftime", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0007), ApiDomain::Global, "os", "getenv", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0008), ApiDomain::Global, "os", "setlocale", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Global, 2, 0x0009), ApiDomain::Global, "os", "time", kV1, 0,
     ThreadAffinity::Any, ApiMaturity::Experimental},

    // Diagnostic-only surface, never part of the Mod-visible API contract.
    {MakeId(ApiDomain::Diagnostic, 1, 0x0001), ApiDomain::Diagnostic, "RuntimeTest",
     "MarkPostUpdate", kV1, 0, ThreadAffinity::MainUpdate, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Diagnostic, 1, 0x0002), ApiDomain::Diagnostic, "RuntimeTest",
     "MarkPostRender", kV1, 0, ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Diagnostic, 1, 0x0003), ApiDomain::Diagnostic, "RuntimeTest",
     "MarkPostRenderPaused", kV1, 0, ThreadAffinity::MainRender, ApiMaturity::Experimental},
    {MakeId(ApiDomain::Diagnostic, 1, 0x0004), ApiDomain::Diagnostic, "RuntimeTest",
     "MarkMusicDiagnosticCycleCompleted", kV1, 0, ThreadAffinity::MainRender,
     ApiMaturity::Experimental},
    {MakeId(ApiDomain::Diagnostic, 1, 0x0005), ApiDomain::Diagnostic, "RuntimeTest",
     "MarkMusicDiagnosticCurrentId", kV1, 0, ThreadAffinity::MainRender,
     ApiMaturity::Experimental},
};

// Constant-initialized: no guard variable and no .init_array dependency.
const ApiCatalog g_defaultCatalog{kDefaultApis, sizeof(kDefaultApis) / sizeof(kDefaultApis[0])};

} // namespace

const ApiCatalog& ApiCatalog::Default() noexcept {
    return g_defaultCatalog;
}

const LuaApiDescriptor* ApiCatalog::At(std::size_t index) const noexcept {
    if (index >= count_) {
        return nullptr;
    }
    return &entries_[index];
}

const LuaApiDescriptor* ApiCatalog::Find(std::uint32_t id) const noexcept {
    for (std::size_t index = 0; index < count_; ++index) {
        if (entries_[index].id == id) {
            return &entries_[index];
        }
    }
    return nullptr;
}

const LuaApiDescriptor* ApiCatalog::Find(const char* owner, const char* name) const noexcept {
    if (EmptyText(owner) || EmptyText(name)) {
        return nullptr;
    }
    for (std::size_t index = 0; index < count_; ++index) {
        if (SameText(entries_[index].owner, owner) && SameText(entries_[index].name, name)) {
            return &entries_[index];
        }
    }
    return nullptr;
}

Status ApiCatalog::Validate() const noexcept {
    if (entries_ == nullptr || count_ == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    if (count_ > kCapacity) {
        return Status{StatusCode::CapacityExceeded};
    }
    for (std::size_t index = 0; index < count_; ++index) {
        const LuaApiDescriptor& entry = entries_[index];
        if (entry.id == 0 || EmptyText(entry.owner) || EmptyText(entry.name)) {
            return Status{StatusCode::InvalidArgument};
        }
        if ((entry.id >> 24) != entry.DomainByte()) {
            return Status{StatusCode::InvalidArgument};
        }
        for (std::size_t other = index + 1; other < count_; ++other) {
            const LuaApiDescriptor& candidate = entries_[other];
            if (candidate.id == entry.id) {
                return Status{StatusCode::Rejected};
            }
            if (SameText(candidate.owner, entry.owner) && SameText(candidate.name, entry.name)) {
                return Status{StatusCode::Rejected};
            }
        }
    }
    return Status::Ok();
}

const char* ToString(ApiDomain domain) noexcept {
    switch (domain) {
        case ApiDomain::Global: return "Global";
        case ApiDomain::Mod: return "Mod";
        case ApiDomain::Game: return "Game";
        case ApiDomain::Level: return "Level";
        case ApiDomain::Room: return "Room";
        case ApiDomain::ItemPool: return "ItemPool";
        case ApiDomain::Music: return "Music";
        case ApiDomain::Rng: return "Rng";
        case ApiDomain::Persistence: return "Persistence";
        case ApiDomain::Input: return "Input";
        case ApiDomain::Diagnostic: return "Diagnostic";
        case ApiDomain::Font: return "Font";
        case ApiDomain::Vector: return "Vector";
        case ApiDomain::Sprite: return "Sprite";
        case ApiDomain::Isaac: return "Isaac";
        case ApiDomain::Seed: return "Seed";
    }
    return "Unknown";
}

const char* ToString(ApiMaturity maturity) noexcept {
    switch (maturity) {
        case ApiMaturity::Experimental: return "Experimental";
        case ApiMaturity::HostVerified: return "HostVerified";
        case ApiMaturity::HardwareVerified: return "HardwareVerified";
        case ApiMaturity::Disabled: return "Disabled";
    }
    return "Unknown";
}

} // namespace isaac::runtime
