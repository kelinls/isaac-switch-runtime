#pragma once

#include "domain/persistence/persistence_record.hpp"
#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

struct PersistenceFileHeader {
    std::uint64_t generation{0};
    std::uint32_t recordCount{0};
    std::uint32_t payloadLength{0};
    std::uint32_t payloadChecksum{0};
};

// Pure encoder/decoder for the versioned Mod persistence file. It performs no
// I/O, touches no global state and is host-testable: the same bytes the on-device
// file already uses (magic "ISMODST2", version 2, 32-byte header, 24-byte record
// header, FNV-1a 32 checksum).
class PersistenceCodec {
public:
    static constexpr char kMagic[9] = {'I', 'S', 'M', 'O', 'D', 'S', 'T', '2', '\0'};
    static constexpr std::uint16_t kVersion = 2;
    static constexpr std::size_t kHeaderSize = 32;
    static constexpr std::size_t kRecordHeaderSize = 24;
    static constexpr std::size_t kMaximumFileSize = 16 * 1024;
    static constexpr std::size_t kMaximumPayloadSize = kMaximumFileSize - kHeaderSize;
    static constexpr std::uint32_t kMaximumRecordCount = 256;

    [[nodiscard]] static std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t length) noexcept;

    [[nodiscard]] static Status EncodeHeader(std::uint8_t* target, std::size_t capacity,
                                             const PersistenceFileHeader& header) noexcept;

    [[nodiscard]] static Status DecodeHeader(const std::uint8_t* bytes, std::size_t length,
                                             PersistenceFileHeader* header) noexcept;

    // Verifies magic, version, declared sizes, record count and payload
    // checksum, then walks every record so a truncated file is rejected before
    // any record is trusted.
    [[nodiscard]] static Status ValidateFile(const std::uint8_t* bytes, std::size_t length) noexcept;

    [[nodiscard]] static Status EncodeRecord(std::uint8_t* payload, std::size_t capacity,
                                             std::size_t offset, const PersistenceRecordView& record,
                                             std::size_t* nextOffset) noexcept;

    [[nodiscard]] static Status DecodeRecord(const std::uint8_t* payload, std::size_t payloadLength,
                                             std::size_t offset, PersistenceRecordView* record,
                                             std::size_t* nextOffset) noexcept;
};

} // namespace isaac::runtime
