#pragma once

#include "application/mod/manifest_parser.hpp"
#include "application/mod/mod_load_step.hpp"
#include "domain/mod/mod_manifest.hpp"
#include "domain/runtime/status.hpp"
#include "ports/content_port.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Where the Mod manifest lives and how much of it we are willing to read. The
// sizes are part of the on-device RomFS contract, so they are named here rather
// than repeated at the call site that owns the buffers.
//
// The manifest capacity was 131072 and had never been stressed (the default Mod
// pack is a 4-file Mod with a 1.8 KB manifest). A real PC texture Mod is another
// order of magnitude: `qualityonsprites` is 712 source files plus 711 generated
// `.pcx`, and the tool's per-file `path`+`sha256` list makes that manifest
// **309 KB**. `GameFileReader::ReadTextFile` reports `LengthOutOfRange` for a file
// longer than the buffer, i.e. an over-long manifest is a hard `ManifestRead`
// failure, so the buffer has to fit the biggest pack we intend to ship. 512 KiB
// covers ~2600 files at the measured ~200 bytes per entry and costs .bss only (the
// module image on disk does not change).
inline constexpr char kRomfsModManifestPath[] = "rom:/isaac_mods/manifest.json";
inline constexpr std::size_t kRomfsModManifestMaximumLength = 524288;
// Lua 源码容量（入口脚本与 `require` 共用同一个上限）。
//
// 原值 16384 是按"小型示例 Mod"定的，**从来没有被真实 PC Mod 压过**，而它是硬失败：
// `GameFileReader::ReadTextFile` 对超长文件返回 `LengthOutOfRange` → `EntryRead` 步骤失败
// （诊断字 `[11] = 4`），Mod 的 Lua 一次都不执行。
//
// 2026-09-12 真机报告 `01789200504` 就是这个：EID 的 `main.lua` 是 **87,328 字节**、
// 它 `require` 的语言文件最大 **243,740 字节**（`descriptions/rep/ko_kr.lua`），都远超 16 KiB。
// 现象是"Mod 加载不报错、回调注册表却是空的、屏幕上什么都没有"——正是"EID 不显示"的根因。
//
// 取值 1 MiB：是 EID 最大 Lua 文件的 4 倍，只花 .bss（磁盘上的模块映像不变）。
// 这个数字与 `runtime/source/runtime_constants.hpp` 的同名常量必须一致，
// `hook_manager.cpp` 里有 `static_assert` 兜住（历史上这里就是靠"两处常量各自演化"埋过坑）。
inline constexpr std::size_t kRomfsModScriptMaximumLength = 1048576;

// Capacity of one assembled path. `manifest_parser` owns the directory/entry
// capacities; these add room for the "rom:/isaac_mods/mods/" prefix and the
// "@" chunk marker (33 bytes covers the longest prefix plus the NUL).
inline constexpr std::size_t kModEntryPathCapacity = kModEntryCapacity + 32;
inline constexpr std::size_t kModRootPathCapacity = kModDirectoryCapacity + 32;
inline constexpr std::size_t kModChunkNameCapacity = kModEntryCapacity + 33;

struct ModLoadFailure {
    ModLoadStep step{ModLoadStep::None};
    std::uint32_t detail{0};
    // 读取失败时**观测到的文件长度**（`ReadTextFile` 在用 `getLength` 判定超长时就已经知道它）。
    // 有了它，诊断字才能区分"文件不存在"与"文件太大放不进缓冲区"——否则两者都只表现为
    // "Mod 没加载"，而真机一轮只能问一个问题（2026-09-12 的 EID 轮次正是这么丢掉的）。
    std::uint64_t observedBytes{0};
};

// Everything the Lua driver needs about one selected Mod: the three assembled
// paths plus the manifest size for diagnostics. Plain pointers into the
// caller's buffers (or into the caller's temporary), never owning storage, so
// this stays a cheap value object.
struct ResolvedManifestMod {
    const char* entryPath{nullptr};
    const char* modRoot{nullptr};
    const char* chunkName{nullptr};
    std::size_t manifestBytes{0};
    // False for a pure-resource Mod: the manifest omitted `entry`, so
    // `entryPath`/`chunkName` are empty strings and `modRoot` is the only path
    // the load path uses (its content mount points).
    bool hasEntry{false};
};

// Reads the manifest through `IContentPort`, parses it with the injected
// `ManifestParser`, and assembles the three paths the Lua layer addresses.
//
// It deliberately stops there: no entry read, no Lua, no engine readiness
// check. `ModLoadService` owns the Lua side, so a manifest problem can never be
// reported as an engine problem.
class ManifestService {
public:
    ManifestService(IContentPort& content, ManifestParser parser) noexcept
        : content_(content), parser_(parser) {}

    [[nodiscard]] Result<ResolvedManifestMod> Resolve(
        std::uint8_t* manifestBuffer, std::size_t manifestCapacity,
        char* entryPath, std::size_t entryPathCapacity,
        char* modRoot, std::size_t modRootCapacity,
        char* chunkName, std::size_t chunkNameCapacity,
        ModLoadFailure* failure = nullptr) const noexcept;

private:
    IContentPort& content_;
    ManifestParser parser_;
};

} // namespace isaac::runtime
