#pragma once

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

// Shared Runtime state for the Lua API family translation units.
//
// The family units (`runtime/src/interfaces/lua/*_api.cpp`) own the handlers,
// while `lua_runtime.cpp` still owns the Lua state, the managed-callback
// dispatcher, the native binding slots and the metatable registration. This
// header is the single transitional place both sides agree on, so a family can
// move out without reaching into another translation unit's internals.
//
// It lives in the legacy source root on purpose: non-layered/probe builds do
// not have `runtime/src` on their include path.
//
// Every accessor here is a stepping stone towards a real port/service
// (`IGameMemoryPort`, callback scope, ...); the declarations stay until the
// owning subsystem moves.
// --- Managed callback registry -------------------------------------------
//
// Declared outside `namespace LuaRuntime` on purpose: this header is included
// from inside `namespace LuaRuntime` in `lua_runtime.cpp`, so anything that must
// belong to the global `isaac::runtime` namespace has to be declared before that
// namespace opens (otherwise it lands in `LuaRuntime::isaac::runtime`).
//
// Forward declarations only: this header must stay includable from host tests
// that do not compile the callback registry.
namespace isaac::runtime {
class CallbackRegistry;
struct ModHandle;
} // namespace isaac::runtime

namespace LuaRuntime {


// --- Mod userdata ---------------------------------------------------------

inline constexpr char kModMetatable[] = "IsaacRuntime.Mod";

struct ModHandle {
    std::uint64_t persistenceNamespace;
};

// Which persistence operation the current managed callback is running. The
// numeric values are a wire format for the trace/journal, so keep them stable.
enum class CallbackOperation : std::uint8_t {
    None = 0,
    SaveData,
    LoadData,
    HasData,
    RemoveData,
};

// Reports the current operation to the Runtime diagnostics. Defined in
// `lua_runtime.cpp`, which owns the diagnostic state.
void RecordCallbackOperation(CallbackOperation operation) noexcept;

// --- Callback registration census ------------------------------------------
//
// PC Mods register many `ModCallbacks` kinds during load; the Runtime only has
// dispatch sites for a few of them (see `HasDispatchSite` in `mod_api.cpp`).
// Registering the rest is allowed on purpose, so this census is how a hardware
// run can tell "registered but never fires" apart from "never registered".
// Defined in `interfaces/lua/mod_api.cpp`（唯一调用方，且一定被打包构建编译）。
void RecordCallbackRegistration(std::uint32_t callbackId, bool hasDispatchSite) noexcept;

// 回调里出过 Lua 错误（只读，不消费；消费入口仍是 `TakeCallbackError`）。诊断要能在
// 不改变既有“取走即清零”语义的前提下报告这个状态。
[[nodiscard]] bool CallbackErrorPending() noexcept;

// 最近一次 Lua 错误（或 Mod 加载失败）的信息：返回前 8 字节（不足补零），`length`
// 收到完整长度。真机上文件写入不可靠，所以错误信息用这种"装进诊断字"的方式留证。
[[nodiscard]] std::uint64_t LastLuaErrorHead(std::uint32_t* length) noexcept;
// `require` 失败的快照：第一次失败与最后一次失败的代码与模块名头 8 字节，以及失败总次数。
// 为什么要它：Mod 普遍用 `pcall(require, ...)` 并静默容忍失败（EID 就是这样），于是真机上
// 只能看到"后面某处索引到了 nil"，看不到**是哪一个 require 挂了、挂在哪一类**。
struct RequireFailureView {
    std::uint32_t firstCode;
    std::uint64_t firstNameHead;
    std::uint32_t lastCode;
    std::uint64_t lastNameHead;
    std::uint32_t total;
};
void RequireFailureSnapshot(RequireFailureView* view) noexcept;
// 模块假堆的已用字节数（`sbrk(0) - __fake_heap`）。整个模块的 malloc 都在这块 .bss 数组里，
// 所以它同时是"Lua + 其它一切"的峰值用量，用来判断 `exl::setting::HeapSize` 够不够。
[[nodiscard]] std::size_t HeapUsedBytes() noexcept;
// 整段错误文本（最多拷 `capacity` 字节，返回拷贝长度）。诊断用：8 字节的头往往只够认出
// "是哪一份脚本"（Lua 5.3.3 的 `luaO_chunkid` 会把长路径截成 "...+末 57 字符"），
// 真正要定位问题需要的是**行号 + 消息**，那在后面。
[[nodiscard]] std::size_t CopyLastLuaErrorText(char* output, std::size_t capacity) noexcept;
// 记录一次失败信息（`InitializeScript` 与回调派发的 Lua 错误路径调用）。
void RecordLuaErrorText(const char* text, std::size_t length) noexcept;

// Bit `id` is set when that `ModCallbacks` value was registered at least once.
// Low covers ids 0..63, high covers 64..127 (the PC table ends at 73).
[[nodiscard]] std::uint64_t RegisteredCallbackKindMaskLow() noexcept;
[[nodiscard]] std::uint64_t RegisteredCallbackKindMaskHigh() noexcept;
// Registrations whose kind has a dispatch site, and those that never fire.
[[nodiscard]] std::uint32_t DispatchableCallbackRegistrationCount() noexcept;
[[nodiscard]] std::uint32_t UnhookedCallbackRegistrationCount() noexcept;

// The Stage13 manifest/require diagnostic registers its POST_UPDATE phase under
// a Lua callback value of its own (the normal MC_POST_UPDATE is 1), so the
// mapping in the `Mod` family needs both the value and whether that diagnostic
// is running.
inline constexpr int kStage13CallbackValue = 1;

// True while the Runtime runs the Stage13 diagnostic. Defined in
// `lua_runtime.cpp`, which owns the Stage13 context.
[[nodiscard]] bool IsStage13CallbackMode() noexcept;

// --- Managed callback scope ----------------------------------------------

// True while the Runtime is invoking a Mod callback, i.e. while the game
// thread is in a state where native objects may be read. API families reject
// calls outside this scope instead of touching game memory from an unsafe
// point.
[[nodiscard]] bool InManagedCallbackScope() noexcept;

// --- Game binding slots ---------------------------------------------------

// Native addresses resolved by the Hook installation. Zero means "not
// available"; the handlers turn that into a Lua error instead of calling a
// null pointer.
[[nodiscard]] std::uintptr_t GameOwnerSlot() noexcept;
[[nodiscard]] std::uintptr_t GameIsPausedThunk() noexcept;
// 游戏本体 NRO（`Repentance.nro`）模块的加载基址。上面的槽/跳板地址都是模块内偏移，只有加上
// 基址才是绝对地址；`Isaac.GetPlayer` 的整条链路（全局槽 → Game* → players 向量 → Entity_Player）
// 与 `EntityPlayer` 的 vptr 判据都建立在基址上。
//
// 与 `GameOwnerSlot` 同一种模式：由 Hook 安装阶段在已经拿到 `TargetModule::base` 的地方发布，
// 0 表示"尚未发布"——handler 据此返回 nil（而不是去解引用一个 0 基址）。
[[nodiscard]] std::uintptr_t EngineModuleBase() noexcept;
// 发布基址（Hook 安装阶段调用一次，与 `SetGameBindings` 同一时机）。声明放在这里而不是
// `lua_runtime.hpp`：消费方是 `interfaces/lua` 的家族 TU，它们只依赖本头。
void SetEngineModuleBase(std::uintptr_t base) noexcept;
[[nodiscard]] std::uintptr_t GameIsGreedModeThunk() noexcept;
[[nodiscard]] std::uintptr_t LevelIsAscentThunk() noexcept;
[[nodiscard]] std::uintptr_t ItemPoolGetCollectibleThunk() noexcept;
[[nodiscard]] std::uintptr_t MusicGetCurrentMusicIdThunk() noexcept;
[[nodiscard]] std::uintptr_t MusicPauseThunk() noexcept;
[[nodiscard]] std::uintptr_t MusicResumeThunk() noexcept;
[[nodiscard]] std::uintptr_t RngSetSeedThunk() noexcept;
[[nodiscard]] std::uintptr_t RngNextThunk() noexcept;
// Input bindings (controller actions). Zero means "not available", which the handlers turn
// into a Lua error instead of answering a fabricated value.
[[nodiscard]] std::uintptr_t InputIsActionPressedThunk() noexcept;
[[nodiscard]] std::uintptr_t InputIsActionTriggeredThunk() noexcept;
[[nodiscard]] std::uintptr_t InputGetActionValueThunk() noexcept;
// Font bindings (`KAGE::Graphics::Font`). Zero means "not available", which the handlers turn
// into a Lua error instead of calling an unverified address. The Font object itself is not
// game-owned: the family unit owns its storage and only these entry points come from the game.
[[nodiscard]] std::uintptr_t FontCtorThunk() noexcept;
[[nodiscard]] std::uintptr_t FontDestructorThunk() noexcept;
[[nodiscard]] std::uintptr_t FontLoadThunk() noexcept;
[[nodiscard]] std::uintptr_t FontUnloadThunk() noexcept;
[[nodiscard]] std::uintptr_t FontIsLoadedThunk() noexcept;
[[nodiscard]] std::uintptr_t FontGetStringWidthThunk() noexcept;
[[nodiscard]] std::uintptr_t FontGetStringWidthUTF8Thunk() noexcept;
[[nodiscard]] std::uintptr_t FontGetLineHeightThunk() noexcept;
[[nodiscard]] std::uintptr_t FontGetBaselineHeightThunk() noexcept;
[[nodiscard]] std::uintptr_t FontGetCharacterWidthThunk() noexcept;
[[nodiscard]] std::uintptr_t FontSetMissingCharacterThunk() noexcept;
[[nodiscard]] std::uintptr_t FontDrawStringThunk() noexcept;
[[nodiscard]] std::uintptr_t FontDrawStringScaledThunk() noexcept;
[[nodiscard]] std::uintptr_t FontDrawStringUTF8Thunk() noexcept;
[[nodiscard]] std::uintptr_t FontDrawStringScaledUTF8Thunk() noexcept;

// Sprite 绑定（`IsaacRepentance::ANM2`）。0 表示不可用，handler 会报错，而不是调用未校验的地址。
[[nodiscard]] std::uintptr_t SpriteCtorThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteDestructorThunk() noexcept;
[[nodiscard]] std::uintptr_t SpritePlayThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteSetAnimationThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteSetFrameNamedThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteSetFrameThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteGetFrameThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteSetLayerFrameThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteGetLayerFrameThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteGetTexelThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteUpdateThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteIsPlayingThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteIsFinishedThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteRenderThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteRenderLayerThunk() noexcept;
[[nodiscard]] std::uintptr_t SpritePlayRandomThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteLoadThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteLoadGraphicsThunk() noexcept;
[[nodiscard]] std::uintptr_t SpriteReplaceSpritesheetThunk() noexcept;
[[nodiscard]] std::uintptr_t LibcxxStringAssignThunk() noexcept;
// 游戏自己的 `operator delete(void*)`：释放游戏分配器给长路径分配的缓冲（见 `kGameOperatorDeleteOffset`）。
[[nodiscard]] std::uintptr_t GameOperatorDeleteThunk() noexcept;

// The ItemPool method guard still lives in `lua_runtime.cpp` next to the rest
// of the instruction checks; the family unit only needs the verdict.
[[nodiscard]] bool ValidateItemPoolGetCollectibleBinding(std::uintptr_t address) noexcept;


// --- Music and RNG call targets ------------------------------------------

// Resolving a Music call needs two things the family unit must not own yet:
// the callback's native Manager pointer and the expected instruction bytes of
// each method. Both live in `lua_runtime.cpp` (the Manager slot is written by
// the managed-callback dispatcher, the byte guards sit next to the other
// instruction checks), so the accessors return the verdict and, on success, the
// resolved Music object address. `false` means "native bindings are
// unavailable"; the handlers turn that into the same Lua error as before.
[[nodiscard]] bool ResolveMusicGetCurrentMusicId(std::uintptr_t method,
                                                 std::uintptr_t* music) noexcept;
[[nodiscard]] bool ResolveMusicPause(std::uintptr_t method, std::uintptr_t* music) noexcept;
[[nodiscard]] bool ResolveMusicResume(std::uintptr_t method, std::uintptr_t* music) noexcept;

// `RNG:SetSeed` and `RNG:Next` only need the method guard verdict: the RNG
// object is the Lua userdata itself, not a native pointer.
[[nodiscard]] bool ValidateRngSetSeedBinding(std::uintptr_t address) noexcept;
[[nodiscard]] bool ValidateRngNextBinding(std::uintptr_t address) noexcept;

[[nodiscard]] isaac::runtime::CallbackRegistry& ManagedCallbackRegistry() noexcept;
[[nodiscard]] isaac::runtime::ModHandle RuntimeOwnerHandle() noexcept;

// --- Lua 报错通道的加固（2026-09-12）-------------------------------------------------
//
// 背景：真机报告的"错误文本"一直不可信 —— `errorLength = 1` 而负载里却读出
// `"...ile name"` 之类的**残留**。原因是 `CaptureLuaErrorTop` 用 `lua_tolstring`
// 取值，而 **Lua 允许 `error(任何值)`**：错误值不是字符串时它什么都不记，长度与内容
// 就会不一致，报告侧读到上一条消息的残渣。
//
// 修法（两层）：
//   1. `RecordLuaErrorValue`：把"任意 Lua 值"转成可读文本再记录（字符串直接用；
//      数字/布尔/nil 用 `lua_tostring`/`lua_typename`；其余用 `luaL_tolstring`——
//      它接受**任意**值并保证产出字符串，且自己负责压栈/弹栈）。
//   2. 本头文件里把 `luaL_error` 改成"先记录整段消息、再真正抛错"的包装宏。
//      `luaL_error` 的消息就是最终文本（含 `file:line:` 前缀），所以这样记录下来的
//      就是**消息本体**，不再依赖 `lua_tostring` 的成功与否。
//
// ★ 为什么宏写在头文件里：本项目有多个 TU 调用 `luaL_error`（约 34 处），逐个改容易漏。
// 宏只在**包含本头文件之后**生效，且用 `push_macro/pop_macro` 保证不泄漏。
// ★ **已知不覆盖**：直接调 `lua_error`（不走 `luaL_error`）的路径；本项目目前没有这种调用，
// 若以后出现，需要一并包装。
void RecordLuaErrorValue(lua_State* state) noexcept;
int ReportAndRecordLuaError(lua_State* state, const char* format, ...);

} // namespace LuaRuntime


// `luaL_error` 的包装：见上面 `ReportAndRecordLuaError` 的注释。
#pragma push_macro("luaL_error")
#undef luaL_error
#define luaL_error(state, ...) ::LuaRuntime::ReportAndRecordLuaError((state), __VA_ARGS__)
