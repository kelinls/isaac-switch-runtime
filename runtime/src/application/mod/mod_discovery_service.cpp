#include "application/mod/mod_discovery_service.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

// 与 `manifest_service.cpp` 同一套前缀（那边是拼清单里的 `directory`/`entry`，这边是拼
// "引擎列出来的目录名"）。两处必须一致，否则同一个模组走清单与走自动发现会落到不同路径上。
constexpr const char* kModsRootRelative = "isaac_mods/mods";
constexpr const char* kManifestRootPrefix = "rom:/isaac_mods/";
constexpr const char* kModRootPrefix = "rom:/isaac_mods/mods/";
constexpr const char* kChunkRootPrefix = "@rom:/isaac_mods/";
constexpr std::size_t kMaximumDiscoveredMods = ResolvedManifestModBatch::kCapacity;

bool JoinPath(char* output, std::size_t capacity, const char* prefix, const char* suffix) noexcept {
    if (output == nullptr || prefix == nullptr || suffix == nullptr) {
        return false;
    }
    const std::size_t prefixLength = std::strlen(prefix);
    const std::size_t suffixLength = std::strlen(suffix);
    if (prefixLength + suffixLength >= capacity) {
        return false;
    }
    std::memcpy(output, prefix, prefixLength);
    std::memcpy(output + prefixLength, suffix, suffixLength + 1);
    return true;
}

// 目录名必须是"一层目录"：空、`.`、`..`、带斜杠或反斜杠的一律拒绝。
// 引擎列出来的名字里出现这些说明我们对返回块的解读错了，**此时宁可少加载一个模组，
// 也不能拿一个可疑字符串去拼路径**（拼出来的路径会让引擎去读别的东西）。
bool IsUsableDirectoryName(const char* name) noexcept {
    if (name == nullptr || name[0] == '\0') {
        return false;
    }
    if (std::strcmp(name, ".") == 0 || std::strcmp(name, "..") == 0) {
        return false;
    }
    for (const char* cursor = name; *cursor != '\0'; ++cursor) {
        if (*cursor == '/' || *cursor == '\\') {
            return false;
        }
    }
    return true;
}

// 按名字升序插入排序（模组数量是个位数，插入排序最简单且不引入算法依赖）。
// 顺序必须确定：两个模组的回调派发顺序会影响游戏行为，而"引擎给的顺序"没有保证。
void SortNames(ModDirectoryEntry* entries, std::size_t count) noexcept {
    for (std::size_t index = 1; index < count; ++index) {
        ModDirectoryEntry current = entries[index];
        std::size_t position = index;
        while (position > 0 &&
               std::strcmp(entries[position - 1].name.data(), current.name.data()) > 0) {
            entries[position] = entries[position - 1];
            --position;
        }
        entries[position] = current;
    }
}

} // namespace

Status ModDiscoveryService::Discover(ResolvedManifestModBatch* batch) const noexcept {
    if (batch == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    *batch = ResolvedManifestModBatch{};

    ModDirectoryEntry names[kMaximumDiscoveredMods]{};
    std::size_t count = 0;
    const Status listed = directories_.ListSubdirectories(kModsRootRelative, names,
                                                         kMaximumDiscoveredMods, &count);
    if (!listed.ok()) {
        return listed;
    }
    if (count == 0) {
        return Status{StatusCode::NotFound};
    }
    if (count > kMaximumDiscoveredMods) {
        return Status{StatusCode::CapacityExceeded};
    }
    SortNames(names, count);

    for (std::size_t index = 0; index < count; ++index) {
        if (!IsUsableDirectoryName(names[index].name.data())) {
            // 一个可疑名字就整批拒绝：宁可让用户看到"没加载"，也不要拿可疑路径去调引擎。
            *batch = ResolvedManifestModBatch{};
            return Status{StatusCode::Corrupted};
        }
        ModPathBuffers& buffers = batch->paths[index];
        if (!JoinPath(buffers.modRoot.data(), buffers.modRoot.size(), kModRootPrefix,
                      names[index].name.data())) {
            *batch = ResolvedManifestModBatch{};
            return Status{StatusCode::CapacityExceeded};
        }
        // `mods/<目录>/main.lua`：与清单里写的 `entry` 同一个形状。
        char entryRelative[kModEntryCapacity]{};
        const std::size_t nameLength = std::strlen(names[index].name.data());
        if (nameLength + 9 >= sizeof(entryRelative)) {   // "mods/" + 名字 + "/main.lua"
            *batch = ResolvedManifestModBatch{};
            return Status{StatusCode::CapacityExceeded};
        }
        std::memcpy(entryRelative, "mods/", 5);
        std::memcpy(entryRelative + 5, names[index].name.data(), nameLength);
        std::memcpy(entryRelative + 5 + nameLength, "/main.lua", 10);
        if (!JoinPath(buffers.entryPath.data(), buffers.entryPath.size(), kManifestRootPrefix,
                      entryRelative) ||
            !JoinPath(buffers.chunkName.data(), buffers.chunkName.size(), kChunkRootPrefix,
                      entryRelative)) {
            *batch = ResolvedManifestModBatch{};
            return Status{StatusCode::CapacityExceeded};
        }
        ResolvedManifestMod& resolved = batch->mods[index];
        resolved.entryPath = buffers.entryPath.data();
        resolved.modRoot = buffers.modRoot.data();
        resolved.chunkName = buffers.chunkName.data();
        resolved.manifestBytes = 0;      // 没有清单：这个字段只用于诊断
        resolved.hasEntry = true;        // 缺了会被 `ModLoadService` 如实报成 EntryAbsent
    }
    batch->count = count;
    batch->manifestBytes = 0;
    return Status{StatusCode::Ok};
}

} // namespace isaac::runtime
