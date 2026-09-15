#include "application/mod/mod_load_service.hpp"

#include "application/mod/mod_load_step.hpp"

namespace isaac::runtime {
namespace {

void RecordFailure(ModLoadFailure* failure, ModLoadStep step,
                   std::uint32_t detail, std::uint64_t observedBytes = 0) noexcept {
    if (failure == nullptr) {
        return;
    }
    failure->step = step;
    failure->detail = detail;
    failure->observedBytes = observedBytes;
}

} // namespace

Result<ModLoadOutcome> ModLoadService::Load(const ModLoadRequest& request,
                                            ModLoadFailure* failure) noexcept {
    if (failure != nullptr) {
        *failure = ModLoadFailure{};
    }
    if (request.resolved == nullptr || request.resolved->modRoot == nullptr) {
        RecordFailure(failure, ModLoadStep::Request,
                      static_cast<std::uint32_t>(StatusCode::InvalidArgument));
        return Status{StatusCode::InvalidArgument};
    }

    // Pure-resource Mod: the manifest declares no entry, so there is no script to
    // read and no engine to initialize. The caller has already registered the
    // Mod's content mount points, and this returns success with the state that
    // says "mounted, no script" instead of a failure.
    if (!request.resolved->hasEntry) {
        ModLoadOutcome scriptless{};
        scriptless.manifestBytes = request.resolved->manifestBytes;
        scriptless.entryBytes = 0;
        scriptless.scriptState = ModScriptState::DeclaredScriptless;
        return scriptless;
    }

    if (request.resolved->entryPath == nullptr || request.resolved->chunkName == nullptr ||
        request.entryBuffer == nullptr || request.entryCapacity == 0) {
        RecordFailure(failure, ModLoadStep::Request,
                      static_cast<std::uint32_t>(StatusCode::InvalidArgument));
        return Status{StatusCode::InvalidArgument};
    }

    std::size_t entryLength = 0;
    const Status read = content_.Read(request.resolved->entryPath, request.entryBuffer,
                                      request.entryCapacity, &entryLength);
    if (!read.ok()) {
        if (read.code() == StatusCode::NotFound) {
            // The entry is named but absent from the package. The Mod still ships
            // usable content, so this degrades to a content-only load; any other
            // read error (capacity, I/O, corruption) stays a failure.
            ModLoadOutcome absent{};
            absent.manifestBytes = request.resolved->manifestBytes;
            absent.entryBytes = 0;
            absent.scriptState = ModScriptState::EntryAbsent;
            return absent;
        }
        RecordFailure(failure, ModLoadStep::EntryRead,
                      static_cast<std::uint32_t>(read.code()), entryLength);
        return read;
    }
    if (entryLength == 0) {
        RecordFailure(failure, ModLoadStep::EntryRead,
                      static_cast<std::uint32_t>(StatusCode::Corrupted));
        return Status{StatusCode::Corrupted};
    }

    ManifestModLoad load{};
    load.entryBytes = reinterpret_cast<const char*>(request.entryBuffer);
    load.entryLength = entryLength;
    load.chunkName = request.resolved->chunkName;
    load.modRoot = request.resolved->modRoot;
    std::uint32_t engineDetail = 0;
    const Status executed = lua_.LoadManifestMod(load, &engineDetail);
    if (!executed.ok()) {
        RecordFailure(failure, ModLoadStep::LuaInit, engineDetail);
        return executed;
    }

    ModLoadOutcome outcome{};
    outcome.manifestBytes = request.resolved->manifestBytes;
    outcome.entryBytes = entryLength;
    outcome.scriptState = ModScriptState::Executed;
    return outcome;
}

} // namespace isaac::runtime
