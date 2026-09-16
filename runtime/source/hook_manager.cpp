#include "hook_manager.hpp"

#include "lib.hpp"
#include "lib/nx/kernel/svc.h"
#if !defined(EXL_DIAGNOSTIC_STAGE)
#include "default_callback_gate.hpp"
#include "default_persistence_gate.hpp"
#include "infrastructure/persistence/engine_save_file_adapter.hpp"
#include "mod_persistence.hpp"
#endif
#include "game_observer.hpp"
#include "lua_runtime.hpp"
// 回调登记普查（`LuaRuntime::RegisteredCallbackKindMaskLow` 等）：诊断出口要把
// “Mod 登记了哪些回调种类、哪些有派发点”报给探针，因此这里需要状态头的声明。
#include "lua_runtime_state.hpp"
#include "manager_update_hook_audit.hpp"
#include "persistence_event_journal.hpp"
#include "persistence_trace.hpp"
#include "content_mount_point_probe.hpp"
#include "runtime_constants.hpp"
#include "interfaces/lua/engine_frame_probe.hpp"
#include "saltynx_probe_break.hpp"
#include "saltynx_self_journal.hpp"
#include "test_run_observer.hpp"
// Every build entry point (production module, diagnostics and the startup
// probes) defines `EXL_LAYERED_RUNTIME`; the non-layered compatibility path was
// deleted with the migration.
#include "application/mod/content_mount_service.hpp"
#include "application/mod/manifest_service.hpp"
#include "application/mod/mod_discovery_service.hpp"
#include "infrastructure/content/engine_content_mount_adapter.hpp"
#include "infrastructure/content/engine_directory_adapter.hpp"
#include "application/mod/mod_load_service.hpp"
#include "application/mod/mod_toggle_service.hpp"
#include "interfaces/lua/mod_menu_api.hpp"
#include "application/runtime/hook_install_service.hpp"
#include "infrastructure/exlaunch/exlaunch_hook_adapter.hpp"
#include "infrastructure/content/game_file_reader_adapter.hpp"
#include "infrastructure/lua/embedded_lua_adapter.hpp"
#include "infrastructure/mod/manifest_selector_adapter.hpp"
#include "ports/module_scanner_port.hpp"

// 零占洞入口中继后端（Task 3）+ 按挂点路由适配器（Task 4）。
#include "infrastructure/hook_routing_adapter.hpp"
#include "infrastructure/relay/entry_relay_binding.hpp"

// 挂点安装报告经快照 reserved 高位回读（Task 5）。与本文件同目录。
#include "hook_install_report_state.hpp"

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
#include "stage14_diagnostic.hpp"
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) || \
    (EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 13 || \
     EXL_DIAGNOSTIC_STAGE == 118)
#include "game_file_reader.hpp"
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
#include "mod_manifest.hpp"
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstring>

namespace IsaacRepentance {
struct Manager;
}

#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
extern "C" NORETURN void ReportStartupProbeStage8ManifestResult(bool initialized, u32 failureDetail);
#endif

namespace {

std::atomic<u32> g_HookEnabled{false};
// 这一帧的 `IsaacRepentance::Manager*`：`Manager::Update` 与 `Manager::Render` 的入口中继各记
// 一次，供 `Present` 前的派发入口使用 —— 那个调用点的 `x0` 是 `KAGE::Graphics::g_Manager`
// （图形管理器），而派发路径要的是 Manager*：`PrimeDefaultCallbacks` 用它做音乐就绪判定
// （`ResolveMusicForManager(manager + kManagerMusicOffset, …)`），Music API 也用它
// （`g_CurrentCallbackManager`）。2026-09-13 的真机报告正栽在这里：中继装上了也进入了
// （掩码位 52/55 = 1），但 `PrimeDefaultCallbacks` 拿到图形管理器后返回 false，于是
// `MC_POST_RENDER` 的回调一次都没被调用（Mod 标记停在 1、屏幕全空）。
std::atomic<std::uintptr_t> g_RenderFrameManager{0};
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_CONTENT_MOUNT
// 一次性标志：内容挂载点探针只跑一次（它自己会 break 结束会话）。用 `u32` 表示布尔语义，
// 而不是布尔型原子量 —— 项目门禁禁止后者出现在运行时源码里（见
// `runtime/tests/test_manager_update_hook_audit.py`，它按文本扫描源码）。
std::atomic<u32> g_ContentMountProbeArmed{0};
#endif
// 下面这组诊断/安装结果字样必须**常编**：顶层的 `TryInstallManager*`（在任何配置下都会编译）要
// 用它们里的记录函数。历史上它们被关在 `#if !defined(EXL_DIAGNOSTIC_STAGE)` +
// `#if defined(EXL_LAYERED_RUNTIME)` 里，于是 `make probe DIAGNOSTIC_STAGE=<n>` 必然报
// "RecordRenderHookInstall was not declared in this scope"（2026-09-13 撞上，见
// docs/问题与解决记录.md）。只有导出给宿主插件与自记日志的那两个入口继续留在分层守卫内。
// Hook diagnostics published to the host plugin.
//
// A production module currently reports nothing about whether the Manager update
// hook was installed or whether its callback is ever entered:
// `HookInstallService::Install` collapses every per-hook result into Ok/Rejected,
// the Runtime's own state has no export, and `ManagerUpdateHookAudit` -- the only
// code that counts callback entries -- is not compiled in production. On hardware
// that left "the diagnostics attach is never reached" indistinguishable from "the
// update hook never fires"; the two callback counters below separate them.
//
// These are plain atomics touched once per callback: no allocation, no file I/O,
// and no dependency the plugin has to provide. The word order is wire format and
// is read by `runtime/src/host_plugin`.
// 诊断字布局（`IsaacModRuntime_GetHookDiagnostics`）：
//   [0..2] 三个中继安装结果  [3] update 回调进入次数  [4] render 回调进入次数
//   [5..6] 已登记的 ModCallbacks 种类掩码（id 0..63，低/高 32 位）
//   [7..8] 同上（id 64..127，PC 表到 73）
//   [9] 有派发点的登记次数  [10] 无派发点（登记了但不会触发）的登记次数
//   [11] 清单 Mod 加载结果字：0 = 带脚本加载成功，1..4 = 失败步（ManifestRead/Parse/PathBuild/
//        EntryRead），0x10+detail = Lua 初始化失败，5 = 资源型 Mod（无脚本）已挂载
//   [12] 回调 Lua 错误标志（只读，不消费）
//   [13] 最近一次 Lua 错误/加载失败信息长度  [14..15] 该信息前 8 字节（两半）
// 5..12 是 2026-09-12 为“EID 加载地基”加的普查：真机要能区分“登记了但不会触发”和
// “根本没登记”，也要能看出 Mod 加载失败在哪一步。
constexpr std::size_t kHookDiagnosticsWordCount = 16;
constexpr std::uint64_t kHookDiagnosticsMagic = 0x3152484341415349ULL;  // "ISAACHR1"

// [0] ManagerUpdate install result, [1] ManagerRender install result,
// [2] PreGetCollectible relay result, [3] update-callback entries,
// [4] render-callback entries.
std::atomic<std::uint32_t> g_HookInstallResults[kHookDiagnosticsWordCount]{};
std::atomic<std::uint32_t> g_UpdateCallbackEntries{0};
std::atomic<std::uint32_t> g_RenderCallbackEntries{0};
// `Present` 前派发中继的状态与进入次数。这两个字不进上面那份 5 字契约（那是与宿主插件
// 之间的线格式），只给探针读：安装结果与"中继到底跑没跑"必须能从设备上读出来。
std::atomic<std::uint32_t> g_RenderPresentRelayState{0};
std::atomic<std::uint32_t> g_RenderPresentRelayEntries{0};

// 卡上开关的**自证**字段（2026-09-16，排障用）。
//
// 为什么必须有：开关文件读不到时 `CardSwitchOn` 返回 false（"就当没这个开关"），
// 于是"开关没生效"与"开关生效了但没用"在设备上长得一模一样 —— 那会把整个二分带偏。
// 这两个字把两种失败分开，调试桩直接读（一次开机读一次就够）：
//   [0] 找到的开关掩码（按 `kCardSwitchNames` 的顺序，bit i = 第 i 个名字的 `.on` 在卡上）
//   [1] 通道状态：bit0 = 至少一次查询成功返回；bit1 = 至少一次查询失败（通道不可用）；
//                 bit2 = 在**不是游戏主线程**的地方被要求探测 ⇒ 拒绝执行（见下面那条纪律）
std::atomic<std::uint32_t> g_CardSwitchMask{0};
std::atomic<std::uint32_t> g_CardSwitchChannel{0};
// ★★ 纪律（2026-09-16 真机撞出来的）：**游戏 nnSdk 的文件 API 只允许在游戏主线程上调用。**
//
// 事故经过：卡上开关最早是在"装挂点"那一步探测的，而那一步跑在**我们自己创建的 worker 线程**上。
// 结果 442190 那一版**每次开机必崩**（两份崩溃报告 `01789571605` / `01789571620`）：
// `runtime + 0x15080`（`EngineSaveFileAdapter::Exists`）→ nnSdk 的
// `_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci + 0x28`
// → nnSdk 里一条 `os::SdkMutexType::Lock` → 空指针 + 0x1b0 的 Data Abort。
// 同一个适配器的 `Read`/`Write` 在**游戏主线程**上一直是好的（模组开关状态的持久化就是它做的）
// ⇒ 差别是**线程**，不是时机（两者相隔几毫秒）。
// 机制：游戏这套 nnSdk 用的是**线程本地堆**（`TlsHeapCentral`，退出对局那次崩溃的栈里也出现过），
// 我们自己 `svcCreateThread`/libnx 建出来的线程没有在游戏的线程体系里登记 ⇒ 任何会**分配内存**的
// SDK 调用都会踩空。所以：**我们自己的线程上只做"读内存 + 写游戏代码"，不做 SDK 调用。**
// 用 u32 而不是 bool 原子：门禁 `test_runtime_source_contains_no_bool_atomics`
// 明令两个源码根里不许出现 bool 特化的原子（0 = 未允许、1 = 允许探测）。
std::atomic<u32> g_CardSwitchProbeAllowed{0};

// 名字顺序就是掩码位序 —— 改了顺序就等于换了 `g_CardSwitchMask` 的语义，别随手改。
//
// 只留"由**游戏主线程**上的代码读取"的开关：挂点跳过类开关原本也在这里，但读它们的那一步
// 跑在 worker 线程上 ⇒ 会崩（见 `g_CardSwitchProbeAllowed` 上面那条纪律）。
// 挂点层面的二分改用**编译开关**（`ONLY_REQUIRED_HOOK=1`），一条构建一个配置，不上卡。
constexpr const char* kCardSwitchNames[] = {"no-menu", "no-lua"};
constexpr std::size_t kCardSwitchNameCount = sizeof(kCardSwitchNames) / sizeof(kCardSwitchNames[0]);

HookInstallResult RecordUpdateHookInstall(HookInstallResult result) {
    g_HookInstallResults[0].store(static_cast<std::uint32_t>(result), std::memory_order_release);
    return result;
}

RenderHookInstallResult RecordRenderHookInstall(RenderHookInstallResult result) {
    g_HookInstallResults[1].store(static_cast<std::uint32_t>(result), std::memory_order_release);
    return result;
}

PreGetCollectibleRelayInstallResult RecordPreGetCollectibleInstall(
    PreGetCollectibleRelayInstallResult result) {
    g_HookInstallResults[2].store(static_cast<std::uint32_t>(result), std::memory_order_release);
    return result;
}

// Mod 贴图转码缓存的计数（探针用位 35 报"至少写出一个 PCX"）。
std::atomic<std::uint32_t> g_TextureCacheWritten{0};
std::atomic<std::uint32_t> g_TextureCacheCandidates{0};
std::atomic<std::uint32_t> g_TextureCacheFailures{0};

RenderPresentRelayInstallResult RecordRenderPresentRelayInstall(
    RenderPresentRelayInstallResult result) {
    g_RenderPresentRelayState.store(static_cast<std::uint32_t>(result), std::memory_order_release);
    return result;
}

// `MC_POST_RENDER` 派发点中继的自证出口（安装结果，进入次数），只给探针构建读。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_RenderPresentRelayDiagnostics(std::uint32_t* output) {
    if (output == nullptr) {
        return;
    }
    output[0] = g_RenderPresentRelayState.load(std::memory_order_acquire);
    output[1] = g_RenderPresentRelayEntries.load(std::memory_order_acquire);
}

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
std::atomic<uintptr_t> g_LuaGameOwnerSlot{0};
std::atomic<uintptr_t> g_LuaGameIsPausedThunk{0};
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
constexpr u64 kStage14SuccessMagic = 0x49534141435F4750ULL;
constexpr u64 kStage14FailureMagic = 0x49534141435F4746ULL;
Stage14Diagnostic::Controller g_Stage14Controller{};

NORETURN void ReportStage14Failure(Stage14Diagnostic::Stage14Failure failure) {
    svcBreak(BreakReason_User, kStage14FailureMagic,
             (14ULL << 32) | static_cast<u32>(failure));
    svcExitProcess();
}

NORETURN void ReportStage14Success(bool paused) {
    svcBreak(BreakReason_User, kStage14SuccessMagic,
             (14ULL << 32) | (paused ? 2ULL : 1ULL));
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
constexpr u64 kStage17SuccessMagic = 0x49534141435F4D4CULL;
constexpr u64 kStage17FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage17Success() {
    svcBreak(BreakReason_User, kStage17SuccessMagic, (17ULL << 32) | 1ULL);
    svcExitProcess();
}

NORETURN void ReportStage17Failure(u32 status) {
    svcBreak(BreakReason_User, kStage17FailureMagic, (17ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
constexpr u64 kStage45SuccessMagic = 0x49534141435F4D49ULL;
constexpr u64 kStage45FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage45Success(u32 musicId) {
    svcBreak(BreakReason_User, kStage45SuccessMagic, (45ULL << 32) | musicId);
    svcExitProcess();
}

NORETURN void ReportStage45Failure(u32 status) {
    svcBreak(BreakReason_User, kStage45FailureMagic, (45ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
constexpr u64 kStage48SuccessMagic = 0x49534141435F4D52ULL;
constexpr u64 kStage48FailureMagic = 0x49534141435F4D46ULL;
constexpr u32 kStage48WindowFrames = 180;
constexpr u32 kStage48PausedActorCapacity = 8;
constexpr u32 kStage48ObservedPause = 1u << 0;
constexpr u32 kStage48ObservedMusicPlay = 1u << 1;
constexpr u32 kStage48ObservedActorPlay = 1u << 2;
constexpr u32 kStage48ActorOverlap = 1u << 3;
constexpr u32 kStage48ActorDifferent = 1u << 4;
// Keep Stage48 observation state visible in the one-shot hardware report.

std::atomic<u32> g_Stage48Armed{false};
std::atomic<u32> g_Stage48Reported{false};
std::atomic<u32> g_Stage48Frames{0};
std::atomic<u32> g_Stage48PauseCount{0};
std::atomic<u32> g_Stage48MusicPlayCount{0};
std::atomic<u32> g_Stage48ActorPlayCount{0};
std::atomic<u32> g_Stage48Flags{0};
std::array<std::atomic<uintptr_t>, kStage48PausedActorCapacity> g_Stage48PausedActors{};

NORETURN void ReportStage48Failure(u32 status) {
    svcBreak(BreakReason_User, kStage48FailureMagic, (48ULL << 32) | status);
    svcExitProcess();
}

NORETURN void ReportStage48Success(u32 payload) {
    svcBreak(BreakReason_User, kStage48SuccessMagic, (48ULL << 32) | payload);
    svcExitProcess();
}

void RememberStage48PausedActor(uintptr_t actor) {
    if (actor == 0) return;
    for (auto& slot : g_Stage48PausedActors) {
        uintptr_t value = slot.load(std::memory_order_acquire);
        if (value == actor) return;
        if (value == 0 && slot.compare_exchange_strong(value, actor, std::memory_order_acq_rel)) return;
    }
}

bool IsStage48PausedActor(uintptr_t actor) {
    for (const auto& slot : g_Stage48PausedActors) {
        if (slot.load(std::memory_order_acquire) == actor && actor != 0) return true;
    }
    return false;
}

void RecordStage48MusicPlay() {
    if (!g_Stage48Armed.load(std::memory_order_acquire)) return;
    g_Stage48MusicPlayCount.fetch_add(1, std::memory_order_relaxed);
    g_Stage48Flags.fetch_or(kStage48ObservedMusicPlay, std::memory_order_release);
}

void RecordStage48SoundActorPause(uintptr_t actor) {
    if (!g_Stage48Armed.load(std::memory_order_acquire)) return;
    g_Stage48PauseCount.fetch_add(1, std::memory_order_relaxed);
    g_Stage48Flags.fetch_or(kStage48ObservedPause, std::memory_order_release);
    RememberStage48PausedActor(actor);
}

void RecordStage48SoundActorPlay(uintptr_t actor) {
    if (!g_Stage48Armed.load(std::memory_order_acquire)) return;
    g_Stage48ActorPlayCount.fetch_add(1, std::memory_order_relaxed);
    u32 flags = kStage48ObservedActorPlay;
    flags |= IsStage48PausedActor(actor) ? kStage48ActorOverlap : kStage48ActorDifferent;
    g_Stage48Flags.fetch_or(flags, std::memory_order_release);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
constexpr u64 kStage88SuccessMagic = 0x49534141435F4952ULL;
constexpr u32 kStage88FrameLimit = 600;
constexpr u32 kStage88ActionRestart = 16;
constexpr u32 kStage88ActionQueryCalled = 1u << 0;
constexpr u32 kStage88ActionRestartSeen = 1u << 1;
constexpr u32 kStage88StageReadable = 1u << 2;
constexpr u32 kStage88PausedSeen = 1u << 3;
constexpr u32 kStage88UnpausedSeen = 1u << 4;
std::atomic<uintptr_t> g_Stage88OwnerSlot{0};
std::atomic<uintptr_t> g_Stage88IsPausedThunk{0};
std::atomic<uintptr_t> g_Stage88ActionMethod{0};
std::atomic<u32> g_Stage88Frames{0};
std::atomic<u32> g_Stage88Flags{0};
std::atomic<u32> g_Stage88LastStage{0};

NORETURN void ReportStage88Success(u32 payload) {
    svcBreak(BreakReason_User, kStage88SuccessMagic, (88ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage88InstantRestart(IsaacRepentance::Manager* manager) {
    const uintptr_t methodAddress = g_Stage88ActionMethod.load(std::memory_order_acquire);
    if (manager != nullptr && methodAddress != 0) {
        using IsActionTriggered = bool (*)(IsaacRepentance::Manager*, u32, u32, void*);
        const auto actionTriggered = reinterpret_cast<IsActionTriggered>(methodAddress);
        u32 flags = kStage88ActionQueryCalled;
        if (actionTriggered(manager, kStage88ActionRestart, 0, nullptr)) flags |= kStage88ActionRestartSeen;
        g_Stage88Flags.fetch_or(flags, std::memory_order_release);
    }
    u32 stage = 0;
    if (ReadCurrentGameLevelStage(g_Stage88OwnerSlot.load(std::memory_order_acquire), &stage) ==
        GameLevelStageObservation::Success) {
        g_Stage88LastStage.store(stage, std::memory_order_release);
        g_Stage88Flags.fetch_or(kStage88StageReadable, std::memory_order_release);
    }
    switch (ObserveGameIsPaused(g_Stage88OwnerSlot.load(std::memory_order_acquire),
                                g_Stage88IsPausedThunk.load(std::memory_order_acquire))) {
    case GameIsPausedObservation::PausedTrue:
        g_Stage88Flags.fetch_or(kStage88PausedSeen, std::memory_order_release);
        break;
    case GameIsPausedObservation::PausedFalse:
        g_Stage88Flags.fetch_or(kStage88UnpausedSeen, std::memory_order_release);
        break;
    default:
        break;
    }
    if (g_Stage88Frames.fetch_add(1, std::memory_order_relaxed) + 1 >= kStage88FrameLimit) {
        const u32 payload = g_Stage88Flags.load(std::memory_order_acquire) |
                            ((g_Stage88LastStage.load(std::memory_order_acquire) & 0xffu) << 8);
        ReportStage88Success(payload);
    }
}

void ConfigureStage88Bindings(uintptr_t ownerSlot, uintptr_t isPausedThunk, uintptr_t actionMethod) {
    g_Stage88OwnerSlot.store(ownerSlot, std::memory_order_release);
    g_Stage88IsPausedThunk.store(isPausedThunk, std::memory_order_release);
    g_Stage88ActionMethod.store(actionMethod, std::memory_order_release);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
constexpr u64 kStage89SuccessMagic = 0x49534141435F4954ULL;
constexpr u32 kStage89FrameLimit = 7200;
constexpr u32 kStage89ActionRestart = 16;
constexpr u32 kStage89ActionQueryCalled = 1u << 0;
constexpr u32 kStage89AllControllersSeen = 1u << 1;
constexpr u32 kStage89ControllerZeroSeen = 1u << 2;
constexpr u32 kStage89ControllerOneSeen = 1u << 3;
constexpr u32 kStage89ControllerTwoSeen = 1u << 4;
constexpr u32 kStage89ControllerThreeSeen = 1u << 5;
constexpr u32 kStage89StageReadable = 1u << 6;
constexpr u32 kStage89PausedSeen = 1u << 7;
constexpr u32 kStage89UnpausedSeen = 1u << 8;
constexpr u32 kStage89FirstControllerNoHit = 0xffu;
constexpr std::array<u32, 5> kStage89Controllers = {0, 1, 2, 3, static_cast<u32>(-1)};
std::atomic<uintptr_t> g_Stage89OwnerSlot{0};
std::atomic<uintptr_t> g_Stage89IsPausedThunk{0};
std::atomic<uintptr_t> g_Stage89ActionMethod{0};
std::atomic<u32> g_Stage89Frames{0};
std::atomic<u32> g_Stage89Flags{0};
std::atomic<u32> g_Stage89LastStage{0};

NORETURN void ReportStage89Success(u32 payload) {
    svcBreak(BreakReason_User, kStage89SuccessMagic, (89ULL << 32) | payload);
    svcExitProcess();
}

u32 Stage89ControllerFlag(size_t index) {
    return index == 4 ? kStage89AllControllersSeen : (1u << (index + 2));
}

u32 Stage89FirstControllerCode(size_t index) {
    return index == 4 ? 0xfeu : static_cast<u32>(index);
}

void ObserveStage89RestartActionControllers(IsaacRepentance::Manager* manager) {
    u32 flags = g_Stage89Flags.load(std::memory_order_acquire);
    const uintptr_t methodAddress = g_Stage89ActionMethod.load(std::memory_order_acquire);
    if (manager != nullptr && methodAddress != 0) {
        using IsActionTriggered = bool (*)(IsaacRepentance::Manager*, u32, u32, void*);
        const auto actionTriggered = reinterpret_cast<IsActionTriggered>(methodAddress);
        flags |= kStage89ActionQueryCalled;
        for (size_t index = 0; index < kStage89Controllers.size(); ++index) {
            if (actionTriggered(manager, kStage89ActionRestart, kStage89Controllers[index], nullptr)) {
                flags |= Stage89ControllerFlag(index);
                g_Stage89Flags.store(flags, std::memory_order_release);
                const u32 payload = flags |
                                    ((g_Stage89LastStage.load(std::memory_order_acquire) & 0xffu) << 16) |
                                    (Stage89FirstControllerCode(index) << 24);
                ReportStage89Success(payload);
            }
        }
        g_Stage89Flags.store(flags, std::memory_order_release);
    }
    u32 stage = 0;
    if (ReadCurrentGameLevelStage(g_Stage89OwnerSlot.load(std::memory_order_acquire), &stage) ==
        GameLevelStageObservation::Success) {
        g_Stage89LastStage.store(stage, std::memory_order_release);
        g_Stage89Flags.fetch_or(kStage89StageReadable, std::memory_order_release);
    }
    switch (ObserveGameIsPaused(g_Stage89OwnerSlot.load(std::memory_order_acquire),
                                g_Stage89IsPausedThunk.load(std::memory_order_acquire))) {
    case GameIsPausedObservation::PausedTrue:
        g_Stage89Flags.fetch_or(kStage89PausedSeen, std::memory_order_release);
        break;
    case GameIsPausedObservation::PausedFalse:
        g_Stage89Flags.fetch_or(kStage89UnpausedSeen, std::memory_order_release);
        break;
    default:
        break;
    }
    if (g_Stage89Frames.fetch_add(1, std::memory_order_relaxed) + 1 >= kStage89FrameLimit) {
        const u32 payload = g_Stage89Flags.load(std::memory_order_acquire) |
                            ((g_Stage89LastStage.load(std::memory_order_acquire) & 0xffu) << 16) |
                            (kStage89FirstControllerNoHit << 24);
        ReportStage89Success(payload);
    }
}

void ConfigureStage89Bindings(uintptr_t ownerSlot, uintptr_t isPausedThunk, uintptr_t actionMethod) {
    g_Stage89OwnerSlot.store(ownerSlot, std::memory_order_release);
    g_Stage89IsPausedThunk.store(isPausedThunk, std::memory_order_release);
    g_Stage89ActionMethod.store(actionMethod, std::memory_order_release);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
constexpr u64 kStage102SuccessMagic = 0x49534141435F4352ULL;
constexpr u32 kStage102EventLimit = 4;
constexpr u32 kStage102EventSeen = 1u << 0;
constexpr u32 kStage102RoomTypeReadable = 1u << 1;
constexpr u32 kStage102RoomTypeFailure = 1u << 2;
std::atomic<u32> g_Stage102EventCount{0};
std::atomic<u32> g_Stage102Flags{0};
std::atomic<u32> g_Stage102LastRoomType{0};

NORETURN void ReportStage102Success(u32 payload) {
    svcBreak(BreakReason_User, kStage102SuccessMagic, (102ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage102ChangeRoom(IsaacRepentance::Game* game) {
    u32 flags = kStage102EventSeen;
    u32 roomType = 0;
    if (ReadGameRoomTypeFromGame(game, &roomType) == GameRoomObservation::Success) {
        g_Stage102LastRoomType.store(roomType, std::memory_order_release);
        flags |= kStage102RoomTypeReadable;
    } else {
        flags |= kStage102RoomTypeFailure;
    }
    g_Stage102Flags.fetch_or(flags, std::memory_order_release);
    if (g_Stage102EventCount.fetch_add(1, std::memory_order_relaxed) + 1 >= kStage102EventLimit) {
        const u32 payload = g_Stage102Flags.load(std::memory_order_acquire) |
                            ((g_Stage102LastRoomType.load(std::memory_order_acquire) & 0xffu) << 8);
        ReportStage102Success(payload);
    }
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
constexpr u64 kStage104SuccessMagic = 0x49534141435F4B52ULL;
constexpr u32 kStage104EventLimit = 4;
constexpr u32 kStage104EventSeen = 1u << 0;
constexpr u32 kStage104KeyReadable = 1u << 1;
constexpr u32 kStage104KeyFailure = 1u << 2;
constexpr u32 kStage104ThirdMatchesFirst = 1u << 3;
constexpr u32 kStage104FourthMatchesSecond = 1u << 4;
constexpr u32 kStage104FirstIndexShift = 8;
constexpr u32 kStage104SecondIndexShift = 16;
constexpr u32 kStage104FirstDimensionShift = 24;
constexpr u32 kStage104SecondDimensionShift = 26;
std::atomic<u32> g_Stage104EventCount{0};
std::atomic<u32> g_Stage104Flags{0};
std::array<GameRoomKey, kStage104EventLimit> g_Stage104Keys{};

NORETURN void ReportStage104Success(u32 payload) {
    svcBreak(BreakReason_User, kStage104SuccessMagic, (104ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage104ChangeRoom(IsaacRepentance::Game* game) {
    const u32 event = g_Stage104EventCount.fetch_add(1, std::memory_order_relaxed);
    if (event >= kStage104EventLimit) return;
    GameRoomKey key{};
    u32 flags = kStage104EventSeen;
    if (ReadGameCurrentRoomKeyFromGame(game, &key) == GameRoomObservation::Success) {
        g_Stage104Keys[event] = key;
        flags |= kStage104KeyReadable;
    } else {
        flags |= kStage104KeyFailure;
    }
    g_Stage104Flags.fetch_or(flags, std::memory_order_release);
    if (event + 1 != kStage104EventLimit) return;
    u32 payload = g_Stage104Flags.load(std::memory_order_acquire);
    if ((payload & kStage104KeyFailure) == 0 &&
        g_Stage104Keys[0] == g_Stage104Keys[2]) payload |= kStage104ThirdMatchesFirst;
    if ((payload & kStage104KeyFailure) == 0 &&
        g_Stage104Keys[1] == g_Stage104Keys[3]) payload |= kStage104FourthMatchesSecond;
    payload |= (g_Stage104Keys[0].roomIndex & 0xffu) << kStage104FirstIndexShift;
    payload |= (g_Stage104Keys[1].roomIndex & 0xffu) << kStage104SecondIndexShift;
    payload |= (g_Stage104Keys[0].dimension & 0x3u) << kStage104FirstDimensionShift;
    payload |= (g_Stage104Keys[1].dimension & 0x3u) << kStage104SecondDimensionShift;
    ReportStage104Success(payload);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
constexpr u64 kStage112SuccessMagic = 0x49534141435F4452ULL;
constexpr u32 kStage112RoomTreasure = 4;
constexpr u32 kStage112TreasureSeen = 1u << 0;
constexpr u32 kStage112KeyReadable = 1u << 1;
constexpr u32 kStage112SnapshotReadable = 1u << 2;
constexpr u32 kStage112SameKeyReentered = 1u << 3;
constexpr u32 kStage112Word0cUnchanged = 1u << 4;
constexpr u32 kStage112Word50Unchanged = 1u << 5;
constexpr u32 kStage112Word0cShift = 8;
constexpr u32 kStage112Word50Shift = 16;
std::atomic<u32> g_Stage112Flags{0};
std::atomic<u32> g_Stage112FirstCaptured{false};
GameRoomKey g_Stage112FirstKey{};
GameRoomDescriptorSnapshot g_Stage112FirstSnapshot{};

NORETURN void ReportStage112Success(u32 payload) {
    svcBreak(BreakReason_User, kStage112SuccessMagic, (112ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage112ChangeRoom(IsaacRepentance::Game* game) {
    u32 roomType = 0;
    if (ReadGameRoomTypeFromGame(game, &roomType) != GameRoomObservation::Success ||
        roomType != kStage112RoomTreasure) return;
    GameRoomKey key{};
    GameRoomDescriptorSnapshot snapshot{};
    if (ReadGameCurrentRoomKeyFromGame(game, &key) != GameRoomObservation::Success ||
        ReadGameRoomDescriptorSnapshotFromGame(game, &snapshot) != GameRoomObservation::Success) return;
    u32 flags = kStage112TreasureSeen | kStage112KeyReadable | kStage112SnapshotReadable;
    if (!g_Stage112FirstCaptured.exchange(true, std::memory_order_acq_rel)) {
        g_Stage112FirstKey = key;
        g_Stage112FirstSnapshot = snapshot;
        g_Stage112Flags.fetch_or(flags, std::memory_order_release);
        return;
    }
    if (!(g_Stage112FirstKey == key)) {
        g_Stage112Flags.fetch_or(flags, std::memory_order_release);
        return;
    }
    flags |= kStage112SameKeyReentered;
    if (g_Stage112FirstSnapshot.candidateWord0c == snapshot.candidateWord0c) {
        flags |= kStage112Word0cUnchanged;
    }
    if (g_Stage112FirstSnapshot.candidateWord50 == snapshot.candidateWord50) {
        flags |= kStage112Word50Unchanged;
    }
    const u32 payload = g_Stage112Flags.fetch_or(flags, std::memory_order_acq_rel) | flags |
                        ((snapshot.candidateWord0c & 0xffu) << kStage112Word0cShift) |
                        ((snapshot.candidateWord50 & 0xffu) << kStage112Word50Shift);
    ReportStage112Success(payload);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
constexpr u64 kStage108SuccessMagic = 0x49534141435F4C52ULL;
constexpr u32 kStage108EventLimit = 2;
constexpr u32 kStage108EventSeen = 1u << 0;
constexpr u32 kStage108SavedEvent = 1u << 1;
constexpr u32 kStage108NewEvent = 1u << 2;
constexpr u32 kStage108SecondEvent = 1u << 3;
constexpr u32 kStage108FirstKindShift = 8;
constexpr u32 kStage108SecondKindShift = 12;
std::atomic<u32> g_Stage108EventCount{0};
std::atomic<u32> g_Stage108Flags{0};
std::atomic<u32> g_Stage108FirstKind{0};
std::atomic<u32> g_Stage108SecondKind{0};

NORETURN void ReportStage108Success(u32 payload) {
    svcBreak(BreakReason_User, kStage108SuccessMagic, (108ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage108Lifecycle(IsaacRepentance::Game*, u32 eventKind) {
    if (eventKind != 1 && eventKind != 2) return;
    const u32 event = g_Stage108EventCount.fetch_add(1, std::memory_order_relaxed);
    if (event >= kStage108EventLimit) return;
    u32 flags = kStage108EventSeen | (eventKind == 1 ? kStage108SavedEvent : kStage108NewEvent);
    if (event == 0) {
        g_Stage108FirstKind.store(eventKind, std::memory_order_release);
    } else {
        g_Stage108SecondKind.store(eventKind, std::memory_order_release);
        flags |= kStage108SecondEvent;
    }
    g_Stage108Flags.fetch_or(flags, std::memory_order_release);
    if (event + 1 == kStage108EventLimit) {
        const u32 payload = g_Stage108Flags.load(std::memory_order_acquire) |
                            (g_Stage108FirstKind.load(std::memory_order_acquire) << kStage108FirstKindShift) |
                            (g_Stage108SecondKind.load(std::memory_order_acquire) << kStage108SecondKindShift);
        ReportStage108Success(payload);
    }
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
constexpr u64 kStage109SuccessMagic = 0x49534141435F5252ULL;

NORETURN void ReportStage109Success(u32 payload) {
    svcBreak(BreakReason_User, kStage109SuccessMagic, (109ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage109Restart(IsaacRepentance::Manager*, u32 pathKind) {
    if (pathKind >= 1 && pathKind <= 3) ReportStage109Success(pathKind);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
constexpr u64 kStage127SuccessMagic = 0x49534141435F534CULL;
std::atomic<u32> g_Stage127Flags{0};

NORETURN void ReportStage127Success(u32 payload) {
    svcBreak(BreakReason_User, kStage127SuccessMagic, (127ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage127SaveLoad(IsaacRepentance::Manager* outerManager, u32 kind) {
    if (outerManager == nullptr || (kind != 1 && kind != 2)) return;
    const u32 bit = kind == 1 ? 1u : 2u;
    const u32 flags = g_Stage127Flags.fetch_or(bit, std::memory_order_acq_rel) | bit;
    if (flags == 3u) ReportStage127Success(flags);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
constexpr u64 kStage128SuccessMagic = 0x49534141435F5344ULL;
std::atomic<u32> g_Stage128Flags{0};

NORETURN void ReportStage128Success(u32 payload) {
    svcBreak(BreakReason_User, kStage128SuccessMagic, (128ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage128SaveDataManager(void* saveDataManager, u32 kind) {
    if (saveDataManager == nullptr || (kind != 1 && kind != 2)) return;
    const u32 bit = kind == 1 ? 1u : 2u;
    const u32 flags = g_Stage128Flags.fetch_or(bit, std::memory_order_acq_rel) | bit;
    if (flags == 3u) ReportStage128Success(flags);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
constexpr u64 kStage110SuccessMagic = 0x49534141435F5352ULL;
constexpr u32 kStage110SavedLifecycle = 1u << 0;
constexpr u32 kStage110PreResetRoom = 1u << 1;
constexpr u32 kStage110NewLifecycle = 1u << 2;
constexpr u32 kStage110PostResetRoom = 1u << 3;
constexpr u32 kStage110CounterReset = 1u << 4;
constexpr u32 kStage110PostResetCountOne = 1u << 5;
std::atomic<u32> g_Stage110Flags{0};
std::atomic<u32> g_Stage110RoomEvents{0};

NORETURN void ReportStage110Success(u32 payload) {
    svcBreak(BreakReason_User, kStage110SuccessMagic, (110ULL << 32) | payload);
    svcExitProcess();
}

void ObserveStage110Lifecycle(IsaacRepentance::Game*, u32 eventKind) {
    if (eventKind == 1) {
        g_Stage110Flags.fetch_or(kStage110SavedLifecycle, std::memory_order_release);
        return;
    }
    if (eventKind != 2) return;
    const u32 before = g_Stage110RoomEvents.exchange(0, std::memory_order_acq_rel);
    u32 flags = kStage110NewLifecycle;
    if (before != 0) flags |= kStage110CounterReset;
    g_Stage110Flags.fetch_or(flags, std::memory_order_release);
}

void ObserveStage110ChangeRoom(IsaacRepentance::Game*) {
    const u32 count = g_Stage110RoomEvents.fetch_add(1, std::memory_order_acq_rel) + 1;
    u32 flag = 0;
    const u32 lifecycle = g_Stage110Flags.load(std::memory_order_acquire);
    if ((lifecycle & kStage110NewLifecycle) == 0) {
        flag = kStage110PreResetRoom;
    } else {
        flag = kStage110PostResetRoom;
        if (count == 1) flag |= kStage110PostResetCountOne;
    }
    const u32 flags = g_Stage110Flags.fetch_or(flag, std::memory_order_acq_rel) | flag;
    constexpr u32 kExpected = kStage110SavedLifecycle | kStage110PreResetRoom |
                              kStage110NewLifecycle | kStage110PostResetRoom |
                              kStage110CounterReset | kStage110PostResetCountOne;
    if ((flags & kExpected) == kExpected) ReportStage110Success(flags);
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
constexpr u64 kStage16SuccessMagic = 0x49534141435F4D50ULL;
constexpr u64 kStage16FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage16Success() {
    svcBreak(BreakReason_User, kStage16SuccessMagic, (16ULL << 32) | 1ULL);
    svcExitProcess();
}

NORETURN void ReportStage16Failure(u32 status) {
    svcBreak(BreakReason_User, kStage16FailureMagic, (16ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
constexpr u64 kStage15SuccessMagic = 0x49534141435F5250ULL;
constexpr u64 kStage15FailureMagic = 0x49534141435F5246ULL;
constexpr u32 kStage15PausedUpdateLimit = 120;

NORETURN void ReportStage15Success() {
    svcBreak(BreakReason_User, kStage15SuccessMagic, (15ULL << 32) | 1ULL);
    svcExitProcess();
}

NORETURN void ReportStage15Failure(u32 status) {
    svcBreak(BreakReason_User, kStage15FailureMagic, (15ULL << 32) | status);
    svcExitProcess();
}

void CheckStage15AfterPausedUpdate() {
    if (LuaRuntime::TakeCallbackError()) {
        ReportStage15Failure(3);
    }
    const u32 pausedUpdates = LuaRuntime::PostUpdateCount();
    if (pausedUpdates == 0) {
        return;
    }
    if (LuaRuntime::PostRenderPausedCount() != 0) {
        ReportStage15Success();
    }
    if (pausedUpdates >= kStage15PausedUpdateLimit) {
        ReportStage15Failure(LuaRuntime::PostRenderCount() == 0 ? 4 : 5);
    }
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
constexpr u32 kStage6ObserverFrameLimit = 600;
std::atomic<u32> g_Stage6ObserverFrames{0};
std::atomic<uintptr_t> g_Stage6GameOwnerSlot{0};
std::atomic<uintptr_t> g_Stage6GameIsPausedThunk{0};

NORETURN void ReportStage6IsPausedSuccess(bool paused) {
    svcBreak(BreakReason_User, 0x49534141435F4950ULL,
             (6ULL << 32) | (paused ? 2ULL : 1ULL));
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
std::atomic<uintptr_t> g_Stage9ResetAddress{0};
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
enum class Stage11State : u32 {
    Unarmed,
    Armed,
    Running,
    Finished,
};
std::atomic<Stage11State> g_Stage11State{Stage11State::Unarmed};
GameFileReader::Bindings g_Stage11Bindings{};

NORETURN void RunStage11RomfsSentinelDiagnostic() {
    if (g_Stage11State.load(std::memory_order_acquire) != Stage11State::Running) {
        svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | 14ULL);
        svcExitProcess();
    }

    const GameFileReader::ReadResult result = GameFileReader::ReadSentinel(g_Stage11Bindings);
    g_Stage11State.store(Stage11State::Finished, std::memory_order_release);
    switch (result) {
    case GameFileReader::ReadResult::Success:
        svcBreak(BreakReason_User, 0x4953414143464F4BULL, (11ULL << 32) | 18ULL);
        svcExitProcess();
    case GameFileReader::ReadResult::OpenFailed:
        svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | 10ULL);
        svcExitProcess();
    case GameFileReader::ReadResult::LengthMismatch:
        svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | 11ULL);
        svcExitProcess();
    case GameFileReader::ReadResult::ReadMismatch:
        svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | 12ULL);
        svcExitProcess();
    case GameFileReader::ReadResult::ContentMismatch:
        svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | 13ULL);
        svcExitProcess();
    }
    UNREACHABLE;
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
constexpr u64 kStage118SuccessMagic = 0x49534141435F4657ULL;
constexpr u64 kStage118FailureMagic = 0x49534141435F4646ULL;
std::atomic<u32> g_Stage118Armed{false};
GameFileReader::WriteBindings g_Stage118Bindings{};

NORETURN void ReportStage118Success() {
    svcBreak(BreakReason_User, kStage118SuccessMagic, (118ULL << 32) | 0x0fULL);
    svcExitProcess();
}

NORETURN void ReportStage118Failure(u32 status) {
    svcBreak(BreakReason_User, kStage118FailureMagic, (118ULL << 32) | status);
    svcExitProcess();
}

NORETURN void RunStage118ManagedFileWriteDiagnostic() {
    switch (GameFileReader::WriteDiagnosticFile(g_Stage118Bindings)) {
    case GameFileReader::WriteDiagnosticResult::Success: ReportStage118Success();
    case GameFileReader::WriteDiagnosticResult::OpenFailed: ReportStage118Failure(10);
    case GameFileReader::WriteDiagnosticResult::WriteMismatch: ReportStage118Failure(11);
    }
    UNREACHABLE;
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
constexpr u64 kStage12SuccessMagic = 0x4953414143454C50ULL;
constexpr u64 kStage12FailureMagic = 0x4953414143454C46ULL;

enum class Stage12State : u32 {
    Unarmed,
    Armed,
    Running,
    Ready,
    Finished,
};

std::atomic<Stage12State> g_Stage12State{Stage12State::Unarmed};
GameFileReader::Bindings g_Stage12Bindings{};

NORETURN void ReportStage12Failure(u32 status) {
    g_Stage12State.store(Stage12State::Finished, std::memory_order_release);
    svcBreak(BreakReason_User, kStage12FailureMagic, (12ULL << 32) | status);
    svcExitProcess();
}

void RunStage12RomfsLuaDiagnostic() {
    if (g_Stage12State.load(std::memory_order_acquire) != Stage12State::Running) {
        ReportStage12Failure(18);
    }

    std::array<u8, kRomfsLuaProbeMaximumLength> script{};
    std::size_t length = 0;
    switch (GameFileReader::ReadScript(g_Stage12Bindings, &script, &length)) {
    case GameFileReader::ScriptReadResult::Success:
        break;
    case GameFileReader::ScriptReadResult::OpenFailed:
        ReportStage12Failure(10);
    case GameFileReader::ScriptReadResult::LengthOutOfRange:
        ReportStage12Failure(11);
    case GameFileReader::ScriptReadResult::ReadMismatch:
        ReportStage12Failure(12);
    }

    switch (LuaRuntime::InitializeFromBuffer(reinterpret_cast<const char*>(script.data()), length,
                                             "@rom:/runtime_probe.lua")) {
    case LuaRuntime::LuaInitResult::Success:
        g_Stage12State.store(Stage12State::Ready, std::memory_order_release);
        return;
    case LuaRuntime::LuaInitResult::StateCreateFailed:
    case LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed:
    case LuaRuntime::LuaInitResult::RuntimePreparationFailed:
        ReportStage12Failure(13);
    case LuaRuntime::LuaInitResult::ScriptLoadFailed:
        ReportStage12Failure(14);
    case LuaRuntime::LuaInitResult::ScriptRunFailed:
        ReportStage12Failure(15);
    case LuaRuntime::LuaInitResult::MissingPostUpdateCallback:
        ReportStage12Failure(16);
    }
    UNREACHABLE;
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
constexpr u64 kStage13SuccessMagic = 0x4953414143525150ULL;
constexpr u64 kStage13FailureMagic = 0x4953414143525146ULL;
constexpr u64 kStage13RequireErrorTailMagic = 0x4953414143525154ULL;

enum class Stage13State : u32 {
    Unarmed,
    Armed,
    Running,
    Ready,
    Finished,
};

std::atomic<Stage13State> g_Stage13State{Stage13State::Unarmed};
GameFileReader::Bindings g_Stage13Bindings{};
std::array<u8, kRomfsModManifestMaximumLength> g_Stage13Manifest{};
std::array<u8, kRomfsModScriptMaximumLength> g_Stage13Entry{};

NORETURN void ReportStage13StateMismatch() {
    ReportStage13Failure(25);
}

template <std::size_t Capacity>
bool JoinStage13Path(std::array<char, Capacity>* output, const char* prefix, const char* suffix) {
    if (output == nullptr || prefix == nullptr || suffix == nullptr) {
        return false;
    }
    const std::size_t prefixLength = std::strlen(prefix);
    const std::size_t suffixLength = std::strlen(suffix);
    if (prefixLength + suffixLength >= Capacity) {
        return false;
    }
    std::memcpy(output->data(), prefix, prefixLength);
    std::memcpy(output->data() + prefixLength, suffix, suffixLength + 1);
    return true;
}

void RunStage13ManifestRequireDiagnostic() {
    if (g_Stage13State.load(std::memory_order_acquire) != Stage13State::Running) {
        ReportStage13StateMismatch();
    }

    std::size_t manifestLength = 0;
    switch (GameFileReader::ReadTextFile(g_Stage13Bindings, kRomfsModManifestPath,
                                         g_Stage13Manifest.data(), g_Stage13Manifest.size(), &manifestLength)) {
    case GameFileReader::TextReadResult::Success:
        break;
    case GameFileReader::TextReadResult::OpenFailed:
        ReportStage13Failure(10);
    case GameFileReader::TextReadResult::LengthOutOfRange:
        ReportStage13Failure(11);
    case GameFileReader::TextReadResult::ReadMismatch:
        ReportStage13Failure(12);
    case GameFileReader::TextReadResult::InvalidArgument:
        ReportStage13StateMismatch();
    }

    ModManifest::SelectedMod selected{};
    if (ModManifest::SelectFirstEnabled(reinterpret_cast<const char*>(g_Stage13Manifest.data()),
                                        manifestLength, &selected) != ModManifest::ParseResult::Success) {
        ReportStage13Failure(13);
    }

    std::array<char, ModManifest::kEntryCapacity + 32> entryPath{};
    std::array<char, ModManifest::kDirectoryCapacity + 32> modRoot{};
    std::array<char, ModManifest::kEntryCapacity + 33> chunkName{};
    if (!JoinStage13Path(&entryPath, "rom:/isaac_mods/", selected.entry.data()) ||
        !JoinStage13Path(&modRoot, "rom:/isaac_mods/mods/", selected.directory.data()) ||
        !JoinStage13Path(&chunkName, "@rom:/isaac_mods/", selected.entry.data())) {
        ReportStage13StateMismatch();
    }

    std::size_t entryLength = 0;
    switch (GameFileReader::ReadTextFile(g_Stage13Bindings, entryPath.data(),
                                         g_Stage13Entry.data(), g_Stage13Entry.size(), &entryLength)) {
    case GameFileReader::TextReadResult::Success:
        break;
    case GameFileReader::TextReadResult::OpenFailed:
        ReportStage13Failure(14);
    case GameFileReader::TextReadResult::LengthOutOfRange:
    case GameFileReader::TextReadResult::ReadMismatch:
        ReportStage13Failure(15);
    case GameFileReader::TextReadResult::InvalidArgument:
        ReportStage13StateMismatch();
    }

    const LuaRuntime::LuaInitResult result = LuaRuntime::InitializeStage13Mod(
        reinterpret_cast<const char*>(g_Stage13Entry.data()), entryLength,
        chunkName.data(), modRoot.data(), g_Stage13Bindings);
    if (result != LuaRuntime::LuaInitResult::Success) {
        switch (LuaRuntime::RequireFailureDetail()) {
        case 19:
            ReportStage13Failure(19);
        case 20:
            ReportStage13Failure(20);
        case 21:
            ReportStage13Failure(21);
        case 22:
            ReportStage13Failure(22);
        case 27:
            g_Stage13State.store(Stage13State::Finished, std::memory_order_release);
            svcBreak(BreakReason_User, kStage13RequireErrorTailMagic, LuaRuntime::RequireErrorTail());
            svcExitProcess();
        case 26:
            ReportStage13Failure(26);
        default:
            break;
        }
    }
    switch (result) {
    case LuaRuntime::LuaInitResult::Success:
        g_Stage13State.store(Stage13State::Ready, std::memory_order_release);
        return;
    case LuaRuntime::LuaInitResult::StateCreateFailed:
    case LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed:
    case LuaRuntime::LuaInitResult::RuntimePreparationFailed:
        ReportStage13Failure(16);
    case LuaRuntime::LuaInitResult::ScriptLoadFailed:
        ReportStage13Failure(17);
    case LuaRuntime::LuaInitResult::ScriptRunFailed:
        ReportStage13Failure(18);
    case LuaRuntime::LuaInitResult::MissingPostUpdateCallback:
        ReportStage13Failure(23);
    }
    UNREACHABLE;
}
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE)
enum class DefaultManifestState : u32 { Unarmed, Armed, Running, Ready, Failed };
enum class DefaultManifestFailureDetail : u32 {
    None = 0,
    ManifestRead = 1,
    ManifestParse = 2,
    PathBuild = 3,
    EntryRead = 4,
    // 5 不是失败码：清单没写 `entry`（纯资源 Mod），或写的那个文件包内不存在。这时内容挂载点
    // 照常注册、Lua 完全不初始化、`InitializeDefaultManifestMod` 返回成功、Mod 状态为 Ready。
    // 诊断字 [11] 因此有了第三种含义：0 = 带脚本加载成功，1..4 = 失败在哪一步，5 = 只挂载内容。
    Scriptless = 5,
    // 6：没有可用清单，运行时**自己列目录**发现的模组（方案 A，2026-09-16）。
    // 不是失败：与 0 一样是"加载成功"，只是告诉读数"这批模组不是清单点名的那批"。
    Discovered = 6,
};
std::atomic<DefaultManifestState> g_DefaultManifestState{DefaultManifestState::Unarmed};
std::atomic<u32> g_DefaultManifestFailureDetail{static_cast<u32>(DefaultManifestFailureDetail::None)};
// 诊断字 `[11]` 本体（打包格式见 `mod_load_step.hpp` 的 `PackModLoadWord`）。
//
// 为什么另开一个字而不是改造 `g_DefaultManifestFailureDetail`：后者是既有探针阶段
// （`EXL_STARTUP_PROBE_STAGE == 8`）与 `TestRunObserver` 的线上格式，语义不能动；诊断字 `[11]`
// 只在"人读崩溃报告"这条路上被消费，把它升级成"步骤 + 原因 + 文件大小"是纯增益。
std::atomic<u32> g_DefaultManifestFailureWord{0};
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
std::atomic<u32> g_DefaultManifestInitializationAllowed{0};

bool DefaultManifestInitializationAllowed() {
    return g_DefaultManifestInitializationAllowed.load(std::memory_order_acquire);
}
#endif
DefaultCallbackGate g_DefaultCallbackGate{};
DefaultPersistenceGate g_DefaultPersistenceGate{};
#if defined(EXL_PERSISTENCE_REQUIRED)
constexpr bool kDefaultPersistenceRequired = true;
#else
constexpr bool kDefaultPersistenceRequired = false;
#endif
GameFileReader::Bindings g_DefaultManifestBindings{};


std::array<u8, kRomfsModManifestMaximumLength> g_DefaultManifest{};
std::array<u8, kRomfsModScriptMaximumLength> g_DefaultEntry{};
// The manifest read, Mod selection, path assembly, entry read and Lua
// initialization live in the application services, so the failure detail is
// derived from the service's step instead of local branches. Every build entry
// point defines `EXL_LAYERED_RUNTIME`, so there is exactly one path here.
#if defined(EXL_LAYERED_RUNTIME)
// Diagnostics entry point implemented in `saltynx_runtime_bridge.cpp`: it arms
// the event journal once the file table exists and a safe thread is running.
// Layered builds only: the plugin/diagnostic builds do not compile that bridge,
// so the call site below is guarded by the same macro.
#define ISAAC_RUNTIME_HAS_DIAGNOSTICS_BRIDGE 1
// Hidden, not default. A default-visibility declaration makes the linker emit a `.plt`
// call whose lazy-binding GOT slot is an `R_AARCH64_JUMP_SLOT` starting at the linker's
// PLT0 address; if the module's rtld does not resolve it, the stub falls through to
// `*(0x74B80)`, a resolver pointer with no relocation and a zero value. Hidden
// visibility binds the call locally and emits a direct `bl` instead.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_TryAttachDiagnostics();
// Bounded inside the Runtime: it writes at most three probe files in a session.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeFileWriteFromHook();
// Probe-only: steps through the Runtime's own `fs` write path and breaks with the Result
// codes, one per register. Bounded inside.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeFsSteps();
// Bounded inside: writes at most three probe files with the game's own libc functions,
// resolved by name through SaltyNX's symbol lookup.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeNativeFileWrite();
// Bounded inside: calls SaltyNX's own IPC entries and keeps their Result codes, which says
// whether the Runtime can talk to SaltyNX's sysmodule at all.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeSaltyIpc();
// Bounded inside, once: reopens SaltyNX's service session (the payload closes its own when it
// finishes) and tries a file write through it from the game's thread.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeSaltyReinit();
#endif

#if defined(EXL_LAYERED_RUNTIME)
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetHookDiagnostics(std::uint32_t* output, std::size_t wordCount) {
    if (output == nullptr || wordCount < kHookDiagnosticsWordCount) {
        return 0;
    }
    for (std::size_t index = 0; index < 3; ++index) {
        output[index] = g_HookInstallResults[index].load(std::memory_order_acquire);
    }
    output[3] = g_UpdateCallbackEntries.load(std::memory_order_acquire);
    output[4] = g_RenderCallbackEntries.load(std::memory_order_acquire);
    const std::uint64_t kinds = LuaRuntime::RegisteredCallbackKindMaskLow();
    const std::uint64_t kindsHigh = LuaRuntime::RegisteredCallbackKindMaskHigh();
    output[5] = static_cast<std::uint32_t>(kinds & 0xFFFFFFFFULL);
    output[6] = static_cast<std::uint32_t>(kinds >> 32);
    output[7] = static_cast<std::uint32_t>(kindsHigh & 0xFFFFFFFFULL);
    output[8] = static_cast<std::uint32_t>(kindsHigh >> 32);
    output[9] = LuaRuntime::DispatchableCallbackRegistrationCount();
    output[10] = LuaRuntime::UnhookedCallbackRegistrationCount();
    output[11] = g_DefaultManifestFailureWord.load(std::memory_order_acquire);
    output[12] = LuaRuntime::CallbackErrorPending() ? 1u : 0u;
    std::uint32_t errorLength = 0;
    std::uint64_t errorHead = LuaRuntime::LastLuaErrorHead(&errorLength);
    output[13] = errorLength;
    output[14] = static_cast<std::uint32_t>(errorHead & 0xFFFFFFFFULL);
    output[15] = static_cast<std::uint32_t>(errorHead >> 32);
    return kHookDiagnosticsMagic;
}

// Same five words, for the module's own in-process readers.
//
// `IsaacModRuntime_GetHookDiagnostics` is exported with default visibility for the
// host plugin, so a call to it from another translation unit of this module would be
// a `.plt` call resolved by the module loader (see the note above). The self-journal
// runs inside this module and must not depend on that resolution, so it reads the
// same words through this hidden entry point, which the linker binds with a direct
// `bl`.
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_FillHookJournalWords(std::uint32_t* output) {
    if (output == nullptr) {
        return;
    }
    for (std::size_t index = 0; index < 3; ++index) {
        output[index] = g_HookInstallResults[index].load(std::memory_order_acquire);
    }
    output[3] = g_UpdateCallbackEntries.load(std::memory_order_acquire);
    output[4] = g_RenderCallbackEntries.load(std::memory_order_acquire);
}
#endif

bool LoadDefaultManifestModThroughService(u32* failureDetail);

bool InitializeDefaultManifestMod(u32* failureDetail) {
    if (failureDetail == nullptr) return false;
    *failureDetail = static_cast<u32>(DefaultManifestFailureDetail::None);
    return LoadDefaultManifestModThroughService(failureDetail);
}

// File-scope so nothing depends on dynamic initialisation order, and so the rebuild
// relay can reach the same instance through `SessionContentMountService()`.
isaac::runtime::EngineContentMountAdapter g_ContentMountAdapter;
isaac::runtime::ContentMountService g_ContentMountService{g_ContentMountAdapter};
// 方案 A（2026-09-16）：没有清单时自己列 `isaac_mods/mods`，把发现的目录当模组。
// 与适配器同样放在文件作用域：这两个对象都没有动态初始化的依赖。
isaac::runtime::EngineDirectoryAdapter g_ModDirectoryAdapter;
isaac::runtime::ModDiscoveryService g_ModDiscoveryService{g_ModDirectoryAdapter};
// 模组开关（2026-09-16，路线 B）：状态存在游戏**存档分区**里（真机通道见
// `docs/问题与解决记录.md` 续三十四），开机时读出来、按它过滤掉被关掉的模组。
// 放在文件作用域的理由同上面两个：没有动态初始化的依赖，菜单侧也能拿到同一个实例。
isaac::runtime::EngineSaveFileAdapter g_SaveFileAdapter;
isaac::runtime::ModToggleService g_ModToggleService{g_SaveFileAdapter};

// 把内嵌的模组开关菜单当成"第一个模组"装进 Lua：走标准模组路径（同样的上下文与文件绑定）。
// 需要一个合法的 `modRoot`（引擎的 stage13 上下文只接受 `rom:/isaac_mods/mods/` 前缀下的路径）；
// 菜单本身不 `require`，所以这个根只用于满足上下文校验。
// 卡上开关（定义在下面，这里先声明：装菜单时要用它）。
bool CardSwitchOn(const char* name);

bool LoadEmbeddedMenuMod() {
    if (CardSwitchOn("no-menu") || CardSwitchOn("no-lua")) {
        return false;   // 卡上开关：这一轮不装菜单（等价于"连自带菜单都没有 Lua 状态"）
    }
    return LuaRuntime::InitializeEmbeddedMenuMod("rom:/isaac_mods/mods/isaac-switch-mod-menu",
                                                 g_DefaultManifestBindings) ==
           LuaRuntime::LuaInitResult::Success;
}

// 卡上开关 `no-lua.on`：这一轮**完全不建 Lua 状态**（自带菜单与真实模组都不加载），
// 但模块本身照旧在（挂点按各自的开关决定）。与 `no-menu` 的区别只是"真实模组还装不装"。
bool CardSwitchNoLua() { return CardSwitchOn("no-lua"); }

// ---- 卡上开关（2026-09-16，排障效率）------------------------------------------------
//
// 为什么要有它：定位崩溃时"一版一构建一部署"太贵（每轮十几分钟）。运行时改成**开机时看卡上有没有
// 标记文件**，来决定这一轮装哪些挂点 —— 于是**一份包能测很多种配置**，改卡上的文件（FTP 一秒）
// 加一次重启就能换一个变量，不需要我重新构建。
//
// 标记文件放在游戏覆盖层目录里（我用 FTP 能写、游戏能读）：
//   `/atmosphere/contents/010021C000B6A000/romfs/isaac_mods/mods/<名字>.on`
//   ⇒ 运行时按 `rom:/isaac_mods/mods/<名字>.on` 去问"存在吗"。
// ⚠️ 放在 `mods/` 里是安全的：模组自动发现**只认子目录**，文件会被忽略。
//
// 支持的开关（名字就是文件名的前半段）：
//   `no-menu`        不装自带的模组开关菜单（真实模组照旧加载）
//   `no-lua`         不建任何 Lua 状态（菜单与真实模组都不加载）
//
// ⚠️ 这两个开关都只由**游戏主线程**上的代码读取（模组加载那条路），**不允许**在 worker 线程上读
// —— 理由见 `g_CardSwitchProbeAllowed` 上面那段事故记录。
//
// **一次开机只探测一遍**：第一次（被允许的）调用时把 `kCardSwitchNames` 里每个名字都问一遍，
// 结果存进 `g_CardSwitchMask` / `g_CardSwitchChannel`（设备侧可读，见那里的注释），之后只查掩码。
void BuildCardSwitchPath(const char* name, char* out, std::size_t capacity) {
    const char* prefix = "rom:/isaac_mods/mods/";
    const char* suffix = ".on";
    std::size_t length = 0;
    if (out == nullptr || capacity == 0) {
        return;
    }
    out[0] = '\0';
    const char* pieces[3] = {prefix, name, suffix};
    for (const char* piece : pieces) {
        for (const char* cursor = piece; *cursor != '\0' && length + 1 < capacity; ++cursor) {
            out[length++] = *cursor;
        }
    }
    out[length] = '\0';
}

void ProbeCardSwitches() {
    static bool probed = false;
    if (probed) {
        return;
    }
    // ★ 硬闸：**不确认自己在游戏线程上就不碰卡**。
    // 这一条是 442190 那次"每次开机必崩"的直接修复 —— 那种崩溃发生在 nnSdk 内部，
    // 从我们的代码里看不出任何异常，排查代价极高（两份崩溃报告 + 一次完整装机）。
    if (g_CardSwitchProbeAllowed.load(std::memory_order_acquire) == 0) {
        g_CardSwitchChannel.fetch_or(4U, std::memory_order_release);
        return;
    }
    probed = true;
    u32 mask = 0;
    u32 channel = 0;
    for (std::size_t index = 0; index < kCardSwitchNameCount; ++index) {
        char path[128] = {};
        BuildCardSwitchPath(kCardSwitchNames[index], path, sizeof(path));
        bool exists = false;
        if (!g_SaveFileAdapter.Exists(path, &exists).ok()) {
            channel |= 2U;   // 查询本身失败：通道不可用（不是"文件不在"）
            continue;
        }
        channel |= 1U;
        if (exists) {
            mask |= (1U << index);
        }
    }
    g_CardSwitchMask.store(mask, std::memory_order_release);
    g_CardSwitchChannel.store(channel, std::memory_order_release);
}

bool CardSwitchOn(const char* name) {
    if (name == nullptr) {
        return false;
    }
    ProbeCardSwitches();
    const u32 mask = g_CardSwitchMask.load(std::memory_order_acquire);
    for (std::size_t index = 0; index < kCardSwitchNameCount; ++index) {
        if (std::strcmp(kCardSwitchNames[index], name) == 0) {
            return (mask & (1U << index)) != 0;
        }
    }
    // 表里没有的名字：如实返回 false（不现问卡 —— 那会引入"有的开关问了、有的没问"的
    // 两套行为，掩码也就无法自证了）。
    return false;
}

// 挂点层面的"跳过判据"（`ONLY_REQUIRED_HOOK=1` 的二分构建用）。
//
// 为什么是**编译期**而不是卡上开关：读卡要用游戏 nnSdk 的文件 API，而装挂点这一步跑在
// **worker 线程**上（见 `g_CardSwitchProbeAllowed` 上面的事故记录）。所以挂点二分只能一条构建
// 一个配置；卡上开关只留给"游戏主线程上读得到"的那两个（`no-menu` / `no-lua`）。
#if defined(EXL_ONLY_REQUIRED_HOOK) && EXL_ONLY_REQUIRED_HOOK == 1
bool SkipOptionalHooksPredicate(void*, isaac::runtime::HookId id) noexcept {
    return !isaac::runtime::IsRequired(id);
}
#endif

u32 MapModLoadFailure(const isaac::runtime::ModLoadFailure& failure) {
    if (failure.step == isaac::runtime::ModLoadStep::None) return 0;
    if (failure.step == isaac::runtime::ModLoadStep::LuaInit) return 0x10U + failure.detail;
    return static_cast<u32>(failure.step);
}

// `runtime_constants.hpp` 与 `manifest_service.hpp` 各有一份同名常量，历史上就是靠"两处各自
// 演化"埋过坑（缓冲区大小与服务判定不一致 = 一类只在真机上才能发现的错）。这里把它们钉在一起：
// 两个头文件在本文件里同时可见。
static_assert(isaac::runtime::kRomfsModScriptMaximumLength == kRomfsModScriptMaximumLength,
              "脚本缓冲区上限在两处定义必须一致（runtime_constants.hpp / manifest_service.hpp）");
// 同上：多模组上限在旧解析器与 domain 各有一份，必须一致 —— 不一致时"清单里有 4 个 Mod"
// 会在解析层通过、在解析结果类型里装不下（或者反过来），而这类错只在真机上看得见。
static_assert(isaac::runtime::kModManifestCapacity == ::ModManifest::kMaximumSelectedMods,
              "多模组上限在两处定义必须一致（mod_manifest.hpp / domain/mod_manifest.hpp）");

// 把 `ModLoadFailure` 变成诊断字 `[11]`：低 8 位沿用旧步骤字，bits 8..15 是原因（`StatusCode`
// 或 Lua 的 detail），bits 16..47 是读取失败时观测到的文件长度。
u32 PackModLoadDiagnostics(const isaac::runtime::ModLoadFailure& failure) {
    const u32 stepWord = MapModLoadFailure(failure);
    if (stepWord == 0) return 0;
    return isaac::runtime::PackModLoadWord(stepWord, failure.detail, failure.observedBytes);
}

bool LoadDefaultManifestModThroughService(u32* failureDetail) {
    // ★ 进入这个函数 = **我们确定自己在游戏主线程上**（它只由 `ManagerUpdateHook::Callback` 调用，
    // 而那个回调是游戏自己的 `Manager::Update` 帧里进来的）。从这一行起才允许用游戏 nnSdk 的
    // 文件 API（卡上开关就要用它）—— 理由见 `g_CardSwitchProbeAllowed` 上面那段事故记录。
    g_CardSwitchProbeAllowed.store(1, std::memory_order_release);
    // 卡上开关 `no-lua.on`：**这一轮一个 Lua 状态都不建**（自带菜单与真实模组都不加载，
    // 连清单/自动发现都不走）。用来把"运行时在，但 Lua 侧完全不存在"这一档单独隔离出来。
    // 注意它不改变挂点安装：挂点层面的二分用 `ONLY_REQUIRED_HOOK=1` 的构建。
    if (CardSwitchNoLua()) {
        if (failureDetail != nullptr) {
            *failureDetail = 0;
        }
        return true;
    }
    isaac::runtime::GameFileReaderAdapter content{g_DefaultManifestBindings};
    isaac::runtime::EmbeddedLuaAdapter lua{g_DefaultManifestBindings};
    // The JSON backend stays the hand-written parser that already runs on
    // device; the adapter is the only bridge to it, so `ManifestParser` and
    // `ManifestService` have no legacy dependency of their own.
    //
    // 两个后端一起给：`SelectFunction` 是"取第一个启用的"（历史口径，诊断阶段仍在用），
    // `SelectAllFunction` 是"取全部启用的"（多模组加载用的就是它）。
    isaac::runtime::ManifestService manifestService{
        content, isaac::runtime::ManifestParser{
                     isaac::runtime::ManifestSelectorAdapter::SelectFunction(),
                     isaac::runtime::ManifestSelectorAdapter::SelectAllFunction()}};
    isaac::runtime::ModLoadService service{content, lua};
    isaac::runtime::ModLoadFailure failure{};
    TestRunObserver::Mark(TestRunObserver::State::LuaInitializeEntered);
    // 一批 Mod 的路径缓冲放在**静态存储**里：`ResolvedManifestModBatch` 约 9.6 KiB，
    // 放在这条路径的栈上不合适（它跑在游戏线程），而且 `ResolvedManifestMod` 里的指针
    // 必须活得和这一批一样久 —— 挂在函数内的 static 正好满足。
    static isaac::runtime::ResolvedManifestModBatch batch{};
    const isaac::runtime::Status resolved =
        manifestService.ResolveAll(g_DefaultManifest.data(), g_DefaultManifest.size(), &batch,
                                   &failure);
    // 方案 A（2026-09-16）：**清单是可选项**。清单缺席（或读不出来/写坏了）时，运行时自己列
    // `isaac_mods/mods`，把发现的每个子目录当成一个模组 —— 玩家"把模组文件夹丢进卡里"就装好了，
    // 与 PC 的语义一致（见 `mod_discovery_service.hpp`）。
    //
    // 三条纪律：
    //   1. **有合法清单就听清单**（顺序、开关都由清单说了算，向后兼容）；
    //   2. 自动发现只在**清单路径失败**时兜底，而且只在失败原因是"没有清单/读不出来/解析失败"
    //      这类"清单不可用"的情况 —— 路径拼装失败（`PathBuild`）说明清单本身有问题，
    //      那种情况下"静默改用自动发现"会把用户写的清单悄悄忽略掉；
    //   3. 自动发现失败时**如实报失败**，不要退回"什么都没加载"却看起来正常。
    u32 discoveryMark = 0;
    if (!resolved.ok()) {
        const bool manifestUnusable =
            failure.step == isaac::runtime::ModLoadStep::ManifestRead ||
            failure.step == isaac::runtime::ModLoadStep::ManifestParse;
        const isaac::runtime::Status discovered =
            manifestUnusable ? g_ModDiscoveryService.Discover(&batch)
                             : isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
        if (discovered.ok()) {
            discoveryMark = static_cast<u32>(DefaultManifestFailureDetail::Discovered);
        } else {
            const u32 detail = MapModLoadFailure(failure);
            *failureDetail = detail;
            g_DefaultManifestFailureWord.store(PackModLoadDiagnostics(failure),
                                               std::memory_order_release);
            TestRunObserver::Mark(TestRunObserver::State::LuaInitializeReturned, detail);
            return false;
        }
    }
    (void)discoveryMark;
    // 模组开关（2026-09-16，路线 B）：**在挂载点与脚本之前**按状态把被关掉的模组移出去。
    // 真机已验：模组加载这一步存档分区已经挂好，所以这一次读就是"当次生效"的。
    // 读失败（通道不可用/文件读坏）时**不拦**：宁可照常加载（用户看到的是"开关没生效"），
    // 也不要因为读不出状态就少加载模组 —— 那会变成"游戏行为莫名其妙变了"。
    // 菜单要显示**全部**扫描到的模组（包括被关掉的，用户正是要重新打开它们），
    // 所以清单在过滤**之前**先灌进去；过滤之后再把"这次真的加载了哪些"标上。
    isaac::runtime::ModMenuAttachToggles(&g_ModToggleService);
    isaac::runtime::ModMenuPublishDiscovered(batch);
    if (const isaac::runtime::Status toggles = g_ModToggleService.Load(); toggles.ok()) {
        (void)g_ModToggleService.ApplyToBatch(&batch);
    }
    isaac::runtime::ModMenuMarkActive(batch);

    // 每个 Mod 各自的资源目录都要挂上，并且要在**它自己的**入口脚本之前挂好 ——
    // 一个只带 `resources/` 的 Mod 就是靠这一步生效的。挂载点表由引擎在内容重载时重建，
    // 那时会丢掉这里的挂点，所以 `ContentMountService` 同时记住这些 Mod 供重建中继恢复。
    for (std::size_t index = 0; index < batch.count; ++index) {
        (void)g_ContentMountService.RegisterMod(batch.mods[index].modRoot);
    }
    // 注意：**这里刻意不登记任何东西**。贴图侧的结论是——引擎最终只认磁盘上真实存在的
    // `.pcx`，所以"把请求改回 `.png`"的那条图片中继保持空转（表为空 = 永不改写），转码由
    // 打包工具 `tools/pc_mod_manifest.py` 在生成 romfs 时完成（PNG 旁边同时写入同名 `.pcx`）。
    // 模组开关菜单：**永远先作为"第一个模组"加载**（2026-09-16 真机结论见该函数注释）。
    // 放在模组之前有两个理由：① 它要最先看到呼出键（晚了会被模组抢走）；② 它是"模组全关时
    // 菜单依然可用"的实现方式 —— 不再需要那条"只有菜单的运行时"特殊路径。
    static_cast<void>(LoadEmbeddedMenuMod());
    isaac::runtime::ModLoadBatchOutcome loadOutcome{};
    const isaac::runtime::Status loaded =
        service.LoadAll(batch, g_DefaultEntry.data(), g_DefaultEntry.size(), &loadOutcome,
                        &failure);
    u32 detail = MapModLoadFailure(failure);
    // 模组加载失败（脚本报错等）会让运行时把 Lua 状态收掉 —— 那样连自带菜单都没了，
    // 用户就失去"在游戏里把出问题的模组关掉"的唯一手段。这里把菜单重新装一遍（标准模组路径），
    // 失败本身照旧如实上报（诊断字不变），只是菜单还活着。
    if (!loaded.ok() || loadOutcome.anyFailure) {
        static_cast<void>(LoadEmbeddedMenuMod());
    }
    if (loaded.ok() && !loadOutcome.anyFailure && loadOutcome.count > 0) {
        // 全部成功时，用**第一个** Mod 的脚本状态决定诊断字：PC 的清单里第一个 Mod 通常就是
        // 主体（我们的 EID 部署就是这样）。任何 Mod 失败都走上面的失败分支。
        if (discoveryMark != 0) {
            // 自动发现：诊断字用 6（`Discovered`）——与"清单里指定"区分开，
            // 真机上一眼就能看出"这次是靠扫描加载的"。
            detail = discoveryMark;
        }
        const isaac::runtime::ModLoadOutcome& first = loadOutcome.scripts[0];
        if (detail == 0 && !first.ScriptExecuted()) {
            // 纯资源型 Mod（清单没写 `entry`，或 entry 指向的文件不存在）：加载**成功**，
            // 内容挂载点已注册，只是没有脚本可跑。诊断字 [11] 用 5 把它和"带脚本成功（0）"
            // 区分开。多模组下"第一个 Mod 无脚本、后面有脚本"是合法清单，这里如实报 5，
            // 具体每个 Mod 的状态在 `loadOutcome.scripts[]` 里。
            detail = static_cast<u32>(DefaultManifestFailureDetail::Scriptless);
        }
    }
    *failureDetail = detail;
    // 诊断字 `[11]`：脚本型成功为 0、纯资源型为 5、失败则带上"原因 + 观测到的文件大小"。
    g_DefaultManifestFailureWord.store(
        loaded.ok() ? detail : PackModLoadDiagnostics(failure), std::memory_order_release);
    // The layered path owns the whole use case, so the "Lua returned" mark
    // carries the same composite detail the install observer records: 0 on a
    // scripted success, the legacy step code on failure, and 5 when the Mod was
    // mounted without a script (Lua was deliberately never initialized).
    TestRunObserver::Mark(TestRunObserver::State::LuaInitializeReturned, detail);
    return loaded.ok() && !loadOutcome.anyFailure;
}

bool PrimeDefaultCallbacks(uintptr_t manager) {
    if (g_DefaultCallbackGate.IsOpen()) return true;
    return g_DefaultCallbackGate.Observe(ObserveGameIsPaused(
        g_LuaGameOwnerSlot.load(std::memory_order_acquire),
        g_LuaGameIsPausedThunk.load(std::memory_order_acquire)),
        LuaRuntime::IsMusicReadyForDefaultCallback(manager));
}

#endif

bool IsMappedRxModuleCodeWindow(uintptr_t address, size_t length) {
    if (length == 0 || address > UINTPTR_MAX - length) {
        return false;
    }
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0) {
        return false;
    }
    const uintptr_t end = address + length;
    const uintptr_t mappingEnd = info.addr + info.size;
    return mappingEnd >= info.addr && address >= info.addr && end <= mappingEnd &&
           (info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx;
}

HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook) {
    static void Callback(IsaacRepentance::Manager* self) {
        g_RenderFrameManager.store(reinterpret_cast<uintptr_t>(self), std::memory_order_release);
#if defined(EXL_LAYERED_RUNTIME)
        // One relaxed increment per entry. This is the only thing in a production
        // build that can prove whether this callback is reached at all.
        g_UpdateCallbackEntries.fetch_add(1, std::memory_order_relaxed);
#endif
#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
        // Consume a bounded confirmation/failure retry at the next game-thread
        // safe point; stable frames do no file I/O once the queue is empty.
        if (PersistenceEventJournal::HasPending()) {
            PersistenceEventJournal::DrainPendingBounded(1);
        }
#endif
        if (PersistenceEventJournal::MarkOnce(
                PersistenceEventJournal::Event::ManagerCallbackEntered)) {
            PersistenceEventJournal::FlushBounded();
        }
        ManagerUpdateHookAudit::RecordCallbackEntry();
        PersistenceTrace::RecordCallbackEntry();
#if defined(ISAAC_RUNTIME_HAS_DIAGNOSTICS_BRIDGE)
        // Diagnostics arms itself here and retries until the file port is usable; it
        // is deliberately NOT called from the SaltyNX registration callback, where
        // object construction plus file I/O crashed on hardware (report 01789053980).
        // The call is unconditional: an attach gated on a frame counter produced no
        // evidence at all on hardware, and the bridge makes it a no-op once armed.
        IsaacModRuntime_TryAttachDiagnostics();
#endif
#if defined(EXL_LAYERED_RUNTIME)
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_FILEIO
        // Self-journal. Bounded to three attempts per marker inside the writer, so a
        // steady frame does no file I/O; the record names this copy of the module and
        // reports whether the registered file table is visible from the code that runs.
        // Probe builds only: its route (libnx `fs`) is measured dead on hardware.
        //
        // 由 `PROBE_BREAK_FILEIO` 单独控制。**这一处是第一轮二分漏掉的那个**：它和入口、
        // 渲染钩子那两处一样会走 service-manager 与 fs 两段阻塞式 IPC 初始化，而更新钩子在
        // 加载期间就会触发（2026-09-13）。
        //
        // 注：这里刻意不写出那两个初始化函数的字面名字 —— `test_runtime_constants` 的
        // "默认运行时不得出现文件系统日志路径"是按**整文件子串**判定的，注释里出现也会红。
        // 只改措辞、不放宽断言：真正的调用一旦写进来，那条门禁仍旧会红。
        IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalUpdateMarker);
#endif
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_CONTENT_MOUNT
        // Stage 147: register the test mod's `resources/` directory as a KAGE content
        // mount point and report whether the engine then resolves a file that only
        // exists in the SD overlay. Armed earlier than the break below because a break
        // ends the session and this is the open question.
        // 由"测试 Mod 自己画了多少帧"触发（见 `kContentMountProbeAfterDraws` 的注释）：update
        // 回调次数与渲染帧数都会在加载阶段被烧掉大半，只有 Mod 的绘制计数只在房间里增长。
        if (g_ContentMountProbeArmed.load(std::memory_order_relaxed) == 0) {
            double drawn = 0.0;
            const bool drewEnough =
                LuaRuntime::ReadLuaGlobalNumber("STAGE149_FRAME", &drawn) &&
                drawn >= static_cast<double>(isaac::runtime::kContentMountProbeAfterDraws);
            // 兜底：主口径（Mod 自己画够帧数）没出现时也必须有一次报告。Mod 的回调一旦报错，
            // 派发器会**静默摘除**它，屏幕上什么都不显示也不会崩 —— 没有这条兜底，那一轮就只剩
            // "没有报错也没有显示"这个现象（2026-09-13 第七轮实测）。
            const bool waitedEnough =
                g_UpdateCallbackEntries.load(std::memory_order_relaxed) >=
                isaac::runtime::kContentMountProbeFallbackAfterUpdates;
            // 批次 2c 探针：把"回调里的 Lua 错误"变成可读证据（`ISAACERR`）。派发器在回调
            // `lua_pcallk` 失败时会静默摘除那个回调，现象就是"不崩、也不显示"。放在本块最前面：
            // 同一帧里它必须先于另外两个探针看到会话状态，也只有它能结束会话（其余两个已让路）。
            IsaacModRuntime_ProbeCallbackError();
            // 批次 2 探针：每帧判一次"玩家数组是否已经可读"，命中就报错一次
            // （`Isaac.GetPlayer` 的偏移实现前必须先有这份真机证据）。批次 2c 起它先给上面的
            // 错误探针让路（`kEnginePlayerProbeDeferUntilErrorProbe`），保证一次会话只崩一次。
            IsaacModRuntime_ProbeEnginePlayers();
            if (drewEnough || waitedEnough) {
                g_ContentMountProbeArmed.store(1, std::memory_order_relaxed);
                IsaacModRuntime_ProbeContentMountPoint();
            }
        }
        // 批次 4 探针（`ISAACEL1`）：`Room + 0x1950` 内嵌 `EntityList` 这条静态结论从未上过
        // 真机 —— 本探针在"两级 Game 链与容器指纹全部成立、活表非空、玩家已存在"时，把容器
        // 指纹、三张表的 begin/count、首个实体字段与一次自遍历（vptr 命中数 + 分区直方图）
        // 装进栈负载报错一次（负载落在崩溃报告 Stack Dump 的 `[SP, SP+0x100)` 窗口内）。
        //
        // 位置：**本块最后一个**（既有探针调用之后）。四个探针都会直接报错结束会话，所以
        // "排在最后"就意味着本探针**可能永远轮不到**：可能抢在它前面的会话结束者有
        // `IsaacModRuntime_ProbeCallbackError` 的 600 次兜底、内容挂载点的 600 帧绘制口径，
        // 以及渲染回调里那个 1500 帧的通用探针 break。本探针触发前还会检查既有的一次性标志
        // （见 `content_mount_point_probe.cpp` 的 `kEntityListProbeYieldsToOtherProbes`），
        // 保证不会把别人的证据抢掉。要单独复跑 `ISAACEL1` 时，把上面两个探针的调用与渲染路径
        // 的通用 break 关掉/推后即可，本探针的触发逻辑不用动。
        IsaacModRuntime_ProbeEntityList();
        // 探针 break 由渲染帧数统一触发（见渲染回调里的那处），这里只保留 update 回调的计数，
        // 免得两次计时口径不一致时先到的那个把会话提前结束。
#endif
#endif
        PersistenceTrace::Flush();
        ManagerUpdateHookAudit::Flush();
        Orig(self);
        PersistenceTrace::Mark(PersistenceTrace::Phase::OriginalReturned);
        if (PersistenceEventJournal::MarkOnce(
                PersistenceEventJournal::Event::OriginalReturned)) {
            PersistenceEventJournal::FlushBounded();
        }
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ObserveStage88InstantRestart(self);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ObserveStage89RestartActionControllers(self);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        if (g_Stage118Armed.exchange(false, std::memory_order_acq_rel)) {
            RunStage118ManagedFileWriteDiagnostic();
        }
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 7
#if !defined(EXL_DIAGNOSTIC_STAGE)
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
        if (DefaultManifestInitializationAllowed()) {
#endif
        DefaultManifestState expected = DefaultManifestState::Armed;
        if (g_DefaultManifestState.compare_exchange_strong(expected, DefaultManifestState::Running,
                                                           std::memory_order_acq_rel)) {
            PersistenceTrace::Mark(PersistenceTrace::Phase::ManifestStarted);
            PersistenceEventJournal::MarkAndFlush(
                PersistenceEventJournal::Event::ManifestEntered);
            u32 failureDetail = 0;
            TestRunObserver::Mark(TestRunObserver::State::ManifestInstallEntered, 1);
            const bool initialized = InitializeDefaultManifestMod(&failureDetail);
            TestRunObserver::Mark(TestRunObserver::State::ManifestInstallReturned,
                                  initialized ? 0 : failureDetail);
            g_DefaultManifestFailureDetail.store(failureDetail, std::memory_order_release);
            g_DefaultManifestState.store(initialized ? DefaultManifestState::Ready : DefaultManifestState::Failed,
                                        std::memory_order_release);
            PersistenceEventJournal::MarkAndFlush(
                PersistenceEventJournal::Event::ManifestReturned,
                PersistenceEventJournal::Operation::None,
                initialized ? 0U : failureDetail,
                static_cast<u64>(g_DefaultManifestState.load(std::memory_order_acquire)));
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
            ReportStartupProbeStage8ManifestResult(initialized, failureDetail);
#endif
#if defined(EXL_PERSISTENCE_TRACE)
            u64 traceDetail = failureDetail;
            if (!initialized &&
                (failureDetail == 0x10U + static_cast<u32>(LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed) ||
                 failureDetail == 0x10U + static_cast<u32>(LuaRuntime::LuaInitResult::RuntimePreparationFailed))) {
                const auto result = static_cast<LuaRuntime::LuaInitResult>(failureDetail - 0x10U);
                const u64 preparationDetail = LuaRuntime::PreparationFailureDetail();
                traceDetail = (static_cast<u64>(result) << 32) | preparationDetail;
            }
            if (!initialized) traceDetail |= PersistenceTrace::kFailureDetailMask;
            PersistenceTrace::Mark(PersistenceTrace::Phase::ManifestCompleted, traceDetail);
#endif
        }
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
        }
#endif
#endif
        if (LuaRuntime::IsReady()) {
#if !defined(EXL_DIAGNOSTIC_STAGE)
            const bool fileApiReady = ModPersistence::IsFileApiReady();
            const bool shouldDispatch =
                g_DefaultPersistenceGate.ShouldDispatch(fileApiReady, kDefaultPersistenceRequired);
            const u64 gateDetail =
                (static_cast<u64>(fileApiReady ? 1U : 0U) << 24) |
                (static_cast<u64>(kDefaultPersistenceRequired ? 1U : 0U) << 16) |
                (static_cast<u64>(shouldDispatch ? 1U : 0U) << 8) |
                static_cast<u64>(g_DefaultManifestState.load(std::memory_order_acquire));
            if (PersistenceEventJournal::MarkIfChanged(
                    PersistenceEventJournal::Event::GateEvaluated,
                    PersistenceEventJournal::Operation::None,
                    shouldDispatch ? 1U : 0U, gateDetail)) {
                PersistenceEventJournal::FlushBounded();
            }
            if (shouldDispatch) {
#if defined(EXL_PERSISTENCE_TRACE) && defined(EXL_PERSISTENCE_REQUIRED)
                if (fileApiReady) {
                    const u64 detail =
                        (static_cast<u64>(g_DefaultManifestState.load(std::memory_order_acquire)) << 8) | 1ULL;
                    PersistenceTrace::Mark(PersistenceTrace::Phase::PersistenceGateReady, detail);
                }
#endif
#endif
            const bool firstDispatch = PersistenceEventJournal::MarkOnce(
                PersistenceEventJournal::Event::DispatchEntered);
            if (firstDispatch) PersistenceEventJournal::FlushBounded();
            PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchStarted);
            LuaRuntime::DispatchPostUpdate();
            PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchReturned);
            if (PersistenceEventJournal::MarkOnce(
                    PersistenceEventJournal::Event::DispatchReturned)) {
                PersistenceEventJournal::FlushBounded();
            }
#if defined(EXL_PERSISTENCE_TRACE)
            if (LuaRuntime::TakeCallbackError()) {
                const u64 callbackDetail = LuaRuntime::CallbackFailureDetail();
                PersistenceTrace::Mark(PersistenceTrace::Phase::CallbackFailed, callbackDetail);
            }
#elif defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
            if (LuaRuntime::TakeCallbackError()) {
                const u64 callbackDetail = LuaRuntime::CallbackFailureDetail();
                PersistenceEventJournal::MarkAndFlush(
                    PersistenceEventJournal::Event::CallbackFailed,
                    PersistenceEventJournal::Operation::None, 0, callbackDetail);
            }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 7
            if (LuaRuntime::TakeCallbackError()) {
                svcBreak(BreakReason_User, 0x49534141435F4C46ULL, (7ULL << 32) | 5ULL);
                svcExitProcess();
            }
            if (LuaRuntime::PostUpdateCount() >= 120) {
                svcBreak(BreakReason_User, 0x49534141435F4C50ULL, (7ULL << 32) | 120ULL);
                svcExitProcess();
            }
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE)
            }
#endif
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        switch (g_Stage14Controller.TryClaimCallback()) {
        case Stage14Diagnostic::Claim::WaitForReady:
        case Stage14Diagnostic::Claim::IgnoreFinished:
            return;
        case Stage14Diagnostic::Claim::Run:
            break;
        }
        LuaRuntime::DispatchPostUpdate();
        const u32 postUpdateCount = LuaRuntime::PostUpdateCount();
        switch (Stage14Diagnostic::ClassifyCallback(
            postUpdateCount, LuaRuntime::TakeCallbackError())) {
        case Stage14Diagnostic::CallbackResult::SuccessFalse:
            ReportStage14Success(false);
        case Stage14Diagnostic::CallbackResult::SuccessTrue:
            ReportStage14Success(true);
        case Stage14Diagnostic::CallbackResult::CallbackError:
            ReportStage14Failure(Stage14Diagnostic::Stage14Failure::CallbackError);
        case Stage14Diagnostic::CallbackResult::CallbackNotReached:
            ReportStage14Failure(Stage14Diagnostic::Stage14Failure::CallbackNotReached);
        case Stage14Diagnostic::CallbackResult::BooleanTypeMismatch:
            ReportStage14Failure(Stage14Diagnostic::Stage14Failure::BooleanTypeMismatch);
        }
        UNREACHABLE;
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        if (LuaRuntime::IsReady()) {
            LuaRuntime::DispatchPostUpdate();
            CheckStage15AfterPausedUpdate();
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        const GameIsPausedObservation observation = ObserveGameIsPaused(
            g_Stage6GameOwnerSlot.load(std::memory_order_acquire),
            g_Stage6GameIsPausedThunk.load(std::memory_order_acquire));
        switch (observation) {
        case GameIsPausedObservation::PausedFalse:
            ReportStage6IsPausedSuccess(false);
        case GameIsPausedObservation::PausedTrue:
            ReportStage6IsPausedSuccess(true);
        case GameIsPausedObservation::OwnerNull:
        case GameIsPausedObservation::OwnerUnreadable:
        case GameIsPausedObservation::GameNull:
        case GameIsPausedObservation::GameUnreadable:
        case GameIsPausedObservation::ThunkUnavailable:
            break;
        }
        const u32 frame = g_Stage6ObserverFrames.fetch_add(1, std::memory_order_relaxed) + 1;
        if (frame < kStage6ObserverFrameLimit) return;
        switch (DeepestGameOwnerObservation()) {
        case GameOwnerObservation::OwnerNull:
            ReportStage6Failure(39);
        case GameOwnerObservation::OwnerUnreadable:
            ReportStage6Failure(40);
        case GameOwnerObservation::GameNull:
            ReportStage6Failure(41);
        case GameOwnerObservation::GameUnreadable:
            ReportStage6Failure(42);
        case GameOwnerObservation::Success:
            UNREACHABLE;
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        Stage11State expected = Stage11State::Armed;
        if (!g_Stage11State.compare_exchange_strong(expected, Stage11State::Running, std::memory_order_acq_rel)) {
            return;
        }
        RunStage11RomfsSentinelDiagnostic();
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        Stage12State expected = Stage12State::Armed;
        if (g_Stage12State.compare_exchange_strong(expected, Stage12State::Running, std::memory_order_acq_rel)) {
            RunStage12RomfsLuaDiagnostic();
        } else if (expected != Stage12State::Ready) {
            return;
        }
        if (g_Stage12State.load(std::memory_order_acquire) != Stage12State::Ready) {
            return;
        }
        LuaRuntime::DispatchPostUpdate();
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage12Failure(17);
        }
        if (LuaRuntime::PostUpdateCount() >= 120) {
            g_Stage12State.store(Stage12State::Finished, std::memory_order_release);
            svcBreak(BreakReason_User, kStage12SuccessMagic, (12ULL << 32) | 120ULL);
            svcExitProcess();
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        Stage13State expected = Stage13State::Armed;
        if (g_Stage13State.compare_exchange_strong(expected, Stage13State::Running, std::memory_order_acq_rel)) {
            RunStage13ManifestRequireDiagnostic();
        } else if (expected != Stage13State::Ready) {
            ReportStage13StateMismatch();
        }
        if (g_Stage13State.load(std::memory_order_acquire) != Stage13State::Ready) {
            ReportStage13StateMismatch();
        }
        LuaRuntime::DispatchPostUpdate();
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage13Failure(24);
        }
        if (LuaRuntime::PostUpdateCount() >= 120) {
            g_Stage13State.store(Stage13State::Finished, std::memory_order_release);
            svcBreak(BreakReason_User, kStage13SuccessMagic, (13ULL << 32) | 120ULL);
            svcExitProcess();
        }
#endif
    }
};

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
HOOK_DEFINE_TRAMPOLINE(MusicReplayMusicPlayHook) {
    static void Callback(void* self, u32 musicId, float volume) {
        (void)self;
        (void)musicId;
        (void)volume;
        RecordStage48MusicPlay();
    }
};

HOOK_DEFINE_TRAMPOLINE(MusicReplaySoundActorPlayHook) {
    static void Callback(void* self, u32 flags) {
        (void)flags;
        RecordStage48SoundActorPlay(reinterpret_cast<uintptr_t>(self));
    }
};

HOOK_DEFINE_TRAMPOLINE(MusicReplaySoundActorPauseHook) {
    static void Callback(void* self) {
        RecordStage48SoundActorPause(reinterpret_cast<uintptr_t>(self));
    }
};
#endif

// 派发实现与"新中继是否可用"的判定，定义在本文件后面（trampoline 需要提前知道它们）。
void DispatchModPostRender(void* self);
bool IsRenderPresentRelayInstalled();

// 两条后端路径共用的渲染钩子体：`call_original` 由后端提供
// （exlaunch 蹦床传 `Orig`，入口中继传回退入口）。
// 注意：`g_RenderFrameManager` 必须在跑原函数**之前**记下，Present 前的派发要用它。
void RenderHookBody(IsaacRepentance::Manager* self,
                    void (*call_original)(IsaacRepentance::Manager*)) {
    // 记下这一帧的 Manager*：Present 前的派发入口（下面）要用它，因为那个调用点的 x0
    // 是图形管理器。同一个线程、同一帧内先入口后 Present，所以读到的就是本帧的值。
    g_RenderFrameManager.store(reinterpret_cast<uintptr_t>(self), std::memory_order_release);
#if defined(EXL_LAYERED_RUNTIME)
    // Positive control for `g_UpdateCallbackEntries`: the Mod's MC_POST_RENDER
    // path is known to work on hardware, so this counter must be non-zero.
    g_RenderCallbackEntries.fetch_add(1, std::memory_order_relaxed);
#endif
    // Second arming point for the diagnostics journal.
    //
    // The Mod's MC_POST_RENDER route is dispatched from this callback and demonstrably
    // runs on hardware, so this callback is known to execute in every session that
    // loads a Mod. The update hook, by contrast, has never been observed to fire:
    // `TryAttachDiagnostics` only ever ran from there, and `isaac-runtime-events.bin`
    // has never appeared once. Arming from both hooks removes that single point of
    // failure and, because the call is a no-op once the journal is attached, costs
    // one predictable branch per frame.
    //
    // 与更新钩子那处同样按 `ISAAC_RUNTIME_HAS_DIAGNOSTICS_BRIDGE` 守卫：那个桥只在分层
    // 构建里编译，未加守卫的非分层/诊断构建会报 "was not declared in this scope"。
#if defined(ISAAC_RUNTIME_HAS_DIAGNOSTICS_BRIDGE)
    IsaacModRuntime_TryAttachDiagnostics();
#endif
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_FILEIO
    // Same bounded self-journal record from the render callback, so a session in
    // which only one of the two hooks fires still names the copy that ran.
    IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalRenderMarker);
#endif
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_FILEIO
    // The "who can write a file" probes, and the two SaltyNX IPC probes. All four are
    // bounded inside, and all four are probe-build only: a production build must not
    // touch any of them, because their whole purpose is to find out which of these
    // routes works at all (2026-09-11: none of the Runtime-side ones does).
    //
    // 由 `PROBE_BREAK_FILEIO` 单独控制：这四个是探针包里在启动阶段就会走失效路由的部分
    // （2026-09-13 二分定位探针包启动故障）。
    IsaacModRuntime_ProbeFileWriteFromHook();
    IsaacModRuntime_ProbeNativeFileWrite();
    IsaacModRuntime_ProbeSaltyIpc();
    IsaacModRuntime_ProbeSaltyReinit();
#endif
    // Probe break, from the render callback as well: a session in which only this
    // hook fires must still produce a crash report that names the running copy.
#if defined(EXL_PROBE_BREAK)
    // 帧时间归因探针（`ISAACEV1`）：A/B 两相跑满（或时间兜底）后，在渲染钩子里结束会话
    // 一次。放在这里而不是派发路径里，是为了让崩溃点始终落在"钩子入口"这个已经反复验证
    // 过归因方式的位置（见 `saltynx_probe_break.hpp` 的证据纪律）。
    if (isaac::runtime::EngineFrameProbeTakeBreak()) {
        IsaacModRuntime_EngineProbeBreak();
    }
#endif
    if (g_RenderCallbackEntries.load(std::memory_order_relaxed) >=
        isaac::runtime::kProbeBreakAfterEntries) {
        IsaacModRuntime_ProbeBreak(isaac::runtime::kProbeBreakRenderCallback);
    }
    call_original(self);
    // 回退路径：`Present` 前中继没装上（补丁缺失或守卫不符）时，仍在入口中继派发一次。
    // 时机不对——那时本帧已经上屏、队列已清空，画出来的东西会进下一帧且被实体盖住——但
    // 至少 Mod 的 MC_POST_RENDER 不会整个消失，也不会一帧派发两次（两条路径互斥）。
    if (!IsRenderPresentRelayInstalled()) {
        DispatchModPostRender(self);
    }
}

// 旧后端（exlaunch 蹦床）：原函数由蹦床的 `Orig` 提供。
HOOK_DEFINE_TRAMPOLINE(ManagerRenderHook) {
    static void Callback(IsaacRepentance::Manager* self) {
        RenderHookBody(self, [](IsaacRepentance::Manager* inner) { Orig(inner); });
    }
};

// 新后端（入口中继）：原函数由 entry_relay 记录的回退入口提供。
// 入口桩是**尾调用**，所以本回调以 `Manager::Render` 的身份运行，
// `ret` 直接回到游戏的调用方；ABI 必须与目标完全一致（`IsaacRepentance::Manager*`）。
extern "C" void EntryRelayManagerRenderCallback(void* self) {
    auto* manager = reinterpret_cast<IsaacRepentance::Manager*>(self);
    RenderHookBody(manager, [](IsaacRepentance::Manager* inner) {
        const auto original =
            isaac::runtime::Original<void (*)(IsaacRepentance::Manager*)>(
                isaac::runtime::HookId::ManagerRender);
        original(inner);
    });
}

// 新后端（入口中继）下的更新挂点。与渲染那一路的差别很关键：**不另写一份钩子体**。
// 蹦床里的 `Callback` 用 `Orig(self)` 调原函数；中继把原函数存在自己的槽里，安装时由
// `PublishEntryRelayOriginals()` 把中继记下的回退入口发布给 `Orig`（`PublishOriginal`）——
// 于是两个后端跑的是**同一个函数**，"行为一致"由构造保证，不必把这份约 300 行的钩子体
// （诊断构建下还有十几处按源码切片断言它的测试）复制成第二份。
//
// 为什么可以直接调 `Callback`：入口桩是**尾调用**，本回调以 `Manager::Update` 的身份运行，
// 与蹦床调 `Callback` 时的 ABI 完全一致（`IsaacRepentance::Manager*`）。
extern "C" void EntryRelayManagerUpdateCallback(void* self) {
    ManagerUpdateHook::Callback(reinterpret_cast<IsaacRepentance::Manager*>(self));
}

// `MC_POST_RENDER` 的派发入口。调用者不是 trampoline，而是 `Manager::Render` 体内最后一次
// `Present` 调用点上的中继桩（IPS 记录 `render-present-relay`）：桩先 `blr` 到这里，返回后
// 才补回原来的 `bl Present`。
//
// 为什么必须放在这里：入口中继把原函数整体当子调用跑完才回来，所以它派发时本帧已经上屏、
// 帧图像队列已经清空。2026-09-13 的真机照片证明后果有两条：回调里画的东西进的是**下一帧**
// 队列，而且在那一帧里排在游戏自己的图像之前 —— 于是画在实体（以撒）与 HUD **之下**，只压在
// 房间地面上。放到 `Present` 之前，图像在本帧入队、本帧 apply，并排在最后一个被画，这才是
// PC 的 `MC_POST_RENDER` 覆盖层语义。
void DispatchModPostRender(void* self) {
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
#if !defined(EXL_DIAGNOSTIC_STAGE)
    if (LuaRuntime::IsReady() && PrimeDefaultCallbacks(reinterpret_cast<uintptr_t>(self))) {
#else
    if (LuaRuntime::IsReady()) {
#endif
        LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(self));
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage15Failure(3);
        }
        if (LuaRuntime::PostUpdateCount() != 0 && LuaRuntime::PostRenderPausedCount() != 0) {
            ReportStage15Success();
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage16Failure(3);
        }
        if (LuaRuntime::MusicDiagnosticCycleCompleted()) {
            ReportStage16Success();
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage17Failure(3);
        }
        if (LuaRuntime::MusicDiagnosticCycleCompleted()) {
            ReportStage17Success();
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage45Failure(3);
        }
        u32 musicId = 0;
        if (LuaRuntime::MusicDiagnosticCurrentId(&musicId)) {
            ReportStage45Success(musicId);
        }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
        if (LuaRuntime::TakeCallbackError()) {
            ReportStage48Failure(3);
        }
        Stage48AdvanceMusicReplayProbe();
        u32 payload = 0;
        if (Stage48TakeMusicReplayReport(&payload)) {
            if ((payload & kStage48ObservedPause) == 0) {
                ReportStage48Failure(4);
            }
            ReportStage48Success(payload);
        }
#endif
    }
#endif
}

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_TextureCacheDiagnostics(std::uint32_t* output) {
    if (output == nullptr) {
        return;
    }
    output[0] = g_TextureCacheCandidates.load(std::memory_order_acquire);
    output[1] = g_TextureCacheWritten.load(std::memory_order_acquire);
    output[2] = g_TextureCacheFailures.load(std::memory_order_acquire);
}

bool IsRenderPresentRelayInstalled() {
    return g_RenderPresentRelayState.load(std::memory_order_acquire) ==
           static_cast<std::uint32_t>(RenderPresentRelayInstallResult::Success);
}

// 由 `Present` 前中继桩调用（中继桩在 `Manager::Render` 体内最后一次 `Present` 调用之前）。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_DispatchPostRenderBeforePresent(void* graphicsManager) {
    g_RenderPresentRelayEntries.fetch_add(1, std::memory_order_relaxed);
    // 参数是 `Present` 的实参（`KAGE::Graphics::g_Manager`），派发路径不用它 —— 见
    // `g_RenderFrameManager` 的注释。两个中继都没跑过时只能拿它兜底；那种情况下
    // `PrimeDefaultCallbacks` 会返回 false，回调不执行，但也不会崩。
    uintptr_t manager = g_RenderFrameManager.load(std::memory_order_acquire);
    if (manager == 0) {
        manager = reinterpret_cast<uintptr_t>(graphicsManager);
    }
    DispatchModPostRender(reinterpret_cast<void*>(manager));
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
NORETURN void ManagerLoadConfigsDiagnosticCallback(IsaacRepentance::Manager* self) {
    if (self == nullptr) {
        svcBreak(BreakReason_User, 0x49534141434D464CULL, (8ULL << 32) | 4ULL);
        svcExitProcess();
    }

    const uintptr_t selfAddress = reinterpret_cast<uintptr_t>(self);
    if (selfAddress > UINTPTR_MAX - kManagerToModManagerOffset) {
        svcBreak(BreakReason_User, 0x49534141434D464CULL, (8ULL << 32) | 4ULL);
        svcExitProcess();
    }

    const uintptr_t candidate = selfAddress + kManagerToModManagerOffset;
    if ((candidate & 7u) != 0) {
        svcBreak(BreakReason_User, 0x49534141434D464CULL, (8ULL << 32) | 4ULL);
        svcExitProcess();
    }

    svcBreak(BreakReason_User, 0x49534141434D4F44ULL, (8ULL << 32) | 0x36800ULL);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
constexpr u64 kStage9FailureMagic = 0x495341414352464CULL;

NORETURN void ReportStage9CallbackFailure(u32 status) {
    svcBreak(BreakReason_User, kStage9FailureMagic, (9ULL << 32) | status);
    svcExitProcess();
}

NORETURN void ManagerLoadConfigsResetDiagnosticCallback(IsaacRepentance::Manager* self) {
    if (self == nullptr) {
        ReportStage9CallbackFailure(4);
    }
    const uintptr_t selfAddress = reinterpret_cast<uintptr_t>(self);
    if (selfAddress > UINTPTR_MAX - kManagerToModManagerOffset) {
        ReportStage9CallbackFailure(4);
    }
    const uintptr_t candidate = selfAddress + kManagerToModManagerOffset;
    if ((candidate & 7u) != 0) {
        ReportStage9CallbackFailure(4);
    }
    const uintptr_t resetAddress = g_Stage9ResetAddress.load(std::memory_order_acquire);
    if (resetAddress == 0) {
        ReportStage9CallbackFailure(3);
    }
    using ResetFn = void (*)(void*);
    const auto reset = reinterpret_cast<ResetFn>(resetAddress);
    reset(reinterpret_cast<void*>(candidate));
    svcBreak(BreakReason_User, 0x4953414143525354ULL, (9ULL << 32) | 0x422440ULL);
    svcExitProcess();
}
#endif

} // namespace

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
void MarkStage14DiagnosticReady() {
    g_Stage14Controller.MarkReady();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
NORETURN void ReportStage13Failure(u32 status) {
    g_Stage13State.store(Stage13State::Finished, std::memory_order_release);
    svcBreak(BreakReason_User, kStage13FailureMagic, (13ULL << 32) | status);
    svcExitProcess();
}
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE)
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
void AllowDefaultManifestInitialization() {
    g_DefaultManifestInitializationAllowed.store(1, std::memory_order_release);
}
#endif

// 走入口中继的挂点：中继把"原函数"记在自己的槽里，蹦床把它记在 `Orig` 里。要让两个后端共用
// 同一份钩子体，就在中继装好后把中继记下的**回退入口**发布给蹦床的 `Orig`
// （`TrampolineHook::PublishOriginal`）：此后钩子体里的 `Orig(...)` 等价于"跳回原入口"。
//
// 只处理**复用 `Callback` 体**的那一路（`ManagerUpdate`）。`ManagerRender` 的钩子体早已抽成
// 自由函数、由中继回调自己读回退入口，因此不需要发布。
//
// 时序：在 `TryInstallDefaultManifestMod` 里安装成功后立即调用，仍在模块初始化线程上
// （游戏主循环尚未开始），所以不存在"入口已改写、`Orig` 还是空"的窗口。发布失败（地址为 0
// 或未对齐）意味着安装器的守恒被破坏，此处不做兜底 —— 与 `ManagerRender` 那一路"回退入口
// 在安装函数返回前写入"的既有口径一致。
void PublishEntryRelayOriginals(const TargetModule& module) {
    const auto usesRelay = [](isaac::runtime::HookId id) {
        return isaac::runtime::kHookBackends[static_cast<std::size_t>(id)] ==
               isaac::runtime::HookBackend::EntryRelay;
    };
    if (usesRelay(isaac::runtime::HookId::ManagerUpdate)) {
        const uintptr_t original =
            isaac::runtime::EntryRelayFallbackEntry(isaac::runtime::HookId::ManagerUpdate);
        if (original != 0) {
            ManagerUpdateHook::PublishOriginal(original);
        }
    }
    // 取道具前挂点：旧安装器在这一步还**兼职**把"原函数入口"交给 Lua 侧的
    // `ItemPool:GetCollectible`（`ValidateItemPoolGetCollectibleMethod` 会核对那个地址的形态）。
    // 零占洞路径发布的地址与旧后端**完全相同** —— 都是游戏入口本身：从 Lua 调它等于再走一遍整条
    // 取道具路径（含 Mod 回调，与 PC 语义一致）；入口现在被入口中继的桩接管，桩会把控制权交回我们的
    // 回调，而回调里的"正在派发"守卫会让它直接落到原函数。
    if (usesRelay(isaac::runtime::HookId::PreGetCollectible)) {
        // 这里直接写 `base + offset`（不经过 `target_address`：它的定义在本函数之后）。
        LuaRuntime::SetItemPoolGetCollectibleBinding(module.base + kPreGetCollectibleRelayOffset);
    }
}

// 引擎绑定的校验与发布：定义在文件后部、与旧更新钩子安装器相邻（要用的 Verify* 辅助函数都在那儿）。
// 这里先声明，因为**零占洞后端路径也要调用它** —— 那条路径不经过旧安装器，若不显式调用，
// Sprite/Font 等绑定就会缺失，Mod 的 Lua 在 `Sprite()` 处直接失败（2026-09-14 真机）。
void PublishManagerEngineBindings(const TargetModule& module);

DefaultManifestInstallResult TryInstallDefaultManifestMod(const TargetModule& module) {
    uintptr_t target = 0;
    uintptr_t slot = 0;
    GameFileReader::Bindings bindings{};
    // 这里验哪一套前提，取决于更新挂点走哪个后端（2026-09-14 真机撞出来的迁移缺陷）：
    //   * 旧 IPS 后端：游戏映像里**必须有**那段中继（入口已被改写成跳转、洞里有桩），
    //     `VerifyManagerRelay` 检查的正是这个前提；
    //   * 零占洞后端：前提**正好相反** —— 入口要保持原样（入口中继自己拿 16 字节台账做版本
    //     比对），洞也不再需要。若这时仍按旧前提验，**删掉那份 IPS 之后这里会直接判失败，
    //     整个 Mod 都装不上**（实测：`ManifestInstallReturned` 的 detail = 1，五个挂点全空）。
    // 因此按 `kHookBackends` 选择：走零占洞时只做模块/构建号与文件读取绑定这两项校验。
    const bool updateUsesRelay =
        isaac::runtime::kHookBackends[static_cast<std::size_t>(
            isaac::runtime::HookId::ManagerUpdate)] == isaac::runtime::HookBackend::EntryRelay;
    const bool updateVerified = VerifyManagerUpdate(module, &target);
    const bool relayVerified =
        updateUsesRelay || (updateVerified && VerifyManagerRelay(module, target, &slot));
    if (!updateVerified || !relayVerified ||
        !GameFileReader::VerifyBindings(module, &bindings)) {
        return DefaultManifestInstallResult::FileBindingMismatch;
    }
    g_DefaultManifestBindings = bindings;
    g_DefaultManifestState.store(DefaultManifestState::Armed, std::memory_order_release);
    // The required/optional interception policy lives in HookInstallService, so
    // this function only verifies the manifest bindings, arms the state and
    // delegates installation of every hook (including the two optional relays
    // that used to be installed directly here).
    isaac::runtime::ModuleInfo info{};
    info.base = module.base;
    info.textSize = module.textSize;
    info.imageSize = module.size;
    info.valid = module.base != 0 && module.textSize != 0;
    const std::size_t buildIdBytes = std::min(info.buildId.size(), module.buildId.size());
    std::memcpy(info.buildId.data(), module.buildId.data(), buildIdBytes);
    // 先在安装前把真实回调接进入口中继绑定表，再换成按挂点路由的适配器
    // （M2a 第一路：`ManagerUpdate` 已切到 `EntryRelay`；`ManagerRender` 是 M1 切的）。
    isaac::runtime::RegisterEntryRelayCallbacks();
    isaac::runtime::HookRoutingAdapter hookAdapter{};
    isaac::runtime::HookInstallService hookService{hookAdapter};
#if defined(EXL_ONLY_REQUIRED_HOOK) && EXL_ONLY_REQUIRED_HOOK == 1
    // 二分构建（2026-09-16）：这一轮**只装必需挂点**（`ManagerUpdate`），五个可选挂点一个都不碰。
    // 用来回答"主界面停 10 秒崩 / 退出对局崩"是不是可选挂点引起的。
    // ⚠️ 判据必须是**编译期常量**：这一步跑在 worker 线程上，读卡会崩（见 `CardSwitchOn` 上面那段）。
    hookService.SetSkipPredicate(&SkipOptionalHooksPredicate, nullptr);
#endif
    isaac::runtime::HookInstallReport hookReport{};
    const bool hooksInstalled =
        info.valid && hookService.InstallProductionHooks(info, &hookReport).ok();
    // 必须在下面那个早退之前：装失败也要能在设备侧读到（否则失败结果永远记不下来）。
    // 失败码来自路由适配器转发的入口中继后端（0 表示无失败）。
    isaac::runtime::PublishHookInstallReport(
        hookReport, isaac::runtime::HookRoutingFailureCode(hookAdapter));
    if (!hooksInstalled) {
        DefaultManifestState expected = DefaultManifestState::Armed;
        if (g_DefaultManifestState.compare_exchange_strong(expected, DefaultManifestState::Unarmed,
                                                           std::memory_order_acq_rel)) {
            g_DefaultManifestBindings = {};
        }
        return DefaultManifestInstallResult::FileBindingMismatch;
    }
    // 更新挂点复用蹦床里那个 `Callback`（入口中继回调直接调它），所以必须在这里把中继记下的
    // 回退入口发布给 `Orig` —— 此后的游戏帧才会走到我们的代码。到达这一行意味着
    // `InstallProductionHooks` 已返回成功，即必需的更新挂点已经装上。
    // 同一个函数里还会补上"取道具前"挂点原本由旧安装器兼管的那项发布（`ItemPool:GetCollectible`
    // 的原函数绑定），理由见其定义处。
    PublishEntryRelayOriginals(module);
    // 引擎绑定（Sprite/Font/音乐/输入/RNG/玩家槽…）也必须在这里发布：这些绑定原本只在旧 IPS
    // 安装器里发布，而更新挂点一旦走零占洞后端就不再经过那个安装器。缺了它们，Mod 的 Lua
    // 会在 `Sprite()`/`Font()` 处当场失败（2026-09-14 真机，见那个函数的注释）。
    // 与 `Orig` 的发布同理：此处仍在模块初始化线程上，游戏主循环尚未开始，`InitMod` 之前必定完成。
    PublishManagerEngineBindings(module);
    // `MC_POST_RENDER` 的 Present 前派发点与内容挂载点重建中继都是可选能力：装不上只是
    // 回退（回调晚一帧派发）或不重建挂载点，不影响安装成败 —— 两个都由
    // `HookInstallService::InstallProductionHooks` 经 `IHookPort` 安装，装没装上记在
    // `HookInstallReport` 里（设备侧可回读）。
    return DefaultManifestInstallResult::Success;
}
#endif

uintptr_t target_address(uintptr_t base, uintptr_t offset) {
    return base + offset;
}

bool verify_bytes(const u8* expected, const u8* actual, size_t length) {
    return expected != nullptr && actual != nullptr && std::memcmp(expected, actual, length) == 0;
}

bool VerifyManagerUpdate(const TargetModule& module, uintptr_t* target) {
    if (target == nullptr || module.base == 0 || module.textSize < kManagerUpdateFileOffset + kManagerRelayExpectedEntry.size()) {
        return false;
    }
    if (module.buildId != kTargetBuildId || kManagerUpdateFileOffset % 4 != 0) {
        return false;
    }
    const uintptr_t candidate = target_address(module.base, kManagerUpdateFileOffset);
    if (candidate < module.base || !module.Contains(candidate, kManagerRelayExpectedEntry.size()) || candidate % 4 != 0) {
        return false;
    }
    if (!IsMappedRxModuleCodeWindow(candidate, kManagerRelayExpectedEntry.size())) {
        return false;
    }
    *target = candidate;
    return true;
}

bool VerifyManagerRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || target == 0 ||
        module.textSize < kManagerRelayCodeOffset + kManagerRelayExpectedBytes.size()) {
        return false;
    }
    if (target != target_address(module.base, kManagerUpdateFileOffset) ||
        !module.Contains(target, kManagerRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kManagerRelayExpectedEntry.size())) {
        return false;
    }
    std::array<u8, kManagerRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kManagerRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }

    const uintptr_t relay = target_address(module.base, kManagerRelayCodeOffset);
    if (relay < module.base || !module.Contains(relay, kManagerRelayExpectedBytes.size()) || relay % 4 != 0 ||
        !IsMappedRxModuleCodeWindow(relay, kManagerRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerRelayExpectedBytes.size() - sizeof(uintptr_t)> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kManagerRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }

    const uintptr_t candidateSlot = target_address(module.base, kManagerRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

bool VerifyManagerRender(const TargetModule& module, uintptr_t* target) {
    if (target == nullptr || module.base == 0 ||
        module.textSize < kManagerRenderFileOffset + kManagerRenderRelayExpectedEntry.size() ||
        module.buildId != kTargetBuildId || kManagerRenderFileOffset % 4 != 0) {
        return false;
    }
    const uintptr_t candidate = target_address(module.base, kManagerRenderFileOffset);
    if (candidate < module.base || !module.Contains(candidate, kManagerRenderRelayExpectedEntry.size()) ||
        candidate % 4 != 0 || !IsMappedRxModuleCodeWindow(candidate, kManagerRenderRelayExpectedEntry.size())) {
        return false;
    }
    *target = candidate;
    return true;
}

bool VerifyManagerRenderRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || target == 0 ||
        module.textSize < kManagerRenderRelayCodeOffset + kManagerRenderRelayExpectedBytes.size() ||
        target != target_address(module.base, kManagerRenderFileOffset) ||
        !module.Contains(target, kManagerRenderRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kManagerRenderRelayExpectedEntry.size())) {
        return false;
    }
    std::array<u8, kManagerRenderRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kManagerRenderRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    const uintptr_t relay = target_address(module.base, kManagerRenderRelayCodeOffset);
    if (relay < module.base || relay % 4 != 0 ||
        !module.Contains(relay, kManagerRenderRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kManagerRenderRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerRenderRelayExpectedBytes.size() - sizeof(uintptr_t)> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kManagerRenderRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kManagerRenderRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

namespace {
bool VerifyPreGetCollectibleRelay(const TargetModule& module, uintptr_t* slot, uintptr_t* original) {
    if (slot == nullptr || original == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kPreGetCollectibleRelayExpectedOriginal.size() > module.textSize ||
        kPreGetCollectibleRelayExpectedBytes.size() > module.textSize ||
        sizeof(uintptr_t) > module.textSize ||
        kPreGetCollectibleRelayOffset > module.textSize - kPreGetCollectibleRelayExpectedOriginal.size() ||
        kPreGetCollectibleRelayCodeOffset > module.textSize - kPreGetCollectibleRelayExpectedBytes.size() ||
        kPreGetCollectibleRelaySlotOffset > module.textSize - sizeof(uintptr_t)) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kPreGetCollectibleRelayOffset);
    const uintptr_t relay = target_address(module.base, kPreGetCollectibleRelayCodeOffset);
    const uintptr_t candidateSlot = target_address(module.base, kPreGetCollectibleRelaySlotOffset);
    if (target < module.base || relay < module.base || candidateSlot < module.base ||
        (target & 3u) != 0 || (relay & 3u) != 0 ||
        (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(target, kPreGetCollectibleRelayExpectedOriginal.size()) ||
        !module.Contains(relay, kPreGetCollectibleRelayExpectedBytes.size()) ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(target, kPreGetCollectibleRelayExpectedOriginal.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kPreGetCollectibleRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    std::array<u8, kPreGetCollectibleRelayExpectedOriginal.size()> targetBytes{};
    std::array<u8, kPreGetCollectibleRelayExpectedBytes.size()> relayBytes{};
    std::memcpy(targetBytes.data(), reinterpret_cast<const void*>(target), targetBytes.size());
    std::memcpy(relayBytes.data(), reinterpret_cast<const void*>(relay), relayBytes.size());
    if (!verify_bytes(kPreGetCollectibleRelayExpectedEntry.data(), targetBytes.data(),
                      kPreGetCollectibleRelayExpectedEntry.size()) ||
        !verify_bytes(kPreGetCollectibleRelayExpectedOriginal.data() +
                          kPreGetCollectibleRelayExpectedEntry.size(),
                      targetBytes.data() + kPreGetCollectibleRelayExpectedEntry.size(),
                      targetBytes.size() - kPreGetCollectibleRelayExpectedEntry.size()) ||
        !verify_bytes(kPreGetCollectibleRelayExpectedBytes.data(), relayBytes.data(), relayBytes.size())) {
        return false;
    }
    *slot = candidateSlot;
    *original = target;
    return true;
}

PreGetCollectibleRelayInstallResult PublishPreGetCollectibleRelayCallback(uintptr_t slot,
                                                                          uintptr_t callback) {
    if (slot == 0 || callback == 0 || (callback & 3u) != 0) {
        return PreGetCollectibleRelayInstallResult::RelayPublishFailed;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (rwSlot == nullptr) return PreGetCollectibleRelayInstallResult::RelayPublishFailed;
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return PreGetCollectibleRelayInstallResult::RelaySlotNotEmpty;
    }
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? PreGetCollectibleRelayInstallResult::Success
               : PreGetCollectibleRelayInstallResult::RelayPublishFailed;
}
}

PreGetCollectibleRelayInstallResult TryInstallPreGetCollectibleRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    uintptr_t original = 0;
    if (!VerifyPreGetCollectibleRelay(module, &slot, &original)) {
        return RecordPreGetCollectibleInstall(
            PreGetCollectibleRelayInstallResult::RelayPatchMismatch);
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&LuaRuntime::DispatchPreGetCollectible);
    if (callback == 0 || (callback & 3u) != 0) {
        return RecordPreGetCollectibleInstall(
            PreGetCollectibleRelayInstallResult::RelayPublishFailed);
    }
    const auto publish = PublishPreGetCollectibleRelayCallback(slot, callback);
    if (publish == PreGetCollectibleRelayInstallResult::Success) {
        LuaRuntime::SetItemPoolGetCollectibleBinding(original);
    }
    return RecordPreGetCollectibleInstall(publish);
}

#if defined(EXL_DIAGNOSTIC_STAGE) && \
    (EXL_DIAGNOSTIC_STAGE == 102 || EXL_DIAGNOSTIC_STAGE == 104 || EXL_DIAGNOSTIC_STAGE == 110 || EXL_DIAGNOSTIC_STAGE == 112)
namespace {
bool VerifyStage102ChangeRoomRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kGameChangeRoomRelayCodeOffset + kGameChangeRoomRelayExpectedBytes.size()) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kGameChangeRoomCallFileOffset);
    if (target < module.base || target % 4 != 0 ||
        !module.Contains(target, kGameChangeRoomRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kGameChangeRoomRelayExpectedEntry.size())) {
        return false;
    }
    std::array<u8, kGameChangeRoomRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kGameChangeRoomRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    const uintptr_t relay = target_address(module.base, kGameChangeRoomRelayCodeOffset);
    if (relay < module.base || relay % 4 != 0 ||
        !module.Contains(relay, kGameChangeRoomRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kGameChangeRoomRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameChangeRoomRelayExpectedBytes.size()> relayBytes{};
    std::memcpy(relayBytes.data(), reinterpret_cast<const void*>(relay), relayBytes.size());
    if (!verify_bytes(kGameChangeRoomRelayExpectedBytes.data(), relayBytes.data(), relayBytes.size())) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameChangeRoomRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}
}

#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
Stage110ChangeRoomInstallResult TryInstallStage110ChangeRoomDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage102ChangeRoomRelay(module, &slot)) return Stage110ChangeRoomInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return Stage110ChangeRoomInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage110ChangeRoom);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage110ChangeRoomInstallResult::Success : Stage110ChangeRoomInstallResult::RelayPublishFailed;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
Stage102ChangeRoomInstallResult TryInstallStage102ChangeRoomDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage102ChangeRoomRelay(module, &slot)) {
        return Stage102ChangeRoomInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return Stage102ChangeRoomInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage102ChangeRoom);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return Stage102ChangeRoomInstallResult::RelayPublishFailed;
    }
    return Stage102ChangeRoomInstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
namespace {
bool VerifyStage104ChangeRoomRelay(const TargetModule& module, uintptr_t* slot) {
    return VerifyStage102ChangeRoomRelay(module, slot);
}
}

Stage104RoomKeyInstallResult TryInstallStage104RoomKeyDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage104ChangeRoomRelay(module, &slot)) {
        return Stage104RoomKeyInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return Stage104RoomKeyInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage104ChangeRoom);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return Stage104RoomKeyInstallResult::RelayPublishFailed;
    }
    return Stage104RoomKeyInstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
Stage112DescriptorReentryInstallResult TryInstallStage112DescriptorReentryDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage102ChangeRoomRelay(module, &slot)) {
        return Stage112DescriptorReentryInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return Stage112DescriptorReentryInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage112ChangeRoom);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage112DescriptorReentryInstallResult::Success
               : Stage112DescriptorReentryInstallResult::RelayPublishFailed;
}
#endif

// ---- 游戏开局：**常驻**挂点（2026-09-14 从诊断阶段 108/110 提升而来）----------------
//
// 引擎侧的中继由 `atmosphere/nro_patches/isaac-repentance-lifecycle-relay` 的 IPS 放进代码洞：
// 它先把 `Game::Start` / `Game::StartFromSavedState` 的原 PLT 调用**跑完**，再从槽里取出我们
// 发布进去的回调调用（签名 `void(void* game, std::uint32_t eventKind)`，1 = 读档、2 = 新局），
// 最后回到调用点之后继续。所以"游戏确实已经开好局了"这件事由时机本身保证。
//
// 为什么这里与下面 Stage108 诊断那段看着重复：诊断设施已按裁定**冻结**（不再新增、不再维护），
// 所以常驻路径自带一份校验，而不是去改动那两段被冻结的代码；等诊断阶段整体清理时再合并。
namespace {

// ★ 刻意只记账、**不在这里跑 Lua**：本函数运行在引擎的开局序列里（`Game::Start` 刚返回），
// 在这个时机调用模组回调风险高（游戏对象刚建、状态还在收尾）。真正的派发交给下一帧的
// `LuaRuntime::DispatchPostUpdate()`（见 `lua_runtime.cpp` 的 `NoteGameStarted` /
// `DispatchPostGameStarted`）。代价只是"晚一帧"，换来的是不必在开局序列里运行 Lua。
void ObserveGameStart(void* game, std::uint32_t eventKind) {
    (void)game;
    if (eventKind != 1 && eventKind != 2) {
        return;
    }
    LuaRuntime::NoteGameStarted(eventKind == 1);
}

// 安装前先核对"放代码洞的那份补丁确实是我们要的那一份"：两个调用点的分支指令、两段中继代码、
// 以及槽本身的位置都必须逐字节对得上。对不上就拒绝安装 —— 宁可少一个事件，
// 也不跳进一段来路不明的代码。
bool VerifyGameStartRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kGameStartNewRelayCodeOffset + kGameStartNewRelayExpectedBytes.size()) {
        return false;
    }
    const auto verifyRelay = [&module](uintptr_t callOffset, const auto& entry,
                                       uintptr_t relayOffset, const auto& code) {
        const uintptr_t target = target_address(module.base, callOffset);
        const uintptr_t relay = target_address(module.base, relayOffset);
        if (target < module.base || target % 4 != 0 || !module.Contains(target, entry.size()) ||
            !IsMappedRxModuleCodeWindow(target, entry.size()) || relay < module.base || relay % 4 != 0 ||
            !module.Contains(relay, code.size()) || !IsMappedRxModuleCodeWindow(relay, code.size())) {
            return false;
        }
        return verify_bytes(entry.data(), reinterpret_cast<const u8*>(target), entry.size()) &&
               verify_bytes(code.data(), reinterpret_cast<const u8*>(relay), code.size());
    };
    if (!verifyRelay(kGameStartSavedCallFileOffset, kGameStartSavedRelayExpectedEntry,
                     kGameStartSavedRelayCodeOffset, kGameStartSavedRelayExpectedBytes) ||
        !verifyRelay(kGameStartNewCallFileOffset, kGameStartNewRelayExpectedEntry,
                     kGameStartNewRelayCodeOffset, kGameStartNewRelayExpectedBytes)) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameStartRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

}  // namespace

GameStartRelayInstallResult TryInstallGameStartRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyGameStartRelay(module, &slot)) {
        return GameStartRelayInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return GameStartRelayInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveGameStart);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return GameStartRelayInstallResult::RelayPublishFailed;
    }
    return GameStartRelayInstallResult::Success;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 108 || EXL_DIAGNOSTIC_STAGE == 110)
namespace {
bool VerifyStage108LifecycleRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kGameStartNewRelayCodeOffset + kGameStartNewRelayExpectedBytes.size()) {
        return false;
    }
    const auto verifyRelay = [&module](uintptr_t callOffset, const auto& entry,
                                       uintptr_t relayOffset, const auto& code) {
        const uintptr_t target = target_address(module.base, callOffset);
        const uintptr_t relay = target_address(module.base, relayOffset);
        if (target < module.base || target % 4 != 0 || !module.Contains(target, entry.size()) ||
            !IsMappedRxModuleCodeWindow(target, entry.size()) || relay < module.base || relay % 4 != 0 ||
            !module.Contains(relay, code.size()) || !IsMappedRxModuleCodeWindow(relay, code.size())) {
            return false;
        }
        return verify_bytes(entry.data(), reinterpret_cast<const u8*>(target), entry.size()) &&
               verify_bytes(code.data(), reinterpret_cast<const u8*>(relay), code.size());
    };
    if (!verifyRelay(kGameStartSavedCallFileOffset, kGameStartSavedRelayExpectedEntry,
                     kGameStartSavedRelayCodeOffset, kGameStartSavedRelayExpectedBytes) ||
        !verifyRelay(kGameStartNewCallFileOffset, kGameStartNewRelayExpectedEntry,
                     kGameStartNewRelayCodeOffset, kGameStartNewRelayExpectedBytes)) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameStartRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}
}

#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
Stage108LifecycleInstallResult TryInstallStage108LifecycleDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage108LifecycleRelay(module, &slot)) {
        return Stage108LifecycleInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return Stage108LifecycleInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage108Lifecycle);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return Stage108LifecycleInstallResult::RelayPublishFailed;
    }
    return Stage108LifecycleInstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
Stage110LifecycleInstallResult TryInstallStage110LifecycleDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage108LifecycleRelay(module, &slot)) return Stage110LifecycleInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return Stage110LifecycleInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage110Lifecycle);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage110LifecycleInstallResult::Success : Stage110LifecycleInstallResult::RelayPublishFailed;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
namespace {
bool VerifyStage109RestartRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kGameRestartRelayCodeOffsets[2] + kGameRestartRelay2ExpectedBytes.size()) {
        return false;
    }
    constexpr std::array<const std::array<u8, 4>*, 3> kEntries = {
        &kGameRestartRelay0ExpectedEntry, &kGameRestartRelay1ExpectedEntry, &kGameRestartRelay2ExpectedEntry,
    };
    constexpr std::array<const std::array<u8, 80>*, 3> kCodes = {
        &kGameRestartRelay0ExpectedBytes, &kGameRestartRelay1ExpectedBytes, &kGameRestartRelay2ExpectedBytes,
    };
    for (size_t index = 0; index < kGameRestartCallFileOffsets.size(); ++index) {
        const uintptr_t entry = target_address(module.base, kGameRestartCallFileOffsets[index]);
        const uintptr_t relay = target_address(module.base, kGameRestartRelayCodeOffsets[index]);
        if (entry < module.base || entry % 4 != 0 || !module.Contains(entry, (*kEntries[index]).size()) ||
            !IsMappedRxModuleCodeWindow(entry, (*kEntries[index]).size()) || relay < module.base ||
            relay % 4 != 0 || !module.Contains(relay, (*kCodes[index]).size()) ||
            !IsMappedRxModuleCodeWindow(relay, (*kCodes[index]).size()) ||
            !verify_bytes((*kEntries[index]).data(), reinterpret_cast<const u8*>(entry), (*kEntries[index]).size()) ||
            !verify_bytes((*kCodes[index]).data(), reinterpret_cast<const u8*>(relay), (*kCodes[index]).size())) {
            return false;
        }
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameRestartRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}
}

Stage109RestartInstallResult TryInstallStage109RestartDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage109RestartRelay(module, &slot)) return Stage109RestartInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return Stage109RestartInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage109Restart);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage109RestartInstallResult::Success
               : Stage109RestartInstallResult::RelayPublishFailed;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
namespace {
bool VerifyStage127SaveLoadRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kSaveLoadRelayLoadCodeOffset + kSaveLoadRelayLoadExpectedBytes.size()) return false;
    const uintptr_t saveEntry = target_address(module.base, kSaveLoadRelaySaveCallFileOffset);
    const uintptr_t loadEntry = target_address(module.base, kSaveLoadRelayLoadCallFileOffset);
    const uintptr_t saveRelay = target_address(module.base, kSaveLoadRelaySaveCodeOffset);
    const uintptr_t loadRelay = target_address(module.base, kSaveLoadRelayLoadCodeOffset);
    if (!IsMappedRxModuleCodeWindow(saveEntry, 4) || !IsMappedRxModuleCodeWindow(loadEntry, 4) ||
        !IsMappedRxModuleCodeWindow(saveRelay, kSaveLoadRelaySaveExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(loadRelay, kSaveLoadRelayLoadExpectedBytes.size())) return false;
    if (!verify_bytes(kSaveLoadRelaySaveExpectedEntry.data(), reinterpret_cast<const u8*>(saveEntry), 4) ||
        !verify_bytes(kSaveLoadRelayLoadExpectedEntry.data(), reinterpret_cast<const u8*>(loadEntry), 4) ||
        !verify_bytes(kSaveLoadRelaySaveExpectedBytes.data(), reinterpret_cast<const u8*>(saveRelay), 80) ||
        !verify_bytes(kSaveLoadRelayLoadExpectedBytes.data(), reinterpret_cast<const u8*>(loadRelay), 80)) return false;
    const uintptr_t candidate = target_address(module.base, kSaveLoadRelaySlotOffset);
    if ((candidate & (alignof(uintptr_t) - 1)) != 0 || !module.Contains(candidate, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidate, sizeof(uintptr_t))) return false;
    *slot = candidate;
    return true;
}
}

Stage127SaveLoadInstallResult TryInstallStage127SaveLoadDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage127SaveLoadRelay(module, &slot)) return Stage127SaveLoadInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return Stage127SaveLoadInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage127SaveLoad);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage127SaveLoadInstallResult::Success : Stage127SaveLoadInstallResult::RelayPublishFailed;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
namespace {
bool VerifyStage128SaveDataManagerRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        module.textSize < kStage128SaveDataManagerRelayLoadCodeOffset +
                              kStage128SaveDataManagerRelayLoadExpectedBytes.size()) return false;
    const uintptr_t saveEntry = target_address(module.base, kStage128SaveDataManagerRelaySaveCallFileOffset);
    const uintptr_t loadEntry = target_address(module.base, kStage128SaveDataManagerRelayLoadCallFileOffset);
    const uintptr_t saveRelay = target_address(module.base, kStage128SaveDataManagerRelaySaveCodeOffset);
    const uintptr_t loadRelay = target_address(module.base, kStage128SaveDataManagerRelayLoadCodeOffset);
    if (!IsMappedRxModuleCodeWindow(saveEntry, 4) || !IsMappedRxModuleCodeWindow(loadEntry, 4) ||
        !IsMappedRxModuleCodeWindow(saveRelay, kStage128SaveDataManagerRelaySaveExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(loadRelay, kStage128SaveDataManagerRelayLoadExpectedBytes.size())) return false;
    if (!verify_bytes(kStage128SaveDataManagerRelaySaveExpectedEntry.data(), reinterpret_cast<const u8*>(saveEntry), 4) ||
        !verify_bytes(kStage128SaveDataManagerRelayLoadExpectedEntry.data(), reinterpret_cast<const u8*>(loadEntry), 4) ||
        !verify_bytes(kStage128SaveDataManagerRelaySaveExpectedBytes.data(), reinterpret_cast<const u8*>(saveRelay), 80) ||
        !verify_bytes(kStage128SaveDataManagerRelayLoadExpectedBytes.data(), reinterpret_cast<const u8*>(loadRelay), 80)) return false;
    const uintptr_t candidate = target_address(module.base, kStage128SaveDataManagerRelaySlotOffset);
    if ((candidate & (alignof(uintptr_t) - 1)) != 0 || !module.Contains(candidate, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidate, sizeof(uintptr_t))) return false;
    *slot = candidate;
    return true;
}
}

Stage128SaveDataManagerInstallResult TryInstallStage128SaveDataManagerDiagnostic(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage128SaveDataManagerRelay(module, &slot))
        return Stage128SaveDataManagerInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0)
        return Stage128SaveDataManagerInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveStage128SaveDataManager);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback
               ? Stage128SaveDataManagerInstallResult::Success
               : Stage128SaveDataManagerInstallResult::RelayPublishFailed;
}
#endif

bool VerifyGameOwnerSlot(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.size < sizeof(uintptr_t) ||
        kGameOwnerGlobalSlotOffset > UINTPTR_MAX - module.base ||
        kGameOwnerGlobalSlotOffset > module.size - sizeof(uintptr_t)) {
        return false;
    }
    const uintptr_t candidate = module.base + kGameOwnerGlobalSlotOffset;
    if ((candidate & (alignof(uintptr_t) - 1)) != 0 ||
        candidate > UINTPTR_MAX - sizeof(uintptr_t)) return false;
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, candidate)) || info.size == 0 ||
        info.addr > UINTPTR_MAX - info.size) {
        return false;
    }
    const uintptr_t end = candidate + sizeof(uintptr_t);
    if (candidate < info.addr || end < candidate || end > info.addr + info.size ||
        (info.type & MemState_Type) != MemType_ModuleCodeMutable || info.perm != Perm_Rw) {
        return false;
    }
    *slot = candidate;
    return true;
}

bool VerifyGameIsPausedThunk(const TargetModule& module, uintptr_t* thunk) {
    if (thunk == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kGameIsPausedThunkOffset > UINTPTR_MAX - module.base ||
        kGameIsPausedThunkExpectedBytes.size() > module.textSize ||
        kGameIsPausedThunkOffset > module.textSize - kGameIsPausedThunkExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kGameIsPausedThunkOffset;
    if ((candidate & 3) != 0 ||
        !module.Contains(candidate, kGameIsPausedThunkExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kGameIsPausedThunkExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameIsPausedThunkExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kGameIsPausedThunkExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *thunk = candidate;
    return true;
}

bool VerifyGameIsGreedMode(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kGameIsGreedModeOffset > UINTPTR_MAX - module.base ||
        kGameIsGreedModeExpectedBytes.size() > module.textSize ||
        kGameIsGreedModeOffset > module.textSize - kGameIsGreedModeExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kGameIsGreedModeOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kGameIsGreedModeExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kGameIsGreedModeExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameIsGreedModeExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kGameIsGreedModeExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyLevelIsAscent(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kLevelIsAscentOffset > UINTPTR_MAX - module.base ||
        kLevelIsAscentExpectedBytes.size() > module.textSize ||
        kLevelIsAscentOffset > module.textSize - kLevelIsAscentExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kLevelIsAscentOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kLevelIsAscentExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kLevelIsAscentExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kLevelIsAscentExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kLevelIsAscentExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

// 批次 8（2026-09-15）：`Level:GetAbsoluteStage()` / `Level:IsNextStageAvailable()` 的入口守卫。
// 形状与 `VerifyLevelIsAscent` 完全相同（`const` 成员、只吃 `this`），所以三份逻辑逐句对应：
// 偏移不越界、入口 4 字节对齐、落在模块代码段且映射为 Rx、再逐字节比对 16 字节守卫。
// 任一项不符就只留绑定为 0（handler 会报"绑定不可用"），不影响其它挂点安装。
bool VerifyGetRenderPositionStub(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kGetRenderPositionStubOffset > UINTPTR_MAX - module.base ||
        kGetRenderPositionStubExpectedBytes.size() > module.textSize ||
        kGetRenderPositionStubOffset >
            module.textSize - kGetRenderPositionStubExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kGetRenderPositionStubOffset;
    if ((candidate & 3) != 0 ||
        !module.Contains(candidate, kGetRenderPositionStubExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kGetRenderPositionStubExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGetRenderPositionStubExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kGetRenderPositionStubExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyLevelGetAbsoluteStage(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kLevelGetAbsoluteStageOffset > UINTPTR_MAX - module.base ||
        kLevelGetAbsoluteStageExpectedBytes.size() > module.textSize ||
        kLevelGetAbsoluteStageOffset > module.textSize - kLevelGetAbsoluteStageExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kLevelGetAbsoluteStageOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kLevelGetAbsoluteStageExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kLevelGetAbsoluteStageExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kLevelGetAbsoluteStageExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kLevelGetAbsoluteStageExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

// 2026-09-16：`Room:GetFrameCount()` 的入口（`Room::GetFrameCount @ 0x470B0C`）。
// 与上面几条同形：**安装期必须核对入口 16 字节**；不符就只留 0 ⇒ handler 报"绑定不可用"，
// 不会去调一个"地址恰好落在模块里"的东西，也不会编造帧数。
bool VerifyRoomGetFrameCount(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kRoomGetFrameCountOffset > UINTPTR_MAX - module.base ||
        kRoomGetFrameCountExpectedBytes.size() > module.textSize ||
        kRoomGetFrameCountOffset > module.textSize - kRoomGetFrameCountExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kRoomGetFrameCountOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kRoomGetFrameCountExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kRoomGetFrameCountExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kRoomGetFrameCountExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kRoomGetFrameCountExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyLevelIsNextStageAvailable(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kLevelIsNextStageAvailableOffset > UINTPTR_MAX - module.base ||
        kLevelIsNextStageAvailableExpectedBytes.size() > module.textSize ||
        kLevelIsNextStageAvailableOffset > module.textSize - kLevelIsNextStageAvailableExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kLevelIsNextStageAvailableOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kLevelIsNextStageAvailableExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kLevelIsNextStageAvailableExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kLevelIsNextStageAvailableExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kLevelIsNextStageAvailableExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyManagerIsActionTriggered(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kManagerIsActionTriggeredOffset > UINTPTR_MAX - module.base ||
        kManagerIsActionTriggeredExpectedBytes.size() > module.textSize ||
        kManagerIsActionTriggeredOffset > module.textSize - kManagerIsActionTriggeredExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kManagerIsActionTriggeredOffset;
    if ((candidate & 3) != 0 || !module.Contains(candidate, kManagerIsActionTriggeredExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kManagerIsActionTriggeredExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerIsActionTriggeredExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kManagerIsActionTriggeredExpectedBytes.data(), bytes.data(), bytes.size())) return false;
    *method = candidate;
    return true;
}

bool VerifyManagerIsActionPressed(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kManagerIsActionPressedOffset > UINTPTR_MAX - module.base ||
        kManagerIsActionPressedExpectedBytes.size() > module.textSize ||
        kManagerIsActionPressedOffset > module.textSize - kManagerIsActionPressedExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kManagerIsActionPressedOffset;
    if ((candidate & 3) != 0 ||
        !module.Contains(candidate, kManagerIsActionPressedExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kManagerIsActionPressedExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerIsActionPressedExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kManagerIsActionPressedExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyManagerGetActionValue(const TargetModule& module, uintptr_t* method) {
    if (method == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kManagerGetActionValueOffset > UINTPTR_MAX - module.base ||
        kManagerGetActionValueExpectedBytes.size() > module.textSize ||
        kManagerGetActionValueOffset > module.textSize - kManagerGetActionValueExpectedBytes.size()) {
        return false;
    }
    const uintptr_t candidate = module.base + kManagerGetActionValueOffset;
    if ((candidate & 3) != 0 ||
        !module.Contains(candidate, kManagerGetActionValueExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, kManagerGetActionValueExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerGetActionValueExpectedBytes.size()> bytes{};
    std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
    if (!verify_bytes(kManagerGetActionValueExpectedBytes.data(), bytes.data(), bytes.size())) {
        return false;
    }
    *method = candidate;
    return true;
}

bool VerifyMusicBindings(const TargetModule& module, uintptr_t* getCurrentMusicId,
                         uintptr_t* pause, uintptr_t* resume) {
    if (getCurrentMusicId == nullptr || pause == nullptr || resume == nullptr || module.base == 0 ||
        module.buildId != kTargetBuildId) {
        return false;
    }
    const auto verify = [&module](uintptr_t offset, const u8* expected, std::size_t length,
                                  uintptr_t* output) {
        if (offset > UINTPTR_MAX - module.base || length > module.textSize ||
            offset > module.textSize - length) {
            return false;
        }
        const uintptr_t candidate = module.base + offset;
        if ((candidate & 3) != 0 || !module.Contains(candidate, length) ||
            !IsMappedRxModuleCodeWindow(candidate, length)) {
            return false;
        }
        std::array<u8, 16> bytes{};
        std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), length);
        if (!verify_bytes(expected, bytes.data(), length)) {
            return false;
        }
        *output = candidate;
        return true;
    };
    return verify(kMusicGetCurrentMusicIdOffset, kMusicGetCurrentMusicIdExpectedBytes.data(),
                  kMusicGetCurrentMusicIdExpectedBytes.size(), getCurrentMusicId) &&
           verify(kMusicPauseOffset, kMusicPauseExpectedBytes.data(), kMusicPauseExpectedBytes.size(), pause) &&
           verify(kMusicResumeOffset, kMusicResumeExpectedBytes.data(), kMusicResumeExpectedBytes.size(), resume);
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif
bool VerifyRngBindings(const TargetModule& module, uintptr_t* setSeed, uintptr_t* next) {
    if (setSeed == nullptr || next == nullptr || module.base == 0 ||
        module.buildId != kTargetBuildId) {
        return false;
    }
    const auto verify = [&module](uintptr_t offset, const u8* expected, std::size_t length,
                                  uintptr_t* output) {
        if (offset > UINTPTR_MAX - module.base || length > module.textSize ||
            offset > module.textSize - length) {
            return false;
        }
        const uintptr_t candidate = module.base + offset;
        if ((candidate & 3) != 0 || !module.Contains(candidate, length) ||
            !IsMappedRxModuleCodeWindow(candidate, length)) {
            return false;
        }
        std::array<u8, 16> bytes{};
        std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), length);
        if (!verify_bytes(expected, bytes.data(), length)) {
            return false;
        }
        *output = candidate;
        return true;
    };
    return verify(kRngSetSeedOffset, kRngSetSeedExpectedBytes.data(),
                  kRngSetSeedExpectedBytes.size(), setSeed) &&
           verify(kRngNextOffset, kRngNextExpectedBytes.data(),
                  kRngNextExpectedBytes.size(), next);
}

bool TryInstallRngBindings(const TargetModule& module) {
    uintptr_t setSeed = 0;
    uintptr_t next = 0;
    if (!VerifyRngBindings(module, &setSeed, &next)) {
        return false;
    }
    LuaRuntime::SetRngBindings(setSeed, next);
    return true;
}

// One row per `KAGE::Graphics::Font` entry point. All eleven guards are the same 16-byte window,
// so a single loop checks them all, and the table doubles as the list of which field of
// `LuaFontBindings` each guard fills.
struct BindingGuardEntry {
    uintptr_t offset;
    std::size_t memberOffset;
    const std::array<u8, 16>* expected;
};

constexpr std::array<BindingGuardEntry, 15> kFontBindingEntries{{
    {kFontCtorOffset, offsetof(LuaRuntime::LuaFontBindings, ctor), &kFontCtorExpectedBytes},
    {kFontDestructorOffset, offsetof(LuaRuntime::LuaFontBindings, destructor_),
     &kFontDestructorExpectedBytes},
    {kFontLoadOffset, offsetof(LuaRuntime::LuaFontBindings, load), &kFontLoadExpectedBytes},
    {kFontUnloadOffset, offsetof(LuaRuntime::LuaFontBindings, unload), &kFontUnloadExpectedBytes},
    {kFontIsLoadedOffset, offsetof(LuaRuntime::LuaFontBindings, isLoaded),
     &kFontIsLoadedExpectedBytes},
    {kFontGetStringWidthOffset, offsetof(LuaRuntime::LuaFontBindings, getStringWidth),
     &kFontGetStringWidthExpectedBytes},
    {kFontGetStringWidthUTF8Offset, offsetof(LuaRuntime::LuaFontBindings, getStringWidthUTF8),
     &kFontGetStringWidthUTF8ExpectedBytes},
    {kFontGetLineHeightOffset, offsetof(LuaRuntime::LuaFontBindings, getLineHeight),
     &kFontGetLineHeightExpectedBytes},
    {kFontGetBaselineHeightOffset, offsetof(LuaRuntime::LuaFontBindings, getBaselineHeight),
     &kFontGetBaselineHeightExpectedBytes},
    {kFontGetCharacterWidthOffset, offsetof(LuaRuntime::LuaFontBindings, getCharacterWidth),
     &kFontGetCharacterWidthExpectedBytes},
    {kFontSetMissingCharacterOffset, offsetof(LuaRuntime::LuaFontBindings, setMissingCharacter),
     &kFontSetMissingCharacterExpectedBytes},
    {kFontDrawStringOffset, offsetof(LuaRuntime::LuaFontBindings, drawString),
     &kFontDrawStringExpectedBytes},
    // 三个绘制变体。**每一行都必须在这里**：漏掉的字段会保持 0，handler 于是抛
    // "native binding is unavailable"，而 host 测试直接发布绑定、看不到这个缺口 ——
    // 2026-09-13 第八轮的真机现象就是"只画出了普通 DrawString 的几行，缩放那行起全没了"
    // （报告 `01789144065` 掩码：MARK=6、位 61=0，即 Mod 自己 pcall 到了这个错误）。
    {kFontDrawStringScaledOffset, offsetof(LuaRuntime::LuaFontBindings, drawStringScaled),
     &kFontDrawStringScaledExpectedBytes},
    {kFontDrawStringUTF8Offset, offsetof(LuaRuntime::LuaFontBindings, drawStringUTF8),
     &kFontDrawStringUTF8ExpectedBytes},
    {kFontDrawStringScaledUTF8Offset,
     offsetof(LuaRuntime::LuaFontBindings, drawStringScaledUTF8),
     &kFontDrawStringScaledUTF8ExpectedBytes},
}};

// Font bindings are optional, exactly like the controller-input ones: they must never block hook
// installation, and every entry point is judged on its own. A row whose guard does not match
// leaves its field at zero, which the `Font` handlers turn into an unavailable-binding Lua error;
// the other rows are still verified, so a single moved function does not disable the whole family.
// 同一个校验循环服务所有"原生入口表"家族：逐行判守卫、命中就写进对应字段，未命中保持 0。
// 每个家族各有一张表；表里少一行 = 那个字段永远为 0（2026-09-13 的 Font 事故），所以
// `runtime/tests/` 里有一条测试逐家族比对"结构体字段集合 == 守卫表成员集合"。
template <typename Bindings, std::size_t Count>
bool VerifyBindingTable(const TargetModule& module,
                        const std::array<BindingGuardEntry, Count>& entries, Bindings* bindings) {
    if (bindings == nullptr || module.base == 0 || module.buildId != kTargetBuildId) {
        return false;
    }
    auto* const base = reinterpret_cast<u8*>(bindings);
    bool verified = true;
    for (const BindingGuardEntry& entry : entries) {
        const std::size_t length = entry.expected->size();
        auto* output = reinterpret_cast<uintptr_t*>(base + entry.memberOffset);
        *output = 0;
        if (entry.offset > UINTPTR_MAX - module.base || length > module.textSize ||
            entry.offset > module.textSize - length) {
            verified = false;
            continue;
        }
        const uintptr_t candidate = module.base + entry.offset;
        if ((candidate & 3) != 0 || !module.Contains(candidate, length) ||
            !IsMappedRxModuleCodeWindow(candidate, length)) {
            verified = false;
            continue;
        }
        std::array<u8, 16> bytes{};
        std::memcpy(bytes.data(), reinterpret_cast<const void*>(candidate), bytes.size());
        if (!verify_bytes(entry.expected->data(), bytes.data(), bytes.size())) {
            verified = false;
            continue;
        }
        *output = candidate;
    }
    return verified;
}

bool VerifyFontBindings(const TargetModule& module, LuaRuntime::LuaFontBindings* bindings) {
    return VerifyBindingTable(module, kFontBindingEntries, bindings);
}

// `Sprite`（`IsaacRepentance::ANM2`）第一步：只收 `char const*`/数值的入口。尺寸 `0x158` 由
// `tools/stage155_anm2_size_audit.py` 两种独立方法定案；`Load`/`ReplaceSpritesheet` 需要
// libc++ 的 `std::string` 桥，放在第二步。
constexpr std::array<BindingGuardEntry, 21> kSpriteBindingEntries{{
    {kSpriteCtorOffset, offsetof(LuaRuntime::LuaSpriteBindings, ctor), &kSpriteCtorExpectedBytes},
    {kSpriteDestructorOffset, offsetof(LuaRuntime::LuaSpriteBindings, destructor_),
     &kSpriteDestructorExpectedBytes},
    {kSpritePlayOffset, offsetof(LuaRuntime::LuaSpriteBindings, play), &kSpritePlayExpectedBytes},
    {kSpriteSetAnimationOffset, offsetof(LuaRuntime::LuaSpriteBindings, setAnimation),
     &kSpriteSetAnimationExpectedBytes},
    {kSpriteSetFrameNamedOffset, offsetof(LuaRuntime::LuaSpriteBindings, setFrameNamed),
     &kSpriteSetFrameNamedExpectedBytes},
    {kSpriteSetFrameOffset, offsetof(LuaRuntime::LuaSpriteBindings, setFrame),
     &kSpriteSetFrameExpectedBytes},
    {kSpriteGetFrameOffset, offsetof(LuaRuntime::LuaSpriteBindings, getFrame),
     &kSpriteGetFrameExpectedBytes},
    {kSpriteSetLayerFrameOffset, offsetof(LuaRuntime::LuaSpriteBindings, setLayerFrame),
     &kSpriteSetLayerFrameExpectedBytes},
    {kSpriteGetLayerFrameOffset, offsetof(LuaRuntime::LuaSpriteBindings, getLayerFrame),
     &kSpriteGetLayerFrameExpectedBytes},
    // `GetTexel`：EID 判"赎罪线问号底座"的依据（见 `runtime_constants.hpp` 该条的说明）。
    {kSpriteGetTexelOffset, offsetof(LuaRuntime::LuaSpriteBindings, getTexel),
     &kSpriteGetTexelExpectedBytes},
    {kSpriteUpdateOffset, offsetof(LuaRuntime::LuaSpriteBindings, update), &kSpriteUpdateExpectedBytes},
    {kSpriteIsPlayingOffset, offsetof(LuaRuntime::LuaSpriteBindings, isPlaying),
     &kSpriteIsPlayingExpectedBytes},
    {kSpriteIsFinishedOffset, offsetof(LuaRuntime::LuaSpriteBindings, isFinished),
     &kSpriteIsFinishedExpectedBytes},
    {kSpriteRenderOffset, offsetof(LuaRuntime::LuaSpriteBindings, render), &kSpriteRenderExpectedBytes},
    {kSpriteRenderLayerOffset, offsetof(LuaRuntime::LuaSpriteBindings, renderLayer),
     &kSpriteRenderLayerExpectedBytes},
    {kSpritePlayRandomOffset, offsetof(LuaRuntime::LuaSpriteBindings, playRandom),
     &kSpritePlayRandomExpectedBytes},
    // 第二步：需要 libc++ `std::string` 的三个入口，以及借来代建字符串的 `assign` 桩。
    {kSpriteLoadOffset, offsetof(LuaRuntime::LuaSpriteBindings, load), &kSpriteLoadExpectedBytes},
    {kSpriteLoadGraphicsOffset, offsetof(LuaRuntime::LuaSpriteBindings, loadGraphics),
     &kSpriteLoadGraphicsExpectedBytes},
    {kSpriteReplaceSpritesheetOffset, offsetof(LuaRuntime::LuaSpriteBindings, replaceSpritesheet),
     &kSpriteReplaceSpritesheetExpectedBytes},
    {kLibcxxStringAssignOffset, offsetof(LuaRuntime::LuaSpriteBindings, libcxxStringAssign),
     &kLibcxxStringAssignExpectedBytes},
    // 长路径（超过 SSO）的缓冲要用游戏自己的 `operator delete` 释放，不能借本模块的 `free`。
    {kGameOperatorDeleteOffset, offsetof(LuaRuntime::LuaSpriteBindings, gameOperatorDelete),
     &kGameOperatorDeleteExpectedBytes},
}};

bool VerifySpriteBindings(const TargetModule& module, LuaRuntime::LuaSpriteBindings* bindings) {
    return VerifyBindingTable(module, kSpriteBindingEntries, bindings);
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif

// Shared relay plumbing for both the Stage48 diagnostics and the production rebuild
// relay: these two helpers must stay outside every `#if`, otherwise a build that does
// not enable the diagnostic loses the definition while a caller keeps the reference
// (hardware caught that as `PC=0` from an unresolved PLT slot).
namespace {
bool VerifyStage48Relay(const TargetModule& module, uintptr_t targetOffset,
                        const std::array<u8, 16>& original,
                        const std::array<u8, 4>& expectedEntry,
                        uintptr_t relayOffset, const u8* expectedRelay,
                        std::size_t relayLength, uintptr_t slotOffset,
                        uintptr_t* slot) {
    if (slot == nullptr || expectedRelay == nullptr || module.base == 0 ||
        module.buildId != kTargetBuildId || original.size() > module.textSize ||
        relayLength > module.textSize || sizeof(uintptr_t) > module.textSize ||
        targetOffset > UINTPTR_MAX - module.base ||
        relayOffset > UINTPTR_MAX - module.base ||
        slotOffset > UINTPTR_MAX - module.base ||
        targetOffset > module.textSize - original.size() ||
        relayOffset > module.textSize - relayLength ||
        slotOffset > module.textSize - sizeof(uintptr_t)) {
        return false;
    }
    const uintptr_t target = module.base + targetOffset;
    const uintptr_t relay = module.base + relayOffset;
    const uintptr_t candidateSlot = module.base + slotOffset;
    if ((target & 3u) != 0 || (relay & 3u) != 0 ||
        (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(target, original.size()) || !module.Contains(relay, relayLength) ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(target, original.size()) ||
        !IsMappedRxModuleCodeWindow(relay, relayLength) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    std::array<u8, 16> targetBytes{};
    std::memcpy(targetBytes.data(), reinterpret_cast<const void*>(target), targetBytes.size());
    if (!verify_bytes(expectedEntry.data(), targetBytes.data(), expectedEntry.size()) ||
        !verify_bytes(original.data() + expectedEntry.size(),
                      targetBytes.data() + expectedEntry.size(),
                      original.size() - expectedEntry.size())) {
        return false;
    }
    std::array<u8, 64> relayBytes{};
    if (relayLength > relayBytes.size()) return false;
    std::memcpy(relayBytes.data(), reinterpret_cast<const void*>(relay), relayLength);
    if (!verify_bytes(expectedRelay, relayBytes.data(), relayLength)) return false;
    *slot = candidateSlot;
    return true;
}

bool PublishStage48RelayCallback(uintptr_t slot, uintptr_t callback) {
    if (slot == 0 || callback == 0 || (callback & 3u) != 0) return false;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (rwSlot == nullptr || __atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return false;
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    return __atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == callback;
}

}  // namespace

// Stage 148 rebuild relay.
//
// 它由 `ExlaunchHookAdapter`（`HookId::RebuildMountPoints`）调用，不再是 Lua 层回调；
// 属于内容挂载子系统，装不上只意味着重建挂载点表后 Mod 的挂载点不恢复，不得让 Mod 加载失败。
// 与其它中继一样逐步校验（入口分支、代码洞字节、回调槽为空）之后才发布回调地址。
//
// This definition must stay *outside* every `#if` block: it is called from the port
// adapter (`ExlaunchHookAdapter`), which every non-diagnostic build compiles together with
// `TryInstallDefaultManifestMod`, and a definition that a conditional removes leaves the
// module with an undefined symbol whose PLT slot stays zero -- hardware caught exactly
// that as `PC=0` inside `ModuleWorker`.
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_RebuildContentMountPointsRelay();

// 新后端（入口中继）下的"取道具前"挂点。回调体与旧后端**共用同一个派发器**
// （`LuaRuntime::DispatchPreGetCollectible`），差别只在"没人覆盖时谁来跑原函数"：
//   * 旧后端的 IPS 中继桩自己判 —— 回调返回值的高 32 位为 0 就跳回原函数；
//   * 入口中继的桩不做任何判断（尾调用进回调），所以**回调自己判**：高 32 位非 0 ⇒ Mod 覆盖了
//     返回值，直接以低 32 位返回；否则调回退入口执行原函数。
// 目标 ABI 是 `ItemPool::GetCollectible(this, poolType, seed, noDecrease, defaultItem)` —— 第 5 个参数
// 真的存在（Lua 侧的 `ItemPool:GetCollectible` 就是按 5 个实参调它、旧 IPS 桩也原样透传 x4），
// 所以这里必须**原样收、原样交给原函数**，少传一个就会让原函数读到垃圾。
extern "C" std::uint32_t EntryRelayPreGetCollectibleCallback(void* itemPool, std::uint32_t itemPoolType,
                                                             std::uint32_t seed,
                                                             std::uint32_t noDecrease,
                                                             std::uint32_t defaultItem) {
    const std::uint64_t dispatched = LuaRuntime::DispatchPreGetCollectible(
        itemPool, itemPoolType, seed, noDecrease, defaultItem);
    if ((dispatched >> 32) != 0) {
        return static_cast<std::uint32_t>(dispatched);
    }
    const auto original = isaac::runtime::Original<std::uint32_t (*)(
        void*, std::uint32_t, std::uint32_t, std::uint32_t, std::uint32_t)>(
        isaac::runtime::HookId::PreGetCollectible);
    return original(itemPool, itemPoolType, seed, noDecrease, defaultItem);
}

// 新后端（入口中继）下的挂载点重建挂点。语义与旧后端一致：**先跑原函数，再跑我们的重挂载**。
// 旧后端把中继桩放在函数体内唯一的 `ret` 上，天然就是"返回前"；入口挂法得自己按这个顺序调。
// 函数无参数（`_ZN15IsaacRepentance25RebuildContentMountPointsEv`）；入口桩是尾调用，
// 本回调即以它的身份运行，`ret` 直接回到游戏的调用方（含尾调用点）。
extern "C" void EntryRelayRebuildMountPointsCallback() {
    const auto original =
        isaac::runtime::Original<void (*)()>(isaac::runtime::HookId::RebuildMountPoints);
    original();
    IsaacModRuntime_RebuildContentMountPointsRelay();
}

bool TryInstallRebuildMountPointsRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyStage48Relay(module, kRebuildMountPointsRelayOffset,
                            kRebuildMountPointsRelayExpectedOriginal,
                            kRebuildMountPointsRelayExpectedEntry,
                            kRebuildMountPointsRelayCodeOffset,
                            kRebuildMountPointsRelayExpectedBytes.data(),
                            kRebuildMountPointsRelayExpectedBytes.size(),
                            kRebuildMountPointsRelaySlotOffset, &slot)) {
        return false;
    }
    return PublishStage48RelayCallback(
        slot, reinterpret_cast<uintptr_t>(&IsaacModRuntime_RebuildContentMountPointsRelay));
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48

Stage48MusicReplayProbeInstallResult TryInstallStage48MusicReplayProbe(const TargetModule& module) {
    uintptr_t musicSlot = 0;
    uintptr_t actorPlaySlot = 0;
    uintptr_t actorPauseSlot = 0;
    if (!VerifyStage48Relay(module, kStage48MusicPlayOffset, kStage48MusicPlayExpectedBytes,
                            kStage48MusicPlayRelayExpectedEntry, kStage48MusicPlayRelayCodeOffset,
                            kStage48MusicPlayRelayExpectedBytes.data(), kStage48MusicPlayRelayExpectedBytes.size(),
                            kStage48MusicPlayRelaySlotOffset, &musicSlot))
        return Stage48MusicReplayProbeInstallResult::TargetMismatchMusicPlay;
    if (!VerifyStage48Relay(module, kStage48SoundActorPlayOffset, kStage48SoundActorPlayExpectedBytes,
                            kStage48SoundActorPlayRelayExpectedEntry, kStage48SoundActorPlayRelayCodeOffset,
                            kStage48SoundActorPlayRelayExpectedBytes.data(), kStage48SoundActorPlayRelayExpectedBytes.size(),
                            kStage48SoundActorPlayRelaySlotOffset, &actorPlaySlot))
        return Stage48MusicReplayProbeInstallResult::TargetMismatchSoundActorPlay;
    if (!VerifyStage48Relay(module, kStage48SoundActorPauseOffset, kStage48SoundActorPauseExpectedBytes,
                            kStage48SoundActorPauseRelayExpectedEntry, kStage48SoundActorPauseRelayCodeOffset,
                            kStage48SoundActorPauseRelayExpectedBytes.data(), kStage48SoundActorPauseRelayExpectedBytes.size(),
                            kStage48SoundActorPauseRelaySlotOffset, &actorPauseSlot))
        return Stage48MusicReplayProbeInstallResult::TargetMismatchSoundActorPause;
    if (!PublishStage48RelayCallback(musicSlot, reinterpret_cast<uintptr_t>(&MusicReplayMusicPlayHook::Callback)))
        return Stage48MusicReplayProbeInstallResult::RelayPublishMusicPlayFailed;
    if (!PublishStage48RelayCallback(actorPlaySlot, reinterpret_cast<uintptr_t>(&MusicReplaySoundActorPlayHook::Callback)))
        return Stage48MusicReplayProbeInstallResult::RelayPublishSoundActorPlayFailed;
    if (!PublishStage48RelayCallback(actorPauseSlot, reinterpret_cast<uintptr_t>(&MusicReplaySoundActorPauseHook::Callback)))
        return Stage48MusicReplayProbeInstallResult::RelayPublishSoundActorPauseFailed;
    return Stage48MusicReplayProbeInstallResult::Success;
}

void Stage48ArmMusicReplayProbe() {
    for (auto& slot : g_Stage48PausedActors) slot.store(0, std::memory_order_release);
    g_Stage48Frames.store(0, std::memory_order_release);
    g_Stage48PauseCount.store(0, std::memory_order_release);
    g_Stage48MusicPlayCount.store(0, std::memory_order_release);
    g_Stage48ActorPlayCount.store(0, std::memory_order_release);
    g_Stage48Flags.store(0, std::memory_order_release);
    g_Stage48Reported.store(false, std::memory_order_release);
    g_Stage48Armed.store(true, std::memory_order_release);
}

void Stage48AdvanceMusicReplayProbe() {
    if (g_Stage48Armed.load(std::memory_order_acquire)) {
        g_Stage48Frames.fetch_add(1, std::memory_order_relaxed);
    }
}

bool Stage48TakeMusicReplayReport(u32* payload) {
    if (payload == nullptr || !g_Stage48Armed.load(std::memory_order_acquire) ||
        g_Stage48Frames.load(std::memory_order_acquire) < kStage48WindowFrames) {
        return false;
    }
    u32 expected = 0;
    if (!g_Stage48Reported.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) return false;
    const u32 flags = g_Stage48Flags.load(std::memory_order_acquire);
    const u32 pauseCount = g_Stage48PauseCount.load(std::memory_order_acquire);
    const u32 musicPlayCount = g_Stage48MusicPlayCount.load(std::memory_order_acquire);
    const u32 actorPlayCount = g_Stage48ActorPlayCount.load(std::memory_order_acquire);
    *payload = flags | (std::min(pauseCount, 0xffu) << 8) |
               (std::min(musicPlayCount, 0xffu) << 16) | (std::min(actorPlayCount, 0xffu) << 24);
    g_Stage48Armed.store(false, std::memory_order_release);
    return true;
}
#endif

bool VerifyGameObserverRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 ||
        module.textSize < kGameObserverRelayCodeOffset + kGameObserverRelayExpectedBytes.size()) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kGameInitCallFileOffset);
    if (target < module.base || target % 4 != 0 ||
        !module.Contains(target, kGameObserverRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kGameObserverRelayExpectedEntry.size())) {
        return false;
    }
    const uintptr_t context = target - kGameInitContextExpectedBytes.size();
    if (context < module.base || !module.Contains(context, kGameInitContextExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(context, kGameInitContextExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameInitContextExpectedBytes.size()> contextBytes{};
    std::memcpy(contextBytes.data(), reinterpret_cast<const void*>(context), contextBytes.size());
    if (!verify_bytes(kGameInitContextExpectedBytes.data(), contextBytes.data(), contextBytes.size())) {
        return false;
    }
    std::array<u8, kGameObserverRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kGameObserverRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    const uintptr_t relay = target_address(module.base, kGameObserverRelayCodeOffset);
    if (relay < module.base || relay % 4 != 0 ||
        !module.Contains(relay, kGameObserverRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kGameObserverRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameObserverRelayExpectedBytes.size()> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kGameObserverRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameObserverRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

GameObserverInstallResult TryInstallGameObserverRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyGameObserverRelay(module, &slot)) {
        return GameObserverInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return GameObserverInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveGameFrame);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return GameObserverInstallResult::RelayPublishFailed;
    }
    return GameObserverInstallResult::Success;
}

bool VerifyGameUpdateObserverRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 ||
        module.textSize < kGameUpdateObserverRelayCodeOffset + kGameUpdateObserverRelayExpectedBytes.size()) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kGameUpdateCallFileOffset);
    if (target < module.base || target % 4 != 0 ||
        !module.Contains(target, kGameUpdateObserverRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kGameUpdateObserverRelayExpectedEntry.size())) {
        return false;
    }
    const uintptr_t context = target - kGameUpdateContextExpectedBytes.size();
    if (context < module.base || !module.Contains(context, kGameUpdateContextExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(context, kGameUpdateContextExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameUpdateContextExpectedBytes.size()> contextBytes{};
    std::memcpy(contextBytes.data(), reinterpret_cast<const void*>(context), contextBytes.size());
    if (!verify_bytes(kGameUpdateContextExpectedBytes.data(), contextBytes.data(), contextBytes.size())) {
        return false;
    }
    std::array<u8, kGameUpdateObserverRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kGameUpdateObserverRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    const uintptr_t relay = target_address(module.base, kGameUpdateObserverRelayCodeOffset);
    if (relay < module.base || relay % 4 != 0 ||
        !module.Contains(relay, kGameUpdateObserverRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kGameUpdateObserverRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameUpdateObserverRelayExpectedBytes.size()> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kGameUpdateObserverRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameUpdateObserverRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

GameUpdateObserverInstallResult TryInstallGameUpdateObserverRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyGameUpdateObserverRelay(module, &slot)) {
        return GameUpdateObserverInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return GameUpdateObserverInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveGameUpdateFrame);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return GameUpdateObserverInstallResult::RelayPublishFailed;
    }
    return GameUpdateObserverInstallResult::Success;
}

bool VerifyGameState2ObserverRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 ||
        module.textSize < kGameState2ObserverRelayCodeOffset + kGameState2ObserverRelayExpectedBytes.size()) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kGameState2CallFileOffset);
    if (target < module.base || target % 4 != 0 ||
        !module.Contains(target, kGameState2ObserverRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kGameState2ObserverRelayExpectedEntry.size())) {
        return false;
    }
    const uintptr_t context = target - kGameState2ContextExpectedBytes.size();
    if (context < module.base || !module.Contains(context, kGameState2ContextExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(context, kGameState2ContextExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameState2ContextExpectedBytes.size()> contextBytes{};
    std::memcpy(contextBytes.data(), reinterpret_cast<const void*>(context), contextBytes.size());
    if (!verify_bytes(kGameState2ContextExpectedBytes.data(), contextBytes.data(), contextBytes.size())) {
        return false;
    }
    std::array<u8, kGameState2ObserverRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kGameState2ObserverRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    const uintptr_t relay = target_address(module.base, kGameState2ObserverRelayCodeOffset);
    if (relay < module.base || relay % 4 != 0 ||
        !module.Contains(relay, kGameState2ObserverRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kGameState2ObserverRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kGameState2ObserverRelayExpectedBytes.size()> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kGameState2ObserverRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }
    const uintptr_t candidateSlot = target_address(module.base, kGameState2ObserverRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}

GameState2ObserverInstallResult TryInstallGameState2ObserverRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyGameState2ObserverRelay(module, &slot)) {
        return GameState2ObserverInstallResult::RelayPatchMismatch;
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return GameState2ObserverInstallResult::RelaySlotNotEmpty;
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveGameState2Frame);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return GameState2ObserverInstallResult::RelayPublishFailed;
    }
    return GameState2ObserverInstallResult::Success;
}

bool VerifyGameRenderObserverRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.textSize < kGameIsPausedRenderObserverRelayCodeOffset + kGameIsPausedRenderObserverRelayExpectedBytes.size()) return false;
    const uintptr_t target = target_address(module.base, kGameIsPausedRenderCallFileOffset);
    const uintptr_t context = target - kGameIsPausedRenderContextExpectedBytes.size();
    if (target < module.base || context < module.base || !module.Contains(target, kGameIsPausedRenderObserverRelayExpectedEntry.size()) || !module.Contains(context, kGameIsPausedRenderContextExpectedBytes.size()) || !IsMappedRxModuleCodeWindow(target, 4) || !IsMappedRxModuleCodeWindow(context, kGameIsPausedRenderContextExpectedBytes.size())) return false;
    std::array<u8, 12> contextBytes{}; std::array<u8, 4> entry{};
    std::memcpy(contextBytes.data(), reinterpret_cast<const void*>(context), contextBytes.size());
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kGameIsPausedRenderContextExpectedBytes.data(), contextBytes.data(), contextBytes.size()) || !verify_bytes(kGameIsPausedRenderObserverRelayExpectedEntry.data(), entry.data(), entry.size())) return false;
    const uintptr_t relay = target_address(module.base, kGameIsPausedRenderObserverRelayCodeOffset);
    if (relay < module.base || !module.Contains(relay, kGameIsPausedRenderObserverRelayExpectedBytes.size()) || !IsMappedRxModuleCodeWindow(relay, kGameIsPausedRenderObserverRelayExpectedBytes.size())) return false;
    std::array<u8, 64> relayBytes{}; std::memcpy(relayBytes.data(), reinterpret_cast<const void*>(relay), relayBytes.size());
    if (!verify_bytes(kGameIsPausedRenderObserverRelayExpectedBytes.data(), relayBytes.data(), relayBytes.size())) return false;
    const uintptr_t candidateSlot = target_address(module.base, kGameIsPausedRenderObserverRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & 7) != 0 || !module.Contains(candidateSlot, sizeof(uintptr_t)) || !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) return false;
    *slot = candidateSlot;
    return true;
}

GameRenderObserverInstallResult TryInstallGameRenderObserverRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyGameRenderObserverRelay(module, &slot)) return GameRenderObserverInstallResult::RelayPatchMismatch;
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t)); auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) return GameRenderObserverInstallResult::RelaySlotNotEmpty;
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ObserveGameRenderFrame);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE); slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) return GameRenderObserverInstallResult::RelayPublishFailed;
    return GameRenderObserverInstallResult::Success;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 8 || EXL_DIAGNOSTIC_STAGE == 9)
bool VerifyManagerLoadConfigs(const TargetModule& module, uintptr_t* target) {
    if (target == nullptr || module.base == 0 ||
        module.textSize < kManagerLoadConfigsFileOffset + kManagerLoadConfigsRelayExpectedEntry.size()) {
        return false;
    }
    if (module.buildId != kTargetBuildId || kManagerLoadConfigsFileOffset % 4 != 0) {
        return false;
    }
    const uintptr_t candidate = target_address(module.base, kManagerLoadConfigsFileOffset);
    if (candidate < module.base || !module.Contains(candidate, kManagerLoadConfigsRelayExpectedEntry.size()) ||
        candidate % 4 != 0 || !IsMappedRxModuleCodeWindow(candidate, kManagerLoadConfigsRelayExpectedEntry.size())) {
        return false;
    }
    *target = candidate;
    return true;
}

bool VerifyManagerLoadConfigsRelay(const TargetModule& module, uintptr_t target, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || target == 0 ||
        module.textSize < kManagerLoadConfigsRelayCodeOffset + kManagerLoadConfigsRelayExpectedBytes.size()) {
        return false;
    }
    if (target != target_address(module.base, kManagerLoadConfigsFileOffset) ||
        !module.Contains(target, kManagerLoadConfigsRelayExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(target, kManagerLoadConfigsRelayExpectedEntry.size())) {
        return false;
    }
    std::array<u8, kManagerLoadConfigsRelayExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kManagerLoadConfigsRelayExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }

    const uintptr_t relay = target_address(module.base, kManagerLoadConfigsRelayCodeOffset);
    if (relay < module.base || !module.Contains(relay, kManagerLoadConfigsRelayExpectedBytes.size()) ||
        relay % 4 != 0 || !IsMappedRxModuleCodeWindow(relay, kManagerLoadConfigsRelayExpectedBytes.size())) {
        return false;
    }
    std::array<u8, kManagerLoadConfigsRelayExpectedBytes.size() - sizeof(uintptr_t)> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kManagerLoadConfigsRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }

    const uintptr_t candidateSlot = target_address(module.base, kManagerLoadConfigsRelaySlotOffset);
    if (candidateSlot < module.base || (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}
#endif

HookInstallResult TryInstallManagerUpdateHook(const TargetModule& module) {
    uintptr_t target = 0;
    if (!VerifyManagerUpdate(module, &target)) {
        return RecordUpdateHookInstall(HookInstallResult::InstructionMismatch);
    }

    uintptr_t slot = 0;
    if (!VerifyManagerRelay(module, target, &slot)) {
        return RecordUpdateHookInstall(HookInstallResult::RelayPatchMismatch);
    }

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
    uintptr_t ownerSlot = 0;
    if (!VerifyGameOwnerSlot(module, &ownerSlot)) {
        return HookInstallResult::GameOwnerSlotMismatch;
    }

    uintptr_t isPausedThunk = 0;
    if (!VerifyGameIsPausedThunk(module, &isPausedThunk)) {
        return HookInstallResult::GameIsPausedThunkMismatch;
    }

    g_Stage6GameOwnerSlot.store(ownerSlot, std::memory_order_release);
    if (g_Stage6GameOwnerSlot.load(std::memory_order_acquire) != ownerSlot) {
        return HookInstallResult::GameOwnerSlotPublishFailed;
    }
    g_Stage6GameIsPausedThunk.store(isPausedThunk, std::memory_order_release);
    if (g_Stage6GameIsPausedThunk.load(std::memory_order_acquire) != isPausedThunk) {
        return HookInstallResult::GameIsPausedThunkPublishFailed;
    }
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
    uintptr_t ownerSlot = 0;
    uintptr_t isPausedThunk = 0;
    uintptr_t actionMethod = 0;
    if (!VerifyGameOwnerSlot(module, &ownerSlot) || !VerifyGameIsPausedThunk(module, &isPausedThunk)) {
        return HookInstallResult::Stage88GameBindingsMismatch;
    }
    if (!VerifyManagerIsActionTriggered(module, &actionMethod)) {
        return HookInstallResult::ManagerIsActionTriggeredMismatch;
    }
    ConfigureStage88Bindings(ownerSlot, isPausedThunk, actionMethod);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
    uintptr_t ownerSlot = 0;
    uintptr_t isPausedThunk = 0;
    uintptr_t actionMethod = 0;
    if (!VerifyGameOwnerSlot(module, &ownerSlot) || !VerifyGameIsPausedThunk(module, &isPausedThunk)) {
        return HookInstallResult::Stage89GameBindingsMismatch;
    }
    if (!VerifyManagerIsActionTriggered(module, &actionMethod)) {
        return HookInstallResult::ManagerIsActionTriggeredMismatch;
    }
    ConfigureStage89Bindings(ownerSlot, isPausedThunk, actionMethod);
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE)
    // Optional native bindings must not prevent generic Lua callbacks from running.
    // 生产分支的"校验 + 发布"已抽成 `PublishManagerEngineBindings()`（定义在本文件后部、
    // 与更新钩子的旧安装器相邻），并在下面钩子写好后统一调用一次。
    // 抽出来的原因见那个函数的注释：零占洞后端路径**不会**走到这个安装器，但它同样需要这些绑定。
#else
#if EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
    uintptr_t isActionPressed = 0;
    uintptr_t isActionTriggered = 0;
    uintptr_t getActionValue = 0;
    uintptr_t ownerSlot = 0;
    if (!VerifyGameOwnerSlot(module, &ownerSlot)) {
        return HookInstallResult::GameBindingsMismatch;
    }
    uintptr_t isPausedThunk = 0;
    if (!VerifyGameIsPausedThunk(module, &isPausedThunk)) {
        return HookInstallResult::GameBindingsMismatch;
    }
    uintptr_t isGreedMode = 0;
    if (!VerifyGameIsGreedMode(module, &isGreedMode)) {
        return HookInstallResult::GameBindingsMismatch;
    }
    uintptr_t isAscent = 0;
    if (!VerifyLevelIsAscent(module, &isAscent)) {
        return HookInstallResult::GameBindingsMismatch;
    }
    uintptr_t getCurrentMusicId = 0;
    uintptr_t musicPause = 0;
    uintptr_t musicResume = 0;
    if (!VerifyMusicBindings(module, &getCurrentMusicId, &musicPause, &musicResume)) {
        return HookInstallResult::MusicBindingsMismatch;
    }
    g_LuaGameOwnerSlot.store(ownerSlot, std::memory_order_release);
    g_LuaGameIsPausedThunk.store(isPausedThunk, std::memory_order_release);
    const uintptr_t publishedOwnerSlot = g_LuaGameOwnerSlot.load(std::memory_order_acquire);
    const uintptr_t publishedIsPausedThunk = g_LuaGameIsPausedThunk.load(std::memory_order_acquire);
    if (publishedOwnerSlot != ownerSlot || publishedIsPausedThunk != isPausedThunk) {
        return HookInstallResult::GameBindingsPublishFailed;
    }
    // 控制器输入绑定：与生产分支同样按"可选"处理 —— 守卫不符时留 0，handler 报绑定不可用。
    // 这段以前只写在 `#if !defined(EXL_DIAGNOSTIC_STAGE)` 分支里，而下面同一个
    // `SetInputBindings` 调用在诊断档（14/15/16/17/45/48）也会编译，于是诊断构建报
    // "isActionPressed was not declared in this scope"（2026-09-13 修）。
    VerifyManagerIsActionPressed(module, &isActionPressed);
    VerifyManagerIsActionTriggered(module, &isActionTriggered);
    VerifyManagerGetActionValue(module, &getActionValue);
#endif
#endif

    const uintptr_t fallback = target_address(module.base, kManagerRelayFallbackOffset);
    if (!ManagerUpdateHook::PublishOriginal(fallback)) {
        return RecordUpdateHookInstall(HookInstallResult::RelayPublishFailed);
    }

    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return RecordUpdateHookInstall(HookInstallResult::RelaySlotNotEmpty);
    }

    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ManagerUpdateHook::Callback);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return RecordUpdateHookInstall(HookInstallResult::RelayPublishFailed);
    }

    ManagerUpdateHookAudit::RecordInstall(
        module.base, target, target_address(module.base, kManagerRelayCodeOffset),
        slot, callback, IsMappedRxModuleCodeWindow(callback, sizeof(u32)));

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
#if !defined(EXL_DIAGNOSTIC_STAGE)
    // 引擎绑定：与零占洞后端共用同一个实现（抽出来的原因见函数注释）。
    PublishManagerEngineBindings(module);
#else
    LuaRuntime::SetGameBindings(publishedOwnerSlot, publishedIsPausedThunk);
    // 游戏本体模块基址（批次 2）：`Isaac.GetPlayer` 的指针链与 `Entity_Player` 的 vptr 判据都用它。
    // 发布点与 `SetGameBindings` 同一个：这里已经拿到并且校验过 `TargetModule::base`。
    LuaRuntime::SetEngineModuleBase(module.base);
    LuaRuntime::SetGameIsGreedModeBinding(isGreedMode);
    LuaRuntime::SetLevelIsAscentBinding(isAscent);
    // 批次 8（2026-09-15）：诊断构建这条分支也要发布，否则诊断档里 `Level` 家族少两个方法
    // （历史教训：这里与 `PublishManagerEngineBindings` 曾因漏发而分叉）。
    {
        uintptr_t absoluteStage = 0;
        uintptr_t nextStageAvailable = 0;
        VerifyLevelGetAbsoluteStage(module, &absoluteStage);
        VerifyLevelIsNextStageAvailable(module, &nextStageAvailable);
        LuaRuntime::SetLevelGetAbsoluteStageBinding(absoluteStage);
        LuaRuntime::SetLevelIsNextStageAvailableBinding(nextStageAvailable);
        // 2026-09-16：`Room:GetFrameCount()`。同样两处发布点都要发（漏发=某个档位少一个方法）。
        uintptr_t roomGetFrameCount = 0;
        VerifyRoomGetFrameCount(module, &roomGetFrameCount);
        LuaRuntime::SetRoomGetFrameCountBinding(roomGetFrameCount);
        // 批次 10：`Room:WorldToScreenPosition()` 的引擎函数（两处发布点都要发 —— 漏发会让
        // 诊断档里少一个方法，这条教训本项目已经栽过一次）。
        uintptr_t getRenderPosition = 0;
        VerifyGetRenderPositionStub(module, &getRenderPosition);
        LuaRuntime::SetGetRenderPositionBinding(getRenderPosition);
    }
    LuaRuntime::SetMusicBindings(getCurrentMusicId, musicPause, musicResume);
    LuaRuntime::SetInputBindings(isActionPressed, isActionTriggered, getActionValue);
    // RNG is optional; an unavailable guard must not block the default Mod.
    TryInstallRngBindings(module);
    // Font is optional for the same reason. The record is verified and published right here
    // rather than in the per-stage branch above, so every diagnostic variant that reaches this
    // block compiles unchanged; a failed guard only leaves that one field at zero.
    LuaRuntime::LuaFontBindings fontBindings{};
    VerifyFontBindings(module, &fontBindings);
    LuaRuntime::SetFontBindings(fontBindings);
    // Sprite 同样按"可选能力"处理：守卫不符只让那一个字段保持 0，handler 会报绑定不可用。
    LuaRuntime::LuaSpriteBindings spriteBindings{};
    VerifySpriteBindings(module, &spriteBindings);
    LuaRuntime::SetSpriteBindings(spriteBindings);
#endif
#endif
    g_HookEnabled.store(true, std::memory_order_release);
    return RecordUpdateHookInstall(HookInstallResult::Success);
}

#if !defined(EXL_DIAGNOSTIC_STAGE)
// 引擎绑定（Lua 层要用的那组游戏内地址）：与"装钩子"本来是两件事，历史上却挤在更新钩子的旧安装器
// 里（`TryInstallManagerUpdateHook`）。M2a 第一次真机失败正是栽在这一点上 —— 把更新挂点切到零占洞
// 后端会**跳过那个安装器**，于是这些绑定一个都没发布：EID 的 `main.lua:61` 调 `Sprite()` 当场报
// "Sprite native binding is unavailable"，整个 Mod 加载失败
// （`TestRunObserver` 的 `ManifestInstallReturned` detail = 0x15 = LuaInit/ScriptRunFailed）。
// 因此把"校验 + 发布"抽成独立一步，两条后端路径都调用同一个实现：
//   * 旧 IPS 后端：仍在 `TryInstallManagerUpdateHook` 里调用（位置、顺序都不变）；
//   * 零占洞后端：在 `TryInstallDefaultManifestMod` 里、钩子装好之后调用。
// 生产分支下这些绑定一律按"可选"处理：守卫不符只让对应字段留 0（handler 会报该绑定不可用），
// 不阻断任何钩子的安装、也不影响安装结果。
void PublishManagerEngineBindings(const TargetModule& module) {
    uintptr_t ownerSlot = 0;
    uintptr_t isPausedThunk = 0;
    const bool hasOwnerSlot = VerifyGameOwnerSlot(module, &ownerSlot);
    const bool hasIsPaused = VerifyGameIsPausedThunk(module, &isPausedThunk);
    uintptr_t isGreedMode = 0;
    VerifyGameIsGreedMode(module, &isGreedMode);
    uintptr_t isAscent = 0;
    VerifyLevelIsAscent(module, &isAscent);
    uintptr_t getCurrentMusicId = 0;
    uintptr_t musicPause = 0;
    uintptr_t musicResume = 0;
    VerifyMusicBindings(module, &getCurrentMusicId, &musicPause, &musicResume);
    // Controller input bindings: optional, like the others, so a missing guard leaves the
    // handler reporting an unavailable binding instead of blocking hook installation.
    uintptr_t isActionPressed = 0;
    uintptr_t isActionTriggered = 0;
    uintptr_t getActionValue = 0;
    VerifyManagerIsActionPressed(module, &isActionPressed);
    VerifyManagerIsActionTriggered(module, &isActionTriggered);
    VerifyManagerGetActionValue(module, &getActionValue);
    if (!hasOwnerSlot || !hasIsPaused) {
        ownerSlot = 0;
        isPausedThunk = 0;
    }
    g_LuaGameOwnerSlot.store(ownerSlot, std::memory_order_release);
    g_LuaGameIsPausedThunk.store(isPausedThunk, std::memory_order_release);
    const uintptr_t publishedOwnerSlot = g_LuaGameOwnerSlot.load(std::memory_order_acquire);
    const uintptr_t publishedIsPausedThunk = g_LuaGameIsPausedThunk.load(std::memory_order_acquire);
    LuaRuntime::SetGameBindings(publishedOwnerSlot, publishedIsPausedThunk);
    // 游戏本体模块基址（批次 2）：`Isaac.GetPlayer` 的指针链与 `Entity_Player` 的 vptr 判据都用它。
    // 发布点与 `SetGameBindings` 同一个：这里已经拿到并且校验过 `TargetModule::base`。
    LuaRuntime::SetEngineModuleBase(module.base);
    LuaRuntime::SetGameIsGreedModeBinding(isGreedMode);
    LuaRuntime::SetLevelIsAscentBinding(isAscent);
    // 批次 8（2026-09-15）：与上面那两处同样的"可选能力"处理 —— 守卫不符只留 0，
    // handler 会报"绑定不可用"，不会编造值，也不影响其它挂点。
    uintptr_t absoluteStage = 0;
    uintptr_t nextStageAvailable = 0;
    VerifyLevelGetAbsoluteStage(module, &absoluteStage);
    VerifyLevelIsNextStageAvailable(module, &nextStageAvailable);
    LuaRuntime::SetLevelGetAbsoluteStageBinding(absoluteStage);
    LuaRuntime::SetLevelIsNextStageAvailableBinding(nextStageAvailable);
    // 2026-09-16：`Room:GetFrameCount()`（`0x470B0C`）。与上面几条同样按"可选能力"处理：
    // 守卫不符只留 0，handler 报"绑定不可用"，**不编造帧数**。
    uintptr_t roomGetFrameCount = 0;
    VerifyRoomGetFrameCount(module, &roomGetFrameCount);
    LuaRuntime::SetRoomGetFrameCountBinding(roomGetFrameCount);
    // 批次 10（2026-09-15）：`Room:WorldToScreenPosition()` 要调的引擎函数。同样按"可选能力"
    // 处理：守卫不符只留 0，handler 报"绑定不可用"，不编造坐标。
    uintptr_t getRenderPosition = 0;
    VerifyGetRenderPositionStub(module, &getRenderPosition);
    LuaRuntime::SetGetRenderPositionBinding(getRenderPosition);
    LuaRuntime::SetMusicBindings(getCurrentMusicId, musicPause, musicResume);
    LuaRuntime::SetInputBindings(isActionPressed, isActionTriggered, getActionValue);
    // RNG is optional; an unavailable guard must not block the default Mod.
    TryInstallRngBindings(module);
    // Font is optional for the same reason. The record is verified and published right here
    // rather than in the per-stage branch above, so every diagnostic variant that reaches this
    // block compiles unchanged; a failed guard only leaves that one field at zero.
    LuaRuntime::LuaFontBindings fontBindings{};
    VerifyFontBindings(module, &fontBindings);
    LuaRuntime::SetFontBindings(fontBindings);
    // Sprite 同样按"可选能力"处理：守卫不符只让那一个字段保持 0，handler 会报绑定不可用。
    LuaRuntime::LuaSpriteBindings spriteBindings{};
    VerifySpriteBindings(module, &spriteBindings);
    LuaRuntime::SetSpriteBindings(spriteBindings);
}
#endif

namespace {
// `Present` 前派发中继：调用点已被 IPS 改成分支，代码洞里是"调回调→补回原 bl→跳回调用点+4"，
// 槽保持零值直到运行时发布回调。三段都要逐步校验，任何一段不符都返回 false（只留绑定为 0，
// 不影响其它钩子安装）。
bool VerifyManagerPresentRelay(const TargetModule& module, uintptr_t* slot) {
    if (slot == nullptr || module.base == 0 || module.buildId != kTargetBuildId ||
        kManagerPresentCallExpectedEntry.size() > module.textSize ||
        kManagerPresentRelayExpectedBytes.size() > module.textSize ||
        sizeof(uintptr_t) > module.textSize ||
        kManagerPresentCallFileOffset > module.textSize - kManagerPresentCallExpectedEntry.size() ||
        kManagerPresentRelayCodeOffset > module.textSize - kManagerPresentRelayExpectedBytes.size() ||
        kManagerPresentRelaySlotOffset > module.textSize - sizeof(uintptr_t)) {
        return false;
    }
    const uintptr_t target = target_address(module.base, kManagerPresentCallFileOffset);
    const uintptr_t relay = target_address(module.base, kManagerPresentRelayCodeOffset);
    const uintptr_t candidateSlot = target_address(module.base, kManagerPresentRelaySlotOffset);
    if (target < module.base || relay < module.base || candidateSlot < module.base ||
        (target & 3u) != 0 || (relay & 3u) != 0 ||
        (candidateSlot & (alignof(uintptr_t) - 1)) != 0 ||
        !module.Contains(target, kManagerPresentCallExpectedEntry.size()) ||
        !module.Contains(relay, kManagerPresentRelayExpectedBytes.size()) ||
        !module.Contains(candidateSlot, sizeof(uintptr_t)) ||
        !IsMappedRxModuleCodeWindow(target, kManagerPresentCallExpectedEntry.size()) ||
        !IsMappedRxModuleCodeWindow(relay, kManagerPresentRelayExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(candidateSlot, sizeof(uintptr_t))) {
        return false;
    }
    std::array<u8, kManagerPresentCallExpectedEntry.size()> entry{};
    std::memcpy(entry.data(), reinterpret_cast<const void*>(target), entry.size());
    if (!verify_bytes(kManagerPresentCallExpectedEntry.data(), entry.data(), entry.size())) {
        return false;
    }
    std::array<u8, kManagerPresentRelayExpectedBytes.size() - sizeof(uintptr_t)> bridge{};
    std::memcpy(bridge.data(), reinterpret_cast<const void*>(relay), bridge.size());
    if (!verify_bytes(kManagerPresentRelayExpectedBytes.data(), bridge.data(), bridge.size())) {
        return false;
    }
    *slot = candidateSlot;
    return true;
}
}  // namespace

// 图片加载中继：入口已被 IPS 改成分支（首字必须是跳向我们代码洞的 `b`），回调槽保持零值
// 直到运行时发布。校验的每一步都返回**可区分的码**，探针会把低位报出来（位 23..25），
// 这样"到底卡在哪一步"不需要靠猜。
RenderPresentRelayInstallResult TryInstallManagerPresentRelay(const TargetModule& module) {
    uintptr_t slot = 0;
    if (!VerifyManagerPresentRelay(module, &slot)) {
        return RecordRenderPresentRelayInstall(RenderPresentRelayInstallResult::RelayPatchMismatch);
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return RecordRenderPresentRelayInstall(RenderPresentRelayInstallResult::RelaySlotNotEmpty);
    }
    const uintptr_t callback =
        reinterpret_cast<uintptr_t>(&IsaacModRuntime_DispatchPostRenderBeforePresent);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return RecordRenderPresentRelayInstall(RenderPresentRelayInstallResult::RelayPublishFailed);
    }
    return RecordRenderPresentRelayInstall(RenderPresentRelayInstallResult::Success);
}

// ---- M2b：`Present` 的 GOT 槽方案（零代码字节）--------------------------------------
//
// 拦截函数**按调用方过滤**：PLT 桩是尾调用（`br x17`），所以进入我们函数时 `x30`（LR）就是
// 原来那处调用的返回地址。只有它等于"模块基址 + `kManagerPresentCallFileOffset` + 4"时才派发
// `MC_POST_RENDER`；其余调用原样转发给真正的 `Present`。
//
// 为什么必须过滤：这条槽被 43 处调用共用（见 `kManagerPresentStubOffset` 的注释）。不过滤的话
// `MC_POST_RENDER` 会在它从没被派发过的时机与次数上被派发（菜单、过场、背景、实体更新…）。
//
// 为什么调 `target` 而不是"槽里读出来的原值"：槽在模块初始化期可能还指着**懒绑定解析桩**
// （文件初值 `0x66FAA0`）。调原值会走解析桩，而解析桩解析完会把**我们刚写进去的拦截函数覆盖掉**，
// 于是一次之后就再也不会派发。直接调 `Present` 本体（`0x4F3014`，已由三段守卫核对过）绕开这个坑。
std::atomic<uintptr_t> g_PresentGotSlotDispatchReturn{0};
std::atomic<uintptr_t> g_PresentGotSlotTarget{0};

extern "C" __attribute__((noinline)) uintptr_t
IsaacModRuntime_PresentGotSlotIntercept(uintptr_t a0, uintptr_t a1, uintptr_t a2, uintptr_t a3,
                                        uintptr_t a4, uintptr_t a5, uintptr_t a6, uintptr_t a7) {
    // 先取返回地址：任何调用之前做，避免 `x30` 被自己用掉。
    const uintptr_t caller = reinterpret_cast<uintptr_t>(__builtin_return_address(0));
    if (caller == g_PresentGotSlotDispatchReturn.load(std::memory_order_acquire)) {
        // `a0` 就是调用点上下文里的 `x0`：`Present` 的实参（`KAGE::Graphics::g_Manager`），
        // 与 IPS 方案传给派发入口的那个参数同源。
        IsaacModRuntime_DispatchPostRenderBeforePresent(reinterpret_cast<void*>(a0));
    }
    const uintptr_t target = g_PresentGotSlotTarget.load(std::memory_order_acquire);
    if (target == 0) {
        return 0;  // 安装成功才会改槽，这里只是保底：绝不跳空
    }
    using PresentFn = uintptr_t (*)(uintptr_t, uintptr_t, uintptr_t, uintptr_t,
                                    uintptr_t, uintptr_t, uintptr_t, uintptr_t);
    // 整数参数按 x0..x7 原样转发；IPS 方案同样只保存 x0..x4 + LR 就能工作，
    // 说明这处调用不依赖 x5 以上与浮点寄存器。
    return reinterpret_cast<PresentFn>(target)(a0, a1, a2, a3, a4, a5, a6, a7);
}

namespace {
// 该地址所在页是否可写（可写就直接写，避开 `RwPages` 的 `EXL_ASSERT` 路径）。
bool PageIsWritable(uintptr_t address) {
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address))) {
        return false;
    }
    return (info.perm & Perm_W) != 0;
}
}  // namespace

RenderPresentRelayInstallResult TryInstallManagerPresentGotSlot(const TargetModule& module) {
    if (module.base == 0 || module.buildId != kTargetBuildId) {
        return RecordRenderPresentRelayInstall(
            RenderPresentRelayInstallResult::GotSlotTargetMismatch);
    }
    const uintptr_t callsite = target_address(module.base, kManagerPresentCallFileOffset);
    const uintptr_t stub = target_address(module.base, kManagerPresentStubOffset);
    const uintptr_t target = target_address(module.base, kManagerPresentTargetOffset);
    const uintptr_t slot = target_address(module.base, kManagerPresentGotSlotOffset);
    // 三段守卫：调用点必须已回到**原样**（即那份 IPS 已删）、桩形态、`Present` 本体序言。
    if (!module.Contains(callsite, kManagerPresentCallExpectedBytes.size()) ||
        !module.Contains(stub, kManagerPresentStubExpectedBytes.size()) ||
        !module.Contains(target, kManagerPresentTargetExpectedBytes.size()) ||
        !verify_bytes(kManagerPresentCallExpectedBytes.data(),
                      reinterpret_cast<const u8*>(callsite),
                      kManagerPresentCallExpectedBytes.size()) ||
        !verify_bytes(kManagerPresentStubExpectedBytes.data(),
                      reinterpret_cast<const u8*>(stub),
                      kManagerPresentStubExpectedBytes.size()) ||
        !verify_bytes(kManagerPresentTargetExpectedBytes.data(),
                      reinterpret_cast<const u8*>(target),
                      kManagerPresentTargetExpectedBytes.size())) {
        return RecordRenderPresentRelayInstall(
            RenderPresentRelayInstallResult::GotSlotTargetMismatch);
    }
    // 槽必须在已映射页内、8 字节对齐、且加载器已经写过（非零）。
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, slot)) || info.size == 0 ||
        slot < info.addr || slot > info.addr + info.size ||
        sizeof(uintptr_t) > info.addr + info.size - slot ||
        (slot & (alignof(uintptr_t) - 1)) != 0) {
        return RecordRenderPresentRelayInstall(
            RenderPresentRelayInstallResult::GotSlotUnavailable);
    }
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) == 0) {
        return RecordRenderPresentRelayInstall(
            RenderPresentRelayInstallResult::GotSlotUnavailable);
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&IsaacModRuntime_PresentGotSlotIntercept);
    g_PresentGotSlotDispatchReturn.store(callsite + 4, std::memory_order_release);
    g_PresentGotSlotTarget.store(target, std::memory_order_release);
    if (PageIsWritable(slot)) {
        // 数据页直接写即可（不是代码改写，不需要 icache 失效）。
        __atomic_store_n(reinterpret_cast<uintptr_t*>(slot), callback, __ATOMIC_RELEASE);
    } else {
        exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
        auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
        __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
        slotPages.Flush();
    }
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return RecordRenderPresentRelayInstall(
            RenderPresentRelayInstallResult::GotSlotWriteFailed);
    }
    // 与 IPS 路径共用同一个状态字：渲染入口中继据此判断"Present 前派发点已就绪"，
    // 从而不再走那条"晚一帧"的回退派发。
    return RecordRenderPresentRelayInstall(RenderPresentRelayInstallResult::Success);
}

RenderHookInstallResult TryInstallManagerRenderHook(const TargetModule& module) {
    uintptr_t target = 0;
    if (!VerifyManagerRender(module, &target)) {
        return RecordRenderHookInstall(RenderHookInstallResult::InstructionMismatch);
    }
    uintptr_t slot = 0;
    if (!VerifyManagerRenderRelay(module, target, &slot)) {
        return RecordRenderHookInstall(RenderHookInstallResult::RelayPatchMismatch);
    }
    const uintptr_t fallback = target_address(module.base, kManagerRenderRelayFallbackOffset);
    if (!ManagerRenderHook::PublishOriginal(fallback)) {
        return RecordRenderHookInstall(RenderHookInstallResult::RelayPublishFailed);
    }
    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return RecordRenderHookInstall(RenderHookInstallResult::RelaySlotNotEmpty);
    }
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ManagerRenderHook::Callback);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return RecordRenderHookInstall(RenderHookInstallResult::RelayPublishFailed);
    }
    return RecordRenderHookInstall(RenderHookInstallResult::Success);
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
LoadConfigsDiagnosticInstallResult TryInstallManagerLoadConfigsDiagnostic(const TargetModule& module) {
    uintptr_t target = 0;
    if (!VerifyManagerLoadConfigs(module, &target)) {
        return LoadConfigsDiagnosticInstallResult::InstructionOrRelayMismatch;
    }

    uintptr_t slot = 0;
    if (!VerifyManagerLoadConfigsRelay(module, target, &slot)) {
        return LoadConfigsDiagnosticInstallResult::InstructionOrRelayMismatch;
    }

    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return LoadConfigsDiagnosticInstallResult::RelaySlotNotEmpty;
    }

    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ManagerLoadConfigsDiagnosticCallback);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        return LoadConfigsDiagnosticInstallResult::RelayPublishFailed;
    }
    return LoadConfigsDiagnosticInstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
LoadConfigsResetDiagnosticInstallResult TryInstallManagerLoadConfigsResetDiagnostic(const TargetModule& module) {
    uintptr_t target = 0;
    if (!VerifyManagerLoadConfigs(module, &target)) {
        return LoadConfigsResetDiagnosticInstallResult::LoadConfigsInstructionOrRelayMismatch;
    }

    uintptr_t slot = 0;
    if (!VerifyManagerLoadConfigsRelay(module, target, &slot)) {
        return LoadConfigsResetDiagnosticInstallResult::LoadConfigsInstructionOrRelayMismatch;
    }

    const uintptr_t resetAddress = target_address(module.base, kModManagerResetFileOffset);
    if (resetAddress < module.base || resetAddress % 4 != 0 ||
        !module.Contains(resetAddress, kModManagerResetExpectedBytes.size()) ||
        !IsMappedRxModuleCodeWindow(resetAddress, kModManagerResetExpectedBytes.size())) {
        return LoadConfigsResetDiagnosticInstallResult::ResetInstructionOrMappingMismatch;
    }
    std::array<u8, kModManagerResetExpectedBytes.size()> resetBytes{};
    std::memcpy(resetBytes.data(), reinterpret_cast<const void*>(resetAddress), resetBytes.size());
    if (!verify_bytes(kModManagerResetExpectedBytes.data(), resetBytes.data(), resetBytes.size())) {
        return LoadConfigsResetDiagnosticInstallResult::ResetInstructionOrMappingMismatch;
    }

    exl::util::RwPages slotPages(slot, sizeof(uintptr_t));
    auto* rwSlot = reinterpret_cast<uintptr_t*>(slotPages.GetRw());
    if (__atomic_load_n(rwSlot, __ATOMIC_ACQUIRE) != 0) {
        return LoadConfigsResetDiagnosticInstallResult::RelaySlotNotEmpty;
    }

    g_Stage9ResetAddress.store(resetAddress, std::memory_order_release);
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&ManagerLoadConfigsResetDiagnosticCallback);
    __atomic_store_n(rwSlot, callback, __ATOMIC_RELEASE);
    slotPages.Flush();
    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {
        g_Stage9ResetAddress.store(0, std::memory_order_release);
        return LoadConfigsResetDiagnosticInstallResult::RelayPublishFailed;
    }
    return LoadConfigsResetDiagnosticInstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
Stage11InstallResult TryInstallRomfsSentinelDiagnostic(const TargetModule& module) {
    uintptr_t target = 0;
    uintptr_t slot = 0;
    GameFileReader::Bindings bindings{};
    if (!VerifyManagerUpdate(module, &target) || !VerifyManagerRelay(module, target, &slot) ||
        !GameFileReader::VerifyBindings(module, &bindings)) {
        return Stage11InstallResult::FileBindingMismatch;
    }

    g_Stage11Bindings = bindings;
    g_Stage11State.store(Stage11State::Armed, std::memory_order_release);
    if (TryInstallManagerUpdateHook(module) != HookInstallResult::Success) {
        Stage11State expected = Stage11State::Armed;
        if (g_Stage11State.compare_exchange_strong(expected, Stage11State::Unarmed, std::memory_order_acq_rel)) {
            g_Stage11Bindings = {};
        }
        return Stage11InstallResult::FileBindingMismatch;
    }
    return Stage11InstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
Stage12InstallResult TryInstallRomfsLuaDiagnostic(const TargetModule& module) {
    uintptr_t target = 0;
    uintptr_t slot = 0;
    GameFileReader::Bindings bindings{};
    if (!VerifyManagerUpdate(module, &target) || !VerifyManagerRelay(module, target, &slot) ||
        !GameFileReader::VerifyBindings(module, &bindings)) {
        return Stage12InstallResult::FileBindingMismatch;
    }

    g_Stage12Bindings = bindings;
    g_Stage12State.store(Stage12State::Armed, std::memory_order_release);
    if (TryInstallManagerUpdateHook(module) != HookInstallResult::Success) {
        Stage12State expected = Stage12State::Armed;
        if (g_Stage12State.compare_exchange_strong(expected, Stage12State::Unarmed, std::memory_order_acq_rel)) {
            g_Stage12Bindings = {};
        }
        return Stage12InstallResult::FileBindingMismatch;
    }
    return Stage12InstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
Stage13InstallResult TryInstallManifestRequireDiagnostic(const TargetModule& module) {
    uintptr_t target = 0;
    uintptr_t slot = 0;
    GameFileReader::Bindings bindings{};
    if (!VerifyManagerUpdate(module, &target) || !VerifyManagerRelay(module, target, &slot) ||
        !GameFileReader::VerifyBindings(module, &bindings)) {
        return Stage13InstallResult::FileBindingMismatch;
    }

    g_Stage13Bindings = bindings;
    g_Stage13State.store(Stage13State::Armed, std::memory_order_release);
    if (TryInstallManagerUpdateHook(module) != HookInstallResult::Success) {
        Stage13State expected = Stage13State::Armed;
        if (g_Stage13State.compare_exchange_strong(expected, Stage13State::Unarmed, std::memory_order_acq_rel)) {
            g_Stage13Bindings = {};
            return Stage13InstallResult::FileBindingMismatch;
        }
    }
    return Stage13InstallResult::Success;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
Stage118InstallResult TryInstallStage118ManagedFileWriteDiagnostic(const TargetModule& module) {
    GameFileReader::WriteBindings bindings{};
    if (!GameFileReader::VerifyWriteBindings(module, &bindings)) {
        return Stage118InstallResult::FileBindingMismatch;
    }
    g_Stage118Bindings = bindings;
    if (TryInstallManagerUpdateHook(module) != HookInstallResult::Success) {
        g_Stage118Bindings = {};
        return Stage118InstallResult::HookInstallFailed;
    }
    g_Stage118Armed.store(true, std::memory_order_release);
    return Stage118InstallResult::Success;
}
#endif

bool InstallManagerUpdateHook(const TargetModule& module) {
    return TryInstallManagerUpdateHook(module) == HookInstallResult::Success;
}

bool IsHookEnabled() {
    return g_HookEnabled.load(std::memory_order_acquire);
}
