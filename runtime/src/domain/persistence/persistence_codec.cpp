#include "domain/persistence/persistence_codec.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

void WriteU16(std::uint8_t* target, std::uint16_t value) noexcept {
    target[0] = static_cast<std::uint8_t>(value);
    target[1] = static_cast<std::uint8_t>(value >> 8);
}

void WriteU32(std::uint8_t* target, std::uint32_t value) noexcept {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

void WriteU64(std::uint8_t* target, std::uint64_t value) noexcept {
    for (std::size_t index = 0; index < 8; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

std::uint16_t ReadU16(const std::uint8_t* source) noexcept {
    return static_cast<std::uint16_t>(source[0]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(source[1]) << 8);
}

std::uint32_t ReadU32(const std::uint8_t* source) noexcept {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(source[index]) << (index * 8);
    }
    return value;
}

std::uint64_t ReadU64(const std::uint8_t* source) noexcept {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(source[index]) << (index * 8);
    }
    return value;
}

} // namespace

std::uint32_t PersistenceCodec::Checksum(const std::uint8_t* bytes, std::size_t length) noexcept {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}

Status PersistenceCodec::EncodeHeader(std::uint8_t* target, std::size_t capacity,
                                      const PersistenceFileHeader& header) noexcept {
    if (target == nullptr || capacity < kHeaderSize) {
        return Status{StatusCode::InvalidArgument};
    }
    if (header.recordCount > kMaximumRecordCount || header.payloadLength > kMaximumPayloadSize) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::memset(target, 0, kHeaderSize);
    std::memcpy(target, kMagic, sizeof(kMagic) - 1);
    WriteU16(target + 8, kVersion);
    WriteU16(target + 10, static_cast<std::uint16_t>(kHeaderSize));
    WriteU64(target + 12, header.generation);
    WriteU32(target + 20, header.recordCount);
    WriteU32(target + 24, header.payloadLength);
    WriteU32(target + 28, header.payloadChecksum);
    return Status::Ok();
}

Status PersistenceCodec::DecodeHeader(const std::uint8_t* bytes, std::size_t length,
                                      PersistenceFileHeader* header) noexcept {
    if (bytes == nullptr || header == nullptr || length < kHeaderSize) {
        return Status{StatusCode::InvalidArgument};
    }
    if (std::memcmp(bytes, kMagic, sizeof(kMagic) - 1) != 0 ||
        ReadU16(bytes + 8) != kVersion ||
        ReadU16(bytes + 10) != kHeaderSize) {
        return Status{StatusCode::Corrupted};
    }
    const std::uint32_t recordCount = ReadU32(bytes + 20);
    const std::uint32_t payloadLength = ReadU32(bytes + 24);
    if (recordCount > kMaximumRecordCount || payloadLength > kMaximumPayloadSize ||
        payloadLength != length - kHeaderSize) {
        return Status{StatusCode::Corrupted};
    }
    header->generation = ReadU64(bytes + 12);
    header->recordCount = recordCount;
    header->payloadLength = payloadLength;
    header->payloadChecksum = ReadU32(bytes + 28);
    return Status::Ok();
}

Status PersistenceCodec::DecodeRecord(const std::uint8_t* payload, std::size_t payloadLength,
                                      std::size_t offset, PersistenceRecordView* record,
                                      std::size_t* nextOffset) noexcept {
    if (payload == nullptr || record == nullptr || nextOffset == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    if (offset > payloadLength || payloadLength - offset < kRecordHeaderSize) {
        return Status{StatusCode::Corrupted};
    }
    const std::uint8_t* bytes = payload + offset;
    const std::uint32_t valueLength = ReadU32(bytes + 20);
    if (valueLength > payloadLength - offset - kRecordHeaderSize) {
        return Status{StatusCode::Corrupted};
    }
    record->namespaceId = ReadU64(bytes);
    record->key = ReadU64(bytes + 8);
    record->type = bytes[16];
    record->valueLength = valueLength;
    record->value = bytes + kRecordHeaderSize;
    *nextOffset = offset + kRecordHeaderSize + valueLength;
    return Status::Ok();
}

Status PersistenceCodec::EncodeRecord(std::uint8_t* payload, std::size_t capacity,
                                      std::size_t offset, const PersistenceRecordView& record,
                                      std::size_t* nextOffset) noexcept {
    if (payload == nullptr || nextOffset == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    if (offset > capacity || capacity - offset < kRecordHeaderSize) {
        return Status{StatusCode::CapacityExceeded};
    }
    if (record.valueLength != 0 && record.value == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    if (record.valueLength > capacity - offset - kRecordHeaderSize) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::uint8_t* bytes = payload + offset;
    WriteU64(bytes, record.namespaceId);
    WriteU64(bytes + 8, record.key);
    bytes[16] = record.type;
    bytes[17] = 0;
    bytes[18] = 0;
    bytes[19] = 0;
    WriteU32(bytes + 20, record.valueLength);
    if (record.valueLength != 0) {
        std::memcpy(bytes + kRecordHeaderSize, record.value, record.valueLength);
    }
    *nextOffset = offset + kRecordHeaderSize + record.valueLength;
    return Status::Ok();
}

Status PersistenceCodec::ValidateFile(const std::uint8_t* bytes, std::size_t length) noexcept {
    PersistenceFileHeader header{};
    const Status decoded = DecodeHeader(bytes, length, &header);
    if (!decoded.ok()) {
        return decoded;
    }
    const std::uint8_t* payload = bytes + kHeaderSize;
    if (Checksum(payload, header.payloadLength) != header.payloadChecksum) {
        return Status{StatusCode::Corrupted};
    }
    std::size_t offset = 0;
    for (std::uint32_t index = 0; index < header.recordCount; ++index) {
        PersistenceRecordView record{};
        const Status recordStatus = DecodeRecord(payload, header.payloadLength, offset, &record, &offset);
        if (!recordStatus.ok()) {
            return recordStatus;
        }
    }
    if (offset != header.payloadLength) {
        return Status{StatusCode::Corrupted};
    }
    return Status::Ok();
}

} // namespace isaac::runtime
