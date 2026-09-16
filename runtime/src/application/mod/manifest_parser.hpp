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
    // 启用的 Mod 个数超过 `kModManifestCapacity`（多模组，2026-09-16）。
    // 单列一条的理由：这与"字节坏了"完全不同 —— 清单是合法的，只是我们装不下，
    // 混进 `InvalidJson` 会让真机诊断指向错误的方向（去查清单格式而不是数量）。
    TooManyMods,
};

struct ManifestParseOutcome {
    ModManifest manifest{};
    ManifestParseFailure failure{ManifestParseFailure::None};
};

// 多模组（2026-09-16）：同一份清单字节里**所有**启用的 Mod。
struct ManifestParseAllOutcome {
    ModManifestSet manifest{};
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

// 同一个后端的"多模组"形态。
//
// 与单模组的 `ManifestSelectFunction`（只能报"成功/失败"）不同，这一个**要求后端说出原因**：
// 多模组路径上"一个都没启用"、"条数超过上限"、"字节坏了"是三件要分开查的事
// （分别对应 `NoEnabledMod` / `TooManyMods` / `Json/Schema`），压成一个 `false` 就等于把
// 真机上唯一的线索丢掉。返回 `None` 表示成功且至少有一个启用的 Mod。
using ManifestSelectAllFunction = ManifestParseFailure (*)(const char* json, std::size_t length,
                                                          ModManifestSet* manifest) noexcept;

// Manifest bytes in, one enabled Mod out. No I/O, no Lua, no global state, so a
// malformed manifest cannot fail anywhere except through `failure`.
class ManifestParser {
public:
    explicit ManifestParser(ManifestSelectFunction select = nullptr,
                            ManifestSelectAllFunction selectAll = nullptr) noexcept
        : select_(select), selectAll_(selectAll) {}

    [[nodiscard]] ManifestParseOutcome Parse(const char* json, std::size_t length) const noexcept;

    // 收下全部启用的 Mod（多模组加载）。判定口径与 `Parse` 逐条一致，只是不再是"第一个"。
    [[nodiscard]] ManifestParseAllOutcome ParseAll(const char* json,
                                                   std::size_t length) const noexcept;

private:
    ManifestSelectFunction select_;
    ManifestSelectAllFunction selectAll_{nullptr};
};

} // namespace isaac::runtime
