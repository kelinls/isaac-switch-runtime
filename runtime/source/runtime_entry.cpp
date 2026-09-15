#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 6 || EXL_DIAGNOSTIC_STAGE == 7 || EXL_DIAGNOSTIC_STAGE == 8 || EXL_DIAGNOSTIC_STAGE == 9 || EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 13 || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48 || EXL_DIAGNOSTIC_STAGE == 88 || EXL_DIAGNOSTIC_STAGE == 89 || EXL_DIAGNOSTIC_STAGE == 102 || EXL_DIAGNOSTIC_STAGE == 104 || EXL_DIAGNOSTIC_STAGE == 108 || EXL_DIAGNOSTIC_STAGE == 109 || EXL_DIAGNOSTIC_STAGE == 110 || EXL_DIAGNOSTIC_STAGE == 112 || EXL_DIAGNOSTIC_STAGE == 118 || EXL_DIAGNOSTIC_STAGE == 127 || EXL_DIAGNOSTIC_STAGE == 128

#include "lib.hpp"

#include "module_finder.hpp"
#include "hook_manager.hpp"
#include "lua_runtime.hpp"
#include "manager_update_hook_audit.hpp"
#include "persistence_event_journal.hpp"
#include "runtime_constants.hpp"
#include "runtime_state.hpp"
#include "test_run_observer.hpp"
#if defined(EXL_LAYERED_RUNTIME)
#include "bootstrap/runtime_bootstrap.hpp"
#include "saltynx_self_journal.hpp"
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
#include "stage14_diagnostic.hpp"
#endif

#include "lib/nx/kernel/svc.h"
#include "lib/nx/kernel/virtmem.h"

#include <atomic>

namespace {
std::atomic<RuntimeState> g_RuntimeState{RuntimeState::Cold};
enum class StartupStatus : u32 {
    TitleOk,
    TitleQueryFailed,
    TitleMismatch,
};
std::atomic<StartupStatus> g_StartupStatus{StartupStatus::TitleQueryFailed};
alignas(0x1000) u8 g_WorkerStack[0x4000];
Handle g_WorkerHandle = INVALID_HANDLE;

#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
constexpr u32 kStartupProbeStage6RuntimeStateRecorded = 1U << 0;
constexpr u32 kStartupProbeStage6HookInitialized = 1U << 1;
constexpr u32 kStartupProbeStage6TitleOk = 1U << 2;
constexpr u32 kStartupProbeStage6ThreadCreated = 1U << 3;
constexpr u32 kStartupProbeStage6WorkerDispatched = 1U << 4;
std::atomic<u32> g_StartupProbeStage6Gates{0};

extern "C" NORETURN void ReportStartupProbeWorkerGate(u32 gates);

void MarkStartupProbeStage6Gate(u32 gate) {
    g_StartupProbeStage6Gates.fetch_or(gate, std::memory_order_release);
}

NORETURN void StartupProbeWorker(void*) {
    MarkStartupProbeStage6Gate(kStartupProbeStage6WorkerDispatched);
    ReportStartupProbeWorkerGate(
        g_StartupProbeStage6Gates.load(std::memory_order_acquire));
}
#endif

#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
constexpr u32 kStartupProbeStage7RuntimeStateRecorded = 1U << 0;
constexpr u32 kStartupProbeStage7HookInitialized = 1U << 1;
constexpr u32 kStartupProbeStage7TitleOk = 1U << 2;
constexpr u32 kStartupProbeStage7ThreadCreated = 1U << 3;
constexpr u32 kStartupProbeStage7WorkerDispatched = 1U << 4;
std::atomic<u32> g_StartupProbeStage7Gates{0};

extern "C" NORETURN void ReportStartupProbeWorkerGate(u32 gates);
extern "C" void RunStartupProbeStage7WorkerGate(u32 gates);

void MarkStartupProbeStage7Gate(u32 gate) {
    g_StartupProbeStage7Gates.fetch_or(gate, std::memory_order_release);
}
#endif

#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
constexpr u32 kStartupProbeStage8WorkerEntered = 1U << 0;
constexpr u32 kStartupProbeStage8TitleOk = 1U << 1;
constexpr u32 kStartupProbeStage8ScanStarted = 1U << 2;
constexpr u32 kStartupProbeStage8ModuleFound = 1U << 3;
constexpr u32 kStartupProbeStage8InstallSucceeded = 1U << 4;
constexpr u32 kStartupProbeStage8LuaInitialized = 1U << 5;
std::atomic<u32> g_StartupProbeStage8Gates{0};

extern "C" void RunStartupProbeStage8WorkerGate(u32 gates);

void MarkStartupProbeStage8Gate(u32 gate) {
    g_StartupProbeStage8Gates.fetch_or(gate, std::memory_order_release);
}

NORETURN void ReportStartupProbeStage8Gates() {
    TestRunObserver::Mark(TestRunObserver::State::FinalReportEntered,
                          g_StartupProbeStage8Gates.load(std::memory_order_acquire));
    RunStartupProbeStage8WorkerGate(
        g_StartupProbeStage8Gates.load(std::memory_order_acquire));
    UNREACHABLE;
}
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || \
     (EXL_STARTUP_PROBE_STAGE != 6 && EXL_STARTUP_PROBE_STAGE != 8))
constexpr u32 kManagerHookAuditSampleLimit = 300;
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || \
     (EXL_STARTUP_PROBE_STAGE != 6 && EXL_STARTUP_PROBE_STAGE != 8))
// ISAAC_PI: default manifest/Update hook installation failed before any callback can run.
constexpr u64 kTraceDefaultInstallFailureMagic = 0x49534141435F5049ULL;

NORETURN void ReportTraceDefaultInstallFailure(DefaultManifestInstallResult result) {
    svcBreak(BreakReason_User, kTraceDefaultInstallFailureMagic, static_cast<u32>(result));
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
constexpr u64 kStage15FailureMagic = 0x49534141435F5246ULL;

NORETURN void ReportStage15Failure(u32 status) {
    svcBreak(BreakReason_User, kStage15FailureMagic, (15ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
constexpr u64 kStage16FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage16Failure(u32 status) {
    svcBreak(BreakReason_User, kStage16FailureMagic, (16ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
constexpr u64 kStage17FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage17Failure(u32 status) {
    svcBreak(BreakReason_User, kStage17FailureMagic, (17ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
constexpr u64 kStage45FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage45Failure(u32 status) {
    svcBreak(BreakReason_User, kStage45FailureMagic, (45ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
constexpr u64 kStage48FailureMagic = 0x49534141435F4D46ULL;

NORETURN void ReportStage48Failure(u32 status) {
    svcBreak(BreakReason_User, kStage48FailureMagic, (48ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
constexpr u64 kStage88FailureMagic = 0x49534141435F4946ULL;

NORETURN void ReportStage88Failure(u32 status) {
    svcBreak(BreakReason_User, kStage88FailureMagic, (88ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
constexpr u64 kStage89FailureMagic = 0x49534141435F4946ULL;

NORETURN void ReportStage89Failure(u32 status) {
    svcBreak(BreakReason_User, kStage89FailureMagic, (89ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
constexpr u64 kStage102FailureMagic = 0x49534141435F4346ULL;

NORETURN void ReportStage102Failure(u32 status) {
    svcBreak(BreakReason_User, kStage102FailureMagic, (102ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
constexpr u64 kStage104FailureMagic = 0x49534141435F4B46ULL;

NORETURN void ReportStage104Failure(u32 status) {
    svcBreak(BreakReason_User, kStage104FailureMagic, (104ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
constexpr u64 kStage108FailureMagic = 0x49534141435F4C46ULL;

NORETURN void ReportStage108Failure(u32 status) {
    svcBreak(BreakReason_User, kStage108FailureMagic, (108ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
constexpr u64 kStage109FailureMagic = 0x49534141435F5246ULL;

NORETURN void ReportStage109Failure(u32 status) {
    svcBreak(BreakReason_User, kStage109FailureMagic, (109ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
constexpr u64 kStage127FailureMagic = 0x49534141435F5346ULL;

NORETURN void ReportStage127Failure(u32 status) {
    svcBreak(BreakReason_User, kStage127FailureMagic, (127ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
constexpr u64 kStage128FailureMagic = 0x49534141435F5346ULL;

NORETURN void ReportStage128Failure(u32 status) {
    svcBreak(BreakReason_User, kStage128FailureMagic, (128ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
constexpr u64 kStage110FailureMagic = 0x49534141435F5346ULL;

NORETURN void ReportStage110Failure(u32 status) {
    svcBreak(BreakReason_User, kStage110FailureMagic, (110ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
constexpr u64 kStage112FailureMagic = 0x49534141435F4446ULL;

NORETURN void ReportStage112Failure(u32 status) {
    svcBreak(BreakReason_User, kStage112FailureMagic, (112ULL << 32) | status);
    svcExitProcess();
}
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
constexpr u64 kStage118FailureMagic = 0x49534141435F4646ULL;

NORETURN void ReportStage118Failure(u32 status) {
    svcBreak(BreakReason_User, kStage118FailureMagic, (118ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 7
constexpr u64 kStage7FailureMagic = 0x49534141435F4C46ULL;

NORETURN void ReportStage7Failure(u32 status) {
    svcBreak(BreakReason_User, kStage7FailureMagic, (7ULL << 32) | status);
    svcExitProcess();
}

NORETURN void ReportStage7LuaInitializationFailure(LuaRuntime::LuaInitResult result) {
    switch (result) {
    case LuaRuntime::LuaInitResult::StateCreateFailed:
        ReportStage7Failure(1);
    case LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed:
        ReportStage7Failure(LuaRuntime::PreparationFailureDetail());
    case LuaRuntime::LuaInitResult::RuntimePreparationFailed:
        ReportStage7Failure(LuaRuntime::PreparationFailureDetail());
    case LuaRuntime::LuaInitResult::ScriptLoadFailed:
        ReportStage7Failure(2);
    case LuaRuntime::LuaInitResult::ScriptRunFailed:
        ReportStage7Failure(3);
    case LuaRuntime::LuaInitResult::MissingPostUpdateCallback:
        ReportStage7Failure(4);
    case LuaRuntime::LuaInitResult::Success:
        UNREACHABLE;
    }
    UNREACHABLE;
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
constexpr u64 kStage8FailureMagic = 0x49534141434D464CULL;

NORETURN void ReportStage8Failure(u32 status) {
    svcBreak(BreakReason_User, kStage8FailureMagic, (8ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
constexpr u64 kStage9FailureMagic = 0x495341414352464CULL;

NORETURN void ReportStage9Failure(u32 status) {
    svcBreak(BreakReason_User, kStage9FailureMagic, (9ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
NORETURN void ReportStage11Failure(u32 status) {
    svcBreak(BreakReason_User, 0x495341414346464CULL, (11ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
NORETURN void ReportStage12Failure(u32 status) {
    svcBreak(BreakReason_User, 0x4953414143454C46ULL, (12ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
constexpr u64 kStage14FailureMagic = 0x49534141435F4746ULL;

NORETURN void ReportStage14Failure(Stage14Diagnostic::Stage14Failure failure) {
    svcBreak(BreakReason_User, kStage14FailureMagic,
             (14ULL << 32) | static_cast<u32>(failure));
    svcExitProcess();
}

NORETURN void ReportStage14LuaInitializationFailure(LuaRuntime::LuaInitResult result) {
    if (result == LuaRuntime::LuaInitResult::Success) {
        UNREACHABLE;
    }
    ReportStage14Failure(Stage14Diagnostic::Stage14Failure::LuaInitializationFailed);
}
#endif

void CloseWorkerHandle() {
    if (g_WorkerHandle != INVALID_HANDLE) {
        svcCloseHandle(g_WorkerHandle);
        g_WorkerHandle = INVALID_HANDLE;
    }
}

void SetRuntimeState(const RuntimeContext& context) {
    const RuntimeState current = g_RuntimeState.load(std::memory_order_acquire);
    g_RuntimeState.store(Step(current, context), std::memory_order_release);
}

#if !defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 6
void ModuleWorker(void*) {
#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::WorkerEntered);
#endif
    TestRunObserver::Mark(TestRunObserver::State::ModuleWorkerEntered);
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
    MarkStartupProbeStage7Gate(kStartupProbeStage7WorkerDispatched);
    RunStartupProbeStage7WorkerGate(
        g_StartupProbeStage7Gates.load(std::memory_order_acquire));
#endif
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
    MarkStartupProbeStage8Gate(kStartupProbeStage8WorkerEntered);
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::Started, 0);
    ManagerUpdateHookAudit::Flush();
#endif
    const StartupStatus startup = g_StartupStatus.load(std::memory_order_acquire);
    TestRunObserver::Mark(TestRunObserver::State::TitleStateRead,
                          static_cast<std::uint32_t>(startup));
    if (startup != StartupStatus::TitleOk) {
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
        ReportStartupProbeStage8Gates();
#else
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        ReportStage6Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
        ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
        ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        ReportStage11Failure(3);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        ReportStage12Failure(3);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        ReportStage13Failure(3);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        ReportStage14Failure(Stage14Diagnostic::Stage14Failure::StartupFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
        ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
        ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
        ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
        ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
        ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
        ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
        ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
        ReportStage128Failure(1);
#endif
        svcExitThread();
#endif
    }
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
    MarkStartupProbeStage8Gate(kStartupProbeStage8TitleOk);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && \
    (EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 13)
    bool stage11CandidateSeen = false;
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::Scanning, 0);
#endif
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
    MarkStartupProbeStage8Gate(kStartupProbeStage8ScanStarted);
#endif
    for (u32 attempt = 0; attempt < kTargetModuleScanAttemptLimit; ++attempt) {
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
        ManagerUpdateHookAudit::RecordScanAttempt(attempt + 1);
        ManagerUpdateHookAudit::Flush();
#endif
        TestRunObserver::Mark(TestRunObserver::State::ModuleScanEntered, attempt + 1);
        const TargetModuleScanResult scan = ScanTargetModule();
        TestRunObserver::Mark(TestRunObserver::State::ModuleScanReturned,
                              static_cast<std::uint32_t>(scan.status));
        if (scan.status == TargetModuleScanStatus::BuildMismatch && scan.module.has_value()) {
            SetRuntimeState({.buildMismatch = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
            ReportStartupProbeStage8Gates();
#else
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
            ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::BuildMismatch, 0);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
            ReportStage6Failure(3);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
            ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
            ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
            ReportStage11Failure(4);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
            ReportStage12Failure(4);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
            ReportStage13Failure(4);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
            ReportStage14Failure(Stage14Diagnostic::Stage14Failure::InstallFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
            ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
            ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
            ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
            ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
            ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
            ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
            ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
            ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
            ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
            ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
            ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
            ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
            ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
            ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
            ReportStage128Failure(1);
#endif
            svcExitThread();
#endif
        }
        if (scan.status == TargetModuleScanStatus::Found && scan.module.has_value()) {
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
            MarkStartupProbeStage8Gate(kStartupProbeStage8ModuleFound);
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
            ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::Found, 0);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
            const Stage11InstallResult install = TryInstallRomfsSentinelDiagnostic(*scan.module);
            if (install == Stage11InstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
                svcExitThread();
            } else {
                stage11CandidateSeen = true;
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
            const Stage12InstallResult install = TryInstallRomfsLuaDiagnostic(*scan.module);
            if (install == Stage12InstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
                svcExitThread();
            } else {
                stage11CandidateSeen = true;
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
            const Stage13InstallResult install = TryInstallManifestRequireDiagnostic(*scan.module);
            if (install == Stage13InstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
                svcExitThread();
            } else {
                stage11CandidateSeen = true;
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
            const LoadConfigsResetDiagnosticInstallResult install =
                TryInstallManagerLoadConfigsResetDiagnostic(*scan.module);
            if (install == LoadConfigsResetDiagnosticInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                switch (install) {
                case LoadConfigsResetDiagnosticInstallResult::LoadConfigsInstructionOrRelayMismatch:
                case LoadConfigsResetDiagnosticInstallResult::RelaySlotNotEmpty:
                case LoadConfigsResetDiagnosticInstallResult::RelayPublishFailed:
                    ReportStage9Failure(2);
                case LoadConfigsResetDiagnosticInstallResult::ResetInstructionOrMappingMismatch:
                    ReportStage9Failure(3);
                case LoadConfigsResetDiagnosticInstallResult::Success:
                    UNREACHABLE;
                }
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
            const LoadConfigsDiagnosticInstallResult install =
                TryInstallManagerLoadConfigsDiagnostic(*scan.module);
            if (install == LoadConfigsDiagnosticInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                switch (install) {
                case LoadConfigsDiagnosticInstallResult::InstructionOrRelayMismatch:
                    ReportStage8Failure(2);
                case LoadConfigsDiagnosticInstallResult::RelaySlotNotEmpty:
                case LoadConfigsDiagnosticInstallResult::RelayPublishFailed:
                    ReportStage8Failure(3);
                case LoadConfigsDiagnosticInstallResult::Success:
                    UNREACHABLE;
                }
            }
#else
#if !defined(EXL_DIAGNOSTIC_STAGE)
            // Render and collectible interception are optional capabilities. The
            // Update hook is the only prerequisite for loading a manifest Mod;
            // missing optional IPS patches must not disable generic callbacks.
            // The layered build installs all three through HookInstallService
            // inside TryInstallDefaultManifestMod, so it must not install the
            // optional hooks twice.
#if !defined(EXL_LAYERED_RUNTIME)
            static_cast<void>(TryInstallManagerRenderHook(*scan.module));
            static_cast<void>(TryInstallPreGetCollectibleRelay(*scan.module));
#endif
            TestRunObserver::Mark(TestRunObserver::State::ManifestInstallEntered);
            const DefaultManifestInstallResult install = TryInstallDefaultManifestMod(*scan.module);
            TestRunObserver::Mark(TestRunObserver::State::ManifestInstallReturned,
                                  static_cast<std::uint32_t>(install));
            if (install == DefaultManifestInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
                MarkStartupProbeStage8Gate(kStartupProbeStage8InstallSucceeded);
                AllowDefaultManifestInitialization();
#endif
#if defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
                ManagerUpdateHookAudit::RecordWorkerState(
                    ManagerUpdateHookAudit::WorkerState::InstallSuccess, 0);
                for (u32 sample = 1; sample < kManagerHookAuditSampleLimit; ++sample) {
                    svcSleepThread(kTargetModuleScanIntervalNanoseconds);
                    const bool finalSample = sample + 1 == kManagerHookAuditSampleLimit;
                    ManagerUpdateHookAudit::Sample(finalSample);
                    ManagerUpdateHookAudit::Flush();
                }
#endif
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
                ReportStartupProbeStage8Gates();
#else
#if defined(EXL_PERSISTENCE_TRACE)
                ManagerUpdateHookAudit::RecordWorkerState(
                    ManagerUpdateHookAudit::WorkerState::InstallFailure, static_cast<u32>(install));
#if !defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
                ReportTraceDefaultInstallFailure(install);
#endif
#endif
#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
                PersistenceEventJournal::Mark(
                    PersistenceEventJournal::Event::CallbackFailed,
                    PersistenceEventJournal::Operation::None,
                    static_cast<u32>(install));
                PersistenceEventJournal::FlushBounded();
#endif
#endif
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
            const Stage118InstallResult install =
                TryInstallStage118ManagedFileWriteDiagnostic(*scan.module);
            if (install == Stage118InstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage118Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
            const Stage127SaveLoadInstallResult install = TryInstallStage127SaveLoadDiagnostic(*scan.module);
            if (install == Stage127SaveLoadInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage127Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
            const Stage128SaveDataManagerInstallResult install =
                TryInstallStage128SaveDataManagerDiagnostic(*scan.module);
            if (install == Stage128SaveDataManagerInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage128Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
            const Stage102ChangeRoomInstallResult install =
                TryInstallStage102ChangeRoomDiagnostic(*scan.module);
            if (install == Stage102ChangeRoomInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage102Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
            const Stage104RoomKeyInstallResult install =
                TryInstallStage104RoomKeyDiagnostic(*scan.module);
            if (install == Stage104RoomKeyInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage104Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
            const Stage112DescriptorReentryInstallResult install =
                TryInstallStage112DescriptorReentryDiagnostic(*scan.module);
            if (install == Stage112DescriptorReentryInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage112Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
            const Stage108LifecycleInstallResult install =
                TryInstallStage108LifecycleDiagnostic(*scan.module);
            if (install == Stage108LifecycleInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage108Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
            const Stage109RestartInstallResult install = TryInstallStage109RestartDiagnostic(*scan.module);
            if (install == Stage109RestartInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage109Failure(static_cast<u32>(install));
            }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
            const Stage110ChangeRoomInstallResult roomInstall = TryInstallStage110ChangeRoomDiagnostic(*scan.module);
            if (roomInstall != Stage110ChangeRoomInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage110Failure(static_cast<u32>(roomInstall));
            }
            const Stage110LifecycleInstallResult lifecycleInstall = TryInstallStage110LifecycleDiagnostic(*scan.module);
            if (lifecycleInstall == Stage110LifecycleInstallResult::Success) {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                ReportStage110Failure(0x10u | static_cast<u32>(lifecycleInstall));
            }
#else
            const HookInstallResult install = TryInstallManagerUpdateHook(*scan.module);
            if (install == HookInstallResult::Success) {
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
                const auto stage48Install = TryInstallStage48MusicReplayProbe(*scan.module);
                if (stage48Install != Stage48MusicReplayProbeInstallResult::Success) {
                    SetRuntimeState({.moduleFound = true, .hookAttempted = true});
                    ReportStage48Failure(static_cast<u32>(stage48Install));
                }
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48)
                if (TryInstallManagerRenderHook(*scan.module) != RenderHookInstallResult::Success) {
                    SetRuntimeState({.moduleFound = true, .hookAttempted = true});
#if EXL_DIAGNOSTIC_STAGE == 15
                    ReportStage15Failure(1);
#elif EXL_DIAGNOSTIC_STAGE == 16
                    ReportStage16Failure(1);
#elif EXL_DIAGNOSTIC_STAGE == 17
                    ReportStage17Failure(1);
#elif EXL_DIAGNOSTIC_STAGE == 45
                    ReportStage45Failure(1);
#else
                    ReportStage48Failure(1);
#endif
                }
#endif
                SetRuntimeState({.moduleFound = true, .hookAttempted = true, .hookSucceeded = true});
#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 7 || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48
                TestRunObserver::Mark(TestRunObserver::State::LuaInitializeEntered);
                const LuaRuntime::LuaInitResult luaResult = LuaRuntime::Initialize();
                TestRunObserver::Mark(TestRunObserver::State::LuaInitializeReturned,
                                      static_cast<std::uint32_t>(luaResult));
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStartupProbeStage8Gates();
                }
                MarkStartupProbeStage8Gate(kStartupProbeStage8LuaInitialized);
                ReportStartupProbeStage8Gates();
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 7
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage7LuaInitializationFailure(luaResult);
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage14LuaInitializationFailure(luaResult);
                }
                MarkStage14DiagnosticReady();
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage15Failure(2);
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage16Failure(2);
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage17Failure(2);
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage45Failure(2);
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 48
                if (luaResult != LuaRuntime::LuaInitResult::Success) {
                    ReportStage48Failure(2);
                }
#else
                static_cast<void>(luaResult);
#endif
#endif
            } else {
                SetRuntimeState({.moduleFound = true, .hookAttempted = true});
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
                if (install == HookInstallResult::InstructionMismatch) {
                    ReportStage6Failure(4);
                }
                switch (install) {
                case HookInstallResult::RelayPatchMismatch:
                    ReportStage6Failure(15);
                case HookInstallResult::RelaySlotNotEmpty:
                    ReportStage6Failure(16);
                case HookInstallResult::RelayPublishFailed:
                    ReportStage6Failure(17);
                case HookInstallResult::GameOwnerSlotMismatch:
                    ReportStage6Failure(37);
                case HookInstallResult::GameOwnerSlotPublishFailed:
                    ReportStage6Failure(38);
                case HookInstallResult::GameIsPausedThunkMismatch:
                    ReportStage6Failure(43);
                case HookInstallResult::GameIsPausedThunkPublishFailed:
                    ReportStage6Failure(44);
                case HookInstallResult::Success:
                case HookInstallResult::InstructionMismatch:
                    UNREACHABLE;
                }
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
                ReportStage14Failure(Stage14Diagnostic::Stage14Failure::InstallFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
                ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
                ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
                ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
                ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
                if (install == HookInstallResult::ManagerIsActionTriggeredMismatch) {
                    ReportStage88Failure(2);
                }
                if (install == HookInstallResult::Stage88GameBindingsMismatch) {
                    ReportStage88Failure(3);
                }
                ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
                if (install == HookInstallResult::ManagerIsActionTriggeredMismatch) {
                    ReportStage89Failure(2);
                }
                if (install == HookInstallResult::Stage89GameBindingsMismatch) {
                    ReportStage89Failure(3);
                }
                ReportStage89Failure(1);
#endif
            }
#endif
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) || \
    (EXL_DIAGNOSTIC_STAGE != 11 && EXL_DIAGNOSTIC_STAGE != 12 && EXL_DIAGNOSTIC_STAGE != 13)
            svcExitThread();
#endif
        }
        if (attempt + 1 < kTargetModuleScanAttemptLimit) {
            svcSleepThread(kTargetModuleScanIntervalNanoseconds);
        }
    }
    SetRuntimeState({.fatalFailure = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
    ReportStartupProbeStage8Gates();
#else
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    ManagerUpdateHookAudit::RecordWorkerState(ManagerUpdateHookAudit::WorkerState::TimedOut, 0);
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
    ReportStage6Failure(2);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
    ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
    ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
    ReportStage11Failure(stage11CandidateSeen ? 9 : 6);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
    ReportStage12Failure(stage11CandidateSeen ? 9 : 6);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
    ReportStage13Failure(stage11CandidateSeen ? 9 : 6);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
    ReportStage14Failure(Stage14Diagnostic::Stage14Failure::ModuleNotFound);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
    ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
    ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
    ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
    ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
    ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
    ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
    ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
    ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
    ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
    ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
    ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
    ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
    ReportStage128Failure(1);
#endif
#endif
    svcExitThread();
}
#endif

}

#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
extern "C" NORETURN void ReportStartupProbeStage8ManifestResult(bool initialized, u32) {
    MarkStartupProbeStage8Gate(kStartupProbeStage8InstallSucceeded);
    if (initialized) {
        MarkStartupProbeStage8Gate(kStartupProbeStage8LuaInitialized);
    }
    ReportStartupProbeStage8Gates();
}
#endif

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
namespace {
constexpr u64 kStage6FailureMagic = 0x49534141435F4946ULL;
}

NORETURN void ReportStage6Failure(u32 status) {
    svcBreak(BreakReason_User, kStage6FailureMagic, (6ULL << 32) | status);
    svcExitProcess();
}
#endif

#if defined(EXL_STARTUP_PROBE_STAGE)
extern "C" void RunStartupProbeExlMainGate();
#endif

#if defined(EXL_LAYERED_RUNTIME)
// Third arming point for the diagnostics journal, and the earliest one. It can only
// succeed when the host plugin has already registered the file table, which is exactly
// the case this call exists to cover: the plugin may load before the game reaches its
// first Manager update, and the port now reads the table live, so a later attempt
// succeeds even if this one runs too early. Hidden visibility keeps the call direct.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_TryAttachDiagnostics();
// Records "this copy of the module reached its entry" so a plugin can tell which copy it
// is talking to. Kept hidden: it is called only from here.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_PublishRuntimeSelf(std::uintptr_t);
#endif

extern "C" void exl_main(void*, void*) {
    // First statement of the entry: the calling thread gets a libc thread pointer if it
    // has none. devkitA64 dereferences `[tpidrro_el0 + 0x1f8]` on every thread-pointer
    // read, the host plugin's own code included, and a game thread leaves that slot
    // zeroed.
    // 两个调用都只存在于分层构建的 SaltyNX 桥里（`saltynx_runtime_bridge.cpp` 的非分层副本
    // 是空 TU），所以必须与声明一起守卫 —— 未加守卫的非分层/诊断构建会报未声明（2026-09-13 修）。
#if defined(EXL_LAYERED_RUNTIME)
    IsaacModRuntime_EnsureThreadTls();
    IsaacModRuntime_PublishRuntimeSelf(reinterpret_cast<std::uintptr_t>(&exl_main));
#endif
    // 实验 E1（2026-09-15）：把运行时的**启动期工作整体推迟 15 秒**。
    // 目的：验证"我们的启动期动作与'系统继续装载游戏后续模块'抢时序"这条假设
    //       （崩溃点 `nn::ro::LoadModule → RoModule::BindVariables` 就在那个窗口里）。
    // 唯一变量就是这个等待——下面的逻辑一字未改。若这一版不再崩，说明窗口是真的；
    // 若照崩，说明与我们的代码无关，要回到系统/覆盖层那条线去查。
    svcSleepThread(15'000'000'000ULL);
    TestRunObserver::Mark(TestRunObserver::State::ExlMainEntered);
#if defined(EXL_LAYERED_RUNTIME)
    // Layered entry: the Composition Root records the entry boundary before the
    // legacy startup path runs. Control flow, addresses and side effects stay
    // exactly as they are in the non-layered build.
    static_cast<void>(isaac::runtime::RuntimeBootstrap::Start());
    IsaacModRuntime_TryAttachDiagnostics();
#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_FILEIO
    // First self-journal record of this session, written from the entry itself: it proves this
    // copy reached `exl_main` and reports whether the registered file table is visible here.
    // Probe builds only: the route it uses is measured dead on hardware (2026-09-11).
    //
    // 由 `PROBE_BREAK_FILEIO` 单独控制：这是探针包里最早、也是唯一在 `exl_main` 入口就走
    // 失效文件路由的调用点（2026-09-13 二分定位探针包启动故障）。
    IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalExlMainMarker);
#endif
#endif
#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
    PersistenceEventJournal::MarkAndFlush(
        PersistenceEventJournal::Event::RuntimeEntered);
#endif
#if defined(EXL_STARTUP_PROBE_STAGE)
    RunStartupProbeExlMainGate();
#endif
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    ManagerUpdateHookAudit::RecordWorkerState(
        ManagerUpdateHookAudit::WorkerState::RuntimeEntered, 0);
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
    MarkStartupProbeStage6Gate(kStartupProbeStage6RuntimeStateRecorded);
#elif defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
    MarkStartupProbeStage7Gate(kStartupProbeStage7RuntimeStateRecorded);
#endif
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && (EXL_DIAGNOSTIC_STAGE == 6 || EXL_DIAGNOSTIC_STAGE == 7 || EXL_DIAGNOSTIC_STAGE == 8 || EXL_DIAGNOSTIC_STAGE == 9 || EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 13 || EXL_DIAGNOSTIC_STAGE == 14 || EXL_DIAGNOSTIC_STAGE == 15 || EXL_DIAGNOSTIC_STAGE == 16 || EXL_DIAGNOSTIC_STAGE == 17 || EXL_DIAGNOSTIC_STAGE == 45 || EXL_DIAGNOSTIC_STAGE == 48 || EXL_DIAGNOSTIC_STAGE == 88 || EXL_DIAGNOSTIC_STAGE == 89 || EXL_DIAGNOSTIC_STAGE == 102 || EXL_DIAGNOSTIC_STAGE == 104 || EXL_DIAGNOSTIC_STAGE == 108 || EXL_DIAGNOSTIC_STAGE == 109 || EXL_DIAGNOSTIC_STAGE == 110 || EXL_DIAGNOSTIC_STAGE == 112 || EXL_DIAGNOSTIC_STAGE == 118 || EXL_DIAGNOSTIC_STAGE == 127 || EXL_DIAGNOSTIC_STAGE == 128)
    exl::util::impl::InitMemLayout();
    virtmemSetup();
#endif
    exl::hook::Initialize();
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
    MarkStartupProbeStage6Gate(kStartupProbeStage6HookInitialized);
#elif defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
    MarkStartupProbeStage7Gate(kStartupProbeStage7HookInitialized);
#endif
    u64 programId = 0;
    if (R_FAILED(svcGetInfo(&programId, InfoType_ProgramId, CUR_PROCESS_HANDLE, 0))) {
        g_StartupStatus.store(StartupStatus::TitleQueryFailed, std::memory_order_release);
        SetRuntimeState({.fatalFailure = true});
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        ReportStage6Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
        ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
        ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        ReportStage11Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        ReportStage12Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        ReportStage13Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        ReportStage14Failure(Stage14Diagnostic::Stage14Failure::StartupFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
        ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
        ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
        ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
        ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
        ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
        ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
        ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
        ReportStage128Failure(1);
#endif
    } else if (programId != kTargetTitleId) {
        g_StartupStatus.store(StartupStatus::TitleMismatch, std::memory_order_release);
        SetRuntimeState({.titleChecked = true, .titleOk = false});
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        ReportStage6Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
        ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
        ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        ReportStage11Failure(2);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        ReportStage12Failure(2);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        ReportStage13Failure(2);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        ReportStage14Failure(Stage14Diagnostic::Stage14Failure::StartupFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
        ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
        ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
        ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
        ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
        ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
        ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
        ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
        ReportStage128Failure(1);
#endif
    } else {
        g_StartupStatus.store(StartupStatus::TitleOk, std::memory_order_release);
        SetRuntimeState({.titleChecked = true, .titleOk = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
        MarkStartupProbeStage6Gate(kStartupProbeStage6TitleOk);
#elif defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
        MarkStartupProbeStage7Gate(kStartupProbeStage7TitleOk);
#endif
}
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
    void* workerEntry = reinterpret_cast<void*>(&StartupProbeWorker);
#else
    void* workerEntry = reinterpret_cast<void*>(&ModuleWorker);
#endif
    const Result createResult = svcCreateThread(&g_WorkerHandle, workerEntry, nullptr,
                                                g_WorkerStack + sizeof(g_WorkerStack), 0x3A, 2);
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    if (R_FAILED(createResult)) {
        ManagerUpdateHookAudit::RecordWorkerState(
            ManagerUpdateHookAudit::WorkerState::ThreadCreateFailed,
            static_cast<u32>(createResult));
    }
#endif
    if (R_FAILED(createResult)) {
        SetRuntimeState({.fatalFailure = true});
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
        ReportStartupProbeWorkerGate(
            g_StartupProbeStage7Gates.load(std::memory_order_acquire));
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        ReportStage6Failure(7);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
        ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
        ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        ReportStage11Failure(7);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        ReportStage12Failure(7);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        ReportStage13Failure(7);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        ReportStage14Failure(Stage14Diagnostic::Stage14Failure::WorkerFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
        ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
        ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
        ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
        ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
        ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
        ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
        ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
        ReportStage128Failure(1);
#endif
        return;
    }
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6
    MarkStartupProbeStage6Gate(kStartupProbeStage6ThreadCreated);
#elif defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
    MarkStartupProbeStage7Gate(kStartupProbeStage7ThreadCreated);
#endif
    const Result startResult = svcStartThread(g_WorkerHandle);
#if !defined(EXL_DIAGNOSTIC_STAGE) && defined(EXL_PERSISTENCE_TRACE) && \
    (!defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 8)
    if (R_FAILED(startResult)) {
        ManagerUpdateHookAudit::RecordWorkerState(
            ManagerUpdateHookAudit::WorkerState::ThreadStartFailed,
            static_cast<u32>(startResult));
    }
#endif
    if (R_FAILED(startResult)) {
        SetRuntimeState({.fatalFailure = true});
        CloseWorkerHandle();
#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
        ReportStartupProbeWorkerGate(
            g_StartupProbeStage7Gates.load(std::memory_order_acquire));
#endif
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6
        ReportStage6Failure(7);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8
        ReportStage8Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 9
        ReportStage9Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 11
        ReportStage11Failure(8);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12
        ReportStage12Failure(8);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
        ReportStage13Failure(8);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 14
        ReportStage14Failure(Stage14Diagnostic::Stage14Failure::WorkerFailed);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 15
        ReportStage15Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 16
        ReportStage16Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 17
        ReportStage17Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 45
        ReportStage45Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 88
        ReportStage88Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 89
        ReportStage89Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 102
        ReportStage102Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 104
        ReportStage104Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108
        ReportStage108Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 109
        ReportStage109Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110
        ReportStage110Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 112
        ReportStage112Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
        ReportStage118Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 127
        ReportStage127Failure(1);
#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 128
        ReportStage128Failure(1);
#endif
        return;
    }
    CloseWorkerHandle();
}

extern "C" NORETURN void exl_exception_entry() {
    EXL_ABORT("isaac-runtime-probe exception");
}

#endif
