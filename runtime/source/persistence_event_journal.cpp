#include "persistence_event_journal.hpp"

#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)

#include <array>
#include <atomic>

#ifndef EXL_TEST_BUILD_ID
#define EXL_TEST_BUILD_ID 1ULL
#endif
static_assert(static_cast<std::uint64_t>(EXL_TEST_BUILD_ID) != 0,
              "EXL_TEST_BUILD_ID must be non-zero");

namespace PersistenceEventJournal {
namespace {

struct PendingRecord {
    std::array<std::uint8_t, kRecordSize> bytes{};
};

std::array<PendingRecord, kQueueCapacity> g_queue{};
std::size_t g_head = 0;
std::size_t g_count = 0;
std::atomic_flag g_queueLock = ATOMIC_FLAG_INIT;
std::atomic_flag g_flushLock = ATOMIC_FLAG_INIT;
std::atomic<std::uintptr_t> g_open{0};
std::atomic<std::uintptr_t> g_write{0};
std::atomic<std::uintptr_t> g_close{0};
std::atomic<std::uint32_t> g_sequence{0};
std::atomic<std::uint32_t> g_callbackCount{0};
// 状态字一律用 `std::atomic<std::uint32_t>` 的 `0/1`，**不用布尔型原子**：本项目的 AArch64
// `-Oz` 构建下，布尔型原子的 `load()` 会生成落在可执行文本边界之后的 PLT 跳板，真机表现为
// `Instruction Abort`、PC 恰为 `__text_end__`（见 `docs/问题与解决记录.md` 的「2026-09-04」
// 布尔原子 PLT 一节）。这条约束对两个源码根逐文件把守，见
// `runtime/tests/test_manager_update_hook_audit.py::test_runtime_source_contains_no_bool_atomics`。
std::atomic<std::uint32_t> g_overflow{0};
std::atomic<std::uint32_t> g_overflowPending{0};
std::atomic<std::uint32_t> g_fileApiReady{0};
std::atomic<std::uint32_t> g_pending{0};
std::atomic<std::uint32_t> g_pendingDrainBudget{0};
std::atomic<std::uint32_t> g_alignmentBroken{0};
std::atomic<std::uint32_t> g_onceMask{0};
std::atomic<std::uint64_t> g_lastChangedDetail{0};
std::atomic<std::uint32_t> g_hasChangedDetail{0};
std::atomic<std::uint32_t> g_lastFlush{static_cast<std::uint32_t>(FlushResult::NeverAttempted)};

constexpr std::uint32_t kPendingDrainBudget = 8;

bool TryLock(std::atomic_flag& lock) { return !lock.test_and_set(std::memory_order_acquire); }

void Unlock(std::atomic_flag& lock) { lock.clear(std::memory_order_release); }

void ArmPending(bool resetBudget) {
    const bool wasPending = g_pending.exchange(1, std::memory_order_acq_rel) != 0;
    if (resetBudget || !wasPending) {
        g_pendingDrainBudget.store(kPendingDrainBudget, std::memory_order_release);
    }
}

void RefreshPending() {
    if (!TryLock(g_queueLock)) return;
    const bool pending =
        g_count != 0 && g_alignmentBroken.load(std::memory_order_acquire) == 0;
    g_pending.store(pending ? 1U : 0U, std::memory_order_release);
    Unlock(g_queueLock);
}

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

void Encode(PendingRecord* record, Event event, Operation operation, std::uint32_t result,
            std::uint64_t detail) {
    auto& bytes = record->bytes;
    constexpr char magic[] = "ISAACPE1";
    for (std::size_t index = 0; index < 8; ++index) bytes[index] = magic[index];
    WriteU32(bytes.data() + 8, 1);
    WriteU32(bytes.data() + 12, kRecordSize);
    WriteU64(bytes.data() + 16, static_cast<std::uint64_t>(EXL_TEST_BUILD_ID));
    WriteU32(bytes.data() + 24, g_sequence.fetch_add(1, std::memory_order_relaxed) + 1);
    WriteU32(bytes.data() + 28, static_cast<std::uint32_t>(event));
    WriteU32(bytes.data() + 32, static_cast<std::uint32_t>(operation));
    WriteU32(bytes.data() + 36, result);
    WriteU64(bytes.data() + 40, detail);
    WriteU32(bytes.data() + 48, g_callbackCount.load(std::memory_order_relaxed));
    const std::uint32_t flags = g_overflow.load(std::memory_order_relaxed) |
        (g_alignmentBroken.load(std::memory_order_relaxed) != 0 ? 2U : 0U);
    WriteU32(bytes.data() + 52, flags);
    WriteU32(bytes.data() + 56, g_lastFlush.load(std::memory_order_relaxed));
    WriteU32(bytes.data() + 60, Checksum(bytes.data(), 60));
}

bool Enqueue(Event event, Operation operation, std::uint32_t result, std::uint64_t detail) {
    if (g_alignmentBroken.load(std::memory_order_acquire) != 0) return false;
    if (!TryLock(g_queueLock)) {
        g_overflow.store(1, std::memory_order_release);
        g_overflowPending.store(1, std::memory_order_release);
        ArmPending(false);
        return false;
    }
    const bool wasEmpty = g_count == 0;
    const bool budgetExhausted = g_pendingDrainBudget.load(std::memory_order_acquire) == 0;
    const auto append = [&](Event entryEvent, Operation entryOperation, std::uint32_t entryResult,
                            std::uint64_t entryDetail) {
        Encode(&g_queue[(g_head + g_count) % kQueueCapacity], entryEvent, entryOperation,
               entryResult, entryDetail);
        ++g_count;
    };
    if (event != Event::QueueOverflow &&
        g_overflowPending.load(std::memory_order_acquire) != 0) {
        if (g_count == kQueueCapacity) {
            g_overflow.store(1, std::memory_order_release);
            Unlock(g_queueLock);
            return false;
        }
        std::uint32_t expected = 1;
        if (g_overflowPending.compare_exchange_strong(expected, 0,
                                                       std::memory_order_acq_rel)) {
            append(Event::QueueOverflow, Operation::None, 0, 0);
        }
    }
    if (g_count == kQueueCapacity) {
        g_overflow.store(1, std::memory_order_release);
        g_overflowPending.store(1, std::memory_order_release);
        Unlock(g_queueLock);
        return false;
    }
    append(event, operation, result, detail);
    ArmPending(wasEmpty || budgetExhausted);
    Unlock(g_queueLock);
    return true;
}

struct FlushClaim {
    std::array<std::uint8_t, kMaxRecordsPerFlush * kRecordSize> bytes{};
    std::size_t count = 0;
    bool containsConfirmation = false;
};

bool Claim(FlushClaim* claim) {
    if (!TryLock(g_queueLock)) return false;
    const std::size_t count = g_count;
    if (count == 0) {
        Unlock(g_queueLock);
        return true;
    }
    claim->count = count < kMaxRecordsPerFlush ? count : kMaxRecordsPerFlush;
    for (std::size_t index = 0; index < claim->count; ++index) {
        const auto& source = g_queue[(g_head + index) % kQueueCapacity].bytes;
        const std::uint32_t event = static_cast<std::uint32_t>(source[28]) |
            (static_cast<std::uint32_t>(source[29]) << 8) |
            (static_cast<std::uint32_t>(source[30]) << 16) |
            (static_cast<std::uint32_t>(source[31]) << 24);
        claim->containsConfirmation |= event == static_cast<std::uint32_t>(Event::FlushConfirmation);
        for (std::size_t byte = 0; byte < kRecordSize; ++byte) {
            claim->bytes[index * kRecordSize + byte] = source[byte];
        }
    }
    g_head = (g_head + claim->count) % kQueueCapacity;
    g_count -= claim->count;
    Unlock(g_queueLock);
    return true;
}

void Restore(const FlushClaim& claim) {
    if (claim.count == 0) return;
    if (!TryLock(g_queueLock)) {
        g_overflow.store(1, std::memory_order_release);
        g_overflowPending.store(1, std::memory_order_release);
        ArmPending(false);
        return;
    }
    const std::size_t available = kQueueCapacity - g_count;
    const std::size_t restoreCount = available < claim.count ? available : claim.count;
    g_head = (g_head + kQueueCapacity - restoreCount) % kQueueCapacity;
    for (std::size_t index = 0; index < restoreCount; ++index) {
        auto& target = g_queue[(g_head + index) % kQueueCapacity].bytes;
        for (std::size_t byte = 0; byte < kRecordSize; ++byte) {
            target[byte] = claim.bytes[index * kRecordSize + byte];
        }
    }
    g_count += restoreCount;
    if (restoreCount != claim.count) {
        g_overflow.store(1, std::memory_order_release);
        g_overflowPending.store(1, std::memory_order_release);
    }
    ArmPending(false);
    Unlock(g_queueLock);
}

FlushResult FlushResultFor(FlushClaim* claim) {
    if (g_alignmentBroken.load(std::memory_order_acquire) != 0) return FlushResult::ShortWrite;
    if (!Claim(claim)) return FlushResult::Busy;
    if (claim->count == 0) return FlushResult::NeverAttempted;
    const auto open = reinterpret_cast<OpenFn>(g_open.load(std::memory_order_acquire));
    const auto write = reinterpret_cast<WriteFn>(g_write.load(std::memory_order_acquire));
    const auto close = reinterpret_cast<CloseFn>(g_close.load(std::memory_order_acquire));
    if (open == nullptr || write == nullptr || close == nullptr) {
        Restore(*claim);
        return FlushResult::NoApi;
    }
    void* file = open(kEventPath, "ab");
    if (file == nullptr) { Restore(*claim); return FlushResult::OpenFailed; }
    const std::size_t expected = claim->count * kRecordSize;
    const std::size_t written = write(claim->bytes.data(), 1, expected, file);
    const int closeResult = close(file);
    if (written != expected) {
        // The append may have committed a prefix. Retrying the claim would
        // place a complete record after an unaligned tail and duplicate data.
        g_alignmentBroken.store(1, std::memory_order_release);
        RefreshPending();
        return FlushResult::ShortWrite;
    }
    if (closeResult != 0) {
        // A complete write may already be durable even when close reports an
        // error. Re-queueing would create an indistinguishable duplicate.
        RefreshPending();
        return FlushResult::CloseFailed;
    }
    return FlushResult::Succeeded;
}

} // namespace

void ConfigureFileApi(OpenFn open, WriteFn write, CloseFn close) {
    g_open.store(reinterpret_cast<std::uintptr_t>(open), std::memory_order_release);
    g_write.store(reinterpret_cast<std::uintptr_t>(write), std::memory_order_release);
    g_close.store(reinterpret_cast<std::uintptr_t>(close), std::memory_order_release);
    g_fileApiReady.store(open != nullptr && write != nullptr && close != nullptr ? 1U : 0U,
                         std::memory_order_release);
}

bool FileApiConfigured() { return g_fileApiReady.load(std::memory_order_acquire) != 0; }

bool HasPending() {
    return g_alignmentBroken.load(std::memory_order_acquire) == 0 &&
           g_pending.load(std::memory_order_acquire) != 0 &&
           g_pendingDrainBudget.load(std::memory_order_acquire) != 0;
}

void Mark(Event event, Operation operation, std::uint32_t result, std::uint64_t detail) {
    if (event == Event::ManagerCallbackEntered) {
        g_callbackCount.fetch_add(1, std::memory_order_relaxed);
    }
    static_cast<void>(Enqueue(event, operation, result, detail));
}

bool MarkOnce(Event event, Operation operation, std::uint32_t result, std::uint64_t detail) {
    const auto value = static_cast<std::uint32_t>(event);
    if (value >= 32) return false;
    const std::uint32_t bit = 1U << value;
    std::uint32_t mask = g_onceMask.load(std::memory_order_acquire);
    do {
        if ((mask & bit) != 0) return false;
    } while (!g_onceMask.compare_exchange_weak(mask, mask | bit,
                                                std::memory_order_acq_rel));
    Mark(event, operation, result, detail);
    return true;
}

bool MarkIfChanged(Event event, Operation operation, std::uint32_t result,
                   std::uint64_t detail) {
    std::uint32_t expected = 0;
    if (g_hasChangedDetail.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) {
        g_lastChangedDetail.store(detail, std::memory_order_release);
        Mark(event, operation, result, detail);
        return true;
    }
    std::uint64_t previous = g_lastChangedDetail.load(std::memory_order_acquire);
    if (previous == detail) return false;
    if (!g_lastChangedDetail.compare_exchange_strong(previous, detail,
                                                     std::memory_order_acq_rel)) return false;
    Mark(event, operation, result, detail);
    return true;
}

FlushResult FlushBounded() {
    if (g_flushLock.test_and_set(std::memory_order_acquire)) return FlushResult::Busy;
    FlushClaim claim{};
    const FlushResult result = FlushResultFor(&claim);
    g_lastFlush.store(static_cast<std::uint32_t>(result), std::memory_order_release);
    if (result == FlushResult::Succeeded && !claim.containsConfirmation) {
        Enqueue(Event::FlushConfirmation, Operation::None,
                static_cast<std::uint32_t>(result), 0);
    }
    if (result == FlushResult::Succeeded) RefreshPending();
    g_flushLock.clear(std::memory_order_release);
    return result;
}

FlushResult DrainPendingBounded(std::size_t maxAttempts) {
    FlushResult last = FlushResult::NeverAttempted;
    for (std::size_t attempt = 0; attempt < maxAttempts; ++attempt) {
        if (!HasPending()) break;
        std::uint32_t budget = g_pendingDrainBudget.load(std::memory_order_acquire);
        while (budget != 0 &&
               !g_pendingDrainBudget.compare_exchange_weak(
                   budget, budget - 1, std::memory_order_acq_rel)) {
        }
        if (budget == 0) break;
        last = FlushBounded();
        if (last == FlushResult::Busy || last == FlushResult::NoApi ||
            last == FlushResult::OpenFailed || last == FlushResult::ShortWrite ||
            last == FlushResult::CloseFailed) {
            break;
        }
    }
    RefreshPending();
    return last;
}

FlushResult MarkAndFlush(Event event, Operation operation, std::uint32_t result,
                         std::uint64_t detail) {
    Mark(event, operation, result, detail);
    return FlushBounded();
}

} // namespace PersistenceEventJournal

#endif
