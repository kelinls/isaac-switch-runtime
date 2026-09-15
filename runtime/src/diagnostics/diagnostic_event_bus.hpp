#pragma once

#include "diagnostics/diagnostic_event.hpp"
#include "diagnostics/diagnostic_health.hpp"
#include "diagnostics/in_memory_ring_buffer_sink.hpp"

#include <cstdint>

namespace isaac::runtime {

// Publishes events to the ring it owns.
//
// The bus is an observer: `Publish` never blocks a game thread for longer than a
// bounded attempt, never allocates, and reports failures through
// `DiagnosticHealth` instead of handing them to business code. The boolean
// result exists for tests and for the journal; no business path may depend on it.
//
// `Publish` returns false when the event was dropped (ring full or a concurrent
// producer holding the claim). The attempt is still counted, so the parser can
// compare attempts against the records it actually finds.
template <std::size_t RingCapacity = kDiagnosticRingCapacity>
class DiagnosticEventBus {
public:
    DiagnosticEventBus(InMemoryRingBufferSink<RingCapacity>& ring,
                       DiagnosticHealth& health) noexcept
        : ring_(ring), health_(health) {}

    [[nodiscard]] bool Publish(const DiagnosticEvent& event) noexcept {
        ++published_;
        if (!ring_.TryPublish(event)) {
            health_.RecordDrop();
            return false;
        }
        return true;
    }

    [[nodiscard]] InMemoryRingBufferSink<RingCapacity>& Ring() noexcept { return ring_; }
    [[nodiscard]] const InMemoryRingBufferSink<RingCapacity>& Ring() const noexcept { return ring_; }
    [[nodiscard]] DiagnosticHealth& Health() noexcept { return health_; }
    [[nodiscard]] const DiagnosticHealth& Health() const noexcept { return health_; }

    // Publish attempts, including the ones that were dropped.
    [[nodiscard]] std::uint32_t PublishedCount() const noexcept { return published_; }
    // Events actually accepted by the ring.
    [[nodiscard]] std::uint32_t AcceptedCount() const noexcept {
        return published_ - health_.DroppedEvents();
    }

private:
    InMemoryRingBufferSink<RingCapacity>& ring_;
    DiagnosticHealth& health_;
    std::uint32_t published_{0};
};

} // namespace isaac::runtime
