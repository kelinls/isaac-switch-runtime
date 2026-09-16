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

ManifestParseAllOutcome ManifestParser::ParseAll(const char* json, std::size_t length) const noexcept {
    ManifestParseAllOutcome outcome{};
    if (selectAll_ == nullptr || json == nullptr || length == 0) {
        outcome.failure = ManifestParseFailure::InvalidArgument;
        return outcome;
    }
    // 后端必须说出原因（见 `ManifestSelectAllFunction` 的注释）：这里**原样**采用它的判定，
    // 不再压成一句"字节坏了"。
    const ManifestParseFailure reported = selectAll_(json, length, &outcome.manifest);
    if (reported != ManifestParseFailure::None) {
        outcome.manifest = ModManifestSet{};
        outcome.failure = reported;
        return outcome;
    }
    if (outcome.manifest.empty()) {
        outcome.manifest = ModManifestSet{};
        outcome.failure = ManifestParseFailure::InvalidMod;
        return outcome;
    }
    // 每个条目的 `directory` 都必须可用：空的目录名会让加载路径去拼一个空路径。
    // `entry` 为空**不是**错误（纯资源 Mod，见 `ModLoadService`）。
    for (std::size_t index = 0; index < outcome.manifest.count; ++index) {
        if (outcome.manifest.entries[index].directory[0] == '\0') {
            outcome.manifest = ModManifestSet{};
            outcome.failure = ManifestParseFailure::InvalidMod;
            return outcome;
        }
    }
    return outcome;
}

} // namespace isaac::runtime
