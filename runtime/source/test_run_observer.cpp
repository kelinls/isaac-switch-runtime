#include "test_run_observer.hpp"

#include "hook_install_report_state.hpp"  // Task 5：挂点安装报告经 reserved 高位回读

#include <atomic>
#include <cstddef>
#include <cstdint>

#ifndef EXL_TEST_BUILD_ID
#define EXL_TEST_BUILD_ID 0x202609090001ULL
#endif
static_assert(static_cast<std::uint64_t>(EXL_TEST_BUILD_ID) != 0,
              "EXL_TEST_BUILD_ID must be non-zero");

namespace {
std::atomic<std::uint64_t> g_stateDetail{0};
std::atomic<std::uint32_t> g_sequence{0};
// Diagnostics attach outcome, published through the snapshot's `reserved` word.
std::atomic<std::uint32_t> g_diagnosticsAttach{
    static_cast<std::uint32_t>(TestRunDiagnosticsAttach::NotAttempted)};

std::uint32_t Checksum(const TestRunSnapshot& snapshot) {
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(&snapshot);
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < offsetof(TestRunSnapshot, checksum); ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}
}

namespace TestRunObserver {
void Mark(State state, std::uint32_t detail) {
    const std::uint64_t stateDetail =
        (static_cast<std::uint64_t>(detail) << 32) | static_cast<std::uint32_t>(state);
    g_sequence.fetch_add(1, std::memory_order_seq_cst);
    g_stateDetail.store(stateDetail, std::memory_order_seq_cst);
}

void MarkDiagnosticsAttach(std::uint32_t state) noexcept {
    g_diagnosticsAttach.store(state, std::memory_order_seq_cst);
}
}

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetTestRunSnapshot(
    TestRunSnapshot* output, std::size_t outputSize) {
    if (output == nullptr || outputSize < sizeof(TestRunSnapshot)) return 0;

    TestRunSnapshot snapshot{};
    const std::uint64_t stateDetail = g_stateDetail.load(std::memory_order_seq_cst);
    snapshot.state = static_cast<std::uint32_t>(stateDetail);
    snapshot.detail = static_cast<std::uint32_t>(stateDetail >> 32);
    snapshot.sequence = g_sequence.load(std::memory_order_seq_cst);
    // `reserved` 低 8 位继续承载诊断 attach（既有，不动）；高 24 位写入挂点安装报告
    // （由 PackHookInstallReport 左移 8 位打包）。48 字节布局不变、启动状态机三字段不动。
    const isaac::runtime::HookInstallReportView hooks =
        isaac::runtime::HookInstallReportSnapshot();
    snapshot.reserved = g_diagnosticsAttach.load(std::memory_order_seq_cst) |
                        isaac::runtime::PackHookInstallReport(hooks);

    snapshot.magic = kTestRunSnapshotMagic;
    snapshot.version = kTestRunSnapshotVersion;
    snapshot.buildId = static_cast<std::uint64_t>(EXL_TEST_BUILD_ID);
#ifdef EXL_STARTUP_PROBE_STAGE
    snapshot.kind = static_cast<std::uint32_t>(TestRunKind::StartupProbe);
    snapshot.stage = EXL_STARTUP_PROBE_STAGE;
#elif defined(EXL_DIAGNOSTIC_STAGE)
    snapshot.kind = static_cast<std::uint32_t>(TestRunKind::Diagnostic);
    snapshot.stage = EXL_DIAGNOSTIC_STAGE;
#else
    snapshot.kind = static_cast<std::uint32_t>(TestRunKind::Default);
    snapshot.stage = 0;
#endif
    snapshot.checksum = Checksum(snapshot);
    *output = snapshot;
    return kTestRunSnapshotMagic;
}
