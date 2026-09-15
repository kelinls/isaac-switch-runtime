#pragma once

#include "ports/content_mount_port.hpp"

#include <array>
#include <cstddef>
#include <string_view>

namespace isaac::runtime {

// Keeps the engine's content mount points in sync with the loaded Mods.
//
// Registering is not a one-time setup: the engine clears its mount point table and
// rebuilds it (base content, add-on content, Rep+ patch) whenever content is
// reloaded, which drops every Mod mount point with it. The service therefore
// remembers which Mod directories were mounted so the rebuild relay can restore
// them, and it owns that memory itself because the rebuild callback can run at a
// point where no Mod load request is in flight.
class ContentMountService {
public:
    static constexpr std::size_t kMaxMods = 8;
    static constexpr std::size_t kMaxDirectoryLength = 128;
    // A Mod contributes its PC-style content directories; all of them are optional in a
    // Mod. These are the leaf names of the PC Mod contract, so they belong to the Mod
    // application layer rather than to the engine adapter.
    //
    // `resources/gfx`（以及 `content/gfx`）**必须一起挂**：ANM2 内部的
    // `<Spritesheet Path="...">` 是**相对 gfx 目录**的裸文件名（本体自己的 .anm2 就是这么写的，
    // 例如 `005.100_collectible.anm2` 里的 `Items/Collectibles/Collectibles_001_TheSadOnion.png`
    // 对应 `resources/gfx/items/collectibles/...`），而引擎侧 `AnmCache::PreloadANM2Images`
    // （`0x158cc`）是把**挂载点字符串直接拼在路径前面**去找文件的（`strncpy_s` + `strcat_s`，
    // 逐个挂载点试）。只有 `.../resources` 一个挂载点时，Mod 自带 .anm2 里的 `draw149.png` 会被
    // 拼成 `.../resources/draw149.png`（不存在）→ 贴图永远加载不上 → `AnimationLayer::RenderFrame`
    // 在 `[spritesheet+0x7c] == 0` 处**静默返回**：现象是"不崩、不报错、什么都不画"
    // （2026-09-13 真机报告 `01789148187`/`01789148432` 正是这个形态）。
    static constexpr std::array<std::string_view, 4> kLeaves = {
        std::string_view{"resources"},
        std::string_view{"resources/gfx"},
        std::string_view{"content"},
        std::string_view{"content/gfx"},
    };
    static constexpr std::size_t kLeafCount = kLeaves.size();

    explicit ContentMountService(IContentMountPort& port) noexcept : port_(port) {}

    // Records the Mod directory and mounts its content leaves. Registering the same
    // directory twice is a no-op, matching the engine's own duplicate handling.
    [[nodiscard]] Status RegisterMod(std::string_view modDirectory) noexcept;
    // Re-mounts every recorded Mod; called after the engine rebuilds its table.
    [[nodiscard]] Status RemountAll() noexcept;
    // Drops the record without touching the engine (a Mod that failed to load must
    // not keep a mount point alive).
    void Clear() noexcept;

    [[nodiscard]] std::size_t mod_count() const noexcept { return count_; }
    [[nodiscard]] std::string_view mod_directory(std::size_t index) const noexcept;

private:
    struct Entry {
        std::array<char, kMaxDirectoryLength> directory{};
        std::size_t length{0};
    };

    [[nodiscard]] Status MountLeaves(const Entry& entry) noexcept;
    [[nodiscard]] const Entry* Find(std::string_view modDirectory) const noexcept;

    IContentMountPort& port_;
    std::array<Entry, kMaxMods> entries_{};
    std::size_t count_{0};
};

// The session-scoped instance the rebuild relay reports through. It is published by
// the bootstrap path once the port exists and stays null before that, which the relay
// treats as "nothing to restore".
void SetSessionContentMountService(ContentMountService* service) noexcept;
ContentMountService* SessionContentMountService() noexcept;

} // namespace isaac::runtime
