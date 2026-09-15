#pragma once

#include <cstddef>
#include <cstdint>

namespace ManagerUpdateHookAudit {

using OpenFn = void* (*)(const char*, const char*);
using WriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using CloseFn = int (*)(void*);

enum class WorkerState : std::uint32_t {
    Unseen = 0,
    Started = 1,
    Scanning = 2,
    Found = 3,
    InstallSuccess = 4,
    InstallFailure = 5,
    BuildMismatch = 6,
    TimedOut = 7,
    RuntimeEntered = 8,
    ThreadCreateFailed = 9,
    ThreadStartFailed = 10,
};

inline constexpr std::uint32_t kInstallRecorded = 1U << 0;
inline constexpr std::uint32_t kTargetEntryMatches = 1U << 1;
inline constexpr std::uint32_t kRelayMatches = 1U << 2;
inline constexpr std::uint32_t kSlotMatches = 1U << 3;
inline constexpr std::uint32_t kCallbackMappedRx = 1U << 4;
inline constexpr std::uint32_t kAuditCompleted = 1U << 5;
inline constexpr std::uint32_t kCallbackEntered = 1U << 6;

struct ObserverSnapshot {
    std::uint32_t workerState;
    std::uint32_t workerDetail;
    std::uint32_t scanAttempt;
    std::uint32_t sampleCount;
    std::uint32_t callbackCount;
    std::uint32_t flags;
};

inline constexpr std::size_t kObserverSnapshotWordCount = 6;

constexpr char kAuditPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-hook-audit.bin";

#if defined(EXL_PERSISTENCE_TRACE)
void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close);
void Flush();
void RecordWorkerState(WorkerState state, std::uint32_t detail);
void RecordScanAttempt(std::uint32_t attempt);
void RecordInstall(std::uintptr_t moduleBase, std::uintptr_t target,
                   std::uintptr_t relay, std::uintptr_t slot,
                   std::uintptr_t callback, bool callbackMappedRx);
void Sample(bool finalSample);
void RecordCallbackEntry();
ObserverSnapshot CaptureObserverSnapshot();
#else
inline void ConfigureFileApi(OpenFn, WriteFn, CloseFn) {}
inline void Flush() {}
inline void RecordWorkerState(WorkerState, std::uint32_t) {}
inline void RecordScanAttempt(std::uint32_t) {}
inline void RecordInstall(std::uintptr_t, std::uintptr_t, std::uintptr_t,
                          std::uintptr_t, std::uintptr_t, bool) {}
inline void Sample(bool) {}
inline void RecordCallbackEntry() {}
inline ObserverSnapshot CaptureObserverSnapshot() { return {}; }
#endif

} // namespace ManagerUpdateHookAudit
