#include "saltynx_external_plugin_persistence_bridge.hpp"
#include "test_run_observer_protocol.hpp"

#include <switch.h>

#include <cstddef>
#include <cstdint>

extern "C" std::uintptr_t SaltySDCore_FindSymbol(const char*);

namespace {
using FindSymbol = std::uintptr_t (*)(const char*);
using RegisterFileApi = std::uint64_t (*)(std::uintptr_t, std::uintptr_t, std::uintptr_t,
                                          std::uintptr_t);
using FileOpen = void* (*)(const char*, const char*);
using FileWrite = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using FileClose = int (*)(void*);
using GetObserverState = std::uint64_t (*)(std::uint32_t*, std::size_t);
using GetTestRunSnapshot = std::uint64_t (*)(TestRunSnapshot*, std::size_t);

#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
static_assert(static_cast<std::uint64_t>(EXL_TEST_BUILD_ID) != 0,
              "EXL_TEST_BUILD_ID must be non-zero");
constexpr char kRegistrationPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-registration-events.bin";
enum class RegistrationEvent : std::uint32_t {
    SymbolsResolved = 1,
    RegisterReturned = 2,
};
#endif

[[gnu::used]] volatile FindSymbol g_find_symbol = &SaltySDCore_FindSymbol;

constexpr char kOpenSymbol[] = "SaltySDCore_fopen";
constexpr char kReadSymbol[] = "SaltySDCore_fread";
constexpr char kWriteSymbol[] = "SaltySDCore_fwrite";
constexpr char kCloseSymbol[] = "SaltySDCore_fclose";
constexpr char kRegisterSymbol[] = "IsaacModRuntime_RegisterSaltyFileApi";
constexpr char kObserverStateSymbol[] = "IsaacModRuntime_GetObserverState";
constexpr char kTestRunSnapshotSymbol[] = "IsaacModRuntime_GetTestRunSnapshot";
constexpr char kTracePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-bridge.bin";
constexpr char kObserverPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-observer.bin";
constexpr char kTestRunPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-run.bin";
constexpr std::uint64_t kFileApiRegistered = 0x49534141435F4631ULL;
constexpr std::uint64_t kObserverStateExported = 0x49534141435F4F31ULL;
constexpr s64 kObserverDelayNanoseconds = 3'000'000'000LL;
constexpr std::uint32_t kObserverDelayMilliseconds = 3000;
constexpr std::size_t kObserverStateWordCount = 6;

void WriteU32(std::uint8_t* target, std::uint32_t value) {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t length) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}

#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
void WriteU64(std::uint8_t* target, std::uint64_t value) {
    for (std::size_t index = 0; index < 8; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

void WriteRegistrationEvent(FileOpen open, FileWrite write, FileClose close,
                            RegistrationEvent event, std::uint8_t apiFlags,
                            bool registerResolved, std::uint64_t registerResult,
                            std::uint32_t sequence) {
    if (open == nullptr || write == nullptr || close == nullptr) return;
    std::uint8_t payload[48]{};
    const char magic[] = "ISAACRG1";
    for (std::size_t index = 0; index < 8; ++index) payload[index] = magic[index];
    WriteU32(payload + 8, 1);
    WriteU32(payload + 12, sizeof(payload));
    WriteU64(payload + 16, static_cast<std::uint64_t>(EXL_TEST_BUILD_ID));
    WriteU32(payload + 24, sequence);
    WriteU32(payload + 28, static_cast<std::uint32_t>(event));
    WriteU32(payload + 32, static_cast<std::uint32_t>(apiFlags) |
        (registerResolved ? (1U << 8) : 0));
    WriteU64(payload + 36, registerResult);
    WriteU32(payload + 44, Checksum(payload, 44));
    void* file = open(kRegistrationPath, "ab");
    if (file == nullptr) return;
    static_cast<void>(write(payload, 1, sizeof(payload), file));
    static_cast<void>(close(file));
}
#endif

#if !defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
void WriteTrace(FileOpen open, FileWrite write, FileClose close, std::uint8_t flags,
                std::uint8_t registerFlags) {
    std::uint8_t payload[16]{};
    const char magic[] = "ISAACBR1";
    for (std::size_t index = 0; index < 8; ++index) {
        payload[index] = static_cast<std::uint8_t>(magic[index]);
    }
    payload[8] = static_cast<std::uint8_t>(flags | registerFlags);

    void* file = open(kTracePath, "wb");
    if (file == nullptr) {
        return;
    }
    static_cast<void>(write(payload, 1, sizeof(payload), file));
    static_cast<void>(close(file));
}

void WriteObserver(FileOpen open, FileWrite write, FileClose close, const std::uint32_t* state,
                   std::uint32_t flags) {
    if (open == nullptr || write == nullptr || close == nullptr) {
        return;
    }
    void* file = open(kObserverPath, "wb");
    if (file == nullptr) return;

    std::uint8_t payload[48]{};
    const char magic[] = "ISAACOB1";
    for (std::size_t index = 0; index < 8; ++index) {
        payload[index] = static_cast<std::uint8_t>(magic[index]);
    }
    WriteU32(payload + 8, 1);
    WriteU32(payload + 12, flags);
    WriteU32(payload + 16, kObserverDelayMilliseconds);
    for (std::size_t index = 0; index < kObserverStateWordCount; ++index) {
        WriteU32(payload + 20 + index * sizeof(std::uint32_t), state[index]);
    }
    WriteU32(payload + 44, Checksum(payload, 44));
    static_cast<void>(write(payload, 1, sizeof(payload), file));
    static_cast<void>(close(file));
}

void WriteTestRunSnapshot(FileOpen open, FileWrite write, FileClose close,
                          GetTestRunSnapshot snapshotFn) {
    if (open == nullptr || write == nullptr || close == nullptr || snapshotFn == nullptr) return;
    TestRunSnapshot snapshot{};
    if (snapshotFn(&snapshot, sizeof(snapshot)) != kTestRunSnapshotMagic) return;
    void* file = open(kTestRunPath, "ab");
    if (file == nullptr) return;
    static_cast<void>(write(&snapshot, 1, sizeof(snapshot), file));
    static_cast<void>(close(file));
}
#endif
} // namespace

void RunSaltyNxPersistenceBridge() {
    const std::uintptr_t openAddress = g_find_symbol(kOpenSymbol);
    const std::uintptr_t readAddress = g_find_symbol(kReadSymbol);
    const std::uintptr_t writeAddress = g_find_symbol(kWriteSymbol);
    const std::uintptr_t closeAddress = g_find_symbol(kCloseSymbol);
    const auto open = reinterpret_cast<FileOpen>(openAddress);
    const auto write = reinterpret_cast<FileWrite>(writeAddress);
    const auto close = reinterpret_cast<FileClose>(closeAddress);
    std::uint8_t flags = 0;
    if (openAddress != 0) flags |= 1U;
    if (readAddress != 0) flags |= 2U;
    if (writeAddress != 0) flags |= 4U;
    if (closeAddress != 0) flags |= 8U;

#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)
    // Without open/write/close the registration log cannot distinguish a missing
    // write channel from a plugin that never ran; combine this result with saltysd.log.
    const auto registerApi = reinterpret_cast<RegisterFileApi>(g_find_symbol(kRegisterSymbol));
    WriteRegistrationEvent(open, write, close, RegistrationEvent::SymbolsResolved, flags,
                            registerApi != nullptr, 0, 1);
    if (registerApi == nullptr) return;
    const std::uint64_t result = registerApi(openAddress, readAddress, writeAddress, closeAddress);
    WriteRegistrationEvent(open, write, close, RegistrationEvent::RegisterReturned, flags,
                            true, result, 2);
    return;
#else

    const auto registerApi = reinterpret_cast<RegisterFileApi>(g_find_symbol(kRegisterSymbol));
    const auto observerState =
        reinterpret_cast<GetObserverState>(g_find_symbol(kObserverStateSymbol));
    const auto testRunSnapshot =
        reinterpret_cast<GetTestRunSnapshot>(g_find_symbol(kTestRunSnapshotSymbol));
    if (registerApi == nullptr) {
        if (open != nullptr && write != nullptr && close != nullptr) {
            WriteTrace(open, write, close, flags, 0);
        }
        return;
    }
    if (open != nullptr && write != nullptr && close != nullptr) {
        WriteTrace(open, write, close, flags, 0x10U);
    }
    const std::uint64_t result = registerApi(openAddress, readAddress, writeAddress, closeAddress);
    std::uint8_t registerFlags = 0x10U | 0x20U | (result == kFileApiRegistered ? 0x40U : 0);
    if (result == kFileApiRegistered && open != nullptr && write != nullptr && close != nullptr) {
        WriteTestRunSnapshot(open, write, close, testRunSnapshot);
        svcSleepThread(kObserverDelayNanoseconds);
        if (observerState != nullptr) {
            std::uint32_t state[kObserverStateWordCount]{};
            std::uint32_t observerFlags = 0x7U;
            if (observerState(state, kObserverStateWordCount) == kObserverStateExported) {
                observerFlags |= 0x8U;
            }
            WriteObserver(open, write, close, state, observerFlags);
            registerFlags |= 0x80U;
        }
        WriteTestRunSnapshot(open, write, close, testRunSnapshot);
    }
    if (open != nullptr && write != nullptr && close != nullptr) {
        WriteTrace(open, write, close, flags, registerFlags);
    }
#endif
}
