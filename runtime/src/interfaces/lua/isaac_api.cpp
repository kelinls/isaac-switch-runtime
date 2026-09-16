#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"
#include "interfaces/lua/field_api.hpp"

#include "interfaces/lua/mod_api.hpp"
#include "interfaces/lua/owner_binding.hpp"
#include "interfaces/lua/vector_api.hpp"
// `Entity:GetSprite()` 的实现体在 `sprite_api.cpp`（`Sprite` 句柄的布局与所有权都在那里，
// 见 `lua_object_handles.hpp` 的 `SpriteHandle`）：这里只做实体校验 + 推入一个句柄。
#include "interfaces/lua/sprite_api.hpp"

#include "application/callback/callback_dispatcher.hpp"
#include "application/callback/callback_registry.hpp"
#include "domain/callback/callback_descriptor.hpp"

#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

// `svcQueryMemory`/`MemoryInfo`/`Perm_R` 只存在于设备侧（libnx）。宿主构建没有它们，
// 可读性检查退化为保守版本，见 `IsEngineMemoryReadable`。
#if defined(__SWITCH__)
#include "lib/nx/nx.h"
#endif

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <atomic>
#include <cstring>
#include <limits>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {

// **把我们自己 API 的报错消息也留证**（2026-09-12 第十一轮）。
//
// 背景：探针 `ISAACERR` 的"错误文本"只来自 `CaptureLuaErrorTop`（`lua_pcallk` 失败路径），
// 而**我们自己的 API 用 `luaL_error` 报的错不走那条路** —— 真机报告 `01789219353` 因此只给出
// 一个 `.`，无法定位是哪个 API、哪一句。
//
// 做法（2026-09-15 改）：**直接调 `luaL_error` 就行** —— `lua_runtime_state.hpp` 已经把它定义成宏，
// 转调独立编译的 `LuaRuntime::ReportAndRecordLuaError`，那条路径本身就是"先把整段消息记进探针
// 缓冲区（`RecordLuaErrorText`）、再真正抛错"，并且会带上 `file:line:` 位置前缀。
//
// ★ 这里原先有个**本 TU 私有的模板** `ReportApiError(...)`，做的是同一件事，已删除。原因很具体：
// 编译器把模板体在**每个调用点整个内联** —— 每次都在栈上建一个 256 字节缓冲区、清零、把消息拷
// 进去，再调 `RecordLuaErrorText`。实测 `IsaacGetFrameCount`（只做"检查参数个数 + 压一个整数"）
// 编出来 **152 字节**，其中绝大部分就是这段内联代码；改走宏那条路径后，调用点只剩
// "取字符串地址 + `bl`"。这是成本压缩最直接的一笔：API 还要成百上千地长，每个报错点省约 85 字节。

// 探针（2026-09-12 第九轮）：EID 的 `hasDescription`（`eid_api.lua:1065`）第一件事就是
// `EID:EntitySanityCheck(entity)` + `entity.Type`，只有在它返回真之后才会走到
// `EID:getEntityData`。真机读数显示 `Entity.GetData` **一次都没被调用**，所以要么
// `hasDescription` 的候选实体全是"没有 Type 的 udata"，要么它压根没被调用。
// 这两个读数（Type 字段被读取的次数 + 最近一次的值）能一次分开。
std::atomic<std::uint32_t> g_EntityTypeFieldReads{0};
std::atomic<std::uint32_t> g_LastEntityTypeValue{0};
std::atomic<std::uint32_t> g_LastEntityVariantValue{0};
std::atomic<std::uint32_t> g_LastEntitySubTypeValue{0};
// 以**字符串键**读取实体字段的次数（`entity.XXX` 的 XXX 不是已知整数/字段名时也会计数）。
// 用途：EID 的 `EID:getEntityData(entity, key)` 若走 `entity:GetData()`，我们会看到 `Entity.GetData`
// 位；若它走的是别的字段名，这里能看到"确实发生过字符串字段读取"。
std::atomic<std::uint32_t> g_EntityStringFieldReads{0};
std::atomic<std::uint64_t> g_LastEntityStringFieldHead{0};

// `Pickup.Touched` 的探针（2026-09-12，动作三）：EID 用它当"摘掉隐瞒"的条件
// （`eid_api.lua:3338`/`3340`），而这个字段的偏移本身还是"待真机确认"的猜测。
std::atomic<std::uint32_t> g_EntityPickupTouchedReads{0};
std::atomic<std::uint32_t> g_EntityPickupTouchedTrue{0};
std::atomic<std::uint32_t> g_LastEntityPickupTouchedRaw{0};




// `Entity.FrameCount` 的探针读数（2026-09-12 第八轮；语义见该字段实现处的注释）。
std::atomic<std::uint32_t> g_FrameCountProbeReads{0};
std::atomic<std::uint32_t> g_FrameCountPositive{0};
std::atomic<std::uint32_t> g_LastGameFrameCount{0};
std::atomic<std::uint32_t> g_LastEntitySpawnFrame{0};
std::atomic<std::uint32_t> g_LastFrameCountResult{0};

// --- `Isaac.GetItemConfig()` 的探针（2026-09-12 第八轮）------------------------------
//
// 真机报告 `01789227137`：加载期"最后调用的 API"= 位 17（本函数），之后 EID 就断了，
// 但**我们到底返回了 nil 还是对象**从负载里看不出来（错误消息通道给的是 1 字节残留）。
// 这个探针把三种结局分别记下来：
//   bit0 链路解析成功（`ResolveItemConfig` 拿到了 ItemConfig 地址）
//   bit1 收藏品向量此刻可读
//   bit2 因为向量还没建立（`begin == 0`）而走了"返回对象"那条
//   bit3 因为参数明显非法（长度不是 8 的倍数 / 超上限）而返回 nil
//   bits8..31 调用次数（封顶 0xFFFFFF）
std::atomic<std::uint32_t> g_GetItemConfigProbe{0};

void RecordGetItemConfigForProbe(std::uint32_t flags) noexcept {
    std::uint32_t current = g_GetItemConfigProbe.load(std::memory_order_relaxed);
    std::uint32_t calls = (current >> 8) + 1;
    if (calls > 0xFFFFFFu) {
        calls = 0xFFFFFFu;
    }
    g_GetItemConfigProbe.store((calls << 8) | (flags & 0xFFu), std::memory_order_relaxed);
}


std::uint32_t GetItemConfigProbeSnapshot() noexcept {
    return g_GetItemConfigProbe.load(std::memory_order_relaxed);
}

EntityFieldProbe EntityFieldProbeSnapshot() noexcept {
    // 见结构体注释：字符串键读取次数与最近一次键名（前 8 字节）。
    // （下面填的是同一个结构体的新字段）
    EntityFieldProbe probe{};
    probe.typeReads = g_EntityTypeFieldReads.load(std::memory_order_relaxed);
    probe.lastType = g_LastEntityTypeValue.load(std::memory_order_relaxed);
    probe.lastVariant = g_LastEntityVariantValue.load(std::memory_order_relaxed);
    probe.lastSubType = g_LastEntitySubTypeValue.load(std::memory_order_relaxed);
    probe.stringReads = g_EntityStringFieldReads.load(std::memory_order_relaxed);
    probe.lastStringHead = g_LastEntityStringFieldHead.load(std::memory_order_relaxed);
    probe.touchedReads = g_EntityPickupTouchedReads.load(std::memory_order_relaxed);
    probe.touchedTrue = g_EntityPickupTouchedTrue.load(std::memory_order_relaxed);
    probe.lastTouchedRaw = g_LastEntityPickupTouchedRaw.load(std::memory_order_relaxed);
    return probe;
}

FrameCountProbe FrameCountProbeSnapshot() noexcept {
    FrameCountProbe probe{};
    probe.reads = g_FrameCountProbeReads.load(std::memory_order_relaxed);
    probe.positive = g_FrameCountPositive.load(std::memory_order_relaxed);
    probe.gameFrameCount = g_LastGameFrameCount.load(std::memory_order_relaxed);
    probe.spawnFrame = g_LastEntitySpawnFrame.load(std::memory_order_relaxed);
    probe.result = g_LastFrameCountResult.load(std::memory_order_relaxed);
    return probe;
}

namespace {

using LuaRuntime::EngineModuleBase;
using LuaRuntime::EntityHandle;
using LuaRuntime::InManagedCallbackScope;
using LuaRuntime::ItemConfigHandle;
using LuaRuntime::ItemConfigItemHandle;
using LuaRuntime::kEntityMetatable;
using LuaRuntime::kEntityPickupMetatable;
using LuaRuntime::kEntityPlayerMetatable;
using LuaRuntime::kItemConfigItemMetatable;
using LuaRuntime::kItemConfigMetatable;
using LuaRuntime::kVectorMetatable;
using LuaRuntime::VectorHandle;

int IsaacGetFrameCount(lua_State* state);
int IsaacGetTime(lua_State* state);
int IsaacDebugString(lua_State* state);
int IsaacIsInGame(lua_State* state);
int IsaacRunCallback(lua_State* state);
int IsaacGetPlayer(lua_State* state);
int IsaacGetItemConfig(lua_State* state);
int IsaacFindByType(lua_State* state);
int IsaacFindInRadius(lua_State* state);
int IsaacCountEnemies(lua_State* state);
int IsaacCountBosses(lua_State* state);
int IsaacWorldToScreen(lua_State* state);
int IsaacWorldToRenderPosition(lua_State* state);
int IsaacGetPersistentGameData(lua_State* state);
int IsaacGetTrinketIdByName(lua_State* state);
int IsaacGetCallbacks(lua_State* state);
int IsaacLoadModData(lua_State* state);
int IsaacSaveModData(lua_State* state);
int IsaacRenderScaledText(lua_State* state);

// Entity / EntityPlayer / EntityPickup
int EntityIndex(lua_State* state);
int EntityPlayerIndex(lua_State* state);
int EntityPickupIndex(lua_State* state);
int EntityPickupIsShopItem(lua_State* state);
int EntityToPlayer(lua_State* state);
int EntityToPickup(lua_State* state);
int EntityToFamiliar(lua_State* state);
int EntityGetData(lua_State* state);
int EntityGetSprite(lua_State* state);
int EntityPlayerHasCollectible(lua_State* state);
// `EntityPlayer:AddCollectible`（2026-09-14 新增，真实现）。声明与实现同在本单元（Isaac 家族）——
// 契约测试要求"家族方法体住在家族单元里、登记名与 catalog 的 id 一致"。
int EntityPlayerAddCollectible(lua_State* state);
int EntityPlayerGetData(lua_State* state);
int EntityPlayerGetOtherTwin(lua_State* state);
int EntityPlayerGetEffectiveMaxHearts(lua_State* state);
int EntityPlayerGetActiveItem(lua_State* state);
// 批次 6（2026-09-12）：EID 在 update 回调里逐帧调用的一批 `EntityPlayer` 成员。缺任何一个，
// `player:X()` 就是 "attempt to call a nil value"，整条回调被派发器静默摘除（真机报告
// `01789210946` 的 `eid_api.lua:2606` 就是 `player:GetPill(0)`）。
int EntityPlayerGetPill(lua_State* state);
int EntityPlayerGetCard(lua_State* state);
int EntityPlayerGetMainTwin(lua_State* state);
int EntityPlayerGetCollectibleRNG(lua_State* state);
int EntityPlayerGetTrinketRNG(lua_State* state);
int EntityPlayerGetCardRNG(lua_State* state);
int EntityPlayerGetPillRNG(lua_State* state);
int EntityPlayerGetNumKeys(lua_State* state);
int EntityPlayerGetNumBombs(lua_State* state);
int EntityPlayerGetNumCoins(lua_State* state);
int EntityPlayerGetHearts(lua_State* state);
int EntityPlayerGetSoulCharge(lua_State* state);
int EntityPlayerGetBloodCharge(lua_State* state);
int EntityPlayerGetPoopMana(lua_State* state);
int EntityPlayerGetPoopSpell(lua_State* state);
int EntityPlayerGetZodiacEffect(lua_State* state);
int EntityPlayerGetModelingClayEffect(lua_State* state);
int EntityPlayerGetGlyphOfBalanceDrop(lua_State* state);
int EntityPlayerGetCollectibleNum(lua_State* state);
int EntityPlayerGetTrinketMultiplier(lua_State* state);
int EntityPlayerGetPlayerFormCounter(lua_State* state);
int EntityPlayerGetSmeltedTrinkets(lua_State* state);
int EntityPlayerGetEffects(lua_State* state);
int EntityPlayerGetName(lua_State* state);
int EntityPlayerHasTrinket(lua_State* state);
int EntityPlayerHasGoldenBomb(lua_State* state);
int EntityPlayerHasPlayerForm(lua_State* state);
int EntityPlayerCanPickRedHearts(lua_State* state);
int EntityPlayerIsSubPlayer(lua_State* state);
int EntityPlayerGetTrinket(lua_State* state);

// 全局函数（`GetPtrHash`）。
int GlobalGetPtrHash(lua_State* state);

// 会话级状态复位（`RegisterIsaacApi` 调用）。实体 ModData 表的存储在这里清空：
// 换 Lua 状态时那些 registry 引用随旧状态一起消失，所以**不能**在这里 `luaL_unref`。
void ResetEntityDataStore() noexcept;

// ItemConfig / ItemConfig_Item
int ItemConfigIndex(lua_State* state);
int ItemConfigItemIndex(lua_State* state);
int ItemConfigGetCollectible(lua_State* state);
int ItemConfigGetTrinket(lua_State* state);
int ItemConfigGetCard(lua_State* state);
int ItemConfigGetPillEffect(lua_State* state);
int ItemConfigIsCollectible(lua_State* state);
int ItemConfigHasTags(lua_State* state);
int ItemConfigItemHasTags(lua_State* state);
int ItemConfigItemIsCollectible(lua_State* state);
int ItemConfigItemIsAvailable(lua_State* state);

// Catalog 的 id 与实现的对应关系。id 一旦分配不得复用（见 `api_descriptor.hpp`）。
constexpr LuaHandlerBinding kIsaacHandlers[] = {
    {0x0E010001, &IsaacGetFrameCount},
    {0x0E010002, &IsaacGetTime},
    {0x0E010003, &IsaacDebugString},
    {0x0E010004, &IsaacIsInGame},
    {0x0E010005, &IsaacRunCallback},
    {0x0E010006, &IsaacGetPlayer},
    {0x0E010007, &IsaacGetItemConfig},
    {0x0E010008, &IsaacFindByType},
    {0x0E010009, &IsaacFindInRadius},
    {0x0E01000A, &IsaacCountEnemies},
    {0x0E01000B, &IsaacCountBosses},
    {0x0E01000C, &IsaacWorldToScreen},
    {0x0E01000D, &IsaacWorldToRenderPosition},
    {0x0E01000E, &IsaacGetPersistentGameData},
    {0x0E01000F, &IsaacGetTrinketIdByName},
    {0x0E010010, &IsaacGetCallbacks},
    {0x0E010011, &IsaacLoadModData},
    {0x0E010012, &IsaacSaveModData},
    {0x0E010013, &IsaacRenderScaledText},
};

// `EntityPlayer` 的成员（owner 独立）：`GetPlayerType`/`HasCollectible` 与 PC 的同名方法一一对应。
// 批次 3 补上 EID 渲染路径直接调用的四个：`GetOtherTwin`（单人恒 nil）、`GetEffectiveMaxHearts`、
// `GetSoulHearts`、`GetBrokenHearts`（三者读 `runtime_constants.hpp` 里定位到的字段，恒返回数字）。
constexpr LuaHandlerBinding kEntityPlayerHandlers[] = {
    {0x0E010015, &EntityPlayerHasCollectible},
    {0x0E01004E, &EntityPlayerAddCollectible},
    {0x0E010017, &EntityPlayerGetData},
    {0x0E01001F, &EntityPlayerGetOtherTwin},
    {0x0E010020, &EntityPlayerGetEffectiveMaxHearts},
    {0x0E010026, &EntityPlayerGetActiveItem},
    {0x0E010027, &EntityPlayerGetTrinket},
    // 批次 6：EID 逐帧调用的一批成员（安全默认值，语义与成熟度见各自的实现注释）。
    {0x0E010030, &EntityPlayerGetPill},
    {0x0E010031, &EntityPlayerGetCard},
    {0x0E010032, &EntityPlayerGetMainTwin},
    {0x0E010033, &EntityPlayerGetCollectibleRNG},
    {0x0E010034, &EntityPlayerGetTrinketRNG},
    {0x0E010035, &EntityPlayerGetCardRNG},
    {0x0E010036, &EntityPlayerGetPillRNG},
    {0x0E010037, &EntityPlayerGetNumKeys},
    {0x0E010038, &EntityPlayerGetNumBombs},
    {0x0E010039, &EntityPlayerGetNumCoins},
    {0x0E01003A, &EntityPlayerGetHearts},
    {0x0E01003C, &EntityPlayerGetSoulCharge},
    {0x0E01003D, &EntityPlayerGetBloodCharge},
    {0x0E01003E, &EntityPlayerGetPoopMana},
    {0x0E01003F, &EntityPlayerGetPoopSpell},
    {0x0E010040, &EntityPlayerGetZodiacEffect},
    {0x0E010041, &EntityPlayerGetModelingClayEffect},
    {0x0E010042, &EntityPlayerGetGlyphOfBalanceDrop},
    {0x0E010043, &EntityPlayerGetCollectibleNum},
    {0x0E010044, &EntityPlayerGetTrinketMultiplier},
    {0x0E010045, &EntityPlayerGetPlayerFormCounter},
    {0x0E010046, &EntityPlayerGetSmeltedTrinkets},
    {0x0E010047, &EntityPlayerGetEffects},
    {0x0E010048, &EntityPlayerGetName},
    {0x0E010049, &EntityPlayerHasTrinket},
    {0x0E01004A, &EntityPlayerHasGoldenBomb},
    {0x0E01004B, &EntityPlayerHasPlayerForm},
    {0x0E01004C, &EntityPlayerCanPickRedHearts},
    {0x0E01004D, &EntityPlayerIsSubPlayer},
};

// `Entity` 的成员（owner 独立）。批次 4 补上 `ToPickup`/`GetData`（id 0x0E010024/0x0E010025）；
// 批次 5 补上 `GetSprite`（id 0x0E01002A）。
constexpr LuaHandlerBinding kEntityHandlers[] = {
    {0x0E010014, &EntityToPlayer},
    {0x0E010024, &EntityToPickup},
    {0x0E010051, &EntityToFamiliar},
    {0x0E010025, &EntityGetData},
    {0x0E01002A, &EntityGetSprite},
};

// `ItemConfig` 的成员（owner 独立，与 `Entity`/`EntityPlayer` 同一形态：挂在元表的
// `__methods` 上）。`ItemConfig_Item` 的字段（`ID`/`Type`/`Name`/`Description`）走 `__index`，
// 不占 catalog 行也不占绑定行（与 `EntityPlayer` 的 `Position`/`Type` 同例）。
// `EntityPickup` 的成员（owner 独立）。批次 4 只有一条：`IsShopItem`。
constexpr LuaHandlerBinding kEntityPickupHandlers[] = {
    {0x0E010029, &EntityPickupIsShopItem},
};

constexpr LuaHandlerBinding kItemConfigHandlers[] = {
    {0x0E010018, &ItemConfigGetCollectible},
    {0x0E010019, &ItemConfigGetTrinket},
    {0x0E01001A, &ItemConfigGetCard},
    {0x0E01001B, &ItemConfigGetPillEffect},
    {0x0E01001C, &ItemConfigIsCollectible},
    {0x0E01001D, &ItemConfigHasTags},
};

constexpr LuaHandlerBinding kItemConfigItemHandlers[] = {
    {0x0E01001E, &ItemConfigItemHasTags},
    {0x0E010023, &ItemConfigItemIsCollectible},
    {0x0E010055, &ItemConfigItemIsAvailable},
};

constexpr char kIsaacOwner[] = "Isaac";
constexpr char kEntityOwner[] = "Entity";
constexpr char kEntityPlayerOwner[] = "EntityPlayer";
constexpr char kEntityPickupOwner[] = "EntityPickup";
constexpr char kItemConfigOwner[] = "ItemConfig";
constexpr char kItemConfigItemOwner[] = "ItemConfig_Item";

template <typename Table>
constexpr std::size_t RowCount(const Table& table) noexcept {
    return sizeof(table) / sizeof(table[0]);
}

// `Isaac.GetTime()` 的换算率：Runtime 的 update 派发在未暂停时按 30 Hz 标称运行。
constexpr std::int64_t kFramesPerSecond = 30;

// `Isaac.RunCallback` 的递归上限。PC 版没有这个限制，但我们的派发是同步 Lua 调用，
// 一个回调再 `RunCallback` 它自己就会一直套下去；给一个小上限，越界直接报 Lua 错误。
constexpr std::size_t kRunCallbackMaximumDepth = 8;

// 安全 stub 的成员名单。数组下标即成员身份，告警文本单独传，所以新加一个 stub 只需加一行枚举，
// 不需要再写一遍"是否已经告警过"的判断。
//
// 注意：`GetPlayer` **已经不在这里**——批次 2 起它是真实现（读引擎内存），失败时返回 nil
// 而不是"安全默认值"，所以它既不属于 stub 告警名单，也不该有告警。`GetItemConfig` 同理
// （批次 2b 起是真实现：内嵌 `ItemConfig` 的只读视图），也从这份名单里移除了。
// `GetCallbacks` 批次 3 起同样是真实现（命名回调表 + `CallbackRegistry`），一并移除。
// 批次 4 再把 `FindByType`/`FindInRadius`/`CountEnemies` 移出名单：它们已经是真实现
// （房间实体容器遍历），拿不到容器时返回**空表/0**，那是"引擎数据读不到"而不是"未实现"，
// 留一行"未实现"告警会把前者误导成后者。
// `CountBosses` **留在名单里**：boss 判据（vtable 槽或实体字段）本轮没有任何证据，
// 见 `IsaacCountBosses` 的注释——它照旧返回 0 并告警一次。
enum class Stub : std::size_t {
    CountBosses = 0,
    WorldToScreen,
    WorldToRenderPosition,
    GetPersistentGameData,
    GetTrinketIdByName,
    LoadModData,
    SaveModData,
    RenderScaledText,
    Count,
};

// 单线程路径（Lua 只在托管回调里跑）上的普通全局：故意不用锁也不用原子——没有第二个线程
// 访问它，而本项目在真机上已经被 `thread_local` 坑过一次（见 `lua_runtime.cpp`）。
bool g_StubWarned[static_cast<std::size_t>(Stub::Count)] = {};
std::size_t g_RunCallbackDepth = 0;

// 与 Lua `print` **同一个输出通道**的一行文本。
//
// `print` 是标准库函数（`lua_runtime.cpp` 用 `luaopen_base` 打开它），所以"复用同一个通道"
// 的唯一正确做法就是调用它本身：将来 Runtime 把 `print` 换成 SaltySD 日志时，
// `Isaac.DebugString` 与 stub 告警会一起跟着走，不需要在这里再维护第二条日志路径。
//
// 失败必须吞掉：脚本可能把 `print` 覆盖成别的东西，而 `DebugString`/stub 告警都不允许
// 把错误抛回 Mod（本轮的目标正是"被调用时不报 Lua 错误"）。
void EmitLine(lua_State* state, const char* text, std::size_t length) {
    if (state == nullptr || text == nullptr) {
        return;
    }
    lua_getglobal(state, "print");
    if (lua_type(state, -1) != LUA_TFUNCTION) {
        lua_pop(state, 1);
        return;
    }
    lua_pushlstring(state, text, length);
    if (lua_pcallk(state, 1, 0, 0, 0, nullptr) != LUA_OK) {
        lua_pop(state, 1);
    }
}

// 安全 stub 的告警：每个成员每会话只写一行。
void WarnStubOnce(lua_State* state, Stub stub, const char* member) {
    const std::size_t index = static_cast<std::size_t>(stub);
    if (member == nullptr || index >= static_cast<std::size_t>(Stub::Count) ||
        g_StubWarned[index]) {
        return;
    }
    g_StubWarned[index] = true;
    std::array<char, 96> line{};
    const int written = std::snprintf(line.data(), line.size(), "未实现：%s（返回安全默认值）",
                                      member);
    if (written <= 0) {
        return;
    }
    const std::size_t length = static_cast<std::size_t>(written) < line.size()
                                   ? static_cast<std::size_t>(written)
                                   : line.size() - 1;
    EmitLine(state, line.data(), length);
}

void ResetSessionState() {
    for (std::size_t index = 0; index < static_cast<std::size_t>(Stub::Count); ++index) {
        g_StubWarned[index] = false;
    }
    g_RunCallbackDepth = 0;
    ManagedFrameClock::Reset();
    ResetEntityDataStore();
}

std::int64_t FrameCount() {
    // 30 Hz 下 `uint64` 溢出需要 190 亿年，所以直接窄化到 Lua 的整数类型。
    return static_cast<std::int64_t>(ManagedFrameClock::Count());
}

void PushNil(lua_State* state) {
    lua_pushnil(state);
}

void PushZero(lua_State* state) {
    lua_pushinteger(state, 0);
}

// 恒等变换：没有相机偏移可用，返回一个**同值的新 Vector**（PC 返回的也是变换后的新对象，
// 复用同一个 userdata 会让 Mod 改到调用方手里的那个值）。参数不是 Vector 就返回 nil——
// `Vector` 是 Runtime 自己的 userdata，类型不对时绝不能假装算出一个坐标。
int WorldToIdentity(lua_State* state, Stub stub, const char* member) {
    WarnStubOnce(state, stub, member);
    auto* vector = static_cast<VectorHandle*>(luaL_testudata(state, 1, kVectorMetatable));
    if (vector == nullptr) {
        lua_pushnil(state);
        return 1;
    }
    auto* result = static_cast<VectorHandle*>(lua_newuserdata(state, sizeof(VectorHandle)));
    result->x = vector->x;
    result->y = vector->y;
    luaL_getmetatable(state, kVectorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

// --- 近似实现（有真实行为）-------------------------------------------------

int IsaacGetFrameCount(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "Isaac.GetFrameCount accepts no arguments");
    }
    lua_pushinteger(state, static_cast<lua_Integer>(FrameCount()));
    return 1;
}

int IsaacGetTime(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "Isaac.GetTime accepts no arguments");
    }
    // **近似**：`GetFrameCount() / 30` 的整数秒。PC 版返回的是引擎自己的游戏内计时
    // （暂停、过场、加载都由引擎扣减），我们的引擎时钟偏移尚未定位，所以这里只是
    // "Runtime 帧数换算成秒"，不声称与 PC 逐值一致。
    lua_pushinteger(state, static_cast<lua_Integer>(FrameCount() / kFramesPerSecond));
    return 1;
}

int IsaacDebugString(lua_State* state) {
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Isaac.DebugString accepts one string");
    }
    std::size_t length = 0;
    const char* text = luaL_checklstring(state, 1, &length);
    EmitLine(state, text, length);
    return 0;
}

int IsaacIsInGame(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "Isaac.IsInGame accepts no arguments");
    }
    // 与各家族同一个判断：只有处在 Runtime 的托管回调作用域里，Lua 才"在游戏里"——
    // 那里的游戏对象才是可读的。
    lua_pushboolean(state, InManagedCallbackScope() ? 1 : 0);
    return 1;
}

// 用我们自己的回调注册表派发 `callbackId`，后续参数原样交给已注册的 Lua 函数。
//
// 没有注册者时安静返回（PC 版同样如此）；返回值一律丢弃——PC 的返回值语义依赖具体回调
// 种类，那些种类我们还没有派发点，装作实现反而是错的。
//
// **数字 id 的语义保持不变**（既有行为与测试依赖它）。**字符串名**（PC 的自定义回调名）
// 走另一条路：`RunNamedCallbacks` 按名字派发，并在第一个非 nil 返回值处停下、返回那个值
// —— 与 PC 文档一致（`analysis/isaacdocs-snapshot/docs/Isaac.md:579`：
// "Runs all callbacks added under callbackId, breaking on the first return and returning that
// value"）。EID 在**加载期的最后一行**就调用 `Isaac.RunCallback("EID_POST_LOAD")`
// （`main.lua:2030`），所以字符串形式必须是"能派发"而不是报错。
int IsaacRunCallback(lua_State* state) {
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 1) {
        return luaL_error(state, "Isaac.RunCallback expects a callback id");
    }
    if (lua_type(state, 1) == LUA_TSTRING) {
        if (g_RunCallbackDepth >= kRunCallbackMaximumDepth) {
            return luaL_error(state, "Isaac.RunCallback nesting exceeds the maximum depth");
        }
        ++g_RunCallbackDepth;
        const int results = RunNamedCallbacks(state, lua_tostring(state, 1), 2);
        // `RunNamedCallbacks` 的注册结构留在栈上（返回值在它之上），只把深度还回去。
        --g_RunCallbackDepth;
        return results;
    }
    if (!lua_isinteger(state, 1)) {
        return luaL_error(state, "Isaac.RunCallback expects a callback id");
    }
    const lua_Integer requested = lua_tointegerx(state, 1, nullptr);
    if (requested < 0 ||
        requested > static_cast<lua_Integer>(std::numeric_limits<CallbackId>::max())) {
        return luaL_error(state, "Isaac.RunCallback received an out-of-range callback id");
    }
    const CallbackId id = static_cast<CallbackId>(requested);
    CallbackRegistry& registry = LuaRuntime::ManagedCallbackRegistry();
    const std::size_t registrations = registry.CountOf(id);
    if (registrations == 0) {
        return 0;
    }
    if (g_RunCallbackDepth >= kRunCallbackMaximumDepth) {
        return luaL_error(state, "Isaac.RunCallback nesting exceeds the maximum depth");
    }
    ++g_RunCallbackDepth;
    for (std::size_t index = 0; index < registrations; ++index) {
        const CallbackDescriptor* descriptor = registry.At(id, index);
        if (descriptor == nullptr || !descriptor->valid()) {
            break;
        }
        lua_rawgeti(state, LUA_REGISTRYINDEX, descriptor->luaReference);
        for (int argument = 2; argument <= argumentCount; ++argument) {
            lua_pushvalue(state, argument);
        }
        const int status = lua_pcallk(state, argumentCount - 1, 0, 0, 0, nullptr);
        if (status != LUA_OK) {
            // 先把深度还回去，再把错误原样抛给调用方：`lua_error` 会 longjmp，
            // 不先减就会让深度永久泄漏，之后每一次 `RunCallback` 都会被上限挡住。
            --g_RunCallbackDepth;
            return lua_error(state);
        }
    }
    --g_RunCallbackDepth;
    return 0;
}

// --- 引擎实体只读视图（批次 2）------------------------------------------------
//
// 偏移来源 = **反汇编定位 + 真机 verified**：常量表在 `runtime_constants.hpp`
// （全局槽 `0xAAC698`、玩家向量 `+0x25C50/+0x25C58`、字段偏移与 `Entity_Player` 的
// vptr `0xA37110`/尺寸 `0x2FA0`/`HasCollectible` `0x27D3F4`），并已由真机探针
// `IsaacModRuntime_ProbeEnginePlayers` 的崩溃报告（魔数 `ISAACGP1`）交叉验证：1 个玩家、
// `Type == 1`、`Variant == 0`、`SubType == 0`、`PlayerType == 0`（Isaac）、下标 0、
// 位置 `(80.0, 280.0)`、vptr 命中。所以这里按"已证事实"实现，不再重新验证偏移。
//
// **读是只读的；写只有一个入口**（2026-09-16 起）：`Position`/`Velocity`/`Size`/收藏品数组
// 这些字段仍然只读 —— 它们参与碰撞、AI、掉落与存档，写坏的症状是随机崩溃或存档损坏，
// 而"写哪些字段、什么时机写是安全的"目前只有 `ControlsCooldown` 一条有 PC 语义与引擎读点
// 双重支撑（见 `EntityNewIndex`）。其余写路径一律经 `WriteEngine`，且必须先确认该页可写。
bool IsEngineMemoryReadable(std::uintptr_t address, std::size_t length) noexcept {
    // 实现见 `interfaces/lua/engine_memory_guard.hpp`：四个 API 族共用同一份缓存与计数。
    //
    // 语义逐字不变：空地址、长度 0、地址回绕一律 false；设备侧仍要求返回码成功、区间完整落在
    // 映射内、且带 `Perm_R`。变的是**命中缓存时不再进内核** —— `svcQueryMemory` 返回的描述
    // 覆盖一整段连续且权限一致的映射，整段都能复用那一次结论。缓存只在受管回调派发期间有效
    // （`EngineGuardEnterDispatch`/`EngineGuardLeaveDispatch`），所以过期窗口为零。
    if (!EngineGuardRangeUsable(address, length)) {
        return false;
    }
#if defined(__SWITCH__)
    // 派发作用域内命中已登记的映射区间：这一帧的同一段内存不再重复问内核。
    if (EngineGuardLookupCached(address, length)) {
        return true;
    }
    const std::uint64_t syscallStart = EngineGuardSyscallBegin();
    MemoryInfo info{};
    u32 pageInfo = 0;
    // 显式写 `std::uint32_t`：本命名空间里有一个同名模板 `Result<T>` 会遮蔽 libnx 的
    // `Result`（u32）typedef，直接写 `Result` 会撞上模板推导。
    const std::uint32_t result = svcQueryMemory(&info, &pageInfo, address);
    EngineGuardSyscallEnd(syscallStart);
    if (R_FAILED(result) || info.size == 0 || info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    if (address < info.addr || address + length > info.addr + info.size ||
        (info.perm & Perm_R) == 0) {
        return false;
    }
    // 这次系统调用描述的是一整段连续且权限一致的映射，整段都可以复用这个结论。
    EngineGuardRememberReadable(info.addr, info.addr + info.size);
    return true;
#else
    // 宿主构建没有 `svcQueryMemory`；宿主测试的"引擎内存"是本进程 malloc 出来的伪造块，
    // 本来就一定可读（见 `runtime/tests/test_lua_entity_player.py`）。
    return true;
#endif
}

// 读一个引擎字段：先做可读性检查再 `memcpy`。指针猜错时必须变成"读不到"而不是"读了就崩"。
template <typename T>
bool ReadEngine(std::uintptr_t address, T* value) noexcept {
    if (value == nullptr || !IsEngineMemoryReadable(address, sizeof(T))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

// 写一个引擎字段（2026-09-16 新增，与上面的读原语成对）。
//
// 上面那段"本批次不提供任何写引擎内存的接口"的注释是批次 3 的口径，从**写通道**这一批起
// 被取代：现在**允许**写，但只有一个入口（`WriteEngine`）且必须先确认"这一页可写"。
// 判据与读侧同源却**分开缓存**：可读（`Perm_R`）不等于可写（`Perm_W`），模块映像的代码段与
// 只读数据段就是可读不可写（详见 `interfaces/lua/engine_memory_guard.hpp` 里
// `EngineGuardWritableRegionCache` 的注释）。
//
// 目前唯一的调用点是 `EntityPlayer.ControlsCooldown`（`EntityNewIndex`）—— EID 用它抑制输入。
// 其它字段**没有**写入口，理由与批次 3 相同：写参与碰撞/AI/掉落/存档的字段，症状是随机崩溃
// 或存档损坏，必须有证据才做。
bool IsEngineMemoryWritable(std::uintptr_t address, std::size_t length) noexcept {
    if (!EngineGuardRangeUsable(address, length)) {
        return false;
    }
#if defined(__SWITCH__)
    if (EngineGuardLookupWritable(address, length)) {
        return true;
    }
    const std::uint64_t syscallStart = EngineGuardSyscallBegin();
    MemoryInfo info{};
    u32 pageInfo = 0;
    const std::uint32_t result = svcQueryMemory(&info, &pageInfo, address);
    EngineGuardSyscallEnd(syscallStart);
    if (R_FAILED(result) || info.size == 0 || info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    if (address < info.addr || address + length > info.addr + info.size ||
        (info.perm & Perm_W) == 0) {
        return false;
    }
    EngineGuardRememberWritable(info.addr, info.addr + info.size);
    return true;
#else
    // 宿主构建没有 `svcQueryMemory`：宿主测试里的"引擎内存"是本进程申请出来的伪造块。
    return true;
#endif
}

template <typename T>
bool WriteEngine(std::uintptr_t address, const T& value) noexcept {
    if (!IsEngineMemoryWritable(address, sizeof(T))) {
        return false;
    }
    std::memcpy(reinterpret_cast<void*>(address), &value, sizeof(T));
    return true;
}

// 玩家数量上限（`kEnginePlayerMaximumCount`）现在是共享常量（`runtime_constants.hpp`），
// 因为 `Game:GetNumPlayers`（`game_api.cpp`）用同一条向量做同一层保险。

// `*(u64*)candidate == base + kEntityPlayerVtableOffset` —— 判"这个指针真是一张 Entity_Player"
// 的最强判据（真机探针的 bit4 就是它，已命中）。
bool IsEntityPlayer(std::uintptr_t candidate) noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (candidate == 0 || base == 0 || base > UINTPTR_MAX - kEntityPlayerVtableOffset) {
        return false;
    }
    std::uintptr_t vtable = 0;
    if (!ReadEngine(candidate, &vtable)) {
        return false;
    }
    return vtable == base + kEntityPlayerVtableOffset;
}

// 引擎 `Game` 的玩家向量首尾（`Game + kGamePlayerArrayBeginOffset/EndOffset`）。
// 返回 0 表示"读不到"（基址未发布 / 槽不可读 / 槽为空 / `Game` 为空 / 向量不可读或不自洽）。
//
// 这是**唯一**一处做这件事的地方：`Isaac.GetPlayer` 与 `Game:GetNumPlayers` 都从这里取答案，
// 因此不可能再出现"一个说有 1 个玩家、另一个说玩家是 nil"（真机报告 `01789207110` 的缺陷）。
// `Isaac.FindInRadius` 的探针读数（2026-09-12）：描述文字一个字都没画出来，而
// `Font:DrawString*` 调用次数为 0 —— 说明 EID 的 `OnRender` 没走到绘制。它选描述的第一步就是
// `Isaac.FindInRadius(sourcePos, MaxDistance*40, searchPartitions)`（`main.lua:1466`），
// 所以"这个查询到底找出了几个实体"是下一个分界点：
//   * 查询恒返回空 ⇒ 问题在我们这边（房间指纹/分区掩码/半径判据）；
//   * 查询有结果而描述仍不出 ⇒ 问题在描述构建/选择。
// `Isaac.GetPlayer` 的成功/失败计数（原因 1）：EID 的 `EID.player` 依赖它；恒失败则
// `OnRender` 里的 `for ... in ipairs({ EID.player })` 一次都不执行，描述第一步永不发生。
std::atomic<std::uint32_t> g_GetPlayerSuccess{0};
std::atomic<std::uint32_t> g_GetPlayerFailure{0};
std::atomic<std::uint32_t> g_FindInRadiusCalls{0};
std::atomic<std::uint32_t> g_FindInRadiusResults{0};
std::atomic<std::uint32_t> g_FindInRadiusLive{0};
std::atomic<std::uint32_t> g_FindInRadiusPlayers{0};
std::atomic<std::uint32_t> g_FindInRadiusEffects{0};
std::atomic<std::uint32_t> g_FindInRadiusRoomOk{0};
std::atomic<std::uint32_t> g_FindInRadiusFingerprint{0};
std::atomic<std::uint32_t> g_FindInRadiusLastMask{0};
std::atomic<std::uint32_t> g_FindInRadiusLastRadius{0};



// 玩家查询的**阶段码**（探针读；语义见 `EnginePlayerLookup::stage` 的注释）。
std::atomic<std::uint32_t> g_EnginePlayerLookupStage{0};
std::atomic<std::uintptr_t> g_EnginePlayerLookupFirst{0};
std::atomic<std::uintptr_t> g_EnginePlayerLookupVtable{0};

std::atomic<std::uint32_t> g_EnginePlayerLookupCount{0};
// `ResolveEnginePlayerBegin` **自己**读到的 begin/end（探针用：与探针那一侧的读数对照，
// 才能分清"解析器读不到"与"探针读法不同"）。
std::atomic<std::uintptr_t> g_ResolverBegin{0};
std::atomic<std::uintptr_t> g_ResolverEnd{0};

void RecordEnginePlayerLookup(std::uint32_t stage, std::uintptr_t first,
                              std::uintptr_t second, std::uint32_t count = 0) noexcept {
    g_EnginePlayerLookupCount.store(count, std::memory_order_relaxed);
    g_EnginePlayerLookupVtable.store(second, std::memory_order_relaxed);
    g_EnginePlayerLookupFirst.store(first, std::memory_order_relaxed);
    g_EnginePlayerLookupStage.store(stage, std::memory_order_release);
}

std::uintptr_t ResolveEntityPlayerBegin(std::uintptr_t* beginOut, std::size_t* countOut) noexcept {
    if (beginOut == nullptr || countOut == nullptr) {
        RecordEnginePlayerLookup(1, 0, 0);
        return 0;
    }
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        RecordEnginePlayerLookup(1, 0, 0);
        return 0;
    }
    // 两级解引用：槽里是**指针变量**，`*(u64*)槽` 才是 `Game*`。
    std::uintptr_t ownerSlot = 0;
    std::uintptr_t game = 0;
    if (!ReadEngine(base + kGameOwnerGlobalSlotOffset, &ownerSlot) || ownerSlot == 0) {
        RecordEnginePlayerLookup(2, 0, 0);
        return 0;
    }
    if (!ReadEngine(ownerSlot, &game) || game == 0 ||
        game > UINTPTR_MAX - kGamePlayerArrayEndOffset) {
        RecordEnginePlayerLookup(3, 0, 0);
        return 0;
    }
    std::uintptr_t begin = 0;
    std::uintptr_t end = 0;
    // **判据只用值本身，不用"那次读成功了吗"（2026-09-12 第四轮真机定案）**。
    //
    // 为什么：真机报告 `01789209282` 里，探针（另一个回调）**直接读同一个地址成功**，而
    // `Isaac.GetPlayer` 路径上那次读被 `ReadEngine` 判为失败 —— 同一个地址、同一帧，结论相反。
    // 说明 `ReadEngine` 的成功条件是**环境相关**的（它对 `__SWITCH__` 走 `svcQueryMemory` 的
    // 页属性检查，读不到只代表"这一刻不该读"，不代表"值不是 0"）。把它当作"内存里没有这个值"
    // 的证据，就会把默认构造的空向量误判成"读不到"。
    //
    // 于是这里改成纯值判据，而 `std::vector` 的默认构造形态（`begin == end == 0`）是**合法空向量**：
    //   * `begin == 0 && end == 0` → count = 0，readable = true（"确实没有玩家"，而不是"读不到"）；
    //   * `end >= begin` 且 `begin != 0` → 正常，count = (end - begin) / 8；
    //   * 其余（`begin == 0` 但 `end != 0`、或 `end < begin`）→ 区间不自洽，阶段 4。
    //
    // 这一条修正的直接后果：EID 的 `for i = 0, game:GetNumPlayers() - 1 do local player =
    // Isaac.GetPlayer(i) ... player.QueuedItem`（`features/eid_api.lua:2620-2625`）在"还没有
    // 玩家"的帧上不会再拿到 nil（循环次数为 0），也就不会再抛错、被派发器静默摘除。
    // 两次读的**返回值刻意不用**（见上）：值才是判据。
    const bool beginRead = ReadEngine(game + kGamePlayerArrayBeginOffset, &begin);
    const bool endRead = ReadEngine(game + kGamePlayerArrayEndOffset, &end);
    g_ResolverBegin.store(beginRead ? begin : ~static_cast<std::uintptr_t>(0),
                          std::memory_order_relaxed);
    g_ResolverEnd.store(endRead ? end : ~static_cast<std::uintptr_t>(0),
                        std::memory_order_relaxed);
    if (begin == 0 && end == 0) {
        // 合法空向量（也涵盖"两次读都没读到"这种最坏情形）：如实报 0 个玩家。
        // 宁可让 `Game:GetNumPlayers()` 答"没有玩家"（那时 `Isaac.GetPlayer(0)` 本来就是 nil），
        // 也不要答"有 1 个玩家"然后让 Mod 去索引一个 nil。
        *beginOut = 0;
        *countOut = 0;
        RecordEnginePlayerLookup(0, 0, 0, 0);
        return 0;
    }
    if (begin == 0 || end < begin) {
        RecordEnginePlayerLookup(4, begin, end);
        return 0;
    }
    const std::size_t count = static_cast<std::size_t>((end - begin) / sizeof(std::uintptr_t));
    if (count > kEnginePlayerMaximumCount) {
        RecordEnginePlayerLookup(5, 0, 0);
        return 0;
    }
    *beginOut = begin;
    *countOut = count;
    RecordEnginePlayerLookup(0, 0, 0, static_cast<std::uint32_t>(count));
    return begin;
}

// `Isaac.GetPlayer(index)` 的整条链路。返回 0 表示"拿不到"，调用方据此返回 nil：
// 向量读不到 / 下标越界 / 元素为空 / vptr 不匹配。
std::uintptr_t ResolveEntityPlayer(std::size_t index) noexcept {
    std::uintptr_t begin = 0;
    std::size_t count = 0;
    if (ResolveEntityPlayerBegin(&begin, &count) == 0) {
        return 0;  // 阶段码已由 ResolveEntityPlayerBegin 记录（1..5）
    }
    if (index >= count) {
        RecordEnginePlayerLookup(6, 0, 0);
        return 0;
    }
    std::uintptr_t player = 0;
    std::uintptr_t vtable = 0;
    if (!ReadEngine(begin + index * sizeof(std::uintptr_t), &player) || player == 0) {
        RecordEnginePlayerLookup(7, 0, 0);
        return 0;
    }
    if (!ReadEngine(player, &vtable) || !IsEntityPlayer(player)) {
        // 这一档最值钱：元素存在、指针非空，但 vptr 不是 `Entity_Player`
        // （偏移不对 / 该槽放的是别的实体 / 内存还没写好）。把原始 vptr 报出来。
        RecordEnginePlayerLookup(8, player, vtable);
        return 0;
    }
    RecordEnginePlayerLookup(0, player, vtable);
    return player;
}

void PushEntityHandle(lua_State* state, std::uintptr_t entity, const char* metatable) {
    auto* handle = static_cast<EntityHandle*>(lua_newuserdata(state, sizeof(EntityHandle)));
    handle->entity = reinterpret_cast<void*>(entity);
    luaL_getmetatable(state, metatable);
    lua_setmetatable(state, -2);
}

// 前置声明：通用 `Entity` 判据定义在下面的"房间实体容器与查询"段（`ValidatedEntity` 要用它）。
bool IsEntityVtableInRange(std::uintptr_t vtable) noexcept;
bool IsEntityPointer(std::uintptr_t candidate) noexcept;

// 句柄 → 通过校验的实体指针（0 = 失效）。**每一次**访问都重做校验：句柄可能跨过换局/重开一局，
// 过期的原生指针绝不允许被解引用。
//
// 批次 4 起判据从"vptr == `Entity_Player`"放宽成**通用 `Entity`**：`vptr` 落在
// `[base + 0xA34F58, base + 0xA38230)`（`Entity` 家族的 vtable 区间，见 `runtime_constants.hpp`）。
// 之所以能放宽：句柄里的指针**只可能**来自实体容器遍历（`Isaac.FindInRadius`/`FindByType`）
// 或玩家向量的遍历结果（`Isaac.GetPlayer`），区间校验是第二道闸门而不是身份来源。
std::uintptr_t ValidatedEntity(const EntityHandle* handle) noexcept {
    if (handle == nullptr) {
        return 0;
    }
    const std::uintptr_t candidate = reinterpret_cast<std::uintptr_t>(handle->entity);
    return IsEntityPointer(candidate) ? candidate : 0;
}

// `EntityPlayer` 专属方法（`HasCollectible`/`GetPlayerType`/血量/主动道具/饰品/`GetData`）
// 仍然要求**精确**的 `Entity_Player` vptr：区间校验只保证"这是一张 Entity"，把一张眼泪或
// 掉落物当玩家去读 `+0x16B8` 虽然不会越界，但拿到的是别人的字段。
std::uintptr_t ValidatedEntityPlayer(const EntityHandle* handle) noexcept {
    if (handle == nullptr) {
        return 0;
    }
    const std::uintptr_t candidate = reinterpret_cast<std::uintptr_t>(handle->entity);
    return IsEntityPlayer(candidate) ? candidate : 0;
}

// `ToPlayer` / `ToPickup` / `GetData` 挂在 `Entity`（PC 就在基类上），但调用方拿到的通常是
// `EntityPlayer`/`EntityPickup` 视图，所以三张元表都要接受：只认一张会在 `player:ToPlayer()`
// 处误报。
EntityHandle* CheckEntityHandle(lua_State* state, int index) {
    auto* handle =
        static_cast<EntityHandle*>(luaL_testudata(state, index, kEntityPlayerMetatable));
    if (handle == nullptr) {
        handle = static_cast<EntityHandle*>(luaL_testudata(state, index, kEntityMetatable));
    }
    if (handle == nullptr) {
        handle = static_cast<EntityHandle*>(luaL_testudata(state, index, kEntityPickupMetatable));
    }
    if (handle == nullptr) {
        luaL_error(state, "expected an Entity, EntityPlayer or EntityPickup");
        return nullptr;  // 不会到达：`luaL_error` 长跳到受保护调用
    }
    return handle;
}

// 读一个整型参数。**接受范围与 PC 的 `luaL_checkinteger` 一致**：整数、整值浮点（`57.0`）、
// 可转换的数字串都收，`1.5`/`"nope"`/缺参数都不收 —— 与 `ReadItemId` 同一口径。
//
// 为什么不能用 `lua_isinteger`：Lua 5.3 里 `57.0` 是 **float**，`lua_isinteger` 返回 false，
// 而 `lua_tointegerx` 会把它精确转成 57。Mod 写 `EntityPartition.ENEMY * 1.0` 或者把掩码
// 从配置里读出来（JSON 数字都是 float）都会踩到这条：判错就会安静地返回空表。
bool ReadIntegerArgument(lua_State* state, int index, lua_Integer* value) noexcept {
    if (value == nullptr || lua_gettop(state) < index) {
        return false;
    }
    int isNumber = 0;
    const lua_Integer converted = lua_tointegerx(state, index, &isNumber);
    if (isNumber == 0) {
        return false;
    }
    *value = converted;
    return true;
}

// --- 房间实体容器与查询（批次 4）-----------------------------------------------
//
// 全部偏移的出处 = `runtime_constants.hpp` 的"房间实体容器"段（2026-09-12 反汇编静态定位，
// `docs/问题与解决记录.md` 批次 4；`Room::AddEntity` 的 `add x0,x0,#0x1950` 是内嵌而非指针的铁证）。
// 五条纪律：
//
//   1. **只读**。这一整段不写引擎内存（唯一的写是 `GetData()` 自己那张 Lua 表）。
//   2. **每次都校验容器指纹**（`EL+0x50==0x4000 && EL+0x80==0x800 && EL+0xE0==0x8000`）。
//      三个容量都是固定常量、从不重分配，所以它们同时是"这段内存真是一张 `EntityList`"的
//      判据。指纹不匹配 → 返回**空结果**，不报错、不解引用。
//   3. **count 必须 <= 容量**，否则整张表作废（防止把垃圾当长度去遍历）。
//   4. **不缓存实体指针跨帧**：句柄里的地址只当身份，每次访问重做 vptr 校验。
//      （`GetData()` 的 Lua 表缓存是唯一的例外，键里带房间 epoch，见 `EntityDataStore`。）
//   5. 不调引擎的 `QueryRadius`/`QueryType`：它们的 sret 指向 `EL+0xD8` 的**单调 arena**，
//      不可重入、不可跨帧持有。自己遍历 `EL+0x78`（引擎 `collide`/`Update`/`CountType` 用的
//      同一张表）更稳。

// `Game*`：与 `Isaac.GetPlayer` **完全同一条链**（模块基址 + `kGameOwnerGlobalSlotOffset`
// 是 `g_Game` 的槽，槽里是**指针变量**，解一次才是 `Game*`）。0 = 拿不到。
std::uintptr_t ResolveGameAddress() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        return 0;
    }
    std::uintptr_t ownerSlot = 0;
    std::uintptr_t game = 0;
    if (!ReadEngine(base + kGameOwnerGlobalSlotOffset, &ownerSlot) || ownerSlot == 0 ||
        !ReadEngine(ownerSlot, &game) || game == 0) {
        return 0;
    }
    return game;
}

// `Room*` = `*(u64*)(Game + 0x21550)`（`Level` 内嵌在 `Game` 起始处，这是 Level 的那个成员）。
std::uintptr_t ResolveRoom() noexcept {
    const std::uintptr_t game = ResolveGameAddress();
    if (game == 0 || game > UINTPTR_MAX - kGameRoomPointerOffset) {
        return 0;
    }
    std::uintptr_t room = 0;
    if (!ReadEngine(game + kGameRoomPointerOffset, &room)) {
        return 0;
    }
    return room;
}

// `EL+0x1950 + 相对偏移`：内嵌对象，所以是**加法**。
struct EntityTableSpec {
    std::uintptr_t beginOffset;
    std::uintptr_t capacityOffset;
    std::uintptr_t countOffset;
    std::uint32_t expectedCapacity;
};

// 活表（`EL+0x78`，cap 0x800）：房间内活动实体全集，枚举用它。
constexpr EntityTableSpec kLiveEntityTable{kEntityListLiveBeginOffset,
                                           kEntityListLiveCapacityOffset,
                                           kEntityListLiveCountOffset, kEntityListLiveCapacity};
// EFFECT 表（`EL+0xA8`，cap 0x4000）：`Type == 0x3E8` 走这张。
constexpr EntityTableSpec kEffectEntityTable{kEntityListEffectBeginOffset,
                                             kEntityListEffectCapacityOffset,
                                             kEntityListEffectCountOffset,
                                             kEntityListGeneralCapacity};

struct EntityTableView {
    std::uintptr_t begin{0};
    std::uint32_t count{0};
};

// 容器指纹：三个固定容量必须同时成立（顺序与 `docs/问题与解决记录.md` 一致）。
bool EntityListFingerprintMatches(std::uintptr_t list) noexcept {
    if (list == 0) {
        return false;
    }
    std::uint32_t generalCapacity = 0;
    std::uint32_t liveCapacity = 0;
    std::uint32_t arenaCapacity = 0;
    if (!ReadEngine(list + kEntityListGeneralCapacityOffset, &generalCapacity) ||
        !ReadEngine(list + kEntityListLiveCapacityOffset, &liveCapacity) ||
        !ReadEngine(list + kEntityListArenaCapacityOffset, &arenaCapacity)) {
        return false;
    }
    return generalCapacity == kEntityListGeneralCapacity &&
           liveCapacity == kEntityListLiveCapacity &&
           arenaCapacity == kEntityListArenaCapacity;
}

// 读一条 `Entity**` 表：先校验它自己的容量（指纹的一部分），再校验 `count <= cap`，
// 最后**一次**可读性检查覆盖整个元素数组（真机上 `svcQueryMemory` 是系统调用，逐个元素检查
// 会让一次全房间扫描变成上千次调用，所以元素读取用 `ReadEngineUnchecked`）。
bool ReadEntityTable(std::uintptr_t list, const EntityTableSpec& spec, EntityTableView* view) noexcept {
    if (list == 0 || view == nullptr || list > UINTPTR_MAX - spec.countOffset) {
        return false;
    }
    std::uint32_t capacity = 0;
    std::uint32_t count = 0;
    if (!ReadEngine(list + spec.capacityOffset, &capacity) ||
        capacity != spec.expectedCapacity ||
        !ReadEngine(list + spec.countOffset, &count) || count > capacity) {
        return false;
    }
    view->begin = 0;
    view->count = count;
    if (count == 0) {
        return true;
    }
    std::uintptr_t begin = 0;
    if (!ReadEngine(list + spec.beginOffset, &begin) || begin == 0 ||
        !IsEngineMemoryReadable(begin, static_cast<std::size_t>(count) * sizeof(std::uintptr_t))) {
        return false;
    }
    view->begin = begin;
    return true;
}

// 已经用一次可读性检查覆盖过整段时使用：只做 `memcpy`，不再重复系统调用。
template <typename T>
bool ReadEngineUnchecked(std::uintptr_t address, T* value) noexcept {
    if (value == nullptr) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

// `vptr` 是否落在 `Entity` 家族的 vtable 区间里。
bool IsEntityVtableInRange(std::uintptr_t vtable) noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || vtable == 0 || base > UINTPTR_MAX - kEntityVtableRangeEndOffset) {
        return false;
    }
    return vtable >= base + kEntityVtableRangeBeginOffset &&
           vtable < base + kEntityVtableRangeEndOffset;
}

// 通用 `Entity` 判据：`Type` 无关，只看 vptr 是否落在 `Entity` 家族的 vtable 区间里。
//
// 为什么这是安全的：`vtable` 是我们**先读出来**再比较的值，比较之前不解引用任何东西；
// 猜错的指针只会让 `vptr` 落在区间外而被拒。
bool IsEntityPointer(std::uintptr_t candidate) noexcept {
    if (candidate == 0) {
        return false;
    }
    std::uintptr_t vtable = 0;
    if (!ReadEngine(candidate, &vtable)) {
        return false;
    }
    return IsEntityVtableInRange(vtable);
}

// 扫描用的实体快照：**一次**可读性检查覆盖 `[entity, entity + 0x348)`，之后逐字段 `memcpy`。
struct EntitySnapshot {
    std::uintptr_t address{0};
    std::uintptr_t vtable{0};
    std::uint64_t flags{0};
    std::uint32_t type{0};
    std::uint32_t variant{0};
    std::uint32_t subType{0};
    std::uint32_t index{0};
    float x{0.0F};
    float y{0.0F};
    float size{0.0F};
};

bool CaptureEntity(std::uintptr_t address, EntitySnapshot* snapshot) noexcept {
    if (address == 0 || snapshot == nullptr ||
        !IsEngineMemoryReadable(address, kEntitySnapshotSpan)) {
        return false;
    }
    const auto field = [address](std::uintptr_t offset) noexcept {
        return address + offset;
    };
    std::uintptr_t vtable = 0;
    std::uint64_t flags = 0;
    if (!ReadEngineUnchecked(field(0), &vtable) || !ReadEngineUnchecked(field(kEntityFlagsOffset), &flags)) {
        return false;
    }
    if (!IsEntityVtableInRange(vtable)) {
        return false;
    }
    snapshot->address = address;
    snapshot->vtable = vtable;
    snapshot->flags = flags;
    static_cast<void>(ReadEngineUnchecked(field(kEntityTypeOffset), &snapshot->type));
    static_cast<void>(ReadEngineUnchecked(field(kEntityVariantOffset), &snapshot->variant));
    static_cast<void>(ReadEngineUnchecked(field(kEntitySubTypeOffset), &snapshot->subType));
    static_cast<void>(ReadEngineUnchecked(field(kEntityIndexOffset), &snapshot->index));
    static_cast<void>(ReadEngineUnchecked(field(kEntityPositionOffset), &snapshot->x));
    static_cast<void>(ReadEngineUnchecked(field(kEntityPositionOffset + sizeof(float)), &snapshot->y));
    static_cast<void>(ReadEngineUnchecked(field(kEntitySizeOffset), &snapshot->size));
    return true;
}

// `EntityFlag.FLAG_NO_QUERY`（`+0x1B8` bit46）：查询结果里必须跳过它。
bool EntityHasNoQueryFlag(const EntitySnapshot& snapshot) noexcept {
    return (snapshot.flags & (1ULL << kEntityFlagNoQueryBit)) != 0;
}

// PC `EntityPartition` 的位（`enums.lua:4489`；与引擎 `w2` 掩码逐位一致）。
constexpr std::uint32_t kPartitionFamiliar = 1U << 0;
constexpr std::uint32_t kPartitionBullet = 1U << 1;
constexpr std::uint32_t kPartitionTear = 1U << 2;
constexpr std::uint32_t kPartitionEnemy = 1U << 3;
constexpr std::uint32_t kPartitionPickup = 1U << 4;
constexpr std::uint32_t kPartitionPlayer = 1U << 5;
constexpr std::uint32_t kPartitionEffect = 1U << 6;
// 活表里"除了 PLAYER 之外的普通分区"。PLAYER 由玩家向量单独提供（引擎自己也不走 CellSpace）。
constexpr std::uint32_t kLiveTablePartitions =
    kPartitionFamiliar | kPartitionBullet | kPartitionTear | kPartitionEnemy | kPartitionPickup;

// 分类判据：逐条照抄引擎 `EntityList::collide()`（`0x77690`–`0x778a0` 的跳转表）。
std::uint32_t ClassifyEntity(const EntitySnapshot& snapshot) noexcept {
    const std::uint32_t type = snapshot.type;
    if (type == 2) return kPartitionTear;
    if (type == 3) return snapshot.variant == 0xEF ? kPartitionEnemy : kPartitionFamiliar;
    if (type == 8) return kPartitionTear;    // KNIFE
    if (type == 9) return kPartitionBullet;  // PROJECTILE
    if (type == 5 || type == 6) return kPartitionPickup;
    if (type == 4) return kPartitionEnemy;   // BOMB
    if (type == kEntityTypeEffect) return kPartitionEffect;
    if (type == kEntityTypePlayer) return kPartitionPlayer;
    if (type - kEntityEnemyTypeBase < kEntityEnemyTypeSpan) return kPartitionEnemy;
    return 0;
}

// `IsEnemy = (u32)(Type - 10) < 0x3DE`：`Isaac.CountEnemies()` 的判据（任务给定的那一条，
// 也正是 `collide()` 里的同一条比较）。
bool IsEnemyType(std::uint32_t type) noexcept {
    return type - kEntityEnemyTypeBase < kEntityEnemyTypeSpan;
}

// 半径判据（引擎原文）：`dist² < (radius + e.Size)²` —— **把实体 Size 加进半径**。
bool EntityMatchesRadius(const EntitySnapshot& snapshot, double x, double y,
                         double radius) noexcept {
    const double dx = static_cast<double>(snapshot.x) - x;
    const double dy = static_cast<double>(snapshot.y) - y;
    const double reach = radius + static_cast<double>(snapshot.size);
    return dx * dx + dy * dy < reach * reach;
}

// 一次查询最多返回多少条。正常房间里是几十条；这个上限只为"容器被写坏成 count == cap"
// 这类情形兜底（`EL+0x78` 的 cap 是 0x800，EFFECT 表是 0x4000）。
constexpr std::size_t kMaximumQueryResults = 4096;
std::uintptr_t g_QuerySeen[kMaximumQueryResults] = {};
std::size_t g_QuerySeenCount = 0;

// 每个查询开始时清空。去重表是**文件级**的（不是栈上的：`kMaximumQueryResults` 个 u64 放进
// 栈会占 32 KiB），而 Lua 只在托管回调里单线程执行，所以"每个查询前清一次"就够了 ——
// 漏掉这一次会让**第二次**调用去重表里全是上一次的结果，直接返回空表。
void ResetQueryDedup() noexcept {
    g_QuerySeenCount = 0;
}

bool QueryRecordUnique(std::uintptr_t entity) noexcept {
    for (std::size_t index = 0; index < g_QuerySeenCount; ++index) {
        if (g_QuerySeen[index] == entity) {
            return false;
        }
    }
    if (g_QuerySeenCount >= kMaximumQueryResults) {
        return false;
    }
    g_QuerySeen[g_QuerySeenCount++] = entity;
    return true;
}

// 查询的推出状态：结果表在栈上的下标 + 已经写进去的条数。
struct QuerySink {
    lua_State* state{nullptr};
    int resultIndex{0};
    int emitted{0};
};

void PushQueryResult(QuerySink* sink, std::uintptr_t entity) {
    if (sink == nullptr || sink->state == nullptr ||
        sink->emitted >= static_cast<int>(kMaximumQueryResults) || !QueryRecordUnique(entity)) {
        return;
    }
    // 玩家实体给 `EntityPlayer` 视图（PC 里 `FindByType(ENTITY_PLAYER)` 返回的就是它），
    // 其余给 `Entity`。判据是**精确**的 `Entity_Player` vptr，不是区间。
    PushEntityHandle(sink->state, entity,
                     IsEntityPlayer(entity) ? kEntityPlayerMetatable : kEntityMetatable);
    lua_rawseti(sink->state, sink->resultIndex, ++sink->emitted);
}

// 遍历一张 `Entity**` 表，只把"vptr 落在 Entity 区间"的非空元素交给 `visit`。
template <typename Visit>
void ForEachEntityInTable(std::uintptr_t list, const EntityTableSpec& spec, Visit&& visit) {
    EntityTableView view{};
    if (!ReadEntityTable(list, spec, &view)) {
        return;
    }
    for (std::uint32_t index = 0; index < view.count; ++index) {
        std::uintptr_t entity = 0;
        if (!ReadEngineUnchecked(view.begin + static_cast<std::size_t>(index) * sizeof(std::uintptr_t),
                                 &entity)) {
            return;
        }
        if (entity != 0 && IsEntityPointer(entity)) {
            visit(entity);
        }
    }
}

// `Game + 0x25C50` 的 `std::vector<Entity_Player*>`（与 `Isaac.GetPlayer` 同一对偏移）。
// `PLAYER` 分区走这里，不走 CellSpace —— 与引擎 `QueryRadius` 的分支一致。
template <typename Visit>
void ForEachPlayerEntity(std::uintptr_t game, Visit&& visit) {
    if (game == 0 || game > UINTPTR_MAX - kGamePlayerArrayEndOffset) {
        return;
    }
    std::uintptr_t begin = 0;
    std::uintptr_t end = 0;
    if (!ReadEngine(game + kGamePlayerArrayBeginOffset, &begin) ||
        !ReadEngine(game + kGamePlayerArrayEndOffset, &end) || begin == 0 || end < begin) {
        return;
    }
    const std::size_t count = static_cast<std::size_t>((end - begin) / sizeof(std::uintptr_t));
    if (count == 0 || count > kEnginePlayerMaximumCount ||
        !IsEngineMemoryReadable(begin, count * sizeof(std::uintptr_t))) {
        return;
    }
    for (std::size_t index = 0; index < count; ++index) {
        std::uintptr_t entity = 0;
        if (!ReadEngineUnchecked(begin + index * sizeof(std::uintptr_t), &entity)) {
            return;
        }
        if (entity != 0 && IsEntityPointer(entity)) {
            visit(entity);
        }
    }
}

// --- `Entity:GetData()` 的 Runtime 自管存储（批次 4）----------------------------
//
// 引擎**没有** `GetData` 字段（三条独立否定证据见 `docs/问题与解决记录.md`），PC 的语义是
// "一张与实体同生命周期的 Lua 表"（`Entity.md:346`："Initially, this will always be an empty
// table. Any values stored in the table by mods will persist until the entity is despawned."）。
// 所以这里由 Runtime 自己维护：
//
//   * 键 = **房间 epoch + 实体指针 + 生成帧**（`+0x2F4`）。
//   * **房间 epoch** 在"`Room*` 或活表 `begin` 变化"时 +1 —— 换房间 / 换局 / 实体表重排都会
//     让它变。这是与 PC 的**已知差异**：PC 里"跨房间存活"的实体（玩家/跟班/`FLAG_PERSISTENT`）
//     的 ModData 会保留，我们这里会连同旧房间的表一起失效（宁可丢数据，也不能把新房间里
//     **地址恰好复用**的另一个实体的数据串给它）。
//   * **生成帧** 再兜一层"同一地址被新实体复用"：新实体的生成帧与旧的几乎不可能相同。
//   * 表存在 Lua registry 里（`luaL_ref`），换 Lua 状态时随旧状态一起消失，所以
//     `ResetEntityDataStore()` **不**做 `luaL_unref`。
//   * 容量固定 `kEntityDataCapacity`，满了按"最久未使用"淘汰（并 `luaL_unref`），
//     所以这里不会因为 Mod 遍历大量实体而无限增长。
constexpr std::size_t kEntityDataCapacity = 128;

struct EntityDataEntry {
    bool occupied{false};
    std::uint64_t epoch{0};
    std::uintptr_t entity{0};
    std::uint32_t spawnFrame{0};
    int reference{LUA_NOREF};
    std::uint64_t lastUsed{0};
};

std::array<EntityDataEntry, kEntityDataCapacity> g_EntityDataEntries{};
std::uint64_t g_EntityDataClock = 0;
std::uint64_t g_EntityDataEpoch = 0;
std::uintptr_t g_EntityDataEpochRoom = 0;
std::uintptr_t g_EntityDataEpochList = 0;
bool g_EntityDataEpochValid = false;

std::uint64_t EntityDataEpochFor(std::uintptr_t room, std::uintptr_t listBegin) noexcept {
    if (!g_EntityDataEpochValid || room != g_EntityDataEpochRoom ||
        listBegin != g_EntityDataEpochList) {
        g_EntityDataEpochValid = true;
        g_EntityDataEpochRoom = room;
        g_EntityDataEpochList = listBegin;
        ++g_EntityDataEpoch;
    }
    return g_EntityDataEpoch;
}

// 同一实体（epoch + 指针 + 生成帧 全同）必须拿到**同一张**表；返回 1（表在栈顶）。
int PushEntityDataTable(lua_State* state, std::uintptr_t entity) {
    std::uint32_t spawnFrame = 0;
    if (!ReadEngine(entity + kEntitySpawnFrameOffset, &spawnFrame)) {
        spawnFrame = 0;
    }
    std::uintptr_t room = ResolveRoom();
    std::uintptr_t listBegin = 0;
    if (room != 0) {
        const std::uintptr_t list = room + kRoomEntityListOffset;
        EntityTableView view{};
        if (EntityListFingerprintMatches(list) && ReadEntityTable(list, kLiveEntityTable, &view)) {
            listBegin = view.begin;
        } else {
            // 容器不可信 → 房间身份也当成"未知"，退化成"按指针 + 生成帧"作键。
            room = 0;
        }
    }
    const std::uint64_t epoch = EntityDataEpochFor(room, listBegin);
    ++g_EntityDataClock;
    for (EntityDataEntry& entry : g_EntityDataEntries) {
        if (!entry.occupied || entry.epoch != epoch || entry.entity != entity ||
            entry.spawnFrame != spawnFrame) {
            continue;
        }
        entry.lastUsed = g_EntityDataClock;
        lua_rawgeti(state, LUA_REGISTRYINDEX, entry.reference);
        return 1;
    }
    lua_newtable(state);
    const int reference = luaL_ref(state, LUA_REGISTRYINDEX);
    EntityDataEntry* slot = nullptr;
    for (EntityDataEntry& entry : g_EntityDataEntries) {
        if (!entry.occupied) {
            slot = &entry;
            break;
        }
    }
    if (slot == nullptr) {
        slot = &g_EntityDataEntries[0];
        for (EntityDataEntry& entry : g_EntityDataEntries) {
            if (entry.lastUsed < slot->lastUsed) {
                slot = &entry;
            }
        }
        luaL_unref(state, LUA_REGISTRYINDEX, slot->reference);
    }
    slot->occupied = true;
    slot->epoch = epoch;
    slot->entity = entity;
    slot->spawnFrame = spawnFrame;
    slot->reference = reference;
    slot->lastUsed = g_EntityDataClock;
    lua_rawgeti(state, LUA_REGISTRYINDEX, reference);
    return 1;
}

void ResetEntityDataStore() noexcept {
    for (EntityDataEntry& entry : g_EntityDataEntries) {
        entry.occupied = false;
        entry.reference = LUA_NOREF;
    }
    g_EntityDataClock = 0;
    g_EntityDataEpoch = 0;
    g_EntityDataEpochRoom = 0;
    g_EntityDataEpochList = 0;
    g_EntityDataEpochValid = false;
}

// Entity 的整型字段（偏移来自 `Entity::Init` 的存值形态与真机探针）。
//
// `signed32` 为 true 的字段按**有符号**读：PC 的 `EntityPlayer.ControllerIndex` 文档是
// `-1 = 键盘/无手柄`，按无符号读会把它变成 4294967295。其余字段（`Type`/`Variant`/…）
// 在引擎里就是无符号枚举值，保持原样。
struct EntityIntegerField {
    const char* name;
    std::uintptr_t offset;
    bool signed32;
};

// 基类字段。`Index`（`+0x30`）批次 4 起在这里：`Room::AddEntity` 的
// `str w8,[x1,#0x30]`（存的就是 `Room+0x1948` 的房间实体表序号）是 `Entity.Index` 的硬证据。
// 注意它与 `kEntityPlayerIndexOffset`（`0x19F0`，"玩家在 `players` 向量里的下标"）**不是**
// 同一个量：后者不再作为任何 Lua 字段暴露（PC 里没有对应成员），常量保留供诊断使用。
constexpr EntityIntegerField kEntityIntegerFields[] = {
    {"Type", kEntityTypeOffset, false},
    {"Variant", kEntityVariantOffset, false},
    {"SubType", kEntitySubTypeOffset, false},
    {"Index", kEntityIndexOffset, false},
    // `InitSeed`：EID 把它当表键（`main.lua:206`/`264`），nil 会变成 `table index is nil`。
    {"InitSeed", kEntityInitSeedOffset, false},
    // `DropSeed`（2026-09-16）：EID 用它拼抓娃娃机的掉落判定键
    // （`main.lua:1173/1174` 的 `crane.InitSeed.."Drop"..crane.DropSeed`）。
    // 偏移是硬的（实体唯一的 RNG 成员、种子字，见 `kEntityDropSeedOffset` 的注释）；
    // **"Lua 名就叫 DropSeed"是推断**（PC 名字块里 DropSeed 紧挨 GetDropRNG，且 InitSeed 另有 0x3E8）。
    // 它只被当键用，取到的是"当前种子/状态字"（取过随机数会变），不会影响判定逻辑本身。
    {"DropSeed", kEntityDropSeedOffset, false},
};

// 只有 `Entity_Player` 有的整型字段。
//
// `ControllerIndex`（`0x19EC`，批次 3）：EID 在 `EID:setPlayer()`（`main.lua:1076`）里写
// `EID.controllerIndexes[p.ControllerIndex] = 1` —— nil 会直接触发 "table index is nil"。
// 偏移来源见 `runtime_constants.hpp`（`Entity_Player::SetControllerIndex` 的第一条存值指令）。
// 玩家专属的 **float** 字段表（2026-09-16）。用"名字 → 偏移"的静态表形态 —— 枚举工具
// （`tools/eid_api_gap_report.py`）靠这种形态认"已实现"，散装 strcmp 会让它继续报缺口。
struct EntityFloatField {
    const char* name;
    std::uintptr_t offset;
};
constexpr EntityFloatField kEntityPlayerFloatFields[] = {
    // 四个都是 `Entity_Player` 上的战斗属性，EID 只在"安慰奖"描述里用（见调用处注释）。
    {"Damage", kEntityPlayerDamageOffset},
    {"MoveSpeed", kEntityPlayerMoveSpeedOffset},
    {"MaxFireDelay", kEntityPlayerMaxFireDelayOffset},
    {"TearRange", kEntityPlayerTearRangeOffset},
};

constexpr EntityIntegerField kEntityPlayerIntegerFields[] = {
    {"PlayerType", kEntityPlayerTypeOffset, false},
    {"ControllerIndex", kEntityPlayerControllerIndexOffset, true},
    // `ControlsCooldown`（2026-09-16）：EID 读它判断"还能不能操作"，也会**写**它来抑制按键
    // （`eid_bagofcrafting.lua:877`、`eid_holdmapdesc.lua:669` 都是 `= 2`）。
    // 写入口见本单元的 `EntityNewIndex`（挂在 `EntityPlayer` 元表的 `__newindex` 上）。
    {"ControlsCooldown", kEntityPlayerControlsCooldownOffset, true},
};

// 字段名 → Lua 值。返回 false 表示"不是这个对象的字段"，让调用方继续找方法。
//
// 字段**不占 catalog 行**（与 `Vector` 的 `X`/`Y` 同例）：它们是 `__index` 里的名字，
// 没有独立的 `lua_CFunction`，Catalog 的门禁要求"登记的条目必须真的被注册"。
bool PushEntityField(lua_State* state, std::uintptr_t entity, const char* field, bool playerView) {
    for (const EntityIntegerField& candidate : kEntityIntegerFields) {
        if (std::strcmp(field, candidate.name) != 0) {
            continue;
        }
        if (candidate.signed32) {
            std::int32_t value = 0;
            if (!ReadEngine(entity + candidate.offset, &value)) {
                return false;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(value));
            return true;
        }
        std::uint32_t value = 0;
        if (!ReadEngine(entity + candidate.offset, &value)) {
            return false;
        }
        if (std::strcmp(candidate.name, "Type") == 0) {
            g_EntityTypeFieldReads.fetch_add(1, std::memory_order_relaxed);
            g_LastEntityTypeValue.store(value, std::memory_order_relaxed);
        } else if (std::strcmp(candidate.name, "Variant") == 0) {
            g_LastEntityVariantValue.store(value, std::memory_order_relaxed);
        } else if (std::strcmp(candidate.name, "SubType") == 0) {
            g_LastEntitySubTypeValue.store(value, std::memory_order_relaxed);
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    // `Player`（2026-09-16）：**只有跟班有**这个字段（PC 的 `EntityFamiliar.Player` = 主人）。
    // 判据与 `Entity:ToFamiliar()` 完全一致（`Type == ENTITY_FAMILIAR(3)` 且 vptr 精确等于
    // `base + kEntityFamiliarVtableOffset`）—— 不满足就返回 false，落到上层给 nil
    // （PC 里别的实体本来就没有这个成员，不能给它编一个）。
    // EID 的用法：`wisp:ToFamiliar().Player`（`eid_api.lua:3069`）。
    if (std::strcmp(field, "Player") == 0) {
        const std::uintptr_t base = EngineModuleBase();
        std::uintptr_t vtable = 0;
        std::uint32_t type = 0;
        if (base == 0 || base > UINTPTR_MAX - kEntityFamiliarVtableOffset ||
            !ReadEngine(entity, &vtable) || !ReadEngine(entity + kEntityTypeOffset, &type) ||
            type != kEntityTypeFamiliar || vtable != base + kEntityFamiliarVtableOffset) {
            return false;
        }
        std::uintptr_t owner = 0;
        if (!ReadEngine(entity + kEntityFamiliarPlayerOffset, &owner)) {
            return false;
        }
        if (owner == 0) {
            lua_pushnil(state);
            return true;
        }
        PushEntityHandle(state, owner,
                         IsEntityPlayer(owner) ? kEntityPlayerMetatable : kEntityMetatable);
        return true;
    }
    // `Parent`（2026-09-16）：指针字段，指向父实体（跟班的主段、多段实体的主段…）。
    // PC 里 `nil` 表示"没有父"，所以指针为 0 时必须给 nil —— 给一个"空实体句柄"会让
    // EID 的 `player.Parent == nil`（`main.lua:1089`）判错。
    if (std::strcmp(field, "Parent") == 0) {
        std::uintptr_t parent = 0;
        if (!ReadEngine(entity + kEntityParentOffset, &parent)) {
            return false;
        }
        if (parent == 0) {
            lua_pushnil(state);
            return true;
        }
        // 句柄要按**它自己**的类型给元表（父实体可能是玩家、跟班或任意实体），
        // 否则 `parent.Luck` 这类玩家字段会在错的元表上查不到。
        PushEntityHandle(state, parent,
                         IsEntityPlayer(parent) ? kEntityPlayerMetatable : kEntityMetatable);
        return true;
    }
    // `PositionOffset`（2026-09-16）：`Vector2`（两个 float）。EID 用它修正实体在屏幕上的位置
    // （`main.lua:924`）。读不到就返回 false ⇒ 上层给 nil（不编一个 0 向量）。
    if (std::strcmp(field, "PositionOffset") == 0) {
        float x = 0.0F;
        float y = 0.0F;
        if (!ReadEngine(entity + kEntityPositionOffsetOffset, &x) ||
            !ReadEngine(entity + kEntityPositionOffsetOffset + sizeof(float), &y)) {
            return false;
        }
        // `Vector` userdata（PC 侧这个字段就是 `Vector`，不是一张表）。
        isaac::runtime::PushLuaVector(state, x, y);
        return true;
    }
    if (playerView) {
        // `player.Luck`（2026-09-14）：EID 的幸运值公式在**逐帧渲染路径**上读它
        // （`features/eid_data.lua` 里 48 条 `EID.LuckFormulas[...](player.Luck)`）。
        // 此前这个字段缺失 ⇒ Lua 侧拿到 `nil` ⇒ 算术抛错 ⇒ 那条渲染回调当场中断 ⇒
        // **该帧那片道具的描述全部不画**。真机症状：某些底座道具"既没文字也没问号"
        // （问号只用于未解锁的道具），而多数道具正常——正是"某一条公式抛错、同帧后面全丢"。
        // 出错现场：`eid_data.lua:1215: attempt to perform arithmetic on a nil value (local 'luck')`。
        // 引擎字段是 32 位整数（偏移来源见 `runtime_constants.hpp` 的
        // `kEntityPlayerLuckOffset`），PC 侧 `Luck` 是浮点，所以这里转成 Lua number 交付。
        if (std::strcmp(field, "Luck") == 0) {
            std::int32_t luck = 0;
            if (!ReadEngine(entity + kEntityPlayerLuckOffset, &luck)) {
                return false;
            }
            lua_pushnumber(state, static_cast<lua_Number>(luck));
            return true;
        }
        // 四个战斗属性（2026-09-16）：全部是 float（偏移与证据见 `runtime_constants.hpp`）。
        // EID 只用它们做"安慰奖（Consolation Prize 644）"描述里的档位换算
        // （`features/eid_modifiers.lua:461-464`），缺一个就是 `nil^0.56` 那种算术抛错。
        for (const EntityFloatField& candidate : kEntityPlayerFloatFields) {
            if (std::strcmp(field, candidate.name) != 0) {
                continue;
            }
            float value = 0.0F;
            if (!ReadEngine(entity + candidate.offset, &value)) {
                return false;
            }
            lua_pushnumber(state, static_cast<lua_Number>(value));
            return true;
        }
        // `CanFly`（1 字节布尔，2026-09-16）：EID 用它决定要不要禁用"障碍物提示"（`main.lua:1193`）。
        if (std::strcmp(field, "CanFly") == 0) {
            std::uint8_t value = 0;
            if (!ReadEngine(entity + kEntityPlayerCanFlyOffset, &value)) {
                return false;
            }
            lua_pushboolean(state, value != 0 ? 1 : 0);
            return true;
        }
        for (const EntityIntegerField& candidate : kEntityPlayerIntegerFields) {
            if (std::strcmp(field, candidate.name) != 0) {
                continue;
            }
            if (candidate.signed32) {
                std::int32_t value = 0;
                if (!ReadEngine(entity + candidate.offset, &value)) {
                    return false;
                }
                lua_pushinteger(state, static_cast<lua_Integer>(value));
                return true;
            }
            std::uint32_t value = 0;
            if (!ReadEngine(entity + candidate.offset, &value)) {
                return false;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(value));
            return true;
        }
        // `player.QueuedItem`（批次 6，2026-09-12）：EID 在**逐帧**路径上读它 14 次
        // （`features/eid_api.lua:2625/2627/2631/...`、`main.lua` 多处）：
        //   `if player.QueuedItem then ... player.QueuedItem.Item ... end`
        // 我们的 `__index` 对未知字段返回 nil，而 `if nil then` 是安全的 —— **但**
        // `EID:evaluateQueuedItems` 之后还有把 `QueuedItem` 交给别处的路径（`eid_api.lua:2632`
        // 的 `player.QueuedItem.Item` 已在 `if` 里），所以返回 nil 目前也能活。
        //
        // 这里仍然返回**一张只读表**而不是 nil，理由是语义：`player.QueuedItem` 在 PC 上
        // 永远是一张表（字段 `Item`/`Charge` 等），返回表能让 Mod 的 `~= nil` 判断、`pairs`
        // 遍历与索引都不出错；表本身是空的（等于"没有排队道具"），
        // `player.QueuedItem.Item` 得到 nil —— 与"没拿在手上"的 PC 形态一致。
        if (std::strcmp(field, "QueuedItem") == 0) {
            lua_newtable(state);
            return true;
        }
    }
    if (std::strcmp(field, "Size") == 0) {
        float radius = 0.0F;
        if (!ReadEngine(entity + kEntitySizeOffset, &radius)) {
            return false;
        }
        lua_pushnumber(state, static_cast<lua_Number>(radius));
        return true;
    }
    if (std::strcmp(field, "FrameCount") == 0) {
        // `Entity::GetFrameCount()`（`0x5A210`）= `[Game + 0x24F99C] - [this + 0x2F4]`，
        // 两个 u32 相减（故意保留无符号回绕，与引擎逐位一致）。
        std::uint32_t now = 0;
        std::uint32_t spawn = 0;
        const std::uintptr_t game = ResolveGameAddress();
        // 探针（2026-09-12 第八轮）：EID 的 `main.lua:1473` 判 `entity.FrameCount > 0`，
        // 而过滤器链掩码显示它**从不**走到后面的 `EID:getEntityData` —— 只有 `FrameCount`
        // 恒 0 才会这样。这里把三个原始读数记下来（引擎帧计数 / 实体出生帧 / 差值），
        // 是"偏移不对"还是"Game 指针不对"就能一眼分开。
        g_FrameCountProbeReads.fetch_add(1, std::memory_order_relaxed);
        g_LastGameFrameCount.store(now, std::memory_order_relaxed);
        g_LastEntitySpawnFrame.store(spawn, std::memory_order_relaxed);
        if (game == 0 || !ReadEngine(game + kGameFrameCountOffset, &now) ||
            !ReadEngine(entity + kEntitySpawnFrameOffset, &spawn)) {
            // **恒返回数字**：EID 直接拿它做比较（`main.lua:1473` 的 `entity.FrameCount > 0`），
            // nil 会变成 "attempt to compare nil with number" 而打断整段渲染。
            PushZero(state);
            return true;
        }
        g_LastGameFrameCount.store(now, std::memory_order_relaxed);
        g_LastEntitySpawnFrame.store(spawn, std::memory_order_relaxed);
        g_LastFrameCountResult.store(now - spawn, std::memory_order_relaxed);
        if (now > spawn) {
            g_FrameCountPositive.fetch_add(1, std::memory_order_relaxed);
        }
        lua_pushinteger(state, static_cast<lua_Integer>(now - spawn));
        return true;
    }
    if (std::strcmp(field, "Position") == 0) {
        float x = 0.0F;
        float y = 0.0F;
        if (!ReadEngine(entity + kEntityPositionOffset, &x) ||
            !ReadEngine(entity + kEntityPositionOffset + sizeof(float), &y)) {
            return false;
        }
        // 返回**新的** `Vector` userdata：把同一块 userdata 交出去（别名）会让 Mod 的
        // `player.Position.X = 1` 之类写法改到别人的值上；`Isaac.WorldToScreen` 同理。
        auto* vector = static_cast<VectorHandle*>(lua_newuserdata(state, sizeof(VectorHandle)));
        vector->x = x;
        vector->y = y;
        luaL_getmetatable(state, kVectorMetatable);
        lua_setmetatable(state, -2);
        return true;
    }
    return false;
}

// `EntityPickup` 的字段（偏移出处见 `runtime_constants.hpp` 的"批次 4"段）。
//
// 每次访问都**重做**"这真是一张 `Entity_Pickup`"的校验（`Type == 5` **且** vptr ==
// `base + 0xA36E28`）：`Entity_Pickup` 的 vtable 是唯一的，这两条同时成立才读它的字段。
//
// ★ `Touched`（`+0x560`）是**猜测**：`docs/问题与解决记录.md` 的"语义未定项"里明确写着
// "`Entity_Pickup+0x560` 是否为 `Touched` 待真机确认"。PC 的返回类型是 boolean，所以这里按
// u8 != 0 转成布尔；它是本批次唯一一条"没有铁证"的字段。
bool PushEntityPickupField(lua_State* state, std::uintptr_t entity, const char* field) {
    const std::uintptr_t base = EngineModuleBase();
    if (entity == 0 || base == 0 || base > UINTPTR_MAX - kEntityPickupVtableOffset) {
        return false;
    }
    std::uintptr_t vtable = 0;
    std::uint32_t type = 0;
    if (!ReadEngine(entity, &vtable) || !ReadEngine(entity + kEntityTypeOffset, &type) ||
        type != kEntityTypePickup || vtable != base + kEntityPickupVtableOffset) {
        return false;
    }
    if (std::strcmp(field, "Price") == 0 || std::strcmp(field, "ShopItemId") == 0 ||
        std::strcmp(field, "OptionsPickupIndex") == 0) {
        const std::uintptr_t offset = std::strcmp(field, "Price") == 0
                                          ? kEntityPickupPriceOffset
                                          : (std::strcmp(field, "ShopItemId") == 0
                                                 ? kEntityPickupShopItemIdOffset
                                                 : kEntityPickupOptionsIndexOffset);
        // 三个字段在引擎里都是 `int`；`ShopItemId` 的文档明确提到会用**负数**（-1）修 D6 重投，
        // 所以按有符号读。
        std::int32_t value = 0;
        if (!ReadEngine(entity + offset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "ForceBlind") == 0 || std::strcmp(field, "Touched") == 0) {
        const std::uintptr_t offset = std::strcmp(field, "ForceBlind") == 0
                                          ? kEntityPickupForceBlindOffset
                                          : kEntityPickupTouchedOffset;
        std::uint8_t value = 0;
        if (!ReadEngine(entity + offset, &value)) {
            return false;
        }
        // 探针：`Touched` 是"待真机确认"的偏移，而 EID 拿它当"摘掉隐瞒"的条件。
        // `ForceBlind` 不记（它不是本轮的问题点），只记 `Touched`。
        if (std::strcmp(field, "Touched") == 0) {
            g_EntityPickupTouchedReads.fetch_add(1, std::memory_order_relaxed);
            if (value != 0) {
                g_EntityPickupTouchedTrue.fetch_add(1, std::memory_order_relaxed);
            }
            g_LastEntityPickupTouchedRaw.store(value, std::memory_order_relaxed);
        }
        lua_pushboolean(state, value != 0 ? 1 : 0);
        return true;
    }
    return false;
}

// 方法表查找：先本类型的 `__methods`，`EntityPlayer` 再回落到 `Entity` 的（PC 里就是继承）。
bool PushMethodFrom(lua_State* state, const char* metatable, const char* field) {
    if (luaL_getmetatable(state, metatable) != LUA_TTABLE) {
        lua_pop(state, 1);
        return false;
    }
    lua_getfield(state, -1, "__methods");
    lua_remove(state, -2);
    if (!lua_istable(state, -1)) {
        lua_pop(state, 1);
        return false;
    }
    lua_getfield(state, -1, field);
    lua_remove(state, -2);
    if (lua_isnil(state, -1)) {
        lua_pop(state, 1);
        return false;
    }
    return true;
}

// 视图身份：决定用哪套字段与方法表（`EntityPlayer`/`EntityPickup` 都回落到 `Entity` 的方法表，
// 与 PC 的继承关系一致），以及用哪一级句柄校验。
enum class EntityView : std::uint8_t { Base, Player, Pickup };

const char* EntityViewMetatable(EntityView view) noexcept {
    if (view == EntityView::Player) {
        return kEntityPlayerMetatable;
    }
    return view == EntityView::Pickup ? kEntityPickupMetatable : kEntityMetatable;
}

int IndexEntity(lua_State* state, EntityView view) {
    const char* metatable = EntityViewMetatable(view);
    auto* handle = static_cast<EntityHandle*>(luaL_checkudata(state, 1, metatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    // 校验失败（伪造/过期句柄）时字段与方法都不给：返回 nil 比报错更接近 PC 的"空对象"，
    // 也不会让一个已经失效的引用把 Mod 直接打断。
    //
    // `EntityPlayer` 视图要求**精确**的 `Entity_Player` vptr（玩家专属字段/方法都在它上面），
    // `Entity`/`EntityPickup` 用 `Entity` 家族的 vtable 区间。
    const std::uintptr_t entity =
        view == EntityView::Player ? ValidatedEntityPlayer(handle) : ValidatedEntity(handle);
    if (entity != 0) {
        bool pushed = false;
        if (view == EntityView::Pickup) {
            // `EntityPickup` 自己的字段优先，其次回落到 `Entity` 的（PC 里它就是 `Entity` 的子类）。
            pushed = PushEntityPickupField(state, entity, field) ||
                     PushEntityField(state, entity, field, false);
        } else {
            pushed = PushEntityField(state, entity, field, view == EntityView::Player);
        }
        if (pushed) {
            return 1;
        }
    }
    g_EntityStringFieldReads.fetch_add(1, std::memory_order_relaxed);
    {
        std::uint64_t head = 0;
        for (std::size_t index = 0; index < 8 && field[index] != '\0'; ++index) {
            head |= static_cast<std::uint64_t>(static_cast<unsigned char>(field[index]))
                    << (8 * index);
        }
        g_LastEntityStringFieldHead.store(head, std::memory_order_relaxed);
    }
    if (PushMethodFrom(state, metatable, field)) {
        return 1;
    }
    if (view != EntityView::Base && PushMethodFrom(state, kEntityMetatable, field)) {
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

int EntityIndex(lua_State* state) {
    return IndexEntity(state, EntityView::Base);
}

int EntityPlayerIndex(lua_State* state) {
    return IndexEntity(state, EntityView::Player);
}

int EntityPickupIndex(lua_State* state) {
    return IndexEntity(state, EntityView::Pickup);
}

// --- 引擎字段**写**入口（2026-09-16，写通道第一批）----------------------------
//
// 为什么需要它：PC 的 `EntityPlayer.ControlsCooldown` 是**可写**成员，EID 用它"吃掉"随后的按键
// ——`features/eid_bagofcrafting.lua:877` 与 `features/eid_holdmapdesc.lua:669` 都是
// `player.ControlsCooldown = 2`（意思是"接下来两帧不要吃输入"，用于背包/按住地图的界面）。
// 我们的实体是 userdata、元表里没有 `__newindex` 时，这两处赋值会直接抛
// "attempt to index a userdata value" ⇒ 那两条回调每帧报错、被派发器摘除。
//
// 口径（与 `Sprite`/`KColor` 的 `__newindex` 一致）：
//   * **只有 `EntityPlayer` 视图能写** —— 这一批只有玩家字段可写；
//   * **只认 `ControlsCooldown`**（偏移与证据见 `runtime_constants.hpp` 的
//     `kEntityPlayerControlsCooldownOffset`：`Entity_Player` 自己的 tick 读它做递减），
//     别的字段名一律报错，避免把拼错的字段名静默吞掉；
//   * 写入经 `WriteEngine<std::int32_t>`（先确认这一页**可写**再写）。这一批**不**静默失败：
//     实体地址刚刚才通过 vptr + 可读性校验，写不进去说明可写性判断本身出了问题，
//     报错比"EID 的抑制输入永远不生效却没人知道"更好排查。
int EntityNewIndex(lua_State* state) {
    auto* handle = static_cast<EntityHandle*>(luaL_checkudata(state, 1, kEntityPlayerMetatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        return luaL_error(state, "EntityPlayer fields are written by name");
    }
    const char* field = lua_tostring(state, 2);
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity == 0) {
        // 过期/伪造句柄：与读路径同一口径 —— 不报错、也不写（写一个失效地址才是真危险）。
        return 0;
    }
    if (std::strcmp(field, "ControlsCooldown") == 0) {
        const lua_Integer value = luaL_checkinteger(state, 3);
        const std::int32_t stored = static_cast<std::int32_t>(value);
        if (!WriteEngine<std::int32_t>(entity + kEntityPlayerControlsCooldownOffset, stored)) {
            return luaL_error(state, "EntityPlayer.ControlsCooldown is not writable on this object");
        }
        return 0;
    }
    return luaL_error(state, "EntityPlayer fields are read-only except ControlsCooldown");
}

// --- Entity / EntityPlayer 方法 ----------------------------------------------

int EntityToPlayer(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Entity:ToPlayer accepts no arguments");
    }
    // PC 文档（`Entity.md:631`）："If the conversion is not successful, this function returns
    // `nil`"。批次 2 时只会交付出 `EntityPlayer`，所以无条件返回自身；批次 4 起
    // `Isaac.FindInRadius`/`FindByType` 会给出任意实体，这里必须真的判类型。
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity == 0) {
        // 不是 `Entity_Player`（含过期句柄）→ nil。
        PushNil(state);
        return 1;
    }
    if (luaL_testudata(state, 1, kEntityPlayerMetatable) != nullptr) {
        // 已经拿着 `EntityPlayer` 视图：PC 的 `EntityPlayer:ToPlayer()` 返回的**就是自己**，
        // 身份相等（`player:ToPlayer() == player`）必须成立。
        lua_pushvalue(state, 1);
        return 1;
    }
    // 拿着 `Entity` 视图但底层确实是玩家：换成 `EntityPlayer` 视图（PC 返回的是同一个对象，
    // 只是类型是 `EntityPlayer`）。
    PushEntityHandle(state, entity, kEntityPlayerMetatable);
    return 1;
}

// `Entity:ToPickup()`（批次 4）：PC 文档（`Entity.md:635`）"Used to cast an Entity object to an
// EntityPickup object... If the conversion is not successful, this function returns nil"。
// 判据两条同时成立：`Type == 5`（ENTITY_PICKUP）**且** vptr == `base + 0xA36E28`
// （`Entity_Pickup` 的 vtable 唯一，`docs/问题与解决记录.md` 批次 4 的小偏移表）。
int EntityToPickup(lua_State* state) {
    RecordFilterChain(2U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Entity:ToPickup accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntity(handle);
    const std::uintptr_t base = EngineModuleBase();
    std::uintptr_t vtable = 0;
    std::uint32_t type = 0;
    if (entity == 0 || base == 0 || base > UINTPTR_MAX - kEntityPickupVtableOffset ||
        !ReadEngine(entity, &vtable) || !ReadEngine(entity + kEntityTypeOffset, &type) ||
        type != kEntityTypePickup || vtable != base + kEntityPickupVtableOffset) {
        PushNil(state);
        return 1;
    }
    PushEntityHandle(state, entity, kEntityPickupMetatable);
    return 1;
}

// `EntityPickup:IsShopItem()`（`EntityPickup.md:35`）：**安全 stub，恒 false**。
//
// 为什么不做真实现：判"这个掉落物是不是商店商品"需要一个**证据支持的判据**，而我们只有
// `ShopItemId`（`+0x56C`）这一个候选字段，它的文档语义是"商店商品列表里的下标"
// （`EntityPickup.md:93` 明说"新生成的收藏品默认 `ShopItemId == 0`"，而且**负数**被当作
// "随便给一个不参与重投的 id"用）—— 也就是说 `0` 既可能是"不是商店商品"也可能是"列表第 0 项"，
// 光靠它判会把正常商品判错。引擎自己的 `EntityPickup::IsShopItem()` 用的是哪一位/哪个字段
// 本轮**没有定位**。
//
// 为什么必须有这个名字：EID 在**卡片/药丸描述**路径上无条件调用它
// （`main.lua:1611`/`1636`：`closest:ToPickup():IsShopItem() and (...)`，`main.lua:926` 的指示箭头
// 路径、`features/eid_bagofcrafting.lua:935` 同理）。返回 nil 方法会让那一句变成
// "attempt to call a nil value (method 'IsShopItem')"，整段描述渲染直接中断 —— 而这正是
// 本批次要打通的那条链。`false` 的降级后果只是"不按商店规则隐藏描述"（一个配置项失效），
// 比报错安全得多，也不影响任何其他判据。
int EntityPickupIsShopItem(lua_State* state) {
    RecordFilterChain(4U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPickup:IsShopItem accepts no arguments");
    }
    // 仍然走一次句柄校验（`CheckEntityHandle` 已经保证这是 `EntityPickup` 视图；这里再确认
    // 底层指针仍然是一张有效实体），但返回值**恒为 false**：见上面的注释 —— 我们没有能判定
    // "商店商品"的证据，而 PC 的返回类型是 boolean，false 是"不隐藏描述"这个安全分支。
    static_cast<void>(ValidatedEntity(handle));
    lua_pushboolean(state, 0);
    return 1;
}

// `Entity:GetData()`（批次 4）：引擎**没有**这个字段（三条独立否定证据见
// `docs/问题与解决记录.md`），所以由 Runtime 自管一张"与实体同生命周期"的 Lua 表。
// 同一实体（房间 epoch + 指针 + 生成帧 全同）**每次都拿到同一张表**，也就是说
// `entity:GetData()[k] = v` 写进去的东西下次读还在 —— 这正是 EID 的用法
// （`features/eid_api.lua:2219`/`2229`）。
//
// 与 PC 的差异写在 `PushEntityDataTable` 的注释里（换房间会让旧的表失效）。
// `Entity:ToFamiliar()`（批次 9）：PC 在**基类** `Entity` 上提供它，跟班返回 `EntityFamiliar`、
// 其它实体返回 nil。EID 用它把跟班从候选实体里挑出来。
//
// 判据与 `Entity:ToPickup()` 同一形态（两条都要求 vptr **精确**相等，避免把别的派生类当跟班）：
//   * `Type == ENTITY_FAMILIAR(3)`；
//   * `*(u64*)entity == base + kEntityFamiliarVtableOffset`。
// vtable 偏移的证据：符号表里 `_ZTVN15IsaacRepentance15Entity_FamiliarE` @ `0xA35718`，
// 加 `0x10`（跳过 offset-to-top 与 typeinfo 两个表头字）⇒ `0xA35728`；同一条规则在既有的
// `Entity_Player`（符号 `0xA37100` → 常量 `0xA37110`）与 `Entity_Pickup`（`0xA36E18` → `0xA36E28`）
// 上已经对过两次，互为交叉验证。
//
// 视图用 `Entity` 基类元表：我们目前没有 `EntityFamiliar` 专属元表，而 PC 里跟班的方法绝大部分
// 继承自 `Entity`；返回 nil 会让 EID 的筛选取不到跟班，返回基类视图至少语义正确。
int EntityToFamiliar(lua_State* state) {
    RecordFilterChain(6U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Entity:ToFamiliar accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntity(handle);
    const std::uintptr_t base = EngineModuleBase();
    std::uintptr_t vtable = 0;
    std::uint32_t type = 0;
    if (entity == 0 || base == 0 || base > UINTPTR_MAX - kEntityFamiliarVtableOffset ||
        !ReadEngine(entity, &vtable) || !ReadEngine(entity + kEntityTypeOffset, &type) ||
        type != kEntityTypeFamiliar || vtable != base + kEntityFamiliarVtableOffset) {
        PushNil(state);
        return 1;
    }
    PushEntityHandle(state, entity, kEntityMetatable);
    return 1;
}

int EntityGetData(lua_State* state) {
    RecordFilterChain(0U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Entity:GetData accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntity(handle);
    if (entity == 0) {
        // 过期/伪造句柄 → nil（EID 的判据就是 `entity:GetData() ~= nil`，见 `eid_api.lua:2218`）。
        PushNil(state);
        return 1;
    }
    return PushEntityDataTable(state, entity);
}

// `Entity:GetSprite()`（`Entity.md`）：PC 的 `Entity` 有一个 `Sprite` 成员，Lua 拿到的是它的
// 只读视图。Switch 版**没有** `GetSprite` 访问器符号 —— 因为这个成员是**内嵌对象**，访问器
// 会被内联成一句 `this + 偏移`（`docs/PC-Lua-API-对照清单.md:1449` 早就把这条判据写明了）。
//
// 偏移的定案证据在 `runtime_constants.hpp`（`kEntitySpriteOffset`）：`Entity::Entity()`
// 构造的就是 `this+0x50` 上的 `ANM2`，`Entity::Render()` 渲染的也是它，`Entity_Pickup::
// ReloadGraphics()` 给它换 spritesheet。
//
// 为什么必须有它：EID 的 `EID:IsAltChoice()`（`main.lua:216`）在**宝藏房**里对未拾取的底座调
// `pickup:GetSprite()`；缺了它就是 "attempt to call a nil value (method 'GetSprite')"，而那一句
// 在 `EID:OnRender()`（`main.lua:1312` 注册的 `MC_POST_RENDER`）里、整段没有 pcall，
// 于是**整个房间的描述渲染**都被打断。
//
// 所有权：交出去的 `Sprite` 句柄指向**引擎实体内部**的内存，所以它带
// `kSpriteSourceEngineEntity` 标记，`__gc` 不会析构/释放（见 `sprite_api.cpp`）；每次访问都会
// 重新校验这张实体还活着。
//
// 降级：拿不到实体（过期句柄 / 基址未发布）→ 返回 **nil**，不报错（与 `Entity:GetData()` 同一
// 口径）：报错会把整段描述渲染打断，而 nil 至少让 Mod 的 `if sprite then` 分支走另一条路。
int EntityGetSprite(lua_State* state) {
    RecordFilterChain(1U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Entity:GetSprite accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntity(handle);
    if (entity == 0) {
        PushNil(state);
        return 1;
    }
    return PushEngineEntitySpriteHandle(state, reinterpret_cast<void*>(entity));
}

// `Entity_Player::HasCollectible(eCollectibleType, bool)`：模块内固定偏移，先确认那 4 字节可读，
// 读不到就说明基址不对，绝不能盲跳。ABI 与其它引擎方法一致（`x0 = this`、`w1 = id`、`w2 = bool`）。
std::uintptr_t HasCollectibleMethod() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kEntityPlayerHasCollectibleOffset) {
        return 0;
    }
    const std::uintptr_t method = base + kEntityPlayerHasCollectibleOffset;
    return IsEngineMemoryReadable(method, sizeof(std::uint32_t)) ? method : 0;
}

// `Entity_Player::AddCollectible` 的地址：与 `HasCollectibleMethod` 同一种接法（模块基址 + 偏移），
// 但**多做一次入口指纹核对** —— 这个函数的调用会真的改变玩家状态，偏移一旦写错就会去调别的函数，
// 后果比"读到一个错值"严重得多；核对 4 字节的成本可以忽略。
std::uintptr_t AddCollectibleMethod() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kEntityPlayerAddCollectibleOffset) {
        return 0;
    }
    const std::uintptr_t method = base + kEntityPlayerAddCollectibleOffset;
    if (!IsEngineMemoryReadable(method, sizeof(std::uint32_t))) {
        return 0;
    }
    std::uint32_t entryWord = 0;
    if (!ReadEngine(method, &entryWord) ||
        entryWord != kEntityPlayerAddCollectibleEntryWord) {
        return 0;
    }
    return method;
}

// `ItemConfig::Item::IsAvailable(long, uint)` 的地址：模块基址 + 偏移，**调用前核对入口 4 字节**。
// 这条会真的去调引擎函数（`flags`/`this` 由我们给），偏移写错就会去调别的函数 ⇒ 指纹不是可选项。
std::uintptr_t ItemIsAvailableMethod() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kItemConfigItemIsAvailableOffset) {
        return 0;
    }
    const std::uintptr_t method = base + kItemConfigItemIsAvailableOffset;
    std::uint32_t entryWord = 0;
    if (!IsEngineMemoryReadable(method, sizeof(std::uint32_t)) ||
        !ReadEngine(method, &entryWord) || entryWord != kItemConfigItemIsAvailableEntryWord) {
        return 0;
    }
    return method;
}

std::uintptr_t CardIsAvailableMethod() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kItemConfigCardIsAvailableOffset) {
        return 0;
    }
    const std::uintptr_t method = base + kItemConfigCardIsAvailableOffset;
    std::uint32_t entryWord = 0;
    if (!IsEngineMemoryReadable(method, sizeof(std::uint32_t)) ||
        !ReadEngine(method, &entryWord) || entryWord != kItemConfigCardIsAvailableEntryWord) {
        return 0;
    }
    return method;
}

std::uintptr_t PillEffectIsAvailableMethod() noexcept {
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kItemConfigPillEffectIsAvailableOffset) {
        return 0;
    }
    const std::uintptr_t method = base + kItemConfigPillEffectIsAvailableOffset;
    std::uint32_t entryWord = 0;
    if (!IsEngineMemoryReadable(method, sizeof(std::uint32_t)) ||
        !ReadEngine(method, &entryWord) || entryWord != kItemConfigPillEffectIsAvailableEntryWord) {
        return 0;
    }
    return method;
}

// `kind` → 该调哪个引擎函数（0 = `Item`（带 flags）、1 = `Card`、2 = `PillEffect`）。
// （宿主实现指针必须定义在**使用它的 `CallItemConfigIsAvailable` 之前** —— 上面那次"定义放在
//  文件末尾、使用在前面"的直接后果就是 `use of undeclared identifier`。）
#if !defined(__SWITCH__)
ItemConfigIsAvailableHostImplementation g_ItemConfigIsAvailableHostImplementation = nullptr;
#endif

// `kind` → 该调哪个引擎函数（0 = `Item`（带 flags）、1 = `Card`、2 = `PillEffect`）。
bool CallItemConfigIsAvailable(std::uintptr_t method, std::uintptr_t entry, int kind) {
#if defined(__SWITCH__)
    if (kind == 0) {
        // `Item::IsAvailable(long flags, uint)`：x0 = 条目、x1 = flags（兼容层定义、已登记偏离）、
        // x2 = 0（反汇编里读到的四个分支都没用到第三个参数）。
        using ItemFn = bool (*)(void*, std::int64_t, std::uint32_t);
        return reinterpret_cast<ItemFn>(method)(reinterpret_cast<void*>(entry),
                                                kItemIsAvailableFlags, 0);
    }
    using NoArgFn = bool (*)(void*);
    return reinterpret_cast<NoArgFn>(method)(reinterpret_cast<void*>(entry));
#else
    static_cast<void>(method);
    const ItemConfigIsAvailableHostImplementation injected =
        g_ItemConfigIsAvailableHostImplementation;
    return injected != nullptr &&
           injected(kind, reinterpret_cast<void*>(entry), kind == 0 ? kItemIsAvailableFlags : 0);
#endif
}


#if !defined(__SWITCH__)
EntityPlayerHasCollectibleHostImplementation g_HasCollectibleHostImplementation = nullptr;
CurrentLanguageCodeHostImplementation g_CurrentLanguageCodeHostImplementation = nullptr;
// `g_ItemConfigIsAvailableHostImplementation` 定义在它的使用点之前（见 `ItemIsAvailableMethod` 之后）。
#endif

bool CallEntityPlayerHasCollectible(std::uintptr_t method, std::uintptr_t entity,
                                    std::uint32_t collectibleId) {
#if defined(__SWITCH__)
    using HasCollectibleFn = bool (*)(void*, std::uint32_t, std::uint32_t);
    return reinterpret_cast<HasCollectibleFn>(method)(reinterpret_cast<void*>(entity), collectibleId,
                                                      0);
#else
    // 宿主构建没有引擎映像，也无法把一个 C++ 函数放到 `base + 0x27D3F4`，所以那一条 `bl` 由
    // 测试注入的实现顶替（声明见 `isaac_api.hpp`）。宿主仍然能验证"vptr 校验先于调用""参数就是
    // `this` 与 id""返回值原样变成 Lua 布尔"；设备构建不存在这条通路。
    static_cast<void>(method);
    const EntityPlayerHasCollectibleHostImplementation injected = g_HasCollectibleHostImplementation;
    return injected != nullptr && injected(reinterpret_cast<void*>(entity), collectibleId);
#endif
}

// 拿不到实体或拿不到方法时返回 false（PC 的返回值是布尔，`if player:HasCollectible(x) then`
// 在 Mod 里到处都是，报错会直接打断调用方；"没有这一件"是最安全的降级）。
int EntityPlayerAddCollectible(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 6 || !lua_isinteger(state, 2)) {
        return luaL_error(
            state,
            "EntityPlayer:AddCollectible accepts (type[, charge[, force[, slot[, varData]]]])");
    }
    const lua_Integer type = lua_tointegerx(state, 2, nullptr);
    if (type < 0 || static_cast<std::uint64_t>(type) > std::numeric_limits<std::uint32_t>::max()) {
        return luaL_error(state, "EntityPlayer:AddCollectible type is outside uint32 range");
    }
    const lua_Integer charge = argumentCount >= 3 ? lua_tointegerx(state, 3, nullptr) : 0;
    const bool force = argumentCount >= 4 && lua_toboolean(state, 4) != 0;
    // 默认槽位必须是 `ActiveSlot.SLOT_PRIMARY = 0`（PC 文档的默认值）。2026-09-14 真机定位到：
    // 这里原先是 `-1`，于是 `player:AddCollectible(628, 0, true)`（不写 slot）会把**主动道具**
    // 放进一个不存在的槽位 —— 引擎仍把它记进"拥有列表"（`HasCollectible` 变 true），但**不装备**，
    // 玩家在主动道具槽里看不到、也用不了。表现极具迷惑性：既不报错、也不是没生效。
    const lua_Integer slot = argumentCount >= 5 ? lua_tointegerx(state, 5, nullptr) : 0;
    const lua_Integer varData = argumentCount >= 6 ? lua_tointegerx(state, 6, nullptr) : 0;
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    const std::uintptr_t method = AddCollectibleMethod();
    if (entity == 0 || method == 0) {
        // 实例或能力不可用时**保守返回 false**（与同族接口同一口径）：报错会摘掉整条回调。
        lua_pushboolean(state, 0);
        return 1;
    }
    using AddCollectibleFn = std::uint32_t (*)(std::uintptr_t, std::uint32_t, std::uint32_t,
                                               std::uint32_t, std::uint32_t, std::uint32_t);
    reinterpret_cast<AddCollectibleFn>(method)(
        entity, static_cast<std::uint32_t>(type), static_cast<std::uint32_t>(charge),
        force ? 1U : 0U, static_cast<std::uint32_t>(slot),
        static_cast<std::uint32_t>(varData));
    // 返回值取"**实际结果**"：调用后用 `HasCollectible`（同族、已实现的接口）复核一次。
    // 引擎那一层的返回类型没有从反汇编确认过，与其信寄存器里的值，不如用效果说话 ——
    // 参数顺序万一写错，这里会如实暴露成 false。
    const std::uintptr_t hasCollectible = HasCollectibleMethod();
    bool collected = false;
    if (hasCollectible != 0) {
        using HasCollectibleFn = std::uint32_t (*)(std::uintptr_t, std::uint32_t, std::uint32_t);
        collected =
            reinterpret_cast<HasCollectibleFn>(hasCollectible)(
                entity, static_cast<std::uint32_t>(type), 0U) != 0;
    }
    lua_pushboolean(state, collected ? 1 : 0);
    return 1;
}

int EntityPlayerHasCollectible(lua_State* state) {
    RecordApiSequenceSecondary(0U);
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 2 || !lua_isinteger(state, 2)) {
        return luaL_error(state, "EntityPlayer:HasCollectible accepts one collectible id");
    }
    const lua_Integer requested = lua_tointegerx(state, 2, nullptr);
    if (requested < 0 ||
        static_cast<std::uint64_t>(requested) > std::numeric_limits<std::uint32_t>::max()) {
        return luaL_error(state, "EntityPlayer:HasCollectible collectible id is outside uint32 range");
    }
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    const std::uintptr_t method = HasCollectibleMethod();
    if (entity == 0 || method == 0) {
        lua_pushboolean(state, 0);
        return 1;
    }
    const bool collected = CallEntityPlayerHasCollectible(
        method, entity, static_cast<std::uint32_t>(requested));
    lua_pushboolean(state, collected ? 1 : 0);
    return 1;
}


// `EntityPlayer:GetData()`：批次 2 起它返回 nil（"ModData 结构还没有证据"），**批次 4 起
// 返回 Runtime 自管的那张表** —— EID 真的往它里面写（`features/eid_api.lua:2229`：
// `entity:GetData()[str] = value`），返回 nil 会让 EID 的实体数据通路整条失效。
// 同一个实体每次都拿到**同一张**表；与 PC 的差异见 `PushEntityDataTable` 的注释。
int EntityPlayerGetData(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPlayer:GetData accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity == 0) {
        PushNil(state);
        return 1;
    }
    return PushEntityDataTable(state, entity);
}

// `EntityPlayer:GetOtherTwin()`（批次 3）：PC 文档（`EntityPlayer.md:1172`）说只有
// 双人角色/多形态角色才有"另一个"（Jacob↔Esau、T.Forgotten↔T.Soul、T.Lazarus+Birthright）。
// **真机单人场景返回 nil** —— EID 的写法是 `p:GetOtherTwin() or p`（`main.lua:1072`）
// 与 `{ EID.player, EID.player:GetOtherTwin() }`（`main.lua:1075`），nil 正好是它期望的
// "没有另一个"；返回 self 反而会让 EID 把同一个玩家当两个人处理。
//
// 为什么现在不做真实现：要判"有没有 twin"必须先定位 `Entity_Player` 的分身指针
// （`+0x2460`/`+0x2580`/`+0x2588` 这几个在 `SetControllerIndex` 里出现过，但那是**传播**
// controller index 用的，不能推断它们是 twin），再判断 twin 的 vptr 与类型；本轮没有那份证据，
// 所以按"单人 nil"如实降级，并在报告里列为未实现。
int EntityPlayerGetOtherTwin(lua_State* state) {
    CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPlayer:GetOtherTwin accepts no arguments");
    }
    PushNil(state);
    return 1;
}


// `EntityPlayer:GetEffectiveMaxHearts()`：**红心容器**（`+0x16B8`，半心为单位）加上
// **2 × 骨心**（`+0x2450`）—— 与引擎自己的 `Entity_Player::GetEffectiveMaxHearts()`
// （`0x27E8AC`）逐分支一致：当 `PlayerType <= 0x28` 且 `(1 << PlayerType)` 命中掩码
// `0x11883021410`（???/The Lost/Black Judas/The Soul/T.Judas/T.???/T.Lost/T.Forgotten/
// T.Bethany/T.Soul）时**只**返回红心容器。偏移与掩码的出处见 `runtime_constants.hpp`。
int EntityPlayerGetEffectiveMaxHearts(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPlayer:GetEffectiveMaxHearts accepts no arguments");
    }
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    std::uint32_t containers = 0;
    if (entity == 0 ||
        !ReadEngine(entity + kEntityPlayerRedHeartContainersOffset, &containers)) {
        PushZero(state);
        return 1;
    }
    std::int64_t total = static_cast<std::int64_t>(containers);
    std::uint32_t playerType = 0;
    bool countsBoneHearts = true;
    if (ReadEngine(entity + kEntityPlayerTypeOffset, &playerType) &&
        playerType <= kEntityPlayerMaximumTypeForBoneHearts) {
        countsBoneHearts =
            (kEntityPlayerBoneHeartlessTypeMask & (1ULL << playerType)) == 0;
    }
    std::uint32_t boneHearts = 0;
    if (countsBoneHearts && ReadEngine(entity + kEntityPlayerBoneHeartsOffset, &boneHearts)) {
        total += static_cast<std::int64_t>(boneHearts) * 2;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(total));
    return 1;
}



// 读一个 `Entity_Player` 无符号整数字段并压栈，读不出来时给 0。**恒返回数字**：
// EID 把主动道具/饰品/婴儿皮肤的结果直接拿去比较或当表键（`main.lua:1076` 那一类写法），
// nil 会变成 "attempt to compare/index a nil value"。
void PushPlayerUintOrZero(lua_State* state, const EntityHandle* handle, std::uintptr_t offset) {
    std::uint32_t value = 0;
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity != 0 && ReadEngine(entity + offset, &value)) {
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return;
    }
    PushZero(state);
}

// `EntityPlayer:GetActiveItem(ActiveSlot = SLOT_PRIMARY)`（批次 4）：PC 文档
// （`EntityPlayer.md:763`）"Returns the currently held item. Returns `0` when no item is held."
// 偏移 `+0x1964 + slot*0x1C`（`docs/问题与解决记录.md` 批次 4 的小偏移表；同一组里
// `+0x1968`/`+0x196C`/`+0x197C` 是充能/第二充能/最大充能，本批次**不**暴露它们）。
//
// 槽号：PC 的 `ActiveSlot` 0..3（`SLOT_PRIMARY`/`SLOT_SECONDARY`/`SLOT_POCKET`/`SLOT_POCKET2`）。
// 我们不复制"越界自动夹到 0"这种行为：槽号非整数或为负 → 0（"没有道具"）；槽号过大
// （≥ kEntityPlayerMaximumActiveSlots）→ 0，**不去读一个可能落在别的字段上的地址**。
constexpr lua_Integer kEntityPlayerMaximumActiveSlots = 4;

int EntityPlayerGetActiveItem(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    const int argumentCount = lua_gettop(state);
    if (argumentCount > 2) {
        return luaL_error(state, "EntityPlayer:GetActiveItem accepts an optional active slot");
    }
    lua_Integer slot = 0;
    if (argumentCount == 2 && !ReadIntegerArgument(state, 2, &slot)) {
        PushZero(state);
        return 1;
    }
    if (slot < 0 || slot >= kEntityPlayerMaximumActiveSlots) {
        PushZero(state);
        return 1;
    }
    static_cast<void>(PushPlayerUintOrZero(
        state, handle,
        kEntityPlayerActiveItemOffset +
            static_cast<std::uintptr_t>(slot) * kEntityPlayerActiveItemStride));
    return 1;
}

// `EntityPlayer:GetTrinket(int TrinketIndex)`（批次 4）：PC 文档（`EntityPlayer.md:1315`）
// "Gets the ID of the trinket the player is holding in the given trinketslot (0 or 1).
// Returns `0` when no trinket is held in the given slot."
// 偏移 `+0x1AB0 + slot*4`，**2 槽**；槽号越界返回 0（任务给定的口径，与 PC 不同：
// PC 越界行为没有文档，这里选择"不读越界地址"）。
int EntityPlayerGetTrinket(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    lua_Integer slot = 0;
    if (lua_gettop(state) != 2 || !ReadIntegerArgument(state, 2, &slot)) {
        return luaL_error(state, "EntityPlayer:GetTrinket accepts one trinket slot");
    }
    if (slot < 0 || static_cast<std::uint64_t>(slot) >= kEntityPlayerTrinketSlotCount) {
        PushZero(state);
        return 1;
    }
    PushPlayerUintOrZero(
        state, handle,
        kEntityPlayerTrinketOffset + static_cast<std::uintptr_t>(slot) * sizeof(std::uint32_t));
    return 1;
}

// --- 批次 6：EID 逐帧调用的一批 `EntityPlayer` 成员（安全默认值）-----------------
//
// 背景（真机报告 `01789210946`）：EID 的 update 回调里
// `features/eid_api.lua:2606` 写的是 `EID.PlayerHeldPill[i] = player:GetPill(0)`。
// 我们没实现 `GetPill` 时，这一句是 "attempt to call a nil value (method 'GetPill')"，
// 于是**整条 update 回调**抛错、被派发器静默摘除 —— 从此 EID 再也不更新、屏幕全空，
// 而游戏不崩。这类缺口的代价因此远大于"少一个数据"。
//
// 所以这一批的口径是：**先把名字补齐并返回类型正确的默认值**，让 EID 的调用链活下来；
// 具体数值/对象等引擎字段偏移定位后再逐个换成真实现（成熟度在 Catalog 里标 `Experimental`）。
// 默认值的选择规则：
//   * 返回"库存计数/等级"一类数字 → `0`（PC 在"没有"时就是 0，EID 全是算术与比较）；
//   * 返回"是否有/是不是"一类布尔 → `false`；
//   * 返回"可能为 nil 的对象或 id"（`GetPill`/`GetCard`/`GetMainTwin`/各 `*RNG`/`GetName`）
//     → `nil`。这几个在 PC 上本来就是"没有就是 nil"，EID 也按 nil 判（例如
//     `if player:GetMainTwin() then`），比编一个假对象安全。
//
// 参数检查沿用本文件既有写法：参数个数不符即 `luaL_error`（与 PC 的 Lua 报错行为一致，
// 也让 Mod 作者的笔误可见），但**缺数据不报错**。
namespace {

// 只做参数检查：这一批默认值都不依赖引擎读数。
bool EntityPlayerProbeNoArguments(lua_State* state, const char* name) {
    static_cast<void>(CheckEntityHandle(state, 1));
    if (lua_gettop(state) != 1) {
        luaL_error(state, "%s accepts no arguments", name);
        return false;
    }
    return true;
}

bool EntityPlayerProbeSlots(lua_State* state, const char* name, int maximum) {
    static_cast<void>(CheckEntityHandle(state, 1));
    lua_Integer slot = 0;
    if (lua_gettop(state) != 2 || !ReadIntegerArgument(state, 2, &slot) || slot < 0 ||
        slot >= maximum) {
        luaL_error(state, "%s accepts one slot in [0, %d]", name, maximum - 1);
        return false;
    }
    return true;
}

// `GetCard`/`GetPill` 的槽位口径（2026-09-12 修正）。
//
// ★ **这两个方法与其它"带槽位"成员不同：越界槽位在 PC 上不是错误，而是"没有"**。
// 证据链（真机报告 `01789218953`／`01789219353`，两份的负载文本都指向同一处）：
//   * EID 的 `features/eid_api.lua:1866` 写 `for j = 0, (EID.isRepentance and 3 or 1) do`
//     然后 `player:GetCard(j)`（`eid_data.lua` 的 `Pocket1..Pocket4` 同样问到 3）。
//   * 旧实现把这句当成"槽位必须落在 [0,1]"，`j = 2` 时抛
//     `EntityPlayer:GetCard accepts one slot in [0, 1]` —— 这个 Lua 错误让派发器
//     **静默摘除整条 update 回调**（屏幕上什么都没有，游戏不崩）。
//   * PC 文档（`analysis/isaacdocs-snapshot/docs/EntityPlayer.md:882`）的签名是
//     `GetCard(int SlotId)`，返回类型 `Card`，默认值 `0` —— 没有"越界即错误"这一说。
// 因此这里**只钉"参数必须是整数"**（那才是 Mod 作者的笔误），槽位越界一律走"没有"。
bool EntityPlayerProbePocketSlot(lua_State* state, const char* name, lua_Integer* slot) {
    static_cast<void>(CheckEntityHandle(state, 1));
    if (lua_gettop(state) != 2 || !ReadIntegerArgument(state, 2, slot)) {
        luaL_error(state, "%s accepts one integer slot", name);
        return false;
    }
    return true;
}

} // namespace

int EntityPlayerGetPill(lua_State* state) {
    lua_Integer slot = 0;
    if (!EntityPlayerProbePocketSlot(state, "EntityPlayer:GetPill", &slot)) {
        return 0;
    }
    // PC：`GetPill(int PillIndex)` 返回药丸 id，**没拿药丸时返回 0**（与 `GetTrinket` 同口径）。
    // 越界槽位（例如 EID 会问到的槽位 3）同样是"没有" → 0，**不报错**：
    // 报错会摘掉整条 update 回调（证据链见上面 `EntityPlayerProbePocketSlot` 上方的注释）。
    // ★ 这台机器上还没有"按槽位读玩家口袋"的引擎字段（`Entity_Player` 的口袋数组偏移未定位），
    // 所以 0 同时是我们能给的最保守答案：PC 上"空口袋"本来就是 0，而 EID 的每一处
    // `GetCard`/`GetPill` 都按 `id ~= 0` 判（`eid_data.lua:91-116`、`eid_bagofcrafting.lua:100`）。
    // 换句话说：口袋里有东西时它会缺一条口袋描述，但**不会**造成错误结论或误判。
    PushZero(state);
    return 1;
}

int EntityPlayerGetCard(lua_State* state) {
    lua_Integer slot = 0;
    if (!EntityPlayerProbePocketSlot(state, "EntityPlayer:GetCard", &slot)) {
        return 0;
    }
    PushZero(state);  // 没拿卡时 0；越界槽位同理（见 GetPill 的注释）
    return 1;
}

int EntityPlayerGetMainTwin(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetMainTwin")) {
        return 0;
    }
    PushNil(state);  // 不是双子里的小退时 nil（PC 亦然）
    return 1;
}

int EntityPlayerGetCollectibleRNG(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:GetCollectibleRNG", 1)) {
        return 0;
    }
    PushNil(state);
    return 1;
}

int EntityPlayerGetTrinketRNG(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:GetTrinketRNG", 2)) {
        return 0;
    }
    PushNil(state);
    return 1;
}

int EntityPlayerGetCardRNG(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:GetCardRNG", 1)) {
        return 0;
    }
    PushNil(state);
    return 1;
}

int EntityPlayerGetPillRNG(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:GetPillRNG", 1)) {
        return 0;
    }
    PushNil(state);
    return 1;
}

int EntityPlayerGetNumKeys(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetNumKeys")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetNumBombs(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetNumBombs")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetNumCoins(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetNumCoins")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetHearts(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetHearts")) {
        return 0;
    }
    PushZero(state);
    return 1;
}


int EntityPlayerGetSoulCharge(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetSoulCharge")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetBloodCharge(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetBloodCharge")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetPoopMana(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetPoopMana")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetPoopSpell(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetPoopSpell")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetZodiacEffect(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetZodiacEffect")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetModelingClayEffect(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetModelingClayEffect")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetGlyphOfBalanceDrop(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetGlyphOfBalanceDrop")) {
        return 0;
    }
    PushZero(state);
    return 1;
}

// `EntityPlayer:GetCollectibleNum(id[, ignoreModifiers])`
// —— 2026-09-14 从"恒 0 占位"改成**真读数**。
//
// 为什么必须真读：EID 的**套装/变身进度**（`EID:evaluateTransformationProgress`，
// `features/eid_api.lua`）正是靠它逐个道具累加；占位返回 0 会让进度**永远显示 `(0/3)`** ——
// 不报错、只是永远不对（用户实测："一直是 (0/3)"）。
//
// 数据来源：`Entity_Player` 的"每收藏品计数数组"——一对 `begin/end` 指针（元素为 32 位整数），
// 见 `runtime_constants.hpp` 的 `kEntityPlayerCollectibleBeginOffset/EndOffset`。
// 口径与同族一致：**任何一步不成立都保守返回 0**，绝不报错（报错会摘掉整条回调）。
int EntityPlayerGetCollectibleNum(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    const int argumentCount = lua_gettop(state);
    lua_Integer collectibleId = 0;
    if (argumentCount < 2 || argumentCount > 3 ||
        !ReadIntegerArgument(state, 2, &collectibleId)) {
        return luaL_error(
            state,
            "EntityPlayer:GetCollectibleNum accepts (collectible[, ignoreModifiers])");
    }
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity == 0 || collectibleId < 0 ||
        static_cast<std::uint64_t>(collectibleId) > std::numeric_limits<std::uint32_t>::max()) {
        PushZero(state);
        return 1;
    }
    std::uintptr_t begin = 0;
    std::uintptr_t end = 0;
    if (!ReadEngine(entity + kEntityPlayerCollectibleBeginOffset, &begin) ||
        !ReadEngine(entity + kEntityPlayerCollectibleEndOffset, &end) || begin == 0 ||
        end <= begin || ((end - begin) % sizeof(std::uint32_t)) != 0) {
        PushZero(state);
        return 1;
    }
    const std::size_t count = static_cast<std::size_t>((end - begin) / sizeof(std::uint32_t));
    if (static_cast<std::uint64_t>(collectibleId) >= count) {
        PushZero(state);
        return 1;
    }
    std::uint32_t owned = 0;
    if (!ReadEngine(begin + static_cast<std::uintptr_t>(collectibleId) * sizeof(std::uint32_t),
                    &owned)) {
        PushZero(state);
        return 1;
    }
    lua_pushinteger(state, static_cast<lua_Integer>(owned));
    return 1;
}

// `EntityPlayer:GetTrinketMultiplier(id)` —— 2026-09-14 从"恒 0 占位"改成**真读数**。
// PC 语义：该饰品在身上的**份数**（EID 的套装进度用它累加饰品贡献）。
// 数据来源：`Entity_Player` 的两个饰品槽（`kEntityPlayerTrinketOffset`，步长 4 字节）：
// 某个槽等于该 id 就 +1。槽读不到就跳过（保守，不报错）。
int EntityPlayerGetTrinketMultiplier(lua_State* state) {
    auto* handle = CheckEntityHandle(state, 1);
    lua_Integer trinketId = 0;
    if (lua_gettop(state) != 2 || !ReadIntegerArgument(state, 2, &trinketId)) {
        return luaL_error(state, "EntityPlayer:GetTrinketMultiplier accepts one trinket id");
    }
    const std::uintptr_t entity = ValidatedEntityPlayer(handle);
    if (entity == 0 || trinketId <= 0 ||
        static_cast<std::uint64_t>(trinketId) > std::numeric_limits<std::uint32_t>::max()) {
        PushZero(state);
        return 1;
    }
    lua_Integer multiplier = 0;
    for (std::size_t slot = 0; slot < kEntityPlayerTrinketSlotCount; ++slot) {
        std::uint32_t held = 0;
        if (!ReadEngine(entity + kEntityPlayerTrinketOffset + slot * sizeof(std::uint32_t),
                        &held)) {
            continue;
        }
        if (held == static_cast<std::uint32_t>(trinketId)) {
            multiplier += 1;
        }
    }
    lua_pushinteger(state, multiplier);
    return 1;
}

int EntityPlayerGetPlayerFormCounter(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:GetPlayerFormCounter", 64)) {
        return 0;
    }
    PushZero(state);
    return 1;
}

int EntityPlayerGetSmeltedTrinkets(lua_State* state) {
    static_cast<void>(CheckEntityHandle(state, 1));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPlayer:GetSmeltedTrinkets accepts no arguments");
    }
    // PC 返回一个可遍历集合；空表是最安全的近似（Mod 遍历得到 0 个元素，而不是报错）。
    lua_newtable(state);
    return 1;
}

int EntityPlayerGetEffects(lua_State* state) {
    static_cast<void>(CheckEntityHandle(state, 1));
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "EntityPlayer:GetEffects accepts no arguments");
    }
    lua_newtable(state);  // 同上：空集合
    return 1;
}

int EntityPlayerGetName(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:GetName")) {
        return 0;
    }
    PushNil(state);  // 名字来自存档，未定位 → nil（EID 只做字符串拼接，nil 会被 tostring 兜住）
    return 1;
}

int EntityPlayerHasTrinket(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:HasTrinket", 1024)) {
        return 0;
    }
    lua_pushboolean(state, 0);
    return 1;
}

int EntityPlayerHasGoldenBomb(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:HasGoldenBomb")) {
        return 0;
    }
    lua_pushboolean(state, 0);
    return 1;
}

int EntityPlayerHasPlayerForm(lua_State* state) {
    if (!EntityPlayerProbeSlots(state, "EntityPlayer:HasPlayerForm", 64)) {
        return 0;
    }
    lua_pushboolean(state, 0);
    return 1;
}

int EntityPlayerCanPickRedHearts(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:CanPickRedHearts")) {
        return 0;
    }
    // PC：满血时 false。默认 false 与 EID 的用法（决定是否显示心之容器相关行）方向一致。
    lua_pushboolean(state, 0);
    return 1;
}

int EntityPlayerIsSubPlayer(lua_State* state) {
    if (!EntityPlayerProbeNoArguments(state, "EntityPlayer:IsSubPlayer")) {
        return 0;
    }
    lua_pushboolean(state, 0);  // 不是副玩家（双子的小退才是）
    return 1;
}


// --- ItemConfig 只读视图（批次 2b）-------------------------------------------
//
// 偏移来源 = **反汇编定位 + 真机现象逐位互证**（常量表与全部指令级证据在
// `runtime_constants.hpp` 的 ItemConfig 段）。三条纪律：
//
//   1. `IC = Manager + kManagerItemConfigOffset` 是**加法**：`ItemConfig` 内嵌在 `Manager` 里，
//      引擎中不存在指向它的指针。上一轮探针把 `*(M + 0x36538)`（其实是 `collectibles.begin()`）
//      当成了 `ItemConfig*`，这就是那次"条目指针为 0"的根因。
//   2. **每一次**访问都重新走完整条链路（基址 → `g_Manager` 槽 → `Manager*` → `IC` → 向量 →
//      条目）：句柄里的地址只当"身份"，不当"可用性"（换局/重开后 `ItemConfig::Init` 会重排向量）。
//   3. 任何一步不可读、长度非法、下标越界 → 返回 `nil`/`false`，**不抛 Lua 错误**：
//      `Isaac.GetItemConfig():GetCollectible(id)` 在 Mod 里"缺件"是常态。
//
// **只做只读**：本批次不提供任何写引擎内存的接口（PC 也没有：`ItemConfig` 的 Lua 侧是只读视图）。
constexpr std::size_t kItemConfigMaximumItems = 4096;         // 733 的 5 倍余量
constexpr std::size_t kItemConfigMaximumStringLength = 4096;  // libc++ 串的长度上限

struct ItemVectorView {
    std::uintptr_t begin;
    std::uintptr_t end;
    std::size_t count;
};

// 读一条 `std::vector<ItemConfig::Item*>`。向量在 `IC` 上占相邻两个 u64（反汇编里每个 getter
// 的第一条指令都是 `ldp`），所以"步长 8 / 非空 / 有上限"这三条检查就是全部判据。
bool ReadItemVector(std::uintptr_t owner, std::uintptr_t beginOffset, std::uintptr_t endOffset,
                    ItemVectorView* view) noexcept {
    if (view == nullptr || beginOffset > endOffset || owner > UINTPTR_MAX - endOffset) {
        return false;
    }
    std::uintptr_t begin = 0;
    std::uintptr_t end = 0;
    if (!ReadEngine(owner + beginOffset, &begin) || !ReadEngine(owner + endOffset, &end)) {
        return false;
    }
    if (begin == 0 || end <= begin || (end - begin) % sizeof(std::uintptr_t) != 0) {
        return false;
    }
    const std::size_t count = static_cast<std::size_t>((end - begin) / sizeof(std::uintptr_t));
    if (count > kItemConfigMaximumItems) {
        return false;
    }
    view->begin = begin;
    view->end = end;
    view->count = count;
    return true;
}

// 整条链路：基址 → `g_Manager` 槽 → `Manager*` → 内嵌 `ItemConfig`。返回 0 = **链路本身**
// 拿不到（基址未发布 / 槽不可读 / `Manager` 为空 / 偏移越界）。
//
// ★★ **2026-09-12 关键修正：不再把"收藏品向量此刻不可读"当成"拿不到 ItemConfig"。**
//
// 真机证据（报告 `01789226184`，模块指纹 `777E4394…` 与本地构建一致）：EID 的
// `main.lua:37` 是 `EID.itemConfig = Isaac.GetItemConfig()`，而那次会话的
// **"最后调用的 API" = 位 17（`Isaac.GetItemConfig`）、主掩码调用总数 = 1** ——
// 也就是说 EID 在**加载期**只调用了这一个 API，紧接着的
// `EID.itemConfig:GetCollectible(...)` 就断了。原因是旧实现要求"收藏品向量可读"
// （`begin != 0 && end > begin`），而在**主界面/还没开局**时那个向量还没建立
// （`begin == 0`）⇒ 我们返回 `nil` ⇒ EID 的下一次 `Index` 直接报
// `attempt to index a nil value`，且门面在返回前就返回了，**整个 EID 起不来**。
//
// PC 语义对照：`Isaac.GetItemConfig()` 返回引擎的 `ItemConfig` 单例，**永远不是 nil**
// （`analysis/isaacdocs-snapshot/docs/Isaac.md`）。所以：
//   * 这里只负责"对象拿得到拿不到"；
//   * "向量此刻有没有内容"交给**每次调用现读**的 getter（`ValidatedItemConfig` 已改成现读，
//     见它的注释），这样句柄也不会因为跨帧的向量重建而失效。
std::uintptr_t ResolveItemConfig(bool* chainResolved = nullptr) noexcept {
    if (chainResolved != nullptr) {
        *chainResolved = false;
    }
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameManagerGlobalSlotOffset) {
        return 0;
    }
    // 两级解引用：槽里是**指针变量**（`g_Manager` 自己的地址），解一次才是 `Manager*`。
    std::uintptr_t managerSlot = 0;
    std::uintptr_t manager = 0;
    if (!ReadEngine(base + kGameManagerGlobalSlotOffset, &managerSlot) || managerSlot == 0 ||
        !ReadEngine(managerSlot, &manager) || manager == 0 ||
        manager > UINTPTR_MAX - kManagerItemConfigOffset) {
        return 0;
    }
    if (chainResolved != nullptr) {
        *chainResolved = true;
    }
    return manager + kManagerItemConfigOffset;
}

void PushItemConfigHandle(lua_State* state, std::uintptr_t config, const ItemVectorView& view,
                         bool itemsWereReadable) {
    auto* handle = static_cast<ItemConfigHandle*>(lua_newuserdata(state, sizeof(ItemConfigHandle)));
    handle->config = reinterpret_cast<void*>(config);
    handle->items = reinterpret_cast<void*>(view.begin);
    handle->count = static_cast<std::uint64_t>(view.count);
    handle->itemsWereReadable = itemsWereReadable ? 1U : 0U;
    handle->reserved = 0;
    luaL_getmetatable(state, kItemConfigMetatable);
    lua_setmetatable(state, -2);
}

void PushItemConfigItemHandle(lua_State* state, std::uintptr_t item, std::uintptr_t beginOffset,
                              std::uintptr_t itemsBegin, std::size_t index) {
    auto* handle =
        static_cast<ItemConfigItemHandle*>(lua_newuserdata(state, sizeof(ItemConfigItemHandle)));
    handle->item = reinterpret_cast<void*>(item);
    handle->items = reinterpret_cast<void*>(itemsBegin);
    handle->beginOffset = static_cast<std::uint32_t>(beginOffset);
    handle->index = static_cast<std::uint32_t>(index);
    luaL_getmetatable(state, kItemConfigItemMetatable);
    lua_setmetatable(state, -2);
}

// 句柄 → 通过校验的 `IC`（0 = 失效）。缓存的 `items`/`count` 必须与现场逐值一致：换局/重开后
// `ItemConfig::Init` 会重新分配向量，旧句柄必须自证过期而不是继续读已经不属于它的内存。
std::uintptr_t ValidatedItemConfig(const ItemConfigHandle* handle) noexcept {
    if (handle == nullptr) {
        return 0;
    }
    const std::uintptr_t config = ResolveItemConfig();
    if (config == 0 || config != reinterpret_cast<std::uintptr_t>(handle->config)) {
        return 0;
    }
    // ★ 两种情况分开（2026-09-12，真机报告 `01789226184`）：
    //   * 句柄创建时**向量还没建立**（`itemsWereReadable == 0`）：允许现在才建立 ——
    //     EID 在加载期就取 `Isaac.GetItemConfig()`，那时向量是空的；若沿用旧判据，
    //     这个仍然有效的句柄一开局就被判过期、`EID.itemConfig` 直接变死。
    //   * 句柄创建时**向量已建立**：沿用"begin/count 必须与现场一致"的过期判据
    //     （换局后 `ItemConfig::Init` 会重排向量，旧句柄必须自证过期，见
    //     `test_stale_handles_degrade_and_new_handles_re_read_the_engine`）。
    if (handle->itemsWereReadable == 0) {
        return config;
    }
    ItemVectorView view{};
    if (!ReadItemVector(config, kItemConfigCollectibleBeginOffset, kItemConfigCollectibleEndOffset,
                        &view)) {
        return 0;
    }
    if (view.begin != reinterpret_cast<std::uintptr_t>(handle->items) ||
        handle->count != static_cast<std::uint64_t>(view.count)) {
        return 0;
    }
    return config;
}

// 条目句柄 → 通过校验的 `ItemConfig::Item*`（0 = 失效）。校验是"它仍然是这条向量的那个元素"，
// 而不是"这个地址看起来可读"：条目就是向量元素（`ldr x0,[begin, w1, uxtw #3]` 是反汇编里
// 四个 getter 的共同形态），所以这条判据同时覆盖了"换局后向量重排"与"指针被改坏"两种情况。
std::uintptr_t ValidatedItem(const ItemConfigItemHandle* handle) noexcept {
    if (handle == nullptr) {
        return 0;
    }
    const std::uintptr_t config = ResolveItemConfig();
    const std::uintptr_t item = reinterpret_cast<std::uintptr_t>(handle->item);
    // `beginOffset` 是 u32（句柄里存不下别的），所以"偏移 + 8"不会回绕；这里只拒 0 配置与空条目。
    if (config == 0 || item == 0) {
        return 0;
    }
    ItemVectorView view{};
    if (!ReadItemVector(config, handle->beginOffset, handle->beginOffset + sizeof(std::uintptr_t),
                        &view)) {
        return 0;
    }
    if (view.begin != reinterpret_cast<std::uintptr_t>(handle->items) ||
        handle->index >= view.count) {
        return 0;
    }
    std::uintptr_t current = 0;
    if (!ReadEngine(view.begin + static_cast<std::size_t>(handle->index) * sizeof(std::uintptr_t),
                    &current)) {
        return 0;
    }
    return current == item ? item : 0;
}

// libc++ `std::string`（SSO）→ Lua 字符串。
//
// 布局（`ItemConfig::Item::GetDisplayName` 的 `ldrb w8,[x21,#0x8]!` + `lsr x9,x8,#1` 就是硬证据）：
//   * byte0 的 bit0 = `is_long`；
//   * 短串：data 内联在 `+0x01`、size = `byte0 >> 1`（libc++ 的 size 左移一位存放）；
//   * 长串：size 在 `+0x08`、data 指针在 `+0x10`。
// **不是 `const char*`** —— 上一版把它当 `const char*` 读，拿到的就是 SSO 头两个字节。
//
// `allowEmpty`：`Name`/`Description` 的空串仍按"读不到"处理（调用方当 nil），但
// `GfxFileName` 的空串是**合法值** —— PC 上它是普通 `std::string` 字段，空串表示"没有自定义图集"，
// 引擎自己的解析器也只对 `gfx` 属性做 `append`。EID 的
// `spriteDummy:ReplaceSpritesheet(1, item.GfxFileName)`（`features/eid_api.lua:1278`）就是这条语义：
// 拿到 nil 会被我们的参数校验拒绝并抛 Lua 错误，拿到 `""` 则是"清掉这一层"的正常调用。
bool PushLibcxxString(lua_State* state, std::uintptr_t object, bool allowEmpty = false) noexcept {
    std::uint8_t first = 0;
    if (!ReadEngine(object, &first)) {
        return false;
    }
    std::uintptr_t data = 0;
    std::size_t size = 0;
    if ((first & 1U) != 0) {
        std::uint64_t length = 0;
        std::uintptr_t pointer = 0;
        if (!ReadEngine(object + 8, &length) || !ReadEngine(object + 0x10, &pointer)) {
            return false;
        }
        if (length == 0 && allowEmpty) {
            lua_pushliteral(state, "");
            return true;
        }
        if (pointer == 0 || length == 0 || length > kItemConfigMaximumStringLength) {
            return false;
        }
        data = pointer;
        size = static_cast<std::size_t>(length);
    } else {
        const std::size_t length = static_cast<std::size_t>(first >> 1);
        if (length == 0) {
            if (allowEmpty) {
                lua_pushliteral(state, "");
                return true;
            }
            return false;
        }
        data = object + 1;
        size = length;
    }
    if (!IsEngineMemoryReadable(data, size)) {
        return false;
    }
    // 字节原样交给 Lua（不假设 UTF-8）：Repentance 的 `Name`/`Description` 返回的是 `#KEY`
    // 形状的资源键，EID 这类 Mod 就是拿它去查字符串表的。
    lua_pushlstring(state, reinterpret_cast<const char*>(data), size);
    return true;
}

// `ItemConfig_Item` 的字段。返回 false = "不是这个对象的字段或读不出来"，让调用方继续找方法。
//
// ★ `Type` = `+0x00` 是 PC 的 **`ItemType`**（`0/1/2/3/4 = NULL/PASSIVE/TRINKET/ACTIVE/FAMILIAR`），
// 不是"向量类别"：`items.xml` 的 `type` 属性就是被解析器原样写进这个字段的
// （`ItemConfig::Load` 的 `"passive"→1`/`"trinket"→2`/`"active"→3`/`"familiar"→4`/`"null"→0`，
// 逐条反汇编证据在 `runtime_constants.hpp` 的 `kItemConfigItemTypeOffset` 注释里）。
// 所以 EID 的 `descObj.ItemType = itemConfig.Type`（`features/eid_api.lua:782`）本来就是对的，
// 本批次**没有改这个偏移**，只把常量名与注释从"kind/类别"改成真实语义。
// 引擎侧**没有**另一个"这个条目属于哪条向量"的字段：向量身份来自它从哪条 `std::vector` 取出，
// 所以没有 `ItemKind` 这个名字可用（原先的 `1 = COLLECTIBLE` 是误读）。
bool PushItemField(lua_State* state, std::uintptr_t item, const char* field) noexcept {
    if (std::strcmp(field, "ID") == 0) {
        std::uint32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemIdOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "Type") == 0) {
        std::uint32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemTypeOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "Name") == 0) {
        return PushLibcxxString(state, item + kItemConfigItemNameOffset);
    }
    if (std::strcmp(field, "Description") == 0) {
        return PushLibcxxString(state, item + kItemConfigItemDescriptionOffset);
    }
    // `GfxFileName`：PC 文档里的 `string` 字段，EID 在
    // `features/eid_api.lua:1278` 把它直接喂给 `Sprite:ReplaceSpritesheet`。
    // 空串按合法值返回（见 `PushLibcxxString` 的 `allowEmpty` 注释）。
    if (std::strcmp(field, "GfxFileName") == 0) {
        return PushLibcxxString(state, item + kItemConfigItemGfxFileNameOffset, true);
    }
    // `Quality` / `CraftingQuality`（2026-09-16）：EID 靠它们显示道具品质
    // （`features/eid_api.lua:2702` → `main.lua:663` 的 `{{QualityN}}`）与做背包合成排序
    // （`eid_bagofcrafting.lua:393/407`）。缺了这两个字段的症状是**静默的**：`desc.Quality`
    // 为 nil 被 `and` 短路，品质图标直接不显示，既不报错也没有日志 —— 这正是"缺口账本
    // 只统计方法、字段从没被枚举过"留下的洞（见 `docs/错误复盘.md` 2026-09-16）。
    // 偏移证据见 `runtime_constants.hpp` 的 `kItemConfigItemQualityOffset` 注释。
    // 两个都是 32 位整数；`CraftingQuality` 在 `items.xml` 未指定时由引擎用 `Quality` 回填，
    // 所以这里**照读原值**，不自己编缺省（读不到时按"字段读不出来"降级成 nil，交给 Mod 的
    // `or` 兜底，与 PC 的 `item.CraftingQuality or item.Quality` 写法一致）。
    if (std::strcmp(field, "Quality") == 0) {
        std::uint32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemQualityOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "CraftingQuality") == 0) {
        std::uint32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemCraftingQualityOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    // 2026-09-16 第二批（字段缺口台账里的 planned 项，同在 `items.xml` 属性分派表上）：
    // `AchievementID` / `Tags` / `MaxCharges` / `ChargeType`。
    // 逐个的偏移依据见 `runtime_constants.hpp` 里那四个常量的注释（都是同一条链上的 `str`）。
    // **有符号 / 无符号是按 PC 文档定的，不是随手选的**：
    //   * `AchievementID` 文档写 int 且"默认可解锁时返回 -1"，EID 直接与 `-1` 比较
    //     （`features/eid_api.lua:2003`）⇒ 无符号读会让它永远不相等；
    //   * `MaxCharges` / `ChargeType` 也是 int ⇒ 有符号读（-1 这类哨兵值不丢）；
    //   * `Tags` 是**位掩码**（EID 用 `item.Tags & ItemConfig.TAG_QUEST`）⇒ 无符号读，
    //     第 31 位不会被符号扩展污染成"高 32 位全 1"。
    if (std::strcmp(field, "AchievementID") == 0) {
        std::int32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemAchievementIdOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "Tags") == 0) {
        std::uint32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemTagsOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "MaxCharges") == 0) {
        std::int32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemMaxChargesOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    if (std::strcmp(field, "ChargeType") == 0) {
        std::int32_t value = 0;
        if (!ReadEngine(item + kItemConfigItemChargeTypeOffset, &value)) {
            return false;
        }
        lua_pushinteger(state, static_cast<lua_Integer>(value));
        return true;
    }
    // `Hidden`（2026-09-16，第三批）：PC 文档写的是 `boolean`，引擎里就是 `+0xB7` 的**一个字节**
    // （`ItemConfig::Item::IsAvailable` 第一条指令 `ldrb w8,[x0,#0xb7]`）⇒ 按字节读、压成布尔，
    // **不要**按 4 字节整数读（会把后面 3 个字节的无关内容当成真值）。
    // EID 的两处用法：`eid_api.lua:2008` 的 `if item.Hidden then`（隐藏道具不出描述）、
    // `eid_api.lua:1927` Spindown Dice 预测里的 `not item.Hidden`（缺这个字段时那一半恒真）。
    if (std::strcmp(field, "Hidden") == 0) {
        std::uint8_t value = 0;
        if (!ReadEngine(item + kItemConfigItemHiddenOffset, &value)) {
            return false;
        }
        lua_pushboolean(state, value != 0 ? 1 : 0);
        return true;
    }
    return false;
}

ItemConfigHandle* CheckItemConfigHandle(lua_State* state, int index) {
    return static_cast<ItemConfigHandle*>(luaL_checkudata(state, index, kItemConfigMetatable));
}

ItemConfigItemHandle* CheckItemConfigItemHandle(lua_State* state, int index) {
    return static_cast<ItemConfigItemHandle*>(
        luaL_checkudata(state, index, kItemConfigItemMetatable));
}

// `GetCollectible`/`GetTrinket`/`GetCard`/`GetPillEffect` 的共同实现：句柄校验 → 向量读取 →
// 下标边界 → 条目句柄。
//
// 负下标一律返回 nil：引擎的 `GetCollectible` 对 `id < 0` 会 `tbz w1,#0x1f` 转发到
// `ProceduralItemManager::GetProceduralItem`（`*(g_Game) + 0x345F18`），我们**不支持程序化道具**
// （那需要一整套 ProceduralItemManager 视图），所以直接 nil —— 编一个条目出来比返回 nil 更糟。
// 注意 `GetTrinket` 在引擎里是 `and w1, w1, #0x7fff`（负值会绕成正下标），我们**不复制**这个
// 行为：负 id 在这里同样是 nil。
// id 参数的解析口径：与 PC 的 `luaL_checkinteger` **同一接受范围**（整数、整值浮点如 `1.0`、
// 可转换的数字串都收；`1.5`/`'nope'`/缺参数都不收），区别只在**不收时返回 nil 而不是抛错**。
bool ReadItemId(lua_State* state, int index, lua_Integer* id) noexcept {
    if (id == nullptr || lua_gettop(state) < index) {
        return false;
    }
    int isNumber = 0;
    const lua_Integer value = lua_tointegerx(state, index, &isNumber);
    if (isNumber == 0) {
        return false;
    }
    *id = value;
    return true;
}

int ItemConfigGetEntry(lua_State* state, std::uintptr_t beginOffset, std::uintptr_t endOffset) {
    auto* handle = CheckItemConfigHandle(state, 1);
    lua_Integer requested = 0;
    const std::uintptr_t config =
        ReadItemId(state, 2, &requested) ? ValidatedItemConfig(handle) : 0;
    if (config == 0 || requested < 0) {
        RecordItemConfigResult(false);  // 探针：EID 的 hasDescription 会因为这里恒空而恒假
        lua_pushnil(state);
        return 1;
    }
    ItemVectorView view{};
    if (!ReadItemVector(config, beginOffset, endOffset, &view) ||
        static_cast<std::uint64_t>(requested) >= static_cast<std::uint64_t>(view.count)) {
        RecordItemConfigResult(false);
        lua_pushnil(state);
        return 1;
    }
    const std::size_t index = static_cast<std::size_t>(requested);
    std::uintptr_t item = 0;
    if (!ReadEngine(view.begin + index * sizeof(std::uintptr_t), &item) || item == 0) {
        RecordItemConfigResult(false);
        lua_pushnil(state);
        return 1;
    }
    RecordItemConfigResult(true);
    PushItemConfigItemHandle(state, item, beginOffset, view.begin, index);
    return 1;
}

int ItemConfigGetCollectible(lua_State* state) {
    RecordFilterChain(3U);  // 过滤器链：`ItemConfig.GetCollectible`
    return ItemConfigGetEntry(state, kItemConfigCollectibleBeginOffset,
                              kItemConfigCollectibleEndOffset);
}

int ItemConfigGetTrinket(lua_State* state) {
    return ItemConfigGetEntry(state, kItemConfigTrinketBeginOffset, kItemConfigTrinketEndOffset);
}

int ItemConfigGetCard(lua_State* state) {
    return ItemConfigGetEntry(state, kItemConfigCardBeginOffset, kItemConfigCardEndOffset);
}

int ItemConfigGetPillEffect(lua_State* state) {
    return ItemConfigGetEntry(state, kItemConfigPillEffectBeginOffset,
                              kItemConfigPillEffectEndOffset);
}

// `ItemConfig:IsCollectible(id)`：**收藏品向量里存在这个条目，并且它的 `Type == ITEM_PASSIVE(1)`**。
//
// ★ 这是本实现自己的判据（PC 文档里只有 `ItemConfig_Item:IsCollectible()` 与静态的
// `ItemConfig.IsValidCollectible(id)`），**且它带着一个已知偏差**：`+0x00` 其实是 `ItemType`
// （见 `kItemConfigItemTypeOffset` 的证据），所以"要求 == 1"等价于**"必须是被动道具"**——
// 主动道具（`ITEM_ACTIVE == 3`）与跟班（`ITEM_FAMILIAR == 4`）会被判成 false。
// 引擎自己的 `ItemConfig::IsValidCollectible(eCollectibleType)`（`0x3C0D64`）只判"存在"。
//
// 为什么本批次**没改**它：这是已被验收的既有行为（`runtime/tests/test_lua_item_config.py`
// 的 `wrong_kind` 场景按"kind==1"断言），而 EID **不调用**它（EID 调的是条目级的
// `ItemConfig_Item:IsCollectible()`，见下面那个新实现），所以它不构成 EID 的阻断项。
// 是否把两者统一成"存在且 `Type != ITEM_NULL`"留给主线程决定（改动是一行判据 + 一个场景）。
//
// 我们**不调用**引擎方法：只读向量 + 一个字段即可判定，且宿主可测。
int ItemConfigIsCollectible(lua_State* state) {
    auto* handle = CheckItemConfigHandle(state, 1);
    lua_Integer requested = 0;
    const bool argumentValid = ReadItemId(state, 2, &requested) && requested >= 0;
    const std::uintptr_t config = argumentValid ? ValidatedItemConfig(handle) : 0;
    bool exists = false;
    std::uint32_t type = kItemTypeNull;
    if (config != 0) {
        ItemVectorView view{};
        if (ReadItemVector(config, kItemConfigCollectibleBeginOffset,
                           kItemConfigCollectibleEndOffset, &view) &&
            static_cast<std::uint64_t>(requested) < static_cast<std::uint64_t>(view.count)) {
            std::uintptr_t item = 0;
            if (ReadEngine(view.begin + static_cast<std::size_t>(requested) * sizeof(std::uintptr_t),
                           &item) &&
                item != 0 && ReadEngine(item + kItemConfigItemTypeOffset, &type)) {
                exists = true;
            }
        }
    }
    lua_pushboolean(state, (exists && type == kItemTypePassive) ? 1 : 0);
    return 1;
}

// `HasTags` / `ItemConfig_Item:HasTags`：**没有证据，恒返回 false**。
//
// 为什么不做：PC 的 tags 是 `ItemConfig::Item` 里的一组位（`Tags` 字段）加标签表，而
// * `Item` 的 tags 字段偏移**未定位**（反汇编只给出了 `+0x00` 类别 / `+0x04` id / `+0x08`
//   名字 / `+0x20` 描述这四个字段，其余字段没有任何证据）；
// * 符号表里确实有 `ItemConfig::IsTaggedCollectible(eCollectibleType, u64)`（`0x3C0E10`）与
//   `IsTaggedTrinket`（`0x3C0EB8`），但入参 tag 掩码的编码、以及"标签表在哪个容器"都没有证据，
//   猜一个值去调用等于伪造结论。
// 所以这里按"安全默认值"处理：PC 的返回类型是 boolean，`false` 让 `if item:HasTags(...) then`
// 走进"没有这个标签"的分支 —— 比报错、也比编一个返回值安全。等有反汇编证据再接。
int ItemConfigHasTags(lua_State* state) {
    CheckItemConfigHandle(state, 1);
    lua_pushboolean(state, 0);
    return 1;
}

int ItemConfigItemHasTags(lua_State* state) {
    CheckItemConfigItemHandle(state, 1);
    lua_pushboolean(state, 0);
    return 1;
}

// `ItemConfig_Item:IsCollectible()`（**无参**，批次 3）：PC 在**条目**上提供这个方法
// （`analysis/isaacdocs-snapshot/docs/ItemConfig_Item.md:42`："Returns if the item is a
// collectible"），EID 直接这么用：`EID.itemConfig:GetCollectible(id):IsCollectible()`
// （`main.lua:669`/`738`/`757`，都在描述文本/池子图标的构建路径上）。缺了它，那些语句会以
// "attempt to call a nil value (method 'IsCollectible')" 打断 `printDescription`。
//
// 判据与 `ItemConfig:IsCollectible(id)` 同源：**句柄仍然有效（条目就是那条向量的那个元素）
// 且 `Type != ITEM_NULL`**。句柄失效/条目为空 → false（"这个句柄不再指向一个收藏品"）。
int ItemConfigItemIsCollectible(lua_State* state) {
    auto* handle = CheckItemConfigItemHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "ItemConfig_Item:IsCollectible accepts no arguments");
    }
    const std::uintptr_t item = ValidatedItem(handle);
    std::uint32_t type = kItemTypeNull;
    const bool readable = item != 0 && ReadEngine(item + kItemConfigItemTypeOffset, &type);
    lua_pushboolean(state, (readable && type != kItemTypeNull) ? 1 : 0);
    return 1;
}

// `ItemConfig_Item:IsAvailable()`（批次"IsAvailable 一族"，2026-09-16）：EID 在
// `features/eid_api.lua:1988`、`:2013` 与 `eid_bagofcrafting.lua:826` 三处对**收藏品条目**
// 调它，用来判断"这件道具解锁了没有"（`EID:isCollectibleUnlocked`），进而决定 Spindown Dice
// 与背包合成要不要跳过它 —— 也是让 `AchievementID` / `Tags` 两个字段真正产生可见效果的关键。
//
// PC 契约（`analysis/isaacdocs-snapshot/docs/ItemConfig_Item.md:35-38`）：
// "true = 已解锁；false = 没解锁 **或被 tags 挡掉**"。
//
// 引擎侧对应**三个不同类的函数**，只能按"条目属于哪条向量"分派（句柄里就存着 `beginOffset`）：
//   收藏品 / 饰品 / 其他 `ItemConfig::Item` 条目 → `Item::IsAvailable(long flags, uint)`
//   卡牌条目                                   → `Card::IsAvailable()`
//   药丸条目                                   → `PillEffect::IsAvailable()`
// ⚠️ `flags` 取值是**我们的兼容层定义**（引擎里三个函数没有任何调用点，参数学不到），
// 依据见 `runtime_constants.hpp` 的 `kItemIsAvailableFlags` 注释，并在 `api_deviation.cpp` 登记。
//
// 降级口径：句柄失效 / 方法拿不到（偏移或入口指纹不对）⇒ 返回 **false**（"这件不可用"）。
// 为什么不给 true：EID 拿 false 的行为是"跳过这件道具"，而给 true 会让未解锁的道具照样出现 ——
// 两种都是猜，但"少显示"比"多显示未解锁内容"更接近 PC 语义，且不会让 Spindown Dice 给出非法结果。
int ItemConfigItemIsAvailable(lua_State* state) {
    auto* handle = CheckItemConfigItemHandle(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "ItemConfig_Item:IsAvailable accepts no arguments");
    }
    const std::uintptr_t entry = ValidatedItem(handle);
    if (entry == 0) {
        lua_pushboolean(state, 0);
        return 1;
    }
    int kind = 0;
    if (handle->beginOffset == kItemConfigCardBeginOffset) {
        kind = 1;
    } else if (handle->beginOffset == kItemConfigPillEffectBeginOffset) {
        kind = 2;
    }
    const std::uintptr_t method =
        kind == 1 ? CardIsAvailableMethod()
                  : (kind == 2 ? PillEffectIsAvailableMethod() : ItemIsAvailableMethod());
    if (method == 0) {
        lua_pushboolean(state, 0);
        return 1;
    }
    lua_pushboolean(state, CallItemConfigIsAvailable(method, entry, kind) ? 1 : 0);
    return 1;
}


// `__index`：方法 → nil（`ItemConfig` 没有字段，PC 同）。
//
// **方法不因句柄失效而消失**：`config:GetCollectible(id)` 在过期句柄上必须返回 nil，而不是让
// Mod 吃到 `attempt to call a nil value (method 'GetCollectible')` —— 后者更难排查。这与
// `Entity` 的 `HasCollectible` 同一口径：失效句柄上的方法仍然可调用，只是安静地降级。
int ItemConfigIndex(lua_State* state) {
    CheckItemConfigHandle(state, 1);
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    if (PushMethodFrom(state, kItemConfigMetatable, field)) {
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

int ItemConfigItemIndex(lua_State* state) {
    auto* handle = CheckItemConfigItemHandle(state, 1);
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    const std::uintptr_t item = ValidatedItem(handle);
    // `MimicCharge`（2026-09-16）：**卡牌与胶囊各有一个**（`+0x64` / `+0x48`），收藏品/饰品没有。
    // 所以它不放在 `PushItemField`（那里只有"条目指针"，不知道是卡还是胶囊），而是放在这里
    // —— 句柄的 `beginOffset` 就是种类判据（与 `IsAvailable` 的分派同一条）。
    if (item != 0 && std::strcmp(field, "MimicCharge") == 0) {
        std::uintptr_t offset = 0;
        if (handle->beginOffset == kItemConfigCardBeginOffset) {
            offset = kItemConfigCardMimicChargeOffset;
        } else if (handle->beginOffset == kItemConfigPillEffectBeginOffset) {
            offset = kItemConfigPillEffectMimicChargeOffset;
        }
        if (offset != 0) {
            std::int32_t value = 0;
            if (!ReadEngine(item + offset, &value)) {
                lua_pushnil(state);
                return 1;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(value));
            return 1;
        }
    }
    if (item != 0 && PushItemField(state, item, field)) {
        return 1;
    }
    if (PushMethodFrom(state, kItemConfigItemMetatable, field)) {
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

// `Isaac.GetItemConfig()`：PC 语义是"拿到引擎的 `ItemConfig` 单例"（EID 的第一句就是它）。
// 拿不到（基址未发布 / 槽不可读 / `Manager` 为空 / 向量非法 / 长度为 0）一律返回 nil，
// **不抛 Lua 错误** —— 与 `Isaac.GetPlayer` 同一口径。
// `Isaac.GetItemConfig()` 的三种结局（2026-09-12 定稿，与测试场景逐条对齐）：
//   * **整条链路拿不到**（基址未发布 / `g_Manager` 槽为 0 / 槽里是空指针）⇒ `nil`。
//     这是"引擎还没准备好"，Mod 必须能拿到 nil 并活下去；测试的 `no_base`/`slot_zero`/
//     `no_manager` 钉的就是这一条。
//   * **链路拿到了、只是收藏品向量此刻还没建立**（`begin == 0`，主界面/加载期）⇒ **返回对象**。
//     PC 上它永远不是 nil；真机报告 `01789226184` 证明 EID 在加载期就取它（`main.lua:37`），
//     返回 nil 会让下一句 `EID.itemConfig:GetCollectible(...)` 报
//     "attempt to index a nil value"，**整个 EID 起不来**。
//   * **链路拿到了、但向量参数明显非法**（长度不是 8 的倍数 / 超过上限）⇒ `nil`：
//     那是"偏移或内存坏了"的信号，编一个空对象只会把问题往后推。
int IsaacGetItemConfig(lua_State* state) {
    RecordApiSequence(17U);
    bool chainResolved = false;
    const std::uintptr_t config = ResolveItemConfig(&chainResolved);
    if (config == 0 || !chainResolved) {
        RecordGetItemConfigForProbe(0U);  // 链路拿不到 → 返回 nil
        lua_pushnil(state);
        return 1;
    }
    ItemVectorView view{};
    const bool itemsWereReadable =
        ReadItemVector(config, kItemConfigCollectibleBeginOffset, kItemConfigCollectibleEndOffset,
                       &view);
    std::uint32_t flags = 1U;  // bit0：链路解析成功
    if (itemsWereReadable) {
        flags |= 2U;  // bit1：向量此刻可读
    } else {
        // 区分"还没建立"（`begin == 0`，合法）与"参数非法"（长度/上限不对，视为坏内存）。
        std::uintptr_t begin = 0;
        std::uintptr_t end = 0;
        const bool vectorFieldsReadable =
            ReadEngine(config + kItemConfigCollectibleBeginOffset, &begin) &&
            ReadEngine(config + kItemConfigCollectibleEndOffset, &end);
        if (!vectorFieldsReadable || begin != 0) {
            RecordGetItemConfigForProbe(flags | 8U);  // bit3：参数非法 → 返回 nil
            lua_pushnil(state);
            return 1;
        }
        flags |= 4U;  // bit2：向量还没建立 → **返回对象**
    }
    RecordGetItemConfigForProbe(flags);
    PushItemConfigHandle(state, config, view, itemsWereReadable);
    return 1;
}

// `Isaac.GetPlayer(index)`：PC 语义是 **0 基**（0 = 玩家 1）。以下情况一律返回 nil，**不抛
// Lua 错误**（本轮的目标正是"被调用时不报错"）：下标越界、元素为空指针、vptr 不匹配、
// 内存不可读、基址尚未发布、参数不是整数或为负数。
int IsaacGetPlayer(lua_State* state) {
    RecordApiSequence(16U);
    std::int64_t index = 0;
    if (lua_gettop(state) >= 1) {
        if (!lua_isinteger(state, 1)) {
            lua_pushnil(state);
            return 1;
        }
        index = static_cast<std::int64_t>(lua_tointegerx(state, 1, nullptr));
    }
    if (index < 0) {
        lua_pushnil(state);
        return 1;
    }
    const std::uintptr_t player = ResolveEntityPlayer(static_cast<std::size_t>(index));
    if (player == 0) {
        g_GetPlayerFailure.fetch_add(1, std::memory_order_relaxed);
        lua_pushnil(state);
        return 1;
    }
    g_GetPlayerSuccess.fetch_add(1, std::memory_order_relaxed);
    PushEntityHandle(state, player, kEntityPlayerMetatable);
    return 1;
}

} // namespace

EnginePlayerArray ResolveEnginePlayerArray() noexcept {
    EnginePlayerArray result{};
    std::uintptr_t begin = 0;
    std::size_t count = 0;
    if (ResolveEntityPlayerBegin(&begin, &count) == 0) {
        return result;  // readable == false：调用方不得把它当成"真的没有玩家"
    }
    // 只发布一个“从头开始每个元素都通过 Entity_Player 校验”的有效前缀。换局/退房过渡期里，
    // `players` 容器的 begin/end 可能已经更新而元素槽尚未清空；如果这里照抄容器 count，
    // `Game:GetNumPlayers()` 会报 1，而 `Isaac.GetPlayer(0)` 已验证失败并返回 nil，EID 随即索引
    // nil。数量 API 与获取 API 必须共享同一个可调用契约，所以遇到首个无效元素就截断。
    std::size_t validCount = 0;
    for (std::size_t index = 0; index < count; ++index) {
        std::uintptr_t player = 0;
        std::uintptr_t vtable = 0;
        if (!ReadEngine(begin + index * sizeof(std::uintptr_t), &player) || player == 0 ||
            !ReadEngine(player, &vtable) || !IsEntityPlayer(player)) {
            break;
        }
        ++validCount;
    }
    result.count = validCount;
    result.readable = true;
    return result;
}

bool IsEnginePlayerPointer(std::uintptr_t candidate) noexcept {
    return IsEntityPlayer(candidate);
}

// `Isaac.CountEnemies` 最后一次返回的个数（探针读；语义见该函数里的注释）。
std::atomic<std::uint32_t> g_LastCountEnemies{0};

std::uint32_t LastCountEnemiesForProbe() noexcept {
    return g_LastCountEnemies.load(std::memory_order_relaxed);
}

// `Isaac.FindInRadius` 的探针读数出口（语义见 `g_FindInRadius*` 的定义处）。
FindInRadiusProbe FindInRadiusProbeSnapshot() noexcept {
    FindInRadiusProbe probe{};
    probe.calls = g_FindInRadiusCalls.load(std::memory_order_relaxed);
    probe.results = g_FindInRadiusResults.load(std::memory_order_relaxed);
    probe.live = g_FindInRadiusLive.load(std::memory_order_relaxed);
    probe.players = g_FindInRadiusPlayers.load(std::memory_order_relaxed);
    probe.effects = g_FindInRadiusEffects.load(std::memory_order_relaxed);
    probe.roomResolved = g_FindInRadiusRoomOk.load(std::memory_order_relaxed);
    probe.fingerprintOk = g_FindInRadiusFingerprint.load(std::memory_order_relaxed);
    probe.lastMask = g_FindInRadiusLastMask.load(std::memory_order_relaxed);
    probe.lastRadius = g_FindInRadiusLastRadius.load(std::memory_order_relaxed);
    return probe;
}

EnginePlayerChain ReadEnginePlayerChainForProbe() noexcept {
    EnginePlayerChain chain{};
    chain.moduleBase = EngineModuleBase();
    if (chain.moduleBase == 0 || chain.moduleBase > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
        return chain;
    }
    if (!ReadEngine(chain.moduleBase + kGameOwnerGlobalSlotOffset, &chain.gameSlot)) {
        chain.gameSlot = 0;
        return chain;
    }
    if (chain.gameSlot == 0 || !ReadEngine(chain.gameSlot, &chain.game)) {
        chain.game = 0;
        return chain;
    }
    if (chain.game > UINTPTR_MAX - kGamePlayerArrayEndOffset) {
        chain.game = 0;
        return chain;
    }
    chain.beginReadable = ReadEngine(chain.game + kGamePlayerArrayBeginOffset, &chain.begin) ? 1u : 0u;
    chain.endReadable = ReadEngine(chain.game + kGamePlayerArrayEndOffset, &chain.end) ? 1u : 0u;
    return chain;
}

EnginePlayerLookup LastEnginePlayerLookupForProbe() noexcept {
    EnginePlayerLookup result{};
    result.firstElement = g_EnginePlayerLookupFirst.load(std::memory_order_relaxed);
    result.second = g_EnginePlayerLookupVtable.load(std::memory_order_relaxed);
    result.count = g_EnginePlayerLookupCount.load(std::memory_order_relaxed);
    result.stage = g_EnginePlayerLookupStage.load(std::memory_order_acquire);
    result.resolverBegin = g_ResolverBegin.load(std::memory_order_relaxed);
    result.resolverEnd = g_ResolverEnd.load(std::memory_order_relaxed);
    return result;
}

namespace {

// --- 安全 stub（返回安全默认值，每成员每会话告警一次）-------------------------

// PC 返回的是一个集合；空表是最安全的近似——Mod 遍历它得到 0 个元素，而不是报错。
// --- 房间实体查询（批次 4，真实现）---------------------------------------------
//
// 两个查询都返回**Lua 数组**（整数键 `1..n`）。拿不到容器 / 指纹不匹配 / 参数不可用时返回
// **空表**，不抛 Lua 错误 —— 与 `Isaac.GetPlayer`/`GetItemConfig` 同一口径；`FindInRadius`
// 在 EID 里是**每帧**都调的（`main.lua:1466`），报错会直接打断渲染。

// 扫描一个实体并判断它是否命中"半径 + 分区掩码"。
bool EntityMatchesRadiusQuery(std::uintptr_t address, double x, double y, double radius,
                              std::uint32_t mask) {
    EntitySnapshot snapshot{};
    if (!CaptureEntity(address, &snapshot)) {
        return false;
    }
    if (EntityHasNoQueryFlag(snapshot)) {
        return false;
    }
    if ((ClassifyEntity(snapshot) & mask) == 0) {
        return false;
    }
    return EntityMatchesRadius(snapshot, x, y, radius);
}

// `Isaac.FindInRadius(Vector Position, float Radius, EntityPartition Partitions = 0xFFFFFFFF)`
// （`Isaac.md:125`）。分区 → 数据源：
//   * 普通分区（FAMILIAR/BULLET/TEAR/ENEMY/PICKUP）→ 活表 `EL+0x78`；
//   * PLAYER → `Game+0x25C50` 的玩家向量（引擎自己也不走 CellSpace）；
//   * EFFECT → `EL+0xA8` 表。
// 三段都过同一套半径/掩码判据，并用"已推出指针集合"去重（同一实体可能同时出现在玩家向量与
// 活表里）。**不排序**（PC 文档明说"not sorted by nearest first"，按实体加载顺序返回）。
int IsaacFindInRadius(lua_State* state) {
    RecordApiSequence(18U);
    const int argumentCount = lua_gettop(state);
    auto* position = static_cast<VectorHandle*>(luaL_testudata(state, 1, kVectorMetatable));
    bool valid = position != nullptr && argumentCount >= 2 && argumentCount <= 3 &&
                 lua_isnumber(state, 2) != 0;
    double x = 0.0;
    double y = 0.0;
    double radius = 0.0;
    if (valid) {
        x = static_cast<double>(position->x);
        y = static_cast<double>(position->y);
        radius = static_cast<double>(lua_tonumber(state, 2));
        valid = std::isfinite(radius) && radius >= 0.0;
    }
    std::uint32_t mask = 0xFFFFFFFFU;  // PC 的默认值 = "全部分区"（`Isaac.md:125`）
    if (valid && argumentCount == 3) {
        lua_Integer requested = 0;
        if (!ReadIntegerArgument(state, 3, &requested)) {
            valid = false;
        } else {
            mask = static_cast<std::uint32_t>(requested);
        }
    }
    g_FindInRadiusCalls.fetch_add(1, std::memory_order_relaxed);
    g_FindInRadiusLastMask.store(mask, std::memory_order_relaxed);
    g_FindInRadiusLastRadius.store(
        static_cast<std::uint32_t>(radius) & 0xFFFFFFU, std::memory_order_relaxed);
    g_FindInRadiusResults.store(0, std::memory_order_relaxed);
    lua_newtable(state);
    const int resultIndex = lua_gettop(state);
    ResetQueryDedup();
    if (!valid || mask == 0) {
        return 1;
    }
    const std::uintptr_t room = ResolveRoom();
    g_FindInRadiusRoomOk.store(room != 0 ? 1U : 0U, std::memory_order_relaxed);
    if (room == 0 || !EntityListFingerprintMatches(room + kRoomEntityListOffset)) {
        g_FindInRadiusFingerprint.store(0, std::memory_order_relaxed);
        return 1;
    }
    g_FindInRadiusFingerprint.store(1, std::memory_order_relaxed);
    const std::uintptr_t list = room + kRoomEntityListOffset;
    QuerySink sink{state, resultIndex, 0};
    const auto consider = [&](std::uintptr_t entity) {
        if (EntityMatchesRadiusQuery(entity, x, y, radius, mask)) {
            PushQueryResult(&sink, entity);
        }
    };
    // 探针读数：三个数据源各自命中多少（`QuerySink::emitted` 是已写入 Lua 表的条数）。
    if ((mask & kLiveTablePartitions) != 0) {
        const int before = sink.emitted;
        ForEachEntityInTable(list, kLiveEntityTable, consider);
        g_FindInRadiusLive.store(static_cast<std::uint32_t>(sink.emitted - before),
                                 std::memory_order_relaxed);
    }
    if ((mask & kPartitionPlayer) != 0) {
        const int before = sink.emitted;
        ForEachPlayerEntity(ResolveGameAddress(), consider);
        g_FindInRadiusPlayers.store(static_cast<std::uint32_t>(sink.emitted - before),
                                    std::memory_order_relaxed);
    }
    if ((mask & kPartitionEffect) != 0) {
        const int before = sink.emitted;
        ForEachEntityInTable(list, kEffectEntityTable, consider);
        g_FindInRadiusEffects.store(static_cast<std::uint32_t>(sink.emitted - before),
                                    std::memory_order_relaxed);
    }
    g_FindInRadiusResults.store(static_cast<std::uint32_t>(sink.emitted),
                               std::memory_order_relaxed);
    return 1;
}

// `Isaac.FindByType(EntityType Type, int Variant = -1, int SubType = -1, boolean Cache = false,
// boolean IgnoreFriendly = false)`（`Isaac.md:118`）：`Variant`/`SubType` 传 -1 表示通配。
//
// ★ **第 4 个参数是 `Cache`，不是 `Nearest`。** AB+/旧文档把它写成 `Nearest`（"只返回最近
// 一个"），忏悔版（本 Runtime 的目标版本）已经改成纯粹的性能提示（`analysis/isaacdocs-snapshot/
// docs/Isaac.md:118`）。EID 就是按忏悔版写的：`Isaac.FindByType(5, 100, -1, true, false)`
// （`main.lua:366`/`826`/`1133`）要拿**房间里所有**收藏品底座，`EntityType.ENTITY_EFFECT`
// 那一组（`main.lua:1468`）要拿某个效果变体的**所有**实例。所以这里接受该参数但**忽略**它
// （我们每次都直接遍历，没有缓存可言），恒返回全部命中项 —— 按 `Nearest` 实现会让 EID
// 每个变体只剩一个实体，是明确的功能回退。
//
// 第 5 个参数 `IgnoreFriendly` 同样接受但忽略：判"友好"的旗标位没有任何证据
// （`Entity+0x1B8` 里只有 bit3/bit46 有解释），编一个判据比忽略它更糟。
//
// 排除 `FLAG_NO_QUERY`（`+0x1B8` bit46）：PC 文档（`Isaac.md:121`）"If an entity has
// EntityFlag.FLAG_NO_QUERY then it will be excluded from the results"。
int IsaacFindByType(lua_State* state) {
    RecordApiSequence(19U);
    const int argumentCount = lua_gettop(state);
    lua_Integer type = 0;
    lua_Integer variant = -1;
    lua_Integer subType = -1;
    bool valid = argumentCount >= 1 && argumentCount <= 5 &&
                 ReadIntegerArgument(state, 1, &type);
    if (valid && argumentCount >= 2) {
        valid = ReadIntegerArgument(state, 2, &variant);
    }
    if (valid && argumentCount >= 3) {
        valid = ReadIntegerArgument(state, 3, &subType);
    }
    lua_newtable(state);
    const int resultIndex = lua_gettop(state);
    ResetQueryDedup();
    // `type < 0` 永远不匹配（`Entity.Type` 是无符号枚举），与 PC 的比较语义一致。
    if (!valid || type < 0) {
        return 1;
    }
    const std::uintptr_t room = ResolveRoom();
    if (room == 0 || !EntityListFingerprintMatches(room + kRoomEntityListOffset)) {
        return 1;
    }
    const std::uintptr_t list = room + kRoomEntityListOffset;
    QuerySink sink{state, resultIndex, 0};
    const auto matches = [&](const EntitySnapshot& snapshot) {
        if (EntityHasNoQueryFlag(snapshot)) {
            return false;
        }
        if (static_cast<lua_Integer>(snapshot.type) != type) {
            return false;
        }
        if (variant >= 0 && static_cast<lua_Integer>(snapshot.variant) != variant) {
            return false;
        }
        if (subType >= 0 && static_cast<lua_Integer>(snapshot.subType) != subType) {
            return false;
        }
        return true;
    };
    const auto consider = [&](std::uintptr_t entity) {
        EntitySnapshot snapshot{};
        if (CaptureEntity(entity, &snapshot) && matches(snapshot)) {
            PushQueryResult(&sink, entity);
        }
    };
    // 三张表都扫（去重）：EFFECT 在 `EL+0xA8`，玩家在玩家向量里，其余在活表。
    ForEachEntityInTable(list, kLiveEntityTable, consider);
    ForEachPlayerEntity(ResolveGameAddress(), consider);
    ForEachEntityInTable(list, kEffectEntityTable, consider);
    return 1;
}

// `Isaac.CountEnemies()`（`Isaac.md:66`）：活表里 `IsEnemy = (u32)(Type - 10) < 0x3DE` 的实体数。
//
// 已知偏差（写在这里而不是藏起来）：引擎 `EntityList::collide()` 的 ENEMY 分区还包含
// "被魅惑的跟班"（`Type 3 && Variant 0xEF`）与炸弹（`Type 4`），而任务给定的 `CountEnemies`
// 判据只有那条类型区间比较 —— 本实现照抄后者。`FLAG_NO_QUERY` 也**不**参与计数
// （没有证据说明引擎的 `CountType` 会跳过它）。
int IsaacCountEnemies(lua_State* state) {
    RecordApiSequence(20U);
    std::int64_t count = 0;
    // 探针：EID 的 `OnRender` 用 `Isaac.CountEnemies() > 0`（配合 `HideInBattle`）决定是否提前
    // 返回；"描述一个字都没画"必须能看出这个读数是多少。
    struct CountRecorder {
        std::int64_t* target;
        ~CountRecorder() {
            g_LastCountEnemies.store(static_cast<std::uint32_t>(*target),
                                     std::memory_order_relaxed);
        }
    } recorder{&count};
    const std::uintptr_t room = ResolveRoom();
    if (room != 0 && EntityListFingerprintMatches(room + kRoomEntityListOffset)) {
        ForEachEntityInTable(room + kRoomEntityListOffset, kLiveEntityTable,
                             [&count](std::uintptr_t entity) {
                                 EntitySnapshot snapshot{};
                                 if (CaptureEntity(entity, &snapshot) &&
                                     IsEnemyType(snapshot.type)) {
                                     ++count;
                                 }
                             });
    }
    lua_pushinteger(state, static_cast<lua_Integer>(count));
    return 1;
}

// `Isaac.CountBosses()`：**仍然是安全 stub（恒 0 + 每会话告警一次）**。
//
// 为什么这一条没做：boss 判据在引擎里是 `Entity_NPC` 上的一条虚函数/旗标，本轮拿到的
// 证据里**没有**任何一条能判定"这张实体是不是 boss"（`EntityList::collide()` 的 ENEMY 是
// 一整片 `Type` 区间，`Entity+0x1B8` 只有 bit3/bit46 有解释，vtable 槽位与重定位表逐槽
// 还原的工作也没做过）。凭一个猜的判据返回数字，比返回 0 更糟：`Isaac.CountBosses() > 0`
// 在 Mod 里是用来切"boss 房"逻辑的。
int IsaacCountBosses(lua_State* state) {
    RecordApiSequence(21U);
    WarnStubOnce(state, Stub::CountBosses, "Isaac.CountBosses");
    PushZero(state);
    return 1;
}

int IsaacWorldToScreen(lua_State* state) {
    return WorldToIdentity(state, Stub::WorldToScreen, "Isaac.WorldToScreen");
}

int IsaacWorldToRenderPosition(lua_State* state) {
    return WorldToIdentity(state, Stub::WorldToRenderPosition, "Isaac.WorldToRenderPosition");
}

int IsaacGetPersistentGameData(lua_State* state) {
    WarnStubOnce(state, Stub::GetPersistentGameData, "Isaac.GetPersistentGameData");
    PushZero(state);
    return 1;
}

int IsaacGetTrinketIdByName(lua_State* state) {
    WarnStubOnce(state, Stub::GetTrinketIdByName, "Isaac.GetTrinketIdByName");
    PushZero(state);
    return 1;
}

// `Isaac.GetCallbacks(callbackId[, createIfMissing])`：PC 返回"登记在 callbackId 下的回调表"
// （`analysis/isaacdocs-snapshot/docs/Isaac.md:138`），条目是
// `{ Function = <回调函数>, Mod = <Mod 对象> }`。
//
// 批次 3 起是真实现：
//   * 字符串名 → 命名回调表（`Mod:AddCallback("SOMENAME", fn)` 登记的那些）。
//     EID 的 `features/eid_bagofcrafting_search.lua:49` 与
//     `features/eid_language_manager.lua:81` 就是这个用法；返回空表虽然"不报错"，但会让
//     EID 的名字转换回调永远收不到事件（静默功能缺失），所以这里如实返回。
//   * 数字 id → 我们自己的 `CallbackRegistry`（只含已登记的 (id, owner) 对）。
//
// **永远返回表**（没有登记时是空表）：PC 的同名调用返回 nil 会让 `pairs(...)` 直接报错，
// 而"没有回调"在 PC 上也是空表。第二个参数（`createIfMissing`）我们接受但忽略——
// 我们本来就会为缺失的名字返回一张空表。
// （本函数原先是安全 stub；批次 3 从 stub 名单里移除，见 `Stub` 枚举的注释。）
int IsaacGetCallbacks(lua_State* state) {
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 1 || argumentCount > 2) {
        return luaL_error(state, "Isaac.GetCallbacks accepts a callback id and an optional flag");
    }
    if (lua_type(state, 1) == LUA_TSTRING) {
        PushNamedCallbacks(state, lua_tostring(state, 1));
        return 1;
    }
    if (!lua_isinteger(state, 1)) {
        return luaL_error(state, "Isaac.GetCallbacks expects a callback id");
    }
    const lua_Integer requested = lua_tointegerx(state, 1, nullptr);
    if (requested < 0 ||
        requested > static_cast<lua_Integer>(std::numeric_limits<CallbackId>::max())) {
        lua_newtable(state);
        return 1;
    }
    const CallbackId id = static_cast<CallbackId>(requested);
    CallbackRegistry& registry = LuaRuntime::ManagedCallbackRegistry();
    const std::size_t registrations = registry.CountOf(id);
    lua_newtable(state);
    std::size_t ordinal = 0;
    for (std::size_t index = 0; index < registrations; ++index) {
        const CallbackDescriptor* descriptor = registry.At(id, index);
        if (descriptor == nullptr || !descriptor->valid()) {
            continue;
        }
        lua_newtable(state);
        lua_rawgeti(state, LUA_REGISTRYINDEX, descriptor->luaReference);
        lua_setfield(state, -2, "Function");
        lua_rawgeti(state, LUA_REGISTRYINDEX, descriptor->modReference);
        lua_setfield(state, -2, "Mod");
        // 栈上是 [结果表, 条目]：条目在 -1、结果表在 -2，所以按下标 -2 追加。
        lua_rawseti(state, -2, static_cast<lua_Integer>(++ordinal));
    }
    return 1;
}

// `Isaac.LoadModData` / `Isaac.SaveModData` 在 PC 上是 `Isaac` 这一层上的持久化入口；
// 我们的持久化走 `Mod:SaveData`/`Mod:LoadData`，还没打通到 `Isaac` 这一层，
// 所以这里不报错、也不假装存下了数据。
int IsaacLoadModData(lua_State* state) {
    WarnStubOnce(state, Stub::LoadModData, "Isaac.LoadModData");
    return 0;
}

int IsaacSaveModData(lua_State* state) {
    WarnStubOnce(state, Stub::SaveModData, "Isaac.SaveModData");
    return 0;
}

// HUD 文本渲染未实现（需要先定位渲染入口与字体参数的 ABI），调用它只会告警一次。
int IsaacRenderScaledText(lua_State* state) {
    WarnStubOnce(state, Stub::RenderScaledText, "Isaac.RenderScaledText");
    return 0;
}

// --- 全局函数（批次 4）----------------------------------------------------------

// `GetPtrHash(object)`：PC 的"把对象指针变成稳定整数"（`GlobalFunctions.md:143`），
// EID 用它做实体身份比较（`main.lua:492`/`527`/`1473`、`features/eid_api.lua:2387`）。
//
// 我们只对**持有原生指针**的三种句柄（`Entity`/`EntityPlayer`/`EntityPickup`）返回实体地址；
// `Game`/`Level`/`Room`/`Sprite`/`RNG` 等句柄要么是占位符、要么是 Runtime 自己的对象，PC 里
// 它们的 hash 也不是"引擎对象地址"，所以这里返回 0（= "这个对象没有可比较的指针"），
// **不报错**：EID 的用法是 `GetPtrHash(a) == GetPtrHash(b)`，0 只会让比较为真/假，不会打断。
int GlobalGetPtrHash(lua_State* state) {
    RecordFilterChain(5U);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "GetPtrHash accepts one object");
    }
    for (const char* metatable : {kEntityMetatable, kEntityPlayerMetatable, kEntityPickupMetatable}) {
        auto* handle = static_cast<EntityHandle*>(luaL_testudata(state, 1, metatable));
        if (handle == nullptr) {
            continue;
        }
        const std::uintptr_t entity = ValidatedEntity(handle);
        lua_pushinteger(state, entity == 0 ? 0
                                          : static_cast<lua_Integer>(entity));
        return 1;
    }
    PushZero(state);
    return 1;
}

} // namespace

// ============================================================================
// 字段读取型 API：共享处理器 + 数据行（门禁三期成本压缩，机制说明见 `field_api.hpp`）
// ============================================================================
namespace {

//: 从 Lua 栈上第 `index` 个参数解出接收者对象地址（无效/不可读时返回 0）。
std::uintptr_t ResolveFieldReceipt(lua_State* state, FieldReceipt receipt, int index) {
    switch (receipt) {
        case FieldReceipt::Entity: {
            auto* handle = CheckEntityHandle(state, index);
            return ValidatedEntity(handle);
        }
        case FieldReceipt::EntityPlayer: {
            auto* handle = CheckEntityHandle(state, index);
            return ValidatedEntityPlayer(handle);
        }
        case FieldReceipt::ItemConfigItem: {
            auto* handle = CheckItemConfigItemHandle(state, index);
            return ValidatedItem(handle);
        }
        case FieldReceipt::Level: {
            // `Level` 内嵌在 `Game` 起始处，解析链与 `Room`/描述符那条**逐字相同**：
            // `g_Game` 槽是"槽的地址"（指针变量）→ 解一次得 owner → 再解一次才是 `Game*`。
            // 少解一层会去读模块里的代码字节（真机探针上踩过同一个坑）。
            const std::uintptr_t moduleBase = EngineModuleBase();
            if (moduleBase == 0 || moduleBase > UINTPTR_MAX - kGameOwnerGlobalSlotOffset) {
                return 0;
            }
            const std::uintptr_t slot = moduleBase + kGameOwnerGlobalSlotOffset;
            if (!IsEngineMemoryReadable(slot, sizeof(std::uintptr_t))) {
                return 0;
            }
            std::uintptr_t owner = 0;
            if (!ReadEngine(slot, &owner) || owner == 0 ||
                !IsEngineMemoryReadable(owner, sizeof(std::uintptr_t))) {
                return 0;
            }
            std::uintptr_t level = 0;
            if (!ReadEngine(owner, &level)) {
                return 0;
            }
            return level;
        }
    }
    return 0;
}

void PushMissingFieldValue(lua_State* state, FieldMissing missing) {
    switch (missing) {
        case FieldMissing::Nil:
            lua_pushnil(state);
            return;
        case FieldMissing::False:
            lua_pushboolean(state, 0);
            return;
        case FieldMissing::MinusOne:
            lua_pushinteger(state, -1);
            return;
        case FieldMissing::Zero:
        default:
            PushZero(state);
            return;
    }
}

//: `VectorCount` 的合理上限：正常容器（收藏品/饰品/卡片…）都是几十个元素的量级。
//: 超过它说明 begin/end 读到的不是一对真指针（内存不可信），按"读不到"降级而不是返回巨大数字。
constexpr std::uintptr_t kFieldVectorCountMaximum = 4096;

//: `Isaac` 族（含 `Entity`/`EntityPlayer`/`ItemConfig_Item`）的字段读取型 API。
//: **家族的绑定行必须住在自己的 TU 里**（契约门禁会逐条核对），所以 `Level` 的行表在
//: `remaining_api.cpp`（`kLevelFieldApis`）——共享处理器是同一个 `FieldApiHandler`。
//:
//: 每一行 = 一个 API，**0 字节代码**；手写同类方法实测 236–312 字节。
//: 只收"无参数、接收者+固定偏移、读不到按族约定降级"的形状；带参数或需要分支的（例如
//: `GetEffectiveMaxHearts` 要按 `PlayerType` 决定是否算骨心）继续手写，不硬塞。
constexpr FieldApiRow kFieldApis[] = {
    // `EntityPlayer:GetPlayerType()`：`+0x1738`。读不到给 nil（`== PlayerType.ISAAC` 在 nil
    // 上自然为假，假值会让 Mod 走进错误分支）；探针位 1 与原先手写实现保持一致。
    {0x0E010016, kEntityPlayerTypeOffset, 0, FieldKind::U32, FieldMissing::Nil,
     FieldReceipt::EntityPlayer, 1},
    // `EntityPlayer:GetSoulHearts()`：`+0x16C4`，半心为单位（PC 文档：1 单位 = 半颗心）。
    {0x0E010021, kEntityPlayerSoulHeartsOffset, 0, FieldKind::U32, FieldMissing::Zero,
     FieldReceipt::EntityPlayer, 0xFF},
    // `EntityPlayer:GetBrokenHearts()`：`+0x2470`。PC 文档明确它**不**翻倍。
    {0x0E010022, kEntityPlayerBrokenHeartsOffset, 0, FieldKind::U32, FieldMissing::Zero,
     FieldReceipt::EntityPlayer, 0xFF},
    // `EntityPlayer:GetMaxHearts()`：`+0x16B8`（红心**容器**，1 单位 = 半颗心容器 ——
    // PC 文档 `EntityPlayer.md` 的 `GetMaxHearts` 原文）。原先这里返回硬编码 0，
    // 属于"有实现但语义偏弱"，现在按已确认偏移真读。
    {0x0E01003B, kEntityPlayerRedHeartContainersOffset, 0, FieldKind::U32, FieldMissing::Zero,
     FieldReceipt::EntityPlayer, 0xFF},
    // `EntityPlayer:GetBabySkin()`：`+0x20E8`，**有符号**，非婴儿 = `-1`（读不到也给 -1：
    // 编一个非负值会让 Mod 走进"这是婴儿"的分支去查一套不存在的皮肤）。
    {0x0E010028, kEntityPlayerBabySkinOffset, 0, FieldKind::I32, FieldMissing::MinusOne,
     FieldReceipt::EntityPlayer, 0xFF},
    // --- 批次 7（2026-09-15）：EID 用到、而运行时里**完全没有**的两条 -----------------
    // `EntityPlayer:GetCollectibleCount()`：**按 id 的计数数组求和**（`+0x1AB8`/`+0x1AC0`
    // 是 begin/end 这一对指针，每格 4 字节 = "该收藏品有几件"）。
    //
    // ★ 这里原先按"容器格子数"实现，**真机验收当场证伪**（2026-09-16）：它返回 733，
    // 而 733 只是数组长度（Repentance 的收藏品 id 空间），玩家实际只有 1 件（D6，id 105）。
    // 口径依据：PC 侧另有一条 `EntityPlayer:GetCollectibleNum(id)`（按 id 数），
    // 本项目的它与这张数组的第 id 格同源 ⇒ `GetCollectibleCount()` 应当是**总件数**。
    {0x0E01004F, kEntityPlayerCollectibleBeginOffset, static_cast<std::uint32_t>(
         kEntityPlayerCollectibleEndOffset), FieldKind::SumU32Array, FieldMissing::Zero,
     FieldReceipt::EntityPlayer, 0xFF},
    // `ItemConfig_Item:IsTrinket()`：`Type == ITEM_TRINKET`（`kItemTypeTrinket = 2`）。
    // `offset2` 在这里是"要比较的常量"，不是第二个偏移 —— 见 `FieldKind::BoolEquals`。
    {0x0E010050, kItemConfigItemTypeOffset, kItemTypeTrinket, FieldKind::BoolEquals,
     FieldMissing::False, FieldReceipt::ItemConfigItem, 0xFF},
};

} // namespace

const FieldApiRow* FieldApiRows(std::size_t* count) noexcept {
    if (count != nullptr) {
        *count = sizeof(kFieldApis) / sizeof(kFieldApis[0]);
    }
    return kFieldApis;
}

int FieldApiHandler(lua_State* state) {
    const auto* row =
        static_cast<const FieldApiRow*>(lua_touserdata(state, lua_upvalueindex(1)));
    if (row == nullptr) {
        return luaL_error(state, "field api row missing");
    }
    if (row->probeBit != 0xFF) {
        RecordApiSequenceSecondary(row->probeBit);
    }
    if (lua_gettop(state) != 1) {
        // 消息按 catalog 里的 `owner:name` 现拼，与手写处理器逐字一致
        // （例如 `"EntityPlayer:GetSoulHearts accepts no arguments"`）。只在出错路径上拼。
        const LuaApiDescriptor* descriptor = ApiCatalog::Default().Find(row->id);
        return luaL_error(state, "%s:%s accepts no arguments",
                          descriptor != nullptr ? descriptor->owner : "?",
                          descriptor != nullptr ? descriptor->name : "?");
    }
    const std::uintptr_t receiver = ResolveFieldReceipt(state, row->receipt, 1);
    if (receiver == 0) {
        PushMissingFieldValue(state, row->missing);
        return 1;
    }
    switch (row->kind) {
        case FieldKind::I32: {
            std::int32_t value = 0;
            if (!ReadEngine(receiver + row->offset, &value)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(value));
            return 1;
        }
        case FieldKind::Bool: {
            std::uint8_t value = 0;
            if (!ReadEngine(receiver + row->offset, &value)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushboolean(state, value != 0 ? 1 : 0);
            return 1;
        }
        case FieldKind::Sum2U32: {
            std::uint32_t first = 0;
            std::uint32_t second = 0;
            if (!ReadEngine(receiver + row->offset, &first) ||
                !ReadEngine(receiver + row->offset2, &second)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(first + second));
            return 1;
        }
        case FieldKind::BoolEquals: {
            // `offset2` 在这里是"要比较的常量"（例如 `ITEM_TRINKET`），不是第二个偏移。
            std::uint32_t value = 0;
            if (!ReadEngine(receiver + row->offset, &value)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushboolean(state, value == row->offset2 ? 1 : 0);
            return 1;
        }
        case FieldKind::VectorCount: {
            // `offset`/`offset2` 是一对 `T*`（begin/end），元素数是 `(end - begin) / sizeof(T)`。
            std::uintptr_t begin = 0;
            std::uintptr_t end = 0;
            if (!ReadEngine(receiver + row->offset, &begin) ||
                !ReadEngine(receiver + row->offset2, &end)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            // 空容器在引擎里就是 `begin == end`；`end < begin` 或跨度离谱说明内存不可信，
            // 一律按"读不到"降级 —— 编一个巨大数字会让 Mod 走进错误分支。
            if (end < begin) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            const std::uintptr_t span = end - begin;
            const std::uintptr_t stride = sizeof(std::uint32_t);
            if (span / stride > kFieldVectorCountMaximum) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(span / stride));
            return 1;
        }
        case FieldKind::BoolNonZero: {
            // 读一个 `u32`，"非零即真"。`Level:IsAltStage()` 就是这一条：
            // PC 契约（`Level.md`）= "StageType 不是 STAGETYPE_ORIGINAL(0)"。
            std::uint32_t value = 0;
            if (!ReadEngine(receiver + row->offset, &value)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushboolean(state, value != 0 ? 1 : 0);
            return 1;
        }
        case FieldKind::SumU32Array: {
            // `offset`/`offset2` 是一对 `u32[]` 的 begin/end，把每一格**相加**。
            //
            // 为什么是求和而不是"数非零格子"：这张数组是"**按收藏品 id 的计数**"，
            // 而 PC 侧另有一条 `EntityPlayer:GetCollectibleNum(id)`（按 id 数）——
            // 本项目的 `GetCollectibleNum` 读的就是同一张数组的第 id 格。
            // 既然"按 id 的数量"已经有独立 API，`GetCollectibleCount()` 就应当是**总件数**。
            // 真机证据（2026-09-16）：D6（id 105）= 1、其余为 0 ⇒ 求和 = 1、非零格子也 = 1，
            // 两种口径在这个场景下同值；口径的选择依据是上面那条 PC 契约，不是这个数值。
            std::uintptr_t begin = 0;
            std::uintptr_t end = 0;
            if (!ReadEngine(receiver + row->offset, &begin) ||
                !ReadEngine(receiver + row->offset2, &end)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            if (begin == 0 || end < begin || ((end - begin) % sizeof(std::uint32_t)) != 0 ||
                (end - begin) / sizeof(std::uint32_t) > kFieldVectorCountMaximum) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            std::uint64_t total = 0;
            for (std::uintptr_t address = begin; address < end; address += sizeof(std::uint32_t)) {
                std::uint32_t value = 0;
                if (!ReadEngine(address, &value)) {
                    PushMissingFieldValue(state, row->missing);
                    return 1;
                }
                total += value;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(total));
            return 1;
        }
        case FieldKind::U32:
        default: {
            std::uint32_t value = 0;
            if (!ReadEngine(receiver + row->offset, &value)) {
                PushMissingFieldValue(state, row->missing);
                return 1;
            }
            lua_pushinteger(state, static_cast<lua_Integer>(value));
            return 1;
        }
    }
}

// 引擎实体存活判据的导出实现（声明与理由见 `isaac_api.hpp`）：就是 `ValidatedEntity` 用的那一条，
// 不另写一份 —— `sprite_api.cpp` 的引擎实体 `Sprite` 句柄每次访问都要重新问一次。
bool IsLiveEntityPointer(std::uintptr_t candidate) noexcept {
    return IsEntityPointer(candidate);
}

std::size_t AttachIsaacMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kIsaacOwner, kIsaacHandlers, RowCount(kIsaacHandlers));
}

std::size_t AttachEntityMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kEntityOwner, kEntityHandlers, RowCount(kEntityHandlers));
}

std::size_t AttachEntityPlayerMethods(lua_State* state) noexcept {
    std::size_t fieldApiCount = 0;
    return AttachOwnerMethods(state, kEntityPlayerOwner, kEntityPlayerHandlers,
                              RowCount(kEntityPlayerHandlers), FieldApiRows(&fieldApiCount),
                              fieldApiCount);
}

std::size_t AttachEntityPickupMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kEntityPickupOwner, kEntityPickupHandlers,
                              RowCount(kEntityPickupHandlers));
}

std::size_t AttachItemConfigMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kItemConfigOwner, kItemConfigHandlers,
                              RowCount(kItemConfigHandlers));
}

std::size_t AttachItemConfigItemMethods(lua_State* state) noexcept {
    std::size_t fieldApiCount = 0;
    // 同一张数据行表也挂给 `ItemConfig_Item` 族：绑定循环按 id 匹配，不会串族
    // （行里的 `receipt` 决定用哪套接收者校验）。
    return AttachOwnerMethods(state, kItemConfigItemOwner, kItemConfigItemHandlers,
                              RowCount(kItemConfigItemHandlers), FieldApiRows(&fieldApiCount),
                              fieldApiCount);
}

#if !defined(__SWITCH__)
void SetEntityPlayerHasCollectibleHostImplementation(
    EntityPlayerHasCollectibleHostImplementation function) noexcept {
    g_HasCollectibleHostImplementation = function;
}

void SetCurrentLanguageCodeHostImplementation(
    CurrentLanguageCodeHostImplementation function) noexcept {
    g_CurrentLanguageCodeHostImplementation = function;
}

void SetItemConfigIsAvailableHostImplementation(
    ItemConfigIsAvailableHostImplementation function) noexcept {
    g_ItemConfigIsAvailableHostImplementation = function;
}
#endif

namespace {

// `Entity` / `EntityPlayer` / `EntityPickup` / `ItemConfig` / `ItemConfig_Item` 的元表。字段走
// `__index` 函数（字段名与方法名都在那里），方法表挂在元表的 `__methods` 上 —— 与 `Vector`
// 同一形态，理由相同：`__index` 必须是函数才能同时服务 `item.Name` 与 `item:HasTags(...)`。
//
// 这一段**建立元表**而不是像其它家族那样让 `lua_runtime.cpp` 建：本轮改动边界不允许再改旧 TU 的
// 注册区，所以家族入口自己建表（旧 TU 只调用 `RegisterIsaacApi`）。
// 全局 `ItemConfig` 表：PC 里它是一张**类表** —— `Isaac.GetItemConfig()` 给实例，而
// `ItemConfig.TAG_*` 是表上的常量（`Tags` 是位掩码）。
//
// 为什么必须补：EID 的 `features/eid_data.lua:1025` 第一件事就是
// `ItemConfig.TAG_GUPPY`，而我们此前只有实例元表、**没有这个全局**，于是整包加载在那一行
// 断掉（真机报告 `01789202729`：`...eid_data.lua:1025: attempt to index a nil value`）。
// EID 另外还用 `TAG_QUEST`/`TAG_NO_GREED`/`TAG_NO_CHALLENGE` 等（`main.lua:669`、`eid_api.lua:2031`）。
//
// 常量值直接取自 `analysis/isaacdocs-snapshot/docs/enums/ItemConfig.md`（`1 << N`），
// `runtime/tests/test_lua_item_config.py` 会拿那份文档逐条核对，防止这里抄错。
struct ItemConfigConstant {
    const char* name;
    std::uint32_t bit;
};

constexpr ItemConfigConstant kItemConfigTagConstants[] = {
    ItemConfigConstant{"TAG_DEAD", 0},
    ItemConfigConstant{"TAG_SYRINGE", 1},
    ItemConfigConstant{"TAG_MOM", 2},
    ItemConfigConstant{"TAG_TECH", 3},
    ItemConfigConstant{"TAG_BATTERY", 4},
    ItemConfigConstant{"TAG_GUPPY", 5},
    ItemConfigConstant{"TAG_FLY", 6},
    ItemConfigConstant{"TAG_BOB", 7},
    ItemConfigConstant{"TAG_MUSHROOM", 8},
    ItemConfigConstant{"TAG_BABY", 9},
    ItemConfigConstant{"TAG_ANGEL", 10},
    ItemConfigConstant{"TAG_DEVIL", 11},
    ItemConfigConstant{"TAG_POOP", 12},
    ItemConfigConstant{"TAG_BOOK", 13},
    ItemConfigConstant{"TAG_SPIDER", 14},
    ItemConfigConstant{"TAG_QUEST", 15},
    ItemConfigConstant{"TAG_MONSTER_MANUAL", 16},
    ItemConfigConstant{"TAG_NO_GREED", 17},
    ItemConfigConstant{"TAG_FOOD", 18},
    ItemConfigConstant{"TAG_TEARS_UP", 19},
    ItemConfigConstant{"TAG_OFFENSIVE", 20},
    ItemConfigConstant{"TAG_NO_KEEPER", 21},
    ItemConfigConstant{"TAG_NO_LOST_BR", 22},
    ItemConfigConstant{"TAG_STARS", 23},
    ItemConfigConstant{"TAG_SUMMONABLE", 24},
    ItemConfigConstant{"TAG_NO_CANTRIP", 25},
    ItemConfigConstant{"TAG_WISP", 26},
    ItemConfigConstant{"TAG_UNIQUE_FAMILIAR", 27},
    ItemConfigConstant{"TAG_NO_CHALLENGE", 28},
    ItemConfigConstant{"TAG_NO_DAILY", 29},
    ItemConfigConstant{"TAG_LAZ_SHARED", 30},
    ItemConfigConstant{"TAG_LAZ_SHARED_GLOBAL", 31},
    ItemConfigConstant{"TAG_NO_EDEN", 32},
};

void PublishItemConfigConstants(lua_State* state) {
    lua_newtable(state);
    for (const ItemConfigConstant& constant : kItemConfigTagConstants) {
        // `lua_Integer` 是 64 位，`1 << 32`（`TAG_NO_EDEN`）不会溢出。
        lua_pushinteger(state, static_cast<lua_Integer>(1) << constant.bit);
        lua_setfield(state, -2, constant.name);
    }
    lua_setglobal(state, "ItemConfig");
}

void RegisterViewMetatables(lua_State* state) {
    luaL_newmetatable(state, kEntityMetatable);
    lua_newtable(state);
    static_cast<void>(AttachEntityMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pushcfunction(state, EntityIndex);
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kEntityPlayerMetatable);
    lua_newtable(state);
    static_cast<void>(AttachEntityPlayerMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pushcfunction(state, EntityPlayerIndex);
    lua_setfield(state, -2, "__index");
    // 写入口（2026-09-16）：见 `EntityNewIndex` 的注释（目前只放行 `ControlsCooldown`）。
    // 基类 `Entity` 的元表**故意没有** `__newindex` —— PC 里也没有可写的基类字段，
    // 让它保持"赋值即报错"，比给一个什么都接受的写入口安全。
    lua_pushcfunction(state, EntityNewIndex);
    lua_setfield(state, -2, "__newindex");
    lua_pop(state, 1);

    // `EntityPickup`（批次 4）：没有自己的 catalog 条目（它的成员只有字段），方法表留空，
    // `__index` 回落到 `Entity` 的（PC 里 `EntityPickup` 就是 `Entity` 的子类）。
    luaL_newmetatable(state, kEntityPickupMetatable);
    lua_newtable(state);
    static_cast<void>(AttachEntityPickupMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pushcfunction(state, EntityPickupIndex);
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kItemConfigMetatable);
    lua_newtable(state);
    static_cast<void>(AttachItemConfigMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pushcfunction(state, ItemConfigIndex);
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);

    luaL_newmetatable(state, kItemConfigItemMetatable);
    lua_newtable(state);
    static_cast<void>(AttachItemConfigItemMethods(state));
    lua_setfield(state, -2, "__methods");
    lua_pushcfunction(state, ItemConfigItemIndex);
    lua_setfield(state, -2, "__index");
    lua_pop(state, 1);
}

} // namespace

std::size_t RegisterIsaacApi(lua_State* state) noexcept {
    if (state == nullptr) {
        return 0;
    }
    // 每个 Lua 状态创建时调用一次：stub 告警与帧计数都是"每会话"口径，在这里归零
    // （实体 `GetData` 的 Lua 表存储也在这里清空，见 `ResetEntityDataStore`）。
    ResetSessionState();
    // 只读视图的元表要先建好：`Isaac.GetPlayer` / `Isaac.GetItemConfig` 一旦返回 userdata，
    // 元表就必须已经存在。
    RegisterViewMetatables(state);
    // 全局 `GetPtrHash`：PC 的全局函数（`GlobalFunctions.md:143`），EID 在实体身份比较里用。
    // 它**不是**某个对象的方法，所以不进 `AttachOwnerMethods`（catalog 里 owner 是 `Global`）。
    lua_pushcfunction(state, GlobalGetPtrHash);
    lua_setglobal(state, "GetPtrHash");
    // 全局 `ItemConfig` 常量表（`ItemConfig.TAG_*`）。理由见 `PublishItemConfigConstants`。
    PublishItemConfigConstants(state);
    lua_newtable(state);
    const std::size_t attached = AttachIsaacMethods(state);
    lua_setglobal(state, "Isaac");
    return attached;
}

namespace {

bool IsLanguageCodeByte(char value) noexcept {
    return (value >= 'a' && value <= 'z') || (value >= '0' && value <= '9') || value == '_';
}

bool ReadLanguageCodeString(std::uintptr_t address, char* output, std::size_t capacity) noexcept {
    if (address == 0 || output == nullptr || capacity < 2) {
        return false;
    }
    for (std::size_t index = 0; index + 1 < capacity; ++index) {
        char value = 0;
        if (!IsEngineMemoryReadable(address + index, sizeof(value))) {
            return false;
        }
        std::memcpy(&value, reinterpret_cast<const void*>(address + index), sizeof(value));
        if (value == '\0') {
            output[index] = '\0';
            return index != 0;
        }
        if (!IsLanguageCodeByte(value)) {
            return false;
        }
        output[index] = value;
    }
    output[capacity - 1] = '\0';
    return output[0] != '\0';
}

bool ReadCurrentLanguageCode(char* output, std::size_t capacity) noexcept {
    if (output == nullptr || capacity < 2) {
        return false;
    }
#if !defined(__SWITCH__)
    if (g_CurrentLanguageCodeHostImplementation != nullptr) {
        const char* injected = g_CurrentLanguageCodeHostImplementation();
        if (injected == nullptr) {
            return false;
        }
        std::size_t length = 0;
        while (length + 1 < capacity && injected[length] != '\0' &&
               IsLanguageCodeByte(injected[length])) {
            output[length] = injected[length];
            ++length;
        }
        output[length] = '\0';
        return length != 0;
    }
#endif
    const std::uintptr_t base = EngineModuleBase();
    if (base == 0 || base > UINTPTR_MAX - kGameManagerGlobalSlotOffset ||
        base > UINTPTR_MAX - kManagerGetLanguageCodeOffset) {
        return false;
    }
    std::uintptr_t managerSlot = 0;
    std::uintptr_t manager = 0;
    if (!ReadEngine(base + kGameManagerGlobalSlotOffset, &managerSlot) || managerSlot == 0 ||
        !ReadEngine(managerSlot, &manager) || manager == 0) {
        return false;
    }
    const std::uintptr_t method = base + kManagerGetLanguageCodeOffset;
    std::array<u8, kManagerGetLanguageCodeExpectedBytes.size()> actual{};
    if (!ReadEngine(method, &actual) || actual != kManagerGetLanguageCodeExpectedBytes) {
        return false;
    }
    using GetLanguageCodeFn = const char* (*)(void*);
    const char* code =
        reinterpret_cast<GetLanguageCodeFn>(method)(reinterpret_cast<void*>(manager));
    return ReadLanguageCodeString(reinterpret_cast<std::uintptr_t>(code), output, capacity);
}

} // namespace

namespace {

int OptionsIndex(lua_State* state) noexcept {
    const char* key = lua_tostring(state, 2);
    if (key != nullptr && std::strcmp(key, "Language") == 0) {
        char languageCode[16] = {};
        if (!ReadCurrentLanguageCode(languageCode, sizeof(languageCode))) {
            std::memcpy(languageCode, "en", 3);
        }
        lua_pushstring(state, languageCode);
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

} // namespace

std::size_t RegisterOptionsTable(lua_State* state) noexcept {
    if (state == nullptr) {
        return 0;
    }
    lua_newtable(state);
    // PC 文档定义 `Options.HUDOffset` 为 0..1 的 float；EID 的定位公式以此为输入，默认 1.0
    // 表示不做额外偏移。此前误用 EID 配置里的 0..10 整数，导致描述被推到右下角。
    lua_pushnumber(state, 1.0);
    lua_setfield(state, -2, "HUDOffset");
    // `Language` 是只读动态字段：EID 在整个会话中会反复读取它，不能在初始化时缓存。
    // 引擎切换语言后，下一次 `Options.Language` 会重新调用 `Manager::GetLanguageCode()`。
    lua_newtable(state);
    lua_pushcfunction(state, OptionsIndex);
    lua_setfield(state, -2, "__index");
    lua_setmetatable(state, -2);
    lua_setglobal(state, "Options");
    return 2;
}

bool ShouldDispatchManagedCallbacks() noexcept {
#if defined(__SWITCH__) && !defined(EXL_DIAGNOSTIC_STAGE)
    if (EngineModuleBase() == 0 || ResolveRoom() == 0) {
        return false;
    }
    // 退房过渡期会出现“room 仍非空、player 已经失效”的窗口。只检查 room 会让 EID 进入该
    // 窗口并拿到 nil player；必须同时要求至少一个可验证玩家。
    const EnginePlayerArray players = ResolveEnginePlayerArray();
    return players.readable && players.count != 0;
#else
    return true;
#endif
}

// `Isaac.GetPlayer` 的成功/失败计数（原因 1）：EID 的 `EID.player` 依赖它；恒失败则
// `OnRender` 里的 `for ... in ipairs({ EID.player })` 一次都不执行，描述的第一步永不发生。


PlayerLookupProbe PlayerLookupProbeSnapshot() noexcept {
    PlayerLookupProbe probe{};
    probe.getPlayerSuccess = g_GetPlayerSuccess.load(std::memory_order_relaxed);
    probe.getPlayerFailure = g_GetPlayerFailure.load(std::memory_order_relaxed);
    return probe;
}

} // namespace isaac::runtime
