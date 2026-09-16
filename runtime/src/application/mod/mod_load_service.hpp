#pragma once

#include "application/mod/manifest_service.hpp"
#include "domain/runtime/status.hpp"
#include "ports/content_port.hpp"
#include "ports/lua_engine_port.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// What the Lua driver needs for one load: the entry script buffer plus the
// paths and sizes the manifest service already resolved.
struct ModLoadRequest {
    const ResolvedManifestMod* resolved{nullptr};
    std::uint8_t* entryBuffer{nullptr};
    std::size_t entryCapacity{0};
};

// Whether the Lua half of the load ran.
//
// A PC Mod does not need a script: the game mounts the `resources/` it ships and
// that is the whole Mod. The loader therefore treats a missing entry as a normal
// outcome rather than a failure, and says which of the two cases it saw, because
// only the caller can turn that into a diagnostic.
enum class ModScriptState : std::uint32_t {
    // The entry was read and handed to the engine.
    Executed = 0,
    // The manifest omits `entry`: the Mod is declared as resources only.
    DeclaredScriptless = 1,
    // The manifest names an entry but the package does not contain that file.
    // This used to fail the whole load, which also dropped the content mount
    // points and made every texture the Mod ships unreachable.
    EntryAbsent = 2,
};

struct ModLoadOutcome {
    std::size_t manifestBytes{0};
    std::size_t entryBytes{0};
    ModScriptState scriptState{ModScriptState::Executed};

    [[nodiscard]] bool ScriptExecuted() const noexcept {
        return scriptState == ModScriptState::Executed;
    }
};

// 一批 Mod 的加载结果（多模组加载，2026-09-16）。
//
// 每个 Mod 各记一份 `ModLoadOutcome`：这一批唯一需要对外说清楚的就是"哪些 Mod 跑起了脚本、
// 哪些是纯资源型、哪个失败了"。
struct ModLoadBatchOutcome {
    static constexpr std::size_t kCapacity = kModManifestCapacity;

    std::array<ModLoadOutcome, kCapacity> scripts{};
    std::size_t count{0};
    // 第一个失败的 Mod 的下标；`anyFailure == false` 时无意义。
    std::size_t failedIndex{0};
    bool anyFailure{false};
};

// Drives the Lua half of the Mod loading use case: read the entry script
// through the content port and hand the bytes to the engine.
//
// The manifest half (read, parse, select, path assembly) belongs to
// `ManifestService`, so a manifest problem is never reported from here and this
// service never needs to know how a manifest is parsed.
//
// The service is the bootstrap path itself: it must not require an already
// initialized Lua engine, because loading the Mod entry script is what
// initializes the engine.
//
// A pure-resource Mod never reaches the engine at all: with no entry (or with an
// entry the package does not contain) the call succeeds and reports the state
// through `ModLoadOutcome::scriptState`, leaving the caller's content mount
// points as the whole effect of the load.
class ModLoadService {
public:
    ModLoadService(IContentPort& content, ILuaEnginePort& lua) noexcept
        : content_(content), lua_(lua) {}

    [[nodiscard]] Result<ModLoadOutcome> Load(const ModLoadRequest& request,
                                             ModLoadFailure* failure = nullptr) noexcept;

    // 多模组（2026-09-16）：把一批已解析的 Mod **按顺序**加载进同一个 Lua 状态。
    //
    // 语义要点：
    //   * 每个 Mod 的入口脚本都在**同一个** Lua 状态里执行（PC 就是这样：多个 Mod 共享
    //     `Isaac` 等全局，回调按登记顺序一起派发）；追加执行由 `ILuaEnginePort` 的实现负责；
    //   * 入口缓冲是**共用的**（一个 Mod 读完、跑完，再读下一个），所以调用方只需要一块缓冲；
    //   * 某个 Mod 的入口读失败/脚本报错时**不中断这一批**：PC 上坏掉的 Mod 不会连带
    //     拖死其它 Mod，而"哪个 Mod 坏了"照样通过 `ModLoadBatchOutcome` 报出来；
    //   * 纯资源型 Mod 正常跳过引擎（与单个 `Load` 完全一致）。
    [[nodiscard]] Status LoadAll(const ResolvedManifestModBatch& batch,
                                 std::uint8_t* entryBuffer, std::size_t entryCapacity,
                                 ModLoadBatchOutcome* outcome,
                                 ModLoadFailure* failure = nullptr) noexcept;

private:
    IContentPort& content_;
    ILuaEnginePort& lua_;
};

} // namespace isaac::runtime
