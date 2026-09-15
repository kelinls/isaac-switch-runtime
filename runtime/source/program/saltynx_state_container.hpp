#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace saltynx_state {
constexpr std::uint16_t kFormatVersion = 1;
constexpr std::size_t kHeaderSize = 32;
constexpr std::size_t kRecordSize = 32;
constexpr std::size_t kRecordCount = 2;
constexpr std::size_t kContainerSize = kHeaderSize + kRecordSize * kRecordCount;

struct StateRecord {
    std::uint64_t name_space;
    std::uint64_t key;
    std::int64_t value;
};

inline void Put(std::uint8_t* output, std::size_t offset, std::uint64_t value, std::size_t bytes) {
    for (std::size_t index = 0; index < bytes; ++index) {
        output[offset + index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

inline std::uint64_t Get(const std::uint8_t* input, std::size_t offset, std::size_t bytes) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < bytes; ++index) {
        value |= static_cast<std::uint64_t>(input[offset + index]) << (index * 8);
    }
    return value;
}

inline std::uint32_t Checksum(const std::uint8_t* input, std::size_t size) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < size; ++index) {
        value ^= (index >= 28 && index < 32) ? 0U : input[index];
        value *= 16777619U;
    }
    return value;
}

inline void PutRecord(std::uint8_t* output, std::size_t offset, const StateRecord& record) {
    Put(output, offset, record.name_space, 8);
    Put(output, offset + 8, record.key, 8);
    output[offset + 16] = 1;
    output[offset + 17] = 0;
    Put(output, offset + 18, 8, 2);
    Put(output, offset + 20, static_cast<std::uint64_t>(record.value), 8);
    Put(output, offset + 28, 0, 4);
}

inline bool GetRecord(const std::uint8_t* input, std::size_t offset, StateRecord& record) {
    if (input[offset + 16] != 1 || Get(input, offset + 18, 2) != 8) {
        return false;
    }
    record = {
        Get(input, offset, 8),
        Get(input, offset + 8, 8),
        static_cast<std::int64_t>(Get(input, offset + 20, 8)),
    };
    return true;
}

inline void BuildStateContainer(
    std::array<std::uint8_t, kContainerSize>& output,
    std::uint64_t generation,
    const StateRecord (&records)[kRecordCount]) {
    constexpr char kMagic[] = "ISMODST1";
    output.fill(0);
    for (std::size_t index = 0; index < 8; ++index) {
        output[index] = kMagic[index];
    }
    Put(output.data(), 8, kFormatVersion, 2);
    Put(output.data(), 10, kHeaderSize, 2);
    Put(output.data(), 12, generation, 8);
    Put(output.data(), 20, kRecordCount, 4);
    Put(output.data(), 24, kRecordSize * kRecordCount, 4);
    for (std::size_t index = 0; index < kRecordCount; ++index) {
        PutRecord(output.data(), kHeaderSize + index * kRecordSize, records[index]);
    }
    Put(output.data(), 28, Checksum(output.data(), output.size()), 4);
}

inline bool ValidateContainerEnvelope(const std::array<std::uint8_t, kContainerSize>& input) {
    constexpr char kMagic[] = "ISMODST1";
    for (std::size_t index = 0; index < 8; ++index) {
        if (input[index] != kMagic[index]) {
            return false;
        }
    }
    if (Get(input.data(), 8, 2) != kFormatVersion ||
        Get(input.data(), 10, 2) != kHeaderSize ||
        Get(input.data(), 20, 4) != kRecordCount ||
        Get(input.data(), 24, 4) != kRecordSize * kRecordCount ||
        Get(input.data(), 28, 4) != Checksum(input.data(), input.size())) {
        return false;
    }
    return true;
}

inline bool ValidateContainerRecords(
    const std::array<std::uint8_t, kContainerSize>& input,
    const StateRecord (&expected)[kRecordCount]) {
    for (std::size_t index = 0; index < kRecordCount; ++index) {
        StateRecord actual{};
        if (!GetRecord(input.data(), kHeaderSize + index * kRecordSize, actual) ||
            actual.name_space != expected[index].name_space ||
            actual.key != expected[index].key || actual.value != expected[index].value) {
            return false;
        }
    }
    return true;
}

inline bool ValidateStateContainer(
    const std::array<std::uint8_t, kContainerSize>& input,
    const StateRecord (&expected)[kRecordCount]) {
    return ValidateContainerEnvelope(input) && ValidateContainerRecords(input, expected);
}
} // namespace saltynx_state
