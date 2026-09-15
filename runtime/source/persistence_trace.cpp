#include "persistence_trace.hpp"

#if defined(EXL_PERSISTENCE_TRACE)

#include <array>
#include <atomic>

namespace PersistenceTrace {
namespace {

constexpr std::size_t kSnapshotBaseSize = 40;
constexpr std::uint32_t kVersion = 2;
constexpr std::array<std::uint8_t, 8> kMagic = {'I', 'S', 'A', 'A', 'C', 'P', 'T', '2'};

std::atomic<std::uintptr_t> g_open{0};
std::atomic<std::uintptr_t> g_write{0};
std::atomic<std::uintptr_t> g_close{0};
std::atomic<std::uint32_t> g_fileApiReady{0};
std::atomic<std::uint32_t> g_phase{0};
std::atomic<std::uint64_t> g_milestones{0};
std::array<std::atomic<std::uint64_t>, 9> g_details{};
std::atomic<std::uint32_t> g_callbackCount{0};
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
    const std::uint32_t phase = g_phase.load(std::memory_order_acquire);
    if (open != nullptr && write != nullptr && close != nullptr && phase != 0) {
        std::array<std::uint8_t, kSnapshotBaseSize + 9> snapshot{};
        for (std::size_t index = 0; index < kMagic.size(); ++index) {
            snapshot[index] = kMagic[index];
        }
        WriteU32(snapshot.data() + 8, kVersion);
        WriteU32(snapshot.data() + 12, phase);
        WriteU64(snapshot.data() + 16, g_milestones.load(std::memory_order_acquire));
        WriteU64(snapshot.data() + 24, g_details[phase - 1].load(std::memory_order_acquire));
        WriteU32(snapshot.data() + 32, g_callbackCount.load(std::memory_order_acquire));
        WriteU32(snapshot.data() + 36, Checksum(snapshot.data(), 36));

        void* file = open(kTracePath, "wb");
        if (file == nullptr) {
            g_dirty.store(1, std::memory_order_release);
        } else {
            const std::size_t length = kSnapshotBaseSize + phase;
            const bool wrote = write(snapshot.data(), 1, length, file) == length;
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

} // namespace

void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close) {
    if (open == nullptr || write == nullptr || close == nullptr) {
        return;
    }
    g_open.store(reinterpret_cast<std::uintptr_t>(open), std::memory_order_relaxed);
    g_write.store(reinterpret_cast<std::uintptr_t>(write), std::memory_order_relaxed);
    g_close.store(reinterpret_cast<std::uintptr_t>(close), std::memory_order_relaxed);
    g_fileApiReady.store(1, std::memory_order_release);
    const std::uint64_t bit = 1;
    g_milestones.fetch_or(bit, std::memory_order_release);
    std::uint32_t current = g_phase.load(std::memory_order_acquire);
    while (current < static_cast<std::uint32_t>(Phase::FileApiRegistered) &&
           !g_phase.compare_exchange_weak(current,
                                          static_cast<std::uint32_t>(Phase::FileApiRegistered),
                                          std::memory_order_acq_rel)) {
    }
    g_dirty.store(1, std::memory_order_release);
}

void Flush() { TryFlush(); }

void Mark(Phase phase, std::uint64_t detail) {
    const std::uint32_t value = static_cast<std::uint32_t>(phase);
    if (value == 0 || value > 9) {
        return;
    }
    const std::uint64_t bit = std::uint64_t{1} << (value - 1);
    bool changed = (g_milestones.fetch_or(bit, std::memory_order_release) & bit) == 0;
    if (g_details[value - 1].exchange(detail, std::memory_order_acq_rel) != detail) {
        changed = true;
    }

    std::uint32_t current = g_phase.load(std::memory_order_acquire);
    while (current < value) {
        if (g_phase.compare_exchange_weak(current, value, std::memory_order_acq_rel)) {
            changed = true;
            break;
        }
    }
    if (!changed) {
        return;
    }
    g_dirty.store(1, std::memory_order_release);
}

void RecordCallbackEntry() {
    g_callbackCount.fetch_add(1, std::memory_order_relaxed);
    g_dirty.store(1, std::memory_order_release);
    Mark(Phase::CallbackEntered);
}

} // namespace PersistenceTrace

#endif
