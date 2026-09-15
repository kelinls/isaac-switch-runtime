#include "diagnostics/binary_event_codec.hpp"

namespace isaac::runtime {
namespace {

// Little-endian readers/writers. The parser is a separate Python tool, so the
// byte order has to be explicit rather than "whatever the compiler does".
void WriteU16(std::uint8_t* target, std::uint16_t value) noexcept {
    target[0] = static_cast<std::uint8_t>(value & 0xFFU);
    target[1] = static_cast<std::uint8_t>((value >> 8) & 0xFFU);
}

void WriteU32(std::uint8_t* target, std::uint32_t value) noexcept {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>((value >> (8 * index)) & 0xFFU);
    }
}

void WriteU64(std::uint8_t* target, std::uint64_t value) noexcept {
    for (std::size_t index = 0; index < 8; ++index) {
        target[index] = static_cast<std::uint8_t>((value >> (8 * index)) & 0xFFU);
    }
}

std::uint16_t ReadU16(const std::uint8_t* source) noexcept {
    return static_cast<std::uint16_t>(source[0] |
                                      (static_cast<std::uint16_t>(source[1]) << 8));
}

std::uint32_t ReadU32(const std::uint8_t* source) noexcept {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(source[index]) << (8 * index);
    }
    return value;
}

std::uint64_t ReadU64(const std::uint8_t* source) noexcept {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(source[index]) << (8 * index);
    }
    return value;
}

// FNV-1a: small, dependency-free and good enough to catch truncation and a
// half-written record. It is not a cryptographic check and is not used as one.
constexpr std::uint32_t kFnvOffsetBasis = 2166136261U;
constexpr std::uint32_t kFnvPrime = 16777619U;

} // namespace

Status BinaryEventCodec::Encode(const DiagnosticEvent& event, std::uint8_t* target,
                                std::size_t capacity) noexcept {
    if (target == nullptr || capacity < kRecordSize) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::uint8_t* cursor = target;
    WriteU64(cursor, kDiagnosticEventMagic);                      cursor += 8;
    WriteU16(cursor, kDiagnosticEventSchemaVersion);              cursor += 2;
    WriteU16(cursor, static_cast<std::uint16_t>(kRecordSize));    cursor += 2;
    WriteU32(cursor, static_cast<std::uint32_t>(event.origin));   cursor += 4;
    WriteU64(cursor, event.buildId);                              cursor += 8;
    WriteU32(cursor, event.sequence);                             cursor += 4;
    WriteU16(cursor, static_cast<std::uint16_t>(event.subsystem)); cursor += 2;
    WriteU16(cursor, event.event);                                cursor += 2;
    *cursor++ = static_cast<std::uint8_t>(event.severity);
    *cursor++ = static_cast<std::uint8_t>(event.phase);
    WriteU16(cursor, event.flags);                                cursor += 2;
    WriteU32(cursor, event.threadTag);                            cursor += 4;
    WriteU32(cursor, event.result.domain);                        cursor += 4;
    WriteU32(cursor, event.result.code);                          cursor += 4;
    WriteU64(cursor, event.detail);                               cursor += 8;
    WriteU32(cursor, 0);                                          cursor += 4;  // checksum

    WriteU32(target + kRecordSize - 4, Checksum(target, kRecordSize));
    return Status::Ok();
}

Status BinaryEventCodec::Decode(const std::uint8_t* record, std::size_t size,
                                DiagnosticEvent* event) noexcept {
    if (record == nullptr || event == nullptr || size != kRecordSize) {
        return Status{StatusCode::InvalidArgument};
    }
    if (ReadU64(record) != kDiagnosticEventMagic) {
        return Status{StatusCode::Corrupted};
    }
    if (ReadU16(record + 8) != kDiagnosticEventSchemaVersion ||
        ReadU16(record + 10) != static_cast<std::uint16_t>(kRecordSize)) {
        return Status{StatusCode::Unsupported};
    }
    if (ReadU32(record + kRecordSize - 4) != Checksum(record, kRecordSize)) {
        return Status{StatusCode::Corrupted};
    }

    DiagnosticEvent decoded{};
    decoded.origin = static_cast<DiagnosticOrigin>(ReadU32(record + 12));
    decoded.buildId = ReadU64(record + 16);
    decoded.sequence = ReadU32(record + 24);
    decoded.subsystem = static_cast<DiagnosticSubsystem>(ReadU16(record + 28));
    decoded.event = ReadU16(record + 30);
    decoded.severity = static_cast<DiagnosticSeverity>(record[32]);
    decoded.phase = static_cast<DiagnosticPhase>(record[33]);
    decoded.flags = ReadU16(record + 34);
    decoded.threadTag = ReadU32(record + 36);
    decoded.result.domain = ReadU32(record + 40);
    decoded.result.code = ReadU32(record + 44);
    decoded.detail = ReadU64(record + 48);
    *event = decoded;
    return Status::Ok();
}

std::uint32_t BinaryEventCodec::Checksum(const std::uint8_t* record,
                                         std::size_t size) noexcept {
    if (record == nullptr || size != kRecordSize) {
        return 0;
    }
    std::uint32_t hash = kFnvOffsetBasis;
    for (std::size_t index = 0; index < size; ++index) {
        // The checksum field itself is excluded so it can be written last.
        const std::uint8_t byte =
            index >= kRecordSize - 4 ? static_cast<std::uint8_t>(0) : record[index];
        hash ^= byte;
        hash *= kFnvPrime;
    }
    return hash;
}

} // namespace isaac::runtime
