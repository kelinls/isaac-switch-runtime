#pragma once

#include "module_finder.hpp"

#include <cstdint>

uintptr_t target_address(uintptr_t base, uintptr_t offset);
bool verify_bytes(const u8* expected, const u8* actual, size_t length);

bool VerifyManagerUpdate(const TargetModule& module, uintptr_t* target);
bool VerifyManagerRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot);
bool VerifyManagerRender(const TargetModule& module, uintptr_t* target);
bool VerifyManagerRenderRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot);
enum class RenderHookInstallResult {
    Success,
    InstructionMismatch,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
RenderHookInstallResult TryInstallManagerRenderHook(const TargetModule& module);
// `MC_POST_RENDER` 的派发点从 `Manager::Render` 入口中继搬到该函数体内最后一次 `Present`
// 调用之前（补丁种类 `render-present-relay`）。入口中继是在 Present 之后派发的，于是回调里
// 画的东西进的是下一帧队列，并被实体（以撒）与 HUD 盖住；详见 runtime_constants.hpp。
enum class RenderPresentRelayInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
    // M2b：改 GOT 槽的方案（零代码字节），失败码继续往后排，旧值不动。
    GotSlotTargetMismatch = 4,   // 桩 / `Present` 本体 / 调用点三段守卫任一不符（含"IPS 还没删"）
    GotSlotUnavailable = 5,      // 槽不在已映射页里，或槽内当前值是 0（加载器还没解析出来）
    GotSlotWriteFailed = 6,      // 写完读回不一致
};
RenderPresentRelayInstallResult TryInstallManagerPresentRelay(const TargetModule& module);
// M2b：把 `Present` 的 GOT 槽改写成我们的拦截函数（只改数据、零代码字节），
// 拦截函数按调用方过滤，只有那**一处**调用点会派发 `MC_POST_RENDER`。
RenderPresentRelayInstallResult TryInstallManagerPresentGotSlot(const TargetModule& module);
enum class PreGetCollectibleRelayInstallResult {
    Success,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
PreGetCollectibleRelayInstallResult TryInstallPreGetCollectibleRelay(const TargetModule& module);

// Stage 148: verify and publish the rebuild relay, which restores Mod content mount
// points after the engine rebuilds its mount point table. Reports true only when the
// relay entry, its cave and the (still empty) callback slot all match the audited build
// and the callback address was published. A false result is not fatal for Mod loading:
// it only means mount points are not restored after a rebuild.
bool TryInstallRebuildMountPointsRelay(const TargetModule& module);
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
enum class Stage102ChangeRoomInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage102ChangeRoomInstallResult TryInstallStage102ChangeRoomDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
enum class Stage104RoomKeyInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage104RoomKeyInstallResult TryInstallStage104RoomKeyDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
enum class Stage112DescriptorReentryInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage112DescriptorReentryInstallResult TryInstallStage112DescriptorReentryDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
enum class Stage108LifecycleInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage108LifecycleInstallResult TryInstallStage108LifecycleDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
enum class Stage109RestartInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage109RestartInstallResult TryInstallStage109RestartDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
enum class Stage127SaveLoadInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage127SaveLoadInstallResult TryInstallStage127SaveLoadDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
enum class Stage128SaveDataManagerInstallResult {
    Success = 0,
    RelayPatchMismatch = 1,
    RelaySlotNotEmpty = 2,
    RelayPublishFailed = 3,
};
Stage128SaveDataManagerInstallResult TryInstallStage128SaveDataManagerDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
enum class Stage110ChangeRoomInstallResult { Success = 0, RelayPatchMismatch = 1, RelaySlotNotEmpty = 2, RelayPublishFailed = 3 };
enum class Stage110LifecycleInstallResult { Success = 0, RelayPatchMismatch = 1, RelaySlotNotEmpty = 2, RelayPublishFailed = 3 };
Stage110ChangeRoomInstallResult TryInstallStage110ChangeRoomDiagnostic(const TargetModule& module);
Stage110LifecycleInstallResult TryInstallStage110LifecycleDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
enum class Stage118InstallResult { Success = 0, FileBindingMismatch = 1, HookInstallFailed = 2 };
Stage118InstallResult TryInstallStage118ManagedFileWriteDiagnostic(const TargetModule& module);
#endif
bool VerifyGameObserverRelay(const TargetModule& module, uintptr_t* slot);
enum class GameObserverInstallResult {
    Success,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
GameObserverInstallResult TryInstallGameObserverRelay(const TargetModule& module);
bool VerifyGameUpdateObserverRelay(const TargetModule& module, uintptr_t* slot);
enum class GameUpdateObserverInstallResult {
    Success,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
GameUpdateObserverInstallResult TryInstallGameUpdateObserverRelay(const TargetModule& module);
bool VerifyGameState2ObserverRelay(const TargetModule& module, uintptr_t* slot);
enum class GameState2ObserverInstallResult {
    Success,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
GameState2ObserverInstallResult TryInstallGameState2ObserverRelay(const TargetModule& module);
enum class GameRenderObserverInstallResult { Success, RelayPatchMismatch, RelaySlotNotEmpty, RelayPublishFailed };
GameRenderObserverInstallResult TryInstallGameRenderObserverRelay(const TargetModule& module);
bool VerifyGameOwnerSlot(const TargetModule& module, uintptr_t* slot);
bool VerifyGameIsPausedThunk(const TargetModule& module, uintptr_t* thunk);
bool VerifyGameIsGreedMode(const TargetModule& module, uintptr_t* method);
bool VerifyLevelIsAscent(const TargetModule& module, uintptr_t* method);
bool VerifyManagerIsActionTriggered(const TargetModule& module, uintptr_t* method);
bool VerifyMusicBindings(const TargetModule& module, uintptr_t* getCurrentMusicId,
                         uintptr_t* pause, uintptr_t* resume);
bool VerifyRngBindings(const TargetModule& module, uintptr_t* setSeed, uintptr_t* next);
bool TryInstallRngBindings(const TargetModule& module);
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
enum class Stage48MusicReplayProbeInstallResult {
    Success = 0,
    TargetMismatchMusicPlay = 11,
    TargetMismatchSoundActorPlay = 12,
    TargetMismatchSoundActorPause = 13,
    RelayPublishMusicPlayFailed = 21,
    RelayPublishSoundActorPlayFailed = 22,
    RelayPublishSoundActorPauseFailed = 23,
};
Stage48MusicReplayProbeInstallResult TryInstallStage48MusicReplayProbe(const TargetModule& module);
void Stage48ArmMusicReplayProbe();
void Stage48AdvanceMusicReplayProbe();
bool Stage48TakeMusicReplayReport(std::uint32_t* payload);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
[[noreturn]] void ReportStage6Failure(std::uint32_t status);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 8 || EXL_DIAGNOSTIC_STAGE == 9)
bool VerifyManagerLoadConfigs(const TargetModule& module, uintptr_t* target);
bool VerifyManagerLoadConfigsRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot);
#endif
enum class HookInstallResult {
    Success,
    InstructionMismatch,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
    GameBindingsMismatch,
    GameBindingsPublishFailed,
    MusicBindingsMismatch,
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
    GameOwnerSlotMismatch,
    GameOwnerSlotPublishFailed,
    GameIsPausedThunkMismatch,
    GameIsPausedThunkPublishFailed,
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
    Stage88GameBindingsMismatch,
    ManagerIsActionTriggeredMismatch,
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
    Stage89GameBindingsMismatch,
    ManagerIsActionTriggeredMismatch,
#endif
};
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
enum class LoadConfigsDiagnosticInstallResult {
    Success,
    InstructionOrRelayMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
#endif
HookInstallResult TryInstallManagerUpdateHook(const TargetModule& module);
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
LoadConfigsDiagnosticInstallResult TryInstallManagerLoadConfigsDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
enum class LoadConfigsResetDiagnosticInstallResult {
    Success,
    LoadConfigsInstructionOrRelayMismatch,
    ResetInstructionOrMappingMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
LoadConfigsResetDiagnosticInstallResult TryInstallManagerLoadConfigsResetDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
enum class Stage11InstallResult {
    Success,
    FileBindingMismatch,
};
Stage11InstallResult TryInstallRomfsSentinelDiagnostic(const TargetModule& module);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
enum class Stage12InstallResult {
    Success,
    FileBindingMismatch,
};
Stage12InstallResult TryInstallRomfsLuaDiagnostic(const TargetModule& module);
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
#if !defined(EXL_DIAGNOSTIC_STAGE)
enum class DefaultManifestInstallResult {
    Success,
    FileBindingMismatch,
};
DefaultManifestInstallResult TryInstallDefaultManifestMod(const TargetModule& module);
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
void AllowDefaultManifestInitialization();
#endif
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
enum class Stage13InstallResult {
    Success,
    FileBindingMismatch,
};
Stage13InstallResult TryInstallManifestRequireDiagnostic(const TargetModule& module);
[[noreturn]] void ReportStage13Failure(std::uint32_t status);
#endif
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
void MarkStage14DiagnosticReady();
#endif
bool InstallManagerUpdateHook(const TargetModule& module);

// 游戏开局的常驻中继安装结果。与 `ManagerUpdate` 一样，失败**不阻断**加载 Mod：
// 失败只会让 `MC_POST_GAME_STARTED` 收不到事件，而不是让整个运行时起不来。
enum class GameStartRelayInstallResult {
    Success,
    RelayPatchMismatch,
    RelaySlotNotEmpty,
    RelayPublishFailed,
};
GameStartRelayInstallResult TryInstallGameStartRelay(const TargetModule& module);
bool IsHookEnabled();
