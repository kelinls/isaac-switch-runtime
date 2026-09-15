#if defined(EXL_DIAGNOSTIC_STAGE)

#include "lib.hpp"
#include "fs_ipc.h"
#include "runtime_constants.hpp"
#include "lib/nx/kernel/svc.h"
#include "lib/nx/kernel/virtmem.h"

namespace {

constexpr u64 kDiagnosticSuccessMagic = 0x49534141435F4449ULL;
constexpr u64 kDiagnosticExceptionMagic = 0x49534141435F4558ULL;
constexpr u64 kDiagnosticFsMagic = 0x49534141435F4653ULL;

#if EXL_DIAGNOSTIC_STAGE != 4 && EXL_DIAGNOSTIC_STAGE != 5
NORETURN void FinishStage() {
    svcBreak(BreakReason_User, kDiagnosticSuccessMagic, EXL_DIAGNOSTIC_STAGE);
    svcExitProcess();
}
#endif

#if EXL_DIAGNOSTIC_STAGE == 4 || EXL_DIAGNOSTIC_STAGE == 5
NORETURN void FinishFilesystemDiagnostic(RuntimeFsDiagnosticResult diagnostic) {
    const u64 detail = (static_cast<u64>(diagnostic.stage) << 32) | diagnostic.result;
    svcBreak(BreakReason_User, kDiagnosticFsMagic, detail);
    svcExitProcess();
}
#endif

#if EXL_DIAGNOSTIC_STAGE == 5
alignas(0x1000) u8 g_FsDiagnosticWorkerStack[0x4000];

NORETURN void FsDiagnosticWorker(void*) {
    svcSleepThread(12'000'000'000);
    FinishFilesystemDiagnostic(RuntimeFsLogDiagnose(kLogDirectory, kLogPath));
}

void StartDelayedFilesystemDiagnostic() {
    Handle worker = INVALID_HANDLE;
    Result result = svcCreateThread(&worker, reinterpret_cast<void*>(&FsDiagnosticWorker), nullptr,
                                    g_FsDiagnosticWorkerStack + sizeof(g_FsDiagnosticWorkerStack), 0x3A, 2);
    if (R_FAILED(result)) {
        FinishFilesystemDiagnostic({9, static_cast<u32>(result)});
    }
    result = svcStartThread(worker);
    svcCloseHandle(worker);
    if (R_FAILED(result)) {
        FinishFilesystemDiagnostic({9, static_cast<u32>(result)});
    }
}
#endif

void RunStage() {
#if EXL_DIAGNOSTIC_STAGE >= 1
    exl::util::impl::InitMemLayout();
#endif
#if EXL_DIAGNOSTIC_STAGE >= 2
    virtmemSetup();
#endif
#if EXL_DIAGNOSTIC_STAGE >= 3
    exl::hook::Initialize();
#endif
}

}

extern "C" void exl_main(void*, void*) {
    RunStage();
#if EXL_DIAGNOSTIC_STAGE == 4
    FinishFilesystemDiagnostic(RuntimeFsLogDiagnose(kLogDirectory, kLogPath));
#elif EXL_DIAGNOSTIC_STAGE == 5
    StartDelayedFilesystemDiagnostic();
#else
    FinishStage();
#endif
}

extern "C" NORETURN void exl_exception_entry() {
    svcBreak(BreakReason_User, kDiagnosticExceptionMagic, EXL_DIAGNOSTIC_STAGE);
    svcExitProcess();
}

#endif
