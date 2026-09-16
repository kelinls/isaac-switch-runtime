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

// 同一次加载里最多几个 Mod（2026-09-16，多模组加载）。
//
// 与旧解析器 `ModManifest::kMaximumSelectedMods` 必须一致（`hook_manager.cpp` 里有
// `static_assert` 兜住，理由同 `kRomfsModScriptMaximumLength`：两份常量各自演化过就埋过坑）。
// 取 4 的依据：整个集合是**按值传递**的解析结果，8 个 Mod 会让它涨到 10 KiB 以上。
inline constexpr std::size_t kModManifestCapacity = 4;

// 一次解析的结果：清单里**所有** `enabled = true` 的 Mod，顺序 = 清单里的书写顺序
// （PC 正是按这个顺序依次执行各 Mod 的入口脚本）。
struct ModManifestSet {
    std::array<ModManifestEntry, kModManifestCapacity> entries{};
    std::size_t count{0};

    [[nodiscard]] bool empty() const noexcept { return count == 0; }
};

} // namespace isaac::runtime
