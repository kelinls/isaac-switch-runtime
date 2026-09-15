#pragma once

#include <cstddef>
#include <cstdint>

namespace PersistenceTrace {

using OpenFn = void* (*)(const char*, const char*);
using WriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using CloseFn = int (*)(void*);

enum class Phase : std::uint32_t {
    FileApiRegistered = 1,
    CallbackEntered = 2,
    OriginalReturned = 3,
    ManifestStarted = 4,
    ManifestCompleted = 5,
    PersistenceGateReady = 6,
    DispatchStarted = 7,
    DispatchReturned = 8,
    CallbackFailed = 9,
};

constexpr char kTracePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-trace.bin";
constexpr std::uint64_t kFailureDetailMask = std::uint64_t{1} << 63;

#if defined(EXL_PERSISTENCE_TRACE)
void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close);
void Flush();
void Mark(Phase phase, std::uint64_t detail = 0);
void RecordCallbackEntry();
#else
inline void ConfigureFileApi(OpenFn, WriteFn, CloseFn) {}
inline void Flush() {}
inline void Mark(Phase, std::uint64_t = 0) {}
inline void RecordCallbackEntry() {}
#endif

} // namespace PersistenceTrace
