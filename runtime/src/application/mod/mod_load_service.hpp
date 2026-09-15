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

private:
    IContentPort& content_;
    ILuaEnginePort& lua_;
};

} // namespace isaac::runtime
