#pragma once

#include "domain/mod/mod_manifest.hpp"
#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Why a manifest parse failed. `InvalidArgument`/`InvalidJson`/`InvalidSchema`/
// `InvalidMod` are malformed input; `NoEnabledMod` is well-formed input that
// simply enables nothing. The load path reports both as `ManifestSelect` and
// relies on the mapped `Status` for the distinction, so this enum stays
// internal to the manifest package and never reaches Lua or the device
// diagnostics as a raw value.
enum class ManifestParseFailure : std::uint32_t {
    None = 0,
    InvalidArgument,
    InvalidJson,
    InvalidSchema,
    InvalidMod,
    NoEnabledMod,
};

struct ManifestParseOutcome {
    ModManifest manifest{};
    ManifestParseFailure failure{ManifestParseFailure::None};
};

// The JSON backend the parser delegates the actual text handling to. Keeping it
// a plain function pointer is deliberate:
//
//   * the parser stays a pure value type with no file, Lua or legacy dependency
//     (design §10.3), and the composition root supplies the hand-written
//     parser by name;
//   * the JSON implementation can be replaced later without rebuilding the
//     parser's callers or changing any test that injects a fake.
//
// `true` means "parsed and one enabled Mod was selected"; `false` means the
// bytes are malformed. The backend must not modify `json`.
using ManifestSelectFunction = bool (*)(const char* json, std::size_t length,
                                        ModManifest* manifest) noexcept;

// Manifest bytes in, one enabled Mod out. No I/O, no Lua, no global state, so a
// malformed manifest cannot fail anywhere except through `failure`.
class ManifestParser {
public:
    explicit ManifestParser(ManifestSelectFunction select = nullptr) noexcept
        : select_(select) {}

    [[nodiscard]] ManifestParseOutcome Parse(const char* json, std::size_t length) const noexcept;

private:
    ManifestSelectFunction select_;
};

} // namespace isaac::runtime
