#pragma once

#include <array>
#include <cstdint>

// Lua userdata handles and metatable names for the object API families.
//
// They live in the legacy source root (not under `runtime/src`) because the
// non-layered/probe builds do not have the layered include path, and because
// `lua_runtime.cpp` still owns the metatable registration. The family
// translation units own the handlers that create and validate these objects.
namespace LuaRuntime {

inline constexpr char kGameMetatable[] = "IsaacRuntime.Game";
inline constexpr char kLevelMetatable[] = "IsaacRuntime.Level";
inline constexpr char kItemPoolMetatable[] = "IsaacRuntime.ItemPool";
inline constexpr char kRoomMetatable[] = "IsaacRuntime.Room";
inline constexpr char kSeedsMetatable[] = "IsaacRuntime.Seeds";
inline constexpr char kMusicMetatable[] = "IsaacRuntime.Music";
inline constexpr char kRngMetatable[] = "IsaacRuntime.RNG";
inline constexpr char kFontMetatable[] = "IsaacRuntime.Font";
inline constexpr char kColorMetatable[] = "IsaacRuntime.KColor";
// PC's `Vector` is `KAGE::Math::Vector2`: 8 bytes `{float X, float Y}`, no vtable (audited in
// `analysis/stage150-sprite-abi/`). Like `KColor` it is a pure value type, so the handle stores
// the value itself -- no engine object, and nothing has to be published from the hook.
inline constexpr char kVectorMetatable[] = "IsaacRuntime.Vector";
// Lua 的 `Sprite` = 引擎的 `IsaacRepentance::ANM2`（stage150 审计的类身份判定）。对象尺寸由
// `tools/stage155_anm2_size_audit.py` 用两种独立方法定案为 `0x158`（见 `runtime_constants.hpp`）。
inline constexpr char kSpriteMetatable[] = "IsaacRuntime.Sprite";
// `Entity` / `EntityPlayer`（批次 2）：`Isaac.GetPlayer(i)` 返回的只读实体视图。PC 里
// `EntityPlayer` 继承 `Entity`，所以这里也建两张元表：`Entity` 承载基类字段与方法，
// `EntityPlayer` 通过 `__index` 回落到它（见 `interfaces/lua/isaac_api.cpp`）。
inline constexpr char kEntityMetatable[] = "IsaacRuntime.Entity";
inline constexpr char kEntityPlayerMetatable[] = "IsaacRuntime.EntityPlayer";
// `EntityPickup`（批次 4）：`Entity:ToPickup()` 的返回值，PC 里它是 `Entity` 的子类。
// 句柄体复用 `EntityHandle`（都是"一个原生实体指针"），字段（`Price`/`ShopItemId`/
// `OptionsPickupIndex`/`ForceBlind`/`Touched`）走它自己的 `__index`。判据是 `Type == 5`
// **且** vptr == `base + 0xA36E28`（`Entity_Pickup` 的 vtable 唯一），见 `isaac_api.cpp`。
inline constexpr char kEntityPickupMetatable[] = "IsaacRuntime.EntityPickup";
// `ItemConfig` / `ItemConfig::Item`（批次 2b）：`Isaac.GetItemConfig()` 返回的只读配置视图。
// 两张元表：`ItemConfig` 承载 `GetCollectible`/`GetTrinket`/…，`ItemConfig_Item` 承载条目字段
// （`ID`/`Type`/`Name`/`Description`）与条目级方法（`HasTags`）。
inline constexpr char kItemConfigMetatable[] = "IsaacRuntime.ItemConfig";
inline constexpr char kItemConfigItemMetatable[] = "IsaacRuntime.ItemConfig_Item";

// The handle bodies are placeholders on purpose: the Lua layer must never hold
// a native Game/Level/Room pointer, so the object identity is resolved again
// inside each managed callback scope.
struct GameHandle {
    std::uint8_t reserved;
};

struct LevelHandle {
    std::uint8_t reserved;
};

struct ItemPoolHandle {
    std::uint8_t reserved;
};

struct RoomHandle {
    std::uint8_t reserved;
};

// `Seeds`（PC 的 `Game:GetSeeds()` 返回值）。句柄**不持有引擎指针**，只带一份自洽的种子快照：
// Switch 的 `IsaacRepentance::Seeds` 是 `Game` 里的一个成员对象，而它的字段偏移尚未定位
// （符号表里 `Seeds` 只有 `SetStartSeed`/`Reset`/`Seed2String` 这类方法，没有 `GetStartSeed`，
// 说明 PC 那条 Lua 访问器是**字段直读**）。真机报告 `01789210180` 就是 EID 在
// `features/eid_api.lua:2046` 调 `game:GetSeeds():IsCustomRun()` 时因为拿到 nil 而整条回调报错，
// 所以先给出"有对象、有方法、值自洽"的最小实现（成熟度标记为 `Experimental`），
// 而不是让 Mod 崩在 nil 上。
struct SeedHandle {
    std::uint32_t startSeed;
    std::uint8_t customRun;
};

struct MusicHandle {
    std::uint8_t reserved;
};

struct RngHandle {
    std::array<std::uint8_t, 16> storage{};
};

// Unlike every other handle in this file, `font` is not a game-owned pointer: it points at a
// `KAGE::Graphics::Font` that the Runtime itself allocated (`kFontObjectSize` bytes, 16-byte
// aligned) and that the Runtime must therefore release. `Game`/`Level`/`Room`/`Music` are
// resolved again inside each managed callback because the game owns those objects; a Font has
// no such owner, which is why this handle carries the instance and why `__gc` frees it.
//
// 批次 3 追加三个**颜色偏移**：PC 的 `Color(R,G,B,A,RO,GO,BO)` 重载
// （`analysis/isaacdocs-snapshot/docs/Color.md:27`），EID 的 `EID:renderIcon`
// （`features/eid_api.lua:1369`）用的就是七参形式。这三个值只保存在句柄里、不参与任何引擎调用
// （`font_api` 传给引擎的仍然是句柄起头的 16 字节 RGBA，与既有的"16 字节颜色"假设一致）；
// 好处是后三个参数不再被静默丢掉，而且整块内存都被初始化。
struct ColorHandle {
    float red;
    float green;
    float blue;
    float alpha;
    float offsetRed;
    float offsetGreen;
    float offsetBlue;
};

struct FontHandle {
    void* font;
};

struct VectorHandle {
    float x;
    float y;
};

// 与 `Font` 同类：`sprite` 指向本模块自己分配、也必须由本模块释放的 `ANM2`（`kSpriteObjectSize`
// 字节、16 字节对齐）；游戏只提供构造/析构入口，从不拥有这块内存。
//
// 批次 3 起这里还缓存 PC 的 `Sprite.Scale`/`Sprite.Color`/`Sprite.FlipX` 三个**可写属性**
// （EID 在 `features/eid_api.lua:1368`、`main.lua:949`/`1026` 直接给它们赋值）。这三项**只写进
// 本句柄**、不回写 `ANM2`：它们对应的对象偏移没有证据（见 `sprite_api.cpp` 的 `__newindex`
// 注释），而猜一个偏移写进我们自己分配的对象同样是踩内存。
//
// --- `source`：句柄**来源/所有权**标记（2026-09-12，批次 5 `Entity:GetSprite()`）-----------
//
// `Sprite` 句柄现在有两种来源，**释放语义完全相反**：
//
//   * `kSpriteSourceRuntimeOwned` —— Lua 的 `Sprite()` 构造出来的对象：内存由本模块
//     `malloc`、由引擎 ctor 构造，`__gc` 必须调 `~ANM2()` 再 `free`（原行为，不变）；
//   * `kSpriteSourceEngineEntity` —— `Entity:GetSprite()` 借出来的对象：它是**引擎实体内部的
//     成员**（`Entity + kEntitySpriteOffset`，实体自己拥有这块内存、自己负责析构）。
//     `__gc` **绝不能**调 `~ANM2()`、**绝不能** `free` —— 那会把引擎的实体对象拆掉/把一块
//     不是 malloc 基址的地址交给 `free`。
//
// 因此 `source` 是"析构时放不放手"的唯一依据，也是"每次访问要不要重新解析"的依据：
// 引擎对象随时可能随实体消失，所以 `owner`（来源实体指针）也要存下来，每次访问都
// **重新校验**实体仍然活着，再重新算出 `owner + kEntitySpriteOffset`（句柄里的 `sprite`
// 只当身份缓存，不作为解引用依据，避免句柄跨帧后指向已释放的实体）。
struct SpriteHandle {
    void* sprite;
    // 仅 `kSpriteSourceEngineEntity` 非空：借出这个 sprite 的引擎实体地址。
    void* owner;
    std::uint8_t source;
    float scaleX;
    float scaleY;
    float colorRed;
    float colorGreen;
    float colorBlue;
    float colorAlpha;
    std::uint8_t flipX;
    std::uint8_t hasScale;
    std::uint8_t hasColor;
    std::uint8_t hasFlipX;
};

// `SpriteHandle::source` 的取值（放在结构体之后，避免读取顺序上的疑问）。
inline constexpr std::uint8_t kSpriteSourceRuntimeOwned = 0;
inline constexpr std::uint8_t kSpriteSourceEngineEntity = 1;

// `Entity` / `EntityPlayer`：**必须**保存原生指针 —— 与上面 `Game`/`Level`/`Room` 的占位句柄
// 不同，玩家 1 与玩家 2 只有指针能区分，而且 `EntityPlayer` 的方法（`HasCollectible`）本来
// 就要以 `this` 调用引擎方法。代价是句柄可能持有过期指针，所以每一次字段/方法访问都重新做
// 可读性 + vptr 校验，校验失败一律返回安全值而不是解引用（见 `interfaces/lua/isaac_api.cpp`）。
struct EntityHandle {
    void* entity;
};

// `ItemConfig`：与 `EntityHandle` 同理，句柄必须保存原生地址 —— 但这里的地址是**内嵌对象**
// 的地址（`IC = Manager + kManagerItemConfigOffset`，加法，见 `runtime_constants.hpp`），
// 引擎里不存在指向它的指针。
//
// `items`/`count` 是**缓存**，只为让"每个句柄对应哪一份向量"可核对；**每一次**访问都会从
// 引擎基址重新解引用整条链路并重新读 begin/end，缓存的这两个值只用来判断"句柄是否已经过期"
// （换局、Manager 重建、ItemConfig 重新 `Init` 都会让它们与现场不一致）。
struct ItemConfigHandle {
    void* config;
    void* items;
    std::uint64_t count;
    // 句柄创建时"收藏品向量是否已经建立"（2026-09-12）。两种情况必须分开处理：
    //   * 创建时**向量还没建立**（`begin == 0`，真机报告 `01789226184`：EID 在加载期就取
    //     `Isaac.GetItemConfig()`）⇒ `items`/`count` 记为 0，访问时**允许现读**，
    //     否则这个句柄一开局就作废、EID 的 `EID.itemConfig` 直接变成死的；
    //   * 创建时向量**已经建立** ⇒ 沿用"begin/count 必须与现场一致"的过期判据
    //     （换局后 `ItemConfig::Init` 会重排向量，旧句柄必须自证过期）。
    std::uint32_t itemsWereReadable;
    std::uint32_t reserved;
};

// `ItemConfig::Item`：条目就是向量元素（反汇编证实 `ldr x0,[begin, w1, uxtw #3]`），所以
// 句柄存的是那个元素的地址，外加"它属于哪条向量的哪个下标"这三项身份信息。访问时重新解析
// 整条链路并核对"这条向量的 begin 没变、下标处的元素还是同一个地址"，而不是盲解引用一个
// 可能过期的指针（与 `EntityHandle` 的 vptr 校验同一风格）。
//
// `beginOffset` 必须存下来（而不是从 `kind` 反推）：`kind` 与向量的对应关系只对
// COLLECTIBLE(1)/TRINKET(2) 有证据，卡牌/药丸/无道具条目的编号没有；存偏移则与 kind 无关。
struct ItemConfigItemHandle {
    void* item;
    void* items;
    std::uint32_t beginOffset;
    std::uint32_t index;
};

} // namespace LuaRuntime
