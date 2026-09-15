#pragma once

#include <cstddef>
#include <cstdint>

#include "common.hpp"

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
#include "game_file_reader.hpp"
#endif

namespace LuaRuntime {

enum class LuaInitResult : u32 {
    Success,
    StateCreateFailed,
    RuntimePreparationMemoryFailed,
    RuntimePreparationFailed,
    ScriptLoadFailed,
    ScriptRunFailed,
    MissingPostUpdateCallback,
};

LuaInitResult Initialize();
LuaInitResult InitializeFromBuffer(const char* script, std::size_t length, const char* chunkName);
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
LuaInitResult InitializeManifestMod(const char* entry, std::size_t entryLength,
                                    const char* chunkName, const char* modRoot,
                                    const GameFileReader::Bindings& bindings);
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
LuaInitResult InitializeStage13Mod(const char* entry, std::size_t entryLength,
                                  const char* chunkName, const char* modRoot,
                                  const GameFileReader::Bindings& bindings);
u32 RequireFailureDetail();
u64 RequireErrorTail();
#endif
#endif
u32 PreparationFailureDetail();
void SetGameBindings(uintptr_t ownerSlot, uintptr_t isPausedThunk);
void SetGameIsGreedModeBinding(uintptr_t method);
void SetLevelIsAscentBinding(uintptr_t method);
// 批次 8（2026-09-15）：`Level` 家族另外两个成员方法的入口地址。
//   一次发布，两个都是"可选能力"——任一个为 0 时对应 handler 报"绑定不可用"，
//   绝不返回编造的值（`Level:GetAbsoluteStage()` 编个 0 会让 Mod 以为"在第一层"）。
void SetLevelGetAbsoluteStageBinding(uintptr_t method);
void SetLevelIsNextStageAvailableBinding(uintptr_t method);
void SetItemPoolGetCollectibleBinding(uintptr_t method);
void SetMusicBindings(uintptr_t getCurrentMusicId, uintptr_t pause, uintptr_t resume);

// Publishes the controller-input bindings resolved during Hook installation. Any of the
// three may be zero, and the handlers then report an unavailable binding rather than a
// fabricated reading.
void SetInputBindings(uintptr_t isActionPressed, uintptr_t isActionTriggered,
                      uintptr_t getActionValue);
void SetRngBindings(uintptr_t setSeed, uintptr_t next);

// The `Font` family's native entry points, published as one record because an eleven-argument
// setter would be unreadable at the call site. Every field may stay zero: the Font handlers then
// report an unavailable binding instead of calling through a null pointer, and the family is an
// optional capability like the controller input bindings.
//
// The record holds the game module's addresses only. The storage for a Font instance is *not*
// part of this contract: `font_api.cpp` allocates and releases that block with this module's own
// `malloc`/`free` (see the comment there), which keeps the published record exactly equal to
// what `VerifyFontBindings` proved.
struct LuaFontBindings {
    uintptr_t ctor{0};
    uintptr_t destructor_{0};  // `~Font()`; the audit shows it is a tail-jump to `Unload()`
    uintptr_t load{0};
    uintptr_t unload{0};
    uintptr_t isLoaded{0};
    uintptr_t getStringWidth{0};
    uintptr_t getStringWidthUTF8{0};
    uintptr_t getLineHeight{0};
    uintptr_t getBaselineHeight{0};
    uintptr_t getCharacterWidth{0};
    uintptr_t setMissingCharacter{0};
    uintptr_t drawString{0};
    // 三个绘制变体（PC 同族的 `DrawStringScaled`/`DrawStringUTF8`/`DrawStringScaledUTF8`）。
    // 缩放走 s2/s3，其余参数与 `drawString` 一致；EID 用的是 `DrawStringScaledUTF8`。
    uintptr_t drawStringScaled{0};
    uintptr_t drawStringUTF8{0};
    uintptr_t drawStringScaledUTF8{0};
};
void SetFontBindings(const LuaFontBindings& bindings);

// `Sprite`（引擎侧 `IsaacRepentance::ANM2`）第一步的入口集合：全部只收 `char const*` 与数值，
// 因此不涉及 libc++/libstdc++ 的 `std::string` ABI 差异（`Load`/`ReplaceSpritesheet` 需要那层桥，
// 放在第二步）。和 Font 一样：字段可以保持 0，handler 于是报“绑定不可用”，绝不调用未校验的地址。
struct LuaSpriteBindings {
    uintptr_t ctor{0};
    uintptr_t destructor_{0};  // `~ANM2()`（D1）：析构成员但**不释放 this**
    uintptr_t play{0};
    uintptr_t setAnimation{0};
    uintptr_t setFrameNamed{0};
    uintptr_t setFrame{0};
    uintptr_t getFrame{0};
    uintptr_t setLayerFrame{0};
    uintptr_t getLayerFrame{0};
    // `GetTexel(Vector2 samplePos, Vector2 renderPos, float alphaThreshold, int layerId)`：
    // **两个 `Vector2` 按值传**（AAPCS64 的 HFA 规则 → s0..s3 / 一般寄存器不必参与），
    // 返回 16 字节 `KColor` 走 x8 间接结果寄存器。ABI 解码见
    // `analysis/stage150-sprite-abi/<build>.json` 的 `records.sprite_get_texel.abi`
    // （confidence = verified，符号与 16 字节守卫都与 NRO 逐字节核对过）。
    uintptr_t getTexel{0};
    uintptr_t update{0};
    uintptr_t isPlaying{0};
    uintptr_t isFinished{0};
    uintptr_t render{0};
    uintptr_t renderLayer{0};
    uintptr_t playRandom{0};
    // 第二步：需要 libc++ `std::string` 的三个入口，外加借来代建字符串的
    // `basic_string::assign(char const*)`（游戏导入的 PLT 桩）。
    uintptr_t load{0};
    uintptr_t loadGraphics{0};
    uintptr_t replaceSpritesheet{0};
    uintptr_t libcxxStringAssign{0};
    // 释放"落在堆上的长路径"要用**游戏自己的** `operator delete`（见 `kGameOperatorDeleteOffset`）。
    uintptr_t gameOperatorDelete{0};
};
void SetSpriteBindings(const LuaSpriteBindings& bindings);
void DispatchPostUpdate();
// 引擎侧的开局中继只记账、不在这里跑 Lua（它运行在 `Game::Start` 刚返回的那一刻，
// 那时候调用模组回调风险高）。真正的派发在下一帧的 `DispatchPostUpdate()` 里做。
// `isSave`：真 = 读档进游戏，假 = 新开一局（来自中继的事件种类）。
void NoteGameStarted(bool isSave) noexcept;
void DispatchPostRender(uintptr_t manager);
std::uint64_t DispatchPreGetCollectible(void* itemPool, std::uint32_t itemPoolType,
                                        std::uint32_t seed, std::uint32_t noDecrease,
                                        std::uint32_t lastCollectibleType);
#if !defined(EXL_DIAGNOSTIC_STAGE)
bool IsMusicReadyForDefaultCallback(uintptr_t manager);
#endif
bool IsReady();
u32 PostUpdateCount();
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
u32 PostRenderCount();
u32 PostRenderPausedCount();
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17)
bool MusicDiagnosticCycleCompleted();
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
bool MusicDiagnosticCurrentId(u32* value);
#endif
bool TakeCallbackError();

// Probe-facing read-only access to the Lua state, used to turn "did the Mod actually run"
// into machine evidence instead of an audible guess:
//   * `ReadLuaGlobalNumber` reads a global the Mod itself wrote (a progress marker);
//   * `RegisteredCallbackCount` reports how many callbacks of one id are still registered --
//     a Lua error inside a callback makes the dispatcher drop that callback, so a falling
//     count is how a swallowed error becomes visible.
[[nodiscard]] bool ReadLuaGlobalNumber(const char* name, double* value);

// 读 `<global>.<field>`：先 `lua_getglobal(global)`，再在**表上**取字段。
//
// 为什么需要它（2026-09-12 的教训）：探针用 `ReadLuaGlobalNumber("EID.GameRenderCount")`
// 连读两次都答"没有"，而同一份报告里 update/render 回调进入次数是 900/878 —— 两者不可能同真，
// 说明那条通道给不出可信结论。EID 的 `EID` 是一张表（`RegisterMod` 的返回值再加字段），
// 所以这里按"表 + 字段"取，并且把**取不到的原因**分成三种报出来：
//   1 = 全局不是表（`EID` 本身没建出来）
//   2 = 表里没有这个字段（值是 nil）
//   3 = 字段在但不是 number
//   0 = 成功（此时 `*value` 有效）
[[nodiscard]] std::uint32_t ReadLuaTableNumber(const char* globalName, const char* fieldName,
                                               double* value);
[[nodiscard]] bool ReadLuaTableBoolean(const char* globalName, const char* fieldName,
                                       bool* value);
// 读 `<global>.<table>.<field>`（例如 `EID.Config.HideInBattle`）。语义与两级版本一致。
[[nodiscard]] std::uint32_t ReadLuaNestedNumber(const char* globalName, const char* tableName,
                                                const char* fieldName, double* value);
[[nodiscard]] bool ReadLuaNestedBoolean(const char* globalName, const char* tableName,
                                        const char* fieldName, bool* value);
// 读 `<global>.<field>` 是不是一张表（用来区分"字段不存在"与"字段是一张表"）。
[[nodiscard]] bool LuaTableFieldIsTable(const char* globalName, const char* fieldName);
[[nodiscard]] std::uint32_t RegisteredCallbackCount(std::uint32_t callbackId);
#if defined(EXL_PERSISTENCE_TRACE) || defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
u64 CallbackFailureDetail();
#endif

} // namespace LuaRuntime
