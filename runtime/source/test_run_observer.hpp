#pragma once

#include "test_run_observer_protocol.hpp"

#include <cstdint>

namespace TestRunObserver {

enum class State : std::uint32_t {
    Unseen = 0,
    ExlMainEntered = 1,
    ModuleWorkerEntered = 2,
    TitleStateRead = 3,
    ModuleScanEntered = 4,
    ModuleScanReturned = 5,
    ManifestInstallEntered = 6,
    ManifestInstallReturned = 7,
    LuaInitializeEntered = 8,
    LuaInitializeReturned = 9,
    FinalReportEntered = 10,
};

void Mark(State state, std::uint32_t detail = 0);

// Records what the diagnostics journal did when it tried to arm itself. Exposed
// through the snapshot's `reserved` word, which the host plugin already writes to
// `isaac-runtime-run.bin`, so the outcome is observable on hardware without
// changing the snapshot layout.
void MarkDiagnosticsAttach(std::uint32_t state) noexcept;
}

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetTestRunSnapshot(
    TestRunSnapshot* output, std::size_t outputSize);
