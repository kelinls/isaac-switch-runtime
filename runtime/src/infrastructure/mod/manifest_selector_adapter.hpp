#pragma once

#include "application/mod/manifest_parser.hpp"

#include <cstddef>

namespace isaac::runtime {

// Supplies the `ManifestParser` JSON backend from the hand-written parser that
// already runs on device (`runtime/source/mod_manifest.cpp`).
//
// This is the only place in `runtime/src` that names the legacy
// `::ModManifest` type, which keeps the parser itself free of any legacy
// dependency (design §10.3) while the JSON implementation is still shared with
// the non-layered/probe builds. Replacing the backend later means replacing
// this adapter, not touching the parser, the service or their tests.
//
// Compiles only in layered builds: the legacy header lives in the primary
// source root, which is on the include path there and in the plugin/probe
// builds, but not for host-only test compilations.
class ManifestSelectorAdapter {
public:
    // The function-pointer form is what `ManifestParser` takes, so callers can
    // write `ManifestParser{ManifestSelectorAdapter::SelectFunction()}`.
    [[nodiscard]] static ManifestSelectFunction SelectFunction() noexcept;

private:
    // `ManifestSelectFunction` signature; maps the legacy parse result onto the
    // domain manifest and reports malformed input as `false`.
    [[nodiscard]] static bool Select(const char* json, std::size_t length,
                                     ModManifest* manifest) noexcept;
};

} // namespace isaac::runtime
