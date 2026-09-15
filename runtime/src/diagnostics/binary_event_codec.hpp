#pragma once

#include "diagnostics/diagnostic_event.hpp"
#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Fixed-size record codec for diagnostic events.
//
// The on-disk record is written field by field in little-endian order, so the
// file format does not depend on the compiler's struct layout or on the target
// endianness. Every record carries the magic, the schema version, its own size
// and a checksum; `Decode` rejects anything that does not match, which is what
// lets the offline parser tell a truncated file from a corrupt record.
class BinaryEventCodec {
public:
    // 8 magic + 2 schema + 2 recordSize + 4 origin + 8 buildId + 4 sequence
    // + 2 subsystem + 2 event + 1 severity + 1 phase + 2 flags + 4 threadTag
    // + 4 resultDomain + 4 resultCode + 8 detail + 4 checksum
    static constexpr std::size_t kRecordSize = 60;

    // Encodes into a buffer of exactly `kRecordSize` bytes. Fails with
    // `CapacityExceeded` when the buffer is too small, so a caller can never
    // silently write a partial record.
    [[nodiscard]] static Status Encode(const DiagnosticEvent& event, std::uint8_t* target,
                                       std::size_t capacity) noexcept;

    // Decodes one record. Rejects a wrong buffer size, a wrong magic, an unknown
    // schema version, a mismatched record size and a checksum mismatch; the
    // schema is part of the format so a future version cannot be parsed as this
    // one by accident.
    [[nodiscard]] static Status Decode(const std::uint8_t* record, std::size_t size,
                                       DiagnosticEvent* event) noexcept;

    // Checksum of a record with its checksum field treated as zero. Exposed
    // because the offline parser and the tests both verify records they read
    // from somewhere else.
    [[nodiscard]] static std::uint32_t Checksum(const std::uint8_t* record,
                                                std::size_t size) noexcept;
};

} // namespace isaac::runtime
