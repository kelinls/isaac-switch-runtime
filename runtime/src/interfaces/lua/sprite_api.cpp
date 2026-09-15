#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/sprite_api.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"

#include "interfaces/lua/isaac_api.hpp"
#include "interfaces/lua/owner_binding.hpp"

#include "lua_object_handles.hpp"
#include "lua_runtime_state.hpp"
#include "runtime_constants.hpp"

#include <array>
#include <cstdlib>
#include <cstring>

// `svcQueryMemory`/`MemoryInfo`/`Perm_R` 只存在于设备侧（libnx）；宿主构建里可读性检查退化成
// "只排除空地址"。与 `isaac_api.cpp`/`game_api.cpp`/`remaining_api.cpp` 同一形态（那三个 TU
// 各自有一份同样的私有实现，本 TU 只为 `Sprite:GetAnimation()` 读动画名而需要它）。
#if defined(__SWITCH__)
#include "lib/nx/nx.h"
#endif

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

using LuaRuntime::ColorHandle;
using LuaRuntime::kColorMetatable;
using LuaRuntime::kSpriteMetatable;
using LuaRuntime::kSpriteSourceEngineEntity;
using LuaRuntime::kSpriteSourceRuntimeOwned;
using LuaRuntime::SpriteHandle;
using LuaRuntime::VectorHandle;

constexpr char kSpriteOwner[] = "Sprite";
constexpr std::size_t kSpriteStorageSlack = kSpriteObjectAlignment;

// 引擎字段读取原语：先确认可读再 `memcpy`（指针猜错必须变成"读不到"而不是"读了就崩"）。
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

template <typename T>
bool ReadEngine(std::uintptr_t address, T* value) noexcept {
    if (value == nullptr || !IsEngineMemoryReadable(address, sizeof(T))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

// --- 所有权与来源（批次 5）-----------------------------------------------------
//
// `SpriteHandle` 有两种来源，释放语义相反（详见 `lua_object_handles.hpp` 的注释）：
//
//   RuntimeOwned（Lua 的 `Sprite()`）—— 内存由本模块 `malloc`，`__gc` 必须 `~ANM2()` + `free`；
//   EngineEntity（`Entity:GetSprite()`）—— 内存是**引擎实体**的成员，`__gc` 什么都不做。
//
// ★ 这里只有一条纪律：**任何**访问都先走 `ResolveSpriteObject`，绝不再直接读 `handle->sprite`
// 去解引用。引擎实体随时可能随换房间/死亡消失，所以引擎来源的地址**每次重新算**（`owner` →
// 校验实体活着 → `owner + kEntitySpriteOffset`），句柄里缓存的地址只当身份。
void* ResolveSpriteObject(const SpriteHandle* handle) noexcept {
    if (handle == nullptr) {
        return nullptr;
    }
    if (handle->source == kSpriteSourceEngineEntity) {
        const std::uintptr_t owner = reinterpret_cast<std::uintptr_t>(handle->owner);
        // "还活着"的判据只有一份实现（`isaac_api.cpp` 的 `ValidatedEntity` 用的那条）：
        // vptr 仍落在 `Entity` 家族区间内且地址可读。
        if (!IsLiveEntityPointer(owner)) {
            return nullptr;
        }
        return reinterpret_cast<void*>(owner + kEntitySpriteOffset);
    }
    return handle->sprite;
}

// 动画名的字节上限。libc++ 的 `std::string` 可能是长串（数据在引擎堆上，指针来自引擎内存），
// 所以我们**不**做无界 `strlen`：逐字节确认可读、遇到 0 或到达上限就停。
constexpr std::size_t kAnimationNameCapacity = 64;

// --- `Sprite:GetTexel` 的探针（2026-09-12，动作一）-------------------------------
//
// 目的：`GetTexel` 是"EID 判赎罪线问号底座"的唯一依据，而它的调用约定（HFA 按值传）
// 是本轮唯一有风险的改动。真机上一轮结束后要能回答三个问题：
//   * 引擎入口到底被调到了几次（`calls`）；
//   * 有多少次是"取不到入口/没有动画状态"的退回落值（`fallbacks`）；
//   * 最近一次真实读数长什么样（`lastWord` = 低 32 位 R、次 32 位 G，足以区分
//     "全 0（引擎说这里是空像素）"和"非 0（真读到了贴图颜色）"）。
std::atomic<std::uint32_t> g_SpriteGetTexelCalls{0};
std::atomic<std::uint32_t> g_SpriteGetTexelFallbacks{0};
std::atomic<std::uint32_t> g_SpriteGetTexelLastReason{0};
std::atomic<std::uint64_t> g_SpriteGetTexelLastWord{0};

// 落值：与旧占位一致的不透明白色。`reason` 进探针：1 = 没有引擎入口，
// 2 = sprite 地址不可读，3 = 没有当前动画状态（`ANM2+0x60` 为空）。
int PushTexelFallback(lua_State* state, std::uint32_t reason) {
    g_SpriteGetTexelFallbacks.fetch_add(1, std::memory_order_relaxed);
    g_SpriteGetTexelLastReason.store(reason, std::memory_order_relaxed);
    auto* color = static_cast<ColorHandle*>(lua_newuserdata(state, sizeof(ColorHandle)));
    color->red = 1.0F;
    color->green = 1.0F;
    color->blue = 1.0F;
    color->alpha = 1.0F;
    color->offsetRed = 0.0F;
    color->offsetGreen = 0.0F;
    color->offsetBlue = 0.0F;
    luaL_getmetatable(state, kColorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

void RecordSpriteGetTexelForProbe(float red, float green, bool enginePath) noexcept {
    if (enginePath) {
        g_SpriteGetTexelCalls.fetch_add(1, std::memory_order_relaxed);
        std::uint32_t redBits = 0;
        std::uint32_t greenBits = 0;
        std::memcpy(&redBits, &red, sizeof(redBits));
        std::memcpy(&greenBits, &green, sizeof(greenBits));
        g_SpriteGetTexelLastWord.store((static_cast<std::uint64_t>(greenBits) << 32) | redBits,
                                       std::memory_order_relaxed);
    }
}

// 读 `ANM2+0x38` 指向的"当前动画名"。返回 false 表示"没有当前动画或读不到"（调用方回空串）。
//
// 读法与引擎自己的 `ANM2::IsPlaying(char const*)`（`0xa454`）逐条一致：对象指针为 0 → 无动画；
// 首字节最低位 = long 标志 → 长串取 `+0x10` 的数据指针、短串取 `+1` 的内联数据。
bool ReadAnimationName(std::uintptr_t sprite, char* out, std::size_t capacity) noexcept {
    if (sprite == 0 || out == nullptr || capacity == 0) {
        return false;
    }
    std::uintptr_t nameObject = 0;
    if (!ReadEngine(sprite + kSpriteAnimationNameOffset, &nameObject) || nameObject == 0) {
        return false;
    }
    std::uint8_t firstByte = 0;
    if (!ReadEngine(nameObject, &firstByte)) {
        return false;
    }
    std::uintptr_t data = 0;
    if ((firstByte & kLibcxxStringLongFlag) != 0) {
        std::uintptr_t pointer = 0;
        if (!ReadEngine(nameObject + kLibcxxStringDataOffset, &pointer) || pointer == 0) {
            return false;
        }
        data = pointer;
    } else {
        data = nameObject + kLibcxxStringShortDataOffset;
    }
    const std::size_t limit = capacity - 1 < kAnimationNameCapacity ? capacity - 1
                                                                    : kAnimationNameCapacity;
    std::size_t length = 0;
    while (length < limit) {
        char character = 0;
        if (!ReadEngine(data + length, &character)) {
            break;
        }
        if (character == '\0') {
            break;
        }
        out[length] = character;
        ++length;
    }
    out[length] = '\0';
    return length != 0;
}


// The native object belongs to this module, exactly like `Font`'s: the game only supplies the
// constructor/destructor entry points. Allocation mirrors the Font family (raw block, aligned
// pointer stored just in front of it so `__gc` can hand the original back to `free`).
void* AllocateSpriteStorage() noexcept {
    void* raw = std::malloc(kSpriteObjectSize + kSpriteStorageSlack);
    if (raw == nullptr) {
        return nullptr;
    }
    const std::uintptr_t rawAddress = reinterpret_cast<std::uintptr_t>(raw);
    const std::uintptr_t aligned =
        (rawAddress + sizeof(void*) + (kSpriteObjectAlignment - 1)) &
        ~(static_cast<std::uintptr_t>(kSpriteObjectAlignment) - 1);
    *reinterpret_cast<void**>(aligned - sizeof(void*)) = raw;
    return reinterpret_cast<void*>(aligned);
}

void ReleaseSpriteStorage(void* sprite) noexcept {
    if (sprite == nullptr) {
        return;
    }
    void* raw = *reinterpret_cast<void**>(reinterpret_cast<std::uintptr_t>(sprite) - sizeof(void*));
    std::free(raw);
}

int ResolveSprite(lua_State* state, const char* apiName, void** sprite) {
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    void* resolved = ResolveSpriteObject(handle);
    if (resolved == nullptr) {
        // 两种失效原因分开报，否则真机日志会把"实体没了"误读成"我们自己把 Sprite 释放了"。
        if (handle->source == kSpriteSourceEngineEntity) {
            return luaL_error(
                state, "%s: the engine entity this Sprite belongs to is no longer alive", apiName);
        }
        return luaL_error(state, "%s was called on a released Sprite", apiName);
    }
    *sprite = resolved;
    return 0;
}

// `Vector` 值类型（`KAGE::Math::Vector2`：8 字节 `{float X, float Y}`、无虚表）。三个渲染方法
// 都按引用收它，所以这里把 Lua 值的内存直接当参数传 —— 与引擎期望的形式完全一致。
VectorHandle* CheckVector(lua_State* state, int index) {
    return static_cast<VectorHandle*>(luaL_checkudata(state, index, LuaRuntime::kVectorMetatable));
}

// --- 批次 3：可写属性（`Scale`/`Color`/`FlipX`）与字段读取 ----------------------
//
// 为什么需要 `__index`/`__newindex`：`Sprite` 的 userdata 只有元表里的 `__gc` 与 `__index`
// 方法表；给 userdata 赋值而元表没有 `__newindex` 时，Lua 直接抛
// "attempt to index a userdata value"。EID 在**第一次画东西之前**就写这三个属性
// （`features/eid_api.lua:1368` 的 `spriteObj.Scale = ...` / `:1369` 的 `spriteObj.Color = ...`
// 在 `EID:renderIcon` 里，`main.lua:946`/`982` 的 `sprite.FlipX` 在 `EID:renderIndicator` 里），
// 所以这一条不做，第一次画字/画图必然报错。
//
// **只缓存、不回写引擎**：这三个属性在 `ANM2` 里的偏移没有证据 —— `ANM2::Render`
// （`0x9BD4`）只会把 `[this+0x30]`/`[this+0x60]` 两个指针当第 6 个参数传给
// `AnimationLayer::RenderFrame`，而 `AnimationState::RenderLayer`（`0x9B8C`）证明那个参数是
// `AnimationState*`/`AnimationData*` 一类的内部对象，不是 `Color`/`Scale`/`FlipX` 的落点。
// 拿一个猜出来的偏移往对象里写，与"写引擎内存"是同一类风险（对象虽然是我们分配的，
// 写坏它的后果一样是踩内存），所以本批次按验收口径停在"值留在句柄里、绘制路径读得到"。
//
// 允许的字段就是 PC 的这三个（大小写与 PC 一致）；其它字段名报错，避免把"拼错的属性名"
// 静默吞掉（与 `Vector`/`KColor` 的 `__newindex` 同一口径）。
constexpr char kSpriteScaleField[] = "Scale";
constexpr char kSpriteColorField[] = "Color";
constexpr char kSpriteFlipXField[] = "FlipX";

bool ReadVectorPair(lua_State* state, int index, float* x, float* y) {
    auto* vector = static_cast<VectorHandle*>(
        luaL_testudata(state, index, LuaRuntime::kVectorMetatable));
    if (vector == nullptr) {
        return false;
    }
    *x = vector->x;
    *y = vector->y;
    return true;
}

#if !defined(__SWITCH__)
SpritePropertyApplyHostImplementation g_PropertyApplyHostImplementation = nullptr;
#endif

// 把句柄里缓存的三个属性交给"应用到原生对象"这一步。绘制路径（`Render`/`RenderLayer`）
// 在调用引擎之前无条件调用它，所以"渲染确实读到了 Mod 写的值"是可验证的。
void ApplyCachedSpriteProperties(void* sprite, const SpriteHandle* handle) noexcept {
    if (sprite == nullptr || handle == nullptr) {
        return;
    }
#if defined(__SWITCH__)
    // 设备侧：见上面的注释 —— 还没有 ANM2 属性偏移，所以这一步是空操作（绝不猜偏移写内存）。
    static_cast<void>(handle);
#else
    const SpritePropertyApplyHostImplementation apply = g_PropertyApplyHostImplementation;
    if (apply != nullptr) {
        apply(sprite, handle);
    }
#endif
}

// 把缓存的 `Scale` 压成一张 `{X, Y}` 表？不：PC 的 `Sprite.Scale` 读出来就是 `Vector`，
// 所以这里原样产出 `Vector` userdata（与 `EntityPlayer.Position` 同一做法：新对象，
// 不共享同一块 userdata）。
void PushCachedScale(lua_State* state, const SpriteHandle* handle) {
    auto* vector = static_cast<VectorHandle*>(lua_newuserdata(state, sizeof(VectorHandle)));
    vector->x = handle->scaleX;
    vector->y = handle->scaleY;
    luaL_getmetatable(state, LuaRuntime::kVectorMetatable);
    lua_setmetatable(state, -2);
}

void PushCachedColor(lua_State* state, const SpriteHandle* handle) {
    auto* color = static_cast<ColorHandle*>(lua_newuserdata(state, sizeof(ColorHandle)));
    color->red = handle->colorRed;
    color->green = handle->colorGreen;
    color->blue = handle->colorBlue;
    color->alpha = handle->colorAlpha;
    // 颜色偏移不在 `Sprite.Color` 的缓存里（见 `SpriteNewIndex`：它只取 RGBA），整块初始化干净。
    color->offsetRed = 0.0F;
    color->offsetGreen = 0.0F;
    color->offsetBlue = 0.0F;
    luaL_getmetatable(state, kColorMetatable);
    lua_setmetatable(state, -2);
}

// `Render(position, topLeftClamp = Vector.Zero, bottomRightClamp = Vector.Zero)`；缺失的 clamp
// 用零向量补齐（PC 的默认值就是 `Vector.Zero`）。
int RequireClampVectors(lua_State* state, int firstIndex, VectorHandle** topLeft,
                        VectorHandle** bottomRight) {
    static VectorHandle zero{0.0f, 0.0f};
    const int argumentCount = lua_gettop(state);
    *topLeft = &zero;
    *bottomRight = &zero;
    if (argumentCount >= firstIndex + 1 && !lua_isnoneornil(state, firstIndex + 1)) {
        *topLeft = CheckVector(state, firstIndex + 1);
    }
    if (argumentCount >= firstIndex + 2 && !lua_isnoneornil(state, firstIndex + 2)) {
        *bottomRight = CheckVector(state, firstIndex + 2);
    }
    return 0;
}


// --- libc++ 字符串桥（第二步） -------------------------------------------------
//
// `ANM2::Load` 与 `ReplaceSpritesheet` 收 libc++ 的 `std::string const&`：24 字节 SSO 对象
// （`__short{size:7, long:1, data[23]}` / `__long{...}`），而本模块编的是 libstdc++（32 字节）——
// 两者的内存布局不同，直接传我们的 string 会读错。
//
// 做法：全零的 24 字节在 libc++ 里是**合法的空短串**（long 标志为 0、size 为 0），所以先把缓冲
// 清零，再借游戏自己导入的 `basic_string::assign(char const*)`（PLT 桩，守卫见
// `kLibcxxStringAssignOffset`）把路径填进去。这样我们对布局的假设只剩"全零 = 空串"这一条。
//
// **按路径缓存、暂不释放**：`assign` 对超过 SSO 容量的长路径会在游戏侧堆上分配一块缓冲，而释放
// 它需要知道 libc++ `__long` 的数据指针偏移与 long 标志位 —— 那个布局我们还没有实测，猜错就是踩
// 内存。所以这里按路径复用同一个 24 字节对象：每个**不同**路径最多留一份缓冲（有界，通常几十份），
// 绝不做"猜布局去 free"。探针已经把测量这个布局列入待办（掩码位），实测出来后可以补上正规释放。
constexpr std::size_t kLibcxxStringSize = 24;
constexpr std::size_t kStringPathCapacity = 192;
constexpr std::size_t kStringCacheEntries = 32;

struct CachedNativeString {
    char path[kStringPathCapacity];
    alignas(8) unsigned char object[kLibcxxStringSize];
    bool used;
};
CachedNativeString g_nativeStrings[kStringCacheEntries]{};

// 长路径（不满足 SSO）时，`assign` 会在**游戏侧**堆上分配缓冲。释放它必须用**游戏自己的**
// `operator delete`：本模块（`subsdk9`）有自己的 newlib 堆，用本模块的 `free` 会踩坏堆。
// 布局是实测的（探针掩码位 17/18）：数据指针在对象偏移 `kLibcxxStringDataOffset`，首字最低位
// 为 1 表示 long。拿不到游戏 `operator delete` 桩时**宁可不释放**（缓存本身有界，最多 32 条）。
void ReleaseNativeString(unsigned char* object) noexcept {
    if (object == nullptr) {
        return;
    }
    const std::uintptr_t deallocate = LuaRuntime::GameOperatorDeleteThunk();
    if (deallocate == 0) {
        return;
    }
    const auto* words = reinterpret_cast<const std::uint64_t*>(object);
    if ((words[0] & 1ULL) == 0) {
        return;  // 短串：内容内联在对象里，没有堆缓冲
    }
    void* data = reinterpret_cast<void*>(words[kLibcxxStringDataOffset / sizeof(std::uint64_t)]);
    if (data == nullptr) {
        return;
    }
    using DeleteFn = void (*)(void*);
    reinterpret_cast<DeleteFn>(deallocate)(data);
    std::memset(object, 0, kLibcxxStringSize);  // 复位成"空短串"，避免留下悬垂指针
}

// 返回一块已填好该路径的 libc++ 字符串对象；失败（路径过长、缓存满、绑定缺失）返回 nullptr。
// 缓存满时**先释放最旧一条**再复用（原来是直接失败；这条路径在真实 Mod 会话里也可能被走到）。
//
// ★ `allowEmpty`（2026-09-12，真机报告 `01789225234` 定案）：**空串是合法参数，不是错误**。
//
// 真机证据：那两份报告的负载里，错误文本残留片段是
// `Sprite:ReplaceSpritesheet could not build a native file name string`——
// 也就是说 EID 调 `ReplaceSpritesheet(layer, "")` 时我们**抛错拒绝了**。而空串在 PC 上完全合法：
// EID 的 `loadCustomSprites`（`eid_api.lua:1360` 附近）先用空串把某一层的自定义图集清掉，
// 再用真实路径替换。我们这里一抛错，**整条描述构建被打断**（派发器静默摘除回调），
// 表现就是"进到道具范围内什么都不显示"。
//
// 所以把"空串"从失败条件里摘出来，由调用方按语义决定是否允许：
//   * `Sprite:ReplaceSpritesheet` —— 允许（空串 = 清除该层的替换）；
//   * `Sprite:Load` —— 不允许（加载一个空路径没有意义，PC 也会失败）。
// 全零的 24 字节在 libc++ 里就是合法的空短串，所以"允许空串"这条路径不需要额外构造。
std::size_t g_nativeStringCursor = 0;

void* BuildNativeString(const char* path, bool allowEmpty) {
    if (path == nullptr) {
        return nullptr;
    }
    const std::size_t length = std::strlen(path);
    if (length == 0 && !allowEmpty) {
        return nullptr;
    }
    if (length + 1 > kStringPathCapacity) {
        return nullptr;
    }
    const std::uintptr_t assign = LuaRuntime::LibcxxStringAssignThunk();
    if (assign == 0) {
        return nullptr;
    }
    for (CachedNativeString& entry : g_nativeStrings) {
        if (entry.used && std::strcmp(entry.path, path) == 0) {
            return entry.object;
        }
    }
    for (CachedNativeString& entry : g_nativeStrings) {
        if (entry.used) {
            continue;
        }
        std::memset(entry.object, 0, sizeof(entry.object));
        using AssignFn = void* (*)(void*, const char*);
        reinterpret_cast<AssignFn>(assign)(entry.object, path);
        std::memcpy(entry.path, path, length + 1);
        entry.used = true;
        return entry.object;
    }
    // 缓存满：按游标释放一条最旧的（含它可能持有的长路径缓冲），然后复用这一格。
    CachedNativeString& victim = g_nativeStrings[g_nativeStringCursor];
    g_nativeStringCursor = (g_nativeStringCursor + 1) % kStringCacheEntries;
    ReleaseNativeString(victim.object);
    std::memset(victim.object, 0, sizeof(victim.object));
    using AssignFn = void* (*)(void*, const char*);
    reinterpret_cast<AssignFn>(assign)(victim.object, path);
    std::memcpy(victim.path, path, length + 1);
    victim.used = true;
    return victim.object;
}

// --- `Sprite:ReplaceSpritesheet` 的探针（2026-09-12 第六轮）------------------------
//
// 真机连续两轮都停在这个函数上，而 `BuildNativeString` 的三条失败路径（`path == nullptr`、
// 路径过长、`assign` 绑定缺失）返回的都是 nullptr，光看"失败"分不清是哪条。
// 这里把调用时的**文件名长度**与**前 8 字节**记下来：
//   * 长度 = 0xFFFFFFFF ⇒ 传进来的根本不是字符串（`lua_tostring` 返回 nullptr）；
//   * 长度 > 191        ⇒ 路径超容量；
//   * 长度正常          ⇒ 前 8 字节应当是可读路径（例如 `resources`），据此排除"值是垃圾"。
std::atomic<std::uint32_t> g_ReplaceSpritesheetNameLength{0};
std::atomic<std::uint64_t> g_ReplaceSpritesheetNameHead{0};
std::atomic<std::uint32_t> g_ReplaceSpritesheetCalls{0};

void RecordSpriteReplaceSpritesheetForProbe(std::uint32_t length, const char* name) noexcept {
    g_ReplaceSpritesheetCalls.fetch_add(1, std::memory_order_relaxed);
    g_ReplaceSpritesheetNameLength.store(length, std::memory_order_relaxed);
    std::uint64_t head = 0;
    if (name != nullptr) {
        const std::size_t take = length < 8 ? length : 8;
        if (take != 0) {
            std::memcpy(&head, name, take);
        }
    }
    g_ReplaceSpritesheetNameHead.store(head, std::memory_order_relaxed);
}

// --- Handlers --------------------------------------------------------------

int SpriteLoad(lua_State* state);
int SpriteLoadGraphics(lua_State* state);
int SpriteReplaceSpritesheet(lua_State* state);
int SpritePlay(lua_State* state);
int SpriteSetAnimation(lua_State* state);
int SpriteSetFrame(lua_State* state);
int SpriteGetFrame(lua_State* state);
int SpriteSetLayerFrame(lua_State* state);
int SpriteGetLayerFrame(lua_State* state);
int SpriteUpdate(lua_State* state);
int SpriteIsPlaying(lua_State* state);
int SpriteIsFinished(lua_State* state);
int SpritePlayRandom(lua_State* state);
int SpriteRender(lua_State* state);
int SpriteRenderLayer(lua_State* state);
int SpriteGetTexel(lua_State* state);
// `SpriteGetAnimation` 的声明在 `sprite_api.hpp`（定义也在**命名空间**作用域，不在匿名命名空间
// 里），这里刻意不重复声明：在匿名命名空间里再写一遍会变成"另一个函数"，绑定表引用到的那个
// 符号就没有定义了。

constexpr LuaHandlerBinding kSpriteHandlers[] = {
    {0x0D01000E, &SpriteLoad},
    {0x0D01000F, &SpriteLoadGraphics},
    {0x0D010010, &SpriteReplaceSpritesheet},
    {0x0D010001, &SpritePlay},
    {0x0D010002, &SpriteSetAnimation},
    // PC 的 `SetFrame` 有两个重载：`(int)` 与 `(string, int)`。注册表是按**名字**挂的，
    // 所以这里只留一个入口，由它看参数类型分派（与 PC 绑定层的做法一致）。
    {0x0D010003, &SpriteSetFrame},
    {0x0D010004, &SpriteGetFrame},
    {0x0D010005, &SpriteSetLayerFrame},
    {0x0D010006, &SpriteGetLayerFrame},
    {0x0D010007, &SpriteUpdate},
    {0x0D010008, &SpriteIsPlaying},
    {0x0D010009, &SpriteIsFinished},
    {0x0D01000A, &SpritePlayRandom},
    {0x0D01000B, &SpriteRender},
    {0x0D01000C, &SpriteRenderLayer},
    // `GetTexel` 用 0x12：0x0D 已被 `IsLoaded` 占用（第一版误用同一个 id，Catalog 的
    // "id 唯一" 校验立刻失败 —— 这个门禁正是为了拦这种手抄错误）。
    {0x0D010012, &SpriteGetTexel},
    {0x0D01000D, &SpriteIsLoaded},
    // 批次 5：`Sprite:GetAnimation()`（纯读 `ANM2+0x38`，没有引擎调用）。
    {0x0D010011, &SpriteGetAnimation},
};

// PC: `void Load(string ANM2Path, boolean LoadGraphics)`；引擎侧是
// `Load(std::string const&, bool)`（x1 = 字符串对象、w2 = 加载图形）。
int SpriteLoad(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:Load", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 3 || lua_type(state, 2) != LUA_TSTRING ||
        (argumentCount == 3 && lua_type(state, 3) != LUA_TBOOLEAN)) {
        return luaL_error(state, "Sprite:Load accepts a path and an optional loadGraphics flag");
    }
    const std::uintptr_t method = LuaRuntime::SpriteLoadThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:Load native binding is unavailable");
    }
    // `Load` 不接受空路径（PC 亦然），所以 `allowEmpty = false`。
    void* nativeString = BuildNativeString(lua_tostring(state, 2), false);
    if (nativeString == nullptr) {
        return luaL_error(state,
                          "Sprite:Load could not build a native path string (missing binding, "
                          "empty path or path too long)");
    }
    using LoadFn = void (*)(void*, const void*, bool);
    reinterpret_cast<LoadFn>(method)(sprite, nativeString,
                                     argumentCount == 3 && lua_toboolean(state, 3) != 0);
    return 0;
}

// PC: `void LoadGraphics()` —— 用已经 `Load` 过的数据把图形载入 GPU。
int SpriteLoadGraphics(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:LoadGraphics", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Sprite:LoadGraphics accepts no arguments");
    }
    const std::uintptr_t method = LuaRuntime::SpriteLoadGraphicsThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:LoadGraphics native binding is unavailable");
    }
    using LoadGraphicsFn = void (*)(void*);
    reinterpret_cast<LoadGraphicsFn>(method)(sprite);
    return 0;
}

// PC: `void ReplaceSpritesheet(int LayerId, string PngFilename)`；引擎侧
// `ReplaceSpritesheet(int, std::string const&)`（w1 = 层号、x2 = 字符串对象）。
int SpriteReplaceSpritesheet(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:ReplaceSpritesheet", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 3 || !lua_isinteger(state, 2) || lua_type(state, 3) != LUA_TSTRING) {
        return luaL_error(state, "Sprite:ReplaceSpritesheet accepts a layer id and a file name");
    }
    const std::uintptr_t method = LuaRuntime::SpriteReplaceSpritesheetThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:ReplaceSpritesheet native binding is unavailable");
    }
    // `allowEmpty = true`：空串表示"清掉这一层的图集替换"，是 EID 的正常用法（见上面
    // `BuildNativeString` 的注释与真机报告 `01789225234`）。
    const char* fileName = lua_tostring(state, 3);
    // 探针（2026-09-12 第六轮）：真机连续两轮都停在这里，而**失败原因分不清**
    // （`path == nullptr` / 路径过长 / assign 绑定缺失，三者都返回 nullptr）。
    // 把"哪一条"和"路径长度 + 前 8 字节"记下来，下一次报告就能直接判。
    if (fileName != nullptr) {
        const std::size_t fileNameLength = std::strlen(fileName);
        RecordSpriteReplaceSpritesheetForProbe(static_cast<std::uint32_t>(fileNameLength), fileName);
    } else {
        RecordSpriteReplaceSpritesheetForProbe(0xFFFFFFFFu, nullptr);
    }
    void* nativeString = BuildNativeString(fileName, true);
    if (nativeString == nullptr) {
        // 三条失败路径分别报错，报告里一眼可辨（不再只给一句笼统的话）。
        if (fileName == nullptr) {
            return luaL_error(state, "Sprite:ReplaceSpritesheet got a nil file name");
        }
        if (std::strlen(fileName) + 1 > kStringPathCapacity) {
            return luaL_error(state,
                              "Sprite:ReplaceSpritesheet file name is longer than %d bytes",
                              static_cast<int>(kStringPathCapacity - 1));
        }
        return luaL_error(state,
                          "Sprite:ReplaceSpritesheet native string binding is unavailable");
    }
    using ReplaceFn = void (*)(void*, int, const void*);
    reinterpret_cast<ReplaceFn>(method)(sprite, static_cast<int>(lua_tointeger(state, 2)),
                                       nativeString);
    return 0;
}

int SpritePlay(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:Play", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 3 || lua_type(state, 2) != LUA_TSTRING ||
        (argumentCount == 3 && lua_type(state, 3) != LUA_TBOOLEAN)) {
        return luaL_error(state, "Sprite:Play accepts an animation name and an optional force flag");
    }
    const std::uintptr_t method = LuaRuntime::SpritePlayThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:Play native binding is unavailable");
    }
    using PlayFn = void (*)(void*, const char*, bool);
    reinterpret_cast<PlayFn>(method)(sprite, lua_tostring(state, 2),
                                    argumentCount == 3 && lua_toboolean(state, 3) != 0);
    return 0;
}

int SpriteSetAnimation(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:SetAnimation", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 3 || lua_type(state, 2) != LUA_TSTRING ||
        (argumentCount == 3 && lua_type(state, 3) != LUA_TBOOLEAN)) {
        return luaL_error(state,
                          "Sprite:SetAnimation accepts an animation name and an optional reset flag");
    }
    const std::uintptr_t method = LuaRuntime::SpriteSetAnimationThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:SetAnimation native binding is unavailable");
    }
    using SetAnimationFn = bool (*)(void*, const char*, bool);
    // PC: `boolean SetAnimation(string AnimationName, boolean Reset = true)`.
    const bool reset = argumentCount == 3 ? lua_toboolean(state, 3) != 0 : true;
    lua_pushboolean(state, reinterpret_cast<SetAnimationFn>(method)(sprite, lua_tostring(state, 2),
                                                                    reset) ? 1 : 0);
    return 1;
}

// `Sprite:SetFrame(int)` 与 `Sprite:SetFrame(string, int)` 是 PC 的一对重载：按第二个参数的
// 类型分派到各自的原生入口。
int SpriteSetFrame(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:SetFrame", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount == 2 && lua_isinteger(state, 2)) {
        const std::uintptr_t method = LuaRuntime::SpriteSetFrameThunk();
        if (method == 0) {
            return luaL_error(state, "Sprite:SetFrame native binding is unavailable");
        }
        using SetFrameFn = void (*)(void*, int);
        reinterpret_cast<SetFrameFn>(method)(sprite, static_cast<int>(lua_tointeger(state, 2)));
        return 0;
    }
    if (argumentCount == 3 && lua_type(state, 2) == LUA_TSTRING && lua_isinteger(state, 3)) {
        const std::uintptr_t method = LuaRuntime::SpriteSetFrameNamedThunk();
        if (method == 0) {
            return luaL_error(state, "Sprite:SetFrame native binding is unavailable");
        }
        using SetFrameNamedFn = void (*)(void*, const char*, int);
        reinterpret_cast<SetFrameNamedFn>(method)(
            sprite, lua_tostring(state, 2), static_cast<int>(lua_tointeger(state, 3)));
        return 0;
    }
    return luaL_error(state,
                      "Sprite:SetFrame accepts a frame number or an animation name and a frame "
                      "number");
}

int SpriteGetFrame(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:GetFrame", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Sprite:GetFrame accepts no arguments");
    }
    const std::uintptr_t method = LuaRuntime::SpriteGetFrameThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:GetFrame native binding is unavailable");
    }
    using GetFrameFn = int (*)(const void*);
    lua_pushinteger(state, reinterpret_cast<GetFrameFn>(method)(sprite));
    return 1;
}

int SpriteSetLayerFrame(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:SetLayerFrame", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 3 || !lua_isinteger(state, 2) || !lua_isinteger(state, 3)) {
        return luaL_error(state, "Sprite:SetLayerFrame accepts a layer id and a frame number");
    }
    const std::uintptr_t method = LuaRuntime::SpriteSetLayerFrameThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:SetLayerFrame native binding is unavailable");
    }
    using SetLayerFrameFn = void (*)(void*, int, int);
    reinterpret_cast<SetLayerFrameFn>(method)(sprite, static_cast<int>(lua_tointeger(state, 2)),
                                              static_cast<int>(lua_tointeger(state, 3)));
    return 0;
}

int SpriteGetLayerFrame(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:GetLayerFrame", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 2 || !lua_isinteger(state, 2)) {
        return luaL_error(state, "Sprite:GetLayerFrame accepts a layer id");
    }
    const std::uintptr_t method = LuaRuntime::SpriteGetLayerFrameThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:GetLayerFrame native binding is unavailable");
    }
    using GetLayerFrameFn = int (*)(void*, int);
    lua_pushinteger(state, reinterpret_cast<GetLayerFrameFn>(method)(
                               sprite, static_cast<int>(lua_tointeger(state, 2))));
    return 1;
}

int SpriteUpdate(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:Update", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Sprite:Update accepts no arguments");
    }
    const std::uintptr_t method = LuaRuntime::SpriteUpdateThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:Update native binding is unavailable");
    }
    using UpdateFn = void (*)(void*);
    reinterpret_cast<UpdateFn>(method)(sprite);
    return 0;
}

// PC 文档里 `IsPlaying` / `IsFinished` 的形参个数是 **[0, 1]**（见
// `analysis/lua-api-inventory/inventory.json`：`符号形参 1 / 文档形参 [0, 1]`），动画名可省。
// 引擎的 `ANM2::IsPlaying(char const*)` / `IsFinished(char const*)`（`0xA454` / `0xA528`）
// 对**空字符串**的语义正是"只问当前动画"：
//   `IsPlaying`：当前动画为空 → false；`[+0x54]`（在播标志）为 0 → false；`*name == 0` → true。
// 所以省掉动画名时传空串，和 PC 行为一致。2026-09-13 真机上测试 Mod 调 `sprite:IsPlaying()`
// 被这里拒掉（错误指纹 49 = `Sprite:IsPlaying accepts an animation name`），既误伤真机诊断，
// 也是真实的兼容性缺口（真实 Mod 有省参数的写法）。
constexpr char kAnyAnimationName[] = "";

int SpriteIsPlaying(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:IsPlaying", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount > 2 || (argumentCount == 2 && lua_type(state, 2) != LUA_TSTRING)) {
        return luaL_error(state, "Sprite:IsPlaying accepts an optional animation name");
    }
    const std::uintptr_t method = LuaRuntime::SpriteIsPlayingThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:IsPlaying native binding is unavailable");
    }
    const char* name = argumentCount == 2 ? lua_tostring(state, 2) : kAnyAnimationName;
    using IsPlayingFn = bool (*)(const void*, const char*);
    lua_pushboolean(state, reinterpret_cast<IsPlayingFn>(method)(sprite, name) ? 1 : 0);
    return 1;
}

int SpriteIsFinished(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:IsFinished", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount > 2 || (argumentCount == 2 && lua_type(state, 2) != LUA_TSTRING)) {
        return luaL_error(state, "Sprite:IsFinished accepts an optional animation name");
    }
    const std::uintptr_t method = LuaRuntime::SpriteIsFinishedThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:IsFinished native binding is unavailable");
    }
    const char* name = argumentCount == 2 ? lua_tostring(state, 2) : kAnyAnimationName;
    using IsFinishedFn = bool (*)(const void*, const char*);
    lua_pushboolean(state, reinterpret_cast<IsFinishedFn>(method)(sprite, name) ? 1 : 0);
    return 1;
}

int SpritePlayRandom(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:PlayRandom", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 2 || !lua_isinteger(state, 2)) {
        return luaL_error(state, "Sprite:PlayRandom accepts a seed");
    }
    const std::uintptr_t method = LuaRuntime::SpritePlayRandomThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:PlayRandom native binding is unavailable");
    }
    using PlayRandomFn = void (*)(void*, unsigned int);
    reinterpret_cast<PlayRandomFn>(method)(
        sprite, static_cast<unsigned int>(lua_tointeger(state, 2)));
    return 0;
}

// PC: `void Render(Vector Position, Vector TopLeftClamp = Vector.Zero,
//                 Vector BottomRightClamp = Vector.Zero)`。
// 引擎侧的原型是三个 `Vector2 const&`，按引用传我们的 8 字节值类型正是它期望的形式。
int SpriteRender(lua_State* state) {
    RecordApiSequence(48U);
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:Render", &sprite); error != 0) {
        return error;
    }
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 4) {
        return luaL_error(state, "Sprite:Render accepts a position and optional clamps");
    }
    VectorHandle* position = CheckVector(state, 2);
    VectorHandle* topLeft = nullptr;
    VectorHandle* bottomRight = nullptr;
    static_cast<void>(RequireClampVectors(state, 2, &topLeft, &bottomRight));
    const std::uintptr_t method = LuaRuntime::SpriteRenderThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:Render native binding is unavailable");
    }
    // 绘制前先把 Mod 写的 Scale/Color/FlipX 交给应用入口（设备侧目前是空操作，
    // 见 `ApplyCachedSpriteProperties` 的注释）。
    ApplyCachedSpriteProperties(sprite, handle);
    using RenderFn = void (*)(void*, const void*, const void*, const void*);
    reinterpret_cast<RenderFn>(method)(sprite, position, topLeft, bottomRight);
    return 0;
}

// `Sprite:GetTexel(Vector SamplePos, Vector RenderPos, float AlphaThreshold, int LayerID = 0)`
//
// **为什么必须有这个名字**：EID 的 `IsAltChoice`（`main.lua:230-231`）用它逐像素比对
// "赎罪线问号底座"与目标底座的贴图 ——
//   `local qcolor = questionMarkSprite:GetTexel(Vector(i,j), nullVector, 1, 1)`
// 方法不存在时那一句是 "attempt to call a nil value"，整条回调在宝箱房里每帧报错、被派发器摘除。
//
// **2026-09-12 起改成真实读数**（此前是"恒返回不透明白色"的占位）：
// 占位让 EID 在**每一个**采样点上都得到"两个 sprite 一模一样"，于是它把**每个**底座道具
// 都当成"赎罪线红问号底座" → 走"隐瞒"那一支：只画那个小红问号、不写名字与效果。
// 这与真机现象（宝箱房底座道具只有问号、没有描述）逐字对上。
//
// 实现方式：**直接调引擎自己的 `ANM2::GetTexel`**（`kSpriteGetTexelOffset`），而不是自己去遍历
// `ANM2 → LayerState → SpriteSheet → ImageBase` 的像素链 —— 用游戏自己的答案回答游戏自己的问题。
//
// **调用约定（本轮唯一有风险的地方）**：AAPCS64 的 HFA 规则把两个 `KAGE::Math::Vector2`
// （各 2 个 float）放进 `s0..s3`，`float alphaThreshold` 进 `s4`，`int layerId` 进 `w1`；
// 返回值是 16 字节 `KColor`，走 **x8 间接结果寄存器**。证据：
// `analysis/stage150-sprite-abi/<build>.json` 的 `records.sprite_get_texel`
// （`confidence = verified`：符号、16 字节守卫、ABI 三重核对）。C++ 无法把"按值传的 HFA 结构"
// 精确钉在 s0..s3 上（编译器可自由选择传参形态），所以用一小段内联汇编自己装参数。
//
// 取不到引擎入口、sprite 不可读、或还没有当前动画状态（`ANM2+0x60` 为空）时返回**不透明白色**，
// 并把原因记进探针 —— 与旧占位行为一致，但不再是无条件的。
int SpriteGetTexel(lua_State* state) {
    RecordApiSequence(50U);
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:GetTexel", &sprite); error != 0) {
        return error;
    }
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 2 || argumentCount > 5) {
        return luaL_error(state, "Sprite:GetTexel accepts a sample position with optional "
                                 "render position, alpha threshold and layer id");
    }
    const VectorHandle* samplePos = CheckVector(state, 2);
    // PC 的默认值：`RenderPos = Vector.Zero`、`AlphaThreshold = 0`、`LayerID = 0`。
    // EID 传的 `nullVector` 正是零向量、阈值 1、层号 1（写死，不依赖默认值）。
    const VectorHandle defaultZero{0.0F, 0.0F};
    const VectorHandle* renderPos = argumentCount >= 3 ? CheckVector(state, 3) : &defaultZero;
    float alphaThreshold = 0.0F;
    if (argumentCount >= 4) {
        if (!lua_isnumber(state, 4)) {
            return luaL_error(state, "Sprite:GetTexel expects a number as the alpha threshold");
        }
        alphaThreshold = static_cast<float>(lua_tonumber(state, 4));
    }
    int layerId = 0;
    if (argumentCount >= 5) {
        if (!lua_isinteger(state, 5)) {
            return luaL_error(state, "Sprite:GetTexel expects an integer layer id");
        }
        layerId = static_cast<int>(lua_tointeger(state, 5));
    }

    const std::uintptr_t method = LuaRuntime::SpriteGetTexelThunk();
    if (method == 0) {
        return PushTexelFallback(state, 1U);
    }
    // `ANM2+0x60` 是 `AnimationState*`：引擎的 `ANM2::GetTexel` 第一件事就是
    // `add x0, x0, #0x60`（`0xC098`）再 tail-call `AnimationState::GetTexel`（`0xC0B8`）。
    // 状态为空说明还没 `Load`/`SetAnimation`，那时调下去就是空指针解引用。
    const std::uintptr_t spriteAddress = reinterpret_cast<std::uintptr_t>(sprite);
    std::uintptr_t animationState = 0;
    if (spriteAddress == 0 ||
        !ReadEngine(spriteAddress + kSpriteAnimationStateOffset, &animationState)) {
        return PushTexelFallback(state, 2U);
    }
    if (animationState == 0) {
        return PushTexelFallback(state, 3U);
    }

    struct EngineColor {
        float red;
        float green;
        float blue;
        float alpha;
    };
    EngineColor color{1.0F, 1.0F, 1.0F, 1.0F};
    // x0 = ANM2*（引擎入口自己再 +0x60）、s0..s3 = 两个 Vector2、s4 = 阈值、w1 = 层号、
    // x8 = 16 字节返回值缓冲。`memory` clobber 覆盖返回值缓冲与引擎内部可能的写入。
    __asm__ volatile(
        "mov x0, %[sprite]\n\t"
        "ldp s0, s1, [%[sample]]\n\t"
        "ldp s2, s3, [%[render]]\n\t"
        "fmov s4, %w[threshold]\n\t"
        "mov w1, %w[layer]\n\t"
        "mov x8, %[out]\n\t"
        "blr %[entry]\n\t"
        :
        : [sprite] "r"(spriteAddress), [sample] "r"(samplePos), [render] "r"(renderPos),
          [threshold] "r"(alphaThreshold), [layer] "r"(layerId), [out] "r"(&color),
          [entry] "r"(method)
        : "x0", "x1", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17",
          "x30", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "v8", "v9", "v10", "v11",
          "v12", "v13", "v14", "v15", "memory", "cc");
    RecordSpriteGetTexelForProbe(color.red, color.green, true);
    auto* handle = static_cast<ColorHandle*>(lua_newuserdata(state, sizeof(ColorHandle)));
    handle->red = color.red;
    handle->green = color.green;
    handle->blue = color.blue;
    handle->alpha = color.alpha;
    handle->offsetRed = 0.0F;
    handle->offsetGreen = 0.0F;
    handle->offsetBlue = 0.0F;
    luaL_getmetatable(state, kColorMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

// PC: `void RenderLayer(int LayerId, Vector Position, Vector TopLeftClamp = Vector.Zero,
//                       Vector BottomRightClamp = Vector.Zero)`。
// 引擎侧 `RenderLayer(int, Vector2 const&, Vector2 const&, Vector2 const&)`：`w1` 是层号，
// 后面三个 `Vector2 const&` 走 x2/x3/x4。
int SpriteRenderLayer(lua_State* state) {
    RecordApiSequence(49U);
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:RenderLayer", &sprite); error != 0) {
        return error;
    }
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    const int argumentCount = lua_gettop(state);
    if (argumentCount < 3 || argumentCount > 5 || !lua_isinteger(state, 2)) {
        return luaL_error(state,
                          "Sprite:RenderLayer accepts a layer id, a position and optional clamps");
    }
    const int layerId = static_cast<int>(lua_tointeger(state, 2));
    VectorHandle* position = CheckVector(state, 3);
    VectorHandle* topLeft = nullptr;
    VectorHandle* bottomRight = nullptr;
    static_cast<void>(RequireClampVectors(state, 3, &topLeft, &bottomRight));
    const std::uintptr_t method = LuaRuntime::SpriteRenderLayerThunk();
    if (method == 0) {
        return luaL_error(state, "Sprite:RenderLayer native binding is unavailable");
    }
    // 与 `Sprite:Render` 同一步：绘制前把 Mod 写的三个属性交给应用入口。
    ApplyCachedSpriteProperties(sprite, handle);
    using RenderLayerFn = void (*)(void*, int, const void*, const void*, const void*);
    reinterpret_cast<RenderLayerFn>(method)(sprite, layerId, position, topLeft, bottomRight);
    return 0;
}

}  // namespace

int SpriteIsLoaded(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:IsLoaded", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Sprite:IsLoaded accepts no arguments");
    }
    // 引擎自己的"图形已加载"标志（`ANM2+0x149`）：不发明状态，直接读它。
    //
    // 走 `ReadEngine`（先确认可读）而不是裸解引用：引擎实体来源的 sprite 只要实体还在就必然
    // 可读，但"读不到"必须降级成 `false` 而不是崩在一条只读访问器里。
    std::uint8_t flag = 0;
    if (!ReadEngine(reinterpret_cast<std::uintptr_t>(sprite) + kSpriteLoadedFlagOffset, &flag)) {
        lua_pushboolean(state, 0);
        return 1;
    }
    lua_pushboolean(state, flag != 0 ? 1 : 0);
    return 1;
}

// `Entity:GetSprite()` 的句柄（批次 5）。调用方（`isaac_api.cpp` 的 `EntityGetSprite`）已经
// 用 `ValidatedEntity` 校验过 `entity`，这里只负责建句柄 + 打上**非拥有**标记。
int PushEngineEntitySpriteHandle(lua_State* state, void* entity) {
    if (entity == nullptr) {
        return luaL_error(state, "Entity:GetSprite requires a live entity");
    }
    auto* handle = static_cast<SpriteHandle*>(lua_newuserdata(state, sizeof(SpriteHandle)));
    handle->sprite = reinterpret_cast<void*>(reinterpret_cast<std::uintptr_t>(entity) +
                                             kEntitySpriteOffset);
    handle->owner = entity;
    handle->source = kSpriteSourceEngineEntity;
    // 三个缓存属性的初值与 PC 的对象默认值一致（与 `CreateSpriteHandle` 同一套理由）。
    handle->scaleX = 1.0F;
    handle->scaleY = 1.0F;
    handle->colorRed = 1.0F;
    handle->colorGreen = 1.0F;
    handle->colorBlue = 1.0F;
    handle->colorAlpha = 1.0F;
    handle->flipX = 0;
    handle->hasScale = 0;
    handle->hasColor = 0;
    handle->hasFlipX = 0;
    luaL_getmetatable(state, kSpriteMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

int CreateSpriteHandle(lua_State* state) {
    if (lua_gettop(state) != 0) {
        return luaL_error(state, "Sprite accepts no arguments");
    }
    const std::uintptr_t ctor = LuaRuntime::SpriteCtorThunk();
    const std::uintptr_t destructorMethod = LuaRuntime::SpriteDestructorThunk();
    if (ctor == 0 || destructorMethod == 0) {
        return luaL_error(state, "Sprite native binding is unavailable");
    }
    auto* handle = static_cast<SpriteHandle*>(lua_newuserdata(state, sizeof(SpriteHandle)));
    handle->sprite = nullptr;
    // 三个缓存属性的初值与 PC 的对象默认值一致：Scale = (1,1)、Color = 白、
    // FlipX = false。`has*` 标志只用来区分"Mod 写过"与"没写过"（`__index` 据此决定
    // 是否返回缓存；两者取值相同，所以即使 Mod 从不写，读到的也是 PC 的默认形状）。
    handle->scaleX = 1.0F;
    handle->scaleY = 1.0F;
    handle->colorRed = 1.0F;
    handle->colorGreen = 1.0F;
    handle->colorBlue = 1.0F;
    handle->colorAlpha = 1.0F;
    handle->flipX = 0;
    handle->hasScale = 0;
    handle->hasColor = 0;
    handle->hasFlipX = 0;
    void* sprite = AllocateSpriteStorage();
    if (sprite == nullptr) {
        return luaL_error(state, "Sprite could not allocate its native object");
    }
    using CtorFn = void (*)(void*);
    reinterpret_cast<CtorFn>(ctor)(sprite);
    handle->sprite = sprite;
    // Lua 的 `Sprite()` 建出来的对象由**本模块**拥有：`__gc` 会析构并释放它。
    handle->owner = nullptr;
    handle->source = kSpriteSourceRuntimeOwned;
    luaL_getmetatable(state, kSpriteMetatable);
    lua_setmetatable(state, -2);
    return 1;
}

// PC 的 `Sprite:GetAnimation()`：**纯读**，不调引擎。
//
// 读的是 `ANM2+0x38`（指向当前动画名的 libc++ `std::string`），证据 = 引擎自己的
// `ANM2::IsPlaying(char const*)` 就是这么读的（`runtime_constants.hpp` 的
// `kSpriteAnimationNameOffset`）。没有当前动画（指针为 0）或读不到时返回**空串**：
// PC 的返回类型是 string，nil 会让 Mod 的 `name ~= "Idle"` 比较直接报
// "attempt to compare nil with string"；空串与两者都不相等，走的正是 EID 的"不隐藏描述"分支。
int SpriteGetAnimation(lua_State* state) {
    void* sprite = nullptr;
    if (const int error = ResolveSprite(state, "Sprite:GetAnimation", &sprite); error != 0) {
        return error;
    }
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Sprite:GetAnimation accepts no arguments");
    }
    std::array<char, kAnimationNameCapacity> name{};
    if (!ReadAnimationName(reinterpret_cast<std::uintptr_t>(sprite), name.data(), name.size())) {
        lua_pushliteral(state, "");
        return 1;
    }
    lua_pushstring(state, name.data());
    return 1;
}

// `__index`：先看三个缓存的属性，再落到元表 `__methods` 里的方法（与 `Vector` 同一形态：
// `__index` 必须是函数才能同时服务字段与方法）。
int SpriteIndex(lua_State* state) {
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    if (std::strcmp(field, kSpriteScaleField) == 0) {
        PushCachedScale(state, handle);
        return 1;
    }
    if (std::strcmp(field, kSpriteColorField) == 0) {
        PushCachedColor(state, handle);
        return 1;
    }
    if (std::strcmp(field, kSpriteFlipXField) == 0) {
        lua_pushboolean(state, handle->flipX != 0 ? 1 : 0);
        return 1;
    }
    if (luaL_getmetatable(state, kSpriteMetatable) != LUA_TTABLE) {
        lua_pushnil(state);
        return 1;
    }
    lua_getfield(state, -1, "__methods");
    lua_remove(state, -2);
    if (!lua_istable(state, -1)) {
        lua_pop(state, 1);
        lua_pushnil(state);
        return 1;
    }
    lua_getfield(state, -1, field);
    lua_remove(state, -2);
    return 1;
}

// `__newindex`：三个可写属性（大小写与 PC 一致）。
//
// 类型不对时**报错**（与 `Vector.X`/`KColor.Red` 一致）：把 `sprite.FlipX = 1` 这种写法
// 静默接受会让 Mod 的笔误变成"看起来生效了其实没有"。未知字段名同样报错 —— 这一批只承诺
// PC 的这三个属性，多接受一个没实现的名字等于撒谎。
int SpriteNewIndex(lua_State* state) {
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        return luaL_error(state, "Sprite fields are Scale, Color and FlipX");
    }
    const char* field = lua_tostring(state, 2);
    if (std::strcmp(field, kSpriteScaleField) == 0) {
        float x = 0.0F;
        float y = 0.0F;
        if (!ReadVectorPair(state, 3, &x, &y)) {
            return luaL_error(state, "Sprite.Scale must be a Vector");
        }
        handle->scaleX = x;
        handle->scaleY = y;
        handle->hasScale = 1;
        return 0;
    }
    if (std::strcmp(field, kSpriteColorField) == 0) {
        auto* color = static_cast<ColorHandle*>(luaL_testudata(state, 3, kColorMetatable));
        if (color == nullptr) {
            return luaL_error(state, "Sprite.Color must be a KColor");
        }
        handle->colorRed = color->red;
        handle->colorGreen = color->green;
        handle->colorBlue = color->blue;
        handle->colorAlpha = color->alpha;
        handle->hasColor = 1;
        return 0;
    }
    if (std::strcmp(field, kSpriteFlipXField) == 0) {
        if (lua_type(state, 3) != LUA_TBOOLEAN) {
            return luaL_error(state, "Sprite.FlipX must be a boolean");
        }
        handle->flipX = lua_toboolean(state, 3) != 0 ? 1 : 0;
        handle->hasFlipX = 1;
        return 0;
    }
    return luaL_error(state, "Sprite has no writable field named %s", field);
}

#if !defined(__SWITCH__)
void SetSpritePropertyApplyHostImplementation(
    SpritePropertyApplyHostImplementation function) noexcept {
    g_PropertyApplyHostImplementation = function;
}
#endif

int DestroySpriteHandle(lua_State* state) {
    auto* handle = static_cast<SpriteHandle*>(luaL_checkudata(state, 1, kSpriteMetatable));
    // ★ 引擎实体借出的 `Sprite`（`Entity:GetSprite()`）：这块 `ANM2` 是**引擎实体的成员**，
    // 不是本模块分配的。所以这里**什么都不做** —— 既不调 `~ANM2()`（那会把引擎对象的成员拆掉），
    // 也不 `free`（`handle->sprite` 不是 malloc 基址，`ReleaseSpriteStorage` 会把
    // `sprite-8` 里的字节当指针去 free）。
    //
    // 为什么"什么都不做"是对的：`__gc` 只在 Lua 已经无法再引用这个 userdata 时才跑，所以
    // "释放后句柄还能用"不会带来任何可观察行为；反过来，真实的实体 sprite 只能由引擎释放。
    // 这条分支就是所有权标记存在的唯一理由（去掉它 = 每次 GC 都踩一次引擎内存）。
    if (handle->source == kSpriteSourceEngineEntity) {
        return 0;
    }
    if (handle->sprite == nullptr) {
        return 0;  // 幂等：`__gc` 可能被显式 `collectgarbage()` 触发两次
    }
    const std::uintptr_t destructorMethod = LuaRuntime::SpriteDestructorThunk();
    if (destructorMethod != 0) {
        // `~ANM2()`（D1）只析构成员、**不释放内存** —— 内存由本模块分配，所以随后自己 free。
        using DtorFn = void (*)(void*);
        reinterpret_cast<DtorFn>(destructorMethod)(handle->sprite);
    }
    ReleaseSpriteStorage(handle->sprite);
    handle->sprite = nullptr;
    return 0;
}

std::size_t AttachSpriteMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kSpriteOwner, kSpriteHandlers,
                              sizeof(kSpriteHandlers) / sizeof(kSpriteHandlers[0]));
}

// 探针读取口（见 `sprite_api.hpp`）：定义在具名命名空间中，`content_mount_point_probe.cpp`
// 才能链到它；匿名命名空间里的那几个 `std::atomic` 仍只在本 TU 可见。
void SpriteGetTexelProbeSnapshot(std::uint32_t* calls, std::uint32_t* fallbacks,
                                 std::uint32_t* reason, std::uint64_t* lastWord) noexcept {
    if (calls != nullptr) *calls = g_SpriteGetTexelCalls.load(std::memory_order_relaxed);
    if (fallbacks != nullptr) *fallbacks = g_SpriteGetTexelFallbacks.load(std::memory_order_relaxed);
    if (reason != nullptr) *reason = g_SpriteGetTexelLastReason.load(std::memory_order_relaxed);
    if (lastWord != nullptr) *lastWord = g_SpriteGetTexelLastWord.load(std::memory_order_relaxed);
}

SpriteReplaceSpritesheetProbe SpriteReplaceSpritesheetProbeSnapshot() noexcept {
    SpriteReplaceSpritesheetProbe view{};
    view.calls = g_ReplaceSpritesheetCalls.load(std::memory_order_relaxed);
    view.nameLength = g_ReplaceSpritesheetNameLength.load(std::memory_order_relaxed);
    view.nameHead = g_ReplaceSpritesheetNameHead.load(std::memory_order_relaxed);
    return view;
}

} // namespace isaac::runtime
