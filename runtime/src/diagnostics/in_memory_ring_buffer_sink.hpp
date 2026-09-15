#pragma once

#include "diagnostics/diagnostic_event.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Fixed capacity of the always-available ring. 128 records is 7680 bytes, which
// fits the Runtime's static budget and holds enough early-boot events to explain
// a failed start.
inline constexpr std::size_t kDiagnosticRingCapacity = 4;

// Fixed-capacity, allocation-free event ring.
//
// Producers are the module entry thread, the Runtime worker and the game's
// update/render threads, so the storage is a compile-time array and the
// publication protocol never blocks and never spins without a bound: a producer
// that finds the claim flag taken gives up and reports the drop, which the
// health state turns into "evidence may be incomplete" instead of stalling a
// game thread.
//
// Capacity must be a power of two so the wrap is a mask, not a division.
template <std::size_t RingCapacity = kDiagnosticRingCapacity>
class InMemoryRingBufferSink {
    static_assert(RingCapacity >= 2, "a ring must hold at least two events");
    static_assert((RingCapacity & (RingCapacity - 1)) == 0,
                  "ring capacity must be a power of two");

public:
    static constexpr std::size_t Capacity = RingCapacity;

    // Publishes one event. Returns false when the ring is full or another
    // producer holds the claim flag; either way the caller keeps running.
    [[nodiscard]] bool TryPublish(const DiagnosticEvent& event) noexcept {
        std::uint32_t expected = 0;
        if (!claim_.compare_exchange_strong(expected, 1U, std::memory_order_acq_rel,
                                           std::memory_order_relaxed)) {
            // Another producer holds the claim. Give up instead of spinning:
            // the drop is counted so the evidence gap stays visible.
            ++contended_;
            ++dropped_;
            return false;
        }
        // `count_` is only written under the claim flag, so it can be read plainly
        // here; readers use the atomic `Count()` accessor.
        const std::size_t count = plainCount_;
        if (count == Capacity) {
            claim_.store(0U, std::memory_order_release);
            ++dropped_;
            return false;
        }
        entries_[(head_ + count) & (Capacity - 1)] = event;
        plainCount_ = count + 1;
        // Publish the new count only after the slot is written.
        count_.store(plainCount_, std::memory_order_release);
        claim_.store(0U, std::memory_order_release);
        return true;
    }

    [[nodiscard]] std::size_t Count() const noexcept {
        return count_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::uint32_t DroppedCount() const noexcept {
        return dropped_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint32_t ContendedCount() const noexcept {
        return contended_.load(std::memory_order_relaxed);
    }

    // Reads the index-th event in publication order. Returns false when the
    // index is out of range, so a consumer can drain defensively.
    [[nodiscard]] bool At(std::size_t index, DiagnosticEvent* event) const noexcept {
        if (event == nullptr || index >= Count()) {
            return false;
        }
        *event = entries_[(head_ + index) & (Capacity - 1)];
        return true;
    }

    // Drops the oldest `count` events, as a journal does after it has written
    // them. Only valid on a quiescent ring (no concurrent producer).
    void DiscardOldest(std::size_t count) noexcept {
        const std::size_t available = Count();
        const std::size_t remove = count > available ? available : count;
        head_ = (head_ + remove) & (Capacity - 1);
        plainCount_ -= remove;
        count_.store(plainCount_, std::memory_order_release);
    }

    void Reset() noexcept {
        head_ = 0;
        plainCount_ = 0;
        count_.store(0, std::memory_order_release);
        dropped_.store(0, std::memory_order_relaxed);
        contended_.store(0, std::memory_order_relaxed);
    }

private:
    DiagnosticEvent entries_[RingCapacity]{};
    std::size_t head_{0};
    std::size_t plainCount_{0};
    std::atomic<std::size_t> count_{0};
    std::atomic<std::uint32_t> dropped_{0};
    std::atomic<std::uint32_t> contended_{0};
    // 32 位 `0/1` 原子，不是布尔型原子：本项目的 AArch64 `-Oz` 构建下布尔型原子的 `load()`
    // 会生成落在可执行文本边界之后的 PLT 跳板，真机表现为 `Instruction Abort`、PC 恰为
    // `__text_end__`（见 `docs/问题与解决记录.md` 的「2026-09-04」布尔原子 PLT 一节）。
    // 本文件经 `diagnostic_session.hpp` → `saltynx_runtime_bridge.cpp` 进入设备构建。
    std::atomic<std::uint32_t> claim_{0};
};

} // namespace isaac::runtime
