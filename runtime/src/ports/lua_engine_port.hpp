#pragma once

#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// A script chunk held by the caller. Keeping the bytes out of the port means
// the port needs no Lua header and no lua_State is ever exposed.
struct LuaChunk {
    const char* name{nullptr};
    const char* bytes{nullptr};
    std::size_t size{0};
};

// Loading a manifest-selected Mod. The entry script bytes are read by the
// caller through `IContentPort` and handed over here; the engine still needs
// the Mod root because `require` resolves sibling files relative to it through
// the same content bindings.
struct ManifestModLoad {
    const char* entryBytes{nullptr};
    std::size_t entryLength{0};
    const char* chunkName{nullptr};
    const char* modRoot{nullptr};
};

class ILuaEnginePort {
public:
    virtual ~ILuaEnginePort() = default;

    [[nodiscard]] virtual Status Initialize() noexcept = 0;

    // Reports whether a script has already been initialized. It is an
    // observability query: `LoadManifestMod` performs the initialization of
    // the manifest-selected Mod, so it must not require a ready engine.
    [[nodiscard]] virtual bool IsReady() const noexcept = 0;

    // Compiles and runs one chunk; a Lua error is a Status, never a longjmp
    // across the Runtime boundary.
    [[nodiscard]] virtual Status RunChunk(const LuaChunk& chunk) noexcept = 0;

    // Loads the entry script of a manifest-selected Mod. On failure the
    // engine's own failure code is written to `failureDetail` (0 on success);
    // it is an opaque number to the application layer and only forwarded to
    // diagnostics, so legacy failure codes stay observable.
    [[nodiscard]] virtual Status LoadManifestMod(const ManifestModLoad& load,
                                                std::uint32_t* failureDetail) noexcept = 0;

    // Calls a global function registered by the Mod entry script.
    [[nodiscard]] virtual Status CallGlobal(const char* name, int argumentCount,
                                            int* resultCount) noexcept = 0;

    virtual void Shutdown() noexcept = 0;
};

} // namespace isaac::runtime
