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

} // namespace isaac::runtime
