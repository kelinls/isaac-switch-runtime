#pragma once

#include <cstddef>
#include <cstdint>

namespace PersistenceEventJournal {

using OpenFn = void* (*)(const char*, const char*);
using WriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using CloseFn = int (*)(void*);

enum class Event : std::uint32_t {
    FileApiAccepted = 1,
    ManagerCallbackEntered,
    OriginalReturned,
    ManifestEntered,
    ManifestReturned,
    GateEvaluated,
    DispatchEntered,
    DispatchReturned,
    OperationEntered,
    OperationReturned,
    CallbackFailed,
    FlushConfirmation,
    QueueOverflow,
    RuntimeEntered,
    WorkerEntered,
};

enum class Operation : std::uint32_t {
    None = 0,
    SaveData = 1,
    LoadData = 2,
    HasData = 3,
    RemoveData = 4,
};

enum class FlushResult : std::uint32_t {
    NeverAttempted = 0,
    Busy,
    NoApi,
    OpenFailed,
    ShortWrite,
    CloseFailed,
    Succeeded,
};

constexpr std::size_t kRecordSize = 64;
constexpr std::size_t kQueueCapacity = 32;
constexpr std::size_t kMaxRecordsPerFlush = 8;
constexpr std::size_t kMaxPendingDrainAttempts = 8;
constexpr char kEventPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-persistence-events.bin";

#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close);
bool FileApiConfigured();
bool HasPending();
void Mark(Event event, Operation operation = Operation::None, std::uint32_t result = 0,
          std::uint64_t detail = 0);
bool MarkOnce(Event event, Operation operation = Operation::None, std::uint32_t result = 0,
              std::uint64_t detail = 0);
bool MarkIfChanged(Event event, Operation operation = Operation::None, std::uint32_t result = 0,
                   std::uint64_t detail = 0);
FlushResult MarkAndFlush(Event event, Operation operation = Operation::None,
                         std::uint32_t result = 0, std::uint64_t detail = 0);
FlushResult FlushBounded();
FlushResult DrainPendingBounded(std::size_t maxAttempts = kMaxPendingDrainAttempts);
#else
inline void ConfigureFileApi(OpenFn, WriteFn, CloseFn) {}
inline bool FileApiConfigured() { return false; }
inline bool HasPending() { return false; }
inline void Mark(Event, Operation = Operation::None, std::uint32_t = 0, std::uint64_t = 0) {}
inline bool MarkOnce(Event, Operation = Operation::None, std::uint32_t = 0,
                     std::uint64_t = 0) { return false; }
inline bool MarkIfChanged(Event, Operation = Operation::None, std::uint32_t = 0,
                          std::uint64_t = 0) { return false; }
inline FlushResult MarkAndFlush(Event, Operation = Operation::None, std::uint32_t = 0,
                                std::uint64_t = 0) {
    return FlushResult::NeverAttempted;
}
inline FlushResult FlushBounded() { return FlushResult::NeverAttempted; }
inline FlushResult DrainPendingBounded(std::size_t = kMaxPendingDrainAttempts) {
    return FlushResult::NeverAttempted;
}
#endif

} // namespace PersistenceEventJournal
