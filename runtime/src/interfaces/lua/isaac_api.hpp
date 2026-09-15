#pragma once

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// 全局 `Isaac` 表（引擎门面）。
//
// 这一族存在的理由很具体：PC Mod 的第一句常常是 `Isaac.GetItemConfig()`
// （EID 就是），而引擎数据（玩家实体、ItemConfig、`FindByType` ...）要等引擎结构偏移
// 定位后才能接。所以先把**表面**补齐，让 `Isaac.X()` 被调用时不报 Lua 错误：
//
//   * 真实现（读引擎/读 Runtime 自己的状态）：`GetFrameCount` / `GetTime` / `DebugString` /
//     `IsInGame` / `RunCallback`；批次 2 起 `GetPlayer`（引擎基址 → `Game*` →
//     `players` 向量 → `Entity_Player`，带 vptr 校验）；批次 2b 起 `GetItemConfig`
//     （`Manager + 0x36538` 的**内嵌** `ItemConfig`，只读视图）。三者拿不到都返回 nil；
//   * 安全 stub（返回安全默认值，每个成员每会话只在自己的输出通道上告警一次）：
//     其余成员，见 `isaac_api.cpp` 的 `Stub`。
//
// 表由家族自己建立并挂成全局：`lua_runtime.cpp` 只负责在 Mod 脚本运行前调用一次。
// 只读视图的元表（`Entity`/`EntityPlayer`/`ItemConfig`/`ItemConfig_Item`）同样由本 TU 建立
// （见 `RegisterIsaacApi`）。
[[nodiscard]] std::size_t RegisterIsaacApi(lua_State* state) noexcept;

// 把 Catalog 里 owner 为 `Isaac` 的方法挂到**栈顶的表**上（`RegisterIsaacApi` 的组成部分）。
// 单独导出是为了让契约测试能像其它家族一样核对绑定表与 Catalog 的 id 一致。
[[nodiscard]] std::size_t AttachIsaacMethods(lua_State* state) noexcept;

// 引擎 `Game` 的**玩家向量**（`std::vector<Entity_Player*>`）的解析结果。
//
// 为什么把它单独抽出来给 `game_api.cpp` 用：真机报告 `01789207110` 暴露的缺陷正是
// "同一个引擎事实被两条 API 各自解释了一遍" —— `Game:GetNumPlayers()` 读不到时降级答 **1**，
// 而 `Isaac.GetPlayer(0)` 读不到时诚实答 **nil**，于是 EID 的
// `for i = 0, game:GetNumPlayers() - 1 do local player = Isaac.GetPlayer(i) ... player.QueuedItem`
// （`features/eid_api.lua:2620-2625`）在玩家向量为空的那几帧索引 nil、报错、被派发器静默摘除。
// **两者对同一事实必须给出同一个答案**，所以判定与"是否可读"都只在这里做一次。
struct EnginePlayerArray {
    // 向量元素个数。`readable == false` 时它恒为 0，调用方**不得**把它当成"真的没有玩家"。
    std::size_t count{0};
    // 引擎侧的读取是否成功（基址/槽/`Game*`/向量首尾都可读且自洽）。
    bool readable{false};
};

// 解析引擎的玩家向量。`index` 用不到时传 0。任何一环不可读都返回 `readable == false`。
[[nodiscard]] EnginePlayerArray ResolveEnginePlayerArray() noexcept;

// `Entity_Player` 指针判据（`*(u64*)ptr == 模块基址 + Entity_Player vtable 偏移`）。
[[nodiscard]] bool IsEnginePlayerPointer(std::uintptr_t candidate) noexcept;

// 玩家解析的**原始读数**（探针用，见 `isaac_api.cpp` 的 `RecordEnginePlayerLookup`）。
//
// 为什么需要：真机报告 `01789208345`/`01789208403` 里 EID 反复在
// `features/eid_api.lua:2625` 索引 `player`（nil），而"到底哪一步返回了 nil"在 Lua 侧看不出来
// —— `Isaac.GetPlayer(0)` 的失败有 8 个可能阶段（基址/槽/`Game`/向量区间/长度/下标/空元素/vptr）。
// 把这个结构体报出来，一轮真机就能定位阶段，而不是再猜一轮。
struct EnginePlayerLookup {
    // 最后一次查询读到的**原始值**（语义随阶段变化，见下）：
    //   * 阶段 4：`firstElement` = 读到的向量 begin、`second` = 读到的向量 end；
    //   * 阶段 8：`firstElement` = 元素指针、`second` = 该元素的 vptr；
    //   * 其余阶段：全 0。
    std::uintptr_t firstElement{0};
    std::uintptr_t second{0};
    // 读到的向量元素个数（阶段 0 时有效）。
    std::uint32_t count{0};
    // `ResolveEnginePlayerBegin` 自己读到的 begin/end（读失败时记 `~0`，与"读到 0"区分开）。
    std::uintptr_t resolverBegin{0};
    std::uintptr_t resolverEnd{0};
    // 失败阶段：0 = 成功；1 = 基址不可用；2 = 槽不可读/为空；3 = `Game*` 为空；
    // 4 = 向量区间不可读或不自洽；5 = 元素个数超过上限；6 = 下标越界；7 = 元素为空指针；
    // 8 = vptr 不是 `Entity_Player`。
    std::uint32_t stage{0};
};

[[nodiscard]] EnginePlayerLookup LastEnginePlayerLookupForProbe() noexcept;

// 引擎玩家链的**逐阶段**读数（探针用）。`Isaac.GetPlayer` 的失败有 8 个可能阶段，而
// `EnginePlayerLookup.stage` 只报"最后一个失败的阶段"。真机报告 `01789209134` 里那个阶段码
// 是被后续成功查询覆盖过的，读不出根因；逐阶段一次性报出去才能一轮定位。
struct EnginePlayerChain {
    std::uintptr_t moduleBase{0};  // 模块基址（0 = 未发布）
    std::uintptr_t gameSlot{0};    // 槽里的指针变量地址（0 = 槽不可读/为空）
    std::uintptr_t game{0};        // `Game*`（0 = 未构造）
    std::uintptr_t begin{0};       // 玩家向量 begin
    std::uintptr_t end{0};         // 玩家向量 end
    std::uint32_t beginReadable{0};
    std::uint32_t endReadable{0};
};

[[nodiscard]] EnginePlayerChain ReadEnginePlayerChainForProbe() noexcept;

// `Isaac.FindInRadius` 的探针读数（语义与用途见 `isaac_api.cpp` 里 `g_FindInRadius*` 的注释）。
struct FindInRadiusProbe {
    std::uint32_t calls{0};        // 累计调用次数
    std::uint32_t results{0};      // **最后一次**查询返回的实体个数
    std::uint32_t live{0};         // 其中来自活表
    std::uint32_t players{0};      // 其中来自玩家向量
    std::uint32_t effects{0};      // 其中来自效果表
    std::uint32_t roomResolved{0}; // 最后那次查询时 Room* 是否拿到
    std::uint32_t fingerprintOk{0};// 最后那次查询时房间实体容器指纹是否匹配
    std::uint32_t lastMask{0};     // 最后一次的 EntityPartition 掩码
    std::uint32_t lastRadius{0};   // 最后一次的半径（float 位模式低 24 位）
};

[[nodiscard]] FindInRadiusProbe FindInRadiusProbeSnapshot() noexcept;

// `Entity.Type`/`Variant`/`SubType` 的读取读数（探针）：EID 的 `hasDescription` 先用
// `type(entity) == "userdata"` 与 `entity.Type` 过滤候选实体，只有通过之后才会调
// `Entity:GetData`。真机显示 `GetData` 一次未被调用，所以这两个读数能分清
// "候选实体被 Type 过滤"与"hasDescription 压根没被调用"。
struct EntityFieldProbe {
    std::uint32_t typeReads{0};
    std::uint32_t lastType{0};
    std::uint32_t lastVariant{0};
    std::uint32_t lastSubType{0};
    // 以字符串键读实体字段的次数与最近一次的键名前 8 字节（EID 的 `getEntityData` 可能走这条）。
    std::uint32_t stringReads{0};
    std::uint64_t lastStringHead{0};
    // `Pickup.Touched` 的读数（2026-09-12，动作三）。EID 拿它当"摘掉隐瞒"的条件
    // （`eid_api.lua:3338`/`3340` 的 `not entity:ToPickup().Touched`），而**这个字段偏移本身
    // 还是"待真机确认"的猜测**（`kEntityPickupTouchedOffset`）。这里同时记三个数：
    //   * `touchedReads`/`touchedTrue` —— 读了多少次、其中为真的次数；
    //   * `lastTouchedRaw` —— 最近一次读到的**原始字节**（不是转成布尔之后的值），
    //     这样"偏移读错了、读到的是别的字节"也能分辨。
    std::uint32_t touchedReads{0};
    std::uint32_t touchedTrue{0};
    std::uint32_t lastTouchedRaw{0};
};

[[nodiscard]] EntityFieldProbe EntityFieldProbeSnapshot() noexcept;

// `Isaac.GetItemConfig()` 的探针（第八轮）：bit0 链路解析成功、bit1 向量可读、
// bit2 因"向量还没建立"而返回对象、bit3 因"参数非法"而返回 nil；bits8..31 调用次数。
[[nodiscard]] std::uint32_t GetItemConfigProbeSnapshot() noexcept;

// `Entity.FrameCount` 的探针读数：EID 的 `main.lua:1473` 判 `entity.FrameCount > 0`，
// 而过滤器链掩码显示它从不走到后面的 `EID:getEntityData` —— 只有该值恒 0 才会如此。
//   `reads`/`positive` 分别是读取次数与"结果 > 0"的次数；后三个是最后一次的原始值。
struct FrameCountProbe {
    std::uint32_t reads{0};
    std::uint32_t positive{0};
    std::uint32_t gameFrameCount{0};  // `[Game + 0x24F99C]`
    std::uint32_t spawnFrame{0};      // `[entity + 0x2F4]`
    std::uint32_t result{0};          // 两者之差
};

[[nodiscard]] FrameCountProbe FrameCountProbeSnapshot() noexcept;


// `Isaac.GetPlayer` 的成功/失败次数（原因 1：`EID.player` 依赖它）。
struct PlayerLookupProbe {
    std::uint32_t getPlayerSuccess{0};
    std::uint32_t getPlayerFailure{0};
};

[[nodiscard]] PlayerLookupProbe PlayerLookupProbeSnapshot() noexcept;

// `Isaac.CountEnemies()` 最后一次返回的个数（EID 用它 + `HideInBattle` 决定是否隐藏描述）。
[[nodiscard]] std::uint32_t LastCountEnemiesForProbe() noexcept;

// `Entity` / `EntityPlayer` 的方法表（owner 分别是 `Entity`、`EntityPlayer`，同样是
// `LuaHandlerBinding` + `AttachOwnerMethods` 模式）。它们挂在各自元表的 `__methods` 上；
// `EntityPlayer` 的 `__index` 会回落到 `Entity` 的方法表（PC 里就是继承）。
[[nodiscard]] std::size_t AttachEntityMethods(lua_State* state) noexcept;
[[nodiscard]] std::size_t AttachEntityPlayerMethods(lua_State* state) noexcept;
// `EntityPickup` 的方法表（批次 4）：目前只有 `IsShopItem`（安全 stub，见 `isaac_api.cpp`）。
[[nodiscard]] std::size_t AttachEntityPickupMethods(lua_State* state) noexcept;

// `ItemConfig` / `ItemConfig_Item` 的方法表（批次 2b，同一个 `LuaHandlerBinding` +
// `AttachOwnerMethods` 模式，同样挂在各自元表的 `__methods` 上）。条目的字段
// （`ID`/`Type`/`Name`/`Description`）走 `__index`，与 `EntityPlayer` 的 `Position` 同例，
// 因此不占绑定行。
[[nodiscard]] std::size_t AttachItemConfigMethods(lua_State* state) noexcept;
[[nodiscard]] std::size_t AttachItemConfigItemMethods(lua_State* state) noexcept;

// 引擎实体存活判据（`vptr` 落在 `[base + kEntityVtableRangeBeginOffset, …End)` 的 `Entity`
// 家族区间内、且该地址可读）。
//
// 导出它的唯一理由：`sprite_api.cpp` 的 `Entity:GetSprite()` 句柄必须**每次访问**都重新确认
// "借出 sprite 的那个实体还活着"（见 `lua_object_handles.hpp` 的 `kSpriteSourceEngineEntity`），
// 而"什么算一张活的 Entity"只应该有一份实现 —— 就是 `isaac_api.cpp` 里 `ValidatedEntity` 用的
// 那一条。返回 `true` 表示可以安全地按 `Entity` 布局读它的字段。
[[nodiscard]] bool IsLiveEntityPointer(std::uintptr_t candidate) noexcept;

#if !defined(__SWITCH__)
// 宿主测试注入点。**设备构建（`-D__SWITCH__`）里不存在这个类型和这个函数。**
//
// 为什么需要它：`EntityPlayer:HasCollectible` 的设备实现是一条 `bl`
// `base + kEntityPlayerHasCollectibleOffset`，而宿主构建既没有引擎映像、也不可能把一个 C++
// 函数恰好放在那个地址上。注入点顶替的就是这一条 `bl`，让宿主测试仍然能验证真正容易写错的
// 那三段：vptr 校验必须先于调用、传给引擎的第一个参数就是实体指针、id 与返回值原样往返。
using EntityPlayerHasCollectibleHostImplementation = bool (*)(void* entity,
                                                             std::uint32_t collectibleId);
void SetEntityPlayerHasCollectibleHostImplementation(
    EntityPlayerHasCollectibleHostImplementation function) noexcept;

using CurrentLanguageCodeHostImplementation = const char* (*)();
void SetCurrentLanguageCodeHostImplementation(
    CurrentLanguageCodeHostImplementation function) noexcept;
#endif

// 全局 `Options` 表。EID 只读 `HUDOffset` 与 `Language` 两个字段，缺了它们 EID 一加载就报错。
//
// 两个字段仍是从引擎选项结构读取的真实实现的替代品：`HUDOffset = 10` 对齐 EID 默认配置，
// `Language = "zh"` 对齐当前简体中文环境。它们是**值**而不是方法，因此 Catalog 里有条目、
// 绑定表里没有绑定行。
[[nodiscard]] std::size_t RegisterOptionsTable(lua_State* state) noexcept;

// Device-side gate for managed callbacks: menus and load transitions can leave a stale player
// vector behind. Do not dispatch until both the current room and a validated player exist.
[[nodiscard]] bool ShouldDispatchManagedCallbacks() noexcept;

} // namespace isaac::runtime
