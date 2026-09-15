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

} // namespace isaac::runtime
