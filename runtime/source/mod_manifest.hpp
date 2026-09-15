#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace ModManifest {

inline constexpr std::size_t kDirectoryCapacity = 256;
inline constexpr std::size_t kEntryCapacity = 1024;

struct SelectedMod {
    std::array<char, kDirectoryCapacity> directory{};
    // Empty (all zero) when the manifest omits `entry`: the Mod is a pure-resource
    // Mod and has no script to run.
    std::array<char, kEntryCapacity> entry{};
};

enum class ParseResult : std::uint32_t {
    Success,
    InvalidArgument,
    InvalidJson,
    InvalidSchema,
    InvalidMod,
    NoEnabledMod,
};

ParseResult SelectFirstEnabled(const char* json, std::size_t length, SelectedMod* output);

} // namespace ModManifest
