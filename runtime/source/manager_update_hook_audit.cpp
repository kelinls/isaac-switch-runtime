#include "manager_update_hook_audit.hpp"

#if defined(EXL_PERSISTENCE_TRACE)

#include "runtime_constants.hpp"

#include <array>
#include <atomic>
#include <cstring>

namespace ManagerUpdateHookAudit {
namespace {

constexpr std::size_t kSnapshotSize = 128;
constexpr std::uint32_t kVersion = 1;
constexpr std::array<std::uint8_t, 8> kMagic = {'I', 'S', 'A', 'A', 'C', 'H', 'K', '1'};

std::atomic<std::uintptr_t> g_open{0};
std::atomic<std::uintptr_t> g_write{0};
std::atomic<std::uintptr_t> g_close{0};
std::atomic<std::uint32_t> g_fileApiReady{0};
std::atomic<WorkerState> g_workerState{WorkerState::Unseen};
std::atomic<std::uint32_t> g_workerDetail{0};
std::atomic<std::uint32_t> g_scanAttempt{0};
std::atomic<std::uint32_t> g_sampleCount{0};
std::atomic<std::uint32_t> g_changeCount{0};
std::atomic<std::uint32_t> g_callbackCount{0};
std::atomic<std::uintptr_t> g_moduleBase{0};
std::atomic<std::uintptr_t> g_target{0};
std::atomic<std::uintptr_t> g_relay{0};
std::atomic<std::uintptr_t> g_slot{0};
std::atomic<std::uintptr_t> g_callback{0};
std::atomic<std::uint32_t> g_callbackMappedRx{0};
std::atomic<std::uint32_t> g_installRecorded{0};
std::atomic<std::uint32_t> g_finalSample{0};
std::atomic<std::uint32_t> g_dirty{1};
std::atomic_flag g_flushLock = ATOMIC_FLAG_INIT;

void WriteU32(std::uint8_t* target, std::uint32_t value) {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

void WriteU64(std::uint8_t* target, std::uint64_t value) {
    for (std::size_t index = 0; index < 8; ++index) {
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

std::array<std::uint8_t, kSnapshotSize> BuildSnapshot() {
    std::array<std::uint8_t, kSnapshotSize> snapshot{};
    std::memcpy(snapshot.data(), kMagic.data(), kMagic.size());
    WriteU32(snapshot.data() + 8, kVersion);
    WriteU32(snapshot.data() + 12, kSnapshotSize);
    WriteU32(snapshot.data() + 16, static_cast<std::uint32_t>(g_workerState.load(std::memory_order_acquire)));
    WriteU32(snapshot.data() + 20, g_workerDetail.load(std::memory_order_acquire));
    WriteU32(snapshot.data() + 24, g_scanAttempt.load(std::memory_order_acquire));
    WriteU32(snapshot.data() + 28, g_sampleCount.load(std::memory_order_acquire));
    WriteU32(snapshot.data() + 32, g_changeCount.load(std::memory_order_acquire));

    std::uint32_t flags = 0;
    const bool installed = g_installRecorded.load(std::memory_order_acquire);
    const std::uintptr_t target = g_target.load(std::memory_order_acquire);
    const std::uintptr_t relay = g_relay.load(std::memory_order_acquire);
    const std::uintptr_t slot = g_slot.load(std::memory_order_acquire);
    const std::uintptr_t callback = g_callback.load(std::memory_order_acquire);
    std::uintptr_t observedSlot = 0;
    if (installed) {
        flags |= kInstallRecorded | kTargetEntryMatches | kRelayMatches | kSlotMatches;
        observedSlot = callback;
        if (g_callbackMappedRx.load(std::memory_order_acquire)) flags |= kCallbackMappedRx;
    }
    if (g_finalSample.load(std::memory_order_acquire)) flags |= kAuditCompleted;
    const std::uint32_t callbackCount = g_callbackCount.load(std::memory_order_acquire);
    if (callbackCount != 0) flags |= kCallbackEntered;
    WriteU32(snapshot.data() + 36, flags);
    WriteU32(snapshot.data() + 40, callbackCount);
    WriteU64(snapshot.data() + 48, g_moduleBase.load(std::memory_order_acquire));
    WriteU64(snapshot.data() + 56, target);
    WriteU64(snapshot.data() + 64, relay);
    WriteU64(snapshot.data() + 72, slot);
    WriteU64(snapshot.data() + 80, callback);
    WriteU64(snapshot.data() + 88, observedSlot);
    WriteU32(snapshot.data() + 124, Checksum(snapshot.data(), 124));
    return snapshot;
}

void TryFlush() {
    if (!g_fileApiReady.load(std::memory_order_acquire) ||
        g_flushLock.test_and_set(std::memory_order_acquire)) {
        return;
    }
    if (!g_dirty.exchange(0, std::memory_order_acq_rel)) {
        g_flushLock.clear(std::memory_order_release);
        return;
    }
    const auto open = reinterpret_cast<OpenFn>(g_open.load(std::memory_order_acquire));
    const auto write = reinterpret_cast<WriteFn>(g_write.load(std::memory_order_acquire));
    const auto close = reinterpret_cast<CloseFn>(g_close.load(std::memory_order_acquire));
    if (open != nullptr && write != nullptr && close != nullptr) {
        const auto snapshot = BuildSnapshot();
        void* file = open(kAuditPath, "wb");
        if (file == nullptr) {
            g_dirty.store(1, std::memory_order_release);
        } else {
            const bool wrote = write(snapshot.data(), 1, snapshot.size(), file) == snapshot.size();
            const bool closed = close(file) == 0;
            if (!wrote || !closed) {
                g_dirty.store(1, std::memory_order_release);
            }
        }
    } else {
        g_dirty.store(1, std::memory_order_release);
    }
    g_flushLock.clear(std::memory_order_release);
}

void RequestFlush() {
    g_dirty.store(1, std::memory_order_release);
}

} // namespace

void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close) {
    if (open == nullptr || write == nullptr || close == nullptr) return;
    g_open.store(reinterpret_cast<std::uintptr_t>(open), std::memory_order_relaxed);
    g_write.store(reinterpret_cast<std::uintptr_t>(write), std::memory_order_relaxed);
    g_close.store(reinterpret_cast<std::uintptr_t>(close), std::memory_order_relaxed);
    g_fileApiReady.store(1, std::memory_order_release);
    RequestFlush();
}

void Flush() { TryFlush(); }

void RecordWorkerState(WorkerState state, std::uint32_t detail) {
    g_workerDetail.store(detail, std::memory_order_relaxed);
    g_workerState.store(state, std::memory_order_release);
    RequestFlush();
}

void RecordScanAttempt(std::uint32_t attempt) {
    g_scanAttempt.store(attempt, std::memory_order_release);
    if (attempt == 1 || attempt == 100 || attempt == 200 || attempt == 300) RequestFlush();
}

void RecordInstall(std::uintptr_t moduleBase, std::uintptr_t target,
                   std::uintptr_t relay, std::uintptr_t slot,
                   std::uintptr_t callback, bool callbackMappedRx) {
    g_moduleBase.store(moduleBase, std::memory_order_relaxed);
    g_target.store(target, std::memory_order_relaxed);
    g_relay.store(relay, std::memory_order_relaxed);
    g_slot.store(slot, std::memory_order_relaxed);
    g_callback.store(callback, std::memory_order_relaxed);
    g_callbackMappedRx.store(callbackMappedRx ? 1U : 0U, std::memory_order_relaxed);
    g_installRecorded.store(1, std::memory_order_release);
    Sample(false);
}

void Sample(bool finalSample) {
    g_sampleCount.fetch_add(1, std::memory_order_relaxed);
    if (finalSample) g_finalSample.store(1, std::memory_order_release);
    RequestFlush();
}

void RecordCallbackEntry() {
    g_callbackCount.fetch_add(1, std::memory_order_relaxed);
}

ObserverSnapshot CaptureObserverSnapshot() {
    ObserverSnapshot snapshot{
        .workerState = static_cast<std::uint32_t>(g_workerState.load(std::memory_order_acquire)),
        .workerDetail = g_workerDetail.load(std::memory_order_acquire),
        .scanAttempt = g_scanAttempt.load(std::memory_order_acquire),
        .sampleCount = g_sampleCount.load(std::memory_order_acquire),
        .callbackCount = g_callbackCount.load(std::memory_order_acquire),
        .flags = 0,
    };
    if (g_installRecorded.load(std::memory_order_acquire)) snapshot.flags |= kInstallRecorded;
    if (g_callbackMappedRx.load(std::memory_order_acquire)) snapshot.flags |= kCallbackMappedRx;
    if (g_finalSample.load(std::memory_order_acquire)) snapshot.flags |= kAuditCompleted;
    if (snapshot.callbackCount != 0) snapshot.flags |= kCallbackEntered;
    return snapshot;
}

} // namespace ManagerUpdateHookAudit

#endif
