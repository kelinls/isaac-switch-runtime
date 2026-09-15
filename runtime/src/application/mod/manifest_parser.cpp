#include "application/mod/manifest_parser.hpp"

namespace isaac::runtime {

ManifestParseOutcome ManifestParser::Parse(const char* json, std::size_t length) const noexcept {
    ManifestParseOutcome outcome{};
    if (select_ == nullptr || json == nullptr || length == 0) {
        outcome.failure = ManifestParseFailure::InvalidArgument;
        return outcome;
    }
    if (!select_(json, length, &outcome.manifest)) {
        // The backend does not report which rule rejected the bytes, and the
        // load path only distinguishes "malformed" from "nothing enabled", so
        // this stays the generic malformed-input failure.
        outcome.manifest = ModManifest{};
        outcome.failure = ManifestParseFailure::InvalidJson;
        return outcome;
    }
    if (outcome.manifest.directory[0] == '\0') {
        // A backend that reports success without a usable directory would
        // otherwise send the load path after an empty path. An empty *entry* is
        // not an error any more: it is the pure-resource Mod (no entry script at
        // all), and `ModLoadService` skips engine initialization while the content
        // mount points stay registered.
        outcome.manifest = ModManifest{};
        outcome.failure = ManifestParseFailure::InvalidMod;
        return outcome;
    }
    return outcome;
}

} // namespace isaac::runtime
