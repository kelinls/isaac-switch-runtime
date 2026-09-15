#pragma once

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Stable event identity. The concrete payload schema belongs to the
// diagnostics layer; producers only need this header.
struct EventHeader {
    std::uint32_t id{0};
    std::uint32_t sequence{0};
    std::uint64_t buildId{0};
    std::uint64_t timestampMilliseconds{0};
};

// Diagnostics is an observer: publishing never blocks the caller for longer
// than a bounded attempt and never changes a business result.
class IEventSink {
public:
    virtual ~IEventSink() = default;

    virtual void Publish(const EventHeader& header, const std::uint8_t* payload,
                         std::size_t payloadSize) noexcept = 0;
};

} // namespace isaac::runtime
