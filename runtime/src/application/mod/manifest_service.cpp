#include "application/mod/manifest_service.hpp"

#include "application/mod/mod_load_step.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

constexpr const char* kManifestRootPrefix = "rom:/isaac_mods/";
constexpr const char* kModRootPrefix = "rom:/isaac_mods/mods/";
constexpr const char* kChunkRootPrefix = "@rom:/isaac_mods/";

// Mirrors the legacy path assembly: the Mod entry is addressed under the
// manifest root and `require` resolves relative to the Mod directory.
bool JoinPath(char* output, std::size_t capacity, const char* prefix,
              const char* suffix) noexcept {
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

void RecordFailure(ModLoadFailure* failure, ModLoadStep step,
                   std::uint32_t detail) noexcept {
    if (failure == nullptr) {
        return;
    }
    failure->step = step;
    failure->detail = detail;
}

Status MapParseFailure(ManifestParseFailure failure) noexcept {
    switch (failure) {
        case ManifestParseFailure::None:
            return Status::Ok();
        case ManifestParseFailure::InvalidArgument:
            return Status{StatusCode::InvalidArgument};
        case ManifestParseFailure::NoEnabledMod:
            return Status{StatusCode::NotFound};
        case ManifestParseFailure::TooManyMods:
            // 清单合法、只是条数超过本运行时的上限 ⇒ 与"字节坏了"分开报（`Corrupted` 会把人
            // 引到清单格式上去查）。
            return Status{StatusCode::CapacityExceeded};
        case ManifestParseFailure::InvalidJson:
        case ManifestParseFailure::InvalidSchema:
        case ManifestParseFailure::InvalidMod:
            return Status{StatusCode::Corrupted};
    }
    return Status{StatusCode::Corrupted};
}

} // namespace

Result<ResolvedManifestMod> ManifestService::Resolve(
    std::uint8_t* manifestBuffer, std::size_t manifestCapacity,
    char* entryPath, std::size_t entryPathCapacity,
    char* modRoot, std::size_t modRootCapacity,
    char* chunkName, std::size_t chunkNameCapacity,
    ModLoadFailure* failure) const noexcept {
    if (failure != nullptr) {
        *failure = ModLoadFailure{};
    }
    if (manifestBuffer == nullptr || manifestCapacity == 0 || entryPath == nullptr ||
        entryPathCapacity == 0 || modRoot == nullptr || modRootCapacity == 0 ||
        chunkName == nullptr || chunkNameCapacity == 0) {
        RecordFailure(failure, ModLoadStep::Request,
                      static_cast<std::uint32_t>(StatusCode::InvalidArgument));
        return Status{StatusCode::InvalidArgument};
    }

    std::size_t manifestBytes = 0;
    const Status read = content_.Read(kRomfsModManifestPath, manifestBuffer,
                                      manifestCapacity, &manifestBytes);
    if (!read.ok()) {
        RecordFailure(failure, ModLoadStep::ManifestRead,
                      static_cast<std::uint32_t>(read.code()));
        return read;
    }
    if (manifestBytes == 0) {
        RecordFailure(failure, ModLoadStep::ManifestRead,
                      static_cast<std::uint32_t>(StatusCode::Corrupted));
        return Status{StatusCode::Corrupted};
    }

    const ManifestParseOutcome parsed =
        parser_.Parse(reinterpret_cast<const char*>(manifestBuffer), manifestBytes);
    if (parsed.failure != ManifestParseFailure::None) {
        const Status status = MapParseFailure(parsed.failure);
        RecordFailure(failure, ModLoadStep::ManifestParse,
                      static_cast<std::uint32_t>(status.code()));
        return status;
    }

    // A pure-resource Mod declares no `entry`: only the Mod root is assembled and
    // the two script paths stay empty. Everything else about the resolution --
    // the manifest read, the parse, the failure reporting -- is unchanged.
    const bool hasEntry = parsed.manifest.HasEntry();
    if (!JoinPath(modRoot, modRootCapacity, kModRootPrefix, parsed.manifest.Directory())) {
        RecordFailure(failure, ModLoadStep::PathBuild,
                      static_cast<std::uint32_t>(StatusCode::CapacityExceeded));
        return Status{StatusCode::CapacityExceeded};
    }
    if (hasEntry) {
        if (!JoinPath(entryPath, entryPathCapacity, kManifestRootPrefix, parsed.manifest.Entry()) ||
            !JoinPath(chunkName, chunkNameCapacity, kChunkRootPrefix, parsed.manifest.Entry())) {
            RecordFailure(failure, ModLoadStep::PathBuild,
                          static_cast<std::uint32_t>(StatusCode::CapacityExceeded));
            return Status{StatusCode::CapacityExceeded};
        }
    } else {
        entryPath[0] = '\0';
        chunkName[0] = '\0';
    }

    ResolvedManifestMod resolved{};
    resolved.entryPath = entryPath;
    resolved.modRoot = modRoot;
    resolved.chunkName = chunkName;
    resolved.manifestBytes = manifestBytes;
    resolved.hasEntry = hasEntry;
    return resolved;
}

Status ManifestService::ResolveAll(std::uint8_t* manifestBuffer, std::size_t manifestCapacity,
                                   ResolvedManifestModBatch* batch,
                                   ModLoadFailure* failure) const noexcept {
    if (failure != nullptr) {
        *failure = ModLoadFailure{};
    }
    if (manifestBuffer == nullptr || manifestCapacity == 0 || batch == nullptr) {
        RecordFailure(failure, ModLoadStep::Request,
                      static_cast<std::uint32_t>(StatusCode::InvalidArgument));
        return Status{StatusCode::InvalidArgument};
    }

    std::size_t manifestBytes = 0;
    const Status read = content_.Read(kRomfsModManifestPath, manifestBuffer,
                                      manifestCapacity, &manifestBytes);
    if (!read.ok()) {
        RecordFailure(failure, ModLoadStep::ManifestRead,
                      static_cast<std::uint32_t>(read.code()));
        return read;
    }
    if (manifestBytes == 0) {
        RecordFailure(failure, ModLoadStep::ManifestRead,
                      static_cast<std::uint32_t>(StatusCode::Corrupted));
        return Status{StatusCode::Corrupted};
    }

    const ManifestParseAllOutcome parsed =
        parser_.ParseAll(reinterpret_cast<const char*>(manifestBuffer), manifestBytes);
    if (parsed.failure != ManifestParseFailure::None) {
        const Status status = MapParseFailure(parsed.failure);
        RecordFailure(failure, ModLoadStep::ManifestParse,
                      static_cast<std::uint32_t>(status.code()));
        return status;
    }

    // 逐条拼路径：一条失败就整批失败（半个批次更危险 —— 调用方会以为其余 Mod 是"加载成功但没内容"）。
    for (std::size_t index = 0; index < parsed.manifest.count; ++index) {
        const ModManifestEntry& entry = parsed.manifest.entries[index];
        ModPathBuffers& buffers = batch->paths[index];
        if (!JoinPath(buffers.modRoot.data(), buffers.modRoot.size(), kModRootPrefix,
                      entry.directory.data())) {
            RecordFailure(failure, ModLoadStep::PathBuild,
                          static_cast<std::uint32_t>(StatusCode::CapacityExceeded));
            batch->count = 0;
            return Status{StatusCode::CapacityExceeded};
        }
        const bool hasEntry = entry.entry[0] != '\0';
        if (hasEntry) {
            if (!JoinPath(buffers.entryPath.data(), buffers.entryPath.size(), kManifestRootPrefix,
                          entry.entry.data()) ||
                !JoinPath(buffers.chunkName.data(), buffers.chunkName.size(), kChunkRootPrefix,
                          entry.entry.data())) {
                RecordFailure(failure, ModLoadStep::PathBuild,
                              static_cast<std::uint32_t>(StatusCode::CapacityExceeded));
                batch->count = 0;
                return Status{StatusCode::CapacityExceeded};
            }
        } else {
            buffers.entryPath[0] = '\0';
            buffers.chunkName[0] = '\0';
        }
        ResolvedManifestMod& resolved = batch->mods[index];
        resolved.entryPath = buffers.entryPath.data();
        resolved.modRoot = buffers.modRoot.data();
        resolved.chunkName = buffers.chunkName.data();
        resolved.manifestBytes = manifestBytes;
        resolved.hasEntry = hasEntry;
    }
    batch->count = parsed.manifest.count;
    batch->manifestBytes = manifestBytes;
    return Status{StatusCode::Ok};
}

} // namespace isaac::runtime
