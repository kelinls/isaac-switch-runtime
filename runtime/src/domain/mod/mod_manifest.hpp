#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Capacities match the existing on-device manifest contract; changing them
// changes how much of a manifest can be addressed, so they are part of the
// domain model rather than a platform detail.
inline constexpr std::size_t kModDirectoryCapacity = 256;
inline constexpr std::size_t kModEntryCapacity = 1024;

enum class ManifestParseResult : std::uint32_t {
    Ok = 0,
    InvalidArgument,
    InvalidJson,
    InvalidSchema,
    InvalidMod,
    NoEnabledMod,
};

// One entry of the manifest "mods" array. `enabled` comes from the manifest,
// never from a default.
struct ModManifestEntry {
    std::array<char, kModDirectoryCapacity> directory{};
    std::array<char, kModEntryCapacity> entry{};
    bool enabled{false};
};

// The selected, enabled Mod as a pure value object: no file handle, no Lua
// state and no pointer to game memory.
struct ModManifest {
    std::array<char, kModDirectoryCapacity> directory{};
    std::array<char, kModEntryCapacity> entry{};

    [[nodiscard]] const char* Directory() const noexcept { return directory.data(); }
    [[nodiscard]] const char* Entry() const noexcept { return entry.data(); }

    // A PC Mod may ship resources only: the game mounts its `resources/` and never
    // runs a script for it, so `main.lua` -- and therefore the manifest `entry`
    // -- is optional. An absent entry is stored as the empty string, which is
    // also the "no script" flag every layer below reads.
    [[nodiscard]] bool HasEntry() const noexcept { return entry[0] != '\0'; }
};

} // namespace isaac::runtime
