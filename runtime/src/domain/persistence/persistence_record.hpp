#pragma once

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// FNV-1a 64 namespace of a Mod name; this is the exact function the current
// on-device format uses, so refactoring cannot change existing file identity.
[[nodiscard]] constexpr std::uint64_t HashModNamespace(const char* name, std::size_t length) noexcept {
    std::uint64_t value = 14695981039346656037ULL;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= static_cast<unsigned char>(name[index]);
        value *= 1099511628211ULL;
    }
    return value;
}

// "mod-data" as a little-endian key, matching the current format.
inline constexpr std::uint64_t kModDataKey = 0x6D6F642D64617461ULL;
inline constexpr std::uint8_t kStringRecordType = 1;

// A decoded record. The value bytes stay owned by the caller's buffer; this
// type never allocates.
struct PersistenceRecordView {
    std::uint64_t namespaceId{0};
    std::uint64_t key{0};
    std::uint8_t type{0};
    std::uint32_t valueLength{0};
    const std::uint8_t* value{nullptr};
};

} // namespace isaac::runtime
