#include "common.hpp"

#include "lib/nx/kernel/svc.h"
#include "program/setting.hpp"
#include "test_run_observer.hpp"

#if defined(EXL_STARTUP_PROBE_STAGE)
namespace {

constexpr u64 kStartupProbeMagic = 0x49534141435F5355ULL;

NORETURN void ReportStartupProbe(u32 reachedGate) {
    TestRunObserver::Mark(TestRunObserver::State::FinalReportEntered, reachedGate);
    svcBreak(BreakReason_User, kStartupProbeMagic,
             (static_cast<u64>(EXL_STARTUP_PROBE_STAGE) << 32) | reachedGate);
    svcExitProcess();
}

} // namespace
#endif

extern "C" {
    /* These magic symbols are provided by the linker.  */
    extern void (*__preinit_array_start []) (void) __attribute__((weak));
    extern void (*__preinit_array_end []) (void) __attribute__((weak));
    extern void (*__init_array_start []) (void) __attribute__((weak));
    extern void (*__init_array_end []) (void) __attribute__((weak));

    /* Exported by program. */
    extern void exl_main(void*, void*);
    /* Optionally exported by program. */
    __attribute__((weak)) extern void exl_init();

    #ifdef EXL_USE_FAKEHEAP

    char __fake_heap[exl::setting::HeapSize];

    void __init_heap() {
        extern char * fake_heap_start;
        extern char * fake_heap_end;

        fake_heap_start = __fake_heap;
        fake_heap_end   = __fake_heap + exl::setting::HeapSize;
    }
    
    #endif

    void __init_array(void) {
        size_t count;
        size_t i;

        count = __preinit_array_end - __preinit_array_start;
        for (i = 0; i < count; i++)
            __preinit_array_start[i] ();

        count = __init_array_end - __init_array_start;
        for (i = 0; i < count; i++)
            __init_array_start[i] ();
    }
    
    /* Called when loaded as a module with RTLD. */
    void exl_module_init() {
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 1
        ReportStartupProbe(1);
        #endif
        #ifdef EXL_USE_FAKEHEAP
        #if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE >= 3
        __init_heap();
        #endif
        #endif
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 2
        ReportStartupProbe(2);
        #endif
        #if !defined(EXL_DIAGNOSTIC_STAGE)
        exl_init();
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 3
        ReportStartupProbe(3);
        #endif
        __init_array();
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 4
        ReportStartupProbe(4);
        #endif
        #endif
        exl_main(NULL, NULL);
    }

    /* Called when loaded as the entrypoint of the process, like RTLD. */
    void exl_entrypoint_init(void* x0, void* x1) {
        #ifdef EXL_USE_FAKEHEAP
        #if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE >= 3
        __init_heap();
        #endif
        #endif
        #if !defined(EXL_DIAGNOSTIC_STAGE)
        exl_init();
        __init_array();
        #endif
        exl_main(x0, x1);
    }

    void RunStartupProbeExlMainGate() {
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 5
        ReportStartupProbe(5);
        #endif
    }

    NORETURN void ReportStartupProbeWorkerGate(u32 gates) {
        #if defined(EXL_STARTUP_PROBE_STAGE) && \
            (EXL_STARTUP_PROBE_STAGE == 6 || EXL_STARTUP_PROBE_STAGE == 7 || \
             EXL_STARTUP_PROBE_STAGE == 8)
        ReportStartupProbe(gates);
        #else
        static_cast<void>(gates);
        svcExitProcess();
        #endif
    }

    // Stage 7 keeps the complete ModuleWorker body reachable at compile time by
    // presenting this report as an ordinary call. The implementation still
    // terminates through ReportStartupProbe on hardware.
    void RunStartupProbeStage7WorkerGate(u32 gates) {
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7
        ReportStartupProbe(gates);
        #else
        static_cast<void>(gates);
        #endif
    }

    void RunStartupProbeStage8WorkerGate(u32 gates) {
        #if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8
        ReportStartupProbe(gates);
        #else
        static_cast<void>(gates);
        #endif
    }

    void exl_module_fini(void) {}

}

#include <lib/util/sys/soc.hpp>
#include <lib/util/sys/mem_layout.hpp>
#include <lib/patch/patcher_impl.hpp>
#include <lib/util/version.hpp>
#include <lib/reloc/reloc.hpp>

#include <lib/log/logger_mgr.hpp>
#include <program/loggers.hpp>

#include <lib/util/sys/mem_layout.hpp>

extern "C" void exl_init() {

#if !defined(EXL_DIAGNOSTIC_STAGE)

    /* Getting the SOC type in an application context is more effort than it's worth. */
    #ifndef EXL_AS_MODULE
    Logging.Log(EXL_LOG_PREFIX "Inferring SOC type...");
    exl::util::impl::InitSocType();
    #endif

    Logging.Log(EXL_LOG_PREFIX "Inspecting memory layout...");
    exl::util::impl::InitMemLayout();
    virtmemSetup();
    Logging.Log(EXL_LOG_PREFIX "Initializing patcher...");
    exl::patch::impl::InitPatcherImpl();
    exl::util::impl::InitVersion();
    exl::reloc::impl::Initialize();
#endif
}
