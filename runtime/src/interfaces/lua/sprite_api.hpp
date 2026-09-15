#pragma once

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// `Sprite` as a Lua object family (step 1: everything that takes only `char const*` and numbers).
//
// The audit (`analysis/stage150-sprite-abi/`) identifies Lua's `Sprite` with the engine's
// `IsaacRepentance::ANM2`, and `tools/stage155_anm2_size_audit.py` settles what step 1 needed
// most: the object is **0x158 bytes**, proven twice over (the game's own `operator new(0x158)`
// immediately before `ANM2::ANM2()` in `AnmCache::CreateGlobalAnim`, and three consecutive ANM2
// members spaced exactly 0x158 apart in one parent object).
//
// Step 1 deliberately excludes `Load` and `ReplaceSpritesheet`: those take libc++'s
// `std::string const&` (24 bytes) while this module is built against libstdc++ (32 bytes), so they
// need a separately verified string bridge. Everything here takes `char const*`/numbers, so there
// is no ABI risk at all.
[[nodiscard]] std::size_t AttachSpriteMethods(lua_State* state) noexcept;

// Lua constructor `Sprite()`; the Runtime allocates and owns the native object (16-byte aligned).
int CreateSpriteHandle(lua_State* state);

// `__gc`: runs the game's complete-object destructor (`~ANM2()`, which does **not** free `this`)
// and then releases the block this module allocated.
int DestroySpriteHandle(lua_State* state);

// Reads the engine's own "graphics loaded" flag (`ANM2+0x149`) instead of inventing state.
int SpriteIsLoaded(lua_State* state);

// --- `Sprite:GetTexel` 的探针（2026-09-12，动作一）---------------------------------
//
// `GetTexel` 是 EID 判"赎罪线问号底座"的唯一依据，而它的调用约定（两个 `Vector2` 按 HFA
// 走 s0..s3、返回值走 x8）是本轮唯一有风险的改动。探针把四件事暴露给崩溃报告：
//   * `calls`     —— 引擎入口真正被调到的次数；
//   * `fallbacks` —— 走了"不透明白色"落值的次数；
//   * `reason`    —— 最近一次落值的原因码（1 = 无引擎入口，2 = sprite 不可读，
//                    3 = 没有当前动画状态，即 `ANM2+0x60 == 0`）；
//   * `lastWord`  —— 最近一次**真实读数**：低 32 位 = R 的位模式，高 32 位 = G 的位模式。
//                    全 0 = "引擎说这个采样点是空像素"，非 0 = 真读到了贴图颜色。
struct SpriteGetTexelProbeView {
    std::uint32_t calls{0};
    std::uint32_t fallbacks{0};
    std::uint32_t reason{0};
    std::uint64_t lastWord{0};
};

void SpriteGetTexelProbeSnapshot(std::uint32_t* calls, std::uint32_t* fallbacks,
                                 std::uint32_t* reason, std::uint64_t* lastWord) noexcept;

// --- `Sprite:ReplaceSpritesheet` 的探针（2026-09-12 第六轮）------------------------
//
// 真机连续两轮都停在这个函数上，而 `BuildNativeString` 的三条失败路径（参数不是字符串、
// 路径过长、`assign` 绑定缺失）返回的都是 nullptr —— 光看"失败"分不清是哪条。
// 这个快照给出调用时的**文件名长度**与**前 8 字节**：
//   * 长度 = 0xFFFFFFFF ⇒ 参数根本不是字符串；
//   * 长度 > 191        ⇒ 路径超出容量；
//   * 长度正常          ⇒ 看前 8 字节是不是可读路径。
void RecordSpriteReplaceSpritesheetForProbe(std::uint32_t length, const char* name) noexcept;
struct SpriteReplaceSpritesheetProbe {
    std::uint32_t calls{0};
    std::uint32_t nameLength{0};
    std::uint64_t nameHead{0};
};
[[nodiscard]] SpriteReplaceSpritesheetProbe SpriteReplaceSpritesheetProbeSnapshot() noexcept;

// --- 批次 5：`Entity:GetSprite()` --------------------------------------------------
//
// 推入一个**引擎实体借出**的 `Sprite` 句柄（原生对象 = `entity + kEntitySpriteOffset`，见
// `runtime_constants.hpp` 的 `kEntitySpriteOffset` 证据）。调用方（`isaac_api.cpp` 的
// `EntityGetSprite`）必须已经用 `ValidatedEntity` 校验过这个实体指针。
//
// 与 `CreateSpriteHandle`（Lua 的 `Sprite()` 构造）的关键区别是**所有权**：这块 `ANM2` 是引擎
// 实体自己的成员，所以句柄带 `kSpriteSourceEngineEntity` 标记 ——
//   * `__gc` 不调 `~ANM2()`、不 `free`；
//   * 每次访问都重新用 `IsLiveEntityPointer` 校验来源实体还在，并用 `owner + 偏移` 重新算出
//     原生地址（句柄里缓存的地址只当身份，不作为解引用依据）。
// 详情与理由写在 `lua_object_handles.hpp` 的 `SpriteHandle` 注释里。
int PushEngineEntitySpriteHandle(lua_State* state, void* entity);

// PC 的 `Sprite:GetAnimation()`：**只读**，没有引擎调用 —— 动画名就是 `ANM2+0x38` 指向的
// libc++ `std::string`（引擎自己的 `ANM2::IsPlaying(char const*)` 就是这么读的，见
// `runtime_constants.hpp` 的 `kSpriteAnimationNameOffset`）。没有当前动画时返回空串。
int SpriteGetAnimation(lua_State* state);

// --- 批次 3：`Sprite` 的可写属性与字段读取 --------------------------------------
//
// `__index` / `__newindex`。方法查找仍然走元表的 `__methods`（`AttachSpriteMethods` 挂上去的
// 那一张），这两个函数只额外处理 PC 的三个可写属性：
//
//   `sprite.Scale = Vector(x, y)`       （EID `features/eid_api.lua:1368`、`main.lua:1026`）
//   `sprite.Color = Color(r,g,b,a,...)` （EID `features/eid_api.lua:1369`、`main.lua:960`）
//   `sprite.FlipX = true/false`         （EID `main.lua:946`/`982`）
//
// 没有 `__newindex` 时，给 userdata 赋值会直接报 "attempt to index a userdata value"，
// 而那三行在 EID 的**绘制路径**上，所以缺了它第一次画东西就会炸。
int SpriteIndex(lua_State* state);
int SpriteNewIndex(lua_State* state);

#if !defined(__SWITCH__)
// 宿主注入点。**设备构建（`-D__SWITCH__`）里不存在这个类型和这个函数。**
//
// 为什么需要它：`Sprite:Render`/`RenderLayer` 会在把绘制交给引擎之前，把句柄里缓存的
// Scale/Color/FlipX 交给"应用到原生对象"这一步；设备侧这一步**目前是空操作**（这三个属性
// 在 `ANM2` 里的偏移没有证据，见 `sprite_api.cpp`）。宿主测试用这个注入点证明
// "渲染路径确实读取了缓存里的值"，也就是本批次 `__newindex` 的验收口径；
// 真正回写引擎要等 ANM2 属性偏移定位之后再补。
//
// 第二个参数是 `LuaRuntime::SpriteHandle const*`（本头文件不引 `lua_object_handles.hpp`，
// 保持家族头只依赖 `lua.h` 的约定；宿主 harness 自己转成具体类型读字段）。
using SpritePropertyApplyHostImplementation = void (*)(void* sprite, const void* handle);
void SetSpritePropertyApplyHostImplementation(
    SpritePropertyApplyHostImplementation function) noexcept;
#endif

} // namespace isaac::runtime
