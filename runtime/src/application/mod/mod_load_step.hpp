#pragma once

#include <cstdint>

namespace isaac::runtime {

// Ordered steps of the Mod loading use case, shared by `ManifestService` (read,
// parse, path build) and `ModLoadService` (entry read, Lua init) so the device
// diagnostics keep one step vocabulary after the split.
//
// The numeric values are a wire format: they must stay equal to the legacy
// `DefaultManifestFailureDetail` codes (`ManifestRead = 1`, `ManifestParse = 2`,
// `PathBuild = 3`, `EntryRead = 4`) so `TestRunObserver` and
// `DefaultManifestFailureDetail` report the same number before and after the
// migration.
enum class ModLoadStep : std::uint32_t {
    None = 0,
    ManifestRead = 1,
    ManifestParse = 2,
    PathBuild = 3,
    EntryRead = 4,
    // Lua initialization failures carry the engine's own result in the low
    // byte: the reported detail is `0x10 + detail`.
    LuaInit = 0x10,
    // Not a legacy step: the request itself was unusable. Keeping it distinct
    // from `None` means a rejected call is never reported as "no failure".
    Request = 0x20,
};

// 诊断字 `[11]` 的打包格式（2026-09-12 起）。
//
// 历史值只有"步骤"这一个字节的含义：`0` = 带脚本加载成功、`1..4` = 失败在哪一步、
// `5` = 只挂载内容（纯资源 Mod）、`0x10 + detail` = Lua 初始化失败。真机报告 `01789200504`
// 证明了这样不够用：它只告诉我们 `EntryRead` 失败，**没告诉我们为什么**（文件不存在？
// 还是文件太大放不进缓冲区？），而真机一轮只能问一个问题 —— 那一轮因此白丢。
//
// 所以高位补上"为什么"与"多大"：
//   bits 0..7   旧格式的步骤字（判定"是否失败"只看这 8 位）
//   bits 8..15  失败细节（`StatusCode`；对 LuaInit 是引擎自己的 detail）
//   bits 16..47 读取失败时**观测到的文件长度**（超过 4 GiB 截断）
inline constexpr std::uint32_t kModLoadWordStepMask = 0xFFu;
inline constexpr std::uint32_t kModLoadWordScriptless = 5u;

inline constexpr std::uint32_t PackModLoadWord(std::uint32_t stepWord, std::uint32_t detail,
                                               std::uint64_t observedBytes) noexcept {
    const std::uint32_t clampedBytes =
        observedBytes > 0xFFFFFFFFULL ? 0xFFFFFFFFu : static_cast<std::uint32_t>(observedBytes);
    return (stepWord & kModLoadWordStepMask) | ((detail & 0xFFu) << 8) | (clampedBytes << 16);
}

// 只按低 8 位判定"这次加载是不是失败了"。`Scriptless`（5）不是失败：Mod 的内容挂载点已注册，
// 只是没有脚本可跑。
inline constexpr bool IsModLoadFailureWord(std::uint32_t word) noexcept {
    const std::uint32_t step = word & kModLoadWordStepMask;
    return step != 0u && step != kModLoadWordScriptless;
}

// 编译期自测（每次构建都会跑，比宿主机文本断言强）：打包/判定必须满足这几个性质。
// 用真机报告 `01789200504` 的真实数字（步骤 4 = 入口脚本读取、原因 5 = 缓冲区太小、
// 文件 87328 字节 = EID 的 `main.lua`）当样例。
static_assert(PackModLoadWord(4u, 5u, 87328u) == (4u | (5u << 8) | (87328u << 16)),
              "PackModLoadWord 的位域布局变了");
static_assert(IsModLoadFailureWord(PackModLoadWord(4u, 5u, 87328u)),
              "打包后的失败字必须仍被判为失败");
static_assert(!IsModLoadFailureWord(0u), "0 = 带脚本加载成功，不是失败");
static_assert(!IsModLoadFailureWord(kModLoadWordScriptless),
              "5 = 纯资源型已挂载，不是失败");
static_assert(IsModLoadFailureWord(4u), "只有步骤字节时也必须判为失败（旧格式兼容）");
static_assert(IsModLoadFailureWord(5u | (5u << 8)) == false,
              "纯资源型即使带上原因字节也不是失败");
static_assert((PackModLoadWord(4u, 5u, 87328u) & kModLoadWordStepMask) == 4u,
              "低 8 位必须还是旧的步骤字");
static_assert(PackModLoadWord(0u, 0u, 0u) == 0u, "成功必须报 0");
static_assert(PackModLoadWord(5u, 0u, 0u) == 5u, "纯资源型必须报 5");
static_assert(PackModLoadWord(0x10u, 7u, 0u) >> 8 == 7u,
              "Lua 初始化失败的原因必须落在 bits 8..15");

} // namespace isaac::runtime
