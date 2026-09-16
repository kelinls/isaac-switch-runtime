#include "infrastructure/mod/manifest_selector_adapter.hpp"

#include "mod_manifest.hpp"

#include <algorithm>
#include <cstring>

namespace isaac::runtime {

bool ManifestSelectorAdapter::Select(const char* json, std::size_t length,
                                     ModManifest* manifest) noexcept {
    if (json == nullptr || length == 0 || manifest == nullptr) {
        return false;
    }
    ::ModManifest::SelectedMod selected{};
    if (::ModManifest::SelectFirstEnabled(json, length, &selected) !=
        ::ModManifest::ParseResult::Success) {
        return false;
    }
    *manifest = ModManifest{};
    const std::size_t directoryBytes =
        std::min(manifest->directory.size() - 1, std::strlen(selected.directory.data()));
    const std::size_t entryBytes =
        std::min(manifest->entry.size() - 1, std::strlen(selected.entry.data()));
    std::memcpy(manifest->directory.data(), selected.directory.data(), directoryBytes);
    std::memcpy(manifest->entry.data(), selected.entry.data(), entryBytes);
    manifest->directory[directoryBytes] = '\0';
    manifest->entry[entryBytes] = '\0';
    return true;
}

ManifestSelectFunction ManifestSelectorAdapter::SelectFunction() noexcept {
    return &ManifestSelectorAdapter::Select;
}

ManifestParseFailure ManifestSelectorAdapter::SelectAll(const char* json, std::size_t length,
                                                        ModManifestSet* manifest) noexcept {
    if (json == nullptr || length == 0 || manifest == nullptr) {
        return ManifestParseFailure::InvalidArgument;
    }
    ::ModManifest::SelectedMods selected{};
    switch (::ModManifest::SelectAllEnabled(json, length, &selected)) {
        case ::ModManifest::ParseResult::Success:
            break;
        case ::ModManifest::ParseResult::InvalidArgument:
            return ManifestParseFailure::InvalidArgument;
        case ::ModManifest::ParseResult::InvalidJson:
            return ManifestParseFailure::InvalidJson;
        case ::ModManifest::ParseResult::InvalidSchema:
            return ManifestParseFailure::InvalidSchema;
        case ::ModManifest::ParseResult::InvalidMod:
            return ManifestParseFailure::InvalidMod;
        case ::ModManifest::ParseResult::NoEnabledMod:
            return ManifestParseFailure::NoEnabledMod;
        case ::ModManifest::ParseResult::TooManyMods:
            return ManifestParseFailure::TooManyMods;
    }
    *manifest = ModManifestSet{};
    for (std::size_t index = 0; index < selected.count; ++index) {
        ModManifestEntry& target = manifest->entries[index];
        const ::ModManifest::SelectedMod& source = selected.mods[index];
        const std::size_t directoryBytes =
            std::min(target.directory.size() - 1, std::strlen(source.directory.data()));
        const std::size_t entryBytes =
            std::min(target.entry.size() - 1, std::strlen(source.entry.data()));
        std::memcpy(target.directory.data(), source.directory.data(), directoryBytes);
        std::memcpy(target.entry.data(), source.entry.data(), entryBytes);
        target.directory[directoryBytes] = '\0';
        target.entry[entryBytes] = '\0';
        target.enabled = true;
    }
    manifest->count = selected.count;
    return ManifestParseFailure::None;
}

ManifestSelectAllFunction ManifestSelectorAdapter::SelectAllFunction() noexcept {
    return &ManifestSelectorAdapter::SelectAll;
}

} // namespace isaac::runtime
