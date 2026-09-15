#pragma once

#include "ports/lua_engine_port.hpp"

#include "game_file_reader.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Bridges ILuaEnginePort onto the existing LuaRuntime. It owns the content
// bindings used by `require`, so the application layer never sees a game
// pointer or a lua_State.
class EmbeddedLuaAdapter final : public ILuaEnginePort {
public:
    explicit EmbeddedLuaAdapter(const GameFileReader::Bindings& bindings) noexcept
        : bindings_(bindings) {}

    [[nodiscard]] Status Initialize() noexcept override;
    [[nodiscard]] bool IsReady() const noexcept override;
    [[nodiscard]] Status RunChunk(const LuaChunk& chunk) noexcept override;
    [[nodiscard]] Status LoadManifestMod(const ManifestModLoad& load,
                                        std::uint32_t* failureDetail) noexcept override;
    [[nodiscard]] Status CallGlobal(const char* name, int argumentCount,
                                    int* resultCount) noexcept override;
    void Shutdown() noexcept override;

private:
    GameFileReader::Bindings bindings_{};
};

} // namespace isaac::runtime
